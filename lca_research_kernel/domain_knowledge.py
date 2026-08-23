"""Source-bound knowledge queries and minimal, replayable knowledge slices.

This is a private deep implementation inside the existing LCA Domain Kernel
and Context Assembler responsibilities.  It is not Project Record truth and it
does not turn a knowledge or graph hit into a project fact.
"""

from __future__ import annotations

import re
from collections import defaultdict
from copy import deepcopy
from typing import Callable, Mapping, Sequence

from .semantic_graph import canonical_sha256


QUERY_CONTRACT = "lca_research_kernel.domain_knowledge_query/v1"
SLICE_CONTRACT = "lca_research_kernel.domain_knowledge_slice/v1"
QUERY_FIELDS = {
    "contract",
    "cognitive_operation",
    "answer_obligations",
    "required_roles",
    "referent_refs",
    "target_object_kinds",
    "relation_kinds",
    "query_mode",
    "claim_ceiling",
    "applicability",
    "query_sha256",
}
APPLICABILITY_FIELDS = {
    "geographies",
    "times",
    "technologies",
    "methods",
}
QUERY_MODES = {
    "exact_identity",
    "local_entity",
    "dependency_path",
    "temporal_version",
    "global_structure",
    "exploratory_drift",
}
COGNITIVE_OPERATIONS = {
    "extract",
    "explain",
    "apply",
    "compare",
    "calculate",
    "interpret",
    "audit",
    "research",
}
REFERENCE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*:[^\s:][^\s]*$")


def _strings(
    value: object,
    label: str,
    *,
    sorted_output: bool = True,
) -> list[str]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError(f"domain knowledge {label} are invalid")
    normalized = list(value)
    return sorted(normalized) if sorted_output else normalized


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"domain knowledge {label} is invalid")
    return value


