#!/usr/bin/env python3
"""Probe the D-18 service protocol through a no-effect in-process mock."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lca_research_service.integration_harness import probe_mock_protocol


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_root", type=Path)
    args = parser.parse_args()
    report = probe_mock_protocol(args.data_root)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
