"""Compile claim-to-evidence replacement and lineage invalidation routes."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set


JsonObject = Dict[str, object]
REPLACEMENT_MAP_CONTRACT = (
    "lca_research_kernel.claim_evidence_replacement_map/v1"
)
CLAIM_MATRIX_CONTRACT = (
    "lca_research_kernel.route_model_assurance_claim_matrix/v1"
)
EVIDENCE_CONTRACT = "lca_research_kernel.evidence_acquisition_manifest/v1"
ACQUISITION_CONTRACT = (
    "lca_research_kernel.responsibility_aware_acquisition_options/v1"
)
LINEAGE_CONTRACT = "lca_research_kernel.model_lineage_invalidation/v1"
ROUTES = ("hydrothermal", "solid-state")

PROTOCOL_FIELDS = {
    "headline-capacity-to-sample-row",
    "electrochemical-test-temperature",
    "phase-purity-and-xrd-impurities",
}
SPECIES_FIELDS = {
    "coating-gas-identity-and-flow",
    "coating-emissions-and-solid-liquid-waste",
    "gas-wastewater-and-solid-residue-species",
}
ENERGY_FIELDS = {
    "coating-temperature-duration-and-energy",
    "route-specific-energy-and-equipment-boundary",
}
FORMULA_FIELDS = {
    "route-specific-carbon-content-basis",
    "route-specific-sucrose-input-and-yield",
    "candidate-specific-route-inventory",
    "continuous-route-feed-composition-and-flow",
    "solid-state-comparator-material-and-thermal-inventory",
    "route-specific-product-and-coproduct-yield",
}

FIELD_CHANGED_NODES = {
    "headline-capacity-to-sample-row": ["scientific-gap-base-register"],
    "electrochemical-test-temperature": ["scientific-gap-base-register"],
    "phase-purity-and-xrd-impurities": ["scientific-gap-base-register"],
    "route-specific-carbon-content-basis": ["inventory-plan", "canonical-reference-scenarios"],
    "route-specific-sucrose-input-and-yield": ["inventory-plan", "canonical-reference-scenarios"],
    "coating-gas-identity-and-flow": ["residual-flow-validation", "residual-flow-qa"],
    "coating-temperature-duration-and-energy": ["inventory-plan", "canonical-reference-scenarios", "ton-unit-contract"],
    "coating-emissions-and-solid-liquid-waste": ["residual-flow-validation", "residual-flow-qa"],
    "candidate-specific-route-inventory": ["inventory-plan", "canonical-reference-scenarios"],
    "continuous-route-feed-composition-and-flow": ["inventory-plan", "canonical-reference-scenarios"],
    "continuous-route-temperature-pressure-residence-time": ["inventory-plan", "canonical-reference-scenarios"],
    "solid-state-comparator-material-and-thermal-inventory": ["inventory-plan", "canonical-reference-scenarios"],
    "route-specific-product-and-coproduct-yield": ["inventory-plan", "canonical-reference-scenarios"],
    "route-specific-energy-and-equipment-boundary": ["inventory-plan", "ton-unit-contract"],
    "gas-wastewater-and-solid-residue-species": ["residual-flow-validation", "residual-flow-qa"],
}


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


def _scope(document: JsonObject, label: str) -> tuple[str, str]:
    return (
        _required_text(document.get("scope_id"), f"{label}.scope_id"),
        _required_text(document.get("revision"), f"{label}.revision"),
    )


def _field_route_scope(field_id: str) -> List[str]:
    if field_id.startswith("continuous-route-"):
        return ["hydrothermal"]
    if field_id == "solid-state-comparator-material-and-thermal-inventory":
        return ["solid-state"]
    return list(ROUTES)


def _field_gap(field_id: str) -> JsonObject:
    if field_id in PROTOCOL_FIELDS:
        return {
            "gap_id": "performance-protocol-full-text",
            "scientific_impact_score": 85,
            "execution_priority_score": 25,
        }
    if field_id in {
        "gas-wastewater-and-solid-residue-species",
        "coating-gas-identity-and-flow",
        "coating-emissions-and-solid-liquid-waste",
    }:
        return {
            "gap_id": "elementary-emissions-characterization",
            "scientific_impact_score": 100,
            "execution_priority_score": 24,
        }
    if field_id in {
        "route-specific-carbon-content-basis",
        "route-specific-sucrose-input-and-yield",
        "coating-temperature-duration-and-energy",
        "candidate-specific-route-inventory",
    }:
        return {
            "gap_id": "carbon-coating-inventory",
            "scientific_impact_score": 100,
            "execution_priority_score": 28,
        }
    return {
        "gap_id": "route-aligned-primary-inventories",
        "scientific_impact_score": 100,
        "execution_priority_score": 24,
    }


def _claim_links(field_id: str, route_scope: List[str]) -> Set[str]:
    claims: Set[str] = {"shared:prohibit-publication-ready-claim"}
    if field_id in PROTOCOL_FIELDS:
        for route in ROUTES:
            claims.add(f"{route}:unresolved-target-protocol-fields")
        claims.update(
            {
                "shared:prohibit-comparative-inventory-ranking",
                "shared:prohibit-comparative-lcia",
            }
        )
        return claims
    for route in route_scope:
        claims.add(f"{route}:unresolved-route-primary-inventory")
        if field_id in FORMULA_FIELDS:
            claims.add(f"{route}:unresolved-formula-basis")
            claims.add(f"{route}:conditional-named-formula-stoichiometry")
        if field_id in SPECIES_FIELDS:
            claims.add(f"{route}:unresolved-residual-species-and-compartment")
        if field_id in ENERGY_FIELDS:
            claims.add(f"{route}:conditional-ton-unit-energy-interpretation")
        if field_id in {"continuous-route-temperature-pressure-residence-time"}:
            claims.add(f"{route}:unresolved-target-protocol-fields")
    if field_id in SPECIES_FIELDS:
        claims.update(
            {
                "shared:prohibit-species-resolved-residual",
                "shared:prohibit-characterized-elementary-emissions",
                "shared:prohibit-comparative-lcia",
            }
        )
    else:
        claims.update(
            {
                "shared:prohibit-comparative-inventory-ranking",
                "shared:prohibit-comparative-lcia",
            }
        )
    if field_id in ENERGY_FIELDS or field_id in FORMULA_FIELDS:
        claims.add("shared:prohibit-global-or-probabilistic-sensitivity")
    return claims


def _descendants(changed: List[str], adjacency: Dict[str, Set[str]]) -> List[str]:
    seen = set(changed)
    queue = deque(changed)
    while queue:
        current = queue.popleft()
        for downstream in sorted(adjacency.get(current, set())):
            if downstream not in seen:
                seen.add(downstream)
                queue.append(downstream)
    return sorted(seen)


def compile_replacement_map(
    claim_matrix: JsonObject,
    claim_binding: JsonObject,
    evidence_manifest: JsonObject,
    evidence_binding: JsonObject,
    acquisition_package: JsonObject,
    acquisition_binding: JsonObject,
    lineage_report: JsonObject,
    lineage_binding: JsonObject,
) -> JsonObject:
    """Compile field-to-claim replacement routes and graph-derived invalidation."""

    if (
        claim_matrix.get("contract") != CLAIM_MATRIX_CONTRACT
        or claim_matrix.get("status") != "route_model_assurance_claim_matrix_compiled"
        or claim_matrix.get("claim_count") != 26
        or claim_matrix.get("scientific_comparison_ready") is not False
        or claim_matrix.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("accepted claim matrix is required")
    if (
        evidence_manifest.get("contract") != EVIDENCE_CONTRACT
        or evidence_manifest.get("status") != "lawful_evidence_acquisition_manifest_compiled"
        or evidence_manifest.get("source_target_count") != 2
        or evidence_manifest.get("values_invented") is not False
        or evidence_manifest.get("scientific_comparison_ready") is not False
        or evidence_manifest.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("accepted evidence manifest is required")
    if (
        acquisition_package.get("contract") != ACQUISITION_CONTRACT
        or acquisition_package.get("status")
        != "responsibility_aware_acquisition_options_compiled"
        or acquisition_package.get("target_count") != 2
        or acquisition_package.get("unresolved_field_count") != 15
        or acquisition_package.get("external_action_executed_count") != 0
        or acquisition_package.get("scientific_comparison_ready") is not False
        or acquisition_package.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("accepted acquisition package is required")
    graph = lineage_report.get("invalidation_graph")
    if (
        lineage_report.get("contract") != LINEAGE_CONTRACT
        or lineage_report.get("status") != "gap_progress_and_current_lineage_compiled"
        or not isinstance(graph, dict)
        or graph.get("status") != "current_content_addressed_lineage_compiled"
    ):
        raise ValueError("accepted lineage report is required")
    scopes = {
        _scope(claim_matrix, "claim matrix"),
        _scope(evidence_manifest, "evidence manifest"),
        _scope(acquisition_package, "acquisition package"),
        _scope(lineage_report, "lineage report"),
    }
    if len(scopes) != 1:
        raise ValueError("replacement-map source scope conflicts")
    scope_id, revision = next(iter(scopes))
    bindings = {
        "claim_matrix": _artifact(claim_binding, "claim binding"),
        "evidence_manifest": _artifact(evidence_binding, "evidence binding"),
        "acquisition_package": _artifact(acquisition_binding, "acquisition binding"),
        "lineage_report": _artifact(lineage_binding, "lineage binding"),
    }

    fields = []
    field_ids = set()
    targets = evidence_manifest.get("source_targets")
    if not isinstance(targets, list) or len(targets) != 2:
        raise ValueError("exactly two source targets are required")
    for target in targets:
        if not isinstance(target, dict):
            raise ValueError("source target is malformed")
        target_id = _required_text(target.get("target_id"), "target_id")
        rows = target.get("required_fields")
        if not isinstance(rows, list):
            raise ValueError("required fields are malformed")
        for field in rows:
            if not isinstance(field, dict):
                raise ValueError("required field is malformed")
            field_id = _required_text(field.get("field_id"), "field_id")
            if field_id in field_ids or field.get("status") != "unresolved" or field.get("value_invented") is not False:
                raise ValueError("15 unique unresolved fields are required")
            field_ids.add(field_id)
            accepted = field.get("accepted_evidence")
            if not isinstance(accepted, list) or not accepted:
                raise ValueError("field accepted evidence is missing")
            fields.append(
                {
                    "target_id": target_id,
                    "field_id": field_id,
                    "accepted_evidence": [
                        _required_text(value, "accepted evidence") for value in accepted
                    ],
                }
            )
    if len(field_ids) != 15 or field_ids != set(FIELD_CHANGED_NODES):
        raise ValueError("15 unique unresolved fields are required")

    claims = claim_matrix.get("claims")
    if not isinstance(claims, list) or len(claims) != 26:
        raise ValueError("26 current claims are required")
    claims_by_id = {}
    for claim in claims:
        if not isinstance(claim, dict):
            raise ValueError("claim is malformed")
        claim_id = _required_text(claim.get("claim_id"), "claim_id")
        if claim_id in claims_by_id:
            raise ValueError("claim IDs must be unique")
        claims_by_id[claim_id] = claim
    changeable_claims = {
        claim_id
        for claim_id, claim in claims_by_id.items()
        if claim.get("claim_class") != "verified_executable_fact"
    }
    verified_claims = set(claims_by_id) - changeable_claims
    if len(changeable_claims) != 18 or len(verified_claims) != 8:
        raise ValueError("claim class counts changed")

    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or len(nodes) != 11:
        raise ValueError("11 current lineage nodes are required")
    node_rows = {}
    for row in nodes:
        if not isinstance(row, dict) or row.get("status") != "current":
            raise ValueError("11 current lineage nodes are required")
        node_id = _required_text(row.get("node_id"), "node_id")
        if node_id in node_rows:
            raise ValueError("lineage node IDs must be unique")
        node_rows[node_id] = row
    if not isinstance(edges, list) or len(edges) != 10:
        raise ValueError("10 lineage edges are required")
    adjacency: Dict[str, Set[str]] = defaultdict(set)
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("lineage edge is malformed")
        upstream = _required_text(edge.get("upstream"), "edge.upstream")
        downstream = _required_text(edge.get("downstream"), "edge.downstream")
        if upstream not in node_rows or downstream not in node_rows:
            raise ValueError("lineage edge references missing node")
        adjacency[upstream].add(downstream)

    option_rows = acquisition_package.get("options")
    if not isinstance(option_rows, list) or len(option_rows) != 5:
        raise ValueError("five acquisition options are required")
    eligible_by_field: Dict[str, List[str]] = {field_id: [] for field_id in field_ids}
    for option in option_rows:
        if not isinstance(option, dict):
            raise ValueError("acquisition option is malformed")
        option_id = _required_text(option.get("option_id"), "option_id")
        direct = option.get("directly_addressable_field_ids")
        if not isinstance(direct, list):
            raise ValueError("option field coverage is malformed")
        if "credential" in option_id and direct:
            raise ValueError("credential options cannot directly replace fields")
        for field_id in direct:
            if field_id not in eligible_by_field:
                raise ValueError("option references unknown field")
            eligible_by_field[field_id].append(option_id)

    field_routes = []
    reverse: Dict[str, Set[str]] = {claim_id: set() for claim_id in changeable_claims}
    all_node_ids = set(node_rows)
    for field in sorted(fields, key=lambda row: row["field_id"]):
        field_id = field["field_id"]
        route_scope = _field_route_scope(field_id)
        changed = sorted(FIELD_CHANGED_NODES[field_id])
        if not set(changed).issubset(all_node_ids):
            raise ValueError(f"field {field_id} lacks exact lineage node coverage")
        stale = _descendants(changed, adjacency)
        preserved = sorted(all_node_ids - set(stale))
        linked = sorted(_claim_links(field_id, route_scope))
        if not set(linked).issubset(changeable_claims):
            unknown = sorted(set(linked) - changeable_claims)
            raise ValueError(f"field {field_id} links unknown/change-ineligible claims: {unknown}")
        for claim_id in linked:
            reverse[claim_id].add(field_id)
        gap = _field_gap(field_id)
        actions = [
            {
                "action_id": "register-and-validate-admitted-field-evidence",
                "required_inputs": ["target_id", "field_id", "source artifact path/sha256", "page/span provenance"],
                "executed": False,
            },
            *[
                {
                    "action_id": f"rebuild-lineage-node:{node_id}",
                    "node_id": node_id,
                    "rebuild_action": _required_text(node_rows[node_id].get("rebuild_action"), "rebuild_action"),
                    "executed": False,
                }
                for node_id in stale
            ],
            {
                "action_id": "recompile-model-assurance-claim-matrix",
                "required_inputs": ["accepted replacement artifacts", "revalidated stale nodes"],
                "executed": False,
            },
        ]
        field_routes.append(
            {
                **field,
                "status": "replacement_route_compiled_not_executed",
                "route_scope": route_scope,
                **gap,
                "eligible_option_ids": sorted(eligible_by_field[field_id]),
                "affected_claim_ids": linked,
                "changed_node_ids": changed,
                "stale_node_ids": stale,
                "preserved_node_ids": preserved,
                "replacement_actions": actions,
                "history_deleted": False,
                "evidence_admitted": False,
                "model_mutation_executed": False,
                "claim_released": False,
            }
        )
    unmapped = sorted(claim_id for claim_id, mapped in reverse.items() if not mapped)
    if unmapped:
        raise ValueError(f"changeable claims lack field replacement routes: {unmapped}")
    reverse_index = [
        {
            "claim_id": claim_id,
            "claim_class": claims_by_id[claim_id]["claim_class"],
            "field_ids": sorted(reverse[claim_id]),
            "claim_transition_executed": False,
        }
        for claim_id in sorted(reverse)
    ]
    preservation_rules = [
        {
            "claim_id": claim_id,
            "status": "retain_until_bound_evidence_or_node_changes",
            "revalidation_trigger_field_ids": [
                row["field_id"]
                for row in field_routes
                if claims_by_id[claim_id]["route_id"] == "shared"
                or claims_by_id[claim_id]["route_id"] in row["route_scope"]
            ],
            "history_deleted": False,
        }
        for claim_id in sorted(verified_claims)
    ]
    selected_route = next(
        row
        for row in field_routes
        if row["field_id"] == "candidate-specific-route-inventory"
    )
    selected = {
        "target_id": selected_route["target_id"],
        "field_id": selected_route["field_id"],
        "gap_id": selected_route["gap_id"],
        "scientific_impact_score": selected_route["scientific_impact_score"],
        "execution_priority_score": selected_route["execution_priority_score"],
        "changed_node_ids": selected_route["changed_node_ids"],
        "expected_stale_node_ids": selected_route["stale_node_ids"],
        "expected_preserved_node_ids": selected_route["preserved_node_ids"],
        "selection_basis": "existing scientific impact 100 and highest recorded execution priority 28; no new score",
        "executed": False,
    }
    return {
        "schema_version": 1,
        "contract": REPLACEMENT_MAP_CONTRACT,
        "scope_id": scope_id,
        "revision": revision,
        "status": "claim_evidence_replacement_map_compiled",
        "source_bindings": bindings,
        "target_count": 2,
        "field_replacement_route_count": len(field_routes),
        "field_replacement_routes": field_routes,
        "exact_lineage_node_coverage_count": sum(
            bool(row["changed_node_ids"]) for row in field_routes
        ),
        "changeable_claim_count": len(changeable_claims),
        "claim_replacement_index": reverse_index,
        "verified_claim_preservation_count": len(verified_claims),
        "verified_claim_preservation_rules": preservation_rules,
        "selected_dry_run": selected,
        "selected_dry_run_field_id": selected["field_id"],
        "external_action_executed_count": 0,
        "evidence_admission_executed_count": 0,
        "model_mutation_executed_count": 0,
        "claim_transition_executed_count": 0,
        "history_deleted": False,
        "scientific_field_evidence_admitted": False,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "routing": {
            "next_internal_route": "simulate-high-impact-evidence-replacement",
            "pause_internal_work": False,
        },
        "next_action": (
            "Run a no-mutation dry-run for candidate-specific-route-inventory with a "
            "synthetic changed artifact hash; verify stale/preserved nodes and expected claim transitions."
        ),
    }


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def write_report(report: JsonObject, output: Path) -> None:
    if report.get("contract") != REPLACEMENT_MAP_CONTRACT:
        raise ValueError("claim evidence replacement map is required")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(_json_bytes(report))


def _read_json(path: Path) -> JsonObject:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "claim-matrix", "claim-binding", "evidence-manifest", "evidence-binding",
        "acquisition-package", "acquisition-binding", "lineage-report", "lineage-binding",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = compile_replacement_map(
        _read_json(args.claim_matrix), _read_json(args.claim_binding),
        _read_json(args.evidence_manifest), _read_json(args.evidence_binding),
        _read_json(args.acquisition_package), _read_json(args.acquisition_binding),
        _read_json(args.lineage_report), _read_json(args.lineage_binding),
    )
    write_report(report, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
