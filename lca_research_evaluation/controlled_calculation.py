"""Thin D-18 runner for one source-bound, controlled LCA calculation."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Callable, Mapping

from lca_research_kernel.assurance import AssuranceBoundary
from lca_research_kernel.capability_gateway import CapabilityGateway
from lca_research_kernel.context_assembler import ContextAssembler
from lca_research_kernel.execution_environment import ProjectExecutionEnvironment
from lca_research_kernel.lca_domain_kernel import LCADomainKernel
from lca_research_kernel.product_interface import ProductInterface
from lca_research_kernel.project_record import ProjectRecord, canonical_sha256
from lca_research_kernel.research_orchestrator import ResearchOrchestrator
from lca_research_kernel.run_control import DEFAULT_RUN_BUDGET, RunController
from lca_research_kernel.run_trace import RunTrace


RUN_CONTRACT = "lca_research_evaluation.controlled_lca_calculation_run/v1"
CHECKPOINT_CONTRACT = (
    "lca_research_evaluation.controlled_lca_calculation_checkpoint/v1"
)
BRANCH_FILES = {
    "complete": "fixture/material-package.json",
    "missing": "fixture/missing-critical-field-package.json",
    "tool_failure": "fixture/material-package.json",
}
VALIDATION_CAPABILITY = "tidas.validate"
CALCULATION_CAPABILITY = "openlca.calculate"


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"controlled calculation JSON is unreadable: {path}") from error
    if not isinstance(value, dict):
        raise ValueError("controlled calculation JSON must contain an object")
    return value


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _NoModelRuntime:
    """Stop ProductInterface at a typed model boundary without a model call."""

    def capabilities(self) -> list[dict]:
        return []

    def normalize_error(self, error: BaseException) -> dict:
        return {
            "code": "model_unavailable",
            "retryable": False,
            "message": type(error).__name__,
        }


class ControlledLCACalculation:
    """Compose existing D-18 ports; the evaluator owns no accepted truth."""

    def __init__(
        self,
        *,
        repository_root: Path,
        workspace: Path,
        fixture_root: Path,
        capability_gateway: CapabilityGateway,
        assurance: AssuranceBoundary,
        run_id: str,
        clock: Callable[[], str],
    ):
        self.repository_root = Path(repository_root).resolve()
        self.workspace = Path(workspace).resolve()
        self.fixture_root = Path(fixture_root).resolve()
        self.capability_gateway = capability_gateway
        self.assurance = assurance
        self.run_id = run_id
        self.clock = clock
        self.domain_kernel = LCADomainKernel()
        self.context_assembler = ContextAssembler()
        self._interruption_checkpoint: str | None = None

    def interrupt_after(self, checkpoint: str) -> dict:
        if checkpoint != "tidas_validation":
            raise ValueError("controlled calculation checkpoint is unsupported")
        self._interruption_checkpoint = checkpoint
        return {
            "contract": CHECKPOINT_CONTRACT,
            "checkpoint": checkpoint,
            "resume_route": "resume-controlled-lca-calculation",
        }

    def run(
        self,
        *,
        branch: str,
        resume_from: Mapping[str, object] | None = None,
    ) -> dict:
        material, material_ref = self._load_material(branch)
        if resume_from is not None:
            self._validate_checkpoint(resume_from)
        research_id = (
            "controlled-calculation-"
            + canonical_sha256(
                {
                    "material_sha256": material_ref["sha256"],
                    "branch": branch,
                }
            )[:20]
        )
        interface = ProductInterface(
            self.workspace,
            model_gateway=_NoModelRuntime(),
            capability_gateway=self.capability_gateway,
            assurance=self.assurance,
            clock=self.clock,
            run_id=self.run_id,
        )
        interface.start_research(
            {
                "contract": "lca_research_kernel.research_start/v1",
                "research_id": research_id,
                "title": "Controlled source-bound LCA calculation",
                "objective": (
                    "Calculate one source-bound LCA result without changing "
                    "TianGong authority or promoting tool output to formal fact."
                ),
                "source_refs": deepcopy(material["source_refs"]),
                "received_at": self.clock(),
            }
        )
        record = ProjectRecord.open_or_create_project(
            self.workspace / "projects" / research_id
        )
        trace = RunTrace(
            record.workspace / "run-traces" / f"{self.run_id}.jsonl",
            run_id=self.run_id,
        )
        execution_policy = _load_json(
            self.repository_root
            / "lca_research_kernel/contracts/project-execution-policy.json"
        )
        environment = ProjectExecutionEnvironment.create(
            self.workspace,
            research_id,
            execution_policy,
        )
        gateway = self.capability_gateway.with_execution_environment(environment)
        orchestrator = ResearchOrchestrator(
            record,
            model_gateway=_NoModelRuntime(),
            capability_gateway=gateway,
            assurance=self.assurance,
            clock=self.clock,
            run_trace=trace,
            context_assembler=self.context_assembler,
            run_controller=RunController.restore(
                DEFAULT_RUN_BUDGET,
                run_id=self.run_id,
                trace=trace,
            ),
            execution_environment=environment,
        )
        self._submit_material(
            orchestrator,
            material=material,
            material_ref=material_ref,
            branch=branch,
        )
        goal_scope = self._goal_scope(material)
        scope_validation = self.domain_kernel.validate_goal_and_scope(goal_scope)
        self._submit_scope_assessment(
            orchestrator,
            goal_scope=goal_scope,
            scope_validation=scope_validation,
            material_ref=material_ref,
        )
        if scope_validation["status"] != "valid":
            return self._result(
                branch=branch,
                research_id=research_id,
                record=record,
                trace=trace,
                calculation_executed=False,
                receipts=[],
                candidate_result_ref=None,
                assurance_decision_ref=None,
                next_route="retain-bounded-missing-functional-unit-conclusion",
                scope_validation=scope_validation,
            )

        payload = {
            "scientific_inputs": deepcopy(material["scientific_inputs"]),
            "scientific_inputs_sha256": material["scientific_inputs_sha256"],
            "source_refs": deepcopy(material["source_refs"]),
            "execution_spec": self._load_execution_spec(material),
        }
        validation = orchestrator.execute_bounded_capability(
            research_id,
            capability_id=VALIDATION_CAPABILITY,
            effect="local_read",
            purpose="validate exact source-bound TIDAS calculation inputs",
            payload=payload,
        )
        if validation["status"] != "completed":
            return self._failure_result(
                branch, research_id, record, trace, validation, scope_validation
            )
        validation_content = validation["sourced_result"]["content"]
        if (
            not isinstance(validation_content, Mapping)
            or validation_content.get("validation_status") != "passed"
            or validation_content.get("scientific_inputs_sha256")
            != material["scientific_inputs_sha256"]
        ):
            raise ValueError("TIDAS validation result is invalid or drifted")
        receipts = [self._capability_receipt(validation, attempts=1)]

        if self._interruption_checkpoint == "tidas_validation":
            self._interruption_checkpoint = None
            return self._result(
                branch=branch,
                research_id=research_id,
                record=record,
                trace=trace,
                calculation_executed=False,
                receipts=receipts,
                candidate_result_ref=None,
                assurance_decision_ref=None,
                next_route="resume-controlled-lca-calculation",
                scope_validation=scope_validation,
            )

        attempts = 0
        max_attempts = 1
        if branch == "tool_failure":
            plan = _load_json(self.fixture_root / "fixture/tool-failure-plan.json")
            if plan.get("scientific_inputs_sha256") != material["scientific_inputs_sha256"]:
                raise ValueError("tool failure plan scientific input drifted")
            max_attempts = int(plan["max_attempts"])
        calculation = None
        while attempts < max_attempts:
            attempts += 1
            calculation = orchestrator.execute_bounded_capability(
                research_id,
                capability_id=CALCULATION_CAPABILITY,
                effect="local_write",
                purpose="execute isolated exact source-bound LCA calculation",
                payload=payload,
            )
            if calculation["status"] == "completed":
                break
            failure = calculation.get("failure")
            if (
                not isinstance(failure, Mapping)
                or failure.get("retryable") is not True
            ):
                break
        if calculation is None or calculation["status"] != "completed":
            return self._failure_result(
                branch,
                research_id,
                record,
                trace,
                calculation or {"status": "failed"},
                scope_validation,
            )
        receipts.append(self._capability_receipt(calculation, attempts=attempts))
        calculation_content = calculation["sourced_result"]["content"]
        calculation_validation = self.domain_kernel.validate_model_and_calculation(
            calculation_content
        )
        if calculation_validation["status"] != "valid":
            raise ValueError("OpenLCA calculation result failed LCA Domain validation")
        accepted_event = self._submit_calculation_candidate(
            orchestrator,
            calculation=calculation_content,
            calculation_result=calculation["sourced_result"],
            calculation_validation=calculation_validation,
            material_ref=material_ref,
        )
        candidate = record.read_current_project_view()["facts"][
            "controlled-calculation-candidate"
        ]
        candidate_ref = {
            "project_id": research_id,
            "fact_id": candidate["fact_id"],
            "sha256": canonical_sha256(candidate),
        }
        assurance_ref = {
            "event_id": accepted_event["event_id"],
            "event_sha256": accepted_event["event_sha256"],
        }
        # Record the post-acceptance boundary before returning so a replay
        # observes the same idempotency key and does not append a new trace row.
        orchestrator.run_until_boundary(research_id)
        return self._result(
            branch=branch,
            research_id=research_id,
            record=record,
            trace=trace,
            calculation_executed=True,
            receipts=receipts,
            candidate_result_ref=candidate_ref,
            assurance_decision_ref=assurance_ref,
            next_route="accept-real-lca-calculation",
            scope_validation=scope_validation,
            calculation_validation=calculation_validation,
        )

    def verify(self, result: Mapping[str, object]) -> dict:
        errors: list[str] = []
        if result.get("contract") != RUN_CONTRACT:
            errors.append("run_contract_invalid")
        branch = result.get("branch")
        if branch not in BRANCH_FILES:
            errors.append("branch_invalid")
        research_id = result.get("research_id")
        try:
            record = ProjectRecord.open_or_create_project(
                self.workspace / "projects" / str(research_id)
            )
            replay = record.rebuild_and_verify()
            view = record.read_current_project_view()
        except (OSError, ValueError) as error:
            errors.append(f"project_record_replay_failed:{type(error).__name__}")
            replay = {}
            view = {}
        receipts = result.get("capability_receipts")
        if not isinstance(receipts, list):
            errors.append("capability_receipts_invalid")
            receipts = []
        if any(
            not isinstance(row, Mapping)
            or row.get("executed_external_writes") != 0
            for row in receipts
        ):
            errors.append("capability_external_write_detected")
        claim_ceiling = "candidate_only"
        if branch == "missing":
            claim_ceiling = "no_lca_result_claim_until_goal_scope_complete"
            if result.get("calculation_executed") is not False or receipts:
                errors.append("missing_branch_executed_runtime")
            if "controlled-calculation-candidate" in (view.get("facts") or {}):
                errors.append("missing_branch_contains_calculation_candidate")
        elif result.get("calculation_executed") is True:
            candidate = (view.get("facts") or {}).get(
                "controlled-calculation-candidate"
            )
            if not isinstance(candidate, Mapping):
                errors.append("calculation_candidate_absent")
            else:
                if candidate.get("classification") != "candidate":
                    errors.append("calculation_candidate_promoted")
                content = candidate.get("value", {}).get("calculation")  # type: ignore[union-attr]
                if not isinstance(content, Mapping):
                    errors.append("calculation_content_absent")
                else:
                    oracle = _load_json(
                        self.fixture_root / "oracle/calculation-oracle.json"
                    )
                    expected = oracle["expected_results"]["impacts"][0]
                    actual = content.get("result")
                    if not isinstance(actual, Mapping):
                        errors.append("calculation_result_absent")
                    else:
                        if actual.get("unit") != expected["unit"]:
                            errors.append("calculation_result_unit_drifted")
                        try:
                            difference = abs(
                                Decimal(str(actual.get("value")))
                                - Decimal(str(expected["value"]))
                            )
                            tolerance = max(
                                Decimal(oracle["numeric_tolerance"]["absolute"]),
                                abs(Decimal(str(expected["value"])))
                                * Decimal(oracle["numeric_tolerance"]["relative"]),
                            )
                            if difference > tolerance:
                                errors.append("calculation_result_value_drifted")
                        except Exception:
                            errors.append("calculation_result_value_invalid")
                    method = content.get("method")
                    if not isinstance(method, Mapping) or (
                        method.get("identity") != oracle["method_identity"]
                        or method.get("version") != oracle["method_version"]
                    ):
                        errors.append("calculation_method_identity_drifted")
        if view.get("result") is not None:
            errors.append("calculation_candidate_bypassed_formal_acceptance")
        if result.get("executed_external_writes") != 0:
            errors.append("run_external_write_detected")
        report = {
            "contract": (
                "lca_research_evaluation.controlled_lca_calculation_verification/v1"
            ),
            "status": "passed" if not errors else "failed",
            "errors": sorted(set(errors)),
            "branch": branch,
            "research_id": research_id,
            "project_record_replay": replay,
            "capability_receipt_count": len(receipts),
            "executed_external_writes": 0,
            "claim_ceiling": claim_ceiling,
        }
        report["verification_sha256"] = canonical_sha256(report)
        return report

    def _load_material(self, branch: str) -> tuple[dict, dict]:
        if branch not in BRANCH_FILES:
            raise ValueError("controlled calculation branch is unsupported")
        relative = BRANCH_FILES[branch]
        path = (self.fixture_root / relative).resolve()
        try:
            path.relative_to(self.fixture_root)
        except ValueError as error:
            raise ValueError("controlled calculation material path escaped") from error
        manifest = _load_json(self.fixture_root / "fixture-manifest.json")
        files = manifest.get("files")
        if not isinstance(files, Mapping):
            raise ValueError("controlled calculation fixture file registry is invalid")
        registered = dict(files)
        material = _load_json(path)
        digest = canonical_sha256(material)
        if registered.get(relative) != digest:
            raise ValueError("controlled calculation material hash drifted")
        if material.get("external_writes") != 0:
            raise ValueError("controlled calculation material permits external writes")
        return material, {"path": relative, "sha256": digest}

    def _submit_material(
        self,
        orchestrator: ResearchOrchestrator,
        *,
        material: Mapping[str, object],
        material_ref: Mapping[str, str],
        branch: str,
    ) -> None:
        candidate = {
            "contract": "lca_research_kernel.project_candidate_change/v1",
            "candidate_id": "controlled-calculation-material-"
            + material_ref["sha256"][:24],
            "change_type": "fact.upserted",
            "fact": {
                "fact_id": "research-source-material",
                "classification": "official",
                "domain_kind": "official_source_material",
                "value": deepcopy(dict(material)),
                "source": {
                    "authority": "tiangong_official_fixed_data",
                    "version": "content_addressed",
                    "content_sha256": material_ref["sha256"],
                    "source_refs": deepcopy(material["source_refs"]),
                    "branch": branch,
                },
            },
            "next_route": "validate-controlled-calculation-goal-scope",
            "proposed_at": self.clock(),
        }
        orchestrator.submit_candidate_change(
            candidate,
            idempotency_key=(
                "controlled-calculation-material:" + material_ref["sha256"]
            ),
        )

    def _load_execution_spec(
        self, material: Mapping[str, object]
    ) -> dict:
        matches = []
        for row in material.get("source_refs") or []:
            if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
                continue
            path = (self.repository_root / row["path"]).resolve()
            try:
                path.relative_to(self.repository_root)
            except ValueError as error:
                raise ValueError("calculation execution spec ref escaped") from error
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                isinstance(value, Mapping)
                and value.get("contract")
                == "lca_research_kernel.controlled_lca_calculation/v1"
            ):
                matches.append(deepcopy(dict(value)))
        if len(matches) != 1:
            raise ValueError("calculation execution spec is absent or ambiguous")
        return matches[0]

    def _submit_scope_assessment(
        self,
        orchestrator: ResearchOrchestrator,
        *,
        goal_scope: Mapping[str, object],
        scope_validation: Mapping[str, object],
        material_ref: Mapping[str, str],
    ) -> None:
        candidate = {
            "contract": "lca_research_kernel.project_candidate_change/v1",
            "candidate_id": "controlled-calculation-scope-"
            + canonical_sha256(
                {"goal_scope": goal_scope, "validation": scope_validation}
            )[:24],
            "change_type": "fact.upserted",
            "fact": {
                "fact_id": "controlled-calculation-goal-scope",
                "classification": (
                    "computed"
                    if scope_validation["status"] == "valid"
                    else "missing"
                ),
                "domain_kind": "lca_goal_and_scope_assessment",
                "value": {
                    "goal_scope": deepcopy(dict(goal_scope)),
                    "validation": deepcopy(dict(scope_validation)),
                },
                "source": {
                    "authority": "lca_domain_kernel",
                    "content_sha256": canonical_sha256(scope_validation),
                    "material_sha256": material_ref["sha256"],
                },
            },
            "next_route": (
                "validate-source-bound-tidas-inputs"
                if scope_validation["status"] == "valid"
                else "retain-bounded-missing-functional-unit-conclusion"
            ),
            "proposed_at": self.clock(),
        }
        orchestrator.submit_candidate_change(
            candidate,
            idempotency_key=(
                "controlled-calculation-scope:" + material_ref["sha256"]
            ),
        )

    def _submit_calculation_candidate(
        self,
        orchestrator: ResearchOrchestrator,
        *,
        calculation: Mapping[str, object],
        calculation_result: Mapping[str, object],
        calculation_validation: Mapping[str, object],
        material_ref: Mapping[str, str],
    ) -> dict:
        candidate = {
            "contract": "lca_research_kernel.project_candidate_change/v1",
            "candidate_id": "controlled-calculation-candidate-"
            + canonical_sha256(calculation_result)[:24],
            "change_type": "fact.upserted",
            "fact": {
                "fact_id": "controlled-calculation-candidate",
                "classification": "candidate",
                "domain_kind": "source_bound_lca_calculation",
                "value": {
                    "calculation": deepcopy(dict(calculation)),
                    "domain_validation": deepcopy(dict(calculation_validation)),
                    "claim_ceiling": "candidate_only",
                },
                "source": {
                    "authority": "capability_sourced_result",
                    "capability_id": calculation_result["capability_id"],
                    "content_sha256": calculation_result["content_sha256"],
                    "material_sha256": material_ref["sha256"],
                },
            },
            "next_route": "accept-real-lca-calculation",
            "proposed_at": self.clock(),
        }
        return orchestrator.submit_candidate_change(
            candidate,
            idempotency_key=(
                "controlled-calculation-candidate:"
                + calculation_result["content_sha256"]
            ),
        )

    def _goal_scope(self, material: Mapping[str, object]) -> dict:
        scientific = material["scientific_inputs"]
        method = scientific["impact_method"]
        goal_scope = {
            "research_goal": "Execute one exact source-bound native LCA calculation.",
            "lifecycle_stage": "upstream_analysis",
            "expected_use": "controlled_scientific_interpretation",
            "system_boundary": [
                "source-bound foreground processes",
                "exact provider-linked native graph",
            ],
            "method_basis": {
                "authority": "TianGong",
                "version": method["version"],
                "identity": method["id"],
            },
        }
        functional_unit = material.get("functional_unit")
        if isinstance(functional_unit, Mapping):
            goal_scope["functional_unit"] = deepcopy(dict(functional_unit))
        return goal_scope

    def _capability_receipt(
        self, outcome: Mapping[str, object], *, attempts: int
    ) -> dict:
        sourced = outcome["sourced_result"]
        return {
            "capability_id": sourced["capability_id"],
            "operation_id": outcome["operation_id"],
            "content_sha256": sourced["content_sha256"],
            "source_identity": sourced["source_identity"],
            "source_version": sourced["source_version"],
            "effect": sourced["effect"],
            "attempts": attempts,
            "executed_external_writes": sourced["executed_external_writes"],
        }

    def _result(
        self,
        *,
        branch: str,
        research_id: str,
        record: ProjectRecord,
        trace: RunTrace,
        calculation_executed: bool,
        receipts: list[dict],
        candidate_result_ref: Mapping[str, object] | None,
        assurance_decision_ref: Mapping[str, object] | None,
        next_route: str,
        scope_validation: Mapping[str, object],
        calculation_validation: Mapping[str, object] | None = None,
    ) -> dict:
        view = record.read_current_project_view()
        result = {
            "contract": RUN_CONTRACT,
            "research_id": research_id,
            "branch": branch,
            "calculation_executed": calculation_executed,
            "capability_receipts": deepcopy(receipts),
            "project_record_ref": {
                "last_event_id": view["last_event_id"],
                "last_event_sha256": view["last_event_sha256"],
            },
            "run_trace_ref": trace.reference(),
            "candidate_result_ref": deepcopy(candidate_result_ref),
            "assurance_decision_ref": deepcopy(assurance_decision_ref),
            "scope_validation": deepcopy(dict(scope_validation)),
            "calculation_validation": (
                deepcopy(dict(calculation_validation))
                if isinstance(calculation_validation, Mapping)
                else None
            ),
            "executed_external_writes": 0,
            "next_route": next_route,
        }
        result["run_sha256"] = canonical_sha256(result)
        return result

    def _failure_result(
        self,
        branch: str,
        research_id: str,
        record: ProjectRecord,
        trace: RunTrace,
        outcome: Mapping[str, object],
        scope_validation: Mapping[str, object],
    ) -> dict:
        failure = outcome.get("failure")
        return self._result(
            branch=branch,
            research_id=research_id,
            record=record,
            trace=trace,
            calculation_executed=False,
            receipts=[],
            candidate_result_ref=None,
            assurance_decision_ref=None,
            next_route=(
                str(failure.get("next_route"))
                if isinstance(failure, Mapping)
                else "repair-bounded-calculation-runtime"
            ),
            scope_validation=scope_validation,
        )

    def _validate_checkpoint(self, checkpoint: Mapping[str, object]) -> None:
        if (
            checkpoint.get("contract") != CHECKPOINT_CONTRACT
            or checkpoint.get("checkpoint") != "tidas_validation"
            or checkpoint.get("resume_route") != "resume-controlled-lca-calculation"
        ):
            raise ValueError("controlled calculation checkpoint is invalid")
