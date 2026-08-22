"""Compile lightweight scientific-gap progress and model invalidation lineage."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Dict, List, Sequence


JsonObject = Dict[str, object]

LINEAGE_CONTRACT = "lca_research_kernel.model_lineage_invalidation/v1"
COMPLETED_LOCAL_GAPS = (
    "mass-conserving-uncharacterized-outputs",
    "ton-unit-sensitivity",
)


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _artifact(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an artifact binding")
    path = _required_text(value.get("path"), f"{label}.path")
    sha256 = _required_text(value.get("sha256"), f"{label}.sha256")
    if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
        raise ValueError(f"{label}.sha256 must be a lowercase sha256")
    return {"path": path, "sha256": sha256}


def _node(
    node_id: str,
    kind: str,
    artifact: JsonObject,
    rebuild_action: str,
) -> JsonObject:
    return {
        "node_id": node_id,
        "kind": kind,
        "path": artifact["path"],
        "sha256": artifact["sha256"],
        "status": "current",
        "rebuild_action": rebuild_action,
    }


def evaluate_invalidation(
    graph: JsonObject, changed_node_ids: Sequence[str]
) -> JsonObject:
    """Return deterministic transitive descendants without mutating history."""

    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("invalidation graph is malformed")
    known = {
        _required_text(row.get("node_id"), "node_id")
        for row in nodes
        if isinstance(row, dict)
    }
    changed = sorted(set(changed_node_ids))
    if not changed or any(node_id not in known for node_id in changed):
        raise ValueError("changed_node_ids must resolve to current lineage nodes")
    children = {node_id: set() for node_id in known}
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("invalidation edge must be an object")
        upstream = _required_text(edge.get("upstream"), "edge.upstream")
        downstream = _required_text(edge.get("downstream"), "edge.downstream")
        if upstream not in known or downstream not in known:
            raise ValueError("invalidation edge references an unknown node")
        children[upstream].add(downstream)
    stale = set()
    frontier = list(changed)
    while frontier:
        current = frontier.pop(0)
        for child in sorted(children[current]):
            if child not in stale and child not in changed:
                stale.add(child)
                frontier.append(child)
    return {
        "changed_node_ids": changed,
        "stale_node_ids": sorted(stale),
        "history_deleted": False,
        "routing": [
            {
                "node_id": node_id,
                "rebuild_action": next(
                    row["rebuild_action"]
                    for row in nodes
                    if row["node_id"] == node_id
                ),
            }
            for node_id in sorted(stale)
        ],
    }


def compile_lineage_contract(
    base_register: JsonObject,
    bindings: JsonObject,
    completions: List[JsonObject],
    *,
    scope_id: str,
    revision: str,
) -> JsonObject:
    """Overlay completed gaps and compile the current content-addressed DAG."""

    scope = _required_text(scope_id, "scope_id")
    rev = _required_text(revision, "revision")
    ranking = base_register.get("execution_ranking")
    if not isinstance(ranking, list) or not ranking:
        raise ValueError("base register must contain an execution ranking")
    gap_ids = []
    for row in ranking:
        if not isinstance(row, dict):
            raise ValueError("scientific gap row must be an object")
        gap_id = _required_text(row.get("gap_id"), "gap_id")
        if gap_id in gap_ids:
            raise ValueError(f"duplicate scientific gap: {gap_id}")
        gap_ids.append(gap_id)
    completion_by_gap = {}
    for completion in completions:
        if not isinstance(completion, dict):
            raise ValueError("gap completion must be an object")
        gap_id = _required_text(completion.get("gap_id"), "completion.gap_id")
        if gap_id not in gap_ids or gap_id in completion_by_gap:
            raise ValueError("gap completion does not resolve uniquely")
        completion_by_gap[gap_id] = {
            "gap_id": gap_id,
            "decision_id": _required_text(
                completion.get("decision_id"), "completion.decision_id"
            ),
            "completion_artifact": _artifact(
                completion.get("completion_artifact"),
                "completion.completion_artifact",
            ),
            "status": "completed",
        }
    if set(completion_by_gap) != set(COMPLETED_LOCAL_GAPS):
        raise ValueError("lineage beat requires both completed local scientific slices")
    completed_gaps = [
        completion_by_gap[gap_id]
        for gap_id in gap_ids
        if gap_id in completion_by_gap
    ]
    remaining = [
        copy.deepcopy(row) for row in ranking if row["gap_id"] not in completion_by_gap
    ]
    ready_remaining = [
        row for row in remaining if row.get("actionability") == "ready"
    ]
    if ready_remaining:
        selected_scientific_gap = copy.deepcopy(ready_remaining[0])
        next_internal_route = {
            "route_id": "execute-next-ready-scientific-gap",
            "gap_id": selected_scientific_gap["gap_id"],
            "reason": "a source-bound scientific ticket remains locally executable",
        }
        routing_status = "ready_scientific_gap_selected"
    else:
        selected_scientific_gap = None
        next_internal_route = {
            "route_id": "current-lineage-invalidation-assurance",
            "gap_id": None,
            "reason": (
                "all remaining scientific gaps are evidence-blocked, while local "
                "lineage assurance is executable without inventing evidence"
            ),
        }
        routing_status = "no_ready_scientific_gap_internal_assurance_selected"

    base = _artifact(bindings.get("base_register"), "base_register")
    plan = _artifact(bindings.get("inventory_plan"), "inventory_plan")
    canonical = _artifact(
        bindings.get("canonical_reference_scenarios"),
        "canonical_reference_scenarios",
    )
    residual = bindings.get("residual_outputs")
    ton = bindings.get("ton_unit")
    if not isinstance(residual, dict) or not isinstance(ton, dict):
        raise ValueError("residual and ton-unit lineage bindings are required")
    residual_rows = _artifact(residual.get("scenario_rows"), "residual.scenario_rows")
    flow_validation = _artifact(
        residual.get("flow_validation"), "residual.flow_validation"
    )
    flow_qa = _artifact(residual.get("flow_qa"), "residual.flow_qa")
    ton_contract = _artifact(ton.get("compiled_contract"), "ton.compiled_contract")
    scenarios = ton.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != 2:
        raise ValueError("ton-unit lineage must contain exactly two scenarios")

    nodes = [
        _node(
            "scientific-gap-base-register",
            "routing_register",
            base,
            "recompile gap progress and re-evaluate affected scientific slices",
        ),
        _node(
            "inventory-plan",
            "source_bound_plan",
            plan,
            "recompile inventory evidence and all dependent energy interpretations",
        ),
        _node(
            "canonical-reference-scenarios",
            "process_scenarios",
            canonical,
            "re-run conservation residual derivation and downstream scenarios",
        ),
        _node(
            "residual-flow-validation",
            "native_validation",
            flow_validation,
            "revalidate residual Flow rows before process integration",
        ),
        _node(
            "residual-flow-qa",
            "native_qa",
            flow_qa,
            "rerun residual Flow QA before process integration",
        ),
        _node(
            "residual-scenarios",
            "process_scenarios",
            residual_rows,
            "reapply residual patches, Process validation and route QA",
        ),
        _node(
            "ton-unit-contract",
            "deterministic_interpretation_contract",
            ton_contract,
            "recompile both ton-unit patches and endpoint delta report",
        ),
    ]
    scenario_ids = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise ValueError("ton-unit scenario binding must be an object")
        scenario_id = _required_text(scenario.get("scenario_id"), "scenario_id")
        if scenario_id in scenario_ids:
            raise ValueError("duplicate ton-unit scenario binding")
        scenario_ids.add(scenario_id)
        nodes.extend(
            [
                _node(
                    f"ton-scenario:{scenario_id}",
                    "process_scenarios",
                    _artifact(scenario.get("rows"), f"{scenario_id}.rows"),
                    f"reapply the {scenario_id} patch from current upstream hashes",
                ),
                _node(
                    f"ton-validation:{scenario_id}",
                    "native_validation",
                    _artifact(
                        scenario.get("validation"), f"{scenario_id}.validation"
                    ),
                    f"rerun 2/2 Process validation for {scenario_id}",
                ),
            ]
        )
    if scenario_ids != {"reported-us-short-ton", "metric-tonne"}:
        raise ValueError("unexpected ton-unit scenario identities")

    edges = [
        {
            "upstream": "scientific-gap-base-register",
            "downstream": "residual-scenarios",
            "reason": "the residual slice was admitted by the base gap register",
        },
        {
            "upstream": "canonical-reference-scenarios",
            "downstream": "residual-scenarios",
            "reason": "accepted kg exchanges determine the conservation residual",
        },
        {
            "upstream": "residual-flow-validation",
            "downstream": "residual-scenarios",
            "reason": "residual process exchanges reference validated Flow rows",
        },
        {
            "upstream": "residual-flow-qa",
            "downstream": "residual-scenarios",
            "reason": "residual Flow semantics must remain QA-accepted",
        },
        {
            "upstream": "inventory-plan",
            "downstream": "ton-unit-contract",
            "reason": "the plan supplies both endpoint amounts and conversion constants",
        },
        {
            "upstream": "residual-scenarios",
            "downstream": "ton-unit-contract",
            "reason": "both ton interpretations start from the current residual rows",
        },
    ]
    for scenario_id in sorted(scenario_ids):
        edges.extend(
            [
                {
                    "upstream": "ton-unit-contract",
                    "downstream": f"ton-scenario:{scenario_id}",
                    "reason": "the scenario is materialized from this interpretation contract",
                },
                {
                    "upstream": f"ton-scenario:{scenario_id}",
                    "downstream": f"ton-validation:{scenario_id}",
                    "reason": "the validation report is target-bound to these scenario rows",
                },
            ]
        )
    graph = {
        "schema_version": 1,
        "contract": LINEAGE_CONTRACT,
        "scope_id": scope,
        "revision": rev,
        "status": "current_content_addressed_lineage_compiled",
        "nodes": nodes,
        "edges": edges,
        "policy": {
            "history_deleted_on_invalidation": False,
            "stale_artifacts_remain_addressable": True,
            "projection_marks_currentness": True,
            "transitive_descendants_only": True,
        },
        "change_routes": [
            {
                "change_class": "inventory_plan_content_changed",
                "changed_node_ids": ["inventory-plan"],
            },
            {
                "change_class": "canonical_process_content_changed",
                "changed_node_ids": ["canonical-reference-scenarios"],
            },
            {
                "change_class": "species_resolved_residual_evidence_arrived",
                "changed_node_ids": [
                    "residual-flow-validation",
                    "residual-flow-qa",
                ],
            },
            {
                "change_class": "reported_ton_interpretation_changed",
                "changed_node_ids": ["ton-unit-contract"],
            },
        ],
    }
    controlled_simulation = evaluate_invalidation(graph, ["ton-unit-contract"])
    return {
        "schema_version": 1,
        "contract": LINEAGE_CONTRACT,
        "scope_id": scope,
        "revision": rev,
        "status": "gap_progress_and_current_lineage_compiled",
        "gap_progress": {
            "base_register": base,
            "completed_gaps": completed_gaps,
            "remaining_scientific_ranking": remaining,
            "blocked_high_impact": copy.deepcopy(
                base_register.get("blocked_high_impact", [])
            ),
            "ready_scientific_gap_count": len(ready_remaining),
            "selected_scientific_gap": selected_scientific_gap,
            "routing_status": routing_status,
            "next_internal_route": next_internal_route,
            "scientific_comparison_ready": False,
        },
        "invalidation_graph": graph,
        "controlled_simulation": controlled_simulation,
        "next_action": (
            "Route the highest-impact blocked gaps through a lawful evidence-acquisition "
            "plan while retaining exact stale/rebuild routing and closed comparison gates."
        ),
    }


def _read_json(path: Path) -> JsonObject:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--register", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--completions", type=Path, required=True)
    parser.add_argument("--scope-id", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    completions = json.loads(args.completions.read_text(encoding="utf-8"))
    if not isinstance(completions, list):
        raise ValueError("completions must contain a JSON list")
    result = compile_lineage_contract(
        _read_json(args.register),
        _read_json(args.bindings),
        completions,
        scope_id=args.scope_id,
        revision=args.revision,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
