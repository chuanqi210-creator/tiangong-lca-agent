#!/usr/bin/env python3
"""Build the open D-18 performance corpus without exposing final answers."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_evaluation.corpus import (
    compile_candidate_pool,
    materialize_open_splits,
    verify_corpus,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--splits", required=True)
    args = parser.parse_args()
    splits = tuple(
        item.strip() for item in args.splits.split(",") if item.strip()
    )
    if splits != ("development", "validation"):
        parser.error("only development,validation may be materialized before freeze")
    root = args.root.resolve()
    contract = json.loads(
        (
            root
            / "lca_research_kernel/contracts/performance-parity-program-v1.json"
        ).read_text(encoding="utf-8")
    )
    pool = compile_candidate_pool(root, contract)
    manifest = materialize_open_splits(pool, args.output)
    report = verify_corpus(args.output, include_final=False)
    print(
        json.dumps(
            {
                "status": report["status"],
                "pool_sha256": pool["pool_sha256"],
                "manifest_sha256": manifest["manifest_sha256"],
                "materialized_task_count": manifest["materialized_task_count"],
                "official_object_count": pool["official_object_count"],
                "final_tasks_materialized": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
