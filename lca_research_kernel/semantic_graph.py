"""Deterministic, source-bound semantic graph values for D-18 research context.

The graph is a rebuildable projection.  It deliberately has no Project Record
write surface and no persistence policy beyond canonical serialization.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Iterable, Mapping, Optional, Set


GRAPH_CONTRACT = "lca_research_kernel.research_semantic_graph/v1"
REGISTRY_CONTRACT = "lca_research_kernel.research_semantic_graph_registry/v1"
DEFAULT_REGISTRY = (
    Path(__file__).resolve().parent
    / "contracts"
    / "research-semantic-graph-v1.json"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
NODE_REQUIRED_FIELDS = {
    "node_id",
    "namespace",
    "node_kind",
    "label",
    "source_ref",
    "source_sha256",
    "source_version",
    "authority_class",
    "classification",
    "project_id",
    "generation",
    "valid_from",
    "valid_to",
    "attributes",
}
EDGE_REQUIRED_FIELDS = {
    "edge_id",
    "edge_kind",
    "source_id",
    "target_id",
    "source_ref",
    "source_sha256",
    "source_version",
    "authority_class",
    "classification",
    "project_id",
    "generation",
    "valid_from",
    "valid_to",
    "derivation",
}


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _read_registry(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("semantic graph registry is unavailable or invalid") from error
    if not isinstance(value, dict) or value.get("contract") != REGISTRY_CONTRACT:
        raise ValueError("semantic graph registry contract is unsupported")
    for key in (
        "namespaces",
        "authority_classes",
        "classifications",
        "accepted_path_authorities",
        "accepted_path_classifications",
        "edge_registry",
    ):
        if not isinstance(value.get(key), (dict, list)):
            raise ValueError(f"semantic graph registry {key} is invalid")
    return value


def _require_text(value: Mapping[str, object], key: str, label: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{label} {key} is absent")
    return item


def _require_scope(
    value: Mapping[str, object], *, project_id: str, generation: int, label: str
) -> None:
    if value.get("project_id") != project_id or value.get("generation") != generation:
        raise ValueError(f"{label} project scope drifted")


def supports_governed_domain_relation(
    relation: object,
    source_kind: object,
    target_kind: object,
    *,
    registry_path: Optional[Path] = None,
) -> bool:
    """Return whether the graph contract can encode one generic domain edge."""

    if not all(
        isinstance(value, str) and value.strip()
        for value in (relation, source_kind, target_kind)
    ):
        return False
    registry = _read_registry(Path(registry_path or DEFAULT_REGISTRY))
    domain_kinds = set(registry["namespaces"].get("domain") or [])
    rule = registry["edge_registry"].get(relation)
    if (
        source_kind not in domain_kinds
        or target_kind not in domain_kinds
        or not isinstance(rule, Mapping)
    ):
        return False
    source_namespaces = rule.get("source_namespaces")
    source_kinds = rule.get("source_kinds")
    target_namespaces = rule.get("target_namespaces")
    target_kinds = rule.get("target_kinds")
    source_allowed = (
        isinstance(source_namespaces, list) and "domain" in source_namespaces
    ) or (
        isinstance(source_kinds, list) and source_kind in source_kinds
    )
    target_allowed = (
        isinstance(target_namespaces, list) and "domain" in target_namespaces
    ) or (
        isinstance(target_kinds, list) and target_kind in target_kinds
    )
    return source_allowed and target_allowed


def _validate_common(
    value: Mapping[str, object],
    *,
    project_id: str,
    generation: int,
    label: str,
    authorities: Set[str],
    classifications: Set[str],
) -> None:
    _require_scope(value, project_id=project_id, generation=generation, label=label)
    for key in ("source_ref", "source_version", "authority_class", "classification"):
        _require_text(value, key, label)
    digest = value.get("source_sha256")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise ValueError(f"{label} source hash is invalid")
    if value.get("authority_class") not in authorities:
        raise ValueError(f"{label} authority class is unsupported")
    if value.get("classification") not in classifications:
        raise ValueError(f"{label} classification is unsupported")
    for key in ("valid_from", "valid_to"):
        item = value.get(key)
        if item is not None and (not isinstance(item, str) or not item.strip()):
            raise ValueError(f"{label} {key} is invalid")


class SemanticGraph:
    """Validated in-memory reference graph with canonical replay."""

    def __init__(
        self,
        *,
        project_id: str,
        generation: int,
        registry_path: Optional[Path] = None,
    ):
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("semantic graph project_id is invalid")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise ValueError("semantic graph generation is invalid")
        self.project_id = project_id
        self.generation = generation
        self._registry_path = Path(registry_path or DEFAULT_REGISTRY)
        self._registry = _read_registry(self._registry_path)
        self._nodes: dict[str, dict] = {}
        self._edges: dict[str, dict] = {}
        self._edge_ids_by_node: dict[str, set[str]] = {}

    @property
    def graph_sha256(self) -> str:
        return self.to_projection()["graph_sha256"]

    @property
    def nodes(self) -> list[dict]:
        return [deepcopy(self._nodes[key]) for key in sorted(self._nodes)]

    @property
    def edges(self) -> list[dict]:
        return [deepcopy(self._edges[key]) for key in sorted(self._edges)]

    def add_node(self, value: Mapping[str, object]) -> dict:
        if not isinstance(value, Mapping) or set(value) != NODE_REQUIRED_FIELDS:
            raise ValueError("semantic node fields are incomplete or unsupported")
        normalized = deepcopy(dict(value))
        node_id = _require_text(normalized, "node_id", "semantic node")
        namespace = _require_text(normalized, "namespace", "semantic node")
        node_kind = _require_text(normalized, "node_kind", "semantic node")
        _require_text(normalized, "label", "semantic node")
        if node_id.split(":", 1)[0] != namespace:
            raise ValueError("semantic node namespace drifted")
        namespaces = self._registry["namespaces"]
        if namespace not in namespaces or node_kind not in namespaces[namespace]:
            raise ValueError("semantic node kind is unsupported")
        if not isinstance(normalized.get("attributes"), Mapping):
            raise ValueError("semantic node attributes are invalid")
        _validate_common(
            normalized,
            project_id=self.project_id,
            generation=self.generation,
            label="semantic node",
            authorities=set(self._registry["authority_classes"]),
            classifications=set(self._registry["classifications"]),
        )
        prior = self._nodes.get(node_id)
        if prior is not None and prior != normalized:
            raise ValueError("semantic node identity drifted")
        self._nodes[node_id] = normalized
        return deepcopy(normalized)

    def add_edge(self, value: Mapping[str, object]) -> dict:
        if not isinstance(value, Mapping) or set(value) != EDGE_REQUIRED_FIELDS:
            raise ValueError("semantic edge fields are incomplete or unsupported")
        normalized = deepcopy(dict(value))
        edge_id = _require_text(normalized, "edge_id", "semantic edge")
        edge_kind = _require_text(normalized, "edge_kind", "semantic edge")
        source_id = _require_text(normalized, "source_id", "semantic edge")
        target_id = _require_text(normalized, "target_id", "semantic edge")
        source = self._nodes.get(source_id)
        target = self._nodes.get(target_id)
        if source is None or target is None:
            raise ValueError("semantic edge endpoint is absent")
        rule = self._registry["edge_registry"].get(edge_kind)
        if not isinstance(rule, Mapping):
            raise ValueError("semantic edge kind is unsupported")
        if not self._edge_endpoint_allowed(source, rule, "source") or not self._edge_endpoint_allowed(
            target, rule, "target"
        ):
            raise ValueError("semantic edge kind does not permit endpoint pair")
        if not isinstance(normalized.get("derivation"), Mapping):
            raise ValueError("semantic edge derivation is invalid")
        _validate_common(
            normalized,
            project_id=self.project_id,
            generation=self.generation,
            label="semantic edge",
            authorities=set(self._registry["authority_classes"]),
            classifications=set(self._registry["classifications"]),
        )
        prior = self._edges.get(edge_id)
        if prior is not None and prior != normalized:
            raise ValueError("semantic edge identity drifted")
        self._edges[edge_id] = normalized
        self._edge_ids_by_node.setdefault(source_id, set()).add(edge_id)
        self._edge_ids_by_node.setdefault(target_id, set()).add(edge_id)
        return deepcopy(normalized)

    @staticmethod
    def _edge_endpoint_allowed(
        node: Mapping[str, object], rule: Mapping[str, object], side: str
    ) -> bool:
        namespaces = rule.get(f"{side}_namespaces")
        kinds = rule.get(f"{side}_kinds")
        if isinstance(namespaces, list) and node.get("namespace") not in namespaces:
            return False
        if isinstance(kinds, list) and node.get("node_kind") not in kinds:
            return False
        return isinstance(namespaces, list) or isinstance(kinds, list)

    def node(self, node_id: str) -> Optional[dict]:
        value = self._nodes.get(node_id)
        return None if value is None else deepcopy(value)

    def neighbors(
        self,
        node_id: str,
        *,
        edge_kinds: Optional[Iterable[str]] = None,
        accepted_fact_required: bool = False,
    ) -> list[dict]:
        allowed_kinds = None if edge_kinds is None else set(edge_kinds)
        results = []
        for edge_id in sorted(self._edge_ids_by_node.get(node_id, set())):
            edge = self._edges[edge_id]
            if edge["source_id"] != node_id:
                continue
            if allowed_kinds is not None and edge["edge_kind"] not in allowed_kinds:
                continue
            target = self._nodes[edge["target_id"]]
            if accepted_fact_required and not (
                self._accepted(edge) and self._accepted(target)
            ):
                continue
            results.append({"edge": deepcopy(edge), "node": deepcopy(target)})
        return results

    def adjacent(
        self,
        node_id: str,
        *,
        accepted_fact_required: bool = False,
    ) -> list[dict]:
        """Return incident edges in O(degree), without exposing graph storage."""

        results = []
        for edge_id in sorted(self._edge_ids_by_node.get(node_id, set())):
            edge = self._edges[edge_id]
            adjacent_id = (
                edge["target_id"]
                if edge["source_id"] == node_id
                else edge["source_id"]
            )
            adjacent = self._nodes[adjacent_id]
            if accepted_fact_required and not (
                self._accepted(edge) and self._accepted(adjacent)
            ):
                continue
            results.append(
                {"edge": deepcopy(edge), "node": deepcopy(adjacent)}
            )
        return results

    def find_paths(
        self,
        start_id: str,
        target_id: str,
        *,
        max_hops: int,
        edge_kinds: Optional[Iterable[str]] = None,
        accepted_fact_required: bool = False,
    ) -> list[dict]:
        if start_id not in self._nodes or target_id not in self._nodes:
            return []
        if isinstance(max_hops, bool) or not isinstance(max_hops, int) or max_hops < 1:
            raise ValueError("semantic path max_hops is invalid")
        paths = []
        queue = [(start_id, [start_id], [])]
        while queue:
            current, node_ids, edge_ids = queue.pop(0)
            if len(edge_ids) >= max_hops:
                continue
            for row in self.neighbors(
                current,
                edge_kinds=edge_kinds,
                accepted_fact_required=accepted_fact_required,
            ):
                next_id = row["node"]["node_id"]
                if next_id in node_ids:
                    continue
                next_nodes = node_ids + [next_id]
                next_edges = edge_ids + [row["edge"]["edge_id"]]
                if next_id == target_id:
                    payload = {
                        "contract": "lca_research_kernel.semantic_path/v1",
                        "node_ids": next_nodes,
                        "edge_ids": next_edges,
                        "hop_count": len(next_edges),
                    }
                    payload["path_sha256"] = canonical_sha256(payload)
                    paths.append(payload)
                else:
                    queue.append((next_id, next_nodes, next_edges))
        return sorted(paths, key=lambda row: (row["hop_count"], row["path_sha256"]))

    def _accepted(self, value: Mapping[str, object]) -> bool:
        return (
            value.get("authority_class")
            in self._registry["accepted_path_authorities"]
            and value.get("classification")
            in self._registry["accepted_path_classifications"]
        )

    def to_projection(self) -> dict:
        payload = {
            "contract": GRAPH_CONTRACT,
            "project_id": self.project_id,
            "generation": self.generation,
            "registry_sha256": hashlib.sha256(
                self._registry_path.read_bytes()
            ).hexdigest(),
            "nodes": self.nodes,
            "edges": self.edges,
        }
        payload["graph_sha256"] = canonical_sha256(payload)
        return payload

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_projection()) + b"\n"

    @classmethod
    def from_projection(
        cls, value: Mapping[str, object], *, registry_path: Optional[Path] = None
    ) -> "SemanticGraph":
        if not isinstance(value, Mapping) or value.get("contract") != GRAPH_CONTRACT:
            raise ValueError("semantic graph projection contract is unsupported")
        normalized = deepcopy(dict(value))
        expected = normalized.pop("graph_sha256", None)
        if not isinstance(expected, str) or canonical_sha256(normalized) != expected:
            raise ValueError("semantic graph projection hash drifted")
        graph = cls(
            project_id=normalized.get("project_id"),
            generation=normalized.get("generation"),
            registry_path=registry_path,
        )
        registry_digest = hashlib.sha256(graph._registry_path.read_bytes()).hexdigest()
        if normalized.get("registry_sha256") != registry_digest:
            raise ValueError("semantic graph registry drifted")
        nodes = normalized.get("nodes")
        edges = normalized.get("edges")
        if not isinstance(nodes, list) or not isinstance(edges, list):
            raise ValueError("semantic graph projection rows are invalid")
        for node_value in nodes:
            graph.add_node(node_value)
        for edge_value in edges:
            graph.add_edge(edge_value)
        return graph
