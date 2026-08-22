"""Compile source-bound route inventory evidence into a screening exchange plan."""

from __future__ import annotations

import copy
import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional


JsonObject = Dict[str, Any]
MMBTU_TO_MJ = Decimal("1055.05585262")
SHORT_TON_TO_KG = Decimal("907.18474")
SUCROSE_MOLAR_MASS = Decimal("342.297")
SUCROSE_CARBON_MASS = Decimal("144.132")


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")
    return value.strip()


def _positive_decimal(value: object, path: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{path} must be a positive number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{path} must be a positive number") from error
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{path} must be a positive number")
    return result


def _round(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.000000001")))


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _provenance(
    evidence: JsonObject, source_ref: Optional[JsonObject]
) -> JsonObject:
    canonical = _canonical_bytes(evidence)
    if source_ref is None:
        source = {
            "source_type": "in_memory",
            "path": None,
            "sha256": hashlib.sha256(canonical).hexdigest(),
            "bytes": len(canonical),
        }
    else:
        source = copy.deepcopy(source_ref)
        if source.get("source_type") != "local_file":
            raise ValueError("inventory_source_ref.source_type must be local_file")
        path = Path(_text(source.get("path"), "inventory_source_ref.path")).resolve()
        content = path.read_bytes()
        if json.loads(content) != evidence:
            raise ValueError(
                "inventory_source_ref.path content does not match inventory evidence"
            )
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if source.get("sha256") != actual_sha256:
            raise ValueError("inventory_source_ref.sha256 does not match its file")
        if source.get("bytes") != len(content):
            raise ValueError("inventory_source_ref.bytes does not match its file")
        source["path"] = str(path)
    return {
        "source": source,
        "original_input": copy.deepcopy(evidence),
        "canonical_sha256": hashlib.sha256(canonical).hexdigest(),
        "validation": {
            "status": "passed",
            "validator": "lca_research_kernel.inventory_evidence/v1",
        },
    }


def _source_ids(evidence: JsonObject) -> set[str]:
    sources = evidence.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("inventory_evidence.sources must be a non-empty list")
    identifiers: List[str] = []
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise ValueError(f"inventory_evidence.sources[{index}] must be an object")
        identifiers.append(
            _text(source.get("source_id"), f"sources[{index}].source_id")
        )
        _text(source.get("title"), f"sources[{index}].title")
        _text(source.get("source_type"), f"sources[{index}].source_type")
        _text(source.get("landing_url"), f"sources[{index}].landing_url")
        publication_year = _positive_decimal(
            source.get("publication_year"), f"sources[{index}].publication_year"
        )
        if publication_year != publication_year.to_integral_value():
            raise ValueError(f"sources[{index}].publication_year must be an integer")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("inventory_evidence source_id values must be unique")
    return set(identifiers)


def _decision_source_ids(evidence: JsonObject) -> set[str]:
    """Collect field-level source references, excluding source registry identities."""

    identifiers: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "source_id" or key.endswith("_source_id"):
                    identifiers.add(_text(child, f"decision source {key}"))
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(evidence.get("routes"))
    visit(evidence.get("carbon_coating"))
    return identifiers


def _reaction_exchange_candidates(
    route: JsonObject, source_ids: set[str]
) -> tuple[List[JsonObject], Decimal, Decimal, Dict[str, Decimal], List[str]]:
    reaction = route.get("reaction")
    if not isinstance(reaction, dict):
        raise ValueError(f"route {route.get('route_id')} reaction must be an object")
    source_id = _text(reaction.get("source_id"), "reaction.source_id")
    if source_id not in source_ids:
        raise ValueError("reaction.source_id is not registered")
    product = reaction.get("reference_product")
    reactants = reaction.get("reactants")
    if not isinstance(product, dict) or not isinstance(reactants, list) or not reactants:
        raise ValueError("reaction must define a reference product and reactants")
    product_coefficient = _positive_decimal(
        product.get("coefficient"), "reference_product.coefficient"
    )
    if product.get("formula") != "LiFePO4":
        raise ValueError("reference_product.formula must be LiFePO4")
    product_molar_mass = _positive_decimal(
        product.get("molar_mass_g_mol"), "reference_product.molar_mass_g_mol"
    )
    denominator = product_coefficient * product_molar_mass
    candidates: List[JsonObject] = []
    exact_reactant_amounts: Dict[str, Decimal] = {}
    reactant_total = Decimal(0)
    for index, reactant in enumerate(reactants):
        if not isinstance(reactant, dict):
            raise ValueError(f"reaction.reactants[{index}] must be an object")
        coefficient = _positive_decimal(
            reactant.get("coefficient"), f"reactants[{index}].coefficient"
        )
        molar_mass = _positive_decimal(
            reactant.get("molar_mass_g_mol"),
            f"reactants[{index}].molar_mass_g_mol",
        )
        amount = coefficient * molar_mass / denominator
        reactant_total += amount
        flow_key = _text(
            reactant.get("flow_key"), f"reactants[{index}].flow_key"
        )
        if flow_key in exact_reactant_amounts:
            raise ValueError(f"duplicate reactant flow_key: {flow_key}")
        exact_reactant_amounts[flow_key] = amount
        unit = _text(reactant.get("unit"), f"reactants[{index}].unit")
        if unit != "kg":
            raise ValueError(f"reactants[{index}].unit must be kg")
        candidates.append(
            {
                "flow_key": flow_key,
                "name": _text(reactant.get("name"), f"reactants[{index}].name"),
                "direction": "Input",
                "exchange_type": "technosphere",
                "amount": _round(amount),
                "unit": unit,
                "source_ids": [source_id],
                "derivation": "stoichiometric_mass_per_1kg_LiFePO4",
                "evidence_status": "source_bound_engineering_calculation",
            }
        )
    coproduct_total = Decimal(0)
    coproducts = reaction.get("coproducts", [])
    if not isinstance(coproducts, list):
        raise ValueError("reaction.coproducts must be a list")
    for index, coproduct in enumerate(coproducts):
        if not isinstance(coproduct, dict):
            raise ValueError(f"reaction.coproducts[{index}] must be an object")
        amount = (
            _positive_decimal(
                coproduct.get("coefficient"), f"coproducts[{index}].coefficient"
            )
            * _positive_decimal(
                coproduct.get("molar_mass_g_mol"),
                f"coproducts[{index}].molar_mass_g_mol",
            )
            / denominator
        )
        coproduct_total += amount
        unit = _text(coproduct.get("unit"), f"coproducts[{index}].unit")
        if unit != "kg":
            raise ValueError(f"coproducts[{index}].unit must be kg")
        candidates.append(
            {
                "flow_key": _text(
                    coproduct.get("flow_key"), f"coproducts[{index}].flow_key"
                ),
                "name": _text(coproduct.get("name"), f"coproducts[{index}].name"),
                "direction": "Output",
                "exchange_type": "co_product",
                "amount": _round(amount),
                "unit": unit,
                "source_ids": [source_id],
                "derivation": "stoichiometric_mass_per_1kg_LiFePO4",
                "evidence_status": "source_bound_engineering_calculation",
            }
        )
    unquantified_outputs = reaction.get("unquantified_outputs", [])
    if not isinstance(unquantified_outputs, list) or any(
        not isinstance(output, str) or not output.strip()
        for output in unquantified_outputs
    ):
        raise ValueError("reaction.unquantified_outputs must be a list of strings")
    return (
        candidates,
        reactant_total,
        coproduct_total,
        exact_reactant_amounts,
        [output.strip() for output in unquantified_outputs],
    )


def _compile_route(route: JsonObject, source_ids: set[str]) -> JsonObject:
    route_id = _text(route.get("route_id"), "route.route_id")
    alignment = _text(route.get("evidence_alignment"), "route.evidence_alignment")
    (
        candidates,
        reactant_total,
        coproduct_total,
        exact_reactant_amounts,
        unquantified_outputs,
    ) = _reaction_exchange_candidates(route, source_ids)
    expected_model_basis = {
        "solid-state": "single_product_per_1kg_lfp",
        "hydrothermal": "unallocated_joint_process_per_1kg_lfp",
    }[route_id]
    model_basis = _text(
        route.get("inventory_model_basis"), "route.inventory_model_basis"
    )
    if model_basis != expected_model_basis:
        raise ValueError(
            f"route {route_id} inventory_model_basis must be {expected_model_basis}"
        )
    energy = route.get("purchased_energy")
    if not isinstance(energy, dict):
        raise ValueError(f"route {route_id} purchased_energy must be an object")
    energy_source_id = _text(energy.get("source_id"), "purchased_energy.source_id")
    if energy_source_id not in source_ids:
        raise ValueError("purchased_energy.source_id is not registered")
    if energy.get("unit") != "mmBtu_per_reported_ton_LiFePO4":
        raise ValueError(
            "purchased_energy.unit must be mmBtu_per_reported_ton_LiFePO4"
        )
    if (
        energy.get("reported_ton_interpretation")
        != "assumed_us_short_ton_unconfirmed"
    ):
        raise ValueError(
            "purchased_energy.reported_ton_interpretation must retain the ton-unit assumption"
        )
    expected_allocation_basis = (
        "mass_allocated_to_lfp"
        if route_id == "hydrothermal"
        else "single_product_not_allocated"
    )
    if energy.get("allocation_basis") != expected_allocation_basis:
        raise ValueError(
            f"route {route_id} purchased_energy.allocation_basis must be "
            f"{expected_allocation_basis}"
        )
    reported_energy = _positive_decimal(
        energy.get("value"), "purchased_energy.value"
    )
    reported_short_ton_mj = reported_energy * MMBTU_TO_MJ / SHORT_TON_TO_KG
    reported_metric_tonne_mj = reported_energy * MMBTU_TO_MJ / Decimal(1000)
    allocation_fraction = (
        Decimal(1) / (Decimal(1) + coproduct_total)
        if route_id == "hydrothermal"
        else Decimal(1)
    )
    energy_mj = reported_short_ton_mj / allocation_fraction
    metric_tonne_energy_mj = reported_metric_tonne_mj / allocation_fraction
    carrier = _text(energy.get("carrier"), "purchased_energy.carrier")
    expected_carrier = {
        "solid-state": "electricity",
        "hydrothermal": "natural_gas_heat_proxy",
    }[route_id]
    if carrier != expected_carrier:
        raise ValueError(
            f"route {route_id} purchased_energy.carrier must be {expected_carrier}"
        )
    energy_flow_key = (
        "electricity" if carrier == "electricity" else "natural-gas-heat-proxy"
    )
    candidates.append(
        {
            "flow_key": energy_flow_key,
            "name": (
                "Electricity, process"
                if carrier == "electricity"
                else "Natural gas heat, process proxy"
            ),
            "direction": "Input",
            "exchange_type": "energy",
            "amount": _round(energy_mj),
            "unit": "MJ",
            "source_ids": [energy_source_id],
            "derivation": (
                "reported_ton_assumed_short_ton_to_MJ_per_kg"
                + (
                    "_then_reverse_mass_allocation"
                    if route_id == "hydrothermal"
                    else ""
                )
            ),
            "evidence_status": "source_bound_ton_and_route_proxy",
        }
    )
    allocation = None
    solution_proxy = None
    if route_id == "hydrothermal":
        solution = route.get("solution_basis")
        if not isinstance(solution, dict):
            raise ValueError("hydrothermal solution_basis must be an object")
        solution_source_id = _text(solution.get("source_id"), "solution_basis.source_id")
        if solution_source_id not in source_ids:
            raise ValueError("solution_basis.source_id is not registered")
        try:
            iron_sulfate_amount = exact_reactant_amounts["iron-sulfate"]
        except KeyError as error:
            raise ValueError(
                "hydrothermal reaction must include iron-sulfate"
            ) from error
        concentration = _positive_decimal(
            solution.get("iron_sulfate_concentration_g_l"),
            "solution_basis.iron_sulfate_concentration_g_l",
        )
        density = _positive_decimal(
            solution.get("solution_density_proxy_kg_l"),
            "solution_basis.solution_density_proxy_kg_l",
        )
        solution_mass_proxy = (
            iron_sulfate_amount * Decimal(1000) / concentration * density
        )
        water_mass_proxy = solution_mass_proxy - reactant_total
        if water_mass_proxy <= 0:
            raise ValueError("hydrothermal water proxy must be positive")
        candidates.append(
            {
                "flow_key": "process-water-proxy",
                "name": "Process water proxy",
                "direction": "Input",
                "exchange_type": "technosphere",
                "amount": _round(water_mass_proxy),
                "unit": "kg",
                "source_ids": [solution_source_id],
                "derivation": (
                    "FeSO4_concentration_solution_mass_proxy_minus_all_reactant_solutes"
                ),
                "evidence_status": "source_bound_density_proxy",
            }
        )
        solution_proxy = {
            "solution_mass_proxy_kg": _round(solution_mass_proxy),
            "dissolved_reactant_mass_kg": _round(reactant_total),
            "water_mass_proxy_kg": _round(water_mass_proxy),
            "source_ids": [solution_source_id],
            "density_assumption_kg_l": float(density),
        }
        reaction_allocation = route["reaction"].get("allocation")
        if not isinstance(reaction_allocation, dict):
            raise ValueError("hydrothermal reaction allocation must be an object")
        allocation_method = _text(
            reaction_allocation.get("method"), "allocation.method"
        )
        if allocation_method != "mass":
            raise ValueError("hydrothermal allocation.method must be mass")
        allocation = {
            "method": allocation_method,
            "lfp_mass_fraction": _round(allocation_fraction),
            "source_ids": [
                _text(reaction_allocation.get("source_id"), "allocation.source_id")
            ],
            "status": "unallocated_joint_process_with_mass_allocation_metadata",
        }
        if allocation["source_ids"][0] not in source_ids:
            raise ValueError("allocation.source_id is not registered")
    conditions = route.get("conditions")
    if not isinstance(conditions, dict):
        raise ValueError(f"route {route_id} conditions must be an object")
    condition_source_ids = [
        _text(value, f"conditions.{key}")
        for key, value in conditions.items()
        if key == "source_id" or key.endswith("_source_id")
    ]
    if not condition_source_ids or any(
        source_id not in source_ids for source_id in condition_source_ids
    ):
        raise ValueError("all condition source IDs must be registered")
    flow_keys = [candidate["flow_key"] for candidate in candidates]
    if len(set(flow_keys)) != len(flow_keys):
        raise ValueError(f"route {route_id} exchange flow_key values must be unique")
    if route_id == "hydrothermal":
        lithium_sulfate = [
            candidate
            for candidate in candidates
            if candidate["flow_key"] == "lithium-sulfate"
            and candidate["direction"] == "Output"
            and candidate["exchange_type"] == "co_product"
        ]
        if len(lithium_sulfate) != 1:
            raise ValueError(
                "hydrothermal screening reaction must yield one lithium-sulfate coproduct"
            )
    allocation_basis = (
        "unallocated_joint_process_per_1kg_lfp_physical_reference"
        if route_id == "hydrothermal"
        else "single_product_per_1kg_lfp"
    )
    for candidate in candidates:
        candidate["allocation_basis"] = allocation_basis
        candidate["uncertainty"] = {
            "status": "not_characterized_source_gap",
            "distribution": None,
        }
    return {
        "route_id": route_id,
        "evidence_alignment": alignment,
        "alignment_reason": _text(
            route.get("alignment_reason"), "route.alignment_reason"
        ),
        "basis": "1 kg uncoated LiFePO4 route core",
        "inventory_model_basis": model_basis,
        "exchange_candidates": candidates,
        "reactant_mass_total_kg": _round(reactant_total),
        "coproduct_mass_total_kg": _round(coproduct_total),
        "energy": {
            "amount": _round(energy_mj),
            "unit": "MJ",
            "carrier": carrier,
            "source_ids": [energy_source_id],
            "model_allocation_basis": allocation_basis,
            "reported_value": float(reported_energy),
            "reported_unit": "mmBtu_per_reported_ton_LiFePO4",
            "reported_allocation_basis": expected_allocation_basis,
            "reported_short_ton_assumption_mj_per_kg_lfp": _round(
                reported_short_ton_mj
            ),
            "metric_tonne_sensitivity_model_amount_mj": _round(
                metric_tonne_energy_mj
            ),
            "conversion_assumptions": {
                "reported_ton_interpretation": (
                    "assumed_us_short_ton_unconfirmed"
                ),
                "mmBtu_to_MJ": float(MMBTU_TO_MJ),
                "short_ton_to_kg": float(SHORT_TON_TO_KG),
                "metric_tonne_to_kg": 1000,
            },
        },
        "allocation": allocation,
        "solution_proxy": solution_proxy,
        "unquantified_output_gaps": [
            {
                "description": output,
                "status": "not_quantified_no_cutoff_decision",
                "source_ids": [
                    _text(route["reaction"].get("source_id"), "reaction.source_id")
                ],
            }
            for output in unquantified_outputs
        ],
        "patch_status": "screening_only_not_yet_applied",
    }


def compile_inventory_evidence(
    evidence: JsonObject, source_ref: Optional[JsonObject] = None
) -> JsonObject:
    """Validate evidence and derive a transparent, non-publication screening plan."""
    if evidence.get("schema_version") != 1:
        raise ValueError("inventory_evidence.schema_version must be 1")
    scope_id = _text(evidence.get("scope_id"), "inventory_evidence.scope_id")
    revision = _text(evidence.get("revision"), "inventory_evidence.revision")
    basis = evidence.get("basis")
    if not isinstance(basis, dict) or basis.get("amount") != 1 or basis.get("unit") != "kg":
        raise ValueError("inventory_evidence basis must be 1 kg")
    source_ids = _source_ids(evidence)
    evidence_search = evidence.get("evidence_search")
    if not isinstance(evidence_search, dict):
        raise ValueError("inventory_evidence.evidence_search must be an object")
    required_source_ids = evidence_search.get("required_source_ids")
    if (
        not isinstance(required_source_ids, list)
        or not required_source_ids
        or any(
            not isinstance(source_id, str) or not source_id.strip()
            for source_id in required_source_ids
        )
        or len(set(required_source_ids)) != len(required_source_ids)
        or any(source_id not in source_ids for source_id in required_source_ids)
    ):
        raise ValueError(
            "evidence_search.required_source_ids must be unique registered source IDs"
        )
    missing_decision_sources = _decision_source_ids(evidence) - set(
        required_source_ids
    )
    if missing_decision_sources:
        raise ValueError(
            "evidence_search.required_source_ids omits decision-bearing sources: "
            + ", ".join(sorted(missing_decision_sources))
        )
    sources_by_id = {
        source["source_id"]: source for source in evidence["sources"]
    }
    required_search_sources = [
        {
            "source_id": source_id,
            "title": _text(
                sources_by_id[source_id].get("title"),
                f"source {source_id}.title",
            ),
            "url": _text(
                sources_by_id[source_id].get("landing_url"),
                f"source {source_id}.landing_url",
            ),
        }
        for source_id in required_source_ids
    ]
    routes = evidence.get("routes")
    if not isinstance(routes, list) or len(routes) != 2:
        raise ValueError("inventory_evidence.routes must contain exactly two routes")
    route_ids = [
        _text(route.get("route_id"), f"routes[{index}].route_id")
        if isinstance(route, dict)
        else ""
        for index, route in enumerate(routes)
    ]
    if set(route_ids) != {"solid-state", "hydrothermal"} or len(set(route_ids)) != 2:
        raise ValueError("inventory evidence must cover both route IDs exactly once")
    compiled_routes = [_compile_route(route, source_ids) for route in routes]

    coating = evidence.get("carbon_coating")
    if not isinstance(coating, dict):
        raise ValueError("inventory_evidence.carbon_coating must be an object")
    coating_source_id = _text(coating.get("source_id"), "carbon_coating.source_id")
    if coating_source_id not in source_ids:
        raise ValueError("carbon_coating.source_id is not registered")
    carbon_fraction = _positive_decimal(
        coating.get("target_retained_carbon_wt_percent"),
        "carbon_coating.target_retained_carbon_wt_percent",
    ) / Decimal(100)
    if carbon_fraction >= 1:
        raise ValueError(
            "carbon_coating.target_retained_carbon_wt_percent must be below 100"
        )
    sucrose_if_lfp_basis = carbon_fraction / (
        SUCROSE_CARBON_MASS / SUCROSE_MOLAR_MASS
    )
    sucrose_if_composite_basis = (carbon_fraction / (Decimal(1) - carbon_fraction)) / (
        SUCROSE_CARBON_MASS / SUCROSE_MOLAR_MASS
    )
    carbon_basis_status = _text(
        coating.get("carbon_content_basis_status"),
        "carbon_coating.carbon_content_basis_status",
    )
    if carbon_basis_status != "ambiguous_in_public_record":
        raise ValueError(
            "carbon_coating.carbon_content_basis_status must retain the public-record ambiguity"
        )

    benchmarks = evidence.get("engineering_benchmarks")
    if not isinstance(benchmarks, list) or not benchmarks:
        raise ValueError("inventory_evidence.engineering_benchmarks must be non-empty")
    normalized_benchmarks = []
    for index, benchmark in enumerate(benchmarks):
        if not isinstance(benchmark, dict):
            raise ValueError(f"engineering_benchmarks[{index}] must be an object")
        stars = _positive_decimal(
            benchmark.get("stars_at_capture"),
            f"engineering_benchmarks[{index}].stars_at_capture",
        )
        if stars != stars.to_integral_value():
            raise ValueError(
                f"engineering_benchmarks[{index}].stars_at_capture must be an integer"
            )
        normalized_benchmarks.append(
            {
                "repository": _text(
                    benchmark.get("repository"), f"engineering_benchmarks[{index}].repository"
                ),
                "url": _text(
                    benchmark.get("url"), f"engineering_benchmarks[{index}].url"
                ),
                "stars_at_capture": int(stars),
                "captured_at": _text(
                    benchmark.get("captured_at"),
                    f"engineering_benchmarks[{index}].captured_at",
                ),
                "module_migration": _text(
                    benchmark.get("module_migration"),
                    f"engineering_benchmarks[{index}].module_migration",
                ),
            }
        )
    normalized_benchmarks.sort(
        key=lambda row: (-row["stars_at_capture"], row["repository"])
    )
    limitations = evidence.get("limitations")
    if not isinstance(limitations, list) or not limitations:
        raise ValueError("inventory_evidence.limitations must be a non-empty list")
    normalized_limitations = [
        _text(value, f"limitations[{index}]")
        for index, value in enumerate(limitations)
    ]
    return {
        "schema_version": 1,
        "scope_id": scope_id,
        "revision": revision,
        "status": "screening_proxy_plan_ready",
        "scientific_comparison_ready": False,
        "basis": copy.deepcopy(basis),
        "routes": compiled_routes,
        "carbon_coating": {
            "status": "source_gap_retained",
            "source_ids": [coating_source_id],
            "target_retained_carbon_wt_percent": float(carbon_fraction * 100),
            "carbon_content_basis_status": carbon_basis_status,
            "minimum_sucrose_if_full_carbon_retention_kg": _round(
                sucrose_if_lfp_basis
            ),
            "sucrose_if_carbon_percent_of_lfp_basis_kg": _round(
                sucrose_if_lfp_basis
            ),
            "sucrose_if_carbon_percent_of_final_composite_basis_kg": _round(
                sucrose_if_composite_basis
            ),
            "minimum_sucrose_semantics": (
                "chemical lower-bound range under an unresolved carbon-content basis; "
                "do not apply as an exchange without source-bound basis and carbon yield"
            ),
            "exact_headline_sample_condition_status": _text(
                coating.get("exact_headline_sample_condition_status"),
                "carbon_coating.exact_headline_sample_condition_status",
            ),
            "nitrogen_and_reducing_gas_quantities_status": _text(
                coating.get("nitrogen_and_reducing_gas_quantities_status"),
                "carbon_coating.nitrogen_and_reducing_gas_quantities_status",
            ),
            "sucrose_to_product_conversion_status": _text(
                coating.get("sucrose_to_product_conversion_status"),
                "carbon_coating.sucrose_to_product_conversion_status",
            ),
        },
        "readiness": {
            "screening_exchange_patch": "ready_with_high_risk_proxies",
            "performance_aligned_inventory": "not_ready_source_gaps",
            "comparative_lcia": "prohibited",
        },
        "evidence_search": {
            "required_sources": required_search_sources,
        },
        "risk_register": [
            {
                "risk_id": f"inventory-route-proxy:{scope_id}:{revision}",
                "severity": "high",
                "summary": (
                    "Argonne route cores do not exactly match the performance experiment; "
                    "screening exchanges must remain a named proxy scenario."
                ),
            },
            {
                "risk_id": f"inventory-carbon-coating-gap:{scope_id}:{revision}",
                "severity": "high",
                "summary": (
                    "Exact carbon-content basis, sucrose yield, purge gas, coating energy, "
                    "emissions, and sample condition are not source-bound for the "
                    "147/135 mAh/g candidates."
                ),
            },
            {
                "risk_id": f"inventory-ton-unit-ambiguity:{scope_id}:{revision}",
                "severity": "medium",
                "summary": (
                    "The Argonne extracted sections report energy per ton without an "
                    "explicit tonne definition; short-ton modeling and metric-tonne "
                    "sensitivity are both retained."
                ),
            },
            {
                "risk_id": f"inventory-unquantified-output:{scope_id}:{revision}",
                "severity": "high",
                "summary": (
                    "The solid-state reaction retains unquantified gaseous outputs and "
                    "cannot be treated as inventory-complete before quantification or "
                    "an explicit cutoff decision."
                ),
            },
        ],
        "engineering_benchmarks": normalized_benchmarks,
        "limitations": normalized_limitations,
        "provenance": _provenance(evidence, source_ref),
    }


def inventory_evidence_search_request(compiled: JsonObject) -> JsonObject:
    """Build the bounded native TianGong evidence-search request for one plan."""
    return {
        "question": (
            "Which primary or government sources support route-specific material, "
            "energy, water, coproduct, allocation, and carbon-coating fields for "
            "solid-state and continuous-supercritical LiFePO4 synthesis?"
        ),
        "field": {
            "dataset_type": "process",
            "field_path": "processDataSet.exchanges.exchange",
        },
        "profile": "deep",
        "preferred_domains": [
            "greet.anl.gov",
            "doi.org",
            "sciencedirect.com",
            "pubs.rsc.org",
        ],
        "required_terms": [
            "LiFePO4",
            "solid-state",
            "hydrothermal",
            "energy",
            "sucrose",
        ],
        "required_evidence": {
            "claims": [
                "route reaction or precursor identities",
                "route preparation energy or an explicit proxy model",
                "carbon-coating condition and retained-carbon evidence",
                "allocation and coproduct evidence where applicable",
            ],
            "require_complete_year": False,
        },
        "budget": {
            "max_queries": 12,
            "max_results_per_query": 10,
        },
        "scope_id": compiled["scope_id"],
        "revision": compiled["revision"],
    }
