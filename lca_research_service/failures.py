"""Bounded service failure taxonomy without exception-message disclosure."""

from __future__ import annotations

from typing import Any

FAILURE_CONTRACT = "lca_research_service.failure_receipt/v1"


def normalize_failure(error: BaseException, *, layer: str) -> dict[str, Any]:
    if isinstance(error, TimeoutError):
        code, retryable = "timeout", True
    elif isinstance(error, PermissionError):
        code, retryable = "permission_denied", False
    elif isinstance(error, FileNotFoundError):
        code, retryable = "capability_unavailable", False
    elif isinstance(error, ValueError):
        code, retryable = "invalid_result_or_contract", False
    else:
        code, retryable = "execution_failed", False
    return {
        "contract": FAILURE_CONTRACT,
        "layer": layer,
        "failure_code": code,
        "retryable": retryable,
        "executed_external_effects": [],
        "recovery_route": (
            "retry-bounded-service-operation"
            if retryable
            else "inspect-service-failure-receipt"
        ),
    }
