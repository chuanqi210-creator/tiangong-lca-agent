"""Append-only execution trace separated from accepted Project Record truth."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Mapping, Optional

import fcntl


TRACE_EVENT_CONTRACT = "lca_research_kernel.run_trace_event/v1"
SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
}
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"\bauthorization\s*:", re.IGNORECASE),
    re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{4,}", re.IGNORECASE),
    re.compile(r"\b(?:api[_ -]?key|password|secret)\s*[=:]", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
)


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _normalized_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _assert_no_sensitive(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = _normalized_key(key)
            if (
                normalized in {_normalized_key(item) for item in SENSITIVE_KEYS}
                or normalized.endswith("apikey")
                or normalized.endswith("password")
                or normalized.endswith("secret")
                or normalized.endswith("accesstoken")
                or normalized.endswith("refreshtoken")
            ):
                raise ValueError("Run Trace sensitive field is forbidden")
            _assert_no_sensitive(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_sensitive(item)
    elif isinstance(value, str):
        if any(pattern.search(value) for pattern in SENSITIVE_VALUE_PATTERNS):
            raise ValueError("Run Trace sensitive value is forbidden")


class RunTrace:
    """Hash-chained runtime evidence; never an accepted project truth plane."""

    def __init__(self, path: Path, *, run_id: str):
        self.path = Path(path)
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("Run Trace run_id is required")
        self.run_id = run_id.strip()
        self._lock = threading.RLock()
        self._cached_signature: tuple[int, int, int, int, int] | None = None
        self._cached_events: list[dict] | None = None
        self._cached_keys: dict[str, dict] = {}
        self._cached_event_type_counts: dict[str, int] = {}

    def _file_signature(self) -> tuple[int, int, int, int, int] | None:
        try:
            status = self.path.stat()
        except FileNotFoundError:
            return None
        return (
            status.st_dev,
            status.st_ino,
            status.st_size,
            status.st_mtime_ns,
            status.st_ctime_ns,
        )

    def _cache(
        self,
        events: list[dict],
        signature: tuple[int, int, int, int, int] | None,
    ) -> None:
        self._cached_events = events
        self._cached_signature = signature
        self._cached_keys = {event["idempotency_key"]: event for event in events}
        counts: dict[str, int] = {}
        for event in events:
            event_type = event["event_type"]
            counts[event_type] = counts.get(event_type, 0) + 1
        self._cached_event_type_counts = counts

    def _verified_events(self) -> list[dict]:
        for _ in range(2):
            before = self._file_signature()
            if self._cached_events is not None and before == self._cached_signature:
                if self._file_signature() == before:
                    return self._cached_events
                continue
            if before is None:
                if self._file_signature() is None:
                    self._cache([], None)
                    return self._cached_events or []
                continue
            try:
                lines = self.path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError as error:
                raise ValueError("Run Trace encoding is invalid") from error
            if self._file_signature() != before:
                continue
            rows = []
            for number, line in enumerate(lines, start=1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(
                        f"Run Trace JSON is invalid at line {number}"
                    ) from error
                if not isinstance(value, dict):
                    raise ValueError("Run Trace event must be an object")
                if value.get("run_id") != self.run_id:
                    raise ValueError("Run Trace run_id drifted")
                rows.append(value)
            self._validate(rows)
            if self._file_signature() != before:
                continue
            self._cache(rows, before)
            return self._cached_events or []
        raise ValueError("Run Trace changed during verification")

    @staticmethod
    def _append_bytes(descriptor: int, content: bytes) -> None:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
            raise ValueError("Run Trace path is unsafe")
        original_size = status.st_size
        try:
            written = os.write(descriptor, content)
            if written != len(content):
                raise OSError("Run Trace append made no progress")
            os.fsync(descriptor)
        except BaseException:
            os.ftruncate(descriptor, original_size)
            os.fsync(descriptor)
            raise

    def append(
        self,
        event_type: str,
        payload: Mapping[str, object],
        *,
        idempotency_key: str,
        occurred_at: str,
    ) -> dict:
        if (
            not isinstance(event_type, str)
            or not event_type.startswith("research.run.")
        ):
            raise ValueError("Run Trace event type is unsupported")
        if not isinstance(payload, Mapping):
            raise ValueError("Run Trace payload must be an object")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError("Run Trace idempotency key is required")
        if not isinstance(occurred_at, str) or not occurred_at.strip():
            raise ValueError("Run Trace occurred_at is required")
        normalized_payload = deepcopy(dict(payload))
        _assert_no_sensitive(normalized_payload)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self.path, flags, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                events = self._verified_events()
                prior = self._cached_keys.get(idempotency_key)
                if prior is not None:
                    if (
                        prior["event_type"] == event_type
                        and prior["payload"] == normalized_payload
                    ):
                        return deepcopy(prior)
                    raise ValueError(
                        "Run Trace idempotency key was reused with different content"
                    )
                sequence = len(events) + 1
                previous = events[-1]["event_sha256"] if events else None
                basis = {
                    "run_id": self.run_id,
                    "sequence": sequence,
                    "event_type": event_type,
                    "idempotency_key": idempotency_key,
                    "occurred_at": occurred_at,
                    "payload": normalized_payload,
                    "previous_event_sha256": previous,
                }
                event = {
                    "contract": TRACE_EVENT_CONTRACT,
                    "event_id": str(
                        uuid.uuid5(uuid.NAMESPACE_URL, canonical_sha256(basis))
                    ),
                    **basis,
                }
                event["event_sha256"] = canonical_sha256(event)
                self._validate_next(
                    event,
                    sequence=sequence,
                    previous=previous,
                    keys=self._cached_keys,
                )
                self._append_bytes(
                    descriptor, canonical_json_bytes(event) + b"\n"
                )
                events.append(event)
                signature = self._file_signature()
                if signature is None:
                    raise ValueError("Run Trace disappeared after append")
                self._cache(events, signature)
                return deepcopy(event)
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def events(self) -> list[dict]:
        with self._lock:
            return deepcopy(self._verified_events())

    def snapshot(self) -> dict:
        """Return verified O(1) counters after at most one stream replay."""

        with self._lock:
            events = self._verified_events()
            return {
                "contract": "lca_research_kernel.run_trace_snapshot/v1",
                "run_id": self.run_id,
                "event_count": len(events),
                "last_event_sha256": (
                    events[-1]["event_sha256"] if events else None
                ),
                "event_type_counts": deepcopy(self._cached_event_type_counts),
            }

    def verify(self) -> dict:
        events = self.events()
        return {
            "contract": "lca_research_kernel.run_trace_verification/v1",
            "status": "passed",
            "run_id": self.run_id,
            "event_count": len(events),
            "last_event_sha256": events[-1]["event_sha256"] if events else None,
            "trace_sha256": canonical_sha256(events),
        }

    def lookup_model_candidate(
        self, request_sha256: str
    ) -> Optional[dict]:
        for event in reversed(self.events()):
            if (
                event["event_type"] == "research.run.model.completed"
                and event["payload"].get("request_sha256") == request_sha256
            ):
                candidate = event["payload"].get("candidate")
                if not isinstance(candidate, Mapping):
                    raise ValueError("Run Trace completed model candidate is absent")
                if event["payload"].get("candidate_sha256") != canonical_sha256(
                    candidate
                ):
                    raise ValueError("Run Trace model candidate hash drifted")
                return deepcopy(dict(candidate))
        return None

    def lookup_native_capability_evidence(
        self,
        *,
        request_id: str,
        operation_receipt_sha256: str,
    ) -> Optional[dict]:
        """Resolve one native evidence envelope for gateway authentication."""

        matches = []
        for event in self.events():
            if event["event_type"] != "research.run.capability.native_evidence":
                continue
            payload = event["payload"]
            if (
                payload.get("request_id") == request_id
                and payload.get("operation_receipt_sha256")
                == operation_receipt_sha256
            ):
                matches.append(payload)
        if not matches:
            return None
        if len(matches) != 1:
            raise ValueError("Run Trace native execution evidence is ambiguous")
        envelope = matches[0]
        evidence = envelope.get("evidence")
        if (
            not isinstance(evidence, Mapping)
            or envelope.get("evidence_sha256") != canonical_sha256(evidence)
        ):
            raise ValueError("Run Trace native execution evidence drifted")
        return deepcopy(dict(envelope))

    def latest_answer_preview(self) -> Optional[dict]:
        for event in reversed(self.events()):
            if event["event_type"] not in {
                "research.run.answer.previewed",
                "research.run.answer.preview_refined",
            }:
                continue
            preview = event["payload"].get("preview")
            if (
                not isinstance(preview, Mapping)
                or preview.get("contract") != "answer_candidate_preview/v1"
                or preview.get("status") != "candidate_only"
            ):
                raise ValueError("Run Trace answer preview is invalid")
            unsigned = {
                key: value
                for key, value in preview.items()
                if key != "preview_sha256"
            }
            if preview.get("preview_sha256") != canonical_sha256(unsigned):
                raise ValueError("Run Trace answer preview hash drifted")
            return deepcopy(dict(preview))
        return None

    def reference(self) -> dict:
        verification = self.verify()
        return {
            "contract": "lca_research_kernel.run_trace_ref/v1",
            "run_id": self.run_id,
            "path": self.path.name,
            "last_event_sha256": verification["last_event_sha256"],
            "trace_sha256": verification["trace_sha256"],
            "event_count": verification["event_count"],
        }

    def _validate(self, events: list[dict]) -> None:
        previous = None
        keys = set()
        for sequence, event in enumerate(events, start=1):
            self._validate_next(
                event,
                sequence=sequence,
                previous=previous,
                keys=keys,
            )
            keys.add(event["idempotency_key"])
            previous = event["event_sha256"]

    def _validate_next(
        self,
        event: Mapping[str, object],
        *,
        sequence: int,
        previous: object,
        keys: object,
    ) -> None:
        if event.get("contract") != TRACE_EVENT_CONTRACT:
            raise ValueError("Run Trace event contract is unsupported")
        if event.get("run_id") != self.run_id:
            raise ValueError("Run Trace run_id drifted")
        if event.get("sequence") != sequence:
            raise ValueError("Run Trace event sequence drifted")
        if event.get("previous_event_sha256") != previous:
            raise ValueError("Run Trace previous hash drifted")
        key = event.get("idempotency_key")
        if not isinstance(key, str) or key in keys:
            raise ValueError("Run Trace idempotency key is invalid or duplicated")
        unsigned = {
            field: item
            for field, item in event.items()
            if field != "event_sha256"
        }
        if event.get("event_sha256") != canonical_sha256(unsigned):
            raise ValueError("Run Trace event content hash drifted")
        _assert_no_sensitive(event.get("payload"))
