"""State-driven problem signatures and graph path retrieval for D-18."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional

from .semantic_graph import SemanticGraph, canonical_sha256


SIGNATURE_CONTRACT = "lca_research_kernel.research_problem_signature/v1"
EXPECTED_CHANGE_CONTRACT = "lca_research_kernel.expected_state_change/v1"
RETRIEVAL_CONTRACT = "lca_research_kernel.semantic_retrieval_result/v1"
VIEW_CONTRACT = "lca_research_kernel.project_record_view/v1"
QUERY_MODES = {
    "exact_identity",
    "local_entity",
    "dependency_path",
    "temporal_version",
    "global_structure",
    "exploratory_drift",
}
ACCEPTED_AUTHORITIES = {
    "accepted_project_truth",
    "tiangong_official",
    "governed_method",
    "deterministic_computation",
}
ACCEPTED_CLASSIFICATIONS = {"official", "accepted", "computed", "missing"}
IDENTITY_ARTIFACTS = {
    "identity_confirmation",
    "dataset_identity_confirmation",
    "version_identity_confirmation",
}
MODE_HOPS = {
    "exact_identity": 0,
    "local_entity": 1,
    "dependency_path": 3,
    "temporal_version": 2,
    "global_structure": 4,
    "exploratory_drift": 2,
}


def _sorted_strings(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"research problem {label} is invalid")
    return sorted(set(value))


def _required_bool(value: Mapping[str, object], key: str) -> bool:
    item = value.get(key)
    if not isinstance(item, bool):
        raise ValueError(f"expected state change {key} is invalid")
    return item


def _accepted(value: Mapping[str, object]) -> bool:
    accepted = (
        value.get("authority_class") in ACCEPTED_AUTHORITIES
        and value.get("classification") in ACCEPTED_CLASSIFICATIONS
    )
    attributes = value.get("attributes")
    if (
        isinstance(attributes, Mapping)
        and "selectable" in attributes
        and attributes.get("selectable") is not True
    ):
        return False
    return accepted


def _node_roles(node: Mapping[str, object]) -> set[str]:
    result = {
        str(node.get("node_kind") or "").lower(),
        str(node.get("namespace") or "").lower(),
    }
    attributes = node.get("attributes")
    if isinstance(attributes, Mapping):
        roles = attributes.get("semantic_roles")
        if isinstance(roles, list):
            result.update(item for item in roles if isinstance(item, str) and item)
        role = attributes.get("semantic_role")
        if isinstance(role, str) and role:
            result.add(role)
        domain_kind = attributes.get("domain_kind")
        if isinstance(domain_kind, str) and domain_kind:
            result.add(domain_kind)
            result.add(
                {
                    "official_source_material": "source_material",
                    "source_bound_research_material": "active_source_material",
                    "capability_result": "capability_result",
                    "user_turn": "active_user_turn",
                    "research_goal": "research_goal",
                }.get(domain_kind, domain_kind)
            )
    return result


class ProblemSignatureCompiler:
    """Compile retrieval intent from accepted structured state, never wording."""

    def compile(
        self,
        *,
        project_view: Mapping[str, object],
        expected_state_change: Mapping[str, object],
        generation: int,
    ) -> dict:
        if (
            not isinstance(project_view, Mapping)
            or project_view.get("contract") != VIEW_CONTRACT
            or not isinstance(project_view.get("project_id"), str)
        ):
            raise ValueError("research problem Project Record view is invalid")
        if (
            not isinstance(expected_state_change, Mapping)
            or expected_state_change.get("contract") != EXPECTED_CHANGE_CONTRACT
        ):
            raise ValueError("expected state change contract is unsupported")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise ValueError("research problem generation is invalid")
        desired_artifact = expected_state_change.get("desired_artifact")
        claim_ceiling = expected_state_change.get("claim_ceiling")
        if not isinstance(desired_artifact, str) or not desired_artifact.strip():
            raise ValueError("expected state change desired_artifact is invalid")
        if not isinstance(claim_ceiling, str) or not claim_ceiling.strip():
            raise ValueError("expected state change claim_ceiling is invalid")
        target_kinds = _sorted_strings(
            expected_state_change.get("target_object_kinds"), "target_object_kinds"
        )
        exact_node_ids = _sorted_strings(
            expected_state_change.get("exact_node_ids"), "exact_node_ids"
        )
        required_roles = _sorted_strings(
            expected_state_change.get("required_evidence_roles"),
            "required_evidence_roles",
        )
        conflicts = _sorted_strings(
            expected_state_change.get("conflict_types"), "conflict_types"
        )
        independent = _sorted_strings(
            expected_state_change.get("independent_evidence_dimensions"),
            "independent_evidence_dimensions",
        )
        completion = _sorted_strings(
            expected_state_change.get("completion_predicates"),
            "completion_predicates",
        )
        accepted_roles = set()
        explicit_missing = set()
        accepted_value_hashes: dict[str, set[str]] = {}
        facts = project_view.get("facts") or {}
        if not isinstance(facts, Mapping):
            raise ValueError("research problem Project Record facts are invalid")
        for fact_id, fact in facts.items():
            if not isinstance(fact, Mapping) or fact.get("fact_id") != fact_id:
                raise ValueError("research problem fact identity drifted")
            role = self._fact_role(fact)
            if not isinstance(role, str) or not role:
                continue
            classification = fact.get("classification")
            if classification == "missing":
                explicit_missing.add(role)
            elif (
                classification in {"official", "computed", "assumption"}
                and role in required_roles
            ):
                accepted_roles.add(role)
                accepted_value_hashes.setdefault(role, set()).add(
                    canonical_sha256(fact.get("value"))
                )
                node_id = fact.get("node_id")
                if isinstance(node_id, str) and node_id:
                    exact_node_ids.append(node_id)
        conflicting_roles = {
            role
            for role, value_hashes in accepted_value_hashes.items()
            if len(value_hashes) > 1
        }
        accepted_roles -= conflicting_roles
        conflicts = sorted(
            {
                *conflicts,
                *(f"deterministic_project_fact_conflict:{role}" for role in conflicting_roles),
            }
        )
        exact_node_ids = sorted(set(exact_node_ids))
        missing_roles = sorted(
            (set(required_roles) | explicit_missing | conflicting_roles)
            - accepted_roles
        )
        requirements = {
            "calculation": _required_bool(expected_state_change, "calculation_required"),
            "source_confirmation": _required_bool(
                expected_state_change, "source_confirmation_required"
            ),
            "comparison": _required_bool(expected_state_change, "comparison_required"),
            "global_structure": _required_bool(
                expected_state_change, "global_structure_required"
            ),
            "unknown_target": _required_bool(expected_state_change, "unknown_target"),
        }
        query_mode = self._query_mode(
            desired_artifact=desired_artifact,
            exact_node_ids=exact_node_ids,
            required_roles=required_roles,
            conflicts=conflicts,
            requirements=requirements,
        )
        signature = {
            "contract": SIGNATURE_CONTRACT,
            "project_id": project_view["project_id"],
            "generation": generation,
            "desired_artifact": desired_artifact,
            "target_object_kinds": target_kinds,
            "exact_node_ids": exact_node_ids,
            "accepted_roles": sorted(accepted_roles),
            "conflicting_roles": sorted(conflicting_roles),
            "required_evidence_roles": required_roles,
            "missing_roles": missing_roles,
            "requirements": requirements,
            "conflict_types": conflicts,
            "claim_ceiling": claim_ceiling,
            "independent_evidence_dimensions": independent,
            "completion_predicates": completion,
            "query_mode": query_mode,
        }
        signature["signature_sha256"] = canonical_sha256(signature)
        return signature

    @staticmethod
    def _fact_role(fact: Mapping[str, object]) -> Optional[str]:
        role = fact.get("semantic_role")
        if isinstance(role, str) and role:
            return role
        domain_kind = fact.get("domain_kind")
        if not isinstance(domain_kind, str) or not domain_kind:
            return None
        return {
            "official_source_material": "source_material",
            "source_bound_research_material": "active_source_material",
            "capability_result": "capability_result",
            "user_turn": "active_user_turn",
            "research_goal": "research_goal",
        }.get(domain_kind, domain_kind)

    @staticmethod
    def _query_mode(
        *,
        desired_artifact: str,
        exact_node_ids: list[str],
        required_roles: list[str],
        conflicts: list[str],
        requirements: Mapping[str, bool],
    ) -> str:
        if any("version" in conflict or "temporal" in conflict for conflict in conflicts):
            return "temporal_version"
        if requirements["global_structure"]:
            return "global_structure"
        if requirements["unknown_target"] or not exact_node_ids:
            return "exploratory_drift"
        if required_roles:
            return "dependency_path"
        if desired_artifact in IDENTITY_ARTIFACTS:
            return "exact_identity"
        return "local_entity"


class SemanticRetriever:
    """Retrieve minimal, typed, accepted-authority paths for one signature."""

    def __init__(
        self,
        semantic_candidate_provider: Optional[
            Callable[[Mapping[str, object], SemanticGraph], Iterable[str]]
        ] = None,
    ):
        self.semantic_candidate_provider = semantic_candidate_provider

    def retrieve(
        self, graph: SemanticGraph, signature: Mapping[str, object]
    ) -> dict:
        normalized = self._validate_signature(graph, signature)
        mode = str(normalized["query_mode"])
        seeds = [
            node_id
            for node_id in normalized["exact_node_ids"]
            if graph.node(node_id) is not None
        ]
        candidate_seed_ids = []
        if not seeds and self.semantic_candidate_provider is not None:
            candidate_seed_ids = sorted(
                {
                    node_id
                    for node_id in self.semantic_candidate_provider(normalized, graph)
                    if isinstance(node_id, str) and graph.node(node_id) is not None
                }
            )
            seeds.extend(candidate_seed_ids)
        if not seeds and graph.node("project:goal") is not None:
            seeds.append("project:goal")
        required_roles = list(normalized["required_evidence_roles"])
        paths = []
        exclusions = []
        covered = set()
        if required_roles:
            for role in required_roles:
                selected, rejected = self._best_role_path(
                    graph, seeds, role, mode=mode
                )
                exclusions.extend(rejected)
                if selected is not None:
                    paths.append(selected)
                    covered.add(role)
        else:
            paths.extend(self._seed_paths(graph, seeds, mode=mode))
        if mode == "temporal_version":
            paths = self._include_temporal_neighbors(graph, paths, seeds)
        if mode == "global_structure" and not required_roles:
            paths.extend(self._neighborhood_paths(graph, seeds, max_hops=4))
        exclusions.extend(self._candidate_exclusions(graph, required_roles))
        paths = self._deduplicate_paths(paths)
        exclusions = self._deduplicate_exclusions(exclusions)
        selected_nodes = sorted(
            {node_id for path in paths for node_id in path["node_ids"]}
        )
        result = {
            "contract": RETRIEVAL_CONTRACT,
            "project_id": graph.project_id,
            "generation": graph.generation,
            "graph_sha256": graph.graph_sha256,
            "signature_sha256": normalized["signature_sha256"],
            "query_mode": mode,
            "seed_node_ids": sorted(seeds),
            "semantic_candidate_seed_ids": candidate_seed_ids,
            "paths": paths,
            "selected_node_ids": selected_nodes,
            "covered_roles": sorted(covered),
            "uncovered_roles": sorted(set(required_roles) - covered),
            "exclusions": exclusions,
            "coverage": (
                1.0
                if not required_roles
                else len(covered) / len(set(required_roles))
            ),
            "authority_rule": "accepted_typed_paths_only",
        }
        result["retrieval_sha256"] = canonical_sha256(result)
        return result

    def _validate_signature(
        self, graph: SemanticGraph, signature: Mapping[str, object]
    ) -> dict:
        if not isinstance(signature, Mapping) or signature.get("contract") != SIGNATURE_CONTRACT:
            raise ValueError("research problem signature contract is unsupported")
        normalized = deepcopy(dict(signature))
        stored = normalized.pop("signature_sha256", None)
        if not isinstance(stored, str) or canonical_sha256(normalized) != stored:
            raise ValueError("research problem signature drifted")
        normalized["signature_sha256"] = stored
        if (
            normalized.get("project_id") != graph.project_id
            or normalized.get("generation") != graph.generation
        ):
            raise ValueError("research problem graph scope drifted")
        if normalized.get("query_mode") not in QUERY_MODES:
            raise ValueError("research problem query mode is unsupported")
        for key in (
            "exact_node_ids",
            "required_evidence_roles",
            "missing_roles",
            "target_object_kinds",
        ):
            _sorted_strings(normalized.get(key), key)
        return normalized

    def _best_role_path(
        self,
        graph: SemanticGraph,
        seeds: list[str],
        role: str,
        *,
        mode: str,
    ) -> tuple[Optional[dict], list[dict]]:
        candidates = []
        rejected = []
        role_nodes = [row for row in graph.nodes if role in _node_roles(row)]
        paths_by_seed = {
            seed: self._bounded_paths_from(
                graph, seed, max_hops=MODE_HOPS[mode]
            )
            for seed in seeds
        }
        for node in role_nodes:
            # Governed method knowledge explains a dependency but cannot
            # satisfy a Project Record evidence requirement.  Keeping this
            # boundary here prevents a method/RAG hit from closing a factual
            # gap before Project Record acceptance.
            if node.get("authority_class") == "governed_method":
                continue
            if node.get("classification") == "missing":
                rejected.append(
                    {
                        "identity": node["node_id"],
                        "required_role": role,
                        "reason": "typed_missing_dependency",
                    }
                )
                continue
            if not _accepted(node):
                rejected.append(
                    {
                        "identity": node["node_id"],
                        "required_role": role,
                        "reason": "candidate_or_inferred_authority",
                    }
                )
                continue
            for seed in seeds:
                raw_path = paths_by_seed[seed].get(str(node["node_id"]))
                if raw_path is None:
                    continue
                candidates.append(
                    self._path_payload(
                        graph,
                        raw_path[0],
                        raw_path[1],
                        required_role=role,
                        reason=f"covers_required_role:{role}",
                    )
                )
        if not candidates:
            return None, rejected
        candidates.sort(
            key=lambda row: (
                row["hop_count"],
                -self._path_retrieval_priority(graph, row),
                -row["authority_score"],
                row["path_sha256"],
            )
        )
        return candidates[0], rejected

    @staticmethod
    def _path_retrieval_priority(
        graph: SemanticGraph, path: Mapping[str, object]
    ) -> int:
        score = 0
        for node_id in path.get("node_ids", []):
            node = graph.node(str(node_id))
            attributes = node.get("attributes") if isinstance(node, Mapping) else None
            priority = (
                attributes.get("retrieval_priority")
                if isinstance(attributes, Mapping)
                else None
            )
            if isinstance(priority, int) and not isinstance(priority, bool):
                score += priority
        return score

    def _shortest_path(
        self,
        graph: SemanticGraph,
        start_id: str,
        target_id: str,
        *,
        max_hops: int,
    ) -> Optional[tuple[list[str], list[str]]]:
        return self._bounded_paths_from(
            graph, start_id, max_hops=max_hops
        ).get(target_id)

    def _bounded_paths_from(
        self,
        graph: SemanticGraph,
        start_id: str,
        *,
        max_hops: int,
    ) -> dict[str, tuple[list[str], list[str]]]:
        start = graph.node(start_id)
        if start is None or not _accepted(start):
            return {}
        paths = {start_id: ([start_id], [])}
        queue = deque([start_id])
        while queue:
            current = queue.popleft()
            node_ids, edge_ids = paths[current]
            if len(edge_ids) >= max_hops:
                continue
            for adjacent, edge in self._accepted_adjacent(graph, current):
                next_id = str(adjacent["node_id"])
                if next_id in paths:
                    continue
                paths[next_id] = (
                    node_ids + [next_id],
                    edge_ids + [str(edge["edge_id"])],
                )
                queue.append(next_id)
        return paths

    @staticmethod
    def _accepted_adjacent(
        graph: SemanticGraph, node_id: str
    ) -> list[tuple[dict, dict]]:
        results = [
            (row["node"], row["edge"])
            for row in graph.adjacent(
                node_id, accepted_fact_required=True
            )
        ]
        return sorted(results, key=lambda row: (row[1]["edge_kind"], row[0]["node_id"]))

    def _seed_paths(
        self, graph: SemanticGraph, seeds: list[str], *, mode: str
    ) -> list[dict]:
        paths = []
        for seed in sorted(seeds):
            node = graph.node(seed)
            if node is None or not _accepted(node):
                continue
            paths.append(
                self._path_payload(
                    graph,
                    [seed],
                    [],
                    required_role=None,
                    reason="exact_seed" if mode == "exact_identity" else "local_seed",
                )
            )
            if mode == "local_entity":
                paths.extend(self._neighborhood_paths(graph, [seed], max_hops=1))
        return paths

    def _neighborhood_paths(
        self, graph: SemanticGraph, seeds: list[str], *, max_hops: int
    ) -> list[dict]:
        paths = []
        for seed in sorted(seeds):
            reachable = self._bounded_paths_from(
                graph, seed, max_hops=max_hops
            )
            nodes_by_id = {str(node["node_id"]): node for node in graph.nodes}
            for target in sorted(reachable):
                if target == seed:
                    continue
                node = nodes_by_id[target]
                attributes = node.get("attributes")
                if (
                    isinstance(attributes, Mapping)
                    and isinstance(attributes.get("knowledge_id"), str)
                    and attributes.get("project_truth_effect") == "none"
                ):
                    # Governed explanations are selected by explicit
                    # knowledge roles.  A broad graph neighborhood must not
                    # disclose every method entry merely because each one is
                    # connected to the project goal.
                    continue
                path = reachable[target]
                paths.append(
                    self._path_payload(
                        graph,
                        path[0],
                        path[1],
                        required_role=None,
                        reason="bounded_neighborhood",
                    )
                )
        return paths

    def _include_temporal_neighbors(
        self, graph: SemanticGraph, paths: list[dict], seeds: list[str]
    ) -> list[dict]:
        result = list(paths)
        for seed in seeds:
            for adjacent, edge in self._accepted_adjacent(graph, seed):
                if edge.get("edge_kind") != "supersedes":
                    continue
                result.append(
                    self._path_payload(
                        graph,
                        [seed, str(adjacent["node_id"])],
                        [str(edge["edge_id"])],
                        required_role="dataset_version",
                        reason="temporal_supersession",
                    )
                )
        return result

    @staticmethod
    def _path_payload(
        graph: SemanticGraph,
        node_ids: list[str],
        edge_ids: list[str],
        *,
        required_role: Optional[str],
        reason: str,
    ) -> dict:
        authorities = []
        for node_id in node_ids:
            node = graph.node(node_id)
            if node is not None:
                authorities.append(str(node["authority_class"]))
        payload = {
            "contract": "lca_research_kernel.semantic_retrieval_path/v1",
            "node_ids": list(node_ids),
            "edge_ids": list(edge_ids),
            "hop_count": len(edge_ids),
            "required_role": required_role,
            "reason": reason,
            "accepted_authority": all(
                authority in ACCEPTED_AUTHORITIES for authority in authorities
            ),
            "authority_score": min(
                [
                    {
                        "tiangong_official": 4,
                        "accepted_project_truth": 4,
                        "governed_method": 3,
                        "deterministic_computation": 3,
                    }.get(authority, 0)
                    for authority in authorities
                ]
                or [0]
            ),
        }
        payload["path_sha256"] = canonical_sha256(payload)
        return payload

    @staticmethod
    def _candidate_exclusions(
        graph: SemanticGraph, required_roles: list[str]
    ) -> list[dict]:
        result = []
        for node in graph.nodes:
            if _accepted(node):
                continue
            matching = sorted(set(required_roles).intersection(_node_roles(node)))
            if matching:
                result.append(
                    {
                        "identity": node["node_id"],
                        "required_role": matching[0],
                        "reason": "candidate_or_inferred_authority",
                    }
                )
        return result

    @staticmethod
    def _deduplicate_paths(paths: list[dict]) -> list[dict]:
        unique = {row["path_sha256"]: row for row in paths}
        return sorted(
            unique.values(),
            key=lambda row: (
                "" if row["required_role"] is None else row["required_role"],
                row["hop_count"],
                row["path_sha256"],
            ),
        )

    @staticmethod
    def _deduplicate_exclusions(rows: list[dict]) -> list[dict]:
        unique = {
            (row["identity"], row.get("required_role"), row["reason"]): row
            for row in rows
        }
        return [unique[key] for key in sorted(unique, key=str)]
