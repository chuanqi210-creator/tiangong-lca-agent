"""Observe the first governed batch of USLCI depth-4 Provider Processes."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from pathlib import Path
from typing import Dict, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from lca_research_kernel.uslci_provider_probe import _fetch_one
    from lca_research_kernel.uslci_unit_provider_batch_2 import _compile_current
else:
    from .uslci_provider_probe import _fetch_one
    from .uslci_unit_provider_batch_2 import _compile_current


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_depth_4_provider_cumulative_batch/v1"
LOGIC_VERSION = "2026-07-17.1"
UPSTREAM_CONTRACT = (
    "lca_research_kernel.uslci_unit_provider_current_breadth_closure/v1"
)
SLICE_CONTRACT = "lca_research_kernel.selected_uslci_depth_4_provider_batch/v1"
EXPECTED_ROUTE = "probe-uslci-depth-4-provider-closure-batch-1"
NEXT_ROUTE = "extend-uslci-depth-4-provider-closure-to-batch-2"
REPOSITORY = "Federal_LCA_Commons/US_electricity_baseline"
FRONTIER_SIZE = 200
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


def _exact_ids(rows, expected_count, label):
    ids = [row.get("process_id") for row in rows]
    if (
        len(ids) != expected_count
        or len(set(ids)) != expected_count
        or not all(isinstance(process_id, str) for process_id in ids)
    ):
        raise ValueError(f"{label} must contain {expected_count} unique Process IDs")
    return ids


def input_version_for_bindings(bindings):
    """Derive the shared compiler/activity identity from ordered input bindings."""
    return _sha(_bytes({"logic": LOGIC_VERSION, "inputs": bindings}))[:16]


def _validate_ranked_frontier(rows):
    ids = _exact_ids(rows, FRONTIER_SIZE, "ranked depth-4 frontier")
    normalized = []
    for index, row in enumerate(rows, 1):
        incoming = row.get("incoming_edge_count")
        if (
            row.get("depth") != 4
            or row.get("acquisition_rank") != index
            or not isinstance(incoming, int)
            or isinstance(incoming, bool)
            or incoming <= 0
            or row.get("acquisition_weight") != incoming
        ):
            raise ValueError("ranked depth-4 frontier rank or weight drifted")
        normalized.append(dict(row))
    expected = sorted(
        normalized,
        key=lambda row: (-row["incoming_edge_count"], row["process_id"]),
    )
    if [row["process_id"] for row in expected] != ids:
        raise ValueError("ranked depth-4 frontier is not deterministic")
    return normalized


def _validate_selected(selected, ranked):
    ids = selected.get("process_ids")
    expected_ids = [row["process_id"] for row in ranked[:BATCH_SIZE]]
    total = sum(row["incoming_edge_count"] for row in ranked)
    selected_incoming = sum(
        row["incoming_edge_count"] for row in ranked[:BATCH_SIZE]
    )
    coverage = format(Decimal(selected_incoming) / Decimal(total) * 100, ".12f")
    if (
        selected.get("contract") != SLICE_CONTRACT
        or selected.get("slice_id") != EXPECTED_ROUTE
        or selected.get("depth") != 4
        or selected.get("batch_index") != 1
        or ids != expected_ids
        or selected.get("process_count") != BATCH_SIZE
        or selected.get("deferred_process_count") != FRONTIER_SIZE - BATCH_SIZE
        or selected.get("frontier_incoming_edge_count") != total
        or selected.get("selected_incoming_edge_count") != selected_incoming
        or selected.get("selected_incoming_edge_coverage_percent") != coverage
    ):
        raise ValueError("selected depth-4 batch is not the exact first ranked prefix")
    for key, expected in (
        ("public_read_allowed", True),
        ("credential_required", False),
        ("external_write_allowed", False),
        ("process_creation_allowed", False),
        ("model_connection_allowed", False),
        ("comparison_allowed", False),
        ("lcia_allowed", False),
    ):
        if selected.get(key) is not expected:
            raise ValueError(f"selected depth-4 authorization drifted: {key}")
    return ids


def build_depth_4_requests(selected):
    row = _object(selected, "selected depth-4 batch")
    ids = row.get("process_ids")
    if (
        row.get("contract") != SLICE_CONTRACT
        or row.get("slice_id") != EXPECTED_ROUTE
        or row.get("depth") != 4
        or row.get("batch_index") != 1
        or not isinstance(ids, list)
        or len(ids) != BATCH_SIZE
        or len(set(ids)) != BATCH_SIZE
        or row.get("public_read_allowed") is not True
        or row.get("credential_required") is not False
    ):
        raise ValueError("selected depth-4 batch must be bounded and credential-free")
    return [
        {
            "route_id": f"process:{process_id}",
            "process_id": process_id,
            "request_url": (
                "https://lcacommons.gov/lca-collaboration/ws/public/browse/"
                f"{REPOSITORY}/PROCESS/{process_id}"
            ),
            "method": "GET",
            "accept": "application/json",
        }
        for process_id in ids
    ]


def fetch_uslci_depth_4_provider_batch(
    selected, raw_root: Path, *, workers=4, attempts=3, timeout=30
):
    requests = build_depth_4_requests(selected)
    raw_root.mkdir(parents=True, exist_ok=True)
    result = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_fetch_one, request, attempts=attempts, timeout=timeout): request
            for request in requests
        }
        for future in as_completed(futures):
            request = futures[future]
            result[request["process_id"]] = future.result()[0]
    for index, request in enumerate(requests, 1):
        path = raw_root / f"{index:03d}-{request['process_id']}.json"
        path.write_bytes(_bytes(result[request["process_id"]], True))
    return result


def reuse_uslci_depth_4_provider_responses(selected, cache_root: Path, raw_root: Path):
    requests = build_depth_4_requests(selected)
    raw_root.mkdir(parents=True, exist_ok=True)
    result = {}
    for index, request in enumerate(requests, 1):
        name = f"{index:03d}-{request['process_id']}.json"
        source = cache_root / name
        if not source.is_file():
            raise ValueError(f"cached depth-4 response missing: {name}")
        envelope = _load_json(source.read_bytes(), f"cached response {name}")
        if (
            envelope.get("route_id") != request["route_id"]
            or envelope.get("http_status") != 200
            or envelope.get("error") is not None
            or envelope.get("payload", {}).get("@id") != request["process_id"]
        ):
            raise ValueError(f"cached depth-4 response invalid: {name}")
        shutil.copyfile(source, raw_root / name)
        result[request["process_id"]] = envelope
    return result


def compile_uslci_depth_4_provider_batch(
    *,
    closure_report_bytes,
    closure_report_binding,
    ranked_frontier_bytes,
    ranked_frontier_binding,
    selected_batch_bytes,
    selected_batch_binding,
    layer_3_observed_ids_bytes,
    layer_3_observed_ids_binding,
    probe_processes_bytes,
    probe_processes_binding,
    generation_processes_bytes,
    generation_processes_binding,
    raw_envelopes,
    response_reuse_count=0,
):
    refs = [
        _binding(closure_report_binding, closure_report_bytes, "closure report"),
        _binding(ranked_frontier_binding, ranked_frontier_bytes, "ranked frontier"),
        _binding(selected_batch_binding, selected_batch_bytes, "selected batch"),
        _binding(
            layer_3_observed_ids_binding,
            layer_3_observed_ids_bytes,
            "layer-3 observed ledger",
        ),
        _binding(probe_processes_binding, probe_processes_bytes, "probe Processes"),
        _binding(
            generation_processes_binding,
            generation_processes_bytes,
            "generation Processes",
        ),
    ]
    report = _load_json(closure_report_bytes, "closure report")
    ranked = _validate_ranked_frontier(
        _load_jsonl(ranked_frontier_bytes, "ranked frontier")
    )
    selected = _load_json(selected_batch_bytes, "selected batch")
    selected_ids = _validate_selected(selected, ranked)
    layer_3_rows = _load_jsonl(layer_3_observed_ids_bytes, "layer-3 ledger")
    probe_rows = _load_jsonl(probe_processes_bytes, "probe Processes")
    generation_rows = _load_jsonl(
        generation_processes_bytes, "generation Processes"
    )

    if (
        report.get("contract") != UPSTREAM_CONTRACT
        or report.get("logic_version") != "2026-07-17.2"
        or report.get("status")
        != "uslci_current_breadth_layer_closed_depth_4_selected"
        or report.get("current_breadth_layer_closed") is not True
        or report.get("whole_provider_network_closed") is not False
        or report.get("current_layer_observed_process_count") != 313
        or report.get("depth_4_provider_process_count") != FRONTIER_SIZE
        or report.get("selected_depth_4_batch_count") != BATCH_SIZE
        or report.get("routing", {}).get("next_internal_route") != EXPECTED_ROUTE
        or report.get("network_read_executed_count") != 0
        or report.get("process_creation_executed_count") != 0
        or report.get("model_connection_executed_count") != 0
        or report.get("scientific_comparison_ready") is not False
        or report.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("closure report does not authorize depth-4 batch 1")
    artifact_bindings = _object(report.get("artifact_bindings"), "closure outputs")
    for key, ref in (
        ("ranked_depth_4_frontier", refs[1]),
        ("selected_depth_4_batch", refs[2]),
    ):
        expected = _object(artifact_bindings.get(key), key)
        if (
            expected.get("sha256") != ref["sha256"]
            or expected.get("bytes") != ref["bytes"]
        ):
            raise ValueError(f"closure report and {key} disagree")
    if not any(
        row.get("sha256") == refs[3]["sha256"]
        and row.get("bytes") == refs[3]["bytes"]
        for row in report.get("input_bindings", [])
    ):
        raise ValueError("closure report is not bound to the closed layer-3 ledger")
    total_incoming = sum(row["incoming_edge_count"] for row in ranked)
    if report.get("depth_4_incoming_edge_count") != total_incoming:
        raise ValueError("closure report and ranked frontier counts disagree")

    layer_3_ids = _exact_ids(layer_3_rows, 313, "layer-3 ledger")
    probe_ids = _exact_ids(probe_rows, 2, "probe Process observations")
    generation_ids = _exact_ids(
        generation_rows, 67, "generation Process observations"
    )
    depth_4_ids = [row["process_id"] for row in ranked]
    sets = [set(probe_ids), set(generation_ids), set(layer_3_ids), set(depth_4_ids)]
    if any(sets[left] & sets[right] for left in range(4) for right in range(left + 1, 4)):
        raise ValueError("prior and scheduled Process identity sets must be disjoint")
    if set(raw_envelopes) != set(selected_ids):
        raise ValueError("exact selected depth-4 response set required")

    processes, exchanges, summaries, edges, responses = _compile_current(
        selected_ids,
        raw_envelopes,
        batch_index=1,
        layer=4,
        allowed_process_types=("LCI_RESULT",),
        time_policy="historical_through_2023",
    )
    prior_ids = sets[0] | sets[1] | sets[2]
    scheduled_depth_4_ids = sets[3]
    classified_edges = []
    novel = {}
    prior_edge_count = 0
    scheduled_edge_count = 0
    novel_edge_count = 0
    for edge in edges:
        provider_id = edge["provider_process_id"]
        if provider_id in prior_ids:
            classification = "already_observed_prior_layer"
            prior_edge_count += 1
        elif provider_id in scheduled_depth_4_ids:
            classification = "scheduled_depth_4_frontier"
            scheduled_edge_count += 1
        else:
            classification = "novel_depth_5_frontier"
            novel_edge_count += 1
            item = novel.setdefault(
                provider_id,
                {
                    "process_id": provider_id,
                    "process_name": edge["provider_process_name"],
                    "incoming_edge_count": 0,
                    "first_seen_depth_4_batch": 1,
                    "depth": 5,
                    "status": "deferred_until_depth_4_frontier_complete",
                },
            )
            item["incoming_edge_count"] += 1
        classified_edges.append({**edge, "target_classification": classification})
    depth_5_frontier = sorted(novel.values(), key=lambda row: row["process_id"])

    next_rows = ranked[BATCH_SIZE : BATCH_SIZE * 2]
    cumulative_incoming = sum(
        row["incoming_edge_count"] for row in ranked[: BATCH_SIZE * 2]
    )
    next_coverage = format(
        Decimal(cumulative_incoming) / Decimal(total_incoming) * 100, ".12f"
    )
    selected_next_slice = {
        "contract": SLICE_CONTRACT,
        "slice_id": "probe-uslci-depth-4-provider-closure-batch-2",
        "depth": 4,
        "batch_index": 2,
        "source_repository": selected.get("source_repository", REPOSITORY),
        "source_repository_version": selected.get(
            "source_repository_version", "1.2026-06.0"
        ),
        "process_ids": [row["process_id"] for row in next_rows],
        "process_count": BATCH_SIZE,
        "previously_observed_process_count": BATCH_SIZE,
        "deferred_process_count": FRONTIER_SIZE - BATCH_SIZE * 2,
        "frontier_incoming_edge_count": total_incoming,
        "cumulative_incoming_edge_count": cumulative_incoming,
        "cumulative_incoming_edge_coverage_percent": next_coverage,
        "weight_semantics": "structural_incoming_edge_occurrence_count_only_not_environmental_impact",
        "public_read_allowed": True,
        "credential_required": False,
        "external_write_allowed": False,
        "process_creation_allowed": False,
        "model_connection_allowed": False,
        "comparison_allowed": False,
        "lcia_allowed": False,
    }
    counts = {
        "exchange": len(exchanges),
        "product": sum(row["product_exchange_count"] for row in summaries),
        "waste": sum(row["waste_exchange_count"] for row in summaries),
        "elementary": sum(row["elementary_exchange_count"] for row in summaries),
        "emission": sum(row["elementary_emission_count"] for row in summaries),
        "resource": sum(row["elementary_resource_count"] for row in summaries),
        "negative": sum(row["negative_source_amount_count"] for row in summaries),
    }
    if (
        not isinstance(response_reuse_count, int)
        or isinstance(response_reuse_count, bool)
        or not 0 <= response_reuse_count <= BATCH_SIZE
    ):
        raise ValueError("response reuse count invalid")
    valid_until_year_counts = {}
    for process in processes:
        year = process["valid_until"][:4]
        valid_until_year_counts[year] = valid_until_year_counts.get(year, 0) + 1
    covering_2023_count = sum(
        process["valid_from"] <= "2023-12-31" <= process["valid_until"]
        for process in processes
    )
    input_version = input_version_for_bindings(refs)
    batch_version = _sha(
        _bytes(
            {
                "logic": LOGIC_VERSION,
                "inputs": refs,
                "responses": responses,
                "edge_classification": classified_edges,
            }
        )
    )[:16]
    return {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "status": "uslci_depth_4_provider_cumulative_batch_observed",
        "scope_id": report.get("scope_id"),
        "revision": report.get("revision"),
        "input_version": input_version,
        "batch_version": batch_version,
        "batch_index": 1,
        "parent_closure_version": report.get("closure_version"),
        "input_bindings": refs,
        "response_bindings": responses,
        "public_read_count": BATCH_SIZE,
        "successful_public_read_count": BATCH_SIZE,
        "network_read_executed_count": BATCH_SIZE - response_reuse_count,
        "response_reuse_count": response_reuse_count,
        "fetched_process_count": BATCH_SIZE,
        "prior_observed_process_count": len(prior_ids),
        "scheduled_process_universe_count": len(prior_ids | scheduled_depth_4_ids),
        "batch_process_with_elementary_inventory_count": sum(
            row["has_elementary_inventory"] for row in summaries
        ),
        "batch_lci_result_process_count": sum(
            row["process_type"] == "LCI_RESULT" for row in processes
        ),
        "batch_valid_until_year_counts": valid_until_year_counts,
        "batch_process_covering_2023_count": covering_2023_count,
        "batch_historical_before_2023_count": BATCH_SIZE - covering_2023_count,
        "batch_exchange_observation_count": counts["exchange"],
        "batch_product_exchange_count": counts["product"],
        "batch_waste_exchange_count": counts["waste"],
        "batch_elementary_exchange_count": counts["elementary"],
        "batch_elementary_emission_count": counts["emission"],
        "batch_elementary_resource_count": counts["resource"],
        "batch_negative_source_amount_count": counts["negative"],
        "batch_provider_edge_count": len(classified_edges),
        "prior_layer_back_edge_count": prior_edge_count,
        "scheduled_depth_4_cross_edge_count": scheduled_edge_count,
        "novel_depth_5_provider_edge_count": novel_edge_count,
        "depth_5_frontier_process_count": len(depth_5_frontier),
        "depth_4_frontier_process_count": FRONTIER_SIZE,
        "depth_4_observed_process_count": BATCH_SIZE,
        "depth_4_remaining_process_count": FRONTIER_SIZE - BATCH_SIZE,
        "current_cumulative_incoming_edge_coverage_percent": selected[
            "selected_incoming_edge_coverage_percent"
        ],
        "selected_next_batch_count": BATCH_SIZE,
        "next_cumulative_incoming_edge_coverage_percent": next_coverage,
        "provider_graph_closed": False,
        "process_creation_executed_count": 0,
        "flow_creation_executed_count": 0,
        "identity_preflight_executed_count": 0,
        "build_plan_executed_count": 0,
        "native_invocation_executed_count": 0,
        "model_connection_executed_count": 0,
        "model_mutation_executed_count": 0,
        "external_write_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "batch_process_observations": processes,
        "batch_exchange_observations": exchanges,
        "batch_inventory_summaries": summaries,
        "batch_provider_edges": classified_edges,
        "cumulative_depth_4_observed_ids": [
            {"process_id": process_id, "depth": 4, "batch_index": 1}
            for process_id in selected_ids
        ],
        "cumulative_depth_5_frontier": depth_5_frontier,
        "selected_next_slice": selected_next_slice,
        "routing": {"next_internal_route": NEXT_ROUTE, "pause_internal_work": False},
        "next_action": (
            "Continue with exact depth-4 ranks 51-100 while keeping novel depth-5 "
            "Providers deferred."
        ),
    }


def materialize_uslci_depth_4_provider_batch(result, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    names = {
        "batch_process_observations": "uslci-depth-4-batch-1-process-observations.jsonl",
        "batch_exchange_observations": "uslci-depth-4-batch-1-exchange-observations.jsonl",
        "batch_inventory_summaries": "uslci-depth-4-batch-1-inventory-summaries.jsonl",
        "batch_provider_edges": "uslci-depth-4-batch-1-provider-edges.jsonl",
        "cumulative_depth_4_observed_ids": "uslci-depth-4-cumulative-observed-process-ids.jsonl",
        "cumulative_depth_5_frontier": "uslci-depth-5-cumulative-provider-frontier.jsonl",
        "selected_next_slice": "selected-uslci-depth-4-provider-batch-2.json",
    }
    paths = {}
    bindings = {}
    for key, name in names.items():
        value = result[key]
        content = (
            _bytes(value, True)
            if key == "selected_next_slice"
            else b"".join(_bytes(row) for row in value)
        )
        path = out_dir / name
        path.write_bytes(content)
        paths[key] = path
        bindings[key] = {"path": name, "sha256": _sha(content), "bytes": len(content)}
    report = {key: value for key, value in result.items() if key not in names}
    report["artifact_bindings"] = bindings
    report_path = out_dir / "uslci-depth-4-provider-cumulative-batch-1.json"
    report_path.write_bytes(_bytes(report, True))
    return {"report": report_path, **paths}


def main(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "closure-report",
        "closure-report-binding",
        "ranked-frontier",
        "ranked-frontier-binding",
        "selected-batch",
        "selected-batch-binding",
        "layer-3-observed-ids",
        "layer-3-observed-ids-binding",
        "probe-processes",
        "probe-processes-binding",
        "generation-processes",
        "generation-processes-binding",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--raw-cache-dir", type=Path)
    args = parser.parse_args(argv)
    selected_bytes = args.selected_batch.read_bytes()
    selected = _load_json(selected_bytes, "selected batch")
    if args.raw_cache_dir:
        raw = reuse_uslci_depth_4_provider_responses(
            selected, args.raw_cache_dir, args.out_dir / "raw-public-responses"
        )
        response_reuse_count = BATCH_SIZE
    else:
        raw = fetch_uslci_depth_4_provider_batch(
            selected, args.out_dir / "raw-public-responses", workers=args.workers
        )
        response_reuse_count = 0
    result = compile_uslci_depth_4_provider_batch(
        closure_report_bytes=args.closure_report.read_bytes(),
        closure_report_binding=_load_json(
            args.closure_report_binding.read_bytes(), "binding"
        ),
        ranked_frontier_bytes=args.ranked_frontier.read_bytes(),
        ranked_frontier_binding=_load_json(
            args.ranked_frontier_binding.read_bytes(), "binding"
        ),
        selected_batch_bytes=selected_bytes,
        selected_batch_binding=_load_json(
            args.selected_batch_binding.read_bytes(), "binding"
        ),
        layer_3_observed_ids_bytes=args.layer_3_observed_ids.read_bytes(),
        layer_3_observed_ids_binding=_load_json(
            args.layer_3_observed_ids_binding.read_bytes(), "binding"
        ),
        probe_processes_bytes=args.probe_processes.read_bytes(),
        probe_processes_binding=_load_json(
            args.probe_processes_binding.read_bytes(), "binding"
        ),
        generation_processes_bytes=args.generation_processes.read_bytes(),
        generation_processes_binding=_load_json(
            args.generation_processes_binding.read_bytes(), "binding"
        ),
        raw_envelopes=raw,
        response_reuse_count=response_reuse_count,
    )
    materialize_uslci_depth_4_provider_batch(result, args.out_dir / "outputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
