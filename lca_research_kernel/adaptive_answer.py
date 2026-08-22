"""State-driven answer planning and delivery policy for the D-18 runtime."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Mapping


CONTRACT = "adaptive_answer_quality_contract/v1"
DEFAULT_CONTRACT_PATH = Path("docs/adaptive-answer-quality-contract-v2.json")
RESEARCH_DEPTHS = [
    "direct",
    "targeted_source",
    "deterministic_tool",
    "independent_review",
    "parallel_research_optional",
]
DELIVERY_SHAPES = [
    "direct_brief",
    "evidence_table",
    "technical_explanation",
    "bounded_gap",
    "multi_artifact_package",
]
HARD_GATES = [
    "answers_user_task",
    "major_claims_evidence_or_calculation_bound",
    "source_identity_version_time_traceable",
    "numeric_unit_basis_exact",
    "fact_assumption_recommendation_gap_separated",
    "critical_gap_blocks_complete_conclusion",
    "claim_strength_within_evidence",
    "tiangong_official_identity_immutable",
    "assurance_required_for_formal_write",
    "zero_forbidden_effects",
]
INVALIDATION_ROUTES = [
    "rerun_affected_answer",
    "regrade_frozen_answer",
    "rerun_clean_acceptance",
    "rebuild_delivery_only",
    "unknown_change_fail_closed",
]
SCORE_ORDER = [
    "hard_gates",
    "action_value",
    "delivery_fit",
    "time_calls_cost",
]
DECISION_BASIS = [
    "project_record_state",
    "architecture_decision_frame",
    "expected_state_change",
    "evidence_topology",
    "allowed_capabilities",
]
EXPECTED_TOP_LEVEL_FIELDS = {
    "contract",
    "contract_sha256",
    "delivery_shapes",
    "evidence_invalidation_routes",
    "hard_gates",
    "next_routes",
    "research_depths",
    "routing_policy",
    "rubric_profiles",
    "score_order",
    "unknown_field_policy",
}
PLAN_STATE_FIELDS = {
    "project_record_ref",
    "state_sha256",
    "evidence_status",
    "missing_facts",
    "calculation_required",
    "evidence_conflicts",
    "independent_research_lanes",
    "high_value_parallelism",
}
FORBIDDEN_ROUTING_FIELDS = {
    "question_id",
    "case_name",
    "provider_name",
    "keywords",
    "task_text",
}


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def validate_adaptive_answer_contract(contract: Mapping[str, object]) -> dict:
    """Validate the stable adaptive-answer policy and reject silent expansion."""

    if not isinstance(contract, Mapping) or set(contract) != EXPECTED_TOP_LEVEL_FIELDS:
        raise ValueError("adaptive answer contract fields are invalid")
    normalized = deepcopy(dict(contract))
    if normalized["contract"] != CONTRACT:
        raise ValueError("adaptive answer contract identity drifted")
    if normalized["hard_gates"] != HARD_GATES:
        raise ValueError("adaptive answer hard gate registry drifted")
    if normalized["research_depths"] != RESEARCH_DEPTHS:
        raise ValueError("adaptive answer research depth registry drifted")
    if normalized["delivery_shapes"] != DELIVERY_SHAPES:
        raise ValueError("adaptive answer delivery shape registry drifted")
    if normalized["evidence_invalidation_routes"] != INVALIDATION_ROUTES:
        raise ValueError("adaptive answer invalidation routes drifted")
    if normalized["score_order"] != SCORE_ORDER:
        raise ValueError("adaptive answer score order drifted")
    if normalized["unknown_field_policy"] != "reject":
        raise ValueError("adaptive answer unknown fields must be rejected")
    routing = normalized["routing_policy"]
    if not isinstance(routing, Mapping) or dict(routing) != {
        "case_identity_matching": False,
        "decision_basis": DECISION_BASIS,
        "keyword_matching": False,
        "provider_name_matching": False,
    }:
        raise ValueError("adaptive answer routing policy drifted")
    profiles = normalized["rubric_profiles"]
    if not isinstance(profiles, Mapping) or set(profiles) != set(RESEARCH_DEPTHS):
        raise ValueError("adaptive answer rubric profiles drifted")
    for name, weights in profiles.items():
        if (
            not isinstance(weights, Mapping)
            or not weights
            or any(
                isinstance(weight, bool)
                or not isinstance(weight, int)
                or weight <= 0
                for weight in weights.values()
            )
            or sum(weights.values()) != 100
        ):
            raise ValueError(f"adaptive answer rubric weight is invalid: {name}")
    if normalized["next_routes"] != ["grade-frozen-development-answer-set"]:
        raise ValueError("adaptive answer requires exactly one next route")
    body = deepcopy(normalized)
    stored_hash = body.pop("contract_sha256")
    if not isinstance(stored_hash, str) or _canonical_sha256(body) != stored_hash:
        raise ValueError("adaptive answer contract self hash drifted")
    return normalized


class AdaptiveAnswerPolicy:
    """Compile state-driven research depth, rubric, claims and delivery shape."""

    def __init__(self, contract: Mapping[str, object]) -> None:
        self.contract = validate_adaptive_answer_contract(contract)
        self.contract_sha256 = str(self.contract["contract_sha256"])

    @classmethod
    def load(cls, root: Path, path: Path = DEFAULT_CONTRACT_PATH):
        root = Path(root).resolve()
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            value = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("adaptive answer contract is unreadable") from error
        if not isinstance(value, Mapping):
            raise ValueError("adaptive answer contract must be an object")
        return cls(value)

    def compile_plan(
        self,
        project_state: Mapping[str, object],
        decision_frame: Mapping[str, object],
        available_capabilities: list[str],
    ) -> tuple[dict, dict]:
        if not isinstance(project_state, Mapping) or set(project_state) != PLAN_STATE_FIELDS:
            forbidden = FORBIDDEN_ROUTING_FIELDS.intersection(project_state)
            suffix = f": {sorted(forbidden)}" if forbidden else ""
            raise ValueError(f"adaptive answer routing input is invalid{suffix}")
        if (
            not isinstance(decision_frame, Mapping)
            or decision_frame.get("contract")
            != "lca_research_kernel.architecture_decision_frame/v1"
            or not isinstance(decision_frame.get("expected_state_change"), Mapping)
        ):
            raise ValueError("adaptive answer decision frame is invalid")
        if (
            not isinstance(available_capabilities, list)
            or any(not isinstance(value, str) or not value for value in available_capabilities)
        ):
            raise ValueError("adaptive answer capabilities are invalid")
        selected_capabilities = decision_frame.get("selected_capabilities")
        if (
            not isinstance(selected_capabilities, list)
            or any(
                not isinstance(value, str) or not value
                for value in selected_capabilities
            )
            or not set(selected_capabilities).issubset(set(available_capabilities))
        ):
            raise ValueError("adaptive answer selected capabilities are invalid")
        for name in ("missing_facts", "evidence_conflicts"):
            if not isinstance(project_state[name], list):
                raise ValueError(f"adaptive answer {name} is invalid")
        if not isinstance(project_state["independent_research_lanes"], int):
            raise ValueError("adaptive answer independent research lanes are invalid")

        evidence_status = project_state["evidence_status"]
        if evidence_status == "critical_gap":
            depth = "direct"
            stop_condition = "return_bounded_gap"
        elif project_state["evidence_conflicts"]:
            depth = "independent_review"
            stop_condition = "resolve_or_preserve_dissent"
        elif (
            project_state["independent_research_lanes"] >= 2
            and project_state["high_value_parallelism"] is True
        ):
            depth = "parallel_research_optional"
            stop_condition = "integrate_independent_evidence"
        elif project_state["calculation_required"] is True:
            depth = "deterministic_tool"
            stop_condition = "produce_validated_calculation_candidate"
        elif evidence_status == "material_evidence_ready":
            depth = "targeted_source"
            stop_condition = "compose_from_selected_material_evidence"
        elif evidence_status == "source_confirmation_required":
            depth = "targeted_source"
            stop_condition = "bind_required_official_source"
        elif evidence_status == "sufficient":
            depth = "direct"
            stop_condition = "compose_from_accepted_evidence"
        else:
            raise ValueError("adaptive answer evidence status is unsupported")

        rubric = {
            "contract": "task_evaluation_rubric/v1",
            "research_depth": depth,
            "hard_gates": deepcopy(self.contract["hard_gates"]),
            "weights": deepcopy(self.contract["rubric_profiles"][depth]),
            "score_order": deepcopy(self.contract["score_order"]),
            "basis_sha256": _canonical_sha256(
                {
                    "project_state": dict(project_state),
                    "decision_frame_sha256": decision_frame.get("frame_sha256"),
                }
            ),
        }
        rubric["rubric_sha256"] = _canonical_sha256(rubric)
        plan = {
            "contract": "adaptive_answer_plan/v1",
            "contract_sha256": self.contract_sha256,
            "project_record_ref": deepcopy(project_state["project_record_ref"]),
            "project_state_sha256": project_state["state_sha256"],
            "decision_frame_sha256": decision_frame.get("frame_sha256"),
            "expected_state_change": deepcopy(
                dict(decision_frame["expected_state_change"])
            ),
            "research_depth": depth,
            "selected_capabilities": sorted(set(selected_capabilities)),
            "stop_condition": stop_condition,
            "rubric_sha256": rubric["rubric_sha256"],
        }
        plan["plan_sha256"] = _canonical_sha256(plan)
        return plan, rubric

    def compile_claim_set(
        self,
        accepted_result: Mapping[str, object],
        assurance_receipt: Mapping[str, object],
    ) -> dict:
        if not isinstance(assurance_receipt, Mapping) or assurance_receipt.get(
            "accepted"
        ) is not True:
            raise ValueError("Assurance acceptance is required for a Claim Set")
        receipt_sha256 = assurance_receipt.get("receipt_sha256")
        if not isinstance(receipt_sha256, str) or len(receipt_sha256) != 64:
            raise ValueError("Assurance receipt identity is invalid")
        if not isinstance(accepted_result, Mapping):
            raise ValueError("accepted result is invalid")
        normalized = {}
        for name in ("claims", "values", "missing_facts", "artifacts", "limitations"):
            value = accepted_result.get(name, [])
            if not isinstance(value, list):
                raise ValueError(f"accepted result {name} is invalid")
            normalized[name] = deepcopy(value)
        for claim in normalized["claims"]:
            if (
                not isinstance(claim, Mapping)
                or not isinstance(claim.get("statement"), str)
                or not isinstance(claim.get("source_refs"), list)
                or not claim.get("source_refs")
            ):
                raise ValueError("accepted claim is not source-bound")
        claim_set = {
            "contract": "adaptive_claim_set/v1",
            "assurance_receipt_sha256": receipt_sha256,
            **normalized,
        }
        claim_set["claim_set_sha256"] = _canonical_sha256(claim_set)
        return claim_set

    def compose_delivery_plan(self, claim_set: Mapping[str, object]) -> dict:
        if not isinstance(claim_set, Mapping) or claim_set.get("contract") != (
            "adaptive_claim_set/v1"
        ):
            raise ValueError("adaptive Claim Set is invalid")
        missing = claim_set.get("missing_facts")
        artifacts = claim_set.get("artifacts")
        values = claim_set.get("values")
        claims = claim_set.get("claims")
        if any(
            not isinstance(value, list)
            for value in (missing, artifacts, values, claims)
        ):
            raise ValueError("adaptive Claim Set collections are invalid")
        if any(
            isinstance(row, Mapping) and row.get("critical") is True
            for row in missing
        ):
            shape = "bounded_gap"
        elif len(artifacts) >= 2:
            shape = "multi_artifact_package"
        elif len(values) >= 2:
            shape = "evidence_table"
        elif len(claims) >= 3:
            shape = "technical_explanation"
        else:
            shape = "direct_brief"
        plan = {
            "contract": "adaptive_delivery_plan/v1",
            "claim_set_sha256": claim_set.get("claim_set_sha256"),
            "delivery_shape": shape,
            "section_policy": {
                "lead_with_conclusion": shape != "bounded_gap",
                "show_critical_gap_first": shape == "bounded_gap",
                "include_machine_artifact_refs": bool(artifacts),
                "render_internal_taxonomy": False,
                "render_escaped_newlines": False,
            },
        }
        plan["delivery_plan_sha256"] = _canonical_sha256(plan)
        return plan


def validate_frozen_answer_generations(
    plan_events: list[Mapping[str, object]],
    rubric_events: list[Mapping[str, object]],
) -> tuple[dict[str, tuple[dict, int]], dict[str, tuple[dict, int]]]:
    """Validate paired, content-addressed answer plans from one Run Trace."""

    if len(plan_events) != len(rubric_events):
        raise ValueError("adaptive answer plan and rubric generations drifted")
    frozen_rubrics: dict[str, tuple[dict, int]] = {}
    for event in rubric_events:
        payload = event.get("payload")
        frozen = payload.get("rubric") if isinstance(payload, Mapping) else None
        if (
            not isinstance(frozen, Mapping)
            or frozen.get("contract") != "task_evaluation_rubric/v1"
        ):
            raise ValueError("frozen adaptive answer contracts are invalid")
        unsigned = {
            key: value
            for key, value in frozen.items()
            if key != "rubric_sha256"
        }
        identity = frozen.get("rubric_sha256")
        sequence = event.get("sequence")
        if (
            not isinstance(identity, str)
            or identity != _canonical_sha256(unsigned)
            or identity in frozen_rubrics
            or isinstance(sequence, bool)
            or not isinstance(sequence, int)
        ):
            raise ValueError("frozen adaptive answer identity drifted")
        frozen_rubrics[identity] = (deepcopy(dict(frozen)), sequence)

    frozen_plans: dict[str, tuple[dict, int]] = {}
    referenced_rubrics: set[str] = set()
    for event in plan_events:
        payload = event.get("payload")
        frozen = payload.get("plan") if isinstance(payload, Mapping) else None
        if (
            not isinstance(frozen, Mapping)
            or frozen.get("contract") != "adaptive_answer_plan/v1"
        ):
            raise ValueError("frozen adaptive answer contracts are invalid")
        unsigned = {
            key: value for key, value in frozen.items() if key != "plan_sha256"
        }
        identity = frozen.get("plan_sha256")
        rubric_identity = frozen.get("rubric_sha256")
        sequence = event.get("sequence")
        if (
            not isinstance(identity, str)
            or identity != _canonical_sha256(unsigned)
            or identity in frozen_plans
            or rubric_identity not in frozen_rubrics
            or isinstance(sequence, bool)
            or not isinstance(sequence, int)
        ):
            raise ValueError("frozen adaptive answer identity drifted")
        rubric_sequence = frozen_rubrics[str(rubric_identity)][1]
        if rubric_sequence != sequence + 1:
            raise ValueError("adaptive answer plan and rubric generation split")
        frozen_plans[identity] = (deepcopy(dict(frozen)), sequence)
        referenced_rubrics.add(str(rubric_identity))
    if (
        len(frozen_plans) != len(frozen_rubrics)
        or referenced_rubrics != set(frozen_rubrics)
    ):
        raise ValueError("adaptive answer plan and rubric generations drifted")
    return frozen_plans, frozen_rubrics


def validate_answer_generation_order(
    events: list[Mapping[str, object]],
    frozen_plans: Mapping[str, tuple[Mapping[str, object], int]],
    frozen_rubrics: Mapping[str, tuple[Mapping[str, object], int]],
) -> None:
    """Prove each observed model attempt follows its exact frozen generation."""

    execution_events = [
        event
        for event in events
        if event.get("event_type")
        in {
            "research.run.model.requested",
            "research.run.capability.requested",
        }
    ]
    if execution_events and frozen_plans:
        first_frozen_sequence = min(
            max(sequence, frozen_rubrics[str(plan["rubric_sha256"])][1])
            for plan, sequence in frozen_plans.values()
        )
        if first_frozen_sequence >= min(
            int(event["sequence"]) for event in execution_events
        ):
            raise ValueError(
                "adaptive answer plan and rubric were not frozen before execution"
            )
    for event in execution_events:
        payload = event.get("payload")
        plan_identity = (
            payload.get("adaptive_plan_sha256")
            if isinstance(payload, Mapping)
            else None
        )
        legacy_implicit_binding = plan_identity is None
        if plan_identity is None:
            # Historical traces predate per-generation request binding. They
            # remain replayable only under the original single-generation rule,
            # but capability scope is still mechanically checked below.
            if len(frozen_plans) != 1:
                raise ValueError(
                    "execution request has no adaptive answer generation binding"
                )
            plan_identity = next(iter(frozen_plans))
        frozen = frozen_plans.get(str(plan_identity))
        if frozen is None:
            raise ValueError("execution adaptive answer generation is absent")
        plan, plan_sequence = frozen
        if not legacy_implicit_binding and payload.get(
            "adaptive_project_state_sha256"
        ) != plan.get("project_state_sha256"):
            raise ValueError("execution adaptive answer project state drifted")
        if event.get("event_type") == "research.run.capability.requested":
            selected_capabilities = plan.get("selected_capabilities")
            if (
                not isinstance(selected_capabilities, list)
                or payload.get("capability_id") not in selected_capabilities
            ):
                raise ValueError(
                    "capability is absent from its adaptive answer generation"
                )
        rubric_sequence = frozen_rubrics[str(plan["rubric_sha256"])][1]
        if max(plan_sequence, rubric_sequence) >= int(event["sequence"]):
            raise ValueError(
                "adaptive answer plan and rubric were not frozen before execution"
            )


__all__ = [
    "AdaptiveAnswerPolicy",
    "DELIVERY_SHAPES",
    "HARD_GATES",
    "RESEARCH_DEPTHS",
    "validate_adaptive_answer_contract",
    "validate_answer_generation_order",
    "validate_frozen_answer_generations",
]
