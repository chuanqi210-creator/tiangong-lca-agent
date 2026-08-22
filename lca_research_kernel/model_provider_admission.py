"""Independent, secret-free verification of real model-provider probes."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Mapping

from .model_provider_catalog import canonical_sha256
from .model_runtime import load_model_runtime_contract
from .model_provider_adapters import validate_structured_output
from .run_trace import RunTrace


def _self_hash(value: Mapping[str, object], field: str) -> bool:
    expected = value.get(field)
    payload = {key: deepcopy(item) for key, item in value.items() if key != field}
    return isinstance(expected, str) and expected == canonical_sha256(payload)


def review_provider_probe(
    root: Path,
    *,
    provider_id: str,
    receipt: Mapping[str, object],
    trace_path: Path,
    contract_override: Mapping[str, object] | None = None,
) -> dict:
    """Review one provider receipt against its frozen catalog and Run Trace."""

    root = Path(root).resolve()
    contract = (
        deepcopy(dict(contract_override))
        if contract_override is not None
        else load_model_runtime_contract(root)
    )
    catalog = contract["provider_catalog"]
    matching = [
        row for row in catalog["providers"] if row["provider_id"] == provider_id
    ]
    if len(matching) != 1:
        raise ValueError("provider slot is absent or duplicated")
    row = matching[0]
    allowed_probe_catalog_sha256s = {
        contract["provider_catalog_sha256"],
    }
    if row.get("admission_state") == "active_verified":
        evidence = row.get("active_verified_evidence")
        if (
            not isinstance(evidence, list)
            or len(evidence) != 1
            or not isinstance(evidence[0], Mapping)
        ):
            raise ValueError("active provider evidence is invalid")
        allowed_probe_catalog_sha256s.add(
            evidence[0].get("admitted_from_catalog_sha256")
        )
    observed_probe_catalog_sha256 = receipt.get("provider_catalog_sha256")
    if (
        receipt.get("contract")
        != "lca_research_kernel.model_provider_probe_receipt/v1"
        or receipt.get("provider") != provider_id
        or observed_probe_catalog_sha256
        not in allowed_probe_catalog_sha256s
        or not _self_hash(receipt, "receipt_sha256")
    ):
        raise ValueError("provider receipt identity is invalid")
    trace = RunTrace(
        Path(trace_path),
        run_id=f"d18-runtime-probe-{provider_id}",
    )
    verification = trace.verify()
    events = trace.events()
    completed = [
        event
        for event in events
        if event["event_type"] == "research.run.model.completed"
    ]
    if len(completed) != 1:
        raise ValueError("provider trace must contain one completed candidate")
    candidate = completed[0]["payload"].get("candidate")
    if not isinstance(candidate, Mapping):
        raise ValueError("provider trace candidate is absent")
    runtime_identity = candidate.get("runtime_identity")
    expected_runtime_identity = {
        "source_lock_sha256": contract["source_lock_sha256"],
        "policy_sha256": contract["policy_sha256"],
        "provider_catalog_sha256": observed_probe_catalog_sha256,
        "execution_mode": "verification_probe",
    }
    checks = {
        "trace_hash_chain_valid": verification["status"] == "passed",
        "provider_identity_matches": (
            candidate.get("provider") == provider_id
            and receipt.get("provider") == provider_id
        ),
        "provider_reported_model_matches": (
            candidate.get("model") == receipt.get("actual_model")
            and isinstance(candidate.get("model"), str)
            and bool(candidate["model"])
        ),
        "provider_response_identity_matches": (
            candidate.get("provider_response_id")
            == receipt.get("provider_response_id")
        ),
        "request_identity_matches": (
            candidate.get("request_sha256") == receipt.get("request_sha256")
        ),
        "output_identity_matches": (
            canonical_sha256(candidate.get("output"))
            == receipt.get("output_sha256")
        ),
        "runtime_identity_matches": runtime_identity == expected_runtime_identity,
        "network_permission_matches": (
            candidate.get("network_authorization", {}).get("host")
            == row["network_host"]
            and isinstance(candidate.get("execution_policy_sha256"), str)
        ),
        "candidate_only": candidate.get("status") == "candidate_only",
        "no_tool_or_external_effect": (
            candidate.get("tool_requests") == []
            and receipt.get("tool_requests") == 0
            and receipt.get("external_writes") == 0
            and receipt.get("scientific_result_created") is False
        ),
        "exact_sdk_source_bound": (
            contract["sdk_sources"][row["sdk_distribution"]]["version"]
            == row["sdk_version"]
        ),
    }
    p1 = [] if all(checks.values()) else [
        key for key, passed in checks.items() if not passed
    ]
    review = {
        "contract": "lca_research_kernel.model_provider_admission_review/v1",
        "status": "passed" if not p1 else "failed",
        "provider": provider_id,
        "probe_receipt_sha256": receipt["receipt_sha256"],
        "provider_catalog_sha256": observed_probe_catalog_sha256,
        "run_trace": verification,
        "checks": checks,
        "p1": len(p1),
        "p2": 0,
        "findings": p1,
        "external_effects": 0,
        "review_context": "fresh_process_registered_receipt_and_run_trace",
    }
    review["review_sha256"] = canonical_sha256(review)
    return review


def review_schema_profile_probe(
    root: Path,
    *,
    provider_id: str,
    profile_id: str,
    receipt: Mapping[str, object],
    trace_path: Path,
    raw_acceptance_path: Path,
    contract_override: Mapping[str, object] | None = None,
) -> dict:
    """Independently bind one immutable provider trace to one schema profile."""

    root = Path(root).resolve()
    trace_path = Path(trace_path).resolve()
    raw_acceptance_path = Path(raw_acceptance_path).resolve()
    contract = (
        deepcopy(dict(contract_override))
        if contract_override is not None
        else load_model_runtime_contract(root)
    )
    profiles = contract["provider_catalog"]["schema_profiles"]
    profile = profiles.get(profile_id)
    matching = [
        row
        for row in contract["provider_catalog"]["providers"]
        if row["provider_id"] == provider_id
    ]
    if len(matching) != 1 or not isinstance(profile, Mapping):
        raise ValueError("provider or schema profile is absent")
    row = matching[0]
    raw_acceptance = json.loads(
        raw_acceptance_path.read_text(encoding="utf-8")
    )
    trace = RunTrace(
        trace_path,
        run_id=f"d18-runtime-probe-{provider_id}",
    )
    verification = trace.verify()
    completed = [
        event
        for event in trace.events()
        if event["event_type"] == "research.run.model.completed"
    ]
    candidate = (
        completed[0]["payload"].get("candidate")
        if len(completed) == 1
        else None
    )
    refs = receipt.get("validated_event_refs")
    event_ref = (
        refs[0]
        if isinstance(refs, list)
        and len(refs) == 1
        and isinstance(refs[0], Mapping)
        else {}
    )
    raw_pointer = receipt.get("raw_baseline_acceptance")
    raw_file_sha = hashlib.sha256(raw_acceptance_path.read_bytes()).hexdigest()
    trace_file_sha = hashlib.sha256(trace_path.read_bytes()).hexdigest()
    schema_valid = False
    if isinstance(candidate, Mapping):
        try:
            if profile_id == "generic_source_candidate_v1":
                from .model_schema_profiles import generic_source_candidate_schema

                schema = generic_source_candidate_schema()
            else:
                from .model_schema_profiles import profile_probe_spec

                schema = profile_probe_spec(profile_id)["output_schema"]
            validate_structured_output(candidate.get("output"), schema)
        except Exception:
            schema_valid = False
        else:
            schema_valid = True
    checks = {
        "probe_receipt_hash_valid": _self_hash(receipt, "receipt_sha256"),
        "provider_adapter_model_match": (
            isinstance(candidate, Mapping)
            and candidate.get("provider") == provider_id
            and candidate.get("adapter_id") == row["route_id"]
            and candidate.get("model") == receipt.get("actual_model")
            and isinstance(candidate.get("model"), str)
            and bool(candidate["model"])
        ),
        "current_schema_hash_matches": (
            receipt.get("profile_id") == profile_id
            and receipt.get("provider_id") == provider_id
            and receipt.get("schema_sha256") == profile.get("schema_sha256")
        ),
        "structured_output_valid": (
            receipt.get("structured_output_valid") is True
            and schema_valid
        ),
        "candidate_only": (
            isinstance(candidate, Mapping)
            and candidate.get("status") == "candidate_only"
        ),
        "tool_requests_zero": (
            isinstance(candidate, Mapping)
            and candidate.get("tool_requests") == []
            and receipt.get("tool_requests") == 0
        ),
        "external_writes_zero": (
            receipt.get("external_writes") == 0
            and raw_acceptance.get("run_trace_observation", {}).get(
                "external_write_count"
            )
            == 0
        ),
        "scientific_result_zero": (
            receipt.get("scientific_result_created") is False
            and raw_acceptance.get("scientific_result_count") == 0
        ),
        "sealed_reference_reads_zero": (
            receipt.get("sealed_reference_reads") == 0
            and raw_acceptance.get("run_trace_observation", {}).get(
                "sealed_reference_read_count"
            )
            == 0
        ),
        "secret_scan_zero": raw_acceptance.get("secret_scan_findings") == 0,
        "raw_acceptance_hash_valid": (
            isinstance(raw_pointer, Mapping)
            and raw_pointer.get("sha256") == raw_file_sha
        ),
        "raw_acceptance_status_bounded": (
            raw_acceptance.get("status")
            == "accepted_bounded_blind_baseline"
        ),
        "raw_completed_call_count_matches": (
            raw_acceptance.get("model_observation", {}).get(
                "completed_call_count"
            )
            == 1
            and receipt.get("observed_successful_call_count") == 1
        ),
        "run_trace_chain_valid": (
            verification.get("status") == "passed"
            and verification.get("event_count") == 2
        ),
        "run_trace_file_hash_valid": event_ref.get("file_sha256")
        == trace_file_sha,
        "run_trace_event_hash_valid": (
            len(completed) == 1
            and event_ref.get("event_id") == completed[0].get("event_id")
            and event_ref.get("event_sha256")
            == completed[0].get("event_sha256")
        ),
    }
    failed = [key for key, passed in checks.items() if not passed]
    review = {
        "contract": (
            "lca_research_kernel.model_schema_profile_admission_review/v1"
        ),
        "status": "passed" if not failed else "failed",
        "profile_id": profile_id,
        "provider_id": provider_id,
        "schema_sha256": profile["schema_sha256"],
        "probe_receipt_sha256": receipt.get("receipt_sha256"),
        "checks": checks,
        "p1": len(failed),
        "p2": 0,
        "findings": failed,
        "external_effects": 0,
        "review_context": (
            "clean_process_deterministic_review_of_one_synthetic_provider_trace"
        ),
    }
    review["review_sha256"] = canonical_sha256(review)
    return review
