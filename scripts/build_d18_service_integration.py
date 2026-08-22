#!/usr/bin/env python3
"""Build deterministic D-18 frontend integration fixtures without real effects."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lca_research_service.integration_harness import build_integration_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    manifest = build_integration_bundle(args.output)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
