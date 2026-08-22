#!/usr/bin/env python3
"""Build or verify D-18 service-scoped Evidence DAG receipts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lca_research_service.evidence_dag import (
    build_service_evidence_bundle,
    verify_service_evidence_bundle,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--basis", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    report = (
        verify_service_evidence_bundle(root, args.output)
        if args.verify_only
        else build_service_evidence_bundle(
            root, basis_commit=args.basis, output_root=args.output
        )
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status", "accepted") in {"passed", "accepted"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
