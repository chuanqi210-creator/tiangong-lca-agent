"""Continue cumulative USLCI unit-Provider observation from batch 3 onward."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
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
CONTRACT = "lca_research_kernel.uslci_unit_provider_closure_cumulative_batch/v1"
LOGIC_VERSION = "2026-07-17.2"
SLICE_CONTRACT = "lca_research_kernel.selected_uslci_unit_provider_closure_batch/v1"
REPOSITORY = "Federal_LCA_Commons/US_electricity_baseline"
FRONTIER_SIZE = 313
MAX_BATCH_SIZE = 50


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


def _nonnegative_count(report, key):
    value = report.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"prior cumulative count invalid: {key}")
    return value


def _expected_route(batch_index):
    return f"extend-uslci-unit-provider-closure-to-batch-{batch_index}"


def _validate_selected(selected, expected_ids, batch_index, observed_count):
    ids = selected.get("process_ids")
    if (
        selected.get("contract") != SLICE_CONTRACT
        or selected.get("batch_index") != batch_index
        or selected.get("layer") != 3
        or selected.get("previously_observed_process_count") != observed_count
        or not isinstance(ids, list)
        or ids != expected_ids
        or selected.get("process_count") != len(expected_ids)
        or not 0 < len(ids) <= MAX_BATCH_SIZE
    ):
        raise ValueError("selected batch is not the exact next frontier prefix")
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
            raise ValueError(f"selected batch authorization drifted: {key}")
    return ids


def _merge_deeper_frontier(prior_rows, current_edges, prior_batch_index):
    merged = {}
    for row in prior_rows:
        pid = row.get("process_id")
        incoming = row.get("incoming_edge_count")
        first_seen = row.get("first_seen_batch")
        if (
            not isinstance(pid, str)
            or pid in merged
            or not isinstance(incoming, int)
            or incoming <= 0
            or not isinstance(first_seen, int)
            or not 1 <= first_seen <= prior_batch_index
        ):
            raise ValueError("prior cumulative deeper frontier invalid or duplicate")
        merged[pid] = {
            "process_id": pid,
            "process_name": row.get("process_name"),
            "incoming_edge_count": incoming,
            "first_seen_batch": first_seen,
            "depth": 4,
            "status": "deferred_until_layer_3_frontier_complete",
        }
    for edge in current_edges:
        pid = edge["provider_process_id"]
        item = merged.setdefault(
            pid,
            {
                "process_id": pid,
                "process_name": edge["provider_process_name"],
                "incoming_edge_count": 0,
                "first_seen_batch": edge["first_seen_batch"],
                "depth": 4,
                "status": "deferred_until_layer_3_frontier_complete",
            },
        )
        item["incoming_edge_count"] += 1
        item["first_seen_batch"] = min(
            item["first_seen_batch"], edge["first_seen_batch"]
        )
    return sorted(merged.values(), key=lambda row: row["process_id"])


def compile_uslci_unit_provider_continuation(
    *,
    batch_index,
    frontier_bytes,
    frontier_binding,
    prior_report_bytes,
    prior_report_binding,
    prior_observed_ids_bytes,
    prior_observed_ids_binding,
    prior_deeper_frontier_bytes,
    prior_deeper_frontier_binding,
    selected_batch_bytes,
    selected_batch_binding,
    raw_envelopes,
):
    if not isinstance(batch_index, int) or isinstance(batch_index, bool) or batch_index < 3:
        raise ValueError("batch index must be an integer at least 3")
    refs = [
        _binding(frontier_binding, frontier_bytes, "frontier"),
        _binding(prior_report_binding, prior_report_bytes, "prior report"),
        _binding(
            prior_observed_ids_binding,
            prior_observed_ids_bytes,
            "prior cumulative observed IDs",
        ),
        _binding(
            prior_deeper_frontier_binding,
            prior_deeper_frontier_bytes,
            "prior cumulative deeper frontier",
        ),
        _binding(selected_batch_binding, selected_batch_bytes, "selected batch"),
    ]
    frontier = _load_jsonl(frontier_bytes, "frontier")
    prior = _load_json(prior_report_bytes, "prior report")
    observed = _load_jsonl(prior_observed_ids_bytes, "prior cumulative observed IDs")
    prior_deeper = _load_jsonl(
        prior_deeper_frontier_bytes, "prior cumulative deeper frontier"
    )
    selected = _load_json(selected_batch_bytes, "selected batch")

    prior_batch_index = batch_index - 1
    if (
        len(frontier) != FRONTIER_SIZE
        or prior.get("contract") != CONTRACT
        or prior.get("status") != "uslci_unit_provider_cumulative_batch_observed"
        or prior.get("batch_index") != prior_batch_index
        or prior.get("routing", {}).get("next_internal_route")
        != _expected_route(batch_index)
    ):
        raise ValueError("prior cumulative batch index or contract drifted")
    prior_observed_count = _nonnegative_count(
        prior, "cumulative_observed_process_count"
    )
    if prior_observed_count != len(observed):
        raise ValueError("prior cumulative observed count and ledger disagree")
    prior_ids = [row.get("process_id") for row in observed]
    expected_prior_ids = [row.get("process_id") for row in frontier[:prior_observed_count]]
    if (
        len(set(prior_ids)) != len(prior_ids)
        or prior_ids != expected_prior_ids
        or any(row.get("batch_index", 0) > prior_batch_index for row in observed)
    ):
        raise ValueError("prior observed ledger is not a unique exact frontier prefix")
    expected_current_count = min(
        MAX_BATCH_SIZE, FRONTIER_SIZE - prior_observed_count
    )
    expected_ids = [
        row.get("process_id")
        for row in frontier[
            prior_observed_count : prior_observed_count + expected_current_count
        ]
    ]
    selected_ids = _validate_selected(
        selected, expected_ids, batch_index, prior_observed_count
    )

    prior_bindings = _object(prior.get("artifact_bindings"), "prior bindings")
    for key, ref in (
        ("cumulative_observed_ids", refs[2]),
        ("cumulative_deeper_frontier", refs[3]),
        ("selected_next_slice", refs[4]),
    ):
        expected = _object(prior_bindings.get(key), key)
        if (
            expected.get("sha256") != ref["sha256"]
            or expected.get("bytes") != ref["bytes"]
        ):
            raise ValueError(f"prior report and {key} disagree")
    if not any(
        row.get("sha256") == refs[0]["sha256"]
        for row in prior.get("input_bindings", [])
    ):
        raise ValueError("prior report is not bound to the original frontier")
    if set(raw_envelopes) != set(selected_ids):
        raise ValueError("exact selected response set required")

    processes, exchanges, summaries, current_edges, responses = _compile_current(
        selected_ids, raw_envelopes, batch_index=batch_index
    )
    deep_frontier = _merge_deeper_frontier(
        prior_deeper, current_edges, prior_batch_index
    )
    cumulative_ids = [
        *observed,
        *[
            {"process_id": process_id, "batch_index": batch_index}
            for process_id in selected_ids
        ],
    ]
    observed_count = len(cumulative_ids)
    remaining_count = FRONTIER_SIZE - observed_count
    total_weight = sum(
        (Decimal(str(row["derived_acquisition_weight"])) for row in frontier),
        Decimal(0),
    )
    current_weight = sum(
        (
            Decimal(str(row["derived_acquisition_weight"]))
            for row in frontier[:observed_count]
        ),
        Decimal(0),
    )
    current_coverage = format(current_weight / total_weight * 100, ".12f")

    selected_next_slice = None
    selected_next_count = 0
    next_coverage = current_coverage
    if remaining_count:
        next_batch_index = batch_index + 1
        next_end = min(FRONTIER_SIZE, observed_count + MAX_BATCH_SIZE)
        next_rows = frontier[observed_count:next_end]
        covered_next_weight = sum(
            (
                Decimal(str(row["derived_acquisition_weight"]))
                for row in frontier[:next_end]
            ),
            Decimal(0),
        )
        next_coverage = format(covered_next_weight / total_weight * 100, ".12f")
        selected_next_count = len(next_rows)
        selected_next_slice = {
            "contract": SLICE_CONTRACT,
            "slice_id": selected.get(
                "slice_id", "probe-uslci-electricity-unit-provider-closure-batch"
            ),
            "layer": 3,
            "batch_index": next_batch_index,
            "source_repository": selected.get("source_repository", REPOSITORY),
            "source_repository_version": selected.get(
                "source_repository_version", "1.2026-06.0"
            ),
            "process_ids": [row["process_id"] for row in next_rows],
            "process_count": selected_next_count,
            "previously_observed_process_count": observed_count,
            "deferred_process_count": FRONTIER_SIZE - next_end,
            "cumulative_weight_coverage_percent": next_coverage,
            "weight_semantics": "routing_only_not_environmental_impact",
            "public_read_allowed": True,
            "credential_required": False,
            "external_write_allowed": False,
            "process_creation_allowed": False,
            "model_connection_allowed": False,
            "comparison_allowed": False,
            "lcia_allowed": False,
        }
        next_route = _expected_route(next_batch_index)
        next_action = (
            f"Continue the cumulative runtime to batch {next_batch_index} and observe "
            f"frontier rows {observed_count + 1}-{next_end}."
        )
    else:
        next_route = "close-uslci-unit-provider-current-breadth-layer"
        next_action = (
            "Close the fully observed 313-Process unit-Provider breadth layer before "
            "routing any deferred depth-4 Providers."
        )

    batch_counts = {
        "exchange": len(exchanges),
        "product": sum(row["product_exchange_count"] for row in summaries),
        "waste": sum(row["waste_exchange_count"] for row in summaries),
        "elementary": sum(row["elementary_exchange_count"] for row in summaries),
        "emission": sum(row["elementary_emission_count"] for row in summaries),
        "resource": sum(row["elementary_resource_count"] for row in summaries),
        "negative": sum(row["negative_source_amount_count"] for row in summaries),
    }
    version = _sha(
        _bytes(
            {
                "logic": LOGIC_VERSION,
                "batch_index": batch_index,
                "inputs": refs,
                "responses": responses,
            }
        )
    )[:16]
    result = {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "status": "uslci_unit_provider_cumulative_batch_observed",
        "scope_id": prior.get("scope_id"),
        "revision": prior.get("revision"),
        "batch_version": version,
        "batch_index": batch_index,
        "prior_batch_version": prior.get("batch_version"),
        "input_bindings": refs,
        "response_bindings": responses,
        "public_read_count": len(selected_ids),
        "successful_public_read_count": len(selected_ids),
        "fetched_process_count": len(selected_ids),
        "batch_process_with_elementary_inventory_count": sum(
            row["has_elementary_inventory"] for row in summaries
        ),
        "batch_exchange_observation_count": batch_counts["exchange"],
        "batch_product_exchange_count": batch_counts["product"],
        "batch_waste_exchange_count": batch_counts["waste"],
        "batch_elementary_exchange_count": batch_counts["elementary"],
        "batch_elementary_emission_count": batch_counts["emission"],
        "batch_elementary_resource_count": batch_counts["resource"],
        "batch_negative_source_amount_count": batch_counts["negative"],
        "batch_deeper_provider_edge_count": len(current_edges),
        "cumulative_observed_process_count": observed_count,
        "cumulative_process_with_elementary_inventory_count": _nonnegative_count(
            prior, "cumulative_process_with_elementary_inventory_count"
        )
        + sum(row["has_elementary_inventory"] for row in summaries),
        "cumulative_exchange_observation_count": _nonnegative_count(
            prior, "cumulative_exchange_observation_count"
        )
        + batch_counts["exchange"],
        "cumulative_product_exchange_count": _nonnegative_count(
            prior, "cumulative_product_exchange_count"
        )
        + batch_counts["product"],
        "cumulative_waste_exchange_count": _nonnegative_count(
            prior, "cumulative_waste_exchange_count"
        )
        + batch_counts["waste"],
        "cumulative_elementary_exchange_count": _nonnegative_count(
            prior, "cumulative_elementary_exchange_count"
        )
        + batch_counts["elementary"],
        "cumulative_elementary_emission_count": _nonnegative_count(
            prior, "cumulative_elementary_emission_count"
        )
        + batch_counts["emission"],
        "cumulative_elementary_resource_count": _nonnegative_count(
            prior, "cumulative_elementary_resource_count"
        )
        + batch_counts["resource"],
        "cumulative_negative_source_amount_count": _nonnegative_count(
            prior, "cumulative_negative_source_amount_count"
        )
        + batch_counts["negative"],
        "cumulative_deeper_provider_edge_count": _nonnegative_count(
            prior, "cumulative_deeper_provider_edge_count"
        )
        + len(current_edges),
        "cumulative_deeper_frontier_process_count": len(deep_frontier),
        "breadth_frontier_total_count": FRONTIER_SIZE,
        "breadth_remaining_process_count": remaining_count,
        "current_cumulative_weight_coverage_percent": current_coverage,
        "selected_next_batch_count": selected_next_count,
        "next_cumulative_weight_coverage_percent": next_coverage,
        "provider_graph_closed": False,
        "process_creation_executed_count": 0,
        "flow_creation_executed_count": 0,
        "identity_preflight_executed_count": 0,
        "build_plan_executed_count": 0,
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
        "batch_deeper_provider_edges": current_edges,
        "cumulative_deeper_frontier": deep_frontier,
        "cumulative_observed_ids": cumulative_ids,
        "selected_next_slice": selected_next_slice,
        "routing": {"next_internal_route": next_route, "pause_internal_work": False},
        "next_action": next_action,
    }
    return result


def materialize_uslci_unit_provider_continuation(result, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    batch_index = result["batch_index"]
    names = {
        "batch_process_observations": f"uslci-unit-batch-{batch_index}-process-observations.jsonl",
        "batch_exchange_observations": f"uslci-unit-batch-{batch_index}-exchange-observations.jsonl",
        "batch_inventory_summaries": f"uslci-unit-batch-{batch_index}-inventory-summaries.jsonl",
        "batch_deeper_provider_edges": f"uslci-unit-batch-{batch_index}-deeper-provider-edges.jsonl",
        "cumulative_deeper_frontier": "uslci-unit-cumulative-deeper-provider-frontier.jsonl",
        "cumulative_observed_ids": "uslci-unit-cumulative-observed-process-ids.jsonl",
    }
    if result.get("selected_next_slice") is not None:
        names["selected_next_slice"] = (
            f"selected-uslci-unit-provider-closure-batch-{batch_index + 1}.json"
        )
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
    report.pop("selected_next_slice", None)
    report["artifact_bindings"] = bindings
    report_path = out_dir / f"uslci-unit-provider-cumulative-batch-{batch_index}.json"
    report_path.write_bytes(_bytes(report, True))
    return {"report": report_path, **paths}


def fetch_uslci_unit_provider_continuation(
    selected, raw_root: Path, *, workers=4, attempts=3, timeout=30
):
    ids = selected.get("process_ids")
    if (
        selected.get("contract") != SLICE_CONTRACT
        or not isinstance(ids, list)
        or not 0 < len(ids) <= MAX_BATCH_SIZE
        or len(set(ids)) != len(ids)
        or selected.get("public_read_allowed") is not True
        or selected.get("credential_required") is not False
    ):
        raise ValueError("selected continuation batch is not credential-free or bounded")
    requests = [
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


def main(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-index", type=int, required=True)
    for name in (
        "frontier",
        "frontier-binding",
        "prior-report",
        "prior-report-binding",
        "prior-observed-ids",
        "prior-observed-ids-binding",
        "prior-deeper-frontier",
        "prior-deeper-frontier-binding",
        "selected-batch",
        "selected-batch-binding",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    selected_bytes = args.selected_batch.read_bytes()
    selected = _load_json(selected_bytes, "selected batch")
    raw = fetch_uslci_unit_provider_continuation(
        selected, args.out_dir / "raw-public-responses", workers=args.workers
    )
    result = compile_uslci_unit_provider_continuation(
        batch_index=args.batch_index,
        frontier_bytes=args.frontier.read_bytes(),
        frontier_binding=_load_json(args.frontier_binding.read_bytes(), "binding"),
        prior_report_bytes=args.prior_report.read_bytes(),
        prior_report_binding=_load_json(args.prior_report_binding.read_bytes(), "binding"),
        prior_observed_ids_bytes=args.prior_observed_ids.read_bytes(),
        prior_observed_ids_binding=_load_json(
            args.prior_observed_ids_binding.read_bytes(), "binding"
        ),
        prior_deeper_frontier_bytes=args.prior_deeper_frontier.read_bytes(),
        prior_deeper_frontier_binding=_load_json(
            args.prior_deeper_frontier_binding.read_bytes(), "binding"
        ),
        selected_batch_bytes=selected_bytes,
        selected_batch_binding=_load_json(
            args.selected_batch_binding.read_bytes(), "binding"
        ),
        raw_envelopes=raw,
    )
    materialize_uslci_unit_provider_continuation(result, args.out_dir / "outputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
