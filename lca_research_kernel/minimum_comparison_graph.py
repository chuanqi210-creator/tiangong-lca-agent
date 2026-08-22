"""Compile and verify the minimum computable LiFePO4 comparison graph."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Set, Tuple


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.minimum_lifepo4_comparison_graph/v1"
VERIFICATION_CONTRACT = (
    "lca_research_kernel.minimum_lifepo4_comparison_graph_verification/v1"
)
CANARY_REPORT_SHA256 = (
    "9b78ada850b20307480ccadd8ed835144fe6860382cf893a3a943afc6269da02"
)
FREEZE_SHA256 = "aaf8ee4944083ef384c37d38e749dea9231ee2130c84e4db68af9f28f0706508"
PLAN_SET_VERSION = "80c2f62b344154d7"
EXPECTED_ROUTES = ("solid-state", "hydrothermal")
EXPECTED_FLOW_COUNT = 11
EXPECTED_SOURCE_COUNT = 4
NEXT_ROUTE = "materialize-minimum-lifepo4-comparison-model"
OUTPUT_NAMES = {
    "report": "minimum-lifepo4-comparison-graph.json",
    "scope": "shared-comparison-scope.json",
    "nodes": "minimum-lifepo4-comparison-nodes.jsonl",
    "edges": "minimum-lifepo4-comparison-edges.jsonl",
    "exclusions": "minimum-lifepo4-comparison-exclusions.jsonl",
    "risks": "minimum-lifepo4-comparison-risks.jsonl",
    "verification": "minimum-lifepo4-comparison-graph-verification.json",
}
NODE_TYPES = {
    "Flow",
    "Source",
    "foreground Process",
    "Provider",
    "LCIAMethod",
    "LifecycleModel",
}
ROUTE_ROLES = {"solid-state", "hydrothermal", "shared"}
NECESSITY_CLASSES = {"required", "proxy_scenario", "shared_common"}
EVIDENCE_STATUSES = {
    "materialized_local",
    "accepted_source",
    "required_proxy",
    "selected_method",
    "planned_local",
}
PROVIDER_RESOLUTION_CLASSES = {
    "exact_uslci",
    "local_aggregated_proxy",
    "scenario_bound",
}
DECISIONS = (
    "accepted_freeze_preserved",
    "shared_scope_complete",
    "performance_policy_computed",
    "minimum_graph_closed",
    "provider_gaps_classified",
    "lcia_surface_truthful",
    "side_effect_boundary_preserved",
)


def _json_bytes(value: object, *, pretty: bool = False) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _jsonl_bytes(rows: Iterable[Mapping[str, object]]) -> bytes:
    return b"".join(_json_bytes(dict(row)) for row in rows)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _required_object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return dict(value)


def _required_list(value: object, label: str) -> List[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    return list(value)


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _read_bound(workspace: Path, path_value: object, sha_value: object, label: str) -> Tuple[Path, bytes]:
    relative = Path(_required_text(path_value, f"{label}.path"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{label}.path must stay inside the workspace")
    expected = _required_text(sha_value, f"{label}.sha256")
    path = workspace / relative
    if not path.is_file():
        raise ValueError(f"{label} is missing: {relative}")
    content = path.read_bytes()
    if _sha256_bytes(content) != expected:
        raise ValueError(f"{label} hash drifted")
    return path, content


def _localized_text(value: object) -> str:
    rows = value if isinstance(value, list) else [value]
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("#text"), str):
            text = row["#text"].strip()
            if text:
                return text
        if isinstance(row, str) and row.strip():
            return row.strip()
    raise ValueError("localized text is absent")


def _route_from_process(row: Mapping[str, object]) -> str:
    process = _required_object(row.get("processDataSet"), "processDataSet")
    information = _required_object(process.get("processInformation"), "processInformation")
    dataset = _required_object(information.get("dataSetInformation"), "dataSetInformation")
    name = _required_object(dataset.get("name"), "Process name")
    normalized = _localized_text(name.get("baseName")).lower().replace("_", "-")
    if "solid-state" in normalized or "solid state" in normalized:
        return "solid-state"
    if "hydrothermal" in normalized:
        return "hydrothermal"
    raise ValueError("current Process route is not unambiguous")


def _process_id(row: Mapping[str, object]) -> str:
    process = _required_object(row.get("processDataSet"), "processDataSet")
    information = _required_object(process.get("processInformation"), "processInformation")
    dataset = _required_object(information.get("dataSetInformation"), "dataSetInformation")
    return _required_text(dataset.get("common:UUID"), "Process UUID")


def _exchanges(row: Mapping[str, object]) -> List[JsonObject]:
    process = _required_object(row.get("processDataSet"), "processDataSet")
    container = _required_object(process.get("exchanges"), "processDataSet.exchanges")
    raw = container.get("exchange")
    rows = raw if isinstance(raw, list) else [raw]
    if not rows or any(not isinstance(item, dict) for item in rows):
        raise ValueError("Process exchanges must be non-empty JSON objects")
    return [dict(item) for item in rows]


def _flow_ref(exchange: Mapping[str, object]) -> JsonObject:
    return _required_object(exchange.get("referenceToFlowDataSet"), "Flow reference")


def _accepted_freeze(snapshot: Mapping[str, object], workspace: Path) -> Tuple[JsonObject, List[JsonObject]]:
    state = _required_object(
        snapshot.get("uslci_flow_authoring_corpus_release_canary"),
        "release canary state",
    )
    if state.get("status") != "uslci_flow_authoring_corpus_release_canary_completed":
        raise ValueError("release canary is not accepted")
    if state.get("release_plan_set_version") != PLAN_SET_VERSION:
        raise ValueError("release plan set drifted")
    if state.get("report_sha256") != CANARY_REPORT_SHA256:
        raise ValueError("release canary report drifted")
    if state.get("freeze_decision_sha256") != FREEZE_SHA256:
        raise ValueError("release canary freeze drifted")
    report_path, report_content = _read_bound(
        workspace,
        state.get("report_path"),
        state.get("report_sha256"),
        "release canary report",
    )
    freeze_path, freeze_content = _read_bound(
        workspace,
        state.get("freeze_decision_path"),
        state.get("freeze_decision_sha256"),
        "release canary freeze",
    )
    freeze = _required_object(json.loads(freeze_content), "release canary freeze")
    if (
        freeze.get("executed_release_record_count") != 3
        or freeze.get("unmaterialized_release_record_count") != 1453
        or freeze.get("remaining_release_records_authorized") is not False
        or freeze.get("general_corpus_release_frozen") is not True
        or freeze.get("corpus_release_frozen") is not True
    ):
        raise ValueError("release canary freeze cardinality drifted")
    for key in (
        "remote_write_executed_count",
        "model_connection_executed_count",
        "comparison_executed_count",
        "lcia_executed_count",
    ):
        if state.get(key) != 0:
            raise ValueError(f"release canary side-effect boundary drifted: {key}")
    bindings = [
        {
            "path": str(report_path.relative_to(workspace)),
            "sha256": _sha256_bytes(report_content),
            "bytes": len(report_content),
        },
        {
            "path": str(freeze_path.relative_to(workspace)),
            "sha256": _sha256_bytes(freeze_content),
            "bytes": len(freeze_content),
        },
    ]
    return freeze, bindings


def _performance_decision(performance: Mapping[str, object]) -> JsonObject:
    comparison = _required_object(performance.get("comparison"), "performance comparison")
    measurements = _required_list(comparison.get("measurements"), "performance measurements")
    values: Dict[str, float] = {}
    for item in measurements:
        row = _required_object(item, "performance measurement")
        route_id = _required_text(row.get("route_id"), "performance route_id")
        values[route_id] = float(row.get("value"))
    if set(values) != set(EXPECTED_ROUTES):
        raise ValueError("performance evidence must contain exactly both routes")
    symmetric = (
        abs(values["solid-state"] - values["hydrothermal"])
        / ((values["solid-state"] + values["hydrothermal"]) / 2)
        * 100
    )
    policy = _required_object(performance.get("decision_policy"), "decision policy")
    threshold = float(policy.get("threshold_percent"))
    if threshold != 10 or symmetric > threshold:
        raise ValueError("accepted performance decision no longer retains the mass functional unit")
    return {
        "method": "symmetric_relative_difference_percent",
        "measurements_mAh_per_g": values,
        "observed_symmetric_relative_difference_percent": symmetric,
        "threshold_percent": threshold,
        "baseline_decision": "retain_1kg_mass_functional_unit",
        "service_sensitivity": {
            "basis": "1 kAh at cycle 30",
            "unit": "kg_material_per_1kAh_at_cycle_30",
            "solid-state": 1000 / values["solid-state"],
            "hydrothermal": 1000 / values["hydrothermal"],
        },
        "limitations": list(performance.get("limitations") or []),
    }


def _unit_for_flow(flow_key: str) -> JsonObject:
    if flow_key in {"electricity", "natural-gas-heat-proxy"}:
        return {"reference_unit": "MJ", "functional_unit_mapping": "route exchange amount"}
    return {"reference_unit": "kg", "functional_unit_mapping": "mass per product basis"}


def _source_edges(source_usage: Mapping[str, Set[str]]) -> List[JsonObject]:
    rows: List[JsonObject] = []
    for source_id, targets in sorted(source_usage.items()):
        for target in sorted(targets):
            rows.append(
                {
                    "edge_id": f"evidence:{source_id}->{target}",
                    "from_node_id": f"source:{source_id}",
                    "to_node_id": target,
                    "edge_type": "supports",
                    "amount": None,
                    "unit": None,
                    "route_id": "shared",
                }
            )
    return rows


def _risk_rows() -> List[JsonObject]:
    raw = (
        ("route-proxy-mismatch", "high", "model_limitation", True, "Retain route-alignment scenario bounds and prohibit industrial representativeness."),
        ("solid-gaseous-products-missing", "high", "required_proxy", True, "Represent an explicit bounded residual-emission scenario; never infer zero emissions."),
        ("coating-conversion-energy-gases", "high", "required_proxy", True, "Materialize one shared proxy module and perturb conversion, energy, and route differential."),
        ("us-provider-to-cn-applicability", "high", "proxy_applicability", True, "Label every USLCI Provider as a foreign proxy and vary grid/fuel intensity."),
        ("raw-material-provider-gaps", "high", "required_proxy", True, "Use explicit aggregated Provider proxies with source/bound ledgers."),
        ("single-category-burden-shifting", "medium", "claim_boundary", True, "Limit comparative claims to GWP100 and retain excluded-category warning."),
        ("hydrothermal-allocation-choice", "high", "scenario", True, "Apply mass allocation once in baseline and run no-allocation/substitution bounds."),
        ("performance-temperature-missing", "medium", "evidence_gap", True, "Retain the 1 kAh performance-normalized sensitivity and no validated equivalence claim."),
        ("native-cli-lcia-command-absent", "medium", "harness_gap", False, "Use one tested deterministic Kernel adapter over TianGong resulting-Process and TIDAS LCIAMethod for both routes."),
    )
    return [
        {
            "risk_id": risk_id,
            "severity": severity,
            "classification": classification,
            "can_change_comparative_direction": can_flip,
            "bounded_treatment": treatment,
            "review_status": "retained",
        }
        for risk_id, severity, classification, can_flip, treatment in raw
    ]


def _exclusion_rows() -> List[JsonObject]:
    raw = (
        ("remaining-flow-corpus", "1,453 non-canary release records", "not demonstrated members of the minimum graph", "a later exact graph edge proves membership"),
        ("unrestricted-uslci-background", "general USLCI breadth", "screening closure uses only selected Provider or aggregated proxy nodes", "a retained Provider cannot execute without one exact upstream dependency"),
        ("unrelated-providers", "Provider candidates without a retained foreground input", "may-be-useful-later is not expansion authority", "a route exchange or sensitivity parameter requires the Provider"),
        ("other-lcia-categories", "multi-category LCIA", "current minimum method is IPCC AR6 GWP100 with explicit burden-shifting limits", "a category is source-bound and can change the decision"),
        ("china-representativeness", "China-representative public claim", "current Providers are foreign or screening proxies", "CN-specific source-bound Providers pass the same model path"),
        ("publication-hardening", "publication, public voice, or remote save", "outside the responsibility boundary", "user authorization after Goal acceptance"),
        ("downstream-battery-life-cycle", "electrode, cell, use, and end-of-life stages", "outside the accepted cradle-to-gate boundary", "the user changes the Goal and functional unit"),
    )
    return [
        {
            "exclusion_id": item_id,
            "scope": scope,
            "reason": reason,
            "non_blocking_for_goal": True,
            "reentry_trigger": trigger,
        }
        for item_id, scope, reason, trigger in raw
    ]


def _add_edge(
    rows: List[JsonObject],
    edge_id: str,
    source: str,
    target: str,
    edge_type: str,
    *,
    amount: Optional[float] = None,
    unit: Optional[str] = None,
    route_id: str = "shared",
) -> None:
    rows.append(
        {
            "edge_id": edge_id,
            "from_node_id": source,
            "to_node_id": target,
            "edge_type": edge_type,
            "amount": amount,
            "unit": unit,
            "route_id": route_id,
        }
    )


def compile_minimum_comparison_graph(
    *,
    scope: Mapping[str, object],
    performance: Mapping[str, object],
    inventory: Mapping[str, object],
    project_snapshot: Mapping[str, object],
    workspace: Path,
) -> JsonObject:
    """Compile a closed graph without materializing a model or running LCIA."""

    workspace = Path(workspace)
    scope_id = _required_text(scope.get("scope_id"), "scope_id")
    if scope_id != "lifepo4-synthesis-comparison":
        raise ValueError("unexpected LiFePO4 scope")
    freeze_source, input_bindings = _accepted_freeze(project_snapshot, workspace)
    performance_decision = _performance_decision(performance)

    entities = _required_object(project_snapshot.get("inventory_entities"), "inventory entities")
    references = _required_object(project_snapshot.get("inventory_references"), "inventory references")
    if entities.get("flow_count") != EXPECTED_FLOW_COUNT or entities.get("source_count") != EXPECTED_SOURCE_COUNT:
        raise ValueError("current local Flow/Source cardinality drifted")
    if references.get("canonical_entity_counts") != {"flow": 11, "source": 4}:
        raise ValueError("canonical reference cardinality drifted")
    scenario_path, scenario_content = _read_bound(
        workspace,
        references.get("integrated_scenario_rows_path"),
        references.get("integrated_scenario_rows_sha256"),
        "integrated scenario rows",
    )
    input_bindings.append(
        {
            "path": str(scenario_path.relative_to(workspace)),
            "sha256": _sha256_bytes(scenario_content),
            "bytes": len(scenario_content),
        }
    )
    scenario_rows = [json.loads(line) for line in scenario_content.splitlines() if line.strip()]
    routes = {_route_from_process(row): row for row in scenario_rows}
    if set(routes) != set(EXPECTED_ROUTES) or len(scenario_rows) != 2:
        raise ValueError("current scenario rows must contain exactly both routes")

    flow_results = _required_list(entities.get("flow_results"), "Flow results")
    if len(flow_results) != EXPECTED_FLOW_COUNT:
        raise ValueError("current Flow results cardinality drifted")
    flows_by_id: Dict[str, JsonObject] = {}
    flows_by_key: Dict[str, JsonObject] = {}
    for item in flow_results:
        row = _required_object(item, "Flow result")
        flow_id = _required_text(row.get("flow_id"), "Flow ID")
        flow_key = _required_text(row.get("flow_key"), "Flow key")
        if row.get("version") != "00.00.001":
            raise ValueError(f"Flow is not the current local version: {flow_key}")
        path, content = _read_bound(
            workspace,
            row.get("flow_path"),
            row.get("flow_sha256"),
            f"Flow {flow_key}",
        )
        if flow_id in flows_by_id or flow_key in flows_by_key:
            raise ValueError("current Flow identities are not unique")
        flows_by_id[flow_id] = row
        flows_by_key[flow_key] = row
        input_bindings.append(
            {
                "path": str(path.relative_to(workspace)),
                "sha256": _sha256_bytes(content),
                "bytes": len(content),
            }
        )

    source_results = _required_list(entities.get("source_results"), "Source results")
    if len(source_results) != EXPECTED_SOURCE_COUNT:
        raise ValueError("current Source results cardinality drifted")
    source_ids: Dict[str, str] = {}
    for item in source_results:
        row = _required_object(item, "Source result")
        source_id = _required_text(row.get("source_id"), "Source ID")
        if row.get("version") != "00.00.001":
            raise ValueError(f"Source is not the current local version: {source_id}")
        source_ids[source_id] = _required_text(row.get("tidas_source_id"), "TIDAS Source ID")
    if len(source_ids) != EXPECTED_SOURCE_COUNT:
        raise ValueError("current Source identities are not unique")

    route_flow_usage: MutableMapping[str, Set[str]] = defaultdict(set)
    route_input_amounts: Dict[Tuple[str, str], float] = {}
    route_output_amounts: Dict[Tuple[str, str], float] = {}
    for route_id, row in routes.items():
        for exchange in _exchanges(row):
            reference = _flow_ref(exchange)
            flow_id = _required_text(reference.get("@refObjectId"), "exchange Flow ID")
            if flow_id not in flows_by_id:
                raise ValueError(f"scenario references a non-current Flow: {flow_id}")
            if reference.get("@version") != "00.00.001":
                raise ValueError(f"scenario Flow reference version drifted: {flow_id}")
            route_flow_usage[flow_id].add(route_id)
            amount = float(exchange.get("resultingAmount", exchange.get("meanAmount")))
            if exchange.get("exchangeDirection") == "Input":
                route_input_amounts[(route_id, flow_id)] = amount
            elif exchange.get("exchangeDirection") == "Output":
                route_output_amounts[(route_id, flow_id)] = amount
            else:
                raise ValueError("scenario exchange direction is unsupported")

    source_usage: MutableMapping[str, Set[str]] = defaultdict(set)
    nodes: List[JsonObject] = []
    edges: List[JsonObject] = []
    flow_node_ids: Dict[str, str] = {}
    for flow_id, row in sorted(flows_by_id.items(), key=lambda pair: str(pair[1]["flow_key"])):
        flow_key = str(row["flow_key"])
        used_by = route_flow_usage.get(flow_id, set())
        route_role = next(iter(used_by)) if len(used_by) == 1 else "shared"
        node_id = f"flow:{flow_key}"
        flow_node_ids[flow_id] = node_id
        nodes.append(
            {
                "node_id": node_id,
                "node_type": "Flow",
                "route_role": route_role,
                "necessity": "required",
                "evidence_status": "materialized_local",
                "source_refs": [
                    f"artifact:{row['flow_path']}#sha256={row['flow_sha256']}"
                ],
                "unit_mapping": _unit_for_flow(flow_key),
                "downstream_node_ids": [],
                "can_change_comparative_direction": flow_key
                not in {"uncoated-lifepo4-route-core", "lithium-sulfate"},
                "entity_id": flow_id,
                "entity_version": "00.00.001",
                "flow_key": flow_key,
            }
        )

    for source_id, tidas_id in sorted(source_ids.items()):
        nodes.append(
            {
                "node_id": f"source:{source_id}",
                "node_type": "Source",
                "route_role": "shared",
                "necessity": "required",
                "evidence_status": "accepted_source",
                "source_refs": [f"tidas-source:{tidas_id}@00.00.001"],
                "unit_mapping": {"reference_unit": "not_applicable", "functional_unit_mapping": "evidence provenance"},
                "downstream_node_ids": [],
                "can_change_comparative_direction": True,
                "entity_id": tidas_id,
                "entity_version": "00.00.001",
            }
        )

    scenario_binding = f"artifact:{scenario_path.relative_to(workspace)}#sha256={_sha256_bytes(scenario_content)}"
    route_sources = {
        "solid-state": ["argonne-dunn-2015-lfp-lci"],
        "hydrothermal": [
            "argonne-dunn-2015-lfp-lci",
            "fisher-2017-hydrothermal-energy-model",
            "hong-2011-supercritical-route-conditions",
        ],
    }
    for route_id in EXPECTED_ROUTES:
        process_node = f"process:{route_id}"
        nodes.append(
            {
                "node_id": process_node,
                "node_type": "foreground Process",
                "route_role": route_id,
                "necessity": "required",
                "evidence_status": "materialized_local",
                "source_refs": [scenario_binding]
                + [f"source:{source_id}" for source_id in route_sources[route_id]],
                "unit_mapping": {
                    "reference_output": "uncoated-lifepo4-route-core",
                    "native_reference_amount_kg": 1,
                    "functional_unit_core_amount_kg": 0.94,
                },
                "downstream_node_ids": [],
                "can_change_comparative_direction": True,
                "entity_id": _process_id(routes[route_id]),
                "entity_version": "00.00.001",
            }
        )
        for source_id in route_sources[route_id]:
            source_usage[source_id].add(process_node)

    carbon_flow = {
        "node_id": "flow:carbon-coated-lifepo4",
        "node_type": "Flow",
        "route_role": "shared",
        "necessity": "required",
        "evidence_status": "planned_local",
        "source_refs": ["source:hong-2012-carbon-coated-route-comparison"],
        "unit_mapping": {"reference_unit": "kg", "functional_unit_mapping": "1 kg reference product"},
        "downstream_node_ids": [],
        "can_change_comparative_direction": False,
        "entity_id": None,
        "entity_version": "00.00.001",
        "flow_key": "carbon-coated-lifepo4",
    }
    sucrose_flow = {
        "node_id": "flow:sucrose-coating-precursor",
        "node_type": "Flow",
        "route_role": "shared",
        "necessity": "proxy_scenario",
        "evidence_status": "planned_local",
        "source_refs": ["source:hong-2012-carbon-coated-route-comparison"],
        "unit_mapping": {"reference_unit": "kg", "functional_unit_mapping": "conversion-efficiency scenario"},
        "downstream_node_ids": [],
        "can_change_comparative_direction": True,
        "entity_id": None,
        "entity_version": "00.00.001",
        "flow_key": "sucrose-coating-precursor",
    }
    coating_process = {
        "node_id": "process:shared-carbon-coating",
        "node_type": "foreground Process",
        "route_role": "shared",
        "necessity": "proxy_scenario",
        "evidence_status": "planned_local",
        "source_refs": ["source:hong-2012-carbon-coated-route-comparison"],
        "unit_mapping": {
            "reference_output": "carbon-coated-lifepo4",
            "reference_amount_kg": 1,
            "uncoated_core_input_kg": 0.94,
            "retained_carbon_output_kg": 0.06,
        },
        "downstream_node_ids": [],
        "can_change_comparative_direction": True,
        "entity_id": None,
        "entity_version": "00.00.001",
    }
    nodes.extend((carbon_flow, sucrose_flow, coating_process))
    source_usage["hong-2012-carbon-coated-route-comparison"].update(
        {carbon_flow["node_id"], sucrose_flow["node_id"], coating_process["node_id"]}
    )

    input_flow_ids = {flow_id for _, flow_id in route_input_amounts}
    input_flow_ids.add("sucrose-coating-precursor")
    for flow_ref in sorted(input_flow_ids, key=lambda value: str(flows_by_id.get(value, {}).get("flow_key", value))):
        if flow_ref == "sucrose-coating-precursor":
            flow_key = flow_ref
            flow_node = "flow:sucrose-coating-precursor"
            routes_using = {"shared"}
            resolution = "scenario_bound"
        else:
            row = flows_by_id[flow_ref]
            flow_key = str(row["flow_key"])
            flow_node = flow_node_ids[flow_ref]
            routes_using = {route for route, candidate in route_input_amounts if candidate == flow_ref}
            if flow_key in {"electricity", "natural-gas-heat-proxy"}:
                resolution = "exact_uslci"
            elif flow_key == "process-water-proxy":
                resolution = "local_aggregated_proxy"
            else:
                resolution = "scenario_bound"
        if flow_key == "electricity":
            routes_using.add("shared")
        route_role = next(iter(routes_using)) if len(routes_using) == 1 and "shared" not in routes_using else "shared"
        provider_node = f"provider:{flow_key}"
        downstream_processes = {
            f"process:{route}" for route in routes_using if route in EXPECTED_ROUTES
        }
        if flow_key in {"electricity", "sucrose-coating-precursor"}:
            downstream_processes.add("process:shared-carbon-coating")
        nodes.append(
            {
                "node_id": provider_node,
                "node_type": "Provider",
                "route_role": route_role,
                "necessity": "proxy_scenario",
                "evidence_status": "required_proxy",
                "source_refs": [flow_node, "case:lifepo4-route-inventory-evidence.json"],
                "unit_mapping": {
                    "provided_flow": flow_key,
                    "reference_unit": _unit_for_flow(flow_key)["reference_unit"],
                    "resolution_class": resolution,
                },
                "downstream_node_ids": [],
                "can_change_comparative_direction": True,
                "entity_id": None,
                "entity_version": "00.00.001",
                "resolution_class": resolution,
            }
        )
        _add_edge(
            edges,
            f"provider-output:{flow_key}",
            provider_node,
            flow_node,
            "provides",
            unit=str(_unit_for_flow(flow_key)["reference_unit"]),
        )
        for process_node in sorted(downstream_processes):
            _add_edge(
                edges,
                f"provider-link:{flow_key}->{process_node}",
                provider_node,
                process_node,
                "provider_binding",
                route_id=process_node.split(":", 1)[1] if process_node != "process:shared-carbon-coating" else "shared",
            )

    for route_id, row in routes.items():
        process_node = f"process:{route_id}"
        for exchange in _exchanges(row):
            reference = _flow_ref(exchange)
            flow_id = str(reference["@refObjectId"])
            flow_node = flow_node_ids[flow_id]
            amount = float(exchange.get("resultingAmount", exchange.get("meanAmount")))
            direction = exchange.get("exchangeDirection")
            if direction == "Input":
                _add_edge(
                    edges,
                    f"exchange:{route_id}:input:{flow_id}",
                    flow_node,
                    process_node,
                    "technosphere_input",
                    amount=amount,
                    unit=str(_unit_for_flow(str(flows_by_id[flow_id]["flow_key"]))["reference_unit"]),
                    route_id=route_id,
                )
            else:
                _add_edge(
                    edges,
                    f"exchange:{route_id}:output:{flow_id}",
                    process_node,
                    flow_node,
                    "process_output",
                    amount=amount,
                    unit="kg",
                    route_id=route_id,
                )
        _add_edge(
            edges,
            f"route-core:{route_id}",
            process_node,
            "process:shared-carbon-coating",
            "supplies_route_core",
            amount=0.94,
            unit="kg",
            route_id=route_id,
        )

    _add_edge(
        edges,
        "coating-input:core",
        "flow:uncoated-lifepo4-route-core",
        "process:shared-carbon-coating",
        "shared_input",
        amount=0.94,
        unit="kg",
    )
    _add_edge(
        edges,
        "coating-input:sucrose",
        "flow:sucrose-coating-precursor",
        "process:shared-carbon-coating",
        "proxy_input",
        unit="kg",
    )
    electricity_node = flow_node_ids[str(flows_by_key["electricity"]["flow_id"])]
    _add_edge(
        edges,
        "coating-input:energy",
        electricity_node,
        "process:shared-carbon-coating",
        "proxy_energy_input",
        unit="MJ",
    )
    _add_edge(
        edges,
        "coating-output:product",
        "process:shared-carbon-coating",
        "flow:carbon-coated-lifepo4",
        "reference_product_output",
        amount=1,
        unit="kg",
    )

    lcia_node = {
        "node_id": "lciamethod:ipcc-ar6-gwp100-screening",
        "node_type": "LCIAMethod",
        "route_role": "shared",
        "necessity": "required",
        "evidence_status": "selected_method",
        "source_refs": [
            "source:https://www.ipcc.ch/report/ar6/wg1/",
            "tidas-contract:lciamethod@tiangong-cli-0.0.25",
        ],
        "unit_mapping": {"reference_unit": "kg CO2-eq", "functional_unit_mapping": "per 1 kg product"},
        "downstream_node_ids": [],
        "can_change_comparative_direction": True,
        "entity_id": None,
        "entity_version": "00.00.001",
    }
    nodes.append(lcia_node)
    for route_id in EXPECTED_ROUTES:
        model_node = f"lifecyclemodel:{route_id}"
        nodes.append(
            {
                "node_id": model_node,
                "node_type": "LifecycleModel",
                "route_role": route_id,
                "necessity": "required",
                "evidence_status": "planned_local",
                "source_refs": [f"process:{route_id}", "process:shared-carbon-coating"],
                "unit_mapping": {"reference_flow": "carbon-coated-lifepo4", "reference_amount": 1, "reference_unit": "kg"},
                "downstream_node_ids": [],
                "can_change_comparative_direction": True,
                "entity_id": None,
                "entity_version": "00.00.001",
            }
        )
        _add_edge(edges, f"model-route:{route_id}", f"process:{route_id}", model_node, "foreground_root", route_id=route_id)
        _add_edge(edges, f"model-coating:{route_id}", "process:shared-carbon-coating", model_node, "shared_module", route_id=route_id)
        _add_edge(edges, f"model-product:{route_id}", "flow:carbon-coated-lifepo4", model_node, "quantitative_reference", amount=1, unit="kg", route_id=route_id)
        _add_edge(edges, f"model-lcia:{route_id}", lcia_node["node_id"], model_node, "characterizes", route_id=route_id)
    lithium_sulfate_node = flow_node_ids[str(flows_by_key["lithium-sulfate"]["flow_id"])]
    _add_edge(
        edges,
        "allocation:hydrothermal-lithium-sulfate",
        lithium_sulfate_node,
        "lifecyclemodel:hydrothermal",
        "allocation_basis",
        amount=0.696879122,
        unit="kg",
        route_id="hydrothermal",
    )

    edges.extend(_source_edges(source_usage))
    outgoing: MutableMapping[str, Set[str]] = defaultdict(set)
    for edge in edges:
        outgoing[str(edge["from_node_id"])].add(str(edge["to_node_id"]))
    for node in nodes:
        node["downstream_node_ids"] = sorted(outgoing.get(str(node["node_id"]), set()))

    nodes = sorted(nodes, key=lambda row: str(row["node_id"]))
    edges = sorted(edges, key=lambda row: str(row["edge_id"]))
    node_counts = dict(sorted(Counter(str(row["node_type"]) for row in nodes).items()))
    provider_count = node_counts.get("Provider", 0)
    input_value_hashes = {
        "scope": _sha256_bytes(_json_bytes(scope)),
        "performance": _sha256_bytes(_json_bytes(performance)),
        "inventory": _sha256_bytes(_json_bytes(inventory)),
    }
    freeze = {
        "release_plan_set_version": PLAN_SET_VERSION,
        "canary_report_sha256": CANARY_REPORT_SHA256,
        "freeze_decision_sha256": FREEZE_SHA256,
        "materialized_release_record_count": 3,
        "unmaterialized_release_record_count": 1453,
        "remaining_release_records_authorized": False,
        "general_corpus_release_frozen": True,
        "source_status": freeze_source.get("status"),
    }
    scope_contract = {
        "question": scope.get("question"),
        "functional_unit": {
            "product": "carbon-coated LiFePO4 cathode active material",
            "amount": 1,
            "unit": "kg",
            "computational_basis": "0.94 kg uncoated route core plus shared coating to 6 wt% retained carbon",
        },
        "performance_equivalence": performance_decision,
        "system_boundary": scope.get("system_boundary"),
        "geography": scope.get("geography"),
        "temporal_scope": scope.get("temporal_scope"),
        "coating_basis": {
            "retained_carbon_mass_fraction": 0.06,
            "uncoated_core_kg": 0.94,
            "status": "shared_required_proxy_scenario",
        },
        "allocation": {
            "baseline": "hydrothermal_mass_allocation_exactly_once",
            "hydrothermal_mass_share": 157.754762 / (157.754762 + 109.936),
            "sensitivity": ["no_allocation", "coproduct_substitution_bound"],
        },
        "comparison_basis": "same product mass, same boundary, same LCIA method and calculation adapter",
    }
    lcia_method = {
        "method_id": "ipcc-ar6-gwp100-screening",
        "indicator": "climate change GWP100",
        "reference_unit": "kg CO2-eq",
        "tidas_lciamethod_required": True,
        "native_cli_contract_available": True,
        "native_cli_calculation_command_available": False,
        "execution_route": "TianGong resulting-Process plus one deterministic Kernel adapter over the same TIDAS LCI/LCIAMethod contract for both routes",
        "diagnostic_inventory_indicators": ["cumulative purchased energy", "raw-material mass"],
        "claim_boundary": "single-category screening comparison; no multi-category environmental superiority claim",
    }
    exclusions = _exclusion_rows()
    risks = _risk_rows()
    input_version = _sha256_bytes(
        _json_bytes(
            {
                "freeze": freeze,
                "scope": scope_contract,
                "lcia_method": lcia_method,
                "input_value_hashes": input_value_hashes,
                "input_bindings": input_bindings,
                "nodes": nodes,
                "edges": edges,
            }
        )
    )[:16]
    report: JsonObject = {
        "schema_version": 1,
        "contract": CONTRACT,
        "status": "minimum_lifepo4_comparison_graph_compiled",
        "scope_id": scope_id,
        "revision": "v5",
        "input_version": input_version,
        "freeze": freeze,
        "scope_contract": scope_contract,
        "lcia_method": lcia_method,
        "nodes": nodes,
        "edges": edges,
        "exclusions": exclusions,
        "risks": risks,
        "input_bindings": sorted(input_bindings, key=lambda row: str(row["path"])),
        "input_value_sha256": input_value_hashes,
        "node_type_counts": node_counts,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "provider_slot_count": provider_count,
        "provider_resolution_class_counts": dict(
            sorted(
                Counter(
                    str(row["resolution_class"])
                    for row in nodes
                    if row["node_type"] == "Provider"
                ).items()
            )
        ),
        "existing_local_flow_count": 11,
        "new_graph_flow_member_count": 2,
        "existing_local_source_count": 4,
        "unexplained_node_count": 0,
        "dangling_required_node_count": 0,
        "materialization_authorized_in_this_activity": False,
        "graph_nonmember_materialization_authorized": False,
        "model_materialization_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "network_read_executed_count": 0,
        "remote_write_executed_count": 0,
        "publication_executed_count": 0,
        "next_internal_route": NEXT_ROUTE,
        "next_action": "Materialize or reuse only the graph members in one isolated local TianGong model package, then validate, QA, read back, and replay both routes.",
    }
    verification = verify_minimum_comparison_graph(report, workspace)
    report["verification_summary"] = {
        "status": verification["status"],
        "decision_count": verification["decision_count"],
        "accepted_decision_count": verification["accepted_decision_count"],
    }
    return report


def verify_minimum_comparison_graph(report: Mapping[str, object], workspace: Path) -> JsonObject:
    """Make seven fail-closed graph decisions from the compiled fact packet."""

    if report.get("contract") != CONTRACT or report.get("status") != "minimum_lifepo4_comparison_graph_compiled":
        raise ValueError("minimum comparison graph contract is invalid")
    freeze = _required_object(report.get("freeze"), "freeze")
    if (
        freeze.get("release_plan_set_version") != PLAN_SET_VERSION
        or freeze.get("canary_report_sha256") != CANARY_REPORT_SHA256
        or freeze.get("freeze_decision_sha256") != FREEZE_SHA256
        or freeze.get("materialized_release_record_count") != 3
        or freeze.get("unmaterialized_release_record_count") != 1453
        or freeze.get("remaining_release_records_authorized") is not False
        or freeze.get("general_corpus_release_frozen") is not True
    ):
        raise ValueError("accepted freeze is not preserved")

    scope = _required_object(report.get("scope_contract"), "scope contract")
    functional_unit = _required_object(scope.get("functional_unit"), "functional unit")
    coating = _required_object(scope.get("coating_basis"), "coating basis")
    allocation = _required_object(scope.get("allocation"), "allocation")
    performance = _required_object(scope.get("performance_equivalence"), "performance equivalence")
    if (
        functional_unit.get("amount") != 1
        or functional_unit.get("unit") != "kg"
        or functional_unit.get("product") != "carbon-coated LiFePO4 cathode active material"
        or coating.get("uncoated_core_kg") != 0.94
        or coating.get("retained_carbon_mass_fraction") != 0.06
        or allocation.get("baseline") != "hydrothermal_mass_allocation_exactly_once"
        or allocation.get("hydrothermal_mass_share") != 0.5893171688905723
    ):
        raise ValueError("shared scope contract drifted")
    if (
        performance.get("observed_symmetric_relative_difference_percent")
        != 8.51063829787234
        or performance.get("threshold_percent") != 10
        or performance.get("baseline_decision") != "retain_1kg_mass_functional_unit"
    ):
        raise ValueError("performance policy arithmetic drifted")

    nodes_raw = _required_list(report.get("nodes"), "nodes")
    edges_raw = _required_list(report.get("edges"), "edges")
    nodes = [_required_object(row, "node contract") for row in nodes_raw]
    edges = [_required_object(row, "edge contract") for row in edges_raw]
    node_ids: Set[str] = set()
    node_by_id: Dict[str, JsonObject] = {}
    for node in nodes:
        required = {
            "node_id",
            "node_type",
            "route_role",
            "necessity",
            "evidence_status",
            "source_refs",
            "unit_mapping",
            "downstream_node_ids",
            "can_change_comparative_direction",
        }
        if not required.issubset(node):
            raise ValueError("node contract is incomplete")
        node_id = _required_text(node.get("node_id"), "node contract node_id")
        if node_id in node_ids:
            raise ValueError("node contract contains duplicate IDs")
        node_ids.add(node_id)
        node_by_id[node_id] = node
        if node.get("node_type") not in NODE_TYPES:
            raise ValueError("node contract has an unsupported node_type")
        if node.get("route_role") not in ROUTE_ROLES:
            raise ValueError("node contract has an unsupported route_role")
        if node.get("necessity") not in NECESSITY_CLASSES:
            raise ValueError("node contract has an unsupported necessity")
        if node.get("evidence_status") not in EVIDENCE_STATUSES:
            raise ValueError("node contract has an unsupported evidence_status")
        if not isinstance(node.get("source_refs"), list) or not node["source_refs"]:
            raise ValueError("node contract lacks source_refs")
        if not isinstance(node.get("unit_mapping"), dict) or not node["unit_mapping"]:
            raise ValueError("node contract lacks unit_mapping")
        if not isinstance(node.get("downstream_node_ids"), list):
            raise ValueError("node contract lacks downstream IDs")
        if not isinstance(node.get("can_change_comparative_direction"), bool):
            raise ValueError("node contract lacks comparative-direction classification")

    edge_ids: Set[str] = set()
    outgoing: MutableMapping[str, Set[str]] = defaultdict(set)
    for edge in edges:
        edge_id = _required_text(edge.get("edge_id"), "edge_id")
        source = _required_text(edge.get("from_node_id"), "edge source")
        target = _required_text(edge.get("to_node_id"), "edge target")
        if edge_id in edge_ids:
            raise ValueError("edge contract contains duplicate IDs")
        if source not in node_ids or target not in node_ids:
            raise ValueError("edge contract points outside the graph")
        edge_ids.add(edge_id)
        outgoing[source].add(target)
    dangling = 0
    for node_id, node in node_by_id.items():
        declared = set(str(item) for item in node["downstream_node_ids"])
        if declared != outgoing.get(node_id, set()):
            raise ValueError("node contract downstream index drifted")
        if node.get("necessity") == "required" and node.get("node_type") != "LifecycleModel" and not declared:
            dangling += 1
    if dangling or report.get("dangling_required_node_count") != 0:
        raise ValueError("minimum graph has a dangling required node")
    counts = dict(sorted(Counter(str(row["node_type"]) for row in nodes).items()))
    if counts != report.get("node_type_counts"):
        raise ValueError("minimum graph node counts drifted")
    if (
        counts.get("Flow") != 13
        or counts.get("Source") != 4
        or counts.get("foreground Process") != 3
        or counts.get("Provider") != 10
        or counts.get("LCIAMethod") != 1
        or counts.get("LifecycleModel") != 2
        or report.get("unexplained_node_count") != 0
    ):
        raise ValueError("minimum graph membership drifted")

    providers = [row for row in nodes if row.get("node_type") == "Provider"]
    if report.get("provider_slot_count") != len(providers):
        raise ValueError("Provider slot count drifted")
    for provider in providers:
        if provider.get("resolution_class") not in PROVIDER_RESOLUTION_CLASSES:
            raise ValueError("Provider gap is not classified")
        if provider.get("entity_id") is not None:
            raise ValueError("Provider was materialized during graph compilation")

    lcia = _required_object(report.get("lcia_method"), "LCIA method")
    if (
        lcia.get("method_id") != "ipcc-ar6-gwp100-screening"
        or lcia.get("tidas_lciamethod_required") is not True
        or lcia.get("native_cli_contract_available") is not True
        or lcia.get("native_cli_calculation_command_available") is not False
    ):
        raise ValueError("LCIA command or method surface is overstated")
    for key in (
        "model_materialization_executed_count",
        "comparison_executed_count",
        "lcia_executed_count",
        "network_read_executed_count",
        "remote_write_executed_count",
        "publication_executed_count",
    ):
        if report.get(key) != 0:
            raise ValueError(f"graph compilation crossed the side-effect boundary: {key}")
    if report.get("materialization_authorized_in_this_activity") is not False:
        raise ValueError("graph compilation falsely authorizes materialization")
    if report.get("graph_nonmember_materialization_authorized") is not False:
        raise ValueError("graph compilation authorizes non-members")

    workspace = Path(workspace)
    for item in _required_list(report.get("input_bindings"), "input bindings"):
        binding = _required_object(item, "input binding")
        path, content = _read_bound(
            workspace,
            binding.get("path"),
            binding.get("sha256"),
            "minimum graph input binding",
        )
        if path.stat().st_size != binding.get("bytes") or len(content) != binding.get("bytes"):
            raise ValueError("minimum graph input binding bytes drifted")

    decision_rows = [
        {"decision_id": decision_id, "status": "accepted"}
        for decision_id in DECISIONS
    ]
    return {
        "schema_version": 1,
        "contract": VERIFICATION_CONTRACT,
        "status": "accepted",
        "input_version": report.get("input_version"),
        "decision_count": len(decision_rows),
        "accepted_decision_count": len(decision_rows),
        "decisions": decision_rows,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "provider_slot_count": len(providers),
        "next_internal_route": NEXT_ROUTE,
    }


def _binding(path: Path, base: Path) -> JsonObject:
    content = path.read_bytes()
    return {
        "path": str(path.relative_to(base)),
        "sha256": _sha256_bytes(content),
        "bytes": len(content),
    }


def write_minimum_comparison_graph_package(
    report: Mapping[str, object],
    out_dir: Path,
    *,
    workspace: Optional[Path] = None,
) -> JsonObject:
    """Write, reopen, and bind the immutable graph package."""

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    verification = verify_minimum_comparison_graph(
        report,
        Path(workspace)
        if workspace is not None
        else _workspace_from_bindings(report, out_dir),
    )
    payloads = {
        "scope": _json_bytes(report["scope_contract"], pretty=True),
        "nodes": _jsonl_bytes(report["nodes"]),
        "edges": _jsonl_bytes(report["edges"]),
        "exclusions": _jsonl_bytes(report["exclusions"]),
        "risks": _jsonl_bytes(report["risks"]),
        "verification": _json_bytes(verification, pretty=True),
    }
    bindings: JsonObject = {}
    for name, content in payloads.items():
        path = out_dir / OUTPUT_NAMES[name]
        path.write_bytes(content)
        if path.read_bytes() != content:
            raise RuntimeError(f"minimum graph package readback drifted: {name}")
        bindings[name] = _binding(path, out_dir)
    packaged = dict(report)
    packaged["artifact_bindings"] = bindings
    report_path = out_dir / OUTPUT_NAMES["report"]
    report_path.write_bytes(_json_bytes(packaged, pretty=True))
    if json.loads(report_path.read_text(encoding="utf-8")) != packaged:
        raise RuntimeError("minimum graph report readback drifted")
    return packaged


def _workspace_from_bindings(report: Mapping[str, object], out_dir: Path) -> Path:
    """Resolve the shared workspace root from the first bound input and output location."""

    bindings = _required_list(report.get("input_bindings"), "input bindings")
    if not bindings:
        raise ValueError("minimum graph input bindings are absent")
    first = _required_object(bindings[0], "input binding")
    relative = Path(_required_text(first.get("path"), "input binding path"))
    current = Path(out_dir).resolve()
    for parent in (current, *current.parents):
        if (parent / relative).is_file():
            return parent
    raise ValueError("cannot resolve minimum graph workspace from input bindings")


def _load(path: Path) -> JsonObject:
    return _required_object(json.loads(path.read_text(encoding="utf-8")), str(path))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--performance", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--project-snapshot", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = compile_minimum_comparison_graph(
        scope=_load(args.scope),
        performance=_load(args.performance),
        inventory=_load(args.inventory),
        project_snapshot=_load(args.project_snapshot),
        workspace=args.workspace,
    )
    packaged = write_minimum_comparison_graph_package(
        report, args.out_dir, workspace=args.workspace
    )
    print(json.dumps(packaged, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
