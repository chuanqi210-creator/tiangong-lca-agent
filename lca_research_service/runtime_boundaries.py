"""Sanitized Product Interface failure projections for service clients."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_SAFE_TOKEN = re.compile(r"^[a-z][a-z0-9_.-]{0,95}$")

_TEMPORARY_FAILURE_CODES = frozenset(
    {
        "capacity_probe_unavailable",
        "elapsed_time_budget_exhausted",
        "execution_failed",
        "model_call_budget_exhausted",
        "model_unavailable",
        "product_interface_runtime_unavailable",
        "provider_capacity_deferred",
        "provider_failure",
        "provider_precondition_failed",
        "provider_timeout",
        "rate_limited",
        "runtime_unavailable",
        "timeout",
        "tool_call_budget_exhausted",
        "tiangong_credential_required",
        "tiangong_runtime_unavailable",
    }
)

_INVALID_INPUT_CODES = frozenset(
    {
        "duplicate_official_source_body",
        "invalid_capability_intent",
        "invalid_request",
        "invalid_result_or_contract",
        "source_bound_candidate_not_admissible",
    }
)

_TYPED_BOOTSTRAP_FAILURES = {
    "source_lock_drift": {
        "diagnostic_layer": "context_failure",
        "recovery_route": "restore-source-lock",
    },
    "provider_configuration_invalid": {
        "diagnostic_layer": "harness_failure",
        "recovery_route": "repair-provider-profile",
    },
    "filesystem_runtime_unavailable": {
        "diagnostic_layer": "harness_failure",
        "recovery_route": "restore-runtime-storage",
    },
}


def _safe_token(value: object, fallback: str) -> str:
    return value if isinstance(value, str) and _SAFE_TOKEN.fullmatch(value) else fallback


def normalize_runtime_boundary(value: Mapping[str, Any]) -> dict[str, Any]:
    """Project only stable machine fields; never expose provider error bodies."""

    failure_code = _safe_token(
        value.get("failure_code"), "unclassified_runtime_failure"
    )
    retryable = value.get("retryable") is True
    if retryable or failure_code in _TEMPORARY_FAILURE_CODES:
        category = "temporarily_unavailable"
    elif failure_code in _INVALID_INPUT_CODES:
        category = "invalid_input"
    else:
        category = "unexpected_failure"
    typed_bootstrap = _TYPED_BOOTSTRAP_FAILURES.get(failure_code)
    projection: dict[str, Any] = {
        "category": category,
        "boundary": _safe_token(value.get("boundary"), "runtime_failure"),
        "failure_code": failure_code,
        "retryable": retryable,
        "recovery_route": _safe_token(
            value.get("recovery_route"),
            (
                typed_bootstrap["recovery_route"]
                if typed_bootstrap is not None
                else "retry-bounded-service-operation"
                if retryable
                else "inspect-runtime-failure"
            ),
        ),
    }
    if typed_bootstrap is not None:
        projection["diagnostic_layer"] = typed_bootstrap[
            "diagnostic_layer"
        ]
    retry_after = value.get("retry_after_seconds")
    if isinstance(retry_after, int) and not isinstance(retry_after, bool):
        if 0 <= retry_after <= 86_400:
            projection["retry_after_seconds"] = retry_after
    return projection
