#!/usr/bin/env python3
"""Run the frozen D-18 semantic context benchmark."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_evaluation.semantic_context_benchmark import (
    SemanticContextBenchmark,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--acceptance", type=Path)
    args = parser.parse_args()
    benchmark = SemanticContextBenchmark.load(ROOT, args.fixture)
    receipt = benchmark.write(args.output)
    if args.acceptance is not None:
        report = json.loads(
            (args.output / "report.json").read_text(encoding="utf-8")
        )
        args.acceptance.parent.mkdir(parents=True, exist_ok=True)
        args.acceptance.write_text(
            json.dumps(
                {
                    "contract": "lca_research_kernel.semantic_context_acceptance/v1",
                    "fixture_sha256": report["fixture_sha256"],
                    "report_sha256": report["report_sha256"],
                    "status": report["status"],
                    "case_count": report["case_count"],
                    "baseline": report["baseline"],
                    "candidate": report["candidate"],
                    "precision_gain": report["precision_gain"],
                    "failed_gates": report["failed_gates"],
                    "interpretation_limit": report["interpretation_limit"],
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0 if receipt["status"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
