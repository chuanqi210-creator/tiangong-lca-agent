#!/usr/bin/env python3
"""Write or verify the deterministic adaptive-answer repair receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_evaluation.adaptive_answer_repair import (
    ACCEPTANCE_OUTPUT,
    CLAIM_OUTPUT,
    HUMAN_OUTPUT,
    OUTPUT_ROOT,
    render_repair,
    SOURCE_OUTPUT,
    write_repair,
)


def verify(root: Path, *, output_root: Path = None) -> dict:
    root = Path(root).resolve()
    output = Path(output_root) if output_root is not None else root / OUTPUT_ROOT
    result, payloads = render_repair(root)
    expected = {
        "status": result["acceptance"]["status"],
        "next_route": result["acceptance"]["next_route"],
        "artifacts": {
            name: hashlib.sha256(payload).hexdigest()
            for name, payload in payloads.items()
        },
    }
    for name, payload in payloads.items():
        path = output / name
        if not path.is_file() or path.read_bytes() != payload:
            raise ValueError(f"repair artifact drifted: {name}")
    if set(expected["artifacts"]) != {
        CLAIM_OUTPUT,
        SOURCE_OUTPUT,
        ACCEPTANCE_OUTPUT,
        HUMAN_OUTPUT,
    }:
        raise ValueError("repair artifact coverage drifted")
    return expected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    result = write_repair(args.root) if args.write else verify(args.root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
