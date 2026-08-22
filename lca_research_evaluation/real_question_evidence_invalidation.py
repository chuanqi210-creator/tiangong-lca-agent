"""Real-question Evidence DAG routing over the existing D-18 DAG primitives."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping, Sequence

from .evidence_dag import (
    canonical_sha256,
    canonical_tree_identity,
    classify_changed_paths,
    verify_layer_lock,
)
from .real_question_contracts import SCORED_QUESTION_IDS


def _rule(
    rule_id: str,
    stage: str,
    value: str,
    *,
    match_type: str = "exact",
    priority: int = 100,
) -> dict:
    return {
        "rule_id": rule_id,
        "stage": stage,
        "priority": priority,
        "match": {"type": match_type, "value": value},
    }


REAL_QUESTION_POLICY = {
    "contract": "lca_research_evaluation.evidence_invalidation_policy/v1",
    "allowed_routes": [
        "rerun_full_execution",
        "regrade_existing_traces",
        "rerun_clean_acceptance",
        "rebuild_report_only",
        "no_evidence_invalidation",
        "unknown_change_fail_closed",
    ],
    "unknown_change_route": "unknown_change_fail_closed",
    "rules": [
        *[
            _rule(
                "execution-" + path.rsplit("/", 1)[-1].replace(".", "-"),
                "execution",
                path,
            )
            for path in (
                "lca_research_kernel/research_orchestrator.py",
                "lca_research_kernel/context_assembler.py",
                "lca_research_kernel/lca_domain_kernel.py",
                "lca_research_kernel/model_gateway.py",
                "lca_research_kernel/model_provider_adapters.py",
                "lca_research_kernel/model_runtime.py",
                "lca_research_kernel/agent_bootstrap.py",
                "lca_research_kernel/capability_gateway.py",
                "lca_research_kernel/product_interface.py",
                "lca_research_kernel/official_public_source.py",
                "lca_research_kernel/answer_timing.py",
                "lca_research_kernel/run_trace.py",
                "lca_research_kernel/contracts/agent-startup-manifest-v2.json",
                "lca_research_kernel/contracts/model-runtime-policy.json",
                "lca_research_kernel/contracts/model-provider-catalog.json",
                "lca_research_kernel/contracts/official-public-source-index.json",
                "lca_research_kernel/contracts/project-execution-policy.json",
                "lca_research_kernel/contracts/tiangong-source-lock.json",
                "lca_research_evaluation/real_question_pack.py",
                "lca_research_evaluation/real_question_blind_runner.py",
                "lca_research_evaluation/staged_real_question_regression.py",
                "lca_research_service/runtime_factory.py",
                "scripts/run_real_question_blind.py",
                "scripts/run_staged_real_question_regression.py",
            )
        ],
        _rule(
            "execution-agent-contracts",
            "execution",
            "agent/",
            match_type="prefix",
            priority=220,
        ),
        _rule(
            "execution-skill-contracts",
            "execution",
            "skills/",
            match_type="prefix",
            priority=220,
        ),
        _rule(
            "execution-prompt-contracts",
            "execution",
            "prompts/",
            match_type="prefix",
            priority=220,
        ),
        _rule(
            "execution-question-pack",
            "execution",
            "outputs/real-question-optimization/",
            match_type="prefix",
            priority=10,
        ),
        _rule(
            "scoring-implementation",
            "scoring",
            "lca_research_evaluation/dual_track_grader.py",
            priority=200,
        ),
        _rule(
            "scoring-entry",
            "scoring",
            "scripts/grade_real_question_blind_runs.py",
            priority=200,
        ),
        _rule(
            "scoring-development-regression",
            "scoring",
            "lca_research_evaluation/development_regression_grader.py",
            priority=200,
        ),
        _rule(
            "scoring-development-regression-entry",
            "scoring",
            "scripts/grade_staged_real_question_regression.py",
            priority=200,
        ),
        _rule(
            "execution-pack-builder",
            "execution",
            "scripts/build_real_question_pack.py",
            priority=200,
        ),
        _rule(
            "scoring-sealed-reference",
            "scoring",
            "/sealed-reference/",
            match_type="suffix",
            priority=200,
        ),
        _rule(
            "scoring-sealed-reference-file",
            "scoring",
            "sealed-reference-answer.json",
            match_type="suffix",
            priority=210,
        ),
        _rule(
            "acceptance-finalizer",
            "acceptance",
            "lca_research_evaluation/real_question_finalizer.py",
            priority=200,
        ),
        _rule(
            "acceptance-script",
            "acceptance",
            "scripts/finalize_real_question_optimization.py",
            priority=200,
        ),
        _rule(
            "acceptance-tests",
            "acceptance",
            "tests/test_real_question_",
            match_type="prefix",
            priority=150,
        ),
        _rule(
            "report-output",
            "report",
            "__report__/",
            match_type="prefix",
            priority=300,
        ),
    ],
}


def _classify_path(path: str) -> dict:
    """Route only known real-question output roots; never match loose components."""

    normalized = str(path).replace("\\", "/").lstrip("./")
    output_prefix = "outputs/real-question-optimization/"
    if normalized.startswith(output_prefix):
        relative = PurePosixPath(normalized[len(output_prefix) :])
        parts = relative.parts
        if any(
            sensitive in parts
            for sensitive in ("raw-evidence", "question-pack", "blind-runs")
        ):
            return {"path": normalized, "stage": "execution"}
        if (
            "sealed-reference" in parts
            or "grades" in parts
            or (
                "raw-extraction" in parts
                and any("sealed-reference" in part for part in parts)
            )
        ):
            return {"path": normalized, "stage": "scoring"}
        if "reports" in parts:
            return {"path": normalized, "stage": "report"}
    return {"path": normalized, "stage": None}


def classify_real_question_changes(changed_paths: Iterable[str]) -> dict:
    paths = [str(path).replace("\\", "/") for path in changed_paths]
    rewritten = []
    reverse = {}
    for index, path in enumerate(paths):
        special = _classify_path(path)
        if special["stage"] == "report":
            synthetic = f"__report__/{index}.md"
            rewritten.append(synthetic)
            reverse[synthetic] = path
        elif special["stage"] == "scoring":
            synthetic = f"sealed-reference-answer.json"
            rewritten.append(synthetic)
            reverse[synthetic] = path
        elif special["stage"] == "execution":
            synthetic = f"outputs/real-question-optimization/raw-evidence/{index}"
            rewritten.append(synthetic)
            reverse[synthetic] = path
        else:
            rewritten.append(path)
    report = classify_changed_paths(rewritten, REAL_QUESTION_POLICY)
    for row in report["classifications"]:
        if row["path"] in reverse:
            row["path"] = reverse[row["path"]]
    report["changed_paths"] = sorted(set(paths))
    report["report_sha256"] = canonical_sha256(
        {key: value for key, value in report.items() if key != "report_sha256"}
    )
    return report


def _sha(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} is not a lowercase SHA-256")
    return value


def build_real_question_reuse_receipt(
    *,
    repository_root: Path,
    execution_lock: Mapping[str, object],
    raw_root: Path,
    raw_lock: Mapping[str, object],
    changed_paths: Iterable[str],
) -> dict:
    """Derive reuse solely from fresh layer and member-registry verification."""

    execution_verification = verify_layer_lock(
        Path(repository_root), execution_lock
    )
    raw_verification = verify_real_question_raw_evidence_lock(raw_root, raw_lock)
    affected = classify_real_question_changes(changed_paths)
    reasons = []
    if (
        execution_verification.get("status") != "valid"
        or execution_verification.get("layer") != "execution"
    ):
        reasons.append("execution_snapshot_drifted")
    if raw_verification.get("status") != "valid":
        reasons.append("raw_evidence_drifted")
    if affected["rerun_route"] in {
        "rerun_full_execution",
        "unknown_change_fail_closed",
    }:
        reasons.append("change_requires_fresh_execution")
    receipt = {
        "contract": "real_question_evidence_reuse_receipt/v1",
        "status": "accepted" if not reasons else "rejected",
        "execution_lock_sha256": execution_verification.get("lock_sha256"),
        "execution_verification_sha256": execution_verification.get(
            "verification_sha256"
        ),
        "execution_snapshot_unchanged": execution_verification.get("status")
        == "valid",
        "raw_evidence_sha256": raw_verification.get("raw_evidence_sha256"),
        "raw_verification_sha256": raw_verification.get(
            "verification_sha256"
        ),
        "raw_evidence_unchanged": raw_verification.get("status") == "valid",
        "run_count": raw_verification.get("run_count"),
        "failure_count": raw_verification.get("failure_count"),
        "run_count_preserved": raw_verification.get("run_count")
        == len(SCORED_QUESTION_IDS),
        "failed_rows_preserved": raw_verification.get("status") == "valid",
        "cross_version_splicing": False if not reasons else "not_proven",
        "rerun_route": affected["rerun_route"],
        "affected_report_sha256": affected["report_sha256"],
        "reasons": reasons,
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def _member_path(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{label} path is invalid")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or value != relative.as_posix()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError(f"{label} path is invalid")
    path = root.joinpath(*relative.parts)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} file is absent or linked")
    resolved = path.resolve()
    if root not in resolved.parents:
        raise ValueError(f"{label} path escaped the raw tree")
    return path


def _verified_ref(
    root: Path,
    value: object,
    *,
    path_field: str,
    sha_field: str,
    label: str,
) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} reference is invalid")
    path = _member_path(root, value.get(path_field), label)
    expected = _sha(value.get(sha_field), f"{label} member")
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError(f"{label} member hash drifted")
    return deepcopy(dict(value))


def _verify_official_ref(root: Path, value: object) -> dict:
    fields = {
        "raw_path",
        "raw_sha256",
        "receipt_path",
        "receipt_sha256",
        "excerpt_path",
        "excerpt_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError("official source member reference is invalid")
    for path_field, sha_field, label in (
        ("raw_path", "raw_sha256", "official source raw"),
        ("receipt_path", "receipt_sha256", "official source receipt"),
        ("excerpt_path", "excerpt_sha256", "official source excerpt"),
    ):
        _verified_ref(
            root,
            value,
            path_field=path_field,
            sha_field=sha_field,
            label=label,
        )
    return deepcopy(dict(value))


def _load_raw_run_index(root: Path) -> dict:
    root = Path(root)
    path = root / "run-index.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("real-question raw run index is unreadable") from error
    if (
        not isinstance(value, Mapping)
        or set(value) != {"contract", "runs", "failure_count"}
        or value.get("contract") != "real_question_raw_run_index/v1"
        or not isinstance(value.get("runs"), list)
        or len(value["runs"]) != len(SCORED_QUESTION_IDS)
        or isinstance(value.get("failure_count"), bool)
        or not isinstance(value.get("failure_count"), int)
        or not 0 <= value["failure_count"] <= len(SCORED_QUESTION_IDS)
    ):
        raise ValueError("real-question raw run index is invalid")
    runs = []
    observed_ids = []
    answer_trace_paths: set[str] = set()
    observed_failure_count = 0
    expected_fields = {
        "question_id",
        "research_id",
        "answer_path",
        "answer_sha256",
        "trace_path",
        "trace_sha256",
        "official_source_refs",
    }
    for row in value["runs"]:
        if not isinstance(row, Mapping) or set(row) != expected_fields:
            raise ValueError("real-question raw run member is invalid")
        question_id = row.get("question_id")
        research_id = row.get("research_id")
        if not isinstance(research_id, str) or not research_id.strip():
            raise ValueError("real-question raw run identity is invalid")
        observed_ids.append(question_id)
        answer = _verified_ref(
            root,
            row,
            path_field="answer_path",
            sha_field="answer_sha256",
            label=f"{question_id} answer",
        )
        _verified_ref(
            root,
            row,
            path_field="trace_path",
            sha_field="trace_sha256",
            label=f"{question_id} trace",
        )
        for field in ("answer_path", "trace_path"):
            if row[field] in answer_trace_paths:
                raise ValueError("raw answer or trace member is reused")
            answer_trace_paths.add(str(row[field]))
        refs = row.get("official_source_refs")
        if not isinstance(refs, list):
            raise ValueError("official source member registry is invalid")
        official_refs = [_verify_official_ref(root, ref) for ref in refs]
        try:
            answer_value = json.loads(
                _member_path(root, row["answer_path"], "raw answer").read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("raw answer member is not valid JSON") from error
        if not isinstance(answer_value, Mapping) or answer_value.get("status") not in {
            "completed",
            "failed",
        }:
            raise ValueError("raw answer completion status is absent")
        if (
            answer_value.get("question_id") != question_id
            or (
                answer_value.get("research_id") is not None
                and answer_value.get("research_id") != research_id
            )
        ):
            raise ValueError("raw answer question or run identity drifted")
        observed_failure_count += answer_value["status"] == "failed"
        runs.append(
            {
                **deepcopy(dict(row)),
                "official_source_refs": official_refs,
            }
        )
    if observed_ids != SCORED_QUESTION_IDS:
        raise ValueError("real-question raw run population drifted")
    if observed_failure_count != value["failure_count"]:
        raise ValueError("real-question raw failure count drifted")
    if observed_failure_count or value["failure_count"]:
        raise ValueError("failed real-question raw runs cannot be frozen")
    return {
        "contract": value["contract"],
        "runs": runs,
        "failure_count": value["failure_count"],
    }


def build_real_question_raw_evidence_lock(
    raw_root: Path,
    *,
    execution_lock_sha256: str,
) -> dict:
    """Freeze one complete raw tree before the grader may see sealed answers."""

    _sha(execution_lock_sha256, "execution lock")
    supplied_root = Path(raw_root)
    if supplied_root.is_symlink():
        raise ValueError("real-question raw evidence root is linked")
    raw_root = supplied_root.resolve()
    if not raw_root.is_dir():
        raise ValueError("real-question raw evidence root is absent or linked")
    for path in raw_root.rglob("*"):
        relative = path.relative_to(raw_root).as_posix().casefold()
        if path.is_symlink() or "sealed-reference" in relative:
            raise ValueError("real-question raw evidence contains a sealed reference")
    index = _load_raw_run_index(raw_root)
    identity = canonical_tree_identity(raw_root)
    lock = {
        "contract": "real_question_raw_evidence_lock/v1",
        "execution_lock_sha256": execution_lock_sha256,
        "raw_evidence_sha256": identity["tree_sha256"],
        "raw_tree_identity": identity,
        "runs": deepcopy(index["runs"]),
        "failure_count": index["failure_count"],
    }
    lock["lock_sha256"] = canonical_sha256(lock)
    return lock


def verify_real_question_raw_evidence_lock(
    raw_root: Path,
    lock: Mapping[str, object],
) -> dict:
    """Recompute the complete raw tree; never accept a caller-provided boolean."""

    normalized = deepcopy(dict(lock))
    stored = normalized.pop("lock_sha256", None)
    structural_valid = (
        normalized.get("contract") == "real_question_raw_evidence_lock/v1"
        and isinstance(stored, str)
        and stored == canonical_sha256(normalized)
        and isinstance(normalized.get("runs"), list)
        and [
            row.get("question_id")
            for row in normalized.get("runs", [])
            if isinstance(row, Mapping)
        ]
        == SCORED_QUESTION_IDS
    )
    current_sha256 = None
    index = None
    try:
        if not structural_valid:
            raise ValueError("raw evidence lock structure drifted")
        supplied_root = Path(raw_root)
        if supplied_root.is_symlink():
            raise ValueError("raw evidence root is linked")
        resolved_root = supplied_root.resolve()
        for path in resolved_root.rglob("*"):
            if path.is_symlink():
                raise ValueError("raw evidence contains a symbolic link")
        index = _load_raw_run_index(resolved_root)
        identity = canonical_tree_identity(resolved_root)
        current_sha256 = identity["tree_sha256"]
        valid = (
            current_sha256 == normalized.get("raw_evidence_sha256")
            and identity == normalized.get("raw_tree_identity")
            and index.get("runs") == normalized.get("runs")
            and index.get("failure_count") == normalized.get("failure_count")
        )
    except (OSError, ValueError):
        valid = False
    verification = {
        "contract": "real_question_raw_evidence_lock_verification/v1",
        "status": "valid" if valid else "invalid",
        "execution_lock_sha256": normalized.get("execution_lock_sha256"),
        "raw_evidence_sha256": normalized.get("raw_evidence_sha256"),
        "runs": deepcopy(index.get("runs") if valid and index else []),
        "run_count": len(index.get("runs", [])) if valid and index else 0,
        "failure_count": (
            index.get("failure_count") if valid and index else None
        ),
        "observed_raw_evidence_sha256": current_sha256,
        "lock_sha256": stored,
    }
    verification["verification_sha256"] = canonical_sha256(verification)
    return verification


def _validate_development_population(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("development-regression population is invalid")
    question_ids = list(value)
    if (
        not question_ids
        or len(question_ids) != len(set(question_ids))
        or any(question_id not in SCORED_QUESTION_IDS for question_id in question_ids)
    ):
        raise ValueError("development-regression population is invalid")
    return question_ids


def _load_development_subset_runs(
    raw_root: Path, expected_question_ids: Sequence[str]
) -> list[dict]:
    """Verify only the named raw members without weakening the legacy full lock."""

    supplied_root = Path(raw_root)
    if supplied_root.is_symlink():
        raise ValueError("development-regression raw evidence root is linked")
    root = supplied_root.resolve()
    if not root.is_dir():
        raise ValueError("development-regression raw evidence root is absent")
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix().casefold()
        if path.is_symlink():
            raise ValueError("development-regression raw evidence contains a link")
        if "sealed-reference" in relative:
            raise ValueError("development-regression raw evidence contains a sealed reference")

    indexed = {}
    index_path = root / "run-index.json"
    if index_path.is_file():
        try:
            index_value = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("development-regression run index is unreadable") from error
        if not isinstance(index_value, Mapping) or not isinstance(
            index_value.get("runs"), list
        ):
            raise ValueError("development-regression run index is invalid")
        for row in index_value["runs"]:
            if not isinstance(row, Mapping) or not isinstance(
                row.get("question_id"), str
            ):
                raise ValueError("development-regression run member is invalid")
            if row["question_id"] in indexed:
                raise ValueError("development-regression run member is duplicated")
            indexed[row["question_id"]] = dict(row)

    runs = []
    for question_id in _validate_development_population(expected_question_ids):
        indexed_row = indexed.get(question_id, {})
        answer_relative = indexed_row.get(
            "answer_path", f"runs/{question_id}/answer.json"
        )
        trace_relative = indexed_row.get(
            "trace_path", f"runs/{question_id}/trace.json"
        )
        answer_path = _member_path(
            root, answer_relative, f"{question_id} development answer"
        )
        trace_path = _member_path(
            root, trace_relative, f"{question_id} development trace"
        )
        try:
            answer = json.loads(answer_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(
                f"development-regression answer {question_id} is unreadable"
            ) from error
        if (
            not isinstance(answer, Mapping)
            or answer.get("question_id") != question_id
            or answer.get("status") != "completed"
        ):
            raise ValueError(
                f"development-regression answer {question_id} is failed or invalid"
            )
        research_id = answer.get("research_id", indexed_row.get("research_id"))
        if not isinstance(research_id, str) or not research_id.strip():
            raise ValueError("development-regression research identity is absent")
        official_source_refs = answer.get(
            "official_source_refs", indexed_row.get("official_source_refs", [])
        )
        if not isinstance(official_source_refs, list):
            raise ValueError("development-regression official source registry is invalid")
        verified_sources = [
            _verify_official_ref(root, ref) for ref in official_source_refs
        ]
        answer_sha256 = hashlib.sha256(answer_path.read_bytes()).hexdigest()
        trace_sha256 = hashlib.sha256(trace_path.read_bytes()).hexdigest()
        if (
            indexed_row
            and (
                indexed_row.get("answer_sha256") != answer_sha256
                or indexed_row.get("trace_sha256") != trace_sha256
                or indexed_row.get("research_id") != research_id
                or indexed_row.get("official_source_refs") != verified_sources
            )
        ):
            raise ValueError("development-regression indexed member drifted")
        runs.append(
            {
                "question_id": question_id,
                "research_id": research_id,
                "answer_path": str(answer_relative),
                "answer_sha256": answer_sha256,
                "trace_path": str(trace_relative),
                "trace_sha256": trace_sha256,
                "official_source_refs": verified_sources,
            }
        )
    return runs


def build_development_regression_raw_lock(
    raw_root: Path,
    *,
    expected_question_ids: Sequence[str],
    execution_lock_sha256: str,
) -> dict:
    """Freeze an explicit 1/3/12 subset before the grader sees references."""

    execution_lock_sha256 = _sha(execution_lock_sha256, "execution lock")
    expected = _validate_development_population(expected_question_ids)
    runs = _load_development_subset_runs(Path(raw_root), expected)
    lock = {
        "contract": "development_regression_raw_evidence_lock/v1",
        "execution_lock_sha256": execution_lock_sha256,
        "expected_question_ids": expected,
        "raw_evidence_sha256": canonical_sha256({"runs": runs}),
        "runs": runs,
        "failure_count": 0,
        "sealed_reference_members": 0,
    }
    lock["lock_sha256"] = canonical_sha256(lock)
    return lock


def verify_development_regression_raw_lock(
    raw_root: Path, lock: Mapping[str, object]
) -> dict:
    """Recompute the exact subset and reject order, member or Epoch drift."""

    normalized = deepcopy(dict(lock))
    stored = normalized.pop("lock_sha256", None)
    runs = []
    valid = False
    observed_sha256 = None
    try:
        expected = _validate_development_population(
            normalized.get("expected_question_ids")
        )
        _sha(normalized.get("execution_lock_sha256"), "execution lock")
        if (
            normalized.get("contract")
            != "development_regression_raw_evidence_lock/v1"
            or stored != canonical_sha256(normalized)
            or normalized.get("failure_count") != 0
            or normalized.get("sealed_reference_members") != 0
            or [row.get("question_id") for row in normalized.get("runs", [])]
            != expected
        ):
            raise ValueError("development-regression raw lock structure drifted")
        runs = _load_development_subset_runs(Path(raw_root), expected)
        observed_sha256 = canonical_sha256({"runs": runs})
        valid = (
            runs == normalized.get("runs")
            and observed_sha256 == normalized.get("raw_evidence_sha256")
        )
    except (OSError, TypeError, ValueError):
        valid = False
    verification = {
        "contract": "development_regression_raw_evidence_lock_verification/v1",
        "status": "valid" if valid else "invalid",
        "execution_lock_sha256": normalized.get("execution_lock_sha256"),
        "expected_question_ids": deepcopy(
            normalized.get("expected_question_ids", [])
        ),
        "raw_evidence_sha256": normalized.get("raw_evidence_sha256"),
        "observed_raw_evidence_sha256": observed_sha256,
        "runs": deepcopy(runs if valid else []),
        "run_count": len(runs) if valid else 0,
        "failure_count": 0 if valid else None,
        "lock_sha256": stored,
    }
    verification["verification_sha256"] = canonical_sha256(verification)
    return verification


__all__ = [
    "REAL_QUESTION_POLICY",
    "build_development_regression_raw_lock",
    "build_real_question_raw_evidence_lock",
    "build_real_question_reuse_receipt",
    "classify_real_question_changes",
    "verify_development_regression_raw_lock",
    "verify_real_question_raw_evidence_lock",
]
