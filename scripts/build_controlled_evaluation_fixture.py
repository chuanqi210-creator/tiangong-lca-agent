#!/usr/bin/env python3
"""Build the deterministic held-out TianGong controlled-evaluation fixture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lca_research_kernel.controlled_evaluation_fixture import (
    compile_controlled_evaluation_fixture,
    verify_controlled_evaluation_fixture,
    write_controlled_evaluation_fixture,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    bundle = compile_controlled_evaluation_fixture(
        root,
        ready_receipt_path=(
            root
            / "outputs/council-default-lifecycle/finalizer/"
            "d18-production-runtime-core-final/lifecycle-ready-receipt.json"
        ),
        contract_path=(
            root
            / "lca_research_kernel/contracts/controlled-research-evaluation-v1.json"
        ),
        exclusions_path=(
            root
            / "lca_research_kernel/contracts/controlled-evaluation-exclusions.json"
        ),
    )
    manifest = write_controlled_evaluation_fixture(bundle, args.output)
    errors = verify_controlled_evaluation_fixture(args.output)
    if errors:
        raise ValueError("; ".join(errors))
    print(
        json.dumps(
            {
                "status": bundle["status"],
                "selected": bundle["selection_receipt"]["selected"][
                    "process_exact_ref"
                ]
                if bundle["selection_receipt"]["selected"]
                else None,
                "manifest_sha256": manifest["manifest_sha256"],
                "next_route": bundle["next_route"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
