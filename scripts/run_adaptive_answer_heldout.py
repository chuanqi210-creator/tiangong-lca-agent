#!/usr/bin/env python3
"""Freeze or run the single adaptive-answer held-out evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_evaluation.adaptive_answer_heldout import (
    AdaptiveAnswerHeldoutRunner,
    build_replay_receipt,
    canonical_sha256,
    freeze_fixture,
    runtime_evidence_snapshot,
)
from lca_research_service.runtime_factory import RuntimeFactory


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _under_root(root: Path, path: Path, label: str) -> Path:
    resolved_root = root.resolve()
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(f"{label} escaped the repository root") from error
    return resolved


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("freeze", "run"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--candidates", type=Path)
    parser.add_argument("--oracles", type=Path)
    parser.add_argument("--seed-sha256")
    parser.add_argument("--execution-root", type=Path, required=True)
    parser.add_argument("--grader-root", type=Path)
    parser.add_argument("--runtime-data-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    return parser


def run(args: argparse.Namespace) -> dict:
    root = Path(args.root).resolve()
    execution_root = _under_root(root, args.execution_root, "execution root")
    if args.command == "freeze":
        if not args.candidates or not args.oracles or not args.grader_root:
            raise ValueError("freeze paths are incomplete")
        return freeze_fixture(
            _under_root(root, args.candidates, "candidate pool"),
            _under_root(root, args.oracles, "oracle catalog"),
            seed_sha256=args.seed_sha256,
            execution_root=execution_root,
            grader_root=_under_root(root, args.grader_root, "grader root"),
        )

    if not args.runtime_data_root or not args.output_root:
        raise ValueError("runtime paths are incomplete")
    task_path = execution_root / "task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    run_id = "adaptive-heldout-" + canonical_sha256(task)[:24]
    runtime_data_root = _under_root(root, args.runtime_data_root, "runtime data root")
    output_root = _under_root(root, args.output_root, "run output root")
    workspace = runtime_data_root / "projects" / run_id
    replaying = (output_root / "run.json").is_file()
    before = runtime_evidence_snapshot(workspace, run_id) if replaying else None
    binding = RuntimeFactory(
        repository_root=root,
        data_root=runtime_data_root,
        run_id=run_id,
        clock=_utc_now,
    ).build()
    if not binding.ready or binding.runtime is None:
        raise RuntimeError(f"Product Interface unavailable: {binding.failure_code}")
    report = AdaptiveAnswerHeldoutRunner(
        product_interface=binding.runtime.product_interface,
        task_path=task_path,
        output_root=output_root,
        clock=_utc_now,
        evidence_snapshot=lambda: runtime_evidence_snapshot(workspace, run_id),
    ).run_or_resume()
    if replaying:
        assert before is not None
        receipt = build_replay_receipt(
            before, runtime_evidence_snapshot(workspace, run_id)
        )
        replay_path = output_root / "replay-receipt.json"
        content = (
            json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        if replay_path.exists() and replay_path.read_bytes() != content:
            raise ValueError("held-out replay receipt is immutable")
        if not replay_path.exists():
            replay_path.write_bytes(content)
    return report


def main() -> int:
    args = _parser().parse_args()
    try:
        report = run(args)
    except (OSError, RuntimeError, ValueError) as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "failure_code": type(error).__name__,
                    "external_writes": 0,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
