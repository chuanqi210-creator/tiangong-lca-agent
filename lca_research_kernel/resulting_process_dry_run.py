"""Compile and verify native route-local resulting-Process dry-runs."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple, Union

try:
    from .lifecyclemodel_assembly_plan import TIANGONG_PACKAGE
    from .lifecyclemodel_shell import SHELL_CONTRACT
except ImportError:  # Direct activity-script execution from the project workspace.
    from lifecyclemodel_assembly_plan import TIANGONG_PACKAGE  # type: ignore
    from lifecyclemodel_shell import SHELL_CONTRACT  # type: ignore


JsonObject = Dict[str, object]
RESULTING_PROCESS_CONTRACT = (
    "lca_research_kernel.native_route_resulting_process_dry_run/v1"
)
COMPILED_STATUS = "native_route_resulting_process_projection_inputs_compiled"
EXPECTED_ROUTES = {"hydrothermal", "solid-state"}


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash(value: object) -> str:
    return _hash_bytes(_json_bytes(value))


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _list(value: object, label: str) -> List[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _binding(value: object, label: str) -> JsonObject:
    row = _object(value, label)
    path = _text(row.get("path"), f"{label}.path")
    digest = _text(row.get("sha256"), f"{label}.sha256")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError(f"{label}.sha256 must be a lowercase sha256")
    return {"path": path, "sha256": digest}


def _process_root(payload: JsonObject, label: str) -> JsonObject:
    root = payload.get("processDataSet")
    if isinstance(root, dict):
        return root
    for key in ("json_ordered", "json"):
        nested = payload.get(key)
        if isinstance(nested, dict) and isinstance(nested.get("processDataSet"), dict):
            return nested["processDataSet"]
    raise ValueError(f"{label} does not contain processDataSet")


def _process_identity(payload: JsonObject, label: str) -> Tuple[str, str]:
    root = _process_root(payload, label)
    information = _object(root.get("processInformation"), f"{label} information")
    dataset = _object(information.get("dataSetInformation"), f"{label} dataset info")
    administrative = _object(
        root.get("administrativeInformation"), f"{label} administration"
    )
    publication = _object(
        administrative.get("publicationAndOwnership"), f"{label} publication"
    )
    return (
        _text(dataset.get("common:UUID"), f"{label} UUID"),
        _text(publication.get("common:dataSetVersion"), f"{label} version"),
    )


def _exchanges(payload: JsonObject, label: str) -> List[JsonObject]:
    root = _process_root(payload, label)
    wrapper = _object(root.get("exchanges"), f"{label} exchanges")
    raw = wrapper.get("exchange")
    values = raw if isinstance(raw, list) else [raw]
    rows = [row for row in values if isinstance(row, dict)]
    if not rows:
        raise ValueError(f"{label} has no exchanges")
    return rows


def _decimal(value: object, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{label} must be numeric") from error
    if not parsed.is_finite():
        raise ValueError(f"{label} must be finite")
    return parsed.normalize()


def _exchange_semantics(
    payload: JsonObject, label: str
) -> List[Tuple[str, str, Decimal, Decimal]]:
    result = []
    for index, exchange in enumerate(_exchanges(payload, label)):
        flow = _object(
            exchange.get("referenceToFlowDataSet"),
            f"{label} exchange {index} flow",
        )
        mean = _decimal(exchange.get("meanAmount"), f"{label} exchange {index} mean")
        resulting_value = exchange.get("resultingAmount", exchange.get("meanAmount"))
        result.append(
            (
                _text(flow.get("@refObjectId"), f"{label} exchange {index} flow UUID"),
                _text(
                    exchange.get("exchangeDirection"),
                    f"{label} exchange {index} direction",
                ),
                mean,
                _decimal(resulting_value, f"{label} exchange {index} resulting"),
            )
        )
    return sorted(result)


def _reference_semantics(
    payload: JsonObject, label: str
) -> Tuple[str, str, Decimal, Decimal, str]:
    root = _process_root(payload, label)
    information = _object(root.get("processInformation"), f"{label} information")
    quantitative = _object(
        information.get("quantitativeReference"), f"{label} quantitative reference"
    )
    internal_id = str(quantitative.get("referenceToReferenceFlow", ""))
    reference = next(
        (
            exchange
            for exchange in _exchanges(payload, label)
            if str(exchange.get("@dataSetInternalID", "")) == internal_id
        ),
        None,
    )
    if not isinstance(reference, dict):
        raise ValueError(f"{label} reference exchange is missing")
    flow = _object(reference.get("referenceToFlowDataSet"), f"{label} reference flow")
    mean = _decimal(reference.get("meanAmount"), f"{label} reference mean")
    resulting = _decimal(
        reference.get("resultingAmount", reference.get("meanAmount")),
        f"{label} reference resulting",
    )
    return (
        _text(flow.get("@refObjectId"), f"{label} reference flow UUID"),
        _text(flow.get("@version"), f"{label} reference flow version"),
        mean,
        resulting,
        _text(reference.get("exchangeDirection"), f"{label} reference direction"),
    )


def _shell_facts(payload: JsonObject, label: str) -> JsonObject:
    root = _object(payload.get("lifeCycleModelDataSet"), f"{label} root")
    information = _object(
        root.get("lifeCycleModelInformation"), f"{label} information"
    )
    data = _object(information.get("dataSetInformation"), f"{label} dataset info")
    result_ref = _object(
        data.get("referenceToResultingProcess"), f"{label} resulting reference"
    )
    quantitative = _object(
        information.get("quantitativeReference"), f"{label} quantitative reference"
    )
    technology = _object(information.get("technology"), f"{label} technology")
    processes = _object(technology.get("processes"), f"{label} processes")
    instance = _object(processes.get("processInstance"), f"{label} process instance")
    process_ref = _object(
        instance.get("referenceToProcess"), f"{label} process reference"
    )
    administrative = _object(
        root.get("administrativeInformation"), f"{label} administration"
    )
    publication = _object(
        administrative.get("publicationAndOwnership"), f"{label} publication"
    )
    return {
        "model_id": _text(data.get("common:UUID"), f"{label} UUID"),
        "model_version": _text(
            publication.get("common:dataSetVersion"), f"{label} version"
        ),
        "resulting_process_id": _text(
            result_ref.get("@refObjectId"), f"{label} resulting UUID"
        ),
        "resulting_process_version": _text(
            result_ref.get("@version"), f"{label} resulting version"
        ),
        "reference_process_instance_id": str(
            quantitative.get("referenceToReferenceProcess", "")
        ),
        "instance_id": str(instance.get("@dataSetInternalID", "")),
        "multiplication_factor": _decimal(
            instance.get("@multiplicationFactor"), f"{label} multiplication factor"
        ),
        "process_id": _text(
            process_ref.get("@refObjectId"), f"{label} Process UUID"
        ),
        "process_version": _text(
            process_ref.get("@version"), f"{label} Process version"
        ),
    }


def _validated_shell_report(report: JsonObject) -> Dict[str, JsonObject]:
    if (
        report.get("contract") != SHELL_CONTRACT
        or report.get("status") != "native_route_lifecyclemodel_shells_materialized"
        or report.get("tool_identity") != TIANGONG_PACKAGE
        or report.get("route_count") != 2
        or report.get("native_valid_count") != 2
        or report.get("native_invalid_count") != 0
        or report.get("native_graph_model_count") != 2
        or report.get("native_graph_node_count") != 2
        or report.get("native_graph_edge_count") != 0
        or report.get("native_graph_finding_count") != 0
        or report.get("native_qa_model_count") != 2
        or report.get("native_qa_error_count") != 0
        or report.get("native_dry_run_prepared_count") != 2
        or report.get("native_dry_run_failed_count") != 0
        or report.get("resulting_process_materialized_count") != 0
        or report.get("preserved_blocked_check_count") != 9
        or report.get("preserved_scientific_gap_count") != 5
        or report.get("scientific_comparison_ready") is not False
        or report.get("comparative_lcia_allowed") is not False
        or report.get("routing", {}).get("next_internal_route")
        != "build-native-route-resulting-process-dry-run"
        or report.get("routing", {}).get("pause_internal_work") is not False
    ):
        raise ValueError("accepted Beat 31 lifecyclemodel shell report is required")
    routes = _list(report.get("routes"), "Beat 31 routes")
    by_route = {str(row.get("route_id")): row for row in routes if isinstance(row, dict)}
    if set(by_route) != EXPECTED_ROUTES or len(routes) != 2:
        raise ValueError("Beat 31 report must contain both exact routes")
    return by_route


def compile_resulting_process_projection_inputs(
    scenario_rows: bytes,
    scenario_binding: JsonObject,
    shell_report: JsonObject,
    shell_report_binding: JsonObject,
    shell_payloads: Mapping[str, JsonObject],
) -> JsonObject:
    """Validate Beat 31 bindings and return two deterministic local-only requests."""

    scenario_ref = _binding(scenario_binding, "scenario binding")
    if _hash_bytes(scenario_rows) != scenario_ref["sha256"]:
        raise ValueError("current scenario rows do not match their binding")
    report_ref = _binding(shell_report_binding, "Beat 31 report binding")
    if _hash(shell_report) != report_ref["sha256"]:
        raise ValueError("Beat 31 shell report does not match its binding")
    report_routes = _validated_shell_report(shell_report)
    sources = _object(shell_report.get("source_bindings"), "shell source bindings")
    shell_scenario_ref = _binding(
        sources.get("current_residual_scenarios"), "shell scenario source"
    )
    if shell_scenario_ref["sha256"] != scenario_ref["sha256"]:
        raise ValueError("Beat 31 shell report and current scenario hash conflict")
    if set(shell_payloads) != EXPECTED_ROUTES:
        raise ValueError("both route shell payloads are required")

    lines = [line for line in scenario_rows.splitlines(keepends=True) if line.strip()]
    if len(lines) != 2:
        raise ValueError("exactly two current Process rows are required")
    process_rows: Dict[str, Tuple[JsonObject, bytes]] = {}
    for line in lines:
        try:
            row = json.loads(line)
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("current Process rows must be valid JSONL") from error
        if not isinstance(row, dict):
            raise ValueError("current Process row must be an object")
        process_id, _ = _process_identity(row, "current Process")
        process_rows[process_id] = (row, line)

    routes: List[JsonObject] = []
    for route_id in sorted(EXPECTED_ROUTES):
        report_route = report_routes[route_id]
        process_id = _text(report_route.get("process_id"), f"{route_id} Process UUID")
        process_version = _text(
            report_route.get("process_version"), f"{route_id} Process version"
        )
        if process_id not in process_rows:
            raise ValueError(f"{route_id} current Process row is missing")
        process_row, process_bytes = process_rows[process_id]
        row_sha256 = _hash_bytes(process_bytes)
        if row_sha256 != report_route.get("route_row_sha256"):
            raise ValueError(f"{route_id} route-row hash drifted")
        actual_process_id, actual_process_version = _process_identity(
            process_row, f"{route_id} current Process"
        )
        if (actual_process_id, actual_process_version) != (
            process_id,
            process_version,
        ):
            raise ValueError(f"{route_id} current Process identity drifted")
        shell = shell_payloads[route_id]
        shell_sha256 = _hash(shell)
        if shell_sha256 != report_route.get("model_sha256"):
            raise ValueError(f"{route_id} shell payload hash drifted")
        shell_fact = _shell_facts(shell, f"{route_id} shell")
        expected_shell_fact = {
            "model_id": report_route.get("model_id"),
            "model_version": report_route.get("model_version"),
            "resulting_process_id": report_route.get("resulting_process_id"),
            "resulting_process_version": report_route.get("model_version"),
            "reference_process_instance_id": "1",
            "instance_id": "1",
            "multiplication_factor": Decimal("1"),
            "process_id": process_id,
            "process_version": process_version,
        }
        if shell_fact != expected_shell_fact:
            raise ValueError(f"{route_id} shell identity or one-node boundary drifted")
        reference = _reference_semantics(process_row, f"{route_id} current Process")
        if (
            reference[0] != report_route.get("reference_flow_id")
            or reference[1] != report_route.get("reference_flow_version")
            or reference[2] != _decimal(
                report_route.get("reference_amount"), f"{route_id} report amount"
            )
            or reference[3] != reference[2]
        ):
            raise ValueError(f"{route_id} current reference semantics drifted")
        model_id = str(shell_fact["model_id"])
        model_version = str(shell_fact["model_version"])
        process_filename = f"{process_id}_{process_version}.json"
        request: JsonObject = {
            "source_model": {
                "id": model_id,
                "version": model_version,
                "json_ordered_path": "inputs/lifecyclemodel.json",
            },
            "projection": {
                "mode": "primary-only",
                "process_id": model_id,
                "process_version": model_version,
                "metadata_overrides": {
                    "type_of_data_set": "Partly terminated system",
                    "kernel_contract": RESULTING_PROCESS_CONTRACT,
                    "route_id": route_id,
                    "source_process_sha256": row_sha256,
                    "source_shell_sha256": shell_sha256,
                },
                "attach_graph_snapshot": False,
            },
            "process_sources": {
                "process_json_files": [f"inputs/processes/{process_filename}"],
                "allow_remote_lookup": False,
            },
            "publish": {
                "intent": "dry_run",
                "prepare_process_payloads": True,
                "prepare_relation_payloads": True,
            },
        }
        routes.append(
            {
                "route_id": route_id,
                "model_id": model_id,
                "model_version": model_version,
                "process_id": process_id,
                "process_version": process_version,
                "process_filename": process_filename,
                "process_file_path": f"inputs/processes/{process_filename}",
                "process_row_bytes": process_bytes.decode("utf-8"),
                "process_row_sha256": row_sha256,
                "shell_payload": copy.deepcopy(shell),
                "shell_sha256": shell_sha256,
                "reference_flow_id": reference[0],
                "reference_flow_version": reference[1],
                "reference_amount": str(reference[2]),
                "reference_resulting_amount": str(reference[3]),
                "reference_direction": reference[4],
                "request": request,
                "request_sha256": _hash(request),
            }
        )
    identity = {
        "contract": RESULTING_PROCESS_CONTRACT,
        "scope_id": shell_report.get("scope_id"),
        "revision": shell_report.get("revision"),
        "scenario_sha256": scenario_ref["sha256"],
        "shell_report_sha256": report_ref["sha256"],
        "routes": [
            {
                key: row[key]
                for key in (
                    "route_id",
                    "model_id",
                    "process_id",
                    "process_row_sha256",
                    "shell_sha256",
                    "request_sha256",
                )
            }
            for row in routes
        ],
    }
    return {
        "schema_version": 1,
        "contract": RESULTING_PROCESS_CONTRACT,
        "scope_id": shell_report.get("scope_id"),
        "revision": shell_report.get("revision"),
        "status": COMPILED_STATUS,
        "projection_version": _hash(identity)[:16],
        "tool_identity": TIANGONG_PACKAGE,
        "source_bindings": {
            "current_residual_scenarios": scenario_ref,
            "lifecyclemodel_shell_report": report_ref,
        },
        "route_count": 2,
        "routes": routes,
        "preserved_blocked_check_count": 9,
        "preserved_scientific_gap_count": 5,
        "preserved_unresolved_field_count": shell_report.get(
            "preserved_unresolved_field_count"
        ),
        "preserved_unresolved_claim_count": shell_report.get(
            "preserved_unresolved_claim_count"
        ),
        "preserved_prohibited_claim_count": shell_report.get(
            "preserved_prohibited_claim_count"
        ),
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
    }


def _included_process_reference(payload: JsonObject) -> Tuple[str, str]:
    root = _process_root(payload, "projected Process")
    information = _object(
        root.get("processInformation"), "projected Process information"
    )
    technology = _object(
        information.get("technology"), "projected Process technology"
    )
    raw = technology.get("referenceToIncludedProcesses")
    refs = raw if isinstance(raw, list) else [raw]
    rows = [row for row in refs if isinstance(row, dict)]
    if len(rows) != 1:
        raise ValueError("projected Process must reference one included Process")
    return (
        _text(rows[0].get("@refObjectId"), "included Process UUID"),
        _text(rows[0].get("@version"), "included Process version"),
    )


def _diff_paths(left: object, right: object, prefix: str = "") -> List[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        paths: List[str] = []
        for key in sorted(set(left) | set(right)):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in left or key not in right:
                paths.append(path)
            else:
                paths.extend(_diff_paths(left[key], right[key], path))
        return paths
    if isinstance(left, list) and isinstance(right, list):
        if left == right:
            return []
        return [prefix]
    return [] if left == right else [prefix]


EXPECTED_NATIVE_CHANGE_PREFIXES = (
    "processDataSet.processInformation.dataSetInformation.common:UUID",
    "processDataSet.processInformation.dataSetInformation.name",
    "processDataSet.processInformation.dataSetInformation.common:generalComment",
    "processDataSet.processInformation.dataSetInformation.generatedFromLifecycleModel",
    "processDataSet.processInformation.quantitativeReference.referenceToReferenceFlow",
    "processDataSet.processInformation.technology",
    "processDataSet.exchanges",
    "processDataSet.administrativeInformation.publicationAndOwnership.common:dataSetVersion",
    "processDataSet.administrativeInformation.publicationAndOwnership.common:permanentDataSetURI",
    "processDataSet.modellingAndValidation.LCIMethodAndAllocation.typeOfDataSet",
    "projectionMetadata",
    "topologySummary",
)


def verify_route_projection(
    route_input: JsonObject, native_bundle: JsonObject
) -> JsonObject:
    """Verify one native projection without reproducing its aggregation logic."""

    route_id = _text(route_input.get("route_id"), "route_id")
    model_id = _text(route_input.get("model_id"), f"{route_id} model_id")
    model_version = _text(
        route_input.get("model_version"), f"{route_id} model_version"
    )
    process_id = _text(route_input.get("process_id"), f"{route_id} process_id")
    process_version = _text(
        route_input.get("process_version"), f"{route_id} process_version"
    )
    try:
        source_process = json.loads(
            _text(route_input.get("process_row_bytes"), f"{route_id} Process row")
        )
    except json.JSONDecodeError as error:
        raise ValueError(f"{route_id} Process row is invalid JSON") from error
    if not isinstance(source_process, dict):
        raise ValueError(f"{route_id} Process row must be an object")

    source_model = _object(native_bundle.get("source_model"), "native source model")
    report = _object(native_bundle.get("report"), "native projection report")
    native_reference_instance = report.get("reference_process_instance_id")
    source_reference_instance = source_model.get("reference_process_instance_id")
    resolution = _object(
        report.get("resolved_process_summary"), "native process resolution"
    )
    resolution_items = _list(resolution.get("items"), "native resolution items")
    if (
        report.get("status") != "prepared_local_bundle"
        or report.get("projection_mode") != "primary-only"
        or report.get("node_count") != 1
        or report.get("edge_count") != 0
        or report.get("projected_process_count") != 1
        or native_reference_instance not in (None, "1", 1)
        or source_model.get("id") != model_id
        or source_model.get("version") != model_version
        or source_model.get("reference_to_resulting_process_id") != model_id
        or source_model.get("reference_to_resulting_process_version") != model_version
        or source_reference_instance not in (None, "1", 1)
        or source_reference_instance != native_reference_instance
    ):
        raise ValueError(f"{route_id} native one-node projection summary drifted")
    if (
        resolution.get("required_process_count") != 1
        or resolution.get("resolved_process_count") != 1
        or resolution.get("explicit_process_file_count") != 1
        or resolution.get("remote_resolution_count") != 0
        or len(resolution_items) != 1
        or not isinstance(resolution_items[0], dict)
        or resolution_items[0].get("process_id") != process_id
        or resolution_items[0].get("version") != process_version
        or resolution_items[0].get("resolution") != "local_file"
    ):
        raise ValueError(f"{route_id} remote or local Process resolution drifted")

    projected_rows = _list(
        native_bundle.get("projected_processes"), "native projected processes"
    )
    relations = _list(native_bundle.get("relations"), "native projection relations")
    if len(projected_rows) != 1 or not isinstance(projected_rows[0], dict):
        raise ValueError(f"{route_id} must emit one projected Process")
    if len(relations) != 1 or not isinstance(relations[0], dict):
        raise ValueError(f"{route_id} must emit one native relation")
    projected_row = projected_rows[0]
    projected = _object(projected_row.get("json_ordered"), "projected Process payload")
    resulting_id, resulting_version = _process_identity(
        projected, f"{route_id} projected Process"
    )
    if (
        projected_row.get("role") != "primary"
        or projected_row.get("id") != model_id
        or projected_row.get("version") != model_version
        or (resulting_id, resulting_version) != (model_id, model_version)
    ):
        raise ValueError(f"{route_id} projected Process identity drifted")
    relation = relations[0]
    signature = _text(report.get("projection_signature"), "projection signature")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", signature):
        raise ValueError(f"{route_id} projection signature is invalid")
    if (
        relation.get("lifecyclemodel_id") != model_id
        or relation.get("lifecyclemodel_version") != model_version
        or relation.get("resulting_process_id") != model_id
        or relation.get("resulting_process_version") != model_version
        or relation.get("projection_role") != "primary"
        or relation.get("projection_signature") != signature
        or relation.get("is_primary") is not True
    ):
        raise ValueError(f"{route_id} native relation drifted")
    included = _included_process_reference(projected)
    if included != (process_id, process_version):
        raise ValueError(f"{route_id} included Process lineage drifted")

    source_exchange = _exchange_semantics(source_process, "source Process")
    projected_exchange = _exchange_semantics(projected, "projected Process")
    if projected_exchange != source_exchange:
        raise ValueError(f"{route_id} projected exchange semantics drifted")
    source_reference = _reference_semantics(source_process, "source Process")
    projected_reference = _reference_semantics(projected, "projected Process")
    if projected_reference != source_reference:
        raise ValueError(f"{route_id} projected reference semantics drifted")
    if (
        source_reference[0] != route_input.get("reference_flow_id")
        or source_reference[1] != route_input.get("reference_flow_version")
        or source_reference[2]
        != _decimal(route_input.get("reference_amount"), "route reference amount")
        or source_reference[4] != route_input.get("reference_direction")
    ):
        raise ValueError(f"{route_id} bound reference semantics drifted")
    metadata = _object(projected.get("projectionMetadata"), "projection metadata")
    topology = _object(projected.get("topologySummary"), "topology summary")
    if (
        metadata.get("generated_from_lifecyclemodel_id") != model_id
        or metadata.get("generated_from_lifecyclemodel_version") != model_version
        or metadata.get("projection_role") != "primary"
        or metadata.get("projection_signature") != signature
        or metadata.get("type_of_data_set") != "Partly terminated system"
        or topology != {"process_instance_count": 1, "edge_count": 0}
    ):
        raise ValueError(f"{route_id} projection metadata or topology drifted")
    normalized_source = {"processDataSet": copy.deepcopy(_process_root(source_process, "source Process"))}
    changed_paths = _diff_paths(normalized_source, projected)
    unexpected_paths = [
        path
        for path in changed_paths
        if not any(
            path == prefix or path.startswith(f"{prefix}.")
            for prefix in EXPECTED_NATIVE_CHANGE_PREFIXES
        )
    ]
    if unexpected_paths:
        raise ValueError(
            f"{route_id} native projection changed unexpected fields: {unexpected_paths}"
        )
    return {
        "route_id": route_id,
        "model_id": model_id,
        "model_version": model_version,
        "source_process_id": process_id,
        "source_process_version": process_version,
        "source_process_sha256": route_input.get("process_row_sha256"),
        "resulting_process_id": resulting_id,
        "resulting_process_version": resulting_version,
        "included_process_id": included[0],
        "included_process_version": included[1],
        "projection_signature": signature,
        "reference_process_instance_resolution": (
            "explicit_internal_id_1"
            if native_reference_instance in ("1", 1)
            else "sole_positive_instance_fallback"
        ),
        "node_count": 1,
        "edge_count": 0,
        "exchange_count": len(projected_exchange),
        "exchange_semantics_preserved": True,
        "reference_flow_id": source_reference[0],
        "reference_flow_version": source_reference[1],
        "reference_amount": str(source_reference[2]),
        "reference_resulting_amount": str(source_reference[3]),
        "reference_direction": source_reference[4],
        "reference_semantics_preserved": True,
        "remote_resolution_count": 0,
        "changed_field_paths": changed_paths,
        "unexpected_changed_field_paths": [],
        "projected_process": projected,
        "projected_process_sha256": _hash(projected),
    }


def _canonical_decimal_string(value: object, label: str) -> str:
    parsed = _decimal(value, label)
    text = format(parsed, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def compile_projection_schema_normalization_patch(
    verified_routes: Sequence[JsonObject],
) -> JsonObject:
    """Compile a lossless native patch for the pinned projector's numeric output."""

    if len(verified_routes) != 2:
        raise ValueError("two verified projection routes are required for normalization")
    patch_sets = []
    replacement_count = 0
    for row_index, route in enumerate(verified_routes):
        route_replacement_count = 0
        route_id = _text(route.get("route_id"), "verified route_id")
        process_id = _text(
            route.get("resulting_process_id"), f"{route_id} resulting Process UUID"
        )
        version = _text(
            route.get("resulting_process_version"),
            f"{route_id} resulting Process version",
        )
        projected = _object(
            route.get("projected_process"), f"{route_id} raw projected Process"
        )
        actual_id, actual_version = _process_identity(
            projected, f"{route_id} raw projected Process"
        )
        if (actual_id, actual_version) != (process_id, version):
            raise ValueError(f"{route_id} raw projection identity drifted before normalization")
        operations: List[JsonObject] = [
            {
                "op": "test",
                "path": "/processDataSet/processInformation/dataSetInformation/common:UUID",
                "value": process_id,
            },
            {
                "op": "test",
                "path": (
                    "/processDataSet/administrativeInformation/"
                    "publicationAndOwnership/common:dataSetVersion"
                ),
                "value": version,
            },
        ]
        for exchange_index, exchange in enumerate(
            _exchanges(projected, f"{route_id} raw projected Process")
        ):
            for field in ("meanAmount", "resultingAmount"):
                value = exchange.get(field)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                normalized = _canonical_decimal_string(
                    value, f"{route_id} exchange {exchange_index} {field}"
                )
                path = f"/processDataSet/exchanges/exchange/{exchange_index}/{field}"
                operations.extend(
                    [
                        {"op": "test", "path": path, "value": value},
                        {
                            "op": "replace",
                            "path": path,
                            "value": normalized,
                            "basis": (
                                "Pinned TianGong 0.0.25 resulting-process projector emits "
                                "JSON numbers while ProcessSchema requires decimal strings; "
                                "Decimal value is unchanged."
                            ),
                            "evidence": {
                                "contract": RESULTING_PROCESS_CONTRACT,
                                "route_id": route_id,
                                "projection_signature": route.get(
                                    "projection_signature"
                                ),
                                "source_process_sha256": route.get(
                                    "source_process_sha256"
                                ),
                                "raw_json_type": "number",
                                "schema_json_type": "string",
                                "decimal_value": normalized,
                            },
                            "resolution": {
                                "mode": "lossless_schema_serialization_normalization",
                                "used_context_kinds": [
                                    "native_projection_bundle",
                                    "pinned_process_schema",
                                ],
                                "scientific_value_changed": False,
                            },
                        },
                    ]
                )
                replacement_count += 1
                route_replacement_count += 1
        if route_replacement_count == 0:
            raise ValueError(
                f"{route_id} raw native projection did not expose expected numeric amounts"
            )
        patch_sets.append(
            {
                "row_index": row_index,
                "dataset_id": process_id,
                "dataset_version": version,
                "operations": operations,
            }
        )
    operation_count = sum(len(row["operations"]) for row in patch_sets)
    return {
        "schema_version": 1,
        "contract": RESULTING_PROCESS_CONTRACT,
        "patch_status": "completed",
        "normalization_kind": "lossless_json_numeric_to_tidas_string",
        "patch_sets": patch_sets,
        "route_count": 2,
        "operation_count": operation_count,
        "replacement_count": replacement_count,
        "scientific_values_changed": False,
        "exchange_semantics_changed": False,
        "remote_write_authorized": False,
    }


