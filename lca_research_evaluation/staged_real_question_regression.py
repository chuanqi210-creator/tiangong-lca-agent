"""Fail-closed contracts for the staged real-question development regression."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Mapping, Sequence


CONTRACT_PATH = Path("docs/real-question-staged-regression-contract.json")
CONTRACT_ID = "staged_real_question_regression_contract/v1"
ACCEPTED_CODE_BASIS = "26f4edcdde81de4c780cdaf4485b9ae0a16b4d32"
PDF_SHA256 = "73101eec6999b27f4409c6e90f1a41343af694cb41414af6c55fb64482d6775c"
REGRESSION_QUESTION_IDS = [
    "Q3",
    "Q4",
    "Q6",
    "Q7",
    "Q8",
    "Q10",
    "Q11",
    "Q12",
    "Q13",
    "Q14",
    "Q15",
    "Q16",
    "Q17",
    "Q18",
    "Q19",
    "Q20",
]
QUESTION_ROLES = {
    "Q1": "completed_development_reference",
    "Q2": "contaminated_calibration_only",
    "Q3": "development_regression_population",
    "Q4": "development_regression_population",
    "Q5": "completed_development_reference",
    "Q6": "development_regression_population",
    "Q7": "development_regression_population",
    "Q8": "development_regression_population",
    "Q9": "completed_development_reference",
    "Q10": "development_regression_population",
    "Q11": "development_regression_population",
    "Q12": "development_regression_population",
    "Q13": "development_regression_population",
    "Q14": "development_regression_population",
    "Q15": "development_regression_population",
    "Q16": "development_regression_population",
    "Q17": "development_regression_population",
    "Q18": "development_regression_population",
    "Q19": "development_regression_population",
    "Q20": "development_regression_population",
}
TRANCHE_SIZES = [1, 3, 12]
SELECTION_POLICY = {
    "algorithm": "sha256_lexicographic_ascending",
    "input_fields": [
        "accepted_code_commit",
        "pdf_sha256",
        "question_pack_sha256",
        "question_id",
    ],
    "semantic_selection_forbidden": True,
    "standard_answer_visibility": "forbidden",
}
STOP_POLICY = {
    "hard_veto": "stop_immediately",
    "same_failure_class_threshold": 2,
    "failure_classes": [
        "prompt_failure",
        "context_failure",
        "harness_failure",
        "loop_failure",
        "model_limitation",
    ],
    "provider_capacity": "checkpoint_without_scientific_result",
    "unknown_failure": "fail_closed",
}
EVIDENCE_POLICY = {
    "raw_before_reference": True,
    "failed_row_deletion": "forbidden",
    "cross_epoch_splicing": "forbidden",
    "threshold_reduction": "forbidden",
    "grader_change": "regrade_existing_traces",
    "acceptance_change": "rerun_clean_acceptance",
    "unknown_change": "unknown_change_fail_closed",
}
CLAIM_CEILING = {
    "supported": [
        "development_regression_on_revealed_pdf_pack",
        "machine_reproducible_product_interface_evidence",
    ],
    "unsupported": [
        "unseen_question_generalization_demonstrated",
        "all_lca_or_cbam_workflows_supported",
        "automated_scoring_equals_human_expert_acceptance",
        "provider_superiority",
    ],
}
IMMUTABLE_EVIDENCE = {
    "question_pack": {
        "path": "outputs/real-question-optimization/73101eec6999b27f/question-pack/real-question-pack.json",
        "sha256": "84d6a671c89e34a807818a3662bfec1643e41ff153689ea466dd29a0fb81a3e1",
    },
    "contamination_ledger": {
        "path": "outputs/real-question-optimization/73101eec6999b27f/contamination-ledger.json",
        "sha256": "4a8cae87526fde869730c82c6c4116357158b393bceed3f790975567eba9dc6c",
    },
    "failed_v3_baseline": {
        "path": "outputs/real-question-optimization/73101eec6999b27f/blind-execution-v3/raw-baseline-acceptance.json",
        "sha256": "8540533d38178ee1d9c5e7acc054111b7abfc73db96298c38e32fd1272175eb0",
    },
    "minimum_path_acceptance": {
        "path": "outputs/real-question-optimization/73101eec6999b27f/minimum-real-path/acceptance.json",
        "sha256": "301d99b8d6f2b6cf2f4c739946e4f586ea1f5836a209f4697c899e5e6a8bde0c",
    },
    "adaptive_answer_repair_acceptance": {
        "path": "outputs/real-question-optimization/adaptive-answer-repair/acceptance-v3.json",
        "sha256": "c05131f55c2c623fa12f03c017a899738d713795423858882f13281d2a0cd89e",
    },
}
NEXT_ROUTES = ["freeze-staged-regression-epoch-and-select-preflight"]
EXPECTED_FIELDS = {
    "accepted_code_basis",
    "claim_ceiling",
    "contract",
    "contract_sha256",
    "evidence_policy",
    "immutable_evidence",
    "next_routes",
    "pdf_sha256",
    "population",
    "question_roles",
    "selection_policy",
    "stop_policy",
    "tranche_sizes",
    "unknown_field_policy",
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


def _verify_repository_ref(root: Path, value: object, label: str) -> None:
    if not isinstance(value, Mapping) or set(value) != {"path", "sha256"}:
        raise ValueError(f"{label} reference is invalid")
    path = value.get("path")
    digest = value.get("sha256")
    if not isinstance(path, str) or not isinstance(digest, str):
        raise ValueError(f"{label} reference is invalid")
    candidate = (Path(root).resolve() / path).resolve()
    try:
        candidate.relative_to(Path(root).resolve())
    except ValueError as error:
        raise ValueError(f"{label} reference escapes the repository") from error
    if (
        not candidate.is_file()
        or candidate.is_symlink()
        or hashlib.sha256(candidate.read_bytes()).hexdigest() != digest
    ):
        raise ValueError(f"{label} reference bytes drifted")


def validate_staged_regression_contract(
    value: Mapping[str, object], root: Path
) -> dict:
    """Validate the fixed population, evidence roles and stop policy."""

    if not isinstance(value, Mapping) or set(value) != EXPECTED_FIELDS:
        raise ValueError("staged regression contract fields are invalid")
    normalized = deepcopy(dict(value))
    if normalized["contract"] != CONTRACT_ID:
        raise ValueError("staged regression contract identity drifted")
    if normalized["accepted_code_basis"] != ACCEPTED_CODE_BASIS:
        raise ValueError("accepted code basis drifted")
    if normalized["pdf_sha256"] != PDF_SHA256:
        raise ValueError("PDF identity drifted")
    if normalized["population"] != REGRESSION_QUESTION_IDS:
        raise ValueError("regression population drifted")
    if normalized["question_roles"] != QUESTION_ROLES:
        raise ValueError("question roles drifted")
    if normalized["tranche_sizes"] != TRANCHE_SIZES:
        raise ValueError("regression tranche sizes drifted")
    if normalized["selection_policy"] != SELECTION_POLICY:
        raise ValueError("selection policy drifted")
    if normalized["stop_policy"] != STOP_POLICY:
        raise ValueError("stop policy drifted")
    if normalized["evidence_policy"] != EVIDENCE_POLICY:
        raise ValueError("evidence policy drifted")
    if normalized["claim_ceiling"] != CLAIM_CEILING:
        raise ValueError("claim ceiling drifted")
    if normalized["immutable_evidence"] != IMMUTABLE_EVIDENCE:
        raise ValueError("immutable evidence drifted")
    for evidence_id, ref in normalized["immutable_evidence"].items():
        _verify_repository_ref(Path(root), ref, f"immutable evidence {evidence_id}")
    if normalized["next_routes"] != NEXT_ROUTES:
        raise ValueError("staged regression requires exactly one next route")
    if normalized["unknown_field_policy"] != "reject":
        raise ValueError("unknown contract fields must be rejected")
    unsigned = deepcopy(normalized)
    stored = unsigned.pop("contract_sha256")
    if not isinstance(stored, str) or stored != _canonical_sha256(unsigned):
        raise ValueError("staged regression contract self hash drifted")
    return normalized


def load_staged_regression_contract(root: Path) -> dict:
    path = Path(root).resolve() / CONTRACT_PATH
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("staged regression contract is unreadable") from error
    if not isinstance(value, Mapping):
        raise ValueError("staged regression contract must be an object")
    return validate_staged_regression_contract(value, Path(root))


def _require_sha(value: object, label: str, *, length: int = 64) -> str:
    if (
        not isinstance(value, str)
        or len(value) != length
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} is not an exact lowercase hexadecimal identity")
    return value


def _normalize_lock_refs(values: Sequence[Mapping[str, object]]) -> list:
    refs = []
    seen = set()
    if not values:
        raise ValueError("regression Epoch requires execution lock references")
    for value in values:
        if not isinstance(value, Mapping) or set(value) != {"path", "sha256"}:
            raise ValueError("execution lock reference is invalid")
        path = value.get("path")
        digest = value.get("sha256")
        if (
            not isinstance(path, str)
            or not path
            or Path(path).is_absolute()
            or ".." in Path(path).parts
        ):
            raise ValueError("execution lock path is invalid")
        _require_sha(digest, "execution lock SHA-256")
        identity = (path, digest)
        if identity in seen:
            raise ValueError("execution lock reference is duplicated")
        seen.add(identity)
        refs.append({"path": path, "sha256": digest})
    return sorted(refs, key=lambda row: (row["path"], row["sha256"]))


def build_regression_epoch(
    *,
    accepted_commit: str,
    pdf_sha256: str,
    question_pack_sha256: str,
    execution_lock_refs: Sequence[Mapping[str, object]],
) -> dict:
    """Freeze the only inputs allowed to influence deterministic tranche order."""

    accepted_commit = _require_sha(
        accepted_commit, "accepted code commit", length=40
    )
    pdf_sha256 = _require_sha(pdf_sha256, "PDF SHA-256")
    question_pack_sha256 = _require_sha(
        question_pack_sha256, "question-pack SHA-256"
    )
    selection_rows = []
    for question_id in REGRESSION_QUESTION_IDS:
        selection_key = hashlib.sha256(
            "\n".join(
                (accepted_commit, pdf_sha256, question_pack_sha256, question_id)
            ).encode("utf-8")
        ).hexdigest()
        selection_rows.append(
            {"question_id": question_id, "selection_key_sha256": selection_key}
        )
    selection_rows.sort(
        key=lambda row: (row["selection_key_sha256"], row["question_id"])
    )
    epoch = {
        "contract": "staged_regression_epoch/v1",
        "accepted_code_commit": accepted_commit,
        "pdf_sha256": pdf_sha256,
        "question_pack_sha256": question_pack_sha256,
        "execution_lock_refs": _normalize_lock_refs(execution_lock_refs),
        "population": deepcopy(REGRESSION_QUESTION_IDS),
        "selection_rows": selection_rows,
        "ordered_question_ids": [row["question_id"] for row in selection_rows],
        "standard_answer_visibility": "forbidden_until_raw_evidence_frozen",
        "claim_scope": "development_regression_only",
    }
    epoch["epoch_sha256"] = _canonical_sha256(epoch)
    return epoch


def _validate_epoch(value: Mapping[str, object]) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError("regression Epoch must be an object")
    epoch = deepcopy(dict(value))
    stored = epoch.pop("epoch_sha256", None)
    if (
        epoch.get("contract") != "staged_regression_epoch/v1"
        or epoch.get("population") != REGRESSION_QUESTION_IDS
        or epoch.get("standard_answer_visibility")
        != "forbidden_until_raw_evidence_frozen"
        or epoch.get("claim_scope") != "development_regression_only"
        or stored != _canonical_sha256(epoch)
    ):
        raise ValueError("regression Epoch is invalid or drifted")
    ordered = epoch.get("ordered_question_ids")
    if sorted(ordered or []) != sorted(REGRESSION_QUESTION_IDS):
        raise ValueError("regression Epoch question ordering is invalid")
    epoch["epoch_sha256"] = stored
    return epoch


def select_regression_tranches(epoch: Mapping[str, object]) -> dict:
    """Partition the frozen order without looking at question semantics."""

    normalized = _validate_epoch(epoch)
    ordered = normalized["ordered_question_ids"]
    offset = 0
    tranches = []
    names = ("preflight", "small_batch", "remaining_batch")
    for index, (name, size) in enumerate(zip(names, TRANCHE_SIZES)):
        question_ids = ordered[offset : offset + size]
        tranches.append(
            {
                "tranche_index": index,
                "tranche_id": name,
                "question_ids": question_ids,
                "question_count": len(question_ids),
            }
        )
        offset += size
    plan = {
        "contract": "staged_regression_tranche_plan/v1",
        "epoch_sha256": normalized["epoch_sha256"],
        "tranches": tranches,
        "selection_basis": "frozen_sha256_order_only",
    }
    plan["plan_sha256"] = _canonical_sha256(plan)
    return plan


def decide_regression_continuation(
    *,
    epoch: Mapping[str, object],
    completed_rows: Sequence[Mapping[str, object]],
    failure_observations: Sequence[Mapping[str, object]],
) -> dict:
    """Apply the fixed fail-closed stop policy before authorizing another call."""

    normalized = _validate_epoch(epoch)
    known_ids = set(normalized["ordered_question_ids"])
    completed_ids = []
    for row in completed_rows:
        if not isinstance(row, Mapping) or row.get("question_id") not in known_ids:
            raise ValueError("completed regression row is invalid")
        completed_ids.append(row["question_id"])
    if len(completed_ids) != len(set(completed_ids)):
        raise ValueError("completed regression row is duplicated")

    observations = [dict(row) for row in failure_observations]
    repeated: list[tuple[str, str]] = []
    if any(row.get("hard_veto") is True for row in observations):
        decision = "stop_hard_veto"
        reason = "hard_veto_observed"
    elif any(row.get("failure_class") == "provider_capacity" for row in observations):
        decision = "waiting_for_provider_capacity"
        reason = "provider_capacity_unavailable"
    else:
        allowed = set(STOP_POLICY["failure_classes"])
        counts: dict[tuple[str, str], int] = {}
        observation_identities: set[tuple[str, str]] = set()
        unknown = False
        for row in observations:
            failure_class = row.get("failure_class")
            if failure_class not in allowed:
                unknown = True
            else:
                component = row.get("d18_component")
                epoch_sha256 = row.get("epoch_sha256")
                question_id = row.get("question_id")
                if (
                    not isinstance(component, str)
                    or not component.strip()
                    or not isinstance(epoch_sha256, str)
                    or len(epoch_sha256) != 64
                    or any(
                        character not in "0123456789abcdef"
                        for character in epoch_sha256
                    )
                    or question_id not in known_ids
                ):
                    raise ValueError("failure observation is unattributed")
                identity = (epoch_sha256, str(question_id))
                if identity in observation_identities:
                    raise ValueError("failure observation is duplicated")
                observation_identities.add(identity)
                key = (str(failure_class), component.strip())
                counts[key] = counts.get(key, 0) + 1
        repeated = sorted(
            key
            for key, count in counts.items()
            if count >= STOP_POLICY["same_failure_class_threshold"]
        )
        if unknown:
            decision = "stop_unknown_failure"
            reason = "unknown_failure_fail_closed"
        elif repeated:
            decision = "stop_systemic_failure"
            reason = repeated[0][0]
        elif len(completed_ids) == len(known_ids):
            decision = "population_complete"
            reason = "all_frozen_questions_completed"
        else:
            decision = "continue"
            reason = "stop_threshold_not_reached"
    result = {
        "contract": "staged_regression_progress/v1",
        "epoch_sha256": normalized["epoch_sha256"],
        "decision": decision,
        "reason": reason,
        "completed_question_ids": completed_ids,
        "provider_calls_authorized": 1 if decision == "continue" else 0,
        "failure_observation_count": len(observations),
        "repeated_failure_component": (
            repeated[0][1] if repeated else None
        ),
    }
    result["progress_sha256"] = _canonical_sha256(result)
    return result


def build_population_index(
    *, output_root: Path, expected_question_ids: Sequence[str]
) -> dict:
    """Read only accepted frozen answer rows for the requested population."""

    root = Path(output_root).resolve()
    expected = list(expected_question_ids)
    if (
        not expected
        or len(expected) != len(set(expected))
        or any(question_id not in REGRESSION_QUESTION_IDS for question_id in expected)
    ):
        raise ValueError("population index question IDs are invalid")
    rows = []
    for question_id in expected:
        path = root / "runs" / question_id / "answer.json"
        if not path.is_file() or path.is_symlink():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"frozen row {question_id} is unreadable") from error
        if not isinstance(value, Mapping) or value.get("question_id") != question_id:
            raise ValueError(f"frozen row {question_id} identity drifted")
        rows.append(
            {
                "question_id": question_id,
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "status": value.get("status"),
            }
        )
    index = {
        "contract": "staged_regression_population_index/v1",
        "expected_question_ids": expected,
        "completed_question_ids": [row["question_id"] for row in rows],
        "row_refs": rows,
    }
    index["index_sha256"] = _canonical_sha256(index)
    return index


__all__ = [
    "REGRESSION_QUESTION_IDS",
    "build_population_index",
    "build_regression_epoch",
    "decide_regression_continuation",
    "load_staged_regression_contract",
    "select_regression_tranches",
    "validate_staged_regression_contract",
]
