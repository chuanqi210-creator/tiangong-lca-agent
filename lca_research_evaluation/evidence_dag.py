"""Deterministic, layer-aware invalidation for D-18 evaluation evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, Optional


POLICY_CONTRACT = "lca_research_evaluation.evidence_invalidation_policy/v1"
LAYER_LOCK_CONTRACT = "lca_research_evaluation.evidence_layer_lock/v1"
REUSE_CONTRACT = "lca_research_evaluation.evidence_reuse_receipt/v1"
ROUTES = {
    "rerun_full_execution",
    "regrade_existing_traces",
    "rerun_clean_acceptance",
    "rebuild_report_only",
    "no_evidence_invalidation",
    "unknown_change_fail_closed",
}
STAGE_CLOSURES = {
    "execution": ["execution", "raw_evidence", "scoring", "acceptance", "claim"],
    "raw_evidence": ["raw_evidence", "scoring", "acceptance", "claim"],
    "scoring": ["scoring", "acceptance", "claim"],
    "acceptance": ["acceptance", "claim"],
    "report": ["claim"],
}
STAGE_ROUTES = {
    "execution": "rerun_full_execution",
    "raw_evidence": "rerun_full_execution",
    "scoring": "regrade_existing_traces",
    "acceptance": "rerun_clean_acceptance",
    "report": "rebuild_report_only",
}
ROUTE_PRIORITY = {
    "no_evidence_invalidation": 0,
    "rebuild_report_only": 1,
    "rerun_clean_acceptance": 2,
    "regrade_existing_traces": 3,
    "rerun_full_execution": 4,
    "unknown_change_fail_closed": 5,
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_tree_identity(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f"evidence tree is absent: {root}")
    files = []
    for path in sorted(
        (candidate for candidate in root.rglob("*") if candidate.is_file()),
        key=lambda candidate: candidate.relative_to(root).as_posix(),
    ):
        relative = path.relative_to(root).as_posix()
        files.append(
            {
                "path": relative,
                "sha256": _file_sha256(path),
                "bytes": path.stat().st_size,
            }
        )
    body = {
        "contract": "lca_research_evaluation.canonical_tree_identity/v1",
        "file_count": len(files),
        "files": files,
    }
    body["tree_sha256"] = canonical_sha256(files)
    return body


def _validate_self_hash(
    value: Mapping[str, object], field: str, *, label: str
) -> dict[str, Any]:
    normalized = deepcopy(dict(value))
    stored = normalized.pop(field, None)
    if not isinstance(stored, str) or len(stored) != 64:
        raise ValueError(f"{label} self hash is absent")
    if canonical_sha256(normalized) != stored:
        raise ValueError(f"{label} self hash drifted")
    normalized[field] = stored
    return normalized


def _matches(path: str, match: Mapping[str, object]) -> bool:
    kind = match.get("type")
    value = match.get("value")
    if not isinstance(value, str) or not value:
        raise ValueError("evidence invalidation match value is invalid")
    if kind == "exact":
        return path == value
    if kind == "prefix":
        return path.startswith(value)
    if kind == "suffix":
        return path.endswith(value)
    raise ValueError("evidence invalidation match type is invalid")


def load_invalidation_policy(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("evidence invalidation policy is unreadable") from error
    if not isinstance(value, Mapping) or value.get("contract") != POLICY_CONTRACT:
        raise ValueError("evidence invalidation policy contract is invalid")
    normalized = _validate_self_hash(
        value, "policy_sha256", label="evidence invalidation policy"
    )
    rules = normalized.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("evidence invalidation rules are absent")
    seen = set()
    for rule in rules:
        if (
            not isinstance(rule, Mapping)
            or not isinstance(rule.get("rule_id"), str)
            or rule["rule_id"] in seen
            or rule.get("stage") not in STAGE_CLOSURES
            or not isinstance(rule.get("priority"), int)
            or not isinstance(rule.get("match"), Mapping)
        ):
            raise ValueError("evidence invalidation rule is invalid")
        _matches("", rule["match"])
        seen.add(rule["rule_id"])
    if normalized.get("unknown_change_route") != "unknown_change_fail_closed":
        raise ValueError("unknown evidence changes must fail closed")
    if set(normalized.get("allowed_routes", [])) != ROUTES:
        raise ValueError("evidence invalidation route enum drifted")
    return normalized


def classify_changed_paths(
    changed_paths: Iterable[str], policy: Mapping[str, object]
) -> dict[str, Any]:
    normalized_paths = set()
    for path in changed_paths:
        normalized = str(path).replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized.strip():
            normalized_paths.add(normalized)
    paths = sorted(normalized_paths)
    rules = policy.get("rules")
    if not isinstance(rules, list):
        raise ValueError("evidence invalidation rules are absent")
    classifications = []
    unclassified = []
    routes = []
    direct_stages = set()
    for path in paths:
        matches = [
            rule
            for rule in rules
            if isinstance(rule, Mapping)
            and isinstance(rule.get("match"), Mapping)
            and _matches(path, rule["match"])
        ]
        if not matches:
            unclassified.append(path)
            classifications.append(
                {
                    "path": path,
                    "stage": None,
                    "rule_id": None,
                    "status": "unclassified",
                }
            )
            continue
        highest = max(int(rule["priority"]) for rule in matches)
        selected = [rule for rule in matches if int(rule["priority"]) == highest]
        selected_stages = sorted({str(rule["stage"]) for rule in selected})
        if len(selected_stages) != 1:
            unclassified.append(path)
            classifications.append(
                {
                    "path": path,
                    "stage": None,
                    "rule_id": None,
                    "status": "ambiguous",
                }
            )
            continue
        stage = selected_stages[0]
        direct_stages.add(stage)
        routes.append(STAGE_ROUTES[stage])
        classifications.append(
            {
                "path": path,
                "stage": stage,
                "rule_id": sorted(str(rule["rule_id"]) for rule in selected)[0],
                "status": "classified",
            }
        )
    if unclassified:
        rerun_route = "unknown_change_fail_closed"
    elif not paths:
        rerun_route = "no_evidence_invalidation"
    else:
        rerun_route = max(routes, key=lambda route: ROUTE_PRIORITY[route])
    affected = []
    for stage in ("execution", "raw_evidence", "scoring", "acceptance", "claim"):
        if any(stage in STAGE_CLOSURES[item] for item in direct_stages):
            affected.append(stage)
    report = {
        "contract": "lca_research_evaluation.affected_evidence_report/v1",
        "changed_paths": paths,
        "classifications": classifications,
        "direct_stages": sorted(direct_stages),
        "affected_stages": affected,
        "unclassified_paths": sorted(unclassified),
        "rerun_route": rerun_route,
        "fail_closed": bool(unclassified),
    }
    report["report_sha256"] = canonical_sha256(report)
    return report


def build_layer_lock(
    root: Path,
    *,
    layer: str,
    paths: Iterable[str],
    basis: Mapping[str, object],
    evidence: Optional[Mapping[str, object]] = None,
    expected_hashes: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    if layer not in {"execution", "scoring", "acceptance"}:
        raise ValueError("evidence layer is invalid")
    root = Path(root).resolve()
    rows = []
    for relative in sorted(set(paths)):
        target = root / relative
        if not target.is_file():
            raise ValueError(f"evidence lock input is absent: {relative}")
        digest = _file_sha256(target)
        if expected_hashes is not None and expected_hashes.get(relative) != digest:
            raise ValueError(
                f"evidence lock input differs from its accepted basis: {relative}"
            )
        rows.append(
            {
                "path": relative,
                "sha256": digest,
                "bytes": target.stat().st_size,
            }
        )
    if not rows:
        raise ValueError("evidence layer lock has no inputs")
    body = {
        "contract": LAYER_LOCK_CONTRACT,
        "layer": layer,
        "basis": deepcopy(dict(basis)),
        "inputs": rows,
        "inputs_sha256": canonical_sha256(rows),
        "evidence": deepcopy(dict(evidence or {})),
    }
    body["lock_sha256"] = canonical_sha256(body)
    return body


def load_layer_lock(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("evidence layer lock is unreadable") from error
    if (
        not isinstance(value, Mapping)
        or value.get("contract") != LAYER_LOCK_CONTRACT
        or value.get("layer") not in {"execution", "scoring", "acceptance"}
        or not isinstance(value.get("inputs"), list)
        or not value["inputs"]
    ):
        raise ValueError("evidence layer lock contract is invalid")
    normalized = _validate_self_hash(value, "lock_sha256", label="evidence layer lock")
    if normalized.get("inputs_sha256") != canonical_sha256(normalized["inputs"]):
        raise ValueError("evidence layer input registry drifted")
    return normalized


def verify_layer_lock(
    root: Path, lock: Mapping[str, object]
) -> dict[str, Any]:
    normalized = _validate_self_hash(
        lock, "lock_sha256", label="evidence layer lock"
    )
    root = Path(root).resolve()
    drifted = []
    for row in normalized.get("inputs", []):
        if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
            raise ValueError("evidence layer input row is invalid")
        path = root / row["path"]
        if (
            not path.is_file()
            or _file_sha256(path) != row.get("sha256")
            or path.stat().st_size != row.get("bytes")
        ):
            drifted.append(row["path"])
    report = {
        "contract": "lca_research_evaluation.evidence_layer_verification/v1",
        "layer": normalized.get("layer"),
        "lock_sha256": normalized["lock_sha256"],
        "status": "valid" if not drifted else "invalid",
        "drifted_paths": sorted(drifted),
    }
    report["verification_sha256"] = canonical_sha256(report)
    return report


def verify_evidence_dag(
    root: Path,
    *,
    lock_paths: Mapping[str, Path],
    reuse_path: Path,
    raw_root: Path,
) -> dict[str, Any]:
    """Verify the linked layer locks, immutable raw tree, and reuse receipt."""

    root = Path(root).resolve()
    required_layers = {"execution", "scoring", "acceptance"}
    if set(lock_paths) != required_layers:
        raise ValueError("evidence layer lock registry is incomplete")
    locks = {
        layer: load_layer_lock(root / lock_paths[layer])
        for layer in sorted(required_layers)
    }
    layer_checks = {
        layer: verify_layer_lock(root, lock)
        for layer, lock in locks.items()
    }
    try:
        receipt_value = json.loads((root / reuse_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("evidence reuse receipt is unreadable") from error
    if (
        not isinstance(receipt_value, Mapping)
        or receipt_value.get("contract") != REUSE_CONTRACT
    ):
        raise ValueError("evidence reuse receipt contract is invalid")
    receipt = _validate_self_hash(
        receipt_value, "receipt_sha256", label="evidence reuse receipt"
    )

    def file_ref_valid(value: object) -> bool:
        if not isinstance(value, Mapping):
            return False
        relative = value.get("path")
        digest = value.get("sha256")
        if not isinstance(relative, str) or not isinstance(digest, str):
            return False
        target = (root / relative).resolve()
        return (
            root in target.parents
            and target.is_file()
            and _file_sha256(target) == digest
        )

    def tree_ref_valid(value: object) -> bool:
        if not isinstance(value, Mapping):
            return False
        relative = value.get("path")
        if not isinstance(relative, str):
            return False
        target = (root / relative).resolve()
        if root not in target.parents or not target.is_dir():
            return False
        current = canonical_tree_identity(target)
        return (
            value.get("tree_sha256") == current["tree_sha256"]
            and value.get("file_count") == current["file_count"]
        )

    current_raw = canonical_tree_identity(root / raw_root)
    execution_evidence = locks["execution"].get("evidence")
    scoring_evidence = locks["scoring"].get("evidence")
    expected_raw = (
        scoring_evidence.get("raw_tree")
        if isinstance(scoring_evidence, Mapping)
        else None
    )
    receipt_raw = receipt.get("raw_tree")
    raw_tree_valid = all(
        isinstance(expected, Mapping)
        and expected.get("tree_sha256") == current_raw["tree_sha256"]
        and expected.get("file_count") == current_raw["file_count"]
        for expected in (expected_raw, receipt_raw)
    )
    final_corpus_valid = (
        tree_ref_valid(execution_evidence.get("final_corpus_tree"))
        if isinstance(execution_evidence, Mapping)
        else False
    )
    run_manifest_valid = (
        file_ref_valid(scoring_evidence.get("run_manifest"))
        if isinstance(scoring_evidence, Mapping)
        else False
    )
    scoring_artifact_valid = (
        file_ref_valid(scoring_evidence.get("grade_package"))
        if isinstance(scoring_evidence, Mapping)
        else False
    )
    links_valid = (
        locks["scoring"].get("basis", {}).get("execution_lock_sha256")
        == locks["execution"]["lock_sha256"]
        and locks["acceptance"].get("basis", {}).get("scoring_lock_sha256")
        == locks["scoring"]["lock_sha256"]
        and receipt.get("execution_lock_sha256")
        == locks["execution"]["lock_sha256"]
    )
    reuse_valid = (
        receipt.get("status") == "accepted"
        and receipt.get("execution_snapshot_unchanged") is True
        and receipt.get("raw_tree_unchanged") is True
        and receipt.get("run_count_preserved") is True
        and receipt.get("failed_rows_preserved") is True
        and receipt.get("observed_run_count")
        == (
            scoring_evidence.get("run_count")
            if isinstance(scoring_evidence, Mapping)
            else None
        )
        and receipt.get("observed_failure_count")
        == (
            scoring_evidence.get("failure_count")
            if isinstance(scoring_evidence, Mapping)
            else None
        )
        and receipt.get("cross_version_splicing") is False
        and receipt.get("rerun_route")
        in {
            "no_evidence_invalidation",
            "rebuild_report_only",
            "rerun_clean_acceptance",
            "regrade_existing_traces",
        }
    )
    status = (
        "passed"
        if all(check["status"] == "valid" for check in layer_checks.values())
        and links_valid
        and raw_tree_valid
        and final_corpus_valid
        and run_manifest_valid
        and scoring_artifact_valid
        and reuse_valid
        else "failed"
    )
    report = {
        "contract": "lca_research_evaluation.evidence_dag_check/v1",
        "status": status,
        "layer_checks": layer_checks,
        "links_valid": links_valid,
        "raw_tree_valid": raw_tree_valid,
        "raw_tree_sha256": current_raw["tree_sha256"],
        "final_corpus_valid": final_corpus_valid,
        "run_manifest_valid": run_manifest_valid,
        "scoring_artifact_valid": scoring_artifact_valid,
        "reuse_valid": reuse_valid,
        "reuse_receipt_sha256": receipt["receipt_sha256"],
        "rerun_route": receipt.get("rerun_route"),
    }
    report["check_sha256"] = canonical_sha256(report)
    return report


def build_evidence_reuse_receipt(
    root: Path,
    *,
    execution_lock: Mapping[str, object],
    raw_evidence_binding: Mapping[str, object],
    raw_root: Path,
    observed_run_count: int,
    observed_failure_count: int,
    changed_paths: Iterable[str],
    policy: Mapping[str, object],
) -> dict[str, Any]:
    execution_check = verify_layer_lock(root, execution_lock)
    current_tree = canonical_tree_identity(raw_root)
    expected_tree = raw_evidence_binding.get("raw_tree")
    tree_unchanged = (
        isinstance(expected_tree, Mapping)
        and expected_tree.get("tree_sha256") == current_tree["tree_sha256"]
        and expected_tree.get("file_count") == current_tree["file_count"]
    )
    run_count_preserved = (
        observed_run_count == raw_evidence_binding.get("run_count")
    )
    failed_rows_preserved = (
        observed_failure_count == raw_evidence_binding.get("failure_count")
    )
    affected = classify_changed_paths(changed_paths, policy)
    reusable_route = affected["rerun_route"] in {
        "no_evidence_invalidation",
        "rebuild_report_only",
        "rerun_clean_acceptance",
        "regrade_existing_traces",
    }
    reasons = []
    if execution_check["status"] != "valid":
        reasons.append("execution_snapshot_drifted")
    if not tree_unchanged:
        reasons.append("raw_evidence_drifted")
    if not run_count_preserved:
        reasons.append("run_rows_removed_or_added")
    if not failed_rows_preserved:
        reasons.append("failed_rows_removed_or_added")
    if not reusable_route:
        reasons.append("change_requires_fresh_execution")
    body = {
        "contract": REUSE_CONTRACT,
        "status": "accepted" if not reasons else "rejected",
        "execution_lock_sha256": execution_lock.get("lock_sha256"),
        "execution_snapshot_unchanged": execution_check["status"] == "valid",
        "raw_tree_unchanged": tree_unchanged,
        "run_count_preserved": run_count_preserved,
        "failed_rows_preserved": failed_rows_preserved,
        "cross_version_splicing": False,
        "observed_run_count": observed_run_count,
        "observed_failure_count": observed_failure_count,
        "raw_tree": {
            "contract": current_tree["contract"],
            "file_count": current_tree["file_count"],
            "tree_sha256": current_tree["tree_sha256"],
        },
        "affected_report": affected,
        "rerun_route": affected["rerun_route"],
        "reasons": reasons,
    }
    body["receipt_sha256"] = canonical_sha256(body)
    return body


__all__ = [
    "build_evidence_reuse_receipt",
    "build_layer_lock",
    "canonical_sha256",
    "canonical_tree_identity",
    "classify_changed_paths",
    "load_invalidation_policy",
    "load_layer_lock",
    "verify_evidence_dag",
    "verify_layer_lock",
]