def verify_normalized_route_projection(
    route: JsonObject, raw: JsonObject, normalized: JsonObject
) -> JsonObject:
    """Prove that normalization changed representation, not LCA semantics."""

    route_id = _text(route.get("route_id"), "verified route_id")
    expected_identity = (
        _text(route.get("resulting_process_id"), f"{route_id} resulting UUID"),
        _text(
            route.get("resulting_process_version"),
            f"{route_id} resulting version",
        ),
    )
    if _process_identity(raw, f"{route_id} raw projection") != expected_identity:
        raise ValueError(f"{route_id} raw projection identity drifted")
    if _process_identity(normalized, f"{route_id} normalized projection") != expected_identity:
        raise ValueError(f"{route_id} normalized projection identity drifted")
    if _exchange_semantics(raw, f"{route_id} raw projection") != _exchange_semantics(
        normalized, f"{route_id} normalized projection"
    ):
        raise ValueError(f"{route_id} exchange semantics changed during normalization")
    if _reference_semantics(raw, f"{route_id} raw projection") != _reference_semantics(
        normalized, f"{route_id} normalized projection"
    ):
        raise ValueError(f"{route_id} reference semantics changed during normalization")

    raw_exchanges = _exchanges(raw, f"{route_id} raw projection")
    normalized_exchanges = _exchanges(
        normalized, f"{route_id} normalized projection"
    )
    if len(raw_exchanges) != len(normalized_exchanges):
        raise ValueError(f"{route_id} exchange count changed during normalization")
    expected_paths = []
    for exchange_index, (raw_exchange, normalized_exchange) in enumerate(
        zip(raw_exchanges, normalized_exchanges)
    ):
        for field in ("meanAmount", "resultingAmount"):
            raw_value = raw_exchange.get(field)
            normalized_value = normalized_exchange.get(field)
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                raise ValueError(
                    f"{route_id} raw exchange {exchange_index} {field} is not numeric"
                )
            expected_value = _canonical_decimal_string(
                raw_value, f"{route_id} exchange {exchange_index} {field}"
            )
            if normalized_value != expected_value:
                raise ValueError(
                    f"{route_id} exchange {exchange_index} {field} was not "
                    "canonically normalized"
                )
            expected_paths.append(
                f"processDataSet.exchanges.exchange.{exchange_index}.{field}"
            )

    def normalize_expected_amounts(payload: JsonObject) -> JsonObject:
        candidate = copy.deepcopy(payload)
        for exchange_index, exchange in enumerate(
            _exchanges(candidate, f"{route_id} normalization candidate")
        ):
            for field in ("meanAmount", "resultingAmount"):
                exchange[field] = _canonical_decimal_string(
                    exchange[field],
                    f"{route_id} candidate exchange {exchange_index} {field}",
                )
        return candidate

    if normalize_expected_amounts(raw) != normalized:
        raise ValueError(f"{route_id} normalization changed fields outside amounts")
    return {
        "normalization_kind": "lossless_json_numeric_to_tidas_string",
        "normalization_replacement_count": len(expected_paths),
        "normalization_changed_field_paths": expected_paths,
        "normalization_decimal_values_preserved": True,
        "normalization_exchange_semantics_preserved": True,
        "normalization_reference_semantics_preserved": True,
        "normalization_scientific_values_changed": False,
    }


