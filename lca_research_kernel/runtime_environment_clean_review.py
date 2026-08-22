"""Isolated fact-ledger review for the D-18 runtime environment closure."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Mapping

from .project_record import ProjectRecord
from .runtime_environment import RuntimeEnvironmentContract
from .runtime_environment_health import verify_matrix_receipts


SECRET_VALUE = re.compile(
    r"(?i)(authorization\s*:\s*bearer\s+\S+|"
    r"(?:api[_-]?key|password|secret|token)\s*=\s*\S+)"
)


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


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


def validate_current_product_probe_receipt(
    root: Path, receipt: Mapping[str, object]
) -> list[str]:
    """Re-evaluate the source-read receipt against raw Product Gate evidence."""

    from lca_research_service.product_readiness_gate import (
        evaluate_official_journey,
    )

    root = Path(root).resolve()
    errors: list[str] = []
    if receipt.get("contract") != (
        "lca_research_kernel."
        "official_source_product_readiness_requalification/v1"
    ):
        errors.append("runtime_product_probe_contract_invalid")
    if receipt.get("status") != "passed":
        errors.append("runtime_product_probe_not_passed")
    unsigned = {
        key: deepcopy(value)
        for key, value in receipt.items()
        if key != "receipt_sha256"
    }
    if receipt.get("receipt_sha256") != _canonical_sha256(unsigned):
        errors.append("runtime_product_probe_receipt_hash_invalid")
    if (
        receipt.get("external_reads") != 1
        or receipt.get("model_calls") != 2
        or receipt.get("tool_calls") != 1
        or receipt.get("external_writes") != 0
    ):
        if receipt.get("external_reads") != 1:
            errors.append("runtime_product_probe_external_read_count_invalid")
        if receipt.get("model_calls") != 2:
            errors.append("runtime_product_probe_model_count_invalid")
        if receipt.get("tool_calls") != 1:
            errors.append("runtime_product_probe_tool_count_invalid")
        if receipt.get("external_writes") != 0:
            errors.append("runtime_product_probe_external_write_invalid")
    if receipt.get("provider") != "gemini":
        errors.append("runtime_product_probe_provider_invalid")

    contract = RuntimeEnvironmentContract.load(root)
    current = receipt.get("current_runtime")
    if (
        not isinstance(current, Mapping)
        or current.get("catalog_snapshot_sha256")
        != contract.catalog_snapshot_sha256
        or current.get("source_lock_sha256")
        != contract.source_lock_file_sha256
    ):
        errors.append("runtime_product_probe_contract_binding_invalid")
    source_lock = _read(
        root / "lca_research_kernel/contracts/tiangong-source-lock.json"
    )
    rows = source_lock.get("sources")
    adapters = (
        [
            row
            for row in rows
            if isinstance(row, Mapping)
            and row.get("source_id") == "lca.kernel.official-public-source"
        ]
        if isinstance(rows, list)
        else []
    )
    if (
        len(adapters) != 1
        or receipt.get("adapter_version") != adapters[0].get("version")
        or not isinstance(current, Mapping)
        or current.get("adapter_integrity") != adapters[0].get("integrity")
    ):
        errors.append("runtime_product_probe_adapter_binding_invalid")

    product = receipt.get("product_readiness")
    if not isinstance(product, Mapping):
        return [*errors, "runtime_product_probe_evidence_refs_absent"]
    try:
        observation_path = root / str(product["observation_ref"])
        journey_receipt_path = root / str(product["journey_receipt_ref"])
        observation = _read(observation_path)
        journey_receipt = _read(journey_receipt_path)
    except (KeyError, OSError, ValueError, json.JSONDecodeError):
        return [*errors, "runtime_product_probe_evidence_unavailable"]
    if (
        hashlib.sha256(observation_path.read_bytes()).hexdigest()
        != product.get("observation_file_sha256")
        or _canonical_sha256(observation)
        != product.get("observation_sha256")
        or hashlib.sha256(journey_receipt_path.read_bytes()).hexdigest()
        != product.get("journey_receipt_file_sha256")
        or journey_receipt.get("receipt_sha256")
        != product.get("journey_receipt_sha256")
    ):
        errors.append("runtime_product_probe_evidence_hash_invalid")
    evaluated = evaluate_official_journey(observation)
    if evaluated != journey_receipt or evaluated.get("status") != "passed":
        errors.append("runtime_product_probe_journey_invalid")
        return errors
    request = observation.get("request")
    if product.get("release_id") != journey_receipt.get("release_id"):
        errors.append("runtime_product_probe_release_identity_invalid")
    if (
        not isinstance(request, Mapping)
        or product.get("research_id") != request.get("research_id")
    ):
        errors.append("runtime_product_probe_research_identity_invalid")
    if product.get("job_id") != observation.get("job_id"):
        errors.append("runtime_product_probe_job_identity_invalid")

    result_view = observation.get("result_view")
    execution = (
        result_view.get("executionReceipt")
        if isinstance(result_view, Mapping)
        else None
    )
    result = result_view.get("result") if isinstance(result_view, Mapping) else None
    sources = result.get("officialSources") if isinstance(result, Mapping) else None
    claims = (
        result.get("officialClaimBindings") if isinstance(result, Mapping) else None
    )
    if (
        not isinstance(execution, Mapping)
        or not isinstance(sources, list)
        or len(sources) != 1
        or not isinstance(sources[0], Mapping)
        or not isinstance(claims, list)
        or not claims
    ):
        errors.append("runtime_product_probe_projection_invalid")
        return errors
    source = sources[0]
    expected_source = {
        "source_id": source.get("sourceId"),
        "authority": source.get("sourceAuthority"),
        "declared_version": source.get("sourceVersion"),
        "requested_url": source.get("requestedUrl"),
        "final_url": source.get("finalUrl"),
        "parse_location": source.get("parseLocation"),
        "accessed_at": source.get("accessedAt"),
        "body_sha256": source.get("bodySha256"),
        "source_receipt_sha256": source.get("receiptSha256"),
    }
    if receipt.get("source") != expected_source:
        errors.append("runtime_product_probe_source_binding_invalid")
    if (
        receipt.get("claim_binding_count") != len(claims)
        or receipt.get("claim_bindings_sha256") != _canonical_sha256(claims)
    ):
        errors.append("runtime_product_probe_claim_binding_invalid")
    if receipt.get("project_record") != execution.get("project_record"):
        errors.append("runtime_product_probe_project_record_binding_invalid")
    if receipt.get("run_trace_refs") != execution.get("trace_refs"):
        errors.append("runtime_product_probe_trace_binding_invalid")
    if receipt.get("retry") != observation.get("retry"):
        errors.append("runtime_product_probe_retry_binding_invalid")
    return errors


def review_runtime_environment(root: Path) -> dict:
    """Review only the active contract, accepted outputs and executable seams."""

    root = Path(root).resolve()
    p1: list[str] = []
    p2: list[str] = []
    try:
        contract = RuntimeEnvironmentContract.load(root)
    except ValueError as error:
        return {
            "contract": "lca_research_kernel.runtime_environment_clean_review/v1",
            "decision": "rejected",
            "p1_count": 1,
            "p2_count": 0,
            "findings": [{"priority": "P1", "code": str(error)}],
            "capability_count": 0,
            "case_executions": 0,
            "calculations": 0,
        }
    summary_path = root / (
        "outputs/runtime-environment/"
        "runtime-health-summary-source-binding-v10.json"
    )
    matrix_path = root / (
        "outputs/runtime-environment/"
        "runtime-readiness-matrix-source-binding-v10.json"
    )
    try:
        summary = _read(summary_path)
        matrix = _read(matrix_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        p1.append(f"runtime_acceptance_artifact_invalid:{type(error).__name__}")
        summary = {}
        matrix = {}
    if summary.get("status") != "accepted":
        p1.append("runtime_summary_not_accepted")
    if summary.get("capability_count") != len(contract.capability_ids):
        p1.append("runtime_summary_coverage_drifted")
    if summary.get("ambiguous_conditional_runtime") != 0:
        p1.append("conditional_runtime_remains_ambiguous")
    for field in (
        "case_executions",
        "lca_calculations",
        "lcia_calculations",
        "external_writes",
        "credential_values_read",
        "remaining_user_collaboration_items",
    ):
        if summary.get(field) != 0:
            p1.append(f"forbidden_or_unclosed_effect:{field}")
    matrix_ref = summary.get("matrix")
    if not isinstance(matrix_ref, dict) or matrix_ref.get(
        "sha256"
    ) != hashlib.sha256(matrix_path.read_bytes()).hexdigest():
        p1.append("runtime_matrix_reference_drifted")
    errors = contract.validate_matrix(matrix, require_closed=True)
    p1.extend(f"runtime_matrix:{error}" for error in errors)
    try:
        verify_matrix_receipts(root, matrix)
    except ValueError as error:
        p1.append(f"runtime_receipt:{error}")
    requalification = summary.get("requalification")
    if not isinstance(requalification, Mapping):
        p1.append("runtime_product_requalification_absent")
    else:
        receipt_ref = requalification.get("receipt_ref")
        receipt_sha256 = requalification.get("receipt_sha256")
        try:
            receipt_path = root / str(receipt_ref)
            if hashlib.sha256(receipt_path.read_bytes()).hexdigest() != receipt_sha256:
                p1.append("runtime_product_requalification_reference_drifted")
            product_receipt = _read(receipt_path)
            p1.extend(
                validate_current_product_probe_receipt(root, product_receipt)
            )
        except (OSError, ValueError, json.JSONDecodeError):
            p1.append("runtime_product_requalification_invalid")

    catalog = contract.descriptors
    context = catalog.get("tiangong.specification.context-pack", {})
    if context.get("entrypoint") != [
        "tiangong-lca",
        "dataset",
        "context-pack",
    ]:
        p1.append("context_pack_entrypoint_not_adopted")
    if len(catalog) != len(contract.capability_ids):
        p1.append("catalog_coverage_drifted")
    if sum(
        row.get("disposition") == "human_action_required"
        for row in catalog.values()
    ) != 15:
        p1.append("responsibility_boundary_coverage_drifted")

    for relative in (
        "scripts/bootstrap_tiangong_runtime.py",
        "scripts/check_tiangong_runtime.py",
        "runtime/tiangong-node/probe-mcp.mjs",
        "runtime/tiangong-node/probe-openlca.mjs",
        "runtime/tiangong-node/probe-mcp-openlca.mjs",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        if "shell=True" in source or "shell: true" in source:
            p1.append(f"shell_execution_present:{relative}")
        if SECRET_VALUE.search(source):
            p1.append(f"secret_value_present:{relative}")
    for path in (root / "outputs/runtime-environment").rglob("*"):
        if path.is_file():
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if SECRET_VALUE.search(content):
                p1.append(
                    f"runtime_output_contains_secret_value:{path.relative_to(root)}"
                )

    record_pointer = summary.get(
        "project_record", "outputs/runtime-environment/project-record"
    )
    if not isinstance(record_pointer, str) or not record_pointer:
        p1.append("runtime_project_record_pointer_invalid")
        record_pointer = "outputs/runtime-environment/project-record"
    try:
        record = ProjectRecord(root / record_pointer)
        replay = record.rebuild_and_verify()
        view = record.read_current_project_view()
        if replay["status"] != "passed" or len(view["artifacts"]) != 4:
            p2.append("runtime_project_record_incomplete")
        if (
            view.get("capability_catalog", {}).get("catalog_snapshot_sha256")
            != contract.catalog_snapshot_sha256
        ):
            p1.append("runtime_project_record_catalog_drifted")
    except (OSError, ValueError) as error:
        p1.append(f"runtime_project_record_invalid:{type(error).__name__}")

    findings = [
        *({"priority": "P1", "code": code} for code in sorted(set(p1))),
        *({"priority": "P2", "code": code} for code in sorted(set(p2))),
    ]
    return {
        "contract": "lca_research_kernel.runtime_environment_clean_review/v1",
        "decision": "accepted" if not findings else "rejected",
        "p1_count": len(set(p1)),
        "p2_count": len(set(p2)),
        "findings": findings,
        "capability_count": len(contract.capability_ids),
        "case_executions": summary.get("case_executions", 0),
        "calculations": (
            summary.get("lca_calculations", 0)
            + summary.get("lcia_calculations", 0)
        ),
        "review_basis": [
            "runtime-environment-contract",
            "closed-readiness-matrix",
            "content-addressed-real-probe-receipts",
            "runtime-project-record-replay",
            "zero-effect-source-scan",
        ],
    }
