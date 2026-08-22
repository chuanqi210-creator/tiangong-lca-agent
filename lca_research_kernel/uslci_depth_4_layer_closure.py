"""Compile the depth-4 USLCI closure and bounded-graph handoff."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Dict, Sequence


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_depth_4_layer_closure/v1"
MANIFEST_CONTRACT = (
    "lca_research_kernel.uslci_bounded_provider_graph_manifest/v1"
)
LOGIC_VERSION = "2026-07-17.1"
NORMALIZATION_CONTRACT = (
    "lca_research_kernel.uslci_provider_normalization_plan/v1"
)
GENERATION_CONTRACT = (
    "lca_research_kernel.uslci_generation_provider_closure/v1"
)
CURRENT_CLOSURE_CONTRACT = (
    "lca_research_kernel.uslci_unit_provider_current_breadth_closure/v1"
)
DEPTH_4_BATCH_CONTRACT = (
    "lca_research_kernel.uslci_depth_4_provider_cumulative_batch/v1"
)
EXPECTED_ROUTE = "close-uslci-depth-4-provider-layer"
INTEGRATION_ROUTE = "compile-uslci-background-inventory-integration-plan"
DEPTH_5_ROUTE = "probe-uslci-depth-5-provider-closure-batch-1"
ROOT_PROCESS_COUNT = 2
ROOT_PROVIDER_EDGE_COUNT = 68
GENERATION_PROCESS_COUNT = 67
GENERATION_PROVIDER_EDGE_COUNT = 313
UNIT_PROCESS_COUNT = 313
DEPTH_4_PROCESS_COUNT = 200
BATCH_SIZE = 50


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


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return dict(value)


def _load_json(content, label):
    try:
        return _object(json.loads(content), label)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} invalid JSON") from error


def _load_jsonl_allow_empty(content, label):
    try:
        return [
            _object(json.loads(line), label)
            for line in content.decode().splitlines()
            if line
        ]
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} invalid JSONL") from error


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


def _assertion(assertion_id, status, observed, expected, explanation):
    return {
        "assertion_id": assertion_id,
        "status": status,
        "observed": observed,
        "expected": expected,
        "explanation": explanation,
    }


def input_version_for_bindings(bindings):
    """Derive the compiler/activity identity from ordered input bindings."""
    return _sha(_bytes({"logic": LOGIC_VERSION, "inputs": bindings}))[:16]


def _validate_upstream_reports(normalization, generation, current, ranked_ref):
    if (
        normalization.get("contract") != NORMALIZATION_CONTRACT
        or normalization.get("status")
        != "uslci_provider_normalization_plan_compiled"
        or normalization.get("process_count") != ROOT_PROCESS_COUNT
        or normalization.get("provider_connection_requirement_count")
        != ROOT_PROVIDER_EDGE_COUNT
        or normalization.get("upstream_generation_provider_count")
        != GENERATION_PROCESS_COUNT
        or normalization.get("provider_graph_closed") is not False
        or normalization.get("routing", {}).get("next_internal_route")
        != "probe-uslci-electricity-generation-provider-closure"
        or normalization.get("scientific_comparison_ready") is not False
        or normalization.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("normalization report does not prove the root Provider layer")
    if (
        generation.get("contract") != GENERATION_CONTRACT
        or generation.get("status")
        != "uslci_generation_provider_closure_layer_observed"
        or generation.get("fetched_process_count") != GENERATION_PROCESS_COUNT
        or generation.get("observed_provider_edge_count")
        != GENERATION_PROVIDER_EDGE_COUNT
        or generation.get("next_frontier_process_count") != UNIT_PROCESS_COUNT
        or generation.get("provider_graph_closed") is not False
        or generation.get("routing", {}).get("next_internal_route")
        != "probe-uslci-electricity-unit-provider-closure-batch"
        or generation.get("scientific_comparison_ready") is not False
        or generation.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("generation report does not prove the generation Provider layer")
    if (
        current.get("contract") != CURRENT_CLOSURE_CONTRACT
        or current.get("status")
        != "uslci_current_breadth_layer_closed_depth_4_selected"
        or current.get("current_breadth_layer_closed") is not True
        or current.get("whole_provider_network_closed") is not False
        or current.get("current_layer_observed_process_count")
        != UNIT_PROCESS_COUNT
        or current.get("depth_4_provider_process_count")
        != DEPTH_4_PROCESS_COUNT
        or current.get("routing", {}).get("next_internal_route")
        != "probe-uslci-depth-4-provider-closure-batch-1"
        or current.get("scientific_comparison_ready") is not False
        or current.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("current breadth closure does not prove the unit Provider layer")
    ranked_binding = _object(
        _object(current.get("artifact_bindings"), "current closure bindings").get(
            "ranked_depth_4_frontier"
        ),
        "ranked frontier binding",
    )
    if (
        ranked_binding.get("sha256") != ranked_ref["sha256"]
        or ranked_binding.get("bytes") != ranked_ref["bytes"]
    ):
        raise ValueError("current closure and ranked frontier binding disagree")
    scope_revision = {
        (row.get("scope_id"), row.get("revision"))
        for row in (normalization, generation, current)
    }
    if len(scope_revision) != 1 or any(
        not isinstance(value, str) or not value
        for pair in scope_revision
        for value in pair
    ):
        raise ValueError("upstream report scope or revision drifted")


def _validate_ranked_and_observed(ranked, observed):
    if len(ranked) != DEPTH_4_PROCESS_COUNT:
        raise ValueError("ranked depth-4 frontier must contain 200 rows")
    ranked_ids = []
    prior_key = None
    for index, row in enumerate(ranked, 1):
        process_id = row.get("process_id")
        incoming = row.get("incoming_edge_count")
        key = (-incoming, process_id) if isinstance(incoming, int) else None
        if (
            not isinstance(process_id, str)
            or not process_id
            or process_id in ranked_ids
            or not isinstance(incoming, int)
            or isinstance(incoming, bool)
            or incoming <= 0
            or row.get("depth") != 4
            or row.get("acquisition_rank") != index
            or row.get("acquisition_weight") != incoming
            or (prior_key is not None and key < prior_key)
        ):
            raise ValueError("ranked depth-4 frontier order or identity drifted")
        ranked_ids.append(process_id)
        prior_key = key
    if len(observed) != DEPTH_4_PROCESS_COUNT:
        raise ValueError("observed depth-4 ledger must contain 200 rows")
    observed_ids = []
    for index, row in enumerate(observed):
        process_id = row.get("process_id")
        if (
            not isinstance(process_id, str)
            or process_id in observed_ids
            or row.get("depth") != 4
            or row.get("batch_index") != index // BATCH_SIZE + 1
        ):
            raise ValueError("observed depth-4 ledger batch or identity drifted")
        observed_ids.append(process_id)
    if observed_ids != ranked_ids:
        raise ValueError("observed depth-4 ledger is not the exact ordered ranked frontier")
    return ranked_ids


def compile_uslci_depth_4_layer_closure(
    *,
    normalization_report_bytes,
    normalization_report_binding,
    generation_report_bytes,
    generation_report_binding,
    current_closure_report_bytes,
    current_closure_report_binding,
    ranked_frontier_bytes,
    ranked_frontier_binding,
    final_report_bytes,
    final_report_binding,
    observed_ids_bytes,
    observed_ids_binding,
    depth_5_frontier_bytes,
    depth_5_frontier_binding,
):
    refs = [
        _binding(
            normalization_report_binding,
            normalization_report_bytes,
            "normalization report",
        ),
        _binding(
            generation_report_binding,
            generation_report_bytes,
            "generation report",
        ),
        _binding(
            current_closure_report_binding,
            current_closure_report_bytes,
            "current breadth closure report",
        ),
        _binding(ranked_frontier_binding, ranked_frontier_bytes, "ranked frontier"),
        _binding(final_report_binding, final_report_bytes, "final depth-4 report"),
        _binding(observed_ids_binding, observed_ids_bytes, "depth-4 observed ledger"),
        _binding(
            depth_5_frontier_binding,
            depth_5_frontier_bytes,
            "depth-5 frontier",
        ),
    ]
    normalization = _load_json(normalization_report_bytes, "normalization report")
    generation = _load_json(generation_report_bytes, "generation report")
    current = _load_json(current_closure_report_bytes, "current breadth closure")
    ranked = _load_jsonl_allow_empty(ranked_frontier_bytes, "ranked frontier")
    final = _load_json(final_report_bytes, "final depth-4 report")
    observed = _load_jsonl_allow_empty(observed_ids_bytes, "depth-4 observed ledger")
    depth_5 = _load_jsonl_allow_empty(depth_5_frontier_bytes, "depth-5 frontier")

    _validate_upstream_reports(normalization, generation, current, refs[3])
    ranked_ids = _validate_ranked_and_observed(ranked, observed)
    scope_revision = (normalization.get("scope_id"), normalization.get("revision"))
    zero_effects = (
        "process_creation_executed_count",
        "flow_creation_executed_count",
        "native_invocation_executed_count",
        "model_connection_executed_count",
        "model_mutation_executed_count",
        "external_write_executed_count",
        "comparison_executed_count",
        "lcia_executed_count",
    )
    if (
        final.get("contract") != DEPTH_4_BATCH_CONTRACT
        or final.get("status")
        != "uslci_depth_4_provider_cumulative_batch_observed"
        or (final.get("scope_id"), final.get("revision")) != scope_revision
        or final.get("batch_index") != 4
        or final.get("cumulative_depth_4_observed_process_count")
        != DEPTH_4_PROCESS_COUNT
        or final.get("depth_4_remaining_process_count") != 0
        or final.get("current_cumulative_incoming_edge_coverage_percent")
        != "100.000000000000"
        or final.get("selected_next_batch_count") != 0
        or final.get("cumulative_lci_result_process_count")
        != DEPTH_4_PROCESS_COUNT
        or final.get("cumulative_process_with_elementary_inventory_count")
        != DEPTH_4_PROCESS_COUNT
        or final.get("routing", {}).get("next_internal_route") != EXPECTED_ROUTE
        or any(final.get(key) != 0 for key in zero_effects)
        or final.get("scientific_comparison_ready") is not False
        or final.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("final depth-4 report does not prove layer completion")
    final_bindings = _object(final.get("artifact_bindings"), "final artifact bindings")
    for key, ref in (
        ("cumulative_depth_4_observed_ids", refs[5]),
        ("cumulative_depth_5_frontier", refs[6]),
    ):
        expected = _object(final_bindings.get(key), key)
        if (
            expected.get("sha256") != ref["sha256"]
            or expected.get("bytes") != ref["bytes"]
        ):
            raise ValueError(f"final report and {key} binding disagree")
    if "selected_next_slice" in final_bindings:
        raise ValueError("final depth-4 report must not bind a batch-5 slice")
    if not any(
        row.get("sha256") == refs[3]["sha256"]
        for row in final.get("input_bindings", [])
    ):
        raise ValueError("final report lost the ranked frontier binding")

    scheduled = set(ranked_ids)
    depth_5_ids = []
    depth_5_incoming = 0
    for row in depth_5:
        process_id = row.get("process_id")
        incoming = row.get("incoming_edge_count")
        if (
            not isinstance(process_id, str)
            or process_id in depth_5_ids
            or process_id in scheduled
            or not isinstance(incoming, int)
            or isinstance(incoming, bool)
            or incoming <= 0
            or row.get("depth") != 5
        ):
            raise ValueError("depth-5 frontier invalid, duplicate or overlaps depth 4")
        depth_5_ids.append(process_id)
        depth_5_incoming += incoming
    if len(depth_5) != _count(final, "cumulative_depth_5_frontier_process_count"):
        raise ValueError("depth-5 frontier count disagrees with final report")
    depth_4_provider_edges = _count(final, "cumulative_provider_edge_count")
    if depth_5_incoming > depth_4_provider_edges:
        raise ValueError("depth-5 frontier incoming count exceeds Provider-edge evidence")

    unit_to_depth_4_edges = _count(current, "depth_4_incoming_edge_count")
    depth_5_empty = not depth_5
    provider_edges_zero = depth_4_provider_edges == 0
    structurally_terminated = depth_5_empty and provider_edges_zero
    next_route = INTEGRATION_ROUTE if structurally_terminated else DEPTH_5_ROUTE
    next_action = (
        "Compile a TianGong-native background-inventory integration plan from the immutable bounded USLCI graph."
        if structurally_terminated
        else "Read a governed first depth-5 Provider batch before claiming bounded structural termination."
    )
    historical = _count(final, "cumulative_historical_before_2023_count")
    depth_4_negatives = _count(final, "cumulative_negative_source_amount_count")
    current_negatives = current.get("current_layer_signed_negative_count", 0)
    if (
        not isinstance(current_negatives, int)
        or isinstance(current_negatives, bool)
        or current_negatives < 0
    ):
        raise ValueError("current-layer signed-negative count invalid")
    total_negatives = current_negatives + depth_4_negatives

    assertions = [
        _assertion("bound_upstream_lineage_exact", "satisfied", 7, 7, "Seven immutable reports/ledgers/frontiers bind the selected USLCI lineage."),
        _assertion("ranked_depth_4_frontier_exact", "satisfied", len(ranked), DEPTH_4_PROCESS_COUNT, "The ranked frontier contains 200 deterministic unique IDs."),
        _assertion("observed_depth_4_ledger_exact", "satisfied", len(observed), DEPTH_4_PROCESS_COUNT, "The observed ledger equals the ranked frontier with exact batch boundaries."),
        _assertion("depth_4_remaining_zero", "satisfied", 0, 0, "No scheduled depth-4 Process remains unobserved."),
        _assertion("depth_4_structural_weight_complete", "satisfied", "100.000000000000", "100.000000000000", "All 793 source edge occurrences are covered in the real accepted graph; this is not impact."),
        _assertion("depth_4_lci_result_type_exact", "satisfied", _count(final, "cumulative_lci_result_process_count"), DEPTH_4_PROCESS_COUNT, "All depth-4 records preserve the LCI_RESULT type boundary."),
        _assertion("depth_4_elementary_inventory_visible", "satisfied", _count(final, "cumulative_process_with_elementary_inventory_count"), DEPTH_4_PROCESS_COUNT, "Every depth-4 LCI result exposes elementary inventory."),
        _assertion("batch_5_absent", "satisfied", 0, 0, "No batch-5 slice is bound or selected."),
        _assertion("closure_side_effects_zero", "satisfied", 0, 0, "No creation, native invocation, mutation, external write, comparison or LCIA is authorized."),
        _assertion("depth_4_provider_edges_zero", "satisfied" if provider_edges_zero else "retained_blocker", depth_4_provider_edges, 0, "Declared depth-4 Provider edges must be zero for the accepted terminal LCI-result graph."),
        _assertion("depth_5_frontier_empty", "satisfied" if depth_5_empty else "retained_blocker", len(depth_5), 0, "The depth-5 frontier is an explicit content-addressed artifact."),
        _assertion("bounded_structural_termination", "satisfied" if structurally_terminated else "retained_blocker", structurally_terminated, True, "Termination applies only to the accepted lineage-scoped source graph."),
        _assertion("aggregated_result_upstream_semantics", "retained_blocker", False, True, "Zero defaultProvider on an aggregated LCI result does not mean zero upstream burden."),
        _assertion("global_provider_absence_not_proven", "retained_blocker", False, True, "No claim is made about every public, private or future Provider outside the bound lineage."),
        _assertion("temporal_fitness_not_proven", "retained_blocker", historical, 0, "Historical 2016/2020 results remain explicit and are not upgraded to 2023."),
        _assertion("cn_representativeness_not_proven", "retained_blocker", False, True, "U.S. background structure is not automatically China-representative."),
        _assertion("flow_mapping_and_characterization_unapplied", "retained_blocker", False, True, "Foreign Flow/unit mappings and characterization remain unapplied."),
        _assertion("signed_negative_review_retained", "retained_blocker", total_negatives, 0, "Signed negatives remain source-faithful uptake, credit, correction or numerical review markers."),
        _assertion("scientific_comparison_and_lcia_closed", "retained_blocker", False, True, "Scientific comparison and comparative LCIA remain prohibited."),
    ]

    input_version = input_version_for_bindings(refs)
    layer_process_counts = {
        "root": ROOT_PROCESS_COUNT,
        "generation": GENERATION_PROCESS_COUNT,
        "unit": UNIT_PROCESS_COUNT,
        "lci_result": DEPTH_4_PROCESS_COUNT,
    }
    layer_provider_edge_counts = {
        "root": ROOT_PROVIDER_EDGE_COUNT,
        "generation_to_unit": GENERATION_PROVIDER_EDGE_COUNT,
        "unit_to_depth_4": unit_to_depth_4_edges,
        "depth_4_to_depth_5": depth_4_provider_edges,
    }
    manifest = {
        "schema_version": 1,
        "contract": MANIFEST_CONTRACT,
        "logic_version": LOGIC_VERSION,
        "scope_id": scope_revision[0],
        "revision": scope_revision[1],
        "source_repository": "Federal_LCA_Commons/US_electricity_baseline",
        "source_repository_version": normalization.get(
            "source_repository_version", "1.2026-06.0"
        ),
        "lineage_scope": "selected_uslci_electricity_roots_through_ranked_depth_4",
        "input_version": input_version,
        "input_bindings": refs,
        "layer_process_counts": layer_process_counts,
        "bounded_process_count": sum(layer_process_counts.values()),
        "layer_provider_edge_occurrence_counts": layer_provider_edge_counts,
        "bounded_provider_edge_occurrence_count": sum(
            layer_provider_edge_counts.values()
        ),
        "depth_5_frontier_process_count": len(depth_5),
        "depth_5_frontier_incoming_edge_count": depth_5_incoming,
        "depth_4_layer_closed": True,
        "bounded_uslci_provider_graph_structurally_terminated": structurally_terminated,
        "global_provider_absence_proven": False,
        "zero_upstream_burden_proven": False,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "next_internal_route": next_route,
    }
    manifest["manifest_version"] = _sha(_bytes(manifest))[:16]
    closure_version = _sha(
        _bytes(
            {
                "logic": LOGIC_VERSION,
                "input_version": input_version,
                "assertions": assertions,
                "manifest_version": manifest["manifest_version"],
            }
        )
    )[:16]
    return {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "status": "uslci_depth_4_layer_closure_compiled",
        "scope_id": scope_revision[0],
        "revision": scope_revision[1],
        "input_version": input_version,
        "closure_version": closure_version,
        "parent_batch_version": final.get("batch_version"),
        "input_bindings": refs,
        "depth_4_layer_closed": True,
        "bounded_uslci_provider_graph_structurally_terminated": structurally_terminated,
        "global_provider_absence_proven": False,
        "zero_upstream_burden_proven": False,
        "bounded_process_count": manifest["bounded_process_count"],
        "bounded_provider_edge_occurrence_count": manifest[
            "bounded_provider_edge_occurrence_count"
        ],
        "root_process_count": ROOT_PROCESS_COUNT,
        "generation_process_count": GENERATION_PROCESS_COUNT,
        "unit_process_count": UNIT_PROCESS_COUNT,
        "depth_4_process_count": DEPTH_4_PROCESS_COUNT,
        "depth_4_lci_result_process_count": _count(
            final, "cumulative_lci_result_process_count"
        ),
        "depth_4_process_with_elementary_inventory_count": _count(
            final, "cumulative_process_with_elementary_inventory_count"
        ),
        "depth_4_exchange_observation_count": _count(
            final, "cumulative_exchange_observation_count"
        ),
        "depth_4_elementary_exchange_count": _count(
            final, "cumulative_elementary_exchange_count"
        ),
        "depth_4_historical_before_2023_count": historical,
        "depth_4_signed_negative_count": depth_4_negatives,
        "current_and_depth_4_signed_negative_count": total_negatives,
        "depth_4_provider_edge_count": depth_4_provider_edges,
        "depth_5_frontier_process_count": len(depth_5),
        "depth_5_frontier_incoming_edge_count": depth_5_incoming,
        "selected_next_batch_count": 0,
        "satisfied_assertion_count": sum(
            row["status"] == "satisfied" for row in assertions
        ),
        "retained_blocker_count": sum(
            row["status"] == "retained_blocker" for row in assertions
        ),
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
        "closure_assertions": assertions,
        "bounded_graph_manifest": manifest,
        "external_patterns": [
            {
                "project": "networkx/networkx",
                "local_landing": "exact_directed_frontier_termination",
                "installed": False,
            },
            {
                "project": "fivetran/great_expectations",
                "local_landing": "assertion_definition_observation_result_ledger",
                "installed": False,
            },
            {
                "project": "OpenLineage/OpenLineage",
                "local_landing": "bound_datasets_activity_and_separate_facets",
                "installed": False,
            },
        ],
        "routing": {"next_internal_route": next_route, "pause_internal_work": False},
        "next_action": next_action,
    }


def materialize_uslci_depth_4_layer_closure(result, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    assertions = b"".join(_bytes(row) for row in result["closure_assertions"])
    manifest = _bytes(result["bounded_graph_manifest"], True)
    assertion_path = out_dir / "uslci-depth-4-layer-closure-assertions.jsonl"
    manifest_path = out_dir / "uslci-bounded-provider-graph-manifest.json"
    assertion_path.write_bytes(assertions)
    manifest_path.write_bytes(manifest)
    report = {
        key: value
        for key, value in result.items()
        if key not in {"closure_assertions", "bounded_graph_manifest"}
    }
    report["artifact_bindings"] = {
        "closure_assertions": {
            "path": assertion_path.name,
            "sha256": _sha(assertions),
            "bytes": len(assertions),
        },
        "bounded_graph_manifest": {
            "path": manifest_path.name,
            "sha256": _sha(manifest),
            "bytes": len(manifest),
        },
    }
    report_path = out_dir / "uslci-depth-4-layer-closure.json"
    report_path.write_bytes(_bytes(report, True))
    return {
        "report": report_path,
        "closure_assertions": assertion_path,
        "bounded_graph_manifest": manifest_path,
    }


def main(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "normalization-report",
        "normalization-report-binding",
        "generation-report",
        "generation-report-binding",
        "current-closure-report",
        "current-closure-report-binding",
        "ranked-frontier",
        "ranked-frontier-binding",
        "final-report",
        "final-report-binding",
        "observed-ids",
        "observed-ids-binding",
        "depth-5-frontier",
        "depth-5-frontier-binding",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    values = {}
    for stem in (
        "normalization_report",
        "generation_report",
        "current_closure_report",
        "ranked_frontier",
        "final_report",
        "observed_ids",
        "depth_5_frontier",
    ):
        source = getattr(args, stem)
        binding = getattr(args, stem + "_binding")
        values[stem + "_bytes"] = source.read_bytes()
        values[stem + "_binding"] = _load_json(
            binding.read_bytes(), f"{stem} binding"
        )
    result = compile_uslci_depth_4_layer_closure(**values)
    materialize_uslci_depth_4_layer_closure(result, args.out_dir / "outputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
