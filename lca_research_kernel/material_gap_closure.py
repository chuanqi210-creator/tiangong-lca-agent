"""Deterministic, case-independent static-material gap resolution ladder."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Mapping, Optional


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LADDER = [
    "authoritative_supplement",
    "transparent_proxy",
    "parameter_range",
    "bounded_exclusion",
    "close_with_explicit_gap",
]


def _source_ref_valid(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("path"), str)
        and bool(value["path"])
        and isinstance(value.get("sha256"), str)
        and SHA256_RE.fullmatch(value["sha256"]) is not None
    )


class MaterialGapClosure:
    def resolve(
        self,
        gap: Mapping[str, object],
        *,
        availability_decision: Mapping[str, object] | None = None,
    ) -> dict:
        normalized = self._validate_gap(gap)
        if availability_decision is not None:
            if (
                availability_decision.get("decision_kind")
                != "factual_data_availability"
                or availability_decision.get("decision") != "unavailable"
                or normalized["gap_id"]
                not in (availability_decision.get("gap_ids") or [])
            ):
                raise ValueError("material gap availability decision is invalid")
            normalized["user_data_status"] = "not_available"
        common = {
            "contract": "lca_research_kernel.material_gap_resolution/v1",
            "gap_id": normalized["gap_id"],
            "required_role": normalized["required_role"],
            "decision_relevance": normalized["decision_relevance"],
            "ladder": list(LADDER),
            "authorization_required": False,
            "executed_external_effects": 0,
        }
        if normalized["user_data_status"] in {"unknown", "available"}:
            return {
                **common,
                "status": "awaiting_user_data",
                "interaction_kind": "factual_data_availability",
                "question_count": (
                    1 if normalized["user_data_status"] == "unknown" else 0
                ),
                "question": (
                    "你是否拥有该缺失角色的数据？如有，请提供来源、值、单位和版本；如没有，Agent 将自动进入降级梯级。"
                    if normalized["user_data_status"] == "unknown"
                    else None
                ),
                "next_route": "receive-user-material-or-confirm-unavailable",
            }

        authority = self._select_authority(normalized["authority_candidates"])
        if authority is not None:
            return {
                **common,
                "status": "resolved",
                "selected_rung": "authoritative_supplement",
                "classification": "official",
                "resolution": authority,
                "limitations": [],
                "claim_level": "source_bound",
                "next_route": "validate-authoritative-material-in-domain-kernel",
            }

        proxy = self._select_proxy(normalized["proxy_candidates"])
        if proxy is not None:
            return {
                **common,
                "status": "resolved_with_degradation",
                "selected_rung": "transparent_proxy",
                "classification": "candidate",
                "resolution": proxy,
                "limitations": deepcopy(proxy["limitations"]),
                "claim_level": "screening_only",
                "next_route": "run-proxy-sensitivity-and-retain-limitations",
            }

        parameter_range = self._validate_range(normalized["parameter_range"])
        if parameter_range is not None:
            return {
                **common,
                "status": "resolved_with_degradation",
                "selected_rung": "parameter_range",
                "classification": "assumption",
                "resolution": parameter_range,
                "limitations": ["Result depends on the declared parameter interval."],
                "claim_level": "screening_only",
                "next_route": "run-bounded-parameter-sensitivity",
            }

        exclusion = normalized["bounded_exclusion"]
        if self._valid_exclusion(exclusion):
            return {
                **common,
                "status": "closed_with_limitation",
                "selected_rung": "bounded_exclusion",
                "classification": "missing",
                "resolution": deepcopy(dict(exclusion)),
                "limitations": [exclusion["reason"]],
                "closed_claims": deepcopy(exclusion["affected_claims"]),
                "retained_claims": deepcopy(exclusion["retained_claims"]),
                "claim_level": "bounded_no_conclusion",
                "next_route": "deliver-bounded-result-with-exclusion",
            }

        return {
            **common,
            "status": "closed_with_explicit_gap",
            "selected_rung": "close_with_explicit_gap",
            "classification": "missing",
            "resolution": {
                "gap_reason": "No admissible authority, proxy, range, or bounded exclusion was supplied.",
                "decision_impact": normalized["decision_relevance"],
            },
            "limitations": [normalized["decision_relevance"]],
            "closed_claims": [normalized["decision_relevance"]],
            "retained_claims": ["explicit_gap_description"],
            "claim_level": "bounded_no_conclusion",
            "next_route": "deliver-explicit-gap-and-closed-claim",
        }

    def _validate_gap(self, gap: Mapping[str, object]) -> dict:
        if not isinstance(gap, Mapping) or gap.get("contract") != (
            "lca_research_kernel.material_gap/v1"
        ):
            raise ValueError("material gap contract is unsupported")
        normalized = deepcopy(dict(gap))
        for key in ("gap_id", "required_role", "decision_relevance"):
            if not isinstance(normalized.get(key), str) or not normalized[key].strip():
                raise ValueError(f"material gap {key} is absent")
        if normalized.get("user_data_status") not in {
            "unknown",
            "available",
            "not_available",
        }:
            raise ValueError("material gap user_data_status is unsupported")
        for key in ("authority_candidates", "proxy_candidates"):
            if not isinstance(normalized.get(key), list):
                raise ValueError(f"material gap {key} must be a list")
        return normalized

    def _select_authority(self, candidates: list) -> Optional[dict]:
        valid = []
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            required_strings = ("authority", "identity", "version", "unit")
            if (
                candidate.get("qualification") == "exact_version_source_bound"
                and _source_ref_valid(candidate.get("source_ref"))
                and all(
                    isinstance(candidate.get(key), str) and candidate[key]
                    for key in required_strings
                )
                and candidate.get("value") is not None
            ):
                valid.append(deepcopy(dict(candidate)))
        if not valid:
            return None
        return sorted(
            valid,
            key=lambda row: (
                row["authority"],
                row["identity"],
                row["version"],
                row["source_ref"]["path"],
            ),
        )[0]

    def _select_proxy(self, candidates: list) -> Optional[dict]:
        valid = []
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            if (
                _source_ref_valid(candidate.get("source_ref"))
                and all(
                    isinstance(candidate.get(key), str) and candidate[key]
                    for key in (
                        "proxy_identity",
                        "unit",
                        "transfer_justification",
                    )
                )
                and candidate.get("value") is not None
                and isinstance(candidate.get("limitations"), list)
                and bool(candidate["limitations"])
            ):
                valid.append(deepcopy(dict(candidate)))
        if not valid:
            return None
        return sorted(
            valid,
            key=lambda row: (row["proxy_identity"], row["source_ref"]["path"]),
        )[0]

    def _validate_range(self, value: object) -> Optional[dict]:
        if not isinstance(value, Mapping):
            return None
        lower = value.get("lower")
        upper = value.get("upper")
        if (
            isinstance(lower, (int, float))
            and isinstance(upper, (int, float))
            and lower <= upper
            and isinstance(value.get("unit"), str)
            and bool(value["unit"])
            and isinstance(value.get("basis"), str)
            and bool(value["basis"])
            and _source_ref_valid(value.get("source_ref"))
        ):
            return deepcopy(dict(value))
        return None

    def _valid_exclusion(self, value: object) -> bool:
        return (
            isinstance(value, Mapping)
            and isinstance(value.get("reason"), str)
            and bool(value["reason"])
            and isinstance(value.get("affected_claims"), list)
            and bool(value["affected_claims"])
            and isinstance(value.get("retained_claims"), list)
            and bool(value["retained_claims"])
        )
