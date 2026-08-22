#!/usr/bin/env python3
"""Verify and materialize the complete controlled-evaluation acceptance."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_kernel.controlled_research_evaluation import (
    _atomic_write,
    verify_controlled_evaluation_complete,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = verify_controlled_evaluation_complete(
        args.workspace, args.fixture_root
    )
    _atomic_write(args.output, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "error_count": len(report["errors"]),
                "acceptance_sha256": report["acceptance_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
