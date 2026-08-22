#!/usr/bin/env python3
"""Run one controlled calculation branch through the D-18 ports."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lca_research_evaluation.controlled_calculation import (
    ControlledLCACalculation,
)
from lca_research_kernel.assurance import AssuranceBoundary
from lca_research_kernel.capability_adapters import (
    IsolatedOpenLCAIPCCapability,
    SourceBoundTIDASValidationCapability,
)
from lca_research_kernel.capability_gateway import CapabilityGateway
from lca_research_kernel.capability_gateway import CapabilityGatewayError
from lca_research_kernel.project_record import ProjectRecord, canonical_sha256


def _atomic_write(path: Path, value: object) -> None:
    content = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == content:
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"controlled calculation artifact is not an object: {path}")
    return value


class _CountingCapability:
    def __init__(self, delegate):
        self.delegate = delegate
        self.calls = 0

    def describe(self):
        return self.delegate.describe()

    def execute(self, payload, *, execution_identity=None, operation_receipt=None):
        self.calls += 1
        return self.delegate.execute(
            payload,
            execution_identity=execution_identity,
            operation_receipt=operation_receipt,
        )


class _FailOnceAcceptedReplayCapability:
    """Evaluation-only failure injection; never performs a second calculation."""

    def __init__(self, delegate, accepted_content: Mapping[str, object]):
        self.delegate = delegate
        self.accepted_content = copy.deepcopy(dict(accepted_content))
        self.calls = 0
        self.failure_codes: list[str] = []

    def describe(self):
        return self.delegate.describe()

    def execute(self, payload, *, execution_identity=None, operation_receipt=None):
        self.calls += 1
        if self.calls == 1:
            self.failure_codes.append("transport_error")
            raise CapabilityGatewayError(
                "transport_error",
                "injected retryable transport failure",
                retryable=True,
                next_route="retry-same-openlca-request",
            )
        expected = self.accepted_content.get("scientific_inputs_sha256")
        if payload.get("scientific_inputs_sha256") != expected:
            raise CapabilityGatewayError(
                "source_drift",
                "accepted calculation input identity drifted",
                retryable=False,
                next_route="repair-source-bound-calculation-input",
            )
        content = copy.deepcopy(self.accepted_content)
        original_sha256 = canonical_sha256(content)
        content["calculations"] = 0
        content["calculation_id"] = "accepted-replay-" + original_sha256[:24]
        runtime = dict(content.get("runtime") or {})
        runtime.update(
            {
                "replayed_from_accepted_result": True,
                "accepted_result_sha256": original_sha256,
                "original_calculations": self.accepted_content.get(
                    "calculations"
                ),
            }
        )
        content["runtime"] = runtime
        return {
            "content": content,
            "source_identity": "openLCA IPC accepted result replay",
            "source_version": self.delegate.describe()["source_version"],
        }


def _runner(
    *,
    workspace: Path,
    fixture: Path,
    validation,
    calculation,
    run_id: str,
) -> ControlledLCACalculation:
    return ControlledLCACalculation(
        repository_root=ROOT,
        workspace=workspace,
        fixture_root=fixture,
        capability_gateway=CapabilityGateway(
            {
                "tidas.validate": validation,
                "openlca.calculate": calculation,
            }
        ),
        assurance=AssuranceBoundary(),
        run_id=run_id,
        clock=lambda: "2026-07-24T12:30:00+08:00",
    )


def _document(runner: ControlledLCACalculation, result: Mapping[str, object]) -> dict:
    return {
        "result": copy.deepcopy(dict(result)),
        "verification": runner.verify(result),
    }


def _read_calculation_content(
    workspace: Path, result: Mapping[str, object]
) -> dict:
    research_id = result["research_id"]
    record = ProjectRecord.open_or_create_project(
        workspace / "projects" / str(research_id)
    )
    view = record.read_current_project_view()
    facts = view["facts"]
    candidate = facts["controlled-calculation-candidate"]
    return copy.deepcopy(candidate["value"]["calculation"])


def _trace_rows(workspace: Path, result: Mapping[str, object]) -> list[dict]:
    trace_ref = result["run_trace_ref"]
    path = (
        workspace
        / "projects"
        / str(result["research_id"])
        / "run-traces"
        / f"{trace_ref['run_id']}.jsonl"
    )
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _snapshot_counts(workspace: Path, result: Mapping[str, object]) -> dict:
    project = workspace / "projects" / str(result["research_id"])
    events = [
        line
        for line in (project / "research-project-events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    trace = _trace_rows(workspace, result)
    return {
        "accepted_project_events": len(events),
        "model_calls": sum(
            row.get("event_type") == "research.run.model.completed" for row in trace
        ),
        "calculation_effects": sum(
            row.get("event_type") == "research.run.capability.completed"
            and row.get("payload", {}).get("capability_id")
            == "openlca.calculate"
            for row in trace
        ),
        "external_writes": 0,
    }


def execute_acceptance_tranche(
    *,
    acceptance_root: Path,
    fixture: Path,
    runtime: Path,
) -> dict:
    """Execute one real calculation plus bounded, no-recalculation branches."""

    acceptance_root = acceptance_root.resolve()
    complete_dir = acceptance_root / "complete"
    missing_dir = acceptance_root / "missing"
    failure_dir = acceptance_root / "tool-failure"
    recovery_dir = acceptance_root / "recovery"
    expected_outputs = (
        complete_dir / "interrupted.json",
        complete_dir / "resumed.json",
        complete_dir / "replayed.json",
        complete_dir / "execution-receipt.json",
        missing_dir / "run.json",
        failure_dir / "run.json",
        failure_dir / "execution-receipt.json",
        recovery_dir / "counts.json",
    )
    if all(path.is_file() for path in expected_outputs):
        return {
            "status": "reused_accepted_execution_tranche",
            "actual_openlca_call_count": _load_json(
                complete_dir / "execution-receipt.json"
            )["actual_openlca_call_count"],
            "outputs": [path.relative_to(acceptance_root).as_posix() for path in expected_outputs],
        }

    actual_validation = SourceBoundTIDASValidationCapability(ROOT, runtime)
    actual_calculation = _CountingCapability(
        IsolatedOpenLCAIPCCapability(ROOT, runtime)
    )
    complete_workspace = complete_dir / "workspace"
    complete_runner = _runner(
        workspace=complete_workspace,
        fixture=fixture,
        validation=actual_validation,
        calculation=actual_calculation,
        run_id="d18-controlled-lca-accepted-v1",
    )
    checkpoint = complete_runner.interrupt_after("tidas_validation")
    interrupted = complete_runner.run(branch="complete")
    _atomic_write(complete_dir / "interrupted.json", _document(complete_runner, interrupted))
    resumed = complete_runner.run(branch="complete", resume_from=checkpoint)
    _atomic_write(complete_dir / "resumed.json", _document(complete_runner, resumed))
    before_replay = _snapshot_counts(complete_workspace, resumed)
    replayed = complete_runner.run(branch="complete", resume_from=checkpoint)
    _atomic_write(complete_dir / "replayed.json", _document(complete_runner, replayed))
    after_replay = _snapshot_counts(complete_workspace, replayed)
    persisted_calculation = _read_calculation_content(complete_workspace, resumed)
    real_effect_count = before_replay["calculation_effects"]
    complete_receipt = {
        "contract": (
            "lca_research_evaluation.controlled_lca_real_execution_receipt/v1"
        ),
        "actual_openlca_call_count": real_effect_count,
        "adapter_call_count_this_process": actual_calculation.calls,
        "content_calculation_count": persisted_calculation.get("calculations"),
        "scientific_inputs_sha256": persisted_calculation.get(
            "scientific_inputs_sha256"
        ),
        "source_version": persisted_calculation.get("runtime"),
        "executed_external_writes": 0,
    }
    complete_receipt["receipt_sha256"] = canonical_sha256(complete_receipt)
    _atomic_write(complete_dir / "execution-receipt.json", complete_receipt)
    _atomic_write(
        recovery_dir / "counts.json",
        {
            "before_replay": before_replay,
            "after_replay": after_replay,
        },
    )

    missing_workspace = missing_dir / "workspace"
    missing_runner = _runner(
        workspace=missing_workspace,
        fixture=fixture,
        validation=SourceBoundTIDASValidationCapability(ROOT, runtime),
        calculation=IsolatedOpenLCAIPCCapability(ROOT, runtime),
        run_id="d18-controlled-lca-missing-v1",
    )
    missing = missing_runner.run(branch="missing")
    _atomic_write(missing_dir / "run.json", _document(missing_runner, missing))

    replay_capability = _FailOnceAcceptedReplayCapability(
        IsolatedOpenLCAIPCCapability(ROOT, runtime),
        persisted_calculation,
    )
    failure_workspace = failure_dir / "workspace"
    failure_runner = _runner(
        workspace=failure_workspace,
        fixture=fixture,
        validation=SourceBoundTIDASValidationCapability(ROOT, runtime),
        calculation=replay_capability,
        run_id="d18-controlled-lca-tool-failure-v1",
    )
    failure = failure_runner.run(branch="tool_failure")
    _atomic_write(failure_dir / "run.json", _document(failure_runner, failure))
    failure_receipt = {
        "contract": (
            "lca_research_evaluation.controlled_lca_failure_recovery_receipt/v1"
        ),
        "attempts": replay_capability.calls,
        "failure_codes": replay_capability.failure_codes,
        "retryable": True,
        "actual_openlca_call_count": 0,
        "reused_accepted_result": True,
        "scientific_inputs_sha256": persisted_calculation.get(
            "scientific_inputs_sha256"
        ),
        "executed_external_writes": 0,
    }
    failure_receipt["receipt_sha256"] = canonical_sha256(failure_receipt)
    _atomic_write(failure_dir / "execution-receipt.json", failure_receipt)

    return {
        "status": "executed",
        "actual_openlca_call_count": real_effect_count,
        "adapter_call_count_this_process": actual_calculation.calls,
        "missing_capability_receipt_count": len(
            missing.get("capability_receipts") or []
        ),
        "failure_attempts": replay_capability.calls,
        "replay_no_growth": before_replay == after_replay,
        "executed_external_writes": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workspace",
        type=Path,
        default=ROOT / "outputs/d18-controlled-calculation-runs",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=ROOT / "outputs/d18-controlled-calculation-v2",
    )
    parser.add_argument(
        "--runtime",
        type=Path,
        default=ROOT / ".runtime/d18-tiangong-capabilities",
    )
    parser.add_argument(
        "--branch",
        choices=("complete", "missing", "tool_failure"),
    )
    parser.add_argument("--acceptance-root", type=Path)
    parser.add_argument(
        "--run-id",
        default="d18-controlled-lca-calculation-v1",
    )
    args = parser.parse_args()
    if (args.branch is None) == (args.acceptance_root is None):
        parser.error("select exactly one of --branch or --acceptance-root")
    if args.acceptance_root is not None:
        output = execute_acceptance_tranche(
            acceptance_root=args.acceptance_root,
            fixture=args.fixture,
            runtime=args.runtime,
        )
        print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    validation = SourceBoundTIDASValidationCapability(ROOT, args.runtime)
    calculation = IsolatedOpenLCAIPCCapability(ROOT, args.runtime)
    runner = _runner(
        workspace=args.workspace,
        fixture=args.fixture,
        validation=validation,
        calculation=calculation,
        run_id=args.run_id,
    )
    result = runner.run(branch=args.branch)
    verification = runner.verify(result)
    output = {
        "result": result,
        "verification": verification,
    }
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if verification["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
