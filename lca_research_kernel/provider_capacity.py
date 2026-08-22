"""Fail-closed, case-neutral model-provider capacity qualification."""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime
from typing import Mapping


CONTRACT = "lca_research_kernel.provider_capacity_qualification/v1"
_STATUSES = {"qualified", "deferred_capacity", "unavailable"}
_REGION_STATES = {"supported", "unsupported", "unknown"}
_FIELDS = {
    "contract",
    "provider_id",
    "provider_catalog_sha256",
    "source_lock_sha256",
    "sdk_version",
    "schema_profile",
    "adapter_capabilities",
    "region_admission_state",
    "quota",
    "estimated_calls_per_task",
    "requested_task_count",
    "probe_receipt_ref",
    "observed_at",
    "status",
    "qualified_task_count",
    "failure_code",
    "next_route",
    "executed_external_effects",
    "receipt_sha256",
}
_QUOTA_FIELDS = {
    "dimension",
    "limit",
    "remaining",
    "reset_after_seconds",
}
_PROBE_FIELDS = {"path", "sha256"}


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _quota_value(value: object, *, name: str) -> int | float | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise ValueError(f"provider capacity quota {name} is invalid")
    if name in {"limit", "remaining"}:
        if float(value) != int(value):
            raise ValueError(f"provider capacity quota {name} is invalid")
        return int(value)
    return float(value)


def _receipt_hash(receipt: Mapping[str, object]) -> str:
    return _canonical_sha256(
        {key: deepcopy(value) for key, value in receipt.items() if key != "receipt_sha256"}
    )


def build_provider_capacity_qualification(
    *,
    provider_id: str,
    provider_catalog_sha256: str,
    source_lock_sha256: str,
    sdk_version: str,
    schema_profile: str,
    adapter_capabilities: Mapping[str, object],
    region_admission_state: str,
    quota_dimension: str | None,
    observed_limit: int | None,
    observed_remaining: int | None,
    reset_after_seconds: float | None,
    estimated_calls_per_task: int,
    requested_task_count: int,
    probe_receipt_ref: Mapping[str, object] | None,
    observed_at: str,
    qualified_next_route: str = "run-one-qualified-minimum-path-task",
) -> dict:
    """Build one immutable qualification receipt without retrying a provider."""

    limit = _quota_value(observed_limit, name="limit")
    remaining = _quota_value(observed_remaining, name="remaining")
    reset = _quota_value(reset_after_seconds, name="reset_after_seconds")
    if (
        isinstance(estimated_calls_per_task, bool)
        or not isinstance(estimated_calls_per_task, int)
        or estimated_calls_per_task < 1
        or isinstance(requested_task_count, bool)
        or not isinstance(requested_task_count, int)
        or requested_task_count < 1
    ):
        raise ValueError("provider capacity task quota is invalid")
    if (
        not isinstance(qualified_next_route, str)
        or not qualified_next_route.strip()
    ):
        raise ValueError("provider capacity qualified next route is invalid")

    schema_available = adapter_capabilities.get(schema_profile) is True
    if region_admission_state == "unsupported":
        status = "unavailable"
        qualified_task_count = 0
        failure_code = "provider_precondition_failed"
        next_route = "use-qualified-provider-or-supported-region"
    elif not schema_available:
        status = "unavailable"
        qualified_task_count = 0
        failure_code = "schema_profile_unavailable"
        next_route = "qualify-provider-schema-profile"
    elif remaining is not None and remaining < estimated_calls_per_task:
        status = "deferred_capacity"
        qualified_task_count = 0
        failure_code = "rate_limited"
        next_route = "wait-for-provider-capacity-reset"
    elif probe_receipt_ref is None:
        status = "unavailable"
        qualified_task_count = 0
        failure_code = "capacity_probe_unavailable"
        next_route = "run-case-neutral-provider-capacity-probe"
    else:
        status = "qualified"
        qualified_task_count = (
            1
            if remaining is None
            else min(
                requested_task_count,
                int(remaining) // estimated_calls_per_task,
            )
        )
        failure_code = None
        next_route = qualified_next_route.strip()

    receipt = {
        "contract": CONTRACT,
        "provider_id": provider_id,
        "provider_catalog_sha256": provider_catalog_sha256,
        "source_lock_sha256": source_lock_sha256,
        "sdk_version": sdk_version,
        "schema_profile": schema_profile,
        "adapter_capabilities": deepcopy(dict(adapter_capabilities)),
        "region_admission_state": region_admission_state,
        "quota": {
            "dimension": quota_dimension,
            "limit": limit,
            "remaining": remaining,
            "reset_after_seconds": reset,
        },
        "estimated_calls_per_task": estimated_calls_per_task,
        "requested_task_count": requested_task_count,
        "probe_receipt_ref": (
            None
            if probe_receipt_ref is None
            else deepcopy(dict(probe_receipt_ref))
        ),
        "observed_at": observed_at,
        "status": status,
        "qualified_task_count": qualified_task_count,
        "failure_code": failure_code,
        "next_route": next_route,
        "executed_external_effects": 0,
    }
    receipt["receipt_sha256"] = _receipt_hash(receipt)
    validate_provider_capacity_qualification(receipt)
    return receipt


