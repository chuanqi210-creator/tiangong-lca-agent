"""Compile exact provider-network gaps from bound Process/model evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path, PurePosixPath
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

try:
    from .gap_analysis import SCIENTIFIC_GAP_CONTRACT
except ImportError:  # pragma: no cover - direct activity script execution
    from gap_analysis import SCIENTIFIC_GAP_CONTRACT  # type: ignore


JsonObject = Dict[str, object]
PROVIDER_NETWORK_GAP_CONTRACT = (
    "lca_research_kernel.native_route_provider_network_gap_map/v1"
)
PROVIDER_NETWORK_GAP_LOGIC_VERSION = "2026-07-17.1"
PACKAGE_CONTRACT = "lca_research_kernel.native_route_model_execution_package/v1"
EXPECTED_ROUTES = ("hydrothermal", "solid-state")
CURRENT_PROCESSES_PATH = "datasets/current-processes.jsonl"
LIFECYCLEMODELS_PATH = "datasets/lifecyclemodels.jsonl"


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _jsonl_bytes(rows: Sequence[JsonObject]) -> bytes:
    return b"".join(_json_bytes(row) for row in rows)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _array(value: object, label: str) -> List[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _digest(value: object, label: str) -> str:
    digest = _text(value, label)
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return digest


def _safe_path(value: object, label: str) -> str:
    path = _text(value, label)
    parsed = PurePosixPath(path)
    if (
        parsed.is_absolute()
        or parsed.as_posix() != path
        or not parsed.parts
        or any(part in ("", ".", "..") for part in parsed.parts)
        or "\\" in path
    ):
        raise ValueError(f"{label} must be a safe relative path")
    return path


def _parse_json(content: bytes, label: str) -> JsonObject:
    try:
        return _object(json.loads(content), label)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error


def _parse_jsonl(content: bytes, label: str) -> List[JsonObject]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not UTF-8") from error
    rows: List[JsonObject] = []
    for index, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(_object(json.loads(line), f"{label} row {index}"))
        except json.JSONDecodeError as error:
            raise ValueError(f"{label} row {index} is not valid JSON") from error
    if not rows:
        raise ValueError(f"{label} is empty")
    return rows


def _binding(value: object, content: bytes, label: str) -> JsonObject:
    binding = _object(value, label)
    path = _safe_path(binding.get("path"), f"{label} path")
    digest = _digest(binding.get("sha256"), f"{label} hash")
    if digest != _sha256(content):
        raise ValueError(f"{label} hash does not match content")
    if "bytes" in binding and binding.get("bytes") != len(content):
        raise ValueError(f"{label} byte count does not match content")
    return {"path": path, "sha256": digest, "bytes": len(content)}


def _as_rows(value: object) -> List[JsonObject]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_object(row, "row") for row in value]
    return [_object(value, "row")]


def _first_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        text = value.get("#text")
        return text.strip() if isinstance(text, str) else ""
    if isinstance(value, list):
        for row in value:
            text = _first_text(row)
            if text:
                return text
    return ""


def _dataset_version(root: JsonObject, label: str) -> str:
    administrative = _object(root.get("administrativeInformation"), f"{label} administrative information")
    ownership = _object(
        administrative.get("publicationAndOwnership"), f"{label} publication and ownership"
    )
    return _text(ownership.get("common:dataSetVersion"), f"{label} version")


def _flow_identity(row: JsonObject) -> Tuple[str, str, str, str]:
    root = _object(row.get("flowDataSet"), "Flow dataset")
    information = _object(root.get("flowInformation"), "Flow information")
    dataset = _object(information.get("dataSetInformation"), "Flow dataset information")
    flow_id = _text(dataset.get("common:UUID"), "Flow UUID")
    version = _dataset_version(root, f"Flow {flow_id}")
    name = _first_text(_object(dataset.get("name"), "Flow name").get("baseName"))
    if not name:
        raise ValueError(f"Flow {flow_id} has no base name")
    modelling = root.get("modellingAndValidation")
    lci_method = (
        modelling.get("LCIMethod")
        if isinstance(modelling, dict) and isinstance(modelling.get("LCIMethod"), dict)
        else {}
    )
    flow_type = str(lci_method.get("typeOfDataSet") or "Product flow")
    return flow_id, version, name, flow_type


def _process_identity(row: JsonObject) -> Tuple[JsonObject, str, str, str, str]:
    root = _object(row.get("processDataSet"), "Process dataset")
    information = _object(root.get("processInformation"), "Process information")
    dataset = _object(information.get("dataSetInformation"), "Process dataset information")
    process_id = _text(dataset.get("common:UUID"), "Process UUID")
    version = _dataset_version(root, f"Process {process_id}")
    name = _first_text(_object(dataset.get("name"), "Process name").get("baseName"))
    geography = ""
    geography_root = information.get("geography")
    if isinstance(geography_root, dict):
        location = geography_root.get("locationOfOperationSupplyOrProduction")
        if isinstance(location, dict) and isinstance(location.get("@location"), str):
            geography = location["@location"].strip()
    if not name:
        raise ValueError(f"Process {process_id} has no base name")
    return root, process_id, version, name, geography


def _model_identity(row: JsonObject) -> Tuple[JsonObject, str, str, str]:
    root = _object(row.get("lifeCycleModelDataSet"), "Lifecyclemodel dataset")
    information = _object(
        root.get("lifeCycleModelInformation"), "Lifecyclemodel information"
    )
    dataset = _object(information.get("dataSetInformation"), "Lifecyclemodel dataset information")
    model_id = _text(dataset.get("common:UUID"), "Lifecyclemodel UUID")
    version = _dataset_version(root, f"Lifecyclemodel {model_id}")
    technology = _object(information.get("technology"), f"Lifecyclemodel {model_id} technology")
    processes = _object(technology.get("processes"), f"Lifecyclemodel {model_id} processes")
    instances = _as_rows(processes.get("processInstance"))
    if len(instances) != 1:
        raise ValueError(f"Lifecyclemodel {model_id} must contain exactly one Process instance")
    reference = _object(
        instances[0].get("referenceToProcess"),
        f"Lifecyclemodel {model_id} Process reference",
    )
    process_id = _text(reference.get("@refObjectId"), f"Lifecyclemodel {model_id} Process UUID")
    if reference.get("@version") != "00.00.001":
        raise ValueError(f"Lifecyclemodel {model_id} Process version drifted")
    return root, model_id, version, process_id


def _comment_contract(exchange: JsonObject, label: str) -> JsonObject:
    text = _first_text(exchange.get("generalComment"))
    if not text:
        raise ValueError(f"{label} has no structured comment")
    try:
        return _object(json.loads(text), f"{label} structured comment")
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} structured comment is not JSON") from error


def _exchange_facts(exchange: JsonObject, label: str) -> JsonObject:
    internal_id = _text(exchange.get("@dataSetInternalID"), f"{label} internal ID")
    direction = _text(exchange.get("exchangeDirection"), f"{label} direction")
    if direction not in ("Input", "Output"):
        raise ValueError(f"{label} direction must be Input or Output")
    reference = _object(exchange.get("referenceToFlowDataSet"), f"{label} Flow reference")
    flow_id = _text(reference.get("@refObjectId"), f"{label} Flow UUID")
    version = _text(reference.get("@version"), f"{label} Flow version")
    name = _first_text(reference.get("common:shortDescription"))
    if not name:
        raise ValueError(f"{label} Flow name is missing")
    amount_value = exchange.get("resultingAmount", exchange.get("meanAmount"))
    amount = _text(str(amount_value) if amount_value is not None else None, f"{label} amount")
    comment = _comment_contract(exchange, label)
    unit = _text(comment.get("unit"), f"{label} unit")
    return {
        "internal_id": internal_id,
        "direction": direction,
        "flow_id": flow_id,
        "flow_version": version,
        "flow_name": name,
        "amount": amount,
        "unit": unit,
        "comment": comment,
        "quantitative_reference": exchange.get("quantitativeReference") is True,
    }


def _output_boundary(facts: JsonObject, label: str) -> str:
    comment = _object(facts.get("comment"), f"{label} comment")
    if facts.get("quantitative_reference") is True:
        return "functional_output_boundary"
    if (
        comment.get("contract") == "lca_research_kernel.mass_residual_output/v1"
        and comment.get("composition_status") in (
            "unknown",
            "unknown_not_inferred_from_mass_conservation",
        )
        and comment.get("characterization") in (
            "prohibited",
            "prohibited_until_species_resolved_evidence",
        )
    ):
        return "composition_unknown_output_boundary"
    if comment.get("exchange_type") in ("co_product", "coproduct"):
        return "coproduct_boundary_unallocated"
    raise ValueError(f"{label} Output has no accepted boundary class")


def compile_provider_network_gap_map(
    *,
    package_manifest_bytes: bytes,
    package_manifest_binding: JsonObject,
    package_files: Mapping[str, bytes],
    flow_registry_bytes: bytes,
    flow_registry_binding: JsonObject,
    flow_source_bindings: Sequence[JsonObject],
    scientific_gap_bytes: bytes,
    scientific_gap_binding: JsonObject,
    native_graph_report_bytes: bytes,
) -> JsonObject:
    """Compile a deterministic provider-network gap map without resolving providers."""

    manifest_source = _binding(
        package_manifest_binding, package_manifest_bytes, "package manifest binding"
    )
    manifest = _parse_json(package_manifest_bytes, "package manifest")
    file_bindings = _object(manifest.get("file_bindings"), "package file bindings")
    routes_raw = _array(manifest.get("routes"), "package routes")
    if (
        manifest.get("contract") != PACKAGE_CONTRACT
        or manifest.get("status") != "native_route_model_execution_package_verified"
        or manifest.get("tool_identity") != "@tiangong-lca/cli@0.0.25"
        or manifest.get("route_count") != 2
        or manifest.get("file_count") != 20
        or len(file_bindings) != 20
        or manifest.get("blocked_check_count") != 9
        or any(manifest.get(key) is not False for key in ("comparison_allowed", "lcia_allowed", "publication_allowed", "remote_write_allowed"))
    ):
        raise ValueError("package manifest contract, shape or zero-effect boundary drifted")
    if set(package_files) != {CURRENT_PROCESSES_PATH, LIFECYCLEMODELS_PATH}:
        raise ValueError("provider gap compiler requires exactly current Process and lifecyclemodel package files")
    verified_package_files: Dict[str, JsonObject] = {}
    for path, content in sorted(package_files.items()):
        binding = _object(file_bindings.get(path), f"package binding {path}")
        if binding.get("sha256") != _sha256(content) or binding.get("bytes") != len(content):
            raise ValueError(f"package file hash or size drifted: {path}")
        verified_package_files[path] = {
            "path": path,
            "sha256": binding["sha256"],
            "bytes": binding["bytes"],
        }

    flow_registry_source = _binding(
        flow_registry_binding, flow_registry_bytes, "Flow registry binding"
    )
    flow_rows = _parse_jsonl(flow_registry_bytes, "Flow registry")
    flows: Dict[Tuple[str, str], JsonObject] = {}
    for row in flow_rows:
        flow_id, version, name, flow_type = _flow_identity(row)
        key = (flow_id, version)
        if key in flows:
            raise ValueError(f"Flow registry contains duplicate identity: {flow_id}@{version}")
        flows[key] = {
            "flow_id": flow_id,
            "version": version,
            "name": name,
            "flow_type": flow_type,
        }
    source_by_key: Dict[Tuple[str, str], JsonObject] = {}
    for raw in flow_source_bindings:
        source = _object(raw, "Flow source binding")
        flow_id = _text(source.get("flow_id"), "Flow source UUID")
        version = _text(source.get("version"), "Flow source version")
        key = (flow_id, version)
        if key in source_by_key:
            raise ValueError(f"Flow source binding is duplicated: {flow_id}@{version}")
        source_by_key[key] = {
            "flow_id": flow_id,
            "version": version,
            "path": _safe_path(source.get("path"), "Flow source path"),
            "sha256": _digest(source.get("sha256"), "Flow source hash"),
        }
    if set(source_by_key) != set(flows):
        raise ValueError("Flow registry rows and source bindings do not match")

    scientific_gap_source = _binding(
        scientific_gap_binding, scientific_gap_bytes, "scientific gap binding"
    )
    scientific_gaps = _parse_json(scientific_gap_bytes, "scientific gap register")
    blocked = _array(scientific_gaps.get("blocked_high_impact"), "blocked scientific gaps")
    emission_gap = next(
        (
            _object(row, "elementary emission gap")
            for row in blocked
            if isinstance(row, dict)
            and row.get("gap_id") == "elementary-emissions-characterization"
        ),
        None,
    )
    if (
        scientific_gaps.get("contract") != SCIENTIFIC_GAP_CONTRACT
        or scientific_gaps.get("scientific_comparison_ready") is not False
        or emission_gap is None
        or not isinstance(emission_gap.get("blockers"), list)
        or not emission_gap["blockers"]
    ):
        raise ValueError("elementary-emissions scientific gap binding drifted")

    route_map: Dict[str, JsonObject] = {}
    for raw in routes_raw:
        route = _object(raw, "package route")
        route_id = _text(route.get("route_id"), "route ID")
        if route_id in route_map:
            raise ValueError("package route ID is duplicated")
        route_map[route_id] = route
    if set(route_map) != set(EXPECTED_ROUTES):
        raise ValueError("package route set drifted")

    process_rows = _parse_jsonl(package_files[CURRENT_PROCESSES_PATH], "current Processes")
    processes: Dict[str, JsonObject] = {}
    for row in process_rows:
        root, process_id, version, name, geography = _process_identity(row)
        if process_id in processes:
            raise ValueError(f"current Process is duplicated: {process_id}")
        exchanges_root = _object(root.get("exchanges"), f"Process {process_id} exchanges")
        exchanges = _as_rows(exchanges_root.get("exchange"))
        if not exchanges:
            raise ValueError(f"Process {process_id} has no exchanges")
        processes[process_id] = {
            "process_id": process_id,
            "version": version,
            "name": name,
            "geography": geography,
            "exchanges": exchanges,
        }
    model_rows = _parse_jsonl(package_files[LIFECYCLEMODELS_PATH], "lifecyclemodels")
    models: Dict[str, JsonObject] = {}
    for row in model_rows:
        _, model_id, version, process_id = _model_identity(row)
        if model_id in models:
            raise ValueError(f"lifecyclemodel is duplicated: {model_id}")
        models[model_id] = {
            "model_id": model_id,
            "version": version,
            "process_id": process_id,
        }
    if len(processes) != 2 or len(models) != 2:
        raise ValueError("provider gap map requires exactly two Processes and models")

    for route_id, route in route_map.items():
        process_id = _text(route.get("source_process_id"), f"{route_id} source Process")
        model_id = _text(route.get("model_id"), f"{route_id} model")
        if process_id not in processes or model_id not in models:
            raise ValueError(f"{route_id} route Process/model binding is missing")
        if models[model_id]["process_id"] != process_id:
            raise ValueError(f"{route_id} lifecyclemodel Process binding drifted")

    graph_report = _parse_json(native_graph_report_bytes, "native graph report")
    graph_counts = _object(graph_report.get("counts"), "native graph counts")
    graph_models = _array(graph_report.get("models"), "native graph models")
    normalized_graph_models = []
    for raw in graph_models:
        model = _object(raw, "native graph model")
        normalized_graph_models.append(
            {
                "id": _text(model.get("id"), "native graph model ID"),
                "version": _text(model.get("version"), "native graph model version"),
                "node_count": model.get("node_count"),
                "edge_count": model.get("edge_count"),
                "finding_count": model.get("finding_count"),
            }
        )
    normalized_graph_models.sort(key=lambda row: str(row["id"]))
    if (
        graph_report.get("status") != "completed"
        or graph_report.get("format") != "json"
        or graph_report.get("check_connections") is not True
        or graph_counts != {"models": 2, "nodes": 2, "edges": 0, "findings": 0}
        or len(normalized_graph_models) != 2
        or {row["id"] for row in normalized_graph_models} != set(models)
        or any(
            row["version"] != models[str(row["id"])]["version"]
            or row["node_count"] != 1
            or row["edge_count"] != 0
            or row["finding_count"] != 0
            for row in normalized_graph_models
        )
        or graph_report.get("findings") != []
    ):
        raise ValueError("native graph connection evidence drifted")

    route_by_process = {
        str(route["source_process_id"]): route_id for route_id, route in route_map.items()
    }
    output_providers: Dict[Tuple[str, str], List[str]] = {}
    exchange_rows: List[JsonObject] = []
    for process_id, process in sorted(processes.items()):
        route_id = route_by_process.get(process_id)
        if route_id is None:
            raise ValueError(f"Process {process_id} is not bound to a route")
        for index, exchange in enumerate(process["exchanges"]):  # type: ignore[index]
            facts = _exchange_facts(
                _object(exchange, f"{route_id} exchange {index + 1}"),
                f"{route_id} exchange {index + 1}",
            )
            key = (str(facts["flow_id"]), str(facts["flow_version"]))
            flow = flows.get(key)
            if flow is None:
                raise ValueError(
                    f"exchange Flow is missing from registry: {facts['flow_id']}@{facts['flow_version']}"
                )
            if flow["name"] != facts["flow_name"]:
                raise ValueError(f"exchange Flow name drifted: {facts['flow_id']}")
            row = {
                **facts,
                "route_id": route_id,
                "process_id": process_id,
                "process_version": process["version"],
                "consumer_geography": process["geography"],
                "flow_type": flow["flow_type"],
            }
            exchange_rows.append(row)
            if facts["direction"] == "Output":
                output_providers.setdefault(key, []).append(process_id)

    provider_requirements: List[JsonObject] = []
    network_edges: List[JsonObject] = []
    output_boundaries: List[JsonObject] = []
    provider_match_count = 0
    elementary_exchange_count = 0
    for row in sorted(
        exchange_rows,
        key=lambda value: (
            str(value["route_id"]),
            int(str(value["internal_id"])),
        ),
    ):
        if row["flow_type"] == "Elementary flow":
            elementary_exchange_count += 1
        edge_id = f"exchange:{row['route_id']}:{row['internal_id']}"
        flow_node = f"flow:{row['flow_id']}:{row['flow_version']}"
        process_node = f"process:{row['process_id']}:{row['process_version']}"
        if row["direction"] == "Input":
            candidates = sorted(
                process_id
                for process_id in output_providers.get(
                    (str(row["flow_id"]), str(row["flow_version"])), []
                )
                if process_id != row["process_id"]
            )
            if len(candidates) > 1:
                raise ValueError(f"ambiguous local provider Processes for {row['flow_id']}")
            status = (
                "provider_process_matched" if candidates else "provider_process_missing"
            )
            if candidates:
                provider_match_count += 1
            else:
                requirement_seed = {
                    "route_id": row["route_id"],
                    "consumer_process_id": row["process_id"],
                    "exchange_internal_id": row["internal_id"],
                    "flow_id": row["flow_id"],
                    "flow_version": row["flow_version"],
                }
                requirement_id = (
                    f"provider-requirement:{row['route_id']}:"
                    f"{_sha256(_json_bytes(requirement_seed))[:16]}"
                )
                provider_requirements.append(
                    {
                        "requirement_id": requirement_id,
                        "status": status,
                        "route_id": row["route_id"],
                        "consumer_process_id": row["process_id"],
                        "consumer_process_version": row["process_version"],
                        "consumer_geography": row["consumer_geography"],
                        "exchange_internal_id": row["internal_id"],
                        "flow_id": row["flow_id"],
                        "flow_version": row["flow_version"],
                        "flow_name": row["flow_name"],
                        "flow_type": row["flow_type"],
                        "amount": row["amount"],
                        "unit": row["unit"],
                        "local_flow_bound": True,
                        "matched_provider_process_ids": [],
                        "supplier_geography_inferred": False,
                        "resolution_blockers": [
                            "provider_process_identity_not_defined",
                            "source_bound_provider_candidate_missing",
                        ],
                        "native_identity_preflight_request": {
                            "target": {
                                "reference_flow_id": row["flow_id"],
                                "reference_flow_name": row["flow_name"],
                                "provider_role": "background_provider_for_bound_input_exchange",
                            },
                            "candidates": [],
                        },
                    }
                )
            network_edges.append(
                {
                    "edge_id": edge_id,
                    "kind": "process_input_exchange",
                    "source": flow_node,
                    "target": process_node,
                    "route_id": row["route_id"],
                    "exchange_internal_id": row["internal_id"],
                    "amount": row["amount"],
                    "unit": row["unit"],
                    "provider_status": status,
                    "matched_provider_process_ids": candidates,
                }
            )
        else:
            boundary = _output_boundary(row, edge_id)
            output_boundaries.append(
                {
                    "edge_id": edge_id,
                    "route_id": row["route_id"],
                    "process_id": row["process_id"],
                    "exchange_internal_id": row["internal_id"],
                    "flow_id": row["flow_id"],
                    "flow_version": row["flow_version"],
                    "flow_name": row["flow_name"],
                    "amount": row["amount"],
                    "unit": row["unit"],
                    "boundary_class": boundary,
                }
            )
            network_edges.append(
                {
                    "edge_id": edge_id,
                    "kind": "process_output_exchange",
                    "source": process_node,
                    "target": flow_node,
                    "route_id": row["route_id"],
                    "exchange_internal_id": row["internal_id"],
                    "amount": row["amount"],
                    "unit": row["unit"],
                    "boundary_class": boundary,
                }
            )

    provider_requirements.sort(key=lambda row: str(row["requirement_id"]))
    output_boundaries.sort(key=lambda row: str(row["edge_id"]))
    network_nodes: List[JsonObject] = []
    for route_id in sorted(route_map):
        route = route_map[route_id]
        model_id = str(route["model_id"])
        process_id = str(route["source_process_id"])
        model = models[model_id]
        process = processes[process_id]
        network_nodes.extend(
            [
                {
                    "node_id": f"lifecyclemodel:{model_id}:{model['version']}",
                    "kind": "lifecyclemodel",
                    "route_id": route_id,
                    "dataset_id": model_id,
                    "version": model["version"],
                },
                {
                    "node_id": f"process:{process_id}:{process['version']}",
                    "kind": "process",
                    "route_id": route_id,
                    "dataset_id": process_id,
                    "version": process["version"],
                    "name": process["name"],
                    "geography": process["geography"],
                },
            ]
        )
        network_edges.append(
            {
                "edge_id": f"model-process:{route_id}",
                "kind": "lifecyclemodel_contains_process",
                "source": f"lifecyclemodel:{model_id}:{model['version']}",
                "target": f"process:{process_id}:{process['version']}",
                "route_id": route_id,
                "native_declared_connection": False,
            }
        )
    for (flow_id, version), flow in sorted(flows.items()):
        network_nodes.append(
            {
                "node_id": f"flow:{flow_id}:{version}",
                "kind": "flow",
                "dataset_id": flow_id,
                "version": version,
                "name": flow["name"],
                "flow_type": flow["flow_type"],
                "source_binding": source_by_key[(flow_id, version)],
            }
        )
    network_nodes.sort(key=lambda row: str(row["node_id"]))
    network_edges.sort(key=lambda row: str(row["edge_id"]))

    route_summaries: List[JsonObject] = []
    for route_id in sorted(route_map):
        route_exchanges = [row for row in exchange_rows if row["route_id"] == route_id]
        requirements = [
            row for row in provider_requirements if row["route_id"] == route_id
        ]
        boundaries = [row for row in output_boundaries if row["route_id"] == route_id]
        route_summaries.append(
            {
                "route_id": route_id,
                "process_id": route_map[route_id]["source_process_id"],
                "model_id": route_map[route_id]["model_id"],
                "exchange_count": len(route_exchanges),
                "input_exchange_count": sum(
                    row["direction"] == "Input" for row in route_exchanges
                ),
                "output_exchange_count": sum(
                    row["direction"] == "Output" for row in route_exchanges
                ),
                "provider_requirement_count": len(requirements),
                "provider_process_match_count": sum(
                    edge.get("provider_status") == "provider_process_matched"
                    for edge in network_edges
                    if edge.get("route_id") == route_id
                ),
                "output_boundaries": boundaries,
                "elementary_emissions_gap_retained": True,
            }
        )

    functional_count = sum(
        row["boundary_class"] == "functional_output_boundary"
        for row in output_boundaries
    )
    coproduct_count = sum(
        row["boundary_class"] == "coproduct_boundary_unallocated"
        for row in output_boundaries
    )
    residual_count = sum(
        row["boundary_class"] == "composition_unknown_output_boundary"
        for row in output_boundaries
    )
    input_count = sum(row["direction"] == "Input" for row in exchange_rows)
    provider_batch = {
        "schema_version": 1,
        "contract": "lca_research_kernel.native_provider_resolution_batch/v1",
        "status": "provider_resolution_batch_compiled",
        "requirement_count": len(provider_requirements),
        "requests": [
            {
                "requirement_id": row["requirement_id"],
                "route_id": row["route_id"],
                "request": row["native_identity_preflight_request"],
                "acceptance": {
                    "candidate_must_output_flow_id": row["flow_id"],
                    "candidate_must_output_flow_version": row["flow_version"],
                    "identity_similarity_alone_is_sufficient": False,
                    "model_connection_mutation_allowed": False,
                },
            }
            for row in provider_requirements
        ],
        "remote_read_allowed_next_activity": True,
        "remote_write_allowed": False,
        "model_mutation_allowed": False,
    }
    normalized_native_graph = {
        "status": graph_report["status"],
        "format": graph_report["format"],
        "check_connections": True,
        "counts": graph_counts,
        "models": normalized_graph_models,
    }
    map_identity = {
        "contract": PROVIDER_NETWORK_GAP_CONTRACT,
        "logic_version": PROVIDER_NETWORK_GAP_LOGIC_VERSION,
        "package_manifest_sha256": manifest_source["sha256"],
        "package_files": verified_package_files,
        "flow_registry_sha256": flow_registry_source["sha256"],
        "flow_sources": list(source_by_key.values()),
        "scientific_gap_sha256": scientific_gap_source["sha256"],
        "native_graph": normalized_native_graph,
        "provider_requirements": provider_requirements,
        "output_boundaries": output_boundaries,
        "network_nodes": network_nodes,
        "network_edges": network_edges,
        "provider_resolution_batch": provider_batch,
    }
    map_version = _sha256(_json_bytes(map_identity))[:16]
    return {
        "schema_version": 1,
        "contract": PROVIDER_NETWORK_GAP_CONTRACT,
        "logic_version": PROVIDER_NETWORK_GAP_LOGIC_VERSION,
        "status": "native_route_provider_network_gap_map_compiled",
        "map_version": map_version,
        "scope_id": manifest["scope_id"],
        "revision": manifest["revision"],
        "package_version": manifest["package_version"],
        "route_count": 2,
        "process_count": 2,
        "lifecyclemodel_count": 2,
        "exchange_count": len(exchange_rows),
        "unique_flow_count": len(flows),
        "local_flow_bound_count": len(flows),
        "input_exchange_count": input_count,
        "output_exchange_count": len(exchange_rows) - input_count,
        "provider_requirement_count": len(provider_requirements),
        "provider_process_match_count": provider_match_count,
        "functional_output_boundary_count": functional_count,
        "coproduct_boundary_count": coproduct_count,
        "composition_unknown_output_count": residual_count,
        "elementary_emission_exchange_count": elementary_exchange_count,
        "elementary_emission_gap_count": 2,
        "network_node_count": len(network_nodes),
        "network_edge_count": len(network_edges),
        "native_graph_node_count": 2,
        "native_declared_edge_count": 0,
        "native_graph_finding_count": 0,
        "declared_connection_integrity": True,
        "provider_network_complete": False,
        "background_provider_coverage": {
            "matched": provider_match_count,
            "required": input_count,
            "missing": len(provider_requirements),
        },
        "native_capability_boundary": {
            "graph_checks_declared_edges_only": True,
            "automatic_provider_inference_available": False,
            "process_identity_preflight_available": True,
            "orchestrator_requires_explicit_candidate_or_build_policy": True,
        },
        "input_bindings": {
            "package_manifest": manifest_source,
            "package_files": verified_package_files,
            "flow_registry": flow_registry_source,
            "flow_sources": list(source_by_key.values()),
            "scientific_gap_register": scientific_gap_source,
        },
        "native_graph_observation": normalized_native_graph,
        "routes": route_summaries,
        "provider_requirements": provider_requirements,
        "output_boundaries": output_boundaries,
        "network_nodes": network_nodes,
        "network_edges": network_edges,
        "provider_resolution_batch": provider_batch,
        "scientific_network_gaps": [
            {
                "route_id": route_id,
                "gap_id": "elementary-emissions-characterization",
                "exchange_count": 0,
                "status": "retained_scientific_gap_not_inferred_from_absence",
                "blockers": emission_gap["blockers"],
            }
            for route_id in sorted(route_map)
        ],
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "network_read_executed_count": 0,
        "remote_write_executed_count": 0,
        "publication_executed_count": 0,
        "current_model_mutation_executed_count": 0,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "next_action": (
            "Resolve bounded native provider candidates for all nine exact input-Flow "
            "requirements, accepting only candidates that output the bound Flow."
        ),
        "routing": {
            "next_internal_route": "resolve-native-provider-candidates",
            "pause_internal_work": False,
            "reason": (
                "All input Flows are locally bound but no provider Process is bound; "
                "candidate discovery can proceed without changing the accepted models."
            ),
        },
    }


def materialize_provider_network_gap_map(
    compiled: JsonObject, out_dir: Path
) -> Dict[str, Path]:
    """Write detailed gap artifacts and a compact hash-bound report."""

    output = Path(out_dir)
    if output.exists() and any(output.iterdir()):
        raise ValueError("provider network gap output directory must be absent or empty")
    output.mkdir(parents=True, exist_ok=True)
    paths = {
        "provider_requirements": output / "provider-requirements.jsonl",
        "network_nodes": output / "network-nodes.jsonl",
        "network_edges": output / "network-edges.jsonl",
        "provider_resolution_batch": output / "provider-resolution-batch.json",
        "report": output / "native-route-provider-network-gap-map.json",
    }
    detail_values = {
        "provider_requirements": compiled["provider_requirements"],
        "network_nodes": compiled["network_nodes"],
        "network_edges": compiled["network_edges"],
    }
    for key, rows in detail_values.items():
        if not isinstance(rows, list):
            raise ValueError(f"compiled {key} must be a list")
        paths[key].write_bytes(_jsonl_bytes(rows))  # type: ignore[arg-type]
    paths["provider_resolution_batch"].write_bytes(
        _json_bytes(compiled["provider_resolution_batch"])
    )
    artifact_bindings = {}
    for key in (
        "provider_requirements",
        "network_nodes",
        "network_edges",
        "provider_resolution_batch",
    ):
        content = paths[key].read_bytes()
        artifact_bindings[key] = {
            "path": paths[key].name,
            "sha256": _sha256(content),
            "bytes": len(content),
        }
    report = {
        key: value
        for key, value in compiled.items()
        if key
        not in {
            "provider_requirements",
            "network_nodes",
            "network_edges",
            "provider_resolution_batch",
        }
    }
    report["artifact_bindings"] = artifact_bindings
    paths["report"].write_bytes(_json_bytes(report))
    return paths


def run_native_lifecyclemodel_graph(
    node_path: Path,
    cli_entry_path: Path,
    lifecyclemodels_path: Path,
    out_dir: Path,
) -> JsonObject:
    """Run the native declared-connection checker through a copied direct runtime."""

    node = Path(node_path).resolve()
    cli = Path(cli_entry_path).resolve()
    models = Path(lifecyclemodels_path).resolve()
    output = Path(out_dir).resolve()
    for path, label in (
        (node, "copied Node executable"),
        (cli, "copied TianGong CLI entry"),
        (models, "lifecyclemodel input"),
    ):
        if not path.is_file():
            raise ValueError(f"{label} is missing")
    command = [
        str(node),
        str(cli),
        "lifecyclemodel",
        "graph",
        "--input",
        str(models),
        "--out-dir",
        str(output),
        "--format",
        "json",
        "--check-connections",
        "--json",
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    report_path = output / "outputs/graph-report.json"
    if not report_path.is_file():
        raise RuntimeError("native lifecyclemodel graph report is missing")
    return _parse_json(report_path.read_bytes(), "native lifecyclemodel graph report")


def _read_json_file(path: Path, label: str) -> JsonObject:
    try:
        return _parse_json(Path(path).read_bytes(), label)
    except OSError as error:
        raise ValueError(f"{label} cannot be read") from error


def run_provider_network_gap_map(
    *,
    package_manifest_path: Path,
    package_manifest_binding_path: Path,
    package_root: Path,
    flow_registry_path: Path,
    flow_registry_binding_path: Path,
    flow_source_bindings_path: Path,
    scientific_gap_path: Path,
    scientific_gap_binding_path: Path,
    native_node_path: Path,
    native_cli_entry_path: Path,
    out_dir: Path,
) -> JsonObject:
    """Run native graph observation, compile the gap map and write artifacts."""

    package_root = Path(package_root).resolve()
    manifest_path = Path(package_manifest_path).resolve()
    try:
        manifest_path.relative_to(package_root)
    except ValueError as error:
        raise ValueError("package manifest escaped the package root") from error
    package_manifest_bytes = manifest_path.read_bytes()
    package_manifest_binding = _read_json_file(
        package_manifest_binding_path, "package manifest binding"
    )
    flow_registry_bytes = Path(flow_registry_path).read_bytes()
    flow_registry_binding = _read_json_file(
        flow_registry_binding_path, "Flow registry binding"
    )
    try:
        raw_sources = json.loads(Path(flow_source_bindings_path).read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Flow source bindings are invalid") from error
    if not isinstance(raw_sources, list):
        raise ValueError("Flow source bindings must be a JSON array")
    scientific_gap_bytes = Path(scientific_gap_path).read_bytes()
    scientific_gap_binding = _read_json_file(
        scientific_gap_binding_path, "scientific gap binding"
    )
    output = Path(out_dir)
    native_graph_dir = output / "native-graph"
    lifecyclemodels_path = package_root / LIFECYCLEMODELS_PATH
    run_native_lifecyclemodel_graph(
        native_node_path,
        native_cli_entry_path,
        lifecyclemodels_path,
        native_graph_dir,
    )
    compiled = compile_provider_network_gap_map(
        package_manifest_bytes=package_manifest_bytes,
        package_manifest_binding=package_manifest_binding,
        package_files={
            CURRENT_PROCESSES_PATH: (package_root / CURRENT_PROCESSES_PATH).read_bytes(),
            LIFECYCLEMODELS_PATH: lifecyclemodels_path.read_bytes(),
        },
        flow_registry_bytes=flow_registry_bytes,
        flow_registry_binding=flow_registry_binding,
        flow_source_bindings=raw_sources,  # type: ignore[arg-type]
        scientific_gap_bytes=scientific_gap_bytes,
        scientific_gap_binding=scientific_gap_binding,
        native_graph_report_bytes=(
            native_graph_dir / "outputs/graph-report.json"
        ).read_bytes(),
    )
    paths = materialize_provider_network_gap_map(compiled, output / "outputs")
    return _read_json_file(paths["report"], "provider network gap report")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compile native route provider-network gaps."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-binding", type=Path, required=True)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--flow-registry", type=Path, required=True)
    parser.add_argument("--flow-registry-binding", type=Path, required=True)
    parser.add_argument("--flow-source-bindings", type=Path, required=True)
    parser.add_argument("--scientific-gap", type=Path, required=True)
    parser.add_argument("--scientific-gap-binding", type=Path, required=True)
    parser.add_argument("--native-node", type=Path, required=True)
    parser.add_argument("--native-cli-entry", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    report = run_provider_network_gap_map(
        package_manifest_path=args.manifest,
        package_manifest_binding_path=args.manifest_binding,
        package_root=args.package_root,
        flow_registry_path=args.flow_registry,
        flow_registry_binding_path=args.flow_registry_binding,
        flow_source_bindings_path=args.flow_source_bindings,
        scientific_gap_path=args.scientific_gap,
        scientific_gap_binding_path=args.scientific_gap_binding,
        native_node_path=args.native_node,
        native_cli_entry_path=args.native_cli_entry,
        out_dir=args.out_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
