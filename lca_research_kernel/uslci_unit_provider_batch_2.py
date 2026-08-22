"""Compile cumulative USLCI unit-Provider state while observing batch 2."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Dict, Sequence

if __package__ in (None, ""):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from lca_research_kernel.uslci_provider_probe import _normalize_process
    from lca_research_kernel.uslci_unit_provider_batch import fetch_unit_provider_batch
else:
    from .uslci_provider_probe import _normalize_process
    from .uslci_unit_provider_batch import fetch_unit_provider_batch


JsonObject=Dict[str,object]
CONTRACT="lca_research_kernel.uslci_unit_provider_closure_cumulative_batch/v1"
LOGIC_VERSION="2026-07-17.1"
PRIOR_CONTRACT="lca_research_kernel.uslci_unit_provider_closure_batch/v1"
SLICE_CONTRACT="lca_research_kernel.selected_uslci_unit_provider_closure_batch/v1"
EXPECTED_ROUTE="extend-uslci-unit-provider-closure-to-batch-2"
NEXT_ROUTE="extend-uslci-unit-provider-closure-to-batch-3"
REPOSITORY="Federal_LCA_Commons/US_electricity_baseline"


def _bytes(value,pretty=False):return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2 if pretty else None,separators=None if pretty else (",",":"))+"\n").encode()
def _sha(content):return hashlib.sha256(content).hexdigest()
def _obj(value,label):
    if not isinstance(value,dict):raise ValueError(f"{label} must be an object")
    return dict(value)
def _json(content,label):
    try:return _obj(json.loads(content),label)
    except (UnicodeError,json.JSONDecodeError) as error:raise ValueError(f"{label} invalid JSON") from error
def _jsonl(content,label):
    try:rows=[_obj(json.loads(line),label) for line in content.decode().splitlines() if line]
    except (UnicodeError,json.JSONDecodeError) as error:raise ValueError(f"{label} invalid JSONL") from error
    if not rows:raise ValueError(f"{label} empty")
    return rows
def _binding(value,content,label):
    row=_obj(value,f"{label} binding")
    if row.get("sha256")!=_sha(content):raise ValueError(f"{label} binding hash mismatch")
    if row.get("bytes") is not None and row.get("bytes")!=len(content):raise ValueError(f"{label} binding bytes mismatch")
    if not isinstance(row.get("path"),str):raise ValueError(f"{label} binding path missing")
    return {"path":row["path"],"sha256":row["sha256"],"bytes":len(content)}


def _compile_current(ids,raw,batch_index=2,layer=3,allowed_process_types=("UNIT_PROCESS",),time_policy="cover_2023"):
    processes=[];exchanges=[];summaries=[];edges=[];responses=[]
    for pid in ids:
        envelope=_obj(raw[pid],f"response {pid}")
        if envelope.get("route_id")!=f"process:{pid}" or envelope.get("http_status")!=200 or envelope.get("error") is not None:raise ValueError(f"Process {pid} response failed")
        payload=_obj(envelope.get("payload"),f"payload {pid}")
        if payload.get("@id")!=pid:raise ValueError(f"Process {pid} identity mismatch")
        process=_normalize_process(payload,f"unit Provider {pid}",allowed_process_types=allowed_process_types)
        try:
            valid_from=date.fromisoformat(process["valid_from"]);valid_until=date.fromisoformat(process["valid_until"])
        except (TypeError,ValueError) as error:raise ValueError(f"Process {pid} version/time drifted") from error
        if process["process_version"]!="03.00.000" or valid_from>valid_until:raise ValueError(f"Process {pid} version/time drifted")
        if time_policy=="cover_2023" and not (valid_from<=date(2023,12,31) and valid_until.year==2023):raise ValueError(f"Process {pid} version/time drifted")
        if time_policy=="historical_through_2023" and valid_until>date(2023,12,31):raise ValueError(f"Process {pid} version/time drifted")
        if time_policy not in {"cover_2023","historical_through_2023"}:raise ValueError(f"unsupported Process time policy: {time_policy}")
        processes.append({"layer":layer,"batch_index":batch_index,**process});counts={"PRODUCT_FLOW":0,"WASTE_FLOW":0,"ELEMENTARY_FLOW":0};emissions=resources=negatives=0
        for ex in process["exchanges"]:
            kind=ex["flow"]["flow_type"]
            if kind not in counts:raise ValueError(f"Process {pid} unsupported Flow type {kind}")
            counts[kind]+=1;classification="technosphere_product";negative=Decimal(str(ex["amount"]))<0;negatives+=negative
            if kind=="WASTE_FLOW":classification="waste_flow_not_automatically_elementary"
            elif kind=="ELEMENTARY_FLOW":
                if ex["direction"]=="Input":classification="elementary_resource";resources+=1
                else:classification="elementary_emission";emissions+=1
            exchanges.append({"process_id":pid,"process_name":process["process_name"],"exchange_internal_id":ex["internal_id"],"direction":ex["direction"],"amount":str(ex["amount"]),"flow":ex["flow"],"unit":ex["unit"],"flow_property":ex["flow_property"],"classification":classification,"is_quantitative_reference":ex["is_quantitative_reference"],"is_avoided_product":ex["is_avoided_product"],"default_provider":ex.get("default_provider"),"uncertainty_reported":ex["uncertainty_reported"],"dq_entry":ex.get("dq_entry"),"description":ex.get("description"),"negative_source_amount":negative})
            if ex["direction"]=="Input" and ex.get("default_provider") is not None:
                provider=ex["default_provider"];edges.append({"source_process_id":pid,"source_exchange_internal_id":ex["internal_id"],"flow_id":ex["flow"]["flow_id"],"flow_name":ex["flow"]["flow_name"],"amount":str(ex["amount"]),"unit":ex["unit"]["unit_name"],"provider_process_id":provider["id"],"provider_process_name":provider["name"],"depth":layer+1,"first_seen_batch":batch_index,"status":"source_observed_not_materialized"})
        summaries.append({"process_id":pid,"process_name":process["process_name"],"exchange_count":process["exchange_count"],"product_exchange_count":counts["PRODUCT_FLOW"],"waste_exchange_count":counts["WASTE_FLOW"],"elementary_exchange_count":counts["ELEMENTARY_FLOW"],"elementary_emission_count":emissions,"elementary_resource_count":resources,"negative_source_amount_count":negatives,"default_provider_count":process["default_provider_count"],"has_elementary_inventory":counts["ELEMENTARY_FLOW"]>0})
        responses.append({"process_id":pid,"request_url":envelope.get("request_url"),"fetched_at":envelope.get("fetched_at"),"sha256":envelope.get("response_sha256"),"bytes":envelope.get("response_bytes")})
    return processes,exchanges,summaries,edges,responses


def compile_uslci_unit_provider_batch_2(*,frontier_bytes,frontier_binding,prior_report_bytes,prior_report_binding,
        prior_observed_ids_bytes,prior_observed_ids_binding,prior_deeper_edges_bytes,prior_deeper_edges_binding,
        selected_batch_bytes,selected_batch_binding,raw_envelopes):
    refs=[_binding(frontier_binding,frontier_bytes,"frontier"),_binding(prior_report_binding,prior_report_bytes,"prior report"),
        _binding(prior_observed_ids_binding,prior_observed_ids_bytes,"prior observed IDs"),_binding(prior_deeper_edges_binding,prior_deeper_edges_bytes,"prior deeper edges"),
        _binding(selected_batch_binding,selected_batch_bytes,"selected batch")]
    frontier=_jsonl(frontier_bytes,"frontier");prior=_json(prior_report_bytes,"prior report");observed=_jsonl(prior_observed_ids_bytes,"prior observed IDs");prior_edges=_jsonl(prior_deeper_edges_bytes,"prior deeper edges");selected=_json(selected_batch_bytes,"selected batch")
    if len(frontier)!=313 or prior.get("contract")!=PRIOR_CONTRACT or prior.get("logic_version")!="2026-07-17.2" or prior.get("status")!="uslci_unit_provider_closure_batch_observed" or prior.get("batch_index")!=1 or prior.get("routing",{}).get("next_internal_route")!=EXPECTED_ROUTE:raise ValueError("prior batch contract drifted")
    selected_ids=selected.get("process_ids")
    if selected.get("contract")!=SLICE_CONTRACT or selected.get("batch_index")!=2 or not isinstance(selected_ids,list) or selected_ids!=[row.get("process_id") for row in frontier[50:100]]:raise ValueError("batch-2 selection must equal frontier rows 51-100")
    prior_bindings=_obj(prior.get("artifact_bindings"),"prior bindings")
    for key,ref in (("observed_process_ids",refs[2]),("deeper_provider_edges",refs[3]),("selected_next_slice",refs[4])):
        expected=_obj(prior_bindings.get(key),key)
        if expected.get("sha256")!=ref["sha256"] or expected.get("bytes")!=ref["bytes"]:raise ValueError(f"prior report and {key} disagree")
    if not any(row.get("sha256")==refs[0]["sha256"] for row in prior.get("input_bindings",[])):raise ValueError("prior report is not bound to the original frontier")
    prior_ids=[row.get("process_id") for row in observed]
    if len(prior_ids)!=50 or len(set(prior_ids))!=50 or prior_ids!=[row.get("process_id") for row in frontier[:50]]:raise ValueError("prior observed IDs are duplicate, gapped or reordered")
    if set(selected_ids)&set(prior_ids):raise ValueError("batch-2 selection overlaps prior observed IDs")
    if set(raw_envelopes)!=set(selected_ids):raise ValueError("exact batch-2 response set required")
    processes,exchanges,summaries,current_edges,responses=_compile_current(selected_ids,raw_envelopes)
    cumulative_edges=[]
    for edge in prior_edges:
        cumulative_edges.append({**edge,"first_seen_batch":edge.get("first_seen_batch",1)})
    cumulative_edges.extend(current_edges);deep={}
    for edge in cumulative_edges:
        pid=edge["provider_process_id"];item=deep.setdefault(pid,{"process_id":pid,"process_name":edge["provider_process_name"],"incoming_edge_count":0,"first_seen_batch":edge["first_seen_batch"],"depth":4,"status":"deferred_until_layer_3_frontier_complete"});item["incoming_edge_count"]+=1;item["first_seen_batch"]=min(item["first_seen_batch"],edge["first_seen_batch"])
    deep_frontier=sorted(deep.values(),key=lambda row:row["process_id"]);cumulative_ids=[*observed,*[{"process_id":pid,"batch_index":2} for pid in selected_ids]]
    next_rows=frontier[100:150];total=sum((Decimal(str(row["derived_acquisition_weight"])) for row in frontier),Decimal(0));covered=sum((Decimal(str(row["derived_acquisition_weight"])) for row in frontier[:150]),Decimal(0))
    next_slice={"contract":SLICE_CONTRACT,"slice_id":"probe-uslci-electricity-unit-provider-closure-batch","layer":3,"batch_index":3,"source_repository":REPOSITORY,"source_repository_version":"1.2026-06.0","process_ids":[row["process_id"] for row in next_rows],"process_count":50,"previously_observed_process_count":100,"deferred_process_count":163,"cumulative_weight_coverage_percent":format(covered/total*100,".12f"),"weight_semantics":"routing_only_not_environmental_impact","public_read_allowed":True,"credential_required":False,"external_write_allowed":False,"process_creation_allowed":False,"model_connection_allowed":False,"comparison_allowed":False,"lcia_allowed":False}
    batch_counts={"exchange":len(exchanges),"product":sum(row["product_exchange_count"] for row in summaries),"waste":sum(row["waste_exchange_count"] for row in summaries),"elementary":sum(row["elementary_exchange_count"] for row in summaries),"emission":sum(row["elementary_emission_count"] for row in summaries),"resource":sum(row["elementary_resource_count"] for row in summaries),"negative":sum(row["negative_source_amount_count"] for row in summaries)}
    version=_sha(_bytes({"logic":LOGIC_VERSION,"inputs":refs,"responses":responses}))[:16]
    return {"schema_version":1,"contract":CONTRACT,"logic_version":LOGIC_VERSION,"status":"uslci_unit_provider_cumulative_batch_observed","scope_id":prior.get("scope_id"),"revision":prior.get("revision"),"batch_version":version,"batch_index":2,"prior_batch_version":prior.get("batch_version"),"input_bindings":refs,"response_bindings":responses,"public_read_count":50,"successful_public_read_count":50,"fetched_process_count":50,
        "batch_process_with_elementary_inventory_count":sum(row["has_elementary_inventory"] for row in summaries),"batch_exchange_observation_count":batch_counts["exchange"],"batch_product_exchange_count":batch_counts["product"],"batch_waste_exchange_count":batch_counts["waste"],"batch_elementary_exchange_count":batch_counts["elementary"],"batch_elementary_emission_count":batch_counts["emission"],"batch_elementary_resource_count":batch_counts["resource"],"batch_negative_source_amount_count":batch_counts["negative"],"batch_deeper_provider_edge_count":len(current_edges),
        "cumulative_observed_process_count":100,"cumulative_process_with_elementary_inventory_count":prior["process_with_elementary_inventory_count"]+sum(row["has_elementary_inventory"] for row in summaries),"cumulative_exchange_observation_count":prior["exchange_observation_count"]+batch_counts["exchange"],"cumulative_product_exchange_count":prior["product_exchange_count"]+batch_counts["product"],"cumulative_waste_exchange_count":prior["waste_exchange_count"]+batch_counts["waste"],"cumulative_elementary_exchange_count":prior["elementary_exchange_count"]+batch_counts["elementary"],"cumulative_elementary_emission_count":prior["elementary_emission_count"]+batch_counts["emission"],"cumulative_elementary_resource_count":prior["elementary_resource_count"]+batch_counts["resource"],"cumulative_negative_source_amount_count":prior["negative_source_amount_count"]+batch_counts["negative"],"cumulative_deeper_provider_edge_count":len(cumulative_edges),"cumulative_deeper_frontier_process_count":len(deep_frontier),"breadth_frontier_total_count":313,"breadth_remaining_process_count":213,"selected_next_batch_count":50,"next_cumulative_weight_coverage_percent":next_slice["cumulative_weight_coverage_percent"],"provider_graph_closed":False,
        "process_creation_executed_count":0,"flow_creation_executed_count":0,"identity_preflight_executed_count":0,"build_plan_executed_count":0,"model_connection_executed_count":0,"model_mutation_executed_count":0,"external_write_executed_count":0,"comparison_executed_count":0,"lcia_executed_count":0,"scientific_comparison_ready":False,"comparative_lcia_allowed":False,
        "batch_process_observations":processes,"batch_exchange_observations":exchanges,"batch_inventory_summaries":summaries,"batch_deeper_provider_edges":current_edges,"cumulative_deeper_frontier":deep_frontier,"cumulative_observed_ids":cumulative_ids,"selected_next_slice":next_slice,
        "routing":{"next_internal_route":NEXT_ROUTE,"pause_internal_work":False},"next_action":"Extend the cumulative runtime to batch 3 and observe frontier rows 101-150."}


def materialize_uslci_unit_provider_batch_2(result,out_dir:Path):
    out_dir.mkdir(parents=True,exist_ok=True);names={"batch_process_observations":"uslci-unit-batch-2-process-observations.jsonl","batch_exchange_observations":"uslci-unit-batch-2-exchange-observations.jsonl","batch_inventory_summaries":"uslci-unit-batch-2-inventory-summaries.jsonl","batch_deeper_provider_edges":"uslci-unit-batch-2-deeper-provider-edges.jsonl","cumulative_deeper_frontier":"uslci-unit-cumulative-deeper-provider-frontier.jsonl","cumulative_observed_ids":"uslci-unit-cumulative-observed-process-ids.jsonl","selected_next_slice":"selected-uslci-unit-provider-closure-batch-3.json"};paths={};bindings={}
    for key,name in names.items():
        value=result[key];content=_bytes(value,True) if key=="selected_next_slice" else b"".join(_bytes(row) for row in value);path=out_dir/name;path.write_bytes(content);paths[key]=path;bindings[key]={"path":name,"sha256":_sha(content),"bytes":len(content)}
    report={k:v for k,v in result.items() if k not in names};report["artifact_bindings"]=bindings;path=out_dir/"uslci-unit-provider-cumulative-batch-2.json";path.write_bytes(_bytes(report,True));return {"report":path,**paths}


def main(argv:Sequence[str]|None=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("frontier","frontier-binding","prior-report","prior-report-binding","prior-observed-ids","prior-observed-ids-binding","prior-deeper-edges","prior-deeper-edges-binding","selected-batch","selected-batch-binding"):parser.add_argument("--"+name,type=Path,required=True)
    parser.add_argument("--out-dir",type=Path,required=True);parser.add_argument("--workers",type=int,default=4);args=parser.parse_args(argv);selected_bytes=args.selected_batch.read_bytes();selected=_json(selected_bytes,"selected batch");raw=fetch_unit_provider_batch(selected,args.out_dir/"raw-public-responses",workers=args.workers)
    result=compile_uslci_unit_provider_batch_2(frontier_bytes=args.frontier.read_bytes(),frontier_binding=_json(args.frontier_binding.read_bytes(),"binding"),prior_report_bytes=args.prior_report.read_bytes(),prior_report_binding=_json(args.prior_report_binding.read_bytes(),"binding"),prior_observed_ids_bytes=args.prior_observed_ids.read_bytes(),prior_observed_ids_binding=_json(args.prior_observed_ids_binding.read_bytes(),"binding"),prior_deeper_edges_bytes=args.prior_deeper_edges.read_bytes(),prior_deeper_edges_binding=_json(args.prior_deeper_edges_binding.read_bytes(),"binding"),selected_batch_bytes=selected_bytes,selected_batch_binding=_json(args.selected_batch_binding.read_bytes(),"binding"),raw_envelopes=raw);materialize_uslci_unit_provider_batch_2(result,args.out_dir/"outputs");return 0


if __name__=="__main__":raise SystemExit(main())
