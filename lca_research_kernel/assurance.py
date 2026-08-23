"""Generic D-18 scientific, trajectory and responsibility assurance boundary."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path
from typing import Mapping, Sequence

from .lca_domain_kernel import LCADomainKernel
from .official_operation_identity import (
    official_public_source_operation_id,
)
from .content_identity import canonical_json_bytes, canonical_sha256
from .candidate_assurance import evaluate_candidate_change
from .project_record import ProjectRecord
from .responsibility_boundary import classify_operation, compile_handoff
from .material_sources import validate_source_bound_material_revision
from .answer_obligations import GovernedLCAKnowledge
from .research_turn import infer_response_language
from .research_turn import validate_research_turn_frame
from .research_program import validate_research_program


FAILURE_ROUTES = {
    "prompt": "restore-approved-goal-contract",
    "context": "rebuild-source-bound-context",
    "harness": "repair-or-switch-bounded-runtime",
    "loop": "select-next-evidence-bearing-action",
    "model": "retry-or-switch-model-provider",
}
class AssuranceBoundary:
    def __init__(self, domain_kernel: LCADomainKernel = None):
        self.domain_kernel = domain_kernel or LCADomainKernel()

    def authorize_action(self, operation_intent: Mapping[str, object]) -> dict:
        decision = classify_operation(operation_intent)
        result = {
            "contract": "lca_research_kernel.action_authorization/v1",
            "operation_id": operation_intent.get("operation_id"),
            "authorized": decision.disposition == "proceed",
            "disposition": decision.disposition,
            "category": decision.category,
            "reason": decision.reason,
            "intent_sha256": canonical_sha256(operation_intent),
            "executed_external_effects": 0,
        }
        if decision.disposition == "prepare_handoff":
            result["handoff"] = compile_handoff(operation_intent, decision)
        return result

    def evaluate_material_answer(
        self,
        model_candidate: Mapping[str, object],
        *,
        material_bundles: Sequence[Mapping[str, object]],
        use_decision: Mapping[str, object],
        project_record: ProjectRecord | None = None,
        material_use_decision_event_sha256: str | None = None,
        turn_frame: Mapping[str, object] | None = None,
        governed_knowledge: Sequence[Mapping[str, object]] = (),
        response_language: str | None = None,
        answer_quality_repair_exhausted: bool = False,
        research_program: Mapping[str, object] | None = None,
    ) -> dict:
        """Accept exact user evidence without granting TianGong authority."""

        typed_answer = model_candidate.get("contract") == (
            "lca_research_kernel.material_answer_candidate/v2"
        )
        context_error = None
        authoritative_frame = turn_frame
        authoritative_knowledge = list(governed_knowledge)
        authoritative_program = None
        if research_program is not None:
            try:
                authoritative_program = validate_research_program(
                    research_program
                )
                if (
                    not isinstance(turn_frame, Mapping)
                    or authoritative_program["turn_sha256"]
                    != turn_frame.get("turn_sha256")
                    or authoritative_program["evidence_plan"][
                        "use_decision_sha256"
                    ]
                    != canonical_sha256(use_decision)
                    or authoritative_program["requested_outcome"][
                        "response_language"
                    ]
                    != (response_language or "en")
                ):
                    raise ValueError("research program execution binding drifted")
                semantic_closure = authoritative_program.get(
                    "semantic_closure"
                )
                if isinstance(semantic_closure, Mapping):
                    knowledge_ids = sorted(
                        row["knowledge_id"]
                        for row in authoritative_knowledge
                        if isinstance(row, Mapping)
                        and isinstance(row.get("knowledge_id"), str)
                    )
                    if knowledge_ids != semantic_closure.get(
                        "selected_governed_knowledge_ids"
                    ):
                        raise ValueError(
                            "research program governed knowledge drifted"
                        )
            except ValueError:
                context_error = "research_program_authority_invalid"
        if typed_answer:
            try:
                (
                    authoritative_frame,
                    authoritative_knowledge,
                    response_language,
                ) = (
                    self._verify_typed_material_answer_context(
                        project_record=project_record,
                        material_use_decision_event_sha256=(
                            material_use_decision_event_sha256
                        ),
                        use_decision=use_decision,
                        turn_frame=turn_frame,
                        governed_knowledge=governed_knowledge,
                        response_language=response_language,
                    )
                )
            except ValueError:
                context_error = "material_answer_context_authority_invalid"
        if context_error is None:
            assessment = self.domain_kernel.evaluate_material_answer_candidate(
                model_candidate,
                material_bundles=material_bundles,
                use_decision=use_decision,
                turn_frame=authoritative_frame,
                governed_knowledge=authoritative_knowledge,
                response_language=response_language or "en",
                answer_quality_repair_exhausted=answer_quality_repair_exhausted,
                research_program=authoritative_program,
            )
        else:
            assessment = {
                "contract": "lca_research_kernel.material_answer_assessment/v2",
                "status": "rejected",
                "errors": [context_error],
                "scientific_status": "accepted_project_input",
                "claim_ceiling": "source_bounded_interpretation",
                "may_satisfy_tiangong_official_requirement": False,
                "may_promote_to_official": False,
                "citations": [],
                "admissible_candidate": None,
                "answer_quality": None,
            }
        normalized_bundles = []
        for bundle_value in material_bundles:
            try:
                normalized_bundles.append(
                    validate_source_bound_material_revision(bundle_value)
                )
            except ValueError:
                # The domain assessment already records the invalid bundle.
                pass
        accepted_locators = []
        if assessment["status"] == "passed":
            selected = set(use_decision["selected_evidence_locator_ids"])
            for bundle in normalized_bundles:
                for locator_value in bundle["locators"]:
                    if locator_value["locator_id"] not in selected:
                        continue
                    locator = deepcopy(locator_value)
                    acceptance_basis = {
                        "locator_id": locator["locator_id"],
                        "revision_id": locator["revision_id"],
                        "locator_kind": locator["locator_kind"],
                        "locator": deepcopy(locator["locator"]),
                        "content_hash": locator["content_hash"],
                        "scientific_status": "accepted_project_input",
                    }
                    digest = canonical_sha256(acceptance_basis)
                    locator["scientific_status"] = "accepted_evidence"
                    locator["assurance_acceptance_ref"] = {
                        "artifact_id": digest,
                        "sha256": digest,
                    }
                    accepted_locators.append(locator)
        policy_path = (
            Path(__file__).resolve().parent
            / "contracts"
            / "material-context-quality-v1.json"
        )
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        selected_ids = set(use_decision.get("selected_evidence_locator_ids") or [])
        candidate_id_values = model_candidate.get("citation_ids")
        candidate_ids_unique = (
            isinstance(candidate_id_values, list)
            and bool(candidate_id_values)
            and all(
                isinstance(locator_id, str)
                for locator_id in candidate_id_values
            )
            and len(candidate_id_values) == len(set(candidate_id_values))
        )
        candidate_ids = (
            set(candidate_id_values) if candidate_ids_unique else set()
        )
        expected_state_change = use_decision.get("expected_state_change") or {}
        declared_required_roles = expected_state_change.get("required_evidence_roles")
        if not isinstance(declared_required_roles, list) or any(
            not isinstance(role, str) or not role
            for role in declared_required_roles
        ):
            declared_required_roles = []
        required_roles = set(declared_required_roles)
        evidence_roles_by_locator = {
            str(binding["locator_id"]): {
                role
                for role in (binding["evidence_unit"].get("semantic_roles") or [])
                if isinstance(role, str) and role
            }
            for bundle in normalized_bundles
            for binding in bundle["evidence_unit_bindings"]
            if isinstance(binding, Mapping)
            and isinstance(binding.get("locator_id"), str)
            and isinstance(binding.get("evidence_unit"), Mapping)
        }
        covered_roles = {
            role
            for locator_id in selected_ids
            for role in evidence_roles_by_locator.get(locator_id, set())
        }
        dependency_coverage = (
            1.0
            if not required_roles
            else len(covered_roles.intersection(required_roles))
            / len(required_roles)
        )
        locator_hashes = {
            locator["locator_id"]: locator["content_hash"]
            for bundle in normalized_bundles
            for locator in bundle["locators"]
        }
        claim_bindings = model_candidate.get("claim_citation_bindings")
        bound_ids = []
        exact_claim_bindings = isinstance(claim_bindings, list) and bool(
            claim_bindings
        )
        if exact_claim_bindings:
            for binding in claim_bindings:
                if (
                    not isinstance(binding, Mapping)
                    or not isinstance(binding.get("claim_text"), str)
                    or not binding["claim_text"].strip()
                    or not isinstance(binding.get("citation_tokens"), list)
                    or not binding["citation_tokens"]
                    or len(binding["citation_tokens"])
                    != len(set(binding["citation_tokens"]))
                ):
                    exact_claim_bindings = False
                    break
                clause = binding["claim_text"].strip() + " " + " ".join(
                    binding["citation_tokens"]
                )
                if clause not in str(model_candidate.get("answer") or ""):
                    exact_claim_bindings = False
                    break
                for token in binding["citation_tokens"]:
                    if (
                        not isinstance(token, str)
                        or len(token) < 3
                        or token[0] != "["
                        or token[-1] != "]"
                    ):
                        exact_claim_bindings = False
                        break
                    bound_ids.append(token[1:-1])
        answer_locator_ids = re.findall(
            r"\[([^\[\]\s]+)\]", str(model_candidate.get("answer") or "")
        )
        expected_hashes = {
            locator_hashes[locator_id]
            for locator_id in candidate_ids
            if locator_id in locator_hashes
        }
        claims = model_candidate.get("claims")
        citation_not_applicable = (
            typed_answer
            and assessment["status"] == "passed"
            and isinstance(claims, list)
            and bool(claims)
            and all(
                isinstance(claim, Mapping)
                and claim.get("claim_kind")
                in {
                    "governed_method_explanation",
                    "illustrative_non_project_example",
                    "limitation",
                }
                for claim in claims
            )
            and candidate_id_values == []
            and claim_bindings == []
            and answer_locator_ids == []
            and model_candidate.get("scientific_source_sha256s") == []
        )
        citation_correctness = (
            1.0
            if citation_not_applicable
            or (
                candidate_ids_unique
                and candidate_ids
                and candidate_ids.issubset(selected_ids)
                and exact_claim_bindings
                # One selected locator may correctly support multiple claims.
                and set(bound_ids) == candidate_ids
                and Counter(answer_locator_ids) == Counter(bound_ids)
                and set(model_candidate.get("scientific_source_sha256s") or [])
                == expected_hashes
                and len(expected_hashes) == len(candidate_ids)
            )
            else 0.0
        )
        thresholds = {
            "minimum_dependency_coverage": policy["minimum_dependency_coverage"],
            "minimum_citation_correctness": policy["minimum_citation_correctness"],
        }
        quality_metrics = {
            "contract": "lca_research_kernel.material_context_quality_metrics/v1",
            "policy_id": policy["policy_id"],
            "dependency_coverage": dependency_coverage,
            "citation_correctness": citation_correctness,
            "basis": {
                "dependency_roles": "verified_evidence_unit_semantic_roles",
                "citations": (
                    "not_applicable_governed_knowledge_only"
                    if citation_not_applicable
                    else "claim_locator_and_content_hash_binding"
                ),
            },
            "thresholds": thresholds,
            "status": (
                "passed"
                if dependency_coverage >= thresholds["minimum_dependency_coverage"]
                and citation_correctness >= thresholds["minimum_citation_correctness"]
                else "failed"
            ),
        }
        if quality_metrics["status"] != "passed":
            errors = list(assessment["errors"])
            if dependency_coverage < thresholds["minimum_dependency_coverage"]:
                errors.append("dependency_coverage_below_threshold")
            if citation_correctness < thresholds["minimum_citation_correctness"]:
                errors.append("citation_correctness_below_threshold")
            assessment = {**assessment, "status": "failed", "errors": errors}
            accepted_locators = []
        answer_quality = assessment.get("answer_quality")
        repair_authorized = (
            isinstance(answer_quality, Mapping)
            and answer_quality.get("disposition") == "repair_once"
        )
        answer_repair_policy = {
            "contract": "lca_research_kernel.answer_repair_policy/v1",
            "authorized": repair_authorized,
            "maximum_repairs": 1 if repair_authorized else 0,
            "candidate_sha256": canonical_sha256(model_candidate),
            "evaluator": "assurance_boundary",
        }
        return {
            "contract": "lca_research_kernel.material_answer_acceptance/v2",
            "decision": (
                "accepted" if assessment["status"] == "passed" else "rejected"
            ),
            "candidate_sha256": canonical_sha256(model_candidate),
            "evaluator": "assurance_boundary",
            "errors": deepcopy(assessment["errors"]),
            "scientific_status": "accepted_project_input",
            "claim_ceiling": assessment["claim_ceiling"],
            "accepted_locators": accepted_locators,
            "may_satisfy_tiangong_official_requirement": False,
            "may_promote_candidate_to_official": False,
            "domain_assessment": assessment,
            "quality_metrics": quality_metrics,
            "answer_repair_policy": answer_repair_policy,
        }

    @staticmethod
    def _verify_typed_material_answer_context(
        *,
        project_record: ProjectRecord | None,
        material_use_decision_event_sha256: str | None,
        use_decision: Mapping[str, object],
        turn_frame: Mapping[str, object] | None,
        governed_knowledge: Sequence[Mapping[str, object]],
        response_language: str | None,
    ) -> tuple[dict, list[dict], str]:
        """Resolve every typed-answer authority input from the sole Project Record."""

        decision_id = use_decision.get("task_id")
        if (
            not isinstance(project_record, ProjectRecord)
            or not isinstance(material_use_decision_event_sha256, str)
            or not isinstance(decision_id, str)
            or not isinstance(turn_frame, Mapping)
        ):
            raise ValueError("typed material answer authority is unavailable")
        event = project_record.resolve_accepted_material_use_decision_event(
            material_use_decision_event_sha256,
            decision_id,
        )
        candidate = event.get("payload", {}).get("candidate", {})
        if candidate.get("material_use_decision") != use_decision:
            raise ValueError("typed material use decision drifted")

        view = project_record.read_current_project_view()
        facts = view.get("facts")
        active_fact = (
            facts.get("active-user-turn") if isinstance(facts, Mapping) else None
        )
        active_turn = (
            active_fact.get("value") if isinstance(active_fact, Mapping) else None
        )
        prior_sha256 = (
            active_turn.get("prior_result_sha256")
            if isinstance(active_turn, Mapping)
            else None
        )
        prior_result = (
            project_record.resolve_accepted_result(prior_sha256)
            if isinstance(prior_sha256, str)
            else None
        )
        authoritative_response_language = infer_response_language(
            active_turn.get("text") if isinstance(active_turn, Mapping) else None
        )
        if response_language != authoritative_response_language:
            raise ValueError("typed material answer response language drifted")
        frame_view = deepcopy(view)
        frame_view["last_event_sha256"] = material_use_decision_event_sha256
        authoritative_frame = validate_research_turn_frame(
            turn_frame,
            project_view=frame_view,
            prior_result=prior_result,
        )
        repository_root = Path(__file__).resolve().parents[1]
        expected_knowledge = GovernedLCAKnowledge.load(
            repository_root
        ).resolve_turn_slice(
            authoritative_frame,
            response_language=authoritative_response_language,
        )["slice"]["entries"]
        if list(governed_knowledge) != expected_knowledge:
            raise ValueError("typed material answer knowledge drifted")
        return (
            authoritative_frame,
            expected_knowledge,
            authoritative_response_language,
        )

    @staticmethod
    def accept_evidence_locator(
        locator_value: Mapping[str, object],
        *,
        bundle: Mapping[str, object],
    ) -> dict:
        """Bind one candidate locator to its exact verified evidence unit."""

        accepted = AssuranceBoundary.accept_evidence_locators(bundle=bundle)
        locator = accepted.get(locator_value.get("locator_id"))
        if locator is None:
            raise ValueError("evidence locator is not source-bound")
        original = next(
            (
                item
                for item in validate_source_bound_material_revision(bundle)["locators"]
                if item["locator_id"] == locator_value.get("locator_id")
            ),
            None,
        )
        if original != locator_value:
            raise ValueError("evidence locator is not source-bound")
        return locator

    @staticmethod
    def accept_evidence_locators(
        *, bundle: Mapping[str, object]
    ) -> dict[str, dict]:
        """Validate one source-bound bundle once and accept all its locators."""

        normalized_bundle = validate_source_bound_material_revision(bundle)
        accepted: dict[str, dict] = {}
        for raw_locator in normalized_bundle["locators"]:
            locator = deepcopy(raw_locator)
            acceptance_basis = {
                "locator_id": locator["locator_id"],
                "revision_id": locator["revision_id"],
                "locator_kind": locator["locator_kind"],
                "locator": deepcopy(locator["locator"]),
                "content_hash": locator["content_hash"],
                "scientific_status": "accepted_project_input",
            }
            digest = canonical_sha256(acceptance_basis)
            locator["scientific_status"] = "accepted_evidence"
            locator["assurance_acceptance_ref"] = {
                "artifact_id": digest,
                "sha256": digest,
            }
            accepted[locator["locator_id"]] = locator
        return accepted

    def evaluate_capability_catalog(
        self, summary: Mapping[str, object]
    ) -> dict:
        errors: list[str] = []
        if (
            not isinstance(summary, Mapping)
            or summary.get("contract")
            != "lca_research_kernel.capability_catalog_summary/v1"
        ):
            errors.append("unsupported_catalog_summary_contract")
        for key in ("catalog_id", "evaluated_at"):
            if not isinstance(summary.get(key), str) or not str(
                summary.get(key)
            ).strip():
                errors.append(f"{key}_absent")
        for key in ("catalog_snapshot_sha256", "source_lock_sha256"):
            value = summary.get(key)
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                errors.append(f"{key}_invalid")
        if (
            not isinstance(summary.get("capability_count"), int)
            or summary.get("capability_count", 0) < 1
        ):
            errors.append("catalog_has_no_capabilities")
        if summary.get("unclassified_count") != 0:
            errors.append("catalog_contains_unclassified_capabilities")
        return {
            "contract": "lca_research_kernel.capability_catalog_acceptance/v1",
            "decision": "accepted" if not errors else "rejected",
            "summary_sha256": canonical_sha256(summary),
            "evaluator": "assurance_boundary",
            "evaluated_at": summary.get("evaluated_at"),
            "errors": errors,
            "may_promote_capability_results_to_fact": False,
        }

    def evaluate_sourced_capability_result(
        self,
        result: Mapping[str, object],
        *,
        operation_intent: Mapping[str, object] = None,
        project_id: str = None,
        run_id: str = None,
    ) -> dict:
        errors: list[str] = []
        supported_contracts = {
            "lca_research_kernel.sourced_capability_result/v1",
            "lca_research_kernel.sourced_lca_package_capability_result/v1",
        }
        if (
            not isinstance(result, Mapping)
            or result.get("contract") not in supported_contracts
            or result.get("status") != "sourced_result_not_fact"
        ):
            errors.append("unsupported_sourced_capability_result")
        if result.get("executed_external_writes") != 0:
            errors.append("sourced_capability_result_has_external_write")
        if result.get("contract") == (
            "lca_research_kernel.sourced_lca_package_capability_result/v1"
        ):
            errors.append("native_lca_gateway_verification_required")
        if result.get("capability_id") == "official-public-source-read":
            content = result.get("content")
            receipt = (
                content.get("source_receipt")
                if isinstance(content, Mapping)
                else None
            )
            cache_ref = (
                content.get("cache_ref")
                if isinstance(content, Mapping)
                else None
            )
            receipt_hash = (
                receipt.get("body_sha256")
                if isinstance(receipt, Mapping)
                else None
            )
            if (
                not isinstance(receipt, Mapping)
                or receipt.get("contract")
                != "official_public_source_receipt/v1"
                or not isinstance(cache_ref, Mapping)
                or cache_ref.get("sha256") != receipt.get("body_sha256")
                or cache_ref.get("bytes") != receipt.get("body_bytes")
            ):
                errors.append("official_source_receipt_invalid")
            operation_payload = (
                operation_intent.get("payload")
                if isinstance(operation_intent, Mapping)
                else None
            )
            operation_payload_ref = (
                operation_intent.get("payload_ref")
                if isinstance(operation_intent, Mapping)
                else None
            )
            identity_binding_requested = any(
                value is not None
                for value in (operation_intent, project_id, run_id)
            )
            if identity_binding_requested and (
                not isinstance(operation_intent, Mapping)
                or operation_intent.get("target")
                != result.get("capability_id")
                or operation_intent.get("effect") != result.get("effect")
                or not isinstance(operation_payload, Mapping)
                or operation_payload.get("contract")
                != "official_public_source_request/v1"
                or operation_intent.get("operation_id")
                != official_public_source_operation_id(
                    operation_payload,
                    capability_id=str(result.get("capability_id")),
                )
                or not isinstance(operation_payload_ref, Mapping)
                or operation_payload_ref.get("sha256")
                != canonical_sha256(operation_payload)
                or not isinstance(project_id, str)
                or not project_id
                or not isinstance(run_id, str)
                or not run_id
                or operation_payload.get("question_id") != project_id
                or operation_payload.get("run_id") != run_id
                or not isinstance(receipt, Mapping)
                or receipt.get("question_id")
                != operation_payload.get("question_id")
                or receipt.get("run_id") != operation_payload.get("run_id")
                or receipt.get("operation_id")
                != operation_intent.get("operation_id")
                or result.get("request_id")
                != operation_intent.get("operation_id")
                or receipt.get("requested_url")
                != operation_payload.get("url")
                or receipt.get("parse_location")
                != operation_payload.get("parse_location")
                or receipt.get("extraction_request")
                != operation_payload.get("extraction")
            ):
                errors.append("official_source_operation_identity_drifted")
            excerpt = (
                content.get("content_excerpt")
                if isinstance(content, Mapping)
                else None
            )
            artifact_ref = (
                content.get("content_artifact_ref")
                if isinstance(content, Mapping)
                else None
            )
            if (
                isinstance(excerpt, Mapping)
                and excerpt.get("source_body_sha256")
                != receipt_hash
            ):
                errors.append("official_source_excerpt_drifted")
            if (
                isinstance(artifact_ref, Mapping)
                and artifact_ref.get("source_body_sha256")
                != receipt_hash
            ):
                errors.append("official_source_artifact_ref_drifted")
            if not isinstance(excerpt, Mapping) and not isinstance(
                artifact_ref, Mapping
            ):
                errors.append("official_source_readable_content_absent")
            if (
                isinstance(content, Mapping)
                and content.get("broad_claim_eligible") is True
            ):
                scan = content.get("exception_scan")
                if (
                    not isinstance(scan, Mapping)
                    or scan.get("contract")
                    != "official_source_exception_scan_result/v1"
                    or scan.get("basis") != "mechanical_body_scan"
                    or scan.get("scan_complete") is not True
                    or scan.get("source_body_sha256")
                    != receipt_hash
                ):
                    errors.append("official_source_exception_scan_absent")
            if isinstance(content, Mapping) and isinstance(
                content.get("extraction_request"), Mapping
            ):
                binding = content.get("extraction_binding")
                if (
                    not isinstance(binding, Mapping)
                    or binding.get("status") != "metadata_only_unverified"
                    or binding.get("may_support_numeric_claim") is not False
                    or binding.get("source_body_sha256")
                    != receipt_hash
                ):
                    errors.append("official_source_extraction_binding_invalid")
        return {
            "contract": "lca_research_kernel.sourced_capability_acceptance/v1",
            "decision": "accepted" if not errors else "rejected",
            "result_sha256": canonical_sha256(result),
            "evaluator": "assurance_boundary",
            "errors": errors,
            "may_promote_result_to_fact": False,
        }

    def evaluate_candidate_change(
        self,
        candidate: Mapping[str, object],
        project_view: Mapping[str, object],
        *,
        verified_content: Mapping[str, object] = None,
    ) -> dict:
        return evaluate_candidate_change(
            candidate,
            project_view,
            domain_kernel=self.domain_kernel,
            verified_content=verified_content,
        )

    def evaluate_local_artifact(
        self,
        path: Path,
        *,
        role: str,
        evaluated_at: str,
    ) -> dict:
        path = Path(path)
        errors = []
        if not path.is_file():
            errors.append("artifact_absent")
            digest = "0" * 64
        else:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if not isinstance(role, str) or not role.strip():
            errors.append("artifact_role_absent")
        if not isinstance(evaluated_at, str) or not evaluated_at.strip():
            errors.append("artifact_evaluated_at_absent")
        return {
            "contract": "lca_research_kernel.assurance_artifact_acceptance/v1",
            "decision": "accepted" if not errors else "rejected",
            "artifact_sha256": digest,
            "evaluator": "assurance_boundary",
            "evaluated_at": evaluated_at,
            "role": role,
            "errors": errors,
            "may_promote_artifact_content_to_official_fact": False,
        }

    def evaluate_trajectory(
        self,
        trace: Sequence[Mapping[str, object]],
        expected_policy: Mapping[str, object],
    ) -> dict:
        allowed_writers = set(
            expected_policy.get("accepted_project_record_writers") or []
        )
        errors: list[str] = []
        for item in trace:
            actor = item.get("actor")
            effect = item.get("effect")
            if effect == "accepted_project_record_write" and actor not in allowed_writers:
                errors.append("forbidden_project_record_writer")
            if effect in {"external_write", "irreversible"}:
                errors.append("forbidden_unapproved_effect")
        return {
            "contract": "lca_research_kernel.trajectory_evaluation/v1",
            "status": "passed" if not errors else "failed",
            "errors": sorted(set(errors)),
            "event_count": len(trace),
        }

    def evaluate_outcome(
        self,
        outcome: Mapping[str, object],
        lca_contract: Mapping[str, object],
    ) -> dict:
        errors: list[str] = []
        goal_scope = self.domain_kernel.validate_goal_and_scope(
            outcome.get("goal_scope")  # type: ignore[arg-type]
        )
        calculation = self.domain_kernel.validate_model_and_calculation(
            outcome.get("calculation")  # type: ignore[arg-type]
        )
        if goal_scope["status"] != "valid":
            errors.append("invalid_goal_scope")
        if calculation["status"] != "valid":
            errors.append("invalid_calculation")
        method_basis = outcome.get("goal_scope", {}).get("method_basis", {})  # type: ignore[union-attr]
        required_authority = lca_contract.get("required_authority")
        if required_authority and method_basis.get("authority") != required_authority:
            errors.append("authority_basis_mismatch")
        claim_boundary = self.domain_kernel.derive_claim_boundary(
            outcome.get("fact_classifications") or [],  # type: ignore[arg-type]
            decision_relevant_missing=outcome.get("decision_relevant_missing")
            or [],  # type: ignore[arg-type]
        )
        return {
            "contract": "lca_research_kernel.outcome_evaluation/v1",
            "status": "passed" if not errors else "failed",
            "errors": errors,
            "goal_scope": goal_scope,
            "calculation": calculation,
            "claim_boundary": claim_boundary,
        }

    def route_failure(self, failure: Mapping[str, object]) -> dict:
        layer = failure.get("layer")
        if layer not in FAILURE_ROUTES:
            layer = "harness"
            code = "unknown_failure_layer"
        else:
            code = failure.get("code")
        return {
            "contract": "lca_research_kernel.assurance_failure_route/v1",
            "layer": layer,
            "code": code,
            "next_route": FAILURE_ROUTES[layer],
            "may_promote_candidate_to_fact": False,
        }
