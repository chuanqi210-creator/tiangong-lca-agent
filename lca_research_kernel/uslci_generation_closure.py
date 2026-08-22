"""Fetch and compile one USLCI electricity Provider graph-closure layer."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from lca_research_kernel.uslci_provider_probe import _fetch_one, _normalize_process
else:
    from .uslci_provider_probe import _fetch_one, _normalize_process


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_generation_provider_closure/v1"
LOGIC_VERSION = "2026-07-17.1"
UPSTREAM_CONTRACT = "lca_research_kernel.uslci_provider_normalization_plan/v1"
SLICE_CONTRACT = "lca_research_kernel.selected_uslci_provider_closure_slice/v1"
EXPECTED_ROUTE = "probe-uslci-electricity-generation-provider-closure"
NEXT_ROUTE = "probe-uslci-electricity-unit-provider-closure-batch"
REPOSITORY = "Federal_LCA_Commons/US_electricity_baseline"
BASE_URL = "https://lcacommons.gov/lca-collaboration/ws/public/browse/Federal_LCA_Commons/US_electricity_baseline/PROCESS/"


def _j(value, pretty=False):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2 if pretty else None, separators=None if pretty else (",", ":")) + "\n").encode()


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _obj(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return dict(value)


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be text")
    return value.strip()


def _json(content, label):
    try:
        return _obj(json.loads(content), label)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is invalid JSON") from error


def _jsonl(content, label):
    try:
        rows = [_obj(json.loads(line), label) for line in content.decode().splitlines() if line]
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is invalid JSONL") from error
    if not rows:
        raise ValueError(f"{label} is empty")
    return rows


def _binding(value, content, label):
    row = _obj(value, f"{label} binding")
    if row.get("sha256") != _sha(content):
        raise ValueError(f"{label} binding hash mismatch")
    if row.get("bytes") is not None and row.get("bytes") != len(content):
        raise ValueError(f"{label} binding bytes mismatch")
    return {"path": _text(row.get("path"), f"{label} path"), "sha256": row["sha256"], "bytes": len(content)}


def build_process_requests(selected_slice: Mapping[str, object]) -> List[JsonObject]:
    row = _obj(selected_slice, "selected closure slice")
    ids = row.get("upstream_provider_process_ids")
    if row.get("contract") != SLICE_CONTRACT or row.get("slice_id") != EXPECTED_ROUTE or not isinstance(ids, list) or len(ids) != 67 or len(set(ids)) != 67:
        raise ValueError("selected closure slice must contain exactly 67 Process IDs")
    if row.get("public_read_allowed") is not True or row.get("credential_required") is not False:
        raise ValueError("closure reads must remain credential-free")
    return [
        {"route_id": f"process:{pid}", "process_id": _text(pid, "Process ID"), "method": "GET", "request_url": BASE_URL + pid, "accept": "application/json"}
        for pid in sorted(ids)
    ]


def fetch_generation_processes(selected_slice, raw_root: Path, *, attempts=3, timeout=30, workers=4):
    if workers < 1 or workers > 8:
        raise ValueError("workers must be between 1 and 8")
    requests = build_process_requests(selected_slice)
    raw_root.mkdir(parents=True, exist_ok=True)
    results = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_one, request, attempts=attempts, timeout=timeout): request for request in requests}
        for future in as_completed(futures):
            request = futures[future]
            envelope, _ = future.result()
            results[request["process_id"]] = envelope
    for index, request in enumerate(requests, 1):
        envelope = results[request["process_id"]]
        (raw_root / f"{index:03d}-{request['process_id']}.json").write_bytes(_j(envelope, True))
    return results


def compile_uslci_generation_closure(*, normalization_report_bytes, normalization_report_binding, connection_requirements_bytes, connection_requirements_binding, selected_closure_slice_bytes, selected_closure_slice_binding, raw_envelopes):
    report_binding = _binding(normalization_report_binding, normalization_report_bytes, "normalization report")
    connection_binding = _binding(connection_requirements_binding, connection_requirements_bytes, "connection requirements")
    slice_binding = _binding(selected_closure_slice_binding, selected_closure_slice_bytes, "selected closure slice")
    report = _json(normalization_report_bytes, "normalization report")
    connections = _jsonl(connection_requirements_bytes, "connection requirements")
    selected = _json(selected_closure_slice_bytes, "selected closure slice")
    build_process_requests(selected)
    bindings = _obj(report.get("artifact_bindings"), "normalization artifact bindings")
    for key, binding in (("provider_connection_requirements", connection_binding), ("selected_closure_slice", slice_binding)):
        expected = _obj(bindings.get(key), key)
        if expected.get("sha256") != binding["sha256"] or expected.get("bytes") != binding["bytes"]:
            raise ValueError(f"normalization report and {key} disagree")
    if report.get("contract") != UPSTREAM_CONTRACT or report.get("status") != "uslci_provider_normalization_plan_compiled" or report.get("provider_connection_requirement_count") != 68 or report.get("upstream_generation_provider_count") != 67 or report.get("provider_graph_closed") is not False or report.get("routing", {}).get("next_internal_route") != EXPECTED_ROUTE:
        raise ValueError("normalization report contract drifted")
    if len(connections) != 68:
        raise ValueError("exactly 68 connection requirements are required")
    generation = [row for row in connections if row.get("source_consumer_role") == "declared_at_grid_dependency"]
    if len(generation) != 67:
        raise ValueError("exactly 67 generation connections are required")
    selected_ids = selected["upstream_provider_process_ids"]
    if set(selected_ids) != {row.get("source_provider_process_id") for row in generation} or set(raw_envelopes) != set(selected_ids):
        raise ValueError("exact 67-Process source set is required")
    national_weights = {row["source_provider_process_id"]: Decimal(str(row["source_amount"])) for row in generation}
    process_rows, references, summaries, edges = [], [], [], []
    response_bindings = []
    for pid in sorted(selected_ids):
        envelope = _obj(raw_envelopes[pid], f"response {pid}")
        if envelope.get("route_id") != f"process:{pid}" or envelope.get("http_status") != 200 or envelope.get("error") is not None:
            raise ValueError(f"Process {pid} public response failed")
        payload = _obj(envelope.get("payload"), f"Process {pid} payload")
        if payload.get("@id") != pid:
            raise ValueError(f"Process {pid} identity mismatch")
        process = _normalize_process(payload, f"generation Process {pid}")
        if process["process_version"] != "03.00.000" or not process["valid_from"].startswith("2023-") or not process["valid_until"].startswith("2023-"):
            raise ValueError(f"Process {pid} version or time drifted")
        process_rows.append({"layer": 2, **process})
        ref = process["reference_exchange"]
        references.append({"process_id": pid, "process_name": process["process_name"], "flow": ref["flow"], "amount": str(ref["amount"]), "unit": ref["unit"], "flow_property": ref["flow_property"]})
        product = sum(x["flow"]["flow_type"] == "PRODUCT_FLOW" for x in process["exchanges"])
        elementary = sum(x["flow"]["flow_type"] == "ELEMENTARY_FLOW" for x in process["exchanges"])
        summaries.append({"process_id": pid, "exchange_count": process["exchange_count"], "input_exchange_count": process["input_exchange_count"], "output_exchange_count": process["output_exchange_count"], "product_exchange_count": product, "elementary_exchange_count": elementary, "default_provider_count": process["default_provider_count"], "has_elementary_inventory": elementary > 0})
        for exchange in process["exchanges"]:
            provider = exchange.get("default_provider")
            if exchange["direction"] == "Input" and provider is not None:
                derived = national_weights[pid] * Decimal(str(exchange["amount"]))
                edges.append({"source_process_id": pid, "source_exchange_internal_id": exchange["internal_id"], "flow_id": exchange["flow"]["flow_id"], "input_amount": str(exchange["amount"]), "input_unit": exchange["unit"]["unit_name"], "provider_process_id": provider["id"], "provider_process_name": provider["name"], "national_parent_weight": str(national_weights[pid]), "derived_acquisition_weight": format(derived, "f"), "edge_status": "source_observed_not_materialized"})
        response_bindings.append({"process_id": pid, "request_url": envelope.get("request_url"), "fetched_at": envelope.get("fetched_at"), "sha256": envelope.get("response_sha256"), "bytes": envelope.get("response_bytes")})
    observed = {row.get("source_consumer_process_id") for row in connections} | {row.get("source_provider_process_id") for row in connections}
    aggregate = {}
    names = {}
    edge_counts = {}
    for edge in edges:
        pid = edge["provider_process_id"]
        if pid in observed:
            continue
        aggregate[pid] = aggregate.get(pid, Decimal(0)) + Decimal(edge["derived_acquisition_weight"])
        names[pid] = edge["provider_process_name"]
        edge_counts[pid] = edge_counts.get(pid, 0) + 1
    frontier = [{"process_id": pid, "process_name": names[pid], "incoming_edge_count": edge_counts[pid], "derived_acquisition_weight": format(weight, "f"), "weight_semantics": "routing_only_not_environmental_impact", "status": "unobserved_next_frontier"} for pid, weight in aggregate.items()]
    frontier.sort(key=lambda x: (-Decimal(x["derived_acquisition_weight"]), x["process_id"]))
    chosen = frontier[:50]
    total_weight = sum((Decimal(x["derived_acquisition_weight"]) for x in frontier), Decimal(0))
    chosen_weight = sum((Decimal(x["derived_acquisition_weight"]) for x in chosen), Decimal(0))
    coverage = (chosen_weight / total_weight * 100) if total_weight else Decimal(0)
    next_slice = {"contract": "lca_research_kernel.selected_uslci_unit_provider_closure_batch/v1", "slice_id": NEXT_ROUTE, "layer": 3, "source_repository": REPOSITORY, "source_repository_version": "1.2026-06.0", "process_ids": [x["process_id"] for x in chosen], "process_count": len(chosen), "deferred_process_count": len(frontier) - len(chosen), "selected_weight_coverage_percent": format(coverage, ".12f"), "weight_semantics": "routing_only_not_environmental_impact", "public_read_allowed": True, "credential_required": False, "external_write_allowed": False, "process_creation_allowed": False, "model_connection_allowed": False, "comparison_allowed": False, "lcia_allowed": False}
    version = _sha(_j({"logic": LOGIC_VERSION, "inputs": [report_binding, connection_binding, slice_binding], "responses": response_bindings}))[:16]
    return {"schema_version": 1, "contract": CONTRACT, "logic_version": LOGIC_VERSION, "status": "uslci_generation_provider_closure_layer_observed", "scope_id": report.get("scope_id"), "revision": report.get("revision"), "normalization_version": report.get("normalization_version"), "closure_version": version, "input_bindings": [report_binding, connection_binding, slice_binding], "response_bindings": response_bindings, "public_read_count": 67, "successful_public_read_count": 67, "fetched_process_count": 67, "reference_product_count": len(references), "inventory_summary_count": len(summaries), "total_exchange_count": sum(x["exchange_count"] for x in summaries), "process_with_elementary_inventory_count": sum(x["has_elementary_inventory"] for x in summaries), "elementary_exchange_count": sum(x["elementary_exchange_count"] for x in summaries), "observed_provider_edge_count": len(edges), "next_frontier_process_count": len(frontier), "selected_next_batch_count": len(chosen), "deferred_frontier_process_count": len(frontier) - len(chosen), "selected_weight_coverage_percent": next_slice["selected_weight_coverage_percent"], "provider_graph_closed": len(frontier) == 0, "process_creation_executed_count": 0, "flow_creation_executed_count": 0, "identity_preflight_executed_count": 0, "build_plan_executed_count": 0, "model_connection_executed_count": 0, "model_mutation_executed_count": 0, "external_write_executed_count": 0, "comparison_executed_count": 0, "lcia_executed_count": 0, "scientific_comparison_ready": False, "comparative_lcia_allowed": False, "process_observations": process_rows, "reference_products": references, "inventory_summaries": summaries, "provider_edges": edges, "frontier": frontier, "selected_next_slice": next_slice, "external_patterns": [{"project": "scrapy/scrapy", "local_landing": "bounded_parallel_requests_retries_and_raw_envelopes", "installed": False}, {"project": "networkx/networkx", "local_landing": "directed_BFS_layer_and_deduplicated_frontier", "installed": False}], "routing": {"next_internal_route": NEXT_ROUTE if frontier else "review-uslci-provider-graph-closure", "pause_internal_work": False}, "next_action": "Read the selected contribution-prioritized unit-Provider batch without creating or connecting models."}


def materialize_uslci_generation_closure(result, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    detail = {"process_observations": "uslci-generation-process-observations.jsonl", "reference_products": "uslci-generation-reference-products.jsonl", "inventory_summaries": "uslci-generation-inventory-summaries.jsonl", "provider_edges": "uslci-generation-provider-edges.jsonl", "frontier": "uslci-provider-closure-frontier.jsonl", "selected_next_slice": "selected-uslci-unit-provider-closure-batch.json"}
    paths, bindings = {}, {}
    for key, name in detail.items():
        value = result[key]
        content = _j(value, True) if key == "selected_next_slice" else b"".join(_j(row) for row in value)
        path = out_dir / name; path.write_bytes(content); paths[key] = path
        bindings[key] = {"path": name, "sha256": _sha(content), "bytes": len(content)}
    report = {k: v for k, v in result.items() if k not in detail}; report["artifact_bindings"] = bindings
    path = out_dir / "uslci-generation-provider-closure.json"; path.write_bytes(_j(report, True))
    return {"report": path, **paths}


def main(argv: Sequence[str] | None = None):
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("normalization-report", "normalization-report-binding", "connection-requirements", "connection-requirements-binding", "selected-closure-slice", "selected-closure-slice-binding"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True); p.add_argument("--workers", type=int, default=4)
    args = p.parse_args(argv)
    selected_bytes = args.selected_closure_slice.read_bytes(); selected = _json(selected_bytes, "selected slice")
    raw = fetch_generation_processes(selected, args.out_dir / "raw-public-responses", workers=args.workers)
    result = compile_uslci_generation_closure(normalization_report_bytes=args.normalization_report.read_bytes(), normalization_report_binding=_json(args.normalization_report_binding.read_bytes(), "binding"), connection_requirements_bytes=args.connection_requirements.read_bytes(), connection_requirements_binding=_json(args.connection_requirements_binding.read_bytes(), "binding"), selected_closure_slice_bytes=selected_bytes, selected_closure_slice_binding=_json(args.selected_closure_slice_binding.read_bytes(), "binding"), raw_envelopes=raw)
    materialize_uslci_generation_closure(result, args.out_dir / "outputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
