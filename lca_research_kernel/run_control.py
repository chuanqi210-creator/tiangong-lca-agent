"""Bounded run controller with trace-replayable pause and progress state."""

from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from typing import Callable, Mapping

RUN_BUDGET_CONTRACT = "lca_research_kernel.run_budget/v1"
RUN_CONTROL_POLICY_CONTRACT = "lca_research_kernel.run_control_policy/v2"
DEFAULT_RUN_BUDGET = {
    "contract": RUN_CONTROL_POLICY_CONTRACT,
    "max_elapsed_ms": 720000,
    "max_cpu_ms": 300000,
    "max_steps": 32,
    "max_model_calls": 8,
    "max_tool_calls": 16,
    "max_events": 256,
    "max_context_tokens": 12000,
    "max_repeated_fingerprint": 2,
    "heartbeat_interval_ms": 10000,
    "heartbeat_timeout_ms": 45000,
}


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


class RunBudgetExceeded(RuntimeError):
    pass


class NoProgressError(RuntimeError):
    pass


def _validate_budget(value: Mapping[str, object]) -> dict:
    legacy_required = {
        "contract",
        "max_elapsed_ms",
        "max_steps",
        "max_model_calls",
        "max_tool_calls",
        "max_context_tokens",
        "max_repeated_fingerprint",
    }
    current_required = legacy_required | {
        "max_cpu_ms",
        "max_events",
        "heartbeat_interval_ms",
        "heartbeat_timeout_ms",
    }
    contract = value.get("contract") if isinstance(value, Mapping) else None
    required = (
        legacy_required
        if contract == RUN_BUDGET_CONTRACT
        else current_required
    )
    if (
        not isinstance(value, Mapping)
        or set(value) != required
        or contract not in {
            RUN_BUDGET_CONTRACT,
            RUN_CONTROL_POLICY_CONTRACT,
        }
    ):
        raise ValueError("run budget contract is invalid")
    budget = deepcopy(dict(value))
    for key in required - {"contract"}:
        if not isinstance(budget[key], int) or budget[key] < 1:
            raise ValueError(f"run budget {key} is invalid")
    if (
        contract == RUN_CONTROL_POLICY_CONTRACT
        and budget["heartbeat_timeout_ms"] <= budget["heartbeat_interval_ms"]
    ):
        raise ValueError("run heartbeat timeout must exceed its interval")
    return budget


def supervisor_timeout_seconds(value: Mapping[str, object]) -> float:
    """Derive the outer worker kill boundary from the same run policy."""

    budget = _validate_budget(value)
    grace_ms = (
        int(budget["heartbeat_timeout_ms"])
        if budget["contract"] == RUN_CONTROL_POLICY_CONTRACT
        else 60_000
    )
    return (int(budget["max_elapsed_ms"]) + grace_ms) / 1000.0


def supervisor_heartbeat_timeout_seconds(value: Mapping[str, object]) -> float:
    """Return the maximum silence allowed from an active worker lease."""

    budget = _validate_budget(value)
    if budget["contract"] != RUN_CONTROL_POLICY_CONTRACT:
        return 60.0
    return int(budget["heartbeat_timeout_ms"]) / 1000.0


