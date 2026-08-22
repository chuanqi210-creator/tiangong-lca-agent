"""Thin, case-neutral runner for one source-bound controlled evaluation."""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Callable, Mapping

from lca_research_kernel.agent_bootstrap import AgentBootstrap
from lca_research_kernel.context_assembler import ContextAssembler
from lca_research_kernel.execution_environment import ProjectExecutionEnvironment
from lca_research_kernel.lca_domain_kernel import LCADomainKernel
from lca_research_kernel.project_record import ProjectRecord, canonical_sha256
from lca_research_kernel.research_orchestrator import ResearchOrchestrator
from lca_research_kernel.run_control import DEFAULT_RUN_BUDGET, RunController
from lca_research_kernel.run_trace import RunTrace


RUN_CONTRACT = "lca_research_kernel.controlled_evaluation_run/v1"
MATERIAL_CONTRACTS = {
    "complete_material": "lca_research_kernel.controlled_evaluation_material/v1",
    "missing_critical_field": (
        "lca_research_kernel.controlled_evaluation_missing_material/v1"
    ),
}
MATERIAL_PATHS = {
    "complete_material": "fixture/material-package.json",
    "missing_critical_field": "fixture/missing-material-package.json",
}
REQUIRED_SOURCE_FIELDS = {
    "process_identity",
    "reference_flow",
    "method_results",
    "source_refs",
}
VISIBLE_TOP_LEVEL_KEYS = {
    "claim_ceiling",
    "commission",
    "contract",
    "expected_claim_ceiling",
    "fixture_id",
    "hidden_field",
    "source_material",
    "untrusted_content_instruction_effect",
}


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()


def _atomic_write(path: Path, value: object) -> None:
    content = _canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"controlled evaluation JSON is unreadable: {path}") from error
    if not isinstance(value, dict):
        raise ValueError("controlled evaluation JSON must be an object")
    return value


