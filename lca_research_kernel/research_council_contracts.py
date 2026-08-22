"""Pure, fail-closed contracts for Research Council state artifacts."""

import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Dict, List, Mapping, Optional, Sequence


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SECRET_KEY_MARKERS = {
    "accesstoken",
    "apikey",
    "apitoken",
    "authorization",
    "clientsecret",
    "credential",
    "credentials",
    "password",
    "passwd",
    "privatekey",
    "secret",
    "token",
}
_FORWARD_CHANGE_KINDS = (
    "evidence",
    "experiment",
    "hypothesis_eliminated",
    "risk",
    "flip_condition",
    "artifact",
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _reject_secret_keys(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("mapping keys must be strings")
            comparable = re.sub(r"[^a-z0-9]", "", key.casefold())
            if any(marker in comparable for marker in _SECRET_KEY_MARKERS):
                raise ValueError("secret-like key is not allowed")
            _reject_secret_keys(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_secret_keys(child)


def _text(value: object, field: str, *, casefold: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    normalized = value.strip()
    return normalized.casefold() if casefold else normalized


def _normalize(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("mapping keys must be strings")
        return {key: _normalize(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_normalize(child) for child in value]
    raise ValueError("contract inputs must contain only JSON-compatible values")


def _artifact(
    value: object, field: str, *, expected_role: Optional[str] = None
) -> Dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} authority/artifact binding is required")
    _reject_secret_keys(value)
    required = {"role", "path", "sha256", "bytes"}
    if set(value) != required:
        raise ValueError(f"{field} must contain exactly role, path, sha256, and bytes")

    role = _text(value["role"], f"{field}.role", casefold=True)
    if expected_role is not None and role != expected_role:
        raise ValueError(f"{field}.role must be {expected_role}")
    path = _text(value["path"], f"{field}.path")
    if (
        "\\" in path
        or "\x00" in path
        or path.startswith("/")
        or re.match(r"^[a-zA-Z]:", path)
        or PurePosixPath(path).is_absolute()
    ):
        raise ValueError(f"{field}.path must be workspace-relative")
    parts = PurePosixPath(path).parts
    if any(part == ".." for part in parts):
        raise ValueError(f"{field}.path must not contain traversal")
    if not parts or any(part in ("", ".") for part in parts):
        raise ValueError(f"{field}.path must be a safe workspace-relative path")
    path = str(PurePosixPath(path))

    digest = value["sha256"]
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ValueError(f"{field}.sha256 must be a lowercase hexadecimal SHA-256")
    size = value["bytes"]
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValueError(f"{field}.bytes must be a positive integer")
    return {"role": role, "path": path, "sha256": digest, "bytes": size}


def _artifacts(values: object, field: str, *, required: bool = False) -> List[Dict[str, object]]:
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"{field} must be a list")
    normalized = [_artifact(value, f"{field}[{index}]") for index, value in enumerate(values)]
    if required and not normalized:
        raise ValueError(f"{field} must not be empty")
    normalized.sort(key=lambda value: _canonical_json(value))
    fingerprints = [_canonical_json(value) for value in normalized]
    if len(fingerprints) != len(set(fingerprints)):
        raise ValueError(f"duplicate {field} entries are not allowed")
    return normalized


def _records(values: object, field: str, id_field: str) -> List[Dict[str, object]]:
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"{field} must be a list")
    records: List[Dict[str, object]] = []
    seen = set()
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            raise ValueError(f"{field}[{index}] must be an object")
        _reject_secret_keys(value)
        record = _normalize(value)
        assert isinstance(record, dict)
        stable_id = _text(record.get(id_field), f"{field}[{index}].{id_field}", casefold=True)
        if stable_id in seen:
            raise ValueError(f"duplicate {id_field} is not allowed")
        seen.add(stable_id)
        record[id_field] = stable_id
        records.append(record)
    records.sort(key=lambda record: (str(record[id_field]).casefold(), _canonical_json(record)))
    return records


def _strings(values: object, field: str, *, required: bool = False) -> List[str]:
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"{field} must be a list")
    normalized = [_text(value, f"{field}[{index}]") for index, value in enumerate(values)]
    if required and not normalized:
        raise ValueError(f"{field} must not be empty")
    normalized.sort(key=lambda value: (value.casefold(), value))
    if len({value.casefold() for value in normalized}) != len(normalized):
        raise ValueError(f"duplicate {field} entries are not allowed")
    return normalized


def _finish(body: Dict[str, object], noun: str) -> Dict[str, object]:
    digest = hashlib.sha256(_canonical_json(body)).hexdigest()
    result = dict(body)
    result[f"{noun}_content_sha256"] = digest
    result[f"{noun}_id"] = digest[:16]
    return result


def compile_research_agenda(
    *,
    goal: str,
    authority_receipt_ref: Mapping[str, object],
    facts: Sequence[Mapping[str, object]],
    hypotheses: Sequence[Mapping[str, object]],
    risks: Sequence[Mapping[str, object]],
    plan: Sequence[Mapping[str, object]],
) -> Dict[str, object]:
    """Compile a canonical, authority-bound Research Director agenda."""
    _reject_secret_keys((facts, hypotheses, risks, plan))
    body: Dict[str, object] = {
        "contract": "lca_research_kernel.research_agenda/v1",
        "goal": _text(goal, "goal"),
        "authority_receipt_ref": _artifact(
            authority_receipt_ref,
            "authority_receipt_ref",
            expected_role="authority_receipt",
        ),
        "facts": _records(facts, "facts", "fact_id"),
        "hypotheses": _records(hypotheses, "hypotheses", "hypothesis_id"),
        "risks": _records(risks, "risks", "risk_id"),
        "plan": _records(plan, "plan", "step_id"),
        "next_route": "open-or-skip-decision-cell",
    }
    return _finish(body, "agenda")


def compile_decision_cell(
    *,
    agenda_ref: Mapping[str, object],
    authority_receipt_ref: Mapping[str, object],
    decision_id: str,
    objective: str,
    input_artifacts: Sequence[Mapping[str, object]],
    claims: Sequence[Mapping[str, object]],
    roles: Sequence[str],
    stop_conditions: Sequence[str],
    flip_conditions: Sequence[str],
) -> Dict[str, object]:
    """Compile a bounded, source-bound Decision Cell contract."""
    artifacts = _artifacts(input_artifacts, "input_artifacts", required=True)
    normalized_claims = _records(claims, "claims", "claim_id")
    bound_artifacts = {_canonical_json(value) for value in artifacts}
    for index, claim in enumerate(normalized_claims):
        source_refs = _artifacts(claim.get("source_refs"), f"claims[{index}].source_refs", required=True)
        if any(_canonical_json(ref) not in bound_artifacts for ref in source_refs):
            raise ValueError("unbound claim source_refs are not allowed")
        claim["source_refs"] = source_refs

    body: Dict[str, object] = {
        "contract": "lca_research_kernel.decision_cell/v1",
        "agenda_ref": _artifact(
            agenda_ref, "agenda_ref", expected_role="research_agenda"
        ),
        "authority_receipt_ref": _artifact(
            authority_receipt_ref,
            "authority_receipt_ref",
            expected_role="authority_receipt",
        ),
        "decision_id": _text(decision_id, "decision_id", casefold=True),
        "objective": _text(objective, "objective"),
        "input_artifacts": artifacts,
        "claims": normalized_claims,
        "roles": _strings(roles, "roles", required=True),
        "stop_conditions": _strings(stop_conditions, "stop_conditions", required=True),
        "flip_conditions": _strings(flip_conditions, "flip_conditions", required=True),
        "next_route": "investigate-decision-cell",
    }
    return _finish(body, "cell")


def compile_incumbent(
    *,
    cell_ref: Mapping[str, object],
    candidate_id: str,
    conclusion: str,
    claim_refs: Sequence[Mapping[str, object]],
    evidence_artifacts: Sequence[Mapping[str, object]],
    dissent: Sequence[Mapping[str, object]],
    validation_status: str,
) -> Dict[str, object]:
    """Compile only a validated best-current result, retaining dissent."""
    status = _text(validation_status, "validation_status", casefold=True)
    if status != "passed":
        raise ValueError("validation_status must be passed")
    normalized_dissent = _records(dissent, "dissent", "dissent_id")
    for index, item in enumerate(normalized_dissent):
        item["flip_condition"] = _text(
            item.get("flip_condition"), f"dissent[{index}].flip_condition"
        )

    body: Dict[str, object] = {
        "contract": "lca_research_kernel.incumbent/v1",
        "cell_ref": _artifact(cell_ref, "cell_ref", expected_role="decision_cell"),
        "candidate_id": _text(candidate_id, "candidate_id", casefold=True),
        "conclusion": _text(conclusion, "conclusion"),
        "claim_refs": _artifacts(claim_refs, "claim_refs", required=True),
        "evidence_artifacts": _artifacts(
            evidence_artifacts, "evidence_artifacts", required=True
        ),
        "dissent": normalized_dissent,
        "validation_status": status,
        "next_route": "preserve-or-compare-incumbent",
    }
    return _finish(body, "incumbent")


def compile_progress_update(
    *,
    cell_ref: Mapping[str, object],
    changes: Sequence[Mapping[str, object]],
    progress_classification: Optional[str] = None,
) -> Dict[str, object]:
    """Compile progress, deriving forward change only from admissible kinds."""
    del progress_classification
    if not isinstance(changes, (list, tuple)):
        raise ValueError("changes must be a list")
    normalized_changes: List[Dict[str, object]] = []
    observed_kinds = set()
    for index, value in enumerate(changes):
        if not isinstance(value, Mapping):
            raise ValueError(f"changes[{index}] must be an object")
        _reject_secret_keys(value)
        change = _normalize(value)
        assert isinstance(change, dict)
        kind = _text(change.get("kind"), f"changes[{index}].kind", casefold=True)
        change["kind"] = kind
        if kind in _FORWARD_CHANGE_KINDS and "artifact_ref" not in change:
            raise ValueError(f"changes[{index}].artifact_ref is required for {kind}")
        if "artifact_ref" in change:
            change["artifact_ref"] = _artifact(
                change["artifact_ref"], f"changes[{index}].artifact_ref"
            )
        normalized_changes.append(change)
        observed_kinds.add(kind)
    normalized_changes.sort(key=_canonical_json)
    forward_kinds = [kind for kind in _FORWARD_CHANGE_KINDS if kind in observed_kinds]
    classification = "forward_change" if forward_kinds else "no_forward_change"
    body: Dict[str, object] = {
        "contract": "lca_research_kernel.progress_update/v1",
        "cell_ref": _artifact(cell_ref, "cell_ref", expected_role="decision_cell"),
        "changes": normalized_changes,
        "forward_change_kinds": forward_kinds,
        "progress_classification": classification,
        "next_route": (
            "continue-current-cell"
            if classification == "forward_change"
            else "record-no-forward-change"
        ),
    }
    return _finish(body, "progress")
