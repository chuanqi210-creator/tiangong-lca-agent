"""Simulate evidence replacement invalidation without admitting evidence or mutating models."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set


JsonObject = Dict[str, object]
DRY_RUN_CONTRACT = "lca_research_kernel.evidence_replacement_dry_run/v1"
REPLACEMENT_MAP_CONTRACT = (
    "lca_research_kernel.claim_evidence_replacement_map/v1"
)
CLAIM_MATRIX_CONTRACT = (
    "lca_research_kernel.route_model_assurance_claim_matrix/v1"
)
LINEAGE_CONTRACT = "lca_research_kernel.model_lineage_invalidation/v1"
SELECTED_FIELD_ID = "candidate-specific-route-inventory"

CLAIM_INTENTIONS = {
    "conditional_screening_fact": "revalidate_before_retain_or_replace",
    "unresolved_evidence_dependency": (
        "eligible_for_release_review_only_after_admission_and_rebuild"
    ),
    "prohibited_claim": "remain_prohibited_until_all_release_conditions_pass",
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


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


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


def _topological_rebuild_order(
    stale: Set[str], adjacency: Dict[str, Set[str]]
) -> List[str]:
    indegree = {node_id: 0 for node_id in stale}
    for upstream in stale:
        for downstream in adjacency.get(upstream, set()):
            if downstream in stale:
                indegree[downstream] += 1
    queue = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    ordered: List[str] = []
    while queue:
        current = queue.popleft()
        ordered.append(current)
        for downstream in sorted(adjacency.get(current, set())):
            if downstream not in indegree:
                continue
            indegree[downstream] -= 1
            if indegree[downstream] == 0:
                queue.append(downstream)
        queue = deque(sorted(queue))
    if len(ordered) != len(stale):
        raise ValueError("stale lineage subgraph must be acyclic")
    return ordered


def compile_replacement_dry_run(
    replacement_map: JsonObject,
    replacement_binding: JsonObject,
    claim_matrix: JsonObject,
    claim_binding: JsonObject,
    lineage_report: JsonObject,
    lineage_binding: JsonObject,
) -> JsonObject:
    """Derive a no-mutation evidence-replacement test vector and its effects."""

    if (
        replacement_map.get("contract") != REPLACEMENT_MAP_CONTRACT
        or replacement_map.get("status")
        != "claim_evidence_replacement_map_compiled"
        or replacement_map.get("field_replacement_route_count") != 15
        or replacement_map.get("scientific_comparison_ready") is not False
        or replacement_map.get("comparative_lcia_allowed") is not False
        or replacement_map.get("external_action_executed_count") != 0
        or replacement_map.get("evidence_admission_executed_count") != 0
        or replacement_map.get("model_mutation_executed_count") != 0
        or replacement_map.get("claim_transition_executed_count") != 0
    ):
        raise ValueError("accepted claim evidence replacement map is required")
    routing = replacement_map.get("routing")
    if (
        not isinstance(routing, dict)
        or routing.get("next_internal_route")
        != "simulate-high-impact-evidence-replacement"
        or routing.get("pause_internal_work") is not False
    ):
        raise ValueError("replacement map does not route to the dry-run")
    if (
        claim_matrix.get("contract") != CLAIM_MATRIX_CONTRACT
        or claim_matrix.get("status") != "route_model_assurance_claim_matrix_compiled"
        or claim_matrix.get("claim_count") != 26
        or claim_matrix.get("scientific_comparison_ready") is not False
        or claim_matrix.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("accepted claim matrix is required")
    graph = lineage_report.get("invalidation_graph")
    if (
        lineage_report.get("contract") != LINEAGE_CONTRACT
        or lineage_report.get("status") != "gap_progress_and_current_lineage_compiled"
        or not isinstance(graph, dict)
        or graph.get("status") != "current_content_addressed_lineage_compiled"
    ):
        raise ValueError("accepted lineage report is required")

    scopes = {
        _scope(replacement_map, "replacement map"),
        _scope(claim_matrix, "claim matrix"),
        _scope(lineage_report, "lineage report"),
    }
    if len(scopes) != 1:
        raise ValueError("replacement dry-run source scope conflicts")
    scope_id, revision = next(iter(scopes))
    bindings = {
        "replacement_map": _artifact(replacement_binding, "replacement binding"),
        "claim_matrix": _artifact(claim_binding, "claim binding"),
        "lineage_report": _artifact(lineage_binding, "lineage binding"),
    }

    selected = replacement_map.get("selected_dry_run")
    if not isinstance(selected, dict):
        raise ValueError("selected dry-run field is missing")
    selected_field_id = replacement_map.get("selected_dry_run_field_id")
    if (
        selected_field_id != SELECTED_FIELD_ID
        or selected.get("field_id") != SELECTED_FIELD_ID
        or selected.get("executed") is not False
    ):
        raise ValueError("selected dry-run field does not match the accepted route")
    routes = replacement_map.get("field_replacement_routes")
    if not isinstance(routes, list) or len(routes) != 15:
        raise ValueError("15 replacement routes are required")
    selected_routes = [
        row
        for row in routes
        if isinstance(row, dict) and row.get("field_id") == SELECTED_FIELD_ID
    ]
    if len(selected_routes) != 1:
        raise ValueError("selected dry-run field must have exactly one replacement route")
    selected_route = selected_routes[0]
    if (
        selected_route.get("status") != "replacement_route_compiled_not_executed"
        or selected_route.get("evidence_admitted") is not False
        or selected_route.get("model_mutation_executed") is not False
        or selected_route.get("claim_released") is not False
    ):
        raise ValueError("selected dry-run field route is not safe to simulate")

    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or len(nodes) != 11:
        raise ValueError("11 current lineage nodes are required")
    node_rows: Dict[str, JsonObject] = {}
    for row in nodes:
        if not isinstance(row, dict) or row.get("status") != "current":
            raise ValueError("11 current lineage nodes are required")
        node_id = _required_text(row.get("node_id"), "lineage node_id")
        if node_id in node_rows:
            raise ValueError("lineage node IDs must be unique")
        node_rows[node_id] = row
    if not isinstance(edges, list) or len(edges) != 10:
        raise ValueError("10 current lineage edges are required")
    adjacency: Dict[str, Set[str]] = defaultdict(set)
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("lineage edge is malformed")
        upstream = _required_text(edge.get("upstream"), "lineage edge.upstream")
        downstream = _required_text(edge.get("downstream"), "lineage edge.downstream")
        if upstream not in node_rows or downstream not in node_rows:
            raise ValueError("lineage edge references a missing node")
        adjacency[upstream].add(downstream)

    changed = sorted(
        _required_text(value, "changed node ID")
        for value in selected_route.get("changed_node_ids", [])
    )
    if not changed or not set(changed).issubset(node_rows):
        raise ValueError("selected dry-run changed nodes lack exact lineage coverage")
    stale = _descendants(changed, adjacency)
    preserved = sorted(set(node_rows) - set(stale))
    expected_stale = sorted(selected.get("expected_stale_node_ids", []))
    expected_preserved = sorted(selected.get("expected_preserved_node_ids", []))
    route_stale = sorted(selected_route.get("stale_node_ids", []))
    route_preserved = sorted(selected_route.get("preserved_node_ids", []))
    if (
        stale != expected_stale
        or preserved != expected_preserved
        or stale != route_stale
        or preserved != route_preserved
    ):
        raise ValueError("expected stale/preserved sets drifted from the current lineage graph")
    if sorted(selected.get("changed_node_ids", [])) != changed:
        raise ValueError("selected dry-run changed nodes drifted from the field route")

    claims = claim_matrix.get("claims")
    if not isinstance(claims, list) or len(claims) != 26:
        raise ValueError("26 current claims are required")
    claims_by_id: Dict[str, JsonObject] = {}
    for claim in claims:
        if not isinstance(claim, dict):
            raise ValueError("claim is malformed")
        claim_id = _required_text(claim.get("claim_id"), "claim_id")
        if claim_id in claims_by_id:
            raise ValueError("claim IDs must be unique")
        claims_by_id[claim_id] = claim
    affected_ids = sorted(selected_route.get("affected_claim_ids", []))
    if len(affected_ids) != 10 or not set(affected_ids).issubset(claims_by_id):
        raise ValueError("selected route must bind exactly 10 current claims")
    intentions = []
    intention_counts: Dict[str, int] = defaultdict(int)
    for claim_id in affected_ids:
        claim = claims_by_id[claim_id]
        claim_class = _required_text(claim.get("claim_class"), "claim_class")
        if claim_class not in CLAIM_INTENTIONS:
            raise ValueError("selected route includes a non-transition claim class")
        intention = CLAIM_INTENTIONS[claim_class]
        release_conditions = claim.get("release_conditions")
        if not isinstance(release_conditions, list):
            raise ValueError("claim release conditions are malformed")
        intentions.append(
            {
                "claim_id": claim_id,
                "claim_class": claim_class,
                "intention": intention,
                "release_conditions": release_conditions,
                "transition_executed": False,
            }
        )
        intention_counts[intention] += 1

    rules = replacement_map.get("verified_claim_preservation_rules")
    if not isinstance(rules, list) or len(rules) != 8:
        raise ValueError("eight verified claim preservation rules are required")
    revalidation = []
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("verified claim preservation rule is malformed")
        claim_id = _required_text(rule.get("claim_id"), "verified claim_id")
        trigger_fields = rule.get("revalidation_trigger_field_ids")
        if (
            claim_id not in claims_by_id
            or claims_by_id[claim_id].get("claim_class") != "verified_executable_fact"
            or not isinstance(trigger_fields, list)
        ):
            raise ValueError("verified claim preservation rule is invalid")
        requires_revalidation = SELECTED_FIELD_ID in trigger_fields
        revalidation.append(
            {
                "claim_id": claim_id,
                "intention": (
                    "requires_revalidation"
                    if requires_revalidation
                    else "preserved_current"
                ),
                "history_deleted": False,
                "transition_executed": False,
            }
        )
    verified_revalidation_count = sum(
        row["intention"] == "requires_revalidation" for row in revalidation
    )

    synthetic_payload = {
        "contract": DRY_RUN_CONTRACT,
        "classification": "non_evidence_test_vector",
        "scope_id": scope_id,
        "revision": revision,
        "field_id": SELECTED_FIELD_ID,
        "replacement_map_sha256": bindings["replacement_map"]["sha256"],
        "lineage_report_sha256": bindings["lineage_report"]["sha256"],
    }
    synthetic_sha256 = hashlib.sha256(_json_bytes(synthetic_payload)).hexdigest()
    ordered_rebuild = _topological_rebuild_order(set(stale), adjacency)
    expected_counts = {
        "eligible_for_release_review_only_after_admission_and_rebuild": 4,
        "remain_prohibited_until_all_release_conditions_pass": 4,
        "revalidate_before_retain_or_replace": 2,
    }
    if dict(sorted(intention_counts.items())) != expected_counts:
        raise ValueError("selected route claim intention classes drifted")
    if verified_revalidation_count != 8:
        raise ValueError("all eight verified claims must be revalidated for this field")

    return {
        "schema_version": 1,
        "contract": DRY_RUN_CONTRACT,
        "scope_id": scope_id,
        "revision": revision,
        "status": "synthetic_evidence_replacement_dry_run_passed",
        "source_bindings": bindings,
        "selected_field_id": SELECTED_FIELD_ID,
        "synthetic_change": {
            "sha256": synthetic_sha256,
            "classification": "non_evidence_test_vector",
            "field_id": SELECTED_FIELD_ID,
            "admitted_as_evidence": False,
        },
        "node_simulation": {
            "changed_node_ids": changed,
            "stale_node_ids": stale,
            "preserved_node_ids": preserved,
            "expected_sets_match": True,
            "current_node_partition_valid": set(stale).isdisjoint(preserved)
            and set(stale) | set(preserved) == set(node_rows),
            "history_deleted": False,
        },
        "ordered_rebuild_node_ids": ordered_rebuild,
        "claim_transition_intentions": intentions,
        "affected_claim_count": len(intentions),
        "claim_transition_intention_counts": expected_counts,
        "verified_claim_revalidation": revalidation,
        "verified_claim_revalidation_count": verified_revalidation_count,
        "external_action_executed_count": 0,
        "evidence_admission_executed_count": 0,
        "model_mutation_executed_count": 0,
        "claim_transition_executed_count": 0,
        "history_deleted": False,
        "scientific_field_evidence_admitted": False,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "routing": {
            "next_internal_route": "compile-evidence-intake-validation-harness",
            "pause_internal_work": False,
        },
        "next_action": (
            "Compile a fail-closed evidence intake validation harness for target-bound "
            "field artifacts before any admission, rebuild, or claim transition."
        ),
    }


def write_report(report: JsonObject, output: Path) -> None:
    if report.get("contract") != DRY_RUN_CONTRACT:
        raise ValueError("evidence replacement dry-run report is required")
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
        "replacement-map",
        "replacement-binding",
        "claim-matrix",
        "claim-binding",
        "lineage-report",
        "lineage-binding",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = compile_replacement_dry_run(
        _read_json(args.replacement_map),
        _read_json(args.replacement_binding),
        _read_json(args.claim_matrix),
        _read_json(args.claim_binding),
        _read_json(args.lineage_report),
        _read_json(args.lineage_binding),
    )
    write_report(report, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