def _assert_no_hidden_labels(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if "oracle" in normalized or "grader" in normalized:
                raise ValueError("grader-only content is forbidden in model material")
            _assert_no_hidden_labels(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_hidden_labels(item)


def verify_controlled_evaluation_complete(
    workspace: Path,
    fixture_root: Path,
) -> dict:
    """Verify the one complete branch from immutable inputs and append-only evidence."""

    workspace = Path(workspace)
    fixture_root = Path(fixture_root)
    errors: list[str] = []
    result_path = workspace / "complete-run-result.json"
    result = _load_json(result_path)
    if result.get("contract") != RUN_CONTRACT:
        errors.append("complete_run_contract_invalid")
    if result.get("branch") != "complete_material":
        errors.append("complete_run_branch_invalid")
    if result.get("candidate_package_status") != "accepted_candidate":
        errors.append("complete_candidate_not_accepted")
    if result.get("formal_result_status") != "not_accepted":
        errors.append("candidate_was_promoted_to_formal_result")
    if result.get("executed_external_writes") != 0:
        errors.append("external_write_detected")
    research_id = result.get("research_id")
    if not isinstance(research_id, str):
        errors.append("complete_research_identity_absent")
        project = workspace / "projects" / "absent"
    else:
        project = workspace / "projects" / research_id
    try:
        record = ProjectRecord.open_or_create_project(project)
        replay = record.rebuild_and_verify()
        view = record.read_current_project_view()
    except (OSError, ValueError) as error:
        errors.append(f"project_record_replay_failed:{type(error).__name__}")
        replay = {}
        view = {}
    if view.get("result") is not None:
        errors.append("project_record_contains_formal_result")
    candidate_fact = (view.get("facts") or {}).get(
        "controlled-evaluation-candidate"
    )
    if (
        not isinstance(candidate_fact, Mapping)
        or candidate_fact.get("classification") != "candidate"
    ):
        errors.append("project_candidate_fact_absent")
    artifact_roles = {
        row.get("role")
        for row in (view.get("artifacts") or {}).values()
        if isinstance(row, Mapping)
    }
    expected_roles = {
        "controlled_evaluation_machine_package",
        "controlled_evaluation_reader_result_zh_cn",
    }
    if artifact_roles != expected_roles:
        errors.append("registered_artifact_roles_drifted")
    package_path = project / "artifacts/controlled-evaluation-machine-package.json"
    package = _load_json(package_path)
    visible = _load_json(fixture_root / "fixture/material-package.json")
    if package.get("official_material") != visible.get("source_material"):
        errors.append("official_material_drifted")
    rows = package.get("official_material", {}).get("method_results")  # type: ignore[union-attr]
    if not isinstance(rows, list) or len(rows) != 25:
        errors.append("official_result_count_drifted")
    model = package.get("model_candidate")
    if not isinstance(model, Mapping) or model.get("status") != "candidate_only":
        errors.append("model_candidate_boundary_drifted")
    elif model.get("tool_requests") != []:
        errors.append("model_requested_unnecessary_tool")
    if package.get("executed_tool_count") != 0:
        errors.append("tool_execution_detected")
    if package.get("executed_external_writes") != 0:
        errors.append("package_external_write_detected")
    if package.get("scientific_recalculation_performed") is not False:
        errors.append("scientific_recalculation_detected")
    if package.get("domain_acceptance", {}).get("status") != "passed":  # type: ignore[union-attr]
        errors.append("domain_acceptance_failed")
    context_events = package.get("context_events")
    if not isinstance(context_events, list) or len(context_events) != 1:
        errors.append("source_bound_context_event_absent")
    else:
        selected = set(context_events[0]["payload"].get("selected_identities") or [])
        if selected != {
            "project_fact:research-goal",
            "project_fact:research-source-material",
        }:
            errors.append("source_bound_context_selection_drifted")
        excluded = set(context_events[0]["payload"].get("excluded_identities") or [])
        if not any(identity.startswith("capability:") for identity in excluded):
            errors.append("irrelevant_capability_exclusion_absent")
    trace_path = (
        project
        / "run-traces"
        / "d18-controlled-evaluation-complete-v1.jsonl"
    )
    try:
        trace = RunTrace(
            trace_path,
            run_id="d18-controlled-evaluation-complete-v1",
        )
        trace_verification = trace.verify()
        events = trace.events()
    except (OSError, ValueError) as error:
        errors.append(f"run_trace_verification_failed:{type(error).__name__}")
        trace_verification = {}
        events = []
    if sum(
        row["event_type"] == "research.run.model.completed" for row in events
    ) != 1:
        errors.append("complete_model_call_count_invalid")
    if any(
        row["event_type"].startswith("research.run.capability.") for row in events
    ):
        errors.append("complete_capability_call_detected")
    reader_path = (
        project / "artifacts/controlled-evaluation-reader-result.zh-CN.md"
    )
    reader = reader_path.read_text(encoding="utf-8") if reader_path.is_file() else ""
    for phrase in ("限定范围", "候选", "不能"):
        if phrase not in reader:
            errors.append(f"reader_boundary_phrase_absent:{phrase}")
    report = {
        "contract": (
            "lca_research_kernel.controlled_evaluation_complete_acceptance/v1"
        ),
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "research_id": research_id,
        "project_record_replay": replay,
        "run_trace_verification": trace_verification,
        "official_result_count": len(rows) if isinstance(rows, list) else 0,
        "real_model_completed_count": sum(
            row["event_type"] == "research.run.model.completed"
            for row in events
        ),
        "capability_request_count": sum(
            row["event_type"] == "research.run.capability.requested"
            for row in events
        ),
        "executed_external_writes": 0,
        "claim_ceiling": "candidate_only",
        "next_route": "run-missing-data-counterfactual-and-recovery",
    }
    report["acceptance_sha256"] = canonical_sha256(report)
    return report


def snapshot_controlled_evaluation_recovery(workspace: Path) -> dict:
    workspace = Path(workspace)
    result = _load_json(workspace / "counterfactual-run-result.json")
    research_id = result.get("research_id")
    if not isinstance(research_id, str):
        raise ValueError("counterfactual research identity is absent")
    run_trace = result.get("run_trace")
    run_id = run_trace.get("run_id") if isinstance(run_trace, Mapping) else None
    if not isinstance(run_id, str):
        raise ValueError("counterfactual Run Trace identity is absent")
    project = workspace / "projects" / research_id
    record_path = project / ProjectRecord.EVENT_FILE
    trace_path = project / "run-traces" / f"{run_id}.jsonl"
    record_rows = [
        json.loads(line)
        for line in record_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    trace_rows = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    snapshot = {
        "contract": "lca_research_kernel.controlled_evaluation_recovery_snapshot/v1",
        "research_id": research_id,
        "run_id": run_id,
        "project_record_bytes_sha256": _sha256_bytes(record_path.read_bytes()),
        "run_trace_bytes_sha256": _sha256_bytes(trace_path.read_bytes()),
        "project_event_count": len(record_rows),
        "run_trace_event_count": len(trace_rows),
        "model_requested_count": sum(
            row["event_type"] == "research.run.model.requested"
            for row in trace_rows
        ),
        "model_completed_count": sum(
            row["event_type"] == "research.run.model.completed"
            for row in trace_rows
        ),
        "capability_requested_count": sum(
            row["event_type"] == "research.run.capability.requested"
            for row in trace_rows
        ),
        "registered_artifact_count": sum(
            row["event_type"] == "research.project.artifact_registered"
            for row in record_rows
        ),
        "executed_external_writes": 0,
    }
    snapshot["snapshot_sha256"] = canonical_sha256(snapshot)
    return snapshot


def verify_controlled_evaluation_counterfactual_recovery(
    workspace: Path,
    fixture_root: Path,
    before: Mapping[str, object],
) -> dict:
    workspace = Path(workspace)
    fixture_root = Path(fixture_root)
    errors: list[str] = []
    current = snapshot_controlled_evaluation_recovery(workspace)
    comparable = {
        key: value
        for key, value in before.items()
        if key != "snapshot_sha256"
    }
    observed = {
        key: value
        for key, value in current.items()
        if key != "snapshot_sha256"
    }
    if comparable != observed:
        errors.append("recovery_repeated_event_or_runtime_effect")
    result = _load_json(workspace / "counterfactual-run-result.json")
    if result.get("candidate_package_status") != "accepted_bounded_gap":
        errors.append("counterfactual_gap_not_accepted")
    if result.get("domain_acceptance", {}).get("claim_ceiling") != (  # type: ignore[union-attr]
        "bounded_missing_data_conclusion"
    ):
        errors.append("counterfactual_claim_ceiling_drifted")
    checkpoint = _load_json(workspace / "checkpoint-run-result.json")
    if checkpoint.get("candidate_package_status") != (
        "checkpointed_after_model_candidate"
    ):
        errors.append("interruption_checkpoint_absent")
    if checkpoint.get("project_record_sha256") == result.get(
        "project_record_sha256"
    ):
        errors.append("resume_did_not_accept_bounded_candidate")
    material = _load_json(
        fixture_root / "fixture/missing-material-package.json"
    )
    if "functional_unit" in (material.get("source_material") or {}):
        errors.append("counterfactual_hidden_field_restored")
    research_id = result.get("research_id")
    project = workspace / "projects" / str(research_id)
    package = _load_json(
        project / "artifacts/controlled-evaluation-machine-package.json"
    )
    output = package.get("model_candidate", {}).get("output")  # type: ignore[union-attr]
    if not isinstance(output, Mapping):
        errors.append("counterfactual_model_candidate_absent")
    else:
        if output.get("result_statements") != []:
            errors.append("counterfactual_contains_numeric_interpretation")
        normalized_missing = {
            str(item).strip().casefold().replace(" ", "_").replace("-", "_")
            for item in output.get("missing_fields") or []
        }
        if "functional_unit" not in normalized_missing:
            errors.append("counterfactual_functional_unit_gap_absent")
    if current["model_completed_count"] != 1:
        errors.append("counterfactual_model_call_count_invalid")
    if current["capability_requested_count"] != 0:
        errors.append("counterfactual_capability_call_detected")
    report = {
        "contract": (
            "lca_research_kernel.controlled_evaluation_counterfactual_recovery_acceptance/v1"
        ),
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "before_snapshot": deepcopy(dict(before)),
        "after_snapshot": current,
        "interruption_checkpoint_sha256": canonical_sha256(checkpoint),
        "counterfactual_result_sha256": canonical_sha256(result),
        "hidden_field": material.get("hidden_field"),
        "route_changed_by_missing_state": True,
        "real_call_growth_after_resume": 0,
        "capability_effect_growth_after_resume": 0,
        "project_event_growth_after_replay": 0,
        "executed_external_writes": 0,
        "next_route": "score-diagnose-and-finalize-controlled-evaluation",
    }
    report["acceptance_sha256"] = canonical_sha256(report)
    return report


class ControlledResearchEvaluation:
    """Compose existing D-18 ports without adding a product workflow."""

    def __init__(
        self,
        *,
        repository_root: Path,
        workspace: Path,
        fixture_root: Path,
        model_gateway: object,
        capability_gateway: object,
        assurance: object,
        provider_policy: Mapping[str, object],
        permission_policy: Mapping[str, object],
        run_id: str,
        clock: Callable[[], str],
    ):
        self.repository_root = Path(repository_root).resolve()
        self.workspace = Path(workspace).resolve()
        self.fixture_root = Path(fixture_root).resolve()
        self.model_gateway = model_gateway
        self.capability_gateway = capability_gateway
        self.assurance = assurance
        self.provider_policy = deepcopy(dict(provider_policy))
        self.permission_policy = deepcopy(dict(permission_policy))
        self.run_id = run_id
        self.clock = clock
        self.domain_kernel = LCADomainKernel()
        self.context_assembler = ContextAssembler()
        self._validate_policies()

    def run(
        self,
        branch: str,
        *,
        stop_after_model_candidate: bool = False,
    ) -> dict:
        material, material_ref = self._load_material(branch)
        manifest = self._run_manifest(branch, material, material_ref)
        research_id = manifest["research_id"]
        project_workspace = self.workspace / "projects" / research_id
        record = ProjectRecord.open_or_create_project(
            project_workspace,
            project_id=research_id,
            title="Controlled source-bound LCA interpretation",
            objective=material["commission"],
            created_at=self.clock(),
        )
        startup = AgentBootstrap.load(
            self.repository_root,
            self.repository_root
            / "lca_research_kernel/contracts/agent-startup-manifest-v2.json",
        )
        record.record_agent_startup(
            startup,
            run_id=self.run_id,
            observed_at=self.clock(),
        )
        trace = RunTrace(
            project_workspace / "run-traces" / f"{self.run_id}.jsonl",
            run_id=self.run_id,
        )
        execution_policy = _load_json(
            self.repository_root
            / "lca_research_kernel/contracts/project-execution-policy.json"
        )
        execution_environment = ProjectExecutionEnvironment.create(
            self.workspace,
            research_id,
            execution_policy,
        )
        model_gateway = self.model_gateway
        if hasattr(model_gateway, "with_execution_environment"):
            model_gateway = model_gateway.with_execution_environment(
                execution_environment
            )
        capability_gateway = self.capability_gateway
        if capability_gateway is not None and hasattr(
            capability_gateway, "with_execution_environment"
        ):
            capability_gateway = capability_gateway.with_execution_environment(
                execution_environment
            )
        orchestrator = ResearchOrchestrator(
            record,
            model_gateway=model_gateway,
            capability_gateway=capability_gateway,
            assurance=self.assurance,
            clock=self.clock,
            run_trace=trace,
            context_assembler=self.context_assembler,
            run_controller=RunController.restore(
                DEFAULT_RUN_BUDGET,
                run_id=self.run_id,
                trace=trace,
            ),
            execution_environment=execution_environment,
        )
        self._submit_goal(orchestrator, material, material_ref)
        self._submit_material(orchestrator, material, material_ref, branch)
        view = record.read_current_project_view()
        accepted_artifacts = {
            "artifacts/controlled-evaluation-machine-package.json",
            "artifacts/controlled-evaluation-reader-result.zh-CN.md",
        }
        if accepted_artifacts.issubset(set(view.get("artifacts") or {})):
            accepted_package = _load_json(
                record.workspace
                / "artifacts/controlled-evaluation-machine-package.json"
            )
            boundary = {
                "boundary": "awaiting_domain_and_capability_integration",
                "decision_frame": deepcopy(
                    accepted_package.get("decision_frame")
                ),
            }
        else:
            boundary = orchestrator.run_until_boundary(research_id)
            view = record.read_current_project_view()
        domain_acceptance = None
        result_decision_frame = deepcopy(boundary.get("decision_frame"))
        candidate_package_status = (
            "checkpointed_after_model_candidate"
            if stop_after_model_candidate
            and isinstance(view.get("facts", {}).get("model-analysis"), Mapping)
            else "not_evaluated"
        )
        if isinstance(view.get("facts", {}).get("model-analysis"), Mapping):
            domain_acceptance = self.domain_kernel.evaluate_source_bound_interpretation(
                material["source_material"],
                view["facts"]["model-analysis"]["value"],
            )
            if (
                domain_acceptance["status"] == "passed"
                and not stop_after_model_candidate
            ):
                self._accept_candidate_package(
                    orchestrator,
                    record,
                    trace,
                    material,
                    material_ref,
                    boundary,
                    domain_acceptance,
                )
                candidate_package_status = (
                    "accepted_candidate"
                    if branch == "complete_material"
                    else "accepted_bounded_gap"
                )
                view = record.read_current_project_view()
                accepted_package = _load_json(
                    record.workspace
                    / "artifacts/controlled-evaluation-machine-package.json"
                )
                result_decision_frame = deepcopy(
                    accepted_package.get("decision_frame")
                )
        _atomic_write(self.workspace / "control" / "run-manifest.json", manifest)
        result = {
            "contract": RUN_CONTRACT,
            "research_id": research_id,
            "branch": branch,
            "material_status": (
                "complete"
                if branch == "complete_material"
                else "critical_field_missing"
            ),
            "boundary": boundary["boundary"],
            "decision_frame": result_decision_frame,
            "model_result_status": (
                view.get("facts", {})
                .get("model-analysis", {})
                .get("classification", "absent")
            ),
            "formal_result_status": (
                "accepted" if view.get("result") is not None else "not_accepted"
            ),
            "candidate_package_status": candidate_package_status,
            "domain_acceptance": deepcopy(domain_acceptance),
            "project_record_sha256": view["last_event_sha256"],
            "run_trace": trace.verify(),
            "run_manifest": manifest,
            "executed_external_writes": 0,
        }
        return result

    def _accept_candidate_package(
        self,
        orchestrator: ResearchOrchestrator,
        record: ProjectRecord,
        trace: RunTrace,
        material: Mapping[str, object],
        material_ref: Mapping[str, str],
        boundary: Mapping[str, object],
        domain_acceptance: Mapping[str, object],
    ) -> None:
        view = record.read_current_project_view()
        expected_artifacts = {
            "artifacts/controlled-evaluation-machine-package.json",
            "artifacts/controlled-evaluation-reader-result.zh-CN.md",
        }
        if expected_artifacts.issubset(set(view.get("artifacts") or {})):
            record.rebuild_and_verify()
            return
        model_fact = view["facts"]["model-analysis"]
        model_trace_rows = [
            row
            for row in trace.events()
            if row["event_type"] == "research.run.model.completed"
        ]
        if len(model_trace_rows) != 1:
            raise ValueError("controlled evaluation requires one completed model call")
        model_candidate = model_trace_rows[0]["payload"]["candidate"]
        tool_count = sum(
            row["event_type"] == "research.run.capability.requested"
            for row in trace.events()
        )
        candidate = {
            "contract": "lca_research_kernel.project_candidate_change/v1",
            "candidate_id": (
                "controlled-evaluation-candidate-"
                + canonical_sha256(
                    {
                        "material_ref": material_ref,
                        "model_candidate": model_candidate,
                        "domain_acceptance": domain_acceptance,
                    }
                )[:24]
            ),
            "change_type": "fact.upserted",
            "fact": {
                "fact_id": "controlled-evaluation-candidate",
                "classification": "candidate",
                "domain_kind": "source_bound_lca_interpretation",
                "value": {
                    "model_output": deepcopy(model_fact["value"]),
                    "domain_acceptance": deepcopy(dict(domain_acceptance)),
                    "claim_ceiling": domain_acceptance["claim_ceiling"],
                },
                "source": {
                    "authority": "model_candidate_only",
                    "version": str(model_candidate["model"]),
                    "content_sha256": canonical_sha256(model_candidate),
                    "material_sha256": material_ref["sha256"],
                    "run_trace_ref": trace.reference(),
                },
            },
            "next_route": "grade-controlled-evaluation-candidate",
            "proposed_at": self.clock(),
        }
        orchestrator.submit_candidate_change(
            candidate,
            idempotency_key=(
                "controlled-evaluation-candidate:" + material_ref["sha256"]
            ),
        )
        artifact_root = record.workspace / "artifacts"
        package = {
            "contract": (
                "lca_research_kernel.controlled_evaluation_machine_package/v1"
            ),
            "fixture_id": material["fixture_id"],
            "commission": material["commission"],
            "material_ref": deepcopy(dict(material_ref)),
            "official_material": deepcopy(dict(material["source_material"])),
            "model_candidate": deepcopy(dict(model_candidate)),
            "domain_acceptance": deepcopy(dict(domain_acceptance)),
            "decision_frame": deepcopy(boundary.get("decision_frame")),
            "context_events": [
                deepcopy(row)
                for row in trace.events()
                if row["event_type"] == "research.run.context.assembled"
            ],
            "run_trace_ref": trace.reference(),
            "executed_tool_count": tool_count,
            "executed_external_writes": 0,
            "scientific_recalculation_performed": False,
            "claim_ceiling": "candidate_only",
        }
        machine_path = artifact_root / "controlled-evaluation-machine-package.json"
        _atomic_write(machine_path, package)
        reader_path = artifact_root / "controlled-evaluation-reader-result.zh-CN.md"
        reader_path.parent.mkdir(parents=True, exist_ok=True)
        reader_text = self._reader_result(package)
        content = reader_text.encode("utf-8")
        if not reader_path.is_file() or reader_path.read_bytes() != content:
            with tempfile.NamedTemporaryFile(
                dir=reader_path.parent, delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, reader_path)
        orchestrator.accept_local_artifact(
            "artifacts/controlled-evaluation-machine-package.json",
            role="controlled_evaluation_machine_package",
            idempotency_key=(
                "controlled-evaluation-machine-package:" + material_ref["sha256"]
            ),
        )
        orchestrator.accept_local_artifact(
            "artifacts/controlled-evaluation-reader-result.zh-CN.md",
            role="controlled_evaluation_reader_result_zh_cn",
            idempotency_key=(
                "controlled-evaluation-reader-result:" + material_ref["sha256"]
            ),
        )

    def _reader_result(self, package: Mapping[str, object]) -> str:
        material = package["official_material"]
        model = package["model_candidate"]["output"]
        functional_unit = material.get("functional_unit")
        result_count = len(material.get("method_results") or [])
        lines = [
            "# 限定范围的环境影响候选解读",
            "",
            "这是一份受控评测中的候选解读，所有数值仍以绑定的 TianGong 官方材料为准。",
            "",
            "## 结论边界",
            "",
            str(model["bounded_interpretation"]),
            "",
            (
                f"- 功能单位：{functional_unit.get('label')}"
                if isinstance(functional_unit, Mapping)
                else "- 功能单位缺失，因此不能形成完整数值解读。"
            ),
            f"- 已保留 {result_count} 项官方影响指标及其精确方法版本和单位。",
            "- 不同单位之间不能依据数值大小直接排序。",
            "- 零值不能解释为没有排放或没有环境影响。",
            "- 缺少贡献权重时，不能生成热点或贡献排序。",
            "",
            "## 主要限制",
            "",
        ]
        lines.extend(f"- {item}" for item in model.get("limitations") or [])
        lines.extend(
            [
                "",
                "本文件仅是来源绑定、经专业边界检查的候选结果，未替代 TianGong 官方事实。",
                "",
            ]
        )
        return "\n".join(lines)

    def _load_material(self, branch: str) -> tuple[dict, dict]:
        if branch not in MATERIAL_PATHS:
            raise ValueError("controlled evaluation branch is unsupported")
        path = (self.fixture_root / MATERIAL_PATHS[branch]).resolve()
        try:
            path.relative_to(self.fixture_root)
        except ValueError as error:
            raise ValueError("controlled evaluation material path escaped") from error
        material = _load_json(path)
        if material.get("contract") != MATERIAL_CONTRACTS[branch]:
            raise ValueError("controlled evaluation material contract drifted")
        unsupported = set(material) - VISIBLE_TOP_LEVEL_KEYS
        if unsupported:
            raise ValueError(
                "controlled evaluation material contains unsupported fields: "
                + ", ".join(sorted(unsupported))
            )
        _assert_no_hidden_labels(material)
        source = material.get("source_material")
        if not isinstance(source, Mapping):
            raise ValueError("controlled evaluation source material is absent")
        required = set(REQUIRED_SOURCE_FIELDS)
        if branch == "complete_material":
            required.add("functional_unit")
        missing = required - set(source)
        if missing:
            raise ValueError(
                "controlled evaluation source material fields are absent: "
                + ", ".join(sorted(missing))
            )
        path_bytes = path.read_bytes()
        relative = path.relative_to(self.repository_root).as_posix()
        manifest = _load_json(self.fixture_root / "fixture-manifest.json")
        rows = manifest.get("registered_artifacts")
        matched = [
            row
            for row in rows or []
            if isinstance(row, Mapping)
            and row.get("path") == MATERIAL_PATHS[branch]
        ]
        if (
            len(matched) != 1
            or matched[0].get("sha256") != _sha256_bytes(path_bytes)
        ):
            raise ValueError("controlled evaluation material hash drifted")
        return material, {
            "path": relative,
            "sha256": _sha256_bytes(path_bytes),
        }

    def _validate_policies(self) -> None:
        primary = self.provider_policy.get("primary_route")
        if (
            not isinstance(primary, Mapping)
            or primary.get("admission_state") != "active_verified"
            or not isinstance(primary.get("route_id"), str)
            or not isinstance(primary.get("adapter_id"), str)
        ):
            raise ValueError("controlled evaluation requires one active verified route")
        capabilities = self.model_gateway.capabilities()
        if primary["adapter_id"] not in capabilities:
            raise ValueError("controlled evaluation provider adapter is unavailable")
        allowed = self.permission_policy.get("allowed_effects")
        forbidden = self.permission_policy.get("forbidden_effects")
        if not isinstance(allowed, list) or not isinstance(forbidden, list):
            raise ValueError("controlled evaluation permission policy is invalid")
        if set(allowed) & set(forbidden):
            raise ValueError("controlled evaluation permission policy overlaps")
        if any(
            effect not in forbidden
            for effect in (
                "external_write",
                "publication",
                "lca_calculation_trigger",
            )
        ):
            raise ValueError("controlled evaluation forbidden effects are incomplete")

    def _run_manifest(
        self, branch: str, material: Mapping[str, object], material_ref: Mapping[str, str]
    ) -> dict:
        primary = deepcopy(dict(self.provider_policy["primary_route"]))
        fixture_id = str(material["fixture_id"])
        research_id = (
            "controlled-evaluation-"
            + canonical_sha256({"fixture_id": fixture_id, "branch": branch})[:20]
        )
        manifest = {
            "contract": "lca_research_kernel.controlled_evaluation_run_manifest/v1",
            "research_id": research_id,
            "fixture_id": fixture_id,
            "branch": branch,
            "material_ref": deepcopy(dict(material_ref)),
            "provider_route": primary,
            "failover_routes": deepcopy(
                self.provider_policy.get("failover_routes") or []
            ),
            "provider_policy_sha256": canonical_sha256(self.provider_policy),
            "permission_policy_sha256": canonical_sha256(self.permission_policy),
            "permissions": {
                "allowed_effects": sorted(
                    set(self.permission_policy["allowed_effects"])
                ),
                "forbidden_effects": sorted(
                    set(self.permission_policy["forbidden_effects"])
                ),
            },
            "oracle_access": "grader_only_not_loaded",
            "claim_ceiling": "candidate_only",
            "project_record_is_only_accepted_truth": True,
        }
        manifest["manifest_sha256"] = canonical_sha256(manifest)
        return manifest

    def _submit_goal(
        self,
        orchestrator: ResearchOrchestrator,
        material: Mapping[str, object],
        material_ref: Mapping[str, str],
    ) -> None:
        candidate = {
            "contract": "lca_research_kernel.project_candidate_change/v1",
            "candidate_id": "controlled-goal-" + material_ref["sha256"][:24],
            "change_type": "fact.upserted",
            "fact": {
                "fact_id": "research-goal",
                "classification": "official",
                "domain_kind": "research_commission",
                "value": material["commission"],
                "source": {
                    "authority": "user_approved_controlled_commission",
                    "version": "content_addressed",
                    "content_sha256": canonical_sha256(material["commission"]),
                },
            },
            "next_route": "classify-source-bound-material",
            "proposed_at": self.clock(),
        }
        orchestrator.submit_candidate_change(
            candidate,
            idempotency_key=f"controlled-goal:{material_ref['sha256']}",
        )

    def _submit_material(
        self,
        orchestrator: ResearchOrchestrator,
        material: Mapping[str, object],
        material_ref: Mapping[str, str],
        branch: str,
    ) -> None:
        value = deepcopy(dict(material["source_material"]))
        process_identity = value["process_identity"]
        source_refs = value["source_refs"]
        candidate = {
            "contract": "lca_research_kernel.project_candidate_change/v1",
            "candidate_id": "controlled-material-" + material_ref["sha256"][:24],
            "change_type": "fact.upserted",
            "fact": {
                "fact_id": "research-source-material",
                "classification": "official",
                "domain_kind": "official_source_material",
                "value": value,
                "source": {
                    "authority": "tiangong_official_fixed_data",
                    "version": process_identity["version"],
                    "content_sha256": material_ref["sha256"],
                    "source_refs": deepcopy(source_refs),
                    "material_contract": material["contract"],
                    "branch": branch,
                },
            },
            "next_route": "reason-from-source-bound-material",
            "proposed_at": self.clock(),
        }
        orchestrator.submit_candidate_change(
            candidate,
            idempotency_key=f"controlled-material:{branch}:{material_ref['sha256']}",
        )
