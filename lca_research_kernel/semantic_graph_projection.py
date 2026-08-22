"""Build content-addressed semantic projections from accepted D-18 sources."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Iterable, Mapping, Optional

from .semantic_graph import (
    SemanticGraph,
    canonical_sha256,
    supports_governed_domain_relation,
)
from .material_sources import material_revision_context_eligibility
from .tidas_graph_slices import (
    VERSIONED_DATASET_KINDS,
    collect_tidas_graph_slices,
    version_key,
)


VIEW_CONTRACT = "lca_research_kernel.project_record_view/v1"
SOURCE_LOCK_CONTRACT = "lca_research_kernel.source_lock/v1"
CATALOG_CONTRACT = "lca_research_kernel.capability_catalog/v1"
PROJECTION_RECEIPT_CONTRACT = (
    "lca_research_kernel.semantic_graph_projection_receipt/v1"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _safe_identity(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is absent")
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    if not normalized:
        raise ValueError(f"{label} is invalid")
    return normalized


def _opaque_graph_identity(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is absent")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _content_hash(value: object) -> str:
    return canonical_sha256(value)


def _evidence_unit_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    ).hexdigest()


def _integrity_hash(value: Mapping[str, object]) -> str:
    integrity = value.get("integrity")
    if isinstance(integrity, str) and integrity.startswith("sha256-"):
        digest = integrity[7:]
        if SHA256_RE.fullmatch(digest):
            return digest
    return _content_hash(value)


def _source_authority(value: Mapping[str, object]) -> str:
    owner = str(value.get("owner") or "").lower()
    source_id = str(value.get("source_id") or "").lower()
    if "tiangong" in owner or source_id.startswith("tiangong."):
        return "tiangong_official"
    return "governed_method"


def _fact_authority(fact: Mapping[str, object]) -> str:
    source = fact.get("source")
    authority = source.get("authority") if isinstance(source, Mapping) else None
    classification = fact.get("classification")
    if authority in {"tiangong_official", "official_tiangong"}:
        return "tiangong_official"
    if authority in {"model_candidate_only", "candidate_only"} or classification in {
        "candidate",
        "assumption",
    }:
        return "candidate_only"
    if classification == "computed":
        return "deterministic_computation"
    return "accepted_project_truth"


def _fact_source(
    fact: Mapping[str, object], project_view: Mapping[str, object]
) -> tuple[str, str, str]:
    source = fact.get("source")
    if isinstance(source, Mapping):
        digest = source.get("content_sha256")
        version = source.get("version")
        authority = source.get("authority")
        if (
            isinstance(digest, str)
            and SHA256_RE.fullmatch(digest)
            and isinstance(version, str)
            and version
        ):
            return (
                f"project-record:{project_view['last_event_sha256']}#fact:{fact['fact_id']}:{authority}",
                digest,
                version,
            )
    return (
        f"project-record:{project_view['last_event_sha256']}#fact:{fact['fact_id']}",
        str(project_view["last_event_sha256"]),
        str(project_view["last_event_id"]),
    )


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


class SemanticGraphProjector:
    """Translate accepted inputs into a disposable, reproducible graph."""

    def __init__(self, *, project_id: str, generation: int):
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("semantic projection project_id is invalid")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise ValueError("semantic projection generation is invalid")
        self.project_id = project_id
        self.generation = generation

    def project(
        self,
        *,
        project_view: Mapping[str, object],
        source_lock: Mapping[str, object],
        capability_catalog: Mapping[str, object],
        domain_rules: Iterable[Mapping[str, object]] = (),
        skill_descriptors: Iterable[Mapping[str, object]] = (),
        governed_knowledge_entries: Iterable[Mapping[str, object]] = (),
        material_evidence_units: Iterable[Mapping[str, object]] = (),
    ) -> SemanticGraph:
        self._validate_inputs(project_view, source_lock, capability_catalog)
        material_evidence_units = tuple(material_evidence_units)
        graph = SemanticGraph(project_id=self.project_id, generation=self.generation)
        source_nodes = self._project_sources(graph, source_lock)
        capability_nodes = self._project_capabilities(
            graph, capability_catalog, source_nodes
        )
        rule_nodes = self._project_rules(
            graph, domain_rules, source_nodes=source_nodes
        )
        self._project_skills(
            graph,
            skill_descriptors,
            rule_nodes=rule_nodes,
            capability_nodes=capability_nodes,
        )
        self._project_record(graph, project_view)
        self._project_governed_knowledge(graph, governed_knowledge_entries)
        self._project_materials(graph, project_view, material_evidence_units)
        self._project_tidas_object_slices(
            graph,
            project_view=project_view,
            source_lock=source_lock,
            material_evidence_units=material_evidence_units,
        )
        return graph

    def persist(self, graph: SemanticGraph, workspace: Path) -> dict:
        if graph.project_id != self.project_id or graph.generation != self.generation:
            raise ValueError("semantic graph persistence scope drifted")
        root = Path(workspace).resolve()
        directory = root / "generated" / "semantic-graphs"
        path = directory / f"{graph.graph_sha256}.json"
        try:
            path.resolve().relative_to(root)
        except ValueError as error:
            raise ValueError("semantic graph persistence path escaped workspace") from error
        payload = graph.to_bytes()
        _atomic_write(path, payload)
        for other in directory.glob("*.json"):
            if other != path:
                other.unlink()
        return {
            "contract": PROJECTION_RECEIPT_CONTRACT,
            "project_id": self.project_id,
            "generation": self.generation,
            "graph_sha256": graph.graph_sha256,
            "artifact_sha256": hashlib.sha256(payload).hexdigest(),
            "relative_path": path.relative_to(root).as_posix(),
            "authoritative": False,
            "rebuildable": True,
        }

    def _validate_inputs(
        self,
        project_view: Mapping[str, object],
        source_lock: Mapping[str, object],
        capability_catalog: Mapping[str, object],
    ) -> None:
        if (
            not isinstance(project_view, Mapping)
            or project_view.get("contract") != VIEW_CONTRACT
            or project_view.get("project_id") != self.project_id
        ):
            raise ValueError("semantic projection project scope drifted")
        if (
            not isinstance(project_view.get("last_event_sha256"), str)
            or SHA256_RE.fullmatch(str(project_view["last_event_sha256"])) is None
            or not isinstance(project_view.get("last_event_id"), str)
        ):
            raise ValueError("semantic projection Project Record identity is absent")
        if (
            not isinstance(source_lock, Mapping)
            or source_lock.get("contract") != SOURCE_LOCK_CONTRACT
            or not isinstance(source_lock.get("lock_id"), str)
            or not isinstance(source_lock.get("sources"), list)
        ):
            raise ValueError("semantic projection source lock is invalid")
        if (
            not isinstance(capability_catalog, Mapping)
            or capability_catalog.get("contract") != CATALOG_CONTRACT
            or capability_catalog.get("source_lock_id") != source_lock.get("lock_id")
            or not isinstance(capability_catalog.get("capabilities"), list)
        ):
            raise ValueError("semantic projection source lock identity drifted")

    def _node(
        self,
        *,
        node_id: str,
        namespace: str,
        node_kind: str,
        label: str,
        source_ref: str,
        source_sha256: str,
        source_version: str,
        authority_class: str,
        classification: str,
        attributes: Mapping[str, object],
    ) -> dict:
        return {
            "node_id": node_id,
            "namespace": namespace,
            "node_kind": node_kind,
            "label": label,
            "source_ref": source_ref,
            "source_sha256": source_sha256,
            "source_version": source_version,
            "authority_class": authority_class,
            "classification": classification,
            "project_id": self.project_id,
            "generation": self.generation,
            "valid_from": None,
            "valid_to": None,
            "attributes": deepcopy(dict(attributes)),
        }

    def _edge(
        self,
        *,
        edge_kind: str,
        source_id: str,
        target_id: str,
        source_ref: str,
        source_sha256: str,
        source_version: str,
        authority_class: str = "governed_method",
        classification: str = "accepted",
        derivation: Mapping[str, object] | None = None,
    ) -> dict:
        identity = canonical_sha256(
            {
                "edge_kind": edge_kind,
                "source_id": source_id,
                "target_id": target_id,
                "source_sha256": source_sha256,
                "generation": self.generation,
            }
        )
        return {
            "edge_id": f"edge:{identity}",
            "edge_kind": edge_kind,
            "source_id": source_id,
            "target_id": target_id,
            "source_ref": source_ref,
            "source_sha256": source_sha256,
            "source_version": source_version,
            "authority_class": authority_class,
            "classification": classification,
            "project_id": self.project_id,
            "generation": self.generation,
            "valid_from": None,
            "valid_to": None,
            "derivation": deepcopy(
                dict(derivation or {"kind": "exact_structured_relation"})
            ),
        }

    def _project_sources(
        self, graph: SemanticGraph, source_lock: Mapping[str, object]
    ) -> set[str]:
        source_nodes = set()
        lock_id = str(source_lock["lock_id"])
        for row in sorted(source_lock["sources"], key=lambda item: item["source_id"]):
            if not isinstance(row, Mapping):
                raise ValueError("semantic projection source row is invalid")
            source_id = _safe_identity(row.get("source_id"), "source_id")
            version = _safe_identity(row.get("version"), "source version")
            node_id = f"evidence:source:{source_id}"
            authority = _source_authority(row)
            graph.add_node(
                self._node(
                    node_id=node_id,
                    namespace="evidence",
                    node_kind="Source",
                    label=str(row.get("identity") or source_id),
                    source_ref=f"source-lock:{lock_id}#{source_id}",
                    source_sha256=_integrity_hash(row),
                    source_version=version,
                    authority_class=authority,
                    classification="official" if authority == "tiangong_official" else "accepted",
                    attributes=row,
                )
            )
            source_nodes.add(source_id)
        return source_nodes

    def _project_capabilities(
        self,
        graph: SemanticGraph,
        catalog: Mapping[str, object],
        source_nodes: set[str],
    ) -> set[str]:
        result = set()
        catalog_sha = _content_hash(catalog)
        catalog_id = str(catalog.get("catalog_id"))
        for row in sorted(catalog["capabilities"], key=lambda item: item["capability_id"]):
            if not isinstance(row, Mapping):
                raise ValueError("semantic projection capability row is invalid")
            capability_id = _safe_identity(row.get("capability_id"), "capability_id")
            source_id = _safe_identity(row.get("source_lock_id"), "capability source_lock_id")
            if source_id not in source_nodes:
                raise ValueError("semantic projection capability source is absent")
            node_id = f"method:capability:{capability_id}"
            graph.add_node(
                self._node(
                    node_id=node_id,
                    namespace="method",
                    node_kind="Capability",
                    label=capability_id,
                    source_ref=f"capability-catalog:{catalog_id}#{capability_id}",
                    source_sha256=catalog_sha,
                    source_version=catalog_id,
                    authority_class="governed_method",
                    classification="accepted",
                    attributes=row,
                )
            )
            graph.add_edge(
                self._edge(
                    edge_kind="supported_by",
                    source_id=node_id,
                    target_id=f"evidence:source:{source_id}",
                    source_ref=f"capability-catalog:{catalog_id}#{capability_id}",
                    source_sha256=catalog_sha,
                    source_version=catalog_id,
                )
            )
            result.add(capability_id)
        return result

    def _project_rules(
        self,
        graph: SemanticGraph,
        domain_rules: Iterable[Mapping[str, object]],
        *,
        source_nodes: set[str],
    ) -> set[str]:
        result = set()
        for row in sorted(domain_rules, key=lambda item: str(item.get("rule_id"))):
            rule_id = _safe_identity(row.get("rule_id"), "domain rule_id")
            version = _safe_identity(row.get("version"), "domain rule version")
            if row.get("contract") == "lca_research_kernel.domain_rule/v1":
                source_ids = row.get("source_lock_ids")
                if (
                    not isinstance(source_ids, list)
                    or not source_ids
                    or any(
                        not isinstance(source_id, str)
                        or source_id not in source_nodes
                        for source_id in source_ids
                    )
                ):
                    raise ValueError(
                        "semantic projection domain rule source lock is invalid"
                    )
                source_ref = f"domain-rule-registry:{rule_id}@{version}"
                source_sha = _content_hash(row)
                label = str(row.get("title") or rule_id)
            else:
                source_ids = []
                raw_source_ref = row.get("source_ref")
                if isinstance(raw_source_ref, Mapping):
                    source_ref = raw_source_ref.get("path")
                    source_sha = raw_source_ref.get("sha256")
                else:
                    source_ref = raw_source_ref
                    source_sha = row.get("source_sha256")
                label = str(row.get("purpose") or rule_id)
            if not isinstance(source_ref, str) or not source_ref.strip():
                raise ValueError("semantic projection domain rule source_ref is invalid")
            if not isinstance(source_sha, str) or SHA256_RE.fullmatch(source_sha) is None:
                raise ValueError("semantic projection domain rule source hash is invalid")
            node_id = f"method:rule:{rule_id}"
            graph.add_node(
                self._node(
                    node_id=node_id,
                    namespace="method",
                    node_kind="DomainRule",
                    label=label,
                    source_ref=source_ref,
                    source_sha256=source_sha,
                    source_version=version,
                    authority_class="governed_method",
                    classification="accepted",
                    attributes=row,
                )
            )
            for source_id in source_ids:
                graph.add_edge(
                    self._edge(
                        edge_kind="supported_by",
                        source_id=node_id,
                        target_id=f"evidence:source:{source_id}",
                        source_ref=source_ref,
                        source_sha256=source_sha,
                        source_version=version,
                    )
                )
            result.add(rule_id)
        return result

    def _project_skills(
        self,
        graph: SemanticGraph,
        skill_descriptors: Iterable[Mapping[str, object]],
        *,
        rule_nodes: set[str],
        capability_nodes: set[str],
    ) -> None:
        for row in sorted(skill_descriptors, key=lambda item: str(item.get("skill_id"))):
            skill_id = _safe_identity(row.get("skill_id"), "skill_id")
            source_ref = _safe_identity(row.get("source_ref"), "Skill source_ref")
            source_sha = row.get("source_sha256")
            version = _safe_identity(row.get("version"), "Skill version")
            if not isinstance(source_sha, str) or SHA256_RE.fullmatch(source_sha) is None:
                raise ValueError("semantic projection Skill source hash is invalid")
            skill_node = f"method:skill:{skill_id}"
            graph.add_node(
                self._node(
                    node_id=skill_node,
                    namespace="method",
                    node_kind="Skill",
                    label=skill_id,
                    source_ref=source_ref,
                    source_sha256=source_sha,
                    source_version=version,
                    authority_class="governed_method",
                    classification="accepted",
                    attributes=row,
                )
            )
            for rule_id in row.get("required_domain_rule_ids") or []:
                if rule_id not in rule_nodes:
                    continue
                graph.add_edge(
                    self._edge(
                        edge_kind="requires",
                        source_id=skill_node,
                        target_id=f"method:rule:{rule_id}",
                        source_ref=source_ref,
                        source_sha256=source_sha,
                        source_version=version,
                    )
                )
            for capability_id in row.get("capability_ids") or []:
                if capability_id not in capability_nodes:
                    raise ValueError("semantic projection Skill capability is absent")
                graph.add_edge(
                    self._edge(
                        edge_kind="invokes",
                        source_id=skill_node,
                        target_id=f"method:capability:{capability_id}",
                        source_ref=source_ref,
                        source_sha256=source_sha,
                        source_version=version,
                    )
                )

    def _project_governed_knowledge(
        self,
        graph: SemanticGraph,
        entries: Iterable[Mapping[str, object]],
    ) -> None:
        """Expose governed explanations as methods, never as project facts."""

        for row in sorted(entries, key=lambda item: str(item.get("knowledge_id"))):
            knowledge_id = _safe_identity(
                row.get("knowledge_id"), "governed knowledge_id"
            )
            roles = row.get("knowledge_roles")
            source_ref = row.get("source_ref")
            registry_id = row.get("registry_id")
            registry_sha = row.get("registry_sha256")
            if (
                not isinstance(roles, list)
                or not roles
                or any(not isinstance(role, str) or not role for role in roles)
                or not isinstance(source_ref, Mapping)
                or not isinstance(source_ref.get("path"), str)
                or not isinstance(source_ref.get("entry_id"), str)
                or not isinstance(source_ref.get("sha256"), str)
                or SHA256_RE.fullmatch(str(source_ref["sha256"])) is None
                or not isinstance(registry_id, str)
                or not registry_id
                or not isinstance(registry_sha, str)
                or SHA256_RE.fullmatch(registry_sha) is None
            ):
                raise ValueError("semantic projection governed knowledge is invalid")
            node_id = f"method:knowledge:{knowledge_id}"
            exact_ref = f"{source_ref['path']}#{source_ref['entry_id']}"
            attributes = {
                **deepcopy(dict(row)),
                "semantic_roles": deepcopy(roles),
                "project_truth_effect": "none",
                "selectable": True,
            }
            graph.add_node(
                self._node(
                    node_id=node_id,
                    namespace="method",
                    node_kind="DomainRule",
                    label=str(row.get("statement") or row.get("knowledge_id")),
                    source_ref=exact_ref,
                    source_sha256=str(source_ref["sha256"]),
                    source_version=registry_id,
                    authority_class="governed_method",
                    classification="accepted",
                    attributes=attributes,
                )
            )
            graph.add_edge(
                self._edge(
                    edge_kind="addresses_goal",
                    source_id=node_id,
                    target_id="project:goal",
                    source_ref=exact_ref,
                    source_sha256=str(source_ref["sha256"]),
                    source_version=registry_id,
                )
            )
            source_kind = row.get("source_kind")
            target_kind = row.get("target_kind")
            relation = row.get("relation")
            if supports_governed_domain_relation(
                relation, source_kind, target_kind
            ):
                source_concept = f"domain:knowledge:{knowledge_id}:source"
                target_concept = f"domain:knowledge:{knowledge_id}:target"
                for concept_id, concept_kind, side in (
                    (source_concept, source_kind, "source"),
                    (target_concept, target_kind, "target"),
                ):
                    graph.add_node(
                        self._node(
                            node_id=concept_id,
                            namespace="domain",
                            node_kind=str(concept_kind),
                            label=str(concept_kind),
                            source_ref=exact_ref,
                            source_sha256=str(source_ref["sha256"]),
                            source_version=registry_id,
                            authority_class="governed_method",
                            classification="accepted",
                            attributes={
                                "knowledge_id": row["knowledge_id"],
                                "concept_side": side,
                                "generic_concept": True,
                                "project_truth_effect": "none",
                                "semantic_roles": deepcopy(roles),
                            },
                        )
                    )
                    graph.add_edge(
                        self._edge(
                            edge_kind="validated_by",
                            source_id=concept_id,
                            target_id=node_id,
                            source_ref=exact_ref,
                            source_sha256=str(source_ref["sha256"]),
                            source_version=registry_id,
                        )
                    )
                graph.add_edge(
                    self._edge(
                        edge_kind=str(relation),
                        source_id=source_concept,
                        target_id=target_concept,
                        source_ref=exact_ref,
                        source_sha256=str(source_ref["sha256"]),
                        source_version=registry_id,
                    )
                )

    def _project_record(
        self, graph: SemanticGraph, project_view: Mapping[str, object]
    ) -> None:
        event_sha = str(project_view["last_event_sha256"])
        event_id = str(project_view["last_event_id"])
        record_ref = f"project-record:{event_sha}"
        graph.add_node(
            self._node(
                node_id="project:goal",
                namespace="project",
                node_kind="Goal",
                label=str(project_view.get("objective")),
                source_ref=f"{record_ref}#goal",
                source_sha256=event_sha,
                source_version=event_id,
                authority_class="accepted_project_truth",
                classification="accepted",
                attributes={"objective": project_view.get("objective"), "title": project_view.get("title")},
            )
        )
        facts = project_view.get("facts") or {}
        if not isinstance(facts, Mapping):
            raise ValueError("semantic projection facts are invalid")
        for fact_id, raw_fact in sorted(facts.items()):
            if not isinstance(raw_fact, Mapping) or raw_fact.get("fact_id") != fact_id:
                raise ValueError("semantic projection fact identity drifted")
            classification = str(raw_fact.get("classification"))
            namespace = "evidence" if classification == "missing" else "project"
            node_kind = "MissingDatum" if classification == "missing" else "Fact"
            node_id = (
                f"evidence:missing:{_safe_identity(fact_id, 'fact_id')}"
                if classification == "missing"
                else f"project:fact:{_safe_identity(fact_id, 'fact_id')}"
            )
            source_ref, source_sha, source_version = _fact_source(raw_fact, project_view)
            authority = _fact_authority(raw_fact)
            graph.add_node(
                self._node(
                    node_id=node_id,
                    namespace=namespace,
                    node_kind=node_kind,
                    label=str(fact_id),
                    source_ref=source_ref,
                    source_sha256=source_sha,
                    source_version=source_version,
                    authority_class=authority,
                    classification=classification,
                    attributes=raw_fact,
                )
            )
            graph.add_edge(
                self._edge(
                    edge_kind="requires" if classification == "missing" else "addresses_goal",
                    source_id="project:goal" if classification == "missing" else node_id,
                    target_id=node_id if classification == "missing" else "project:goal",
                    source_ref=source_ref,
                    source_sha256=source_sha,
                    source_version=source_version,
                    authority_class=authority,
                    classification=classification,
                )
            )
        self._project_record_collection(
            graph, project_view.get("decisions") or [], "Decision", "decision_id", record_ref, event_sha, event_id
        )
        risks = project_view.get("risks") or {}
        self._project_record_collection(
            graph, risks.values() if isinstance(risks, Mapping) else [], "Risk", "risk_id", record_ref, event_sha, event_id
        )
        artifacts = project_view.get("artifacts") or {}
        if not isinstance(artifacts, Mapping):
            raise ValueError("semantic projection artifacts are invalid")
        for path, artifact in sorted(artifacts.items()):
            if not isinstance(artifact, Mapping):
                raise ValueError("semantic projection artifact is invalid")
            digest = artifact.get("sha256")
            if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
                raise ValueError("semantic projection artifact hash is invalid")
            graph.add_node(
                self._node(
                    node_id=f"evidence:artifact:{canonical_sha256(path)[:24]}",
                    namespace="evidence",
                    node_kind="Artifact",
                    label=str(path),
                    source_ref=f"{record_ref}#artifact:{path}",
                    source_sha256=digest,
                    source_version=event_id,
                    authority_class="accepted_project_truth",
                    classification="accepted",
                    attributes=artifact,
                )
            )
        result = project_view.get("result")
        if isinstance(result, Mapping):
            graph.add_node(
                self._node(
                    node_id="project:result",
                    namespace="project",
                    node_kind="Result",
                    label=str(result.get("result_id") or "accepted-result"),
                    source_ref=f"{record_ref}#result",
                    source_sha256=event_sha,
                    source_version=event_id,
                    authority_class="accepted_project_truth",
                    classification="accepted",
                    attributes=result,
                )
            )
        route = project_view.get("next_route")
        if isinstance(route, str) and route:
            route_id = f"project:route:{_safe_identity(route, 'next_route')}"
            graph.add_node(
                self._node(
                    node_id="project:lifecycle",
                    namespace="project",
                    node_kind="LifecycleState",
                    label=str(project_view.get("status") or "active"),
                    source_ref=f"{record_ref}#lifecycle",
                    source_sha256=event_sha,
                    source_version=event_id,
                    authority_class="accepted_project_truth",
                    classification="accepted",
                    attributes={"status": project_view.get("status")},
                )
            )
            graph.add_node(
                self._node(
                    node_id=route_id,
                    namespace="project",
                    node_kind="Route",
                    label=route,
                    source_ref=f"{record_ref}#next_route",
                    source_sha256=event_sha,
                    source_version=event_id,
                    authority_class="accepted_project_truth",
                    classification="accepted",
                    attributes={"route": route},
                )
            )
            graph.add_edge(
                self._edge(
                    edge_kind="current_route",
                    source_id="project:lifecycle",
                    target_id=route_id,
                    source_ref=f"{record_ref}#next_route",
                    source_sha256=event_sha,
                    source_version=event_id,
                    authority_class="accepted_project_truth",
                )
            )

    def _project_record_collection(
        self,
        graph: SemanticGraph,
        rows: Iterable[object],
        node_kind: str,
        identity_key: str,
        record_ref: str,
        event_sha: str,
        event_id: str,
    ) -> None:
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise ValueError(f"semantic projection {node_kind} is invalid")
            identity = row.get(identity_key) or f"{node_kind.lower()}-{index}"
            safe = _safe_identity(identity, f"{node_kind} identity")
            graph.add_node(
                self._node(
                    node_id=f"project:{node_kind.lower()}:{safe}",
                    namespace="project",
                    node_kind=node_kind,
                    label=safe,
                    source_ref=f"{record_ref}#{node_kind.lower()}:{safe}",
                    source_sha256=event_sha,
                    source_version=event_id,
                    authority_class="accepted_project_truth",
                    classification="accepted",
                    attributes=row,
                )
            )

    def _project_materials(
        self,
        graph: SemanticGraph,
        project_view: Mapping[str, object],
        material_evidence_units: Iterable[Mapping[str, object]],
    ) -> None:
        """Project accepted material relationships without becoming a truth store."""

        materials = project_view.get("materials") or {}
        revisions = project_view.get("material_revisions") or {}
        locators = project_view.get("evidence_locators") or {}
        if not all(isinstance(value, Mapping) for value in (materials, revisions, locators)):
            raise ValueError("semantic projection material state is invalid")
        evidence_by_locator: dict[str, Mapping[str, object]] = {}
        for raw in material_evidence_units:
            if not isinstance(raw, Mapping):
                raise ValueError("semantic projection material evidence unit is invalid")
            locator_id = raw.get("locator_id")
            content_hash = raw.get("content_hash")
            unit = raw.get("evidence_unit")
            if (
                not isinstance(locator_id, str)
                or not locator_id
                or locator_id in evidence_by_locator
                or not isinstance(content_hash, str)
                or SHA256_RE.fullmatch(content_hash) is None
                or not isinstance(unit, Mapping)
                or _evidence_unit_hash(unit) != content_hash
            ):
                raise ValueError("semantic projection material evidence binding drifted")
            evidence_by_locator[locator_id] = raw

        record_sha = str(project_view["last_event_sha256"])
        record_version = str(project_view["last_event_id"])
        for material_id, raw_material in sorted(materials.items()):
            if not isinstance(raw_material, Mapping) or raw_material.get("material_id") != material_id:
                raise ValueError("semantic projection material identity drifted")
            graph.add_node(
                self._node(
                    node_id=f"project:material:{_safe_identity(material_id, 'material_id')}",
                    namespace="project",
                    node_kind="Material",
                    label=str(raw_material.get("display_name") or material_id),
                    source_ref=f"project-record:{record_sha}#material:{material_id}",
                    source_sha256=record_sha,
                    source_version=record_version,
                    authority_class="accepted_project_truth",
                    classification="accepted",
                    attributes={
                        **deepcopy(dict(raw_material)),
                        "trust_class": "untrusted_content",
                        "acceptance_status": "accepted_project_input",
                    },
                )
            )

        artifact_nodes: set[str] = set()
        for revision_id, raw_revision in sorted(revisions.items()):
            if not isinstance(raw_revision, Mapping) or raw_revision.get("revision_id") != revision_id:
                raise ValueError("semantic projection material revision identity drifted")
            material_id = raw_revision.get("material_id")
            material = materials.get(material_id)
            artifact_ref = raw_revision.get("artifact_ref")
            artifact_sha = artifact_ref.get("sha256") if isinstance(artifact_ref, Mapping) else None
            if (
                not isinstance(material, Mapping)
                or not isinstance(artifact_sha, str)
                or SHA256_RE.fullmatch(artifact_sha) is None
            ):
                raise ValueError("semantic projection material revision source is invalid")
            revision_node = f"project:material_revision:{_safe_identity(revision_id, 'revision_id')}"
            artifact_node = f"evidence:input_artifact:{artifact_sha}"
            current = material.get("current_revision_id") == revision_id
            selectable = (
                current
                and material.get("deleted_at") is None
                and material_revision_context_eligibility(
                    project_view, str(revision_id)
                )[0]
                and raw_revision.get("processing_status") in {"ready", "partial", "needs_user"}
            )
            graph.add_node(
                self._node(
                    node_id=revision_node,
                    namespace="project",
                    node_kind="MaterialRevision",
                    label=str(revision_id),
                    source_ref=f"project-record:{record_sha}#material-revision:{revision_id}",
                    source_sha256=artifact_sha,
                    source_version=str(revision_id),
                    authority_class="accepted_project_truth",
                    classification="accepted",
                    attributes={
                        **deepcopy(dict(raw_revision)),
                        "current": current,
                        "selectable": selectable,
                        "trust_class": "untrusted_content",
                        "acceptance_status": "accepted_project_input",
                    },
                )
            )
            if artifact_node not in artifact_nodes:
                graph.add_node(
                    self._node(
                        node_id=artifact_node,
                        namespace="evidence",
                        node_kind="InputArtifact",
                        label=artifact_sha,
                        source_ref=f"input-artifact:sha256:{artifact_sha}",
                        source_sha256=artifact_sha,
                        source_version=artifact_sha,
                        authority_class="accepted_project_truth",
                        classification="accepted",
                        attributes={
                            "artifact_ref": deepcopy(dict(artifact_ref)),
                            "revision_id": revision_id,
                            "trust_class": "untrusted_content",
                            "acceptance_status": "accepted_project_input",
                        },
                    )
                )
                artifact_nodes.add(artifact_node)
            graph.add_edge(
                self._edge(
                    edge_kind="has_revision",
                    source_id=f"project:material:{_safe_identity(material_id, 'material_id')}",
                    target_id=revision_node,
                    source_ref=f"project-record:{record_sha}#material-revision:{revision_id}",
                    source_sha256=artifact_sha,
                    source_version=str(revision_id),
                    authority_class="accepted_project_truth",
                )
            )
            parent = raw_revision.get("parent_revision_id")
            if isinstance(parent, str) and parent in revisions:
                graph.add_edge(
                    self._edge(
                        edge_kind="supersedes",
                        source_id=revision_node,
                        target_id=(
                            "project:material_revision:"
                            + _safe_identity(parent, "revision_id")
                        ),
                        source_ref=(
                            f"project-record:{record_sha}#material-revision:{revision_id}"
                        ),
                        source_sha256=artifact_sha,
                        source_version=str(revision_id),
                        authority_class="accepted_project_truth",
                    )
                )
            graph.add_edge(
                self._edge(
                    edge_kind="derived_from",
                    source_id=revision_node,
                    target_id=artifact_node,
                    source_ref=f"project-record:{record_sha}#material-revision:{revision_id}",
                    source_sha256=artifact_sha,
                    source_version=str(revision_id),
                    authority_class="accepted_project_truth",
                )
            )

        locator_node_by_id: dict[str, str] = {}
        for locator_id, raw_locator in sorted(locators.items()):
            if not isinstance(raw_locator, Mapping) or raw_locator.get("locator_id") != locator_id:
                raise ValueError("semantic projection evidence locator identity drifted")
            revision_id = raw_locator.get("revision_id")
            revision = revisions.get(revision_id)
            binding = evidence_by_locator.get(str(locator_id))
            if not isinstance(revision, Mapping):
                raise ValueError("semantic projection evidence locator revision is absent")
            content_hash = raw_locator.get("content_hash")
            if not isinstance(content_hash, str) or SHA256_RE.fullmatch(content_hash) is None:
                raise ValueError("semantic projection evidence locator hash is invalid")
            if binding is not None and binding.get("content_hash") != content_hash:
                raise ValueError("semantic projection evidence locator content drifted")
            unit = binding.get("evidence_unit") if isinstance(binding, Mapping) else {}
            roles = unit.get("semantic_roles") if isinstance(unit, Mapping) else []
            if not isinstance(roles, list) or any(not isinstance(role, str) or not role for role in roles):
                raise ValueError("semantic projection evidence roles are invalid")
            revision_material = materials.get(revision.get("material_id"))
            selectable = (
                isinstance(revision_material, Mapping)
                and revision_material.get("current_revision_id") == revision_id
                and revision_material.get("deleted_at") is None
                and material_revision_context_eligibility(
                    project_view, str(revision_id)
                )[0]
                and revision.get("processing_status")
                in {"ready", "partial", "needs_user"}
                and raw_locator.get("scientific_status") == "accepted_evidence"
                and isinstance(raw_locator.get("assurance_acceptance_ref"), Mapping)
                and binding is not None
            )
            locator_node = (
                "evidence:locator:"
                + _opaque_graph_identity(locator_id, "locator_id")
            )
            locator_node_by_id[str(locator_id)] = locator_node
            graph.add_node(
                self._node(
                    node_id=locator_node,
                    namespace="evidence",
                    node_kind="EvidenceLocator",
                    label=str(locator_id),
                    source_ref=f"project-record:{record_sha}#evidence-locator:{locator_id}",
                    source_sha256=content_hash,
                    source_version=str(revision_id),
                    authority_class="accepted_project_truth",
                    classification="accepted",
                    attributes={
                        **deepcopy(dict(raw_locator)),
                        "semantic_roles": sorted(set(roles)),
                        "retrieval_priority": (
                            100 if "lca_native_package_root" in roles else 0
                        ),
                        "evidence_kind": unit.get("kind") if isinstance(unit, Mapping) else None,
                        "selectable": selectable,
                        "trust_class": "untrusted_content",
                        "acceptance_status": raw_locator.get("scientific_status"),
                    },
                )
            )
            graph.add_edge(
                self._edge(
                    edge_kind="contains",
                    source_id=f"project:material_revision:{_safe_identity(revision_id, 'revision_id')}",
                    target_id=locator_node,
                    source_ref=f"project-record:{record_sha}#evidence-locator:{locator_id}",
                    source_sha256=content_hash,
                    source_version=str(revision_id),
                    authority_class="accepted_project_truth",
                )
            )
            if roles:
                graph.add_edge(
                    self._edge(
                        edge_kind="supports",
                        source_id=locator_node,
                        target_id="project:goal",
                        source_ref=f"project-record:{record_sha}#evidence-locator:{locator_id}",
                        source_sha256=content_hash,
                        source_version=str(revision_id),
                        authority_class="accepted_project_truth",
                    )
                )
        self._project_accepted_material_fact_relationships(
            graph,
            project_view=project_view,
            locator_node_by_id=locator_node_by_id,
        )
        self._project_generated_artifact_derivations(
            graph,
            project_view=project_view,
            locator_node_by_id=locator_node_by_id,
        )
        result = project_view.get("result")
        if isinstance(result, Mapping) and graph.node("project:result") is not None:
            for citation in result.get("citations") or []:
                locator_id = citation.get("locator_id") if isinstance(citation, Mapping) else None
                locator = locators.get(locator_id) if isinstance(locator_id, str) else None
                if isinstance(locator, Mapping):
                    graph.add_edge(
                        self._edge(
                            edge_kind="cites",
                            source_id="project:result",
                            target_id=(
                                "evidence:locator:"
                                + _opaque_graph_identity(locator_id, "locator_id")
                            ),
                            source_ref=f"project-record:{record_sha}#result",
                            source_sha256=str(locator["content_hash"]),
                            source_version=str(locator["revision_id"]),
                            authority_class="accepted_project_truth",
                        )
                    )

    def _project_tidas_object_slices(
        self,
        graph: SemanticGraph,
        *,
        project_view: Mapping[str, object],
        source_lock: Mapping[str, object],
        material_evidence_units: Iterable[Mapping[str, object]],
    ) -> None:
        """Project exact TIDAS relations without creating a graph truth store."""

        graph_bindings = [
            row
            for row in material_evidence_units
            if isinstance(row, Mapping)
            and isinstance(row.get("evidence_unit"), Mapping)
            and row["evidence_unit"].get("kind") == "tidas_object_graph_slice"
        ]
        if not graph_bindings:
            return

        closure = collect_tidas_graph_slices(
            project_view=project_view,
            source_lock=source_lock,
            material_evidence_units=graph_bindings,
        )
        if not closure["objects"]:
            return
        record_sha = str(project_view["last_event_sha256"])
        object_nodes: dict[str, str] = {}
        version_groups: dict[tuple[str, str], list[dict]] = {}

        def source_ref(source: Mapping[str, object]) -> str:
            return (
                f"project-record:{record_sha}#evidence-locator:"
                f"{source['locator_id']}"
            )

        for row in closure["objects"]:
            ref = str(row["object_ref"])
            source = closure["object_sources"][ref]
            node_id = "domain:tidas-object:" + canonical_sha256(ref)[:32]
            object_nodes[ref] = node_id
            attributes = {
                **deepcopy(dict(row)),
                "locator_id": source["locator_id"],
                "revision_id": source["revision_id"],
                "source_lock_ids": deepcopy(source["source_lock_ids"]),
                "source_locator": deepcopy(source["member_locator"]),
                "selectable": True,
                "project_truth_effect": "none",
            }
            graph.add_node(
                self._node(
                    node_id=node_id,
                    namespace="domain",
                    node_kind=str(row["object_kind"]),
                    label=str(row["label"]),
                    source_ref=source_ref(source),
                    source_sha256=str(source["content_sha256"]),
                    source_version=str(row["version"]),
                    authority_class="accepted_project_truth",
                    classification="accepted",
                    attributes=attributes,
                )
            )
            locator_node = (
                "evidence:locator:"
                + _opaque_graph_identity(source["locator_id"], "locator_id")
            )
            graph.add_edge(
                self._edge(
                    edge_kind="supported_by",
                    source_id=node_id,
                    target_id=locator_node,
                    source_ref=source_ref(source),
                    source_sha256=str(source["content_sha256"]),
                    source_version=str(row["version"]),
                    authority_class="accepted_project_truth",
                    derivation={
                        "kind": "tidas_object_source",
                        "locator_id": source["locator_id"],
                        "source_lock_ids": deepcopy(source["source_lock_ids"]),
                    },
                )
            )
            if row["object_kind"] in VERSIONED_DATASET_KINDS:
                version_groups.setdefault(
                    (str(row["object_kind"]), str(row["identity"])), []
                ).append(row)

        for (kind, identity), rows in sorted(version_groups.items()):
            rows = sorted(rows, key=lambda row: (str(row["version"]), row["object_ref"]))
            dataset_identity = f"{kind}:{identity}"
            dataset_node = (
                "domain:tidas-dataset:"
                + canonical_sha256(dataset_identity)[:32]
            )
            dataset_sha = canonical_sha256(
                [
                    {
                        "object_ref": row["object_ref"],
                        "content_sha256": closure["object_sources"][
                            row["object_ref"]
                        ]["content_sha256"],
                    }
                    for row in rows
                ]
            )
            graph.add_node(
                self._node(
                    node_id=dataset_node,
                    namespace="domain",
                    node_kind="Dataset",
                    label=identity,
                    source_ref=(
                        f"project-record:{record_sha}#tidas-dataset:{dataset_identity}"
                    ),
                    source_sha256=dataset_sha,
                    source_version="content-addressed-version-set",
                    authority_class="accepted_project_truth",
                    classification="accepted",
                    attributes={
                        "dataset_kind": kind,
                        "identity": identity,
                        "versions": [row["version"] for row in rows],
                        "locator_ids": [
                            closure["object_sources"][row["object_ref"]][
                                "locator_id"
                            ]
                            for row in rows
                        ],
                        "semantic_roles": ["dataset_identity"],
                        "selectable": True,
                        "project_truth_effect": "none",
                    },
                )
            )
            for row in rows:
                source = closure["object_sources"][row["object_ref"]]
                version_node = (
                    "domain:tidas-version:"
                    + canonical_sha256(row["object_ref"])[0:32]
                )
                graph.add_node(
                    self._node(
                        node_id=version_node,
                        namespace="domain",
                        node_kind="DatasetVersion",
                        label=str(row["version"]),
                        source_ref=source_ref(source),
                        source_sha256=str(source["content_sha256"]),
                        source_version=str(row["version"]),
                        authority_class="accepted_project_truth",
                        classification="accepted",
                        attributes={
                            "object_ref": row["object_ref"],
                            "object_kind": row["object_kind"],
                            "identity": row["identity"],
                            "version": row["version"],
                            "semantic_roles": ["dataset_version"],
                            "locator_id": source["locator_id"],
                            "source_lock_ids": deepcopy(source["source_lock_ids"]),
                            "selectable": True,
                            "project_truth_effect": "none",
                        },
                    )
                )
                derivation = {
                    "kind": "tidas_version_binding",
                    "locator_id": source["locator_id"],
                    "source_lock_ids": deepcopy(source["source_lock_ids"]),
                }
                for edge_kind, source_id, target_id in (
                    ("has_version", dataset_node, version_node),
                    (
                        "materializes_version",
                        version_node,
                        object_nodes[row["object_ref"]],
                    ),
                ):
                    graph.add_edge(
                        self._edge(
                            edge_kind=edge_kind,
                            source_id=source_id,
                            target_id=target_id,
                            source_ref=source_ref(source),
                            source_sha256=str(source["content_sha256"]),
                            source_version=str(row["version"]),
                            authority_class="accepted_project_truth",
                            derivation=derivation,
                        )
                    )

            ordered_versions = [
                (version_key(str(row["version"])), row)
                for row in rows
            ]
            if len(rows) > 1 and all(key is not None for key, _ in ordered_versions):
                ordered_versions.sort(key=lambda item: item[0])
                for (_, previous), (_, current) in zip(
                    ordered_versions, ordered_versions[1:], strict=False
                ):
                    current_source = closure["object_sources"][
                        current["object_ref"]
                    ]
                    previous_source = closure["object_sources"][
                        previous["object_ref"]
                    ]
                    graph.add_edge(
                        self._edge(
                            edge_kind="supersedes",
                            source_id=object_nodes[current["object_ref"]],
                            target_id=object_nodes[previous["object_ref"]],
                            source_ref=source_ref(current_source),
                            source_sha256=str(current_source["content_sha256"]),
                            source_version=str(current["version"]),
                            authority_class="accepted_project_truth",
                            derivation={
                                "kind": "deterministic_tidas_version_order",
                                "locator_id": current_source["locator_id"],
                                "previous_locator_id": previous_source["locator_id"],
                                "source_lock_ids": deepcopy(
                                    current_source["source_lock_ids"]
                                ),
                            },
                        )
                    )

        for relation in closure["relations"]:
            source = relation["source"]
            graph.add_edge(
                self._edge(
                    edge_kind=str(relation["edge_kind"]),
                    source_id=object_nodes[relation["source_object_ref"]],
                    target_id=object_nodes[relation["target_object_ref"]],
                    source_ref=source_ref(source),
                    source_sha256=str(source["content_sha256"]),
                    source_version=str(source["revision_id"]),
                    authority_class="accepted_project_truth",
                    derivation={
                        "kind": "tidas_structured_relation",
                        "locator_id": source["locator_id"],
                        "source_lock_ids": deepcopy(source["source_lock_ids"]),
                        "relation_attributes": deepcopy(
                            relation["attributes"]
                        ),
                    },
                )
            )

    def _project_generated_artifact_derivations(
        self,
        graph: SemanticGraph,
        *,
        project_view: Mapping[str, object],
        locator_node_by_id: Mapping[str, str],
    ) -> None:
        artifacts = project_view.get("artifacts") or {}
        revisions = project_view.get("material_revisions") or {}
        if not isinstance(artifacts, Mapping) or not isinstance(revisions, Mapping):
            raise ValueError("semantic projection artifact derivation state is invalid")
        record_sha = str(project_view["last_event_sha256"])
        record_version = str(project_view["last_event_id"])
        for path, artifact in sorted(artifacts.items()):
            if not isinstance(artifact, Mapping):
                raise ValueError("semantic projection artifact is invalid")
            derived_from = artifact.get("derived_from")
            if derived_from is None:
                continue
            if not isinstance(derived_from, Mapping) or set(derived_from) != {
                "material_revision_ids",
                "evidence_locator_ids",
            }:
                raise ValueError("semantic projection artifact derivation is invalid")
            revision_ids = derived_from.get("material_revision_ids")
            locator_ids = derived_from.get("evidence_locator_ids")
            if (
                not isinstance(revision_ids, list)
                or not isinstance(locator_ids, list)
                or not revision_ids + locator_ids
                or any(not isinstance(item, str) or not item for item in revision_ids + locator_ids)
                or len(revision_ids) != len(set(revision_ids))
                or len(locator_ids) != len(set(locator_ids))
            ):
                raise ValueError("semantic projection artifact derivation is invalid")
            source_id = f"evidence:artifact:{canonical_sha256(path)[:24]}"
            digest = artifact.get("sha256")
            if graph.node(source_id) is None or not isinstance(digest, str):
                raise ValueError("semantic projection generated artifact is absent")
            targets = []
            for revision_id in revision_ids:
                if revision_id not in revisions:
                    raise ValueError(
                        "semantic projection artifact material revision is absent"
                    )
                targets.append(
                    "project:material_revision:"
                    + _safe_identity(revision_id, "revision_id")
                )
            for locator_id in locator_ids:
                target_id = locator_node_by_id.get(locator_id)
                if not isinstance(target_id, str):
                    raise ValueError(
                        "semantic projection artifact evidence locator is absent"
                    )
                targets.append(target_id)
            for target_id in sorted(targets):
                graph.add_edge(
                    self._edge(
                        edge_kind="derived_from",
                        source_id=source_id,
                        target_id=target_id,
                        source_ref=f"project-record:{record_sha}#artifact:{path}",
                        source_sha256=digest,
                        source_version=record_version,
                        authority_class="accepted_project_truth",
                    )
                )

    def _project_accepted_material_fact_relationships(
        self,
        graph: SemanticGraph,
        *,
        project_view: Mapping[str, object],
        locator_node_by_id: Mapping[str, str],
    ) -> None:
        """Project accepted fact provenance and deterministic contradictions."""

        facts = project_view.get("facts") or {}
        if not isinstance(facts, Mapping):
            raise ValueError("semantic projection facts are invalid")
        role_rows: dict[str, list[tuple[str, Mapping[str, object], list[str]]]] = {}
        record_sha = str(project_view["last_event_sha256"])
        record_version = str(project_view["last_event_id"])
        for fact_id, fact in sorted(facts.items()):
            if not isinstance(fact, Mapping) or fact.get("classification") not in {
                "official",
                "computed",
            }:
                continue
            role = fact.get("semantic_role") or fact.get("domain_kind")
            source = fact.get("source")
            locator_ids = (
                source.get("evidence_locator_ids")
                if isinstance(source, Mapping)
                else None
            )
            if (
                not isinstance(role, str)
                or not role
                or not isinstance(locator_ids, list)
                or any(
                    not isinstance(locator_id, str)
                    or locator_id not in locator_node_by_id
                    for locator_id in locator_ids
                )
            ):
                continue
            fact_node = f"project:fact:{_safe_identity(fact_id, 'fact_id')}"
            if graph.node(fact_node) is None:
                continue
            exact_ids = sorted(set(locator_ids))
            role_rows.setdefault(role, []).append((fact_node, fact, exact_ids))
            for locator_id in exact_ids:
                source_node = locator_node_by_id[locator_id]
                for edge_kind in ("supports", "derived_from"):
                    graph.add_edge(
                        self._edge(
                            edge_kind=edge_kind,
                            source_id=source_node,
                            target_id=fact_node,
                            source_ref=f"project-record:{record_sha}#fact:{fact_id}",
                            source_sha256=str(
                                project_view["evidence_locators"][locator_id][
                                    "content_hash"
                                ]
                            ),
                            source_version=record_version,
                            authority_class="accepted_project_truth",
                        )
                    )
        for rows in role_rows.values():
            for index, (left_fact_node, left_fact, left_locators) in enumerate(rows):
                for right_fact_node, right_fact, right_locators in rows[index + 1 :]:
                    if canonical_sha256(left_fact.get("value")) == canonical_sha256(
                        right_fact.get("value")
                    ):
                        continue
                    pairs = sorted(
                        (left, right)
                        for left in left_locators
                        for right in right_locators
                        if left != right
                    )
                    for left, right in pairs:
                        left_node = locator_node_by_id[left]
                        right_node = locator_node_by_id[right]
                        relation_sha = canonical_sha256(
                            {
                                "left_fact": left_fact_node,
                                "right_fact": right_fact_node,
                                "left_locator": left,
                                "right_locator": right,
                            }
                        )
                        graph.add_edge(
                            self._edge(
                                edge_kind="conflicts_with",
                                source_id=left_node,
                                target_id=right_node,
                                source_ref=(
                                    f"project-record:{record_sha}#deterministic-conflict"
                                ),
                                source_sha256=relation_sha,
                                source_version=record_version,
                                authority_class="accepted_project_truth",
                            )
                        )
