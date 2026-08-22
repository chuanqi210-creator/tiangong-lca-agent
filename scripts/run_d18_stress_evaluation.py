#!/usr/bin/env python3
"""Run the frozen D-18 stress suite without scientific or external effects."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_evaluation.stress import (
    compile_stress_fixtures,
    run_stress_suite,
)
from lca_research_kernel.assurance import AssuranceBoundary
from lca_research_kernel.model_gateway import DeterministicFakeAdapter, ModelGateway
from lca_research_kernel.product_interface import ProductInterface


def _product(root: Path) -> ProductInterface:
    response = {
        "status": "candidate_only",
        "bounded_conclusion": "Only the exact source-bound identity is retained.",
        "missing_fields": [],
        "limitations": ["This is a non-scientific recovery probe."],
        "scientific_source_sha256s": ["a" * 64],
        "runtime_provenance_sha256s": ["f" * 64],
        "official_object_ids": [
            "11111111-1111-1111-1111-111111111111@01.01.000"
        ],
        "values": [],
        "capability_intent": None,
    }
    return ProductInterface(
        root,
        model_gateway=ModelGateway(
            {"fake": DeterministicFakeAdapter(response)},
            default_provider="fake",
        ),
        assurance=AssuranceBoundary(),
        clock=lambda: "2026-07-24T09:30:00Z",
        run_id="stress-recovery-run",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    output = (
        args.output.resolve()
        if args.output.is_absolute()
        else (root / args.output).resolve()
    )
    output.relative_to(root)
    report = run_stress_suite(
        _product(output / "product-runtime"),
        compile_stress_fixtures(),
        output,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
