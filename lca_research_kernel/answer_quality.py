"""Deterministic obligation and support checks for typed research answers."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Mapping, Sequence

from .answer_rendering import (
    illustrative_example_prefix,
    normalize_rendered_clause_text,
    render_typed_material_answer,
)


QUALITY_CONTRACT = "lca_research_kernel.answer_quality_assessment/v2"
CLAIM_FIELDS = {
    "claim_id",
    "claim_kind",
    "text",
    "citation_tokens",
    "knowledge_refs",
    "referent_ids",
    "not_project_fact",
    "satisfies_obligations",
}
CLAIM_KINDS = {
    "source_fact",
    "governed_method_explanation",
    "illustrative_non_project_example",
    "limitation",
}
ALLOWED_OBLIGATIONS_BY_KIND = {
    "source_fact": {
        "direct_resolution",
        "referent_resolution",
        "source_value_explanation",
        "source_boundary",
    },
    "governed_method_explanation": {
        "direct_resolution",
        "referent_resolution",
        "concept_meaning",
        "lca_relationship",
        "source_boundary",
    },
    "illustrative_non_project_example": {
        "illustrative_application",
        "source_boundary",
    },
    "limitation": {
        "uncertainty_and_limit",
        "source_boundary",
    },
}
CLAIM_KIND_TRIGGER_OBLIGATIONS = {
    "source_fact": {
        "direct_resolution",
        "referent_resolution",
        "source_value_explanation",
    },
    "governed_method_explanation": {"concept_meaning", "lca_relationship"},
    "illustrative_non_project_example": {"illustrative_application"},
    "limitation": {"uncertainty_and_limit"},
}
BRACKET_TOKEN = re.compile(r"\[[^\[\]\s]+\]")
CJK_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
INTERNAL_IDENTIFIER = re.compile(
    r"(?<![\w])(?:claim|locator|revision|material|artifact|run|task)-"
    r"[A-Za-z0-9](?:[A-Za-z0-9._:-]*[A-Za-z0-9])?(?![\w])"
)
KNOWLEDGE_ROLE_REQUIREMENTS = {
    "concept_meaning": "lca_concept",
    "lca_relationship": "lca_relationship",
}


def representative_sampling_limitation(response_language: str) -> str:
    """Return the exact user-visible boundary for non-exhaustive material reads."""

    if response_language == "zh-CN":
        return (
            "本说明仅覆盖经确定性选材规则选出的代表性证据，"
            "并非对整个材料的穷尽审查；不能据此推断未展示的内容不存在。"
        )
    if response_language == "en":
        return (
            "This explanation covers only representative evidence selected "
            "by the deterministic material-selection rule; it is not an "
            "exhaustive review of the material, and omitted content must not "
            "be treated as absent."
        )
    raise ValueError("material answer response language is unsupported")


def required_material_limitations(
    use_decision: Mapping[str, object], *, response_language: str
) -> list[str]:
    """Compile machine-owned delivery limits from the accepted use decision."""

    expected = use_decision.get("expected_state_change")
    sampling = (
        expected.get("representative_sampling")
        if isinstance(expected, Mapping)
        else None
    )
    if isinstance(sampling, Mapping) and sampling.get("sampling_applied") is True:
        return [representative_sampling_limitation(response_language)]
    return []
SOURCE_AMOUNT_KEYS = ("amount", "quantity", "value", "mean", "resultingAmount")
SOURCE_UNIT_KEYS = ("unit", "unitName", "referenceUnit")
SOURCE_IDENTITY_KEYS = (
    "flow",
    "flowName",
    "name",
    "process",
    "processName",
    "item",
    "dataset",
    "file",
    "type",
)


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _source_value_anchors(evidence_unit: Mapping[str, object]) -> dict | None:
    """Extract exact amount/unit anchors without interpreting scientific meaning."""

    declared = evidence_unit.get("source_value_anchors")
    if declared is not None:
        if (
            not isinstance(declared, Mapping)
            or set(declared) != {"amount", "unit"}
            or any(
                not isinstance(declared.get(key), str)
                or not declared[key].strip()
                for key in ("amount", "unit")
            )
        ):
            raise ValueError("source value anchors are invalid")
        return {
            "amount": declared["amount"].strip(),
            "unit": declared["unit"].strip(),
        }
    content = evidence_unit.get("content")
    amount = unit = None
    if isinstance(content, Mapping):
        for key in SOURCE_AMOUNT_KEYS:
            value = content.get(key)
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                amount = str(value).strip()
                if amount:
                    break
        for key in SOURCE_UNIT_KEYS:
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                unit = value.strip()
                break
    elif isinstance(content, str):
        amount_match = re.search(
            r"(?:^|[;,|\n])\s*(?:amount|quantity|value|mean)\s*[:=]\s*"
            r"([^;,|\n]+)",
            content,
            flags=re.IGNORECASE,
        )
        unit_match = re.search(
            r"(?:^|[;,|\n])\s*(?:unit|unitName|referenceUnit)\s*[:=]\s*"
            r"([^;,|\n]+)",
            content,
            flags=re.IGNORECASE,
        )
        amount = amount_match.group(1).strip() if amount_match else None
        unit = unit_match.group(1).strip() if unit_match else None
    if not amount or not unit or "<missing>" in {amount, unit}:
        return None
    return {"amount": amount, "unit": unit}


def _claim_contains_source_value(text: str, anchors: Mapping[str, str]) -> bool:
    amount = re.escape(anchors["amount"])
    unit = re.escape(anchors["unit"])
    return (
        re.search(rf"(?<![0-9.]){amount}(?![0-9.])", text) is not None
        and re.search(rf"(?<![A-Za-z0-9_]){unit}(?![A-Za-z0-9_])", text, re.I)
        is not None
    )


def _source_identity_anchors(evidence_unit: Mapping[str, object]) -> list[str]:
    """Extract literal source identities; never infer identity from a role label."""

    declared = evidence_unit.get("source_identity_anchors")
    if declared is not None:
        if (
            not isinstance(declared, list)
            or any(not isinstance(value, str) or not value.strip() for value in declared)
        ):
            raise ValueError("source identity anchors are invalid")
        return sorted(set(value.strip() for value in declared))
    content = evidence_unit.get("content")
    values: list[str] = []
    if isinstance(content, Mapping):
        for key in SOURCE_IDENTITY_KEYS:
            value = content.get(key)
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                rendered = str(value).strip()
                if rendered and rendered != "<missing>":
                    values.append(rendered)
    elif isinstance(content, str):
        keys = "|".join(re.escape(key) for key in SOURCE_IDENTITY_KEYS)
        for match in re.finditer(
            rf"(?:^|[;,|\n])\s*(?:{keys})\s*[:=]\s*([^;,|\n]+)",
            content,
            flags=re.IGNORECASE,
        ):
            value = match.group(1).strip()
            if value and value != "<missing>":
                values.append(value)
        literal = content.strip()
        if (
            evidence_unit.get("kind") == "json_value"
            and literal
            and literal != "<missing>"
            and len(literal) <= 512
        ):
            # A JSON leaf has no field wrapper in the evidence unit. Preserve
            # its exact literal as a source anchor; this proves only that the
            # cited material contains the literal, never an inferred LCA role.
            values.append(literal)
    return sorted(set(values))


def _claim_contains_identity(text: str, anchors: Sequence[str]) -> bool:
    return any(
        re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(anchor)}(?![A-Za-z0-9_])",
            text,
            re.IGNORECASE,
        )
        is not None
        for anchor in anchors
    )


def _governed_rendering_matches(text: str, statements: Sequence[str]) -> bool:
    """Compare exact statements while ignoring whitespace after sentence marks."""

    def canonical(value: str) -> str:
        return re.sub(r"(?<=[.。!?！？])\s+", "", value.strip())

    return canonical(text) == canonical(" ".join(statement.strip() for statement in statements))


def project_typed_material_answer_candidate(
    candidate: Mapping[str, object],
    *,
    locator_hashes: Mapping[str, str],
    response_language: str,
    governed_knowledge: Sequence[Mapping[str, object]] = (),
) -> tuple[dict, bool]:
    """Derive redundant surfaces and render governed claims from exact refs."""

    projected = deepcopy(dict(candidate))
    if projected.get("contract") != (
        "lca_research_kernel.material_answer_candidate/v2"
    ):
        raise ValueError("typed material candidate contract is unsupported")
    claims = projected.get("claims")
    if not isinstance(claims, list) or not claims:
        raise ValueError("typed material candidate claims are absent")
    normalized_claims = []
    knowledge_index = {
        row["knowledge_id"]: row
        for row in governed_knowledge
        if isinstance(row, Mapping)
        and isinstance(row.get("knowledge_id"), str)
        and isinstance(row.get("statement"), str)
        and row.get("rendering_language") == response_language
    }
    expected_ceiling = {
        "governed_method_explanation": "method_explanation_only",
        "illustrative_non_project_example": "illustration_only",
        "limitation": "limitation_only",
    }
    citation_ids: list[str] = []
    bindings = []
    for claim in claims:
        if not isinstance(claim, Mapping):
            raise ValueError("typed material candidate claim is invalid")
        normalized = deepcopy(dict(claim))
        text = str(normalized.get("text") or "").strip()
        tokens = normalized.get("citation_tokens")
        if not isinstance(tokens, list):
            raise ValueError("typed material citation tokens are invalid")
        for token in tokens:
            if isinstance(token, str) and token:
                text = text.replace(token, "")
        text = re.sub(r"[ \t]+([,.;:!?，。；：！？])", r"\1", text).strip()
        claim_kind = normalized.get("claim_kind")
        knowledge_refs = normalized.get("knowledge_refs")
        if (
            claim_kind in expected_ceiling
            and isinstance(knowledge_refs, list)
            and bool(knowledge_refs)
            and len(knowledge_refs) == len(set(knowledge_refs))
            and all(
                isinstance(knowledge_ref, str)
                and knowledge_ref in knowledge_index
                and knowledge_index[knowledge_ref].get("claim_ceiling")
                == expected_ceiling[claim_kind]
                for knowledge_ref in knowledge_refs
            )
        ):
            text = " ".join(
                str(knowledge_index[knowledge_ref]["statement"]).strip()
                for knowledge_ref in knowledge_refs
            )
        if (
            claim_kind == "illustrative_non_project_example"
            and text
            and not text.startswith(illustrative_example_prefix(response_language))
        ):
            text = illustrative_example_prefix(response_language) + text
        normalized["text"] = text
        normalized_claims.append(normalized)
        if normalized.get("claim_kind") == "source_fact":
            bindings.append(
                {
                    "claim_text": normalize_rendered_clause_text(text),
                    "citation_tokens": deepcopy(tokens),
                }
            )
            for token in tokens:
                if (
                    isinstance(token, str)
                    and len(token) >= 3
                    and token[0] == "["
                    and token[-1] == "]"
                    and token[1:-1] not in citation_ids
                ):
                    citation_ids.append(token[1:-1])
    before = _canonical_sha256(projected)
    projected["claims"] = normalized_claims
    projected["claim_citation_bindings"] = bindings
    projected["answer"] = render_typed_material_answer(
        normalized_claims,
        response_language=response_language,
    )
    projected["bounded_conclusion"] = projected["answer"]
    projected["claim_statements"] = None
    projected["recommendations"] = None
    projected["citation_ids"] = citation_ids
    projected["scientific_source_sha256s"] = sorted(
        {
            locator_hashes[locator_id]
            for locator_id in citation_ids
            if locator_id in locator_hashes
        }
    )
    return projected, _canonical_sha256(projected) != before


def build_answer_quality_basis(
    *,
    selected_evidence_units: Sequence[Mapping[str, object]],
    governed_knowledge: Sequence[Mapping[str, object]],
    response_language: str,
) -> dict:
    """Freeze the exact accepted evidence roles and rendered knowledge basis."""

    if response_language not in {"en", "zh-CN"}:
        raise ValueError("answer quality basis language is unsupported")
    evidence_rows = []
    for row in selected_evidence_units:
        evidence_unit = row.get("evidence_unit") if isinstance(row, Mapping) else None
        semantic_roles = (
            (
                evidence_unit["semantic_roles"]
                if "semantic_roles" in evidence_unit
                else []
            )
            if isinstance(evidence_unit, Mapping)
            else None
        )
        if (
            not isinstance(row, Mapping)
            or not isinstance(row.get("locator_id"), str)
            or not isinstance(row.get("content_hash"), str)
            or len(row["content_hash"]) != 64
            or not isinstance(semantic_roles, list)
            or any(not isinstance(role, str) or not role for role in semantic_roles)
        ):
            raise ValueError("answer quality evidence basis is invalid")
        evidence_rows.append(
            {
                "locator_id": row["locator_id"],
                "content_hash": row["content_hash"],
                "semantic_roles": sorted(set(semantic_roles)),
                "source_value_anchors": _source_value_anchors(evidence_unit),
                "source_identity_anchors": _source_identity_anchors(evidence_unit),
            }
        )
    if not evidence_rows or len({row["locator_id"] for row in evidence_rows}) != len(
        evidence_rows
    ):
        raise ValueError("answer quality evidence basis is incomplete")
    knowledge_rows = []
    for row in governed_knowledge:
        if (
            not isinstance(row, Mapping)
            or row.get("authority") != "governed_lca_domain_knowledge"
            or row.get("authority_status") != "product_governed_calibration"
            or not isinstance(row.get("knowledge_id"), str)
            or not isinstance(row.get("statement"), str)
            or row.get("rendering_language") != response_language
            or not isinstance(row.get("rendering_lineage"), Mapping)
            or not isinstance(row.get("source_ref"), Mapping)
        ):
            raise ValueError("answer quality knowledge basis is invalid")
        knowledge_rows.append(
            {
                key: deepcopy(row[key])
                for key in (
                    "knowledge_id",
                    "authority",
                    "authority_status",
                    "claim_ceiling",
                    "knowledge_roles",
                    "statement",
                    "rendering_language",
                    "rendering_lineage",
                    "source_ref",
                )
            }
        )
    basis = {
        "contract": "lca_research_kernel.answer_quality_basis/v2",
        "response_language": response_language,
        "selected_evidence_roles": sorted(
            evidence_rows, key=lambda row: row["locator_id"]
        ),
        "governed_knowledge": sorted(
            knowledge_rows, key=lambda row: row["knowledge_id"]
        ),
    }
    return {**basis, "basis_sha256": _canonical_sha256(basis)}


def validate_answer_quality_basis(value: object) -> dict:
    """Validate one immutable accepted basis without consulting live registry state."""

    if not isinstance(value, Mapping) or set(value) != {
        "contract",
        "response_language",
        "selected_evidence_roles",
        "governed_knowledge",
        "basis_sha256",
    }:
        raise ValueError("accepted answer quality basis is invalid")
    basis = {key: deepcopy(value[key]) for key in value if key != "basis_sha256"}
    if (
        value.get("contract") != "lca_research_kernel.answer_quality_basis/v2"
        or value.get("response_language") not in {"en", "zh-CN"}
        or value.get("basis_sha256") != _canonical_sha256(basis)
        or not isinstance(value.get("selected_evidence_roles"), list)
        or not value["selected_evidence_roles"]
        or not isinstance(value.get("governed_knowledge"), list)
    ):
        raise ValueError("accepted answer quality basis drifted")
    evidence_rows = value["selected_evidence_roles"]
    if any(
        not isinstance(row, Mapping)
        or set(row)
        != {
            "locator_id",
            "content_hash",
            "semantic_roles",
            "source_value_anchors",
            "source_identity_anchors",
        }
        or not isinstance(row.get("locator_id"), str)
        or not isinstance(row.get("content_hash"), str)
        or re.fullmatch(r"[0-9a-f]{64}", row["content_hash"]) is None
        or not isinstance(row.get("semantic_roles"), list)
        or row["semantic_roles"] != sorted(set(row["semantic_roles"]))
        or (
            row.get("source_value_anchors") is not None
            and _source_value_anchors(
                {"source_value_anchors": row["source_value_anchors"]}
            )
            != row["source_value_anchors"]
        )
        or not isinstance(row.get("source_identity_anchors"), list)
        or row["source_identity_anchors"]
        != sorted(set(row["source_identity_anchors"]))
        or any(
            not isinstance(anchor, str) or not anchor
            for anchor in row["source_identity_anchors"]
        )
        for row in evidence_rows
    ) or len({row["locator_id"] for row in evidence_rows}) != len(evidence_rows):
        raise ValueError("accepted answer quality evidence basis drifted")
    knowledge_rows = value["governed_knowledge"]
    if any(
        not isinstance(row, Mapping)
        or set(row)
        != {
            "knowledge_id",
            "authority",
            "authority_status",
            "claim_ceiling",
            "knowledge_roles",
            "statement",
            "rendering_language",
            "rendering_lineage",
            "source_ref",
        }
        or row.get("authority") != "governed_lca_domain_knowledge"
        or row.get("authority_status") != "product_governed_calibration"
        or not isinstance(row.get("knowledge_id"), str)
        or not isinstance(row.get("statement"), str)
        or row.get("rendering_language") != value["response_language"]
        or not isinstance(row.get("knowledge_roles"), list)
        or not isinstance(row.get("source_ref"), Mapping)
        or not isinstance(row.get("rendering_lineage"), Mapping)
        or row["rendering_lineage"].get("authority_status")
        != "product_governed_calibration"
        or not isinstance(
            row["rendering_lineage"].get("upstream_sources"), list
        )
        or not row["rendering_lineage"]["upstream_sources"]
        or any(
            not isinstance(source, Mapping)
            or source.get("evidence_status")
            not in {
                "external_normative_context_not_local_reference_implementation",
                "external_conceptual_calibration_only",
                "adopted_product_contract",
            }
            for source in row["rendering_lineage"]["upstream_sources"]
        )
        for row in knowledge_rows
    ):
        raise ValueError("accepted answer quality knowledge basis drifted")
    return deepcopy(dict(value))


def build_answer_repair_support_invariants(
    claims: object,
    *,
    selected_locator_ids: Sequence[str],
    governed_knowledge_ids: Sequence[str],
) -> dict:
    """Freeze support identities before one bounded presentation/content repair."""

    if not isinstance(claims, list) or any(
        not isinstance(claim, Mapping)
        or not isinstance(claim.get("claim_id"), str)
        for claim in claims
    ):
        raise ValueError("answer repair source claims are invalid")
    support = {
        claim["claim_id"]: {
            key: deepcopy(claim.get(key))
            for key in (
                "claim_kind",
                "citation_tokens",
                "knowledge_refs",
                "referent_ids",
                "not_project_fact",
            )
        }
        for claim in claims
    }
    if len(support) != len(claims):
        raise ValueError("answer repair source claim identities are ambiguous")
    basis = {
        "contract": "lca_research_kernel.answer_repair_support_invariants/v1",
        "original_claim_support": support,
        "selected_locator_ids": sorted(set(selected_locator_ids)),
        "governed_knowledge_ids": sorted(set(governed_knowledge_ids)),
    }
    return {**basis, "invariants_sha256": _canonical_sha256(basis)}


def validate_answer_repair_support_invariants(
    repaired_claims: object,
    invariants: Mapping[str, object],
) -> None:
    """Reject a repair that swaps evidence or governed knowledge support."""

    basis = {
        key: deepcopy(invariants.get(key))
        for key in (
            "contract",
            "original_claim_support",
            "selected_locator_ids",
            "governed_knowledge_ids",
        )
    }
    if (
        invariants.get("contract")
        != "lca_research_kernel.answer_repair_support_invariants/v1"
        or invariants.get("invariants_sha256") != _canonical_sha256(basis)
        or not isinstance(repaired_claims, list)
        or not isinstance(invariants.get("original_claim_support"), Mapping)
    ):
        raise ValueError("answer repair support invariants are invalid")
    repaired_index = {
        claim.get("claim_id"): claim
        for claim in repaired_claims
        if isinstance(claim, Mapping)
        and isinstance(claim.get("claim_id"), str)
    }
    if len(repaired_index) != len(repaired_claims):
        raise ValueError("answer repair claim identities are ambiguous")
    original_support = invariants["original_claim_support"]
    if not set(original_support).issubset(repaired_index):
        raise ValueError("answer repair removed an existing supported claim")
    support_fields = {
        "claim_kind",
        "citation_tokens",
        "knowledge_refs",
        "referent_ids",
        "not_project_fact",
    }
    for claim_id, expected in original_support.items():
        observed = {
            key: deepcopy(repaired_index[claim_id].get(key))
            for key in support_fields
        }
        if observed != expected:
            raise ValueError("answer repair changed existing claim support")
    allowed_locators = {
        f"[{locator_id}]" for locator_id in invariants["selected_locator_ids"]
    }
    allowed_knowledge = set(invariants["governed_knowledge_ids"])
    for claim in repaired_claims:
        if not set(claim.get("citation_tokens") or []).issubset(allowed_locators):
            raise ValueError("answer repair introduced unselected evidence")
        if not set(claim.get("knowledge_refs") or []).issubset(allowed_knowledge):
            raise ValueError("answer repair introduced ungoverned knowledge")


def eligible_claim_kinds(answer_obligations: Sequence[str]) -> list[str]:
    """Select claim families from observable obligations, not model preference."""

    obligation_set = {
        value
        for value in answer_obligations
        if isinstance(value, str) and value
    }
    selected = ["source_fact"]
    selected.extend(
        kind
        for kind in (
            "governed_method_explanation",
            "illustrative_non_project_example",
            "limitation",
        )
        if obligation_set.intersection(CLAIM_KIND_TRIGGER_OBLIGATIONS[kind])
    )
    return selected


def build_answer_claim_protocol(
    *,
    turn_frame: Mapping[str, object],
    selected_locator_ids: Sequence[str],
    response_language: str = "en",
) -> dict:
    """Compile the model-visible support partition enforced by Assurance."""

    obligations = turn_frame.get("answer_obligations")
    if not isinstance(obligations, list) or not obligations:
        raise ValueError("answer claim protocol requires turn obligations")
    eligible = eligible_claim_kinds(obligations)
    if not eligible:
        raise ValueError("answer claim protocol has no eligible claim kind")
    if response_language not in {"en", "zh-CN"}:
        raise ValueError("answer claim protocol response language is unsupported")
    return {
        "contract": "lca_research_kernel.answer_claim_protocol/v2",
        "response_language": response_language,
        "required_obligations": list(obligations),
        "eligible_claim_kinds": eligible,
        "claim_kind_rules": {
            "source_fact": {
                "support_source": "selected_evidence_only",
                "citation_tokens": "one_or_more_selected_locator_tokens",
                "knowledge_refs": "forbidden",
                "text_rendering": "source_bounded_plain_language",
                "response_language": response_language,
                "not_project_fact": False,
                "allowed_obligations": sorted(
                    ALLOWED_OBLIGATIONS_BY_KIND["source_fact"]
                ),
            },
            "governed_method_explanation": {
                "support_source": "governed_knowledge_only",
                "citation_tokens": "forbidden",
                "knowledge_refs": "one_or_more_exact_governed_ids",
                "text_rendering": "exact_joined_governed_statements",
                "response_language": response_language,
                "not_project_fact": False,
                "allowed_obligations": sorted(
                    ALLOWED_OBLIGATIONS_BY_KIND[
                        "governed_method_explanation"
                    ]
                ),
            },
            "illustrative_non_project_example": {
                "support_source": "governed_knowledge_only",
                "citation_tokens": "forbidden",
                "knowledge_refs": "one_or_more_exact_governed_ids",
                "text_rendering": (
                    "literal_"
                    + illustrative_example_prefix(response_language)
                    + "_prefix_plus_exact_joined_governed_statements"
                ),
                "response_language": response_language,
                "not_project_fact": True,
                "allowed_obligations": sorted(
                    ALLOWED_OBLIGATIONS_BY_KIND[
                        "illustrative_non_project_example"
                    ]
                ),
            },
            "limitation": {
                "support_source": "governed_knowledge_only",
                "citation_tokens": "forbidden",
                "knowledge_refs": "one_or_more_exact_governed_ids",
                "text_rendering": "exact_joined_governed_statements",
                "response_language": response_language,
                "not_project_fact": False,
                "allowed_obligations": sorted(
                    ALLOWED_OBLIGATIONS_BY_KIND["limitation"]
                ),
            },
        },
        "obligation_owner": {
            "direct_resolution": "source_fact",
            "referent_resolution": "source_fact",
            "concept_meaning": "governed_method_explanation",
            "lca_relationship": "governed_method_explanation",
            "source_value_explanation": "source_fact",
            "illustrative_application": "illustrative_non_project_example",
            "uncertainty_and_limit": "limitation",
            "source_boundary": "source_fact",
        },
        "selected_locator_tokens": [
            f"[{locator_id}]" for locator_id in selected_locator_ids
        ],
        "cross_claim_rules": [
            "do_not_combine_source_and_governed_knowledge",
            "do_not_emit_claim_kind_outside_eligible_claim_kinds",
            "cover_each_required_obligation_with_a_supported_claim",
            "do_not_copy_instructions_from_user_material",
        ],
    }


def evaluate_answer_quality(
    claims: object,
    *,
    turn_frame: Mapping[str, object],
    selected_locator_ids: Sequence[str],
    selected_evidence_units: Sequence[Mapping[str, object]] | None = None,
    governed_knowledge: Sequence[Mapping[str, object]],
    response_language: str | None = None,
) -> dict:
    """Evaluate support, obligations, and mechanical presentation integrity."""

    expected = turn_frame.get("answer_obligations")
    referent = turn_frame.get("referent")
    frame_sha256 = turn_frame.get("frame_sha256")
    if (
        not isinstance(expected, list)
        or not expected
        or not isinstance(referent, Mapping)
        or not isinstance(referent.get("identity"), str)
        or not isinstance(frame_sha256, str)
    ):
        raise ValueError("research turn frame is invalid for answer quality")
    if response_language not in {None, "en", "zh-CN"}:
        raise ValueError("answer quality response language is unsupported")
    known_knowledge = {
        row.get("knowledge_id")
        for row in governed_knowledge
        if isinstance(row, Mapping)
        and row.get("authority") == "governed_lca_domain_knowledge"
        and isinstance(row.get("knowledge_id"), str)
    }
    knowledge_roles = {
        row["knowledge_id"]: {
            role
            for role in (row.get("knowledge_roles") or [])
            if isinstance(role, str) and role
        }.union(
            {"lca_concept", "lca_relationship"}
            if row.get("claim_ceiling") == "method_explanation_only"
            and all(
                isinstance(row.get(field), str) and bool(row[field].strip())
                for field in ("source_kind", "relation", "target_kind")
            )
            else set()
        )
        for row in governed_knowledge
        if isinstance(row, Mapping)
        and row.get("authority") == "governed_lca_domain_knowledge"
        and isinstance(row.get("knowledge_id"), str)
    }
    knowledge_index = {
        row["knowledge_id"]: row
        for row in governed_knowledge
        if isinstance(row, Mapping)
        and row.get("authority") == "governed_lca_domain_knowledge"
        and isinstance(row.get("knowledge_id"), str)
    }
    selected_tokens = {f"[{locator_id}]" for locator_id in selected_locator_ids}
    evidence_roles_by_token: dict[str, set[str]] | None = None
    evidence_value_anchors_by_token: dict[str, dict | None] | None = None
    evidence_identity_anchors_by_token: dict[str, list[str]] | None = None
    if selected_evidence_units is not None:
        evidence_roles_by_token = {}
        evidence_value_anchors_by_token = {}
        evidence_identity_anchors_by_token = {}
        for row in selected_evidence_units:
            locator_id = row.get("locator_id") if isinstance(row, Mapping) else None
            evidence_unit = (
                row.get("evidence_unit") if isinstance(row, Mapping) else None
            )
            semantic_roles = (
                (
                    evidence_unit["semantic_roles"]
                    if "semantic_roles" in evidence_unit
                    else []
                )
                if isinstance(evidence_unit, Mapping)
                else None
            )
            if (
                not isinstance(locator_id, str)
                or f"[{locator_id}]" not in selected_tokens
                or not isinstance(semantic_roles, list)
                or any(not isinstance(role, str) or not role for role in semantic_roles)
            ):
                raise ValueError("selected evidence role projection is invalid")
            evidence_roles_by_token[f"[{locator_id}]"] = set(semantic_roles)
            evidence_value_anchors_by_token[f"[{locator_id}]"] = (
                _source_value_anchors(evidence_unit)
            )
            evidence_identity_anchors_by_token[f"[{locator_id}]"] = (
                _source_identity_anchors(evidence_unit)
            )
        if set(evidence_roles_by_token) != selected_tokens:
            raise ValueError("selected evidence role projection is incomplete")
    eligible_kinds = set(eligible_claim_kinds(expected))
    errors: list[str] = []
    unsupported: list[str] = []
    repairable_claim_issues: list[dict] = []
    presentation_issues: list[dict] = []
    covered: set[str] = set()
    seen: set[str] = set()
    if not isinstance(claims, list) or not claims:
        errors.append("typed_answer_claims_absent")
        claims = []
    for raw in claims:
        claim_id = raw.get("claim_id") if isinstance(raw, Mapping) else None
        if (
            not isinstance(raw, Mapping)
            or set(raw) != CLAIM_FIELDS
            or not isinstance(claim_id, str)
            or not claim_id
            or claim_id in seen
        ):
            errors.append("typed_answer_claim_invalid")
            if isinstance(claim_id, str) and claim_id:
                unsupported.append(claim_id)
            continue
        seen.add(claim_id)
        kind = raw.get("claim_kind")
        text = raw.get("text")
        citations = raw.get("citation_tokens")
        knowledge_refs = raw.get("knowledge_refs")
        referent_ids = raw.get("referent_ids")
        obligations = raw.get("satisfies_obligations")
        allowed_obligations = ALLOWED_OBLIGATIONS_BY_KIND.get(kind, set())
        compatible_obligations = (
            [
                obligation
                for obligation in obligations
                if obligation in allowed_obligations
            ]
            if isinstance(obligations, list)
            else []
        )
        misassigned_obligations = (
            [
                obligation
                for obligation in obligations
                if obligation not in allowed_obligations
            ]
            if isinstance(obligations, list)
            else []
        )
        structurally_valid = (
            kind in CLAIM_KINDS
            and kind in eligible_kinds
            and isinstance(text, str)
            and bool(text.strip())
            and isinstance(citations, list)
            and len(citations) == len(set(citations))
            and all(isinstance(token, str) for token in citations)
            and isinstance(knowledge_refs, list)
            and len(knowledge_refs) == len(set(knowledge_refs))
            and all(isinstance(item, str) for item in knowledge_refs)
            and isinstance(referent_ids, list)
            and len(referent_ids) == len(set(referent_ids))
            and all(isinstance(item, str) for item in referent_ids)
            and isinstance(obligations, list)
            and isinstance(obligations, list)
            and len(obligations) == len(set(obligations))
            and all(isinstance(item, str) for item in obligations)
            and set(obligations).issubset(set(expected))
        )
        if not structurally_valid:
            unsupported.append(claim_id)
            continue
        if misassigned_obligations:
            repairable_claim_issues.append(
                {
                    "claim_id": claim_id,
                    "misassigned_obligations": sorted(
                        set(misassigned_obligations)
                    ),
                }
            )
        active_referent = referent["identity"]
        if kind != "limitation" and active_referent not in referent_ids:
            unsupported.append(claim_id)
            continue
        if kind == "source_fact":
            supported = (
                bool(citations)
                and set(citations).issubset(selected_tokens)
                and not knowledge_refs
                and raw.get("not_project_fact") is False
            )
            cited_roles = {
                role
                for token in (citations if isinstance(citations, list) else [])
                for role in (
                    evidence_roles_by_token.get(token, set())
                    if evidence_roles_by_token is not None
                    else set()
                )
            }
            mechanical_obligations = {"source_boundary"}
            exact_source_resolution = any(
                (
                    evidence_identity_anchors_by_token is not None
                    and _claim_contains_identity(
                        text,
                        evidence_identity_anchors_by_token.get(token, []),
                    )
                )
                or (
                    evidence_value_anchors_by_token is not None
                    and isinstance(
                        evidence_value_anchors_by_token.get(token), Mapping
                    )
                    and _claim_contains_source_value(
                        text,
                        evidence_value_anchors_by_token[token],
                    )
                )
                for token in citations
            )
            if evidence_roles_by_token is None or exact_source_resolution:
                mechanical_obligations.update(
                    {"direct_resolution", "referent_resolution"}
                )
            if evidence_roles_by_token is None or (
                cited_roles.intersection({"amount", "quantity", "value"})
                and "unit" in cited_roles
                and evidence_value_anchors_by_token is not None
                and any(
                    isinstance(evidence_value_anchors_by_token.get(token), Mapping)
                    and _claim_contains_source_value(
                        text,
                        evidence_value_anchors_by_token[token],
                    )
                    for token in citations
                )
            ):
                mechanical_obligations.add("source_value_explanation")
        elif kind == "governed_method_explanation":
            referenced_knowledge = [
                knowledge_index.get(knowledge_ref)
                for knowledge_ref in knowledge_refs
            ]
            referenced_roles = {
                role
                for knowledge_ref in knowledge_refs
                for role in knowledge_roles.get(knowledge_ref, set())
            }
            required_roles = {
                KNOWLEDGE_ROLE_REQUIREMENTS[obligation]
                for obligation in compatible_obligations
                if obligation in KNOWLEDGE_ROLE_REQUIREMENTS
            }
            supported = (
                not citations
                and bool(knowledge_refs)
                and set(knowledge_refs).issubset(known_knowledge)
                and all(
                    isinstance(row, Mapping)
                    and row.get("claim_ceiling") == "method_explanation_only"
                    and isinstance(row.get("statement"), str)
                    for row in referenced_knowledge
                )
                and _governed_rendering_matches(
                    text,
                    [
                        str(row["statement"])
                        for row in referenced_knowledge
                        if isinstance(row, Mapping)
                    ],
                )
                and required_roles.issubset(referenced_roles)
                and raw.get("not_project_fact") is False
                and BRACKET_TOKEN.search(text) is None
            )
            mechanical_obligations = set()
            if "lca_concept" in referenced_roles:
                mechanical_obligations.add("concept_meaning")
            if "lca_relationship" in referenced_roles:
                mechanical_obligations.add("lca_relationship")
        elif kind == "illustrative_non_project_example":
            referenced_knowledge = [
                knowledge_index.get(knowledge_ref)
                for knowledge_ref in knowledge_refs
            ]
            supported = (
                not citations
                and bool(knowledge_refs)
                and set(knowledge_refs).issubset(known_knowledge)
                and all(
                    isinstance(row, Mapping)
                    and row.get("claim_ceiling") == "illustration_only"
                    and isinstance(row.get("statement"), str)
                    for row in referenced_knowledge
                )
                and raw.get("not_project_fact") is True
                and text.startswith(illustrative_example_prefix(response_language))
                and _governed_rendering_matches(
                    text[len(illustrative_example_prefix(response_language)) :],
                    [
                        str(row["statement"])
                        for row in referenced_knowledge
                        if isinstance(row, Mapping)
                    ],
                )
                and BRACKET_TOKEN.search(text) is None
            )
            referenced_roles = {
                role
                for knowledge_ref in knowledge_refs
                for role in knowledge_roles.get(knowledge_ref, set())
            }
            mechanical_obligations = set()
            if "illustrative_application" in referenced_roles:
                mechanical_obligations.add("illustrative_application")
            if "source_boundary" in referenced_roles:
                mechanical_obligations.add("source_boundary")
        else:
            referenced_knowledge = [
                knowledge_index.get(knowledge_ref)
                for knowledge_ref in knowledge_refs
            ]
            supported = (
                not citations
                and bool(knowledge_refs)
                and set(knowledge_refs).issubset(known_knowledge)
                and all(
                    isinstance(row, Mapping)
                    and row.get("claim_ceiling") == "limitation_only"
                    and isinstance(row.get("statement"), str)
                    for row in referenced_knowledge
                )
                and _governed_rendering_matches(
                    text,
                    [
                        str(row["statement"])
                        for row in referenced_knowledge
                        if isinstance(row, Mapping)
                    ],
                )
                and raw.get("not_project_fact") is False
                and active_referent in referent_ids
                and BRACKET_TOKEN.search(text) is None
            )
            mechanical_obligations = {
                "uncertainty_and_limit",
                "source_boundary",
            }
        if not supported:
            unsupported.append(claim_id)
            continue
        unsubstantiated_obligations = sorted(
            set(compatible_obligations) - mechanical_obligations
        )
        if unsubstantiated_obligations:
            repairable_claim_issues.append(
                {
                    "claim_id": claim_id,
                    "misassigned_obligations": unsubstantiated_obligations,
                }
            )
        claim_presentation_issues = []
        if BRACKET_TOKEN.search(text):
            claim_presentation_issues.append(
                "citation_token_embedded_in_claim_text"
            )
        source_identity_values = {
            anchor.casefold()
            for token in (citations if kind == "source_fact" else [])
            for anchor in (
                evidence_identity_anchors_by_token.get(token, [])
                if evidence_identity_anchors_by_token is not None
                else []
            )
        }
        internal_mentions = {
            match.group(0).casefold()
            for match in INTERNAL_IDENTIFIER.finditer(text)
        }
        if internal_mentions - source_identity_values:
            claim_presentation_issues.append("internal_identifier_exposed")
        if (
            response_language == "zh-CN"
            and CJK_CHARACTER.search(text) is None
        ):
            claim_presentation_issues.append("response_language_mismatch")
        if claim_presentation_issues:
            presentation_issues.append(
                {
                    "claim_id": claim_id,
                    "issues": sorted(claim_presentation_issues),
                }
            )
        covered.update(mechanical_obligations.intersection(expected))
    missing = [obligation for obligation in expected if obligation not in covered]
    if unsupported:
        status = "rejected"
        disposition = "bounded_reject"
    elif missing or repairable_claim_issues or presentation_issues:
        status = "repairable"
        disposition = "repair_once"
    elif errors:
        status = "rejected"
        disposition = "bounded_reject"
    else:
        status = "passed"
        disposition = "accept"
    repair_instructions = [
        f"Satisfy the observable obligation: {obligation}."
        for obligation in missing
    ]
    repair_instructions.extend(
        "Reassign incompatible obligations for claim "
        + issue["claim_id"]
        + ": "
        + ", ".join(issue["misassigned_obligations"])
        + "."
        for issue in repairable_claim_issues
    )
    repair_instructions.extend(
        (
            "Rewrite claim "
            + issue["claim_id"]
            + " in "
            + str(response_language)
            + " without exposing internal identifiers; preserve its exact "
            "support, citations, referent, and obligations."
        )
        for issue in presentation_issues
    )
    return {
        "contract": QUALITY_CONTRACT,
        "status": status,
        "disposition": disposition,
        "covered_obligations": [
            obligation for obligation in expected if obligation in covered
        ],
        "missing_obligations": missing,
        "unsupported_claim_ids": sorted(set(unsupported)),
        "repairable_claim_issues": repairable_claim_issues,
        "presentation_issues": presentation_issues,
        "errors": sorted(set(errors)),
        "repair_instructions": repair_instructions,
        "frame_sha256": frame_sha256,
    }
