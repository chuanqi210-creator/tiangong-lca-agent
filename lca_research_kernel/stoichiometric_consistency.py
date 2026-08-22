"""Compile conditional elemental and stoichiometric consistency checks."""

from __future__ import annotations

import argparse
import json
import re
from decimal import Decimal, InvalidOperation, getcontext
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

getcontext().prec = 34
JsonObject = Dict[str, object]
ACQUISITION_OPTIONS_CONTRACT = (
    "lca_research_kernel.responsibility_aware_acquisition_options/v1"
)
STOICHIOMETRIC_CONSISTENCY_CONTRACT = (
    "lca_research_kernel.elemental_stoichiometric_consistency/v1"
)

# Conventional single values used only for a transparent arithmetic screening basis.
ATOMIC_WEIGHTS = {
    "C": Decimal("12.011"),
    "Fe": Decimal("55.845"),
    "H": Decimal("1.008"),
    "Li": Decimal("6.94"),
    "N": Decimal("14.007"),
    "O": Decimal("15.999"),
    "P": Decimal("30.973761998"),
    "S": Decimal("32.06"),
}
CORE_ELEMENTS = {
    "solid-state": ("Li", "Fe", "P"),
    "hydrothermal": ("Li", "Fe", "P", "S"),
}
STRUCTURAL_PROBES = (
    {
        "probe_id": "lithium-hydroxide-monohydrate",
        "route_id": "hydrothermal",
        "overrides": {"Lithium hydroxide": "LiOH·H2O"},
        "interpretation": "discrete hydration probe; not an evidence-backed bound",
    },
    {
        "probe_id": "iron-sulfate-monohydrate",
        "route_id": "hydrothermal",
        "overrides": {"Iron sulfate": "FeSO4·H2O"},
        "interpretation": "discrete hydration probe; not an evidence-backed bound",
    },
    {
        "probe_id": "iron-sulfate-heptahydrate",
        "route_id": "hydrothermal",
        "overrides": {"Iron sulfate": "FeSO4·7H2O"},
        "interpretation": "discrete hydration probe; not an evidence-backed bound",
    },
    {
        "probe_id": "lithium-sulfate-monohydrate",
        "route_id": "hydrothermal",
        "overrides": {"Lithium sulfate": "Li2SO4·H2O"},
        "interpretation": "discrete hydration probe; not an evidence-backed bound",
    },
)


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _binding(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an artifact binding")
    path = _required_text(value.get("path"), f"{label}.path")
    sha256 = _required_text(value.get("sha256"), f"{label}.sha256")
    if len(sha256) != 64 or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError(f"{label}.sha256 must be a lowercase sha256")
    return {"path": path, "sha256": sha256}


def parse_formula(formula: str) -> Dict[str, int]:
    """Parse simple grouped and hydrate formulas into deterministic element counts."""

    text = _required_text(formula, "formula").replace("∙", "·")

    def parse_part(part: str) -> Dict[str, int]:
        leading = re.match(r"^(\d+)(?=[A-Z(])", part)
        factor = int(leading.group(1)) if leading else 1
        if leading:
            part = part[leading.end() :]

        def group(index: int, closing: bool = False) -> Tuple[Dict[str, int], int]:
            counts: Dict[str, int] = {}
            while index < len(part):
                char = part[index]
                if char == ")":
                    if not closing:
                        raise ValueError(f"unmatched ')' in formula {formula}")
                    return counts, index + 1
                if char == "(":
                    nested, index = group(index + 1, True)
                    match = re.match(r"\d+", part[index:])
                    multiple = int(match.group()) if match else 1
                    if match:
                        index += len(match.group())
                    for element, count in nested.items():
                        counts[element] = counts.get(element, 0) + count * multiple
                    continue
                match = re.match(r"[A-Z][a-z]?", part[index:])
                if not match:
                    raise ValueError(f"unsupported token in formula {formula}")
                element = match.group()
                index += len(element)
                number = re.match(r"\d+", part[index:])
                count = int(number.group()) if number else 1
                if number:
                    index += len(number.group())
                counts[element] = counts.get(element, 0) + count
            if closing:
                raise ValueError(f"unclosed '(' in formula {formula}")
            return counts, index

        counts, end = group(0)
        if end != len(part) or not counts:
            raise ValueError(f"invalid formula {formula}")
        return {element: count * factor for element, count in counts.items()}

    total: Dict[str, int] = {}
    for part in text.split("·"):
        counts = parse_part(part)
        for element, count in counts.items():
            total[element] = total.get(element, 0) + count
    return dict(sorted(total.items()))


def molar_mass(formula: str) -> Decimal:
    counts = parse_formula(formula)
    unknown = sorted(set(counts) - set(ATOMIC_WEIGHTS))
    if unknown:
        raise ValueError(f"atomic-weight basis missing elements: {unknown}")
    return sum(ATOMIC_WEIGHTS[element] * count for element, count in counts.items())


def _description(exchange: JsonObject) -> str:
    try:
        value = exchange["referenceToFlowDataSet"]["common:shortDescription"]
    except (KeyError, TypeError) as error:
        raise ValueError("scenario exchange is missing a Flow description") from error
    if isinstance(value, dict):
        return _required_text(value.get("#text"), "Flow description")
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return _required_text(value[0].get("#text"), "Flow description")
    raise ValueError("scenario Flow description is malformed")


def _scenario(route_id: str, rows: List[JsonObject]) -> Tuple[str, List[JsonObject]]:
    matches = []
    for row in rows:
        try:
            name = row["processDataSet"]["processInformation"]["dataSetInformation"]["name"]["baseName"][0]["#text"]
            exchanges = row["processDataSet"]["exchanges"]["exchange"]
        except (KeyError, IndexError, TypeError) as error:
            raise ValueError("residual scenario structure is malformed") from error
        if route_id in name:
            matches.append((name, exchanges))
    if len(matches) != 1 or not isinstance(matches[0][1], list):
        raise ValueError(f"exactly one {route_id} residual scenario is required")
    return matches[0]


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _case(
    route_id: str,
    exchanges: List[JsonObject],
    formulas: Dict[str, str],
    *,
    case_id: str,
) -> JsonObject:
    balance: Dict[str, Decimal] = {}
    calculations = []
    residual_amount = None
    for exchange in exchanges:
        if not isinstance(exchange, dict):
            raise ValueError("scenario exchange is malformed")
        name = _description(exchange)
        try:
            amount = Decimal(str(exchange.get("meanAmount")))
        except (InvalidOperation, ValueError, TypeError) as error:
            raise ValueError("scenario amount is not a decimal") from error
        if not amount.is_finite() or amount <= 0:
            raise ValueError("scenario amount must be positive and finite")
        direction = _required_text(exchange.get("exchangeDirection"), "direction")
        if name.startswith("Uncharacterized process mass residual"):
            if direction != "Output" or residual_amount is not None:
                raise ValueError("residual Flow must be one unique output")
            residual_amount = amount
            continue
        formula = formulas.get(name)
        if formula is None:
            continue
        mass = molar_mass(formula)
        amount_kmol = amount / mass
        sign = Decimal(1) if direction == "Input" else Decimal(-1)
        counts = parse_formula(formula)
        for element, count in counts.items():
            balance[element] = balance.get(element, Decimal(0)) + sign * amount_kmol * count
        calculations.append(
            {
                "flow_name": name,
                "direction": direction,
                "amount_kg": _decimal_text(amount),
                "formula": formula,
                "molar_mass_kg_per_kmol": _decimal_text(mass),
                "amount_kmol": _decimal_text(amount_kmol),
            }
        )
    if residual_amount is None:
        raise ValueError("scenario residual output is missing")
    residual_mass = sum(
        amount_kmol * ATOMIC_WEIGHTS[element]
        for element, amount_kmol in balance.items()
    )
    delta = residual_mass - residual_amount
    core = {
        element: abs(balance.get(element, Decimal(0)))
        for element in CORE_ELEMENTS[route_id]
    }
    maximum = max(core.values())
    consistent = maximum <= Decimal("1e-7") and abs(delta) <= Decimal("1e-12")
    return {
        "case_id": case_id,
        "status": (
            "conditionally_consistent"
            if consistent and case_id == "pure_anhydrous_named_formula_candidate"
            else "conditionally_consistent_under_probe"
            if consistent
            else "core_element_mismatch_detected"
        ),
        "flow_calculations": sorted(calculations, key=lambda row: row["flow_name"]),
        "element_balance_kmol": {
            element: _decimal_text(balance[element]) for element in sorted(balance)
        },
        "core_element_mismatch_kmol": {
            element: _decimal_text(core[element]) for element in CORE_ELEMENTS[route_id]
        },
        "maximum_core_element_mismatch_kmol": _decimal_text(maximum),
        "conditional_residual_element_mass_kg": _decimal_text(residual_mass),
        "recorded_uncharacterized_residual_kg": _decimal_text(residual_amount),
        "residual_mass_delta_kg": _decimal_text(delta),
        "residual_species_inferred": False,
    }


def compile_stoichiometric_consistency(
    acquisition_package: JsonObject,
    acquisition_binding: JsonObject,
    residual_rows: List[JsonObject],
    residual_binding: JsonObject,
) -> JsonObject:
    """Compile conditional formula-basis assurance without mutating the model."""

    if (
        acquisition_package.get("contract") != ACQUISITION_OPTIONS_CONTRACT
        or acquisition_package.get("status")
        != "responsibility_aware_acquisition_options_compiled"
        or acquisition_package.get("selected_internal_route")
        != "compile-elemental-stoichiometric-consistency-bracket"
        or acquisition_package.get("routing", {}).get("next_internal_route")
        != "compile-elemental-stoichiometric-consistency-bracket"
        or acquisition_package.get("routing", {}).get("pause_internal_work") is not False
        or acquisition_package.get("scientific_comparison_ready") is not False
        or acquisition_package.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("accepted acquisition package is required")
    package_ref = _binding(acquisition_binding, "acquisition package binding")
    residual_ref = _binding(residual_binding, "residual scenario binding")
    package_residual = acquisition_package.get("residual_scenarios")
    if not isinstance(package_residual, dict) or package_residual != residual_ref:
        raise ValueError("residual scenario binding does not match acquisition package")
    selection = acquisition_package.get("internal_route_selection")
    if not isinstance(selection, dict):
        raise ValueError("acquisition package formula selection is missing")
    candidates = selection.get("formula_candidates")
    if not isinstance(candidates, list) or len(candidates) != 9:
        raise ValueError("exactly nine accepted formula candidates are required")
    formulas = {}
    basis_risks = {}
    for row in candidates:
        if not isinstance(row, dict):
            raise ValueError("formula candidate is malformed")
        name = _required_text(row.get("flow_name"), "flow_name")
        if name in formulas:
            raise ValueError("duplicate formula candidate")
        formula = _required_text(row.get("formula"), "formula")
        parse_formula(formula)
        formulas[name] = formula
        basis_risks[name] = _required_text(row.get("basis_status"), "basis_status")

    route_results = []
    structural_probe_count = 0
    for route_id in ("hydrothermal", "solid-state"):
        process_name, exchanges = _scenario(route_id, residual_rows)
        baseline = _case(
            route_id,
            exchanges,
            formulas,
            case_id="pure_anhydrous_named_formula_candidate",
        )
        probes = []
        for probe in STRUCTURAL_PROBES:
            if probe["route_id"] != route_id:
                continue
            probe_formulas = {**formulas, **probe["overrides"]}
            result = _case(
                route_id,
                exchanges,
                probe_formulas,
                case_id=probe["probe_id"],
            )
            result["formula_overrides"] = probe["overrides"]
            result["interpretation"] = probe["interpretation"]
            probes.append(result)
        structural_probe_count += len(probes)
        route_results.append(
            {
                "route_id": route_id,
                "process_name": process_name,
                "baseline_case": baseline,
                "structural_formula_probes": probes,
                "baseline_identity_proven": False,
                "residual_composition_proven": False,
            }
        )

    consistent_count = sum(
        row["baseline_case"]["status"] == "conditionally_consistent"
        for row in route_results
    )
    return {
        "schema_version": 1,
        "contract": STOICHIOMETRIC_CONSISTENCY_CONTRACT,
        "scope_id": _required_text(acquisition_package.get("scope_id"), "scope_id"),
        "revision": _required_text(acquisition_package.get("revision"), "revision"),
        "status": "elemental_stoichiometric_consistency_compiled",
        "acquisition_package": package_ref,
        "residual_scenarios": residual_ref,
        "atomic_weight_basis": {
            "basis_id": "explicit-conventional-screening-values-v1",
            "unit": "g/mol",
            "values": {key: str(value) for key, value in sorted(ATOMIC_WEIGHTS.items())},
            "use_boundary": "conditional arithmetic screening, not substance identity",
        },
        "formula_candidates": [
            {
                "flow_name": name,
                "formula": formulas[name],
                "parsed_composition": parse_formula(formulas[name]),
                "molar_mass_g_mol": _decimal_text(molar_mass(formulas[name])),
                "basis_status": basis_risks[name],
            }
            for name in sorted(formulas)
        ],
        "route_count": len(route_results),
        "conditionally_consistent_route_count": consistent_count,
        "structural_probe_count": structural_probe_count,
        "route_results": route_results,
        "formula_basis_uncertainty": {
            "quantitative_bracket_status": "unbounded_pending_formula_basis_evidence",
            "unbounded_dimensions": [
                "input purity",
                "phosphoric acid solution concentration",
                "lithium hydroxide hydration state",
                "iron sulfate hydration and oxidation state",
                "lithium sulfate hydration state",
            ],
            "structural_probes_are_bounds": False,
            "values_invented": False,
        },
        "quantitative_bracket_status": "unbounded_pending_formula_basis_evidence",
        "scientific_conclusion": "conditional_formula_basis_consistent_not_identity_proof",
        "residual_species_reclassification_allowed": False,
        "model_mutation_allowed": False,
        "scientific_field_evidence_admitted": False,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "routing": {
            "next_internal_route": "compile-route-model-assurance-claim-matrix",
            "pause_internal_work": False,
        },
        "next_action": (
            "Compile a route model-assurance claim matrix that combines native QA, mass "
            "closure, unit interpretation, lineage, and this conditional stoichiometric result."
        ),
    }


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def write_report(report: JsonObject, output: Path) -> None:
    if report.get("contract") != STOICHIOMETRIC_CONSISTENCY_CONTRACT:
        raise ValueError("stoichiometric consistency report is required")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(_json_bytes(report))


def _read_json(path: Path) -> JsonObject:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _read_jsonl(path: Path) -> List[JsonObject]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{path} must contain JSON objects")
    return rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acquisition-package", type=Path, required=True)
    parser.add_argument("--acquisition-binding", type=Path, required=True)
    parser.add_argument("--residual-rows", type=Path, required=True)
    parser.add_argument("--residual-binding", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = compile_stoichiometric_consistency(
        _read_json(args.acquisition_package),
        _read_json(args.acquisition_binding),
        _read_jsonl(args.residual_rows),
        _read_json(args.residual_binding),
    )
    write_report(report, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
