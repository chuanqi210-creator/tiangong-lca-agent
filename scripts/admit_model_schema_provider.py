#!/usr/bin/env python3
"""Run and independently admit one synthetic model schema-profile probe."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Optional


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _pointer(root: Path, path: Path) -> dict:
    resolved = path.resolve(strict=True)
    try:
        relative = resolved.relative_to(root)
    except ValueError as error:
        raise ValueError("admission artifact escapes the repository") from error
    return {"path": str(relative), "sha256": _file_sha256(resolved)}


def _minimal_secret_scan(paths: list[Path]) -> int:
    markers = (
        b"authorization:",
        b"bearer ",
        b"api_key=",
        b"api-key=",
        b"password=",
        b"secret=",
        b"sk-",
    )
    findings = 0
    for path in paths:
        lowered = path.read_bytes().lower()
        findings += sum(marker in lowered for marker in markers)
    return findings


def _synthetic_task() -> str:
    return (
        "Return one generic candidate_only JSON object solely to validate the "
        "provided schema. Set answer, claim_statements, recommendations, and "
        "capability_intent to null; use empty arrays for missing_fields, "
        "scientific_source_sha256s, runtime_provenance_sha256s, "
        "official_object_ids, and values; include one limitation stating that "
        "no scientific question was evaluated. Do not request a tool."
    )


def build_provider_admission_bundle(
    root: Path,
    *,
    provider_id: str,
    profile_id: str,
    output_root: Path,
    environ: Mapping[str, str],
    gateway_factory: Optional[Callable[..., object]] = None,
    clock: Optional[Callable[[], str]] = None,
    contract_override: Optional[Mapping[str, object]] = None,
) -> dict:
    """Execute exactly one selected synthetic route and build promotion evidence."""

    root = Path(root).resolve()
    output_root = Path(output_root).resolve()
    try:
        output_root.relative_to(root)
    except ValueError as error:
        raise ValueError("admission output must remain inside the repository") from error
    output_root.mkdir(parents=True, exist_ok=True)
    bundle_path = output_root / f"{provider_id}-provider-admission-bundle.json"
    if bundle_path.is_file():
        existing = json.loads(bundle_path.read_text(encoding="utf-8"))
        unsigned = {
            key: value
            for key, value in existing.items()
            if key != "bundle_sha256"
        }
        if (
            existing.get("status") == "passed"
            and existing.get("bundle_sha256") == _canonical_sha256(unsigned)
        ):
            return existing
        raise ValueError("existing provider admission bundle is invalid")

    sys.path.insert(0, str(root))
    from lca_research_kernel.execution_environment import (
        ProjectExecutionEnvironment,
    )
    from lca_research_kernel.model_provider_admission import (
        review_provider_probe,
        review_schema_profile_probe,
    )
    from lca_research_kernel.model_provider_catalog import (
        promote_provider_profile,
    )
    from lca_research_kernel.model_runtime import (
        build_production_model_gateway,
        load_model_runtime_contract,
    )
    from lca_research_kernel.research_orchestrator import ResearchOrchestrator
    from lca_research_kernel.run_trace import RunTrace
    from lca_research_kernel.model_schema_profiles import profile_probe_spec
    from scripts.probe_model_providers import provider_receipt

    now = clock or (
        lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    )
    gateway_builder = gateway_factory or build_production_model_gateway
    if contract_override is not None and gateway_factory is None:
        raise ValueError("contract override is restricted to injected test gateways")
    contract = (
        deepcopy(dict(contract_override))
        if contract_override is not None
        else load_model_runtime_contract(root)
    )
    matching = [
        row
        for row in contract["provider_catalog"]["providers"]
        if row["provider_id"] == provider_id
    ]
    profile = contract["provider_catalog"]["schema_profiles"].get(profile_id)
    if (
        len(matching) != 1
        or not isinstance(profile, Mapping)
        or matching[0].get("admission_state")
        not in {"adapter_ready_not_verified", "active_verified"}
        or matching[0].get("probe_eligible") is not True
        or matching[0].get("capabilities", {}).get(profile_id) is not False
    ):
        raise ValueError("provider is not eligible for schema-profile admission")
    row = matching[0]
    profile_stem = (
        "generic-source"
        if profile_id == "generic_source_candidate_v1"
        else profile_id.replace("_", "-")
    )
    if profile_id == "generic_source_candidate_v1":
        probe_task = _synthetic_task()
        probe_context = {
            "contract": (
                "lca_research_kernel.synthetic_schema_admission_context/v1"
            ),
            "synthetic": True,
            "scientific_question": False,
            "sealed_reference_available": False,
            "external_effects_allowed": [],
        }
        probe_schema = ResearchOrchestrator._generic_source_candidate_schema()
    else:
        probe = profile_probe_spec(profile_id)
        probe_task = probe["task"]
        probe_context = probe["context"]
        probe_schema = probe["output_schema"]
    gateway = gateway_builder(
        root,
        environ,
        execution_mode="verification_probe",
        provider_ids={provider_id},
        clock=now,
    )
    execution_policy = json.loads(
        (
            root
            / "lca_research_kernel/contracts/project-execution-policy.json"
        ).read_text(encoding="utf-8")
    )
    with tempfile.TemporaryDirectory(
        prefix=f"d18-{provider_id}-admission-"
    ) as sandbox:
        environment = ProjectExecutionEnvironment.create(
            Path(sandbox),
            f"d18-{provider_id}-schema-admission",
            execution_policy,
        )
        gateway = gateway.with_execution_environment(environment)
        trace_path = output_root / f"{provider_id}-{profile_stem}-run-trace.jsonl"
        trace = RunTrace(
            trace_path, run_id=f"d18-runtime-probe-{provider_id}"
        )
        request = {
            "contract": "lca_research_kernel.model_request/v1",
            "request_id": f"d18-{provider_id}-{profile_id}-admission",
            "provider": row["route_id"],
            "task": probe_task,
            "context": deepcopy(probe_context),
            "output_schema": deepcopy(probe_schema),
            "tools": [],
            "required_capabilities": ["structured_output"],
            "runtime_timeout_seconds": 60,
        }
        candidate = gateway.reason(request, trace=trace)

    events = trace.events()
    completed = [
        event
        for event in events
        if event["event_type"] == "research.run.model.completed"
    ]
    if (
        len(completed) != 1
        or candidate.get("provider") != provider_id
        or candidate.get("adapter_id") != row["route_id"]
        or candidate.get("tool_requests") != []
    ):
        raise ValueError("provider probe did not yield one bounded candidate")

    provider_probe = provider_receipt(
        candidate,
        policy_sha256=contract["policy_sha256"],
        source_lock_sha256=contract["source_lock_sha256"],
        provider_catalog_sha256=contract["provider_catalog_sha256"],
    )
    provider_probe_path = (
        output_root / f"{provider_id}-provider-probe-receipt.json"
    )
    _atomic_json(provider_probe_path, provider_probe)
    provider_review = review_provider_probe(
        root,
        provider_id=provider_id,
        receipt=provider_probe,
        trace_path=trace_path,
        contract_override=contract_override,
    )
    if provider_review["status"] != "passed":
        raise ValueError(
            "provider identity admission review failed: "
            + ",".join(provider_review["findings"])
        )
    provider_review_path = (
        output_root / f"{provider_id}-provider-admission-review.json"
    )
    _atomic_json(provider_review_path, provider_review)

    trace_ref = _pointer(root, trace_path)
    raw_acceptance = {
        "contract": (
            "lca_research_kernel.synthetic_model_profile_raw_acceptance/v1"
        ),
        "status": "accepted_bounded_blind_baseline",
        "provider_id": provider_id,
        "profile_id": profile_id,
        "model_observation": {"completed_call_count": 1},
        "run_trace_observation": {
            "event_count": len(events),
            "external_write_count": 0,
            "sealed_reference_read_count": 0,
        },
        "scientific_result_count": 0,
        "tool_request_count": 0,
        "secret_scan_findings": 0,
        "trace_sha256": trace.verify()["trace_sha256"],
    }
    raw_acceptance["acceptance_sha256"] = _canonical_sha256(raw_acceptance)
    raw_acceptance_path = (
        output_root / f"{provider_id}-{profile_stem}-raw-acceptance.json"
    )
    _atomic_json(raw_acceptance_path, raw_acceptance)
    profile_receipt = {
        "contract": (
            "lca_research_kernel.model_schema_profile_probe_receipt/v1"
        ),
        "status": "accepted",
        "profile_id": profile_id,
        "provider_id": provider_id,
        "adapter_id": row["route_id"],
        "actual_model": candidate["model"],
        "schema_sha256": profile["schema_sha256"],
        "raw_baseline_acceptance": _pointer(root, raw_acceptance_path),
        "observed_successful_call_count": 1,
        "structured_output_valid": True,
        "tool_requests": 0,
        "external_writes": 0,
        "scientific_result_created": False,
        "sealed_reference_reads": 0,
        "validated_event_refs": [
            {
                "path": trace_ref["path"],
                "file_sha256": trace_ref["sha256"],
                "event_id": completed[0]["event_id"],
                "event_sha256": completed[0]["event_sha256"],
            }
        ],
        "claim_ceiling": (
            "This admits one synthetic schema route; it does not create a "
            "scientific result or prove durable quota."
        ),
    }
    profile_receipt["receipt_sha256"] = _canonical_sha256(profile_receipt)
    profile_receipt_path = (
        output_root / f"{provider_id}-{profile_stem}-profile-receipt.json"
    )
    _atomic_json(profile_receipt_path, profile_receipt)
    profile_review = review_schema_profile_probe(
        root,
        provider_id=provider_id,
        profile_id=profile_id,
        receipt=profile_receipt,
        trace_path=trace_path,
        raw_acceptance_path=raw_acceptance_path,
        contract_override=contract_override,
    )
    if profile_review["status"] != "passed":
        raise ValueError("schema-profile admission review failed")
    profile_review_path = (
        output_root / f"{provider_id}-{profile_stem}-profile-review.json"
    )
    _atomic_json(profile_review_path, profile_review)
    promoted = promote_provider_profile(
        contract["provider_catalog"],
        provider_id=provider_id,
        profile_id=profile_id,
        probe_pointer=_pointer(root, profile_receipt_path),
        review_pointer=_pointer(root, profile_review_path),
        provider_receipt=provider_probe,
        provider_review=provider_review,
        profile_receipt=profile_receipt,
        profile_review=profile_review,
    )
    promoted_path = (
        output_root / "model-provider-catalog-promoted-candidate.json"
    )
    _atomic_json(promoted_path, promoted)
    artifact_paths = [
        trace_path,
        raw_acceptance_path,
        provider_probe_path,
        provider_review_path,
        profile_receipt_path,
        profile_review_path,
        promoted_path,
    ]
    findings = _minimal_secret_scan(artifact_paths)
    if findings:
        raise ValueError("provider admission artifacts failed secret scan")
    bundle = {
        "contract": (
            "lca_research_kernel.model_schema_provider_admission_bundle/v1"
        ),
        "status": "passed",
        "provider_id": provider_id,
        "profile_id": profile_id,
        "actual_model": candidate["model"],
        "usage": deepcopy(candidate["usage"]),
        "model_call_count": 1,
        "tool_requests": 0,
        "external_writes": 0,
        "scientific_results": 0,
        "sealed_reference_reads": 0,
        "secret_scan_findings": findings,
        "artifacts": {
            path.name: _pointer(root, path) for path in artifact_paths
        },
    }
    bundle["bundle_sha256"] = _canonical_sha256(bundle)
    _atomic_json(bundle_path, bundle)
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--provider", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--credential-env-file", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    output = args.output_root
    if not output.is_absolute():
        output = root / output
    from scripts.probe_model_providers import (
        load_credential_environment,
    )

    allowed_environment = {}
    if args.credential_env_file is not None:
        allowed_environment.update(
            load_credential_environment(args.credential_env_file)
        )
    bundle = build_provider_admission_bundle(
        root,
        provider_id=args.provider,
        profile_id=args.profile,
        output_root=output,
        environ=allowed_environment,
    )
    print(
        json.dumps(
            {
                "status": bundle["status"],
                "provider": bundle["provider_id"],
                "actual_model": bundle["actual_model"],
                "total_tokens": bundle["usage"]["total_tokens"],
                "tool_requests": bundle["tool_requests"],
                "external_writes": bundle["external_writes"],
                "scientific_results": bundle["scientific_results"],
                "sealed_reference_reads": bundle[
                    "sealed_reference_reads"
                ],
                "bundle_sha256": bundle["bundle_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
