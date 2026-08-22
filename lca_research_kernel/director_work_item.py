"""Source-bound compilation from one Director work item to formal Council inputs."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Mapping, Sequence

from .council_runtime import (
    DECISION_CELL_RESPONSIBILITIES,
    INTERNAL_EFFECTS,
    ROUTE_SIGNAL_KEYS,
    SIGNAL_LEVELS,
    compile_agenda,
    select_route,
)
from .research_council_contracts import _artifact, _reject_secret_keys
from .responsibility_boundary import classify_operation, validate_operation_intent


WORK_ITEM_CONTRACT = "lca_research_kernel.research_director_work_item/v1"
COMPILED_WORK_ITEM_CONTRACT = (
    "lca_research_kernel.research_director_compiled_work_item/v1"
)
ROUTE_EVIDENCE_CONTRACT = "lca_research_kernel.director_route_evidence/v1"
WORK_ITEM_FIELDS = {
    "contract",
    "work_item_id",
    "objective",
    "lifecycle_basis_ref",
    "authority_receipt_ref",
    "input_artifacts",
    "outcome_contract",
    "claims",
    "risks",
    "procedure_evidence",
    "dispute_evidence",
    "dependency_groups",
    "review_requirement",
    "isolation_requirement",
    "flip_conditions",
    "operation_intent",
    "stop_policy",
}
LEVELS = {"low", "medium", "high", "unknown"}
LEVEL_RANK = {"unknown": -1, "low": 0, "medium": 1, "high": 2}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _level(value: object, label: str) -> str:
    normalized = _text(value, label).casefold()
    if normalized not in LEVELS:
        raise ValueError(f"{label} must be low, medium, high, or unknown")
    return normalized


def _mapping(value: object, label: str, keys: set[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError(f"{label} fields are incomplete or unsupported")
    return value


def _registered_ref(project: object, value: object, label: str, role: str = "") -> dict:
    ref = _artifact(value, label, expected_role=role or None)
    registration = None
    for event in project.events():
        if event.get("event_type") != "artifact.registered" or event.get("actor") != "controller":
            continue
        payload = event.get("payload")
        if isinstance(payload, Mapping) and payload.get("path") == ref["path"]:
            registration = payload
    if registration is None:
        raise ValueError(f"{label} is not registered in the Project Artifact Bus")
    if any(registration.get(key) != ref[key] for key in ("sha256", "bytes")):
        raise RuntimeError(f"{label} registration drifted")
    workspace_path = registration.get("workspace_path")
    if not isinstance(workspace_path, str):
        raise RuntimeError(f"{label} registration lacks workspace_path")
    path = (project.workspace / workspace_path).resolve()
    try:
        path.relative_to(project.workspace.resolve())
    except ValueError as error:
        raise RuntimeError(f"{label} registration escapes the Project workspace") from error
    if not path.is_file():
        raise RuntimeError(f"{label} registered artifact is missing")
    content = path.read_bytes()
    if len(content) != ref["bytes"] or hashlib.sha256(content).hexdigest() != ref["sha256"]:
        raise RuntimeError(f"{label} registered artifact drifted")
    return ref


def _refs(project: object, value: object, label: str) -> list[dict]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty list")
    return [
        _registered_ref(project, ref, f"{label}[{index}]")
        for index, ref in enumerate(value)
    ]


def _semantic_evidence(
    project: object,
    value: object,
    label: str,
    level_key: str,
) -> dict:
    row = _mapping(value, label, {level_key, "reason", "source_refs"})
    return {
        level_key: _level(row[level_key], f"{label}.{level_key}"),
        "reason": _text(row["reason"], f"{label}.reason"),
        "source_refs": _refs(project, row["source_refs"], f"{label}.source_refs"),
    }


def _validate_payload_registration(project: object, operation: Mapping[str, object]) -> None:
    payload_ref = operation["payload_ref"]
    registered = None
    for event in project.events():
        payload = event.get("payload")
        if (
            event.get("event_type") == "artifact.registered"
            and event.get("actor") == "controller"
            and isinstance(payload, Mapping)
            and payload.get("path") == payload_ref["path"]
        ):
            registered = payload
    if registered is None:
        raise ValueError("operation payload_ref is not registered in the Project Artifact Bus")
    if registered.get("sha256") != payload_ref["sha256"]:
        raise RuntimeError("operation payload_ref registration drifted")
    workspace = project.workspace.resolve()
    path = (workspace / str(registered.get("workspace_path"))).resolve()
    try:
        path.relative_to(workspace)
    except ValueError as error:
        raise RuntimeError(
            "operation payload_ref registration escapes the Project workspace"
        ) from error
    if (
        not path.is_file()
        or hashlib.sha256(path.read_bytes()).hexdigest() != payload_ref["sha256"]
        or path.stat().st_size != registered.get("bytes")
    ):
        raise RuntimeError("operation payload_ref registered artifact drifted")


def validate_work_item(project: object, work_item: Mapping[str, object]) -> dict:
    """Validate exact work-item semantics and every referenced Project artifact."""

    if not isinstance(work_item, Mapping) or set(work_item) != WORK_ITEM_FIELDS:
        raise ValueError("Research Director work-item fields are incomplete or unsupported")
    if work_item.get("contract") != WORK_ITEM_CONTRACT:
        raise ValueError("Research Director work-item contract is unsupported")
    _reject_secret_keys(work_item)
    lifecycle = _registered_ref(
        project, work_item["lifecycle_basis_ref"], "lifecycle_basis_ref", "lifecycle_basis"
    )
    authority = _registered_ref(
        project,
        work_item["authority_receipt_ref"],
        "authority_receipt_ref",
        "authority_receipt",
    )
    inputs = _refs(project, work_item["input_artifacts"], "input_artifacts")

    outcome_raw = _mapping(
        work_item["outcome_contract"],
        "outcome_contract",
        {"contract", "decision_importance", "acceptance_checks", "reason", "source_refs"},
    )
    checks = outcome_raw["acceptance_checks"]
    if not isinstance(checks, list) or not checks or any(
        not isinstance(row, str) or not row.strip() for row in checks
    ):
        raise ValueError("outcome_contract.acceptance_checks must be non-empty strings")
    outcome = {
        "contract": _text(outcome_raw["contract"], "outcome_contract.contract"),
        "decision_importance": _level(
            outcome_raw["decision_importance"], "outcome_contract.decision_importance"
        ),
        "acceptance_checks": [row.strip() for row in checks],
        "reason": _text(outcome_raw["reason"], "outcome_contract.reason"),
        "source_refs": _refs(
            project, outcome_raw["source_refs"], "outcome_contract.source_refs"
        ),
    }

    raw_claims = work_item["claims"]
    if not isinstance(raw_claims, list) or not raw_claims:
        raise ValueError("claims must be a non-empty list")
    claims = []
    claim_ids = set()
    for index, raw in enumerate(raw_claims):
        row = _mapping(
            raw,
            f"claims[{index}]",
            {"claim_id", "statement", "kind", "authority", "voteable", "source_refs"},
        )
        claim_id = _text(row["claim_id"], f"claims[{index}].claim_id")
        if claim_id in claim_ids:
            raise ValueError("duplicate claim_id is not allowed")
        claim_ids.add(claim_id)
        kind = _text(row["kind"], f"claims[{index}].kind").casefold()
        if kind not in {"fact", "hypothesis"}:
            raise ValueError("claim kind must be fact or hypothesis")
        authority_name = _text(row["authority"], f"claims[{index}].authority").casefold()
        if not isinstance(row["voteable"], bool):
            raise ValueError("claim voteable must be boolean")
        if authority_name == "tiangong_official" and (
            kind != "fact" or row["voteable"] is not False
        ):
            raise ValueError("TianGong official facts cannot be voteable hypotheses")
        claims.append(
            {
                "claim_id": claim_id,
                "statement": _text(row["statement"], f"claims[{index}].statement"),
                "kind": kind,
                "authority": authority_name,
                "voteable": row["voteable"],
                "source_refs": _refs(
                    project, row["source_refs"], f"claims[{index}].source_refs"
                ),
            }
        )

    raw_risks = work_item["risks"]
    if not isinstance(raw_risks, list) or not raw_risks:
        raise ValueError("risks must be a non-empty list")
    risks = []
    for index, raw in enumerate(raw_risks):
        row = _mapping(raw, f"risks[{index}]", {"risk_id", "statement", "source_refs"})
        risks.append(
            {
                "risk_id": _text(row["risk_id"], f"risks[{index}].risk_id"),
                "statement": _text(row["statement"], f"risks[{index}].statement"),
                "source_refs": _refs(
                    project, row["source_refs"], f"risks[{index}].source_refs"
                ),
            }
        )

    procedure = _semantic_evidence(
        project, work_item["procedure_evidence"], "procedure_evidence", "determinism"
    )
    dispute = _semantic_evidence(
        project, work_item["dispute_evidence"], "dispute_evidence", "level"
    )
    dependency_raw = _mapping(
        work_item["dependency_groups"],
        "dependency_groups",
        {"strength", "groups", "reason", "source_refs"},
    )
    raw_groups = dependency_raw["groups"]
    if not isinstance(raw_groups, list) or not raw_groups:
        raise ValueError("dependency_groups.groups must be non-empty")
    groups = []
    for index, raw in enumerate(raw_groups):
        row = _mapping(raw, f"dependency_groups.groups[{index}]", {"group_id", "source_refs"})
        groups.append(
            {
                "group_id": _text(row["group_id"], f"dependency_groups.groups[{index}].group_id"),
                "source_refs": _refs(
                    project,
                    row["source_refs"],
                    f"dependency_groups.groups[{index}].source_refs",
                ),
            }
        )
    dependency = {
        "strength": _level(dependency_raw["strength"], "dependency_groups.strength"),
        "groups": groups,
        "reason": _text(dependency_raw["reason"], "dependency_groups.reason"),
        "source_refs": _refs(
            project, dependency_raw["source_refs"], "dependency_groups.source_refs"
        ),
    }
    review_raw = _mapping(
        work_item["review_requirement"],
        "review_requirement",
        {"adversarial_value", "clean_acceptance_required", "reason", "source_refs"},
    )
    if not isinstance(review_raw["clean_acceptance_required"], bool):
        raise ValueError("review_requirement.clean_acceptance_required must be boolean")
    review = {
        "adversarial_value": _level(
            review_raw["adversarial_value"], "review_requirement.adversarial_value"
        ),
        "clean_acceptance_required": review_raw["clean_acceptance_required"],
        "reason": _text(review_raw["reason"], "review_requirement.reason"),
        "source_refs": _refs(
            project, review_raw["source_refs"], "review_requirement.source_refs"
        ),
    }
    isolation = _semantic_evidence(
        project, work_item["isolation_requirement"], "isolation_requirement", "value"
    )

    raw_flips = work_item["flip_conditions"]
    if not isinstance(raw_flips, list) or not raw_flips:
        raise ValueError("flip_conditions must be a non-empty list")
    flips = []
    for index, raw in enumerate(raw_flips):
        row = _mapping(
            raw,
            f"flip_conditions[{index}]",
            {"condition_id", "statement", "potential", "source_refs"},
        )
        flips.append(
            {
                "condition_id": _text(row["condition_id"], f"flip_conditions[{index}].condition_id"),
                "statement": _text(row["statement"], f"flip_conditions[{index}].statement"),
                "potential": _level(row["potential"], f"flip_conditions[{index}].potential"),
                "source_refs": _refs(
                    project, row["source_refs"], f"flip_conditions[{index}].source_refs"
                ),
            }
        )

    operation = validate_operation_intent(work_item["operation_intent"])
    _validate_payload_registration(project, operation)
    stop_raw = _mapping(
        work_item["stop_policy"],
        "stop_policy",
        {
            "stop_conditions",
            "worker_stop_condition",
            "max_transitions",
            "max_no_progress_rounds",
            "max_route_failures",
            "source_refs",
        },
    )
    stop_conditions = stop_raw["stop_conditions"]
    if not isinstance(stop_conditions, list) or not stop_conditions or any(
        not isinstance(row, str) or not row.strip() for row in stop_conditions
    ):
        raise ValueError("stop_policy.stop_conditions must be non-empty strings")
    for key, expected in (
        ("max_transitions", 8),
        ("max_no_progress_rounds", 2),
        ("max_route_failures", 3),
    ):
        if stop_raw[key] != expected:
            raise ValueError(f"stop_policy.{key} must equal the stable Director threshold")
    stop = {
        "stop_conditions": [row.strip() for row in stop_conditions],
        "worker_stop_condition": _text(
            stop_raw["worker_stop_condition"], "stop_policy.worker_stop_condition"
        ),
        "max_transitions": 8,
        "max_no_progress_rounds": 2,
        "max_route_failures": 3,
        "source_refs": _refs(project, stop_raw["source_refs"], "stop_policy.source_refs"),
    }
    normalized_work_item_id = _text(
        work_item["work_item_id"], "work_item_id"
    ).casefold()
    _validate_work_item_id(normalized_work_item_id)
    return {
        "contract": WORK_ITEM_CONTRACT,
        "work_item_id": normalized_work_item_id,
        "objective": _text(work_item["objective"], "objective"),
        "lifecycle_basis_ref": lifecycle,
        "authority_receipt_ref": authority,
        "input_artifacts": inputs,
        "outcome_contract": outcome,
        "claims": claims,
        "risks": risks,
        "procedure_evidence": procedure,
        "dispute_evidence": dispute,
        "dependency_groups": dependency,
        "review_requirement": review,
        "isolation_requirement": isolation,
        "flip_conditions": flips,
        "operation_intent": operation,
        "stop_policy": stop,
    }


def _validate_work_item_id(value: str) -> str:
    if re.fullmatch(r"[a-z0-9][a-z0-9._-]*", value) is None:
        raise ValueError("work_item_id must be a safe lowercase identifier")
    return value


def compile_route_evidence(work_item: Mapping[str, object]) -> dict:
    """Map semantic, source-bound facts to the accepted eight Council signals."""

    flip = max(
        work_item["flip_conditions"],
        key=lambda row: LEVEL_RANK[str(row["potential"])],
    )
    groups = work_item["dependency_groups"]["groups"]
    independence = "high" if len(groups) > 1 else "low"
    sources = []
    for group in groups:
        sources.extend(group["source_refs"])
    basis = {
        "scientific_importance": {
            "level": work_item["outcome_contract"]["decision_importance"],
            "reason": work_item["outcome_contract"]["reason"],
            "source_refs": work_item["outcome_contract"]["source_refs"],
        },
        "dispute_level": {
            "level": work_item["dispute_evidence"]["level"],
            "reason": work_item["dispute_evidence"]["reason"],
            "source_refs": work_item["dispute_evidence"]["source_refs"],
        },
        "procedure_determinism": {
            "level": work_item["procedure_evidence"]["determinism"],
            "reason": work_item["procedure_evidence"]["reason"],
            "source_refs": work_item["procedure_evidence"]["source_refs"],
        },
        "dependency_strength": {
            "level": work_item["dependency_groups"]["strength"],
            "reason": work_item["dependency_groups"]["reason"],
            "source_refs": work_item["dependency_groups"]["source_refs"],
        },
        "independent_evidence_value": {
            "level": independence,
            "reason": (
                "Multiple independent source groups justify independent evidence work."
                if len(groups) > 1
                else "Only one declared source group exists."
            ),
            "source_refs": sources,
        },
        "adversarial_review_value": {
            "level": work_item["review_requirement"]["adversarial_value"],
            "reason": work_item["review_requirement"]["reason"],
            "source_refs": work_item["review_requirement"]["source_refs"],
        },
        "context_isolation_value": {
            "level": work_item["isolation_requirement"]["value"],
            "reason": work_item["isolation_requirement"]["reason"],
            "source_refs": work_item["isolation_requirement"]["source_refs"],
        },
        "conclusion_flip_potential": {
            "level": flip["potential"],
            "reason": flip["statement"],
            "source_refs": flip["source_refs"],
        },
    }
    signals = {key: basis[key]["level"] for key in ROUTE_SIGNAL_KEYS}
    body = {"contract": ROUTE_EVIDENCE_CONTRACT, "signals": signals, "basis": basis}
    return {**body, "route_evidence_content_sha256": _sha256(body)}


def compile_cell_request(
    work_item: Mapping[str, object], agenda: Mapping[str, object], route_evidence: Mapping[str, object]
) -> dict:
    """Compile only the dynamic responsibilities justified by route evidence."""

    signals = route_evidence["signals"]
    roles = {"model_and_method_judgment"}
    if signals["independent_evidence_value"] == "high":
        roles.add("evidence_investigation")
    if (
        signals["adversarial_review_value"] == "high"
        or signals["dispute_level"] in {"medium", "high"}
        or signals["conclusion_flip_potential"] in {"medium", "high"}
    ):
        roles.add("adversarial_challenge")
    if (
        work_item["review_requirement"]["clean_acceptance_required"]
        or signals["context_isolation_value"] == "high"
    ):
        roles.add("independent_acceptance")
    if not roles.issubset(DECISION_CELL_RESPONSIBILITIES):
        raise ValueError("compiled Decision Cell responsibility is unsupported")
    decision = classify_operation(work_item["operation_intent"])
    allowed = []
    if decision.disposition == "proceed":
        effect = work_item["operation_intent"]["effect"]
        if effect in INTERNAL_EFFECTS:
            allowed = [effect]
    claim_ids = [row["claim_id"] for row in work_item["claims"]]
    return {
        "agenda_revision": agenda["agenda_revision"],
        "decision_id": work_item["work_item_id"],
        "objective": work_item["objective"],
        "input_artifacts": work_item["input_artifacts"],
        "claims": work_item["claims"],
        "roles": sorted(roles),
        "stop_conditions": work_item["stop_policy"]["stop_conditions"],
        "flip_conditions": [row["statement"] for row in work_item["flip_conditions"]],
        "validation_conditions": [
            {
                "condition_id": f"acceptance-{index + 1}",
                "statement": check,
                "claim_ids": claim_ids,
            }
            for index, check in enumerate(work_item["outcome_contract"]["acceptance_checks"])
        ],
        "allowed_effects": allowed,
        "output_contract": work_item["outcome_contract"]["contract"],
        "worker_stop_condition": work_item["stop_policy"]["worker_stop_condition"],
    }


def compile_work_item(project: object, work_item: Mapping[str, object]) -> dict:
    """Compile one accepted work item into explainable existing Council inputs."""

    normalized = validate_work_item(project, work_item)
    facts = [
        {
            "fact_id": row["claim_id"],
            "statement": row["statement"],
            "authority": row["authority"],
            "source_refs": row["source_refs"],
        }
        for row in normalized["claims"]
        if row["kind"] == "fact"
    ]
    hypotheses = [
        {
            "hypothesis_id": row["claim_id"],
            "statement": row["statement"],
            "source_refs": row["source_refs"],
        }
        for row in normalized["claims"]
        if row["kind"] == "hypothesis"
    ]
    agenda = compile_agenda(
        {
            "goal": normalized["objective"],
            "authority_receipt_ref": normalized["authority_receipt_ref"],
            "facts": facts,
            "hypotheses": hypotheses,
            "risks": normalized["risks"],
            "plan": [
                {
                    "step_id": normalized["work_item_id"],
                    "objective": normalized["objective"],
                    "source_refs": normalized["input_artifacts"],
                }
            ],
        }
    )
    route_evidence = compile_route_evidence(normalized)
    route_decision = select_route(agenda, route_evidence["signals"])
    cell_request = (
        compile_cell_request(normalized, agenda, route_evidence)
        if route_decision["route"] == "bounded_decision_cell"
        else None
    )
    body = {
        "contract": COMPILED_WORK_ITEM_CONTRACT,
        "work_item_id": normalized["work_item_id"],
        "work_item_content_sha256": _sha256(normalized),
        "agenda": agenda,
        "signals": route_evidence["signals"],
        "route_evidence": route_evidence,
        "route_decision": route_decision,
        "cell_request": cell_request,
        "responsibility_decision": classify_operation(normalized["operation_intent"]).__dict__,
        "operation_intent": normalized["operation_intent"],
        "lifecycle_basis_ref": normalized["lifecycle_basis_ref"],
        "authority_receipt_ref": normalized["authority_receipt_ref"],
        "input_artifacts": normalized["input_artifacts"],
        "outcome_contract": normalized["outcome_contract"],
        "stop_policy": normalized["stop_policy"],
    }
    return {**body, "compiled_content_sha256": _sha256(body)}
