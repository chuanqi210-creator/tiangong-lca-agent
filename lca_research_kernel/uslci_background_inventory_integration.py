"""Compile a source-complete TianGong integration plan for bounded USLCI data."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, Sequence


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_background_inventory_integration_plan/v1"
SOURCE_MANIFEST_CONTRACT = (
    "lca_research_kernel.uslci_background_source_artifact_manifest/v1"
)
SLICE_CONTRACT = "lca_research_kernel.uslci_native_contract_slice/v1"
STAGE_PLAN_CONTRACT = "lca_research_kernel.uslci_native_integration_stage_plan/v1"
LOGIC_VERSION = "2026-07-17.1"
EXPECTED_ROUTE = "compile-uslci-background-inventory-integration-plan"
NEXT_ROUTE = "preflight-uslci-native-integration-slice"
PROCESS_COUNTS = {"root": 2, "generation": 67, "unit": 313, "depth4": 200}
TYPE_POLICY = {
    "root": "UNIT_PROCESS",
    "generation": "UNIT_PROCESS",
    "unit": "UNIT_PROCESS",
    "depth4": "LCI_RESULT",
}
EDGE_COUNTS = {"root": 68, "generation": 313, "unit": 793, "depth4": 0}
REQUIRED_RULE_IDS = {
    "tidas.process.quantitative-reference.required",
    "tidas.process.exchange.amount.required",
    "tidas.process.evidence.field-bindings.required",
    "tidas.process.identity.duplicate-fingerprint.block",
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


def _sha(content):
    return hashlib.sha256(content).hexdigest()


def _file_sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return dict(value)


def _load_json(content, label):
    try:
        return _object(json.loads(content), label)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} invalid JSON") from error


def _load_jsonl(content, label, allow_empty=False):
    try:
        rows = [
            _object(json.loads(line), label)
            for line in content.decode().splitlines()
            if line
        ]
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} invalid JSONL") from error
    if not rows and not allow_empty:
        raise ValueError(f"{label} empty")
    return rows


def _iter_jsonl(path, label):
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                yield _object(json.loads(line), f"{label} row {line_number}")
            except json.JSONDecodeError as error:
                raise ValueError(f"{label} invalid JSONL row {line_number}") from error


def _binding(value, content, label):
    row = _object(value, f"{label} binding")
    if row.get("sha256") != _sha(content):
        raise ValueError(f"{label} binding hash mismatch")
    if row.get("bytes") is not None and row.get("bytes") != len(content):
        raise ValueError(f"{label} binding bytes mismatch")
    if not isinstance(row.get("path"), str) or not row["path"]:
        raise ValueError(f"{label} binding path missing")
    return {"path": row["path"], "sha256": row["sha256"], "bytes": len(content)}


def _count(row, key):
    value = row.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"invalid non-negative count: {key}")
    return value


def input_version_for_bindings(bindings):
    return _sha(_bytes({"logic": LOGIC_VERSION, "inputs": bindings}))[:16]


def _expected_artifact_keys():
    keys = {"root:0:process_observations"}
    keys.update(
        f"generation:0:{role}"
        for role in ("process_observations", "inventory_summaries", "provider_edges")
    )
    for layer, batches in (("unit", range(1, 8)), ("depth4", range(1, 5))):
        keys.update(
            f"{layer}:{batch}:{role}"
            for batch in batches
            for role in (
                "process_observations",
                "exchange_observations",
                "inventory_summaries",
                "provider_edges",
            )
        )
    return keys


def _verify_source_manifest(manifest, workspace_root):
    if (
        manifest.get("contract") != SOURCE_MANIFEST_CONTRACT
        or manifest.get("artifact_count") != 48
        or not isinstance(manifest.get("artifacts"), list)
        or len(manifest["artifacts"]) != 48
    ):
        raise ValueError("source artifact manifest must contain exact 48 artifacts")
    root = workspace_root.resolve()
    verified = []
    keys = set()
    paths = set()
    for raw in manifest["artifacts"]:
        row = _object(raw, "source artifact")
        key = row.get("artifact_key")
        layer = row.get("layer")
        batch = row.get("batch_index")
        role = row.get("role")
        expected_key = f"{layer}:{batch}:{role}"
        if (
            not isinstance(key, str)
            or key != expected_key
            or key in keys
            or row.get("process_type_policy") != TYPE_POLICY.get(layer)
            or not isinstance(row.get("expected_row_count"), int)
            or isinstance(row.get("expected_row_count"), bool)
            or row["expected_row_count"] < 0
            or not isinstance(row.get("path"), str)
            or not isinstance(row.get("sha256"), str)
            or not isinstance(row.get("bytes"), int)
        ):
            raise ValueError("source artifact duplicate, key, type policy or count invalid")
        path = (root / row["path"]).resolve()
        try:
            path.relative_to(root)
        except ValueError as error:
            raise ValueError("source artifact path escaped workspace") from error
        if row["path"] in paths:
            raise ValueError("source artifact path duplicate")
        if (
            not path.is_file()
            or path.stat().st_size != row["bytes"]
            or _file_sha(path) != row["sha256"]
        ):
            raise ValueError(f"source artifact hash or byte drift: {key}")
        with path.open(encoding="utf-8") as stream:
            line_count = sum(1 for line in stream if line.strip())
        if line_count != row["expected_row_count"]:
            raise ValueError(f"source artifact row-count drift: {key}")
        keys.add(key)
        paths.add(row["path"])
        verified.append({**row, "verified": True, "resolved_path": str(path)})
    if keys != _expected_artifact_keys():
        missing = sorted(_expected_artifact_keys() - keys)
        extra = sorted(keys - _expected_artifact_keys())
        raise ValueError(f"source artifact batch/role coverage drifted: missing={missing}, extra={extra}")
    return sorted(verified, key=lambda row: row["artifact_key"])


def _validate_direct_inputs(
    closure,
    closure_manifest,
    acquisition,
    gap,
    proposals,
    proxy,
    tidas_manifest,
    schema,
    schema_bytes,
    methodology_bytes,
    ruleset,
    ruleset_bytes,
    refs,
):
    if (
        closure.get("contract")
        != "lca_research_kernel.uslci_depth_4_layer_closure/v1"
        or closure.get("status") != "uslci_depth_4_layer_closure_compiled"
        or closure.get("bounded_process_count") != 582
        or closure.get("bounded_provider_edge_occurrence_count") != 1174
        or closure.get("bounded_uslci_provider_graph_structurally_terminated")
        is not True
        or closure.get("global_provider_absence_proven") is not False
        or closure.get("zero_upstream_burden_proven") is not False
        or closure.get("routing", {}).get("next_internal_route") != EXPECTED_ROUTE
        or closure.get("scientific_comparison_ready") is not False
        or closure.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("depth-4 closure does not authorize integration planning")
    expected_manifest_binding = _object(
        _object(closure.get("artifact_bindings"), "closure bindings").get(
            "bounded_graph_manifest"
        ),
        "bounded graph manifest binding",
    )
    if (
        expected_manifest_binding.get("sha256") != refs[1]["sha256"]
        or expected_manifest_binding.get("bytes") != refs[1]["bytes"]
    ):
        raise ValueError("closure and bounded graph manifest binding disagree")
    if (
        closure_manifest.get("contract")
        != "lca_research_kernel.uslci_bounded_provider_graph_manifest/v1"
        or closure_manifest.get("bounded_process_count") != 582
        or closure_manifest.get("bounded_provider_edge_occurrence_count") != 1174
        or closure_manifest.get("layer_process_counts")
        != {"root": 2, "generation": 67, "unit": 313, "lci_result": 200}
        or closure_manifest.get("layer_provider_edge_occurrence_counts")
        != {
            "root": 68,
            "generation_to_unit": 313,
            "unit_to_depth_4": 793,
            "depth_4_to_depth_5": 0,
        }
        or closure_manifest.get(
            "bounded_uslci_provider_graph_structurally_terminated"
        )
        is not True
        or closure_manifest.get("next_internal_route") != EXPECTED_ROUTE
    ):
        raise ValueError("bounded graph manifest drifted")
    scope_pairs = {
        (closure.get("scope_id"), closure.get("revision")),
        (closure_manifest.get("scope_id"), closure_manifest.get("revision")),
    }
    if len(scope_pairs) != 1:
        raise ValueError("closure scope or revision drifted")
    if (
        acquisition.get("contract")
        != "lca_research_kernel.selected_provider_acquisition_slice/v1"
        or acquisition.get("process_creation_allowed") is not False
        or acquisition.get("model_connection_allowed") is not False
        or gap.get("contract")
        != "lca_research_kernel.provider_flow_mapping_gap/v1"
        or gap.get("requirement_id") != acquisition.get("requirement_id")
        or gap.get("mapping_status") != "explicit_mapping_required"
        or gap.get("connection_allowed") is not False
        or gap.get("identity_rewrite_allowed") is not False
        or len(proposals) != 2
        or any(row.get("identity_rewrite_allowed") is not False for row in proposals)
        or proxy.get("contract")
        != "lca_research_kernel.uslci_proxy_applicability_assessment/v1"
        or proxy.get("decision")
        != "conditional_screening_proxy_for_structure_and_unit_pipeline_only"
        or proxy.get("scientific_comparison_ready") is not False
        or proxy.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("foreground mapping or proxy boundary drifted")
    if (
        tidas_manifest.get("kind") != "process"
        or tidas_manifest.get("profile") != "ai-import"
        or set(tidas_manifest.get("includes", []))
        != {"schema", "methodology", "ruleset"}
        or schema.get("type") != "object"
        or "processDataSet" not in schema.get("properties", {})
        or not methodology_bytes.strip()
    ):
        raise ValueError("pinned TIDAS Process contract incomplete")
    packaging_newline_normalization_count = 0
    tidas_contents = {
        "schema": schema_bytes,
        "methodology": methodology_bytes,
        "ruleset": ruleset_bytes,
    }
    for key, ref_index in (("schema", 7), ("methodology", 8), ("ruleset", 9)):
        expected = _object(tidas_manifest.get(key), f"TIDAS {key}")
        exact = (
            expected.get("sha256") != refs[ref_index]["sha256"]
            or expected.get("bytes") != refs[ref_index]["bytes"]
        )
        if not exact:
            continue
        content = tidas_contents[key]
        packaged = content[:-1] if content.endswith(b"\n") else content
        if (
            packaged == content
            or expected.get("sha256") != _sha(packaged)
            or expected.get("bytes") != len(packaged)
        ):
            raise ValueError(f"TIDAS {key} binding drifted")
        packaging_newline_normalization_count += 1
    rule_ids = {
        row.get("id")
        for row in ruleset.get("rules", [])
        if isinstance(row, dict)
    }
    if not REQUIRED_RULE_IDS <= rule_ids:
        raise ValueError("pinned TIDAS runtime ruleset lacks required integration gates")
    return (*next(iter(scope_pairs)), packaging_newline_normalization_count)


def _classification(exchange):
    value = exchange.get("classification")
    if isinstance(value, str) and value:
        return value
    flow = _object(exchange.get("flow"), "exchange flow")
    flow_type = flow.get("flow_type")
    if flow_type == "PRODUCT_FLOW":
        return "technosphere_product"
    if flow_type == "WASTE_FLOW":
        return "waste_flow_not_automatically_elementary"
    if flow_type == "ELEMENTARY_FLOW":
        return (
            "elementary_resource"
            if exchange.get("direction") == "Input"
            else "elementary_emission"
        )
    return "unclassified"


def _mapping_factor(unit_name, reference_unit):
    if unit_name == reference_unit:
        return "1"
    return {
        ("MWh", "MJ"): "3600",
        ("kg*km", "t*km"): "0.001",
        ("sh tn", "kg"): "907.18474",
    }.get((unit_name, reference_unit))


def compile_uslci_background_inventory_integration_plan(
    *,
    workspace_root,
    closure_report_bytes,
    closure_report_binding,
    closure_manifest_bytes,
    closure_manifest_binding,
    acquisition_slice_bytes,
    acquisition_slice_binding,
    flow_mapping_gap_bytes,
    flow_mapping_gap_binding,
    flow_mapping_proposals_bytes,
    flow_mapping_proposals_binding,
    proxy_assessment_bytes,
    proxy_assessment_binding,
    tidas_contract_manifest_bytes,
    tidas_contract_manifest_binding,
    tidas_schema_bytes,
    tidas_schema_binding,
    tidas_methodology_bytes,
    tidas_methodology_binding,
    tidas_ruleset_bytes,
    tidas_ruleset_binding,
    source_artifact_manifest_bytes,
    source_artifact_manifest_binding,
):
    contents = (
        (closure_report_bytes, closure_report_binding, "closure report"),
        (closure_manifest_bytes, closure_manifest_binding, "closure manifest"),
        (acquisition_slice_bytes, acquisition_slice_binding, "acquisition slice"),
        (flow_mapping_gap_bytes, flow_mapping_gap_binding, "flow mapping gap"),
        (flow_mapping_proposals_bytes, flow_mapping_proposals_binding, "flow mapping proposals"),
        (proxy_assessment_bytes, proxy_assessment_binding, "proxy assessment"),
        (tidas_contract_manifest_bytes, tidas_contract_manifest_binding, "TIDAS contract manifest"),
        (tidas_schema_bytes, tidas_schema_binding, "TIDAS schema"),
        (tidas_methodology_bytes, tidas_methodology_binding, "TIDAS methodology"),
        (tidas_ruleset_bytes, tidas_ruleset_binding, "TIDAS ruleset"),
        (source_artifact_manifest_bytes, source_artifact_manifest_binding, "source artifact manifest"),
    )
    refs = [_binding(binding, content, label) for content, binding, label in contents]
    closure = _load_json(closure_report_bytes, "closure report")
    closure_manifest = _load_json(closure_manifest_bytes, "closure manifest")
    acquisition = _load_json(acquisition_slice_bytes, "acquisition slice")
    gap = _load_json(flow_mapping_gap_bytes, "flow mapping gap")
    proposals = _load_jsonl(flow_mapping_proposals_bytes, "flow mapping proposals")
    proxy = _load_json(proxy_assessment_bytes, "proxy assessment")
    tidas_manifest = _load_json(tidas_contract_manifest_bytes, "TIDAS contract manifest")
    schema = _load_json(tidas_schema_bytes, "TIDAS schema")
    ruleset = _load_json(tidas_ruleset_bytes, "TIDAS ruleset")
    source_manifest = _load_json(source_artifact_manifest_bytes, "source artifact manifest")
    scope_id, revision, tidas_packaging_newline_normalization_count = _validate_direct_inputs(
        closure,
        closure_manifest,
        acquisition,
        gap,
        proposals,
        proxy,
        tidas_manifest,
        schema,
        tidas_schema_bytes,
        tidas_methodology_bytes,
        ruleset,
        tidas_ruleset_bytes,
        refs,
    )
    if (
        source_manifest.get("scope_id") != scope_id
        or source_manifest.get("revision") != revision
    ):
        raise ValueError("source manifest scope or revision drifted")
    artifacts = _verify_source_manifest(source_manifest, Path(workspace_root))
    artifact_map = {row["artifact_key"]: row for row in artifacts}

    process_rows = {}
    process_ids_by_layer = defaultdict(set)
    process_ids_by_layer_batch = defaultdict(set)
    process_exchange_expected = {}
    process_exchange_observed = defaultdict(int)
    process_flow_ids = defaultdict(set)
    process_unit_contexts = defaultdict(set)
    process_ledger = []
    flow_stats = {}
    unit_stats = {}
    root_edges = []

    def observe_exchange(layer, exchange, injected_process_id=None):
        process_id = injected_process_id or exchange.get("process_id")
        if process_id not in process_rows:
            raise ValueError("exchange references unknown Process")
        flow = _object(exchange.get("flow"), "exchange flow")
        flow_id = flow.get("flow_id")
        if not isinstance(flow_id, str) or not flow_id:
            raise ValueError("exchange Flow ID missing")
        definition = (
            flow.get("flow_name"),
            flow.get("flow_type"),
            flow.get("reference_unit"),
        )
        if flow_id not in flow_stats:
            flow_stats[flow_id] = {
                "definitions": set(),
                "layers": set(),
                "classifications": set(),
                "observed_exchange_count": 0,
            }
        stats = flow_stats[flow_id]
        stats["definitions"].add(definition)
        stats["layers"].add(layer)
        stats["classifications"].add(_classification(exchange))
        stats["observed_exchange_count"] += 1
        unit = _object(exchange.get("unit"), "exchange unit")
        prop = _object(exchange.get("flow_property"), "flow property")
        unit_key = (
            unit.get("unit_id"),
            unit.get("unit_name"),
            prop.get("reference_unit"),
            prop.get("flow_property_id"),
            prop.get("flow_property_name"),
        )
        if not all(isinstance(value, str) and value for value in unit_key):
            raise ValueError("source unit or Flow-property context incomplete")
        if unit_key not in unit_stats:
            unit_stats[unit_key] = {"layers": set(), "observed_exchange_count": 0}
        unit_stats[unit_key]["layers"].add(layer)
        unit_stats[unit_key]["observed_exchange_count"] += 1
        process_exchange_observed[process_id] += 1
        process_flow_ids[process_id].add(flow_id)
        process_unit_contexts[process_id].add(unit_key)
        provider = exchange.get("default_provider")
        if layer == "root" and isinstance(provider, dict):
            root_edges.append(
                {
                    "source_process_id": process_id,
                    "provider_process_id": provider.get("id"),
                    "flow_id": flow_id,
                    "amount": str(exchange.get("amount")),
                    "unit": unit.get("unit_name"),
                    "source_exchange_internal_id": exchange.get(
                        "exchange_internal_id", exchange.get("internal_id")
                    ),
                }
            )

    process_artifacts = [
        row for row in artifacts if row["role"] == "process_observations"
    ]
    for artifact in process_artifacts:
        layer = artifact["layer"]
        batch = artifact["batch_index"]
        for row in _iter_jsonl(Path(artifact["resolved_path"]), artifact["artifact_key"]):
            process_id = row.get("process_id")
            if (
                not isinstance(process_id, str)
                or not process_id
                or process_id in process_rows
                or row.get("process_type") != TYPE_POLICY[layer]
            ):
                raise ValueError(f"Process identity or type policy drifted in {artifact['artifact_key']}")
            process_rows[process_id] = row
            process_ids_by_layer[layer].add(process_id)
            process_ids_by_layer_batch[(layer, batch)].add(process_id)
            process_exchange_expected[process_id] = _count(row, "exchange_count")
            reference = _object(row.get("reference_exchange"), "reference exchange")
            reference_flow = _object(reference.get("flow"), "reference Flow")
            process_ledger.append(
                {
                    "process_id": process_id,
                    "process_name": row.get("process_name"),
                    "source_process_type": row.get("process_type"),
                    "native_representation_policy": (
                        "unit_process_preserve_provider_edges"
                        if row.get("process_type") == "UNIT_PROCESS"
                        else "terminal_aggregated_inventory_process_with_deviation_note"
                    ),
                    "layer": layer,
                    "batch_index": batch,
                    "exchange_count": process_exchange_expected[process_id],
                    "reference_flow_id": reference_flow.get("flow_id"),
                    "reference_flow_name": reference_flow.get("flow_name"),
                    "reference_unit": reference_flow.get("reference_unit"),
                    "valid_from": row.get("valid_from"),
                    "valid_until": row.get("valid_until"),
                }
            )
            if layer in {"root", "generation"}:
                exchanges = row.get("exchanges")
                if not isinstance(exchanges, list):
                    raise ValueError("root/generation Process exchanges missing")
                for exchange in exchanges:
                    observe_exchange(layer, _object(exchange, "embedded exchange"), process_id)
    if {layer: len(ids) for layer, ids in process_ids_by_layer.items()} != PROCESS_COUNTS:
        raise ValueError("source Process layer counts drifted")

    for artifact in artifacts:
        if artifact["role"] != "exchange_observations":
            continue
        for exchange in _iter_jsonl(Path(artifact["resolved_path"]), artifact["artifact_key"]):
            observe_exchange(artifact["layer"], exchange)
    if any(
        process_exchange_observed[process_id] != expected
        for process_id, expected in process_exchange_expected.items()
    ):
        raise ValueError("Process exchange counts and layer-local observations disagree")

    for artifact in artifacts:
        if artifact["role"] != "inventory_summaries":
            continue
        observed_ids = set()
        for summary in _iter_jsonl(Path(artifact["resolved_path"]), artifact["artifact_key"]):
            process_id = summary.get("process_id")
            if process_id in observed_ids or process_id not in process_ids_by_layer_batch[(artifact["layer"], artifact["batch_index"])]:
                raise ValueError("inventory summary Process coverage drifted")
            if summary.get("exchange_count") is not None and summary.get("exchange_count") != process_exchange_expected[process_id]:
                raise ValueError("inventory summary exchange count drifted")
            observed_ids.add(process_id)
        if observed_ids != process_ids_by_layer_batch[(artifact["layer"], artifact["batch_index"])]:
            raise ValueError("inventory summaries do not cover exact Process batch")

    provider_edges = {"root": root_edges, "generation": [], "unit": [], "depth4": []}
    for artifact in artifacts:
        if artifact["role"] != "provider_edges":
            continue
        provider_edges[artifact["layer"]].extend(
            _iter_jsonl(Path(artifact["resolved_path"]), artifact["artifact_key"])
        )
    layer_targets = {
        "root": process_ids_by_layer["root"] | process_ids_by_layer["generation"],
        "generation": process_ids_by_layer["unit"],
        "unit": process_ids_by_layer["depth4"],
        "depth4": set(),
    }
    layer_sources = {
        "root": process_ids_by_layer["root"],
        "generation": process_ids_by_layer["generation"],
        "unit": process_ids_by_layer["unit"],
        "depth4": process_ids_by_layer["depth4"],
    }
    for layer, edges in provider_edges.items():
        if len(edges) != EDGE_COUNTS[layer]:
            raise ValueError(f"{layer} Provider-edge count drifted")
        for edge in edges:
            if (
                edge.get("source_process_id") not in layer_sources[layer]
                or edge.get("provider_process_id") not in layer_targets[layer]
            ):
                raise ValueError(f"{layer} Provider edge escapes bounded Process layers")
    if any(len(stats["definitions"]) != 1 for stats in flow_stats.values()):
        raise ValueError("source Flow identity definition conflict")

    proposals_by_source = {}
    for proposal in proposals:
        source = _object(proposal.get("source_flow"), "proposal source Flow")
        flow_id = source.get("flow_id")
        if flow_id in proposals_by_source or flow_id not in flow_stats:
            raise ValueError("mapping proposal source Flow duplicate or absent")
        proposals_by_source[flow_id] = proposal
    flow_requirements = []
    for flow_id in sorted(flow_stats):
        stats = flow_stats[flow_id]
        definition = next(iter(stats["definitions"]))
        proposal = proposals_by_source.get(flow_id)
        flow_requirements.append(
            {
                "source_flow_id": flow_id,
                "source_flow_name": definition[0],
                "source_flow_type": definition[1],
                "source_reference_unit": definition[2],
                "observed_layers": sorted(stats["layers"]),
                "observed_exchange_count": stats["observed_exchange_count"],
                "classifications": sorted(stats["classifications"]),
                "mapping_status": (
                    "prior_proposal_requires_native_preflight"
                    if proposal
                    else "unmapped_requires_native_identity_preflight_or_authoring"
                ),
                "proposal_id": proposal.get("proposal_id") if proposal else None,
                "mapping_kind": proposal.get("mapping_kind") if proposal else None,
                "target_flow": proposal.get("target_flow") if proposal else None,
                "semantic_loss_markers": proposal.get("semantic_loss_markers", []) if proposal else [],
                "identity_rewrite_allowed": False,
                "characterization_ready": False,
                "application_status": "proposed_not_applied",
            }
        )
    unit_requirements = []
    unresolved_units = 0
    for key in sorted(unit_stats, key=lambda value: tuple(str(item) for item in value)):
        unit_id, unit_name, reference_unit, property_id, property_name = key
        factor = _mapping_factor(unit_name, reference_unit)
        if factor is None:
            unresolved_units += 1
        stats = unit_stats[key]
        unit_requirements.append(
            {
                "source_unit_id": unit_id,
                "source_unit_name": unit_name,
                "flow_property_id": property_id,
                "flow_property_name": property_name,
                "target_reference_unit": reference_unit,
                "conversion_factor": factor,
                "mapping_status": (
                    "deterministic_conversion_proposed_not_applied"
                    if factor is not None
                    else "unknown_conversion_requires_review"
                ),
                "observed_layers": sorted(stats["layers"]),
                "observed_exchange_count": stats["observed_exchange_count"],
                "application_status": "proposed_not_applied",
            }
        )

    root_by_source = defaultdict(list)
    generation_by_source = defaultdict(list)
    unit_by_source = defaultdict(list)
    for edge in provider_edges["root"]:
        root_by_source[edge["source_process_id"]].append(edge)
    for edge in provider_edges["generation"]:
        generation_by_source[edge["source_process_id"]].append(edge)
    for edge in provider_edges["unit"]:
        unit_by_source[edge["source_process_id"]].append(edge)
    at_user_ids = sorted(
        process_id
        for process_id in process_ids_by_layer["root"]
        if process_rows[process_id].get("role") == "selected_at_user_candidate"
    )
    if len(at_user_ids) != 1:
        raise ValueError("selected at-user root Process not unique")
    at_user = at_user_ids[0]
    user_edges = sorted(
        root_by_source[at_user], key=lambda row: row["provider_process_id"]
    )
    if len(user_edges) != 1 or user_edges[0]["provider_process_id"] not in process_ids_by_layer["root"]:
        raise ValueError("at-user to at-grid Provider edge not exact")
    at_grid = user_edges[0]["provider_process_id"]
    chosen = None
    for root_edge in sorted(root_by_source[at_grid], key=lambda row: row["provider_process_id"]):
        generation_id = root_edge["provider_process_id"]
        for generation_edge in sorted(
            generation_by_source[generation_id],
            key=lambda row: row["provider_process_id"],
        ):
            unit_id = generation_edge["provider_process_id"]
            for unit_edge in sorted(
                unit_by_source[unit_id], key=lambda row: row["provider_process_id"]
            ):
                chosen = (
                    root_edge,
                    generation_edge,
                    unit_edge,
                    generation_id,
                    unit_id,
                    unit_edge["provider_process_id"],
                )
                break
            if chosen:
                break
        if chosen:
            break
    if chosen is None:
        raise ValueError("no complete root-to-terminal Provider path exists")
    root_edge, generation_edge, unit_edge, generation_id, unit_id, terminal_id = chosen
    slice_ids = [at_user, at_grid, generation_id, unit_id, terminal_id]
    slice_edges = [user_edges[0], root_edge, generation_edge, unit_edge]
    required_flow_ids = sorted(
        set().union(*(process_flow_ids[process_id] for process_id in slice_ids))
    )
    required_unit_keys = sorted(
        set().union(*(process_unit_contexts[process_id] for process_id in slice_ids)),
        key=lambda value: tuple(str(item) for item in value),
    )
    process_ledger_by_id = {row["process_id"]: row for row in process_ledger}
    selected_slice = {
        "schema_version": 1,
        "contract": SLICE_CONTRACT,
        "slice_id": NEXT_ROUTE,
        "scope_id": scope_id,
        "revision": revision,
        "selection_semantics": "lexicographic_complete_provider_path_for_native_contract_coverage_only",
        "environmental_contribution_claimed": False,
        "inventory_complete": False,
        "process_count": 5,
        "provider_edge_count": 4,
        "processes": [process_ledger_by_id[process_id] for process_id in slice_ids],
        "provider_edges": slice_edges,
        "required_source_flow_ids": required_flow_ids,
        "required_source_flow_count": len(required_flow_ids),
        "required_source_unit_contexts": [
            {
                "source_unit_id": key[0],
                "source_unit_name": key[1],
                "target_reference_unit": key[2],
                "flow_property_id": key[3],
                "flow_property_name": key[4],
            }
            for key in required_unit_keys
        ],
        "required_source_unit_context_count": len(required_unit_keys),
        "foreground_requirement_id": acquisition.get("requirement_id"),
        "foreground_local_flow": gap.get("local_flow"),
        "foreground_connection_allowed": False,
        "process_creation_allowed": False,
        "native_identity_preflight_allowed": True,
        "native_build_plan_allowed": True,
        "native_materialization_allowed": False,
        "model_connection_allowed": False,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
    }
    stage_plan = {
        "schema_version": 1,
        "contract": STAGE_PLAN_CONTRACT,
        "scope_id": scope_id,
        "revision": revision,
        "stages": [
            {"stage": "bind_source_graph", "status": "completed", "required_process_count": 582, "required_artifact_count": 48},
            {"stage": "preflight_native_contract_slice", "status": "next", "process_count": 5, "flow_count": len(required_flow_ids), "unit_context_count": len(required_unit_keys)},
            {"stage": "materialize_native_contract_slice", "status": "blocked_pending_preflight", "process_count": 5},
            {"stage": "materialize_bounded_graph_in_batches", "status": "blocked_pending_slice_validation", "process_count": 582},
            {"stage": "connect_foreground_provider", "status": "prohibited_pending_mapping_proxy_and_scientific_review", "requirement_id": acquisition.get("requirement_id")},
        ],
        "next_internal_route": NEXT_ROUTE,
    }

    input_version = input_version_for_bindings(refs)
    plan_version = _sha(
        _bytes(
            {
                "logic": LOGIC_VERSION,
                "input_version": input_version,
                "source_artifacts": [
                    {key: row[key] for key in ("artifact_key", "sha256", "bytes")}
                    for row in artifacts
                ],
                "processes": process_ledger,
                "flows": flow_requirements,
                "units": unit_requirements,
                "slice": selected_slice,
            }
        )
    )[:16]
    exchange_count = sum(process_exchange_observed.values())
    return {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "status": "uslci_background_inventory_integration_plan_compiled",
        "scope_id": scope_id,
        "revision": revision,
        "input_version": input_version,
        "plan_version": plan_version,
        "input_bindings": refs,
        "source_artifact_count": len(artifacts),
        "process_count": len(process_rows),
        "unit_process_count": len(process_ids_by_layer["root"] | process_ids_by_layer["generation"] | process_ids_by_layer["unit"]),
        "terminal_lci_result_process_count": len(process_ids_by_layer["depth4"]),
        "exchange_observation_count": exchange_count,
        "provider_edge_occurrence_count": sum(len(rows) for rows in provider_edges.values()),
        "unique_source_flow_count": len(flow_requirements),
        "source_product_flow_count": sum(row["source_flow_type"] == "PRODUCT_FLOW" for row in flow_requirements),
        "source_waste_flow_count": sum(row["source_flow_type"] == "WASTE_FLOW" for row in flow_requirements),
        "source_elementary_flow_count": sum(row["source_flow_type"] == "ELEMENTARY_FLOW" for row in flow_requirements),
        "source_flow_definition_conflict_count": 0,
        "prior_mapping_proposal_count": len(proposals_by_source),
        "unmapped_source_flow_count": len(flow_requirements) - len(proposals_by_source),
        "source_unit_context_count": len(unit_requirements),
        "deterministic_unit_conversion_count": len(unit_requirements) - unresolved_units,
        "unresolved_unit_context_count": unresolved_units,
        "selected_native_slice_process_count": 5,
        "selected_native_slice_flow_count": len(required_flow_ids),
        "selected_native_slice_unit_context_count": len(required_unit_keys),
        "tidas_packaging_newline_normalization_count": tidas_packaging_newline_normalization_count,
        "source_artifact_inventory": [
            {key: value for key, value in row.items() if key != "resolved_path"}
            for row in artifacts
        ],
        "process_type_ledger": sorted(process_ledger, key=lambda row: (row["layer"], row["batch_index"], row["process_id"])),
        "flow_mapping_requirements": flow_requirements,
        "unit_mapping_requirements": unit_requirements,
        "selected_native_slice": selected_slice,
        "integration_stage_plan": stage_plan,
        "network_read_executed_count": 0,
        "process_creation_executed_count": 0,
        "flow_creation_executed_count": 0,
        "native_invocation_executed_count": 0,
        "model_connection_executed_count": 0,
        "model_mutation_executed_count": 0,
        "external_write_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "external_patterns": [
            {"project": "dbt-labs/dbt-core", "local_landing": "source_artifact_manifest_and_exact_dependency_keys", "installed": False},
            {"project": "OpenRefine/OpenRefine", "local_landing": "serializable_unapplied_flow_and_unit_mapping_recipes", "installed": False},
            {"project": "helm/helm", "local_landing": "plan_slice_preflight_materialize_then_batched_expansion", "installed": False},
        ],
        "routing": {"next_internal_route": NEXT_ROUTE, "pause_internal_work": False},
        "next_action": "Run TianGong-native identity, schema and build-plan preflights for the exact five-Process contract slice before materialization.",
    }


def materialize_uslci_background_inventory_integration_plan(result, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    names = {
        "source_artifact_inventory": "uslci-background-source-artifact-inventory.jsonl",
        "process_type_ledger": "uslci-background-process-type-ledger.jsonl",
        "flow_mapping_requirements": "uslci-background-flow-mapping-requirements.jsonl",
        "unit_mapping_requirements": "uslci-background-unit-mapping-requirements.jsonl",
        "selected_native_slice": "selected-uslci-native-contract-slice.json",
        "integration_stage_plan": "uslci-native-integration-stage-plan.json",
    }
    paths = {}
    bindings = {}
    for key, name in names.items():
        value = result[key]
        content = (
            _bytes(value, True)
            if key in {"selected_native_slice", "integration_stage_plan"}
            else b"".join(_bytes(row) for row in value)
        )
        path = out_dir / name
        path.write_bytes(content)
        paths[key] = path
        bindings[key] = {"path": name, "sha256": _sha(content), "bytes": len(content)}
    report = {key: value for key, value in result.items() if key not in names}
    report["artifact_bindings"] = bindings
    report_path = out_dir / "uslci-background-inventory-integration-plan.json"
    report_path.write_bytes(_bytes(report, True))
    return {"report": report_path, **paths}


def main(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", type=Path, required=True)
    for name in (
        "closure-report",
        "closure-report-binding",
        "closure-manifest",
        "closure-manifest-binding",
        "acquisition-slice",
        "acquisition-slice-binding",
        "flow-mapping-gap",
        "flow-mapping-gap-binding",
        "flow-mapping-proposals",
        "flow-mapping-proposals-binding",
        "proxy-assessment",
        "proxy-assessment-binding",
        "tidas-contract-manifest",
        "tidas-contract-manifest-binding",
        "tidas-schema",
        "tidas-schema-binding",
        "tidas-methodology",
        "tidas-methodology-binding",
        "tidas-ruleset",
        "tidas-ruleset-binding",
        "source-artifact-manifest",
        "source-artifact-manifest-binding",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    values = {"workspace_root": args.workspace_root}
    for stem in (
        "closure_report",
        "closure_manifest",
        "acquisition_slice",
        "flow_mapping_gap",
        "flow_mapping_proposals",
        "proxy_assessment",
        "tidas_contract_manifest",
        "tidas_schema",
        "tidas_methodology",
        "tidas_ruleset",
        "source_artifact_manifest",
    ):
        source = getattr(args, stem)
        binding = getattr(args, stem + "_binding")
        values[stem + "_bytes"] = source.read_bytes()
        values[stem + "_binding"] = _load_json(binding.read_bytes(), f"{stem} binding")
    result = compile_uslci_background_inventory_integration_plan(**values)
    materialize_uslci_background_inventory_integration_plan(result, args.out_dir / "outputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
