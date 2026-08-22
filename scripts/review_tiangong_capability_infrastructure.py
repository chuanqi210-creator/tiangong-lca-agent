#!/usr/bin/env python3
"""Run a bounded clean-context review and write its deterministic result."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_kernel.capability_infrastructure_clean_review import (
    review_capability_infrastructure,
)


def _write_atomic(path: Path, value: object) -> None:
    content = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
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
    parser.add_argument(
        "--generated-root",
        type=Path,
        default=ROOT / "outputs" / "tiangong-capability-infrastructure",
    )
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    review = review_capability_infrastructure(
        ROOT,
        arguments.ledger,
        arguments.workspace,
        arguments.generated_root,
    )
    _write_atomic(arguments.output, review)
    print(json.dumps(review, ensure_ascii=False, sort_keys=True))
    return 0 if review["status"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
