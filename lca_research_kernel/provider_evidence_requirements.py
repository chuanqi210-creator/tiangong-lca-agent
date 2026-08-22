"""Compile Provider Process evidence requirements without inventing inventories."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple


JsonObject = Dict[str, object]
PROVIDER_EVIDENCE_REQUIREMENTS_CONTRACT = (
    "lca_research_kernel.provider_process_evidence_requirements/v1"
)
PROVIDER_EVIDENCE_REQUIREMENTS_LOGIC_VERSION = "2026-07-17.1"
PROVIDER_CANDIDATE_RESOLUTION_CONTRACT = (
    "lca_research_kernel.native_provider_candidate_resolution/v1"
)
UNRESOLVED_REQUIREMENTS_CONTRACT = (
    "lca_research_kernel.unresolved_provider_requirements/v1"
)
NATIVE_REQUIRED_FIELDS = [
    "target",
    "identity_decision.decision",
    "unit_of_analysis",
    "name_plan.base_name",
    "target.geography",
    "target.technology_route",
    "quantitative_reference_plan.reference_flow_id",
]
NATIVE_SATISFIED_FIELDS = [
    "target",
    "identity_decision.decision",
    "unit_of_analysis",
    "quantitative_reference_plan.reference_flow_id",
]
NATIVE_MISSING_FIELDS = [
    "name_plan.base_name",
    "target.geography",
    "target.technology_route",
]


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _jsonl_bytes(rows: Sequence[JsonObject]) -> bytes:
    return b"".join(_json_bytes(row) for row in rows)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def provider_evidence_probe_version(
    requirement_id: str, request: Mapping[str, object]
) -> str:
    return _sha256(
        _json_bytes({"requirement_id": requirement_id, "request": dict(request)})
    )[:16]


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _array(value: object, label: str) -> List[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _parse_json(content: bytes, label: str) -> JsonObject:
    try:
        return _object(json.loads(content), label)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error


def _parse_jsonl(content: bytes, label: str) -> List[JsonObject]:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not UTF-8") from error
    rows = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            rows.append(_object(json.loads(line), f"{label} row {index}"))
        except json.JSONDecodeError as error:
            raise ValueError(f"{label} row {index} is not valid JSON") from error
    if not rows:
        raise ValueError(f"{label} is empty")
    return rows


def _binding(value: object, content: bytes, label: str) -> JsonObject:
    binding = _object(value, label)
    path = _text(binding.get("path"), f"{label} path")
    expected = _text(binding.get("sha256"), f"{label} sha256")
    if expected != _sha256(content):
        raise ValueError(f"{label} hash does not match content")
    if "bytes" in binding and binding.get("bytes") != len(content):
        raise ValueError(f"{label} byte count does not match content")
    return {"path": path, "sha256": expected, "bytes": len(content)}


def _index(rows: Sequence[JsonObject], label: str) -> Dict[str, JsonObject]:
    indexed = {}
    for row in rows:
        requirement_id = _text(row.get("requirement_id"), f"{label} requirement ID")
        if requirement_id in indexed:
            raise ValueError(f"{label} contains a duplicate requirement ID")
        indexed[requirement_id] = row
    if len(indexed) != 9:
        raise ValueError(f"{label} must contain exactly nine requirements")
    return indexed


def _validate_inputs(
    *,
    resolution_report_bytes: bytes,
    resolution_report_binding: Mapping[str, object],
    unresolved_requirements_bytes: bytes,
    unresolved_requirements_binding: Mapping[str, object],
    source_route_ledger_bytes: bytes,
    source_route_ledger_binding: Mapping[str, object],
    provider_requirements_bytes: bytes,
    provider_requirements_binding: Mapping[str, object],
) -> Tuple[JsonObject, Dict[str, JsonObject], Dict[str, JsonObject], List[JsonObject], JsonObject]:
    resolution_binding = _binding(
        resolution_report_binding, resolution_report_bytes, "resolution report binding"
    )
    unresolved_binding = _binding(
        unresolved_requirements_binding,
        unresolved_requirements_bytes,
        "unresolved requirements binding",
    )
    source_binding = _binding(
        source_route_ledger_binding,
        source_route_ledger_bytes,
        "source route ledger binding",
    )
    provider_binding = _binding(
        provider_requirements_binding,
        provider_requirements_bytes,
        "Provider requirements binding",
    )
    resolution = _parse_json(resolution_report_bytes, "candidate resolution report")
    unresolved = _parse_json(unresolved_requirements_bytes, "unresolved requirements")
    source_routes = _parse_jsonl(source_route_ledger_bytes, "candidate source routes")
    provider_rows = _parse_jsonl(provider_requirements_bytes, "Provider requirements")
    if (
        resolution.get("contract") != PROVIDER_CANDIDATE_RESOLUTION_CONTRACT
        or resolution.get("status")
        != "native_provider_candidate_resolution_compiled"
        or resolution.get("requirement_count") != 9
        or resolution.get("unresolved_requirement_count") != 9
        or resolution.get("accepted_provider_candidate_count") != 0
        or resolution.get("native_create_new_decision_count") != 9
        or resolution.get("native_create_new_authorizes_build") is not False
        or resolution.get("global_provider_absence_proven") is not False
        or resolution.get("routing", {}).get("next_internal_route")
        != "compile-provider-process-evidence-requirements"
    ):
        raise ValueError("candidate resolution is not the accepted unresolved boundary")
    artifact_bindings = _object(
        resolution.get("artifact_bindings"), "candidate resolution artifact bindings"
    )
    for name, actual in (
        ("unresolved_requirements", unresolved_binding),
        ("source_route_ledger", source_binding),
    ):
        accepted = _object(artifact_bindings.get(name), f"resolution {name} binding")
        if (
            accepted.get("sha256") != actual["sha256"]
            or accepted.get("bytes") != actual["bytes"]
        ):
            raise ValueError(f"candidate resolution {name} artifact drifted")
    if (
        unresolved.get("contract") != UNRESOLVED_REQUIREMENTS_CONTRACT
        or unresolved.get("schema_version") != 1
        or unresolved.get("requirement_count") != 9
    ):
        raise ValueError("unresolved Provider requirement contract drifted")
    unresolved_by_id = _index(
        [
            _object(row, "unresolved requirement")
            for row in _array(unresolved.get("requirements"), "unresolved requirements")
        ],
        "unresolved requirements",
    )
    provider_by_id = _index(provider_rows, "Provider requirements")
    if set(unresolved_by_id) != set(provider_by_id):
        raise ValueError("unresolved and full Provider requirements disagree")
    route_counts = {}
    for route in source_routes:
        requirement_id = _text(route.get("requirement_id"), "source route requirement ID")
        if requirement_id not in unresolved_by_id:
            raise ValueError("source route references an unknown Provider requirement")
        source_route = _text(route.get("source_route"), "source route")
        key = (requirement_id, source_route)
        if key in route_counts:
            raise ValueError("candidate source route is duplicated")
        route_counts[key] = route
    for requirement_id in unresolved_by_id:
        local = route_counts.get((requirement_id, "local_bound_process_catalog"))
        remote = route_counts.get((requirement_id, "tiangong_public_hybrid_search"))
        if (
            not isinstance(local, dict)
            or local.get("status") != "bounded_no_hit"
            or local.get("executed") is not True
            or not isinstance(remote, dict)
            or remote.get("status") != "credential_required_not_executed"
            or remote.get("executed") is not False
            or remote.get("secret_values_recorded") is not False
        ):
            raise ValueError("candidate source route state drifted")
    if len(source_routes) != 18:
        raise ValueError("candidate source ledger must contain two routes per requirement")
    for requirement_id, provider in provider_by_id.items():
        unresolved_row = unresolved_by_id[requirement_id]
        if (
            provider.get("route_id") != unresolved_row.get("route_id")
            or provider.get("consumer_process_id")
            != unresolved_row.get("consumer_process_id")
            or provider.get("exchange_internal_id")
            != unresolved_row.get("exchange_internal_id")
            or provider.get("flow_id") != unresolved_row.get("flow_id")
            or provider.get("flow_version") != unresolved_row.get("flow_version")
            or provider.get("flow_name") != unresolved_row.get("flow_name")
        ):
            raise ValueError("Provider Flow or consumer requirement drifted")
        if (
            provider.get("status") != "provider_process_missing"
            or provider.get("local_flow_bound") is not True
            or provider.get("matched_provider_process_ids") != []
            or provider.get("supplier_geography_inferred") is not False
            or unresolved_row.get("local_route_status") != "bounded_no_hit"
            or unresolved_row.get("remote_route_status")
            != "credential_required_not_executed"
        ):
            raise ValueError("Provider supplier geography or unresolved state drifted")
        if provider.get("consumer_geography") != "CN":
            raise ValueError("current Provider consumer geography drifted")
        _text(provider.get("amount"), "consumer amount")
        if provider.get("unit") not in ("kg", "MJ"):
            raise ValueError("Provider requirement unit is unsupported")
    bindings = {
        "resolution_report": resolution_binding,
        "unresolved_requirements": unresolved_binding,
        "source_route_ledger": source_binding,
        "provider_requirements": provider_binding,
    }
    return resolution, unresolved_by_id, provider_by_id, source_routes, bindings


def _probe_request(
    requirement: JsonObject, resolution: JsonObject, bindings: JsonObject
) -> JsonObject:
    flow_id = _text(requirement.get("flow_id"), "Flow ID")
    flow_version = _text(requirement.get("flow_version"), "Flow version")
    flow_name = _text(requirement.get("flow_name"), "Flow name")
    unit = _text(requirement.get("unit"), "Flow unit")
    resolution_source_id = "provider-candidate-resolution:" + _text(
        resolution.get("resolution_version"), "resolution version"
    )
    requirement_source_id = "provider-gap-requirement:" + _text(
        requirement.get("requirement_id"), "requirement ID"
    )
    return {
        "schema_version": "1",
        "kind": "process",
        "ruleset": {"id": "process-authoring/strict", "version": "1"},
        "target": {
            "intended_reference_flow": flow_id,
            "reference_flow_version": flow_version,
            "reference_flow_name": flow_name,
            "provider_role": "background_provider_for_bound_input_exchange",
        },
        "identity_decision": {
            "decision": "create_new",
            "scope": "local_bound_process_catalog_only",
            "build_authorized": False,
            "global_absence_proven": False,
        },
        "consumer_context": {
            "route_id": requirement["route_id"],
            "consumer_process_id": requirement["consumer_process_id"],
            "consumer_process_version": requirement["consumer_process_version"],
            "exchange_internal_id": requirement["exchange_internal_id"],
            "geography": requirement["consumer_geography"],
            "demand_amount": requirement["amount"],
            "demand_unit": unit,
            "supplier_geography_inferred": False,
        },
        "unit_of_analysis": {
            "decision": "blocked_until_scaling_evidence",
            "target_kind": "process",
            "reference_flow": {
                "id": flow_id,
                "version": flow_version,
                "name": flow_name,
                "reference_unit": unit,
            },
        },
        "quantitative_reference_plan": {
            "reference_flow_id": flow_id,
            "reference_flow_version": flow_version,
            "reference_flow_name": flow_name,
            "reference_unit": unit,
            "reference_direction": "Output",
            "reference_amount_status": "unresolved_not_inferred_from_consumer_amount",
        },
        "evidence_manifest": {
            "sources": [
                {
                    "id": resolution_source_id,
                    "title": "Bounded native Provider candidate resolution",
                    "uri": bindings["resolution_report"]["path"],
                    "sha256": bindings["resolution_report"]["sha256"],
                },
                {
                    "id": requirement_source_id,
                    "title": "Exact Provider requirement from the native gap map",
                    "uri": bindings["provider_requirements"]["path"],
                    "sha256": bindings["provider_requirements"]["sha256"],
                },
            ],
            "field_bindings": [
                {"field_path": "target", "source_id": requirement_source_id},
                {
                    "field_path": "identity_decision.decision",
                    "source_id": resolution_source_id,
                },
                {"field_path": "unit_of_analysis", "source_id": requirement_source_id},
                {
                    "field_path": "quantitative_reference_plan.reference_flow_id",
                    "source_id": requirement_source_id,
                },
            ],
            "provider_scientific_source_status": "unresolved",
        },
        "probe_policy": {
            "purpose": "discover_native_authoring_blockers_without_materialization",
            "materialization_allowed": False,
            "process_creation_allowed": False,
            "model_connection_allowed": False,
        },
    }


def compile_provider_evidence_probe_requests(
    *,
    resolution_report_bytes: bytes,
    resolution_report_binding: Mapping[str, object],
    unresolved_requirements_bytes: bytes,
    unresolved_requirements_binding: Mapping[str, object],
    source_route_ledger_bytes: bytes,
    source_route_ledger_binding: Mapping[str, object],
    provider_requirements_bytes: bytes,
    provider_requirements_binding: Mapping[str, object],
) -> JsonObject:
    resolution, _, provider_by_id, _, bindings = _validate_inputs(
        resolution_report_bytes=resolution_report_bytes,
        resolution_report_binding=resolution_report_binding,
        unresolved_requirements_bytes=unresolved_requirements_bytes,
        unresolved_requirements_binding=unresolved_requirements_binding,
        source_route_ledger_bytes=source_route_ledger_bytes,
        source_route_ledger_binding=source_route_ledger_binding,
        provider_requirements_bytes=provider_requirements_bytes,
        provider_requirements_binding=provider_requirements_binding,
    )
    probes = []
    for requirement_id in sorted(provider_by_id):
        request = _probe_request(provider_by_id[requirement_id], resolution, bindings)
        probes.append(
            {
                "requirement_id": requirement_id,
                "probe_version": provider_evidence_probe_version(requirement_id, request),
                "request": request,
                "expected_action": "validate",
                "expected_status": "blocked",
                "materialization_allowed": False,
            }
        )
    core = {
        "contract": "lca_research_kernel.provider_evidence_native_probe_manifest/v1",
        "logic_version": PROVIDER_EVIDENCE_REQUIREMENTS_LOGIC_VERSION,
        "probe_count": len(probes),
        "input_bindings": bindings,
        "probes": probes,
    }
    return {
        **core,
        "probe_manifest_version": _sha256(_json_bytes(core))[:16],
    }


def _aspect(
    aspect_id: str,
    status: str,
    severity: str,
    landing: Sequence[str],
    acceptance_rule: str,
    accepted_evidence: Sequence[str],
    *,
    known_value: object = None,
    source_binding_required: bool = True,
) -> JsonObject:
    row = {
        "aspect_id": aspect_id,
        "status": status,
        "severity": severity,
        "tidas_or_plan_landing": list(landing),
        "acceptance_rule": acceptance_rule,
        "accepted_evidence_classes": list(accepted_evidence),
        "source_binding_required": source_binding_required,
        "value_invented": False,
    }
    if known_value is not None:
        row["known_value"] = known_value
    return row


def _evidence_suite(requirement: JsonObject) -> JsonObject:
    flow = {
        "flow_id": requirement["flow_id"],
        "flow_version": requirement["flow_version"],
        "flow_name": requirement["flow_name"],
        "flow_type": requirement["flow_type"],
        "reference_unit": requirement["unit"],
        "required_provider_exchange_direction": "Output",
    }
    aspects = [
        _aspect(
            "target_reference_flow_identity",
            "known",
            "blocker",
            ["target.intended_reference_flow", "quantitative_reference_plan.reference_flow_id"],
            "Provider quantitative-reference exchange outputs the exact local Flow UUID/version.",
            ["accepted_native_flow_row", "hash_bound_provider_requirement"],
            known_value=flow,
        ),
        _aspect(
            "process_identity_decision",
            "known",
            "blocker",
            ["identity_decision.decision"],
            "Repeat identity preflight against every acquired candidate set before authoring; create_new remains duplicate evidence only.",
            ["native_identity_preflight_report", "candidate_source_ledger"],
            known_value={
                "decision": "create_new",
                "scope": "two_bound_foreground_processes",
                "build_authorized": False,
            },
        ),
        _aspect(
            "process_name",
            "unresolved",
            "blocker",
            ["name_plan.base_name", "processInformation.dataSetInformation.name"],
            "Name identifies the actual provider operation, product, route and location without proxy overclaim.",
            ["provider_dataset", "primary_or_authoritative_process_documentation"],
        ),
        _aspect(
            "geography_applicability",
            "partial",
            "blocker",
            ["target.geography", "processInformation.geography"],
            "Provider geography is explicit and its applicability to the CN consumer is justified; consumer geography is never copied as supplier geography.",
            ["provider_dataset", "facility_or_market_scope", "documented_geographic_proxy_justification"],
            known_value={
                "consumer_geography": requirement["consumer_geography"],
                "provider_geography": None,
                "promoted": False,
            },
        ),
        _aspect(
            "time_representativeness",
            "unresolved",
            "blocker",
            ["processInformation.time"],
            "Reference year or valid time interval and representativeness statement are source-bound.",
            ["provider_dataset", "release_metadata", "dated_primary_documentation"],
        ),
        _aspect(
            "technology_route",
            "unresolved",
            "blocker",
            ["target.technology_route", "processInformation.technology"],
            "Technology describes the operation that produces the reference Flow and exposes material differences from alternatives.",
            ["provider_dataset", "engineering_process_description", "primary_literature"],
        ),
        _aspect(
            "system_boundary_and_dataset_type",
            "unresolved",
            "blocker",
            ["modellingAndValidation.LCIMethodAndAllocation.typeOfDataSet", "processInformation.technology"],
            "Unit/system-process boundary, included upstream stages and exclusions are explicit and compatible with later network linking.",
            ["provider_dataset", "methodology_report", "system_boundary_diagram"],
        ),
        _aspect(
            "unit_of_analysis",
            "partial",
            "blocker",
            ["unit_of_analysis"],
            "Declared or functional basis specifies Flow property, reference amount, unit and scaling evidence; consumer demand amount is not the Provider reference amount.",
            ["provider_quantitative_reference", "scaling_calculation", "unit_dataset"],
            known_value={
                "target_kind": "process",
                "reference_flow": flow,
                "reference_amount": None,
                "consumer_demand_amount": requirement["amount"],
            },
        ),
        _aspect(
            "quantitative_reference",
            "partial",
            "blocker",
            ["quantitative_reference_plan", "processInformation.quantitativeReference", "exchanges.exchange"],
            "Exactly one intended quantitative-reference Output points to the exact Flow and has a source-bound positive amount and compatible unit.",
            ["provider_dataset", "declared_unit_evidence", "reference_product_specification"],
            known_value={
                **flow,
                "reference_amount": None,
                "amount_inferred_from_consumer": False,
            },
        ),
        _aspect(
            "inventory_structure",
            "unresolved",
            "blocker",
            ["exchange_plan.exchanges", "exchanges.exchange"],
            "Every technosphere input, product/coproduct/waste output and elementary exchange has identity, direction and amount; an output-only shell is rejected.",
            ["provider_dataset", "source_bound_lci_table", "reproducible_inventory_calculation"],
        ),
        _aspect(
            "inventory_completeness_and_cutoff",
            "unresolved",
            "blocker",
            ["modellingAndValidation.dataSourcesTreatmentAndRepresentativeness"],
            "Cut-off rules, omitted stages, elementary-emission coverage and known gaps are explicit; zero emission rows never imply zero emissions.",
            ["provider_dataset", "methodology_report", "completeness_assessment"],
        ),
        _aspect(
            "exchange_amount_unit_and_derivation",
            "unresolved",
            "blocker",
            ["exchange_plan.exchanges", "exchanges.exchange"],
            "Every numeric exchange retains original value/unit, transformations, resulting amount and derivation status with dimensional consistency.",
            ["source_table", "calculation_workbook", "provider_dataset"],
        ),
        _aspect(
            "uncertainty",
            "unresolved",
            "review_marker",
            ["exchanges.exchange.uncertaintyDistribution", "exchanges.exchange.generalComment"],
            "Each exchange states a source-bound distribution and parameters or an explicit not-reported/unknown status; no distribution is fabricated.",
            ["provider_dataset", "statistical_summary", "explicit_missingness_statement"],
        ),
        _aspect(
            "allocation_and_coproduct",
            "unresolved",
            "blocker",
            ["modellingAndValidation.LCIMethodAndAllocation", "exchanges.exchange"],
            "Coproducts and wastes are identified and allocation, substitution or no-allocation treatment is justified and source-bound.",
            ["provider_dataset", "methodology_report", "mass_or_economic_allocation_calculation"],
        ),
        _aspect(
            "provider_evidence_sources",
            "unresolved",
            "blocker",
            ["evidence_manifest.sources", "referencesToDataSource"],
            "At least one lawful provider-specific source identifies version, locator, licence/access state and content hash where retrievable.",
            ["official_lca_dataset", "primary_literature", "authoritative_engineering_report"],
        ),
        _aspect(
            "field_level_evidence_bindings",
            "partial",
            "blocker",
            ["evidence_manifest.field_bindings", "referencesToDataSource"],
            "Every required authored field and exchange points to exact source location and transformation lineage; current bindings cover only requirement facts.",
            ["field_binding", "table_cell_or_text_span", "dataset_json_pointer"],
            known_value={
                "requirement_fact_bindings_available": 4,
                "provider_scientific_field_bindings_available": 0,
            },
        ),
        _aspect(
            "administrative_license_and_review",
            "unresolved",
            "review_marker",
            ["administrativeInformation", "modellingAndValidation.validation"],
            "Dataset version, ownership, licence, review state and allowed use are explicit before draft-save or distribution decisions.",
            ["dataset_metadata", "licence_text", "review_record"],
        ),
    ]
    states = {"known": 0, "partial": 0, "unresolved": 0}
    for row in aspects:
        states[str(row["status"])] += 1
    if states != {"known": 2, "partial": 4, "unresolved": 11}:
        raise RuntimeError("Provider evidence aspect state contract drifted")
    return {
        "requirement_id": requirement["requirement_id"],
        "route_id": requirement["route_id"],
        "consumer_context": {
            "process_id": requirement["consumer_process_id"],
            "process_version": requirement["consumer_process_version"],
            "exchange_internal_id": requirement["exchange_internal_id"],
            "geography": requirement["consumer_geography"],
            "demand_amount": requirement["amount"],
            "demand_unit": requirement["unit"],
        },
        "required_reference_flow": flow,
        "provider_process_id": None,
        "provider_geography": None,
        "provider_reference_amount": None,
        "consumer_geography_promoted_to_provider": False,
        "consumer_amount_promoted_to_provider_reference": False,
        "provider_process_build_authorized": False,
        "state_counts": states,
        "aspects": aspects,
        "future_native_gate_sequence": [
            "process identity-preflight",
            "process build-plan validate",
            "process build-plan materialize",
            "ProcessSchema",
            "dataset validate --type process",
            "qa process",
            "process save-draft --dry-run",
        ],
        "readiness": "blocked_pending_provider_scientific_evidence",
    }


def _acquisition_routes(requirement: JsonObject) -> List[JsonObject]:
    base = {
        "requirement_id": requirement["requirement_id"],
        "route_id": requirement["route_id"],
        "flow_id": requirement["flow_id"],
        "flow_name": requirement["flow_name"],
        "global_absence_proven": False,
        "connection_authorized": False,
    }
    return [
        {
            **base,
            "source_route": "tiangong_public_hybrid_search",
            "status": "credential_required_not_executed",
            "executed": False,
            "human_required": True,
            "access_boundary": "user_session_or_api_credential",
            "secret_values_recorded": False,
            "capability": "native_process_candidate_search",
        },
        {
            **base,
            "source_route": "federal_lca_commons_api",
            "status": "documented_demo_key_available_not_executed",
            "executed": False,
            "human_required": False,
            "access_boundary": "public_documented_demo_key_rate_limited; personal production key requires human",
            "secret_values_recorded": False,
            "capability": "public_PROCESS_search_and_JSON-LD_retrieval",
        },
        {
            **base,
            "source_route": "uslci_public_github_release",
            "status": "credential_free_public_release_ready",
            "executed": False,
            "human_required": False,
            "access_boundary": "public_GitHub_release; dataset_licence_must_be_retained",
            "secret_values_recorded": False,
            "capability": "official_USLCI_JSON-LD_candidate_discovery",
        },
        {
            **base,
            "source_route": "glad_metadata_network",
            "status": "public_metadata_discovery_ready",
            "executed": False,
            "human_required": False,
            "access_boundary": "node_specific_access_and_licence_apply_to_dataset_retrieval",
            "secret_values_recorded": False,
            "capability": "fitness_for_purpose_metadata_discovery",
        },
        {
            **base,
            "source_route": "openlca_nexus_catalog",
            "status": "public_catalog_only_not_executed",
            "executed": False,
            "human_required": False,
            "access_boundary": "pack_specific_login_licence_or_payment_routes_require_human",
            "secret_values_recorded": False,
            "capability": "catalog_candidate_discovery_only",
        },
    ]


def _selected_slice(requirement: JsonObject) -> JsonObject:
    return {
        "contract": "lca_research_kernel.selected_provider_acquisition_slice/v1",
        "slice_id": "probe-uslci-electricity-provider-candidates",
        "requirement_id": requirement["requirement_id"],
        "route_id": requirement["route_id"],
        "flow_id": requirement["flow_id"],
        "flow_name": requirement["flow_name"],
        "selection_basis": "credential_free_official_release_and_domain_specific_open_electricity_inventory_route",
        "impact_priority_claimed": False,
        "source_routes": [
            {
                "route": "uslci_public_github_release",
                "url": "https://github.com/FLCAC-admin/uslci-content/releases",
                "expected_format": "openLCA_JSON-LD",
            },
            {
                "route": "electricity_lci_reference_implementation",
                "url": "https://github.com/NETL-RIC/ElectricityLCI",
                "expected_format": "openLCA_JSON-LD",
            },
        ],
        "query_terms": [
            "electricity",
            "electricity grid mix",
            "electricity generation",
            "electricity consumption mix",
        ],
        "acceptance_output": "hash_bound_candidate_observations_with_reference_product_geography_time_technology_inventory_source_and_licence_facts",
        "hard_rejections": [
            "no_electricity_reference_product_output",
            "metadata_only_without_retrievable_inventory",
            "licence_or_access_state_unknown",
        ],
        "retained_blockers": [
            "US_geography_is_not_CN_applicability",
            "foreign_flow_UUID_requires_explicit_mapping_not_identity_rewrite",
            "candidate_discovery_does_not_authorize_connection",
        ],
        "credential_required": False,
        "external_write": False,
        "process_creation_allowed": False,
        "model_connection_allowed": False,
    }


def _normalize_native_observation(
    raw: Mapping[str, object], probe: JsonObject
) -> JsonObject:
    requirement_id = _text(raw.get("requirement_id"), "native probe requirement ID")
    if requirement_id != probe["requirement_id"]:
        raise ValueError("native probe requirement identity drifted")
    request_bytes = raw.get("request_bytes")
    report_bytes = raw.get("report_bytes")
    if not isinstance(request_bytes, bytes) or not isinstance(report_bytes, bytes):
        raise ValueError("native probe artifacts must be bytes")
    if request_bytes != _json_bytes(probe["request"]):
        raise ValueError("native probe request drifted from the compiled known-fact probe")
    report = _parse_json(report_bytes, "native build-plan gate report")
    required = _object(report.get("required_fields"), "native required fields")
    schema = _object(report.get("schema_validation"), "native schema validation")
    blockers = [
        _object(row, "native blocker")
        for row in _array(report.get("blockers"), "native blockers")
    ]
    expected_codes = {
        "unit_of_analysis_not_automatic": 1,
        "unit_of_analysis_required_field_missing": 2,
        "unit_of_analysis_basis_missing": 1,
        "build_plan_required_field_missing": 3,
    }
    actual_codes = {}
    for blocker in blockers:
        code = _text(blocker.get("code"), "native blocker code")
        actual_codes[code] = actual_codes.get(code, 0) + 1
        if blocker.get("severity") != "blocker":
            raise ValueError("native probe blocker severity drifted")
    if (
        report.get("schema_version") != 1
        or report.get("kind") != "process"
        or report.get("action") != "validate"
        or report.get("status") != "blocked"
        or report.get("report_only") is not True
        or report.get("ruleset_id") != "process-authoring/strict"
        or report.get("inputs", {}).get("identity_decision") != "create_new"
        or report.get("inputs", {}).get("unit_of_analysis_decision")
        != "blocked_until_scaling_evidence"
        or required.get("required") != NATIVE_REQUIRED_FIELDS
        or required.get("satisfied") != NATIVE_SATISFIED_FIELDS
        or required.get("missing") != NATIVE_MISSING_FIELDS
        or schema.get("status") != "not_applicable"
        or actual_codes != expected_codes
        or report.get("next_action") != "fix_build_plan"
    ):
        raise ValueError("native blocked build-plan probe contract drifted")
    return {
        "requirement_id": requirement_id,
        "probe_version": probe["probe_version"],
        "status": "blocked_as_expected",
        "action": "validate",
        "ruleset_id": report["ruleset_id"],
        "ruleset_version": report.get("ruleset_version"),
        "required_field_count": len(NATIVE_REQUIRED_FIELDS),
        "satisfied_required_field_count": len(NATIVE_SATISFIED_FIELDS),
        "missing_required_field_count": len(NATIVE_MISSING_FIELDS),
        "missing_required_fields": NATIVE_MISSING_FIELDS,
        "blocker_count": len(blockers),
        "blocker_code_counts": actual_codes,
        "blockers": blockers,
        "schema_validation_status": schema["status"],
        "materialization_executed": False,
        "request_sha256": _sha256(request_bytes),
        "gate_report_sha256": _sha256(report_bytes),
    }


def compile_provider_evidence_requirements(
    *,
    resolution_report_bytes: bytes,
    resolution_report_binding: Mapping[str, object],
    unresolved_requirements_bytes: bytes,
    unresolved_requirements_binding: Mapping[str, object],
    source_route_ledger_bytes: bytes,
    source_route_ledger_binding: Mapping[str, object],
    provider_requirements_bytes: bytes,
    provider_requirements_binding: Mapping[str, object],
    native_observations: Sequence[Mapping[str, object]],
) -> JsonObject:
    resolution, _, provider_by_id, _, bindings = _validate_inputs(
        resolution_report_bytes=resolution_report_bytes,
        resolution_report_binding=resolution_report_binding,
        unresolved_requirements_bytes=unresolved_requirements_bytes,
        unresolved_requirements_binding=unresolved_requirements_binding,
        source_route_ledger_bytes=source_route_ledger_bytes,
        source_route_ledger_binding=source_route_ledger_binding,
        provider_requirements_bytes=provider_requirements_bytes,
        provider_requirements_binding=provider_requirements_binding,
    )
    probe_manifest = compile_provider_evidence_probe_requests(
        resolution_report_bytes=resolution_report_bytes,
        resolution_report_binding=resolution_report_binding,
        unresolved_requirements_bytes=unresolved_requirements_bytes,
        unresolved_requirements_binding=unresolved_requirements_binding,
        source_route_ledger_bytes=source_route_ledger_bytes,
        source_route_ledger_binding=source_route_ledger_binding,
        provider_requirements_bytes=provider_requirements_bytes,
        provider_requirements_binding=provider_requirements_binding,
    )
    probes_by_id = {
        row["requirement_id"]: row
        for row in probe_manifest["probes"]  # type: ignore[index]
    }
    observation_by_id = {}
    for observation in native_observations:
        requirement_id = _text(
            observation.get("requirement_id"), "native observation requirement ID"
        )
        if requirement_id in observation_by_id:
            raise ValueError("native Provider evidence observation is duplicated")
        observation_by_id[requirement_id] = observation
    if set(observation_by_id) != set(probes_by_id):
        raise ValueError("native Provider evidence probes do not cover nine requirements")
    evidence_requirements = []
    native_gate_observations = []
    acquisition_routes = []
    for requirement_id in sorted(provider_by_id):
        requirement = provider_by_id[requirement_id]
        evidence_requirements.append(_evidence_suite(requirement))
        native_gate_observations.append(
            _normalize_native_observation(
                observation_by_id[requirement_id], probes_by_id[requirement_id]
            )
        )
        acquisition_routes.extend(_acquisition_routes(requirement))
    selected_requirements = [
        row
        for row in provider_by_id.values()
        if row.get("flow_name") == "Electricity, process"
    ]
    if len(selected_requirements) != 1:
        raise ValueError("exactly one electricity Provider requirement is required")
    selected_requirement = selected_requirements[0]
    selected_slice = _selected_slice(selected_requirement)
    known = sum(row["state_counts"]["known"] for row in evidence_requirements)  # type: ignore[index]
    partial = sum(row["state_counts"]["partial"] for row in evidence_requirements)  # type: ignore[index]
    unresolved = sum(
        row["state_counts"]["unresolved"] for row in evidence_requirements  # type: ignore[index]
    )
    native_satisfied = sum(
        row["satisfied_required_field_count"] for row in native_gate_observations
    )
    native_missing = sum(
        row["missing_required_field_count"] for row in native_gate_observations
    )
    native_blockers = sum(row["blocker_count"] for row in native_gate_observations)
    detailed = {
        "evidence_requirements": _jsonl_bytes(evidence_requirements),
        "native_gate_observations": _jsonl_bytes(native_gate_observations),
        "acquisition_routes": _jsonl_bytes(acquisition_routes),
        "selected_slice": _json_bytes(selected_slice),
        "probe_manifest": _json_bytes(probe_manifest),
    }
    detailed_hashes = {name: _sha256(content) for name, content in detailed.items()}
    version_basis = {
        "contract": PROVIDER_EVIDENCE_REQUIREMENTS_CONTRACT,
        "logic_version": PROVIDER_EVIDENCE_REQUIREMENTS_LOGIC_VERSION,
        "input_bindings": bindings,
        "native_report_hashes": [
            row["gate_report_sha256"] for row in native_gate_observations
        ],
        "detailed_hashes": detailed_hashes,
    }
    result = {
        "schema_version": 1,
        "contract": PROVIDER_EVIDENCE_REQUIREMENTS_CONTRACT,
        "logic_version": PROVIDER_EVIDENCE_REQUIREMENTS_LOGIC_VERSION,
        "status": "provider_process_evidence_requirements_compiled",
        "scope_id": resolution.get("scope_id"),
        "revision": resolution.get("revision"),
        "input_bindings": bindings,
        "requirement_count": len(evidence_requirements),
        "aspect_count_per_requirement": 17,
        "evidence_aspect_count": len(evidence_requirements) * 17,
        "known_aspect_count": known,
        "partial_aspect_count": partial,
        "unresolved_aspect_count": unresolved,
        "native_probe_count": len(native_gate_observations),
        "native_blocked_probe_count": sum(
            row["status"] == "blocked_as_expected"
            for row in native_gate_observations
        ),
        "native_required_field_satisfied_count": native_satisfied,
        "native_required_field_missing_count": native_missing,
        "native_blocker_count": native_blockers,
        "acquisition_route_count": len(acquisition_routes),
        "credential_required_route_count": sum(
            row["status"] == "credential_required_not_executed"
            for row in acquisition_routes
        ),
        "credential_free_ready_route_count": sum(
            row["status"] == "credential_free_public_release_ready"
            for row in acquisition_routes
        ),
        "provider_build_authorized_count": 0,
        "process_creation_executed_count": 0,
        "model_connection_executed_count": 0,
        "model_mutation_executed_count": 0,
        "remote_write_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "consumer_geography_promoted_count": 0,
        "consumer_amount_promoted_to_provider_reference_count": 0,
        "external_patterns": [
            {
                "repository": "pydantic/pydantic",
                "stars_observed": 28300,
                "observed_at": "2026-07-17",
                "mechanism": "strict canonical model validation",
                "local_landing": "fail_closed_input_join_and_enumerated_aspect_contract",
                "installed": False,
            },
            {
                "repository": "fivetran/great_expectations",
                "stars_observed": 11600,
                "observed_at": "2026-07-17",
                "mechanism": "expectation_and_validation_result_separation",
                "local_landing": "per_Provider_evidence_suite_and_native_observation",
                "installed": False,
            },
            {
                "repository": "datahub-project/datahub",
                "stars_observed": 12300,
                "observed_at": "2026-07-17",
                "mechanism": "stable_entity_with_independent_metadata_aspects",
                "local_landing": "stable_requirement_ID_with_independently_satisfiable_aspects",
                "installed": False,
            },
            {
                "repository": "FLCAC-admin/uslci-content",
                "stars_observed": 31,
                "observed_at": "2026-07-17",
                "mechanism": "versioned_authoritative_public_LCI_release",
                "local_landing": "credential_free_candidate_acquisition_route",
                "installed": False,
                "domain_authority_overrides_star_priority": True,
            },
        ],
        "selected_requirement_id": selected_requirement["requirement_id"],
        "routing": {
            "next_internal_route": "probe-uslci-electricity-provider-candidates",
            "pause_internal_work": False,
            "reason": "An official credential-free USLCI release route is executable for electricity candidate discovery; geography and Flow mapping remain blockers.",
        },
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "next_action": "Probe the official public USLCI release for electricity Provider candidates, preserving geography, Flow mapping, licence and no-connection boundaries.",
        "evidence_requirements": evidence_requirements,
        "native_gate_observations": native_gate_observations,
        "acquisition_routes": acquisition_routes,
        "selected_slice": selected_slice,
        "probe_manifest": probe_manifest,
        "requirements_version": _sha256(_json_bytes(version_basis))[:16],
    }
    if (
        known != 18
        or partial != 36
        or unresolved != 99
        or native_satisfied != 36
        or native_missing != 27
        or native_blockers != 63
        or len(acquisition_routes) != 45
    ):
        raise RuntimeError("Provider evidence requirement aggregate contract drifted")
    return result


def materialize_provider_evidence_requirements(
    result: Mapping[str, object], out_dir: Path
) -> Dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    requirements = [
        _object(row, "evidence requirement")
        for row in _array(result.get("evidence_requirements"), "evidence requirements")
    ]
    native = [
        _object(row, "native gate observation")
        for row in _array(
            result.get("native_gate_observations"), "native gate observations"
        )
    ]
    routes = [
        _object(row, "acquisition route")
        for row in _array(result.get("acquisition_routes"), "acquisition routes")
    ]
    selected = _object(result.get("selected_slice"), "selected acquisition slice")
    probes = _object(result.get("probe_manifest"), "probe manifest")
    payloads = {
        "evidence_requirements": _jsonl_bytes(requirements),
        "native_gate_observations": _jsonl_bytes(native),
        "acquisition_routes": _jsonl_bytes(routes),
        "selected_slice": _json_bytes(selected),
        "probe_manifest": _json_bytes(probes),
    }
    filenames = {
        "evidence_requirements": "provider-process-evidence-expectations.jsonl",
        "native_gate_observations": "provider-evidence-native-gate-observations.jsonl",
        "acquisition_routes": "provider-evidence-acquisition-routes.jsonl",
        "selected_slice": "selected-provider-acquisition-slice.json",
        "probe_manifest": "provider-native-gate-probes.json",
    }
    paths = {}
    artifact_bindings = {}
    for name, content in payloads.items():
        path = out_dir / filenames[name]
        path.write_bytes(content)
        paths[name] = path
        artifact_bindings[name] = {
            "path": path.name,
            "sha256": _sha256(content),
            "bytes": len(content),
        }
    detailed_keys = {
        "evidence_requirements",
        "native_gate_observations",
        "acquisition_routes",
        "selected_slice",
        "probe_manifest",
    }
    report = {
        key: value
        for key, value in result.items()
        if key not in detailed_keys
    }
    report["artifact_bindings"] = artifact_bindings
    report_path = out_dir / "provider-process-evidence-requirements.json"
    report_path.write_bytes(_json_bytes(report))
    paths["report"] = report_path
    return {"report": paths.pop("report"), **paths}


def run_native_provider_evidence_probes(
    node_path: Path,
    cli_entry_path: Path,
    probe_manifest: Mapping[str, object],
    out_root: Path,
) -> List[JsonObject]:
    if not node_path.is_file() or not cli_entry_path.is_file():
        raise ValueError("copied Node and TianGong CLI entry are required")
    probes = [
        _object(row, "native evidence probe")
        for row in _array(probe_manifest.get("probes"), "native evidence probes")
    ]
    if len(probes) != 9:
        raise ValueError("exactly nine native Provider evidence probes are required")
    observations = []
    for index, probe in enumerate(probes, start=1):
        requirement_id = _text(probe.get("requirement_id"), "probe requirement ID")
        request = _object(probe.get("request"), "probe request")
        version = provider_evidence_probe_version(requirement_id, request)
        if probe.get("probe_version") != version:
            raise ValueError("native Provider evidence probe version drifted")
        root = out_root / f"{index:02d}-{version}"
        request_path = root / "inputs/process-build-plan.json"
        request_path.parent.mkdir(parents=True, exist_ok=True)
        request_bytes = _json_bytes(request)
        request_path.write_bytes(request_bytes)
        subprocess.run(
            [
                str(node_path),
                str(cli_entry_path),
                "process",
                "build-plan",
                "validate",
                "--input",
                str(request_path),
                "--out-dir",
                str(root),
                "--report-only",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        report_path = root / "outputs/build-plan-gate-report.json"
        if not report_path.is_file():
            raise RuntimeError("native Provider evidence gate report is missing")
        observations.append(
            {
                "requirement_id": requirement_id,
                "request_bytes": request_bytes,
                "report_bytes": report_path.read_bytes(),
            }
        )
    return observations


def _load_binding(path: Path) -> JsonObject:
    return _parse_json(path.read_bytes(), f"binding {path}")


def main(argv: Sequence[str] = ()) -> int:
    parser = argparse.ArgumentParser(
        description="Compile Provider Process evidence requirements."
    )
    parser.add_argument("--resolution-report", type=Path, required=True)
    parser.add_argument("--resolution-binding", type=Path, required=True)
    parser.add_argument("--unresolved-requirements", type=Path, required=True)
    parser.add_argument("--unresolved-binding", type=Path, required=True)
    parser.add_argument("--source-routes", type=Path, required=True)
    parser.add_argument("--source-routes-binding", type=Path, required=True)
    parser.add_argument("--provider-requirements", type=Path, required=True)
    parser.add_argument("--provider-requirements-binding", type=Path, required=True)
    parser.add_argument("--native-node", type=Path, required=True)
    parser.add_argument("--native-cli-entry", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(list(argv) if argv else None)
    values = {
        "resolution_report_bytes": args.resolution_report.read_bytes(),
        "resolution_report_binding": _load_binding(args.resolution_binding),
        "unresolved_requirements_bytes": args.unresolved_requirements.read_bytes(),
        "unresolved_requirements_binding": _load_binding(args.unresolved_binding),
        "source_route_ledger_bytes": args.source_routes.read_bytes(),
        "source_route_ledger_binding": _load_binding(args.source_routes_binding),
        "provider_requirements_bytes": args.provider_requirements.read_bytes(),
        "provider_requirements_binding": _load_binding(
            args.provider_requirements_binding
        ),
    }
    probe_manifest = compile_provider_evidence_probe_requests(**values)
    observations = run_native_provider_evidence_probes(
        args.native_node,
        args.native_cli_entry,
        probe_manifest,
        args.out_dir / "native-evidence-probes",
    )
    result = compile_provider_evidence_requirements(
        **values, native_observations=observations
    )
    paths = materialize_provider_evidence_requirements(result, args.out_dir / "outputs")
    print(paths["report"].read_text(encoding="utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