def validate_provider_capacity_qualification(
    receipt: Mapping[str, object],
) -> dict:
    if not isinstance(receipt, Mapping) or set(receipt) != _FIELDS:
        raise ValueError("provider capacity receipt fields are invalid")
    if receipt.get("contract") != CONTRACT:
        raise ValueError("provider capacity receipt contract is invalid")
    for field in ("provider_id", "sdk_version", "schema_profile", "next_route"):
        if not isinstance(receipt.get(field), str) or not receipt[field]:
            raise ValueError(f"provider capacity receipt {field} is invalid")
    for field in ("provider_catalog_sha256", "source_lock_sha256"):
        if not _is_sha256(receipt.get(field)):
            raise ValueError(f"provider capacity receipt {field} is invalid")
    capabilities = receipt.get("adapter_capabilities")
    if (
        not isinstance(capabilities, Mapping)
        or any(not isinstance(key, str) for key in capabilities)
        or any(not isinstance(value, bool) for value in capabilities.values())
    ):
        raise ValueError("provider capacity adapter capabilities are invalid")
    if receipt.get("region_admission_state") not in _REGION_STATES:
        raise ValueError("provider capacity region state is invalid")
    quota = receipt.get("quota")
    if not isinstance(quota, Mapping) or set(quota) != _QUOTA_FIELDS:
        raise ValueError("provider capacity quota fields are invalid")
    dimension = quota.get("dimension")
    if dimension is not None and (
        not isinstance(dimension, str) or not dimension
    ):
        raise ValueError("provider capacity quota dimension is invalid")
    limit = _quota_value(quota.get("limit"), name="limit")
    remaining = _quota_value(quota.get("remaining"), name="remaining")
    _quota_value(quota.get("reset_after_seconds"), name="reset_after_seconds")
    if limit is not None and remaining is not None and remaining > limit:
        raise ValueError("provider capacity quota remaining exceeds limit")
    for field in ("estimated_calls_per_task", "requested_task_count"):
        value = receipt.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("provider capacity task quota is invalid")
    probe = receipt.get("probe_receipt_ref")
    if probe is not None and (
        not isinstance(probe, Mapping)
        or set(probe) != _PROBE_FIELDS
        or not isinstance(probe.get("path"), str)
        or not probe["path"]
        or probe["path"].startswith("/")
        or ".." in probe["path"].split("/")
        or not _is_sha256(probe.get("sha256"))
    ):
        raise ValueError("provider capacity probe receipt reference is invalid")
    observed_at = receipt.get("observed_at")
    if not isinstance(observed_at, str):
        raise ValueError("provider capacity observation time is invalid")
    try:
        datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("provider capacity observation time is invalid") from error
    if receipt.get("status") not in _STATUSES:
        raise ValueError("provider capacity status is invalid")
    qualified = receipt.get("qualified_task_count")
    if isinstance(qualified, bool) or not isinstance(qualified, int) or qualified < 0:
        raise ValueError("provider capacity qualified task count is invalid")
    if receipt["status"] == "qualified":
        if qualified < 1 or receipt.get("failure_code") is not None:
            raise ValueError("qualified provider capacity receipt is inconsistent")
    elif qualified != 0 or not isinstance(receipt.get("failure_code"), str):
        raise ValueError("unavailable provider capacity receipt is inconsistent")
    if receipt.get("executed_external_effects") != 0:
        raise ValueError("provider capacity external effects are invalid")
    if receipt.get("receipt_sha256") != _receipt_hash(receipt):
        raise ValueError("provider capacity receipt hash is invalid")
    return deepcopy(dict(receipt))


def qualification_boundary(receipt: Mapping[str, object]) -> dict:
    """Return the safe control boundary without carrying runtime identities."""

    validate_provider_capacity_qualification(receipt)
    status = receipt["status"]
    quota = receipt["quota"]
    assert isinstance(quota, Mapping)
    return {
        "contract": "lca_research_kernel.research_boundary/v1",
        "boundary": (
            "provider_capacity_available"
            if status == "qualified"
            else (
                "provider_capacity_deferred"
                if status == "deferred_capacity"
                else "provider_unavailable"
            )
        ),
        "provider_id": receipt["provider_id"],
        "failure_code": receipt["failure_code"],
        "retryable": False,
        "retry_after_seconds": quota.get("reset_after_seconds"),
        "recovery_route": receipt["next_route"],
        "executed_external_effects": 0,
        "qualification_receipt_sha256": receipt["receipt_sha256"],
    }
