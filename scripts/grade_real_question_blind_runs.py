#!/usr/bin/env python3
"""Grade frozen blind runs; sealed answers are opened only after lock verification."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_evaluation.dual_track_grader import build_dual_track_grade
from lca_research_evaluation.real_question_contracts import (
    load_real_question_optimization_contract,
)
from lca_research_evaluation.real_question_evidence_invalidation import (
    verify_real_question_raw_evidence_lock,
)


def _load_json(path: Path, label: str) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unreadable") from error


def _atomic_json(path: Path, value: object) -> None:
    content = (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def grade_blind_runs_from_paths(
    *,
    raw_root: Path,
    raw_lock_path: Path,
    sealed_reference_path: Path,
    sealed_package_receipt_path: Path,
    accepted_package_receipt_sha256: str,
    extraction_ledger_path: Path,
    assessments_path: Path,
    output_path: Path,
) -> dict:
    """Verify raw bytes first, then and only then enter the grader-only scope."""

    raw_lock = _load_json(raw_lock_path, "raw evidence lock")
    if not isinstance(raw_lock, Mapping):
        raise ValueError("raw evidence lock is invalid")
    verification = verify_real_question_raw_evidence_lock(raw_root, raw_lock)
    if verification["status"] != "valid":
        raise ValueError("raw evidence lock verification failed before answer reveal")

    assessments = _load_json(assessments_path, "dual-track assessments")
    if not isinstance(assessments, list):
        raise ValueError("dual-track assessments must be a list")
    load_real_question_optimization_contract(ROOT)
    package = build_dual_track_grade(
        assessments,
        raw_root=raw_root,
        raw_lock=raw_lock,
        sealed_reference_path=sealed_reference_path,
        sealed_package_receipt_path=sealed_package_receipt_path,
        accepted_package_receipt_sha256=accepted_package_receipt_sha256,
        extraction_ledger_path=extraction_ledger_path,
    )
    _atomic_json(Path(output_path), package)
    return package


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--raw-lock", type=Path, required=True)
    parser.add_argument("--sealed-reference", type=Path, required=True)
    parser.add_argument("--sealed-package-receipt", type=Path, required=True)
    parser.add_argument("--accepted-package-receipt-sha256", required=True)
    parser.add_argument("--extraction-ledger", type=Path, required=True)
    parser.add_argument("--assessments", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    grade_blind_runs_from_paths(
        raw_root=args.raw_root,
        raw_lock_path=args.raw_lock,
        sealed_reference_path=args.sealed_reference,
        sealed_package_receipt_path=args.sealed_package_receipt,
        accepted_package_receipt_sha256=args.accepted_package_receipt_sha256,
        extraction_ledger_path=args.extraction_ledger,
        assessments_path=args.assessments,
        output_path=args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
