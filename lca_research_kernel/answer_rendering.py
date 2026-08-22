"""Deterministic rendering for accepted typed material-answer claims."""

from __future__ import annotations

from typing import Mapping, Sequence


SUPPORTED_RESPONSE_LANGUAGES = frozenset({"en", "zh-CN"})
_TERMINAL_PUNCTUATION = ".。!?！？"


def illustrative_example_prefix(response_language: str | None) -> str:
    """Return the user-visible non-project-example label for one language."""

    if response_language is None or response_language == "zh-CN":
        return "说明性示例："
    if response_language == "en":
        return "Illustrative example: "
    raise ValueError("material answer response language is unsupported")


def normalize_rendered_clause_text(text: object) -> str:
    """Remove only terminal sentence punctuation before deterministic joining."""

    if not isinstance(text, str) or not text.strip():
        raise ValueError("typed material answer claim text is empty")
    normalized = text.strip().rstrip(_TERMINAL_PUNCTUATION).rstrip()
    if not normalized:
        raise ValueError("typed material answer claim text is empty")
    return normalized


def render_typed_material_answer(
    claims: Sequence[Mapping[str, object]],
    *,
    response_language: str,
) -> str:
    """Render claim text and citations once with locale-stable punctuation."""

    if response_language not in SUPPORTED_RESPONSE_LANGUAGES:
        raise ValueError("material answer response language is unsupported")
    clauses: list[str] = []
    for claim in claims:
        text = claim.get("text")
        citations = claim.get("citation_tokens")
        if (
            not isinstance(text, str)
            or not text.strip()
            or not isinstance(citations, list)
            or any(not isinstance(token, str) or not token for token in citations)
        ):
            raise ValueError("typed material answer claim is not renderable")
        clause = normalize_rendered_clause_text(text)
        if citations:
            clause += " " + " ".join(citations)
        clauses.append(clause)
    if not clauses:
        raise ValueError("typed material answer has no renderable claims")
    if response_language == "zh-CN":
        return "。".join(clauses) + "。"
    return ". ".join(clauses) + "."
