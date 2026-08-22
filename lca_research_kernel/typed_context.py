"""Private typed context contract owned by the D-18 Context Assembler.

This is an implementation seam, not an additional product module. It keeps
execution authority and provider-visible context separate while binding both to
one accepted Research Program and Project Record generation.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Mapping

from .content_identity import canonical_sha256
from .research_program import validate_research_program


CONTEXT_PACKAGE_CONTRACT = "lca_research_kernel.typed_context_package/v1"
PROVIDER_CONTEXT_CONTRACT = "lca_research_kernel.provider_context/v1"
MATERIAL_AUTHORIZATION_REF_CONTRACT = (
    "lca_research_kernel.material_authorization_ref/v1"
)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_PACKAGE_FIELDS = {
    "contract",
    "research_program",
    "research_program_ref",
    "project_state_ref",
    "route",
    "claim_ceiling",
    "resource_budget",
    "stop_condition",
    "model_context",
    "model_context_sha256",
    "estimated_tokens",
    "evidence_closure",
    "material_authorization_ref",
    "package_sha256",
}
_PROGRAM_REF_FIELDS = {
    "contract",
    "program_sha256",
    "turn_sha256",
    "project_state_sha256",
}
_PROJECT_STATE_REF_FIELDS = {"project_id", "state_sha256"}
_ROUTE_FIELDS = {
    "route_id",
    "route_family",
    "research_depth",
    "interaction_kind",
    "route_sha256",
}
_AUTHORIZATION_REF_FIELDS = {
    "contract",
    "decision_id",
    "decision_sha256",
    "project_record_event_sha256",
}


def _require_sha256(value: object, message: str) -> str:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise ValueError(message)
    return value


def _validate_material_authorization_ref(value: object) -> dict | None:
    if value is None:
        return None
    if (
        not isinstance(value, Mapping)
        or set(value) != _AUTHORIZATION_REF_FIELDS
        or value.get("contract") != MATERIAL_AUTHORIZATION_REF_CONTRACT
        or not isinstance(value.get("decision_id"), str)
        or not value["decision_id"]
    ):
        raise ValueError("typed context material authorization ref is invalid")
    normalized = deepcopy(dict(value))
    for field in ("decision_sha256", "project_record_event_sha256"):
        _require_sha256(
            normalized.get(field),
            "typed context material authorization ref is invalid",
        )
    return normalized


def _evidence_closure(model_context: Mapping[str, object]) -> dict:
    rows: list[dict] = []
    evidence_refs = model_context.get("evidence_refs")
    if isinstance(evidence_refs, list):
        for reference in evidence_refs:
            if (
                not isinstance(reference, Mapping)
                or set(reference)
                != {"identity", "authority", "content_sha256"}
                or not isinstance(reference.get("identity"), str)
                or not reference["identity"]
                or not isinstance(reference.get("authority"), str)
                or not reference["authority"]
                or not isinstance(reference.get("content_sha256"), str)
                or _HEX64.fullmatch(reference["content_sha256"]) is None
            ):
                raise ValueError("typed context evidence reference is invalid")
            rows.append(deepcopy(dict(reference)))
    selected_units = model_context.get("selected_evidence_units")
    if isinstance(selected_units, list):
        for unit in selected_units:
            if not isinstance(unit, Mapping):
                raise ValueError("typed context selected evidence is invalid")
            identity = unit.get("locator_id")
            content_sha256 = unit.get("content_hash")
            if (
                not isinstance(identity, str)
                or not identity
                or not isinstance(content_sha256, str)
                or _HEX64.fullmatch(content_sha256) is None
            ):
                raise ValueError("typed context selected evidence is invalid")
            rows.append(
                {
                    "identity": identity,
                    "authority": "user_supplied_project_input",
                    "content_sha256": content_sha256,
                }
            )
    items = model_context.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, Mapping):
                raise ValueError("typed context model item is invalid")
            identity = item.get("identity")
            source = item.get("source")
            if not isinstance(identity, str) or not identity:
                raise ValueError("typed context model item is invalid")
            source = source if isinstance(source, Mapping) else {}
            content_sha256 = source.get("content_sha256")
            if not isinstance(content_sha256, str) or _HEX64.fullmatch(
                content_sha256
            ) is None:
                content_sha256 = canonical_sha256(item.get("content"))
            authority = source.get("authority")
            rows.append(
                {
                    "identity": identity,
                    "authority": (
                        authority
                        if isinstance(authority, str) and authority
                        else "bounded_context"
                    ),
                    "content_sha256": content_sha256,
                }
            )
    rows = sorted(rows, key=lambda row: (row["identity"], row["content_sha256"]))
    if len({(row["identity"], row["content_sha256"]) for row in rows}) != len(
        rows
    ):
        raise ValueError("typed context evidence closure is duplicated")
    closure = {
        "contract": "lca_research_kernel.context_evidence_closure/v1",
        "items": rows,
    }
    closure["closure_sha256"] = canonical_sha256(closure)
    return closure


def build_context_package(
    *,
    research_program: Mapping[str, object],
    project_id: str,
    model_context: Mapping[str, object],
    material_authorization_ref: Mapping[str, object] | None = None,
) -> dict:
    program = validate_research_program(research_program)
    if not isinstance(project_id, str) or not project_id:
        raise ValueError("typed context project identity is invalid")
    if not isinstance(model_context, Mapping):
        raise ValueError("typed context model payload is invalid")
    context = deepcopy(dict(model_context))
    route = program["route"]
    package = {
        "contract": CONTEXT_PACKAGE_CONTRACT,
        "research_program": program,
        "research_program_ref": {
            "contract": program["contract"],
            "program_sha256": program["program_sha256"],
            "turn_sha256": program["turn_sha256"],
            "project_state_sha256": program["project_state_sha256"],
        },
        "project_state_ref": {
            "project_id": project_id,
            "state_sha256": program["project_state_sha256"],
        },
        "route": {field: deepcopy(route[field]) for field in _ROUTE_FIELDS},
        "claim_ceiling": program["claim_ceiling"],
        "resource_budget": deepcopy(program["resource_budget"]),
        "stop_condition": program["stop_condition"],
        "model_context": context,
        "model_context_sha256": canonical_sha256(context),
        "estimated_tokens": (
            context.get("estimated_tokens")
            if isinstance(context.get("estimated_tokens"), int)
            and not isinstance(context.get("estimated_tokens"), bool)
            and context["estimated_tokens"] >= 0
            else max(1, len(str(context)) // 4)
        ),
        "evidence_closure": _evidence_closure(context),
        "material_authorization_ref": _validate_material_authorization_ref(
            material_authorization_ref
        ),
    }
    package["package_sha256"] = canonical_sha256(package)
    return validate_context_package(package)


def validate_context_package(value: Mapping[str, object]) -> dict:
    if (
        not isinstance(value, Mapping)
        or set(value) != _PACKAGE_FIELDS
        or value.get("contract") != CONTEXT_PACKAGE_CONTRACT
    ):
        raise ValueError("typed context package fields are invalid")
    package = deepcopy(dict(value))
    program = validate_research_program(package.get("research_program"))
    program_ref = package.get("research_program_ref")
    expected_program_ref = {
        "contract": program["contract"],
        "program_sha256": program["program_sha256"],
        "turn_sha256": program["turn_sha256"],
        "project_state_sha256": program["project_state_sha256"],
    }
    if (
        not isinstance(program_ref, Mapping)
        or set(program_ref) != _PROGRAM_REF_FIELDS
        or dict(program_ref) != expected_program_ref
    ):
        raise ValueError("typed context program identity drifted")
    project_ref = package.get("project_state_ref")
    if (
        not isinstance(project_ref, Mapping)
        or set(project_ref) != _PROJECT_STATE_REF_FIELDS
        or not isinstance(project_ref.get("project_id"), str)
        or not project_ref["project_id"]
        or project_ref.get("state_sha256") != program["project_state_sha256"]
    ):
        raise ValueError("typed context Project Record state drifted")
    expected_route = {
        field: deepcopy(program["route"][field]) for field in _ROUTE_FIELDS
    }
    if package.get("route") != expected_route:
        raise ValueError("typed context route drifted")
    if (
        package.get("claim_ceiling") != program["claim_ceiling"]
        or package.get("resource_budget") != program["resource_budget"]
        or package.get("stop_condition") != program["stop_condition"]
    ):
        raise ValueError("typed context execution boundary drifted")
    model_context = package.get("model_context")
    if (
        not isinstance(model_context, Mapping)
        or package.get("model_context_sha256")
        != canonical_sha256(model_context)
    ):
        raise ValueError("typed context model payload drifted")
    selected_units = model_context.get("selected_evidence_units")
    context_locator_ids = (
        {
            unit.get("locator_id")
            for unit in selected_units
            if isinstance(unit, Mapping)
            and isinstance(unit.get("locator_id"), str)
        }
        if isinstance(selected_units, list)
        else set()
    )
    program_locator_ids = set(
        program["evidence_plan"]["selected_evidence_locator_ids"]
    )
    if not context_locator_ids.issubset(program_locator_ids):
        raise ValueError("typed context evidence exceeded program selection")
    if (
        not isinstance(package.get("estimated_tokens"), int)
        or isinstance(package["estimated_tokens"], bool)
        or package["estimated_tokens"] < 0
    ):
        raise ValueError("typed context token estimate is invalid")
    closure = package.get("evidence_closure")
    if not isinstance(closure, Mapping) or closure != _evidence_closure(
        model_context
    ):
        raise ValueError("typed context evidence closure drifted")
    package["material_authorization_ref"] = _validate_material_authorization_ref(
        package.get("material_authorization_ref")
    )
    if (
        package["material_authorization_ref"] is not None
        and context_locator_ids != program_locator_ids
    ):
        raise ValueError("typed context authorized evidence closure is incomplete")
    observed = package.pop("package_sha256")
    if (
        not isinstance(observed, str)
        or _HEX64.fullmatch(observed) is None
        or observed != canonical_sha256(package)
    ):
        raise ValueError("typed context package hash drifted")
    package["package_sha256"] = observed
    return package


def provider_context_projection(value: Mapping[str, object]) -> dict:
    """Return model-visible content; execution authorization never crosses."""

    package = validate_context_package(value)
    model_context = package["model_context"]
    provider_contract = {
        "lca_research_kernel.material_model_context/v1": (
            "lca_research_kernel.material_answer_context/v3"
        ),
        "lca_research_kernel.material_answer_repair_model_context/v1": (
            "lca_research_kernel.material_answer_repair/v2"
        ),
        "lca_research_kernel.model_context/v1": (
            "lca_research_kernel.model_context/v1"
        ),
    }.get(model_context.get("contract"), PROVIDER_CONTEXT_CONTRACT)
    projection = {
        "contract": provider_contract,
        "research_program": deepcopy(package["research_program"]),
        "research_program_ref": deepcopy(package["research_program_ref"]),
        "project_state_ref": deepcopy(package["project_state_ref"]),
        "route": deepcopy(package["route"]),
        "claim_ceiling": package["claim_ceiling"],
        "resource_budget": deepcopy(package["resource_budget"]),
        "stop_condition": package["stop_condition"],
    }
    for key, nested in model_context.items():
        if key != "contract" and key not in projection:
            projection[key] = deepcopy(nested)
    turn_semantics = projection.get("turn_semantics")
    if isinstance(turn_semantics, Mapping):
        # Compatibility alias for provider prompts. It is a bounded semantic
        # projection, not the Project Record/material-selection frame.
        projection["turn_frame"] = deepcopy(dict(turn_semantics))
    return projection
