"""Observe one bounded batch of USLCI electricity unit-Provider Processes."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from lca_research_kernel.uslci_generation_closure import fetch_generation_processes
    from lca_research_kernel.uslci_provider_probe import _normalize_process
else:
    from .uslci_generation_closure import fetch_generation_processes
    from .uslci_provider_probe import _normalize_process


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_unit_provider_closure_batch/v1"
LOGIC_VERSION = "2026-07-17.2"
UPSTREAM_CONTRACT = "lca_research_kernel.uslci_generation_provider_closure/v1"
SLICE_CONTRACT = "lca_research_kernel.selected_uslci_unit_provider_closure_batch/v1"
EXPECTED_ROUTE = "probe-uslci-electricity-unit-provider-closure-batch"
NEXT_ROUTE = "extend-uslci-unit-provider-closure-to-batch-2"
REPOSITORY = "Federal_LCA_Commons/US_electricity_baseline"


def _bytes(value, pretty=False):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
        indent=2 if pretty else None, separators=None if pretty else (",", ":")) + "\n").encode()


def _sha(content): return hashlib.sha256(content).hexdigest()


def _object(value, label):
    if not isinstance(value, dict): raise ValueError(f"{label} must be an object")
    return dict(value)


def _load_json(content, label):
    try: return _object(json.loads(content), label)
    except (UnicodeError, json.JSONDecodeError) as error: raise ValueError(f"{label} is invalid JSON") from error


def _load_jsonl(content, label):
    try: rows = [_object(json.loads(line), label) for line in content.decode().splitlines() if line]
    except (UnicodeError, json.JSONDecodeError) as error: raise ValueError(f"{label} is invalid JSONL") from error
    if not rows: raise ValueError(f"{label} is empty")
    return rows


def _binding(value, content, label):
    row = _object(value, f"{label} binding")
    if row.get("sha256") != _sha(content): raise ValueError(f"{label} binding hash mismatch")
    if row.get("bytes") is not None and row.get("bytes") != len(content): raise ValueError(f"{label} binding bytes mismatch")
    if not isinstance(row.get("path"), str): raise ValueError(f"{label} binding path missing")
    return {"path": row["path"], "sha256": row["sha256"], "bytes": len(content)}


def build_batch_requests(selected):
    row = _object(selected, "selected unit-Provider batch")
    ids = row.get("process_ids")
    if row.get("contract") != SLICE_CONTRACT or row.get("slice_id") != EXPECTED_ROUTE or row.get("layer") != 3 or not isinstance(ids, list) or len(ids) != 50 or len(set(ids)) != 50:
        raise ValueError("selected unit-Provider batch must contain 50 unique layer-3 IDs")
    if row.get("public_read_allowed") is not True or row.get("credential_required") is not False:
        raise ValueError("unit-Provider batch must remain credential-free")
    return [{"route_id": f"process:{pid}", "process_id": pid,
        "request_url": "https://lcacommons.gov/lca-collaboration/ws/public/browse/Federal_LCA_Commons/US_electricity_baseline/PROCESS/" + pid,
        "method": "GET", "accept": "application/json"} for pid in ids]


def fetch_unit_provider_batch(selected, raw_root: Path, *, workers=4, attempts=3, timeout=30):
    # The shared driver validates the same credential-free slice shape after adapting field names.
    adapted = {"contract": "lca_research_kernel.selected_uslci_provider_closure_slice/v1",
        "slice_id": "probe-uslci-electricity-generation-provider-closure",
        "upstream_provider_process_ids": selected["process_ids"],
        "public_read_allowed": selected["public_read_allowed"], "credential_required": selected["credential_required"]}
    # It expects 67 IDs, so execute the identical bounded request primitive locally for 50.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    if __package__ in (None, ""):
        from lca_research_kernel.uslci_provider_probe import _fetch_one
    else:
        from .uslci_provider_probe import _fetch_one
    requests = build_batch_requests(selected); raw_root.mkdir(parents=True, exist_ok=True); result = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_one, req, attempts=attempts, timeout=timeout): req for req in requests}
        for future in as_completed(futures):
            req = futures[future]; result[req["process_id"]] = future.result()[0]
    for index, req in enumerate(requests, 1):
        (raw_root / f"{index:03d}-{req['process_id']}.json").write_bytes(_bytes(result[req["process_id"]], True))
    return result


def compile_uslci_unit_provider_batch(*, closure_report_bytes, closure_report_binding,
        frontier_bytes, frontier_binding, selected_batch_bytes, selected_batch_binding, raw_envelopes):
    report_ref = _binding(closure_report_binding, closure_report_bytes, "closure report")
    frontier_ref = _binding(frontier_binding, frontier_bytes, "closure frontier")
    slice_ref = _binding(selected_batch_binding, selected_batch_bytes, "selected batch")
    report = _load_json(closure_report_bytes, "closure report")
    frontier = _load_jsonl(frontier_bytes, "closure frontier")
    selected = _load_json(selected_batch_bytes, "selected batch")
    build_batch_requests(selected)
    if len(frontier) != 313 or [row.get("process_id") for row in frontier[:50]] != selected["process_ids"]:
        raise ValueError("selected batch is not the deterministic first 50 frontier rows")
    bindings = _object(report.get("artifact_bindings"), "closure bindings")
    for key, ref in (("frontier", frontier_ref), ("selected_next_slice", slice_ref)):
        expected = _object(bindings.get(key), key)
        if expected.get("sha256") != ref["sha256"] or expected.get("bytes") != ref["bytes"]:
            raise ValueError(f"closure report and {key} disagree")
    if report.get("contract") != UPSTREAM_CONTRACT or report.get("status") != "uslci_generation_provider_closure_layer_observed" or report.get("next_frontier_process_count") != 313 or report.get("routing", {}).get("next_internal_route") != EXPECTED_ROUTE:
        raise ValueError("closure report contract drifted")
    ids = selected["process_ids"]
    if set(raw_envelopes) != set(ids): raise ValueError("exact selected 50 response set is required")
    process_rows=[]; exchange_rows=[]; summaries=[]; deeper_edges=[]; response_refs=[]
    for pid in ids:
        envelope = _object(raw_envelopes[pid], f"response {pid}")
        if envelope.get("route_id") != f"process:{pid}" or envelope.get("http_status") != 200 or envelope.get("error") is not None:
            raise ValueError(f"Process {pid} response failed")
        payload = _object(envelope.get("payload"), f"Process {pid} payload")
        if payload.get("@id") != pid: raise ValueError(f"Process {pid} identity mismatch")
        process = _normalize_process(payload, f"unit Provider {pid}")
        if process["process_version"] != "03.00.000" or process["valid_from"] > "2023-12-31" or not process["valid_until"].startswith("2023-"):
            raise ValueError(f"Process {pid} version/time drifted")
        process_rows.append({"layer": 3, **process})
        counts = {"PRODUCT_FLOW": 0, "WASTE_FLOW": 0, "ELEMENTARY_FLOW": 0}; emissions=resources=0
        for exchange in process["exchanges"]:
            kind = exchange["flow"]["flow_type"]
            if kind not in counts: raise ValueError(f"Process {pid} unsupported Flow type {kind}")
            counts[kind] += 1
            classification = "technosphere_product"
            if kind == "WASTE_FLOW": classification = "waste_flow_not_automatically_elementary"
            elif kind == "ELEMENTARY_FLOW":
                if exchange["direction"] == "Input": classification="elementary_resource"; resources += 1
                else: classification="elementary_emission"; emissions += 1
            row = {"process_id": pid, "process_name": process["process_name"], "exchange_internal_id": exchange["internal_id"],
                "direction": exchange["direction"], "amount": str(exchange["amount"]), "flow": exchange["flow"],
                "unit": exchange["unit"], "flow_property": exchange["flow_property"], "classification": classification,
                "is_quantitative_reference": exchange["is_quantitative_reference"], "is_avoided_product": exchange["is_avoided_product"],
                "default_provider": exchange.get("default_provider"), "uncertainty_reported": exchange["uncertainty_reported"],
                "dq_entry": exchange.get("dq_entry"), "description": exchange.get("description"),
                "negative_source_amount": Decimal(str(exchange["amount"])) < 0}
            exchange_rows.append(row)
            if exchange["direction"] == "Input" and exchange.get("default_provider") is not None:
                provider=exchange["default_provider"]
                deeper_edges.append({"source_process_id": pid, "source_exchange_internal_id": exchange["internal_id"],
                    "flow_id": exchange["flow"]["flow_id"], "flow_name": exchange["flow"]["flow_name"],
                    "amount": str(exchange["amount"]), "unit": exchange["unit"]["unit_name"],
                    "provider_process_id": provider["id"], "provider_process_name": provider["name"],
                    "depth": 4, "status": "source_observed_not_materialized"})
        summaries.append({"process_id": pid, "process_name": process["process_name"], "exchange_count": process["exchange_count"],
            "product_exchange_count": counts["PRODUCT_FLOW"], "waste_exchange_count": counts["WASTE_FLOW"],
            "elementary_exchange_count": counts["ELEMENTARY_FLOW"], "elementary_emission_count": emissions,
            "elementary_resource_count": resources, "default_provider_count": process["default_provider_count"],
            "negative_source_amount_count": sum(Decimal(str(row["amount"])) < 0 for row in process["exchanges"]),
            "has_elementary_inventory": counts["ELEMENTARY_FLOW"] > 0})
        response_refs.append({"process_id": pid, "request_url": envelope.get("request_url"), "fetched_at": envelope.get("fetched_at"),
            "sha256": envelope.get("response_sha256"), "bytes": envelope.get("response_bytes")})
    deeper = {}
    for edge in deeper_edges:
        pid=edge["provider_process_id"]
        item=deeper.setdefault(pid, {"process_id": pid, "process_name": edge["provider_process_name"], "incoming_edge_count": 0, "depth": 4, "status": "deferred_until_layer_3_frontier_complete"})
        item["incoming_edge_count"] += 1
    deeper_frontier=sorted(deeper.values(), key=lambda row: row["process_id"])
    remaining=frontier[50:]; next_rows=remaining[:50]
    total=sum((Decimal(str(row["derived_acquisition_weight"])) for row in frontier), Decimal(0))
    cumulative=sum((Decimal(str(row["derived_acquisition_weight"])) for row in frontier[:100]), Decimal(0))
    next_slice={"contract": SLICE_CONTRACT, "slice_id": EXPECTED_ROUTE, "layer": 3, "batch_index": 2,
        "source_repository": REPOSITORY, "source_repository_version": "1.2026-06.0",
        "process_ids": [row["process_id"] for row in next_rows], "process_count": len(next_rows),
        "previously_observed_process_count": 50, "deferred_process_count": len(remaining)-len(next_rows),
        "cumulative_weight_coverage_percent": format(cumulative/total*100, ".12f"),
        "weight_semantics": "routing_only_not_environmental_impact", "public_read_allowed": True,
        "credential_required": False, "external_write_allowed": False, "process_creation_allowed": False,
        "model_connection_allowed": False, "comparison_allowed": False, "lcia_allowed": False}
    version=_sha(_bytes({"logic":LOGIC_VERSION,"inputs":[report_ref,frontier_ref,slice_ref],"responses":response_refs}))[:16]
    elementary=sum(row["elementary_exchange_count"] for row in summaries)
    return {"schema_version":1,"contract":CONTRACT,"logic_version":LOGIC_VERSION,
        "status":"uslci_unit_provider_closure_batch_observed","scope_id":report.get("scope_id"),"revision":report.get("revision"),
        "parent_closure_version":report.get("closure_version"),"batch_version":version,"batch_index":1,
        "input_bindings":[report_ref,frontier_ref,slice_ref],"response_bindings":response_refs,"public_read_count":50,
        "successful_public_read_count":50,"fetched_process_count":50,"process_with_elementary_inventory_count":sum(row["has_elementary_inventory"] for row in summaries),
        "exchange_observation_count":len(exchange_rows),"product_exchange_count":sum(row["product_exchange_count"] for row in summaries),
        "waste_exchange_count":sum(row["waste_exchange_count"] for row in summaries),"elementary_exchange_count":elementary,
        "negative_source_amount_count":sum(row["negative_source_amount_count"] for row in summaries),
        "elementary_emission_count":sum(row["elementary_emission_count"] for row in summaries),"elementary_resource_count":sum(row["elementary_resource_count"] for row in summaries),
        "deeper_provider_edge_count":len(deeper_edges),"deeper_frontier_process_count":len(deeper_frontier),
        "breadth_frontier_total_count":313,"breadth_observed_process_count":50,"breadth_remaining_process_count":263,
        "selected_batch_weight_coverage_percent":selected["selected_weight_coverage_percent"],"selected_next_batch_count":len(next_rows),
        "next_cumulative_weight_coverage_percent":next_slice["cumulative_weight_coverage_percent"],"provider_graph_closed":False,
        "process_creation_executed_count":0,"flow_creation_executed_count":0,"identity_preflight_executed_count":0,"build_plan_executed_count":0,
        "model_connection_executed_count":0,"model_mutation_executed_count":0,"external_write_executed_count":0,"comparison_executed_count":0,"lcia_executed_count":0,
        "scientific_comparison_ready":False,"comparative_lcia_allowed":False,"process_observations":process_rows,
        "exchange_observations":exchange_rows,"inventory_summaries":summaries,"deeper_provider_edges":deeper_edges,
        "deeper_frontier":deeper_frontier,"observed_process_ids":[{"process_id":pid,"batch_index":1} for pid in ids],"selected_next_slice":next_slice,
        "routing":{"next_internal_route":NEXT_ROUTE,"pause_internal_work":False},
        "next_action":"Read breadth-first unit-Provider batch 2 before following deferred depth-4 fuel/material Providers."}


def materialize_uslci_unit_provider_batch(result, out_dir:Path):
    out_dir.mkdir(parents=True,exist_ok=True)
    names={"process_observations":"uslci-unit-process-observations.jsonl","exchange_observations":"uslci-unit-exchange-observations.jsonl",
        "inventory_summaries":"uslci-unit-inventory-summaries.jsonl","deeper_provider_edges":"uslci-unit-deeper-provider-edges.jsonl",
        "deeper_frontier":"uslci-unit-deeper-provider-frontier.jsonl","observed_process_ids":"uslci-unit-observed-process-ids.jsonl",
        "selected_next_slice":"selected-uslci-unit-provider-closure-batch.json"}
    paths={};bindings={}
    for key,name in names.items():
        value=result[key];content=_bytes(value,True) if key=="selected_next_slice" else b"".join(_bytes(row) for row in value)
        path=out_dir/name;path.write_bytes(content);paths[key]=path;bindings[key]={"path":name,"sha256":_sha(content),"bytes":len(content)}
    report={k:v for k,v in result.items() if k not in names};report["artifact_bindings"]=bindings
    path=out_dir/"uslci-unit-provider-closure-batch.json";path.write_bytes(_bytes(report,True))
    return {"report":path,**paths}


def main(argv:Sequence[str]|None=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("closure-report","closure-report-binding","frontier","frontier-binding","selected-batch","selected-batch-binding"):
        parser.add_argument("--"+name,type=Path,required=True)
    parser.add_argument("--out-dir",type=Path,required=True);parser.add_argument("--workers",type=int,default=4)
    args=parser.parse_args(argv);selected_bytes=args.selected_batch.read_bytes();selected=_load_json(selected_bytes,"selected batch")
    raw=fetch_unit_provider_batch(selected,args.out_dir/"raw-public-responses",workers=args.workers)
    result=compile_uslci_unit_provider_batch(closure_report_bytes=args.closure_report.read_bytes(),closure_report_binding=_load_json(args.closure_report_binding.read_bytes(),"binding"),
        frontier_bytes=args.frontier.read_bytes(),frontier_binding=_load_json(args.frontier_binding.read_bytes(),"binding"),selected_batch_bytes=selected_bytes,
        selected_batch_binding=_load_json(args.selected_batch_binding.read_bytes(),"binding"),raw_envelopes=raw)
    materialize_uslci_unit_provider_batch(result,args.out_dir/"outputs");return 0


if __name__=="__main__":raise SystemExit(main())