class SourceBoundDomainKnowledgeIndex:
    """Compile stable queries and hide indexing, coverage and source lineage."""

    def __init__(
        self,
        entries: Sequence[Mapping[str, object]],
        *,
        render_entry: Callable[[str, str], Mapping[str, object]],
    ) -> None:
        if not entries or any(not isinstance(row, Mapping) for row in entries):
            raise ValueError("domain knowledge index entries are invalid")
        self._render_entry = render_entry
        self._entries: dict[str, dict] = {}
        self._by_role: dict[str, set[str]] = defaultdict(set)
        self._by_object_kind: dict[str, set[str]] = defaultdict(set)
        self._by_relation: dict[str, set[str]] = defaultdict(set)
        self._applicability_by_id: dict[str, dict[str, list[str]]] = {}
        for raw in entries:
            row = deepcopy(dict(raw))
            knowledge_id = _text(row.get("knowledge_id"), "entry identity")
            if knowledge_id in self._entries:
                raise ValueError("domain knowledge entry identity is duplicated")
            roles = _strings(row.get("knowledge_roles"), "entry roles")
            source_kind = _text(row.get("source_kind"), "source kind")
            target_kind = _text(row.get("target_kind"), "target kind")
            relation = _text(row.get("relation"), "relation")
            applicability = self._entry_applicability(row.get("applicability"))
            self._entries[knowledge_id] = row
            self._applicability_by_id[knowledge_id] = applicability
            for role in roles:
                self._by_role[role].add(knowledge_id)
            self._by_object_kind[source_kind].add(knowledge_id)
            self._by_object_kind[target_kind].add(knowledge_id)
            self._by_relation[relation].add(knowledge_id)

    def compile_query(
        self,
        *,
        cognitive_operation: str,
        answer_obligations: Sequence[str],
        required_roles: Sequence[str],
        referent_refs: Sequence[str] = (),
        target_object_kinds: Sequence[str] = (),
        relation_kinds: Sequence[str] = (),
        query_mode: str,
        claim_ceiling: str,
        applicability: Mapping[str, object] | None = None,
    ) -> dict:
        if cognitive_operation not in COGNITIVE_OPERATIONS:
            raise ValueError("domain knowledge cognitive operation is invalid")
        if query_mode not in QUERY_MODES:
            raise ValueError("domain knowledge query mode is invalid")
        refs = _strings(referent_refs, "referent refs")
        if any(REFERENCE_RE.fullmatch(ref) is None for ref in refs):
            raise ValueError("domain knowledge referent refs are invalid")
        normalized_applicability = self._applicability(applicability or {})
        query = {
            "contract": QUERY_CONTRACT,
            "cognitive_operation": cognitive_operation,
            "answer_obligations": _strings(
                answer_obligations, "answer obligations"
            ),
            "required_roles": _strings(required_roles, "required roles"),
            "referent_refs": refs,
            "target_object_kinds": _strings(
                target_object_kinds, "target object kinds"
            ),
            "relation_kinds": _strings(relation_kinds, "relation kinds"),
            "query_mode": query_mode,
            "claim_ceiling": _text(claim_ceiling, "claim ceiling"),
            "applicability": normalized_applicability,
        }
        query["query_sha256"] = canonical_sha256(query)
        return query

    def resolve_slice(
        self,
        query: Mapping[str, object],
        *,
        response_language: str,
    ) -> dict:
        normalized = self.validate_query(query)
        candidate_ids = self._candidate_ids(normalized)
        entries = [
            deepcopy(dict(self._render_entry(knowledge_id, response_language)))
            for knowledge_id in candidate_ids
        ]
        for row in entries:
            row["applicability"] = deepcopy(
                self._applicability_by_id[row["knowledge_id"]]
            )
        selected_roles = {
            role
            for row in entries
            for role in row["knowledge_roles"]
        }
        selected_kinds = {
            kind
            for row in entries
            for kind in (row["source_kind"], row["target_kind"])
        }
        selected_relations = {row["relation"] for row in entries}
        requirements = {
            "roles": list(normalized["required_roles"]),
            "target_object_kinds": list(
                normalized["target_object_kinds"]
            ),
            "relation_kinds": list(normalized["relation_kinds"]),
        }
        covered = {
            "roles": sorted(set(requirements["roles"]).intersection(selected_roles)),
            "target_object_kinds": sorted(
                set(requirements["target_object_kinds"]).intersection(
                    selected_kinds
                )
            ),
            "relation_kinds": sorted(
                set(requirements["relation_kinds"]).intersection(
                    selected_relations
                )
            ),
        }
        uncovered = {
            key: sorted(set(required) - set(covered[key]))
            for key, required in requirements.items()
        }
        payload = {
            "contract": SLICE_CONTRACT,
            "query_sha256": normalized["query_sha256"],
            "selected_knowledge_ids": candidate_ids,
            "entries": entries,
            "relation_paths": [
                {
                    "knowledge_id": row["knowledge_id"],
                    "source_kind": row["source_kind"],
                    "relation": row["relation"],
                    "target_kind": row["target_kind"],
                    "source_ref": deepcopy(row["source_ref"]),
                }
                for row in entries
            ],
            "covered_requirements": covered,
            "uncovered_requirements": uncovered,
            "source_bindings": self._source_bindings(entries),
            "applicability": deepcopy(normalized["applicability"]),
            "claim_ceiling": normalized["claim_ceiling"],
            "claim_ceiling_effect": "cannot_raise",
            "authority_boundary": {
                "project_truth": "project_record_only",
                "knowledge_index": "rebuildable_non_authoritative_projection",
                "knowledge_output": "governed_method_context_only",
            },
        }
        payload["slice_sha256"] = canonical_sha256(payload)
        return payload

    def validate_query(self, value: Mapping[str, object]) -> dict:
        if not isinstance(value, Mapping) or set(value) != QUERY_FIELDS:
            raise ValueError("domain knowledge query fields are invalid")
        normalized = deepcopy(dict(value))
        observed = normalized.pop("query_sha256", None)
        replay = self.compile_query(
            cognitive_operation=normalized.get("cognitive_operation"),
            answer_obligations=normalized.get("answer_obligations"),
            required_roles=normalized.get("required_roles"),
            referent_refs=normalized.get("referent_refs"),
            target_object_kinds=normalized.get("target_object_kinds"),
            relation_kinds=normalized.get("relation_kinds"),
            query_mode=normalized.get("query_mode"),
            claim_ceiling=normalized.get("claim_ceiling"),
            applicability=normalized.get("applicability"),
        )
        if observed != replay["query_sha256"]:
            raise ValueError("domain knowledge query content drifted")
        return replay

    @staticmethod
    def _applicability(value: Mapping[str, object]) -> dict:
        if not isinstance(value, Mapping):
            raise ValueError("domain knowledge applicability is invalid")
        if not value:
            value = {field: [] for field in APPLICABILITY_FIELDS}
        if set(value) != APPLICABILITY_FIELDS:
            raise ValueError("domain knowledge applicability is invalid")
        try:
            return {
                field: _strings(value[field], f"applicability {field}")
                for field in sorted(APPLICABILITY_FIELDS)
            }
        except (KeyError, ValueError) as error:
            raise ValueError("domain knowledge applicability is invalid") from error

    @classmethod
    def _entry_applicability(cls, value: object) -> dict[str, list[str]]:
        if value is None:
            return {field: ["*"] for field in sorted(APPLICABILITY_FIELDS)}
        normalized = cls._applicability(value)  # type: ignore[arg-type]
        if any(not values for values in normalized.values()):
            raise ValueError("domain knowledge entry applicability is invalid")
        return normalized

    def _candidate_ids(self, query: Mapping[str, object]) -> list[str]:
        roles = list(query["required_roles"])
        kinds = list(query["target_object_kinds"])
        relations = list(query["relation_kinds"])
        if not roles and not relations:
            return []
        candidate_ids = set(self._entries)
        if roles:
            role_ids = set().union(*(self._by_role.get(role, set()) for role in roles))
            candidate_ids.intersection_update(role_ids)
        if relations:
            relation_ids = set().union(
                *(self._by_relation.get(relation, set()) for relation in relations)
            )
            candidate_ids.intersection_update(relation_ids)
        if kinds:
            requested_kinds = set(kinds)
            if len(requested_kinds) == 1:
                kind_ids = set().union(
                    *(
                        self._by_object_kind.get(kind, set())
                        for kind in requested_kinds
                    )
                )
            else:
                kind_ids = {
                    knowledge_id
                    for knowledge_id, entry in self._entries.items()
                    if entry["source_kind"] in requested_kinds
                    and entry["target_kind"] in requested_kinds
                }
            candidate_ids.intersection_update(kind_ids)
        query_applicability = query["applicability"]
        candidate_ids = {
            knowledge_id
            for knowledge_id in candidate_ids
            if self._matches_applicability(
                self._applicability_by_id[knowledge_id],
                query_applicability,
            )
        }
        return sorted(candidate_ids)

    @staticmethod
    def _matches_applicability(
        entry: Mapping[str, Sequence[str]],
        query: Mapping[str, Sequence[str]],
    ) -> bool:
        for field in sorted(APPLICABILITY_FIELDS):
            requested = set(query[field])
            available = set(entry[field])
            if requested and "*" not in available and not requested.intersection(
                available
            ):
                return False
        return True

    @staticmethod
    def _source_bindings(entries: Sequence[Mapping[str, object]]) -> list[dict]:
        bindings: dict[str, dict] = {}
        for entry in entries:
            lineage = entry.get("rendering_lineage")
            upstream = (
                lineage.get("upstream_sources")
                if isinstance(lineage, Mapping)
                else None
            )
            if not isinstance(upstream, list):
                continue
            for source in upstream:
                if not isinstance(source, Mapping):
                    continue
                source_id = source.get("source_id")
                source_type = source.get("source_type")
                if not isinstance(source_id, str) or not isinstance(
                    source_type, str
                ):
                    continue
                declared_license = source.get("license_status")
                declared_redistribution = source.get("redistribution_scope")
                if isinstance(declared_license, str) and isinstance(
                    declared_redistribution, str
                ):
                    license_status = declared_license
                    redistribution_scope = declared_redistribution
                elif source_type == "repository_contract":
                    license_status = "project_owned_contract"
                    redistribution_scope = "repository_contract_only"
                else:
                    license_status = (
                        "unverified_link_and_minimal_excerpt_only"
                    )
                    redistribution_scope = "minimal_excerpt_only"
                projected = {
                    "source_id": source_id,
                    "source_type": source_type,
                    "owner": source.get("owner"),
                    "locator": source.get("locator"),
                    "version": source.get("version"),
                    "integrity": source.get("integrity"),
                    "accessed_at": source.get("accessed_at"),
                    "evidence_status": source.get("evidence_status"),
                    "license_status": license_status,
                    "redistribution_scope": redistribution_scope,
                    "source_applicability": deepcopy(
                        source.get("applicability")
                    ),
                    "applicability_status": (
                        "entry_and_source_matched"
                        if isinstance(source.get("applicability"), Mapping)
                        else "generic_calibration_only"
                    ),
                }
                prior = bindings.get(source_id)
                if prior is not None and prior != projected:
                    raise ValueError("domain knowledge source binding drifted")
                bindings[source_id] = projected
        return [bindings[source_id] for source_id in sorted(bindings)]
