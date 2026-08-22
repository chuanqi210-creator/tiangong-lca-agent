#!/usr/bin/env python3
"""Snapshot or verify idempotent counterfactual recovery."""

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
    snapshot_controlled_evaluation_recovery,
    verify_controlled_evaluation_counterfactual_recovery,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--fixture-root", type=Path)
    args = parser.parse_args()
    if args.before is None:
        value = snapshot_controlled_evaluation_recovery(args.workspace)
    else:
        if args.fixture_root is None:
            raise ValueError("--fixture-root is required for verification")
        before = json.loads(args.before.read_text(encoding="utf-8"))
        value = verify_controlled_evaluation_counterfactual_recovery(
            args.workspace,
            args.fixture_root,
            before,
        )
    _atomic_write(args.output, value)
    print(
        json.dumps(
            {
                "status": value.get("status", "snapshot_created"),
                "error_count": len(value.get("errors") or []),
                "identity_sha256": value.get("acceptance_sha256")
                or value.get("snapshot_sha256"),
            },
            sort_keys=True,
        )
    )
    return 0 if value.get("status", "passed") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