def _native_prefix(tiangong_package: str = TIANGONG_PACKAGE) -> List[str]:
    return [
        "npm",
        "exec",
        "--yes",
        "--offline",
        f"--package={tiangong_package}",
        "--",
        "tiangong-lca",
    ]


def native_projection_commands(
    compiled: JsonObject,
    out_dir: Union[str, Path],
    tiangong_package: str = TIANGONG_PACKAGE,
) -> List[List[str]]:
    """Return pinned offline commands for both projections and Process gates."""

    if compiled.get("contract") != RESULTING_PROCESS_CONTRACT:
        raise ValueError("compiled resulting-Process inputs are required")
    root = Path(out_dir)
    prefix = _native_prefix(tiangong_package)
    commands: List[List[str]] = []
    for route in _list(compiled.get("routes"), "compiled routes"):
        if not isinstance(route, dict):
            raise ValueError("compiled route is malformed")
        route_id = _text(route.get("route_id"), "compiled route_id")
        commands.append(
            [
                *prefix,
                "lifecyclemodel",
                "build-resulting-process",
                "--input",
                str(root / "routes" / route_id / "projection-request.json"),
                "--out-dir",
                str(root / "routes" / route_id / "native-projection"),
                "--json",
            ]
        )
    rows_path = root / "outputs" / "resulting-processes.jsonl"
    raw_rows_path = root / "outputs" / "raw-resulting-processes.jsonl"
    patch_path = root / "inputs" / "resulting-process-normalization-patch.json"
    commands.extend(
        [
            [
                *prefix,
                "dataset",
                "patch",
                "apply",
                "--input",
                str(raw_rows_path),
                "--patch",
                str(patch_path),
                "--out",
                str(rows_path),
                "--out-dir",
                str(root / "native-normalization"),
                "--json",
            ],
            [
                *prefix,
                "dataset",
                "validate",
                "--input",
                str(rows_path),
                "--type",
                "process",
                "--out-dir",
                str(root / "native-validation"),
                "--json",
            ],
            [
                *prefix,
                "qa",
                "process",
                "--rows-file",
                str(rows_path),
                "--out-dir",
                str(root / "native-qa"),
                "--json",
            ],
            [
                *prefix,
                "process",
                "save-draft",
                "--input",
                str(rows_path),
                "--out-dir",
                str(root / "native-save-draft"),
                "--dry-run",
                "--json",
            ],
        ]
    )
    return commands


