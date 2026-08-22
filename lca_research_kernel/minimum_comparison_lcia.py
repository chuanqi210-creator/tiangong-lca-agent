"""Execute a content-bound screening LCIA adapter over the accepted minimum model."""

from __future__ import annotations

import copy
import argparse
import hashlib
import json
import math
import random
import struct
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set

try:
    from .minimum_comparison_model import (
        ALLOCATION_FACTOR,
        CONTRACT as MODEL_CONTRACT,
        DATASET_VERSION,
        NATIVE_INPUTS_CONTRACT,
        ROUTE_MULTIPLIER,
        verify_minimum_comparison_model,
    )
except ImportError:  # Direct execution from the project workspace.
    from minimum_comparison_model import (  # type: ignore
        ALLOCATION_FACTOR,
        CONTRACT as MODEL_CONTRACT,
        DATASET_VERSION,
        NATIVE_INPUTS_CONTRACT,
        ROUTE_MULTIPLIER,
        verify_minimum_comparison_model,
    )


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.minimum_lifepo4_comparative_lcia/v1"
STATUS = "minimum_lifepo4_comparative_lcia_compiled"
METHOD_ID = "ipcc-ar6-gwp100-screening"
EXPECTED_ROUTES = ("solid-state", "hydrothermal")
NEXT_ROUTE = "assemble-lifepo4-comparative-interpretation-final-package"


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _hash(value: object) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return dict(value)


