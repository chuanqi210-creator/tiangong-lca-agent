#!/usr/bin/env python3
"""Build and verify the D-18 evaluation Evidence DAG and reuse receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_evaluation.evidence_dag import (
    build_evidence_reuse_receipt,
    build_layer_lock,
    canonical_sha256,
    canonical_tree_identity,
    classify_changed_paths,
    load_invalidation_policy,
    load_layer_lock,
    verify_evidence_dag,
    verify_layer_lock,
)


CONTRACT_DIR = Path("lca_research_kernel/contracts")
POLICY_PATH = CONTRACT_DIR / "evaluation-invalidation-policy.json"
LOCK_PATHS = {
    "execution": CONTRACT_DIR / "evaluation-execution-lock.json",
    "scoring": CONTRACT_DIR / "evaluation-scoring-lock.json",
    "acceptance": CONTRACT_DIR / "evaluation-acceptance-lock.json",
}
DEFAULT_FREEZE = Path(
    "outputs/d18-performance-benchmark/final-corpus/control/freeze-receipt.json"
)
DEFAULT_RAW = Path("outputs/d18-performance-benchmark/final-runs/final")
DEFAULT_RUN_MANIFEST = Path(
    "outputs/d18-performance-benchmark/final-runs/run-manifest.json"
)
DEFAULT_RUN_SUMMARY = Path(
    "outputs/d18-performance-benchmark/final-runs/final-run-summary.json"
)
DEFAULT_GRADE = Path(
    "outputs/d18-performance-benchmark/final/grade-package.json"
)
DEFAULT_ACCEPTANCE = Path(
    "outputs/d18-performance-benchmark/final/acceptance.json"
)
DEFAULT_REUSE = Path(
    "outputs/d18-performance-benchmark/final/evidence-reuse-receipt.json"
)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: object) -> None:
    content = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == content:
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("Evidence DAG requires a readable Git repository")
    return result.stdout.rstrip("\n")


def _changed_paths(root: Path, basis_commit: str) -> list[str]:
    changed = set(
        line
        for line in _git(root, "diff", "--name-only", basis_commit, "--").splitlines()
        if line
    )
    for line in _git(
        root, "status", "--porcelain=v1", "--untracked-files=all"
    ).splitlines():
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            changed.add(path)
    evidence_outputs = (
        "outputs/",
        ".superpowers/",
    )
    return sorted(
        path
        for path in changed
        if not path.startswith(evidence_outputs) and path != "CONTEXT.md"
    )


def _freeze_rows(freeze: Mapping[str, object]) -> dict[str, dict[str, Any]]:
    rows = freeze.get("protected_files")
    if not isinstance(rows, list):
        raise ValueError("legacy freeze file registry is absent")
    normalized = {}
    for row in rows:
        if (
            not isinstance(row, Mapping)
            or not isinstance(row.get("path"), str)
            or not isinstance(row.get("sha256"), str)
        ):
            raise ValueError("legacy freeze file row is invalid")
        normalized[row["path"]] = dict(row)
    return normalized


def _paths_for_stage(
    freeze_rows: Mapping[str, Mapping[str, object]],
    policy: Mapping[str, object],
    stage: str,
) -> list[str]:
    selected = []
    for path in sorted(freeze_rows):
        report = classify_changed_paths([path], policy)
        if report["direct_stages"] == [stage]:
            selected.append(path)
    return selected


def _verify_run_manifest(root: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = _read(root / manifest_path)
    rows = manifest.get("result_refs")
    if (
        manifest.get("contract")
        != "lca_research_evaluation.performance_run_manifest/v1"
        or not isinstance(rows, list)
        or manifest.get("run_count") != len(rows)
        or len(rows) != 270
    ):
        raise ValueError("raw run manifest is incomplete")
    seen = set()
    for row in rows:
        if (
            not isinstance(row, Mapping)
            or not isinstance(row.get("path"), str)
            or not isinstance(row.get("sha256"), str)
            or row["path"] in seen
        ):
            raise ValueError("raw run manifest row is invalid or duplicated")
        target = (root / manifest_path.parent / row["path"]).resolve()
        if (
            not target.is_file()
            or _sha256(target) != row["sha256"]
            or root.resolve() not in target.parents
        ):
            raise ValueError("raw run manifest row drifted")
        seen.add(row["path"])
    body = dict(manifest)
    stored = body.pop("manifest_sha256", None)
    if stored != canonical_sha256(body):
        raise ValueError("raw run manifest self hash drifted")
    return {
        "path": manifest_path.as_posix(),
        "sha256": _sha256(root / manifest_path),
        "manifest_sha256": stored,
        "result_ref_count": len(rows),
    }


def _additional_acceptance_paths(root: Path) -> list[str]:
    candidates = [
        "lca_research_evaluation/evidence_dag.py",
        "scripts/check_d18_evidence_invalidation.py",
        "tests/test_evaluation_evidence_dag.py",
        "lca_research_kernel/contracts/evaluation-invalidation-policy.json",
    ]
    return [path for path in candidates if (root / path).is_file()]


def _tree_summary(
    identity: Mapping[str, object], *, path: Path | None = None
) -> dict[str, object]:
    summary = {
        "contract": identity["contract"],
        "file_count": identity["file_count"],
        "tree_sha256": identity["tree_sha256"],
    }
    if path is not None:
        summary["path"] = path.as_posix()
    return summary


def build_locks(root: Path, *, basis_commit: str) -> dict[str, Any]:
    root = root.resolve()
    policy = load_invalidation_policy(root / POLICY_PATH)
    freeze = _read(root / DEFAULT_FREEZE)
    freeze_body = dict(freeze)
    stored_freeze = freeze_body.pop("freeze_sha256", None)
    if stored_freeze != canonical_sha256(freeze_body):
        raise ValueError("legacy execution freeze self hash drifted")
    freeze_rows = _freeze_rows(freeze)
    run_manifest = _verify_run_manifest(root, DEFAULT_RUN_MANIFEST)
    summary = _read(root / DEFAULT_RUN_SUMMARY)
    if (
        summary.get("run_count") != 270
        or not isinstance(summary.get("failure_count"), int)
        or summary.get("run_manifest_sha256") != run_manifest["manifest_sha256"]
    ):
        raise ValueError("raw run summary is incomplete or unbound")
    raw_tree = canonical_tree_identity(root / DEFAULT_RAW)
    corpus_tree = canonical_tree_identity(
        root / "outputs/d18-performance-benchmark/final-corpus"
    )
    execution_paths = _paths_for_stage(freeze_rows, policy, "execution")
    execution_lock = build_layer_lock(
        root,
        layer="execution",
        paths=execution_paths,
        basis={
            "basis_commit": basis_commit,
            "legacy_freeze_sha256": stored_freeze,
            "migration_kind": "legacy_monolithic_freeze_to_evidence_dag",
        },
        expected_hashes={
            path: str(row["sha256"]) for path, row in freeze_rows.items()
        },
        evidence={
            "final_corpus_tree": _tree_summary(
                corpus_tree,
                path=Path("outputs/d18-performance-benchmark/final-corpus"),
            ),
        },
    )
    _write(root / LOCK_PATHS["execution"], execution_lock)

    scoring_paths = _paths_for_stage(freeze_rows, policy, "scoring")
    scoring_lock = build_layer_lock(
        root,
        layer="scoring",
        paths=scoring_paths,
        basis={
            "execution_lock_sha256": execution_lock["lock_sha256"],
            "raw_tree_sha256": raw_tree["tree_sha256"],
        },
        expected_hashes={
            path: str(row["sha256"]) for path, row in freeze_rows.items()
        },
        evidence={
            "raw_tree": _tree_summary(raw_tree),
            "run_manifest": run_manifest,
            "run_count": summary["run_count"],
            "failure_count": summary["failure_count"],
            "candidate_count": summary.get("candidate_count"),
            "external_writes": summary.get("external_writes"),
            "credential_values_recorded": summary.get(
                "credential_values_recorded"
            ),
            "grade_package": {
                "path": DEFAULT_GRADE.as_posix(),
                "sha256": _sha256(root / DEFAULT_GRADE),
            },
            "run_manifest_sha256": run_manifest["manifest_sha256"],
        },
    )
    _write(root / LOCK_PATHS["scoring"], scoring_lock)

    acceptance_paths = sorted(
        set(_paths_for_stage(freeze_rows, policy, "acceptance"))
        | set(_additional_acceptance_paths(root))
    )
    acceptance_evidence = {
        "purpose": "acceptance_harness_and_environment_inputs",
        "python": {
            "executable": sys.executable,
            "version": ".".join(str(item) for item in sys.version_info[:3]),
        },
    }
    acceptance_lock = build_layer_lock(
        root,
        layer="acceptance",
        paths=acceptance_paths,
        basis={
            "scoring_lock_sha256": scoring_lock["lock_sha256"],
            "policy_sha256": policy["policy_sha256"],
        },
        evidence=acceptance_evidence,
    )
    _write(root / LOCK_PATHS["acceptance"], acceptance_lock)
    return {
        "contract": "lca_research_evaluation.evidence_dag_build/v1",
        "execution_lock_sha256": execution_lock["lock_sha256"],
        "scoring_lock_sha256": scoring_lock["lock_sha256"],
        "acceptance_lock_sha256": acceptance_lock["lock_sha256"],
        "legacy_freeze_sha256": stored_freeze,
        "raw_tree_sha256": raw_tree["tree_sha256"],
        "run_count": summary["run_count"],
        "failure_count": summary["failure_count"],
    }


def build_reuse(
    root: Path, *, basis_commit: str, output: Path
) -> dict[str, Any]:
    root = root.resolve()
    policy = load_invalidation_policy(root / POLICY_PATH)
    execution_lock = load_layer_lock(root / LOCK_PATHS["execution"])
    scoring_lock = load_layer_lock(root / LOCK_PATHS["scoring"])
    summary = _read(root / DEFAULT_RUN_SUMMARY)
    changed = _changed_paths(root, basis_commit)
    receipt = build_evidence_reuse_receipt(
        root,
        execution_lock=execution_lock,
        raw_evidence_binding=scoring_lock["evidence"],
        raw_root=root / DEFAULT_RAW,
        observed_run_count=int(summary["run_count"]),
        observed_failure_count=int(summary["failure_count"]),
        changed_paths=changed,
        policy=policy,
    )
    receipt["basis_commit"] = basis_commit
    receipt["changed_paths_sha256"] = canonical_sha256(changed)
    body = dict(receipt)
    body.pop("receipt_sha256")
    receipt["receipt_sha256"] = canonical_sha256(body)
    _write(root / output, receipt)
    return receipt


def check(root: Path) -> dict[str, Any]:
    root = root.resolve()
    return verify_evidence_dag(
        root,
        lock_paths=LOCK_PATHS,
        reuse_path=DEFAULT_REUSE,
        raw_root=DEFAULT_RAW,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=("build-locks", "build-reuse", "check", "classify")
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--basis-commit", default="a0d6065df50a43e883190f2e0c9b0c8cdac9a3ca")
    parser.add_argument("--changed-path", action="append", default=[])
    parser.add_argument("--output", type=Path, default=DEFAULT_REUSE)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.action == "build-locks":
        result = build_locks(root, basis_commit=args.basis_commit)
    elif args.action == "build-reuse":
        result = build_reuse(
            root, basis_commit=args.basis_commit, output=args.output
        )
    elif args.action == "classify":
        changed = (
            args.changed_path
            if args.changed_path
            else _changed_paths(root, args.basis_commit)
        )
        result = classify_changed_paths(
            changed, load_invalidation_policy(root / POLICY_PATH)
        )
    else:
        result = check(root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") not in {"failed", "rejected"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
