"""Compile and verify route-local TianGong lifecyclemodel assembly plans."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple


JsonObject = Dict[str, object]
BRIDGE_CONTRACT = "lca_research_kernel.native_route_lifecyclemodel_bridge/v1"
ASSEMBLY_PLAN_CONTRACT = (
    "lca_research_kernel.native_route_lifecyclemodel_assembly_plan/v1"
)
READINESS_CONTRACT = "lca_research_kernel.current_comparison_readiness_gate/v1"
TIANGONG_PACKAGE = "@tiangong-lca/cli@0.0.25"
EXPECTED_ROUTES = {"hydrothermal", "solid-state"}
SCIENTIFIC_USE = (
    "uncoated_route_core_screening_proxy_only_no_comparative_lcia"
)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash(value: object) -> str:
    return _hash_bytes(_json_bytes(value))


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _required_object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _required_list(value: object, label: str) -> List[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    return value


def _binding(value: object, label: str) -> JsonObject:
    row = _required_object(value, label)
    path = _required_text(row.get("path"), f"{label}.path")
    sha256 = _required_text(row.get("sha256"), f"{label}.sha256")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError(f"{label}.sha256 must be a lowercase sha256")
    return {"path": path, "sha256": sha256}


def _localized_text(value: object, label: str) -> str:
    candidates = value if isinstance(value, list) else [value]
    for candidate in candidates:
        if isinstance(candidate, dict):
            text = candidate.get("#text")
            if isinstance(text, str) and text.strip():
                return text.strip()
        elif isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    raise ValueError(f"{label} must contain localized text")


def _route_id(name: JsonObject) -> str:
    pieces = []
    for key in ("baseName", "treatmentStandardsRoutes"):
        try:
            pieces.append(_localized_text(name.get(key), f"process name {key}"))
        except ValueError:
            continue
    normalized = " ".join(pieces).lower().replace("_", "-")
    if "hydrothermal" in normalized and "solid-state" not in normalized:
        return "hydrothermal"
    if (
        ("solid-state" in normalized or "solid state" in normalized)
        and "hydrothermal" not in normalized
    ):
        return "solid-state"
    raise ValueError("current Process route is not unambiguously hydrothermal or solid-state")


def _exchanges(process: JsonObject) -> List[JsonObject]:
    container = _required_object(process.get("exchanges"), "processDataSet.exchanges")
    raw = container.get("exchange")
    rows = raw if isinstance(raw, list) else [raw]
    result = [row for row in rows if isinstance(row, dict)]
    if not result or len(result) != len(rows):
        raise ValueError("processDataSet.exchanges.exchange must contain objects")
    return result


def _screening_declaration(exchange: JsonObject) -> JsonObject:
    comments = exchange.get("generalComment")
    rows = comments if isinstance(comments, list) else [comments]
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("#text"), str):
            continue
        try:
            parsed = json.loads(row["#text"])
        except json.JSONDecodeError:
            continue
        if (
            isinstance(parsed, dict)
            and parsed.get("scientific_use") == SCIENTIFIC_USE
            and parsed.get("unit") == "kg"
        ):
            return parsed
    raise ValueError("quantitative reference lacks the accepted screening-use declaration")


def extract_current_process_facts(row: JsonObject) -> JsonObject:
    """Extract route/root/reference facts from one current TIDAS Process row."""
    process = _required_object(row.get("processDataSet"), "processDataSet")
    information = _required_object(
        process.get("processInformation"), "processDataSet.processInformation"
    )
    dataset = _required_object(
        information.get("dataSetInformation"),
        "processDataSet.processInformation.dataSetInformation",
    )
    process_id = _required_text(dataset.get("common:UUID"), "Process UUID")
    name = _required_object(dataset.get("name"), "Process name")
    route_id = _route_id(name)
    base_name = _localized_text(name.get("baseName"), "Process baseName")
    functional_unit = _localized_text(
        name.get("functionalUnitFlowProperties"), "Process functional-unit text"
    )
    administrative = _required_object(
        process.get("administrativeInformation"),
        "processDataSet.administrativeInformation",
    )
    publication = _required_object(
        administrative.get("publicationAndOwnership"),
        "processDataSet.administrativeInformation.publicationAndOwnership",
    )
    version = _required_text(
        publication.get("common:dataSetVersion"), "Process version"
    )
    permanent_uri = _required_text(
        publication.get("common:permanentDataSetURI"), "Process permanent URI"
    )
    references = [
        exchange
        for exchange in _exchanges(process)
        if exchange.get("quantitativeReference") is True
    ]
    if len(references) != 1:
        raise ValueError("each route requires exactly one quantitative-reference exchange")
    reference = references[0]
    flow = _required_object(
        reference.get("referenceToFlowDataSet"),
        "quantitative reference Flow pointer",
    )
    mean_amount = _required_text(reference.get("meanAmount"), "reference meanAmount")
    resulting_amount = _required_text(
        reference.get("resultingAmount"), "reference resultingAmount"
    )
    direction = _required_text(
        reference.get("exchangeDirection"), "reference exchangeDirection"
    )
    if mean_amount != "1" or resulting_amount != "1" or direction != "Output":
        raise ValueError("current route must preserve the 1 kg screening reference Output")
    declaration = _screening_declaration(reference)
    return {
        "route_id": route_id,
        "process_id": process_id,
        "process_version": version,
        "permanent_uri": permanent_uri,
        "name": base_name,
        "functional_unit": functional_unit,
        "reference": {
            "flow_id": _required_text(flow.get("@refObjectId"), "reference Flow UUID"),
            "flow_version": _required_text(
                flow.get("@version"), "reference Flow version"
            ),
            "mean_amount": mean_amount,
            "resulting_amount": resulting_amount,
            "direction": direction,
            "scientific_use": declaration["scientific_use"],
            "unit": declaration["unit"],
        },
    }


def _readiness_snapshot(readiness: JsonObject) -> JsonObject:
    if (
        readiness.get("contract") != READINESS_CONTRACT
        or readiness.get("status")
        != "current_comparison_readiness_gate_compiled"
        or readiness.get("overall_status")
        != "local_native_lifecyclemodel_planning_ready_comparative_science_closed"
        or readiness.get("check_count") != 12
        or readiness.get("satisfied_check_count") != 2
        or readiness.get("ready_internal_activity_count") != 1
        or readiness.get("blocked_check_count") != 9
        or readiness.get("blocked_scientific_gap_count") != 5
        or readiness.get("scientific_comparison_ready") is not False
        or readiness.get("comparative_lcia_allowed") is not False
        or readiness.get("routing", {}).get("next_internal_route")
        != "plan-native-route-lifecyclemodel-assembly"
        or readiness.get("routing", {}).get("pause_internal_work") is not False
    ):
        raise ValueError("accepted current comparison-readiness gate is required")
    checks = _required_list(readiness.get("checks"), "readiness checks")
    blocked_ids = sorted(
        _required_text(row.get("check_id"), "blocked check id")
        for row in checks
        if isinstance(row, dict) and row.get("status") == "blocked"
    )
    ready_ids = sorted(
        _required_text(row.get("check_id"), "ready check id")
        for row in checks
        if isinstance(row, dict) and row.get("status") == "ready_internal_activity"
    )
    if len(blocked_ids) != 9 or ready_ids != ["native-lifecyclemodel-assembly"]:
        raise ValueError("readiness check identities or statuses drifted")
    return {
        "gate_id": _required_text(readiness.get("gate_id"), "readiness gate_id"),
        "blocked_check_ids": blocked_ids,
        "blocked_check_count": 9,
        "blocked_scientific_gap_count": 5,
        "unresolved_field_count": readiness.get("unresolved_field_count"),
        "unresolved_claim_count": readiness.get("unresolved_claim_count"),
        "prohibited_claim_count": readiness.get("prohibited_claim_count"),
    }


def _request_for_route(
    scope_id: str,
    revision: str,
    facts: JsonObject,
    row: JsonObject,
    row_sha256: str,
    route_row_path: str,
) -> JsonObject:
    route_id = str(facts["route_id"])
    process_id = str(facts["process_id"])
    process_version = str(facts["process_version"])
    candidate = {
        "id": process_id,
        "version": process_version,
        "score": 1,
        "route_id": route_id,
        "dataset_sha256": row_sha256,
    }
    process_envelope = {
        "id": process_id,
        "version": process_version,
        "route_id": route_id,
        "name": facts["name"],
        "source_row_sha256": row_sha256,
        "source_row_path": route_row_path,
        "reference": facts["reference"],
        "tidas_row": row,
    }
    request_id = (
        f"{scope_id}-{revision}-{route_id}-route-model-plan-{row_sha256[:12]}"
    )
    return {
        "request_id": request_id,
        "goal": {
            "name": f"Plan {route_id} route-local lifecyclemodel boundary",
            "scope_id": scope_id,
            "revision": revision,
            "route_id": route_id,
            "functional_unit": facts["functional_unit"],
            "scientific_use": facts["reference"]["scientific_use"],
        },
        "root": {
            "kind": "process",
            "process": process_envelope,
            "requested_action": "reuse_existing_process",
            "existing_process_candidates": [candidate],
        },
        "orchestration": {
            "mode": "collapsed",
            "max_depth": 0,
            "reuse_resulting_process_first": False,
            "allow_process_build": False,
            "allow_submodel_build": False,
            "pin_child_versions": True,
            "stop_at_elementary_flow": True,
        },
        "candidate_sources": {
            "my_processes": False,
            "team_processes": False,
            "public_processes": False,
            "existing_lifecyclemodels": False,
            "existing_resulting_processes": False,
        },
        "publish": {
            "intent": "dry_run",
            "prepare_lifecyclemodel_payload": False,
            "prepare_resulting_process_payload": False,
            "prepare_relation_payload": False,
        },
        "nodes": [],
        "edges": [],
        "notes": [
            "Exact current native-valid Process row; structural planning only.",
            "No Process/submodel build, comparison, LCIA, remote lookup or save.",
        ],
    }


def compile_route_bridge(
    scenario_rows: bytes,
    scenario_binding: JsonObject,
    readiness: JsonObject,
    readiness_binding: JsonObject,
    tool_identity: str = TIANGONG_PACKAGE,
) -> JsonObject:
    """Compile deterministic route-local native orchestration requests."""

    scenario_ref = _binding(scenario_binding, "scenario binding")
    if _hash_bytes(scenario_rows) != scenario_ref["sha256"]:
        raise ValueError("scenario rows do not match their artifact binding")
    readiness_ref = _binding(readiness_binding, "readiness binding")
    readiness_state = _readiness_snapshot(readiness)
    if tool_identity != TIANGONG_PACKAGE:
        raise ValueError("pinned TianGong CLI 0.0.25 is required")
    scope_id = _required_text(readiness.get("scope_id"), "readiness scope_id")
    revision = _required_text(readiness.get("revision"), "readiness revision")
    raw_lines = [line for line in scenario_rows.splitlines(keepends=True) if line.strip()]
    if len(raw_lines) != 2:
        raise ValueError("exactly two current route Process rows are required")
    routes = []
    for raw_line in raw_lines:
        try:
            row = json.loads(raw_line)
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("current route Process rows must be valid JSONL") from error
        if not isinstance(row, dict):
            raise ValueError("each current route Process row must be a JSON object")
        facts = extract_current_process_facts(row)
        route_id = str(facts["route_id"])
        row_sha256 = _hash_bytes(raw_line)
        row_path = f"routes/{route_id}/input/process-row.json"
        request_path = f"routes/{route_id}/input/orchestrate-request.json"
        request = _request_for_route(
            scope_id, revision, facts, row, row_sha256, row_path
        )
        routes.append(
            {
                **facts,
                "route_row_path": row_path,
                "route_row_sha256": row_sha256,
                "request_path": request_path,
                "request_sha256": _hash(request),
                "tidas_row": row,
                "request": request,
                "_route_row_bytes": raw_line,
            }
        )
    routes.sort(key=lambda row: str(row["route_id"]))
    if {str(row["route_id"]) for row in routes} != EXPECTED_ROUTES:
        raise ValueError("two current route Process rows must be hydrothermal and solid-state")
    if len({str(row["process_id"]) for row in routes}) != 2:
        raise ValueError("route Process identities must be distinct")
    identity = {
        "contract": BRIDGE_CONTRACT,
        "scope_id": scope_id,
        "revision": revision,
        "tool_identity": tool_identity,
        "scenario_binding": scenario_ref,
        "readiness_binding": readiness_ref,
        "gate_id": readiness_state["gate_id"],
        "routes": [
            {
                "route_id": row["route_id"],
                "process_id": row["process_id"],
                "process_version": row["process_version"],
                "route_row_sha256": row["route_row_sha256"],
                "request_sha256": row["request_sha256"],
                "reference": row["reference"],
            }
            for row in routes
        ],
    }
    return {
        "schema_version": 1,
        "contract": BRIDGE_CONTRACT,
        "scope_id": scope_id,
        "revision": revision,
        "status": "native_route_lifecyclemodel_bridge_compiled",
        "bridge_version": _hash(identity)[:16],
        "tool_identity": tool_identity,
        "native_action": "lifecyclemodel orchestrate plan",
        "source_bindings": {
            "current_residual_scenarios": scenario_ref,
            "current_comparison_readiness": readiness_ref,
        },
        "readiness_snapshot": readiness_state,
        "route_count": len(routes),
        "routes": routes,
        "local_only": True,
        "process_build_allowed": False,
        "lifecyclemodel_build_allowed": False,
        "comparison_allowed": False,
        "lcia_allowed": False,
        "remote_write_allowed": False,
    }


def _single(rows: object, label: str) -> JsonObject:
    values = _required_list(rows, label)
    if len(values) != 1 or not isinstance(values[0], dict):
        raise ValueError(f"{label} must contain exactly one object")
    return values[0]


def _artifact_binding(
    route_id: str,
    key: str,
    document: JsonObject,
    bindings: Mapping[str, object],
) -> JsonObject:
    if key in bindings:
        return _binding(bindings[key], f"{route_id} {key} binding")
    return {
        "path": f"routes/{route_id}/native/{key.replace('_', '-')}.json",
        "sha256": _hash(document),
    }


def _verify_route_native(route: JsonObject, bundle: JsonObject) -> JsonObject:
    route_id = str(route["route_id"])
    request = _required_object(route.get("request"), f"{route_id} request")
    process_id = str(route["process_id"])
    process_version = str(route["process_version"])
    normalized = _required_object(
        bundle.get("request_normalized"), f"{route_id} normalized request"
    )
    assembly = _required_object(
        bundle.get("assembly_plan"), f"{route_id} assembly plan"
    )
    graph = _required_object(
        bundle.get("graph_manifest"), f"{route_id} graph manifest"
    )
    lineage = _required_object(
        bundle.get("lineage_manifest"), f"{route_id} lineage manifest"
    )
    boundary = _required_object(
        bundle.get("boundary_report"), f"{route_id} boundary report"
    )
    request_id = str(request["request_id"])
    expected_root = request["root"]
    if (
        normalized.get("request_id") != request_id
        or normalized.get("root") != expected_root
        or normalized.get("orchestration") != request["orchestration"]
        or normalized.get("candidate_sources") != request["candidate_sources"]
        or normalized.get("publish") != request["publish"]
    ):
        raise ValueError(f"{route_id} normalized native request drifted")
    if (
        assembly.get("request_id") != request_id
        or assembly.get("root") != expected_root
        or assembly.get("orchestration") != request["orchestration"]
        or assembly.get("publish") != request["publish"]
    ):
        raise ValueError(f"{route_id} assembly root or local-only policy drifted")
    summary = _required_object(assembly.get("summary"), f"{route_id} plan summary")
    if summary.get("invocation_count") != 0:
        raise ValueError(f"{route_id} plan must retain zero native invocation")
    if (
        summary.get("node_count") != 1
        or summary.get("edge_count") != 0
        or summary.get("unresolved_count") != 0
        or assembly.get("invocations") != []
        or assembly.get("edges") != []
        or assembly.get("unresolved") != []
    ):
        raise ValueError(f"{route_id} native plan shape is not the one-node boundary")
    node = _single(assembly.get("nodes"), f"{route_id} assembly nodes")
    selected = _required_object(
        node.get("selected_candidate"), f"{route_id} selected Process candidate"
    )
    if selected.get("id") != process_id or selected.get("version") != process_version:
        raise ValueError(f"{route_id} selected Process candidate drifted")
    if (
        selected.get("dataset_sha256") != route["route_row_sha256"]
        or node.get("node_id") != process_id
        or node.get("resolution") != "reused_existing_process"
        or node.get("boundary_reason") != "collapsed_at_existing_process"
    ):
        raise ValueError(f"{route_id} Process resolution or content binding drifted")
    boundary_row = _single(
        assembly.get("boundaries"), f"{route_id} assembly boundaries"
    )
    if boundary_row != {
        "node_id": process_id,
        "reason": "collapsed_at_existing_process",
    }:
        raise ValueError(f"{route_id} assembly boundary drifted")
    graph_node = _single(graph.get("nodes"), f"{route_id} graph nodes")
    graph_stats = _required_object(graph.get("stats"), f"{route_id} graph stats")
    if (
        graph.get("edges") != []
        or graph.get("unresolved") != []
        or graph_node.get("node_id") != process_id
        or graph_node.get("resolution") != "reused_existing_process"
        or graph_node.get("execution_status") != "reused"
        or graph_node.get("boundary_reason") != "collapsed_at_existing_process"
        or graph_stats.get("node_count") != 1
        or graph_stats.get("edge_count") != 0
        or graph_stats.get("invocation_count") != 0
        or graph_stats.get("unresolved_count") != 0
    ):
        raise ValueError(f"{route_id} graph manifest drifted")
    lineage_root = _required_object(
        lineage.get("root_request"), f"{route_id} lineage root"
    )
    lineage_node = _single(
        lineage.get("node_resolution_log"), f"{route_id} lineage resolution"
    )
    if (
        lineage_root.get("request_id") != request_id
        or lineage_root.get("root") != expected_root
        or lineage.get("builder_invocations") != []
        or lineage.get("unresolved_history") != []
        or lineage_node.get("node_id") != process_id
        or lineage_node.get("resolution") != "reused_existing_process"
        or lineage_node.get("execution_status") != "reused"
    ):
        raise ValueError(f"{route_id} lineage manifest drifted")
    boundary_report_row = _single(
        boundary.get("boundaries"), f"{route_id} boundary report rows"
    )
    execution_summary = _required_object(
        boundary.get("execution_summary"), f"{route_id} boundary execution summary"
    )
    if (
        boundary.get("request_id") != request_id
        or boundary_report_row != boundary_row
        or boundary.get("unresolved") != []
        or any(execution_summary.get(key) != 0 for key in (
            "successful_invocations", "failed_invocations", "blocked_invocations"
        ))
    ):
        raise ValueError(f"{route_id} native boundary report drifted")
    bindings = bundle.get("bindings")
    binding_map = bindings if isinstance(bindings, dict) else {}
    artifact_documents = {
        "request_normalized": normalized,
        "assembly_plan": assembly,
        "graph_manifest": graph,
        "lineage_manifest": lineage,
        "boundary_report": boundary,
    }
    artifact_bindings = {
        key: _artifact_binding(route_id, key, document, binding_map)
        for key, document in artifact_documents.items()
    }
    reference = _required_object(route.get("reference"), f"{route_id} reference")
    return {
        "route_id": route_id,
        "process_id": process_id,
        "process_version": process_version,
        "route_row_path": route["route_row_path"],
        "route_row_sha256": route["route_row_sha256"],
        "request_path": route["request_path"],
        "request_sha256": route["request_sha256"],
        "reference_flow_id": reference["flow_id"],
        "reference_flow_version": reference["flow_version"],
        "reference_amount": reference["mean_amount"],
        "reference_resulting_amount": reference["resulting_amount"],
        "reference_direction": reference["direction"],
        "scientific_use": reference["scientific_use"],
        "native_resolution": "reused_existing_process",
        "boundary_reason": "collapsed_at_existing_process",
        "native_summary": {
            "node_count": 1,
            "edge_count": 0,
            "invocation_count": 0,
            "unresolved_count": 0,
        },
        "native_artifacts": artifact_bindings,
    }


def verify_native_route_plans(
    bridge: JsonObject,
    native_routes: Mapping[str, JsonObject],
) -> JsonObject:
    """Fail closed unless both native plans preserve the exact bridge facts."""

    if (
        bridge.get("contract") != BRIDGE_CONTRACT
        or bridge.get("status") != "native_route_lifecyclemodel_bridge_compiled"
        or bridge.get("tool_identity") != TIANGONG_PACKAGE
        or bridge.get("route_count") != 2
        or bridge.get("local_only") is not True
        or bridge.get("process_build_allowed") is not False
        or bridge.get("lifecyclemodel_build_allowed") is not False
        or bridge.get("comparison_allowed") is not False
        or bridge.get("lcia_allowed") is not False
        or bridge.get("remote_write_allowed") is not False
    ):
        raise ValueError("accepted native route bridge is required")
    routes = _required_list(bridge.get("routes"), "bridge routes")
    route_rows = [row for row in routes if isinstance(row, dict)]
    if (
        len(route_rows) != 2
        or {str(row.get("route_id")) for row in route_rows} != EXPECTED_ROUTES
        or set(native_routes) != EXPECTED_ROUTES
    ):
        raise ValueError("native plan bundles must match both bridge routes")
    readiness = _required_object(
        bridge.get("readiness_snapshot"), "bridge readiness snapshot"
    )
    if (
        readiness.get("blocked_check_count") != 9
        or readiness.get("blocked_scientific_gap_count") != 5
        or len(readiness.get("blocked_check_ids", [])) != 9
    ):
        raise ValueError("nine comparison blockers and five gaps must be preserved")
    verified_routes = [
        _verify_route_native(row, native_routes[str(row["route_id"])])
        for row in sorted(route_rows, key=lambda item: str(item["route_id"]))
    ]
    identity = {
        "contract": ASSEMBLY_PLAN_CONTRACT,
        "scope_id": bridge["scope_id"],
        "revision": bridge["revision"],
        "bridge_version": bridge["bridge_version"],
        "routes": verified_routes,
        "blocked_check_ids": readiness["blocked_check_ids"],
    }
    return {
        "schema_version": 1,
        "contract": ASSEMBLY_PLAN_CONTRACT,
        "scope_id": bridge["scope_id"],
        "revision": bridge["revision"],
        "status": "native_route_lifecyclemodel_assembly_plans_compiled",
        "plan_id": _hash(identity),
        "bridge_version": bridge["bridge_version"],
        "tool_identity": bridge["tool_identity"],
        "native_action": "lifecyclemodel orchestrate plan",
        "source_bindings": bridge["source_bindings"],
        "route_count": 2,
        "routes": verified_routes,
        "native_node_count": 2,
        "native_edge_count": 0,
        "native_boundary_count": 2,
        "native_normalized_request_artifact_count": 2,
        "native_plan_artifact_count": 8,
        "native_invocation_count": 0,
        "native_unresolved_count": 0,
        "preserved_blocked_check_count": 9,
        "preserved_blocked_check_ids": readiness["blocked_check_ids"],
        "preserved_scientific_gap_count": 5,
        "preserved_unresolved_field_count": readiness["unresolved_field_count"],
        "preserved_unresolved_claim_count": readiness["unresolved_claim_count"],
        "preserved_prohibited_claim_count": readiness["prohibited_claim_count"],
        "process_build_executed_count": 0,
        "lifecyclemodel_build_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "network_read_executed_count": 0,
        "external_action_executed_count": 0,
        "remote_write_executed_count": 0,
        "model_mutation_executed_count": 0,
        "claim_transition_executed_count": 0,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "routing": {
            "next_internal_route": "materialize-native-route-lifecyclemodel-shells",
            "pause_internal_work": False,
            "reason": (
                "Two exact existing-Process roots now have verified native plans; "
                "materialize local lifecyclemodel shells without comparison or LCIA."
            ),
        },
        "next_action": (
            "Materialize and validate two local native lifecyclemodel shells from the "
            "verified existing-Process roots without comparison, LCIA, or remote save."
        ),
    }


def _read_json(path: Path, label: str) -> JsonObject:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} must be valid JSON") from error
    return _required_object(value, label)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def _public_bridge(bridge: JsonObject) -> JsonObject:
    routes = []
    for source in bridge["routes"]:
        assert isinstance(source, dict)
        routes.append(
            {
                key: value
                for key, value in source.items()
                if key not in {"_route_row_bytes", "tidas_row", "request"}
            }
        )
    return {**bridge, "routes": routes}


def _relative_binding(path: Path, root: Path) -> JsonObject:
    return {
        "path": str(path.relative_to(root)),
        "sha256": _hash_bytes(path.read_bytes()),
    }


def execute_native_plans(
    scenario_path: Path,
    scenario_binding_path: Path,
    readiness_path: Path,
    readiness_binding_path: Path,
    out_dir: Path,
    tool_identity: str = TIANGONG_PACKAGE,
) -> JsonObject:
    """Write requests, run the pinned offline planner, and compile verification."""

    scenario_bytes = scenario_path.read_bytes()
    readiness_bytes = readiness_path.read_bytes()
    scenario_binding = _read_json(scenario_binding_path, "scenario binding")
    readiness_binding = _read_json(readiness_binding_path, "readiness binding")
    if _hash_bytes(readiness_bytes) != readiness_binding.get("sha256"):
        raise ValueError("readiness report does not match its artifact binding")
    readiness = _required_object(json.loads(readiness_bytes), "readiness report")
    bridge = compile_route_bridge(
        scenario_bytes,
        scenario_binding,
        readiness,
        readiness_binding,
        tool_identity,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    native_routes = {}
    for route in bridge["routes"]:
        assert isinstance(route, dict)
        route_id = str(route["route_id"])
        row_path = out_dir / str(route["route_row_path"])
        request_path = out_dir / str(route["request_path"])
        row_path.parent.mkdir(parents=True, exist_ok=True)
        row_path.write_bytes(route["_route_row_bytes"])
        _write_json(request_path, route["request"])
        if _hash_bytes(row_path.read_bytes()) != route["route_row_sha256"]:
            raise RuntimeError(f"{route_id} split Process row hash drifted")
        if _hash_bytes(request_path.read_bytes()) != route["request_sha256"]:
            raise RuntimeError(f"{route_id} native request hash drifted")
        native_dir = out_dir / "routes" / route_id / "native"
        command = [
            "npm",
            "exec",
            "--yes",
            "--offline",
            f"--package={tool_identity}",
            "--",
            "tiangong-lca",
            "lifecyclemodel",
            "orchestrate",
            "plan",
            "--input",
            str(request_path),
            "--out-dir",
            str(native_dir),
            "--json",
        ]
        environment = dict(os.environ)
        environment["npm_config_offline"] = "true"
        completed = subprocess.run(
            command,
            cwd=str(out_dir),
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"Pinned TianGong native plan failed for {route_id}: "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )
        file_names = {
            "request_normalized": "request.normalized.json",
            "assembly_plan": "assembly-plan.json",
            "graph_manifest": "graph-manifest.json",
            "lineage_manifest": "lineage-manifest.json",
            "boundary_report": "boundary-report.json",
        }
        documents = {}
        bindings = {}
        for key, name in file_names.items():
            path = native_dir / name
            documents[key] = _read_json(path, f"{route_id} {key}")
            bindings[key] = _relative_binding(path, out_dir)
        native_routes[route_id] = {**documents, "bindings": bindings}
    bridge_path = out_dir / "outputs" / "bridge-manifest.json"
    _write_json(bridge_path, _public_bridge(bridge))
    report = verify_native_route_plans(bridge, native_routes)
    report["bridge_manifest"] = _relative_binding(bridge_path, out_dir)
    report_path = out_dir / "outputs" / "native-route-lifecyclemodel-assembly-plan.json"
    _write_json(report_path, report)
    return {**report, "report_path": str(report_path)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile and verify two pinned TianGong route-local assembly plans."
    )
    parser.add_argument("--scenario-rows", type=Path, required=True)
    parser.add_argument("--scenario-binding", type=Path, required=True)
    parser.add_argument("--readiness-report", type=Path, required=True)
    parser.add_argument("--readiness-binding", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--tiangong-package", default=TIANGONG_PACKAGE)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = execute_native_plans(
        args.scenario_rows,
        args.scenario_binding,
        args.readiness_report,
        args.readiness_binding,
        args.out_dir,
        args.tiangong_package,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
