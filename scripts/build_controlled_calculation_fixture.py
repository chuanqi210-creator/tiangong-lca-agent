#!/usr/bin/env python3
"""Build and verify the frozen first real calculation fixture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_evaluation.calculation_fixture import (
    compile_calculation_fixture,
    verify_calculation_fixture,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/d18-controlled-calculation-v2"),
    )
    parser.add_argument(
        "--selection-seed",
        default="d18-performance-parity-first-real-calculation-v1",
    )
    args = parser.parse_args()
    compile_calculation_fixture(ROOT, args.output, selection_seed=args.selection_seed)
    report = verify_calculation_fixture(args.output)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
