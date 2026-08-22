"""Provider-neutral, candidate-only model port for the D-18 runtime."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from copy import copy, deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

REQUEST_CONTRACT = "lca_research_kernel.model_request/v1"
TYPED_REQUEST_CONTRACT = "lca_research_kernel.model_request/v2"
CANDIDATE_CONTRACT = "lca_research_kernel.model_candidate/v1"
CAPACITY_PROBE_TASK = (
    "Validate the generic structured source-candidate schema without producing "
    "a scientific result."
)
CAPACITY_PROBE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "source_requests", "limitations"],
    "properties": {
        "status": {"type": "string", "enum": ["candidate_only"]},
        "source_requests": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["purpose", "url", "method"],
                "properties": {
                    "purpose": {"type": "string"},
                    "url": {"type": "string"},
                    "method": {"type": "string", "enum": ["GET", "HEAD"]},
                },
            },
        },
        "limitations": {"type": "array", "items": {"type": "string"}},
    },
}


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def model_request_sha256(value: Mapping[str, object]) -> str:
    """Hash logical model work without ephemeral per-attempt timeout state."""

    logical = deepcopy(dict(value))
    logical.pop("runtime_timeout_seconds", None)
    return canonical_sha256(logical)


class ModelGatewayError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        next_route: str,
        retry_after_seconds: Optional[float] = None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.next_route = next_route
        if retry_after_seconds is not None and (
            isinstance(retry_after_seconds, bool)
            or not isinstance(retry_after_seconds, (int, float))
            or not math.isfinite(float(retry_after_seconds))
            or float(retry_after_seconds) < 0
        ):
            raise ValueError("model retry_after_seconds is invalid")
        self.retry_after_seconds = (
            None
            if retry_after_seconds is None
            else float(retry_after_seconds)
        )


def _require_request(request: Mapping[str, object]) -> dict[str, Any]:
    if not isinstance(request, Mapping) or request.get("contract") not in {
        REQUEST_CONTRACT,
        TYPED_REQUEST_CONTRACT,
    }:
        raise ModelGatewayError(
            "invalid_request",
            "model request contract is unsupported",
            retryable=False,
            next_route="repair-model-request",
        )
    normalized = deepcopy(dict(request))
    if normalized["contract"] == TYPED_REQUEST_CONTRACT:
        from .typed_context import validate_context_package

        try:
            normalized["context"] = validate_context_package(
                normalized.get("context")
            )
        except (TypeError, ValueError) as error:
            raise ModelGatewayError(
                "invalid_context_package",
                "typed model context package is invalid",
                retryable=False,
                next_route="rebuild-source-bound-context",
            ) from error
    for key in ("request_id", "task"):
        if not isinstance(normalized.get(key), str) or not normalized[key].strip():
            raise ModelGatewayError(
                "invalid_request",
                f"model request {key} is absent",
                retryable=False,
                next_route="repair-model-request",
            )
    if not isinstance(normalized.get("context"), Mapping) or not isinstance(
        normalized.get("output_schema"), Mapping
    ):
        raise ModelGatewayError(
            "invalid_request",
            "model request context and output_schema must be objects",
            retryable=False,
            next_route="repair-model-request",
        )
    for key in ("provider", "model"):
        if key in normalized and (
            not isinstance(normalized.get(key), str) or not normalized[key].strip()
        ):
            raise ModelGatewayError(
                "invalid_request",
                f"model request {key} is invalid",
                retryable=False,
                next_route="repair-model-request",
            )
    runtime_timeout = normalized.get("runtime_timeout_seconds")
    if (
        runtime_timeout is not None
        and (
            isinstance(runtime_timeout, bool)
            or not isinstance(runtime_timeout, (int, float))
            or not math.isfinite(float(runtime_timeout))
            or runtime_timeout <= 0
        )
    ):
        raise ModelGatewayError(
            "invalid_request",
            "model request runtime timeout is invalid",
            retryable=False,
            next_route="repair-model-request",
        )
    tools = normalized.setdefault("tools", [])
    required_capabilities = normalized.setdefault(
        "required_capabilities", ["structured_output"]
    )
    if not isinstance(tools, list) or not isinstance(required_capabilities, list):
        raise ModelGatewayError(
            "invalid_request",
            "model tools and required_capabilities must be lists",
            retryable=False,
            next_route="repair-model-request",
        )
    if any(
        not isinstance(item, str) or not item.strip()
        for item in required_capabilities
    ):
        raise ModelGatewayError(
            "invalid_request",
            "model required_capabilities are invalid",
            retryable=False,
            next_route="repair-model-request",
        )
    return normalized


def validate_model_candidate_request_binding(
    candidate: Mapping[str, object],
    request: Mapping[str, object],
) -> dict:
    """Mechanically bind a candidate to the exact governed request.

    Provider output is still candidate-only.  This check prevents an adapter,
    replay fixture, or recovery path from attaching an otherwise valid output
    to a different Research Program, Context Package, or provider profile.
    """

    if not isinstance(candidate, Mapping):
        raise ValueError("model candidate binding is invalid")
    normalized_request = _require_request(request)
    normalized_candidate = deepcopy(dict(candidate))
    if (
        normalized_candidate.get("request_id")
        != normalized_request["request_id"]
        or normalized_candidate.get("request_sha256")
        != model_request_sha256(normalized_request)
    ):
        raise ValueError("model candidate request binding drifted")
    typed_fields = {
        "context_package_sha256",
        "research_program_sha256",
        "provider_profile_ref",
    }
    if normalized_request["contract"] != TYPED_REQUEST_CONTRACT:
        if typed_fields & set(normalized_candidate):
            raise ValueError("legacy model candidate carried typed authority")
        return normalized_candidate
    context = normalized_request["context"]
    if (
        normalized_candidate.get("context_package_sha256")
        != context["package_sha256"]
    ):
        raise ValueError("model candidate context package binding drifted")
    if (
        normalized_candidate.get("research_program_sha256")
        != context["research_program_ref"]["program_sha256"]
    ):
        raise ValueError("model candidate research program binding drifted")
    profile_ref = normalized_candidate.get("provider_profile_ref")
    if (
        not isinstance(profile_ref, Mapping)
        or set(profile_ref) != {"profile_id", "profile_sha256"}
        or not isinstance(profile_ref.get("profile_id"), str)
        or not profile_ref["profile_id"].strip()
        or not isinstance(profile_ref.get("profile_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", profile_ref["profile_sha256"])
        is None
    ):
        raise ValueError("model candidate provider profile binding is invalid")
    return normalized_candidate


def _provider_request_projection(request: Mapping[str, object]) -> dict:
    """Strip orchestration authorization before an adapter sees the request."""

    if request.get("contract") != TYPED_REQUEST_CONTRACT:
        return deepcopy(dict(request))
    from .typed_context import provider_context_projection

    projected = {
        key: deepcopy(value)
        for key, value in request.items()
        if key != "context"
    }
    projected["contract"] = REQUEST_CONTRACT
    projected["context"] = provider_context_projection(request["context"])
    return projected


class DeterministicFakeAdapter:
    provider_id = "fake"

    def __init__(self, response: object, *, model_id: str = "deterministic-test"):
        self.response = response
        self.model_id = model_id

    def reason(self, request: Mapping[str, object]) -> tuple[dict[str, Any], dict[str, Any]]:
        if isinstance(self.response, Exception):
            raise self.response
        if not isinstance(self.response, Mapping):
            raise ModelGatewayError(
                "invalid_provider_output",
                "fake adapter response must be an object",
                retryable=False,
                next_route="repair-model-fixture",
            )
        return deepcopy(dict(self.response)), {
            "provider_response_id": "fake-" + canonical_sha256(request)[:16],
            "model": self.model_id,
            "provider": self.provider_id,
            "adapter_id": self.provider_id,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
            "tool_requests": [],
            "duration_ms": 0,
            "cost": None,
        }

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": self.provider_id,
            "adapter_id": self.provider_id,
            "configured_model_id": self.model_id,
            "structured_output": True,
            "streaming": True,
            "tool_requests": True,
            "generic_source_candidate_v1": True,
            "remote_execution": False,
        }


class ModelGateway:
    """Capability-policy model selection with bounded retry, failover and trace."""

    def __init__(
        self,
        adapters: Mapping[str, object],
        *,
        default_provider: Optional[str] = None,
        policy: Optional[Mapping[str, object]] = None,
        clock: Callable[[], str] = lambda: "1970-01-01T00:00:00Z",
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        remaining_budget_seconds: Optional[Callable[[], float]] = None,
        execution_environment: object = None,
        answer_timing: object = None,
    ):
        self.adapters = dict(adapters)
        self.default_provider = default_provider
        self.policy = deepcopy(dict(policy)) if isinstance(policy, Mapping) else None
        self.clock = clock
        self.monotonic = monotonic
        self.sleep = sleep
        self.remaining_budget_seconds = remaining_budget_seconds
        self._circuit_failures: dict[str, int] = {}
        self._circuit_open: set[str] = set()
        self._circuit_opened_monotonic: dict[str, float] = {}
        self._circuit_opened_at: dict[str, str] = {}
        self._circuit_runtime_observed: set[str] = set()
        self._circuit_restored_event_keys: dict[
            str, tuple[float, int, str]
        ] = {}
        self.execution_environment = execution_environment
        self.answer_timing = answer_timing
        if not self.adapters:
            raise ValueError("at least one model adapter is required")
        if default_provider is not None and default_provider not in self.adapters:
            raise ValueError("default model provider is not registered")
        if self.policy is not None:
            self._validate_policy(self.policy)

    def reason(
        self,
        request: Mapping[str, object],
        *,
        trace: object = None,
        pre_attempt: Optional[
            Callable[[Mapping[str, object], Mapping[str, object]], None]
        ] = None,
    ) -> dict[str, Any]:
        normalized = _require_request(request)
        request_sha256 = model_request_sha256(normalized)
        if trace is not None:
            self._restore_trace_runtime_state(trace)
            prior = trace.lookup_model_candidate(request_sha256)  # type: ignore[attr-defined]
            if prior is not None:
                return deepcopy(dict(prior))
        if self.policy is not None and "provider" not in normalized:
            return self.reason_with_policy(
                normalized,
                self.policy,
                trace=trace,
                pre_attempt=pre_attempt,
            )
        adapter_id = normalized.get("provider") or self.default_provider
        if not isinstance(adapter_id, str):
            raise ModelGatewayError(
                "provider_unavailable",
                "no model route is configured",
                retryable=False,
                next_route="configure-model-runtime-policy",
            )
        if self.policy is not None:
            matching = [
                route
                for route in self.policy["routes"]
                if route["route_id"] == adapter_id
                or route["adapter_id"] == adapter_id
            ]
            if len(matching) == 1:
                targeted = deepcopy(self.policy)
                targeted["routes"] = matching
                return self.reason_with_policy(
                    normalized,
                    targeted,
                    trace=trace,
                    pre_attempt=pre_attempt,
                )
        adapter = self.adapters.get(adapter_id)
        if adapter is None:
            raise ModelGatewayError(
                "provider_unavailable",
                "requested model route is not registered",
                retryable=True,
                next_route="switch-model-provider",
            )
        attempt_request = self._request_for_attempt(normalized)
        activity_id = f"{normalized['request_id']}:{adapter_id}:1"
        started = self._timing_start(activity_id)
        try:
            candidate = self._invoke(
                attempt_request,
                adapter_id,
                adapter,
                trace=trace,
                attempt=1,
                route_id=adapter_id,
                pre_attempt=pre_attempt,
            )
        except Exception:
            self._record_timed_activity(
                activity_id,
                kind="model",
                started=started,
                useful=False,
            )
            raise
        self._record_timed_activity(
            activity_id,
            kind="model",
            started=started,
        )
        return candidate

    def reason_with_capability_variants(
        self,
        requests: Sequence[Mapping[str, object]],
        *,
        trace: object = None,
        pre_attempt: Optional[
            Callable[[Mapping[str, object], Mapping[str, object]], None]
        ] = None,
    ) -> dict[str, Any]:
        """Select the strongest request schema each route actually supports.

        Variants are ordered from strongest to most portable.  The gateway,
        rather than the research caller, owns the route-specific adaptation so
        a provider outage cannot disable a valid lower-profile failover.
        """

        if not isinstance(requests, Sequence) or isinstance(
            requests, (str, bytes)
        ) or not requests:
            raise ModelGatewayError(
                "invalid_request",
                "model capability variants are absent",
                retryable=False,
                next_route="repair-model-request",
            )
        variants = [_require_request(request) for request in requests]
        request_ids = {variant["request_id"] for variant in variants}
        if len(request_ids) != 1 or any("provider" in row for row in variants):
            raise ModelGatewayError(
                "invalid_request",
                "model capability variants must share one unbound request identity",
                retryable=False,
                next_route="repair-model-request",
            )

        if self.policy is not None:
            routes = sorted(
                (
                    deepcopy(dict(route))
                    for route in self.policy["routes"]
                    if route["adapter_id"] in self.adapters
                ),
                key=lambda row: (row["priority"], row["route_id"]),
            )
        elif isinstance(self.default_provider, str):
            routes = [
                {
                    "route_id": self.default_provider,
                    "adapter_id": self.default_provider,
                    "priority": 0,
                }
            ]
        else:
            routes = []

        # A completed candidate is the durable selection checkpoint.  Resolve
        # every exact logical variant before touching a provider so process
        # restart cannot replay an earlier failed route on the way to the
        # already completed portable variant.
        if trace is not None:
            for selected in variants:
                prior = trace.lookup_model_candidate(  # type: ignore[attr-defined]
                    model_request_sha256(selected)
                )
                if prior is not None:
                    return deepcopy(dict(prior))

        last_error: ModelGatewayError | None = None
        attempted_routes: set[str] = set()
        for selected in variants:
            for route in routes:
                route_id = str(route["route_id"])
                if route_id in attempted_routes:
                    continue
                adapter = self.adapters[route["adapter_id"]]
                capabilities = adapter.capabilities()  # type: ignore[attr-defined]
                if not all(
                    capabilities.get(capability) is True
                    for capability in selected.get("required_capabilities", [])
                ):
                    continue
                attempted_routes.add(route_id)
                try:
                    if self.policy is None:
                        return self.reason(
                            selected,
                            trace=trace,
                            pre_attempt=pre_attempt,
                        )
                    targeted_policy = deepcopy(self.policy)
                    targeted_policy["routes"] = [deepcopy(route)]
                    return self.reason_with_policy(
                        selected,
                        targeted_policy,
                        trace=trace,
                        pre_attempt=pre_attempt,
                    )
                except ModelGatewayError as error:
                    last_error = error
                    continue
        if last_error is not None:
            raise last_error
        raise ModelGatewayError(
            "provider_unavailable",
            "no configured model route satisfies any capability variant",
            retryable=False,
            next_route="configure-eligible-model-route",
        )

    def qualify_capacity(
        self,
        request: Mapping[str, object],
        *,
        requested_task_count: int,
        estimated_calls_per_task: int,
        trace: object,
    ) -> dict[str, Any]:
        """Probe one catalog-selected route once, without retry or project writes."""

        from .provider_capacity import build_provider_capacity_qualification

        normalized = _require_request(request)
        expected_context = {
            "synthetic": True,
            "scientific_result_allowed": False,
            "external_write_allowed": False,
        }
        if (
            normalized.get("runtime_purpose")
            != "provider_capacity_qualification"
            or normalized.get("task") != CAPACITY_PROBE_TASK
            or normalized.get("context") != expected_context
            or normalized.get("tools") != []
            or set(normalized.get("required_capabilities") or [])
            != {"structured_output", "generic_source_candidate_v1"}
            or normalized.get("output_schema") != CAPACITY_PROBE_SCHEMA
            or "provider" in normalized
            or "model" in normalized
        ):
            raise ModelGatewayError(
                "invalid_capacity_probe",
                "provider capacity request is not the case-neutral frozen probe",
                retryable=False,
                next_route="repair-provider-capacity-probe",
            )
        if self.policy is None:
            raise ModelGatewayError(
                "provider_unavailable",
                "provider capacity qualification requires a runtime policy",
                retryable=False,
                next_route="configure-model-runtime-policy",
            )

        routes = sorted(
            (
                deepcopy(dict(route))
                for route in self.policy["routes"]
                if route["adapter_id"] in self.adapters
            ),
            key=lambda row: (row["priority"], row["route_id"]),
        )
        if not routes:
            raise ModelGatewayError(
                "provider_unavailable",
                "no model route is registered for capacity qualification",
                retryable=False,
                next_route="configure-eligible-model-route",
            )

        schema_profile = "generic_source_candidate_v1"
        eligible_routes = [
            route
            for route in routes
            if self.adapters[route["adapter_id"]]
            .capabilities()
            .get(schema_profile)
            is True
        ]
        selected = eligible_routes[0] if eligible_routes else routes[0]
        adapter_id = selected["adapter_id"]
        adapter = self.adapters[adapter_id]
        capabilities = adapter.capabilities()  # type: ignore[attr-defined]
        safe_capabilities = {
            key: value
            for key, value in capabilities.items()
            if isinstance(key, str) and isinstance(value, bool)
        }
        provider_id = capabilities.get("provider")
        if not isinstance(provider_id, str) or not provider_id:
            provider_id = getattr(adapter, "provider_id", adapter_id)
        sdk_version = capabilities.get("sdk_version")
        if not isinstance(sdk_version, str) or not sdk_version:
            sdk_version = "unbound"
        region_state = capabilities.get("region_admission_state", "supported")
        if region_state not in {"supported", "unsupported", "unknown"}:
            region_state = "unknown"
        if not eligible_routes or region_state == "unsupported":
            return build_provider_capacity_qualification(
                provider_id=str(provider_id),
                provider_catalog_sha256=str(
                    self.policy.get("provider_catalog_sha256", "")
                ),
                source_lock_sha256=str(
                    self.policy.get("source_lock_sha256", "")
                ),
                sdk_version=sdk_version,
                schema_profile=schema_profile,
                adapter_capabilities=safe_capabilities,
                region_admission_state=str(region_state),
                quota_dimension=None,
                observed_limit=None,
                observed_remaining=None,
                reset_after_seconds=None,
                estimated_calls_per_task=estimated_calls_per_task,
                requested_task_count=requested_task_count,
                probe_receipt_ref=None,
                observed_at=self.clock(),
            )

        candidate = None
        failure = None
        try:
            candidate = self._invoke(
                self._request_for_attempt(normalized),
                adapter_id,
                adapter,
                trace=trace,
                attempt=1,
                route_id=selected["route_id"],
            )
        except ModelGatewayError as error:
            failure = error
            if trace is not None:
                request_sha256 = model_request_sha256(normalized)
                trace.append(  # type: ignore[attr-defined]
                    "research.run.model.failed",
                    {
                        "request_id": normalized["request_id"],
                        "request_sha256": request_sha256,
                        "adapter_id": adapter_id,
                        "route_id": selected["route_id"],
                        "attempt": 1,
                        "failure_code": error.code,
                        "retryable": False,
                        "retry_after_seconds": error.retry_after_seconds,
                        "retry_decision": "capacity_probe_stopped_without_retry",
                    },
                    idempotency_key=(
                        f"model:{request_sha256}:{selected['route_id']}:"
                        "1:capacity-failed"
                    ),
                    occurred_at=self.clock(),
                )

        observation = (
            candidate.get("rate_limit_observation", {})
            if isinstance(candidate, Mapping)
            else {}
        )
        if not isinstance(observation, Mapping):
            observation = {}
        limit = self._capacity_header_int(
            observation.get("x-ratelimit-limit-requests")
        )
        remaining = self._capacity_header_int(
            observation.get("x-ratelimit-remaining-requests")
        )
        reset_after = self._capacity_reset_seconds(
            observation.get("x-ratelimit-reset-requests")
        )
        if reset_after is None:
            reset_after = self._capacity_reset_seconds(
                observation.get("retry-after")
            )
        if failure is not None:
            if failure.code == "rate_limited":
                remaining = 0
                reset_after = (
                    failure.retry_after_seconds
                    if failure.retry_after_seconds is not None
                    else reset_after
                )
            elif failure.code == "provider_precondition_failed":
                region_state = "unsupported"
        probe_is_valid = (
            candidate is not None
            and candidate.get("status") == "candidate_only"
            and candidate.get("tool_requests") == []
            and isinstance(candidate.get("output"), Mapping)
            and candidate["output"].get("status") == "candidate_only"
            and candidate["output"].get("source_requests") == []
        )
        probe_ref = (
            {
                "path": (
                    "run-traces/provider-capacity/"
                    f"{canonical_sha256(candidate)}.json"
                ),
                "sha256": canonical_sha256(candidate),
            }
            if probe_is_valid
            else None
        )
        return build_provider_capacity_qualification(
            provider_id=str(provider_id),
            provider_catalog_sha256=str(
                self.policy.get("provider_catalog_sha256", "")
            ),
            source_lock_sha256=str(
                self.policy.get("source_lock_sha256", "")
            ),
            sdk_version=sdk_version,
            schema_profile=schema_profile,
            adapter_capabilities=safe_capabilities,
            region_admission_state=str(region_state),
            quota_dimension=(
                "requests"
                if limit is not None or remaining is not None
                else None
            ),
            observed_limit=limit,
            observed_remaining=remaining,
            reset_after_seconds=reset_after,
            estimated_calls_per_task=estimated_calls_per_task,
            requested_task_count=requested_task_count,
            probe_receipt_ref=probe_ref,
            observed_at=self.clock(),
        )

    @staticmethod
    def _capacity_header_int(value: object) -> Optional[int]:
        if not isinstance(value, str) or not value.isdigit():
            return None
        return int(value)

    @staticmethod
    def _capacity_reset_seconds(value: object) -> Optional[float]:
        if not isinstance(value, str) or not value.strip():
            return None
        text = value.strip().lower()
        try:
            numeric = float(text)
        except ValueError:
            numeric = None
        if numeric is not None and math.isfinite(numeric) and numeric >= 0:
            return numeric
        matches = re.findall(r"([0-9]+(?:\.[0-9]+)?)(ms|[hms])", text)
        if not matches or "".join(number + unit for number, unit in matches) != text:
            return None
        multipliers = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
        return sum(float(number) * multipliers[unit] for number, unit in matches)

    def reason_with_policy(
        self,
        request: Mapping[str, object],
        policy: Mapping[str, object],
        *,
        trace: object,
        pre_attempt: Optional[
            Callable[[Mapping[str, object], Mapping[str, object]], None]
        ] = None,
    ) -> dict[str, Any]:
        normalized = _require_request(request)
        self._validate_policy(policy)
        if trace is not None:
            self._restore_trace_runtime_state(trace)
        routes = self._eligible_routes(normalized, policy)
        if not routes:
            raise ModelGatewayError(
                "provider_unavailable",
                "no configured model route satisfies the required capabilities",
                retryable=False,
                next_route="configure-eligible-model-route",
            )
        retry = policy["retry"]
        circuit = policy["circuit_breaker"]
        max_attempts = int(retry["max_attempts_per_route"])
        base_delay = float(retry["base_delay_ms"]) / 1000.0
        rate_limit_default_delay = (
            float(retry.get("rate_limit_default_delay_ms", 0)) / 1000.0
        )
        retryable_codes = set(retry["retryable_codes"])
        threshold = int(circuit["failure_threshold"])
        last_error: Optional[ModelGatewayError] = None
        request_sha256 = model_request_sha256(normalized)
        if trace is not None:
            prior = trace.lookup_model_candidate(request_sha256)  # type: ignore[attr-defined]
            if prior is not None:
                return deepcopy(dict(prior))
        for route_index, route in enumerate(routes):
            adapter_id = route["adapter_id"]
            route_id = route["route_id"]
            if self._circuit_is_blocked(
                adapter_id,
                policy,
                trace=trace,
            ):
                continue
            adapter = self.adapters[adapter_id]
            for attempt in range(1, max_attempts + 1):
                self._circuit_runtime_observed.add(adapter_id)
                attempt_request = self._request_for_attempt(normalized)
                activity_id = (
                    f"{normalized['request_id']}:{route_id}:{attempt}"
                )
                started = self._timing_start(activity_id)
                try:
                    candidate = self._invoke(
                        attempt_request,
                        adapter_id,
                        adapter,
                        trace=trace,
                        attempt=attempt,
                        route_id=route_id,
                        pre_attempt=pre_attempt,
                    )
                except ModelGatewayError as error:
                    last_error = error
                    base_retry = (
                        error.retryable
                        and error.code in retryable_codes
                        and attempt < max_attempts
                    )
                    required_wait = max(
                        base_delay * (2 ** (attempt - 1)),
                        (
                            error.retry_after_seconds
                            if error.retry_after_seconds is not None
                            else (
                                rate_limit_default_delay
                                if error.code == "rate_limited"
                                else 0.0
                            )
                        ),
                    )
                    remaining = self._remaining_seconds()
                    if remaining is not None and remaining <= 0:
                        self._record_timed_activity(
                            activity_id,
                            kind="model",
                            started=started,
                            useful=False,
                        )
                        raise error
                    insufficient_budget = (
                        base_retry
                        and remaining is not None
                        and required_wait > remaining
                    )
                    can_retry = base_retry and not insufficient_budget
                    will_failover = (
                        not can_retry and route_index + 1 < len(routes)
                    )
                    self._record_timed_activity(
                        activity_id,
                        kind="retry" if can_retry or will_failover else "model",
                        started=started,
                        useful=False,
                    )
                    if can_retry:
                        retry_decision = "retry_same_route"
                    elif insufficient_budget:
                        retry_decision = "skip_retry_insufficient_budget"
                    else:
                        retry_decision = "failover_non_retryable"
                    if trace is not None:
                        failure_payload = {
                            "request_id": normalized["request_id"],
                            "request_sha256": request_sha256,
                            "adapter_id": adapter_id,
                            "route_id": route_id,
                            "attempt": attempt,
                            "failure_code": error.code,
                            "retryable": error.retryable,
                            "retry_after_seconds": error.retry_after_seconds,
                            "required_wait_seconds": required_wait,
                            "retry_decision": retry_decision,
                        }
                        if remaining is not None:
                            failure_payload["remaining_budget_seconds"] = remaining
                        trace.append(  # type: ignore[attr-defined]
                            "research.run.model.failed",
                            failure_payload,
                            idempotency_key=(
                                f"model:{request_sha256}:{route_id}:{attempt}:failed"
                            ),
                            occurred_at=self.clock(),
                        )
                    if can_retry:
                        if trace is not None:
                            trace.append(  # type: ignore[attr-defined]
                                "research.run.model.retry_scheduled",
                                {
                                    "request_id": normalized["request_id"],
                                    "request_sha256": request_sha256,
                                    "adapter_id": adapter_id,
                                    "route_id": route_id,
                                    "attempt": attempt,
                                    "delay_ms": int(required_wait * 1000),
                                    "failure_code": error.code,
                                    "retry_after_seconds": (
                                        error.retry_after_seconds
                                    ),
                                    "retry_decision": retry_decision,
                                },
                                idempotency_key=(
                                    f"model:{request_sha256}:{route_id}:{attempt}:retry"
                                ),
                                occurred_at=self.clock(),
                            )
                        wait_activity_id = (
                            f"{normalized['request_id']}:{route_id}:"
                            f"{attempt}:wait"
                        )
                        wait_started = self._timing_start(wait_activity_id)
                        self.sleep(required_wait)
                        self._record_timed_activity(
                            wait_activity_id,
                            kind="wait",
                            started=wait_started,
                        )
                        continue
                    self._circuit_failures[adapter_id] = (
                        self._circuit_failures.get(adapter_id, 0) + 1
                    )
                    if self._circuit_failures[adapter_id] >= threshold:
                        self._circuit_open.add(adapter_id)
                        opened_at = self.clock()
                        self._circuit_opened_monotonic[adapter_id] = (
                            self.monotonic()
                        )
                        self._circuit_opened_at[adapter_id] = opened_at
                        if trace is not None:
                            trace.append(  # type: ignore[attr-defined]
                                "research.run.model.circuit_opened",
                                {
                                    "request_id": normalized["request_id"],
                                    "request_sha256": request_sha256,
                                    "adapter_id": adapter_id,
                                    "route_id": route_id,
                                    "failure_count": self._circuit_failures[adapter_id],
                                    "opened_at": opened_at,
                                },
                                idempotency_key=(
                                    f"model:{request_sha256}:{route_id}:circuit-open"
                                ),
                                occurred_at=opened_at,
                            )
                    if trace is not None and route_index + 1 < len(routes):
                        trace.append(  # type: ignore[attr-defined]
                            "research.run.model.failover_scheduled",
                            {
                                "request_id": normalized["request_id"],
                                "request_sha256": request_sha256,
                                "from_adapter_id": adapter_id,
                                "from_route_id": route_id,
                                "to_route_id": routes[route_index + 1][
                                    "route_id"
                                ],
                                "failure_code": error.code,
                                "retry_after_seconds": (
                                    error.retry_after_seconds
                                ),
                                "required_wait_seconds": required_wait,
                                "reason": (
                                    "retry_wait_exceeds_remaining_budget"
                                    if insufficient_budget
                                    else "failure_not_retryable_on_same_route"
                                ),
                            },
                            idempotency_key=(
                                f"model:{request_sha256}:{route_id}:{attempt}:"
                                "failover"
                            ),
                            occurred_at=self.clock(),
                        )
                    break
                else:
                    self._record_timed_activity(
                        activity_id,
                        kind="model",
                        started=started,
                    )
                    self._circuit_failures[adapter_id] = 0
                    self._close_circuit(adapter_id)
                    return candidate
        if last_error is not None:
            raise last_error
        raise ModelGatewayError(
            "circuit_open",
            "all eligible model routes have open circuits",
            retryable=True,
            next_route="wait-or-reconfigure-model-routes",
        )

    def stream(
        self, request: Mapping[str, object], *, trace: object = None
    ) -> Iterable[dict[str, Any]]:
        normalized = _require_request(request)
        request_sha256 = model_request_sha256(normalized)
        if trace is not None:
            self._restore_trace_runtime_state(trace)
            prior = trace.lookup_model_candidate(request_sha256)  # type: ignore[attr-defined]
            if prior is not None:
                yield {"type": "candidate_final", "candidate": prior, "replayed": True}
                return
        if isinstance(normalized.get("provider"), str):
            adapter_id = normalized["provider"]
            matching = (
                [
                    route
                    for route in self.policy["routes"]
                    if route["route_id"] == adapter_id
                    or route["adapter_id"] == adapter_id
                ]
                if self.policy is not None
                else []
            )
            if len(matching) == 1:
                routes = deepcopy(matching)
                retry = self.policy["retry"]
                threshold = int(
                    self.policy["circuit_breaker"]["failure_threshold"]
                )
            elif adapter_id not in self.adapters:
                raise ModelGatewayError(
                    "provider_unavailable",
                    "requested streaming model route is not registered",
                    retryable=True,
                    next_route="switch-model-provider",
                )
            else:
                routes = [
                    {
                        "route_id": adapter_id,
                        "adapter_id": adapter_id,
                        "priority": 0,
                    }
                ]
                retry = {
                    "max_attempts_per_route": 1,
                    "base_delay_ms": 0,
                    "retryable_codes": [],
                }
                threshold = 1
        elif self.policy is not None:
            routes = self._eligible_routes(normalized, self.policy)
            retry = self.policy["retry"]
            threshold = int(self.policy["circuit_breaker"]["failure_threshold"])
        elif isinstance(self.default_provider, str):
            routes = [
                {
                    "route_id": self.default_provider,
                    "adapter_id": self.default_provider,
                    "priority": 0,
                }
            ]
            retry = {
                "max_attempts_per_route": 1,
                "base_delay_ms": 0,
                "retryable_codes": [],
            }
            threshold = 1
        else:
            routes = []
            retry = {}
            threshold = 1
        if not routes:
            raise ModelGatewayError(
                "provider_unavailable",
                "no streaming model route is available",
                retryable=False,
                next_route="configure-eligible-model-route",
            )
        max_attempts = int(retry["max_attempts_per_route"])
        retryable_codes = set(retry["retryable_codes"])
        base_delay = float(retry["base_delay_ms"]) / 1000.0
        rate_limit_default_delay = (
            float(retry.get("rate_limit_default_delay_ms", 0)) / 1000.0
        )
        last_error = None
        for route_index, route in enumerate(routes):
            route_id = route["route_id"]
            adapter_id = route["adapter_id"]
            active_policy = self.policy if self.policy is not None else {
                "circuit_breaker": {"failure_threshold": threshold}
            }
            if self._circuit_is_blocked(
                adapter_id,
                active_policy,
                trace=trace,
            ):
                continue
            adapter = self.adapters[adapter_id]
            stream_method = getattr(adapter, "stream", None)
            provider_profile_ref = self._provider_profile_ref(adapter_id)
            self._circuit_runtime_observed.add(adapter_id)
            if not callable(stream_method):
                attempt_request = self._request_for_attempt(normalized)
                activity_id = (
                    f"{normalized['request_id']}:{route_id}:stream:1"
                )
                started = self._timing_start(activity_id)
                try:
                    candidate = self._invoke(
                        attempt_request,
                        adapter_id,
                        adapter,
                        trace=trace,
                        attempt=1,
                        route_id=route_id,
                    )
                    remaining = self._remaining_seconds()
                    if remaining is not None and remaining <= 0:
                        raise self._deadline_exhausted()
                except Exception:
                    self._record_timed_activity(
                        activity_id,
                        kind="model",
                        started=started,
                        useful=False,
                    )
                    raise
                self._record_timed_activity(
                    activity_id,
                    kind="model",
                    started=started,
                )
                yield {"type": "candidate_final", "candidate": candidate}
                return
            for attempt in range(1, max_attempts + 1):
                emitted = False
                final = None
                attempt_request = self._request_for_attempt(normalized)
                activity_id = (
                    f"{normalized['request_id']}:{route_id}:stream:{attempt}"
                )
                started = self._timing_start(activity_id)
                network_authorization = self._authorize_model_network(adapter)
                provider_attempt_request = _provider_request_projection(
                    attempt_request
                )
                if normalized.get("contract") == TYPED_REQUEST_CONTRACT:
                    provider_attempt_request["provider_profile_ref"] = deepcopy(
                        provider_profile_ref
                    )
                try:
                    for event in stream_method(provider_attempt_request):
                        remaining = self._remaining_seconds()
                        if remaining is not None and remaining <= 0:
                            raise self._deadline_exhausted()
                        item = deepcopy(dict(event))
                        item["route_id"] = route_id
                        item["attempt"] = attempt
                        emitted = True
                        yield item
                        if event.get("type") == "model_stream_completed":
                            final = event
                    if not isinstance(final, Mapping):
                        raise ModelGatewayError(
                            "invalid_provider_output",
                            "model stream ended without a completed candidate",
                            retryable=True,
                            next_route="retry-or-switch-model-provider",
                        )
                    remaining = self._remaining_seconds()
                    if remaining is not None and remaining <= 0:
                        raise self._deadline_exhausted()
                    candidate = self._candidate(
                        normalized,
                        adapter_id,
                        final["output"],
                        final["metadata"],
                        provider_profile_ref=provider_profile_ref,
                    )
                    self._attach_network_authorization(
                        candidate, network_authorization
                    )
                    if trace is not None:
                        self._trace_completion(
                            trace,
                            normalized,
                            adapter_id,
                            candidate,
                            f"stream:{route_id}:{attempt}",
                        )
                    self._record_timed_activity(
                        activity_id,
                        kind="model",
                        started=started,
                    )
                    self._circuit_failures[adapter_id] = 0
                    self._close_circuit(adapter_id)
                    yield {"type": "candidate_final", "candidate": candidate}
                    return
                except ModelGatewayError as error:
                    last_error = error
                    base_retry = (
                        error.retryable
                        and error.code in retryable_codes
                        and attempt < max_attempts
                    )
                    required_wait = max(
                        base_delay * (2 ** (attempt - 1)),
                        (
                            error.retry_after_seconds
                            if error.retry_after_seconds is not None
                            else (
                                rate_limit_default_delay
                                if error.code == "rate_limited"
                                else 0.0
                            )
                        ),
                    )
                    remaining = self._remaining_seconds()
                    if remaining is not None and remaining <= 0:
                        self._record_timed_activity(
                            activity_id,
                            kind="model",
                            started=started,
                            useful=False,
                        )
                        if emitted:
                            yield {
                                "type": "model_stream_route_aborted",
                                "route_id": route_id,
                                "adapter_id": adapter_id,
                                "attempt": attempt,
                                "failure_code": error.code,
                            }
                        if trace is not None:
                            self._trace_stream_failure(
                                trace,
                                normalized,
                                adapter_id,
                                route_id,
                                attempt,
                                error,
                                required_wait=required_wait,
                                remaining=remaining,
                                retry_decision="deadline_exhausted",
                            )
                        raise error
                    insufficient_budget = (
                        base_retry
                        and remaining is not None
                        and required_wait > remaining
                    )
                    can_retry = base_retry and not insufficient_budget
                    if can_retry:
                        retry_decision = "retry_same_route"
                    elif insufficient_budget:
                        retry_decision = "skip_retry_insufficient_budget"
                    else:
                        retry_decision = "failover_non_retryable"
                    will_failover = (
                        not can_retry and route_index + 1 < len(routes)
                    )
                    self._record_timed_activity(
                        activity_id,
                        kind="retry" if can_retry or will_failover else "model",
                        started=started,
                        useful=False,
                    )
                    if emitted:
                        yield {
                            "type": "model_stream_route_aborted",
                            "route_id": route_id,
                            "adapter_id": adapter_id,
                            "attempt": attempt,
                            "failure_code": error.code,
                        }
                    if trace is not None:
                        self._trace_stream_failure(
                            trace,
                            normalized,
                            adapter_id,
                            route_id,
                            attempt,
                            error,
                            required_wait=required_wait,
                            remaining=remaining,
                            retry_decision=retry_decision,
                        )
                    if can_retry:
                        if trace is not None:
                            self._trace_stream_retry(
                                trace,
                                normalized,
                                adapter_id,
                                route_id,
                                attempt,
                                error,
                                required_wait,
                                retry_decision=retry_decision,
                            )
                        wait_activity_id = (
                            f"{normalized['request_id']}:{route_id}:"
                            f"stream:{attempt}:wait"
                        )
                        wait_started = self._timing_start(wait_activity_id)
                        self.sleep(required_wait)
                        self._record_timed_activity(
                            wait_activity_id,
                            kind="wait",
                            started=wait_started,
                        )
                        continue
                    self._circuit_failures[adapter_id] = (
                        self._circuit_failures.get(adapter_id, 0) + 1
                    )
                    if self._circuit_failures[adapter_id] >= threshold:
                        self._circuit_open.add(adapter_id)
                        opened_at = self.clock()
                        self._circuit_opened_monotonic[adapter_id] = (
                            self.monotonic()
                        )
                        self._circuit_opened_at[adapter_id] = opened_at
                        if trace is not None:
                            trace.append(  # type: ignore[attr-defined]
                                "research.run.model.circuit_opened",
                                {
                                    "request_id": normalized["request_id"],
                                    "request_sha256": request_sha256,
                                    "adapter_id": adapter_id,
                                    "route_id": route_id,
                                    "failure_count": self._circuit_failures[
                                        adapter_id
                                    ],
                                    "opened_at": opened_at,
                                    "stream": True,
                                },
                                idempotency_key=(
                                    f"model:{request_sha256}:{route_id}:"
                                    "stream-circuit-open"
                                ),
                                occurred_at=opened_at,
                            )
                    if trace is not None and route_index + 1 < len(routes):
                        trace.append(  # type: ignore[attr-defined]
                            "research.run.model.failover_scheduled",
                            {
                                "request_id": normalized["request_id"],
                                "request_sha256": request_sha256,
                                "from_adapter_id": adapter_id,
                                "from_route_id": route_id,
                                "to_route_id": routes[route_index + 1][
                                    "route_id"
                                ],
                                "failure_code": error.code,
                                "retry_after_seconds": (
                                    error.retry_after_seconds
                                ),
                                "required_wait_seconds": required_wait,
                                "reason": (
                                    "retry_wait_exceeds_remaining_budget"
                                    if insufficient_budget
                                    else "failure_not_retryable_on_same_route"
                                ),
                                "stream": True,
                            },
                            idempotency_key=(
                                f"model:{request_sha256}:{route_id}:{attempt}:"
                                "stream-failover"
                            ),
                            occurred_at=self.clock(),
                        )
                    break
        if last_error is not None:
            raise last_error
        raise ModelGatewayError(
            "circuit_open",
            "all streaming model routes have open circuits",
            retryable=True,
            next_route="wait-or-reconfigure-model-routes",
        )

    def capabilities(self) -> dict[str, Any]:
        return {
            adapter_id: deepcopy(adapter.capabilities())  # type: ignore[attr-defined]
            for adapter_id, adapter in self.adapters.items()
        }

    def with_execution_environment(self, environment: object) -> "ModelGateway":
        clone = copy(self)
        clone.adapters = self.adapters
        clone.default_provider = self.default_provider
        clone.policy = deepcopy(self.policy)
        clone.clock = self.clock
        clone.monotonic = self.monotonic
        clone.sleep = self.sleep
        clone.remaining_budget_seconds = self.remaining_budget_seconds
        clone._circuit_failures = self._circuit_failures
        clone._circuit_open = self._circuit_open
        clone._circuit_opened_monotonic = self._circuit_opened_monotonic
        clone._circuit_opened_at = self._circuit_opened_at
        clone._circuit_runtime_observed = self._circuit_runtime_observed
        clone._circuit_restored_event_keys = (
            self._circuit_restored_event_keys
        )
        clone.execution_environment = environment
        clone.answer_timing = self.answer_timing
        return clone

    def with_remaining_budget(
        self, remaining_budget_seconds: Callable[[], float]
    ) -> "ModelGateway":
        if not callable(remaining_budget_seconds):
            raise ValueError("remaining model budget port must be callable")
        clone = copy(self)
        clone.adapters = self.adapters
        clone.default_provider = self.default_provider
        clone.policy = deepcopy(self.policy)
        clone.clock = self.clock
        clone.monotonic = self.monotonic
        clone.sleep = self.sleep
        prior_remaining = self.remaining_budget_seconds
        clone.remaining_budget_seconds = (
            remaining_budget_seconds
            if prior_remaining is None
            else lambda: min(
                float(prior_remaining()), float(remaining_budget_seconds())
            )
        )
        clone._circuit_failures = self._circuit_failures
        clone._circuit_open = self._circuit_open
        clone._circuit_opened_monotonic = self._circuit_opened_monotonic
        clone._circuit_opened_at = self._circuit_opened_at
        clone._circuit_runtime_observed = self._circuit_runtime_observed
        clone._circuit_restored_event_keys = (
            self._circuit_restored_event_keys
        )
        clone.execution_environment = self.execution_environment
        clone.answer_timing = self.answer_timing
        return clone

    def with_answer_timing(self, answer_timing: object) -> "ModelGateway":
        if not all(
            hasattr(answer_timing, name)
            for name in (
                "start_activity",
                "record_activity_since",
                "remaining_seconds",
            )
        ):
            raise ValueError("model answer timing port is invalid")
        clone = self.with_remaining_budget(answer_timing.remaining_seconds)
        clone.answer_timing = answer_timing
        return clone

    @property
    def handles_answer_timing(self) -> bool:
        return self.answer_timing is not None

    def _request_for_attempt(
        self, request: Mapping[str, object]
    ) -> dict[str, Any]:
        normalized = deepcopy(dict(request))
        remaining = self._remaining_seconds()
        configured = normalized.get("runtime_timeout_seconds")
        if remaining is not None:
            if remaining <= 0:
                raise ModelGatewayError(
                    "model_deadline_exhausted",
                    "no answer deadline remains for another model attempt",
                    retryable=False,
                    next_route="return-bounded-conclusion-or-gap",
                )
            configured = (
                remaining
                if configured is None
                else min(float(configured), remaining)
            )
        if configured is not None:
            normalized["runtime_timeout_seconds"] = float(configured)
        return normalized

    @staticmethod
    def _deadline_exhausted() -> ModelGatewayError:
        return ModelGatewayError(
            "model_deadline_exhausted",
            "the model attempt consumed the remaining answer deadline",
            retryable=False,
            next_route="return-bounded-conclusion-or-gap",
        )

    def _timing_start(self, activity_id: str) -> float | None:
        if self.answer_timing is None:
            return None
        return self.answer_timing.start_activity(activity_id)

    def _record_timed_activity(
        self,
        activity_id: str,
        *,
        kind: str,
        started: float | None,
        useful: bool = True,
    ) -> None:
        if self.answer_timing is None or started is None:
            return
        self.answer_timing.record_activity_since(
            activity_id,
            kind=kind,
            started_monotonic=started,
            useful=useful,
        )

    def _remaining_seconds(self) -> Optional[float]:
        if self.remaining_budget_seconds is None:
            return None
        value = self.remaining_budget_seconds()
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) < 0
        ):
            raise ValueError("remaining model budget port returned an invalid value")
        return float(value)

    def _restore_trace_runtime_state(self, trace: object) -> None:
        latest: dict[str, tuple[tuple[float, int, str], str, dict]] = {}
        for event in trace.events():  # type: ignore[attr-defined]
            payload = event.get("payload")
            if not isinstance(payload, Mapping):
                continue
            adapter_id = payload.get("adapter_id")
            if not isinstance(adapter_id, str):
                continue
            event_type = event.get("event_type")
            if event_type not in {
                "research.run.model.circuit_opened",
                "research.run.model.completed",
            }:
                continue
            occurred_at = event.get("occurred_at")
            sequence = event.get("sequence")
            event_sha256 = event.get("event_sha256")
            if (
                not isinstance(occurred_at, str)
                or isinstance(sequence, bool)
                or not isinstance(sequence, int)
                or not isinstance(event_sha256, str)
            ):
                continue
            try:
                timestamp = datetime.fromisoformat(
                    occurred_at.replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                continue
            key = (timestamp, sequence, event_sha256)
            prior = latest.get(adapter_id)
            if prior is None or key > prior[0]:
                latest[adapter_id] = (
                    key,
                    str(event_type),
                    deepcopy(dict(payload)),
                )
        for adapter_id, (key, event_type, payload) in latest.items():
            if adapter_id in self._circuit_runtime_observed:
                continue
            if key <= self._circuit_restored_event_keys.get(
                adapter_id, (float("-inf"), -1, "")
            ):
                continue
            self._circuit_restored_event_keys[adapter_id] = key
            self._circuit_opened_monotonic.pop(adapter_id, None)
            if event_type == "research.run.model.completed":
                self._circuit_open.discard(adapter_id)
                self._circuit_opened_at.pop(adapter_id, None)
                self._circuit_failures[adapter_id] = 0
                continue
            self._circuit_open.add(adapter_id)
            count = payload.get("failure_count")
            self._circuit_failures[adapter_id] = (
                count if isinstance(count, int) and count >= 1 else 1
            )
            opened_at = payload.get("opened_at")
            if not isinstance(opened_at, str):
                opened_at = datetime.fromtimestamp(
                    key[0], tz=timezone.utc
                ).isoformat().replace("+00:00", "Z")
            self._circuit_opened_at[adapter_id] = opened_at

    def _close_circuit(self, adapter_id: str) -> None:
        self._circuit_open.discard(adapter_id)
        self._circuit_opened_monotonic.pop(adapter_id, None)
        self._circuit_opened_at.pop(adapter_id, None)

    def _circuit_is_blocked(
        self,
        adapter_id: str,
        policy: Mapping[str, object],
        *,
        trace: object = None,
    ) -> bool:
        if adapter_id not in self._circuit_open:
            return False
        circuit = policy.get("circuit_breaker")
        if not isinstance(circuit, Mapping):
            return True
        cooldown = circuit.get("cooldown_seconds")
        if cooldown is None:
            return True
        elapsed: float | None = None
        opened_monotonic = self._circuit_opened_monotonic.get(adapter_id)
        if opened_monotonic is not None:
            elapsed = max(0.0, self.monotonic() - opened_monotonic)
        else:
            opened_at = self._circuit_opened_at.get(adapter_id)
            if opened_at is not None:
                elapsed = self._elapsed_wall_seconds(opened_at, self.clock())
        if elapsed is None or elapsed < float(cooldown):
            return True
        opened_at = self._circuit_opened_at.get(adapter_id, "unknown")
        self._close_circuit(adapter_id)
        threshold = int(circuit["failure_threshold"])
        self._circuit_failures[adapter_id] = threshold - 1
        if trace is not None:
            trace.append(  # type: ignore[attr-defined]
                "research.run.model.circuit_probe_allowed",
                {
                    "adapter_id": adapter_id,
                    "opened_at": opened_at,
                    "cooldown_seconds": float(cooldown),
                    "elapsed_seconds": elapsed,
                },
                idempotency_key=(
                    f"model:{adapter_id}:{opened_at}:circuit-probe"
                ),
                occurred_at=self.clock(),
            )
        return False

    @staticmethod
    def _elapsed_wall_seconds(started_at: str, observed_at: str) -> float | None:
        try:
            start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        return max(0.0, (observed - start).total_seconds())

    def normalize_error(self, error: BaseException) -> dict[str, Any]:
        if isinstance(error, ModelGatewayError):
            return {
                "contract": "lca_research_kernel.model_failure/v1",
                "code": error.code,
                "retryable": error.retryable,
                "next_route": error.next_route,
                "message": str(error),
                "retry_after_seconds": error.retry_after_seconds,
            }
        return {
            "contract": "lca_research_kernel.model_failure/v1",
            "code": "unknown_model_failure",
            "retryable": False,
            "next_route": "inspect-model-harness",
            "message": type(error).__name__,
        }

    def _invoke(
        self,
        request: Mapping[str, object],
        adapter_id: str,
        adapter: object,
        *,
        trace: object,
        attempt: int,
        route_id: str,
        pre_attempt: Optional[
            Callable[[Mapping[str, object], Mapping[str, object]], None]
        ] = None,
    ) -> dict:
        request_sha256 = model_request_sha256(request)
        provider_profile_ref = self._provider_profile_ref(adapter_id)
        request_event = None
        if trace is not None:
            adaptive_plan = request.get("adaptive_answer_plan")
            typed_context = request.get("context")
            routed_program = (
                typed_context.get("research_program")
                if request.get("contract") == TYPED_REQUEST_CONTRACT
                and isinstance(typed_context, Mapping)
                else None
            )
            routed_route = (
                routed_program.get("route")
                if isinstance(routed_program, Mapping)
                else None
            )
            request_event = trace.append(  # type: ignore[attr-defined]
                "research.run.model.requested",
                {
                    "request_id": request["request_id"],
                    "request_sha256": request_sha256,
                    "adapter_id": adapter_id,
                    "route_id": route_id,
                    "attempt": attempt,
                    "required_capabilities": deepcopy(
                        request.get("required_capabilities", [])
                    ),
                    "adaptive_plan_sha256": (
                        adaptive_plan.get("plan_sha256")
                        if isinstance(adaptive_plan, Mapping)
                        else routed_route.get("adaptive_plan_sha256")
                        if isinstance(routed_route, Mapping)
                        else None
                    ),
                    "adaptive_project_state_sha256": (
                        adaptive_plan.get("project_state_sha256")
                        if isinstance(adaptive_plan, Mapping)
                        else routed_program.get("project_state_sha256")
                        if isinstance(routed_program, Mapping)
                        else None
                    ),
                    "research_program_sha256": (
                        routed_program.get("program_sha256")
                        if isinstance(routed_program, Mapping)
                        else None
                    ),
                    "provider_profile_ref": deepcopy(provider_profile_ref),
                },
                idempotency_key=(
                    f"model:{request_sha256}:{route_id}:{attempt}:requested"
                ),
                occurred_at=self.clock(),
            )
        try:
            network_authorization = self._authorize_model_network(adapter)
        except ModelGatewayError:
            raise
        except Exception as error:
            raise ModelGatewayError(
                "provider_failure",
                f"model provider failed with {type(error).__name__}",
                retryable=True,
                next_route="retry-or-switch-model-provider",
            ) from error
        if pre_attempt is not None:
            pre_attempt(
                deepcopy(dict(request)),
                {
                    "adapter_id": adapter_id,
                    "route_id": route_id,
                    "attempt": attempt,
                },
            )
        provider_request = _provider_request_projection(request)
        if request.get("contract") == TYPED_REQUEST_CONTRACT:
            provider_request["provider_profile_ref"] = deepcopy(
                provider_profile_ref
            )
        try:
            output, metadata = adapter.reason(provider_request)  # type: ignore[attr-defined]
        except ModelGatewayError:
            raise
        except Exception as error:
            raise ModelGatewayError(
                "provider_failure",
                f"model provider failed with {type(error).__name__}",
                retryable=True,
                next_route="retry-or-switch-model-provider",
            ) from error
        remaining = self._remaining_seconds()
        if remaining is not None and remaining <= 0:
            raise ModelGatewayError(
                "model_deadline_exhausted",
                "model attempt consumed the remaining answer deadline",
                retryable=False,
                next_route="return-bounded-conclusion-or-gap",
            )
        candidate = self._candidate(
            request,
            adapter_id,
            output,
            metadata,
            provider_profile_ref=provider_profile_ref,
        )
        self._attach_network_authorization(candidate, network_authorization)
        if request_event is not None:
            candidate["run_trace_ref"] = {
                "contract": "lca_research_kernel.run_trace_event_ref/v1",
                "run_id": request_event["run_id"],
                "event_id": request_event["event_id"],
                "event_sha256": request_event["event_sha256"],
                "event_type": request_event["event_type"],
            }
        if trace is not None:
            self._trace_completion(
                trace, request, adapter_id, candidate, f"{route_id}:{attempt}"
            )
        return candidate

    def _provider_profile_ref(self, adapter_id: str) -> dict:
        capabilities = self.adapters[adapter_id].capabilities()  # type: ignore[attr-defined]
        profile_id = getattr(
            self.adapters[adapter_id],
            "runtime_profile_id",
            f"adapter-capabilities:{adapter_id}",
        )
        profile_sha256 = getattr(
            self.adapters[adapter_id],
            "runtime_profile_sha256",
            canonical_sha256(capabilities),
        )
        if (
            not isinstance(profile_id, str)
            or not profile_id.strip()
            or not isinstance(profile_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", profile_sha256) is None
        ):
            raise ModelGatewayError(
                "provider_profile_invalid",
                "model provider profile identity is invalid",
                retryable=False,
                next_route="repair-model-provider-contract",
            )
        return {
            "profile_id": profile_id,
            "profile_sha256": profile_sha256,
        }

    def validate_candidate_request_binding(
        self,
        candidate: Mapping[str, object],
        request: Mapping[str, object],
    ) -> dict:
        """Validate logical request identity and the exact executed profile."""

        normalized = validate_model_candidate_request_binding(candidate, request)
        if request.get("contract") != TYPED_REQUEST_CONTRACT:
            return normalized
        adapter_id = normalized.get("adapter_id")
        if not isinstance(adapter_id, str) or adapter_id not in self.adapters:
            raise ValueError("model candidate provider profile binding is unavailable")
        if normalized.get("provider_profile_ref") != self._provider_profile_ref(
            adapter_id
        ):
            raise ValueError("model candidate provider profile binding drifted")
        return normalized

    def _authorize_model_network(self, adapter: object) -> Optional[dict]:
        capabilities = adapter.capabilities()  # type: ignore[attr-defined]
        if capabilities.get("remote_execution") is not True:
            return None
        if self.execution_environment is None:
            raise ModelGatewayError(
                "execution_policy_missing",
                "remote model execution requires a project execution environment",
                retryable=False,
                next_route="bind-project-execution-policy",
            )
        host = getattr(adapter, "network_host", None)
        if not isinstance(host, str) or not host:
            raise ModelGatewayError(
                "network_identity_missing",
                "remote model adapter has no exact network identity",
                retryable=False,
                next_route="repair-model-execution-policy",
            )
        receipt = self.execution_environment.authorize_network(
            host, "external_read"
        )
        return {
            "host": host,
            "operation": "external_read",
            "receipt_sha256": receipt["receipt_sha256"],
        }

    def _attach_network_authorization(
        self, candidate: dict, authorization: Optional[Mapping[str, object]]
    ) -> None:
        if authorization is None:
            return
        candidate["execution_policy_sha256"] = (
            self.execution_environment.policy_sha256
        )
        candidate["network_authorization"] = deepcopy(dict(authorization))

    def _trace_stream_failure(
        self,
        trace: object,
        request: Mapping[str, object],
        adapter_id: str,
        route_id: str,
        attempt: int,
        error: ModelGatewayError,
        *,
        required_wait: float,
        remaining: Optional[float],
        retry_decision: str,
    ) -> None:
        request_sha256 = model_request_sha256(request)
        payload = {
            "request_id": request["request_id"],
            "request_sha256": request_sha256,
            "adapter_id": adapter_id,
            "route_id": route_id,
            "attempt": attempt,
            "failure_code": error.code,
            "retryable": error.retryable,
            "retry_after_seconds": error.retry_after_seconds,
            "required_wait_seconds": required_wait,
            "retry_decision": retry_decision,
            "stream": True,
        }
        if remaining is not None:
            payload["remaining_budget_seconds"] = remaining
        trace.append(  # type: ignore[attr-defined]
            "research.run.model.failed",
            payload,
            idempotency_key=(
                f"model:{request_sha256}:{route_id}:{attempt}:stream-failed"
            ),
            occurred_at=self.clock(),
        )

    def _trace_stream_retry(
        self,
        trace: object,
        request: Mapping[str, object],
        adapter_id: str,
        route_id: str,
        attempt: int,
        error: ModelGatewayError,
        delay: float,
        *,
        retry_decision: str,
    ) -> None:
        request_sha256 = model_request_sha256(request)
        trace.append(  # type: ignore[attr-defined]
            "research.run.model.retry_scheduled",
            {
                "request_id": request["request_id"],
                "request_sha256": request_sha256,
                "adapter_id": adapter_id,
                "route_id": route_id,
                "attempt": attempt,
                "delay_ms": int(delay * 1000),
                "failure_code": error.code,
                "retry_after_seconds": error.retry_after_seconds,
                "retry_decision": retry_decision,
                "stream": True,
            },
            idempotency_key=(
                f"model:{request_sha256}:{route_id}:{attempt}:stream-retry"
            ),
            occurred_at=self.clock(),
        )

    def _candidate(
        self,
        request: Mapping[str, object],
        adapter_id: str,
        output: object,
        metadata: Mapping[str, object],
        *,
        provider_profile_ref: Mapping[str, object] | None = None,
    ) -> dict:
        model = metadata.get("model")
        provider = metadata.get("provider")
        if not isinstance(model, str) or not model.strip():
            raise ModelGatewayError(
                "model_identity_missing",
                "provider response did not identify the actual model",
                retryable=False,
                next_route="repair-model-provider-contract",
            )
        if not isinstance(provider, str) or not provider.strip():
            capabilities = self.adapters[adapter_id].capabilities()  # type: ignore[attr-defined]
            provider = capabilities.get("provider")
        if not isinstance(provider, str) or not provider.strip():
            raise ModelGatewayError(
                "provider_identity_missing",
                "model adapter did not identify its provider",
                retryable=False,
                next_route="repair-model-provider-contract",
            )
        normalized_request = deepcopy(dict(request))
        request_sha256 = model_request_sha256(normalized_request)
        candidate = {
            "contract": CANDIDATE_CONTRACT,
            "candidate_id": "model-candidate-"
            + canonical_sha256(
                {
                    "request_sha256": request_sha256,
                    "adapter_id": adapter_id,
                    "provider": provider,
                    "model": model,
                    "output": output,
                    "tool_requests": metadata.get("tool_requests") or [],
                }
            )[:24],
            "request_id": normalized_request["request_id"],
            "request_sha256": request_sha256,
            "adapter_id": adapter_id,
            "provider": provider,
            "model": model,
            "provider_response_id": metadata.get("provider_response_id"),
            "status": "candidate_only",
            "output": deepcopy(output),
            "tool_requests": deepcopy(metadata.get("tool_requests") or []),
            "usage": deepcopy(
                metadata.get("usage")
                or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            ),
            "duration_ms": metadata.get("duration_ms", 0),
            "cost": metadata.get("cost"),
        }
        if normalized_request.get("contract") == TYPED_REQUEST_CONTRACT:
            context = normalized_request.get("context")
            if not isinstance(context, Mapping):
                raise ModelGatewayError(
                    "invalid_context_package",
                    "typed model context package is absent",
                    retryable=False,
                    next_route="rebuild-source-bound-context",
                )
            candidate["context_package_sha256"] = context["package_sha256"]
            candidate["research_program_sha256"] = context[
                "research_program_ref"
            ]["program_sha256"]
            candidate["provider_profile_ref"] = deepcopy(
                dict(
                    provider_profile_ref
                    if provider_profile_ref is not None
                    else self._provider_profile_ref(adapter_id)
                )
            )
        rate_limit_observation = metadata.get("rate_limit_observation")
        if isinstance(rate_limit_observation, Mapping):
            allowed_capacity_headers = {
                "x-ratelimit-limit-requests",
                "x-ratelimit-remaining-requests",
                "x-ratelimit-reset-requests",
                "retry-after",
            }
            sanitized = {
                str(key): str(value)
                for key, value in rate_limit_observation.items()
                if key in allowed_capacity_headers
                and isinstance(value, (str, int, float))
            }
            if sanitized:
                candidate["rate_limit_observation"] = sanitized
        if self.policy is not None:
            identity_fields = (
                "source_lock_sha256",
                "policy_sha256",
                "provider_catalog_sha256",
                "execution_mode",
            )
            if all(
                isinstance(self.policy.get(field), str)
                and bool(self.policy[field])
                for field in identity_fields
            ):
                candidate["runtime_identity"] = {
                    field: self.policy[field] for field in identity_fields
                }
                qualification_sha256 = self.policy.get(
                    "capacity_qualification_sha256"
                )
                if (
                    isinstance(qualification_sha256, str)
                    and qualification_sha256
                ):
                    candidate["runtime_identity"][
                        "capacity_qualification_sha256"
                    ] = qualification_sha256
        return candidate

    def _trace_completion(
        self,
        trace: object,
        request: Mapping[str, object],
        adapter_id: str,
        candidate: Mapping[str, object],
        identity: str,
    ) -> None:
        request_sha256 = model_request_sha256(request)
        trace.append(  # type: ignore[attr-defined]
            "research.run.model.completed",
            {
                "request_id": request["request_id"],
                "request_sha256": request_sha256,
                "adapter_id": adapter_id,
                "provider": candidate["provider"],
                "model": candidate["model"],
                "provider_response_id": candidate.get("provider_response_id"),
                "usage": deepcopy(candidate["usage"]),
                "duration_ms": candidate["duration_ms"],
                "cost": candidate["cost"],
                "candidate": deepcopy(dict(candidate)),
                "candidate_sha256": canonical_sha256(candidate),
            },
            idempotency_key=f"model:{request_sha256}:{identity}:completed",
            occurred_at=self.clock(),
        )

    def _eligible_routes(
        self,
        request: Mapping[str, object],
        policy: Mapping[str, object],
    ) -> list[dict]:
        required = set(request.get("required_capabilities") or [])
        eligible = []
        for route in policy["routes"]:  # type: ignore[index]
            adapter_id = route["adapter_id"]
            adapter = self.adapters.get(adapter_id)
            if adapter is None:
                continue
            capabilities = adapter.capabilities()  # type: ignore[attr-defined]
            if all(capabilities.get(name) is True for name in required):
                eligible.append(deepcopy(dict(route)))
        return sorted(eligible, key=lambda row: (row["priority"], row["route_id"]))

    def _validate_policy(self, policy: Mapping[str, object]) -> None:
        if policy.get("contract") != "lca_research_kernel.model_runtime_policy/v1":
            raise ValueError("model runtime policy contract is unsupported")
        if not isinstance(policy.get("policy_id"), str) or not policy["policy_id"]:
            raise ValueError("model runtime policy identity is absent")
        routes = policy.get("routes")
        retry = policy.get("retry")
        circuit = policy.get("circuit_breaker")
        if not isinstance(routes, list) or not routes:
            raise ValueError("model runtime policy routes are absent")
        identities = set()
        for route in routes:
            if (
                not isinstance(route, Mapping)
                or set(route) != {"route_id", "adapter_id", "priority"}
                or not isinstance(route.get("route_id"), str)
                or not isinstance(route.get("adapter_id"), str)
                or not isinstance(route.get("priority"), int)
            ):
                raise ValueError("model runtime policy route is invalid")
            if route["route_id"] in identities:
                raise ValueError("model runtime policy route is duplicated")
            identities.add(route["route_id"])
        if (
            not isinstance(retry, Mapping)
            or not isinstance(retry.get("max_attempts_per_route"), int)
            or retry["max_attempts_per_route"] < 1
            or not isinstance(retry.get("base_delay_ms"), int)
            or retry["base_delay_ms"] < 0
            or (
                "rate_limit_default_delay_ms" in retry
                and (
                    not isinstance(
                        retry.get("rate_limit_default_delay_ms"), int
                    )
                    or retry["rate_limit_default_delay_ms"] < 0
                )
            )
            or not isinstance(retry.get("retryable_codes"), list)
        ):
            raise ValueError("model runtime retry policy is invalid")
        if (
            not isinstance(circuit, Mapping)
            or not isinstance(circuit.get("failure_threshold"), int)
            or circuit["failure_threshold"] < 1
            or (
                "cooldown_seconds" in circuit
                and (
                    isinstance(circuit.get("cooldown_seconds"), bool)
                    or not isinstance(
                        circuit.get("cooldown_seconds"), (int, float)
                    )
                    or not math.isfinite(
                        float(circuit["cooldown_seconds"])
                    )
                    or float(circuit["cooldown_seconds"]) <= 0
                )
            )
        ):
            raise ValueError("model circuit-breaker policy is invalid")
        identity_fields = (
            "source_lock_sha256",
            "policy_sha256",
            "provider_catalog_sha256",
            "execution_mode",
        )
        present = [field in policy for field in identity_fields]
        if any(present) and (
            not all(present)
            or any(
                not isinstance(policy.get(field), str) or not policy[field]
                for field in identity_fields
            )
        ):
            raise ValueError("model runtime identity is incomplete")


from .model_provider_adapters import (  # noqa: E402
    AnthropicSDKAdapter as AnthropicAdapter,
    GeminiSDKAdapter as GeminiAdapter,
    OpenAISDKAdapter as OpenAIAdapter,
)