class RunController:
    def __init__(
        self,
        budget: Mapping[str, object],
        *,
        run_id: str,
        monotonic: Callable[[], float] = time.monotonic,
        process_time: Callable[[], float] = time.process_time,
    ):
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("run control run_id is required")
        self.budget = _validate_budget(budget)
        self.run_id = run_id
        self.monotonic = monotonic
        self.process_time = process_time
        self.started = monotonic()
        self.cpu_started = process_time()
        self.elapsed_offset_ms = 0
        self.cpu_offset_ms = 0
        self.steps = 0
        self.status = "running"
        self._fingerprints: dict[str, int] = {}
        self._pause_receipt = None
        self.resume_cursor: dict | None = None
        self.latest_checkpoint: dict | None = None
        self._last_heartbeat_elapsed_ms = 0

    def remaining_seconds(self) -> float:
        elapsed_ms = self.elapsed_offset_ms + max(
            0, int((self.monotonic() - self.started) * 1000)
        )
        remaining_ms = max(
            0, int(self.budget["max_elapsed_ms"]) - elapsed_ms
        )
        return remaining_ms / 1000.0

    @classmethod
    def restore(
        cls,
        budget: Mapping[str, object],
        *,
        run_id: str,
        trace: object,
        monotonic: Callable[[], float] = time.monotonic,
        process_time: Callable[[], float] = time.process_time,
    ) -> "RunController":
        controller = cls(
            budget,
            run_id=run_id,
            monotonic=monotonic,
            process_time=process_time,
        )
        controller.steps = 0
        for event in trace.events():  # type: ignore[attr-defined]
            payload = event.get("payload")
            if not isinstance(payload, Mapping):
                continue
            observed_budget_sha = payload.get("budget_sha256")
            if (
                observed_budget_sha is not None
                and observed_budget_sha != _canonical_sha256(controller.budget)
            ):
                raise ValueError("run control budget drifted")
            if event["event_type"] == "research.run.step.started":
                controller.steps = max(
                    controller.steps, int(event["payload"]["step"])
                )
                controller.elapsed_offset_ms = max(
                    controller.elapsed_offset_ms,
                    int(event["payload"]["elapsed_ms"]),
                )
                controller.cpu_offset_ms = max(
                    controller.cpu_offset_ms,
                    int(event["payload"].get("cpu_ms", 0)),
                )
            elif event["event_type"] == "research.run.progress.observed":
                fingerprint = event["payload"]["fingerprint"]
                controller._fingerprints[fingerprint] = int(
                    event["payload"]["repetition"]
                )
            elif event["event_type"] == "research.run.paused":
                controller.status = "paused"
                controller._pause_receipt = deepcopy(event["payload"])
            elif event["event_type"] == "research.run.resumed":
                controller.status = "running"
            elif event["event_type"] == "research.run.cancelled":
                controller.status = "cancelled"
            elif event["event_type"] == "research.run.heartbeat":
                checkpoint = payload.get("checkpoint")
                if not isinstance(checkpoint, Mapping):
                    raise ValueError("run heartbeat checkpoint is invalid")
                checkpoint_copy = deepcopy(dict(checkpoint))
                actual = checkpoint_copy.pop("checkpoint_sha256", None)
                if actual != _canonical_sha256(checkpoint_copy):
                    raise ValueError("run heartbeat checkpoint drifted")
                cursor = checkpoint.get("resume_cursor")
                if not isinstance(cursor, Mapping):
                    raise ValueError("run heartbeat resume cursor is invalid")
                controller.resume_cursor = deepcopy(dict(cursor))
                controller.latest_checkpoint = deepcopy(dict(checkpoint))
                controller.elapsed_offset_ms = max(
                    controller.elapsed_offset_ms,
                    int(checkpoint.get("elapsed_ms", 0)),
                )
                controller.cpu_offset_ms = max(
                    controller.cpu_offset_ms,
                    int(checkpoint.get("cpu_ms", 0)),
                )
                controller._last_heartbeat_elapsed_ms = max(
                    controller._last_heartbeat_elapsed_ms,
                    int(checkpoint.get("elapsed_ms", 0)),
                )
        return controller

    @property
    def is_v2(self) -> bool:
        return self.budget["contract"] == RUN_CONTROL_POLICY_CONTRACT

    def _elapsed_ms(self) -> int:
        return self.elapsed_offset_ms + max(
            0, int((self.monotonic() - self.started) * 1000)
        )

    def _cpu_ms(self) -> int:
        return self.cpu_offset_ms + max(
            0, int((self.process_time() - self.cpu_started) * 1000)
        )

    @staticmethod
    def _trace_counts(trace: object) -> tuple[int, int, int, object]:
        snapshot = trace.snapshot()  # type: ignore[attr-defined]
        counts = snapshot["event_type_counts"]
        return (
            int(snapshot["event_count"]),
            int(counts.get("research.run.model.requested", 0)),
            int(counts.get("research.run.capability.requested", 0)),
            snapshot.get("last_event_sha256"),
        )

    def _check_v2_mechanical_limits(
        self,
        *,
        trace: object,
        reserve_events: int = 0,
        elapsed_ms: int | None = None,
        cpu_ms: int | None = None,
    ) -> dict:
        if not self.is_v2:
            return {}
        event_count, model_calls, tool_calls, _ = self._trace_counts(trace)
        observed_elapsed_ms = self._elapsed_ms() if elapsed_ms is None else elapsed_ms
        observed_cpu_ms = self._cpu_ms() if cpu_ms is None else cpu_ms
        checks = (
            ("event", event_count + reserve_events, self.budget["max_events"]),
            ("CPU", observed_cpu_ms, self.budget["max_cpu_ms"]),
            ("elapsed", observed_elapsed_ms, self.budget["max_elapsed_ms"]),
            ("model call", model_calls, self.budget["max_model_calls"]),
            ("tool call", tool_calls, self.budget["max_tool_calls"]),
        )
        for label, actual, maximum in checks:
            exhausted = actual >= maximum if label == "event" else actual > maximum
            if exhausted:
                raise RunBudgetExceeded(f"run {label} budget exhausted")
        return {
            "event_count": event_count,
            "model_calls": model_calls,
            "tool_calls": tool_calls,
            "elapsed_ms": observed_elapsed_ms,
            "cpu_ms": observed_cpu_ms,
        }

    def before_runtime_attempt(self, kind: str, *, trace: object) -> dict:
        """Check the last boundary before one provider or tool side effect."""

        if kind not in {"model", "tool"}:
            raise ValueError("runtime attempt kind is invalid")
        if self.status != "running":
            raise RuntimeError(f"run is {self.status}")
        receipt = self._check_v2_mechanical_limits(trace=trace)
        if not self.is_v2:
            event_count, model_calls, tool_calls, _ = self._trace_counts(trace)
            actual = model_calls if kind == "model" else tool_calls
            maximum = self.budget[
                "max_model_calls" if kind == "model" else "max_tool_calls"
            ]
            if actual > maximum:
                raise RunBudgetExceeded(f"run {kind} call budget exhausted")
            receipt = {
                "event_count": event_count,
                "model_calls": model_calls,
                "tool_calls": tool_calls,
                "elapsed_ms": self._elapsed_ms(),
            }
        return {
            "contract": "lca_research_kernel.runtime_attempt_guard/v1",
            "kind": kind,
            "budget_sha256": _canonical_sha256(self.budget),
            **receipt,
        }

    def before_step(self, state: Mapping[str, object], *, trace: object) -> dict:
        if self.status == "cancelled":
            raise RuntimeError("run is cancelled")
        if self.status == "paused":
            raise RuntimeError("run is paused")
        if not isinstance(state, Mapping):
            raise ValueError("run state is invalid")
        self._check_v2_mechanical_limits(trace=trace, reserve_events=1)
        elapsed_ms = self._elapsed_ms()
        checks = {
            "elapsed": (elapsed_ms, self.budget["max_elapsed_ms"]),
            "step": (self.steps, self.budget["max_steps"]),
            "model call": (
                state.get("model_calls"),
                self.budget["max_model_calls"],
            ),
            "tool call": (
                state.get("tool_calls"),
                self.budget["max_tool_calls"],
            ),
            "context": (
                state.get("context_tokens"),
                self.budget["max_context_tokens"],
            ),
        }
        for label, (actual, maximum) in checks.items():
            if not isinstance(actual, int) or actual < 0:
                raise ValueError(f"run {label} count is invalid")
            if label == "step" and actual >= maximum:
                raise RunBudgetExceeded(f"run {label} budget exhausted")
            if actual > maximum and label not in {"elapsed", "context", "step"}:
                raise RunBudgetExceeded(f"run {label} budget exhausted")
            if actual > maximum and label in {"elapsed", "context"}:
                raise RunBudgetExceeded(f"run {label} budget exhausted")
        self.steps += 1
        payload = {
            "contract": "lca_research_kernel.run_step_started/v1",
            "step": self.steps,
            "elapsed_ms": elapsed_ms,
            "budget_state": deepcopy(dict(state)),
            "budget_sha256": _canonical_sha256(self.budget),
        }
        if self.is_v2:
            payload["cpu_ms"] = self._cpu_ms()
        trace.append(  # type: ignore[attr-defined]
            "research.run.step.started",
            payload,
            idempotency_key=f"run-control:{self.run_id}:step:{self.steps}",
            occurred_at=f"monotonic:{elapsed_ms}",
        )
        return deepcopy(payload)

    def heartbeat(
        self,
        resume_cursor: Mapping[str, object],
        *,
        trace: object,
        force: bool = False,
    ) -> dict:
        if not self.is_v2:
            raise ValueError("run heartbeat requires a v2 control policy")
        if not isinstance(resume_cursor, Mapping) or not resume_cursor:
            raise ValueError("run resume cursor is required")
        cursor = deepcopy(dict(resume_cursor))
        elapsed_ms = self._elapsed_ms()
        cpu_ms = self._cpu_ms()
        if (
            not force
            and elapsed_ms - self._last_heartbeat_elapsed_ms
            < self.budget["heartbeat_interval_ms"]
            and self.latest_checkpoint is not None
        ):
            return deepcopy(self.latest_checkpoint)
        self._check_v2_mechanical_limits(
            trace=trace,
            reserve_events=1,
            elapsed_ms=elapsed_ms,
            cpu_ms=cpu_ms,
        )
        event_count, _, _, last_event_sha256 = self._trace_counts(trace)
        checkpoint = {
            "contract": "lca_research_kernel.run_checkpoint_ref/v2",
            "run_id": self.run_id,
            "step": self.steps,
            "elapsed_ms": elapsed_ms,
            "cpu_ms": cpu_ms,
            "event_count": event_count,
            "last_event_sha256": last_event_sha256,
            "budget_sha256": _canonical_sha256(self.budget),
            "resume_cursor": cursor,
        }
        checkpoint["checkpoint_sha256"] = _canonical_sha256(checkpoint)
        trace.append(  # type: ignore[attr-defined]
            "research.run.heartbeat",
            {
                "contract": "lca_research_kernel.run_heartbeat/v1",
                "budget_sha256": _canonical_sha256(self.budget),
                "checkpoint": checkpoint,
            },
            idempotency_key=(
                f"run-control:{self.run_id}:heartbeat:"
                f"{checkpoint['checkpoint_sha256']}"
            ),
            occurred_at=f"monotonic:{elapsed_ms}",
        )
        self.resume_cursor = cursor
        self.latest_checkpoint = deepcopy(checkpoint)
        self._last_heartbeat_elapsed_ms = elapsed_ms
        return deepcopy(checkpoint)

    def record_progress(self, fingerprint: str, *, trace: object) -> dict:
        if not isinstance(fingerprint, str) or not fingerprint:
            raise ValueError("progress fingerprint is required")
        repetition = self._fingerprints.get(fingerprint, 0) + 1
        payload = {
            "contract": "lca_research_kernel.run_progress_observed/v1",
            "fingerprint": fingerprint,
            "repetition": repetition,
            "progress": repetition == 1,
        }
        trace.append(  # type: ignore[attr-defined]
            "research.run.progress.observed",
            payload,
            idempotency_key=(
                f"run-control:{self.run_id}:progress:{fingerprint}:{repetition}"
            ),
            occurred_at=f"step:{self.steps}",
        )
        self._fingerprints[fingerprint] = repetition
        if repetition > self.budget["max_repeated_fingerprint"]:
            raise NoProgressError("run repeated the same state without progress")
        return deepcopy(payload)

    def pause(self, reason: str, *, trace: object) -> dict:
        if not isinstance(reason, str) or not reason:
            raise ValueError("pause reason is required")
        if (
            self.status == "paused"
            and isinstance(self._pause_receipt, Mapping)
            and self._pause_receipt.get("reason") == reason
        ):
            return deepcopy(dict(self._pause_receipt))
        basis = {
            "run_id": self.run_id,
            "step": self.steps,
            "reason": reason,
            "budget_sha256": _canonical_sha256(self.budget),
        }
        checkpoint = {
            "contract": "lca_research_kernel.run_checkpoint_ref/v1",
            **basis,
        }
        checkpoint["checkpoint_sha256"] = _canonical_sha256(checkpoint)
        payload = {
            "contract": "lca_research_kernel.run_paused/v1",
            "status": "paused",
            "reason": reason,
            "checkpoint_ref": checkpoint,
        }
        trace.append(  # type: ignore[attr-defined]
            "research.run.paused",
            payload,
            idempotency_key=f"run-control:{self.run_id}:pause:{checkpoint['checkpoint_sha256']}",
            occurred_at=f"step:{self.steps}",
        )
        self.status = "paused"
        self._pause_receipt = deepcopy(payload)
        return deepcopy(payload)

    def resume(self, checkpoint_ref: Mapping[str, object], *, trace: object) -> dict:
        if not isinstance(checkpoint_ref, Mapping):
            raise ValueError("run checkpoint is invalid")
        normalized = deepcopy(dict(checkpoint_ref))
        actual = normalized.pop("checkpoint_sha256", None)
        if actual != _canonical_sha256(normalized):
            raise ValueError("run checkpoint drifted")
        if (
            normalized.get("contract")
            != "lca_research_kernel.run_checkpoint_ref/v1"
            or normalized.get("run_id") != self.run_id
            or normalized.get("budget_sha256") != _canonical_sha256(self.budget)
        ):
            raise ValueError("run checkpoint identity is invalid")
        payload = {
            "contract": "lca_research_kernel.run_resumed/v1",
            "status": "running",
            "checkpoint_sha256": actual,
        }
        trace.append(  # type: ignore[attr-defined]
            "research.run.resumed",
            payload,
            idempotency_key=f"run-control:{self.run_id}:resume:{actual}",
            occurred_at=f"step:{self.steps}",
        )
        self.status = "running"
        return deepcopy(payload)

    def cancel(self, reason: str, *, trace: object) -> dict:
        if not isinstance(reason, str) or not reason:
            raise ValueError("cancel reason is required")
        payload = {
            "contract": "lca_research_kernel.run_cancelled/v1",
            "status": "cancelled",
            "reason": reason,
            "step": self.steps,
        }
        trace.append(  # type: ignore[attr-defined]
            "research.run.cancelled",
            payload,
            idempotency_key=f"run-control:{self.run_id}:cancel:{_canonical_sha256(payload)}",
            occurred_at=f"step:{self.steps}",
        )
        self.status = "cancelled"
        return deepcopy(payload)
