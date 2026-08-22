#!/usr/bin/env python3
"""Verify the adopted D-18 TianGong capability infrastructure."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_kernel.capability_infrastructure_acceptance import (
    verify_capability_infrastructure,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ledger",
        type=Path,
        default=ROOT / "docs" / "lca-agent-capability-infrastructure.json",
    )
    arguments = parser.parse_args()
    print(
        json.dumps(
            verify_capability_infrastructure(ROOT, arguments.ledger),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