def _ids(rows: object, key: str) -> List[str]:
    return sorted(
        str(row.get(key))
        for row in rows
        if isinstance(rows, list) and isinstance(row, dict) and row.get(key)
    )


def verify_native_resulting_process_gates(
    compiled: JsonObject, native: Mapping[str, object]
) -> JsonObject:
    """Fail closed unless both native projections and combined Process gates pass."""

    if (
        compiled.get("contract") != RESULTING_PROCESS_CONTRACT
        or compiled.get("status") != COMPILED_STATUS
        or compiled.get("tool_identity") != TIANGONG_PACKAGE
        or compiled.get("route_count") != 2
        or compiled.get("preserved_blocked_check_count") != 9
        or compiled.get("preserved_scientific_gap_count") != 5
    ):
        raise ValueError("compiled resulting-Process projection inputs are required")
    compiled_routes = [
        row
        for row in _list(compiled.get("routes"), "compiled routes")
        if isinstance(row, dict)
    ]
    verified_routes = [
        row for row in _list(native.get("routes"), "verified routes") if isinstance(row, dict)
    ]
    if (
        len(compiled_routes) != 2
        or len(verified_routes) != 2
        or {row.get("route_id") for row in verified_routes} != EXPECTED_ROUTES
        or any(
            row.get("exchange_semantics_preserved") is not True
            or row.get("reference_semantics_preserved") is not True
            or row.get("remote_resolution_count") != 0
            or row.get("node_count") != 1
            or row.get("edge_count") != 0
            or row.get("unexpected_changed_field_paths") != []
            for row in verified_routes
        )
    ):
        raise ValueError("two local-only one-node native projections are required")
    model_ids = sorted(str(row.get("model_id")) for row in compiled_routes)
    if sorted(str(row.get("resulting_process_id")) for row in verified_routes) != model_ids:
        raise ValueError("native resulting Process identities drifted")
    source_binding = _object(
        _object(compiled.get("source_bindings"), "compiled source bindings").get(
            "current_residual_scenarios"
        ),
        "current scenario binding",
    )
    if native.get("source_rows_sha256_after") != source_binding.get("sha256"):
        raise ValueError("current Process source bytes changed during projection")

    normalization = _object(
        native.get("normalization_patch"), "projection normalization patch"
    )
    normalization_report = _object(
        native.get("normalization_report"), "projection normalization report"
    )
    normalization_evidence = _list(
        native.get("normalization_evidence"), "projection normalization evidence"
    )
    replacement_count = normalization.get("replacement_count")
    operation_count = normalization.get("operation_count")
    allowed_normalization_paths = {
        path
        for row in verified_routes
        for path in row.get("normalization_changed_field_paths", [])
        if isinstance(path, str)
    }
    evidence_paths = {
        str(row.get("path")).lstrip("/").replace("/", ".")
        for row in normalization_evidence
        if isinstance(row, dict)
    }
    if (
        normalization.get("contract") != RESULTING_PROCESS_CONTRACT
        or normalization.get("patch_status") != "completed"
        or normalization.get("normalization_kind")
        != "lossless_json_numeric_to_tidas_string"
        or normalization.get("route_count") != 2
        or not isinstance(replacement_count, int)
        or replacement_count <= 0
        or normalization.get("scientific_values_changed") is not False
        or normalization.get("exchange_semantics_changed") is not False
        or normalization.get("remote_write_authorized") is not False
        or normalization_report.get("status") != "completed"
        or normalization_report.get("row_count") != 2
        or normalization_report.get("patch_count") != 2
        or normalization_report.get("operation_count") != operation_count
        or normalization_report.get("applied_operation_count") != replacement_count
        or normalization_report.get("evidence_count") != replacement_count
        or normalization_report.get("blockers") != []
        or len(normalization_evidence) != replacement_count
        or evidence_paths != allowed_normalization_paths
        or any(
            not isinstance(row, dict)
            or row.get("op") != "replace"
            or row.get("resolution", {}).get("mode")
            != "lossless_schema_serialization_normalization"
            or row.get("resolution", {}).get("scientific_value_changed") is not False
            for row in normalization_evidence
        )
        or any(
            row.get("normalization_kind")
            != "lossless_json_numeric_to_tidas_string"
            or row.get("normalization_decimal_values_preserved") is not True
            or row.get("normalization_exchange_semantics_preserved") is not True
            or row.get("normalization_reference_semantics_preserved") is not True
            or row.get("normalization_scientific_values_changed") is not False
            for row in verified_routes
        )
    ):
        raise ValueError(
            "native projection normalization must be lossless, evidenced and complete"
        )

    validation = _object(native.get("validation_report"), "Process validation report")
    validation_counts = _object(validation.get("counts"), "validation counts")
    validation_rows = _list(validation.get("rows"), "validation rows")
    if (
        validation.get("status") != "completed"
        or validation.get("requested_type") != "process"
        or validation_counts.get("total") != 2
        or validation_counts.get("valid") != 2
        or validation_counts.get("invalid") != 0
        or _ids(validation_rows, "id") != model_ids
        or any(
            not isinstance(row, dict)
            or row.get("type") != "process"
            or row.get("status") != "valid"
            or row.get("issue_count") != 0
            for row in validation_rows
        )
    ):
        raise ValueError("2/2 native resulting Process validation is required")

    qa = _object(native.get("qa_report"), "Process QA report")
    qa_gate = _object(qa.get("ruleset_gate"), "Process QA ruleset gate")
    qa_counts = _object(qa_gate.get("counts"), "Process QA gate counts")
    if (
        qa.get("status") != "completed_local_process_qa"
        or qa.get("input_mode") != "rows_file"
        or qa.get("process_count") != 2
        or qa.get("blocker_count") != 0
        or qa_gate.get("status") != "passed"
        or qa_counts.get("blockers") != 0
        or qa_gate.get("blockers") != []
    ):
        raise ValueError("native resulting Process QA must pass with zero blocker")
    findings = native.get("qa_findings")
    finding_rows = findings if isinstance(findings, list) else []
    if qa.get("rule_finding_count") != len(finding_rows):
        raise ValueError("native Process QA finding artifact drifted")

    save = _object(native.get("save_draft_summary"), "Process save-draft summary")
    save_counts = _object(save.get("counts"), "Process save-draft counts")
    save_rows = _list(save.get("processes"), "Process save-draft rows")
    if (
        save.get("commit") is not False
        or save.get("mode") != "dry_run"
        or save.get("status") != "completed"
        or save_counts != {"selected": 2, "prepared": 2, "executed": 0, "failed": 0}
        or _ids(save_rows, "id") != model_ids
        or any(
            not isinstance(row, dict)
            or row.get("status") != "prepared"
            or row.get("validation", {}).get("ok") is not True
            or row.get("validation", {}).get("issue_count") != 0
            for row in save_rows
        )
    ):
        raise ValueError("native Process save-draft dry-run must prepare 2/2")

    compact_routes = []
    for row in sorted(verified_routes, key=lambda item: str(item.get("route_id"))):
        compact_routes.append(
            {
                key: copy.deepcopy(row.get(key))
                for key in (
                    "route_id",
                    "model_id",
                    "model_version",
                    "source_process_id",
                    "source_process_version",
                    "source_process_sha256",
                    "resulting_process_id",
                    "resulting_process_version",
                    "included_process_id",
                    "included_process_version",
                    "projection_signature",
                    "reference_process_instance_resolution",
                    "node_count",
                    "edge_count",
                    "exchange_count",
                    "exchange_semantics_preserved",
                    "reference_flow_id",
                    "reference_flow_version",
                    "reference_amount",
                    "reference_resulting_amount",
                    "reference_direction",
                    "reference_semantics_preserved",
                    "remote_resolution_count",
                    "changed_field_paths",
                    "unexpected_changed_field_paths",
                    "normalization_kind",
                    "normalization_replacement_count",
                    "normalization_changed_field_paths",
                    "normalization_decimal_values_preserved",
                    "normalization_exchange_semantics_preserved",
                    "normalization_reference_semantics_preserved",
                    "normalization_scientific_values_changed",
                    "raw_projected_process_path",
                    "raw_projected_process_sha256",
                    "projected_process_path",
                    "projected_process_sha256",
                )
            }
        )
    bindings = native.get("bindings")
    native_bindings = bindings if isinstance(bindings, dict) else {}
    return {
        "schema_version": 1,
        "contract": RESULTING_PROCESS_CONTRACT,
        "scope_id": compiled.get("scope_id"),
        "revision": compiled.get("revision"),
        "status": "native_route_resulting_process_dry_run_completed",
        "projection_version": compiled.get("projection_version"),
        "tool_identity": TIANGONG_PACKAGE,
        "source_bindings": compiled.get("source_bindings"),
        "native_artifacts": native_bindings,
        "route_count": 2,
        "routes": compact_routes,
        "resulting_process_projected_count": 2,
        "native_normalization_kind": normalization.get("normalization_kind"),
        "native_normalization_operation_count": operation_count,
        "native_normalization_replacement_count": replacement_count,
        "native_normalization_evidence_count": len(normalization_evidence),
        "native_normalization_scientific_values_changed": False,
        "native_valid_count": 2,
        "native_invalid_count": 0,
        "native_qa_finding_count": len(finding_rows),
        "native_qa_blocker_count": 0,
        "native_dry_run_selected_count": 2,
        "native_dry_run_prepared_count": 2,
        "native_dry_run_executed_count": 0,
        "native_dry_run_failed_count": 0,
        "source_process_rows_unchanged": True,
        "remote_resolution_count": 0,
        "preserved_blocked_check_count": 9,
        "preserved_scientific_gap_count": 5,
        "preserved_unresolved_field_count": compiled.get(
            "preserved_unresolved_field_count"
        ),
        "preserved_unresolved_claim_count": compiled.get(
            "preserved_unresolved_claim_count"
        ),
        "preserved_prohibited_claim_count": compiled.get(
            "preserved_prohibited_claim_count"
        ),
        "network_read_executed_count": 0,
        "external_action_executed_count": 0,
        "remote_write_executed_count": 0,
        "publication_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "current_model_mutation_executed_count": 0,
        "claim_transition_executed_count": 0,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "routing": {
            "next_internal_route": "compile-native-route-model-execution-package",
            "pause_internal_work": False,
            "reason": (
                "Both local resulting Process projections are schema-valid and preserve "
                "one-node semantics; package them with shells and retained blockers before comparison."
            ),
        },
        "next_action": (
            "Compile a hash-bound local route-model execution package from current Processes, "
            "validated shells and resulting Process projections; do not compare or calculate LCIA."
        ),
    }


