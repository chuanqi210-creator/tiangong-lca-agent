#!/usr/bin/env python3
"""Build or verify the metadata-only TianGong capability catalog."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_kernel.capability_catalog import CapabilityCatalog
from lca_research_kernel.tiangong_capability_inventory import (
    build_catalog,
    compare_catalogs,
    scan_tiangong_surfaces,
)


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--source-lock", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--observation", type=Path)
    parser.add_argument("--previous-catalog", type=Path)
    parser.add_argument("--candidate-diff", type=Path)
    parser.add_argument("--catalog-id", default="tiangong-capability-infrastructure-2026-07-23")
    parser.add_argument("--check", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.check:
        catalog = CapabilityCatalog.from_files(args.catalog, args.source_lock)
        kinds = {row["kind"] for row in catalog.list_descriptors()}
        required = {
            "specification",
            "data",
            "native_tool",
            "skill",
            "mcp_tool",
            "build",
            "validation",
            "calculation",
            "evidence",
        }
        missing = sorted(required - kinds)
        if missing:
            raise ValueError(f"catalog coverage is incomplete: {missing}")
        print(
            json.dumps(
                {
                    "status": "passed",
                    "capability_count": len(catalog.list_descriptors()),
                    "snapshot_sha256": catalog.snapshot_sha256,
                },
                sort_keys=True,
            )
        )
        return 0
    if args.source_root is None or args.observation is None:
        raise ValueError("build mode requires --source-root and --observation")
    locks = _read(args.source_lock)
    observation = scan_tiangong_surfaces(args.source_root)
    catalog = build_catalog(observation, locks, catalog_id=args.catalog_id)
    CapabilityCatalog(catalog, locks)
    _write(args.observation, observation)
    if args.previous_catalog is not None:
        if args.candidate_diff is None:
            raise ValueError("--previous-catalog requires --candidate-diff")
        _write(
            args.candidate_diff,
            compare_catalogs(_read(args.previous_catalog), catalog),
        )
    _write(args.catalog, catalog)
    print(
        json.dumps(
            {
                "status": "built",
                "capability_count": len(catalog["capabilities"]),
                "observation_sha256": observation["observation_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
