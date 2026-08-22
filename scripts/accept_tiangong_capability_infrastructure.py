#!/usr/bin/env python3
"""Assurance-accept the adopted catalog into a dedicated Project Record."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_kernel.capability_infrastructure_project import (
    accept_capability_infrastructure,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument(
        "--ledger",
        type=Path,
        default=ROOT / "docs" / "lca-agent-capability-infrastructure.json",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=ROOT / "outputs" / "tiangong-capability-infrastructure" / "project-record",
    )
    arguments = parser.parse_args()
    request = json.loads(arguments.request.read_text(encoding="utf-8"))
    print(
        json.dumps(
            accept_capability_infrastructure(
                ROOT,
                arguments.workspace,
                arguments.ledger,
                request,
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
