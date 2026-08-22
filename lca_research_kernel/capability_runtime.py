"""Assurance-recorded execution of cataloged capabilities."""

from __future__ import annotations

from copy import deepcopy
from typing import Callable, Mapping

from .capability_gateway import CapabilityGatewayError, canonical_sha256


RUN_EVENT_CONTRACT = "lca_research_kernel.capability_run_event/v1"


class CapabilityRuntime:
    def __init__(
        self,
        gateway: object,
        *,
        assurance: object,
        clock: Callable[[], str],
    ):
        self.gateway = gateway
        self.assurance = assurance
        self.clock = clock

    def execute(self, request: Mapping[str, object], *, record: object) -> dict:
        if not isinstance(request, Mapping):
            raise ValueError("capability request must be an object")
        request_id = request.get("request_id")
        capability_id = request.get("capability_id")
        if not isinstance(request_id, str) or not isinstance(capability_id, str):
            raise ValueError("capability request identity is absent")
        current = record.read_current_project_view()  # type: ignore[attr-defined]
        prior = current.get("capability_runs", {}).get(request_id)
        if isinstance(prior, Mapping) and isinstance(prior.get("receipt"), Mapping):
            return deepcopy(dict(prior["receipt"]))
        descriptor = self.gateway.descriptor(capability_id)  # type: ignore[attr-defined]
        input_sha256 = canonical_sha256(request.get("input"))
        observed_at = self.clock()
        common = {
            "contract": RUN_EVENT_CONTRACT,
            "request_id": request_id,
            "capability_id": capability_id,
            "catalog_snapshot_sha256": request.get("catalog_snapshot_sha256"),
            "source_lock_id": request.get("source_lock_id"),
            "input_sha256": input_sha256,
            "observed_at": observed_at,
        }
        record.record_capability_event(  # type: ignore[attr-defined]
            "research.capability.requested",
            {**common, "stage": "requested"},
            idempotency_key=f"capability:{request_id}:requested",
            occurred_at=observed_at,
        )
        authorization = request.get("authorization")
        if not isinstance(authorization, Mapping) or authorization.get(
            "authorized"
        ) is not True:
            receipt = {
                "contract": "lca_research_kernel.capability_run_receipt/v1",
                "request_id": request_id,
                "capability_id": capability_id,
                "status": "blocked",
                "failure": {
                    "code": "human_action_required",
                    "retryable": False,
                    "next_route": descriptor.get("next_route"),
                },
                "attempts": 0,
                "executed_external_effects": 0,
            }
            self._terminal(record, common, "blocked", receipt)
            return receipt
        retry = descriptor.get("retry_policy")
        if not isinstance(retry, Mapping):
            raise ValueError("capability retry policy is absent")
        max_attempts = retry.get("max_attempts")
        retryable_codes = retry.get("retryable_codes")
        if not isinstance(max_attempts, int) or not isinstance(retryable_codes, list):
            raise ValueError("capability retry policy is invalid")
        attempts = 0
        while attempts < max_attempts:
            attempts += 1
            try:
                result = self.gateway.execute(request)  # type: ignore[attr-defined]
                receipt = {
                    **deepcopy(dict(result)),
                    "contract": "lca_research_kernel.capability_run_receipt/v1",
                    "attempts": attempts,
                    "executed_external_effects": 0,
                }
                self._terminal(record, common, "completed", receipt)
                return receipt
            except CapabilityGatewayError as error:
                if (
                    error.retryable
                    and error.code in retryable_codes
                    and attempts < max_attempts
                ):
                    continue
                failure = self.gateway.normalize_failure(error)  # type: ignore[attr-defined]
                receipt = {
                    "contract": "lca_research_kernel.capability_run_receipt/v1",
                    "request_id": request_id,
                    "capability_id": capability_id,
                    "status": "failed",
                    "failure": failure,
                    "attempts": attempts,
                    "executed_external_effects": 0,
                }
                self._terminal(record, common, "failed", receipt)
                return receipt
        raise RuntimeError("bounded capability attempts exhausted without terminal receipt")

    def _terminal(
        self,
        record: object,
        common: Mapping[str, object],
        stage: str,
        receipt: Mapping[str, object],
    ) -> None:
        request_id = str(common["request_id"])
        record.record_capability_event(  # type: ignore[attr-defined]
            f"research.capability.{stage}",
            {**deepcopy(dict(common)), "stage": stage, "receipt": deepcopy(dict(receipt))},
            idempotency_key=f"capability:{request_id}:{stage}",
            occurred_at=str(common["observed_at"]),
        )
