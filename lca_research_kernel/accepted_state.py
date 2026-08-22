"""Deterministic accepted-change reducer for the D-18 accepted-state view."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .change_contracts import validate_locator_acceptance_binding
from .content_identity import canonical_sha256
from .material_sources import (
    material_revision_context_eligibility,
    validate_material_use_decision,
)


MATERIAL_CHANGE_TYPES = {
    "material.registered",
    "material.revision_accepted",
    "material.scope_updated",
    "material.withdrawn",
    "material.restored",
    "evidence.locator_accepted",
    "material.use_decision_recorded",
}


def _ensure_material_projection(view: dict[str, Any]) -> None:
    for key in (
        "materials",
        "material_revisions",
        "evidence_locators",
        "material_use_decisions",
    ):
        view.setdefault(key, {})


def apply_accepted_candidate(
    view: dict[str, Any], candidate: Mapping[str, object]
) -> None:
    """Apply one already validated and accepted candidate to the derived view."""

    change_type = candidate["change_type"]
    if change_type in MATERIAL_CHANGE_TYPES:
        _ensure_material_projection(view)

    if change_type == "fact.upserted":
        fact = deepcopy(candidate["fact"])
        view["facts"][fact["fact_id"]] = fact
    elif change_type == "user_turn.activated":
        view["facts"]["active-user-turn"] = deepcopy(candidate["fact"])
    elif change_type == "decision.recorded":
        view["decisions"].append(deepcopy(candidate["decision"]))
    elif change_type == "risk.upserted":
        risk = deepcopy(candidate["risk"])
        view["risks"][risk["risk_id"]] = risk
    elif change_type == "result.accepted":
        view["result"] = deepcopy(candidate["result"])
    elif change_type == "route.updated":
        pass
    elif change_type == "material.registered":
        material = deepcopy(candidate["material"])
        material["scientific_status"] = "accepted_project_input"
        material_id = material["material_id"]
        prior = view["materials"].get(material_id)
        if prior is not None and prior != material:
            raise ValueError("Project Record material identity drifted")
        view["materials"][material_id] = material
    elif change_type == "material.revision_accepted":
        revision = deepcopy(candidate["material_revision"])
        material = view["materials"].get(revision["material_id"])
        if not isinstance(material, dict):
            raise ValueError("material revision lacks registered material")
        current_revision_id = material.get("current_revision_id")
        if current_revision_id not in {
            revision["revision_id"],
            revision.get("parent_revision_id"),
        }:
            raise ValueError("material revision is not current")
        revision["processing_receipt_ref"] = deepcopy(
            candidate["processing_receipt_ref"]
        )
        if "processing_receipt_sha256" in candidate:
            revision["processing_receipt_sha256"] = candidate[
                "processing_receipt_sha256"
            ]
        view["material_revisions"][revision["revision_id"]] = revision
        turn_scope_sha256 = candidate.get("turn_scope_sha256")
        if turn_scope_sha256 is not None:
            view.setdefault("material_revision_turn_bindings", {})[
                revision["revision_id"]
            ] = turn_scope_sha256
        if current_revision_id != revision["revision_id"]:
            material["current_revision_id"] = revision["revision_id"]
    elif change_type == "evidence.locator_accepted":
        locator = deepcopy(candidate["evidence_locator"])
        if locator["revision_id"] not in view["material_revisions"]:
            raise ValueError("evidence locator lacks accepted revision")
        binding = candidate.get("evidence_binding")
        if isinstance(binding, Mapping):
            validate_locator_acceptance_binding(locator, binding)
        view["evidence_locators"][locator["locator_id"]] = locator
    elif change_type == "material.use_decision_recorded":
        decision = validate_material_use_decision(
            deepcopy(candidate["material_use_decision"]),
            locator_index=view["evidence_locators"],
        )
        for revision_id in decision["allowed_revision_ids"]:
            revision = view["material_revisions"].get(revision_id)
            material = (
                view["materials"].get(revision.get("material_id"))
                if isinstance(revision, Mapping)
                else None
            )
            if (
                not isinstance(material, Mapping)
                or material.get("current_revision_id") != revision_id
                or material.get("deleted_at") is not None
                or not material_revision_context_eligibility(view, revision_id)[0]
            ):
                raise ValueError("material use decision escaped accepted scope")
        decision_id = candidate.get("decision_id") or canonical_sha256(decision)
        view["material_use_decisions"][decision_id] = decision
    elif change_type == "material.scope_updated":
        material = view["materials"].get(candidate.get("material_id"))
        if not isinstance(material, dict):
            raise ValueError("material scope update lacks material")
        material["scope"] = candidate["scope"]
    elif change_type == "material.withdrawn":
        material = view["materials"].get(candidate.get("material_id"))
        if not isinstance(material, dict):
            raise ValueError("material withdrawal lacks material")
        material["deleted_at"] = candidate["deleted_at"]
    elif change_type == "material.restored":
        material = view["materials"].get(candidate.get("material_id"))
        if not isinstance(material, dict):
            raise ValueError("material restore lacks material")
        material["deleted_at"] = None
    else:
        raise ValueError("accepted Project Record change type is unsupported")

    view["next_route"] = candidate["next_route"]
