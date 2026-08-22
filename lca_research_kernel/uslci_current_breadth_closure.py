"""Compile current-layer USLCI closure and select a bounded depth-4 batch."""

from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Dict, Sequence


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_unit_provider_current_breadth_closure/v1"
LOGIC_VERSION = "2026-07-17.2"
UPSTREAM_CONTRACT = "lca_research_kernel.uslci_unit_provider_closure_cumulative_batch/v1"
SELECTED_CONTRACT = "lca_research_kernel.selected_uslci_depth_4_provider_batch/v1"
EXPECTED_ROUTE = "close-uslci-unit-provider-current-breadth-layer"
NEXT_ROUTE = "probe-uslci-depth-4-provider-closure-batch-1"
FRONTIER_SIZE = 313
DEPTH_4_BATCH_SIZE = 50


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


def _load_jsonl(content, label):
    try:
        rows = [
            _object(json.loads(line), label)
            for line in content.decode().splitlines()
            if line
        ]
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} invalid JSONL") from error
    if not rows:
        raise ValueError(f"{label} empty")
    return rows


def _binding(value, content, label):
    row = _object(value, f"{label} binding")
    if row.get("sha256") != _sha(content):
        raise ValueError(f"{label} binding hash mismatch")
    if row.get("bytes") is not None and row.get("bytes") != len(content):
        raise ValueError(f"{label} binding bytes mismatch")
    if not isinstance(row.get("path"), str):
        raise ValueError(f"{label} binding path missing")
    return {"path": row["path"], "sha256": row["sha256"], "bytes": len(content)}


def _count(report, key):
    value = report.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"final cumulative count invalid: {key}")
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
    """Derive the shared compiler/activity identity from ordered input bindings."""
    return _sha(_bytes({"logic": LOGIC_VERSION, "inputs": bindings}))[:16]


