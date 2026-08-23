"""Content-bound dual-layer answer timing on the existing Run Trace plane."""

from __future__ import annotations

import math
import re
import time
from copy import deepcopy
from datetime import datetime
from typing import Callable, Mapping

from .run_trace import canonical_sha256


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PREVIEW_DEADLINE_MS = 90_000
FORMAL_TARGET_MS = 480_000
HARD_BOUNDARY_MS = 720_000


class AnswerTiming:
    """Record candidate preview timing without creating accepted project truth."""

    def __init__(
        self,
        trace: object,
        *,
        run_id: str,
        input_sha256: str,
        wall_clock: Callable[[], str],
        monotonic: Callable[[], float] = time.monotonic,
    ):
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("answer timing run_id is required")
        if not isinstance(input_sha256, str) or SHA256_RE.fullmatch(input_sha256) is None:
            raise ValueError("answer timing input_sha256 is invalid")
        if not callable(wall_clock) or not callable(monotonic):
            raise ValueError("answer timing clocks must be callable")
        self.trace = trace
        self.run_id = run_id.strip()
        self.input_sha256 = input_sha256
        self.wall_clock = wall_clock
        self.monotonic = monotonic
        self._elapsed_offset_ms = self._persisted_elapsed_offset()
        self._started_monotonic = self._finite_non_negative(
            monotonic(), "monotonic clock"
        )

    def enter_request(self) -> dict:
        prior = self._first_event("research.run.answer.requested")
        if prior is not None:
            self._require_same_input(prior)
            return deepcopy(prior["payload"])
        payload = {
            "contract": "answer_request_timing/v1",
            "run_id": self.run_id,
            "input_sha256": self.input_sha256,
            "entered_at": self._wall_time(),
            "elapsed_ms": self._elapsed_ms(),
        }
        self.trace.append(
            "research.run.answer.requested",
            payload,
            idempotency_key=f"answer:{self.run_id}:{self.input_sha256}:requested",
            occurred_at=payload["entered_at"],
        )
        return deepcopy(payload)

    def preview(
        self,
        *,
        direct_judgment: str,
        key_assumptions: list[str],
        unresolved_checks: list[str],
        claim_ceiling: str,
    ) -> dict:
        self.enter_request()
        prior = self._first_event("research.run.answer.previewed")
        if prior is not None:
            self._require_same_input(prior)
            preview = prior["payload"].get("preview")
            if not isinstance(preview, Mapping):
                raise ValueError("answer preview trace payload is invalid")
            return deepcopy(dict(preview))
        preview = self._build_preview(
            direct_judgment=direct_judgment,
            key_assumptions=key_assumptions,
            unresolved_checks=unresolved_checks,
            claim_ceiling=claim_ceiling,
        )
        payload = {
            "contract": "answer_preview_timing/v1",
            "run_id": self.run_id,
            "input_sha256": self.input_sha256,
            "preview": preview,
        }
        self.trace.append(
            "research.run.answer.previewed",
            payload,
            idempotency_key=f"answer:{self.run_id}:{self.input_sha256}:previewed",
            occurred_at=preview["previewed_at"],
        )
        return deepcopy(preview)

    def refine_preview(
        self,
        *,
        direct_judgment: str,
        key_assumptions: list[str],
        unresolved_checks: list[str],
        claim_ceiling: str,
    ) -> dict:
        """Append a better candidate view while preserving the first preview."""

        self.enter_request()
        refinement_sha256 = canonical_sha256(
            {
                "direct_judgment": direct_judgment,
                "key_assumptions": key_assumptions,
                "unresolved_checks": unresolved_checks,
                "claim_ceiling": claim_ceiling,
            }
        )
        for event in self.trace.events():
            if event.get("event_type") != "research.run.answer.preview_refined":
                continue
            self._require_same_input(event)
            payload = event.get("payload")
            prior = (
                payload.get("preview")
                if isinstance(payload, Mapping)
                and payload.get("refinement_sha256") == refinement_sha256
                else None
            )
            if isinstance(prior, Mapping):
                return deepcopy(dict(prior))
        preview = self._build_preview(
            direct_judgment=direct_judgment,
            key_assumptions=key_assumptions,
            unresolved_checks=unresolved_checks,
            claim_ceiling=claim_ceiling,
        )
        payload = {
            "contract": "answer_preview_refinement_timing/v1",
            "run_id": self.run_id,
            "input_sha256": self.input_sha256,
            "refinement_sha256": refinement_sha256,
            "preview": preview,
        }
        self.trace.append(
            "research.run.answer.preview_refined",
            payload,
            idempotency_key=(
                f"answer:{self.run_id}:{self.input_sha256}:preview-refined:"
                f"{refinement_sha256}"
            ),
            occurred_at=preview["previewed_at"],
        )
        return deepcopy(preview)

    def _build_preview(
        self,
        *,
        direct_judgment: str,
        key_assumptions: list[str],
        unresolved_checks: list[str],
        claim_ceiling: str,
    ) -> dict:
        if not isinstance(direct_judgment, str) or not direct_judgment.strip():
            raise ValueError("answer preview direct judgment is required")
        assumptions = self._string_list(key_assumptions, "key assumptions")
        unresolved = self._string_list(unresolved_checks, "unresolved checks")
        if claim_ceiling != "candidate_only":
            raise ValueError("answer preview claim ceiling must remain candidate_only")
        elapsed_ms = self._elapsed_ms()
        preview = {
            "contract": "answer_candidate_preview/v1",
            "status": "candidate_only",
            "run_id": self.run_id,
            "input_sha256": self.input_sha256,
            "direct_judgment": direct_judgment.strip(),
            "key_assumptions": assumptions,
            "unresolved_checks": unresolved,
            "claim_ceiling": "candidate_only",
            "previewed_at": self._wall_time(),
            "elapsed_ms": elapsed_ms,
            "deadline_status": (
                "within_90_second_deadline"
                if elapsed_ms <= PREVIEW_DEADLINE_MS
                else "after_90_second_deadline"
            ),
            "may_write_accepted_project_record_change": False,
        }
        preview["preview_sha256"] = canonical_sha256(preview)
        return preview

    def latest_preview(self) -> dict | None:
        event = self._latest_preview_event()
        if event is None:
            return None
        self._require_same_input(event)
        preview = event["payload"].get("preview")
        if not isinstance(preview, Mapping):
            raise ValueError("answer preview trace payload is invalid")
        return deepcopy(dict(preview))

    def record_activity(
        self,
        activity_id: str,
        *,
        kind: str,
        duration_ms: int | float,
        useful: bool = True,
    ) -> dict:
        self.enter_request()
        if not isinstance(activity_id, str) or not activity_id.strip():
            raise ValueError("answer timing activity_id is required")
        if kind not in {"model", "tool", "external_read", "wait", "retry"}:
            raise ValueError("answer timing activity kind is unsupported")
        duration = self._finite_non_negative(duration_ms, "activity duration")
        if not isinstance(useful, bool):
            raise ValueError("answer timing activity usefulness is invalid")
        normalized_id = activity_id.strip()
        prior = self._activity_event(
            "research.run.answer.activity_recorded", normalized_id
        )
        if prior is not None:
            self._require_same_input(prior)
            prior_payload = prior.get("payload")
            if (
                isinstance(prior_payload, Mapping)
                and prior_payload.get("kind") == kind
                and prior_payload.get("useful") is False
                and useful is True
            ):
                normalized_id += ":recovered"
                prior = self._activity_event(
                    "research.run.answer.activity_recorded", normalized_id
                )
                if prior is None:
                    prior_payload = None
                else:
                    self._require_same_input(prior)
                    prior_payload = prior.get("payload")
            if (
                prior is not None
                and (
                    not isinstance(prior_payload, Mapping)
                    or prior_payload.get("kind") != kind
                    or prior_payload.get("useful") is not useful
                )
            ):
                raise ValueError(
                    "answer timing activity replay changed its semantics"
                )
            if prior is not None:
                return deepcopy(dict(prior_payload))
        payload = {
            "contract": "answer_timing_activity/v1",
            "run_id": self.run_id,
            "input_sha256": self.input_sha256,
            "activity_id": normalized_id,
            "kind": kind,
            "duration_ms": int(duration),
            "useful": useful,
            "elapsed_ms": self._elapsed_ms(),
        }
        event = self.trace.append(
            "research.run.answer.activity_recorded",
            payload,
            idempotency_key=(
                f"answer:{self.run_id}:{self.input_sha256}:activity:"
                f"{normalized_id}"
            ),
            occurred_at=self._wall_time(),
        )
        return deepcopy(event["payload"])

    def start_activity(self, activity_id: str | None = None) -> float:
        """Persist an optional content-bound start before returning its local clock."""

        started = self._finite_non_negative(self.monotonic(), "monotonic clock")
        if activity_id is None:
            return started
        self.enter_request()
        if not isinstance(activity_id, str) or not activity_id.strip():
            raise ValueError("answer timing activity_id is required")
        normalized_id = activity_id.strip()
        prior = self._activity_event(
            "research.run.answer.activity_started", normalized_id
        )
        if prior is not None:
            self._require_same_input(prior)
            return started
        entered = self._first_event("research.run.answer.requested")
        if entered is None:
            raise ValueError("answer timing request event is absent")
        payload = {
            "contract": "answer_timing_activity_start/v1",
            "run_id": self.run_id,
            "input_sha256": self.input_sha256,
            "activity_id": normalized_id,
            "started_at": self._wall_time(),
            "elapsed_ms": self._elapsed_ms(),
            "deadline_identity_sha256": canonical_sha256(
                {
                    "run_id": self.run_id,
                    "input_sha256": self.input_sha256,
                    "request_entered_at": entered["payload"]["entered_at"],
                    "hard_boundary_ms": HARD_BOUNDARY_MS,
                }
            ),
        }
        self.trace.append(
            "research.run.answer.activity_started",
            payload,
            idempotency_key=(
                f"answer:{self.run_id}:{self.input_sha256}:activity:"
                f"{normalized_id}:started"
            ),
            occurred_at=payload["started_at"],
        )
        return started

    def record_activity_since(
        self,
        activity_id: str,
        *,
        kind: str,
        started_monotonic: float,
        useful: bool = True,
    ) -> dict:
        started = self._finite_non_negative(
            started_monotonic, "activity start"
        )
        finished = self._finite_non_negative(
            self.monotonic(), "monotonic clock"
        )
        if finished < started:
            raise ValueError("monotonic activity clock moved backwards")
        return self.record_activity(
            activity_id,
            kind=kind,
            duration_ms=(finished - started) * 1000,
            useful=useful,
        )

    def remaining_seconds(self) -> float:
        return max(0.0, (HARD_BOUNDARY_MS - self._elapsed_ms()) / 1000.0)

    def runtime_timeout_seconds(self, configured_seconds: int | float) -> float:
        configured = self._finite_non_negative(
            configured_seconds, "configured runtime timeout"
        )
        if configured <= 0:
            raise ValueError("configured runtime timeout must be positive")
        remaining = self.remaining_seconds()
        if remaining <= 0:
            raise ValueError("hard answer boundary leaves no runtime timeout")
        return min(float(configured), remaining)

    def mark_formal_acceptance(self, result_sha256: str) -> dict:
        return self._mark_result_event(
            "research.run.answer.formal_accepted",
            "answer_formal_acceptance_timing/v1",
            "formally_accepted_at",
            result_sha256,
        )

    def mark_user_delivery(self, result_sha256: str) -> dict:
        return self._mark_result_event(
            "research.run.answer.delivered",
            "answer_user_delivery_timing/v1",
            "delivered_at",
            result_sha256,
        )

    def receipt(self, *, include_user_delivery: bool = True) -> dict:
        if not isinstance(include_user_delivery, bool):
            raise ValueError("answer timing delivery inclusion flag is invalid")
        self.enter_request()
        events = self.trace.events()
        requested = self._event_from(events, "research.run.answer.requested")
        previewed = self._event_from(events, "research.run.answer.previewed")
        latest_previewed = self._latest_preview_event(events)
        accepted = self._event_from(events, "research.run.answer.formal_accepted")
        delivered = (
            self._event_from(events, "research.run.answer.delivered")
            if include_user_delivery
            else None
        )
        end_elapsed = self._event_elapsed(
            delivered or accepted or latest_previewed or previewed or requested
        )
        durations = {
            "model": 0,
            "tool": 0,
            "external_read": 0,
            "wait": 0,
            "retry": 0,
        }
        counts = {
            "model": 0,
            "tool": 0,
            "external_read": 0,
            "useless_retry": 0,
        }
        recovered_retry_activity_ids = self._recovered_model_retry_activity_ids(
            events
        )
        for event in events:
            if event.get("event_type") != "research.run.answer.activity_recorded":
                continue
            self._require_same_input(event)
            payload = event["payload"]
            kind = payload.get("kind")
            duration = self._finite_non_negative(
                payload.get("duration_ms"), "stored activity duration"
            )
            activity_elapsed = self._finite_non_negative(
                payload.get("elapsed_ms", 0), "stored activity elapsed time"
            )
            end_elapsed = max(end_elapsed, int(activity_elapsed))
            if kind not in durations:
                raise ValueError("answer timing stored activity kind is unsupported")
            durations[kind] += int(duration)
            if kind in {"model", "tool", "external_read"}:
                counts[kind] += 1
            if (
                kind == "retry"
                and payload.get("useful") is False
                and payload.get("activity_id") not in recovered_retry_activity_ids
            ):
                counts["useless_retry"] += 1
        measured = sum(durations.values())
        if measured > end_elapsed:
            raise ValueError(
                "answer timing activities exceed end-to-end elapsed time"
            )
        durations["orchestration"] = end_elapsed - measured
        receipt = {
            "contract": "answer_timing_receipt/v1",
            "run_id": self.run_id,
            "input_sha256": self.input_sha256,
            "timestamps": {
                "request_entered": requested["payload"]["entered_at"],
                "candidate_preview": self._event_timestamp(
                    previewed, "preview", "previewed_at"
                ),
                "latest_candidate_preview": self._event_timestamp(
                    latest_previewed, "preview", "previewed_at"
                ),
                "formal_result_acceptance": self._event_timestamp(
                    accepted, None, "formally_accepted_at"
                ),
                "user_delivery": self._event_timestamp(
                    delivered, None, "delivered_at"
                ),
            },
            "elapsed_ms": {
                "request_to_preview": (
                    self._event_elapsed(previewed) if previewed is not None else None
                ),
                "request_to_latest_preview": (
                    self._event_elapsed(latest_previewed)
                    if latest_previewed is not None
                    else None
                ),
                "request_to_formal_acceptance": (
                    self._event_elapsed(accepted) if accepted is not None else None
                ),
                "request_to_user_delivery": (
                    self._event_elapsed(delivered) if delivered is not None else None
                ),
            },
            "durations_ms": durations,
            "counts": counts,
            "preview_deadline_ms": PREVIEW_DEADLINE_MS,
            "formal_target_ms": FORMAL_TARGET_MS,
            "hard_boundary_ms": HARD_BOUNDARY_MS,
            "formal_target_status": (
                "not_yet_formally_accepted"
                if accepted is None
                else (
                    "within_480_second_target"
                    if self._event_elapsed(accepted) <= FORMAL_TARGET_MS
                    else "after_480_second_target"
                )
            ),
        }
        receipt["receipt_sha256"] = canonical_sha256(receipt)
        return receipt

    @staticmethod
    def _recovered_model_retry_activity_ids(events: list[dict]) -> set[str]:
        """Identify failed model attempts that a later attempt actually recovered."""

        completed_after: dict[str, list[int]] = {}
        for event in events:
            if event.get("event_type") != "research.run.model.completed":
                continue
            payload = event.get("payload")
            sequence = event.get("sequence")
            request_id = payload.get("request_id") if isinstance(payload, Mapping) else None
            if (
                not isinstance(request_id, str)
                or isinstance(sequence, bool)
                or not isinstance(sequence, int)
            ):
                continue
            completed_after.setdefault(request_id, []).append(sequence)

        recovered: set[str] = set()
        for event in events:
            if event.get("event_type") != "research.run.model.failed":
                continue
            payload = event.get("payload")
            sequence = event.get("sequence")
            if not isinstance(payload, Mapping):
                continue
            request_id = payload.get("request_id")
            route_id = payload.get("route_id")
            attempt = payload.get("attempt")
            retry_decision = payload.get("retry_decision")
            if (
                not isinstance(request_id, str)
                or not isinstance(route_id, str)
                or isinstance(attempt, bool)
                or not isinstance(attempt, int)
                or isinstance(sequence, bool)
                or not isinstance(sequence, int)
                or retry_decision
                not in {"retry_same_route", "failover_non_retryable"}
            ):
                continue
            if any(
                completed_sequence > sequence
                for completed_sequence in completed_after.get(request_id, [])
            ):
                recovered.add(f"{request_id}:{route_id}:{attempt}")
        return recovered

    def guard_runtime_step(self, kind: str) -> dict:
        if kind not in {"model", "tool", "external_read"}:
            raise ValueError("answer timing runtime step kind is unsupported")
        elapsed_ms = self._elapsed_ms()
        if elapsed_ms < HARD_BOUNDARY_MS:
            return {
                "contract": "answer_runtime_step_guard/v1",
                "allowed": True,
                "kind": kind,
                "elapsed_ms": elapsed_ms,
                "hard_boundary_ms": HARD_BOUNDARY_MS,
            }
        return {
            "contract": "answer_runtime_step_guard/v1",
            "allowed": False,
            "kind": kind,
            "elapsed_ms": elapsed_ms,
            "hard_boundary_ms": HARD_BOUNDARY_MS,
            "boundary": "hard_answer_boundary",
            "bounded_conclusion": (
                "The run reached its hard answer boundary; no further unbounded "
                "model, tool, or external-read step is permitted."
            ),
            "unresolved_gap": (
                "The remaining check was not completed within the bounded run."
            ),
            "claim_ceiling": "bounded_missing_data_conclusion",
            "next_route": "return-bounded-conclusion-or-gap",
        }

    def _mark_result_event(
        self,
        event_type: str,
        contract: str,
        timestamp_key: str,
        result_sha256: str,
    ) -> dict:
        self.enter_request()
        if not isinstance(result_sha256, str) or SHA256_RE.fullmatch(result_sha256) is None:
            raise ValueError("answer timing result_sha256 is invalid")
        prior = self._first_event(event_type)
        if prior is not None:
            self._require_same_input(prior)
            if prior["payload"].get("result_sha256") != result_sha256:
                raise ValueError("answer timing result replay drifted")
            return deepcopy(prior["payload"])
        payload = {
            "contract": contract,
            "run_id": self.run_id,
            "input_sha256": self.input_sha256,
            "result_sha256": result_sha256,
            timestamp_key: self._wall_time(),
            "elapsed_ms": self._elapsed_ms(),
        }
        self.trace.append(
            event_type,
            payload,
            idempotency_key=(
                f"answer:{self.run_id}:{self.input_sha256}:"
                f"{event_type.rsplit('.', 1)[-1]}"
            ),
            occurred_at=payload[timestamp_key],
        )
        return deepcopy(payload)

    def _elapsed_ms(self) -> int:
        now = self._finite_non_negative(self.monotonic(), "monotonic clock")
        return self._elapsed_offset_ms + max(
            0, int((now - self._started_monotonic) * 1000)
        )

    def _persisted_elapsed_offset(self) -> int:
        events = self.trace.events()
        completed = {
            event["payload"].get("activity_id")
            for event in events
            if event.get("event_type") == "research.run.answer.activity_recorded"
            and isinstance(event.get("payload"), Mapping)
        }
        observed_wall = self._parse_wall_time(
            self._wall_time(), "current wall clock"
        )
        elapsed = [0]
        for event in events:
            if event.get("event_type") not in {
                "research.run.answer.requested",
                "research.run.answer.previewed",
                "research.run.answer.preview_refined",
                "research.run.answer.formal_accepted",
                "research.run.answer.delivered",
                "research.run.answer.activity_started",
                "research.run.answer.activity_recorded",
            }:
                continue
            self._require_same_input(event)
            event_elapsed = self._event_elapsed(event)
            elapsed.append(event_elapsed)
            if event.get("event_type") == "research.run.answer.activity_started":
                payload = event["payload"]
                if payload.get("activity_id") in completed:
                    continue
                started_wall = self._parse_wall_time(
                    payload.get("started_at"), "stored activity wall clock"
                )
                wall_delta_ms = max(
                    0, int((observed_wall - started_wall).total_seconds() * 1000)
                )
                elapsed.append(event_elapsed + wall_delta_ms)
        return max(elapsed)

    def _activity_event(
        self, event_type: str, activity_id: str
    ) -> dict | None:
        for event in self.trace.events():
            payload = event.get("payload")
            if (
                event.get("event_type") == event_type
                and isinstance(payload, Mapping)
                and payload.get("activity_id") == activity_id
            ):
                return event
        return None

    def _first_event(self, event_type: str) -> dict | None:
        for event in self.trace.events():
            if event.get("event_type") == event_type:
                return event
        return None

    def _latest_preview_event(
        self, events: list[dict] | None = None
    ) -> dict | None:
        for event in reversed(events if events is not None else self.trace.events()):
            if event.get("event_type") in {
                "research.run.answer.previewed",
                "research.run.answer.preview_refined",
            }:
                return event
        return None

    @staticmethod
    def _event_from(events: list[dict], event_type: str) -> dict | None:
        for event in events:
            if event.get("event_type") == event_type:
                return event
        return None

    @staticmethod
    def _event_elapsed(event: Mapping[str, object] | None) -> int:
        if event is None:
            raise ValueError("answer timing event is absent")
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            raise ValueError("answer timing event payload is invalid")
        if event.get("event_type") in {
            "research.run.answer.previewed",
            "research.run.answer.preview_refined",
        }:
            payload = payload.get("preview")
            if not isinstance(payload, Mapping):
                raise ValueError("answer timing preview payload is invalid")
        value = payload.get("elapsed_ms")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("answer timing stored elapsed duration is invalid")
        return value

    @staticmethod
    def _event_timestamp(
        event: Mapping[str, object] | None,
        nested_key: str | None,
        key: str,
    ) -> str | None:
        if event is None:
            return None
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            raise ValueError("answer timing event payload is invalid")
        if nested_key is not None:
            payload = payload.get(nested_key)
            if not isinstance(payload, Mapping):
                raise ValueError("answer timing nested event payload is invalid")
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError("answer timing stored timestamp is invalid")
        return value

    def _require_same_input(self, event: Mapping[str, object]) -> None:
        payload = event.get("payload")
        if (
            not isinstance(payload, Mapping)
            or payload.get("run_id") != self.run_id
            or payload.get("input_sha256") != self.input_sha256
        ):
            raise ValueError("answer timing replay identity drifted")

    def _wall_time(self) -> str:
        value = self.wall_clock()
        if not isinstance(value, str) or not value.strip():
            raise ValueError("answer timing wall clock returned an invalid value")
        return value.strip()

    @staticmethod
    def _parse_wall_time(value: object, label: str) -> datetime:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"answer timing {label} is invalid")
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"answer timing {label} is invalid") from error
        if parsed.tzinfo is None:
            raise ValueError(f"answer timing {label} is invalid")
        return parsed

    @staticmethod
    def _finite_non_negative(value: object, label: str) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) < 0
        ):
            raise ValueError(f"answer timing {label} is invalid")
        return float(value)

    @staticmethod
    def _string_list(value: object, label: str) -> list[str]:
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ValueError(f"answer preview {label} are invalid")
        return [item.strip() for item in value]
