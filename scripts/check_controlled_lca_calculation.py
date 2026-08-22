#!/usr/bin/env python3
"""Mechanically accept the first controlled TianGong-native LCA calculation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_kernel.project_record import ProjectRecord, canonical_sha256


EVIDENCE_CONTRACT = (
    "lca_research_evaluation.controlled_lca_calculation_evidence/v1"
)
ACCEPTANCE_CONTRACT = (
    "lca_research_evaluation.controlled_lca_calculation_acceptance/v1"
)
PASS_ROUTE = "build-frozen-sixty-task-lca-agent-benchmark"


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"required calculation artifact is unreadable: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"required calculation artifact is not an object: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_write(path: Path, value: object) -> None:
    content = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
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


def _count_trace(path: Path, event_type: str, capability_id: str | None = None) -> int:
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("event_type") != event_type:
            continue
        payload = row.get("payload")
        if capability_id is not None and (
            not isinstance(payload, Mapping)
            or payload.get("capability_id") != capability_id
        ):
            continue
        count += 1
    return count


def _project_view(workspace: Path, result: Mapping[str, object]) -> tuple[dict, Path]:
    research_id = result.get("research_id")
    if not isinstance(research_id, str) or not research_id:
        raise ValueError("calculation result has no research_id")
    project = workspace / "projects" / research_id
    record = ProjectRecord.open_or_create_project(project)
    record.rebuild_and_verify()
    return record.read_current_project_view(), project


def _calculation_content(view: Mapping[str, object]) -> dict:
    facts = view.get("facts")
    if not isinstance(facts, Mapping):
        raise ValueError("Project Record facts are absent")
    candidate = facts.get("controlled-calculation-candidate")
    if not isinstance(candidate, Mapping):
        raise ValueError("controlled calculation candidate is absent")
    value = candidate.get("value")
    calculation = value.get("calculation") if isinstance(value, Mapping) else None
    if not isinstance(calculation, dict):
        raise ValueError("controlled calculation content is absent")
    return calculation


def _load_execution(path: Path) -> dict:
    document = _load_json(path)
    result = document.get("result")
    verification = document.get("verification")
    if not isinstance(result, dict) or not isinstance(verification, dict):
        raise ValueError(f"execution document lacks result/verification: {path}")
    return document


def collect_acceptance_evidence(root: Path, workspace: Path) -> dict:
    """Compile only source-bound facts; the oracle remains grader-only."""

    root = root.resolve()
    workspace = workspace.resolve()
    complete = _load_execution(workspace / "complete/resumed.json")
    missing = _load_execution(workspace / "missing/run.json")
    failure = _load_execution(workspace / "tool-failure/run.json")
    recovery = _load_json(workspace / "recovery/counts.json")
    execution = _load_json(workspace / "complete/execution-receipt.json")
    failure_execution = _load_json(
        workspace / "tool-failure/execution-receipt.json"
    )

    complete_workspace = workspace / "complete/workspace"
    complete_view, complete_project = _project_view(
        complete_workspace, complete["result"]
    )
    calculation = _calculation_content(complete_view)
    method = calculation.get("method")
    result = calculation.get("result")
    if not isinstance(method, Mapping) or not isinstance(result, Mapping):
        raise ValueError("accepted calculation method/result is absent")

    missing_view, _ = _project_view(
        workspace / "missing/workspace", missing["result"]
    )
    failure_view, failure_project = _project_view(
        workspace / "tool-failure/workspace", failure["result"]
    )
    failure_calculation = _calculation_content(failure_view)
    failure_runtime = failure_calculation.get("runtime")

    complete_trace = complete_project / "run-traces" / (
        complete["result"]["run_trace_ref"]["run_id"] + ".jsonl"
    )
    failure_trace = failure_project / "run-traces" / (
        failure["result"]["run_trace_ref"]["run_id"] + ".jsonl"
    )
    failure_requests = []
    failure_scientific_inputs = []
    for line in failure_trace.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("event_type") not in {
            "research.run.capability.failed",
            "research.run.capability.completed",
        }:
            continue
        payload = row.get("payload")
        if (
            not isinstance(payload, Mapping)
            or payload.get("capability_id") != "openlca.calculate"
        ):
            continue
        # Failure and completion envelopes intentionally hash different
        # transport metadata. The intent hash is the stable, source-bound
        # scientific request identity that must survive a retry unchanged.
        failure_requests.append(payload.get("intent_sha256"))
        failure_scientific_inputs.append(
            failure_execution.get("scientific_inputs_sha256")
        )

    missing_facts = missing_view.get("facts")
    candidate_present = (
        isinstance(missing_facts, Mapping)
        and "controlled-calculation-candidate" in missing_facts
    )
    source_refs = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256(path),
        }
        for path in (
            workspace / "complete/resumed.json",
            workspace / "complete/replayed.json",
            workspace / "missing/run.json",
            workspace / "tool-failure/run.json",
            workspace / "recovery/counts.json",
        )
    ]
    evidence = {
        "contract": EVIDENCE_CONTRACT,
        "complete": {
            "verification_status": complete["verification"].get("status"),
            "calculation_executed": complete["result"].get(
                "calculation_executed"
            ),
            "actual_openlca_call_count": execution.get(
                "actual_openlca_call_count"
            ),
            "actual_calculation_effects": _count_trace(
                complete_trace,
                "research.run.capability.completed",
                "openlca.calculate",
            ),
            "method_identity": method.get("identity"),
            "method_version": method.get("version"),
            "result_value": result.get("value"),
            "result_unit": result.get("unit"),
            "scientific_inputs_sha256": calculation.get(
                "scientific_inputs_sha256"
            ),
            "external_writes": complete["result"].get(
                "executed_external_writes"
            ),
        },
        "missing": {
            "verification_status": missing["verification"].get("status"),
            "calculation_executed": missing["result"].get(
                "calculation_executed"
            ),
            "capability_receipt_count": len(
                missing["result"].get("capability_receipts") or []
            ),
            "candidate_present": candidate_present,
            "external_writes": missing["result"].get(
                "executed_external_writes"
            ),
        },
        "tool_failure": {
            "verification_status": failure["verification"].get("status"),
            "failure_codes": failure_execution.get("failure_codes") or [],
            "retryable": failure_execution.get("retryable"),
            "request_sha256s": failure_requests,
            "scientific_input_sha256s": failure_scientific_inputs,
            "attempts": failure_execution.get("attempts"),
            "actual_openlca_call_count": failure_execution.get(
                "actual_openlca_call_count"
            ),
            "reused_accepted_result": (
                isinstance(failure_runtime, Mapping)
                and failure_runtime.get("replayed_from_accepted_result") is True
            ),
            "external_writes": failure["result"].get(
                "executed_external_writes"
            ),
        },
        "recovery": recovery,
        "executed_external_writes": 0,
        "source_refs": source_refs,
    }
    evidence["evidence_sha256"] = canonical_sha256(evidence)
    return evidence


def evaluate_acceptance_evidence(
    evidence: Mapping[str, object],
    oracle: Mapping[str, object],
) -> dict:
    errors: list[str] = []
    if evidence.get("contract") != EVIDENCE_CONTRACT:
        errors.append("evidence_contract_invalid")
    complete = evidence.get("complete")
    missing = evidence.get("missing")
    failure = evidence.get("tool_failure")
    recovery = evidence.get("recovery")
    if not isinstance(complete, Mapping):
        errors.append("complete_evidence_absent")
        complete = {}
    if not isinstance(missing, Mapping):
        errors.append("missing_evidence_absent")
        missing = {}
    if not isinstance(failure, Mapping):
        errors.append("tool_failure_evidence_absent")
        failure = {}
    if not isinstance(recovery, Mapping):
        errors.append("recovery_evidence_absent")
        recovery = {}

    if complete.get("verification_status") != "passed":
        errors.append("complete_verification_failed")
    if complete.get("calculation_executed") is not True:
        errors.append("complete_calculation_not_executed")
    real_count = complete.get("actual_openlca_call_count")
    if real_count != 1:
        errors.append("real_calculation_count_not_exactly_one")
    if complete.get("actual_calculation_effects") != 1:
        errors.append("complete_calculation_effect_count_invalid")
    if complete.get("method_identity") != oracle.get("method_identity"):
        errors.append("complete_method_identity_drifted")
    if complete.get("method_version") != oracle.get("method_version"):
        errors.append("complete_method_version_drifted")
    if (
        complete.get("scientific_inputs_sha256")
        != oracle.get("scientific_inputs_sha256")
    ):
        errors.append("complete_scientific_inputs_drifted")
    impacts = (oracle.get("expected_results") or {}).get("impacts")  # type: ignore[union-attr]
    expected = impacts[0] if isinstance(impacts, list) and impacts else {}
    if complete.get("result_unit") != expected.get("unit"):
        errors.append("complete_result_unit_drifted")
    try:
        actual_value = Decimal(str(complete.get("result_value")))
        expected_value = Decimal(str(expected.get("value")))
        tolerance_contract = oracle.get("numeric_tolerance") or {}
        tolerance = max(
            Decimal(str(tolerance_contract.get("absolute"))),  # type: ignore[union-attr]
            abs(expected_value)
            * Decimal(str(tolerance_contract.get("relative"))),  # type: ignore[union-attr]
        )
        if abs(actual_value - expected_value) > tolerance:
            errors.append("complete_result_value_drifted")
    except (InvalidOperation, TypeError, ValueError):
        errors.append("complete_result_value_invalid")

    if missing.get("verification_status") != "passed":
        errors.append("missing_verification_failed")
    if missing.get("calculation_executed") is not False:
        errors.append("missing_branch_executed_calculation")
    if missing.get("capability_receipt_count") != 0:
        errors.append("missing_branch_executed_capability")
    if missing.get("candidate_present") is not False:
        errors.append("missing_branch_produced_candidate")

    if failure.get("verification_status") != "passed":
        errors.append("tool_failure_verification_failed")
    if failure.get("failure_codes") != ["transport_error"]:
        errors.append("tool_failure_classification_invalid")
    if failure.get("retryable") is not True:
        errors.append("tool_failure_not_retryable")
    request_hashes = failure.get("request_sha256s")
    if (
        not isinstance(request_hashes, list)
        or len(request_hashes) != 2
        or len(set(request_hashes)) != 1
    ):
        errors.append("tool_failure_request_changed")
    scientific_hashes = failure.get("scientific_input_sha256s")
    if (
        not isinstance(scientific_hashes, list)
        or len(scientific_hashes) != 2
        or len(set(scientific_hashes)) != 1
        or (
            scientific_hashes
            and scientific_hashes[0] != oracle.get("scientific_inputs_sha256")
        )
    ):
        errors.append("tool_failure_scientific_input_changed")
    if failure.get("attempts") != 2:
        errors.append("tool_failure_attempt_count_invalid")
    if failure.get("actual_openlca_call_count") != 0:
        errors.append("tool_failure_recalculated")
    if failure.get("reused_accepted_result") is not True:
        errors.append("tool_failure_did_not_reuse_accepted_result")

    before = recovery.get("before_replay")
    after = recovery.get("after_replay")
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        errors.append("recovery_counts_absent")
    else:
        for field in (
            "accepted_project_events",
            "model_calls",
            "calculation_effects",
        ):
            if after.get(field) != before.get(field):
                errors.append(f"recovery_{field}_grew")
        if after.get("external_writes") != 0:
            errors.append("recovery_external_write_detected")

    external_rows = [
        evidence.get("executed_external_writes"),
        complete.get("external_writes"),
        missing.get("external_writes"),
        failure.get("external_writes"),
    ]
    if any(value != 0 for value in external_rows):
        errors.append("external_write_detected")
    report = {
        "contract": ACCEPTANCE_CONTRACT,
        "status": "passed" if not errors else "failed",
        "errors": sorted(set(errors)),
        "error_count": len(set(errors)),
        "real_calculation_count": real_count,
        "method_identity": complete.get("method_identity"),
        "method_version": complete.get("method_version"),
        "result_value": complete.get("result_value"),
        "result_unit": complete.get("result_unit"),
        "numeric_tolerance": oracle.get("numeric_tolerance"),
        "missing_branch_bounded": (
            missing.get("calculation_executed") is False
            and missing.get("capability_receipt_count") == 0
        ),
        "tool_failure_recovered_same_request": (
            isinstance(request_hashes, list)
            and len(request_hashes) == 2
            and len(set(request_hashes)) == 1
        ),
        "replay_no_growth": (
            isinstance(before, Mapping)
            and isinstance(after, Mapping)
            and all(
                after.get(field) == before.get(field)
                for field in (
                    "accepted_project_events",
                    "model_calls",
                    "calculation_effects",
                )
            )
        ),
        "executed_external_writes": 0,
        "claim_ceiling": (
            "one source-bound candidate calculation accepted; no general "
            "performance or universal scientific correctness claim"
        ),
        "next_routes": [PASS_ROUTE] if not errors else [
            "repair-first-real-lca-calculation-evidence"
        ],
    }
    report["acceptance_sha256"] = canonical_sha256(report)
    return report


def check_workspace(root: Path, workspace: Path) -> dict:
    evidence = collect_acceptance_evidence(root, workspace)
    oracle = _load_json(
        root
        / "outputs/d18-controlled-calculation-v2/oracle/calculation-oracle.json"
    )
    report = evaluate_acceptance_evidence(evidence, oracle)
    _atomic_write(workspace / "final/evidence.json", evidence)
    _atomic_write(workspace / "final/acceptance.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("outputs/d18-controlled-calculation"),
    )
    args = parser.parse_args()
    report = check_workspace(args.root.resolve(), args.workspace.resolve())
    print(
        json.dumps(
            {
                "status": report["status"],
                "error_count": report["error_count"],
                "real_calculation_count": report["real_calculation_count"],
                "acceptance_sha256": report["acceptance_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