def compile_uslci_current_breadth_closure(
    *,
    frontier_bytes,
    frontier_binding,
    final_report_bytes,
    final_report_binding,
    observed_ids_bytes,
    observed_ids_binding,
    deeper_frontier_bytes,
    deeper_frontier_binding,
):
    refs = [
        _binding(frontier_binding, frontier_bytes, "original frontier"),
        _binding(final_report_binding, final_report_bytes, "final cumulative report"),
        _binding(observed_ids_binding, observed_ids_bytes, "observed-ID ledger"),
        _binding(deeper_frontier_binding, deeper_frontier_bytes, "depth-4 frontier"),
    ]
    frontier = _load_jsonl(frontier_bytes, "original frontier")
    report = _load_json(final_report_bytes, "final cumulative report")
    observed = _load_jsonl(observed_ids_bytes, "observed-ID ledger")
    deeper = _load_jsonl(deeper_frontier_bytes, "depth-4 frontier")

    frontier_ids = [row.get("process_id") for row in frontier]
    if (
        len(frontier) != FRONTIER_SIZE
        or len(set(frontier_ids)) != FRONTIER_SIZE
        or not all(isinstance(process_id, str) for process_id in frontier_ids)
    ):
        raise ValueError("original frontier must contain 313 unique Process IDs")
    zero = (
        "process_creation_executed_count",
        "flow_creation_executed_count",
        "identity_preflight_executed_count",
        "build_plan_executed_count",
        "model_connection_executed_count",
        "model_mutation_executed_count",
        "external_write_executed_count",
        "comparison_executed_count",
        "lcia_executed_count",
    )
    if (
        report.get("contract") != UPSTREAM_CONTRACT
        or report.get("status") != "uslci_unit_provider_cumulative_batch_observed"
        or report.get("batch_index") != 7
        or report.get("cumulative_observed_process_count") != FRONTIER_SIZE
        or report.get("breadth_remaining_process_count") != 0
        or report.get("current_cumulative_weight_coverage_percent")
        != "100.000000000000"
        or report.get("selected_next_batch_count") != 0
        or report.get("routing", {}).get("next_internal_route") != EXPECTED_ROUTE
        or any(report.get(key) != 0 for key in zero)
        or report.get("scientific_comparison_ready") is not False
        or report.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("final cumulative report does not prove current-layer completion")
    report_bindings = _object(report.get("artifact_bindings"), "final bindings")
    for key, ref in (
        ("cumulative_observed_ids", refs[2]),
        ("cumulative_deeper_frontier", refs[3]),
    ):
        expected = _object(report_bindings.get(key), key)
        if (
            expected.get("sha256") != ref["sha256"]
            or expected.get("bytes") != ref["bytes"]
        ):
            raise ValueError(f"final report and {key} disagree")
    if "selected_next_slice" in report_bindings:
        raise ValueError("final cumulative report must not contain a batch-8 slice")
    if not any(
        row.get("sha256") == refs[0]["sha256"]
        for row in report.get("input_bindings", [])
    ):
        raise ValueError("final report is not bound to the original frontier")

    observed_ids = [row.get("process_id") for row in observed]
    if (
        len(observed_ids) != FRONTIER_SIZE
        or len(set(observed_ids)) != FRONTIER_SIZE
        or observed_ids != frontier_ids
        or any(
            not isinstance(row.get("batch_index"), int)
            or not 1 <= row["batch_index"] <= 7
            for row in observed
        )
    ):
        raise ValueError("observed ledger must be the exact ordered 313-ID frontier prefix")

    observed_set = set(observed_ids)
    deeper_ids = []
    incoming_total = 0
    normalized_deeper = []
    for row in deeper:
        process_id = row.get("process_id")
        incoming = row.get("incoming_edge_count")
        first_seen = row.get("first_seen_batch")
        if (
            not isinstance(process_id, str)
            or process_id in deeper_ids
            or process_id in observed_set
            or not isinstance(incoming, int)
            or incoming <= 0
            or not isinstance(first_seen, int)
            or not 1 <= first_seen <= 7
            or row.get("depth") != 4
        ):
            raise ValueError("depth-4 frontier is duplicate, overlaps current layer or is invalid")
        deeper_ids.append(process_id)
        incoming_total += incoming
        normalized_deeper.append(
            {
                "process_id": process_id,
                "process_name": row.get("process_name"),
                "incoming_edge_count": incoming,
                "first_seen_batch": first_seen,
                "depth": 4,
                "status": "unobserved_depth_4_frontier",
            }
        )
    if (
        len(normalized_deeper)
        != _count(report, "cumulative_deeper_frontier_process_count")
        or incoming_total != _count(report, "cumulative_deeper_provider_edge_count")
    ):
        raise ValueError("depth-4 frontier counts disagree with final report")

    ranked = []
    for rank, row in enumerate(
        sorted(
            normalized_deeper,
            key=lambda item: (-item["incoming_edge_count"], item["process_id"]),
        ),
        1,
    ):
        ranked.append(
            {
                **row,
                "acquisition_rank": rank,
                "acquisition_weight": row["incoming_edge_count"],
                "weight_semantics": "structural_incoming_edge_occurrence_count_only",
            }
        )
    selected_rows = ranked[:DEPTH_4_BATCH_SIZE]
    selected_incoming = sum(row["incoming_edge_count"] for row in selected_rows)
    selected_coverage = format(
        Decimal(selected_incoming) / Decimal(incoming_total) * 100, ".12f"
    )
    selected = {
        "contract": SELECTED_CONTRACT,
        "slice_id": NEXT_ROUTE,
        "depth": 4,
        "batch_index": 1,
        "source_repository": "Federal_LCA_Commons/US_electricity_baseline",
        "source_repository_version": "1.2026-06.0",
        "process_ids": [row["process_id"] for row in selected_rows],
        "process_count": len(selected_rows),
        "deferred_process_count": len(ranked) - len(selected_rows),
        "frontier_incoming_edge_count": incoming_total,
        "selected_incoming_edge_count": selected_incoming,
        "selected_incoming_edge_coverage_percent": selected_coverage,
        "weight_semantics": "structural_incoming_edge_occurrence_count_only_not_environmental_impact",
        "public_read_allowed": True,
        "credential_required": False,
        "external_write_allowed": False,
        "process_creation_allowed": False,
        "model_connection_allowed": False,
        "comparison_allowed": False,
        "lcia_allowed": False,
    }

    elementary_processes = _count(
        report, "cumulative_process_with_elementary_inventory_count"
    )
    signed_negative_count = _count(
        report, "cumulative_negative_source_amount_count"
    )
    assertions = [
        _assertion("source_frontier_exact", "satisfied", len(frontier), FRONTIER_SIZE, "The original current-layer frontier is exact and unique."),
        _assertion("observed_ledger_exact", "satisfied", len(observed), FRONTIER_SIZE, "The observed ledger equals the ordered source frontier."),
        _assertion("current_layer_remaining_zero", "satisfied", 0, 0, "No current-layer Process ID remains unobserved."),
        _assertion("current_layer_weight_complete", "satisfied", "100.000000000000", "100.000000000000", "Routing weight is complete for this layer only."),
        _assertion("elementary_inventory_visible", "satisfied", elementary_processes, FRONTIER_SIZE, "Every current-layer Process exposes at least one elementary exchange."),
        _assertion("same_layer_next_slice_absent", "satisfied", 0, 0, "No batch-8 slice is present."),
        _assertion("side_effects_zero", "satisfied", 0, 0, "No creation, native build, mutation, external write, comparison or LCIA ran."),
        _assertion("depth_4_frontier_remains", "retained_blocker", len(ranked), 0, "Unobserved depth-4 Provider identities remain."),
        _assertion("whole_network_not_closed", "retained_blocker", False, True, "Current-layer completion is not whole-network closure."),
        _assertion("signed_negative_review", "retained_blocker", signed_negative_count, 0, "Finite signed negatives remain source-faithful review markers."),
        _assertion("cn_representativeness_not_proven", "retained_blocker", False, True, "U.S. inventory visibility does not prove China representativeness or comparison readiness."),
    ]
    input_version = input_version_for_bindings(refs)
    closure_version = _sha(
        _bytes(
            {
                "logic": LOGIC_VERSION,
                "input_version": input_version,
                "assertions": assertions,
                "selected": selected,
            }
        )
    )[:16]
    return {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "status": "uslci_current_breadth_layer_closed_depth_4_selected",
        "scope_id": report.get("scope_id"),
        "revision": report.get("revision"),
        "input_version": input_version,
        "closure_version": closure_version,
        "parent_batch_version": report.get("batch_version"),
        "input_bindings": refs,
        "current_breadth_layer_closed": True,
        "whole_provider_network_closed": False,
        "current_layer_frontier_process_count": FRONTIER_SIZE,
        "current_layer_observed_process_count": FRONTIER_SIZE,
        "current_layer_unobserved_process_count": 0,
        "current_layer_process_with_elementary_inventory_count": elementary_processes,
        "current_layer_exchange_observation_count": _count(
            report, "cumulative_exchange_observation_count"
        ),
        "current_layer_product_exchange_count": _count(
            report, "cumulative_product_exchange_count"
        ),
        "current_layer_waste_exchange_count": _count(
            report, "cumulative_waste_exchange_count"
        ),
        "current_layer_elementary_exchange_count": _count(
            report, "cumulative_elementary_exchange_count"
        ),
        "current_layer_elementary_emission_count": _count(
            report, "cumulative_elementary_emission_count"
        ),
        "current_layer_elementary_resource_count": _count(
            report, "cumulative_elementary_resource_count"
        ),
        "current_layer_signed_negative_count": signed_negative_count,
        "current_layer_weight_coverage_percent": "100.000000000000",
        "depth_4_provider_process_count": len(ranked),
        "depth_4_incoming_edge_count": incoming_total,
        "selected_depth_4_batch_count": len(selected_rows),
        "selected_depth_4_incoming_edge_count": selected_incoming,
        "selected_depth_4_incoming_edge_coverage_percent": selected_coverage,
        "deferred_depth_4_provider_count": len(ranked) - len(selected_rows),
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
        "ranked_depth_4_frontier": ranked,
        "selected_depth_4_batch": selected,
        "routing": {"next_internal_route": NEXT_ROUTE, "pause_internal_work": False},
        "next_action": "Read the selected first 50 depth-4 Providers without creating or connecting model entities.",
    }


def materialize_uslci_current_breadth_closure(result, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    names = {
        "closure_assertions": "uslci-current-breadth-layer-closure-assertions.jsonl",
        "ranked_depth_4_frontier": "uslci-depth-4-ranked-provider-frontier.jsonl",
        "selected_depth_4_batch": "selected-uslci-depth-4-provider-batch-1.json",
    }
    paths = {}
    bindings = {}
    for key, name in names.items():
        value = result[key]
        content = (
            _bytes(value, True)
            if key == "selected_depth_4_batch"
            else b"".join(_bytes(row) for row in value)
        )
        path = out_dir / name
        path.write_bytes(content)
        paths[key] = path
        bindings[key] = {"path": name, "sha256": _sha(content), "bytes": len(content)}
    report = {key: value for key, value in result.items() if key not in names}
    report["artifact_bindings"] = bindings
    report_path = out_dir / "uslci-current-breadth-layer-closure.json"
    report_path.write_bytes(_bytes(report, True))
    return {"report": report_path, **paths}


def main(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "frontier",
        "frontier-binding",
        "final-report",
        "final-report-binding",
        "observed-ids",
        "observed-ids-binding",
        "deeper-frontier",
        "deeper-frontier-binding",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    result = compile_uslci_current_breadth_closure(
        frontier_bytes=args.frontier.read_bytes(),
        frontier_binding=_load_json(args.frontier_binding.read_bytes(), "binding"),
        final_report_bytes=args.final_report.read_bytes(),
        final_report_binding=_load_json(
            args.final_report_binding.read_bytes(), "binding"
        ),
        observed_ids_bytes=args.observed_ids.read_bytes(),
        observed_ids_binding=_load_json(
            args.observed_ids_binding.read_bytes(), "binding"
        ),
        deeper_frontier_bytes=args.deeper_frontier.read_bytes(),
        deeper_frontier_binding=_load_json(
            args.deeper_frontier_binding.read_bytes(), "binding"
        ),
    )
    materialize_uslci_current_breadth_closure(result, args.out_dir / "outputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
