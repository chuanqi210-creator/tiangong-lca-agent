"""Compile a route-scoped ledger of bounded model-assurance claims."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence


JsonObject = Dict[str, object]
SOURCE_CONTRACT = "lca_research_kernel.route_model_assurance_sources/v1"
CLAIM_MATRIX_CONTRACT = (
    "lca_research_kernel.route_model_assurance_claim_matrix/v1"
)
CLAIM_CLASSES = (
    "conditional_screening_fact",
    "prohibited_claim",
    "unresolved_evidence_dependency",
    "verified_executable_fact",
)
ROUTE_IDS = ("hydrothermal", "solid-state")


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _artifact(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an artifact binding")
    path = _required_text(value.get("path"), f"{label}.path")
    sha256 = _required_text(value.get("sha256"), f"{label}.sha256")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError(f"{label}.sha256 must be a lowercase sha256")
    return {"path": path, "sha256": sha256}


def _text_list(value: object, label: str, *, allow_empty: bool = False) -> List[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"{label} must be a non-empty list")
    rows = [_required_text(row, label) for row in value]
    if len(rows) != len(set(rows)):
        raise ValueError(f"{label} contains duplicates")
    return rows


def _claim(
    *,
    claim_id: str,
    route_id: str,
    claim_class: str,
    statement: str,
    meaning: str,
    evidence_refs: List[str],
    invalidation_triggers: List[str],
    rebuild_action: str,
    assumptions: Optional[List[str]] = None,
    release_conditions: Optional[List[str]] = None,
    applies_to_routes: Optional[List[str]] = None,
    comparison_effect: str = "does_not_open_comparison",
    publication_effect: str = "does_not_open_publication",
) -> JsonObject:
    return {
        "claim_id": claim_id,
        "route_id": route_id,
        "applies_to_routes": applies_to_routes or [route_id],
        "claim_class": claim_class,
        "statement": statement,
        "meaning": meaning,
        "evidence_refs": evidence_refs,
        "assumptions": assumptions or [],
        "invalidation_triggers": invalidation_triggers,
        "rebuild_action": rebuild_action,
        "release_conditions": release_conditions or [],
        "comparison_effect": comparison_effect,
        "publication_effect": publication_effect,
    }


def _route_claims(route: JsonObject) -> List[JsonObject]:
    route_id = _required_text(route.get("route_id"), "route_id")
    qa = route["process_qa"]
    residual = route["residual"]
    energy = route["energy_interpretation"]
    stoich = route["stoichiometric_baseline"]
    qa_ref = qa["evidence_ref"]
    return [
        _claim(
            claim_id=f"{route_id}:verified-native-scenario-validation",
            route_id=route_id,
            claim_class="verified_executable_fact",
            statement="The current route scenario is part of a 2/2 native-valid scenario set.",
            meaning="TianGong accepts the current dataset structure and references for execution.",
            evidence_refs=["residual-validation", "residual-scenarios"],
            invalidation_triggers=["residual-validation sha256 changes", "residual-scenarios sha256 changes"],
            rebuild_action="rerun native Process validation for both current route scenarios",
        ),
        _claim(
            claim_id=f"{route_id}:verified-zero-blocker-process-qa",
            route_id=route_id,
            claim_class="verified_executable_fact",
            statement="The current route Process QA gate has zero blocker and zero finding.",
            meaning="The current screening Process passes the registered deterministic QA ruleset.",
            evidence_refs=[qa_ref],
            invalidation_triggers=[f"{qa_ref} sha256 changes", "residual-scenarios sha256 changes"],
            rebuild_action=f"rerun route-isolated Process QA for {route_id}",
        ),
        _claim(
            claim_id=f"{route_id}:verified-composition-unknown-mass-closure",
            route_id=route_id,
            claim_class="verified_executable_fact",
            statement=f"Mass closure includes a {residual['amount_kg']} kg composition-unknown residual output.",
            meaning="The modeled kg balance closes without inventing residual species or compartments.",
            evidence_refs=["residual-scenarios", qa_ref],
            invalidation_triggers=["residual-scenarios sha256 changes", "residual definition changes"],
            rebuild_action="recompile residual definitions and repeat Process validation and QA",
        ),
        _claim(
            claim_id=f"{route_id}:verified-lineage-addressability",
            route_id=route_id,
            claim_class="verified_executable_fact",
            statement="Current route scenario and assurance artifacts are content-addressable in lineage.",
            meaning="A changed upstream hash can invalidate dependent artifacts without deleting history.",
            evidence_refs=["lineage-report", "residual-scenarios"],
            invalidation_triggers=["lineage-report sha256 changes", "any bound upstream artifact changes"],
            rebuild_action="recompile the lineage graph and route only stale descendants",
        ),
        _claim(
            claim_id=f"{route_id}:conditional-ton-unit-energy-interpretation",
            route_id=route_id,
            claim_class="conditional_screening_fact",
            statement=(
                f"Energy is {energy['short_ton_amount_mj']} MJ under the retained short-ton "
                f"interpretation and {energy['metric_tonne_amount_mj']} MJ under metric tonne."
            ),
            meaning="The deterministic unit interpretation changes energy by -9.281526%; it is not a probability model.",
            evidence_refs=["ton-contract"],
            assumptions=["source ton means US short ton or metric tonne", energy["allocation_basis"]],
            invalidation_triggers=["ton-contract sha256 changes", "source unit evidence changes", "allocation basis changes"],
            rebuild_action="recompile and native-validate both ton interpretation scenarios",
        ),
        _claim(
            claim_id=f"{route_id}:conditional-named-formula-stoichiometry",
            route_id=route_id,
            claim_class="conditional_screening_fact",
            statement="Named pure/anhydrous formula candidates close core elements and recorded residual mass within tolerance.",
            meaning="This is conditional arithmetic coherence and not identity proof, purity proof, or residual-species proof.",
            evidence_refs=["stoichiometric-report", "residual-scenarios"],
            assumptions=["flow names map to recorded formulas", "inputs are pure", "formula candidates are anhydrous unless explicit"],
            invalidation_triggers=["stoichiometric-report sha256 changes", "formula basis evidence changes", "scenario amounts change"],
            rebuild_action="recompile formula parsing and route elemental balances",
        ),
        _claim(
            claim_id=f"{route_id}:unresolved-route-primary-inventory",
            route_id=route_id,
            claim_class="unresolved_evidence_dependency",
            statement="A target-bound complete primary inventory for this route is unresolved.",
            meaning="Current exchanges remain screening proxies rather than an admitted paper-specific inventory.",
            evidence_refs=["acquisition-package", "residual-scenarios"],
            invalidation_triggers=["accepted target-paper field evidence arrives", "acquisition-package sha256 changes"],
            rebuild_action="bind admitted field evidence and replace only affected route exchanges through a governed patch",
            release_conditions=["accepted target-bound route inventory fields with page/span provenance"],
        ),
        _claim(
            claim_id=f"{route_id}:unresolved-formula-basis",
            route_id=route_id,
            claim_class="unresolved_evidence_dependency",
            statement="Purity, concentration, hydration, and oxidation-state formula basis remains unbounded.",
            meaning="Structural probes expose sensitivity but do not provide evidence-backed numeric bounds.",
            evidence_refs=["stoichiometric-report", "acquisition-package"],
            invalidation_triggers=["formula-basis evidence is admitted", "stoichiometric-report sha256 changes"],
            rebuild_action="compile evidence-bound formula cases and rerun elemental balances",
            release_conditions=["source-bound purity, concentration, hydration, and oxidation-state basis for applicable flows"],
        ),
        _claim(
            claim_id=f"{route_id}:unresolved-residual-species-and-compartment",
            route_id=route_id,
            claim_class="unresolved_evidence_dependency",
            statement="Residual species, phase, compartment, and characterization remain unresolved.",
            meaning="Mass conservation supplies quantity only and cannot identify releases or impacts.",
            evidence_refs=["residual-scenarios", "acquisition-package", "stoichiometric-report"],
            invalidation_triggers=["species-resolved evidence is admitted", "residual-scenarios sha256 changes"],
            rebuild_action="resolve native flow identities and replace the residual proxy through an invalidating patch",
            release_conditions=["species-resolved quantity and environmental-compartment evidence with valid native flow references"],
        ),
        _claim(
            claim_id=f"{route_id}:unresolved-target-protocol-fields",
            route_id=route_id,
            claim_class="unresolved_evidence_dependency",
            statement="Target-paper protocol, yield, energy-boundary, and performance-alignment fields remain unresolved.",
            meaning="Metadata identity does not satisfy the current 15 field-level scientific contracts.",
            evidence_refs=["acquisition-package"],
            invalidation_triggers=["lawful attachment or attributable primary response is admitted"],
            rebuild_action="extract field evidence, rerun performance/scope gates, and invalidate dependent model nodes",
            release_conditions=["accepted target-paper or attributable primary-response fields for this route"],
        ),
    ]


def _shared_prohibitions() -> List[JsonObject]:
    common = {
        "route_id": "shared",
        "applies_to_routes": list(ROUTE_IDS),
        "claim_class": "prohibited_claim",
        "comparison_effect": "blocks_comparison_or_stronger_claim",
        "publication_effect": "blocks_publication_claim",
    }
    rows = [
        (
            "shared:prohibit-species-resolved-residual",
            "Do not identify either residual output as water, gas, wastewater, solid waste, or another species.",
            "Conditional element balance cannot determine molecular species, phase, or compartment.",
            ["residual-scenarios", "stoichiometric-report"],
            ["species-resolved source evidence and native identity validation for replacement flows"],
        ),
        (
            "shared:prohibit-characterized-elementary-emissions",
            "Do not characterize residual mass as elementary emissions.",
            "No species-resolved elementary flows or characterization references are admitted.",
            ["residual-scenarios", "acquisition-package"],
            ["species, quantity, compartment, native elementary-flow identity, and characterization evidence"],
        ),
        (
            "shared:prohibit-comparative-inventory-ranking",
            "Do not claim one route has the better complete inventory.",
            "Both foreground inventories retain target-paper and allocation gaps.",
            ["acquisition-package", "residual-scenarios"],
            ["route-aligned primary inventories and accepted allocation/scaling basis for both routes"],
        ),
        (
            "shared:prohibit-comparative-lcia",
            "Do not calculate or publish comparative LCIA results.",
            "Elementary emissions, background links, allocation and comparison-critical evidence are incomplete.",
            ["acquisition-package", "residual-scenarios", "lineage-report"],
            ["comparison-ready inventories, characterized elementary flows, background links, allocation, and passed assurance gates"],
        ),
        (
            "shared:prohibit-global-or-probabilistic-sensitivity",
            "Do not call deterministic probes global sensitivity or probabilistic uncertainty.",
            "No evidence-bound distributions, priors, likelihoods, or comparison-ready response model exist.",
            ["ton-contract", "stoichiometric-report", "acquisition-package"],
            ["evidence-bound parameter ranges or distributions and a comparison-ready response model"],
        ),
        (
            "shared:prohibit-publication-ready-claim",
            "Do not claim the two-route LCA is publication-ready.",
            "The current product is an executable, auditable screening model with explicit scientific gaps.",
            ["lineage-report", "acquisition-package", "stoichiometric-report"],
            ["all publication-critical evidence, comparison, assurance, review, and publication dry-run gates pass"],
        ),
    ]
    return [
        _claim(
            claim_id=claim_id,
            statement=statement,
            meaning=meaning,
            evidence_refs=evidence_refs,
            invalidation_triggers=["any listed evidence sha256 changes", "a release condition is satisfied"],
            rebuild_action="recompile the claim matrix from current accepted artifacts",
            release_conditions=release_conditions,
            **common,
        )
        for claim_id, statement, meaning, evidence_refs, release_conditions in rows
    ]


def compile_claim_matrix(source_register: JsonObject, source_binding: JsonObject) -> JsonObject:
    """Compile route-scoped claim records from accepted assurance sources."""

    if (
        source_register.get("contract") != SOURCE_CONTRACT
        or source_register.get("status") != "current_route_model_assurance_sources_registered"
        or source_register.get("scientific_comparison_ready") is not False
        or source_register.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("closed assurance sources are required")
    source_ref = _artifact(source_binding, "source binding")
    scope_id = _required_text(source_register.get("scope_id"), "scope_id")
    revision = _required_text(source_register.get("revision"), "revision")
    evidence = source_register.get("evidence_registry")
    if not isinstance(evidence, dict) or set(evidence) != {
        "residual-scenarios",
        "residual-validation",
        "residual-qa:solid-state",
        "residual-qa:hydrothermal",
        "ton-contract",
        "lineage-report",
        "acquisition-package",
        "stoichiometric-report",
    }:
        raise ValueError("exact assurance evidence registry is required")
    evidence_registry = {
        key: _artifact(value, f"evidence_registry.{key}")
        for key, value in evidence.items()
    }
    routes = source_register.get("routes")
    if not isinstance(routes, list) or {
        row.get("route_id") for row in routes if isinstance(row, dict)
    } != set(ROUTE_IDS) or len(routes) != 2:
        raise ValueError("exact route set is required")
    validated_routes = []
    for route in routes:
        if not isinstance(route, dict):
            raise ValueError("route assurance source is malformed")
        route_id = _required_text(route.get("route_id"), "route_id")
        validation = route.get("dataset_validation")
        qa = route.get("process_qa")
        residual = route.get("residual")
        energy = route.get("energy_interpretation")
        stoich = route.get("stoichiometric_baseline")
        if (
            not isinstance(validation, dict)
            or validation.get("total") != 2
            or validation.get("valid") != 2
            or validation.get("invalid") != 0
            or not isinstance(qa, dict)
            or qa.get("gate_status") != "passed"
            or qa.get("blocker_count") != 0
            or qa.get("finding_count") != 0
            or not isinstance(residual, dict)
            or residual.get("composition_status")
            != "unknown_not_inferred_from_mass_conservation"
            or residual.get("characterization_status")
            != "prohibited_until_species_resolved_evidence"
            or not isinstance(energy, dict)
            or energy.get("route_order_unchanged") is not True
            or not isinstance(stoich, dict)
            or stoich.get("status") != "conditionally_consistent"
            or stoich.get("identity_proven") is not False
        ):
            raise ValueError(f"accepted {route_id} assurance sources are required")
        qa_ref = _required_text(qa.get("evidence_ref"), "process QA evidence ref")
        if qa_ref not in evidence_registry:
            raise ValueError(f"unresolved evidence ref: {qa_ref}")
        validated_routes.append(route)
    shared = source_register.get("shared_assurance")
    if (
        not isinstance(shared, dict)
        or shared.get("lineage_status") != "current_lineage_content_addressed"
        or shared.get("history_deleted") is not False
        or shared.get("unresolved_field_count") != 15
        or shared.get("external_action_executed_count") != 0
        or shared.get("formula_basis_quantitative_bracket_status")
        != "unbounded_pending_formula_basis_evidence"
    ):
        raise ValueError("accepted shared assurance sources are required")

    claims = []
    for route in sorted(validated_routes, key=lambda row: row["route_id"]):
        claims.extend(_route_claims(route))
    claims.extend(_shared_prohibitions())
    claim_ids = [row["claim_id"] for row in claims]
    if len(claim_ids) != len(set(claim_ids)):
        raise ValueError("claim IDs must be unique")
    for claim in claims:
        if claim["claim_class"] not in CLAIM_CLASSES:
            raise ValueError("claim class is invalid")
        if not set(claim["evidence_refs"]).issubset(evidence_registry):
            raise ValueError(f"claim {claim['claim_id']} has unresolved evidence ref")
        _text_list(claim["invalidation_triggers"], "invalidation_triggers")
        _required_text(claim["rebuild_action"], "rebuild_action")
        if claim["claim_class"] in {"unresolved_evidence_dependency", "prohibited_claim"}:
            _text_list(claim["release_conditions"], "release_conditions")
    counts = {
        claim_class: sum(row["claim_class"] == claim_class for row in claims)
        for claim_class in CLAIM_CLASSES
    }
    route_matrices = []
    for route_id in ROUTE_IDS:
        applicable = [
            row for row in claims if route_id in row["applies_to_routes"]
        ]
        route_matrices.append(
            {
                "route_id": route_id,
                "status": "executable_screening_model_claims_bounded",
                **{
                    f"{claim_class}_count": sum(
                        row["claim_class"] == claim_class for row in applicable
                    )
                    for claim_class in CLAIM_CLASSES
                },
                "claim_ids": [row["claim_id"] for row in applicable],
                "scientific_comparison_ready": False,
                "comparative_lcia_allowed": False,
            }
        )
    return {
        "schema_version": 1,
        "contract": CLAIM_MATRIX_CONTRACT,
        "scope_id": scope_id,
        "revision": revision,
        "status": "route_model_assurance_claim_matrix_compiled",
        "source_register": source_ref,
        "evidence_registry": evidence_registry,
        "route_count": 2,
        "claim_count": len(claims),
        "claim_class_counts": counts,
        "verified_executable_fact_count": counts["verified_executable_fact"],
        "conditional_screening_fact_count": counts["conditional_screening_fact"],
        "unresolved_evidence_dependency_count": counts["unresolved_evidence_dependency"],
        "prohibited_claim_count": counts["prohibited_claim"],
        "claims": claims,
        "route_matrices": route_matrices,
        "readiness_representation": "typed_claim_classes_no_aggregate_score",
        "scientific_field_evidence_admitted": False,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "routing": {
            "next_internal_route": "compile-claim-to-evidence-replacement-map",
            "pause_internal_work": False,
        },
        "next_action": (
            "Compile a claim-to-evidence replacement map that binds proxy, conditional, "
            "unresolved, and prohibited claims to the 15 acquisition fields and exact stale nodes."
        ),
    }


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def write_report(report: JsonObject, output: Path) -> None:
    if report.get("contract") != CLAIM_MATRIX_CONTRACT:
        raise ValueError("route claim matrix report is required")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(_json_bytes(report))


def _read_json(path: Path) -> JsonObject:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-register", type=Path, required=True)
    parser.add_argument("--source-binding", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = compile_claim_matrix(
        _read_json(args.source_register), _read_json(args.source_binding)
    )
    write_report(report, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
