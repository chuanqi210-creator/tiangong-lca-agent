"""Pure change contracts shared by accepted-state storage and Assurance."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping

from .content_identity import canonical_sha256


CANDIDATE_CONTRACT = "lca_research_kernel.project_candidate_change/v1"
ACCEPTANCE_CONTRACT = "lca_research_kernel.assurance_acceptance/v1"
FACT_CLASSES = {"official", "candidate", "assumption", "computed", "missing"}
CHANGE_TYPES = {
    "fact.upserted",
    "user_turn.activated",
    "decision.recorded",
    "risk.upserted",
    "result.accepted",
    "route.updated",
    "material.registered",
    "material.revision_accepted",
    "material.scope_updated",
    "material.withdrawn",
    "material.restored",
    "evidence.locator_accepted",
    "material.use_decision_recorded",
}
MATERIAL_LOCATOR_AUTHORITY_BINDING = (
    "lca_research_kernel.material_locator_authority_binding/v1"
)


def _require_string(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"Project Record {key} must be a non-empty string")
    return item


def validate_locator_acceptance_binding(
    locator: Mapping[str, object], binding: Mapping[str, object]
) -> None:
    expected_basis = {
        "locator_id": locator.get("locator_id"),
        "revision_id": locator.get("revision_id"),
        "locator_kind": locator.get("locator_kind"),
        "locator": deepcopy(locator.get("locator")),
        "content_hash": locator.get("content_hash"),
        "scientific_status": "accepted_project_input",
    }
    digest = canonical_sha256(expected_basis)
    acceptance_ref = locator.get("assurance_acceptance_ref")
    if (
        binding.get("locator_id") != locator.get("locator_id")
        or binding.get("content_hash") != locator.get("content_hash")
        or binding.get("source_span")
        != {
            "kind": locator.get("locator_kind"),
            "locator": locator.get("locator"),
        }
        or not isinstance(acceptance_ref, Mapping)
        or acceptance_ref.get("artifact_id") != digest
        or acceptance_ref.get("sha256") != digest
    ):
        raise ValueError("Project Record evidence acceptance binding drifted")


def material_locator_authority_view(
    project_view: Mapping[str, object], revision_id: str
) -> dict[str, object]:
    """Return the stable Project Record authority slice for one locator."""

    materials = project_view.get("materials")
    revisions = project_view.get("material_revisions")
    if not isinstance(materials, Mapping) or not isinstance(revisions, Mapping):
        raise ValueError("material locator authority is unavailable")
    revision = revisions.get(revision_id)
    material = (
        materials.get(revision.get("material_id"))
        if isinstance(revision, Mapping)
        else None
    )
    if not isinstance(revision, Mapping) or not isinstance(material, Mapping):
        raise ValueError("material locator authority is incomplete")
    facts = project_view.get("facts")
    active_turn = facts.get("active-user-turn") if isinstance(facts, Mapping) else None
    turn_bindings = project_view.get("material_revision_turn_bindings")
    turn_binding = (
        turn_bindings.get(revision_id) if isinstance(turn_bindings, Mapping) else None
    )
    return {
        "contract": MATERIAL_LOCATOR_AUTHORITY_BINDING,
        "project_id": project_view.get("project_id"),
        "materials": {str(material["material_id"]): deepcopy(dict(material))},
        "material_revisions": {revision_id: deepcopy(dict(revision))},
        "facts": (
            {"active-user-turn": deepcopy(dict(active_turn))}
            if isinstance(active_turn, Mapping)
            else {}
        ),
        "material_revision_turn_bindings": (
            {revision_id: turn_binding} if isinstance(turn_binding, str) else {}
        ),
    }


def validate_project_candidate(
    value: Mapping[str, object],
    *,
    require_material_bindings: bool = False,
    require_evidence_units_ref: bool = False,
    allow_legacy_active_turn_fact: bool = False,
) -> dict[str, Any]:
    """Validate and normalize one candidate without touching record storage."""

    if not isinstance(value, Mapping) or value.get("contract") != CANDIDATE_CONTRACT:
        raise ValueError("Project Record candidate contract is unsupported")
    candidate = deepcopy(dict(value))
    _require_string(candidate, "candidate_id")
    change_type = _require_string(candidate, "change_type")
    if change_type not in CHANGE_TYPES:
        raise ValueError("Project Record candidate change_type is unsupported")
    _require_string(candidate, "next_route")
    _require_string(candidate, "proposed_at")
    if change_type == "fact.upserted":
        fact = candidate.get("fact")
        if not isinstance(fact, Mapping):
            raise ValueError("Project Record fact candidate is absent")
        fact_id = _require_string(fact, "fact_id")
        if fact_id == "active-user-turn" and not allow_legacy_active_turn_fact:
            raise ValueError(
                "Project Record active user turn requires the dedicated control change"
            )
        if _require_string(fact, "classification") not in FACT_CLASSES:
            raise ValueError("Project Record fact classification is unsupported")
    elif change_type == "user_turn.activated":
        fact = candidate.get("fact")
        if not isinstance(fact, Mapping):
            raise ValueError("Project Record active user turn is absent")
        if (
            fact.get("fact_id") != "active-user-turn"
            or fact.get("classification") != "candidate"
            or fact.get("domain_kind") != "user_turn"
        ):
            raise ValueError("Project Record active user turn control is invalid")
        turn = fact.get("value")
        source = fact.get("source")
        if (
            not isinstance(turn, Mapping)
            or turn.get("contract") != "lca_research_kernel.research_user_turn/v1"
            or not isinstance(turn.get("input_id"), str)
            or not turn["input_id"]
            or not isinstance(turn.get("text"), str)
            or re.fullmatch(r"[0-9a-f]{64}", str(turn.get("turn_sha256"))) is None
            or not isinstance(turn.get("turn_run_id"), str)
            or not turn["turn_run_id"]
            or not isinstance(source, Mapping)
            or source.get("authority") != "user_supplied_project_input"
            or source.get("input_id") != turn.get("input_id")
            or not isinstance(source.get("source_refs"), list)
        ):
            raise ValueError("Project Record active user turn control is invalid")
    elif change_type == "material.registered":
        from .material_sources import validate_research_material

        candidate["material"] = validate_research_material(candidate.get("material"))  # type: ignore[arg-type]
        candidate.pop("material_bundle", None)
    elif change_type == "material.revision_accepted":
        from .material_sources import validate_material_revision

        candidate["material_revision"] = validate_material_revision(
            candidate.get("material_revision")  # type: ignore[arg-type]
        )
        turn_scope_sha256 = candidate.get("turn_scope_sha256")
        if turn_scope_sha256 is not None and (
            not isinstance(turn_scope_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", turn_scope_sha256) is None
        ):
            raise ValueError("Project Record material turn scope binding is invalid")
        candidate.pop("material_bundle", None)
        receipt_ref = candidate.get("processing_receipt_ref")
        if (
            not isinstance(receipt_ref, Mapping)
            or set(receipt_ref) != {"artifact_id", "sha256"}
            or receipt_ref.get("artifact_id") != receipt_ref.get("sha256")
            or not isinstance(receipt_ref.get("sha256"), str)
            or len(receipt_ref["sha256"]) != 64
            or any(
                character not in "0123456789abcdef"
                for character in receipt_ref["sha256"]
            )
        ):
            raise ValueError("Project Record material processing receipt ref is invalid")
        digest = candidate.get("processing_receipt_sha256")
        if require_material_bindings and (
            not isinstance(digest, str) or digest != receipt_ref.get("sha256")
        ):
            raise ValueError("Project Record material processing receipt binding is invalid")
    elif change_type in {
        "material.scope_updated",
        "material.withdrawn",
        "material.restored",
    }:
        _require_string(candidate, "material_id")
        if change_type == "material.scope_updated" and candidate.get("scope") not in {
            "turn",
            "project",
        }:
            raise ValueError("Project Record material scope is unsupported")
        if change_type == "material.withdrawn":
            _require_string(candidate, "deleted_at")
    elif change_type == "evidence.locator_accepted":
        from .material_sources import validate_evidence_locator

        candidate["evidence_locator"] = validate_evidence_locator(
            candidate.get("evidence_locator")  # type: ignore[arg-type]
        )
        evidence_ref = candidate.get("evidence_units_ref")
        if require_evidence_units_ref and (
            not isinstance(evidence_ref, Mapping)
            or set(evidence_ref) != {"artifact_id", "sha256"}
            or evidence_ref.get("artifact_id") != evidence_ref.get("sha256")
            or not isinstance(evidence_ref.get("sha256"), str)
            or len(evidence_ref["sha256"]) != 64
            or any(
                character not in "0123456789abcdef"
                for character in evidence_ref["sha256"]
            )
        ):
            raise ValueError("Project Record evidence units ref is invalid")
        binding = candidate.get("evidence_binding")
        if require_material_bindings and not isinstance(binding, Mapping):
            raise ValueError("Project Record evidence binding is absent")
        if isinstance(binding, Mapping):
            validate_locator_acceptance_binding(candidate["evidence_locator"], binding)
    elif change_type == "material.use_decision_recorded":
        if not isinstance(candidate.get("material_use_decision"), Mapping):
            raise ValueError("Project Record material use decision is absent")
        if require_material_bindings and candidate.get(
            "decision_sha256"
        ) != canonical_sha256(candidate["material_use_decision"]):
            raise ValueError("Project Record material use decision binding drifted")
    return candidate
