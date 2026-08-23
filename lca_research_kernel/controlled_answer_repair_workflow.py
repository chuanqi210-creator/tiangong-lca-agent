"""Private one-shot material-answer repair workflow for Research Orchestrator."""

from __future__ import annotations

from copy import deepcopy
from typing import Callable, Mapping, Sequence

from .answer_quality import (
    build_answer_claim_protocol,
    build_answer_repair_support_invariants,
    project_typed_material_answer_candidate,
    required_material_limitations,
    validate_answer_repair_support_invariants,
)
from .project_record import canonical_sha256
from .research_program import validate_research_program


class ControlledAnswerRepairWorkflow:
    """Execute the sole Assurance-authorized repair without owning truth."""

    def __init__(self, host: object):
        self._host = host

    def run(
        self,
        original_candidate: Mapping[str, object],
        *,
        material_bundles: Sequence[Mapping[str, object]],
        use_decision: Mapping[str, object],
        material_use_decision_event_sha256: str,
        turn_frame: Mapping[str, object],
        governed_knowledge: Sequence[Mapping[str, object]],
        response_language: str,
        output_schema: Mapping[str, object],
        research_program: Mapping[str, object],
        pre_attempt: Callable[
            [Mapping[str, object], Mapping[str, object]], None
        ]
        | None = None,
    ) -> dict:
        host = self._host
        if (
            not callable(getattr(host.run_trace, "append", None))
            or not isinstance(output_schema, Mapping)
        ):
            raise ValueError("answer repair controller ports are unavailable")
        program = self._validated_program(
            research_program,
            turn_frame=turn_frame,
            use_decision=use_decision,
            response_language=response_language,
        )
        locator_hashes = self._locator_hashes(material_bundles)
        projected_original = self._project_and_trace_original(
            original_candidate,
            locator_hashes=locator_hashes,
            response_language=response_language,
            governed_knowledge=governed_knowledge,
        )
        initial_acceptance = self._assess(
            projected_original,
            material_bundles=material_bundles,
            use_decision=use_decision,
            material_use_decision_event_sha256=(
                material_use_decision_event_sha256
            ),
            turn_frame=turn_frame,
            governed_knowledge=governed_knowledge,
            response_language=response_language,
            program=program,
            repair_exhausted=False,
        )
        quality = initial_acceptance.get("domain_assessment", {}).get(
            "answer_quality"
        )
        if not (
            isinstance(quality, Mapping)
            and quality.get("disposition") == "repair_once"
        ):
            return initial_acceptance
        repair_policy = self._authorized_policy(
            initial_acceptance,
            projected_original,
            quality,
        )
        support_invariants, repair_basis = self._repair_basis(
            projected_original,
            quality,
            turn_frame=turn_frame,
            use_decision=use_decision,
            governed_knowledge=governed_knowledge,
        )
        selected_evidence = self._selected_evidence(
            material_bundles,
            use_decision,
            program,
        )
        repair_context = self._repair_context(
            projected_original,
            quality,
            support_invariants=support_invariants,
            selected_evidence=selected_evidence,
            material_bundles=material_bundles,
            use_decision=use_decision,
            turn_frame=turn_frame,
            governed_knowledge=governed_knowledge,
            response_language=response_language,
            program=program,
        )
        request = self._model_request(
            repair_context,
            output_schema=output_schema,
            use_decision=use_decision,
            material_use_decision_event_sha256=(
                material_use_decision_event_sha256
            ),
            program=program,
        )
        self._trace_requested(repair_basis, repair_policy)
        repaired_candidate = host.model_gateway.reason(
            deepcopy(dict(request)),
            trace=host.run_trace,
            pre_attempt=pre_attempt,
        )
        host._assert_model_candidate_request_binding(repaired_candidate, request)
        repaired_output = repaired_candidate.get("output")
        if not isinstance(repaired_output, Mapping):
            raise ValueError("material answer repair candidate output is absent")
        projected_repair, repaired_acceptance, repair_failure = (
            self._evaluate_repaired_output(
                repaired_output,
                original_candidate=original_candidate,
                support_invariants=support_invariants,
                locator_hashes=locator_hashes,
                material_bundles=material_bundles,
                use_decision=use_decision,
                material_use_decision_event_sha256=(
                    material_use_decision_event_sha256
                ),
                turn_frame=turn_frame,
                governed_knowledge=governed_knowledge,
                response_language=response_language,
                program=program,
            )
        )
        self._trace_completed(
            repair_basis,
            projected_repair,
            repaired_acceptance,
            repair_failure=repair_failure,
        )
        return repaired_acceptance

    @staticmethod
    def _validated_program(
        research_program: Mapping[str, object],
        *,
        turn_frame: Mapping[str, object],
        use_decision: Mapping[str, object],
        response_language: str,
    ) -> dict:
        program = validate_research_program(research_program)
        if (
            program["turn_sha256"] != turn_frame.get("turn_sha256")
            or program["project_state_sha256"]
            != turn_frame.get("project_state_sha256")
            or program["requested_outcome"]["response_language"]
            != response_language
            or program["evidence_plan"]["use_decision_sha256"]
            != canonical_sha256(use_decision)
            or program["evidence_plan"]["selected_evidence_locator_ids"]
            != sorted(use_decision.get("selected_evidence_locator_ids") or [])
        ):
            raise ValueError("answer repair research program drifted")
        return program

    @staticmethod
    def _locator_hashes(
        material_bundles: Sequence[Mapping[str, object]],
    ) -> dict[str, str]:
        return {
            binding["locator_id"]: binding["content_hash"]
            for bundle in material_bundles
            for binding in (bundle.get("evidence_unit_bindings") or [])
            if isinstance(binding, Mapping)
            and isinstance(binding.get("locator_id"), str)
            and isinstance(binding.get("content_hash"), str)
        }

    def _project_and_trace_original(
        self,
        original_candidate: Mapping[str, object],
        *,
        locator_hashes: Mapping[str, str],
        response_language: str,
        governed_knowledge: Sequence[Mapping[str, object]],
    ) -> dict:
        projected, changed = project_typed_material_answer_candidate(
            original_candidate,
            locator_hashes=locator_hashes,
            response_language=response_language,
            governed_knowledge=governed_knowledge,
        )
        payload = {
            "contract": (
                "lca_research_kernel.material_candidate_projection_observation/v1"
            ),
            "input_candidate_sha256": canonical_sha256(original_candidate),
            "projected_candidate_sha256": canonical_sha256(projected),
            "projection_changed": changed,
            "projection_basis": "typed_claims_and_citation_tokens",
            "candidate_only": True,
            "project_truth_effect": "none",
        }
        self._host.run_trace.append(
            "research.run.material.candidate_projected",
            payload,
            idempotency_key=(
                "material-candidate-projection:"
                + payload["input_candidate_sha256"]
            ),
            occurred_at=self._host.clock(),
        )
        return projected

    def _assess(
        self,
        candidate: Mapping[str, object],
        *,
        material_bundles: Sequence[Mapping[str, object]],
        use_decision: Mapping[str, object],
        material_use_decision_event_sha256: str,
        turn_frame: Mapping[str, object],
        governed_knowledge: Sequence[Mapping[str, object]],
        response_language: str,
        program: Mapping[str, object],
        repair_exhausted: bool,
    ) -> dict:
        return self._host.assurance.evaluate_material_answer(
            candidate,
            material_bundles=deepcopy(list(material_bundles)),
            use_decision=deepcopy(dict(use_decision)),
            project_record=self._host.project_record,
            material_use_decision_event_sha256=(
                material_use_decision_event_sha256
            ),
            turn_frame=deepcopy(dict(turn_frame)),
            governed_knowledge=deepcopy(list(governed_knowledge)),
            response_language=response_language,
            answer_quality_repair_exhausted=repair_exhausted,
            research_program=program,
        )

    @staticmethod
    def _authorized_policy(
        acceptance: Mapping[str, object],
        projected_original: Mapping[str, object],
        quality: Mapping[str, object],
    ) -> dict:
        policy = acceptance.get("answer_repair_policy")
        if (
            not isinstance(policy, Mapping)
            or policy.get("contract")
            != "lca_research_kernel.answer_repair_policy/v1"
            or policy.get("authorized") is not True
            or policy.get("maximum_repairs") != 1
            or policy.get("evaluator") != "assurance_boundary"
            or policy.get("candidate_sha256")
            != canonical_sha256(projected_original)
            or quality.get("disposition") != "repair_once"
            or not isinstance(quality.get("repair_instructions"), list)
            or not quality["repair_instructions"]
        ):
            raise ValueError("answer repair is not authorized by Assurance")
        return deepcopy(dict(policy))

    @staticmethod
    def _repair_basis(
        projected_original: Mapping[str, object],
        quality: Mapping[str, object],
        *,
        turn_frame: Mapping[str, object],
        use_decision: Mapping[str, object],
        governed_knowledge: Sequence[Mapping[str, object]],
    ) -> tuple[dict, dict]:
        support = build_answer_repair_support_invariants(
            projected_original.get("claims"),
            selected_locator_ids=list(
                use_decision.get("selected_evidence_locator_ids") or []
            ),
            governed_knowledge_ids=[
                row["knowledge_id"]
                for row in governed_knowledge
                if isinstance(row, Mapping)
                and isinstance(row.get("knowledge_id"), str)
            ],
        )
        basis = {
            "turn_frame_sha256": turn_frame.get("frame_sha256"),
            "material_use_decision_sha256": canonical_sha256(use_decision),
            "selected_evidence_locator_ids": deepcopy(
                use_decision.get("selected_evidence_locator_ids")
            ),
            "allowed_revision_ids": deepcopy(
                use_decision.get("allowed_revision_ids")
            ),
            "claim_ceiling": use_decision.get("claim_ceiling"),
            "original_candidate_sha256": canonical_sha256(projected_original),
            "repair_instructions": deepcopy(quality["repair_instructions"]),
            "support_invariants_sha256": support["invariants_sha256"],
        }
        return support, basis

    @staticmethod
    def _selected_evidence(
        material_bundles: Sequence[Mapping[str, object]],
        use_decision: Mapping[str, object],
        program: Mapping[str, object],
    ) -> list[dict]:
        selected_ids = set(
            use_decision.get("selected_evidence_locator_ids") or []
        )
        evidence = [
            {
                "material_id": bundle["research_material"]["material_id"],
                "revision_id": bundle["material_revision"]["revision_id"],
                "locator_id": binding["locator_id"],
                "content_hash": binding["content_hash"],
                "source_span": deepcopy(binding["source_span"]),
                "evidence_unit": deepcopy(binding["evidence_unit"]),
                "trust": "untrusted_content",
                "instruction_effect": "none",
            }
            for bundle in material_bundles
            for binding in (bundle.get("evidence_unit_bindings") or [])
            if isinstance(binding, Mapping)
            and binding.get("locator_id") in selected_ids
        ]
        if {row["locator_id"] for row in evidence} != set(
            program["evidence_plan"]["selected_evidence_locator_ids"]
        ):
            raise ValueError("answer repair program evidence is unavailable")
        return evidence

    @staticmethod
    def _repair_context(
        projected_original: Mapping[str, object],
        quality: Mapping[str, object],
        *,
        support_invariants: Mapping[str, object],
        selected_evidence: Sequence[Mapping[str, object]],
        material_bundles: Sequence[Mapping[str, object]],
        use_decision: Mapping[str, object],
        turn_frame: Mapping[str, object],
        governed_knowledge: Sequence[Mapping[str, object]],
        response_language: str,
        program: Mapping[str, object],
    ) -> dict:
        return {
            "contract": (
                "lca_research_kernel.material_answer_repair_model_context/v1"
            ),
            "objective": program["requested_outcome"]["objective"],
            "turn_semantics": {
                field: deepcopy(turn_frame.get(field))
                for field in (
                    "frame_sha256",
                    "interaction_kind",
                    "cognitive_operation",
                    "referent",
                    "answer_obligations",
                    "required_knowledge_roles",
                    "claim_ceiling",
                    "stop_condition",
                )
                if field in turn_frame
            },
            "original_candidate": deepcopy(projected_original),
            "repair_instructions": deepcopy(quality["repair_instructions"]),
            "selected_evidence_units": deepcopy(list(selected_evidence)),
            "governed_knowledge": deepcopy(list(governed_knowledge)),
            "response_language": response_language,
            "required_limitations": required_material_limitations(
                use_decision,
                response_language=response_language,
            ),
            "processing_boundary": {
                "requires_limitation": any(
                    isinstance(bundle, Mapping)
                    and isinstance(bundle.get("material_revision"), Mapping)
                    and bundle["material_revision"].get("processing_status")
                    in {"partial", "needs_user"}
                    for bundle in material_bundles
                )
            },
            "answer_claim_protocol": build_answer_claim_protocol(
                turn_frame=turn_frame,
                selected_locator_ids=list(
                    use_decision.get("selected_evidence_locator_ids") or []
                ),
                response_language=response_language,
            ),
            "support_invariants": deepcopy(dict(support_invariants)),
            "invariants": {
                "selected_evidence_locator_ids": deepcopy(
                    use_decision.get("selected_evidence_locator_ids")
                ),
                "claim_ceiling": use_decision.get("claim_ceiling"),
                "tools_allowed": False,
                "external_effects_allowed": False,
            },
        }

    def _model_request(
        self,
        repair_context: Mapping[str, object],
        *,
        output_schema: Mapping[str, object],
        use_decision: Mapping[str, object],
        material_use_decision_event_sha256: str,
        program: Mapping[str, object],
    ) -> dict:
        host = self._host
        package = host.context_assembler.typed_package(
            research_program=program,
            project_id=str(
                host.project_record.read_current_project_view()["project_id"]
            ),
            model_context=repair_context,
            material_authorization_ref={
                "contract": (
                    "lca_research_kernel.material_authorization_ref/v1"
                ),
                "decision_id": str(use_decision.get("task_id")),
                "decision_sha256": canonical_sha256(use_decision),
                "project_record_event_sha256": (
                    material_use_decision_event_sha256
                ),
            },
        )
        return {
            "contract": "lca_research_kernel.model_request/v2",
            "request_id": "material-answer-repair-"
            + package["package_sha256"][:24],
            "task": (
                "Repair only the listed observable answer obligations. Keep "
                "the exact accepted turn frame, evidence locators, governed "
                "knowledge references, claim ceiling, candidate-only status, "
                "and zero-effect boundary. Follow context.research_program as "
                "the execution contract and answer_claim_protocol: each "
                "obligation must be assigned only to its compatible claim "
                "kind, and source evidence must never be combined with "
                "governed knowledge. Copy context.required_limitations exactly. "
                "When context.processing_boundary.requires_limitation is true "
                "and that list is empty, retain one explicit processing limit. "
                "Preserve every existing claim's support identity exactly as "
                "context.support_invariants requires. Return the exact typed "
                "material answer schema without hidden reasoning. Write every "
                "user-visible claim in context.response_language and never "
                "expose internal identifiers as prose."
            ),
            "context": package,
            "output_schema": deepcopy(dict(output_schema)),
            "tools": [],
            "required_capabilities": [
                "structured_output",
                "material_answer_v2",
            ],
        }

    def _trace_requested(
        self,
        repair_basis: Mapping[str, object],
        repair_policy: Mapping[str, object],
    ) -> None:
        payload = {
            "contract": (
                "lca_research_kernel.material_answer_repair_observation/v2"
            ),
            **deepcopy(dict(repair_basis)),
            "repair_number": 1,
            "maximum_repairs": 1,
            "repair_policy": deepcopy(dict(repair_policy)),
            "candidate_only": True,
            "project_truth_effect": "none",
        }
        self._host.run_trace.append(
            "research.run.material.answer_repair_requested",
            payload,
            idempotency_key=(
                "material-answer-repair-requested:" + canonical_sha256(payload)
            ),
            occurred_at=self._host.clock(),
        )

    def _evaluate_repaired_output(
        self,
        repaired_output: Mapping[str, object],
        *,
        original_candidate: Mapping[str, object],
        support_invariants: Mapping[str, object],
        locator_hashes: Mapping[str, str],
        material_bundles: Sequence[Mapping[str, object]],
        use_decision: Mapping[str, object],
        material_use_decision_event_sha256: str,
        turn_frame: Mapping[str, object],
        governed_knowledge: Sequence[Mapping[str, object]],
        response_language: str,
        program: Mapping[str, object],
    ) -> tuple[dict, dict, str | None]:
        def assess(candidate: Mapping[str, object]) -> tuple[dict, dict]:
            projected, _ = project_typed_material_answer_candidate(
                candidate,
                locator_hashes=locator_hashes,
                response_language=response_language,
                governed_knowledge=governed_knowledge,
            )
            acceptance = self._assess(
                projected,
                material_bundles=material_bundles,
                use_decision=use_decision,
                material_use_decision_event_sha256=(
                    material_use_decision_event_sha256
                ),
                turn_frame=turn_frame,
                governed_knowledge=governed_knowledge,
                response_language=response_language,
                program=program,
                repair_exhausted=True,
            )
            return projected, acceptance

        try:
            validate_answer_repair_support_invariants(
                repaired_output.get("claims"),
                support_invariants,
            )
        except ValueError:
            projected = deepcopy(dict(repaired_output))
            _, acceptance = assess(original_candidate)
            admissible = acceptance.get("domain_assessment", {}).get(
                "admissible_candidate"
            )
            if (
                acceptance.get("decision") != "accepted"
                or not isinstance(admissible, Mapping)
                or admissible.get("status")
                != "formal_limited_result_accepted"
            ):
                raise ValueError("material answer repair fallback was not bounded")
            return projected, acceptance, (
                "answer_repair_support_invariants_drifted"
            )
        projected, acceptance = assess(repaired_output)
        return projected, acceptance, None

    def _trace_completed(
        self,
        repair_basis: Mapping[str, object],
        projected_repair: Mapping[str, object],
        repaired_acceptance: Mapping[str, object],
        *,
        repair_failure: str | None,
    ) -> None:
        quality = repaired_acceptance.get("domain_assessment", {}).get(
            "answer_quality"
        )
        payload = {
            "contract": (
                "lca_research_kernel.material_answer_repair_observation/v2"
            ),
            **deepcopy(dict(repair_basis)),
            "repair_number": 1,
            "maximum_repairs": 1,
            "repaired_candidate_sha256": canonical_sha256(projected_repair),
            "disposition": (
                quality.get("disposition")
                if isinstance(quality, Mapping)
                else "bounded_reject"
            ),
            "candidate_only": True,
            "project_truth_effect": "none",
        }
        if repair_failure is not None:
            payload["repair_failure"] = repair_failure
        self._host.run_trace.append(
            "research.run.material.answer_repair_completed",
            payload,
            idempotency_key=(
                "material-answer-repair-completed:" + canonical_sha256(payload)
            ),
            occurred_at=self._host.clock(),
        )
