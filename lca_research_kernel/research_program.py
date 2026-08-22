"""Content-bound research programs for the existing Orchestrator."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Mapping

from .interaction_router import (
    DEPTH_ROUTE_FAMILIES,
    DEPTH_ROUTES,
    INTERACTION_KINDS,
    ROUTE_CONTRACT,
    ROUTE_IDS,
)
from .project_record import canonical_sha256
from .domain_rules import validate_domain_rule
from .research_turn import (
    ANSWER_OBLIGATIONS,
    COGNITIVE_OPERATIONS,
    knowledge_roles_for_obligations,
)
from .run_control import _validate_budget as _validate_run_budget


PROGRAM_CONTRACT = "lca_research_kernel.research_program/v2"
V3_PROGRAM_CONTRACT = "lca_research_kernel.research_program/v3"
V4_PROGRAM_CONTRACT = "lca_research_kernel.research_program/v4"
LEGACY_PROGRAM_CONTRACT = "lca_research_kernel.research_program/v1"
PROGRAM_EXECUTION_MODES = {
    LEGACY_PROGRAM_CONTRACT: "shadow",
    PROGRAM_CONTRACT: "material_answer",
    V3_PROGRAM_CONTRACT: "material_answer",
    V4_PROGRAM_CONTRACT: "routed_research",
}
PROGRAM_FIELDS = {
    "contract",
    "turn_sha256",
    "project_state_sha256",
    "requested_outcome",
    "evidence_plan",
    "route",
    "resource_budget",
    "claim_ceiling",
    "stop_condition",
    "execution_mode",
    "program_sha256",
}
V3_PROGRAM_FIELDS = PROGRAM_FIELDS | {"semantic_closure"}
V4_PROGRAM_FIELDS = V3_PROGRAM_FIELDS
REQUESTED_OUTCOME_FIELDS = {
    "objective",
    "objective_sha256",
    "response_language",
    "cognitive_operation",
    "answer_obligations",
    "referent_kind",
    "referent_identity",
}
EVIDENCE_PLAN_FIELDS = {
    "use_decision_sha256",
    "allowed_revision_ids",
    "selected_evidence_locator_ids",
    "required_knowledge_roles",
    "required_evidence_roles",
    "selected_evidence_roles",
    "unresolved_dependencies",
    "excluded_locator_count",
}
ROUTE_FIELDS = {
    "contract",
    "route_id",
    "route_family",
    "research_depth",
    "interaction_kind",
    "decision_frame_sha256",
    "adaptive_plan_sha256",
    "expected_state_change",
    "selected_method_ids",
    "selected_capability_ids",
    "stop_condition",
    "next_route",
    "route_sha256",
}
ROUTE_FAMILIES = {
    "direct_fast_path",
    "evidence_research",
    "full_lca_workflow",
}
EXPECTED_STATE_CHANGE_FIELDS = {"from_state", "to_state", "observable_delta"}
REFERENT_KINDS = {"accepted_claim", "accepted_result", "project_goal"}
RESPONSE_LANGUAGES = {"en", "zh-CN"}
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SEMANTIC_CLOSURE_CONTRACT = (
    "lca_research_kernel.research_program_semantic_closure/v1"
)
SEMANTIC_CLOSURE_FIELDS = {
    "contract",
    "source_bindings",
    "selected_skill_ids",
    "required_domain_rule_ids",
    "selected_domain_rule_ids",
    "selected_domain_rules",
    "required_capability_ids",
    "selected_capability_ids",
    "selected_governed_knowledge_ids",
    "missing_dependencies",
    "closure_sha256",
}
SEMANTIC_SOURCE_BINDING_FIELDS = {
    "capability_catalog_sha256",
    "skill_registry_sha256",
    "governed_knowledge_registry_sha256",
    "domain_rule_registry_sha256",
}


def _required_string(value: object, *, message: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(message)
    return value


def _unique_strings(value: object, *, message: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError(message)
    return list(value)


def _sorted_unique_strings(value: object, *, message: str) -> list[str]:
    values = _unique_strings(value, message=message)
    if values != sorted(values):
        raise ValueError(message)
    return values


def _validate_route(value: object) -> dict:
    if (
        not isinstance(value, Mapping)
        or set(value) != ROUTE_FIELDS
        or value.get("contract") != ROUTE_CONTRACT
        or value.get("route_id") not in ROUTE_IDS
        or value.get("route_family") not in ROUTE_FAMILIES
        or value.get("interaction_kind") not in INTERACTION_KINDS
    ):
        raise ValueError("research program route fields are invalid")
    route = deepcopy(dict(value))
    research_depth = route.get("research_depth")
    if not isinstance(research_depth, str) or not research_depth:
        raise ValueError("research program route is invalid")
    expected_route_id = (
        "project_followup"
        if route.get("interaction_kind") == "project_followup"
        else DEPTH_ROUTES.get(research_depth)
    )
    expected_route_family = (
        "full_lca_workflow"
        if route.get("stop_condition") == "return_bounded_gap"
        else DEPTH_ROUTE_FAMILIES.get(research_depth)
    )
    if (
        expected_route_id is None
        or route.get("route_id") != expected_route_id
        or route.get("route_family") != expected_route_family
    ):
        raise ValueError("research program route semantics drifted")
    for field in (
        "research_depth",
        "stop_condition",
        "next_route",
    ):
        _required_string(route.get(field), message="research program route is invalid")
    if route["next_route"] != str(route["route_id"]).replace("_", "-"):
        raise ValueError("research program route next step drifted")
    for field in ("decision_frame_sha256", "adaptive_plan_sha256", "route_sha256"):
        identity = route.get(field)
        if not isinstance(identity, str) or HEX64.fullmatch(identity) is None:
            raise ValueError("research program route identity is invalid")
    expected = route.get("expected_state_change")
    if not isinstance(expected, Mapping) or set(expected) != EXPECTED_STATE_CHANGE_FIELDS:
        raise ValueError("research program route state change is invalid")
    for field in ("from_state", "to_state"):
        _required_string(
            expected.get(field), message="research program route state change is invalid"
        )
    _sorted_unique_strings(
        expected.get("observable_delta"),
        message="research program route state change is invalid",
    )
    if not expected["observable_delta"]:
        raise ValueError("research program route state change is invalid")
    _sorted_unique_strings(
        route.get("selected_method_ids"),
        message="research program route methods are invalid",
    )
    _sorted_unique_strings(
        route.get("selected_capability_ids"),
        message="research program route capabilities are invalid",
    )
    route_without_sha = deepcopy(route)
    observed_route_sha = route_without_sha.pop("route_sha256")
    if observed_route_sha != canonical_sha256(route_without_sha):
        raise ValueError("research program route hash drifted")
    return route


def _validate_resource_budget(value: object) -> dict:
    try:
        budget = _validate_run_budget(value)
    except (TypeError, ValueError) as error:
        raise ValueError("research program resource budget is invalid") from error
    if any(
        isinstance(item, bool)
        for key, item in budget.items()
        if key != "contract"
    ):
        raise ValueError("research program resource budget is invalid")
    return budget


def validate_program_semantic_closure(value: Mapping[str, object]) -> dict:
    if (
        not isinstance(value, Mapping)
        or set(value) != SEMANTIC_CLOSURE_FIELDS
        or value.get("contract") != SEMANTIC_CLOSURE_CONTRACT
    ):
        raise ValueError("research program semantic closure is invalid")
    closure = deepcopy(dict(value))
    bindings = closure.get("source_bindings")
    if (
        not isinstance(bindings, Mapping)
        or set(bindings) != SEMANTIC_SOURCE_BINDING_FIELDS
    ):
        raise ValueError("research program semantic source bindings are invalid")
    if any(
        not isinstance(bindings.get(field), str)
        or HEX64.fullmatch(str(bindings[field])) is None
        for field in SEMANTIC_SOURCE_BINDING_FIELDS
    ):
        raise ValueError("research program semantic source bindings are invalid")
    for field in (
        "selected_skill_ids",
        "required_domain_rule_ids",
        "selected_domain_rule_ids",
        "required_capability_ids",
        "selected_capability_ids",
        "selected_governed_knowledge_ids",
        "missing_dependencies",
    ):
        _sorted_unique_strings(
            closure.get(field),
            message="research program semantic closure is invalid",
        )
    rules_value = closure.get("selected_domain_rules")
    if not isinstance(rules_value, list):
        raise ValueError("research program semantic domain rules are invalid")
    rules = [validate_domain_rule(rule) for rule in rules_value]
    rule_ids = [rule["rule_id"] for rule in rules]
    if (
        rule_ids != sorted(rule_ids)
        or len(rule_ids) != len(set(rule_ids))
        or rule_ids != closure["selected_domain_rule_ids"]
    ):
        raise ValueError("research program semantic domain rules are invalid")
    missing_rules = sorted(
        set(closure["required_domain_rule_ids"])
        - set(closure["selected_domain_rule_ids"])
    )
    if missing_rules:
        raise ValueError(
            "required domain rules are unavailable: " + ",".join(missing_rules)
        )
    if not set(closure["selected_capability_ids"]).issubset(
        set(closure["required_capability_ids"])
    ):
        raise ValueError("research program semantic capabilities are invalid")
    expected_missing = sorted(
        [
            *("domain_rule:" + rule_id for rule_id in missing_rules),
            *(
                "capability:" + capability_id
                for capability_id in sorted(
                    set(closure["required_capability_ids"])
                    - set(closure["selected_capability_ids"])
                )
            ),
        ]
    )
    if closure["missing_dependencies"] != expected_missing:
        raise ValueError("research program semantic dependencies drifted")
    observed_sha256 = closure.pop("closure_sha256")
    if (
        not isinstance(observed_sha256, str)
        or HEX64.fullmatch(observed_sha256) is None
        or observed_sha256 != canonical_sha256(closure)
    ):
        raise ValueError("research program semantic closure hash drifted")
    closure["closure_sha256"] = observed_sha256
    return closure


def _compile_research_program(
    *,
    objective: str,
    turn_frame: Mapping[str, object],
    material_use_decision: Mapping[str, object],
    interaction_route: Mapping[str, object],
    run_budget: Mapping[str, object],
    response_language: str,
    selected_evidence_roles: list[str],
    semantic_closure: Mapping[str, object] | None = None,
    execution_contract: str | None = None,
) -> dict:
    """Build the candidate execution program after authority is proven."""

    _required_string(objective, message="research program objective is absent")
    if not isinstance(turn_frame, Mapping):
        raise ValueError("research program turn frame is invalid")
    if not isinstance(material_use_decision, Mapping):
        raise ValueError("research program material decision is invalid")
    if not isinstance(interaction_route, Mapping):
        raise ValueError("research program route is invalid")
    if not isinstance(run_budget, Mapping):
        raise ValueError("research program budget is invalid")

    turn_sha256 = _required_string(
        turn_frame.get("turn_sha256"),
        message="research program turn identity is absent",
    )
    project_state_sha256 = _required_string(
        turn_frame.get("project_state_sha256"),
        message="research program project state is absent",
    )
    if (
        HEX64.fullmatch(turn_sha256) is None
        or HEX64.fullmatch(project_state_sha256) is None
    ):
        raise ValueError("research program accepted state identity is invalid")

    allowed_revisions = sorted(
        _unique_strings(
            material_use_decision.get("allowed_revision_ids"),
            message="research program allowed revisions are invalid",
        )
    )
    selected_locators = sorted(
        _unique_strings(
            material_use_decision.get("selected_evidence_locator_ids"),
            message="research program selected locators are invalid",
        )
    )
    if bool(allowed_revisions) != bool(selected_locators):
        raise ValueError("research program evidence selection is incomplete")
    if (
        not allowed_revisions
        and execution_contract != V4_PROGRAM_CONTRACT
    ):
        raise ValueError("research program evidence selection is empty")
    expected = material_use_decision.get("expected_state_change")
    if not isinstance(expected, Mapping):
        raise ValueError("research program expected state change is invalid")
    required_evidence_roles = sorted(
        _unique_strings(
            expected.get("required_evidence_roles", []),
            message="research program required evidence roles are invalid",
        )
    )
    required_knowledge_roles = sorted(
        _unique_strings(
            turn_frame.get("required_knowledge_roles", []),
            message="research program required knowledge roles are invalid",
        )
    )
    selected_roles = sorted(
        _unique_strings(
            selected_evidence_roles,
            message="research program selected evidence roles are invalid",
        )
    )
    unresolved_dependencies = sorted(set(required_evidence_roles) - set(selected_roles))
    exclusions = material_use_decision.get("exclusions", [])
    if not isinstance(exclusions, list):
        raise ValueError("research program exclusions are invalid")
    answer_obligations = _unique_strings(
        turn_frame.get("answer_obligations"),
        message="research program answer obligations are invalid",
    )
    if required_knowledge_roles != knowledge_roles_for_obligations(
        answer_obligations
    ):
        raise ValueError("research program required knowledge roles drifted")
    referent = turn_frame.get("referent")
    if not isinstance(referent, Mapping):
        raise ValueError("research program referent is invalid")

    contract = PROGRAM_CONTRACT
    closure = None
    route_value = deepcopy(dict(interaction_route))
    if execution_contract not in {None, V4_PROGRAM_CONTRACT}:
        raise ValueError("research program execution contract is unsupported")
    if execution_contract == V4_PROGRAM_CONTRACT and semantic_closure is None:
        raise ValueError("routed research program semantic closure is absent")
    if semantic_closure is not None:
        closure = validate_program_semantic_closure(semantic_closure)
        contract = execution_contract or V3_PROGRAM_CONTRACT
        route_value["selected_method_ids"] = deepcopy(
            closure["selected_skill_ids"]
        )
        route_value["selected_capability_ids"] = deepcopy(
            closure["selected_capability_ids"]
        )
        route_value.pop("route_sha256", None)
        route_value["route_sha256"] = canonical_sha256(route_value)
    route = _validate_route(route_value)
    budget = _validate_resource_budget(run_budget)
    objective_projection = (
        objective.strip()
        if len(objective.strip()) <= 512
        else objective.strip()[:509] + "..."
    )

    program = {
        "contract": contract,
        "turn_sha256": turn_sha256,
        "project_state_sha256": project_state_sha256,
        "requested_outcome": {
            "objective": objective_projection,
            "objective_sha256": canonical_sha256(objective_projection),
            "response_language": _required_string(
                response_language,
                message="research program response language is absent",
            ),
            "cognitive_operation": _required_string(
                turn_frame.get("cognitive_operation"),
                message="research program cognitive operation is absent",
            ),
            "answer_obligations": answer_obligations,
            "referent_kind": _required_string(
                referent.get("kind"),
                message="research program referent kind is absent",
            ),
            "referent_identity": _required_string(
                referent.get("identity"),
                message="research program referent identity is absent",
            ),
        },
        "evidence_plan": {
            "use_decision_sha256": canonical_sha256(material_use_decision),
            "allowed_revision_ids": allowed_revisions,
            "selected_evidence_locator_ids": selected_locators,
            "required_knowledge_roles": required_knowledge_roles,
            "required_evidence_roles": required_evidence_roles,
            "selected_evidence_roles": selected_roles,
            "unresolved_dependencies": unresolved_dependencies,
            "excluded_locator_count": len(exclusions),
        },
        "route": route,
        "resource_budget": budget,
        "claim_ceiling": _required_string(
            turn_frame.get("claim_ceiling"),
            message="research program claim ceiling is absent",
        ),
        "stop_condition": _required_string(
            route.get("stop_condition"),
            message="research program stop condition is absent",
        ),
        "execution_mode": PROGRAM_EXECUTION_MODES[contract],
    }
    if closure is not None:
        program["semantic_closure"] = closure
    program["program_sha256"] = canonical_sha256(program)
    return validate_research_program(program)


def validate_research_program(value: Mapping[str, object]) -> dict:
    """Validate and replay one content-bound research program."""

    if not isinstance(value, Mapping):
        raise ValueError("research program fields are invalid")
    contract = value.get("contract")
    expected_fields = (
        V4_PROGRAM_FIELDS
        if contract == V4_PROGRAM_CONTRACT
        else V3_PROGRAM_FIELDS
        if contract == V3_PROGRAM_CONTRACT
        else PROGRAM_FIELDS
    )
    if (
        set(value) != expected_fields
        or not isinstance(contract, str)
        or contract not in PROGRAM_EXECUTION_MODES
        or value.get("execution_mode") != PROGRAM_EXECUTION_MODES[contract]
    ):
        raise ValueError("research program fields are invalid")
    program = deepcopy(dict(value))
    for field in ("turn_sha256", "project_state_sha256", "program_sha256"):
        if (
            not isinstance(program.get(field), str)
            or HEX64.fullmatch(program[field]) is None
        ):
            raise ValueError("research program identity is invalid")
    requested = program.get("requested_outcome")
    evidence = program.get("evidence_plan")
    route = program.get("route")
    if not isinstance(requested, Mapping) or set(requested) != REQUESTED_OUTCOME_FIELDS:
        raise ValueError("research program requested outcome is invalid")
    if not isinstance(evidence, Mapping) or set(evidence) != EVIDENCE_PLAN_FIELDS:
        raise ValueError("research program evidence plan is invalid")
    if not isinstance(route, Mapping) or set(route) != ROUTE_FIELDS:
        raise ValueError("research program route is invalid")
    if contract in {V3_PROGRAM_CONTRACT, V4_PROGRAM_CONTRACT}:
        closure = validate_program_semantic_closure(
            program.get("semantic_closure")
        )
        if (
            route.get("selected_method_ids") != closure["selected_skill_ids"]
            or route.get("selected_capability_ids")
            != closure["selected_capability_ids"]
        ):
            raise ValueError("research program semantic route drifted")
    objective = requested.get("objective")
    if (
        not isinstance(objective, str)
        or not objective.strip()
        or len(objective) > 512
        or requested.get("objective_sha256") != canonical_sha256(objective)
    ):
        raise ValueError("research program requested outcome identity is invalid")
    for field in (
        "response_language",
        "cognitive_operation",
        "referent_kind",
        "referent_identity",
    ):
        _required_string(
            requested.get(field), message="research program requested outcome is invalid"
        )
    if requested["response_language"] not in RESPONSE_LANGUAGES:
        raise ValueError("research program response language is invalid")
    if requested["cognitive_operation"] not in COGNITIVE_OPERATIONS:
        raise ValueError("research program cognitive operation is invalid")
    if requested["referent_kind"] not in REFERENT_KINDS:
        raise ValueError("research program referent kind is invalid")
    obligations = _unique_strings(
        requested.get("answer_obligations"),
        message="research program answer obligations are invalid",
    )
    if not obligations or any(value not in ANSWER_OBLIGATIONS for value in obligations):
        raise ValueError("research program answer obligations are unsupported")
    for field in ("use_decision_sha256",):
        if not isinstance(evidence.get(field), str) or HEX64.fullmatch(
            evidence[field]
        ) is None:
            raise ValueError("research program evidence identity is invalid")
    for field in (
        "allowed_revision_ids",
        "selected_evidence_locator_ids",
        "required_knowledge_roles",
        "required_evidence_roles",
        "selected_evidence_roles",
        "unresolved_dependencies",
    ):
        _sorted_unique_strings(
            evidence.get(field), message="research program evidence plan is invalid"
        )
    if evidence["required_knowledge_roles"] != knowledge_roles_for_obligations(
        obligations
    ):
        raise ValueError("research program required knowledge roles drifted")
    has_allowed = bool(evidence["allowed_revision_ids"])
    has_selected = bool(evidence["selected_evidence_locator_ids"])
    if has_allowed != has_selected:
        raise ValueError("research program evidence selection is incomplete")
    if contract != V4_PROGRAM_CONTRACT and not has_allowed:
        raise ValueError("research program evidence selection is empty")
    if set(evidence["unresolved_dependencies"]) != set(
        evidence["required_evidence_roles"]
    ) - set(evidence["selected_evidence_roles"]):
        raise ValueError("research program unresolved dependencies drifted")
    if (
        not isinstance(evidence["excluded_locator_count"], int)
        or isinstance(evidence["excluded_locator_count"], bool)
        or evidence["excluded_locator_count"] < 0
    ):
        raise ValueError("research program excluded locator count is invalid")
    validated_route = _validate_route(route)
    if program.get("stop_condition") != validated_route["stop_condition"]:
        raise ValueError("research program stop condition drifted")
    if program.get("claim_ceiling") != "source_bound_bounded":
        raise ValueError("research program claim ceiling is invalid")
    program["resource_budget"] = _validate_resource_budget(
        program.get("resource_budget")
    )
    observed_sha256 = program.pop("program_sha256")
    if observed_sha256 != canonical_sha256(program):
        raise ValueError("research program hash drifted")
    program["program_sha256"] = observed_sha256
    return program
