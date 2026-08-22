"""Case-neutral interaction routing inside Product Interface/Orchestrator."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Mapping


POLICY_CONTRACT = "lca_research_kernel.interaction_route_policy/v1"
REQUEST_CONTRACT = "lca_research_kernel.interaction_route_request/v1"
ROUTE_CONTRACT = "lca_research_kernel.interaction_route/v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ROUTE_IDS = (
    "direct_answer",
    "targeted_source",
    "deterministic_tool",
    "research_loop",
    "project_followup",
    "awaiting_user_data",
    "human_action_required",
    "temporarily_unavailable",
)
INTERACTION_KINDS = ("initial", "project_followup", "resume")
DEPTH_ROUTES = {
    "direct": "direct_answer",
    "targeted_source": "targeted_source",
    "deterministic_tool": "deterministic_tool",
    "independent_review": "research_loop",
    "parallel_research_optional": "research_loop",
}
DEPTH_ROUTE_FAMILIES = {
    "direct": "direct_fast_path",
    "targeted_source": "evidence_research",
    "independent_review": "evidence_research",
    "parallel_research_optional": "evidence_research",
    "deterministic_tool": "full_lca_workflow",
}
BOUNDARY_ROUTES = {
    "awaiting_user_data": "awaiting_user_data",
    "human_action_required": "human_action_required",
    "model_unavailable": "temporarily_unavailable",
    "provider_capacity_deferred": "temporarily_unavailable",
    "runtime_failure": "temporarily_unavailable",
    "capability_unavailable": "temporarily_unavailable",
}
FORBIDDEN_FIELDS = {
    "case_name",
    "keywords",
    "provider_name",
    "question_id",
    "task_text",
}
REQUEST_FIELDS = {
    "contract",
    "adaptive_plan",
    "decision_frame",
    "interaction_kind",
    "project_boundary",
    "selected_method_ids",
}


def _sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


class InteractionRouter:
    """Compile one auditable route from existing state and answer policy."""

    def __init__(self, policy: Mapping[str, object]):
        self.policy = deepcopy(dict(policy))

    @classmethod
    def load(cls, path: Path) -> "InteractionRouter":
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("interaction route policy is unavailable") from error
        if not isinstance(value, Mapping):
            raise ValueError("interaction route policy must be an object")
        expected_fields = {
            "contract",
            "route_ids",
            "interaction_kinds",
            "depth_routes",
            "boundary_routes",
            "forbidden_request_fields",
            "invariants",
        }
        if set(value) != expected_fields or value.get("contract") != POLICY_CONTRACT:
            raise ValueError("interaction route policy fields are invalid")
        if tuple(value.get("route_ids") or []) != ROUTE_IDS:
            raise ValueError("interaction route registry drifted")
        if tuple(value.get("interaction_kinds") or []) != INTERACTION_KINDS:
            raise ValueError("interaction kind registry drifted")
        if dict(value.get("depth_routes") or {}) != DEPTH_ROUTES:
            raise ValueError("interaction depth route mapping drifted")
        if dict(value.get("boundary_routes") or {}) != BOUNDARY_ROUTES:
            raise ValueError("interaction boundary route mapping drifted")
        if set(value.get("forbidden_request_fields") or []) != FORBIDDEN_FIELDS:
            raise ValueError("interaction forbidden field registry drifted")
        invariants = value.get("invariants")
        if not isinstance(invariants, Mapping) or not invariants or any(
            item is not True for item in invariants.values()
        ):
            raise ValueError("interaction route invariants are invalid")
        return cls(value)

    def compile(self, request: Mapping[str, object]) -> dict:
        if not isinstance(request, Mapping) or set(request) != REQUEST_FIELDS:
            raise ValueError("interaction route request fields are invalid")
        if FORBIDDEN_FIELDS.intersection(request):
            raise ValueError("interaction route request fields are forbidden")
        if request.get("contract") != REQUEST_CONTRACT:
            raise ValueError("interaction route request contract is invalid")
        interaction_kind = request.get("interaction_kind")
        if interaction_kind not in INTERACTION_KINDS:
            raise ValueError("interaction kind is invalid")
        boundary = request.get("project_boundary")
        if boundary is not None and not isinstance(boundary, str):
            raise ValueError("project boundary is invalid")

        plan = request.get("adaptive_plan")
        if (
            not isinstance(plan, Mapping)
            or plan.get("contract") != "adaptive_answer_plan/v1"
            or plan.get("research_depth") not in DEPTH_ROUTES
            or not isinstance(plan.get("stop_condition"), str)
            or not plan["stop_condition"]
            or not isinstance(plan.get("plan_sha256"), str)
            or SHA256_RE.fullmatch(plan["plan_sha256"]) is None
        ):
            raise ValueError("adaptive interaction plan is invalid")

        frame = request.get("decision_frame")
        expected = (
            frame.get("expected_state_change")
            if isinstance(frame, Mapping)
            else None
        )
        selected_capabilities = (
            frame.get("selected_capabilities")
            if isinstance(frame, Mapping)
            else None
        )
        if (
            not isinstance(frame, Mapping)
            or frame.get("contract")
            != "lca_research_kernel.architecture_decision_frame/v1"
            or not isinstance(frame.get("frame_sha256"), str)
            or SHA256_RE.fullmatch(frame["frame_sha256"]) is None
            or not isinstance(expected, Mapping)
            or any(
                not isinstance(expected.get(field), str) or not expected[field]
                for field in ("from_state", "to_state")
            )
            or not isinstance(expected.get("observable_delta"), list)
            or not expected["observable_delta"]
            or not isinstance(selected_capabilities, list)
            or any(
                not isinstance(value, str) or not value
                for value in selected_capabilities
            )
        ):
            raise ValueError("interaction decision frame is invalid")

        selected_methods = request.get("selected_method_ids")
        if not isinstance(selected_methods, list) or any(
            not isinstance(value, str) or not value for value in selected_methods
        ):
            raise ValueError("interaction selected methods are invalid")

        if boundary in BOUNDARY_ROUTES:
            route_id = BOUNDARY_ROUTES[str(boundary)]
        elif interaction_kind == "project_followup":
            route_id = "project_followup"
        else:
            route_id = DEPTH_ROUTES[str(plan["research_depth"])]
        route = {
            "contract": ROUTE_CONTRACT,
            "route_id": route_id,
            "route_family": self._route_family(
                research_depth=str(plan["research_depth"]),
                stop_condition=str(plan["stop_condition"]),
                boundary=boundary,
            ),
            "research_depth": plan["research_depth"],
            "interaction_kind": interaction_kind,
            "decision_frame_sha256": frame["frame_sha256"],
            "adaptive_plan_sha256": plan["plan_sha256"],
            "expected_state_change": deepcopy(dict(expected)),
            "selected_method_ids": sorted(set(selected_methods)),
            "selected_capability_ids": sorted(set(selected_capabilities)),
            "stop_condition": plan["stop_condition"],
            "next_route": route_id.replace("_", "-"),
        }
        route["route_sha256"] = _sha256(route)
        return route

    @staticmethod
    def _route_family(
        *, research_depth: str, stop_condition: str, boundary: object
    ) -> str:
        """Collapse internal actions into the three user-facing product paths."""

        if boundary in {"awaiting_user_data", "human_action_required"}:
            return "full_lca_workflow"
        if stop_condition == "return_bounded_gap":
            return "full_lca_workflow"
        try:
            return DEPTH_ROUTE_FAMILIES[research_depth]
        except KeyError as error:  # guarded by the policy validator
            raise ValueError("interaction research depth is unsupported") from error
