#!/usr/bin/env python3
"""Grade one frozen adaptive-answer held-out run without model access."""

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

from lca_research_evaluation.adaptive_answer_heldout_grader import grade_heldout


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    grade = grade_heldout(
        args.task,
        args.oracle,
        args.run,
        args.runtime_root,
        args.replay,
    )
    content = (
        json.dumps(grade, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists() and args.output.read_bytes() != content:
        raise ValueError("held-out grade is immutable")
    if not args.output.exists():
        with tempfile.NamedTemporaryFile(dir=args.output.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, args.output)
    print(content.decode("utf-8"), end="")
    return 0 if grade["decision"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
