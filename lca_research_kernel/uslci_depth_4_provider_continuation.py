"""Continue cumulative USLCI depth-4 Provider observation through batch 4."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from pathlib import Path
from typing import Dict, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from lca_research_kernel.uslci_depth_4_provider_batch import (
        CONTRACT,
        REPOSITORY,
        SLICE_CONTRACT,
        _binding,
        _bytes,
        _exact_ids,
        _load_json,
        _object,
        _sha,
        _validate_ranked_frontier,
    )
    from lca_research_kernel.uslci_provider_probe import _fetch_one
    from lca_research_kernel.uslci_unit_provider_batch_2 import _compile_current
else:
    from .uslci_depth_4_provider_batch import (
        CONTRACT,
        REPOSITORY,
        SLICE_CONTRACT,
        _binding,
        _bytes,
        _exact_ids,
        _load_json,
        _object,
        _sha,
        _validate_ranked_frontier,
    )
    from .uslci_provider_probe import _fetch_one
    from .uslci_unit_provider_batch_2 import _compile_current


JsonObject = Dict[str, object]
LOGIC_VERSION = "2026-07-17.1"
FRONTIER_SIZE = 200
BATCH_SIZE = 50


def _load_jsonl_allow_empty(content, label):
    try:
        return [
            _object(json.loads(line), label)
            for line in content.decode().splitlines()
            if line
        ]
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} invalid JSONL") from error


def _expected_route(batch_index):
    return f"extend-uslci-depth-4-provider-closure-to-batch-{batch_index}"


def input_version_for_bindings(batch_index, bindings):
    return _sha(
        _bytes({"logic": LOGIC_VERSION, "batch_index": batch_index, "inputs": bindings})
    )[:16]


def _selected_slice_id(batch_index):
    return f"probe-uslci-depth-4-provider-closure-batch-{batch_index}"


def _prior_count(prior, cumulative_key, batch_key):
    value = prior.get(cumulative_key, prior.get(batch_key))
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"prior count invalid: {cumulative_key}")
    return value


def _validate_selected(selected, ranked, batch_index, observed_count):
    start = observed_count
    expected_ids = [row["process_id"] for row in ranked[start : start + BATCH_SIZE]]
    ids = selected.get("process_ids")
    if (
        selected.get("contract") != SLICE_CONTRACT
        or selected.get("slice_id") != _selected_slice_id(batch_index)
        or selected.get("depth") != 4
        or selected.get("batch_index") != batch_index
        or selected.get("previously_observed_process_count") != observed_count
        or ids != expected_ids
        or selected.get("process_count") != len(expected_ids)
        or not 0 < len(ids) <= BATCH_SIZE
    ):
        raise ValueError("selected depth-4 batch is not the exact next ranked prefix")
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


def _requests(selected, batch_index):
    ids = selected.get("process_ids")
    if (
        selected.get("contract") != SLICE_CONTRACT
        or selected.get("batch_index") != batch_index
        or selected.get("slice_id") != _selected_slice_id(batch_index)
        or not isinstance(ids, list)
        or not 0 < len(ids) <= BATCH_SIZE
        or len(set(ids)) != len(ids)
        or selected.get("public_read_allowed") is not True
        or selected.get("credential_required") is not False
    ):
        raise ValueError("selected continuation batch is not bounded or credential-free")
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


def fetch_uslci_depth_4_provider_continuation(
    selected, batch_index, raw_root: Path, *, workers=4, attempts=3, timeout=30
):
    requests = _requests(selected, batch_index)
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
        (raw_root / f"{index:03d}-{request['process_id']}.json").write_bytes(
            _bytes(result[request["process_id"]], True)
        )
    return result


def reuse_uslci_depth_4_provider_continuation(
    selected, batch_index, cache_root: Path, raw_root: Path
):
    requests = _requests(selected, batch_index)
    raw_root.mkdir(parents=True, exist_ok=True)
    result = {}
    for index, request in enumerate(requests, 1):
        name = f"{index:03d}-{request['process_id']}.json"
        source = cache_root / name
        if not source.is_file():
            raise ValueError(f"cached continuation response missing: {name}")
        envelope = _load_json(source.read_bytes(), f"cached response {name}")
        if (
            envelope.get("route_id") != request["route_id"]
            or envelope.get("http_status") != 200
            or envelope.get("error") is not None
            or envelope.get("payload", {}).get("@id") != request["process_id"]
        ):
            raise ValueError(f"cached continuation response invalid: {name}")
        shutil.copyfile(source, raw_root / name)
        result[request["process_id"]] = envelope
    return result


def compile_uslci_depth_4_provider_continuation(
    *,
    batch_index,
    ranked_frontier_bytes,
    ranked_frontier_binding,
    prior_report_bytes,
    prior_report_binding,
    prior_observed_ids_bytes,
    prior_observed_ids_binding,
    prior_depth_5_frontier_bytes,
    prior_depth_5_frontier_binding,
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
    if not isinstance(batch_index, int) or isinstance(batch_index, bool) or not 2 <= batch_index <= 4:
        raise ValueError("depth-4 continuation batch index must be 2 through 4")
    refs = [
        _binding(ranked_frontier_binding, ranked_frontier_bytes, "ranked frontier"),
        _binding(prior_report_binding, prior_report_bytes, "prior report"),
        _binding(prior_observed_ids_binding, prior_observed_ids_bytes, "prior observed ledger"),
        _binding(prior_depth_5_frontier_binding, prior_depth_5_frontier_bytes, "prior depth-5 frontier"),
        _binding(selected_batch_binding, selected_batch_bytes, "selected batch"),
        _binding(layer_3_observed_ids_binding, layer_3_observed_ids_bytes, "layer-3 ledger"),
        _binding(probe_processes_binding, probe_processes_bytes, "probe Processes"),
        _binding(generation_processes_binding, generation_processes_bytes, "generation Processes"),
    ]
    ranked = _validate_ranked_frontier(_load_jsonl_allow_empty(ranked_frontier_bytes, "ranked frontier"))
    prior = _load_json(prior_report_bytes, "prior report")
    observed = _load_jsonl_allow_empty(prior_observed_ids_bytes, "prior observed ledger")
    prior_depth_5 = _load_jsonl_allow_empty(prior_depth_5_frontier_bytes, "prior depth-5 frontier")
    selected = _load_json(selected_batch_bytes, "selected batch")
    layer_3_rows = _load_jsonl_allow_empty(layer_3_observed_ids_bytes, "layer-3 ledger")
    probe_rows = _load_jsonl_allow_empty(probe_processes_bytes, "probe Processes")
    generation_rows = _load_jsonl_allow_empty(generation_processes_bytes, "generation Processes")

    prior_batch_index = batch_index - 1
    if (
        prior.get("contract") != CONTRACT
        or prior.get("status") != "uslci_depth_4_provider_cumulative_batch_observed"
        or prior.get("batch_index") != prior_batch_index
        or prior.get("routing", {}).get("next_internal_route") != _expected_route(batch_index)
    ):
        raise ValueError("prior depth-4 cumulative batch index or route drifted")
    observed_count = prior_batch_index * BATCH_SIZE
    observed_ids = _exact_ids(observed, observed_count, "prior depth-4 ledger")
    expected_observed = [row["process_id"] for row in ranked[:observed_count]]
    if observed_ids != expected_observed or any(
        row.get("batch_index", 0) > prior_batch_index for row in observed
    ):
        raise ValueError("prior depth-4 ledger is not the exact ordered ranked prefix")
    selected_ids = _validate_selected(selected, ranked, batch_index, observed_count)
    prior_bindings = _object(prior.get("artifact_bindings"), "prior bindings")
    for key, ref in (
        ("cumulative_depth_4_observed_ids", refs[2]),
        ("cumulative_depth_5_frontier", refs[3]),
        ("selected_next_slice", refs[4]),
    ):
        expected = _object(prior_bindings.get(key), key)
        if expected.get("sha256") != ref["sha256"] or expected.get("bytes") != ref["bytes"]:
            raise ValueError(f"prior report and {key} disagree")
    for ref in (refs[0], refs[5], refs[6], refs[7]):
        if not any(row.get("sha256") == ref["sha256"] for row in prior.get("input_bindings", [])):
            raise ValueError("prior report lost a stable frontier or prior-layer binding")

    layer_3_ids = _exact_ids(layer_3_rows, 313, "layer-3 ledger")
    probe_ids = _exact_ids(probe_rows, 2, "probe Process observations")
    generation_ids = _exact_ids(generation_rows, 67, "generation Process observations")
    depth_4_ids = [row["process_id"] for row in ranked]
    sets = [set(probe_ids), set(generation_ids), set(layer_3_ids), set(depth_4_ids)]
    if any(sets[left] & sets[right] for left in range(4) for right in range(left + 1, 4)):
        raise ValueError("prior and scheduled Process identity sets must be disjoint")
    if set(raw_envelopes) != set(selected_ids):
        raise ValueError("exact selected depth-4 response set required")

    prior_novel = {}
    for row in prior_depth_5:
        process_id = row.get("process_id")
        incoming = row.get("incoming_edge_count")
        if (
            not isinstance(process_id, str)
            or process_id in prior_novel
            or process_id in set().union(*sets)
            or not isinstance(incoming, int)
            or incoming <= 0
            or row.get("depth") != 5
        ):
            raise ValueError("prior depth-5 frontier invalid, duplicate or overlaps scheduled universe")
        prior_novel[process_id] = dict(row)
    if len(prior_novel) != _prior_count(prior, "cumulative_depth_5_frontier_process_count", "depth_5_frontier_process_count"):
        raise ValueError("prior depth-5 frontier count disagrees with report")

    processes, exchanges, summaries, edges, responses = _compile_current(
        selected_ids,
        raw_envelopes,
        batch_index=batch_index,
        layer=4,
        allowed_process_types=("LCI_RESULT",),
        time_policy="historical_through_2023",
    )
    prior_layer_ids = sets[0] | sets[1] | sets[2]
    scheduled_depth_4_ids = sets[3]
    merged_depth_5 = dict(prior_novel)
    classified_edges = []
    edge_counts = {"prior": 0, "scheduled": 0, "novel": 0}
    for edge in edges:
        provider_id = edge["provider_process_id"]
        if provider_id in prior_layer_ids:
            classification = "already_observed_prior_layer"
            edge_counts["prior"] += 1
        elif provider_id in scheduled_depth_4_ids:
            classification = "scheduled_depth_4_frontier"
            edge_counts["scheduled"] += 1
        else:
            classification = "novel_depth_5_frontier"
            edge_counts["novel"] += 1
            item = merged_depth_5.setdefault(
                provider_id,
                {"process_id": provider_id, "process_name": edge["provider_process_name"], "incoming_edge_count": 0, "first_seen_depth_4_batch": batch_index, "depth": 5, "status": "deferred_until_depth_4_frontier_complete"},
            )
            item["incoming_edge_count"] += 1
            item["first_seen_depth_4_batch"] = min(item["first_seen_depth_4_batch"], batch_index)
        classified_edges.append({**edge, "target_classification": classification})
    depth_5_frontier = sorted(merged_depth_5.values(), key=lambda row: row["process_id"])
    cumulative_ids = [
        *observed,
        *[{"process_id": process_id, "depth": 4, "batch_index": batch_index} for process_id in selected_ids],
    ]
    current_observed = len(cumulative_ids)
    remaining = FRONTIER_SIZE - current_observed
    total_incoming = sum(row["incoming_edge_count"] for row in ranked)
    current_incoming = sum(row["incoming_edge_count"] for row in ranked[:current_observed])
    current_coverage = format(Decimal(current_incoming) / Decimal(total_incoming) * 100, ".12f")
    selected_next_slice = None
    selected_next_count = 0
    next_coverage = current_coverage
    if remaining:
        next_index = batch_index + 1
        next_end = min(FRONTIER_SIZE, current_observed + BATCH_SIZE)
        next_rows = ranked[current_observed:next_end]
        next_incoming = sum(row["incoming_edge_count"] for row in ranked[:next_end])
        next_coverage = format(Decimal(next_incoming) / Decimal(total_incoming) * 100, ".12f")
        selected_next_count = len(next_rows)
        selected_next_slice = {
            "contract": SLICE_CONTRACT,
            "slice_id": _selected_slice_id(next_index),
            "depth": 4,
            "batch_index": next_index,
            "source_repository": selected.get("source_repository", REPOSITORY),
            "source_repository_version": selected.get("source_repository_version", "1.2026-06.0"),
            "process_ids": [row["process_id"] for row in next_rows],
            "process_count": selected_next_count,
            "previously_observed_process_count": current_observed,
            "deferred_process_count": FRONTIER_SIZE - next_end,
            "frontier_incoming_edge_count": total_incoming,
            "cumulative_incoming_edge_count": next_incoming,
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
        next_route = _expected_route(next_index)
        next_action = f"Continue exact depth-4 ranks {current_observed + 1}-{next_end}; keep depth 5 deferred."
    else:
        next_route = "close-uslci-depth-4-provider-layer"
        next_action = "Compile depth-4 closure and decide whether any novel depth-5 frontier remains."

    batch_counts = {
        "exchange": len(exchanges),
        "product": sum(row["product_exchange_count"] for row in summaries),
        "waste": sum(row["waste_exchange_count"] for row in summaries),
        "elementary": sum(row["elementary_exchange_count"] for row in summaries),
        "emission": sum(row["elementary_emission_count"] for row in summaries),
        "resource": sum(row["elementary_resource_count"] for row in summaries),
        "negative": sum(row["negative_source_amount_count"] for row in summaries),
    }
    years = {}
    for process in processes:
        year = process["valid_until"][:4]
        years[year] = years.get(year, 0) + 1
    prior_years = prior.get("cumulative_valid_until_year_counts", prior.get("batch_valid_until_year_counts"))
    if not isinstance(prior_years, dict) or any(not isinstance(value, int) for value in prior_years.values()):
        raise ValueError("prior validity-year counts invalid")
    cumulative_years = dict(prior_years)
    for year, count in years.items():
        cumulative_years[year] = cumulative_years.get(year, 0) + count
    covering = sum(process["valid_from"] <= "2023-12-31" <= process["valid_until"] for process in processes)
    if not isinstance(response_reuse_count, int) or not 0 <= response_reuse_count <= len(selected_ids):
        raise ValueError("response reuse count invalid")
    input_version = input_version_for_bindings(batch_index, refs)
    batch_version = _sha(_bytes({"logic": LOGIC_VERSION, "batch_index": batch_index, "inputs": refs, "responses": responses, "edges": classified_edges}))[:16]
    result = {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "status": "uslci_depth_4_provider_cumulative_batch_observed",
        "scope_id": prior.get("scope_id"),
        "revision": prior.get("revision"),
        "input_version": input_version,
        "batch_version": batch_version,
        "batch_index": batch_index,
        "prior_batch_version": prior.get("batch_version"),
        "input_bindings": refs,
        "response_bindings": responses,
        "public_read_count": len(selected_ids),
        "successful_public_read_count": len(selected_ids),
        "network_read_executed_count": len(selected_ids) - response_reuse_count,
        "response_reuse_count": response_reuse_count,
        "fetched_process_count": len(selected_ids),
        "batch_lci_result_process_count": sum(process["process_type"] == "LCI_RESULT" for process in processes),
        "batch_process_with_elementary_inventory_count": sum(row["has_elementary_inventory"] for row in summaries),
        "batch_valid_until_year_counts": years,
        "batch_process_covering_2023_count": covering,
        "batch_historical_before_2023_count": len(selected_ids) - covering,
        "batch_exchange_observation_count": batch_counts["exchange"],
        "batch_product_exchange_count": batch_counts["product"],
        "batch_waste_exchange_count": batch_counts["waste"],
        "batch_elementary_exchange_count": batch_counts["elementary"],
        "batch_elementary_emission_count": batch_counts["emission"],
        "batch_elementary_resource_count": batch_counts["resource"],
        "batch_negative_source_amount_count": batch_counts["negative"],
        "batch_provider_edge_count": len(classified_edges),
        "batch_prior_layer_back_edge_count": edge_counts["prior"],
        "batch_scheduled_depth_4_cross_edge_count": edge_counts["scheduled"],
        "batch_novel_depth_5_provider_edge_count": edge_counts["novel"],
        "cumulative_depth_4_observed_process_count": current_observed,
        "depth_4_remaining_process_count": remaining,
        "cumulative_lci_result_process_count": _prior_count(prior, "cumulative_lci_result_process_count", "batch_lci_result_process_count") + len(processes),
        "cumulative_process_with_elementary_inventory_count": _prior_count(prior, "cumulative_process_with_elementary_inventory_count", "batch_process_with_elementary_inventory_count") + sum(row["has_elementary_inventory"] for row in summaries),
        "cumulative_valid_until_year_counts": cumulative_years,
        "cumulative_process_covering_2023_count": _prior_count(prior, "cumulative_process_covering_2023_count", "batch_process_covering_2023_count") + covering,
        "cumulative_historical_before_2023_count": _prior_count(prior, "cumulative_historical_before_2023_count", "batch_historical_before_2023_count") + len(selected_ids) - covering,
        "cumulative_exchange_observation_count": _prior_count(prior, "cumulative_exchange_observation_count", "batch_exchange_observation_count") + batch_counts["exchange"],
        "cumulative_product_exchange_count": _prior_count(prior, "cumulative_product_exchange_count", "batch_product_exchange_count") + batch_counts["product"],
        "cumulative_waste_exchange_count": _prior_count(prior, "cumulative_waste_exchange_count", "batch_waste_exchange_count") + batch_counts["waste"],
        "cumulative_elementary_exchange_count": _prior_count(prior, "cumulative_elementary_exchange_count", "batch_elementary_exchange_count") + batch_counts["elementary"],
        "cumulative_elementary_emission_count": _prior_count(prior, "cumulative_elementary_emission_count", "batch_elementary_emission_count") + batch_counts["emission"],
        "cumulative_elementary_resource_count": _prior_count(prior, "cumulative_elementary_resource_count", "batch_elementary_resource_count") + batch_counts["resource"],
        "cumulative_negative_source_amount_count": _prior_count(prior, "cumulative_negative_source_amount_count", "batch_negative_source_amount_count") + batch_counts["negative"],
        "cumulative_provider_edge_count": _prior_count(prior, "cumulative_provider_edge_count", "batch_provider_edge_count") + len(classified_edges),
        "cumulative_depth_5_frontier_process_count": len(depth_5_frontier),
        "current_cumulative_incoming_edge_coverage_percent": current_coverage,
        "selected_next_batch_count": selected_next_count,
        "next_cumulative_incoming_edge_coverage_percent": next_coverage,
        "provider_graph_closed": False,
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
        "batch_process_observations": processes,
        "batch_exchange_observations": exchanges,
        "batch_inventory_summaries": summaries,
        "batch_provider_edges": classified_edges,
        "cumulative_depth_4_observed_ids": cumulative_ids,
        "cumulative_depth_5_frontier": depth_5_frontier,
        "selected_next_slice": selected_next_slice,
        "routing": {"next_internal_route": next_route, "pause_internal_work": False},
        "next_action": next_action,
    }
    return result


def materialize_uslci_depth_4_provider_continuation(result, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    batch_index = result["batch_index"]
    names = {
        "batch_process_observations": f"uslci-depth-4-batch-{batch_index}-process-observations.jsonl",
        "batch_exchange_observations": f"uslci-depth-4-batch-{batch_index}-exchange-observations.jsonl",
        "batch_inventory_summaries": f"uslci-depth-4-batch-{batch_index}-inventory-summaries.jsonl",
        "batch_provider_edges": f"uslci-depth-4-batch-{batch_index}-provider-edges.jsonl",
        "cumulative_depth_4_observed_ids": "uslci-depth-4-cumulative-observed-process-ids.jsonl",
        "cumulative_depth_5_frontier": "uslci-depth-5-cumulative-provider-frontier.jsonl",
    }
    if result.get("selected_next_slice") is not None:
        names["selected_next_slice"] = f"selected-uslci-depth-4-provider-batch-{batch_index + 1}.json"
    paths = {}
    bindings = {}
    for key, name in names.items():
        value = result[key]
        content = _bytes(value, True) if key == "selected_next_slice" else b"".join(_bytes(row) for row in value)
        path = out_dir / name
        path.write_bytes(content)
        paths[key] = path
        bindings[key] = {"path": name, "sha256": _sha(content), "bytes": len(content)}
    report = {key: value for key, value in result.items() if key not in names}
    report.pop("selected_next_slice", None)
    report["artifact_bindings"] = bindings
    report_path = out_dir / f"uslci-depth-4-provider-cumulative-batch-{batch_index}.json"
    report_path.write_bytes(_bytes(report, True))
    return {"report": report_path, **paths}


def main(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-index", type=int, required=True)
    for name in (
        "ranked-frontier", "ranked-frontier-binding", "prior-report", "prior-report-binding",
        "prior-observed-ids", "prior-observed-ids-binding", "prior-depth-5-frontier",
        "prior-depth-5-frontier-binding", "selected-batch", "selected-batch-binding",
        "layer-3-observed-ids", "layer-3-observed-ids-binding", "probe-processes",
        "probe-processes-binding", "generation-processes", "generation-processes-binding",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--raw-cache-dir", type=Path)
    args = parser.parse_args(argv)
    selected_bytes = args.selected_batch.read_bytes()
    selected = _load_json(selected_bytes, "selected batch")
    if args.raw_cache_dir:
        raw = reuse_uslci_depth_4_provider_continuation(selected, args.batch_index, args.raw_cache_dir, args.out_dir / "raw-public-responses")
        response_reuse_count = len(selected["process_ids"])
    else:
        raw = fetch_uslci_depth_4_provider_continuation(selected, args.batch_index, args.out_dir / "raw-public-responses", workers=args.workers)
        response_reuse_count = 0
    kwargs = {}
    for name in (
        "ranked_frontier", "prior_report", "prior_observed_ids", "prior_depth_5_frontier",
        "selected_batch", "layer_3_observed_ids", "probe_processes", "generation_processes",
    ):
        path = getattr(args, name)
        binding_path = getattr(args, name + "_binding")
        kwargs[name + "_bytes"] = path.read_bytes()
        kwargs[name + "_binding"] = _load_json(binding_path.read_bytes(), "binding")
    result = compile_uslci_depth_4_provider_continuation(
        batch_index=args.batch_index,
        raw_envelopes=raw,
        response_reuse_count=response_reuse_count,
        **kwargs,
    )
    materialize_uslci_depth_4_provider_continuation(result, args.out_dir / "outputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
