"""Deterministic identity for one official public-source evidence operation."""

from __future__ import annotations

from typing import Mapping

from .content_identity import canonical_sha256


OFFICIAL_PUBLIC_SOURCE_CAPABILITY_ID = "official-public-source-read"


def official_public_source_operation_id(
    payload: Mapping[str, object],
    *,
    capability_id: str = OFFICIAL_PUBLIC_SOURCE_CAPABILITY_ID,
) -> str:
    if not isinstance(payload, Mapping):
        raise ValueError("official source operation payload is absent")
    required = ("contract", "question_id", "run_id", "method", "url", "parse_location")
    if any(
        not isinstance(payload.get(key), str)
        or not str(payload[key]).strip()
        for key in required
    ):
        raise ValueError("official source operation payload identity is incomplete")
    if payload.get("contract") != "official_public_source_request/v1":
        raise ValueError("official source operation payload contract is unsupported")
    if not isinstance(capability_id, str) or not capability_id:
        raise ValueError("official source capability identity is absent")
    basis = {
        "contract": "lca_research_kernel.official_source_operation_identity/v1",
        "capability_id": capability_id,
        "question_id": str(payload["question_id"]).strip(),
        "run_id": str(payload["run_id"]).strip(),
        "method": str(payload["method"]).upper(),
        "url": str(payload["url"]).strip(),
        "parse_location": str(payload["parse_location"]).strip(),
        "extraction": payload.get("extraction"),
        "industry_summary": payload.get("industry_summary", False),
        "refresh": payload.get("refresh", False),
    }
    return "capability-" + canonical_sha256(basis)[:24]
