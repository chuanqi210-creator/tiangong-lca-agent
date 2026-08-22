#!/usr/bin/env python3
"""Thin entry point for the grader-only staged regression boundary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lca_research_evaluation.development_regression_grader import (
    build_development_regression_grade,
)


def _object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unreadable") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Grade one byte-frozen development-regression tranche."
    )
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--raw-lock", type=Path, required=True)
    parser.add_argument("--assessments", type=Path, required=True)
    parser.add_argument("--sealed-reference", type=Path, required=True)
    parser.add_argument("--sealed-package-receipt", type=Path, required=True)
    parser.add_argument("--accepted-package-receipt-sha256", required=True)
    parser.add_argument("--extraction-ledger", type=Path, required=True)
    args = parser.parse_args()
    assessments = json.loads(args.assessments.read_text(encoding="utf-8"))
    if not isinstance(assessments, list):
        raise ValueError("assessments must be a list")
    result = build_development_regression_grade(
        assessments,
        raw_root=args.raw_root,
        raw_lock=_object(args.raw_lock, "raw lock"),
        sealed_reference_path=args.sealed_reference,
        sealed_package_receipt_path=args.sealed_package_receipt,
        accepted_package_receipt_sha256=args.accepted_package_receipt_sha256,
        extraction_ledger_path=args.extraction_ledger,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
