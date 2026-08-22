"""Compile and verify precise TianGong authority transitions."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Dict, List, Mapping, Sequence, Tuple

from .tiangong_authority import verify_authority_receipt


JsonObject = Dict[str, object]

AUTHORITY_TRANSITION_CONTRACT = (
    "lca_research_kernel.tiangong_authority_transition/v1"
)
AUTHORITY_DEPENDENCY_GRAPH_CONTRACT = (
    "lca_research_kernel.tiangong_authority_dependency_graph/v1"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EVIDENCE_KINDS = {"official_version_pair", "synthetic_binding_canary"}
_DERIVED_RECEIPT_FIELDS = {"receipt_id", "receipt_content_sha256"}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return copy.deepcopy(value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _safe_path(value: object, label: str) -> str:
    token = _text(value, label).replace("\\", "/")
    path = PurePosixPath(token)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{label} must be a normalized relative path")
    return str(path)


def _normalize_graph(value: object) -> Tuple[JsonObject, List[str]]:
    graph = _object(value, "dependency_graph")
    if graph.get("contract") != AUTHORITY_DEPENDENCY_GRAPH_CONTRACT:
        raise ValueError("dependency graph contract is unsupported")
    node_values = graph.get("nodes")
    if not isinstance(node_values, list) or not node_values:
        raise ValueError("dependency graph nodes are required")
    nodes = []
    seen = set()
    for index, value_row in enumerate(node_values):
        row = _object(value_row, f"nodes[{index}]")
        node_id = _text(row.get("node_id"), f"nodes[{index}].node_id")
        if node_id in seen:
            raise ValueError(f"duplicate dependency node: {node_id}")
        seen.add(node_id)
        artifact = _object(row.get("artifact"), f"{node_id}.artifact")
        sha256 = _text(artifact.get("sha256"), f"{node_id}.artifact.sha256")
        if not _SHA256.fullmatch(sha256):
            raise ValueError(f"{node_id}.artifact.sha256 must be a lowercase SHA-256")
        dependencies = row.get("depends_on_nodes")
        fields = row.get("depends_on_authority_fields")
        if not isinstance(dependencies, list) or not all(
            isinstance(item, str) and item for item in dependencies
        ):
            raise ValueError(f"{node_id}.depends_on_nodes must be a string list")
        if not isinstance(fields, list) or not all(
            isinstance(item, str) and item for item in fields
        ):
            raise ValueError(
                f"{node_id}.depends_on_authority_fields must be a string list"
            )
        if len(dependencies) != len(set(dependencies)) or len(fields) != len(set(fields)):
            raise ValueError(f"{node_id} dependency declarations contain duplicates")
        nodes.append(
            {
                "node_id": node_id,
                "artifact": {
                    "path": _safe_path(artifact.get("path"), f"{node_id}.artifact.path"),
                    "sha256": sha256,
                },
                "depends_on_nodes": sorted(dependencies),
                "depends_on_authority_fields": sorted(fields),
                "rebuild_action": _text(
                    row.get("rebuild_action"), f"{node_id}.rebuild_action"
                ),
            }
        )
    by_id = {row["node_id"]: row for row in nodes}
    for row in nodes:
        missing = sorted(set(row["depends_on_nodes"]) - set(by_id))
        if missing:
            raise ValueError(
                f"dependency node {row['node_id']} refers to missing nodes: {missing}"
            )
    order = _topological_order(by_id)
    return {
        "contract": AUTHORITY_DEPENDENCY_GRAPH_CONTRACT,
        "nodes": sorted(nodes, key=lambda row: str(row["node_id"])),
    }, order


def _topological_order(by_id: Mapping[str, Mapping[str, object]]) -> List[str]:
    indegree = {
        node_id: len(row["depends_on_nodes"]) for node_id, row in by_id.items()
    }
    children: Dict[str, List[str]] = {node_id: [] for node_id in by_id}
    for node_id, row in by_id.items():
        for dependency in row["depends_on_nodes"]:
            children[str(dependency)].append(node_id)
    ready = sorted(node_id for node_id, degree in indegree.items() if degree == 0)
    order = []
    while ready:
        node_id = ready.pop(0)
        order.append(node_id)
        for child in sorted(children[node_id]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort()
    if len(order) != len(by_id):
        raise ValueError("dependency graph contains a cycle")
    return order


def _unwrap_current_receipt(
    value: Mapping[str, object], evidence_kind: str
) -> Tuple[JsonObject, str, bool]:
    row = _object(value, "current_receipt")
    if evidence_kind == "synthetic_binding_canary":
        if row.get("origin") != "synthetic_test_receipt":
            raise ValueError("synthetic canary requires synthetic_test_receipt origin")
        if row.get("official_update_claim") is not False:
            raise ValueError("synthetic canary must set official_update_claim=false")
        receipt = verify_authority_receipt(
            _object(row.get("receipt"), "synthetic current receipt")
        )
        return receipt, "synthetic_test_receipt", False
    if "origin" in row or "receipt" in row:
        raise ValueError("official version pair must use an unwrapped Authority Receipt")
    return verify_authority_receipt(row), "official_authority_receipt", True


def _flatten(value: object, prefix: str = "") -> Dict[str, object]:
    if isinstance(value, Mapping):
        result: Dict[str, object] = {}
        for key in sorted(value, key=str):
            if not prefix and key in _DERIVED_RECEIPT_FIELDS:
                continue
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            result.update(_flatten(value[key], child_prefix))
        return result
    return {prefix: copy.deepcopy(value)}


def _classification(field: str) -> str:
    if field.startswith("authority.functional_unit"):
        return "data_value"
    if field.startswith(
        (
            "authority.dataset",
            "authority.model",
            "authority.reference_flow",
        )
    ):
        return "identity_reference"
    if field.startswith("authority.provider_snapshot"):
        return "provider_graph"
    if field.startswith("authority.lcia_method"):
        return "lcia_method"
    if field.startswith("authority.tidas_schema"):
        return "schema"
    if field.startswith(("authority.tool_runtime", "capability_adoption")):
        return "tool_api_behavior"
    return "metadata_only"


def _changes(previous: Mapping[str, object], current: Mapping[str, object]) -> List[JsonObject]:
    left = _flatten(previous)
    right = _flatten(current)
    rows = []
    for field in sorted(set(left) | set(right)):
        if left.get(field) == right.get(field):
            continue
        classification = _classification(field)
        rows.append(
            {
                "field": field,
                "previous": copy.deepcopy(left.get(field)),
                "current": copy.deepcopy(right.get(field)),
                "classification": classification,
                "governed_output_change": classification != "metadata_only",
            }
        )
    return rows


def _field_matches(change_field: str, governed_field: str) -> bool:
    return change_field == governed_field or change_field.startswith(
        f"{governed_field}."
    )


def compile_authority_transition(
    *,
    previous_receipt: Mapping[str, object],
    current_receipt: Mapping[str, object],
    dependency_graph: Mapping[str, object],
    transition_evidence_kind: str,
) -> JsonObject:
    """Diff exact authority leaves and compute the minimal transitive rebuild set."""

    evidence_kind = _text(transition_evidence_kind, "transition_evidence_kind")
    if evidence_kind not in _EVIDENCE_KINDS:
        raise ValueError("transition evidence kind is unsupported")
    previous = verify_authority_receipt(previous_receipt)
    current, origin, official_claim = _unwrap_current_receipt(
        current_receipt, evidence_kind
    )
    if evidence_kind == "official_version_pair":
        predecessor = current["authority"].get("predecessor")
        if not isinstance(predecessor, dict) or predecessor.get("receipt_id") != previous[
            "receipt_id"
        ]:
            raise ValueError(
                "official version pair current receipt predecessor must join previous receipt"
            )
    graph, topological_order = _normalize_graph(dependency_graph)
    by_id = {row["node_id"]: row for row in graph["nodes"]}
    changes = _changes(previous, current)
    governed_fields = [
        str(row["field"])
        for row in changes
        if row["classification"] != "metadata_only"
    ]
    directly_invalidated = sorted(
        row["node_id"]
        for row in graph["nodes"]
        if any(
            _field_matches(field, dependency)
            for field in governed_fields
            for dependency in row["depends_on_authority_fields"]
        )
    )
    invalidated = set(directly_invalidated)
    for node_id in topological_order:
        row = by_id[node_id]
        if any(dependency in invalidated for dependency in row["depends_on_nodes"]):
            invalidated.add(node_id)
    invalidated_order = [node_id for node_id in topological_order if node_id in invalidated]
    unaffected = sorted(set(by_id) - invalidated)
    rebuild_actions = [
        {
            "order": index,
            "node_id": node_id,
            "rebuild_action": by_id[node_id]["rebuild_action"],
            "previous_artifact": by_id[node_id]["artifact"],
            "depends_on_nodes": by_id[node_id]["depends_on_nodes"],
        }
        for index, node_id in enumerate(invalidated_order, start=1)
    ]
    preserved_artifacts = [
        {"node_id": node_id, "artifact": by_id[node_id]["artifact"]}
        for node_id in unaffected
    ]
    if not changes:
        status = "no_change"
    elif not invalidated_order:
        status = "metadata_only_change"
    else:
        status = "transition_required"
    body: JsonObject = {
        "contract": AUTHORITY_TRANSITION_CONTRACT,
        "status": status,
        "transition_evidence_kind": evidence_kind,
        "current_receipt_origin": origin,
        "official_update_claim": official_claim,
        "canary_isolated": evidence_kind == "synthetic_binding_canary",
        "current_authority_advance_permitted": (
            evidence_kind == "official_version_pair" and status != "transition_required"
        ),
        "previous_receipt_id": previous["receipt_id"],
        "previous_receipt_sha256": previous["receipt_content_sha256"],
        "current_receipt_id": current["receipt_id"],
        "current_receipt_sha256": current["receipt_content_sha256"],
        "previous_receipt": previous,
        "current_receipt": current,
        "dependency_graph_sha256": _digest(graph),
        "changes": changes,
        "change_classes": sorted(
            {str(row["classification"]) for row in changes}
        ),
        "directly_invalidated_nodes": directly_invalidated,
        "invalidated_nodes": invalidated_order,
        "unaffected_nodes": unaffected,
        "preserved_artifacts": preserved_artifacts,
        "rebuild_actions": rebuild_actions,
        "topological_order": topological_order,
        "remote_read_executed_count": 0,
        "remote_write_executed_count": 0,
        "calculation_trigger_executed_count": 0,
        "publication_executed_count": 0,
        "next_route": (
            "execute-exact-authority-rebuild-actions"
            if invalidated_order
            else "assemble-tiangong-second-case-package"
        ),
    }
    digest = _digest(body)
    body["transition_id"] = digest[:16]
    body["transition_content_sha256"] = digest
    return body


def verify_authority_transition(
    transition: Mapping[str, object], dependency_graph: Mapping[str, object]
) -> JsonObject:
    """Recompile a transition from its embedded receipts and reject any drift."""

    row = _object(transition, "transition")
    if row.get("contract") != AUTHORITY_TRANSITION_CONTRACT:
        raise ValueError("authority transition contract is unsupported")
    evidence_kind = _text(
        row.get("transition_evidence_kind"), "transition_evidence_kind"
    )
    current = _object(row.get("current_receipt"), "transition.current_receipt")
    if evidence_kind == "synthetic_binding_canary":
        current_input: Mapping[str, object] = {
            "origin": row.get("current_receipt_origin"),
            "official_update_claim": row.get("official_update_claim"),
            "receipt": current,
        }
    else:
        current_input = current
    canonical = compile_authority_transition(
        previous_receipt=_object(
            row.get("previous_receipt"), "transition.previous_receipt"
        ),
        current_receipt=current_input,
        dependency_graph=dependency_graph,
        transition_evidence_kind=evidence_kind,
    )
    if canonical != row:
        raise ValueError("authority transition is not canonical or has been tampered")
    return copy.deepcopy(row)


def transition_content_sha256(transition: Mapping[str, object]) -> str:
    """Return the governed content digest without trusting recorded identity fields."""

    row = _object(transition, "transition")
    row.pop("transition_id", None)
    row.pop("transition_content_sha256", None)
    return _digest(row)
