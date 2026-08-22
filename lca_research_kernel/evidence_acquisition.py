"""Compile lawful, source-bound evidence acquisition routes for blocked LCA gaps."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Dict, List, Sequence


JsonObject = Dict[str, object]

EVIDENCE_ACQUISITION_CONTRACT = (
    "lca_research_kernel.evidence_acquisition_manifest/v1"
)
KNOWN_LINEAGE_CHANGE_CLASSES = {
    "inventory_plan_content_changed",
    "canonical_process_content_changed",
    "species_resolved_residual_evidence_arrived",
    "reported_ton_interpretation_changed",
}
DEFAULT_CHANGE_NODES = {
    "inventory_plan_content_changed": ["inventory-plan"],
    "canonical_process_content_changed": ["canonical-reference-scenarios"],
    "species_resolved_residual_evidence_arrived": [
        "residual-flow-validation",
        "residual-flow-qa",
    ],
    "reported_ton_interpretation_changed": ["ton-unit-contract"],
}
HUMAN_EFFECT_TYPES = {
    "credential_or_payment",
    "author_contact",
    "contract_or_license_acceptance",
    "external_write",
}


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _text_list(value: object, label: str, *, allow_empty: bool = False) -> List[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"{label} must be a {'possibly empty ' if allow_empty else ''}list")
    result = []
    for index, item in enumerate(value):
        text = _required_text(item, f"{label}[{index}]")
        if text in result:
            raise ValueError(f"{label} contains duplicate value {text}")
        result.append(text)
    return result


def _artifact(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an artifact binding")
    path = _required_text(value.get("path"), f"{label}.path")
    sha256 = _required_text(value.get("sha256"), f"{label}.sha256")
    if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
        raise ValueError(f"{label}.sha256 must be a lowercase sha256")
    return {"path": path, "sha256": sha256}


def _compile_external_patterns(value: object) -> List[JsonObject]:
    if not isinstance(value, list) or not value:
        raise ValueError("external_patterns must contain at least one engineering pattern")
    result = []
    seen = set()
    for row in value:
        if not isinstance(row, dict):
            raise ValueError("external pattern must be an object")
        pattern_id = _required_text(row.get("pattern_id"), "pattern_id")
        if pattern_id in seen:
            raise ValueError(f"duplicate external pattern: {pattern_id}")
        seen.add(pattern_id)
        stars = row.get("stars_observed")
        if not isinstance(stars, int) or stars < 0:
            raise ValueError(f"{pattern_id}.stars_observed must be a non-negative integer")
        repository_url = _required_text(
            row.get("repository_url"), "repository_url"
        )
        result.append(
            {
                "pattern_id": pattern_id,
                "repository": _required_text(row.get("repository"), "repository"),
                "repository_url": repository_url,
                "metadata_source_url": _required_text(
                    row.get("metadata_source_url", repository_url),
                    "metadata_source_url",
                ),
                "stars_observed": stars,
                "observed_at": _required_text(row.get("observed_at"), "observed_at"),
                "engineering_mechanism": _required_text(
                    row.get("engineering_mechanism"), "engineering_mechanism"
                ),
                "local_landing": _required_text(
                    row.get("local_landing"), "local_landing"
                ),
                "migration_status": _required_text(
                    row.get("migration_status"), "migration_status"
                ),
                "non_migration_reason": _required_text(
                    row.get(
                        "non_migration_reason",
                        "Only the named engineering contract is migrated in this beat.",
                    ),
                    "non_migration_reason",
                ),
            }
        )
    return result


def _invalidation_preview(
    graph: JsonObject,
    change_classes: List[str],
    change_nodes: Dict[str, List[str]],
) -> JsonObject:
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    known_nodes = {
        row.get("node_id") for row in nodes if isinstance(row, dict)
    }
    children = {node_id: set() for node_id in known_nodes if isinstance(node_id, str)}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        upstream = edge.get("upstream")
        downstream = edge.get("downstream")
        if upstream in children and downstream in children:
            children[upstream].add(downstream)
    changed = sorted(
        {
            node_id
            for change_class in change_classes
            for node_id in change_nodes.get(change_class, [])
            if node_id in known_nodes
        }
    )
    stale = set()
    frontier = list(changed)
    while frontier:
        current = frontier.pop(0)
        for child in sorted(children.get(current, set())):
            if child not in changed and child not in stale:
                stale.add(child)
                frontier.append(child)
    return {
        "change_classes": change_classes,
        "changed_node_ids": changed,
        "stale_descendant_node_ids": sorted(stale),
        "history_deleted": False,
    }


def compile_evidence_acquisition_manifest(
    lineage: JsonObject,
    assessment: JsonObject,
    assessment_binding: JsonObject,
    *,
    scope_id: str,
    revision: str,
) -> JsonObject:
    """Compile blocked gaps into lawful public routes and human responsibility routes."""

    scope = _required_text(scope_id, "scope_id")
    rev = _required_text(revision, "revision")
    if lineage.get("scope_id") != scope or lineage.get("revision") != rev:
        raise ValueError("lineage does not match the requested scope and revision")
    if assessment.get("scope_id") != scope or assessment.get("revision") != rev:
        raise ValueError("assessment does not match the requested scope and revision")
    if lineage.get("status") != "gap_progress_and_current_lineage_compiled":
        raise ValueError("current accepted lineage is required")
    progress = lineage.get("gap_progress")
    if not isinstance(progress, dict):
        raise ValueError("lineage gap progress is missing")
    gaps = progress.get("remaining_scientific_ranking")
    if not isinstance(gaps, list) or not gaps:
        raise ValueError("remaining scientific gaps are required")
    gap_by_id = {}
    for row in gaps:
        if not isinstance(row, dict):
            raise ValueError("remaining scientific gap must be an object")
        gap_id = _required_text(row.get("gap_id"), "gap_id")
        if gap_id in gap_by_id:
            raise ValueError(f"duplicate remaining gap: {gap_id}")
        if row.get("actionability") != "blocked":
            raise ValueError("evidence acquisition may route only blocked gaps")
        gap_by_id[gap_id] = copy.deepcopy(row)

    route_rows = assessment.get("route_catalog")
    if not isinstance(route_rows, list) or not route_rows:
        raise ValueError("route_catalog must contain at least one route")
    route_by_id = {}
    for row in route_rows:
        if not isinstance(row, dict):
            raise ValueError("route catalog row must be an object")
        route_id = _required_text(row.get("route_id"), "route_id")
        if route_id in route_by_id:
            raise ValueError(f"duplicate acquisition route: {route_id}")
        effect_type = _required_text(row.get("effect_type"), f"{route_id}.effect_type")
        human_required = row.get("human_required")
        if not isinstance(human_required, bool):
            raise ValueError(f"{route_id}.human_required must be boolean")
        if (effect_type in HUMAN_EFFECT_TYPES) != human_required:
            raise ValueError(f"{route_id} effect type and human boundary disagree")
        route_by_id[route_id] = {
            "route_id": route_id,
            "source_class": _required_text(
                row.get("source_class"), f"{route_id}.source_class"
            ),
            "effect_type": effect_type,
            "human_required": human_required,
            "acceptance_output": _text_list(
                row.get("acceptance_output"), f"{route_id}.acceptance_output"
            ),
            "scientific_evidence_capability": _required_text(
                row.get("scientific_evidence_capability"),
                f"{route_id}.scientific_evidence_capability",
            ),
            "tool_or_adapter": _required_text(
                row.get("tool_or_adapter", route_id),
                f"{route_id}.tool_or_adapter",
            ),
            "current_status": _required_text(
                row.get("current_status", "ready"),
                f"{route_id}.current_status",
            ),
            "prerequisites": _text_list(
                row.get("prerequisites", []),
                f"{route_id}.prerequisites",
                allow_empty=True,
            ),
        }

    graph = lineage.get("invalidation_graph")
    if not isinstance(graph, dict):
        raise ValueError("lineage invalidation graph is missing")
    declared_change_classes = set(KNOWN_LINEAGE_CHANGE_CLASSES)
    change_nodes = copy.deepcopy(DEFAULT_CHANGE_NODES)
    for row in graph.get("change_routes", []):
        if isinstance(row, dict) and isinstance(row.get("change_class"), str):
            declared_change_classes.add(row["change_class"])
            node_ids = row.get("changed_node_ids")
            if isinstance(node_ids, list) and all(
                isinstance(node_id, str) for node_id in node_ids
            ):
                change_nodes[row["change_class"]] = list(node_ids)

    targets = assessment.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ValueError("targets must contain at least one source target")
    compiled_targets = []
    direct_gap_ids = set()
    target_ids = set()
    no_hit_count = 0
    for target in targets:
        if not isinstance(target, dict):
            raise ValueError("source target must be an object")
        target_id = _required_text(target.get("target_id"), "target_id")
        if target_id in target_ids:
            raise ValueError(f"duplicate source target: {target_id}")
        target_ids.add(target_id)
        identifiers = target.get("identifiers")
        if not isinstance(identifiers, dict) or not identifiers:
            raise ValueError(f"{target_id}.identifiers must not be empty")
        normalized_identifiers = {
            _required_text(key, f"{target_id}.identifier key"): _required_text(
                value, f"{target_id}.{key}"
            )
            for key, value in sorted(identifiers.items())
        }
        target_gap_ids = _text_list(target.get("gap_ids"), f"{target_id}.gap_ids")
        if any(gap_id not in gap_by_id for gap_id in target_gap_ids):
            raise ValueError(f"{target_id} references an unknown blocked gap")
        direct_gap_ids.update(target_gap_ids)
        route_ids = _text_list(target.get("route_ids"), f"{target_id}.route_ids")
        if any(route_id not in route_by_id for route_id in route_ids):
            raise ValueError(f"{target_id} references an unknown acquisition route")
        change_classes = _text_list(
            target.get("lineage_change_classes"),
            f"{target_id}.lineage_change_classes",
        )
        if any(value not in declared_change_classes for value in change_classes):
            raise ValueError(f"{target_id} references an unknown lineage change class")
        fields = target.get("required_fields")
        if not isinstance(fields, list) or not fields:
            raise ValueError(f"{target_id}.required_fields must not be empty")
        compiled_fields = []
        field_ids = set()
        for field in fields:
            if not isinstance(field, dict):
                raise ValueError(f"{target_id} required field must be an object")
            field_id = _required_text(field.get("field_id"), "field_id")
            if field_id in field_ids:
                raise ValueError(f"duplicate required field {field_id} in {target_id}")
            field_ids.add(field_id)
            compiled_fields.append(
                {
                    "field_id": field_id,
                    "accepted_evidence": _text_list(
                        field.get("accepted_evidence"),
                        f"{target_id}.{field_id}.accepted_evidence",
                    ),
                    "status": "unresolved",
                    "value_invented": False,
                }
            )
        observations = target.get("current_observations", [])
        if not isinstance(observations, list):
            raise ValueError(f"{target_id}.current_observations must be a list")
        compiled_observations = []
        for observation in observations:
            if not isinstance(observation, dict):
                raise ValueError(f"{target_id} observation must be an object")
            route_id = _required_text(observation.get("route_id"), "observation.route_id")
            if route_id not in route_ids:
                raise ValueError(f"{target_id} observation route was not declared")
            status = _required_text(observation.get("status"), "observation.status")
            is_no_hit = status.startswith("no_") or status.endswith("_not_found")
            no_hit_count += int(is_no_hit)
            compiled_observations.append(
                {
                    "route_id": route_id,
                    "status": status,
                    "observed_at": _required_text(
                        observation.get("observed_at"), "observation.observed_at"
                    ),
                    "source_url": _required_text(
                        observation.get("source_url"), "observation.source_url"
                    ),
                    "facts": copy.deepcopy(observation.get("facts", {})),
                    "scientific_absence_proven": False,
                    "scope": "route_and_date_bounded_discovery_result",
                }
            )
        compiled_targets.append(
            {
                "target_id": target_id,
                "title": _required_text(target.get("title"), f"{target_id}.title"),
                "identifiers": normalized_identifiers,
                "gap_ids": target_gap_ids,
                "required_fields": compiled_fields,
                "route_ids": route_ids,
                "lineage_change_classes": change_classes,
                "lineage_invalidation_preview": _invalidation_preview(
                    graph, change_classes, change_nodes
                ),
                "current_observations": compiled_observations,
                "acquisition_status": "required_fields_unresolved",
            }
        )

    dependencies = assessment.get("dependency_only_gaps", [])
    if not isinstance(dependencies, list):
        raise ValueError("dependency_only_gaps must be a list")
    dependency_by_gap = {}
    for row in dependencies:
        if not isinstance(row, dict):
            raise ValueError("dependency-only gap route must be an object")
        gap_id = _required_text(row.get("gap_id"), "dependency gap_id")
        if gap_id not in gap_by_id or gap_id in dependency_by_gap:
            raise ValueError("dependency-only route references an unknown or duplicate gap")
        prerequisites = _text_list(
            row.get("prerequisite_gap_ids"), f"{gap_id}.prerequisite_gap_ids"
        )
        if any(value not in gap_by_id or value == gap_id for value in prerequisites):
            raise ValueError(f"{gap_id} has invalid prerequisite gaps")
        dependency_by_gap[gap_id] = {
            "prerequisite_gap_ids": prerequisites,
            "reason": _required_text(row.get("reason"), f"{gap_id}.reason"),
        }
    covered_gap_ids = direct_gap_ids | set(dependency_by_gap)
    if covered_gap_ids != set(gap_by_id):
        raise ValueError("every blocked gap must have a direct target or dependency route")

    gap_routes = []
    for gap in sorted(
        gap_by_id.values(),
        key=lambda row: (
            -float(row.get("scientific_impact_score", 0)),
            -float(row.get("execution_priority_score", 0)),
            row["gap_id"],
        ),
    ):
        gap_id = gap["gap_id"]
        dependency = dependency_by_gap.get(gap_id)
        gap_routes.append(
            {
                "gap_id": gap_id,
                "scientific_impact_score": gap.get("scientific_impact_score"),
                "execution_priority_score": gap.get("execution_priority_score"),
                "blockers": copy.deepcopy(gap.get("blockers", [])),
                "route_type": "dependency_only" if dependency else "source_acquisition",
                "target_ids": [
                    target["target_id"]
                    for target in compiled_targets
                    if gap_id in target["gap_ids"]
                ],
                "prerequisite_gap_ids": (
                    dependency["prerequisite_gap_ids"] if dependency else []
                ),
                "status": "blocked_pending_source_bound_evidence",
            }
        )

    public_routes = sorted(
        route_id
        for route_id, row in route_by_id.items()
        if not row["human_required"] and row["current_status"] == "ready"
    )
    human_routes = sorted(
        route_id for route_id, row in route_by_id.items() if row["human_required"]
    )
    deferred_routes = sorted(
        route_id
        for route_id, row in route_by_id.items()
        if not row["human_required"] and row["current_status"] != "ready"
    )
    next_internal_route = (
        "execute-public-evidence-discovery"
        if public_routes
        else "compile-local-evidence-extraction"
    )
    return {
        "schema_version": 1,
        "contract": EVIDENCE_ACQUISITION_CONTRACT,
        "scope_id": scope,
        "revision": rev,
        "status": "lawful_evidence_acquisition_manifest_compiled",
        "observed_at": _required_text(assessment.get("observed_at"), "observed_at"),
        "assessment": _artifact(assessment_binding, "assessment_binding"),
        "lineage_contract": _required_text(lineage.get("contract"), "lineage.contract"),
        "blocked_gap_count": len(gap_by_id),
        "source_target_count": len(compiled_targets),
        "gap_routes": gap_routes,
        "source_targets": compiled_targets,
        "route_catalog": [route_by_id[key] for key in sorted(route_by_id)],
        "external_patterns": _compile_external_patterns(
            assessment.get("external_patterns")
        ),
        "evidence_object_model": {
            "source_item": "canonical identifier and bibliographic metadata",
            "attachment": "lawful acquired bytes plus sha256 and access basis",
            "parse_report": "parser identity, version, attachment hash and structured output",
            "extraction": "field, value, unit, page/span, method and source attachment",
            "field_decision": "accepted/rejected/missing status plus validation rationale",
            "separation_rule": "metadata discovery, acquisition, parsing, extraction and acceptance are distinct states",
        },
        "no_hit_policy": {
            "count": no_hit_count,
            "meaning": "a dated route-specific discovery result, never proof that scientific evidence does not exist",
            "retry_trigger": "new location, repository, source version, contact result, or explicit refresh decision",
        },
        "routing": {
            "public_routes": public_routes,
            "human_routes": human_routes,
            "deferred_routes": deferred_routes,
            "next_internal_route": next_internal_route,
            "pause_internal_work": False,
            "human_boundary": "credentials, payment, license acceptance, or outbound contact only",
        },
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "values_invented": False,
        "next_action": (
            "Execute and hash normalized public discovery reports for every DOI; retain "
            "no-hit observations and continue local extraction-contract work while human "
            "credential, payment, or contact routes remain optional responsibility actions."
        ),
    }


def _read_json(path: Path) -> JsonObject:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def main(argv: Sequence[str] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lineage", type=Path, required=True)
    parser.add_argument("--assessment", type=Path, required=True)
    parser.add_argument("--assessment-binding", type=Path, required=True)
    parser.add_argument("--scope-id", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    result = compile_evidence_acquisition_manifest(
        _read_json(args.lineage),
        _read_json(args.assessment),
        _read_json(args.assessment_binding),
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