def _read_json(path: Path, label: str) -> JsonObject:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} is not valid JSON: {path}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object: {path}")
    return value


def _read_jsonl(path: Path, label: str) -> List[JsonObject]:
    rows: List[JsonObject] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise RuntimeError(f"{label} is not readable: {path}") from error
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"{label} contains invalid JSONL: {path}") from error
        if not isinstance(row, dict):
            raise RuntimeError(f"{label} row must be an object: {path}")
        rows.append(row)
    return rows


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def _artifact_binding(path: Path, root: Path) -> JsonObject:
    content = path.read_bytes()
    return {
        "path": str(path.relative_to(root)),
        "sha256": _hash_bytes(content),
        "bytes": len(content),
    }


def _run_native(command: Sequence[str], cwd: Path) -> None:
    environment = dict(os.environ)
    environment["npm_config_offline"] = "true"
    result = subprocess.run(
        list(command),
        cwd=str(cwd),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Pinned TianGong command failed: "
            + " ".join(command)
            + f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def execute_resulting_process_dry_run(
    scenario_rows_path: Path,
    scenario_binding_path: Path,
    shell_report_path: Path,
    shell_report_binding_path: Path,
    shell_root: Path,
    out_dir: Path,
    tiangong_package: str = TIANGONG_PACKAGE,
) -> JsonObject:
    """Run two projections and all combined native Process gates."""

    if tiangong_package != TIANGONG_PACKAGE:
        raise ValueError(f"tiangong package must remain pinned to {TIANGONG_PACKAGE}")
    scenario_bytes = scenario_rows_path.read_bytes()
    scenario_binding = _read_json(scenario_binding_path, "scenario binding")
    report_bytes = shell_report_path.read_bytes()
    shell_report_binding = _read_json(
        shell_report_binding_path, "shell report binding"
    )
    if _hash_bytes(report_bytes) != shell_report_binding.get("sha256"):
        raise ValueError("Beat 31 shell report does not match its binding")
    shell_report = _read_json(shell_report_path, "Beat 31 shell report")
    shell_payloads: Dict[str, JsonObject] = {}
    for route in _list(shell_report.get("routes"), "Beat 31 routes"):
        if not isinstance(route, dict):
            raise ValueError("Beat 31 route is malformed")
        route_id = _text(route.get("route_id"), "Beat 31 route_id")
        relative_path = _text(route.get("model_path"), f"{route_id} shell path")
        shell_path = (shell_root / relative_path).resolve()
        try:
            shell_path.relative_to(shell_root.resolve())
        except ValueError as error:
            raise ValueError(f"{route_id} shell path escaped its artifact root") from error
        if _hash_bytes(shell_path.read_bytes()) != route.get("model_sha256"):
            raise ValueError(f"{route_id} shell artifact hash drifted")
        shell_payloads[route_id] = _read_json(shell_path, f"{route_id} shell")
    compiled = compile_resulting_process_projection_inputs(
        scenario_bytes,
        scenario_binding,
        shell_report,
        shell_report_binding,
        shell_payloads,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    commands = native_projection_commands(compiled, out_dir, tiangong_package)
    verified_routes: List[JsonObject] = []
    raw_projected_payloads: List[JsonObject] = []
    projection_bindings: Dict[str, JsonObject] = {}
    native_filenames = (
        "request.normalized.json",
        "source-model.normalized.json",
        "source-model.summary.json",
        "projection-report.json",
        "process-projection-bundle.json",
    )
    for index, route in enumerate(compiled["routes"]):
        route_id = str(route["route_id"])
        route_root = out_dir / "routes" / route_id
        request_path = route_root / "projection-request.json"
        shell_path = route_root / "inputs" / "lifecyclemodel.json"
        process_path = route_root / str(route["process_file_path"])
        _write_json(request_path, route["request"])
        _write_json(shell_path, route["shell_payload"])
        process_path.parent.mkdir(parents=True, exist_ok=True)
        process_path.write_text(str(route["process_row_bytes"]), encoding="utf-8")
        if _hash_bytes(process_path.read_bytes()) != route["process_row_sha256"]:
            raise RuntimeError(f"{route_id} exact Process input write drifted")
        _run_native(commands[index], out_dir)
        native_root = route_root / "native-projection"
        bundle_path = native_root / "process-projection-bundle.json"
        bundle = _read_json(bundle_path, f"{route_id} native projection bundle")
        verified = verify_route_projection(route, bundle)
        raw_projected = _object(
            verified.get("projected_process"), f"{route_id} raw projected Process"
        )
        raw_projected_path = route_root / "projected-process.raw.json"
        _write_json(raw_projected_path, raw_projected)
        verified["raw_projected_process_path"] = str(
            raw_projected_path.relative_to(out_dir)
        )
        verified["raw_projected_process_sha256"] = _hash(raw_projected)
        verified_routes.append(verified)
        raw_projected_payloads.append(raw_projected)
        projection_bindings[route_id] = {
            "request": _artifact_binding(request_path, out_dir),
            "shell_input": _artifact_binding(shell_path, out_dir),
            "process_input": _artifact_binding(process_path, out_dir),
            "raw_projected_process": _artifact_binding(
                raw_projected_path, out_dir
            ),
            "native": {
                filename.replace(".json", "").replace("-", "_"): _artifact_binding(
                    native_root / filename, out_dir
                )
                for filename in native_filenames
            },
        }
    output_dir = out_dir / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_rows_path = output_dir / "raw-resulting-processes.jsonl"
    raw_rows_path.write_bytes(
        b"".join(_json_bytes(row) for row in raw_projected_payloads)
    )
    normalization_patch = compile_projection_schema_normalization_patch(
        verified_routes
    )
    normalization_patch_path = (
        out_dir / "inputs" / "resulting-process-normalization-patch.json"
    )
    _write_json(normalization_patch_path, normalization_patch)
    _run_native(commands[2], out_dir)

    rows_path = output_dir / "resulting-processes.jsonl"
    normalized_payloads = _read_jsonl(
        rows_path, "normalized resulting Process rows"
    )
    normalized_by_id: Dict[str, JsonObject] = {}
    for normalized in normalized_payloads:
        normalized_id, _ = _process_identity(
            normalized, "normalized resulting Process"
        )
        if normalized_id in normalized_by_id:
            raise RuntimeError("normalized resulting Process identities are not unique")
        normalized_by_id[normalized_id] = normalized
    expected_ids = {
        str(route["resulting_process_id"]) for route in verified_routes
    }
    if len(normalized_payloads) != 2 or set(normalized_by_id) != expected_ids:
        raise RuntimeError("native normalization did not return both exact projections")
    for verified in verified_routes:
        route_id = str(verified["route_id"])
        raw_projected = _object(
            verified.pop("projected_process"), f"{route_id} raw projected Process"
        )
        normalized = normalized_by_id[str(verified["resulting_process_id"])]
        verified.update(
            verify_normalized_route_projection(
                verified, raw_projected, normalized
            )
        )
        normalized_path = (
            out_dir / "routes" / route_id / "projected-process.normalized.json"
        )
        _write_json(normalized_path, normalized)
        verified["projected_process_path"] = str(
            normalized_path.relative_to(out_dir)
        )
        verified["projected_process_sha256"] = _hash(normalized)
        projection_bindings[route_id]["projected_process"] = _artifact_binding(
            normalized_path, out_dir
        )
    diff_path = output_dir / "resulting-process-semantic-diff.json"
    _write_json(
        diff_path,
        {
            "contract": RESULTING_PROCESS_CONTRACT,
            "projection_version": compiled["projection_version"],
            "source_rows_sha256": _hash_bytes(scenario_rows_path.read_bytes()),
            "routes": [
                {
                    key: copy.deepcopy(row.get(key))
                    for key in (
                        "route_id",
                        "source_process_id",
                        "resulting_process_id",
                        "exchange_semantics_preserved",
                        "reference_semantics_preserved",
                        "changed_field_paths",
                        "unexpected_changed_field_paths",
                        "normalization_kind",
                        "normalization_replacement_count",
                        "normalization_changed_field_paths",
                        "normalization_decimal_values_preserved",
                        "normalization_scientific_values_changed",
                    )
                }
                for row in verified_routes
            ],
        },
    )
    for command in commands[3:]:
        _run_native(command, out_dir)

    paths = {
        "normalization_report": out_dir
        / "native-normalization"
        / "outputs"
        / "dataset-patch-apply-report.json",
        "normalization_evidence": out_dir
        / "native-normalization"
        / "outputs"
        / "patch-evidence.jsonl",
        "validation_report": out_dir
        / "native-validation"
        / "outputs"
        / "validation-report.json",
        "qa_report": out_dir / "native-qa" / "process-qa-report.json",
        "qa_findings": out_dir / "native-qa" / "process-qa-rule-findings.jsonl",
        "qa_ruleset_gate": out_dir / "native-qa" / "process-qa-ruleset-gate.json",
        "save_draft_summary": out_dir
        / "native-save-draft"
        / "outputs"
        / "save-draft-rpc"
        / "summary.json",
        "save_draft_failures": out_dir
        / "native-save-draft"
        / "outputs"
        / "save-draft-rpc"
        / "failures.jsonl",
    }
    native: JsonObject = {
        "routes": verified_routes,
        "source_rows_sha256_after": _hash_bytes(scenario_rows_path.read_bytes()),
        "normalization_patch": normalization_patch,
        "normalization_report": _read_json(
            paths["normalization_report"], "projection normalization report"
        ),
        "normalization_evidence": _read_jsonl(
            paths["normalization_evidence"], "projection normalization evidence"
        ),
        "validation_report": _read_json(paths["validation_report"], "validation report"),
        "qa_report": _read_json(paths["qa_report"], "Process QA report"),
        "qa_findings": _read_jsonl(paths["qa_findings"], "Process QA findings"),
        "save_draft_summary": _read_json(
            paths["save_draft_summary"], "Process save-draft summary"
        ),
        "bindings": {
            "routes": projection_bindings,
            "raw_resulting_process_rows": _artifact_binding(raw_rows_path, out_dir),
            "normalization_patch": _artifact_binding(
                normalization_patch_path, out_dir
            ),
            "resulting_process_rows": _artifact_binding(rows_path, out_dir),
            "semantic_diff": _artifact_binding(diff_path, out_dir),
            **{key: _artifact_binding(path, out_dir) for key, path in paths.items()},
        },
    }
    report = verify_native_resulting_process_gates(compiled, native)
    report_path = output_dir / "native-route-resulting-process-dry-run.json"
    _write_json(report_path, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario-rows", type=Path, required=True)
    parser.add_argument("--scenario-binding", type=Path, required=True)
    parser.add_argument("--shell-report", type=Path, required=True)
    parser.add_argument("--shell-report-binding", type=Path, required=True)
    parser.add_argument("--shell-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--tiangong-package", default=TIANGONG_PACKAGE)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = execute_resulting_process_dry_run(
        args.scenario_rows,
        args.scenario_binding,
        args.shell_report,
        args.shell_report_binding,
        args.shell_root,
        args.out_dir,
        args.tiangong_package,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
