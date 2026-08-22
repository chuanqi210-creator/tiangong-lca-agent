"""Compile a reversible USLCI-to-TianGong Provider normalization plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple


JsonObject = Dict[str, object]

USLCI_PROVIDER_NORMALIZATION_CONTRACT = (
    "lca_research_kernel.uslci_provider_normalization_plan/v1"
)
USLCI_PROVIDER_NORMALIZATION_LOGIC_VERSION = "2026-07-17.1"
PROBE_CONTRACT = "lca_research_kernel.uslci_electricity_provider_probe/v1"
MAPPING_GAP_CONTRACT = "lca_research_kernel.provider_flow_mapping_gap/v1"
SELECTED_SLICE_CONTRACT = (
    "lca_research_kernel.selected_provider_normalization_slice/v1"
)
EXPECTED_ROUTE = "compile-uslci-electricity-provider-normalization-plan"
NEXT_ROUTE = "probe-uslci-electricity-generation-provider-closure"
US_LOCATION = "United States of America (the)"
AT_USER_ROLE = "selected_at_user_candidate"
AT_GRID_ROLE = "declared_at_grid_dependency"
MWH_TO_MJ = Decimal("3600")


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _jsonl_bytes(rows: Sequence[Mapping[str, object]]) -> bytes:
    return b"".join(_canonical_bytes(row) for row in rows)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return dict(value)


def _array(value: object, label: str) -> List[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return list(value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _load_json(content: bytes, label: str) -> JsonObject:
    try:
        value = json.loads(content.decode("utf-8"), parse_float=Decimal)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error
    return _object(value, label)


def _load_jsonl(content: bytes, label: str) -> List[JsonObject]:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeError as error:
        raise ValueError(f"{label} is not UTF-8") from error
    rows = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line, parse_float=Decimal)
        except json.JSONDecodeError as error:
            raise ValueError(f"{label} row {index} is not valid JSON") from error
        rows.append(_object(value, f"{label} row {index}"))
    if not rows:
        raise ValueError(f"{label} must contain rows")
    return rows


def _verify_binding(binding: object, content: bytes, label: str) -> JsonObject:
    row = _object(binding, f"{label} binding")
    path = _text(row.get("path"), f"{label} binding path")
    sha256 = _text(row.get("sha256"), f"{label} binding sha256")
    if sha256 != _sha256(content):
        raise ValueError(f"{label} binding hash mismatch")
    if row.get("bytes") is not None and row.get("bytes") != len(content):
        raise ValueError(f"{label} binding byte count mismatch")
    return {"path": path, "sha256": sha256, "bytes": len(content)}


def _decimal(value: object, label: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{label} must be a decimal") from error
    if not amount.is_finite() or amount <= 0:
        raise ValueError(f"{label} must be a positive finite decimal")
    return amount


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _binding_agrees_with_probe_report(
    report: Mapping[str, object], key: str, binding: Mapping[str, object]
) -> None:
    bindings = _object(report.get("artifact_bindings"), "probe artifact bindings")
    expected = _object(bindings.get(key), f"probe {key} artifact binding")
    if (
        expected.get("sha256") != binding.get("sha256")
        or expected.get("bytes") != binding.get("bytes")
    ):
        raise ValueError(f"probe report and {key} binding disagree")


def _strict_inputs(
    *,
    probe_report_bytes: bytes,
    probe_report_binding: object,
    probe_candidate_observations_bytes: bytes,
    probe_candidate_observations_binding: object,
    probe_process_observations_bytes: bytes,
    probe_process_observations_binding: object,
    probe_flow_mapping_gap_bytes: bytes,
    probe_flow_mapping_gap_binding: object,
    probe_selected_normalization_slice_bytes: bytes,
    probe_selected_normalization_slice_binding: object,
) -> Tuple[JsonObject, List[JsonObject], List[JsonObject], JsonObject, JsonObject, List[JsonObject]]:
    contents = {
        "report": probe_report_bytes,
        "candidate_observations": probe_candidate_observations_bytes,
        "process_observations": probe_process_observations_bytes,
        "flow_mapping_gap": probe_flow_mapping_gap_bytes,
        "selected_normalization_slice": probe_selected_normalization_slice_bytes,
    }
    supplied_bindings = {
        "report": probe_report_binding,
        "candidate_observations": probe_candidate_observations_binding,
        "process_observations": probe_process_observations_binding,
        "flow_mapping_gap": probe_flow_mapping_gap_binding,
        "selected_normalization_slice": probe_selected_normalization_slice_binding,
    }
    bindings = [
        _verify_binding(supplied_bindings[key], contents[key], f"probe {key}")
        for key in contents
    ]
    by_key = dict(zip(contents, bindings))
    report = _load_json(probe_report_bytes, "USLCI probe report")
    if (
        report.get("contract") != PROBE_CONTRACT
        or report.get("status") != "uslci_electricity_provider_candidates_probed"
        or report.get("candidate_count") != 4
        or report.get("selected_candidate_count") != 1
        or report.get("fetched_process_count") != 2
        or report.get("repository_version") != "1.2026-06.0"
        or report.get("selected_candidate_id")
        != "75d4be66-12a7-30b3-bc57-fa724c941b0e"
        or report.get("selected_dependency_id")
        != "5c13cc4e-b1eb-3fca-a291-b2891bd253a0"
        or _object(report.get("routing"), "probe routing").get("next_internal_route")
        != EXPECTED_ROUTE
        or report.get("exact_local_flow_identity_match") is not False
        or report.get("flow_mapping_status") != "explicit_mapping_required"
        or report.get("provider_build_authorized_count") != 0
        or report.get("process_creation_executed_count") != 0
        or report.get("model_connection_executed_count") != 0
        or report.get("scientific_comparison_ready") is not False
        or report.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("USLCI probe report contract or boundary drifted")
    for key in (
        "candidate_observations",
        "process_observations",
        "flow_mapping_gap",
        "selected_normalization_slice",
    ):
        _binding_agrees_with_probe_report(report, key, by_key[key])

    candidates = _load_jsonl(
        probe_candidate_observations_bytes, "USLCI candidate observations"
    )
    if len(candidates) != 4:
        raise ValueError("USLCI candidate observations must contain exactly four rows")
    selected = [row for row in candidates if row.get("selected") is True]
    if (
        len(selected) != 1
        or selected[0].get("candidate_id") != report.get("selected_candidate_id")
        or selected[0].get("level") != "at_user"
        or selected[0].get("residual_mix") is not False
        or selected[0].get("location") != US_LOCATION
        or selected[0].get("valid_from_year") != 2023
        or selected[0].get("valid_until_year") != 2023
    ):
        raise ValueError("selected USLCI candidate observation drifted")

    processes = _load_jsonl(
        probe_process_observations_bytes, "USLCI Process observations"
    )
    if len(processes) != 2 or {row.get("role") for row in processes} != {
        AT_USER_ROLE,
        AT_GRID_ROLE,
    }:
        raise ValueError("exactly two expected USLCI Process roles are required")
    gap = _load_json(probe_flow_mapping_gap_bytes, "USLCI Flow mapping gap")
    selected_slice = _load_json(
        probe_selected_normalization_slice_bytes,
        "selected USLCI normalization slice",
    )
    if (
        gap.get("contract") != MAPPING_GAP_CONTRACT
        or gap.get("mapping_status") != "explicit_mapping_required"
        or gap.get("identity_rewrite_allowed") is not False
        or gap.get("unit_conversion_applied") is not False
        or gap.get("connection_allowed") is not False
        or selected_slice.get("contract") != SELECTED_SLICE_CONTRACT
        or selected_slice.get("slice_id") != EXPECTED_ROUTE
        or selected_slice.get("normalization_plan_allowed") is not True
        or selected_slice.get("candidate_process_id")
        != report.get("selected_candidate_id")
        or selected_slice.get("dependency_process_id")
        != report.get("selected_dependency_id")
        or selected_slice.get("source_repository_version")
        != report.get("repository_version")
        or selected_slice.get("candidate_geography") != US_LOCATION
    ):
        raise ValueError("USLCI mapping gap or selected normalization slice drifted")
    if gap.get("requirement_id") != selected_slice.get("requirement_id"):
        raise ValueError("USLCI requirement identity drifted")
    for key in (
        "process_creation_allowed",
        "flow_identity_rewrite_allowed",
        "model_connection_allowed",
        "model_mutation_allowed",
        "remote_write_allowed",
        "comparison_allowed",
        "lcia_allowed",
    ):
        if selected_slice.get(key) is not False:
            raise ValueError(f"selected normalization slice unexpectedly permits {key}")
    return report, candidates, processes, gap, selected_slice, bindings


def _process_graph(
    processes: Sequence[Mapping[str, object]], report: Mapping[str, object]
) -> Tuple[JsonObject, JsonObject, List[JsonObject], List[JsonObject]]:
    by_role = {str(row.get("role")): dict(row) for row in processes}
    at_user = by_role[AT_USER_ROLE]
    at_grid = by_role[AT_GRID_ROLE]
    if (
        at_user.get("process_id") != report.get("selected_candidate_id")
        or at_grid.get("process_id") != report.get("selected_dependency_id")
    ):
        raise ValueError("observed USLCI Process identities drifted")

    normalized_exchanges: Dict[str, List[JsonObject]] = {}
    for role, process, expected_count, expected_inputs in (
        (AT_USER_ROLE, at_user, 2, 1),
        (AT_GRID_ROLE, at_grid, 68, 67),
    ):
        if (
            process.get("process_version") != "03.00.000"
            or _object(process.get("location"), f"{role} location").get(
                "location_name"
            )
            != US_LOCATION
            or not _text(process.get("valid_from"), f"{role} valid_from").startswith(
                "2023-"
            )
            or not _text(process.get("valid_until"), f"{role} valid_until").startswith(
                "2023-"
            )
        ):
            raise ValueError(f"{role} identity, geography, time, or version drifted")
        raw_exchanges = [
            _object(row, f"{role} exchange")
            for row in _array(process.get("exchanges"), f"{role} exchanges")
        ]
        if (
            len(raw_exchanges) != expected_count
            or process.get("exchange_count") != expected_count
            or process.get("input_exchange_count") != expected_inputs
            or process.get("output_exchange_count") != 1
        ):
            raise ValueError(f"{role} exchange counts drifted")
        internal_ids = [row.get("internal_id") for row in raw_exchanges]
        if len(set(internal_ids)) != len(internal_ids):
            raise ValueError(f"{role} exchange internal IDs are not unique")
        references = []
        inputs = []
        for exchange in raw_exchanges:
            direction = exchange.get("direction")
            if direction not in {"Input", "Output"}:
                raise ValueError(f"{role} exchange direction is invalid")
            flow = _object(exchange.get("flow"), f"{role} exchange Flow")
            unit = _object(exchange.get("unit"), f"{role} exchange unit")
            flow_property = _object(
                exchange.get("flow_property"), f"{role} exchange Flow property"
            )
            if (
                flow.get("flow_type") != "PRODUCT_FLOW"
                or unit.get("unit_name") != "MWh"
                or flow_property.get("flow_property_name") != "Energy"
                or flow_property.get("reference_unit") != "MJ"
                or flow.get("reference_unit") != "MJ"
            ):
                raise ValueError(
                    f"{role} exchange must remain an Energy product Flow in MWh/MJ"
                )
            _decimal(exchange.get("amount"), f"{role} exchange amount")
            if exchange.get("is_quantitative_reference") is True:
                references.append(exchange)
            if direction == "Input":
                provider = exchange.get("default_provider")
                if not isinstance(provider, dict):
                    raise ValueError(f"{role} Input has no declared default Provider")
                _text(provider.get("id"), f"{role} default Provider ID")
                _text(provider.get("name"), f"{role} default Provider name")
                if provider.get("type") != "Process":
                    raise ValueError(f"{role} default Provider type drifted")
                inputs.append(exchange)
            elif exchange.get("default_provider") is not None:
                raise ValueError(f"{role} Output unexpectedly declares a Provider")
        if (
            len(references) != 1
            or references[0].get("direction") != "Output"
            or len(inputs) != expected_inputs
        ):
            raise ValueError(f"{role} reference Output or Provider Inputs drifted")
        normalized_exchanges[role] = raw_exchanges

    at_user_inputs = [
        row for row in normalized_exchanges[AT_USER_ROLE] if row["direction"] == "Input"
    ]
    if at_user_inputs[0]["default_provider"]["id"] != at_grid["process_id"]:
        raise ValueError("at-user default Provider does not match the observed dependency")
    at_grid_inputs = [
        row for row in normalized_exchanges[AT_GRID_ROLE] if row["direction"] == "Input"
    ]
    provider_ids = [row["default_provider"]["id"] for row in at_grid_inputs]
    if len(set(provider_ids)) != 67:
        raise ValueError("at-grid Inputs must declare 67 distinct default Providers")
    at_grid_reference = next(
        row
        for row in normalized_exchanges[AT_GRID_ROLE]
        if row["is_quantitative_reference"] is True
    )
    input_flow_ids = {row["flow"]["flow_id"] for row in at_grid_inputs}
    if input_flow_ids != {at_grid_reference["flow"]["flow_id"]}:
        raise ValueError("at-grid Provider Inputs do not share the reference product Flow")
    return (
        at_user,
        at_grid,
        normalized_exchanges[AT_USER_ROLE],
        normalized_exchanges[AT_GRID_ROLE],
    )


def _field_mappings(processes: Sequence[Mapping[str, object]]) -> List[JsonObject]:
    definitions = (
        ("process_id", "/process_id", "processDataSet/processInformation/dataSetInformation/common:UUID", "identity_copy"),
        ("process_name", "/process_name", "processDataSet/processInformation/dataSetInformation/name/baseName", "language_qualified_text"),
        ("category", "/category", "processDataSet/processInformation/dataSetInformation/classificationInformation", "classification_mapping"),
        ("process_type", "/process_type", "processDataSet/modellingAndValidation/LCIMethodAndAllocation/typeOfDataSet", "controlled_vocabulary_mapping"),
        ("reference_exchange", "/reference_exchange/internal_id", "processDataSet/processInformation/quantitativeReference/referenceToReferenceFlow", "internal_id_reference"),
        ("valid_from", "/valid_from", "processDataSet/processInformation/time/common:referenceYear", "extract_year"),
        ("valid_until", "/valid_until", "processDataSet/processInformation/time/common:dataSetValidUntil", "extract_year"),
        ("time_description", "/time_description", "processDataSet/processInformation/time/timeRepresentativenessDescription", "text_copy"),
        ("location", "/location/location_name", "processDataSet/processInformation/geography/locationOfOperationSupplyOrProduction/@location", "location_identity_preflight_required"),
        ("technology_description", "/technology_description", "processDataSet/processInformation/technology/technologyDescriptionAndIncludedProcesses", "text_copy"),
        ("inventory_method_description", "/inventory_method_description", "processDataSet/modellingAndValidation/LCIMethodAndAllocation/deviationsFromLCIMethodApproaches", "text_copy_with_review"),
        ("sampling_description", "/sampling_description", "processDataSet/modellingAndValidation/dataSourcesTreatmentAndRepresentativeness/samplingProcedure", "text_copy"),
        ("intended_application", "/intended_application", "processDataSet/processInformation/dataSetInformation/generalComment", "qualified_text_copy"),
        ("sources", "/sources", "processDataSet/modellingAndValidation/dataSourcesTreatmentAndRepresentativeness/referenceToDataSource", "source_identity_binding_required"),
        ("exchanges", "/exchanges", "processDataSet/exchanges/exchange", "one_to_one_plus_explicit_flow_and_unit_mapping"),
        ("default_providers", "/exchanges/*/default_provider", "provider_connection_requirements", "extract_graph_edge_do_not_flatten"),
    )
    rows = []
    for process in processes:
        role = _text(process.get("role"), "Process role")
        for field, pointer, target, transformation in definitions:
            value = process.get(field)
            if field == "reference_exchange":
                value = _object(value, "reference exchange").get("internal_id")
            elif field == "location":
                value = _object(value, "Process location").get("location_name")
            elif field == "default_providers":
                value = process.get("default_provider_count")
            elif isinstance(value, list):
                value = {"row_count": len(value)}
            rows.append(
                {
                    "mapping_id": _sha256(
                        _canonical_bytes(
                            {
                                "role": role,
                                "source_pointer": pointer,
                                "target_path": target,
                            }
                        )
                    )[:16],
                    "source_process_role": role,
                    "source_format": "openlca_jsonld_v2_observation",
                    "source_pointer": pointer,
                    "source_value_summary": value,
                    "target_format": "tidas_process_or_connection_ledger",
                    "target_path": target,
                    "transformation": transformation,
                    "loss_status": (
                        "separate_graph_representation_required"
                        if field == "default_providers"
                        else "review_required"
                        if transformation.endswith("required")
                        or transformation.endswith("review")
                        else "reversible"
                    ),
                    "application_status": "proposed_not_applied",
                }
            )
    return rows


def _unit_transformations(
    process_exchanges: Sequence[Tuple[str, Mapping[str, object], Sequence[Mapping[str, object]]]]
) -> List[JsonObject]:
    rows = []
    with localcontext() as context:
        context.prec = 60
        inverse = Decimal(1) / MWH_TO_MJ
    for role, process, exchanges in process_exchanges:
        for exchange in exchanges:
            amount = _decimal(exchange.get("amount"), f"{role} exchange amount")
            target_amount = amount * MWH_TO_MJ
            core = {
                "source_process_id": process["process_id"],
                "source_exchange_internal_id": exchange["internal_id"],
                "source_unit": "MWh",
                "target_unit": "MJ",
            }
            rows.append(
                {
                    "transformation_id": _sha256(_canonical_bytes(core))[:16],
                    "source_process_role": role,
                    "source_process_id": process["process_id"],
                    "source_exchange_internal_id": exchange["internal_id"],
                    "direction": exchange["direction"],
                    "flow_id": exchange["flow"]["flow_id"],
                    "flow_property_id": exchange["flow_property"]["flow_property_id"],
                    "dimension": "Energy",
                    "source_amount": _decimal_text(amount),
                    "source_unit": "MWh",
                    "factor": "3600",
                    "factor_definition": "1 MWh = 3600 MJ",
                    "inverse_factor_exact": {"numerator": "1", "denominator": "3600"},
                    "inverse_factor_decimal_60": _decimal_text(inverse),
                    "proposed_target_amount": _decimal_text(target_amount),
                    "target_unit": "MJ",
                    "precision_policy": "Decimal_exact_forward_multiplication_no_binary_float",
                    "changes_flow_identity": False,
                    "changes_geography": False,
                    "application_status": "proposed_not_applied",
                }
            )
    return rows


def _connection_requirements(
    process_exchanges: Sequence[Tuple[str, Mapping[str, object], Sequence[Mapping[str, object]]]]
) -> List[JsonObject]:
    rows = []
    for role, process, exchanges in process_exchanges:
        for exchange in exchanges:
            if exchange.get("direction") != "Input":
                continue
            provider = _object(exchange.get("default_provider"), "default Provider")
            core = {
                "consumer": process["process_id"],
                "exchange": exchange["internal_id"],
                "provider": provider["id"],
                "flow": exchange["flow"]["flow_id"],
            }
            rows.append(
                {
                    "connection_requirement_id": _sha256(_canonical_bytes(core))[:16],
                    "source_consumer_role": role,
                    "source_consumer_process_id": process["process_id"],
                    "source_input_exchange_internal_id": exchange["internal_id"],
                    "source_flow_id": exchange["flow"]["flow_id"],
                    "source_flow_name": exchange["flow"]["flow_name"],
                    "source_amount": _decimal_text(
                        _decimal(exchange["amount"], "Provider connection amount")
                    ),
                    "source_unit": "MWh",
                    "source_provider_process_id": provider["id"],
                    "source_provider_process_name": provider["name"],
                    "source_provider_role": (
                        "at_grid_mix_provider"
                        if role == AT_USER_ROLE
                        else "upstream_generation_mix_provider"
                    ),
                    "target_representation": "tidas_lifecyclemodel_connection",
                    "target_process_exchange_field": None,
                    "status": "source_observed_not_materialized",
                    "connection_allowed": False,
                    "identity_rewrite_allowed": False,
                }
            )
    return rows


def _flow_mapping_proposals(
    at_user_exchanges: Sequence[Mapping[str, object]],
    at_grid_exchanges: Sequence[Mapping[str, object]],
    gap: Mapping[str, object],
) -> Tuple[List[JsonObject], JsonObject]:
    user_reference = next(
        row for row in at_user_exchanges if row.get("is_quantitative_reference") is True
    )
    grid_reference = next(
        row for row in at_grid_exchanges if row.get("is_quantitative_reference") is True
    )
    local = _object(gap.get("local_flow"), "local Flow")
    proposed_grid_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            "urn:lca-research-kernel:uslci-flow:"
            + _text(grid_reference["flow"].get("flow_id"), "grid source Flow ID"),
        )
    )
    proposed_grid = {
        "flow_id": proposed_grid_id,
        "flow_version": "00.00.001",
        "flow_name": "Electricity, AC, 2300-7650 V, USLCI US 2023",
        "flow_type": "Product flow",
        "flow_property": "Energy",
        "reference_unit": "MJ",
        "identity_status": "proposed_pending_native_identity_preflight",
    }
    rows = [
        {
            "proposal_id": "map-at-user-120v-to-existing-local-electricity",
            "source_flow": dict(user_reference["flow"]),
            "target_flow": dict(local),
            "mapping_kind": "candidate_equivalence_to_existing_local_flow",
            "semantic_loss_markers": [
                "voltage_specificity_removed",
                "US_source_geography_not_carried_by_local_flow_identity",
            ],
            "identity_rewrite_allowed": False,
            "application_status": "proposed_not_applied",
        },
        {
            "proposal_id": "map-at-grid-high-voltage-to-proposed-local-flow",
            "source_flow": dict(grid_reference["flow"]),
            "target_flow": proposed_grid,
            "mapping_kind": "proposed_new_local_flow_pending_native_preflight",
            "semantic_loss_markers": [],
            "identity_rewrite_allowed": False,
            "application_status": "proposed_not_applied",
        },
    ]
    return rows, proposed_grid


def _proxy_assessment(selected_slice: Mapping[str, object]) -> JsonObject:
    return {
        "contract": "lca_research_kernel.uslci_proxy_applicability_assessment/v1",
        "requirement_id": selected_slice["requirement_id"],
        "source_geography": US_LOCATION,
        "consumer_geography": "CN",
        "source_reference_year": 2023,
        "decision": "conditional_screening_proxy_for_structure_and_unit_pipeline_only",
        "allowed_uses": [
            "source_parser_verification",
            "unit_normalization_verification",
            "identity_preflight_preparation",
            "provider_graph_closure_preparation",
        ],
        "prohibited_uses": [
            "CN_representativeness_claim",
            "scientific_route_comparison",
            "comparative_LCIA",
            "publication_claim",
            "foreground_provider_acceptance",
        ],
        "dimensions": [
            {
                "dimension": "geography",
                "risk": "high",
                "finding": "US national electricity is not evidence of CN representativeness.",
            },
            {
                "dimension": "time",
                "risk": "medium",
                "finding": "The 2023 baseline is useful for screening but not automatically valid for the foreground study.",
            },
            {
                "dimension": "technology_and_market",
                "risk": "medium_high",
                "finding": "National at-user and at-grid mixes are structurally useful but not proven to match the target route.",
            },
            {
                "dimension": "inventory_closure",
                "risk": "high",
                "finding": "Sixty-seven upstream generation Providers remain unobserved.",
            },
        ],
        "percentage_supply_or_production_covered": 0,
        "tidas_deviation_note_required": True,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
    }


def _native_request_drafts(
    at_user: Mapping[str, object],
    at_grid: Mapping[str, object],
    at_user_exchanges: Sequence[Mapping[str, object]],
    at_grid_exchanges: Sequence[Mapping[str, object]],
    local_flow: Mapping[str, object],
    proposed_grid_flow: Mapping[str, object],
) -> List[JsonObject]:
    def reference(exchanges: Sequence[Mapping[str, object]]) -> Mapping[str, object]:
        return next(row for row in exchanges if row["is_quantitative_reference"] is True)

    user_ref = reference(at_user_exchanges)
    grid_ref = reference(at_grid_exchanges)
    process_specs = (
        (AT_USER_ROLE, at_user, user_ref, local_flow),
        (AT_GRID_ROLE, at_grid, grid_ref, proposed_grid_flow),
    )
    drafts = [
        {
            "draft_id": "flow-identity-preflight-at-grid-high-voltage",
            "native_capability": "flow identity-preflight",
            "request": {
                "target": {
                    "id": proposed_grid_flow["flow_id"],
                    "name": proposed_grid_flow["flow_name"],
                    "flow_type": "Product flow",
                    "flow_property": "Energy",
                    "reference_unit": "MJ",
                    "source_flow_id": grid_ref["flow"]["flow_id"],
                },
                "candidates": [],
            },
            "unresolved": ["native_identity_decision", "remote_candidate_coverage"],
            "invocation_allowed": False,
            "invocation_status": "draft_only_not_invoked",
        }
    ]
    for role, process, source_reference, target_flow in process_specs:
        drafts.append(
            {
                "draft_id": f"process-identity-preflight-{role}",
                "native_capability": "process identity-preflight",
                "request": {
                    "target": {
                        "provider_role": "background_provider_for_bound_input_exchange",
                        "reference_flow_id": target_flow["flow_id"],
                        "reference_flow_name": target_flow["flow_name"],
                        "source_process_id": process["process_id"],
                        "source_process_name": process["process_name"],
                    },
                    "candidates": [],
                },
                "unresolved": [
                    "native_identity_decision",
                    "US_to_CN_proxy_acceptance",
                ],
                "invocation_allowed": False,
                "invocation_status": "draft_only_not_invoked",
            }
        )
        drafts.append(
            {
                "draft_id": f"process-build-plan-{role}",
                "native_capability": "process build-plan validate",
                "request": {
                    "target": {
                        "kind": "Process",
                        "name": process["process_name"],
                        "geography": "US",
                        "technology_route": process.get("technology_description"),
                        "source_process_id": process["process_id"],
                    },
                    "identity_decision": {
                        "decision": "pending_identity_preflight",
                        "automatic": False,
                    },
                    "unit_of_analysis": {
                        "target_kind": "Process",
                        "reference_flow": {
                            "id": target_flow["flow_id"],
                            "name": target_flow["flow_name"],
                            "reference_unit": "MJ",
                            "flow_property": "Energy",
                        },
                        "source_reference_amount": _decimal_text(
                            _decimal(source_reference["amount"], "source reference amount")
                        ),
                        "source_reference_unit": "MWh",
                        "proposed_reference_amount": _decimal_text(
                            _decimal(source_reference["amount"], "source reference amount")
                            * MWH_TO_MJ
                        ),
                        "proposed_reference_unit": "MJ",
                        "declared_basis": "one source reference output normalized from MWh to MJ",
                        "functional_basis": "screening background electricity provider graph",
                        "scaling_evidence": "source_reference_exchange_and_exact_unit_factor",
                    },
                    "source_evidence": {
                        "source_process_id": process["process_id"],
                        "source_process_version": process["process_version"],
                        "source_geography": US_LOCATION,
                        "source_valid_from": process["valid_from"],
                        "source_valid_until": process["valid_until"],
                    },
                },
                "unresolved": [
                    "native_identity_decision",
                    "native_flow_identity_decision"
                    if role == AT_GRID_ROLE
                    else "candidate_flow_equivalence_review",
                    "US_to_CN_proxy_acceptance",
                    "upstream_inventory_closure"
                    if role == AT_GRID_ROLE
                    else "dependency_acceptance",
                ],
                "invocation_allowed": False,
                "invocation_status": "draft_only_not_invoked",
            }
        )
    return drafts


def compile_uslci_provider_normalization(
    *,
    probe_report_bytes: bytes,
    probe_report_binding: object,
    probe_candidate_observations_bytes: bytes,
    probe_candidate_observations_binding: object,
    probe_process_observations_bytes: bytes,
    probe_process_observations_binding: object,
    probe_flow_mapping_gap_bytes: bytes,
    probe_flow_mapping_gap_binding: object,
    probe_selected_normalization_slice_bytes: bytes,
    probe_selected_normalization_slice_binding: object,
) -> JsonObject:
    """Compile one strict, unapplied normalization and Provider-closure plan."""

    report, candidates, processes, gap, selected_slice, input_bindings = _strict_inputs(
        probe_report_bytes=probe_report_bytes,
        probe_report_binding=probe_report_binding,
        probe_candidate_observations_bytes=probe_candidate_observations_bytes,
        probe_candidate_observations_binding=probe_candidate_observations_binding,
        probe_process_observations_bytes=probe_process_observations_bytes,
        probe_process_observations_binding=probe_process_observations_binding,
        probe_flow_mapping_gap_bytes=probe_flow_mapping_gap_bytes,
        probe_flow_mapping_gap_binding=probe_flow_mapping_gap_binding,
        probe_selected_normalization_slice_bytes=probe_selected_normalization_slice_bytes,
        probe_selected_normalization_slice_binding=probe_selected_normalization_slice_binding,
    )
    at_user, at_grid, at_user_exchanges, at_grid_exchanges = _process_graph(
        processes, report
    )
    process_tuples = (
        (AT_USER_ROLE, at_user, at_user_exchanges),
        (AT_GRID_ROLE, at_grid, at_grid_exchanges),
    )
    field_mappings = _field_mappings((at_user, at_grid))
    unit_transformations = _unit_transformations(process_tuples)
    connections = _connection_requirements(process_tuples)
    if len(connections) != 68:
        raise ValueError("exactly 68 Provider connection requirements are required")
    upstream_connections = [
        row for row in connections if row["source_consumer_role"] == AT_GRID_ROLE
    ]
    if len(upstream_connections) != 67:
        raise ValueError("exactly 67 upstream generation Providers are required")
    flow_mappings, proposed_grid_flow = _flow_mapping_proposals(
        at_user_exchanges, at_grid_exchanges, gap
    )
    proxy = _proxy_assessment(selected_slice)
    native_drafts = _native_request_drafts(
        at_user,
        at_grid,
        at_user_exchanges,
        at_grid_exchanges,
        _object(gap.get("local_flow"), "local Flow"),
        proposed_grid_flow,
    )
    upstream_provider_ids = sorted(
        _text(row["source_provider_process_id"], "upstream Provider Process ID")
        for row in upstream_connections
    )
    selected_closure_slice = {
        "contract": "lca_research_kernel.selected_uslci_provider_closure_slice/v1",
        "slice_id": NEXT_ROUTE,
        "requirement_id": selected_slice["requirement_id"],
        "source_repository": selected_slice["source_repository"],
        "source_repository_version": selected_slice["source_repository_version"],
        "source_consumer_process_id": at_grid["process_id"],
        "source_flow_id": proposed_grid_flow["flow_id"],
        "source_foreign_flow_id": at_grid_exchanges[0]["flow"]["flow_id"],
        "upstream_provider_process_ids": upstream_provider_ids,
        "upstream_provider_count": len(upstream_provider_ids),
        "public_read_allowed": True,
        "credential_required": False,
        "external_write_allowed": False,
        "process_creation_allowed": False,
        "flow_creation_allowed": False,
        "identity_preflight_allowed": False,
        "build_plan_allowed": False,
        "model_connection_allowed": False,
        "model_mutation_allowed": False,
        "comparison_allowed": False,
        "lcia_allowed": False,
        "required_outputs": [
            "source_hashed_upstream_process_observations",
            "reference_product_and_provider_edge_validation",
            "elementary_exchange_and_inventory_type_summary",
            "next_provider_graph_closure_frontier",
        ],
    }
    totals = {}
    for role, _, exchanges in process_tuples:
        input_amount = sum(
            (_decimal(row["amount"], "input amount") for row in exchanges if row["direction"] == "Input"),
            Decimal("0"),
        )
        totals[role] = {
            "source_input_amount_mwh": _decimal_text(input_amount),
            "proposed_input_amount_mj": _decimal_text(input_amount * MWH_TO_MJ),
        }
    version_source = {
        "logic_version": USLCI_PROVIDER_NORMALIZATION_LOGIC_VERSION,
        "probe_version": report.get("probe_version"),
        "input_bindings": input_bindings,
        "provider_ids": upstream_provider_ids,
        "proposed_grid_flow_id": proposed_grid_flow["flow_id"],
    }
    normalization_version = _sha256(_canonical_bytes(version_source))[:16]
    zero_effects = {
        "process_creation_executed_count": 0,
        "flow_creation_executed_count": 0,
        "identity_preflight_executed_count": 0,
        "build_plan_executed_count": 0,
        "model_connection_executed_count": 0,
        "model_mutation_executed_count": 0,
        "external_read_executed_count": 0,
        "external_write_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
    }
    return {
        "schema_version": 1,
        "contract": USLCI_PROVIDER_NORMALIZATION_CONTRACT,
        "logic_version": USLCI_PROVIDER_NORMALIZATION_LOGIC_VERSION,
        "status": "uslci_provider_normalization_plan_compiled",
        "scope_id": report.get("scope_id"),
        "revision": report.get("revision"),
        "requirements_version": report.get("requirements_version"),
        "probe_version": report.get("probe_version"),
        "normalization_version": normalization_version,
        "input_bindings": input_bindings,
        "source_repository_version": report.get("repository_version"),
        "candidate_count": len(candidates),
        "process_count": 2,
        "exchange_count": len(at_user_exchanges) + len(at_grid_exchanges),
        "field_mapping_count": len(field_mappings),
        "unit_transformation_count": len(unit_transformations),
        "flow_mapping_proposal_count": len(flow_mappings),
        "provider_connection_requirement_count": len(connections),
        "upstream_generation_provider_count": len(upstream_provider_ids),
        "native_request_draft_count": len(native_drafts),
        "unapplied_transformation_count": len(unit_transformations) + len(flow_mappings),
        "provider_graph_closure_required": True,
        "provider_graph_closed": False,
        "source_amount_totals": totals,
        "proxy_decision": proxy["decision"],
        "proxy_high_risk_dimension_count": sum(
            1 for row in proxy["dimensions"] if row["risk"] == "high"
        ),
        "native_request_invocation_allowed_count": 0,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        **zero_effects,
        "field_mappings": field_mappings,
        "unit_transformations": unit_transformations,
        "flow_mapping_proposals": flow_mappings,
        "provider_connection_requirements": connections,
        "proxy_assessment": proxy,
        "native_request_drafts": native_drafts,
        "selected_closure_slice": selected_closure_slice,
        "external_patterns": [
            {
                "project": "OpenRefine/OpenRefine",
                "mechanism": "serializable_operations_plus_change_history",
                "local_landing": "unapplied_reversible_field_and_unit_transformations",
                "installed": False,
            },
            {
                "project": "dbt-labs/dbt-core",
                "mechanism": "dependency_DAG_plus_assertion_tests",
                "local_landing": "provider_connection_ledger_and_graph_invariants",
                "installed": False,
            },
            {
                "project": "hgrecco/pint",
                "mechanism": "quantity_dimension_and_Decimal_conversion",
                "local_landing": "exact_MWh_to_MJ_transformation_rows",
                "installed": False,
            },
            {
                "project": "GreenDelta/olca-schema",
                "mechanism": "authoritative_exchange_flow_unit_and_defaultProvider_semantics",
                "local_landing": "strict_source_parser_and_separate_connection_ledger",
                "installed": False,
            },
        ],
        "routing": {
            "next_internal_route": NEXT_ROUTE,
            "pause_internal_work": False,
            "reason": (
                "The wrapper and grid aggregator normalize reversibly, but their inventory "
                "cannot close until 67 exact upstream generation Providers are observed."
            ),
        },
        "next_action": (
            "Read and hash the 67 exact upstream USLCI generation Provider Processes, "
            "then compile the next graph-closure frontier without creating or connecting models."
        ),
    }


def materialize_uslci_provider_normalization(
    result: Mapping[str, object], out_dir: Path
) -> Dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    detailed_keys = {
        "field_mappings",
        "unit_transformations",
        "flow_mapping_proposals",
        "provider_connection_requirements",
        "proxy_assessment",
        "native_request_drafts",
        "selected_closure_slice",
    }
    payloads = {
        "field_mappings": _jsonl_bytes(
            [_object(row, "field mapping") for row in result["field_mappings"]]
        ),
        "unit_transformations": _jsonl_bytes(
            [_object(row, "unit transformation") for row in result["unit_transformations"]]
        ),
        "flow_mapping_proposals": _jsonl_bytes(
            [_object(row, "Flow mapping proposal") for row in result["flow_mapping_proposals"]]
        ),
        "provider_connection_requirements": _jsonl_bytes(
            [
                _object(row, "Provider connection requirement")
                for row in result["provider_connection_requirements"]
            ]
        ),
        "proxy_assessment": _json_bytes(
            _object(result["proxy_assessment"], "proxy assessment")
        ),
        "native_request_drafts": _json_bytes(
            _array(result["native_request_drafts"], "native request drafts")
        ),
        "selected_closure_slice": _json_bytes(
            _object(result["selected_closure_slice"], "selected closure slice")
        ),
    }
    filenames = {
        "field_mappings": "openlca-to-tidas-field-mappings.jsonl",
        "unit_transformations": "uslci-unit-transformations.jsonl",
        "flow_mapping_proposals": "uslci-flow-mapping-proposals.jsonl",
        "provider_connection_requirements": "uslci-provider-connection-requirements.jsonl",
        "proxy_assessment": "uslci-proxy-applicability-assessment.json",
        "native_request_drafts": "uslci-native-request-drafts.json",
        "selected_closure_slice": "selected-uslci-provider-closure-slice.json",
    }
    paths = {}
    artifact_bindings = {}
    for key, content in payloads.items():
        path = out_dir / filenames[key]
        path.write_bytes(content)
        paths[key] = path
        artifact_bindings[key] = {
            "path": path.name,
            "sha256": _sha256(content),
            "bytes": len(content),
        }
    report = {key: value for key, value in result.items() if key not in detailed_keys}
    report["artifact_bindings"] = artifact_bindings
    report_path = out_dir / "uslci-provider-normalization-plan.json"
    report_path.write_bytes(_json_bytes(report))
    return {"report": report_path, **paths}


def _binding_path(path: Path) -> Path:
    return Path(str(path) + ".binding.json")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-report", type=Path, required=True)
    parser.add_argument("--probe-report-binding", type=Path)
    parser.add_argument("--candidate-observations", type=Path, required=True)
    parser.add_argument("--candidate-observations-binding", type=Path)
    parser.add_argument("--process-observations", type=Path, required=True)
    parser.add_argument("--process-observations-binding", type=Path)
    parser.add_argument("--flow-mapping-gap", type=Path, required=True)
    parser.add_argument("--flow-mapping-gap-binding", type=Path)
    parser.add_argument("--selected-normalization-slice", type=Path, required=True)
    parser.add_argument("--selected-normalization-slice-binding", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    paths = {
        "report": args.probe_report,
        "candidate_observations": args.candidate_observations,
        "process_observations": args.process_observations,
        "flow_mapping_gap": args.flow_mapping_gap,
        "selected_normalization_slice": args.selected_normalization_slice,
    }
    explicit_bindings = {
        "report": args.probe_report_binding,
        "candidate_observations": args.candidate_observations_binding,
        "process_observations": args.process_observations_binding,
        "flow_mapping_gap": args.flow_mapping_gap_binding,
        "selected_normalization_slice": args.selected_normalization_slice_binding,
    }
    values = {}
    for key, path in paths.items():
        binding_path = explicit_bindings[key] or _binding_path(path)
        values[f"probe_{key}_bytes"] = path.read_bytes()
        values[f"probe_{key}_binding"] = json.loads(binding_path.read_text())
    result = compile_uslci_provider_normalization(**values)
    materialize_uslci_provider_normalization(result, args.out_dir / "outputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
