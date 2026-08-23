"""Governed LCA knowledge and accepted-claim slices for answer obligations."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Mapping, Sequence

from .content_identity import canonical_sha256
from .domain_knowledge import SourceBoundDomainKnowledgeIndex
from .knowledge_pack import GovernedKnowledgePackSet

REGISTRY_CONTRACT = (
    "lca_research_kernel.lca_explanation_knowledge_registry/v2"
)
ENTRY_FIELDS = {
    "knowledge_id",
    "source_kind",
    "relation",
    "target_kind",
    "statement",
    "renderings",
    "knowledge_roles",
    "authority",
    "authority_status",
    "claim_ceiling",
    "source_ref",
}
CLAIM_FIELDS = {
    "claim_id",
    "text",
    "requires_evidence",
    "citation_tokens",
}
TYPED_CLAIM_FIELDS = {
    "claim_id",
    "claim_kind",
    "text",
    "citation_tokens",
    "knowledge_refs",
    "referent_ids",
    "not_project_fact",
    "satisfies_obligations",
}
_TURN_OBLIGATION_KNOWLEDGE_TARGETS = {
    "lca_relationship": (
        ("Exchange", "Process"),
        ("has_exchange",),
    ),
    "source_value_explanation": (
        ("Quantity", "Unit"),
        ("measured_in",),
    ),
    "calculation_explanation": (
        ("Calculation",),
        ("derived_from",),
    ),
    "illustrative_application": (
        ("Exchange", "Illustration"),
        ("illustrates",),
    ),
    "comparison_basis": (
        ("FunctionalUnit", "ProductSystem", "SystemBoundary"),
        ("defines_comparison_basis_for", "scopes"),
    ),
    "uncertainty_and_limit": (
        ("EvidenceSpan", "ProductSystem"),
        ("limits",),
    ),
}

DOMAIN_KNOWLEDGE_INTENT_CONTRACT = (
    "lca_research_kernel.domain_knowledge_intent/v1"
)
DOMAIN_KNOWLEDGE_INTENT_CATALOG_CONTRACT = (
    "lca_research_kernel.domain_knowledge_intent_catalog/v1"
)
DOMAIN_KNOWLEDGE_INTENT_MAX_ITEMS = 8
DOMAIN_KNOWLEDGE_INTENT_FIELDS = {
    "contract",
    "registry_sha256",
    "selected_knowledge_ids",
    "required_roles",
    "target_object_kinds",
    "relation_kinds",
    "intent_sha256",
}


class GovernedLCAKnowledge:
    """Load exact, source-bound explanatory relations from the repository."""

    def __init__(self, root: Path, registry: Mapping[str, object]):
        self.root = Path(root).resolve()
        self.registry = self._validate_registry(registry)
        self._packs = GovernedKnowledgePackSet(
            self.root,
            self.registry.get("knowledge_pack_refs") or [],
        )
        self.registry_sha256 = hashlib.sha256(
            json.dumps(
                self.registry,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self._index = SourceBoundDomainKnowledgeIndex(
            self.projection_entries(),
            render_entry=self._render_entry,
        )

    @classmethod
    def load(cls, root: Path) -> "GovernedLCAKnowledge":
        path = (
            Path(root).resolve()
            / "lca_research_kernel/contracts/lca-explanation-knowledge-v2.json"
        )
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("governed LCA explanation knowledge is unavailable") from error
        if not isinstance(value, Mapping):
            raise ValueError("governed LCA explanation knowledge must be an object")
        return cls(root, value)

    def projection_entries(self) -> list[dict]:
        """Return source-bound method facts for the rebuildable semantic graph."""

        base = [
            {
                **deepcopy(dict(entry)),
                "applicability": {
                    "geographies": ["*"],
                    "times": ["*"],
                    "technologies": ["*"],
                    "methods": ["generic_lca"],
                },
                "registry_id": self.registry["registry_id"],
                "registry_sha256": self.registry_sha256,
            }
            for entry in self.registry["entries"]
        ]
        packed = [
            {
                **entry,
                "registry_id": self.registry["registry_id"],
                "registry_sha256": self.registry_sha256,
            }
            for entry in self._packs.projection_entries()
        ]
        base_ids = {entry["knowledge_id"] for entry in base}
        if base_ids.intersection(entry["knowledge_id"] for entry in packed):
            raise ValueError("governed LCA knowledge identity is duplicated")
        return base + packed

    def intent_catalog(self) -> dict:
        """Expose a compact L1 selection catalog, never source bodies or facts."""

        entries = [
            {
                "knowledge_id": entry["knowledge_id"],
                "source_kind": entry["source_kind"],
                "relation": entry["relation"],
                "target_kind": entry["target_kind"],
                "knowledge_roles": sorted(entry["knowledge_roles"]),
                "claim_ceiling": entry["claim_ceiling"],
                "applicability": deepcopy(entry["applicability"]),
            }
            for entry in sorted(
                self.projection_entries(), key=lambda row: row["knowledge_id"]
            )
        ]
        catalog = {
            "contract": DOMAIN_KNOWLEDGE_INTENT_CATALOG_CONTRACT,
            "registry_id": self.registry["registry_id"],
            "registry_sha256": self.registry_sha256,
            "max_selected_items": DOMAIN_KNOWLEDGE_INTENT_MAX_ITEMS,
            "entries": entries,
            "authority_boundary": {
                "selection_only": True,
                "project_truth_effect": "none",
                "full_content_disclosed": False,
            },
        }
        catalog["catalog_sha256"] = canonical_sha256(catalog)
        return catalog

    def compile_domain_intent(self, selected_knowledge_ids: object) -> dict:
        """Compile untrusted exact IDs into a registry-bound machine intent."""

        selected = self._turn_strings(
            selected_knowledge_ids, "domain knowledge identities"
        )
        if len(selected) > DOMAIN_KNOWLEDGE_INTENT_MAX_ITEMS:
            raise ValueError("domain knowledge selection is too broad")
        selected = sorted(selected)
        entries_by_id = {
            entry["knowledge_id"]: entry for entry in self.projection_entries()
        }
        unavailable = [
            knowledge_id
            for knowledge_id in selected
            if knowledge_id not in entries_by_id
        ]
        if unavailable:
            raise ValueError("domain knowledge identity is unavailable")
        entries = [entries_by_id[knowledge_id] for knowledge_id in selected]
        intent = {
            "contract": DOMAIN_KNOWLEDGE_INTENT_CONTRACT,
            "registry_sha256": self.registry_sha256,
            "selected_knowledge_ids": selected,
            "required_roles": sorted(
                {
                    role
                    for entry in entries
                    for role in entry["knowledge_roles"]
                }
            ),
            "target_object_kinds": sorted(
                {
                    kind
                    for entry in entries
                    for kind in (entry["source_kind"], entry["target_kind"])
                }
            ),
            "relation_kinds": sorted(
                {entry["relation"] for entry in entries}
            ),
        }
        intent["intent_sha256"] = canonical_sha256(intent)
        return intent

    def validate_domain_intent(self, value: Mapping[str, object]) -> dict:
        """Replay exact selection against the current governed registry."""

        if not isinstance(value, Mapping) or set(value) != (
            DOMAIN_KNOWLEDGE_INTENT_FIELDS
        ):
            raise ValueError("domain knowledge intent fields are invalid")
        expected = self.compile_domain_intent(
            value.get("selected_knowledge_ids")
        )
        if dict(value) != expected:
            raise ValueError("domain knowledge intent content drifted")
        return expected

    def _validate_registry(self, value: Mapping[str, object]) -> dict:
        if (
            not isinstance(value, Mapping)
            or set(value)
            not in (
                {"contract", "registry_id", "entries"},
                {
                    "contract",
                    "registry_id",
                    "knowledge_pack_refs",
                    "entries",
                },
            )
            or value.get("contract") != REGISTRY_CONTRACT
            or not isinstance(value.get("registry_id"), str)
            or not value["registry_id"]
            or not isinstance(value.get("entries"), list)
            or not value["entries"]
        ):
            raise ValueError("governed LCA explanation registry is invalid")
        if "knowledge_pack_refs" in value and (
            not isinstance(value["knowledge_pack_refs"], list)
            or not value["knowledge_pack_refs"]
        ):
            raise ValueError("governed LCA knowledge pack references are invalid")
        seen: set[str] = set()
        for entry in value["entries"]:
            if (
                not isinstance(entry, Mapping)
                or set(entry) != ENTRY_FIELDS
                or not isinstance(entry.get("knowledge_id"), str)
                or not entry["knowledge_id"]
                or entry["knowledge_id"] in seen
                or entry.get("authority") != "governed_lca_domain_knowledge"
                or entry.get("authority_status")
                != "product_governed_calibration"
                or entry.get("claim_ceiling")
                not in {
                    "method_explanation_only",
                    "illustration_only",
                    "limitation_only",
                }
                or not isinstance(entry.get("knowledge_roles"), list)
                or not entry["knowledge_roles"]
                or len(entry["knowledge_roles"])
                != len(set(entry["knowledge_roles"]))
                or any(
                    not isinstance(role, str) or not role
                    for role in entry["knowledge_roles"]
                )
            ):
                raise ValueError("governed LCA explanation entry is invalid")
            for field in ("source_kind", "relation", "target_kind", "statement"):
                if not isinstance(entry.get(field), str) or not entry[field].strip():
                    raise ValueError("governed LCA explanation entry is invalid")
            renderings = entry.get("renderings")
            if (
                not isinstance(renderings, Mapping)
                or set(renderings) != {"en", "zh-CN"}
                or any(
                    not isinstance(text, str) or not text.strip()
                    for text in renderings.values()
                )
            ):
                raise ValueError("governed LCA explanation renderings are invalid")
            source_ref = entry.get("source_ref")
            if (
                not isinstance(source_ref, Mapping)
                or set(source_ref) != {"path", "sha256", "entry_id"}
                or not isinstance(source_ref.get("path"), str)
                or source_ref["path"].startswith("/")
                or ".." in source_ref["path"].split("/")
                or not isinstance(source_ref.get("sha256"), str)
                or len(source_ref["sha256"]) != 64
                or source_ref.get("entry_id") != entry["knowledge_id"]
            ):
                raise ValueError("governed LCA explanation source is invalid")
            source_path = (self.root / source_ref["path"]).resolve()
            try:
                source_path.relative_to(self.root)
            except ValueError as error:
                raise ValueError("governed LCA explanation source escaped root") from error
            if (
                not source_path.is_file()
                or hashlib.sha256(source_path.read_bytes()).hexdigest()
                != source_ref["sha256"]
            ):
                raise ValueError("governed LCA explanation source drifted")
            try:
                source_contract = json.loads(
                    source_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError(
                    "governed LCA explanation source entry drifted"
                ) from error
            upstream_sources = (
                source_contract.get("upstream_sources")
                if isinstance(source_contract, Mapping)
                else None
            )
            if (
                not isinstance(source_contract, Mapping)
                or set(source_contract)
                != {
                    "contract",
                    "source_id",
                    "authority_status",
                    "scope",
                    "upstream_sources",
                    "entries",
                }
                or source_contract.get("contract")
                != "lca_research_kernel.lca_explanation_rendering_source/v2"
                or source_contract.get("authority_status")
                != "product_governed_calibration"
                or source_contract.get("scope")
                != "generic_lca_explanation_not_tiangong_identity_or_project_fact"
                or not isinstance(upstream_sources, list)
                or not upstream_sources
            ):
                raise ValueError(
                    "governed LCA explanation source entry drifted"
                )
            upstream_by_id = {}
            for upstream in upstream_sources:
                if (
                    not isinstance(upstream, Mapping)
                    or set(upstream)
                    != {
                        "source_id",
                        "source_type",
                        "owner",
                        "locator",
                        "version",
                        "evidence_status",
                    }
                    or not all(
                        isinstance(upstream.get(key), str)
                        and bool(upstream[key].strip())
                        for key in upstream
                    )
                    or upstream["source_id"] in upstream_by_id
                    or upstream["source_type"]
                    not in {"official_web", "repository_contract"}
                    or upstream["evidence_status"]
                    not in {
                        "external_normative_context_not_local_reference_implementation",
                        "external_conceptual_calibration_only",
                        "adopted_product_contract",
                    }
                ):
                    raise ValueError(
                        "governed LCA explanation upstream source is invalid"
                    )
                if upstream["source_type"] == "official_web":
                    if not upstream["locator"].startswith("https://"):
                        raise ValueError(
                            "governed LCA explanation upstream source is invalid"
                        )
                else:
                    local_path = (self.root / upstream["locator"]).resolve()
                    try:
                        local_path.relative_to(self.root)
                    except ValueError as error:
                        raise ValueError(
                            "governed LCA explanation upstream source escaped root"
                        ) from error
                    expected_sha256 = upstream["version"].removeprefix(
                        "sha256:"
                    )
                    if (
                        not local_path.is_file()
                        or len(expected_sha256) != 64
                        or hashlib.sha256(local_path.read_bytes()).hexdigest()
                        != expected_sha256
                    ):
                        raise ValueError(
                            "governed LCA explanation upstream source drifted"
                        )
                upstream_by_id[upstream["source_id"]] = deepcopy(dict(upstream))
            source_entries = (
                source_contract.get("entries")
                if isinstance(source_contract, Mapping)
                else None
            )
            matches = [
                row
                for row in (source_entries or [])
                if isinstance(row, Mapping)
                and row.get("knowledge_id") == source_ref["entry_id"]
            ]
            source_entry = matches[0] if len(matches) == 1 else None
            source_renderings = (
                source_entry.get("renderings")
                if isinstance(source_entry, Mapping)
                else None
            )
            projected_source_renderings = (
                {
                    language: row.get("text")
                    for language, row in source_renderings.items()
                    if isinstance(row, Mapping)
                }
                if isinstance(source_renderings, Mapping)
                else None
            )
            if (
                not isinstance(source_entry, Mapping)
                or set(source_entry)
                != {
                    "knowledge_id",
                    "source_language",
                    "authority_status",
                    "upstream_source_ids",
                    "source_support",
                    "renderings",
                }
                or source_entry.get("source_language") != "en"
                or source_entry.get("authority_status")
                != "product_governed_calibration"
                or not isinstance(source_entry.get("upstream_source_ids"), list)
                or not source_entry["upstream_source_ids"]
                or len(source_entry["upstream_source_ids"])
                != len(set(source_entry["upstream_source_ids"]))
                or any(
                    source_id not in upstream_by_id
                    for source_id in source_entry["upstream_source_ids"]
                )
                or not isinstance(source_entry.get("source_support"), list)
                or {
                    row.get("source_id")
                    for row in source_entry["source_support"]
                    if isinstance(row, Mapping)
                }
                != set(source_entry["upstream_source_ids"])
                or any(
                    not isinstance(row, Mapping)
                    or set(row)
                    != {
                        "source_id",
                        "location",
                        "original_excerpt",
                        "excerpt_sha256",
                    }
                    or not all(
                        isinstance(row.get(key), str) and bool(row[key].strip())
                        for key in row
                    )
                    or hashlib.sha256(
                        row["original_excerpt"].encode("utf-8")
                    ).hexdigest()
                    != row["excerpt_sha256"]
                    for row in source_entry.get("source_support", [])
                )
                or not isinstance(source_renderings, Mapping)
                or set(source_renderings) != {"en", "zh-CN"}
                or any(
                    not isinstance(row, Mapping)
                    or set(row) != {"text", "transformation"}
                    or not isinstance(row.get("text"), str)
                    or not row["text"].strip()
                    or row.get("transformation")
                    not in {
                        "product_governed_synthesis",
                        "product_governed_translation",
                    }
                    for row in source_renderings.values()
                )
                or projected_source_renderings != dict(entry["renderings"])
                or entry["statement"] != source_renderings["en"]["text"]
            ):
                raise ValueError(
                    "governed LCA explanation source entry drifted"
                )
            seen.add(entry["knowledge_id"])
        return deepcopy(dict(value))

    def select(
        self,
        turn_frame: Mapping[str, object],
        *,
        response_language: str = "en",
    ) -> list[dict]:
        roles = turn_frame.get("required_knowledge_roles")
        if not isinstance(roles, list):
            raise ValueError("research turn knowledge roles are invalid")
        if response_language not in {"en", "zh-CN"}:
            raise ValueError("governed LCA response language is unsupported")
        query = self.compile_query(
            cognitive_operation="explain",
            answer_obligations=[],
            required_roles=roles,
            referent_refs=[],
            target_object_kinds=[],
            relation_kinds=[],
            query_mode="local_entity",
            claim_ceiling="method_explanation_only",
            applicability={
                "geographies": [],
                "times": [],
                "technologies": [],
                "methods": ["generic_lca"],
            },
        )
        return self.resolve_slice(
            query, response_language=response_language
        )["entries"]

    def compile_query(self, **values: object) -> dict:
        """Compile one stable knowledge intent without exposing index details."""

        return self._index.compile_query(**values)

    def resolve_slice(
        self,
        query: Mapping[str, object],
        *,
        response_language: str = "en",
    ) -> dict:
        """Resolve a minimal source-bound slice; never accept a project fact."""

        if response_language not in {"en", "zh-CN"}:
            raise ValueError("governed LCA response language is unsupported")
        return self._index.resolve_slice(
            query, response_language=response_language
        )

    def validate_query(self, query: Mapping[str, object]) -> dict:
        """Replay a query identity without exposing the underlying index."""

        return self._index.validate_query(query)

    def resolve_turn_slice(
        self,
        turn_frame: Mapping[str, object],
        *,
        response_language: str = "en",
    ) -> dict:
        """Compile and resolve one accepted turn through the stable facade."""

        operation = turn_frame.get("cognitive_operation")
        obligations = self._turn_strings(
            turn_frame.get("answer_obligations"), "answer obligations"
        )
        roles = self._turn_strings(
            turn_frame.get("required_knowledge_roles"),
            "required knowledge roles",
        )
        referent = turn_frame.get("referent")
        referent_refs: list[str] = []
        if isinstance(referent, Mapping):
            kind = referent.get("kind")
            if isinstance(kind, str) and kind:
                referent_refs = [kind + ":" + canonical_sha256(referent)]
        targets: set[str] = set()
        relations: set[str] = set()
        for obligation in obligations:
            target_spec = _TURN_OBLIGATION_KNOWLEDGE_TARGETS.get(obligation)
            if target_spec is None:
                continue
            targets.update(target_spec[0])
            relations.update(target_spec[1])
        domain_intent = turn_frame.get("domain_knowledge_intent")
        normalized_intent = None
        if domain_intent is not None:
            normalized_intent = self.validate_domain_intent(domain_intent)
            roles = sorted(
                set(roles).union(normalized_intent["required_roles"])
            )
            targets.update(normalized_intent["target_object_kinds"])
            relations.update(normalized_intent["relation_kinds"])
            referent_refs.append(
                "domain-intent:" + normalized_intent["intent_sha256"]
            )
        query_mode = (
            "exploratory_drift"
            if operation == "research"
            else "dependency_path"
            if operation in {"compare", "calculate", "interpret", "audit"}
            or relations
            else "local_entity"
        )
        query = self.compile_query(
            cognitive_operation=operation,
            answer_obligations=obligations,
            required_roles=roles,
            referent_refs=referent_refs,
            target_object_kinds=sorted(targets),
            relation_kinds=sorted(relations),
            query_mode=query_mode,
            claim_ceiling=str(
                turn_frame.get("claim_ceiling") or "source_bound_bounded"
            ),
            applicability={
                "geographies": [],
                "times": [],
                "technologies": [],
                "methods": ["generic_lca"],
            },
        )
        resolved = self.resolve_slice(
            query, response_language=response_language
        )
        if normalized_intent is not None and not set(
            normalized_intent["selected_knowledge_ids"]
        ).issubset(resolved["selected_knowledge_ids"]):
            raise ValueError("domain knowledge intent did not resolve exactly")
        return {
            "query": query,
            "slice": resolved,
            "domain_knowledge_intent_sha256": (
                normalized_intent["intent_sha256"]
                if normalized_intent is not None
                else None
            ),
        }

    @staticmethod
    def _turn_strings(value: object, label: str) -> list[str]:
        if (
            not isinstance(value, Sequence)
            or isinstance(value, (str, bytes))
            or any(not isinstance(item, str) or not item for item in value)
            or len(value) != len(set(value))
        ):
            raise ValueError(f"research turn {label} are invalid")
        return list(value)

    def _render_entry(self, knowledge_id: str, response_language: str) -> dict:
        if self._packs.contains(knowledge_id):
            projected = self._packs.render_entry(
                knowledge_id, response_language
            )
            projected["registry_id"] = self.registry["registry_id"]
            projected["registry_sha256"] = self.registry_sha256
            return projected
        entry = next(
            (
                row
                for row in self.registry["entries"]
                if row["knowledge_id"] == knowledge_id
            ),
            None,
        )
        if entry is None:
            raise ValueError("governed LCA knowledge identity is unavailable")
        projected = deepcopy(dict(entry))
        projected["registry_id"] = self.registry["registry_id"]
        projected["registry_sha256"] = self.registry_sha256
        projected["statement"] = projected["renderings"][response_language]
        projected["rendering_language"] = response_language
        source_path = self.root / projected["source_ref"]["path"]
        source_contract = json.loads(source_path.read_text(encoding="utf-8"))
        source_entry = next(
            row
            for row in source_contract["entries"]
            if row["knowledge_id"] == projected["knowledge_id"]
        )
        rendering = source_entry["renderings"][response_language]
        projected["rendering_lineage"] = {
            "authority_status": source_entry["authority_status"],
            "source_language": source_entry["source_language"],
            "output_language": response_language,
            "transformation": rendering["transformation"],
            "source_support": deepcopy(source_entry["source_support"]),
            "upstream_sources": [
                deepcopy(
                    next(
                        row
                        for row in source_contract["upstream_sources"]
                        if row["source_id"] == source_id
                    )
                )
                for source_id in source_entry["upstream_source_ids"]
            ],
            "rendering_sha256": hashlib.sha256(
                rendering["text"].encode("utf-8")
            ).hexdigest(),
        }
        return projected


def accepted_claim_slice(
    turn_frame: Mapping[str, object],
    prior_result: Mapping[str, object] | None,
) -> list[dict]:
    """Expose only the exact accepted claim addressed by the current turn."""

    referent = turn_frame.get("referent")
    if prior_result is None:
        if isinstance(referent, Mapping) and referent.get("kind") == "project_goal":
            return []
        raise ValueError("research turn prior result is unavailable")
    claims = prior_result.get("claims")
    if not isinstance(claims, list):
        raise ValueError("accepted prior result claims are invalid")
    normalized: list[dict] = []
    for claim in claims:
        if (
            not isinstance(claim, Mapping)
            or not isinstance(claim.get("claim_id"), str)
        ):
            raise ValueError("accepted prior result claim is invalid")
        if set(claim) == CLAIM_FIELDS:
            normalized.append(deepcopy(dict(claim)))
        elif set(claim) == TYPED_CLAIM_FIELDS:
            normalized.append(
                {
                    "claim_id": claim["claim_id"],
                    "text": claim.get("text"),
                    "requires_evidence": claim.get("claim_kind") == "source_fact",
                    "citation_tokens": deepcopy(claim.get("citation_tokens")),
                }
            )
        else:
            raise ValueError("accepted prior result claim is invalid")
    if not isinstance(referent, Mapping):
        raise ValueError("research turn referent is invalid")
    if referent.get("kind") == "accepted_claim":
        matches = [
            claim
            for claim in normalized
            if claim["claim_id"] == referent.get("identity")
        ]
        if len(matches) != 1:
            raise ValueError("accepted prior claim slice is unavailable")
        return matches
    if referent.get("kind") == "accepted_result":
        return sorted(normalized, key=lambda claim: claim["claim_id"])
    raise ValueError("research turn prior result referent is unsupported")
