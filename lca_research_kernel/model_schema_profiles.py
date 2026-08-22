"""Versioned structured-output families used by research-intelligence routes."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Mapping, Sequence

from .research_turn import (
    ANSWER_OBLIGATIONS,
    COGNITIVE_OPERATIONS,
    RESEARCH_DIALOGUE_MOVES,
)
from .answer_quality import (
    ALLOWED_OBLIGATIONS_BY_KIND,
    eligible_claim_kinds,
)
from .lca_domain_kernel import DIRECT_LCA_MISSING_FIELDS


RESEARCH_TURN_PROFILE = "research_turn_semantics_v1"
RESEARCH_TURN_INTENT_PROFILE = "research_turn_intent_v2"
MATERIAL_ANSWER_PROFILE = "material_answer_v2"
PUBLIC_SOURCE_INTENT_CONTRACT = "lca_research_kernel.public_source_intent/v1"
PUBLIC_SOURCE_INTENT_FIELDS = {
    "contract",
    "purpose",
    "method",
    "url",
    "parse_location",
    "extraction",
    "industry_summary",
}
TIANGONG_NATIVE_INTENT_CONTRACT = "lca_research_kernel.tiangong_native_intent/v1"
TIANGONG_NATIVE_INTENT_FIELDS = {
    "contract",
    "purpose",
    "requested_state_change",
}
_PROBE_REFERENT = "synthetic-prior-claim"
_PROBE_LOCATOR = "synthetic-locator"
_PROBE_KNOWLEDGE = "synthetic-governed-knowledge"


def model_gateway_supports(model_gateway: object, capability: str) -> bool:
    """Read an explicit adapter capability; never infer it from a model name."""

    capabilities_method = getattr(model_gateway, "capabilities", None)
    if not callable(capabilities_method):
        return False
    capabilities = capabilities_method()
    if not isinstance(capabilities, Mapping) or not capabilities:
        return False
    default_provider = getattr(model_gateway, "default_provider", None)
    if isinstance(default_provider, str) and isinstance(
        capabilities.get(default_provider), Mapping
    ):
        return capabilities[default_provider].get(capability) is True
    if len(capabilities) == 1:
        only = next(iter(capabilities.values()))
        return isinstance(only, Mapping) and only.get(capability) is True
    return any(
        isinstance(adapter, Mapping) and adapter.get(capability) is True
        for adapter in capabilities.values()
    )


def generic_source_candidate_schema() -> dict:
    """Candidate syntax for source-bound public research.

    This schema is provider-facing syntax only.  Research Orchestration chooses
    when it applies; the LCA Domain Kernel and Assurance validate its meaning.
    """

    return {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["candidate_only"]},
            "bounded_conclusion": {"type": "string"},
            "answer": {"oneOf": [{"type": "null"}, {"type": "string"}]},
            "claim_statements": {
                "oneOf": [
                    {"type": "null"},
                    {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "modality": {
                                    "type": "string",
                                    "enum": [
                                        "legal_must",
                                        "conditional_trigger",
                                        "officially_permitted",
                                        "official_factual",
                                        "recommended_practice",
                                    ],
                                },
                                "statement": {"type": "string"},
                                "scientific_source_sha256s": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "evidence_basis": {
                                    "type": "object",
                                    "properties": {
                                        "literal": {
                                            "type": "string",
                                            "minLength": 1,
                                        },
                                        "source_sha256": {"type": "string"},
                                    },
                                    "required": ["literal", "source_sha256"],
                                    "additionalProperties": False,
                                },
                            },
                            "required": [
                                "modality",
                                "statement",
                                "scientific_source_sha256s",
                                "evidence_basis",
                            ],
                            "additionalProperties": False,
                        },
                    },
                ]
            },
            "recommendations": {
                "oneOf": [
                    {"type": "null"},
                    {"type": "array", "items": {"type": "string"}},
                ]
            },
            "missing_fields": {
                "type": "array",
                "items": {"type": "string"},
            },
            "limitations": {
                "type": "array",
                "items": {"type": "string"},
            },
            "scientific_source_sha256s": {
                "type": "array",
                "items": {"type": "string"},
            },
            "runtime_provenance_sha256s": {
                "type": "array",
                "items": {"type": "string"},
            },
            "official_object_ids": {
                "type": "array",
                "items": {"type": "string"},
            },
            "values": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "identity": {"type": "string"},
                        "value": {"type": "number"},
                        "unit": {"type": "string"},
                        "source_sha256": {"type": "string"},
                        "time_or_version": {"type": "string"},
                    },
                    "required": [
                        "identity",
                        "value",
                        "unit",
                        "source_sha256",
                        "time_or_version",
                    ],
                    "additionalProperties": False,
                },
            },
            "capability_intent": {
                "oneOf": [
                    {"type": "null"},
                    {
                        "type": "object",
                        "properties": {
                            "contract": {
                                "type": "string",
                                "enum": [PUBLIC_SOURCE_INTENT_CONTRACT],
                            },
                            "purpose": {"type": "string", "minLength": 1},
                            "method": {
                                "type": "string",
                                "enum": ["GET", "HEAD"],
                            },
                            "url": {"type": "string", "minLength": 1},
                            "parse_location": {
                                "type": "string",
                                "minLength": 1,
                            },
                            "extraction": {
                                "oneOf": [
                                    {"type": "null"},
                                    {
                                        "type": "object",
                                        "properties": {
                                            "kind": {
                                                "type": "string",
                                                "enum": ["numeric", "attachment"],
                                            },
                                            "composite_key": {
                                                "type": "object",
                                                "properties": {
                                                    "country_or_region": {
                                                        "type": "string"
                                                    },
                                                    "cn_code": {"type": "string"},
                                                    "year": {"type": "string"},
                                                    "emission_type": {
                                                        "type": "string"
                                                    },
                                                    "production_route": {
                                                        "type": "string"
                                                    },
                                                    "markup_status": {
                                                        "type": "string"
                                                    },
                                                },
                                                "required": [
                                                    "country_or_region",
                                                    "cn_code",
                                                    "year",
                                                    "emission_type",
                                                    "production_route",
                                                    "markup_status",
                                                ],
                                                "additionalProperties": False,
                                            },
                                        },
                                        "required": ["kind", "composite_key"],
                                        "additionalProperties": False,
                                    },
                                ]
                            },
                            "industry_summary": {"type": "boolean"},
                        },
                        "required": sorted(PUBLIC_SOURCE_INTENT_FIELDS),
                        "additionalProperties": False,
                    },
                ]
            },
        },
        "required": [
            "status",
            "bounded_conclusion",
            "answer",
            "claim_statements",
            "recommendations",
            "missing_fields",
            "limitations",
            "scientific_source_sha256s",
            "runtime_provenance_sha256s",
            "official_object_ids",
            "values",
            "capability_intent",
        ],
        "additionalProperties": False,
    }


def source_bound_interpretation_schema() -> dict:
    """Candidate syntax for interpreting already source-bound LCA material."""

    return {
        "type": "object",
        "properties": {
            "sufficiency": {
                "type": "string",
                "enum": [
                    "sufficient_for_bounded_interpretation",
                    "insufficient_critical_data",
                ],
            },
            "bounded_interpretation": {"type": "string"},
            "result_statements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "method_exact_ref": {"type": "string"},
                        "value": {"type": "number"},
                        "unit": {"type": "string"},
                        "statement": {"type": "string"},
                    },
                    "required": [
                        "method_exact_ref",
                        "value",
                        "unit",
                        "statement",
                    ],
                    "additionalProperties": False,
                },
            },
            "limitations": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string"},
            },
            "missing_fields": {
                "type": "array",
                "items": {"type": "string"},
            },
            "source_fact_ids": {
                "type": "array",
                "items": {"type": "string"},
            },
            "capability_intent": {"type": "null"},
            "official_data_preserved": {"type": "boolean"},
            "cross_unit_numeric_ranking_performed": {"type": "boolean"},
            "zero_value_claim": {"type": "string"},
        },
        "required": [
            "sufficiency",
            "bounded_interpretation",
            "result_statements",
            "limitations",
            "missing_fields",
            "source_fact_ids",
            "capability_intent",
            "official_data_preserved",
            "cross_unit_numeric_ranking_performed",
            "zero_value_claim",
        ],
        "additionalProperties": False,
    }


def direct_answer_schema(
    *,
    allow_public_source: bool = False,
    allow_tiangong_native: bool = False,
    official_source_locator: Mapping[str, object] | None = None,
    tiangong_state_changes: Sequence[str] = (),
) -> dict:
    """Candidate syntax for a direct answer and at most one governed intent."""

    capability_options = [{"type": "null"}]
    if allow_public_source:
        public_intent = deepcopy(
            generic_source_candidate_schema()["properties"]["capability_intent"]
        )
        if official_source_locator is not None:
            if (
                official_source_locator.get("contract")
                != "lca_research_kernel.official_source_locator/v1"
                or not isinstance(official_source_locator.get("sources"), list)
                or not official_source_locator["sources"]
            ):
                raise ValueError("official source locator is invalid")
            urls: set[str] = set()
            parse_hints: set[str] = set()
            for source in official_source_locator["sources"]:
                if (
                    not isinstance(source, Mapping)
                    or not isinstance(source.get("url"), str)
                    or not source["url"].strip()
                    or not isinstance(source.get("parse_hints"), list)
                    or not source["parse_hints"]
                    or any(
                        not isinstance(hint, str) or not hint.strip()
                        for hint in source["parse_hints"]
                    )
                ):
                    raise ValueError("official source locator source is invalid")
                urls.add(source["url"].strip())
                parse_hints.update(hint.strip() for hint in source["parse_hints"])
            intent_schema = public_intent["oneOf"][1]
            intent_schema["properties"]["url"] = {
                "type": "string",
                "enum": sorted(urls),
            }
            intent_schema["properties"]["parse_location"] = {
                "type": "string",
                "enum": sorted(parse_hints),
            }
        capability_options.extend(public_intent["oneOf"][1:])
    if allow_tiangong_native:
        if not tiangong_state_changes:
            raise ValueError("TianGong state-change registry is absent")
        capability_options.append(
            {
                "type": "object",
                "properties": {
                    "contract": {
                        "type": "string",
                        "enum": [TIANGONG_NATIVE_INTENT_CONTRACT],
                    },
                    "purpose": {"type": "string", "minLength": 1},
                    "requested_state_change": {
                        "type": "string",
                        "enum": sorted(tiangong_state_changes),
                    },
                },
                "required": sorted(TIANGONG_NATIVE_INTENT_FIELDS),
                "additionalProperties": False,
            }
        )
    capability_intent = (
        capability_options[0]
        if len(capability_options) == 1
        else {"oneOf": capability_options}
    )
    return {
        "type": "object",
        "properties": {
            "answer": {"type": "string", "minLength": 1},
            "values": {"type": "array", "maxItems": 0},
            "official_object_ids": {"type": "array", "maxItems": 0},
            "scientific_source_sha256s": {"type": "array", "maxItems": 0},
            "limitations": {
                "type": "array",
                "items": {"type": "string"},
            },
            "missing_fields": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": sorted(DIRECT_LCA_MISSING_FIELDS),
                },
                "uniqueItems": True,
            },
            "capability_intent": capability_intent,
        },
        "required": [
            "answer",
            "values",
            "official_object_ids",
            "scientific_source_sha256s",
            "limitations",
            "missing_fields",
            "capability_intent",
        ],
        "additionalProperties": False,
    }


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def research_turn_semantic_schema(prior_claim_ids: list[str]) -> dict:
    """Closed semantic-classification schema; no answer text or authority fields."""

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "cognitive_operation": {
                "type": "string",
                "enum": sorted(COGNITIVE_OPERATIONS),
            },
            "answer_obligations": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {
                    "type": "string",
                    "enum": sorted(ANSWER_OBLIGATIONS),
                },
            },
            "referent_claim_id": {
                "type": "string",
                "enum": list(prior_claim_ids),
            },
        },
        "required": [
            "cognitive_operation",
            "answer_obligations",
            "referent_claim_id",
        ],
    }


def research_initial_turn_semantic_schema() -> dict:
    """Initial-turn semantic syntax; the Project Goal remains the referent."""

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "cognitive_operation": {
                "type": "string",
                "enum": sorted(COGNITIVE_OPERATIONS),
            },
            "answer_obligations": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {
                    "type": "string",
                    "enum": sorted(ANSWER_OBLIGATIONS),
                },
            },
        },
        "required": ["cognitive_operation", "answer_obligations"],
    }


def research_turn_intent_schema(prior_claim_ids: list[str]) -> dict:
    """Constrain the model to one semantic move; obligations stay deterministic."""

    if (
        not prior_claim_ids
        or len(prior_claim_ids) != len(set(prior_claim_ids))
        or any(not isinstance(value, str) or not value for value in prior_claim_ids)
    ):
        raise ValueError("research turn intent requires accepted claim identities")
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "dialogue_move": {
                "type": "string",
                "enum": sorted(RESEARCH_DIALOGUE_MOVES),
            },
            "referent_claim_id": {
                "type": "string",
                "enum": list(prior_claim_ids),
            },
        },
        "required": ["dialogue_move", "referent_claim_id"],
    }


def research_initial_turn_intent_schema() -> dict:
    """Initial-turn intent syntax without a fabricated prior-claim referent."""

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "dialogue_move": {
                "type": "string",
                "enum": sorted(RESEARCH_DIALOGUE_MOVES),
            },
        },
        "required": ["dialogue_move"],
    }


def material_answer_v1_schema(
    selected_locator_ids: list[str],
    *,
    required_limitations: list[str] | None = None,
) -> dict:
    """Legacy claim/citation syntax for adapters not admitted to v2.

    Provider syntax belongs beside the versioned schema profiles, not in the
    Product Interface.  The LCA Domain Kernel and Assurance still decide
    whether a returned candidate has acceptable scientific semantics.
    """

    if (
        not selected_locator_ids
        or len(selected_locator_ids) != len(set(selected_locator_ids))
        or any(not isinstance(value, str) or not value for value in selected_locator_ids)
    ):
        raise ValueError("material answer v1 requires selected locator identities")
    bracketed_locator_ids = [
        f"[{locator_id}]" for locator_id in selected_locator_ids
    ]
    citation_tokens = {
        "type": "array",
        "minItems": 1,
        "uniqueItems": True,
        "items": {"type": "string", "enum": bracketed_locator_ids},
    }
    limitations_schema: dict = {
        "type": "array",
        "items": {"type": "string"},
    }
    if required_limitations:
        if (
            len(required_limitations) != len(set(required_limitations))
            or any(not isinstance(value, str) or not value for value in required_limitations)
        ):
            raise ValueError("material answer required limitations are invalid")
        limitations_schema = {
            "type": "array",
            "minItems": len(required_limitations),
            "maxItems": len(required_limitations),
            "uniqueItems": True,
            "items": {"type": "string", "enum": list(required_limitations)},
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "status": {"type": "string", "enum": ["candidate_only"]},
            "answer": {"type": "string", "minLength": 1},
            "bounded_conclusion": {"type": "string", "minLength": 1},
            "claims": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "claim_id": {"type": "string", "minLength": 1},
                        "text": {"type": "string", "minLength": 1},
                        "requires_evidence": {"type": "boolean", "enum": [True]},
                        "citation_tokens": deepcopy(citation_tokens),
                    },
                    "required": [
                        "claim_id",
                        "text",
                        "requires_evidence",
                        "citation_tokens",
                    ],
                },
            },
            "claim_citation_bindings": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "claim_text": {"type": "string", "minLength": 1},
                        "citation_tokens": deepcopy(citation_tokens),
                    },
                    "required": ["claim_text", "citation_tokens"],
                },
            },
            "claim_statements": {"type": "null"},
            "recommendations": {"type": "null"},
            "missing_fields": {"type": "array", "items": {"type": "string"}},
            "limitations": limitations_schema,
            "scientific_source_sha256s": {
                "type": "array",
                "items": {"type": "string"},
            },
            "runtime_provenance_sha256s": {
                "type": "array",
                "items": {"type": "string"},
            },
            "official_object_ids": {
                "type": "array",
                "maxItems": 0,
                "items": {"type": "string"},
            },
            "values": {
                "type": "array",
                "maxItems": 0,
                "items": {"type": "object"},
            },
            "capability_intent": {"type": "null"},
            "citation_ids": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"type": "string", "enum": list(selected_locator_ids)},
            },
        },
        "required": [
            "status",
            "answer",
            "bounded_conclusion",
            "claims",
            "claim_citation_bindings",
            "claim_statements",
            "recommendations",
            "missing_fields",
            "limitations",
            "scientific_source_sha256s",
            "runtime_provenance_sha256s",
            "official_object_ids",
            "values",
            "capability_intent",
            "citation_ids",
        ],
    }


def material_answer_v2_schema(
    selected_locator_ids: list[str],
    *,
    turn_frame: Mapping[str, object],
    governed_knowledge_ids: list[str],
    required_limitations: list[str] | None = None,
) -> dict:
    """Typed explanatory-answer schema with project-bound dynamic enums."""

    obligations = turn_frame.get("answer_obligations")
    referent = turn_frame.get("referent")
    if (
        not isinstance(obligations, list)
        or not obligations
        or not all(isinstance(value, str) and value for value in obligations)
        or not isinstance(referent, Mapping)
        or not isinstance(referent.get("identity"), str)
        or not referent["identity"]
        or not selected_locator_ids
        or len(selected_locator_ids) != len(set(selected_locator_ids))
    ):
        raise ValueError("material answer schema inputs are invalid")
    claim_kinds = eligible_claim_kinds(obligations)
    if not claim_kinds:
        raise ValueError("material answer schema has no eligible claim kind")
    claim_variants = [
        _material_claim_variant(
            claim_kind,
            obligations=obligations,
            selected_locator_ids=selected_locator_ids,
            governed_knowledge_ids=governed_knowledge_ids,
            referent_id=referent["identity"],
        )
        for claim_kind in claim_kinds
    ]
    limitations_schema = {
        "type": "array",
        "items": {"type": "string"},
    }
    if required_limitations:
        if (
            len(required_limitations) != len(set(required_limitations))
            or any(not isinstance(value, str) or not value for value in required_limitations)
        ):
            raise ValueError("material answer required limitations are invalid")
        limitations_schema = {
            "type": "array",
            "minItems": len(required_limitations),
            "maxItems": len(required_limitations),
            "uniqueItems": True,
            "items": {"type": "string", "enum": list(required_limitations)},
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "contract": {
                "type": "string",
                "enum": ["lca_research_kernel.material_answer_candidate/v2"],
            },
            "status": {"type": "string", "enum": ["candidate_only"]},
            "claims": {
                "type": "array",
                "minItems": 1,
                "items": {"oneOf": claim_variants},
            },
            "missing_fields": {
                "type": "array",
                "items": {"type": "string"},
            },
            "limitations": limitations_schema,
            "runtime_provenance_sha256s": {
                "type": "array",
                "items": {"type": "string"},
            },
            "official_object_ids": {
                "type": "array",
                "maxItems": 0,
                "items": {"type": "string"},
            },
            "values": {
                "type": "array",
                "maxItems": 0,
                "items": {"type": "object"},
            },
            "capability_intent": {"type": "null"},
        },
        "required": [
            "contract",
            "status",
            "claims",
            "missing_fields",
            "limitations",
            "runtime_provenance_sha256s",
            "official_object_ids",
            "values",
            "capability_intent",
        ],
    }


def _material_claim_variant(
    claim_kind: str,
    *,
    obligations: Sequence[str],
    selected_locator_ids: Sequence[str],
    governed_knowledge_ids: Sequence[str],
    referent_id: str,
) -> dict:
    """Compile one disjoint support family into provider-enforced fields."""

    allowed_obligations = [
        value
        for value in obligations
        if value in ALLOWED_OBLIGATIONS_BY_KIND[claim_kind]
    ]
    source_claim = claim_kind == "source_fact"
    if not source_claim and not governed_knowledge_ids:
        raise ValueError(
            "governed answer claim requires qualified knowledge identifiers"
        )

    citation_tokens = {
        "type": "array",
        "uniqueItems": True,
        "items": {"type": "string"},
        "minItems": 1 if source_claim else 0,
        "maxItems": len(selected_locator_ids) if source_claim else 0,
    }
    if source_claim:
        citation_tokens["items"]["enum"] = [
            f"[{locator_id}]" for locator_id in selected_locator_ids
        ]

    knowledge_refs = {
        "type": "array",
        "uniqueItems": True,
        "items": {"type": "string"},
        "minItems": 0 if source_claim else 1,
        "maxItems": 0 if source_claim else len(governed_knowledge_ids),
    }
    if not source_claim:
        knowledge_refs["items"]["enum"] = list(governed_knowledge_ids)

    satisfies_obligations = {
        "type": "array",
        "uniqueItems": True,
        "items": {"type": "string"},
    }
    if allowed_obligations:
        satisfies_obligations["items"]["enum"] = allowed_obligations
        satisfies_obligations["maxItems"] = len(allowed_obligations)
        if not source_claim:
            satisfies_obligations["minItems"] = 1
    else:
        satisfies_obligations["maxItems"] = 0

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "claim_id": {"type": "string", "minLength": 1},
            "claim_kind": {"type": "string", "enum": [claim_kind]},
            "text": {"type": "string", "minLength": 1},
            "citation_tokens": citation_tokens,
            "knowledge_refs": knowledge_refs,
            "referent_ids": {
                "type": "array",
                "minItems": 1,
                "maxItems": 1,
                "uniqueItems": True,
                "items": {"type": "string", "enum": [referent_id]},
            },
            "not_project_fact": {
                "type": "boolean",
                "enum": [claim_kind == "illustrative_non_project_example"],
            },
            "satisfies_obligations": satisfies_obligations,
        },
        "required": [
            "claim_id",
            "claim_kind",
            "text",
            "citation_tokens",
            "knowledge_refs",
            "referent_ids",
            "not_project_fact",
            "satisfies_obligations",
        ],
    }


def profile_probe_spec(profile_id: str) -> dict:
    """Return one case-neutral, zero-effect probe for a schema family."""

    if profile_id == RESEARCH_TURN_PROFILE:
        output_schema = research_turn_semantic_schema([_PROBE_REFERENT])
        output = {
            "cognitive_operation": "interpret",
            "answer_obligations": ["source_boundary"],
            "referent_claim_id": _PROBE_REFERENT,
        }
    elif profile_id == RESEARCH_TURN_INTENT_PROFILE:
        output_schema = research_turn_intent_schema([_PROBE_REFERENT])
        output = {
            "dialogue_move": "deepen_explanation",
            "referent_claim_id": _PROBE_REFERENT,
        }
    elif profile_id == MATERIAL_ANSWER_PROFILE:
        frame = {
            "referent": {"identity": _PROBE_REFERENT},
            "answer_obligations": ["source_boundary"],
        }
        output_schema = material_answer_v2_schema(
            [_PROBE_LOCATOR],
            turn_frame=frame,
            governed_knowledge_ids=[_PROBE_KNOWLEDGE],
        )
        output = {
            "contract": "lca_research_kernel.material_answer_candidate/v2",
            "status": "candidate_only",
            "claims": [
                {
                    "claim_id": "synthetic-source-boundary",
                    "claim_kind": "source_fact",
                    "text": (
                        "Synthetic schema qualification only; no scientific "
                        "question or project fact was evaluated."
                    ),
                    "citation_tokens": [f"[{_PROBE_LOCATOR}]"],
                    "knowledge_refs": [],
                    "referent_ids": [_PROBE_REFERENT],
                    "not_project_fact": False,
                    "satisfies_obligations": ["source_boundary"],
                }
            ],
            "missing_fields": [],
            "limitations": ["No scientific question was evaluated."],
            "runtime_provenance_sha256s": [],
            "official_object_ids": [],
            "values": [],
            "capability_intent": None,
        }
    else:
        raise ValueError("research-intelligence schema profile is unsupported")
    return {
        "profile_id": profile_id,
        "task": (
            "Return only the frozen synthetic structured object for schema "
            "qualification. Do not answer a scientific question, use a tool, "
            "read a project source, or produce a project fact."
        ),
        "context": {
            "contract": (
                "lca_research_kernel.synthetic_research_intelligence_probe/v1"
            ),
            "synthetic": True,
            "scientific_question": False,
            "sealed_reference_available": False,
            "external_effects_allowed": [],
            "expected_output": deepcopy(output),
        },
        "output_schema": output_schema,
        "expected_output": output,
        "schema_sha256": canonical_sha256(output_schema),
    }
