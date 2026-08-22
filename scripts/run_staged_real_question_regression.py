#!/usr/bin/env python3
"""Deterministically authorize one frozen development-regression tranche."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_evaluation.staged_real_question_regression import (
    build_population_index,
)


def execute_tranche(
    *,
    epoch: Mapping[str, object],
    tranche_plan: Mapping[str, object],
    tranche_index: int,
    output_root: Path,
    run_questions: Callable[..., Mapping[str, object]],
) -> dict:
    """Run only the selected frozen tranche; a completed replay is read-only."""

    if epoch.get("epoch_sha256") != tranche_plan.get("epoch_sha256"):
        raise ValueError("tranche plan belongs to another regression Epoch")
    if not isinstance(tranche_index, int) or isinstance(tranche_index, bool):
        raise ValueError("tranche index is invalid")
    tranches = tranche_plan.get("tranches")
    if not isinstance(tranches, list) or not 0 <= tranche_index < len(tranches):
        raise ValueError("tranche index is outside the frozen plan")
    tranche = tranches[tranche_index]
    if not isinstance(tranche, Mapping):
        raise ValueError("tranche is invalid")
    question_ids = tranche.get("question_ids")
    if not isinstance(question_ids, list) or not question_ids:
        raise ValueError("tranche question IDs are invalid")
    if tranche_index > 0:
        prior_question_ids = [
            question_id
            for prior in tranches[:tranche_index]
            if isinstance(prior, Mapping)
            for question_id in prior.get("question_ids", [])
            if isinstance(question_id, str)
        ]
        prior_index = build_population_index(
            output_root=output_root,
            expected_question_ids=prior_question_ids,
        )
        prior_statuses = {
            row["question_id"]: row.get("status")
            for row in prior_index["row_refs"]
        }
        if any(prior_statuses.get(question_id) == "failed" for question_id in prior_question_ids):
            raise ValueError("prior staged regression tranche failed")
        if prior_index["completed_question_ids"] != prior_question_ids:
            raise ValueError("prior staged regression tranche is incomplete")
    index = build_population_index(
        output_root=output_root, expected_question_ids=question_ids
    )
    completed = index["completed_question_ids"]
    status_by_question = {
        row["question_id"]: row.get("status") for row in index["row_refs"]
    }
    failed_existing = next(
        (
            question_id
            for question_id in question_ids
            if status_by_question.get(question_id) == "failed"
        ),
        None,
    )
    if failed_existing is not None:
        return {
            "contract": "staged_regression_tranche_execution/v1",
            "epoch_sha256": epoch["epoch_sha256"],
            "tranche_index": tranche_index,
            "question_ids": list(question_ids),
            "status": "stopped_after_question_failure",
            "failed_question_id": failed_existing,
            "population_index": index,
        }
    if completed == question_ids:
        return {
            "contract": "staged_regression_tranche_execution/v1",
            "epoch_sha256": epoch["epoch_sha256"],
            "tranche_index": tranche_index,
            "question_ids": list(question_ids),
            "status": "replayed_without_calls",
            "population_index": index,
        }
    reports = []
    for question_id in question_ids:
        if question_id in completed:
            continue
        reports.append(dict(run_questions(question_ids=[question_id])))
        observed = build_population_index(
            output_root=output_root, expected_question_ids=question_ids
        )
        observed_status = next(
            (
                row.get("status")
                for row in observed["row_refs"]
                if row["question_id"] == question_id
            ),
            None,
        )
        if observed_status is None:
            raise ValueError("tranche runner did not freeze the authorized question")
        if observed_status == "failed":
            return {
                "contract": "staged_regression_tranche_execution/v1",
                "epoch_sha256": epoch["epoch_sha256"],
                "tranche_index": tranche_index,
                "question_ids": list(question_ids),
                "status": "stopped_after_question_failure",
                "failed_question_id": question_id,
                "runner_reports": reports,
                "population_index": observed,
            }
    final_index = build_population_index(
        output_root=output_root, expected_question_ids=question_ids
    )
    if final_index["completed_question_ids"] != question_ids:
        raise ValueError("tranche runner did not freeze every authorized question")
    return {
        "contract": "staged_regression_tranche_execution/v1",
        "epoch_sha256": epoch["epoch_sha256"],
        "tranche_index": tranche_index,
        "question_ids": list(question_ids),
        "status": "executed",
        "runner_reports": reports,
        "population_index": final_index,
    }


__all__ = ["execute_tranche"]


def locked_model_runtime_argv(
    *,
    root: Path,
    script_path: Path,
    current_executable: Path,
    argv: list[str],
) -> list[str] | None:
    """Return a no-shell re-exec command for live provider execution."""

    locked = Path(root).resolve() / "runtime/model-python/.venv/bin/python"
    if not locked.is_file() or not os.access(locked, os.X_OK):
        raise ValueError("locked model Python runtime is unavailable")
    current = Path(current_executable)
    try:
        already_locked = current.is_file() and os.path.samefile(current, locked)
    except OSError:
        already_locked = False
    if already_locked:
        return None
    return [str(locked), str(Path(script_path).resolve()), *argv]


__all__.append("locked_model_runtime_argv")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Freeze or execute one controller-selected regression tranche."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--epoch-root", type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--freeze", action="store_true")
    action.add_argument("--run-tranche", type=int)
    parser.add_argument("--accepted-commit")
    parser.add_argument("--run-started-at")
    parser.add_argument("--credential-env-file", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = args.root.resolve()
    if args.run_tranche is not None:
        reexec_argv = locked_model_runtime_argv(
            root=root,
            script_path=Path(__file__),
            current_executable=Path(sys.executable),
            argv=sys.argv[1:],
        )
        if reexec_argv is not None:
            os.execv(reexec_argv[0], reexec_argv)
    if args.freeze:
        from lca_research_evaluation.staged_real_question_execution import (
            freeze_staged_regression_epoch,
        )

        accepted_commit = args.accepted_commit or subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if not args.run_started_at:
            raise ValueError("freezing requires an explicit run-started-at")
        result = freeze_staged_regression_epoch(
            root=root,
            output_root=args.epoch_root,
            accepted_commit=accepted_commit,
            run_started_at=args.run_started_at,
        )
        summary = {
            "status": "frozen",
            "epoch_sha256": result["epoch"]["epoch_sha256"],
            "execution_lock_sha256": result["execution_lock"]["lock_sha256"],
            "tranche_sizes": [
                row["question_count"] for row in result["plan"]["tranches"]
            ],
        }
    else:
        from lca_research_evaluation.staged_real_question_execution import (
            execute_frozen_tranche,
        )

        if args.credential_env_file is None:
            raise ValueError("tranche execution requires a credential environment file")
        result = execute_frozen_tranche(
            root=root,
            epoch_root=args.epoch_root,
            tranche_index=args.run_tranche,
            credential_env_file=args.credential_env_file,
        )
        summary = {
            "status": result["status"],
            "epoch_sha256": result["epoch_sha256"],
            "tranche_index": result["tranche_index"],
            "question_ids": result["question_ids"],
        }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
