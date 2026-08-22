"""Deterministic, case-neutral semantics for source-bound research claims."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from copy import deepcopy
from difflib import SequenceMatcher
from typing import Mapping, Sequence


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

CLAIM_MODALITIES = {
    "legal_must",
    "legal_exclusion",
    "conditional_trigger",
    "officially_permitted",
    "official_factual",
    "recommended_practice",
}

CLAIM_CLASSES = {
    "legal_requirement",
    "legal_exclusion",
    "conditional_trigger",
    "official_option",
    "official_fact",
    "prudent_recommendation",
}

MODALITY_TO_CLAIM_CLASS = {
    "legal_must": "legal_requirement",
    "legal_exclusion": "legal_exclusion",
    "conditional_trigger": "conditional_trigger",
    "officially_permitted": "official_option",
    "official_factual": "official_fact",
    "recommended_practice": "prudent_recommendation",
}

_NEGATIVE_SCOPE = re.compile(
    r"\b(?:shall|does|do|is|are|will)\s+not\s+"
    r"(?:apply|be\s+applicable)\b"
    r"|\b(?:is|are)\s+(?:expressly\s+)?excluded\s+from\b",
    re.IGNORECASE,
)
_NEGATIVE_OBLIGATION = re.compile(
    r"\b(?:is|are|was|were)\s+not\s+"
    r"(?:required|obliged|obligated|mandatory)\b"
    r"|\b(?:shall|must)\s+not\s+be\s+"
    r"(?:required|obliged|obligated)\b"
    r"|\bnot\s+(?:required|obliged|obligated)\b"
    r"|\bno(?:\s+[a-z-]+){0,2}\s+(?:obligation|duty)\b"
    r"|\b(?:is|are|shall\s+be|must\s+be)\s+exempt(?:ed)?\s+from\b",
    re.IGNORECASE,
)
_CONDITION = re.compile(
    r"\b(?:if|when|where|unless|conditional|conditionally|"
    r"provided\s+that|subject\s+to|only\s+if)\b",
    re.IGNORECASE,
)
_PERMISSION = re.compile(
    r"\b(?:may|can|permitted|permission|allowed|authorized\s+to|"
    r"authorised\s+to|entitled\s+to)\b",
    re.IGNORECASE,
)
_NEGATED_PERMISSION = re.compile(
    r"\b(?:may|can)\s+not\b|\bcannot\b|\bcan't\b|"
    r"\bnot\s+(?:permitted|allowed|authorized|"
    r"authorised|entitled)\b",
    re.IGNORECASE,
)
_ENTITLEMENT_PERMISSION = re.compile(
    r"\bshall\s+be\s+entitled\s+to\b",
    re.IGNORECASE,
)
_RECOMMENDATION = re.compile(
    r"\b(?:should|recommend|recommended|recommendation|guidance|"
    r"best\s+practice|retain|prepare)\b",
    re.IGNORECASE,
)
_DIRECT_MANDATE = re.compile(
    r"\b(?:must|shall|required|obligated|obliged|mandatory)\b"
    r"|\b(?:has|have|had|is|are|was|were)\s+"
    r"(?:(?:an?|the)\s+|under\s+(?:an?|the)?\s*)?"
    r"(?:obligation|duty)\s+(?:to|of)\b"
    r"|\b(?:is|are)\s+to\s+"
    r"(?!be\s+(?:allowed|permitted|entitled|able)\b)[a-z][a-z-]*\b",
    re.IGNORECASE,
)
_EXCLUSIVE_AUTHORITY = re.compile(
    r"\b(?:sole|only)\b(?:\s+[a-z0-9-]+){0,4}\s+"
    r"(?:competent|authorized|authorised|entitled)\s+to\b",
    re.IGNORECASE,
)
_NOT_ONLY_MANDATE = re.compile(
    r"\b(?:shall|must)\s+not\s+only\b|\bnot\s+only\s+(?:shall|must)\b",
    re.IGNORECASE,
)

_STATEMENT_NEGATIVE = re.compile(
    r"\b(?:shall|does|do|is|are|will)\s+not\s+"
    r"(?:apply|be\s+applicable)\b"
    r"|\bnot\s+(?:required|obliged|obligated)\b"
    r"|\bno(?:\s+[a-z-]+){0,2}\s+(?:obligation|duty)\b"
    r"|\b(?:is|are|shall\s+be|must\s+be)\s+exempt(?:ed)?\s+from\b"
    r"|不适用|无需|无须|不要求|未规定|没有义务|无义务|免除|豁免",
    re.IGNORECASE,
)
_STATEMENT_POSITIVE_MANDATE = re.compile(
    r"\b(?:must|shall|required|mandatory|has\s+an?\s+obligation|"
    r"has\s+a\s+duty)\b|必须|应当|须|强制|有义务|不(?:应|得)允许|"
    r"应(?=确保|提交|提供|报告|核查|遵守|保留|记录|计算|履行|申报|"
    r"披露|缴纳|购买|支付|偿还|验证)|"
    r"(?:必然|一定)(?:仍|还|会|将)?(?:需要|需)",
    re.IGNORECASE,
)
_STATEMENT_AFFIRMATIVE_APPLICABILITY = re.compile(
    r"\b(?:this\s+(?:article|regulation|rule)\s+)?"
    r"(?:applies|is\s+applicable)\b|适用于|适用该",
    re.IGNORECASE,
)
_STATEMENT_PERMISSION = re.compile(
    r"\b(?:may|can|permitted|allowed|authorized\s+to|authorised\s+to|"
    r"entitled\s+to)\b|可以|(?<!认)可(?!能|行|见|知)|有权|获准|允许",
    re.IGNORECASE,
)
_STATEMENT_NEGATED_PERMISSION = re.compile(
    r"\b(?:may|can)\s+not\b|\bcannot\b|\bcan't\b|"
    r"\bnot\s+(?:permitted|allowed|authorized|"
    r"authorised|entitled)\b|不可以|不可|不得|不能|禁止|无权|未获准|不(?:应|得)?允许",
    re.IGNORECASE,
)
_STATEMENT_CONDITION = re.compile(
    r"\b(?:if|when|where|unless|provided\s+that|subject\s+to|only\s+if)\b"
    r"|如果|若|当(?=[^，。；]{0,24}(?:时|情况下|条件下))|"
    r"在(?=[^，。；]{0,48}时(?:$|，|,))|只要|仅在|条件下",
    re.IGNORECASE,
)

_EPISTEMIC_INDIRECT_QUESTION = re.compile(
    r"(?:证据不足|无法|尚无法|不能确定|未能确定|尚无|缺乏|未包含|未明确)"
    r"[^。；!?！？]{0,120}?(?:是否|能否|可否)"
    r"[^。；!?！？]{0,120}?(?:规定|要求|说明|依据|证据|结论|状态|"
    r"查验|适用|使用|承担|属于|构成|需要|许可)",
    re.IGNORECASE,
)

_USER_VISIBLE_SENTENCE_SPLIT = re.compile(
    r"[。；;!?！？\n\r]+|(?<!\d)\.(?!\d)"
)
_USER_VISIBLE_SUBCLAUSE_SPLIT = re.compile(r"[，,:：]+")


def required_user_visible_legal_modalities(text: object) -> frozenset[str]:
    """Return legal modalities that a user-visible assertion must bind.

    This is deliberately narrower than general semantic classification: it
    detects only explicit exclusions, mandates, and permissions that must not
    appear in an answer without a corresponding source-bound claim.
    """

    if not isinstance(text, str) or not text.strip():
        return frozenset()
    # An explicit unresolved question (for example, "evidence does not show
    # whether X may be used") is an epistemic boundary, not an assertion that
    # X is legally permitted.  Mask only the bounded indirect-question span;
    # any later affirmative permission or mandate remains visible to the
    # ordinary fail-closed detectors below.
    text = _EPISTEMIC_INDIRECT_QUESTION.sub(
        " unresolved_epistemic_question ", text
    )
    required: set[str] = set()
    if _STATEMENT_NEGATIVE.search(text):
        required.add("legal_exclusion")
    if _STATEMENT_POSITIVE_MANDATE.search(text):
        required.add(
            "conditional_trigger"
            if _STATEMENT_CONDITION.search(text)
            else "legal_must"
        )
    if _STATEMENT_NEGATED_PERMISSION.search(text):
        required.add("legal_must")
    if _STATEMENT_PERMISSION.search(text) and not _STATEMENT_NEGATED_PERMISSION.search(
        text
    ):
        required.add(
            "conditional_trigger"
            if _STATEMENT_CONDITION.search(text)
            else "officially_permitted"
        )
    return frozenset(required)


def _normalized_visible_clause(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _uses_cjk(value: str) -> bool:
    return any("\u4e00" <= character <= "\u9fff" for character in value)


def _same_language_clause_overlap(clause: str, statement: str) -> bool:
    if clause == statement:
        return True
    if min(len(clause), len(statement)) < 8:
        return False
    return SequenceMatcher(None, clause, statement).ratio() >= 0.55


def _user_visible_legal_clauses(text: str) -> tuple[str, ...]:
    """Split assertions without detaching a condition from its consequence."""

    clauses: list[str] = []
    for raw_sentence in _USER_VISIBLE_SENTENCE_SPLIT.split(text):
        fragments = [
            fragment.strip(" \t-•0123456789.)）(")
            for fragment in _USER_VISIBLE_SUBCLAUSE_SPLIT.split(raw_sentence)
            if fragment.strip(" \t-•0123456789.)）(")
        ]
        pending_condition: list[str] = []
        for fragment in fragments:
            modalities = required_user_visible_legal_modalities(fragment)
            if _STATEMENT_CONDITION.search(fragment) and not modalities:
                pending_condition.append(fragment)
                continue
            if not modalities:
                if pending_condition:
                    pending_condition.append(fragment)
                continue
            clause = "，".join([*pending_condition, fragment])
            clauses.append(clause)
            pending_condition = []
    return tuple(clauses)


def unbound_user_visible_legal_clauses(
    text: object,
    claims: Sequence[Mapping[str, object]],
) -> tuple[str, ...]:
    """Return asserted legal clauses not covered by a bound claim statement.

    A modality set is not evidence coverage: unrelated duties can both be
    ``legal_must``. Each visible legal clause must therefore be an exact
    normalized fragment of an admitted claim statement. The Domain Kernel
    separately validates every claim's source identity and modality.
    """

    if not isinstance(text, str) or not text.strip():
        return ()
    claim_statements = tuple(
        (normalized, str(claim.get("modality", "")))
        for claim in claims
        if isinstance(claim, Mapping)
        for statement in [claim.get("statement")]
        if isinstance(statement, str) and statement.strip()
        for claim_clause in (_user_visible_legal_clauses(statement) or (statement,))
        for normalized in [_normalized_visible_clause(claim_clause)]
        if normalized
    )
    unbound: list[str] = []
    seen: set[str] = set()
    for clause in _user_visible_legal_clauses(text):
        required_modalities = required_user_visible_legal_modalities(clause)
        if not clause or not required_modalities:
            continue
        normalized_clause = _normalized_visible_clause(clause)
        if not normalized_clause:
            continue
        covered = any(
            modality in required_modalities
            and _uses_cjk(normalized_clause) == _uses_cjk(statement)
            and _same_language_clause_overlap(normalized_clause, statement)
            for statement, modality in claim_statements
        )
        if not covered and normalized_clause not in seen:
            seen.add(normalized_clause)
            unbound.append(clause)
    return tuple(unbound)


def _literal_semantics(literal: str) -> tuple[str | None, str]:
    if _NOT_ONLY_MANDATE.search(literal):
        return "legal_must", "affirmative_legal_mandate"
    if _NEGATED_PERMISSION.search(literal):
        return None, "negated_permission_not_supported"
    if _CONDITION.search(literal):
        return "conditional_trigger", "conditional_legal_trigger"
    if _NEGATIVE_SCOPE.search(literal):
        return "legal_exclusion", "negative_legal_scope"
    if _NEGATIVE_OBLIGATION.search(literal):
        return "legal_exclusion", "negative_legal_obligation"
    mandate_basis = _ENTITLEMENT_PERMISSION.sub("", literal)
    if _EXCLUSIVE_AUTHORITY.search(literal) or _DIRECT_MANDATE.search(
        mandate_basis
    ):
        return "legal_must", "affirmative_legal_mandate"
    if _PERMISSION.search(literal):
        return "officially_permitted", "official_permission"
    if _RECOMMENDATION.search(literal):
        return "recommended_practice", "prudent_recommendation"
    return None, "no_deterministic_modality_cue"


def resolve_claim_modality(
    *,
    declared_modality: str,
    literal: str,
    statement: str,
    source_sha256: str,
) -> dict:
    """Resolve a source-bound claim modality without changing its raw candidate."""

    if declared_modality not in CLAIM_MODALITIES:
        raise ValueError("declared claim modality is unsupported")
    if not isinstance(literal, str) or not literal.strip():
        raise ValueError("claim evidence literal is absent")
    if not isinstance(statement, str) or not statement.strip():
        raise ValueError("claim statement is absent")
    if not isinstance(source_sha256, str) or SHA256_RE.fullmatch(source_sha256) is None:
        raise ValueError("claim source sha256 is invalid")

    normalized, rule_id = _literal_semantics(literal)
    status = "resolved"
    rationale = "The exact source literal deterministically identifies the claim modality."

    statement_negative = _STATEMENT_NEGATIVE.search(statement) is not None
    statement_mandate = (
        _STATEMENT_POSITIVE_MANDATE.search(statement) is not None
    )
    statement_positive = (
        statement_mandate
        or _STATEMENT_AFFIRMATIVE_APPLICABILITY.search(statement) is not None
    )
    statement_negates_permission = (
        _STATEMENT_NEGATED_PERMISSION.search(statement) is not None
    )
    if (
        declared_modality == "officially_permitted"
        and normalized != "conditional_trigger"
        and _PERMISSION.search(literal)
        and _STATEMENT_PERMISSION.search(statement)
        and not statement_negates_permission
        and not statement_positive
    ):
        normalized = "officially_permitted"
        rule_id = "official_permission"

    if normalized is None:
        if declared_modality == "official_factual":
            normalized = declared_modality
            rule_id = "neutral_official_statement"
            rationale = "No obligation, condition, permission, or recommendation cue is present."
        else:
            status = "unresolved"
            rationale = "The literal does not contain a deterministic cue for the declared modality."

    if declared_modality == "officially_permitted" and statement_negates_permission:
        status = "contradicted"
        normalized = None
        rule_id = "statement_negates_permission"
        rationale = "The candidate statement negates the permission expressed by the source literal."
    elif normalized == "legal_exclusion":
        if not statement_negative:
            status = "contradicted"
            normalized = None
            rule_id = "statement_reverses_exclusion"
            rationale = "The candidate statement reverses the exclusion expressed by the source literal."
    elif normalized == "legal_must" and statement_negative:
        status = "contradicted"
        normalized = None
        rule_id = "statement_reverses_mandate"
        rationale = "The candidate statement negates the mandate expressed by the source literal."
    elif (
        statement_mandate
        and not statement_negative
        and _NOT_ONLY_MANDATE.search(literal) is None
        and _EXCLUSIVE_AUTHORITY.search(literal) is None
        and _DIRECT_MANDATE.search(_ENTITLEMENT_PERMISSION.sub("", literal))
        is None
    ):
        status = "contradicted"
        normalized = None
        rule_id = "statement_adds_mandate"
        rationale = (
            "The candidate statement adds a mandate that is absent from the "
            "exact source literal."
        )

    return {
        "contract": "lca_research_kernel.claim_modality_resolution/v1",
        "status": status,
        "declared_modality": declared_modality,
        "normalized_modality": normalized,
        "rule_id": rule_id,
        "changed": status == "resolved" and normalized != declared_modality,
        "literal_sha256": hashlib.sha256(literal.encode("utf-8")).hexdigest(),
        "source_sha256": source_sha256,
        "rationale": rationale,
    }


def normalize_claim_statements(
    claims: Sequence[Mapping[str, object]],
) -> tuple[list[dict], list[dict], list[str]]:
    """Return an admissible copy, a resolution ledger, and fail-closed errors."""

    normalized_claims = deepcopy(list(claims))
    resolutions: list[dict] = []
    errors: list[str] = []
    for index, claim in enumerate(claims):
        if not isinstance(claim, Mapping):
            errors.append("candidate_claim_statement_invalid")
            continue
        evidence = claim.get("evidence_basis")
        if not isinstance(evidence, Mapping):
            errors.append("candidate_claim_evidence_basis_drifted")
            continue
        try:
            resolution = resolve_claim_modality(
                declared_modality=str(claim.get("modality")),
                literal=str(evidence.get("literal", "")),
                statement=str(claim.get("statement", "")),
                source_sha256=str(evidence.get("source_sha256", "")),
            )
        except ValueError:
            errors.append("candidate_claim_modality_contradicted")
            continue
        resolutions.append(resolution)
        if resolution["status"] != "resolved":
            errors.append("candidate_claim_modality_contradicted")
            continue
        normalized_claims[index]["modality"] = resolution["normalized_modality"]
    return normalized_claims, resolutions, sorted(set(errors))
