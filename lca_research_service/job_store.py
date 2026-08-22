"""Transactional SQLite WAL job control for D-18 workers."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .contracts import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    require_sha256,
    validate_content_ref,
)

_JOB_NAMESPACE = uuid.UUID("9e366a7e-8c1d-4ec1-82b4-85a29d821187")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _plus_seconds(value: str, seconds: int) -> str:
    if seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    return (
        (_parse_time(value) + timedelta(seconds=seconds))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _synchronized(method):
    def locked(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return locked


class JobStore:
    """Own operational job state without duplicating scientific content."""

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or _utc_now
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path,
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    @_synchronized
    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "JobStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _migrate(self) -> None:
        version = self._connection.execute("PRAGMA user_version").fetchone()[0]
        if version > 4:
            raise RuntimeError(f"unsupported job store schema version: {version}")
        if version == 0:
            self._connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE jobs (
                    job_id TEXT PRIMARY KEY,
                    request_sha256 TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    worker_id TEXT,
                    lease_expires_at TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    state_ref_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    recovery_count INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1
                    ,request_ref_json TEXT
                    ,input_ref_json TEXT
                    ,project_id TEXT NOT NULL DEFAULT 'default'
                );
                CREATE TABLE job_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    event_type TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    worker_id TEXT,
                    ref_json TEXT,
                    occurred_at TEXT NOT NULL
                );
                CREATE INDEX job_claim_order
                    ON jobs(status, created_at, job_id);
                CREATE TABLE job_ingress_idempotency(
                    idempotency_key TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    job_id TEXT REFERENCES jobs(job_id)
                );
                PRAGMA user_version=4;
                COMMIT;
                """
            )
        elif version == 1:
            self._connection.executescript(
                """
                BEGIN IMMEDIATE;
                ALTER TABLE jobs ADD COLUMN request_ref_json TEXT;
                ALTER TABLE jobs ADD COLUMN input_ref_json TEXT;
                ALTER TABLE jobs ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default';
                PRAGMA user_version=3;
                COMMIT;
                """
            )
        elif version == 2:
            self._connection.executescript(
                """
                BEGIN IMMEDIATE;
                ALTER TABLE jobs ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default';
                PRAGMA user_version=3;
                COMMIT;
                """
            )
        if version in {1, 2, 3}:
            self._connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE job_ingress_idempotency(
                    idempotency_key TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    job_id TEXT REFERENCES jobs(job_id)
                );
                PRAGMA user_version=4;
                COMMIT;
                """
            )

    @_synchronized
    def reserve_ingress(
        self,
        *,
        idempotency_key: str,
        project_id: str,
        request_sha256: str,
    ) -> str | None:
        """Bind raw request identity before remote URL side effects occur."""

        request_sha256 = require_sha256(
            request_sha256, field="ingress_request_sha256"
        )
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError("idempotency_key must be non-empty")
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("project_id must be non-empty")
        self._begin()
        try:
            existing = self._connection.execute(
                "SELECT * FROM job_ingress_idempotency WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["project_id"] != project_id
                    or existing["request_sha256"] != request_sha256
                ):
                    raise ValueError(
                        "idempotency key is already bound to a different request"
                    )
                self._connection.commit()
                return existing["job_id"]
            self._connection.execute(
                """INSERT INTO job_ingress_idempotency(
                       idempotency_key,project_id,request_sha256,job_id)
                   VALUES(?,?,?,NULL)""",
                (idempotency_key, project_id, request_sha256),
            )
            self._connection.commit()
            return None
        except Exception:
            self._connection.rollback()
            raise

    @_synchronized
    def bind_ingress_job(self, *, idempotency_key: str, job_id: str) -> None:
        self._begin()
        try:
            row = self._connection.execute(
                "SELECT job_id FROM job_ingress_idempotency WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if row is None or (row["job_id"] is not None and row["job_id"] != job_id):
                raise ValueError("job ingress reservation is unavailable")
            job = self._row(job_id)
            if job["idempotency_key"] != idempotency_key:
                raise ValueError("job ingress identity drifted")
            self._connection.execute(
                "UPDATE job_ingress_idempotency SET job_id=? WHERE idempotency_key=?",
                (job_id, idempotency_key),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def _begin(self) -> None:
        self._connection.execute("BEGIN IMMEDIATE")

    def _row(self, job_id: str) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown job: {job_id}")
        return row

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["cancel_requested"] = bool(result["cancel_requested"])
        raw_ref = result.pop("state_ref_json")
        result["state_ref"] = json.loads(raw_ref) if raw_ref else None
        for column, public_key in (
            ("request_ref_json", "request_ref"),
            ("input_ref_json", "input_ref"),
        ):
            raw = result.pop(column)
            result[public_key] = json.loads(raw) if raw else None
        return result

    def _event(
        self,
        job_id: str,
        event_type: str,
        from_status: str | None,
        to_status: str,
        *,
        worker_id: str | None,
        ref: Mapping[str, str] | None,
        occurred_at: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO job_events(
                job_id, event_type, from_status, to_status, worker_id,
                ref_json, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                event_type,
                from_status,
                to_status,
                worker_id,
                json.dumps(ref, sort_keys=True, separators=(",", ":"))
                if ref
                else None,
                occurred_at,
            ),
        )

    @_synchronized
    def create(
        self,
        request_sha256: str,
        idempotency_key: str,
        request_ref: Mapping[str, Any] | None = None,
        *,
        project_id: str = "default",
    ) -> dict[str, Any]:
        request_sha256 = require_sha256(request_sha256, field="request_sha256")
        normalized_request_ref = validate_content_ref(request_ref)
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError("idempotency_key must be non-empty")
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("project_id must be non-empty")
        job_id = str(
            uuid.uuid5(
                _JOB_NAMESPACE,
                hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest(),
            )
        )
        now = self._clock()
        self._begin()
        try:
            existing = self._connection.execute(
                "SELECT * FROM jobs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["request_sha256"] != request_sha256:
                    raise ValueError(
                        "idempotency key is already bound to a different request"
                    )
                if existing["project_id"] != project_id:
                    raise ValueError("idempotency key is bound to another project")
                existing_ref = (
                    json.loads(existing["request_ref_json"])
                    if existing["request_ref_json"]
                    else None
                )
                if existing_ref != normalized_request_ref:
                    raise ValueError(
                        "idempotency key request reference differs"
                    )
                self._connection.commit()
                return self._public(existing)
            self._connection.execute(
                """
                INSERT INTO jobs(
                    job_id, request_sha256, idempotency_key, status,
                    created_at, updated_at, request_ref_json, project_id
                ) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)
                """,
                (
                    job_id,
                    request_sha256,
                    idempotency_key,
                    now,
                    now,
                    json.dumps(
                        normalized_request_ref,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    if normalized_request_ref
                    else None,
                    project_id,
                ),
            )
            self._event(
                job_id,
                "created",
                None,
                "queued",
                worker_id=None,
                ref=None,
                occurred_at=now,
            )
            row = self._row(job_id)
            self._connection.commit()
            return self._public(row)
        except Exception:
            self._connection.rollback()
            raise

    @_synchronized
    def queue_input(
        self, job_id: str, input_ref: Mapping[str, Any]
    ) -> dict[str, Any]:
        normalized = validate_content_ref(input_ref)
        if normalized is None:
            raise ValueError("input reference is required")
        now = self._clock()
        self._begin()
        try:
            row = self._row(job_id)
            if row["status"] != "waiting_for_input":
                raise ValueError("job is not waiting for input")
            self._connection.execute(
                """
                UPDATE jobs
                SET status='queued', input_ref_json=?, updated_at=?,
                    state_ref_json=NULL, version=version+1
                WHERE job_id=?
                """,
                (
                    json.dumps(normalized, sort_keys=True, separators=(",", ":")),
                    now,
                    job_id,
                ),
            )
            self._event(
                job_id,
                "input_supplied",
                "waiting_for_input",
                "queued",
                worker_id=None,
                ref=normalized,
                occurred_at=now,
            )
            updated = self._row(job_id)
            self._connection.commit()
            return self._public(updated)
        except Exception:
            self._connection.rollback()
            raise

    @_synchronized
    def get(self, job_id: str) -> dict[str, Any]:
        return self._public(self._row(job_id))

    @_synchronized
    def latest_for_project(self, project_id: str) -> dict[str, Any] | None:
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("project_id must be non-empty")
        row = self._connection.execute(
            """
            SELECT * FROM jobs
            WHERE project_id = ?
            ORDER BY created_at DESC, job_id DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        return self._public(row) if row is not None else None

    @_synchronized
    def events(self, job_id: str, *, after: int = 0) -> list[dict[str, Any]]:
        self._row(job_id)
        rows = self._connection.execute(
            """
            SELECT * FROM job_events
            WHERE job_id = ? AND sequence > ?
            ORDER BY sequence
            """,
            (job_id, after),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            raw_ref = item.pop("ref_json")
            item["ref"] = json.loads(raw_ref) if raw_ref else None
            result.append(item)
        return result

    @_synchronized
    def claim(self, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        if not worker_id:
            raise ValueError("worker_id must be non-empty")
        now = self._clock()
        expires = _plus_seconds(now, lease_seconds)
        self._begin()
        try:
            row = self._connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'queued'
                ORDER BY created_at, job_id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                self._connection.commit()
                return None
            self._connection.execute(
                """
                UPDATE jobs
                SET status='leased', worker_id=?, lease_expires_at=?,
                    updated_at=?, version=version+1
                WHERE job_id=? AND status='queued'
                """,
                (worker_id, expires, now, row["job_id"]),
            )
            self._event(
                row["job_id"],
                "claimed",
                "queued",
                "leased",
                worker_id=worker_id,
                ref=None,
                occurred_at=now,
            )
            claimed = self._row(row["job_id"])
            self._connection.commit()
            return self._public(claimed)
        except Exception:
            self._connection.rollback()
            raise

    @_synchronized
    def heartbeat(self, job_id: str, worker_id: str) -> dict[str, Any]:
        self._begin()
        try:
            row = self._row(job_id)
            if row["worker_id"] != worker_id:
                raise PermissionError("worker is not the job owner")
            if row["status"] not in {"leased", "running"}:
                raise ValueError("heartbeat requires an owned active lease")
            now = self._clock()
            current_expiry = row["lease_expires_at"]
            if current_expiry is None:
                raise ValueError("active job has no lease expiry")
            original_seconds = max(
                1, int((_parse_time(current_expiry) - _parse_time(row["updated_at"])).total_seconds())
            )
            self._connection.execute(
                """
                UPDATE jobs SET lease_expires_at=?, updated_at=?, version=version+1
                WHERE job_id=?
                """,
                (_plus_seconds(now, original_seconds), now, job_id),
            )
            updated = self._row(job_id)
            self._connection.commit()
            return self._public(updated)
        except Exception:
            self._connection.rollback()
            raise

    @_synchronized
    def transition(
        self,
        job_id: str,
        expected: str,
        target: str,
        ref: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        normalized_ref = validate_content_ref(ref)
        if target not in ALLOWED_TRANSITIONS.get(expected, frozenset()):
            raise ValueError(f"invalid transition: {expected} -> {target}")
        now = self._clock()
        self._begin()
        try:
            row = self._row(job_id)
            if row["status"] != expected:
                raise ValueError(
                    f"transition expected {expected}, found {row['status']}"
                )
            worker_id = row["worker_id"]
            releases_lease = target in TERMINAL_STATES | {
                "queued",
                "waiting_for_input",
            }
            self._connection.execute(
                """
                UPDATE jobs
                SET status=?, state_ref_json=?, updated_at=?,
                    worker_id=?, lease_expires_at=?, version=version+1
                WHERE job_id=?
                """,
                (
                    target,
                    json.dumps(normalized_ref, sort_keys=True, separators=(",", ":"))
                    if normalized_ref
                    else None,
                    now,
                    None if releases_lease else worker_id,
                    None if releases_lease else row["lease_expires_at"],
                    job_id,
                ),
            )
            self._event(
                job_id,
                "transitioned",
                expected,
                target,
                worker_id=worker_id,
                ref=normalized_ref,
                occurred_at=now,
            )
            updated = self._row(job_id)
            self._connection.commit()
            return self._public(updated)
        except Exception:
            self._connection.rollback()
            raise

    @_synchronized
    def request_cancel(self, job_id: str) -> dict[str, Any]:
        now = self._clock()
        self._begin()
        try:
            row = self._row(job_id)
            if row["status"] in {"cancelling", "cancelled"}:
                self._connection.commit()
                return self._public(row)
            if row["status"] in TERMINAL_STATES:
                raise ValueError("cannot cancel a terminal job")
            if "cancelling" not in ALLOWED_TRANSITIONS[row["status"]]:
                raise ValueError(f"cannot cancel job from {row['status']}")
            self._connection.execute(
                """
                UPDATE jobs SET status='cancelling', cancel_requested=1,
                    updated_at=?, version=version+1
                WHERE job_id=?
                """,
                (now, job_id),
            )
            self._event(
                job_id,
                "cancel_requested",
                row["status"],
                "cancelling",
                worker_id=row["worker_id"],
                ref=None,
                occurred_at=now,
            )
            updated = self._row(job_id)
            self._connection.commit()
            return self._public(updated)
        except Exception:
            self._connection.rollback()
            raise

    @_synchronized
    def recover_expired(self, now: str) -> list[dict[str, Any]]:
        self._begin()
        recovered: list[dict[str, Any]] = []
        try:
            rows = self._connection.execute(
                """
                SELECT * FROM jobs
                WHERE status IN ('leased', 'running')
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                ORDER BY created_at, job_id
                """,
                (now,),
            ).fetchall()
            for row in rows:
                self._connection.execute(
                    """
                    UPDATE jobs
                    SET status='queued', worker_id=NULL, lease_expires_at=NULL,
                        updated_at=?, recovery_count=recovery_count+1,
                        version=version+1
                    WHERE job_id=?
                    """,
                    (now, row["job_id"]),
                )
                self._event(
                    row["job_id"],
                    "lease_recovered",
                    row["status"],
                    "queued",
                    worker_id=row["worker_id"],
                    ref=None,
                    occurred_at=now,
                )
                recovered.append(self._public(self._row(row["job_id"])))
            self._connection.commit()
            return recovered
        except Exception:
            self._connection.rollback()
            raise