def _list(value: object, label: str) -> List[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    return list(value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _number(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError(f"{label} must be numeric") from error
    if not parsed.is_finite():
        raise ValueError(f"{label} must be finite")
    return float(parsed)


def _process_id(row: Mapping[str, object], label: str) -> str:
    root = _object(row.get("processDataSet"), f"{label} root")
    information = _object(root.get("processInformation"), f"{label} information")
    data = _object(information.get("dataSetInformation"), f"{label} data")
    return _text(data.get("common:UUID"), f"{label} UUID")


def _exchanges(row: Mapping[str, object], label: str) -> List[JsonObject]:
    root = _object(row.get("processDataSet"), f"{label} root")
    wrapper = _object(root.get("exchanges"), f"{label} exchanges")
    raw = wrapper.get("exchange")
    values = raw if isinstance(raw, list) else [raw]
    rows = [_object(value, f"{label} exchange") for value in values]
    if not rows:
        raise ValueError(f"{label} has no exchanges")
    return rows


def _model_instances(model: Mapping[str, object], label: str) -> List[JsonObject]:
    root = _object(model.get("lifeCycleModelDataSet"), f"{label} root")
    information = _object(
        root.get("lifeCycleModelInformation"), f"{label} information"
    )
    technology = _object(information.get("technology"), f"{label} technology")
    processes = _object(technology.get("processes"), f"{label} processes")
    raw = processes.get("processInstance")
    values = raw if isinstance(raw, list) else [raw]
    return [_object(value, f"{label} instance") for value in values]


def _parameter_map(rows: Sequence[object]) -> Dict[str, JsonObject]:
    parameters: Dict[str, JsonObject] = {}
    required = {
        "parameter_id",
        "baseline",
        "lower",
        "upper",
        "unit",
        "distribution_family",
        "derivation_class",
        "source_or_assumption_refs",
        "can_change_comparative_direction",
        "risk_id",
        "missing_value_interpreted_as_zero",
    }
    for value in rows:
        row = _object(value, "LCIA parameter")
        parameter_id = _text(row.get("parameter_id"), "LCIA parameter ID")
        if parameter_id in parameters or not required.issubset(row):
            raise ValueError("LCIA parameter membership or contract drifted")
        baseline = _number(row.get("baseline"), f"{parameter_id} baseline")
        lower = _number(row.get("lower"), f"{parameter_id} lower")
        upper = _number(row.get("upper"), f"{parameter_id} upper")
        if (
            not lower <= baseline <= upper
            or not upper > lower
            or row.get("distribution_family") != "triangular"
            or row.get("missing_value_interpreted_as_zero") is not False
            or not isinstance(row.get("source_or_assumption_refs"), list)
            or not row.get("source_or_assumption_refs")
        ):
            raise ValueError("LCIA parameter bounds or missing-value policy drifted")
        parameters[parameter_id] = row
    if len(parameters) != 14:
        raise ValueError("exactly 14 LCIA parameters are required")
    return parameters


def compile_minimum_comparison_lcia(
    model_report: Mapping[str, object],
    native_inputs: Mapping[str, object],
    performance_evidence: Mapping[str, object],
) -> JsonObject:
    """Compile exact route/core/coating terms without executing comparative arithmetic."""

    model = _object(model_report, "minimum comparison model")
    native = _object(native_inputs, "minimum comparison native inputs")
    performance = _object(performance_evidence, "performance evidence")
    verify_minimum_comparison_model(model)
    if (
        native.get("contract") != NATIVE_INPUTS_CONTRACT
        or native.get("status")
        != "minimum_lifepo4_comparison_native_inputs_compiled"
        or native.get("model_input_version") != model.get("input_version")
        or native.get("process_count") != 13
        or native.get("lifecyclemodel_count") != 2
        or native.get("lcia_method_count") != 1
        or native.get("parameter_ledger") != model.get("parameter_ledger")
        or native.get("allocation_contract") != model.get("allocation_contract")
    ):
        raise ValueError("accepted native model inputs are required")
    lcia = _object(model.get("lcia_method"), "LCIAMethod adapter")
    if (
        lcia.get("selected_method_id") != METHOD_ID
        or lcia.get("native_cli_calculation_command_available") is not False
        or lcia.get("adapter_required") is not True
        or lcia.get("parameter_ledger_sha256")
        != model.get("parameter_ledger_sha256")
    ):
        raise ValueError("LCIAMethod adapter contract drifted")
    parameter_rows = _list(model.get("parameter_ledger"), "parameter ledger")
    parameters = _parameter_map(parameter_rows)
    if _hash(parameter_rows) != model.get("parameter_ledger_sha256"):
        raise ValueError("parameter ledger content binding drifted")

    providers = [
        _object(value, "Provider Process")
        for value in _list(model.get("provider_processes"), "Provider Processes")
    ]
    provider_by_flow: Dict[str, JsonObject] = {}
    for row in providers:
        flow_id = _text(row.get("provided_flow_id"), "Provider Flow UUID")
        parameter_id = _text(row.get("parameter_id"), "Provider parameter ID")
        if flow_id in provider_by_flow or parameter_id not in parameters:
            raise ValueError("Provider-to-parameter binding drifted")
        provider_by_flow[flow_id] = row
    if len(provider_by_flow) != 10:
        raise ValueError("exactly ten Provider bindings are required")

    process_rows = [
        _object(value, "native Process")
        for value in _list(native.get("process_rows"), "native Process rows")
    ]
    process_by_id = {_process_id(row, "native Process"): row for row in process_rows}
    if len(process_rows) != 13 or len(process_by_id) != 13:
        raise ValueError("native Process membership drifted")
    route_models = {
        _text(row.get("route_id"), "route ID"): row
        for row in (
            _object(value, "LifecycleModel")
            for value in _list(model.get("lifecyclemodels"), "LifecycleModels")
        )
    }
    if set(route_models) != set(EXPECTED_ROUTES):
        raise ValueError("both route LifecycleModels are required")

    consumed: Set[str] = set()
    route_demands: Dict[str, List[JsonObject]] = {}
    route_process_ids: Dict[str, str] = {}
    for route_id in EXPECTED_ROUTES:
        model_row = route_models[route_id]
        if (
            model_row.get("route_process_multiplier") != ROUTE_MULTIPLIER
            or model_row.get("functional_unit_amount") != 1
            or model_row.get("functional_unit_unit") != "kg"
        ):
            raise ValueError(f"{route_id} route/FU contract drifted")
        instances = _model_instances(
            _object(model_row.get("lifecyclemodel"), f"{route_id} model row"),
            f"{route_id} LifecycleModel",
        )
        route_instance = next(
            (
                row
                for row in instances
                if str(row.get("@dataSetInternalID", "")) == "1"
            ),
            None,
        )
        if route_instance is None or _number(
            route_instance.get("@multiplicationFactor"),
            f"{route_id} route multiplier",
        ) != ROUTE_MULTIPLIER:
            raise ValueError(f"{route_id} route instance drifted")
        reference = _object(
            route_instance.get("referenceToProcess"),
            f"{route_id} Process reference",
        )
        process_id = _text(
            reference.get("@refObjectId"), f"{route_id} Process UUID"
        )
        process = _object(process_by_id.get(process_id), f"{route_id} Process")
        route_process_ids[route_id] = process_id
        demands = []
        for exchange in _exchanges(process, f"{route_id} Process"):
            if exchange.get("exchangeDirection") != "Input":
                continue
            flow = _object(
                exchange.get("referenceToFlowDataSet"),
                f"{route_id} input Flow",
            )
            flow_id = _text(flow.get("@refObjectId"), f"{route_id} Flow UUID")
            provider = _object(
                provider_by_flow.get(flow_id), f"{route_id} Flow Provider"
            )
            parameter_id = _text(
                provider.get("parameter_id"), f"{route_id} intensity parameter"
            )
            consumed.add(parameter_id)
            demands.append(
                {
                    "flow_key": provider["provided_flow_key"],
                    "flow_id": flow_id,
                    "provider_process_id": provider["process_id"],
                    "amount_before_route_scaling": _number(
                        exchange.get("meanAmount"), f"{route_id} input amount"
                    ),
                    "amount": _number(
                        exchange.get("meanAmount"), f"{route_id} input amount"
                    )
                    * ROUTE_MULTIPLIER,
                    "unit": provider["reference_unit"],
                    "intensity_parameter_id": parameter_id,
                    "source_or_assumption_refs": copy.deepcopy(
                        parameters[parameter_id]["source_or_assumption_refs"]
                    ),
                }
            )
        if not demands:
            raise ValueError(f"{route_id} has no LCIA demands")
        route_demands[route_id] = demands

    coating = _object(model.get("coating_process"), "coating Process")
    coating_row = _object(coating.get("process"), "coating Process row")
    coating_exchanges = _exchanges(coating_row, "coating Process")
    coating_parameter_by_flow = {
        "sucrose-coating-precursor": "coating:sucrose-input",
        "electricity": "coating:electricity-input",
    }
    coating_demands = []
    for exchange in coating_exchanges:
        if exchange.get("exchangeDirection") != "Input":
            continue
        flow = _object(exchange.get("referenceToFlowDataSet"), "coating input Flow")
        flow_id = _text(flow.get("@refObjectId"), "coating Flow UUID")
        provider = provider_by_flow.get(flow_id)
        if not isinstance(provider, dict):
            continue  # The route-core input is an internal connected product.
        flow_key = _text(provider.get("provided_flow_key"), "coating Flow key")
        if flow_key not in coating_parameter_by_flow:
            raise ValueError("unexpected externally provided coating input")
        amount_parameter_id = coating_parameter_by_flow[flow_key]
        intensity_parameter_id = _text(
            provider.get("parameter_id"), "coating intensity parameter"
        )
        if (
            amount_parameter_id not in parameters
            or intensity_parameter_id not in parameters
            or _number(exchange.get("meanAmount"), "coating exchange amount")
            != _number(parameters[amount_parameter_id]["baseline"], "coating baseline")
        ):
            raise ValueError("coating amount/parameter binding drifted")
        consumed.update((amount_parameter_id, intensity_parameter_id))
        coating_demands.append(
            {
                "flow_key": flow_key,
                "flow_id": flow_id,
                "amount_parameter_id": amount_parameter_id,
                "intensity_parameter_id": intensity_parameter_id,
                "amount_unit": parameters[amount_parameter_id]["unit"],
            }
        )
    if len(coating_demands) != 2:
        raise ValueError("shared coating must have two external Provider demands")
    consumed.add("coating:carbon-loss-gwp-proxy")
    allocation = _object(model.get("allocation_contract"), "allocation contract")
    if (
        allocation.get("route_id") != "hydrothermal"
        or allocation.get("mass_share") != ALLOCATION_FACTOR
        or allocation.get("application_stage") != "lcia_adapter"
        or model.get("allocation_application_count") != 1
    ):
        raise ValueError("hydrothermal allocation contract drifted")
    consumed.add("allocation:hydrothermal-mass-share")
    if consumed != set(parameters):
        raise ValueError("not all LCIA parameters are consumed by the adapter")

    if isinstance(performance.get("comparison"), dict):
        comparison = _object(
            performance.get("comparison"), "performance comparison"
        )
        measurements = {
            _text(row.get("route_id"), "performance route"): _number(
                row.get("value"), "performance value"
            )
            for row in (
                _object(value, "performance measurement")
                for value in _list(comparison.get("measurements"), "measurements")
            )
        }
        property_name = comparison.get("property")
        value_unit = comparison.get("value_unit")
        policy = _object(
            performance.get("decision_policy"), "performance policy"
        )
        performance_scope_id = performance.get("scope_id")
    else:
        compact = _object(
            performance.get("performance_equivalence", performance),
            "compact performance equivalence",
        )
        raw_measurements = _object(
            compact.get("measurements_mAh_per_g"),
            "compact performance measurements",
        )
        measurements = {
            route_id: _number(raw_measurements.get(route_id), route_id)
            for route_id in EXPECTED_ROUTES
        }
        property_name = "specific_discharge_capacity"
        value_unit = "mAh/g"
        policy = compact
        performance_scope_id = performance.get("scope_id", model.get("scope_id"))
    if (
        performance_scope_id != model.get("scope_id")
        or property_name != "specific_discharge_capacity"
        or value_unit != "mAh/g"
        or measurements != {"solid-state": 147.0, "hydrothermal": 135.0}
        or policy.get("threshold_percent") != 10
    ):
        raise ValueError("accepted performance-equivalence evidence drifted")

    identity = {
        "contract": CONTRACT,
        "model_input_version": model.get("input_version"),
        "native_input_version": native.get("input_version"),
        "parameter_ledger_sha256": model.get("parameter_ledger_sha256"),
        "route_demands": route_demands,
        "coating_demands": coating_demands,
        "allocation": allocation,
        "performance": measurements,
    }
    return {
        "schema_version": 1,
        "contract": CONTRACT,
        "status": STATUS,
        "input_version": _hash(identity)[:16],
        "model_contract": MODEL_CONTRACT,
        "model_input_version": model.get("input_version"),
        "native_input_version": native.get("input_version"),
        "scope_id": model.get("scope_id"),
        "revision": model.get("revision"),
        "method_id": METHOD_ID,
        "functional_unit": {
            "amount": 1,
            "unit": "kg",
            "product": "carbon-coated LiFePO4 cathode active material",
            "boundary": "factory_gate_screening",
        },
        "route_process_multiplier": ROUTE_MULTIPLIER,
        "route_process_ids": route_process_ids,
        "route_core_demands": route_demands,
        "coating_demands": coating_demands,
        "carbon_loss_parameter_id": "coating:carbon-loss-gwp-proxy",
        "allocation_parameter_id": "allocation:hydrothermal-mass-share",
        "allocation_mass_share": ALLOCATION_FACTOR,
        "allocation_application_count": 1,
        "allocation_application_scope": "hydrothermal_route_core_only",
        "parameters": copy.deepcopy(parameter_rows),
        "parameter_count": 14,
        "consumed_parameter_ids": sorted(consumed),
        "consumed_parameter_count": len(consumed),
        "performance_capacity_mah_g": measurements,
        "performance_threshold_percent": 10,
        "missing_values_interpreted_as_zero_count": 0,
        "network_read_executed_count": 0,
        "remote_write_executed_count": 0,
        "publication_executed_count": 0,
    }


def _values(compiled: Mapping[str, object], overrides: Mapping[str, float]) -> Dict[str, float]:
    values = {}
    for value in _list(compiled.get("parameters"), "compiled parameters"):
        row = _object(value, "compiled parameter")
        parameter_id = _text(row.get("parameter_id"), "compiled parameter ID")
        selected = overrides.get(parameter_id, _number(row.get("baseline"), "baseline"))
        lower = _number(row.get("lower"), f"{parameter_id} lower")
        upper = _number(row.get("upper"), f"{parameter_id} upper")
        if selected < lower or selected > upper:
            raise ValueError(f"{parameter_id} override is outside accepted bounds")
        values[parameter_id] = float(selected)
    if set(overrides) - set(values):
        raise ValueError("unknown LCIA parameter override")
    return values


def evaluate_minimum_comparison_lcia(
    compiled_input: Mapping[str, object],
    overrides: Mapping[str, float] = {},
    *,
    route_energy_multiplier: float = 1.0,
) -> JsonObject:
    """Evaluate one bounded parameter vector with route/core/coating terms separated."""

    compiled = _object(compiled_input, "compiled LCIA input")
    if (
        compiled.get("contract") != CONTRACT
        or compiled.get("status") != STATUS
        or compiled.get("parameter_count") != 14
        or compiled.get("consumed_parameter_count") != 14
        or compiled.get("allocation_application_count") != 1
        or compiled.get("allocation_application_scope")
        != "hydrothermal_route_core_only"
    ):
        raise ValueError("compiled comparative LCIA input is required")
    values = _values(compiled, overrides)
    if (
        isinstance(route_energy_multiplier, bool)
        or not isinstance(route_energy_multiplier, (int, float))
        or not 0 < route_energy_multiplier <= 1
    ):
        raise ValueError("route energy interpretation multiplier is invalid")
    coating_contributions = []
    coating_total = 0.0
    for value in _list(compiled.get("coating_demands"), "coating demands"):
        row = _object(value, "coating demand")
        amount_parameter = _text(
            row.get("amount_parameter_id"), "coating amount parameter"
        )
        intensity_parameter = _text(
            row.get("intensity_parameter_id"), "coating intensity parameter"
        )
        amount = values[amount_parameter]
        intensity = values[intensity_parameter]
        impact = amount * intensity
        coating_total += impact
        coating_contributions.append(
            {
                "stage": "shared_coating",
                "flow_key": row["flow_key"],
                "amount": amount,
                "amount_parameter_id": amount_parameter,
                "intensity": intensity,
                "intensity_parameter_id": intensity_parameter,
                "impact_kg_co2e": impact,
            }
        )
    carbon_parameter = _text(
        compiled.get("carbon_loss_parameter_id"), "carbon-loss parameter"
    )
    carbon_impact = values[carbon_parameter]
    coating_total += carbon_impact
    coating_contributions.append(
        {
            "stage": "shared_coating",
            "flow_key": "carbon-loss-gwp-proxy",
            "amount": 1,
            "amount_unit": "kg coated product",
            "intensity": carbon_impact,
            "intensity_parameter_id": carbon_parameter,
            "impact_kg_co2e": carbon_impact,
            "proxy_not_elementary_inventory": True,
        }
    )

    route_rows = []
    for route_id in EXPECTED_ROUTES:
        contributions = []
        unallocated = 0.0
        route_demands = _object(
            compiled.get("route_core_demands"), "route demands"
        )
        for value in _list(route_demands.get(route_id), f"{route_id} demands"):
            row = _object(value, f"{route_id} demand")
            parameter_id = _text(
                row.get("intensity_parameter_id"), f"{route_id} intensity parameter"
            )
            amount = _number(row.get("amount"), f"{route_id} amount")
            if row.get("flow_key") in {"electricity", "natural-gas-heat-proxy"}:
                amount *= route_energy_multiplier
            intensity = values[parameter_id]
            impact = amount * intensity
            unallocated += impact
            contributions.append(
                {
                    "stage": "route_core",
                    "flow_key": row["flow_key"],
                    "amount": amount,
                    "unit": row["unit"],
                    "intensity": intensity,
                    "intensity_parameter_id": parameter_id,
                    "unallocated_impact_kg_co2e": impact,
                }
            )
        allocation_factor = (
            values[_text(compiled.get("allocation_parameter_id"), "allocation parameter")]
            if route_id == "hydrothermal"
            else 1.0
        )
        allocated = unallocated * allocation_factor
        total = allocated + coating_total
        route_rows.append(
            {
                "route_id": route_id,
                "route_core_unallocated_kg_co2e": unallocated,
                "allocation_factor": allocation_factor,
                "allocation_application_count": 1 if route_id == "hydrothermal" else 0,
                "route_core_allocated_kg_co2e": allocated,
                "shared_coating_kg_co2e": coating_total,
                "total_kg_co2e": total,
                "route_core_contributions": contributions,
                "shared_coating_contributions": copy.deepcopy(coating_contributions),
            }
        )
    by_route = {str(row["route_id"]): row for row in route_rows}
    solid = float(by_route["solid-state"]["total_kg_co2e"])
    hydro = float(by_route["hydrothermal"]["total_kg_co2e"])
    difference = hydro - solid
    return {
        "schema_version": 1,
        "contract": f"{CONTRACT}_evaluation",
        "input_version": compiled.get("input_version"),
        "parameter_values": values,
        "routes": route_rows,
        "difference_hydro_minus_solid_kg_co2e": difference,
        "ratio_hydro_over_solid": hydro / solid,
        "lower_impact_route": "solid-state" if difference > 0 else "hydrothermal" if difference < 0 else "tie",
        "allocation_application_count": 1,
        "shared_coating_applied_once_per_route": True,
        "route_energy_interpretation_multiplier": route_energy_multiplier,
    }


def execute_minimum_comparison_lcia_baseline(
    compiled_input: Mapping[str, object],
) -> JsonObject:
    result = evaluate_minimum_comparison_lcia(compiled_input)
    result["status"] = "minimum_lifepo4_comparative_lcia_baseline_executed"
    result["scenario_id"] = "baseline_mass_functional_unit"
    result["parameter_count"] = len(result["parameter_values"])
    result["screening_claim_only"] = True
    return result


def _parameter_rows_by_id(compiled: Mapping[str, object]) -> Dict[str, JsonObject]:
    return {
        _text(row.get("parameter_id"), "parameter ID"): row
        for row in (
            _object(value, "parameter")
            for value in _list(compiled.get("parameters"), "parameters")
        )
    }


def _scenario(
    compiled: Mapping[str, object],
    scenario_id: str,
    overrides: Mapping[str, float],
    *,
    route_energy_multiplier: float = 1.0,
    rationale: str,
) -> JsonObject:
    result = evaluate_minimum_comparison_lcia(
        compiled,
        overrides,
        route_energy_multiplier=route_energy_multiplier,
    )
    return {
        **result,
        "scenario_id": scenario_id,
        "parameter_overrides": dict(overrides),
        "rationale": rationale,
        "functional_unit": "1 kg carbon-coated LiFePO4",
    }


def execute_minimum_comparison_lcia_named_scenarios(
    compiled_input: Mapping[str, object],
) -> JsonObject:
    """Execute the bounded named scenarios required for the decision."""

    compiled = _object(compiled_input, "compiled LCIA input")
    parameters = _parameter_rows_by_id(compiled)

    def bound(parameter_id: str, key: str) -> float:
        return _number(parameters[parameter_id][key], f"{parameter_id} {key}")

    scenarios = [
        _scenario(
            compiled,
            "baseline-us-short-ton-mass-fu",
            {},
            rationale="Accepted US-short-ton interpretation and mass allocation baseline.",
        ),
        _scenario(
            compiled,
            "electricity-intensity-low",
            {"provider-gwp:electricity": bound("provider-gwp:electricity", "lower")},
            rationale="Low bounded electricity GWP intensity.",
        ),
        _scenario(
            compiled,
            "electricity-intensity-high",
            {"provider-gwp:electricity": bound("provider-gwp:electricity", "upper")},
            rationale="High bounded electricity GWP intensity.",
        ),
        _scenario(
            compiled,
            "coating-demand-low",
            {
                parameter_id: bound(parameter_id, "lower")
                for parameter_id in (
                    "coating:sucrose-input",
                    "coating:electricity-input",
                    "coating:carbon-loss-gwp-proxy",
                )
            },
            rationale="Low bounded shared coating demand and carbon-loss proxy.",
        ),
        _scenario(
            compiled,
            "coating-demand-high",
            {
                parameter_id: bound(parameter_id, "upper")
                for parameter_id in (
                    "coating:sucrose-input",
                    "coating:electricity-input",
                    "coating:carbon-loss-gwp-proxy",
                )
            },
            rationale="High bounded shared coating demand and carbon-loss proxy.",
        ),
        _scenario(
            compiled,
            "hydrothermal-no-allocation",
            {"allocation:hydrothermal-mass-share": 1.0},
            rationale="Assign all hydrothermal route-core burden to LiFePO4.",
        ),
        {
            **_scenario(
                compiled,
                "hydrothermal-strong-coproduct-burden-split-bound",
                {"allocation:hydrothermal-mass-share": 0.4},
                rationale="Strong bounded coproduct burden split; not an avoided-product credit.",
            ),
            "not_proven_substitution_credit": True,
        },
        _scenario(
            compiled,
            "metric-ton-route-energy-interpretation",
            {},
            route_energy_multiplier=0.90718474,
            rationale="Scale route preparation energy from assumed US short ton to metric-ton denominator; shared coating is unchanged.",
        ),
    ]
    baseline = evaluate_minimum_comparison_lcia(compiled)
    baseline_by_route = {
        str(row["route_id"]): row
        for row in baseline["routes"]
        if isinstance(row, dict)
    }
    capacities = _object(
        compiled.get("performance_capacity_mah_g"), "performance capacities"
    )
    service_routes = []
    for route_id in EXPECTED_ROUTES:
        capacity = _number(capacities.get(route_id), f"{route_id} capacity")
        material_mass = 1000.0 / capacity
        per_kg = float(baseline_by_route[route_id]["total_kg_co2e"])
        service_routes.append(
            {
                "route_id": route_id,
                "capacity_mah_g": capacity,
                "material_mass_kg": material_mass,
                "mass_fu_impact_kg_co2e_per_kg": per_kg,
                "total_kg_co2e_per_1kah": per_kg * material_mass,
            }
        )
    service_by_route = {str(row["route_id"]): row for row in service_routes}
    service_difference = (
        float(service_by_route["hydrothermal"]["total_kg_co2e_per_1kah"])
        - float(service_by_route["solid-state"]["total_kg_co2e_per_1kah"])
    )
    scenarios.append(
        {
            "scenario_id": "performance-normalized-1kAh-cycle-30",
            "functional_unit": "1 kAh at cycle 30",
            "parameter_overrides": {},
            "rationale": "Decision-policy service normalization using accepted 147/135 mAh/g public-abstract values.",
            "routes": service_routes,
            "difference_hydro_minus_solid_kg_co2e_per_1kah": service_difference,
            "lower_impact_route": (
                "solid-state" if service_difference > 0 else "hydrothermal"
                if service_difference < 0
                else "tie"
            ),
            "performance_temperature_missing": True,
            "industrial_performance_claim_allowed": False,
        }
    )
    baseline_direction = scenarios[0]["lower_impact_route"]
    reversals = [
        row["scenario_id"]
        for row in scenarios
        if row.get("lower_impact_route") != baseline_direction
    ]
    return {
        "schema_version": 1,
        "contract": f"{CONTRACT}_named_scenarios",
        "status": "minimum_lifepo4_named_scenarios_executed",
        "input_version": compiled.get("input_version"),
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
        "baseline_lower_impact_route": baseline_direction,
        "direction_reversal_scenario_count": len(reversals),
        "direction_reversal_scenario_ids": reversals,
        "network_read_executed_count": 0,
        "remote_write_executed_count": 0,
        "publication_executed_count": 0,
    }


def execute_minimum_comparison_lcia_sensitivity(
    compiled_input: Mapping[str, object],
) -> JsonObject:
    """Execute both accepted bounds for every parameter and rank difference change."""

    compiled = _object(compiled_input, "compiled LCIA input")
    baseline = evaluate_minimum_comparison_lcia(compiled)
    baseline_difference = float(
        baseline["difference_hydro_minus_solid_kg_co2e"]
    )
    baseline_direction = str(baseline["lower_impact_route"])
    parameters = _parameter_rows_by_id(compiled)
    perturbations = []
    maximum_by_parameter = {}
    for parameter_id in sorted(parameters):
        row = parameters[parameter_id]
        changes = []
        for key in ("lower", "upper"):
            value = _number(row.get(key), f"{parameter_id} {key}")
            result = evaluate_minimum_comparison_lcia(
                compiled, {parameter_id: value}
            )
            difference = float(
                result["difference_hydro_minus_solid_kg_co2e"]
            )
            change = difference - baseline_difference
            changes.append(abs(change))
            perturbations.append(
                {
                    "parameter_id": parameter_id,
                    "bound": key,
                    "value": value,
                    "unit": row["unit"],
                    "difference_hydro_minus_solid_kg_co2e": difference,
                    "change_from_baseline_kg_co2e": change,
                    "absolute_change_kg_co2e": abs(change),
                    "lower_impact_route": result["lower_impact_route"],
                    "direction_reversed": result["lower_impact_route"]
                    != baseline_direction,
                }
            )
        maximum_by_parameter[parameter_id] = max(changes)
    ranked = []
    for rank, (parameter_id, maximum) in enumerate(
        sorted(
            maximum_by_parameter.items(),
            key=lambda item: (-item[1], item[0]),
        ),
        start=1,
    ):
        ranked.append(
            {
                "rank": rank,
                "parameter_id": parameter_id,
                "maximum_absolute_difference_change_kg_co2e": maximum,
                "can_change_comparative_direction": parameters[parameter_id][
                    "can_change_comparative_direction"
                ],
                "risk_id": parameters[parameter_id]["risk_id"],
            }
        )
    return {
        "schema_version": 1,
        "contract": f"{CONTRACT}_oat_sensitivity",
        "status": "minimum_lifepo4_oat_sensitivity_executed",
        "input_version": compiled.get("input_version"),
        "baseline_difference_hydro_minus_solid_kg_co2e": baseline_difference,
        "baseline_lower_impact_route": baseline_direction,
        "parameter_count": len(parameters),
        "perturbation_count": len(perturbations),
        "perturbations": perturbations,
        "ranked_parameters": ranked,
        "direction_reversal_perturbation_count": sum(
            1 for row in perturbations if row["direction_reversed"]
        ),
        "network_read_executed_count": 0,
        "remote_write_executed_count": 0,
        "publication_executed_count": 0,
    }


def _fast_totals(
    compiled: Mapping[str, object], values: Mapping[str, float]
) -> tuple[float, float, float]:
    coating = values["coating:carbon-loss-gwp-proxy"]
    for value in _list(compiled.get("coating_demands"), "coating demands"):
        row = _object(value, "coating demand")
        coating += (
            values[str(row["amount_parameter_id"])]
            * values[str(row["intensity_parameter_id"])]
        )
    route_demands = _object(compiled.get("route_core_demands"), "route demands")
    cores = {}
    for route_id in EXPECTED_ROUTES:
        cores[route_id] = sum(
            _number(row.get("amount"), f"{route_id} amount")
            * values[str(row["intensity_parameter_id"])]
            for row in (
                _object(value, f"{route_id} demand")
                for value in _list(route_demands.get(route_id), f"{route_id} demands")
            )
        )
    solid = cores["solid-state"] + coating
    hydro = (
        cores["hydrothermal"]
        * values["allocation:hydrothermal-mass-share"]
        + coating
    )
    return solid, hydro, hydro - solid


def _percentiles(values: Sequence[float]) -> JsonObject:
    ordered = sorted(values)

    def percentile(probability: float) -> float:
        position = (len(ordered) - 1) * probability
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return ordered[lower]
        weight = position - lower
        return ordered[lower] * (1 - weight) + ordered[upper] * weight

    mean = sum(ordered) / len(ordered)
    variance = sum((value - mean) ** 2 for value in ordered) / len(ordered)
    return {
        "p2_5": percentile(0.025),
        "p50": percentile(0.5),
        "p97_5": percentile(0.975),
        "mean": mean,
        "standard_deviation": math.sqrt(variance),
    }


def _ranks(values: Sequence[float]) -> List[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position + 1
        while end < len(order) and values[order[end]] == values[order[position]]:
            end += 1
        average = (position + 1 + end) / 2.0
        for index in order[position:end]:
            ranks[index] = average
        position = end
    return ranks


def _pearson(left: Sequence[float], right: Sequence[float]) -> float:
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right)
    )
    left_sum = sum((value - left_mean) ** 2 for value in left)
    right_sum = sum((value - right_mean) ** 2 for value in right)
    denominator = math.sqrt(left_sum * right_sum)
    return numerator / denominator if denominator else 0.0


def execute_minimum_comparison_lcia_uncertainty(
    compiled_input: Mapping[str, object],
    *,
    trial_count: int = 20000,
    seed: int = 20260719,
) -> JsonObject:
    """Run shared-draw triangular Monte Carlo and deterministic rank correlations."""

    compiled = _object(compiled_input, "compiled LCIA input")
    if (
        isinstance(trial_count, bool)
        or not isinstance(trial_count, int)
        or trial_count < 20000
        or isinstance(seed, bool)
        or not isinstance(seed, int)
    ):
        raise ValueError("uncertainty requires at least 20,000 trials and an integer seed")
    parameters = _parameter_rows_by_id(compiled)
    parameter_ids = sorted(parameters)
    generator = random.Random(seed)
    samples = {parameter_id: [] for parameter_id in parameter_ids}
    solid_values = []
    hydro_values = []
    differences = []
    digest = hashlib.sha256()
    solid_lower = 0
    hydro_lower = 0
    ties = 0
    for _ in range(trial_count):
        values = {}
        for parameter_id in parameter_ids:
            row = parameters[parameter_id]
            value = generator.triangular(
                _number(row.get("lower"), f"{parameter_id} lower"),
                _number(row.get("upper"), f"{parameter_id} upper"),
                _number(row.get("baseline"), f"{parameter_id} baseline"),
            )
            values[parameter_id] = value
            samples[parameter_id].append(value)
        solid, hydro, difference = _fast_totals(compiled, values)
        solid_values.append(solid)
        hydro_values.append(hydro)
        differences.append(difference)
        digest.update(struct.pack(">ddd", solid, hydro, difference))
        if difference > 0:
            solid_lower += 1
        elif difference < 0:
            hydro_lower += 1
        else:
            ties += 1
    difference_ranks = _ranks(differences)
    correlations = []
    for parameter_id in parameter_ids:
        correlation = _pearson(_ranks(samples[parameter_id]), difference_ranks)
        correlations.append(
            {
                "parameter_id": parameter_id,
                "spearman_rank_correlation_with_hydro_minus_solid": correlation,
                "absolute_correlation": abs(correlation),
                "risk_id": parameters[parameter_id]["risk_id"],
            }
        )
    correlations.sort(
        key=lambda row: (-float(row["absolute_correlation"]), str(row["parameter_id"]))
    )
    for rank, row in enumerate(correlations, start=1):
        row["rank"] = rank
    baseline = evaluate_minimum_comparison_lcia(compiled)
    baseline_difference = float(
        baseline["difference_hydro_minus_solid_kg_co2e"]
    )
    reversal_count = (
        hydro_lower if baseline_difference > 0 else solid_lower
        if baseline_difference < 0
        else trial_count - ties
    )
    return {
        "schema_version": 1,
        "contract": f"{CONTRACT}_uncertainty",
        "status": "minimum_lifepo4_uncertainty_executed",
        "input_version": compiled.get("input_version"),
        "trial_count": trial_count,
        "seed": seed,
        "distribution_family": "triangular",
        "parameter_count": len(parameter_ids),
        "shared_parameter_draw_count_per_trial": len(parameter_ids),
        "correlation_semantics": "one draw per parameter reused by every route term in that trial",
        "route_and_difference_percentiles": {
            "solid_state_kg_co2e": _percentiles(solid_values),
            "hydrothermal_kg_co2e": _percentiles(hydro_values),
            "hydro_minus_solid_kg_co2e": _percentiles(differences),
        },
        "probability_solid_state_lower": solid_lower / trial_count,
        "probability_hydrothermal_lower": hydro_lower / trial_count,
        "probability_tie": ties / trial_count,
        "baseline_lower_impact_route": baseline["lower_impact_route"],
        "direction_reversal_probability": reversal_count / trial_count,
        "parameter_difference_rank_correlations": correlations,
        "trial_digest_sha256": digest.hexdigest(),
        "trial_rows_persisted": False,
        "network_read_executed_count": 0,
        "remote_write_executed_count": 0,
        "publication_executed_count": 0,
    }


def _contribution_table(baseline: Mapping[str, object]) -> JsonObject:
    rows = []
    for value in _list(baseline.get("routes"), "baseline routes"):
        route = _object(value, "baseline route")
        route_id = _text(route.get("route_id"), "baseline route ID")
        for contribution_value in _list(
            route.get("route_core_contributions"), "route-core contributions"
        ):
            contribution = _object(contribution_value, "route-core contribution")
            rows.append(
                {
                    "route_id": route_id,
                    "stage": "route_core",
                    "flow_key": contribution["flow_key"],
                    "unallocated_impact_kg_co2e": contribution[
                        "unallocated_impact_kg_co2e"
                    ],
                    "allocation_factor": route["allocation_factor"],
                    "allocated_impact_kg_co2e": float(
                        contribution["unallocated_impact_kg_co2e"]
                    )
                    * float(route["allocation_factor"]),
                    "parameter_id": contribution["intensity_parameter_id"],
                }
            )
        for contribution_value in _list(
            route.get("shared_coating_contributions"),
            "shared-coating contributions",
        ):
            contribution = _object(
                contribution_value, "shared-coating contribution"
            )
            rows.append(
                {
                    "route_id": route_id,
                    "stage": "shared_coating",
                    "flow_key": contribution["flow_key"],
                    "allocated_impact_kg_co2e": contribution["impact_kg_co2e"],
                    "parameter_id": contribution["intensity_parameter_id"],
                    "allocation_factor": 1,
                }
            )
    return {
        "schema_version": 1,
        "contract": f"{CONTRACT}_contribution_table",
        "row_count": len(rows),
        "rows": rows,
    }


def _equations(compiled: Mapping[str, object]) -> JsonObject:
    return {
        "schema_version": 1,
        "contract": f"{CONTRACT}_equations",
        "functional_unit": copy.deepcopy(compiled["functional_unit"]),
        "route_process_multiplier": compiled["route_process_multiplier"],
        "solid_state": "sum(route demand * provider intensity) + shared coating",
        "hydrothermal": "sum(route demand * provider intensity) * hydro mass-allocation share + shared coating",
        "shared_coating": "sucrose amount * sucrose intensity + electricity amount * electricity intensity + carbon-loss GWP proxy",
        "allocation_application_count": 1,
        "allocation_application_scope": "hydrothermal_route_core_only",
        "route_core_demands": copy.deepcopy(compiled["route_core_demands"]),
        "coating_demands": copy.deepcopy(compiled["coating_demands"]),
        "parameter_source_rows": copy.deepcopy(compiled["parameters"]),
        "missing_values_interpreted_as_zero_count": 0,
    }


def build_minimum_comparison_lcia_analysis(
    compiled_input: Mapping[str, object],
) -> JsonObject:
    """Build every deterministic numerical and bounded-interpretation output."""

    compiled = _object(compiled_input, "compiled LCIA input")
    baseline = execute_minimum_comparison_lcia_baseline(compiled)
    named = execute_minimum_comparison_lcia_named_scenarios(compiled)
    sensitivity = execute_minimum_comparison_lcia_sensitivity(compiled)
    uncertainty = execute_minimum_comparison_lcia_uncertainty(compiled)
    contribution = _contribution_table(baseline)
    baseline_difference = float(
        baseline["difference_hydro_minus_solid_kg_co2e"]
    )
    direction_reversal_detected = bool(
        named["direction_reversal_scenario_count"]
        or sensitivity["direction_reversal_perturbation_count"]
        or uncertainty["direction_reversal_probability"] > 0
    )
    interpretation = {
        "schema_version": 1,
        "contract": f"{CONTRACT}_bounded_interpretation",
        "claim_level": "screening_single_category_evidence_bounded",
        "baseline_observation": (
            "Under the accepted screening parameters, the solid-state route has "
            "the lower modeled GWP100 result."
        ),
        "baseline_difference_hydro_minus_solid_kg_co2e": baseline_difference,
        "baseline_ratio_hydro_over_solid": baseline["ratio_hydro_over_solid"],
        "baseline_lower_impact_route": baseline["lower_impact_route"],
        "named_direction_reversal_scenario_ids": copy.deepcopy(
            named["direction_reversal_scenario_ids"]
        ),
        "oat_direction_reversal_perturbation_count": sensitivity[
            "direction_reversal_perturbation_count"
        ],
        "uncertainty_direction_reversal_probability": uncertainty[
            "direction_reversal_probability"
        ],
        "probability_solid_state_lower": uncertainty[
            "probability_solid_state_lower"
        ],
        "probability_hydrothermal_lower": uncertainty[
            "probability_hydrothermal_lower"
        ],
        "direction_reversal_detected": direction_reversal_detected,
        "decision_relevant_reading": (
            "The baseline direction is not robust to every accepted bound: the "
            "strong hydrothermal burden-split bound and selected provider-intensity "
            "bounds reverse the ordering, and the Monte Carlo distribution retains "
            "non-zero probability for either route to be lower."
        ),
        "limitations": [
            "This is a single-category GWP100 screening comparison, not a multi-category environmental assessment.",
            "Provider intensities are bounded foreground-compatible proxies, not complete industrial background inventories.",
            "Direct emissions, purge gas, unresolved reaction gases, and some background inventories remain missing rather than zero.",
            "The carbon-loss term is a bounded GWP proxy and not a fabricated elementary-flow inventory.",
            "The cycle-30 service sensitivity uses 147 and 135 mAh/g values with test temperature and full coating-condition alignment still unresolved.",
            "The two source route Processes retain material-balance review warnings.",
        ],
        "industrial_superiority_proven": False,
        "multi_category_superiority_proven": False,
        "publication_approval_granted": False,
        "strong_public_claim_allowed": False,
        "human_review_retained_for": [
            "scientific interpretation",
            "allocation choice",
            "provider proxy adequacy",
            "performance evidence alignment",
        ],
    }
    checks = {
        "common_adapter_and_method": compiled.get("method_id") == METHOD_ID,
        "parameter_count_exact": compiled.get("parameter_count") == 14,
        "all_parameters_consumed": compiled.get("consumed_parameter_count") == 14,
        "allocation_exactly_once": baseline.get("allocation_application_count") == 1,
        "baseline_contribution_complete": all(
            abs(
                float(row["total_kg_co2e"])
                - float(row["route_core_allocated_kg_co2e"])
                - float(row["shared_coating_kg_co2e"])
            )
            < 1e-12
            for row in baseline["routes"]
            if isinstance(row, dict)
        ),
        "named_scenarios_complete": named.get("scenario_count") == 9,
        "oat_complete": sensitivity.get("perturbation_count") == 28,
        "uncertainty_complete": uncertainty.get("trial_count") == 20000,
        "uncertainty_shared_draws": uncertainty.get(
            "shared_parameter_draw_count_per_trial"
        )
        == 14,
        "trial_rows_not_persisted": uncertainty.get("trial_rows_persisted") is False,
        "missing_not_zero": compiled.get("missing_values_interpreted_as_zero_count")
        == 0,
        "bounded_claims": interpretation["industrial_superiority_proven"] is False,
    }
    validation = {
        "schema_version": 1,
        "contract": f"{CONTRACT}_validation",
        "status": "accepted" if all(checks.values()) else "rejected",
        "check_count": len(checks),
        "passed_check_count": sum(bool(value) for value in checks.values()),
        "checks": checks,
    }
    if validation["status"] != "accepted":
        raise RuntimeError("comparative LCIA analysis validation failed")
    return {
        "schema_version": 1,
        "contract": f"{CONTRACT}_analysis",
        "status": "minimum_lifepo4_comparative_lcia_analysis_complete",
        "input_version": compiled.get("input_version"),
        "equations": _equations(compiled),
        "baseline": baseline,
        "named_scenarios": named,
        "sensitivity": sensitivity,
        "uncertainty": uncertainty,
        "contribution_table": contribution,
        "interpretation": interpretation,
        "validation": validation,
        "side_effects": {
            "network_read_executed_count": 0,
            "remote_write_executed_count": 0,
            "publication_executed_count": 0,
            "background_inventory_expansion_executed_count": 0,
        },
        "next_internal_route": NEXT_ROUTE,
    }


def _read_json(path: Path, label: str) -> JsonObject:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} is invalid: {path}") from error
    return _object(value, label)


def _read_jsonl(path: Path, label: str) -> List[JsonObject]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise RuntimeError(f"{label} is unreadable: {path}") from error
    rows = []
    for line in lines:
        if not line.strip():
            continue
        try:
            rows.append(_object(json.loads(line), label))
        except json.JSONDecodeError as error:
            raise RuntimeError(f"{label} contains invalid JSONL") from error
    return rows


def _tree_binding(root: Path) -> JsonObject:
    rows = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        content = path.read_bytes()
        rows.append(
            {
                "path": str(path.relative_to(root)),
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
        )
    return {
        "file_count": len(rows),
        "total_bytes": sum(int(row["bytes"]) for row in rows),
        "tree_sha256": hashlib.sha256(_json_bytes(rows)).hexdigest(),
    }


def _verify_source_model_package(
    package_root: Path,
    acceptance_path: Path,
    *,
    expected_report_sha256: str,
    expected_manifest_sha256: str,
    expected_acceptance_sha256: str,
) -> JsonObject:
    root = package_root.resolve()
    report_path = root / "outputs/minimum-lifepo4-comparison-model.json"
    manifest_path = root / "outputs/package-manifest.json"
    for path, expected, label in (
        (report_path, expected_report_sha256, "model report"),
        (manifest_path, expected_manifest_sha256, "model manifest"),
        (acceptance_path, expected_acceptance_sha256, "model acceptance"),
    ):
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"accepted {label} binding drifted")
    report = _read_json(report_path, "model report")
    manifest = _read_json(manifest_path, "model manifest")
    acceptance = _read_json(acceptance_path, "model acceptance")
    manifest_rows = _list(manifest.get("artifacts"), "model manifest artifacts")
    if (
        report.get("contract") != f"{MODEL_CONTRACT}_native_execution"
        or report.get("status") != "minimum_lifepo4_comparison_model_materialized"
        or report.get("model_input_version") != "a0a4b121b199aacb"
        or report.get("input_version") != "4947bde96713141c"
        or report.get("general_corpus_release_frozen") is not True
        or report.get("unmaterialized_release_record_count") != 1453
        or report.get("resulting_process_count") != 2
        or report.get("comparison_executed_count") != 0
        or report.get("lcia_executed_count") != 0
        or report.get("package_manifest", {}).get("sha256")
        != expected_manifest_sha256
        or manifest.get("artifact_count") != len(manifest_rows)
        or acceptance.get("contract")
        != f"{MODEL_CONTRACT}_read_only_independent_acceptance"
        or acceptance.get("status") != "accepted"
        or acceptance.get("source_package_unchanged") is not True
        or acceptance.get("reopened_manifest_artifact_count") != len(manifest_rows)
        or acceptance.get("resulting_process_count") != 2
        or acceptance.get("functional_unit_amount") != 1
        or acceptance.get("unmaterialized_release_record_count") != 1453
    ):
        raise ValueError("accepted model or independent acceptance contract drifted")
    for value in manifest_rows:
        binding = _object(value, "model manifest artifact")
        relative = Path(_text(binding.get("path"), "model manifest path"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("model manifest path escaped package")
        path = root / relative
        if not path.is_file():
            raise ValueError(f"model manifest member is missing: {relative}")
        content = path.read_bytes()
        if (
            hashlib.sha256(content).hexdigest() != binding.get("sha256")
            or len(content) != binding.get("bytes")
        ):
            raise ValueError(f"model manifest member drifted: {relative}")
    source_tree = _tree_binding(root)
    if acceptance.get("source_package") != source_tree:
        raise ValueError("accepted model package tree drifted")
    resulting_rows = _read_jsonl(
        root / "outputs/resulting-processes.jsonl", "resulting Processes"
    )
    native = _read_json(
        root / "inputs/compiled-native-inputs.json", "compiled native inputs"
    )
    expected_result_ids = {
        _text(row.get("resulting_process_id"), "resulting Process ID")
        for row in (
            _object(value, "projection request")
            for value in _list(native.get("projection_requests"), "projection requests")
        )
    }
    result_ids = {_process_id(row, "resulting Process") for row in resulting_rows}
    product_flow_ids = set()
    for row in resulting_rows:
        outputs = [
            exchange
            for exchange in _exchanges(row, "resulting Process")
            if exchange.get("exchangeDirection") == "Output"
            and exchange.get("quantitativeReference") is True
        ]
        if len(outputs) != 1 or _number(outputs[0].get("meanAmount"), "reference amount") != 1:
            raise ValueError("resulting Process functional unit drifted")
        product_flow_ids.add(
            _text(
                _object(
                    outputs[0].get("referenceToFlowDataSet"),
                    "resulting Process reference Flow",
                ).get("@refObjectId"),
                "resulting Process reference Flow ID",
            )
        )
    if (
        len(resulting_rows) != 2
        or result_ids != expected_result_ids
        or len(product_flow_ids) != 1
    ):
        raise ValueError("accepted resulting Process membership drifted")
    return {
        "report": report,
        "manifest": manifest,
        "acceptance": acceptance,
        "compiled_model": _read_json(
            root / "inputs/compiled-model.json", "compiled model"
        ),
        "compiled_native_inputs": native,
        "source_tree": source_tree,
        "resulting_process_sha256": hashlib.sha256(
            (root / "outputs/resulting-processes.jsonl").read_bytes()
        ).hexdigest(),
        "common_reference_product_flow_id": next(iter(product_flow_ids)),
    }


def _write_json(path: Path, value: object) -> JsonObject:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _json_bytes(value)
    path.write_bytes(content)
    if json.loads(path.read_text(encoding="utf-8")) != value:
        raise RuntimeError(f"LCIA artifact readback drifted: {path}")
    return {
        "path": str(path),
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


def execute_minimum_comparison_lcia_package(
    *,
    model_package_root: Path,
    model_acceptance_path: Path,
    comparison_scope_path: Path,
    out_dir: Path,
    expected_model_report_sha256: str,
    expected_model_manifest_sha256: str,
    expected_model_acceptance_sha256: str,
    expected_comparison_scope_sha256: str,
) -> JsonObject:
    """Reopen exact accepted authorities, execute analysis, and write a package."""

    source_tree_before = _tree_binding(model_package_root.resolve())
    authorities = _verify_source_model_package(
        model_package_root,
        model_acceptance_path,
        expected_report_sha256=expected_model_report_sha256,
        expected_manifest_sha256=expected_model_manifest_sha256,
        expected_acceptance_sha256=expected_model_acceptance_sha256,
    )
    scope_content = comparison_scope_path.read_bytes()
    if hashlib.sha256(scope_content).hexdigest() != expected_comparison_scope_sha256:
        raise ValueError("accepted comparison scope binding drifted")
    scope = _object(json.loads(scope_content), "comparison scope")
    compiled = compile_minimum_comparison_lcia(
        authorities["compiled_model"],
        authorities["compiled_native_inputs"],
        scope,
    )
    analysis = build_minimum_comparison_lcia_analysis(compiled)
    source_bindings = {
        "schema_version": 1,
        "contract": f"{CONTRACT}_source_bindings",
        "model_activity_input_version": "f43c6af305d8895e",
        "model_compiler_input_version": "a0a4b121b199aacb",
        "native_input_version": "4947bde96713141c",
        "model_report_sha256": expected_model_report_sha256,
        "model_manifest_sha256": expected_model_manifest_sha256,
        "model_acceptance_sha256": expected_model_acceptance_sha256,
        "model_source_tree": authorities["source_tree"],
        "comparison_scope_sha256": expected_comparison_scope_sha256,
        "resulting_process_sha256": authorities["resulting_process_sha256"],
        "common_reference_product_flow_id": authorities[
            "common_reference_product_flow_id"
        ],
        "general_corpus_release_frozen": True,
        "unmaterialized_release_record_count": 1453,
    }
    package_identity = {
        "contract": CONTRACT,
        "source_bindings": source_bindings,
        "calculation_input_version": compiled["input_version"],
        "uncertainty_trial_count": analysis["uncertainty"]["trial_count"],
        "uncertainty_seed": analysis["uncertainty"]["seed"],
        "next_internal_route": NEXT_ROUTE,
    }
    input_version = _hash(package_identity)[:16]
    root = out_dir.resolve()
    file_values = {
        "inputs/source-bindings.json": source_bindings,
        "inputs/compiled-calculation-input.json": compiled,
        "outputs/equations.json": analysis["equations"],
        "outputs/baseline.json": analysis["baseline"],
        "outputs/named-scenarios.json": analysis["named_scenarios"],
        "outputs/oat-sensitivity.json": analysis["sensitivity"],
        "outputs/uncertainty.json": analysis["uncertainty"],
        "outputs/contribution-table.json": analysis["contribution_table"],
        "outputs/bounded-interpretation.json": analysis["interpretation"],
        "outputs/validation.json": analysis["validation"],
    }
    bindings = []
    for relative, value in file_values.items():
        artifact = _write_json(root / relative, value)
        bindings.append(
            {
                "path": relative,
                "sha256": artifact["sha256"],
                "bytes": artifact["bytes"],
            }
        )
    manifest = {
        "schema_version": 1,
        "contract": f"{CONTRACT}_package_manifest",
        "input_version": input_version,
        "artifact_count": len(bindings),
        "artifacts": bindings,
    }
    manifest_artifact = _write_json(root / "outputs/package-manifest.json", manifest)
    baseline = analysis["baseline"]
    uncertainty = analysis["uncertainty"]
    report = {
        "schema_version": 1,
        "contract": f"{CONTRACT}_execution",
        "status": "minimum_lifepo4_comparative_lcia_executed",
        "input_version": input_version,
        "calculation_input_version": compiled["input_version"],
        "scope_id": compiled["scope_id"],
        "revision": compiled["revision"],
        "method_id": METHOD_ID,
        "functional_unit": "1 kg carbon-coated LiFePO4 at factory gate",
        "baseline_lower_impact_route": baseline["lower_impact_route"],
        "baseline_difference_hydro_minus_solid_kg_co2e": baseline[
            "difference_hydro_minus_solid_kg_co2e"
        ],
        "baseline_ratio_hydro_over_solid": baseline["ratio_hydro_over_solid"],
        "named_scenario_count": analysis["named_scenarios"]["scenario_count"],
        "oat_perturbation_count": analysis["sensitivity"]["perturbation_count"],
        "uncertainty_trial_count": uncertainty["trial_count"],
        "uncertainty_seed": uncertainty["seed"],
        "uncertainty_trial_digest_sha256": uncertainty["trial_digest_sha256"],
        "probability_solid_state_lower": uncertainty[
            "probability_solid_state_lower"
        ],
        "probability_hydrothermal_lower": uncertainty[
            "probability_hydrothermal_lower"
        ],
        "direction_reversal_probability": uncertainty[
            "direction_reversal_probability"
        ],
        "parameter_count": compiled["parameter_count"],
        "direction_reversal_detected": analysis["interpretation"][
            "direction_reversal_detected"
        ],
        "claim_level": analysis["interpretation"]["claim_level"],
        "validation_status": analysis["validation"]["status"],
        "general_corpus_release_frozen": True,
        "unmaterialized_release_record_count": 1453,
        "model_source_package_unchanged": _tree_binding(
            model_package_root.resolve()
        )
        == source_tree_before,
        "network_read_executed_count": 0,
        "remote_write_executed_count": 0,
        "publication_executed_count": 0,
        "background_inventory_expansion_executed_count": 0,
        "package_manifest": {
            "path": "outputs/package-manifest.json",
            "sha256": manifest_artifact["sha256"],
            "bytes": manifest_artifact["bytes"],
            "artifact_count": len(bindings),
        },
        "next_internal_route": NEXT_ROUTE,
        "next_action": (
            "Assemble the accepted numerical evidence, bounded interpretation, "
            "replay, and open risks into the final comparative package."
        ),
    }
    if report["model_source_package_unchanged"] is not True:
        raise RuntimeError("LCIA execution changed the accepted model package")
    _write_json(root / "outputs/minimum-lifepo4-comparative-lcia.json", report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-package-root", type=Path, required=True)
    parser.add_argument("--model-acceptance", type=Path, required=True)
    parser.add_argument("--comparison-scope", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--model-report-sha256", required=True)
    parser.add_argument("--model-manifest-sha256", required=True)
    parser.add_argument("--model-acceptance-sha256", required=True)
    parser.add_argument("--comparison-scope-sha256", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = execute_minimum_comparison_lcia_package(
        model_package_root=args.model_package_root,
        model_acceptance_path=args.model_acceptance,
        comparison_scope_path=args.comparison_scope,
        out_dir=args.out_dir,
        expected_model_report_sha256=args.model_report_sha256,
        expected_model_manifest_sha256=args.model_manifest_sha256,
        expected_model_acceptance_sha256=args.model_acceptance_sha256,
        expected_comparison_scope_sha256=args.comparison_scope_sha256,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
