"""Machine contract for the three-task minimum real research path."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Mapping


CONTRACT = "d18_minimum_real_research_path/v1"
QUESTION_PACK_CONTRACT = "real_question_pack/v1"
QUESTION_PACK_SHA256 = (
    "84d6a671c89e34a807818a3662bfec1643e41ff153689ea466dd29a0fb81a3e1"
)
PDF_SHA256 = (
    "73101eec6999b27f4409c6e90f1a41343af694cb41414af6c55fb64482d6775c"
)
TASK_SPECS = (
    ("Q1", "complete_user_material"),
    ("Q9", "current_official_source_required"),
    ("Q5", "critical_data_missing"),
)
ALLOWED_RESULT_STATES = (
    "provider_capacity_deferred",
    "research_active",
    "formal_result_accepted",
    "formal_limited_result_accepted",
)
TIMING_MS = {
    "candidate_preview": 90_000,
    "formal_result": 480_000,
    "hard_boundary": 720_000,
}
NEXT_ROUTE = "qualify-one-provider-for-minimum-real-path"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DISALLOWED_EXECUTION_TERMS = (
    "sealed-reference",
    "sealed_reference",
    "reference_answer",
    "grader",
    "oracle",
)


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_pack(path: Path) -> dict:
    try:
        pack = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("minimum real path question pack is unavailable") from error
    if not isinstance(pack, dict) or pack.get("contract") != QUESTION_PACK_CONTRACT:
        raise ValueError("minimum real path question pack contract is invalid")
    questions = pack.get("questions")
    if not isinstance(questions, list):
        raise ValueError("minimum real path question list is invalid")
    return pack


def _repository_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError as error:
        raise ValueError("question pack must be inside the repository") from error


def _validate_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def _validate_observation(
    value: object,
    *,
    status: str,
    fit: str,
    label: str,
) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is invalid")
    normalized = deepcopy(dict(value))
    required = {
        "source",
        "observed_at",
        "thread_id",
        "objective_sha256",
        "status",
        "fit",
    }
    if set(normalized) != required:
        raise ValueError(f"{label} fields are invalid")
    for key in ("source", "observed_at", "thread_id"):
        if not isinstance(normalized.get(key), str) or not normalized[key].strip():
            raise ValueError(f"{label} {key} is invalid")
    _validate_sha256(normalized.get("objective_sha256"), f"{label} objective")
    if normalized.get("status") != status or normalized.get("fit") != fit:
        raise ValueError(f"{label} status or fit is invalid")
    return normalized


def _selected_tasks(pack: Mapping[str, object]) -> list[dict]:
    questions = pack["questions"]
    assert isinstance(questions, list)
    by_id: dict[str, Mapping[str, object]] = {}
    for row in questions:
        if not isinstance(row, Mapping):
            raise ValueError("minimum real path question row is invalid")
        question_id = row.get("question_id")
        if not isinstance(question_id, str) or question_id in by_id:
            raise ValueError("minimum real path question identity is invalid")
        by_id[question_id] = row
    selected = []
    for question_id, role in TASK_SPECS:
        row = by_id.get(question_id)
        if row is None:
            raise ValueError(f"minimum real path question is absent: {question_id}")
        selected.append(
            {
                "question_id": question_id,
                "trajectory_role": role,
                "page_number": row.get("page_number"),
                "question_body": row.get("question_body"),
                "image_refs": deepcopy(row.get("image_refs")),
                "question_sha256": _sha256(row),
            }
        )
    return selected


def build_minimum_real_path_contract(
    question_pack_path: Path,
    *,
    accepted_code_commit: str,
    design_sha256: str,
    platform_observation: Mapping[str, object],
    superseded_goal_observation: Mapping[str, object],
) -> dict[str, object]:
    """Build one case-bounded contract without opening evaluation answers."""

    path = Path(question_pack_path)
    if _file_sha256(path) != QUESTION_PACK_SHA256:
        raise ValueError("minimum real path question pack identity drifted")
    if GIT_COMMIT_RE.fullmatch(accepted_code_commit) is None:
        raise ValueError("minimum real path code commit is invalid")
    _validate_sha256(design_sha256, "minimum real path design SHA-256")
    pack = _read_pack(path)
    current = _validate_observation(
        platform_observation,
        status="active",
        fit="exact",
        label="current platform Goal observation",
    )
    superseded = _validate_observation(
        superseded_goal_observation,
        status="blocked",
        fit="superseded_unfinished",
        label="superseded platform Goal observation",
    )
    if current["objective_sha256"] == superseded["objective_sha256"]:
        raise ValueError("platform Goal generation was reused")
    if current["thread_id"] != superseded["thread_id"]:
        raise ValueError("platform Goal thread identity drifted")
    contract = {
        "contract": CONTRACT,
        "goal_id": "prove-minimum-real-research-path",
        "accepted_code_commit": accepted_code_commit,
        "design_sha256": design_sha256,
        "question_pack": {
            "path": _repository_path(path),
            "sha256": QUESTION_PACK_SHA256,
            "source_pdf_sha256": PDF_SHA256,
            "evidence_role": "development_regression_pack",
        },
        "tasks": _selected_tasks(pack),
        "timing_ms": deepcopy(TIMING_MS),
        "allowed_result_states": list(ALLOWED_RESULT_STATES),
        "execution_input_policy": {
            "allowlist": [
                "question_id",
                "question_body",
                "page_number",
                "image_refs",
                "source_refs",
            ],
            "hidden_evaluation_material_access": False,
        },
        "forbidden_effects": [
            "external_write",
            "publication",
            "payment",
            "credential_persistence",
            "irreversible_action",
        ],
        "platform_observation": current,
        "superseded_goal_observation": superseded,
        "claim_ceiling": "development_path_evidence_only",
        "next_routes": [NEXT_ROUTE],
    }
    return validate_minimum_real_path_contract(
        contract,
        question_pack_path=path,
    )


def validate_minimum_real_path_contract(
    contract: Mapping[str, object],
    *,
    question_pack_path: Path,
) -> dict[str, object]:
    """Fail closed on task, source, Goal-generation, or route drift."""

    if not isinstance(contract, Mapping) or contract.get("contract") != CONTRACT:
        raise ValueError("minimum real path contract is unsupported")
    normalized = deepcopy(dict(contract))
    pack_path = Path(question_pack_path)
    if _file_sha256(pack_path) != QUESTION_PACK_SHA256:
        raise ValueError("minimum real path question pack identity drifted")
    pack_ref = normalized.get("question_pack")
    if (
        not isinstance(pack_ref, Mapping)
        or pack_ref.get("path") != _repository_path(pack_path)
        or pack_ref.get("sha256") != QUESTION_PACK_SHA256
        or pack_ref.get("source_pdf_sha256") != PDF_SHA256
        or pack_ref.get("evidence_role") != "development_regression_pack"
    ):
        raise ValueError("minimum real path question pack identity drifted")
    if GIT_COMMIT_RE.fullmatch(str(normalized.get("accepted_code_commit"))) is None:
        raise ValueError("minimum real path code commit is invalid")
    _validate_sha256(normalized.get("design_sha256"), "minimum real path design")
    expected_tasks = _selected_tasks(_read_pack(pack_path))
    if normalized.get("tasks") != expected_tasks:
        raise ValueError("minimum real path task identity or role drifted")
    if normalized.get("timing_ms") != TIMING_MS:
        raise ValueError("minimum real path timing contract drifted")
    if normalized.get("allowed_result_states") != list(ALLOWED_RESULT_STATES):
        raise ValueError("minimum real path result-state contract drifted")
    current = _validate_observation(
        normalized.get("platform_observation"),
        status="active",
        fit="exact",
        label="current platform Goal observation",
    )
    superseded = _validate_observation(
        normalized.get("superseded_goal_observation"),
        status="blocked",
        fit="superseded_unfinished",
        label="superseded platform Goal observation",
    )
    if current["objective_sha256"] == superseded["objective_sha256"]:
        raise ValueError("platform Goal generation was reused")
    routes = normalized.get("next_routes")
    if routes != [NEXT_ROUTE]:
        raise ValueError("minimum real path requires exactly one next route")
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True).lower()
    if any(term in encoded for term in DISALLOWED_EXECUTION_TERMS):
        raise ValueError("minimum real path execution contract leaks hidden evaluation data")
    return normalized


def canonical_json_bytes(value: object) -> bytes:
    """Return the stable checked-in encoding for the machine contract."""

    return _canonical_bytes(value)
