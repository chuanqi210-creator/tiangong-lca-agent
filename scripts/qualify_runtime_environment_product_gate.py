#!/usr/bin/env python3
"""Bind the current official-source runtime row to a passed product journey."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_kernel.runtime_environment import RuntimeEnvironmentContract
from lca_research_kernel.runtime_environment_health import verify_matrix_receipts
from lca_research_service.product_readiness_gate import (
    canonical_sha256,
    evaluate_official_journey,
)
from scripts.accept_runtime_environment import accept_runtime_environment


BASE_MATRIX = Path(
    "outputs/runtime-environment/"
    "runtime-readiness-matrix-source-binding-v8.json"
)
CURRENT_MATRIX = Path(
    "outputs/runtime-environment/"
    "runtime-readiness-matrix-source-binding-v10.json"
)
CURRENT_SUMMARY = Path(
    "outputs/runtime-environment/"
    "runtime-health-summary-source-binding-v10.json"
)
CURRENT_RECEIPT = Path(
    "outputs/runtime-environment/receipts/"
    "official-public-source-product-readiness-v10-20260820.json"
)
CURRENT_PROJECT_RECORD = Path(
    "outputs/runtime-environment/project-record-v12"
)
PRODUCT_EVIDENCE_ROOT = Path(
    "outputs/d18-product-readiness/phase4-live-0cc0034c/"
    "releases/"
    "98213e95e2fd9e391acbe8de523c054f51f2daebb9b8b34ed74c9b78fc8dad35/"
    "9273a9e1a196a9aecb43a59e3e008a59ab084943df767a8a57f266e411165371/"
    "journeys/official_source_lca_question"
)
PRODUCT_OBSERVATION = PRODUCT_EVIDENCE_ROOT / "observation.json"
PRODUCT_JOURNEY_RECEIPT = PRODUCT_EVIDENCE_ROOT / "receipt.json"
OFFICIAL_CAPABILITY_ID = "official-public-source-read"
OFFICIAL_SOURCE_LOCK_ID = "lca.kernel.official-public-source"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(Path(path).read_bytes())


def _read(path: Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _json_bytes(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"immutable evidence drifted: {path}")
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise ValueError(f"immutable evidence drifted: {path}")
    finally:
        temporary.unlink(missing_ok=True)


def _source_lock_row(root: Path, source_id: str) -> dict:
    lock = _read(root / "lca_research_kernel/contracts/tiangong-source-lock.json")
    rows = lock.get("sources")
    if not isinstance(rows, list):
        raise ValueError("TianGong source lock rows are absent")
    matches = [
        dict(row)
        for row in rows
        if isinstance(row, Mapping) and row.get("source_id") == source_id
    ]
    if len(matches) != 1:
        raise ValueError(f"TianGong source lock is ambiguous: {source_id}")
    return matches[0]


def _validate_product_journey(
    observation: Mapping[str, object], journey_receipt: Mapping[str, object]
) -> dict:
    evaluated = evaluate_official_journey(observation)
    errors = evaluated.get("errors")
    if evaluated.get("status") != "passed":
        labels = set(errors) if isinstance(errors, list) else set()
        if "official_external_read_count_invalid" in labels:
            raise ValueError("official product journey external read count is invalid")
        raise ValueError(
            "official product journey is not accepted: "
            + ", ".join(sorted(str(item) for item in labels))
        )
    if dict(journey_receipt) != evaluated:
        raise ValueError("official product journey receipt drifted")

    result_view = observation.get("result_view")
    if not isinstance(result_view, Mapping):
        raise ValueError("official product result view is absent")
    execution = result_view.get("executionReceipt")
    result = result_view.get("result")
    if not isinstance(execution, Mapping) or not isinstance(result, Mapping):
        raise ValueError("official product accepted result evidence is absent")
    counts = execution.get("counts")
    model_calls = execution.get("model_calls")
    sources = result.get("officialSources")
    claim_bindings = result.get("officialClaimBindings")
    project_record = execution.get("project_record")
    trace_refs = execution.get("trace_refs")
    if not isinstance(counts, Mapping):
        raise ValueError("official product execution counts are absent")
    if counts.get("external_read") != 1:
        raise ValueError("official product journey external read count is invalid")
    if (
        counts.get("model") != 2
        or counts.get("tool") != 1
        or counts.get("external_write") != 0
        or counts.get("useless_retry") != 0
    ):
        raise ValueError("official product execution budget drifted")
    if (
        not isinstance(model_calls, list)
        or len(model_calls) != 2
        or {call.get("provider") for call in model_calls if isinstance(call, Mapping)}
        != {"gemini"}
    ):
        raise ValueError("official product provider evidence drifted")
    if not isinstance(sources, list) or len(sources) != 1:
        raise ValueError("official product source evidence is ambiguous")
    source = sources[0]
    if not isinstance(source, Mapping):
        raise ValueError("official product source evidence is invalid")
    source_id = source.get("sourceId")
    body_sha256 = source.get("bodySha256")
    if (
        not isinstance(source_id, str)
        or not source_id
        or not isinstance(body_sha256, str)
        or len(body_sha256) != 64
    ):
        raise ValueError("official product source identity is invalid")
    if not isinstance(claim_bindings, list) or not claim_bindings:
        raise ValueError("official product claim bindings are absent")
    for binding in claim_bindings:
        if (
            not isinstance(binding, Mapping)
            or binding.get("sourceIds") != [source_id]
            or binding.get("sourceSha256s") != [body_sha256]
        ):
            raise ValueError("official product claim/source binding drifted")
    if (
        not isinstance(project_record, Mapping)
        or not isinstance(project_record.get("event_count"), int)
        or not isinstance(project_record.get("last_event_sha256"), str)
    ):
        raise ValueError("official product Project Record reference is absent")
    if not isinstance(trace_refs, list) or len(trace_refs) != 1:
        raise ValueError("official product Run Trace reference is absent")
    return {
        "evaluated_receipt": evaluated,
        "execution": execution,
        "model_calls": model_calls,
        "source": dict(source),
        "claim_bindings": deepcopy(claim_bindings),
        "project_record": dict(project_record),
        "trace_refs": deepcopy(trace_refs),
    }


def build_current_runtime_acceptance(
    root: Path,
    *,
    observation: Mapping[str, object],
    journey_receipt: Mapping[str, object],
    observation_ref: str,
    journey_receipt_ref: str,
) -> dict[str, dict]:
    """Build one current matrix by replacing only the changed official row."""

    root = Path(root).resolve()
    evidence = _validate_product_journey(observation, journey_receipt)
    observation_path = root / observation_ref
    journey_receipt_path = root / journey_receipt_ref
    if _read(observation_path) != dict(observation):
        raise ValueError("official product observation content drifted")
    if _read(journey_receipt_path) != dict(journey_receipt):
        raise ValueError("official product journey receipt content drifted")

    contract = RuntimeEnvironmentContract.load(root)
    baseline_path = root / BASE_MATRIX
    baseline = _read(baseline_path)
    matrix = deepcopy(baseline)
    matrix["catalog_snapshot_sha256"] = contract.catalog_snapshot_sha256
    matrix["source_lock_file_sha256"] = contract.source_lock_file_sha256
    source = evidence["source"]
    matrix["observed_at"] = source["accessedAt"]

    adapter = _source_lock_row(root, OFFICIAL_SOURCE_LOCK_ID)
    execution = evidence["execution"]
    model_calls = evidence["model_calls"]
    product_receipt = {
        "contract": (
            "lca_research_kernel."
            "official_source_product_readiness_requalification/v1"
        ),
        "status": "passed",
        "observed_at": source["accessedAt"],
        "evidence_dag": {
            "invalidated_capability_ids": [OFFICIAL_CAPABILITY_ID],
            "rerun_route": "real_product_journey",
            "reused_unaffected_capability_count": 53,
        },
        "historical_runtime_matrix": {
            "path": BASE_MATRIX.as_posix(),
            "sha256": _sha256(baseline_path),
        },
        "current_runtime": {
            "catalog_snapshot_sha256": contract.catalog_snapshot_sha256,
            "source_lock_sha256": contract.source_lock_file_sha256,
            "adapter_source_id": OFFICIAL_SOURCE_LOCK_ID,
            "adapter_integrity": adapter["integrity"],
        },
        "adapter_version": adapter["version"],
        "product_readiness": {
            "observation_ref": observation_ref,
            "observation_file_sha256": _sha256(observation_path),
            "observation_sha256": canonical_sha256(observation),
            "journey_receipt_ref": journey_receipt_ref,
            "journey_receipt_file_sha256": _sha256(journey_receipt_path),
            "journey_receipt_sha256": journey_receipt["receipt_sha256"],
            "release_id": journey_receipt["release_id"],
            "research_id": observation["request"]["research_id"],
            "job_id": observation["job_id"],
        },
        "provider": "gemini",
        "models": sorted({str(call["model"]) for call in model_calls}),
        "model_calls": execution["counts"]["model"],
        "tool_calls": execution["counts"]["tool"],
        "external_reads": execution["counts"]["external_read"],
        "external_writes": execution["counts"]["external_write"],
        "source": {
            "source_id": source["sourceId"],
            "authority": source["sourceAuthority"],
            "declared_version": source["sourceVersion"],
            "requested_url": source["requestedUrl"],
            "final_url": source["finalUrl"],
            "parse_location": source["parseLocation"],
            "accessed_at": source["accessedAt"],
            "body_sha256": source["bodySha256"],
            "source_receipt_sha256": source["receiptSha256"],
        },
        "claim_binding_count": len(evidence["claim_bindings"]),
        "claim_bindings_sha256": canonical_sha256(evidence["claim_bindings"]),
        "project_record": evidence["project_record"],
        "run_trace_refs": evidence["trace_refs"],
        "retry": deepcopy(observation["retry"]),
        "case_executions_observed": 1,
        "scientific_results_observed": 1,
        "lca_calculations": 0,
    }
    product_receipt["receipt_sha256"] = canonical_sha256(product_receipt)
    product_receipt_bytes = _json_bytes(product_receipt)
    runtime_evidence = {
        "kind": "real_runtime_probe",
        "receipt_ref": CURRENT_RECEIPT.as_posix(),
        "receipt_sha256": _sha256_bytes(product_receipt_bytes),
    }

    rows = matrix.get("capabilities")
    if not isinstance(rows, list):
        raise ValueError("historical runtime matrix rows are absent")
    found = 0
    for row in rows:
        if not isinstance(row, dict) or row.get("capability_id") != OFFICIAL_CAPABILITY_ID:
            continue
        descriptor = contract.descriptors[OFFICIAL_CAPABILITY_ID]
        row.update(
            {
                "catalog_disposition": descriptor["disposition"],
                "source_lock_id": descriptor["source_lock_id"],
                "adapter_id": descriptor["adapter_id"],
                "effect": descriptor["effect"],
                "readiness_status": "executable_verified",
                "evidence": {
                    gate: deepcopy(runtime_evidence)
                    for gate in contract.executable_gates
                },
            }
        )
        found += 1
    if found != 1:
        raise ValueError("official runtime capability row is ambiguous")
    errors = contract.validate_matrix(matrix, require_closed=True)
    if errors:
        raise ValueError("current runtime matrix is not closed: " + "; ".join(errors))

    matrix_bytes = _json_bytes(matrix)
    summary = {
        "contract": "lca_research_kernel.runtime_environment_acceptance/v1",
        "observed_at": source["accessedAt"],
        "status": "accepted",
        "capability_count": len(contract.capability_ids),
        "executable_verified": sum(
            row["readiness_status"] == "executable_verified" for row in rows
        ),
        "boundary_verified_nonlocal": sum(
            row["readiness_status"] == "boundary_verified_nonlocal" for row in rows
        ),
        "ambiguous_conditional_runtime": 0,
        "matrix": {
            "path": CURRENT_MATRIX.as_posix(),
            "sha256": _sha256_bytes(matrix_bytes),
        },
        "runtime_source_lock": {
            "path": "runtime/tiangong-runtime-source-lock.json",
            "sha256": _sha256(root / "runtime/tiangong-runtime-source-lock.json"),
        },
        "project_record": CURRENT_PROJECT_RECORD.as_posix(),
        "requalification": {
            "receipt_ref": CURRENT_RECEIPT.as_posix(),
            "receipt_sha256": _sha256_bytes(product_receipt_bytes),
            "scope": "official-public-source-current-product-runtime",
            "unchanged_rows_reused": 53,
            "real_product_journeys_observed": 1,
        },
        "case_executions": 0,
        "lca_calculations": 0,
        "lcia_calculations": 0,
        "external_writes": 0,
        "credential_values_read": 0,
        "remaining_user_collaboration_items": 0,
        "next_route": "continue-product-readiness-gate",
    }
    return {
        "receipt": product_receipt,
        "matrix": matrix,
        "summary": summary,
    }


def write_current_runtime_acceptance(
    root: Path,
    *,
    source_observation: Path,
    source_journey_receipt: Path,
) -> dict:
    root = Path(root).resolve()
    source_observation = Path(source_observation).resolve()
    source_journey_receipt = Path(source_journey_receipt).resolve()
    observation = _read(source_observation)
    journey_receipt = _read(source_journey_receipt)
    _validate_product_journey(observation, journey_receipt)

    observation_path = root / PRODUCT_OBSERVATION
    journey_receipt_path = root / PRODUCT_JOURNEY_RECEIPT
    _atomic_write(observation_path, source_observation.read_bytes())
    _atomic_write(journey_receipt_path, source_journey_receipt.read_bytes())
    values = build_current_runtime_acceptance(
        root,
        observation=observation,
        journey_receipt=journey_receipt,
        observation_ref=PRODUCT_OBSERVATION.as_posix(),
        journey_receipt_ref=PRODUCT_JOURNEY_RECEIPT.as_posix(),
    )
    paths = {
        "receipt": root / CURRENT_RECEIPT,
        "matrix": root / CURRENT_MATRIX,
        "summary": root / CURRENT_SUMMARY,
    }
    result: dict[str, object] = {}
    for name, path in paths.items():
        payload = _json_bytes(values[name])
        _atomic_write(path, payload)
        result[f"{name}_ref"] = path.relative_to(root).as_posix()
        result[f"{name}_sha256"] = _sha256_bytes(payload)
    verify_matrix_receipts(root, values["matrix"])
    result["project_record"] = accept_runtime_environment(
        root,
        workspace=root / CURRENT_PROJECT_RECORD,
        summary_path=root / CURRENT_SUMMARY,
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--observation",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--journey-receipt",
        type=Path,
        required=True,
    )
    parser.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    if arguments.write:
        result = write_current_runtime_acceptance(
            arguments.root,
            source_observation=arguments.observation,
            source_journey_receipt=arguments.journey_receipt,
        )
    else:
        observation = _read(arguments.observation)
        journey_receipt = _read(arguments.journey_receipt)
        result = {
            key: "valid"
            for key in build_current_runtime_acceptance(
                arguments.root,
                observation=observation,
                journey_receipt=journey_receipt,
                observation_ref=PRODUCT_OBSERVATION.as_posix(),
                journey_receipt_ref=PRODUCT_JOURNEY_RECEIPT.as_posix(),
            )
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
