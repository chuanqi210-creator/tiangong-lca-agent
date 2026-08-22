"""Compile bounded TianGong-native preflight actions for one USLCI slice."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from pathlib import Path
from typing import Dict, Sequence


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_native_slice_preflight_plan/v1"
FLOW_ACTION_CONTRACT = "lca_research_kernel.uslci_flow_identity_preflight_action/v1"
PROCESS_ACTION_CONTRACT = "lca_research_kernel.uslci_process_identity_preflight_action/v1"
BUILD_TEMPLATE_CONTRACT = "lca_research_kernel.uslci_process_build_plan_template/v1"
LOGIC_VERSION = "2026-07-17.1"
EXPECTED_ROUTE = "preflight-uslci-native-integration-slice"
NEXT_ROUTE = "execute-uslci-native-slice-preflight-batch-1"
BATCH_SIZE = 100
MAX_CONCURRENCY = 4
FLOW_TYPE_MAP = {
    "PRODUCT_FLOW": "Product flow",
    "WASTE_FLOW": "Waste flow",
    "ELEMENTARY_FLOW": "Elementary flow",
}


def _bytes(value, pretty=False):
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
        )
        + "\n"
    ).encode()


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return dict(value)


def _load_json(value, label):
    try:
        return _object(json.loads(value), label)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} invalid JSON") from error


def _load_jsonl(value, label, allow_empty=False):
    try:
        rows = [
            _object(json.loads(line), label)
            for line in value.decode().splitlines()
            if line.strip()
        ]
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} invalid JSONL") from error
    if not rows and not allow_empty:
        raise ValueError(f"{label} empty")
    return rows


def _binding(value, content, label):
    row = _object(value, f"{label} binding")
    if (
        not isinstance(row.get("path"), str)
        or not row["path"]
        or row.get("sha256") != _sha(content)
        or row.get("bytes") != len(content)
    ):
        raise ValueError(f"{label} binding hash, bytes or path drifted")
    return {"path": row["path"], "sha256": row["sha256"], "bytes": len(content)}


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} missing")
    return value.strip()


def _uuid5(kind, *parts):
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            "|".join([f"tiangong-lca:uslci-{kind}/v1", *map(str, parts)]),
        )
    )


def input_version_for_bindings(bindings):
    return _sha(_bytes({"logic": LOGIC_VERSION, "inputs": bindings}))[:16]


def _action_key(kind, request, catalog_sha256, runtime_tree_sha256):
    return _sha(
        _bytes(
            {
                "logic": LOGIC_VERSION,
                "kind": kind,
                "request": request,
                "candidate_catalog_sha256": catalog_sha256,
                "runtime_tree_sha256": runtime_tree_sha256,
            }
        )
    )[:16]


def _assert_artifact_binding(integration, key, ref):
    bound = _object(
        _object(integration.get("artifact_bindings"), "integration artifact bindings").get(key),
        f"integration {key} binding",
    )
    if bound.get("sha256") != ref["sha256"] or bound.get("bytes") != ref["bytes"]:
        raise ValueError(f"integration {key} binding drifted")


def compile_uslci_native_slice_preflight_plan(
    *,
    integration_report_bytes,
    integration_report_binding,
    selected_slice_bytes,
    selected_slice_binding,
    flow_mapping_requirements_bytes,
    flow_mapping_requirements_binding,
    unit_mapping_requirements_bytes,
    unit_mapping_requirements_binding,
    normalization_report_bytes,
    normalization_report_binding,
    local_flow_catalog_bytes,
    local_flow_catalog_binding,
    local_process_catalog_bytes,
    local_process_catalog_binding,
    runtime_report_bytes,
    runtime_report_binding,
    runtime_manifest_bytes,
    runtime_manifest_binding,
):
    contents = (
        (integration_report_bytes, integration_report_binding, "integration report"),
        (selected_slice_bytes, selected_slice_binding, "selected slice"),
        (flow_mapping_requirements_bytes, flow_mapping_requirements_binding, "Flow requirements"),
        (unit_mapping_requirements_bytes, unit_mapping_requirements_binding, "unit requirements"),
        (normalization_report_bytes, normalization_report_binding, "normalization report"),
        (local_flow_catalog_bytes, local_flow_catalog_binding, "local Flow catalog"),
        (local_process_catalog_bytes, local_process_catalog_binding, "local Process catalog"),
        (runtime_report_bytes, runtime_report_binding, "runtime report"),
        (runtime_manifest_bytes, runtime_manifest_binding, "runtime manifest"),
    )
    refs = [_binding(binding, value, label) for value, binding, label in contents]
    integration = _load_json(integration_report_bytes, "integration report")
    selected = _load_json(selected_slice_bytes, "selected slice")
    flow_rows = _load_jsonl(flow_mapping_requirements_bytes, "Flow requirements")
    unit_rows = _load_jsonl(unit_mapping_requirements_bytes, "unit requirements")
    normalization = _load_json(normalization_report_bytes, "normalization report")
    flow_catalog = _load_jsonl(local_flow_catalog_bytes, "local Flow catalog")
    process_catalog = _load_jsonl(local_process_catalog_bytes, "local Process catalog")
    runtime = _load_json(runtime_report_bytes, "runtime report")
    runtime_manifest = _load_json(runtime_manifest_bytes, "runtime manifest")

    if (
        integration.get("contract")
        != "lca_research_kernel.uslci_background_inventory_integration_plan/v1"
        or integration.get("status")
        != "uslci_background_inventory_integration_plan_compiled"
        or integration.get("routing", {}).get("next_internal_route") != EXPECTED_ROUTE
        or integration.get("selected_native_slice_process_count") != 5
        or integration.get("scientific_comparison_ready") is not False
        or integration.get("comparative_lcia_allowed") is not False
        or integration.get("process_creation_executed_count") != 0
        or integration.get("model_connection_executed_count") != 0
    ):
        raise ValueError("integration report does not authorize slice preflight planning")
    for key, ref in (
        ("selected_native_slice", refs[1]),
        ("flow_mapping_requirements", refs[2]),
        ("unit_mapping_requirements", refs[3]),
    ):
        _assert_artifact_binding(integration, key, ref)
    if (
        selected.get("contract")
        != "lca_research_kernel.uslci_native_contract_slice/v1"
        or selected.get("process_count") != 5
        or selected.get("provider_edge_count") != 4
        or selected.get("native_identity_preflight_allowed") is not True
        or selected.get("native_build_plan_allowed") is not True
        or selected.get("native_materialization_allowed") is not False
        or selected.get("process_creation_allowed") is not False
        or selected.get("model_connection_allowed") is not False
        or selected.get("scientific_comparison_ready") is not False
        or selected.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("selected native slice contract drifted")
    scope_pairs = {
        (integration.get("scope_id"), integration.get("revision")),
        (selected.get("scope_id"), selected.get("revision")),
        (normalization.get("scope_id"), normalization.get("revision")),
    }
    if len(scope_pairs) != 1:
        raise ValueError("scope or revision drifted")
    scope_id, revision = next(iter(scope_pairs))
    if (
        normalization.get("contract")
        != "lca_research_kernel.uslci_provider_normalization_plan/v1"
        or not isinstance(normalization.get("source_repository_version"), str)
        or normalization.get("scientific_comparison_ready") is not False
        or normalization.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("normalization source-version contract drifted")
    if (
        runtime.get("contract")
        != "lca_research_kernel.portable_native_tool_runtime_closure/v1"
        or runtime.get("status") != "portable_native_tool_runtime_closure_verified"
        or runtime.get("runtime_content_closed") is not True
        or runtime.get("same_platform_relocation_proven") is not True
        or runtime.get("tree_sha256") != runtime_manifest.get("tree_sha256")
        or runtime.get("tiangong_cli", {}).get("version") != "0.0.25"
        or runtime_manifest.get("contract")
        != "lca_research_kernel.portable_native_tool_runtime_closure/v1"
        or runtime_manifest.get("status") != "portable_native_tool_runtime_closure_compiled"
        or runtime_manifest.get("runtime_content_closed") is not True
    ):
        raise ValueError("portable TianGong runtime binding drifted")

    required_flow_ids = selected.get("required_source_flow_ids")
    processes = selected.get("processes")
    required_units = selected.get("required_source_unit_contexts")
    if (
        not isinstance(required_flow_ids, list)
        or not required_flow_ids
        or len(required_flow_ids) != selected.get("required_source_flow_count")
        or len(required_flow_ids) != len(set(required_flow_ids))
        or not isinstance(processes, list)
        or len(processes) != 5
        or not isinstance(required_units, list)
        or len(required_units) != selected.get("required_source_unit_context_count")
    ):
        raise ValueError("slice Flow, Process or unit coverage invalid")

    flow_by_id = {}
    for row in flow_rows:
        flow_id = row.get("source_flow_id")
        if not isinstance(flow_id, str) or not flow_id or flow_id in flow_by_id:
            raise ValueError("Flow mapping requirements contain duplicate identity")
        flow_by_id[flow_id] = row
    if set(required_flow_ids) - set(flow_by_id):
        raise ValueError("required Flow coverage is missing from mapping ledger")

    unit_by_source_unit = {}
    unit_keys = set()
    for row in unit_rows:
        source_unit = row.get("source_unit_name")
        key = (
            row.get("source_unit_id"),
            source_unit,
            row.get("target_reference_unit"),
            row.get("flow_property_id"),
            row.get("flow_property_name"),
        )
        if (
            not all(isinstance(value, str) and value for value in key)
            or source_unit in unit_by_source_unit
            or row.get("application_status") != "proposed_not_applied"
            or not isinstance(row.get("conversion_factor"), str)
        ):
            raise ValueError("unit mapping context duplicate or drifted")
        unit_by_source_unit[source_unit] = row
        unit_keys.add(key)
    required_unit_keys = {
        (
            row.get("source_unit_id"),
            row.get("source_unit_name"),
            row.get("target_reference_unit"),
            row.get("flow_property_id"),
            row.get("flow_property_name"),
        )
        for row in required_units
    }
    if not required_unit_keys <= unit_keys:
        raise ValueError("required unit context drifted from unit mapping ledger")

    reference_flow_ids = []
    process_ids = set()
    for raw in processes:
        process = _object(raw, "slice Process")
        process_id = _text(process.get("process_id"), "source Process ID")
        reference_flow_id = _text(process.get("reference_flow_id"), "reference Flow ID")
        if process_id in process_ids or reference_flow_id not in required_flow_ids:
            raise ValueError("slice Process or reference Flow identity drifted")
        process_ids.add(process_id)
        if reference_flow_id not in reference_flow_ids:
            reference_flow_ids.append(reference_flow_id)
    ordered_flow_ids = reference_flow_ids + sorted(
        set(required_flow_ids) - set(reference_flow_ids)
    )
    repository_version = normalization["source_repository_version"]
    runtime_tree = runtime["tree_sha256"]
    flow_catalog_sha = refs[5]["sha256"]
    process_catalog_sha = refs[6]["sha256"]
    flow_actions = []
    target_by_source_flow = {}
    for position, flow_id in enumerate(ordered_flow_ids, start=1):
        source = flow_by_id[flow_id]
        source_type = source.get("source_flow_type")
        source_unit = source.get("source_reference_unit")
        if source_type not in FLOW_TYPE_MAP or source_unit not in unit_by_source_unit:
            raise ValueError("Flow type or unit context missing from explicit mapping")
        unit = unit_by_source_unit[source_unit]
        proposed = source.get("target_flow")
        if proposed is not None:
            target_source = _object(proposed, "proposed target Flow")
            target = {
                "id": _text(target_source.get("flow_id"), "target Flow ID"),
                "name": _text(target_source.get("flow_name"), "target Flow name"),
                "type_of_dataset": target_source.get("flow_type") or FLOW_TYPE_MAP[source_type],
                "flow_property": target_source.get("flow_property") or unit["flow_property_name"],
                "reference_unit": target_source.get("reference_unit") or unit["target_reference_unit"],
            }
            target_status = "prior_mapping_proposal_pending_native_preflight"
        else:
            target_name = _text(source.get("source_flow_name"), "source Flow name")
            target_type = FLOW_TYPE_MAP[source_type]
            target = {
                "id": _uuid5(
                    "flow-target",
                    repository_version,
                    flow_id,
                    target_name,
                    target_type,
                    unit["flow_property_name"],
                    unit["target_reference_unit"],
                ),
                "name": target_name,
                "type_of_dataset": target_type,
                "flow_property": unit["flow_property_name"],
                "reference_unit": unit["target_reference_unit"],
            }
            target_status = "proposed_local_uuid_pending_native_preflight"
        request = {
            "schema_version": 1,
            "target": {
                **target,
                "source_flow_id": flow_id,
                "source_repository_version": repository_version,
            },
            "candidates": [],
            "remote_candidate_search": {"enabled": False},
        }
        action_key = _action_key(
            "flow-identity-preflight", request, flow_catalog_sha, runtime_tree
        )
        action = {
            "schema_version": 1,
            "contract": FLOW_ACTION_CONTRACT,
            "position": position,
            "source_flow_id": flow_id,
            "source_flow_name": source.get("source_flow_name"),
            "source_flow_type": source_type,
            "source_reference_unit": source_unit,
            "target": target,
            "target_identity_status": target_status,
            "mapping_status": source.get("mapping_status"),
            "proposal_id": source.get("proposal_id"),
            "mapping_kind": source.get("mapping_kind"),
            "semantic_loss_markers": source.get("semantic_loss_markers", []),
            "conversion_factor": unit["conversion_factor"],
            "application_status": "proposed_not_applied",
            "candidate_catalog_sha256": flow_catalog_sha,
            "runtime_tree_sha256": runtime_tree,
            "native_command": "flow identity-preflight",
            "remote_candidate_search_enabled": False,
            "request": request,
            "action_key": action_key,
        }
        flow_actions.append(action)
        target_by_source_flow[flow_id] = target

    flow_batches = []
    for start in range(0, len(flow_actions), BATCH_SIZE):
        rows = flow_actions[start : start + BATCH_SIZE]
        batch_index = len(flow_batches) + 1
        flow_batches.append(
            {
                "batch_index": batch_index,
                "start_position": start + 1,
                "end_position": start + len(rows),
                "action_count": len(rows),
                "action_keys": [row["action_key"] for row in rows],
                "source_flow_ids": [row["source_flow_id"] for row in rows],
                "maximum_native_concurrency": MAX_CONCURRENCY,
                "remote_candidate_search_enabled": False,
                "command_failure_retry_limit": 1,
                "status": "selected_next" if batch_index == 1 else "pending_prior_batch",
            }
        )

    process_actions = []
    build_templates = []
    for index, raw in enumerate(processes, start=1):
        source = _object(raw, "slice Process")
        source_process_id = source["process_id"]
        reference_target = target_by_source_flow[source["reference_flow_id"]]
        target_process_id = _uuid5(
            "process-target",
            repository_version,
            source_process_id,
            source.get("source_process_type"),
            reference_target["id"],
        )
        target = {
            "id": target_process_id,
            "name": source.get("process_name"),
            "geography": "US",
            "technology_route": source.get("process_name"),
            "reference_flow_id": reference_target["id"],
            "reference_flow_name": reference_target["name"],
            "source_process_id": source_process_id,
            "source_process_type": source.get("source_process_type"),
            "valid_from": source.get("valid_from"),
            "valid_until": source.get("valid_until"),
        }
        request = {
            "schema_version": 1,
            "target": target,
            "candidates": [],
            "remote_candidate_search": {"enabled": False},
        }
        action_key = _action_key(
            "process-identity-preflight", request, process_catalog_sha, runtime_tree
        )
        process_action = {
            "schema_version": 1,
            "contract": PROCESS_ACTION_CONTRACT,
            "process_index": index,
            "source_process_id": source_process_id,
            "source_process_type": source.get("source_process_type"),
            "source_layer": source.get("layer"),
            "target": target,
            "reference_flow_source_id": source["reference_flow_id"],
            "reference_flow_target_id": reference_target["id"],
            "depends_on_flow_action_key": next(
                row["action_key"]
                for row in flow_actions
                if row["source_flow_id"] == source["reference_flow_id"]
            ),
            "candidate_catalog_sha256": process_catalog_sha,
            "runtime_tree_sha256": runtime_tree,
            "native_command": "process identity-preflight",
            "remote_candidate_search_enabled": False,
            "request": request,
            "action_key": action_key,
        }
        process_actions.append(process_action)
        build_key = _sha(
            _bytes(
                {
                    "logic": LOGIC_VERSION,
                    "process_action_key": action_key,
                    "flow_action_key": process_action["depends_on_flow_action_key"],
                    "runtime_tree_sha256": runtime_tree,
                }
            )
        )[:16]
        build_templates.append(
            {
                "schema_version": 1,
                "contract": BUILD_TEMPLATE_CONTRACT,
                "process_index": index,
                "source_process_id": source_process_id,
                "target": target,
                "identity_decision": {
                    "decision": "pending_native_identity_preflight",
                    "action_key": action_key,
                },
                "unit_of_analysis": {
                    "decision": "pending_reference_flow_identity_preflight",
                    "target_kind": "process",
                    "reference_flow": {
                        "id": reference_target["id"],
                        "name": reference_target["name"],
                        "reference_unit": reference_target["reference_unit"],
                        "flow_property": reference_target["flow_property"],
                    },
                },
                "name_plan": {"base_name": source.get("process_name")},
                "quantitative_reference_plan": {
                    "reference_flow_id": reference_target["id"],
                    "reference_flow_name": reference_target["name"],
                    "reference_unit": reference_target["reference_unit"],
                    "mean_amount": 1,
                    "source_amount_semantics": "one source quantitative-reference output; unit mapping proposed_not_applied",
                },
                "flow_property_plan": {
                    "reference_property": reference_target["flow_property"],
                    "reference_unit": reference_target["reference_unit"],
                },
                "system_boundary": {
                    "type": "source_USLCI_boundary_preserved_pending_native_materialization",
                    "source_process_type": source.get("source_process_type"),
                    "native_representation_policy": source.get("native_representation_policy"),
                    "provider_edges_preserved": source.get("source_process_type") == "UNIT_PROCESS",
                    "terminal_aggregated_inventory_deviation_required": source.get("source_process_type") == "LCI_RESULT",
                },
                "evidence_manifest": {
                    "source_repository_version": repository_version,
                    "source_process_id": source_process_id,
                    "integration_plan_version": integration.get("plan_version"),
                    "field_bindings": [
                        {"field_path": "target", "source": "selected_native_slice"},
                        {"field_path": "unit_of_analysis", "source": "Flow_and_unit_mapping_requirements"},
                        {"field_path": "system_boundary", "source": "source_Process_type_policy"},
                    ],
                },
                "preflight_policy": {
                    "native_command": "process build-plan validate --report-only",
                    "materialization_allowed": False,
                    "process_creation_allowed": False,
                    "model_connection_allowed": False,
                },
                "depends_on_process_action_key": action_key,
                "depends_on_flow_action_key": process_action["depends_on_flow_action_key"],
                "build_plan_action_key": build_key,
                "status": "blocked_pending_native_identity_decisions",
            }
        )

    nodes = [
        {"action_key": row["action_key"], "kind": "flow_identity_preflight"}
        for row in flow_actions
    ] + [
        {"action_key": row["action_key"], "kind": "process_identity_preflight"}
        for row in process_actions
    ] + [
        {"action_key": row["build_plan_action_key"], "kind": "process_build_plan_validate"}
        for row in build_templates
    ]
    edges = []
    for row in process_actions:
        edges.append(
            {
                "from_action_key": row["depends_on_flow_action_key"],
                "to_action_key": row["action_key"],
                "dependency": "mapped_quantitative_reference_flow_identity",
            }
        )
    for row in build_templates:
        edges.extend(
            [
                {
                    "from_action_key": row["depends_on_flow_action_key"],
                    "to_action_key": row["build_plan_action_key"],
                    "dependency": "reference_flow_identity_decision",
                },
                {
                    "from_action_key": row["depends_on_process_action_key"],
                    "to_action_key": row["build_plan_action_key"],
                    "dependency": "process_identity_decision",
                },
            ]
        )
    action_graph = {
        "schema_version": 1,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
        "ambient_workspace_inputs_allowed": False,
    }
    input_version = input_version_for_bindings(refs)
    plan_version = _sha(
        _bytes(
            {
                "logic": LOGIC_VERSION,
                "input_version": input_version,
                "flow_actions": flow_actions,
                "flow_batches": flow_batches,
                "process_actions": process_actions,
                "build_templates": build_templates,
            }
        )
    )[:16]
    return {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "status": "uslci_native_slice_preflight_plan_compiled",
        "scope_id": scope_id,
        "revision": revision,
        "input_version": input_version,
        "plan_version": plan_version,
        "integration_plan_version": integration.get("plan_version"),
        "source_repository_version": repository_version,
        "runtime_tree_sha256": runtime_tree,
        "tidas_cli_version": runtime.get("tiangong_cli", {}).get("version"),
        "input_bindings": refs,
        "local_flow_candidate_count": len(flow_catalog),
        "local_process_candidate_count": len(process_catalog),
        "flow_action_count": len(flow_actions),
        "quantitative_reference_flow_count": len(reference_flow_ids),
        "flow_batch_count": len(flow_batches),
        "maximum_flow_batch_size": BATCH_SIZE,
        "maximum_native_concurrency": MAX_CONCURRENCY,
        "first_flow_batch_action_count": len(flow_actions[:BATCH_SIZE]),
        "process_identity_action_count": len(process_actions),
        "process_build_plan_template_count": len(build_templates),
        "action_graph_node_count": len(nodes),
        "action_graph_edge_count": len(edges),
        "flow_actions": flow_actions,
        "flow_batches": flow_batches,
        "process_identity_actions": process_actions,
        "process_build_plan_templates": build_templates,
        "action_graph": action_graph,
        "first_flow_batch": flow_actions[:BATCH_SIZE],
        "network_read_executed_count": 0,
        "native_invocation_executed_count": 0,
        "flow_identity_preflight_executed_count": 0,
        "process_identity_preflight_executed_count": 0,
        "process_build_plan_validation_executed_count": 0,
        "flow_creation_executed_count": 0,
        "process_creation_executed_count": 0,
        "model_connection_executed_count": 0,
        "model_mutation_executed_count": 0,
        "external_write_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "external_patterns": [
            {"project": "apache/airflow", "local_landing": "stable_dynamic_action_expansion_batches_and_four_worker_pool", "installed": False},
            {"project": "argoproj/argo-workflows", "local_landing": "explicit_request_candidate_runtime_and_dependency_artifacts", "installed": False},
            {"project": "bazelbuild/bazel", "local_landing": "content_derived_action_keys_and_output_manifest_reuse", "installed": False},
        ],
        "routing": {"next_internal_route": NEXT_ROUTE, "pause_internal_work": False},
        "next_action": "Execute the exact first 100 Flow identity-preflight actions with four-worker bounded concurrency and content-addressed raw-result manifests.",
    }


def materialize_uslci_native_slice_preflight_plan(result, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    names = {
        "flow_actions": "uslci-native-flow-preflight-actions.jsonl",
        "flow_batches": "uslci-native-flow-preflight-batches.jsonl",
        "process_identity_actions": "uslci-native-process-preflight-actions.jsonl",
        "process_build_plan_templates": "uslci-native-process-build-plan-templates.jsonl",
        "action_graph": "uslci-native-slice-preflight-action-graph.json",
        "first_flow_batch": "uslci-native-flow-preflight-batch-1.jsonl",
    }
    paths = {}
    bindings = {}
    for key, name in names.items():
        value = result[key]
        data = (
            _bytes(value, True)
            if key == "action_graph"
            else b"".join(_bytes(row) for row in value)
        )
        path = out_dir / name
        path.write_bytes(data)
        paths[key] = path
        bindings[key] = {"path": name, "sha256": _sha(data), "bytes": len(data)}
    report = {key: value for key, value in result.items() if key not in names}
    report["artifact_bindings"] = bindings
    report_path = out_dir / "uslci-native-slice-preflight-plan.json"
    report_path.write_bytes(_bytes(report, True))
    return {"report": report_path, **paths}


def main(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "integration-report",
        "integration-report-binding",
        "selected-slice",
        "selected-slice-binding",
        "flow-mapping-requirements",
        "flow-mapping-requirements-binding",
        "unit-mapping-requirements",
        "unit-mapping-requirements-binding",
        "normalization-report",
        "normalization-report-binding",
        "local-flow-catalog",
        "local-flow-catalog-binding",
        "local-process-catalog",
        "local-process-catalog-binding",
        "runtime-report",
        "runtime-report-binding",
        "runtime-manifest",
        "runtime-manifest-binding",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    values = {}
    for stem in (
        "integration_report",
        "selected_slice",
        "flow_mapping_requirements",
        "unit_mapping_requirements",
        "normalization_report",
        "local_flow_catalog",
        "local_process_catalog",
        "runtime_report",
        "runtime_manifest",
    ):
        source = getattr(args, stem)
        binding_path = getattr(args, stem + "_binding")
        values[stem + "_bytes"] = source.read_bytes()
        values[stem + "_binding"] = _load_json(
            binding_path.read_bytes(), f"{stem} binding"
        )
    result = compile_uslci_native_slice_preflight_plan(**values)
    materialize_uslci_native_slice_preflight_plan(result, args.out_dir / "outputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
