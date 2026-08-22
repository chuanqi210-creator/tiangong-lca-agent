"""Transactional upload and operational material identity storage."""

from __future__ import annotations

import hashlib
import fcntl
import json
import os
import re
import sqlite3
import stat
import tempfile
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlsplit, urlunsplit

from .input_artifacts import InputArtifactStore, _ensure_directory, _fsync_directory


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_UPLOAD_NAMESPACE = uuid.UUID("2f8a922b-b0b2-4538-a54d-861661088cf7")
_MATERIAL_NAMESPACE = uuid.UUID("c69c0df8-ea04-4fc0-ad3f-59fdd6cc5d40")
_REVISION_NAMESPACE = uuid.UUID("33e59ed5-6a10-4930-a4c8-b4ba88ffeb2d")
_REUSE_NAMESPACE = uuid.UUID("ff5b7229-cd84-4b6a-a5a0-fd67b1766523")
_REMOTE_NAMESPACE = uuid.UUID("5cff0997-9d76-46dd-86db-461ba3627da0")
CURRENT_SCHEMA_VERSION = 7
_DEFAULT_POLICY = {
    "generic_file_max_bytes": 100 * 1024 * 1024,
    "part_bytes": 8 * 1024 * 1024,
    "batch_file_count": 10,
    "batch_max_bytes": 500 * 1024 * 1024,
    "project_max_bytes": 5 * 1024 * 1024 * 1024,
    "session_seconds": 24 * 60 * 60,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _plus_seconds(value: str, seconds: int) -> str:
    return (_parse_time(value) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class MaterialStore:
    """Own transfer state and project-bound material revision pointers only."""

    def __init__(
        self,
        path: str | Path,
        *,
        input_artifacts: InputArtifactStore,
        data_root: str | Path,
        clock: Callable[[], str] | None = None,
        policy: Mapping[str, int] | None = None,
    ) -> None:
        self.path = Path(path).absolute()
        self.data_root = Path(data_root).absolute()
        if self.path.is_symlink():
            raise ValueError("material database path must not be a symlink")
        try:
            self.path.relative_to(self.data_root)
        except ValueError as error:
            raise ValueError("material database escaped data root") from error
        self.input_artifacts = input_artifacts
        self._clock = clock or _utc_now
        self.policy = {**_DEFAULT_POLICY, **dict(policy or {})}
        if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in self.policy.values()):
            raise ValueError("material storage policy values must be positive integers")
        _ensure_directory(self.path.parent, root=self.data_root)
        self.parts_root = self.data_root / "control" / "material-upload-parts"
        _ensure_directory(self.parts_root, root=self.data_root)
        self.remote_intake_locks_root = (
            self.data_root / "control" / "remote-material-intake-locks"
        )
        _ensure_directory(self.remote_intake_locks_root, root=self.data_root)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path, timeout=30, isolation_level=None, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._migrate()
        self._reconcile_terminal_parts()
        self._reconcile_completion_intents()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _migrate(self) -> None:
        version = self._connection.execute("PRAGMA user_version").fetchone()[0]
        if version > CURRENT_SCHEMA_VERSION:
            raise RuntimeError(f"unsupported material store schema version: {version}")
        if version == 0:
            self._connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE upload_sessions(
                    upload_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    declared_mime TEXT NOT NULL,
                    total_bytes INTEGER NOT NULL CHECK(total_bytes >= 0),
                    expected_sha256 TEXT,
                    part_size INTEGER NOT NULL CHECK(part_size > 0),
                    transfer_status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    completed_artifact_id TEXT,
                    completed_material_id TEXT
                );
                CREATE INDEX upload_sessions_project_status
                    ON upload_sessions(project_id, transfer_status, created_at);
                CREATE TABLE upload_parts(
                    upload_id TEXT NOT NULL REFERENCES upload_sessions(upload_id) ON DELETE CASCADE,
                    part_number INTEGER NOT NULL CHECK(part_number >= 0),
                    first_byte INTEGER NOT NULL,
                    last_byte INTEGER NOT NULL,
                    bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    path TEXT NOT NULL,
                    PRIMARY KEY(upload_id, part_number)
                );
                CREATE TABLE materials(
                    material_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    current_revision_id TEXT,
                    scope TEXT NOT NULL,
                    deleted_at TEXT,
                    operational_updated_at TEXT NOT NULL,
                    project_record_ref_json TEXT
                );
                CREATE INDEX materials_project ON materials(project_id, material_id);
                CREATE TABLE material_revisions(
                    revision_id TEXT PRIMARY KEY,
                    material_id TEXT NOT NULL REFERENCES materials(material_id),
                    input_artifact_id TEXT NOT NULL,
                    revision_number INTEGER NOT NULL,
                    processing_status TEXT NOT NULL,
                    scientific_status TEXT NOT NULL,
                    receipt_ref_json TEXT,
                    supersedes_revision_id TEXT REFERENCES material_revisions(revision_id),
                    created_at TEXT NOT NULL,
                    UNIQUE(material_id, revision_number)
                );
                CREATE TABLE material_events(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    material_id TEXT NOT NULL REFERENCES materials(material_id),
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE TABLE processing_claims(
                    revision_id TEXT PRIMARY KEY REFERENCES material_revisions(revision_id),
                    worker_id TEXT NOT NULL,
                    leased_until TEXT NOT NULL,
                    attempt INTEGER NOT NULL
                );
                CREATE TABLE processing_attempts(
                    revision_id TEXT PRIMARY KEY REFERENCES material_revisions(revision_id),
                    attempt INTEGER NOT NULL CHECK(attempt >= 0),
                    last_failure_category TEXT
                );
                CREATE TABLE upload_idempotency(
                    project_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    upload_ids_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, idempotency_key)
                );
                CREATE TABLE material_mutation_idempotency(
                    project_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, idempotency_key)
                );
                CREATE TABLE material_metadata(
                    material_id TEXT PRIMARY KEY REFERENCES materials(material_id),
                    display_name TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
                    tags_json TEXT NOT NULL,
                    directory TEXT NOT NULL,
                    source_kind TEXT NOT NULL
                );
                CREATE TABLE material_revision_metadata(
                    revision_id TEXT PRIMARY KEY REFERENCES material_revisions(revision_id),
                    display_name TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
                    source_kind TEXT NOT NULL
                );
                PRAGMA user_version=4;
                COMMIT;
                """
            )
        elif version == 1:
            columns = [
                row[1]
                for row in self._connection.execute(
                    "PRAGMA table_info(upload_sessions)"
                )
            ]
            if "scope" in columns:
                try:
                    self._connection.execute("PRAGMA foreign_keys=OFF")
                    self._connection.execute("BEGIN IMMEDIATE")
                    statements = (
                        "ALTER TABLE upload_parts RENAME TO upload_parts_legacy",
                        "ALTER TABLE upload_sessions RENAME TO upload_sessions_legacy",
                        """CREATE TABLE upload_sessions(
                            upload_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                            filename TEXT NOT NULL, declared_mime TEXT NOT NULL,
                            total_bytes INTEGER NOT NULL CHECK(total_bytes >= 0),
                            expected_sha256 TEXT, part_size INTEGER NOT NULL CHECK(part_size > 0),
                            transfer_status TEXT NOT NULL, created_at TEXT NOT NULL,
                            expires_at TEXT NOT NULL, completed_artifact_id TEXT,
                            completed_material_id TEXT
                        )""",
                        """INSERT INTO upload_sessions SELECT upload_id, project_id, filename,
                            declared_mime, total_bytes, expected_sha256, part_size,
                            transfer_status, created_at, expires_at, completed_artifact_id,
                            completed_material_id FROM upload_sessions_legacy""",
                        """CREATE TABLE upload_parts(
                            upload_id TEXT NOT NULL REFERENCES upload_sessions(upload_id) ON DELETE CASCADE,
                            part_number INTEGER NOT NULL CHECK(part_number >= 0),
                            first_byte INTEGER NOT NULL, last_byte INTEGER NOT NULL,
                            bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, path TEXT NOT NULL,
                            PRIMARY KEY(upload_id, part_number)
                        )""",
                        """INSERT INTO upload_parts SELECT upload_id, part_number, first_byte,
                            last_byte, bytes, sha256, path FROM upload_parts_legacy""",
                        "DROP TABLE upload_parts_legacy",
                        "DROP TABLE upload_sessions_legacy",
                        """CREATE INDEX IF NOT EXISTS upload_sessions_project_status
                            ON upload_sessions(project_id, transfer_status, created_at)""",
                    )
                    for statement in statements:
                        self._connection.execute(statement)
                    migrated_columns = [
                        row[1] for row in self._connection.execute(
                            "PRAGMA table_info(upload_sessions)"
                        )
                    ]
                    foreign_key = self._connection.execute(
                        "PRAGMA foreign_key_list(upload_parts)"
                    ).fetchone()
                    violations = self._connection.execute(
                        "PRAGMA foreign_key_check"
                    ).fetchall()
                    expected_columns = [
                        "upload_id", "project_id", "filename", "declared_mime",
                        "total_bytes", "expected_sha256", "part_size",
                        "transfer_status", "created_at", "expires_at",
                        "completed_artifact_id", "completed_material_id",
                    ]
                    if (
                        migrated_columns != expected_columns
                        or foreign_key is None
                        or foreign_key[2] != "upload_sessions"
                        or str(foreign_key[6]).upper() != "CASCADE"
                        or violations
                    ):
                        raise RuntimeError("material store migration integrity check failed")
                    self._connection.execute(
                        "CREATE TABLE IF NOT EXISTS upload_idempotency(project_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, request_sha256 TEXT NOT NULL, upload_ids_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(project_id, idempotency_key))"
                    )
                    self._connection.execute(
                        "CREATE TABLE IF NOT EXISTS processing_attempts(revision_id TEXT PRIMARY KEY REFERENCES material_revisions(revision_id), attempt INTEGER NOT NULL CHECK(attempt >= 0), last_failure_category TEXT)"
                    )
                    self._connection.execute(
                        f"PRAGMA user_version={CURRENT_SCHEMA_VERSION}"
                    )
                    self._connection.commit()
                except Exception:
                    if self._connection.in_transaction:
                        self._connection.rollback()
                    raise
                finally:
                    self._connection.execute("PRAGMA foreign_keys=ON")
                    foreign_keys = self._connection.execute(
                        "PRAGMA foreign_keys"
                    ).fetchone()[0]
                    if foreign_keys != 1:
                        raise RuntimeError(
                            "material store migration failed to restore foreign keys"
                        )
            else:
                self._connection.execute(
                    "CREATE TABLE IF NOT EXISTS upload_idempotency(project_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, request_sha256 TEXT NOT NULL, upload_ids_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(project_id, idempotency_key))"
                )
                self._connection.execute(
                    "CREATE TABLE IF NOT EXISTS processing_attempts(revision_id TEXT PRIMARY KEY REFERENCES material_revisions(revision_id), attempt INTEGER NOT NULL CHECK(attempt >= 0), last_failure_category TEXT)"
                )
                self._connection.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION}")
        elif version == 2:
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS upload_idempotency(project_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, request_sha256 TEXT NOT NULL, upload_ids_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(project_id, idempotency_key))"
            )
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS processing_attempts(revision_id TEXT PRIMARY KEY REFERENCES material_revisions(revision_id), attempt INTEGER NOT NULL CHECK(attempt >= 0), last_failure_category TEXT)"
            )
            self._connection.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION}")
        elif version == 3:
            self._connection.execute(
                "CREATE TABLE processing_attempts(revision_id TEXT PRIMARY KEY REFERENCES material_revisions(revision_id), attempt INTEGER NOT NULL CHECK(attempt >= 0), last_failure_category TEXT)"
            )
            self._connection.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION}")
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS material_mutation_idempotency(
                project_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                operation TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                response_json TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(project_id, idempotency_key))"""
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS material_metadata(
                material_id TEXT PRIMARY KEY REFERENCES materials(material_id),
                display_name TEXT NOT NULL, media_type TEXT NOT NULL,
                byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
                tags_json TEXT NOT NULL, directory TEXT NOT NULL,
                source_kind TEXT NOT NULL)"""
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS material_revision_metadata(
                revision_id TEXT PRIMARY KEY REFERENCES material_revisions(revision_id),
                display_name TEXT NOT NULL, media_type TEXT NOT NULL,
                byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
                source_kind TEXT NOT NULL)"""
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS material_authority_intents(
                project_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                operation TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                request_json TEXT NOT NULL, previous_json TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(project_id, idempotency_key))"""
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS remote_intake_fetches(
                project_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_sha256 TEXT NOT NULL,
                artifact_id TEXT NOT NULL,
                byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
                detected_mime TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(project_id, idempotency_key))"""
        )
        self._connection.execute(
            """INSERT OR IGNORE INTO material_metadata(
                   material_id,display_name,media_type,byte_size,tags_json,directory,source_kind)
               SELECT m.material_id,
                      COALESCE((SELECT u.filename FROM upload_sessions u
                                WHERE u.completed_material_id=m.material_id
                                ORDER BY u.created_at,u.upload_id LIMIT 1),m.material_id),
                      COALESCE((SELECT u.declared_mime FROM upload_sessions u
                                WHERE u.completed_material_id=m.material_id
                                ORDER BY u.created_at,u.upload_id LIMIT 1),'application/octet-stream'),
                      COALESCE((SELECT u.total_bytes FROM upload_sessions u
                                WHERE u.completed_material_id=m.material_id
                                ORDER BY u.created_at,u.upload_id LIMIT 1),0),
                      '[]','', 'local_upload'
               FROM materials m"""
        )
        self._connection.execute(
            """INSERT OR IGNORE INTO material_revision_metadata(
                   revision_id,display_name,media_type,byte_size,source_kind)
               SELECT r.revision_id,md.display_name,md.media_type,md.byte_size,md.source_kind
               FROM material_revisions r
               JOIN material_metadata md ON md.material_id=r.material_id"""
        )
        self._connection.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION}")
        self._set_database_permissions()

    def _set_database_permissions(self) -> None:
        for path in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            if path.exists():
                if path.is_symlink() or not path.is_file():
                    raise ValueError("material control database path is unsafe")
                os.chmod(path, 0o600)

    @staticmethod
    def _validate_project(project_id: str) -> str:
        if not isinstance(project_id, str) or _IDENTIFIER.fullmatch(project_id) is None:
            raise ValueError("project_id is invalid")
        return project_id

    @staticmethod
    def _session_public(row: sqlite3.Row, parts: Sequence[sqlite3.Row] = ()) -> dict[str, Any]:
        result = dict(row)
        result["parts"] = [dict(part) for part in parts]
        return result

    def _row(self, project_id: str, upload_id: str) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM upload_sessions WHERE upload_id = ? AND project_id = ?",
            (upload_id, project_id),
        ).fetchone()
        if row is None:
            raise KeyError("upload session is unavailable")
        return row

    @contextmanager
    def _upload_transition_lock(self, upload_id: str):
        """Serialize every terminal or expiry-sensitive upload transition."""

        if not isinstance(upload_id, str) or _IDENTIFIER.fullmatch(upload_id) is None:
            raise ValueError("upload_id is invalid")
        lock_path = self.input_artifacts.locks_root / f"upload-{upload_id}.lock"
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _expire_if_needed(self, row: sqlite3.Row) -> sqlite3.Row:
        if row["transfer_status"] == "uploading" and _parse_time(self._clock()) >= _parse_time(row["expires_at"]):
            self._connection.execute(
                "UPDATE upload_sessions SET transfer_status = 'expired' WHERE upload_id = ? AND transfer_status = 'uploading'",
                (row["upload_id"],),
            )
            row = self._connection.execute("SELECT * FROM upload_sessions WHERE upload_id = ?", (row["upload_id"],)).fetchone()
            self._discard_parts(row["upload_id"])
        return row

    def _discard_parts(self, upload_id: str) -> None:
        rows = self._connection.execute(
            "SELECT path FROM upload_parts WHERE upload_id = ?", (upload_id,)
        ).fetchall()
        for row in rows:
            path = self.data_root / row["path"]
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(self.parts_root.resolve(strict=True))
            except (OSError, ValueError):
                continue
            if path.is_symlink() or not resolved.is_file():
                continue
            resolved.unlink()
        self._connection.execute(
            "DELETE FROM upload_parts WHERE upload_id = ?", (upload_id,)
        )
        directory = self.parts_root / upload_id
        if not directory.is_symlink():
            try:
                directory.rmdir()
            except OSError:
                pass

    def _reconcile_terminal_parts(self) -> None:
        rows = self._connection.execute(
            "SELECT upload_id FROM upload_sessions WHERE transfer_status IN ('cancelled', 'expired', 'completed')"
        ).fetchall()
        for row in rows:
            self._discard_parts(row["upload_id"])

    def _reconcile_completion_intents(self) -> None:
        rows = self._connection.execute(
            """SELECT e.project_id, e.material_id, e.payload_json
               FROM material_events e
               WHERE e.event_type = 'research.material.completion_intent'"""
        ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            with self._upload_transition_lock(payload["upload_id"]):
                session = self._row(payload["project_id"], payload["upload_id"])
                if session["transfer_status"] != "uploading":
                    continue
                try:
                    self.input_artifacts.describe(
                        research_id=row["project_id"], artifact_id=payload["artifact_id"]
                    )
                except (KeyError, ValueError):
                    continue
                self._finish_completion_intent(payload)

    def create_upload(
        self,
        *,
        project_id: str,
        filename: str,
        declared_mime: str,
        total_bytes: int,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        return self.create_upload_batch(
            project_id=project_id,
            uploads=[{
                "filename": filename, "declared_mime": declared_mime,
                "total_bytes": total_bytes, "expected_sha256": expected_sha256,
            }],
        )[0]

    def create_upload_batch(
        self,
        *,
        project_id: str,
        uploads: Sequence[Mapping[str, Any]],
        _idempotency_key: str | None = None,
    ) -> list[dict[str, Any]]:
        project_id = self._validate_project(project_id)
        if not uploads or len(uploads) > self.policy["batch_file_count"]:
            raise ValueError("upload batch file count exceeds policy")
        normalized: list[dict[str, Any]] = []
        total_batch = 0
        for upload in uploads:
            filename = upload.get("filename")
            declared_mime = upload.get("declared_mime")
            total_bytes = upload.get("total_bytes")
            expected = upload.get("expected_sha256")
            if not isinstance(filename, str) or not filename or "\x00" in filename:
                raise ValueError("filename is invalid")
            if not isinstance(declared_mime, str) or "/" not in declared_mime:
                raise ValueError("declared MIME is invalid")
            if not isinstance(total_bytes, int) or isinstance(total_bytes, bool) or total_bytes < 0:
                raise ValueError("total_bytes is invalid")
            if total_bytes > self.policy["generic_file_max_bytes"]:
                raise ValueError("file exceeds upload policy")
            if expected is not None and (not isinstance(expected, str) or _SHA256.fullmatch(expected) is None):
                raise ValueError("expected_sha256 is invalid")
            total_batch += total_bytes
            normalized.append({"filename": filename, "declared_mime": declared_mime, "total_bytes": total_bytes, "expected_sha256": expected})
        if total_batch > self.policy["batch_max_bytes"]:
            raise ValueError("upload batch bytes exceed policy")
        request_sha256 = hashlib.sha256(_json(normalized).encode()).hexdigest()

        with self._lock:
            now = self._clock()
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                if _idempotency_key is not None:
                    prior = self._connection.execute(
                        "SELECT request_sha256, upload_ids_json FROM upload_idempotency WHERE project_id=? AND idempotency_key=?",
                        (project_id, _idempotency_key),
                    ).fetchone()
                    if prior is not None:
                        if prior["request_sha256"] != request_sha256:
                            raise ValueError(
                                "idempotency key was reused for different upload metadata"
                            )
                        self._connection.commit()
                        return [
                            self.get_upload(
                                project_id=project_id,
                                upload_id=upload_id,
                                include_terminal=True,
                            )
                            for upload_id in json.loads(prior["upload_ids_json"])
                        ]
                self._connection.execute(
                    """UPDATE upload_sessions SET transfer_status = 'expired'
                       WHERE project_id = ? AND transfer_status = 'uploading' AND expires_at <= ?""",
                    (project_id, now),
                )
                expired = self._connection.execute(
                    "SELECT upload_id FROM upload_sessions WHERE project_id = ? AND transfer_status = 'expired'",
                    (project_id,),
                ).fetchall()
                used = self._connection.execute(
                    """SELECT COALESCE(SUM(total_bytes), 0) FROM upload_sessions
                       WHERE project_id = ? AND transfer_status IN ('uploading', 'completed')""",
                    (project_id,),
                ).fetchone()[0]
                if used + total_batch > self.policy["project_max_bytes"]:
                    raise ValueError("project input capacity exceeded")
                result = []
                for item in normalized:
                    nonce = uuid.uuid4().hex
                    upload_id = str(uuid.uuid5(_UPLOAD_NAMESPACE, f"{project_id}:{now}:{nonce}"))
                    expires_at = _plus_seconds(now, self.policy["session_seconds"])
                    self._connection.execute(
                        """INSERT INTO upload_sessions(
                            upload_id, project_id, filename, declared_mime, total_bytes,
                            expected_sha256, part_size, transfer_status, created_at, expires_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'uploading', ?, ?)""",
                        (upload_id, project_id, item["filename"], item["declared_mime"], item["total_bytes"], item["expected_sha256"], self.policy["part_bytes"], now, expires_at),
                    )
                    row = self._connection.execute("SELECT * FROM upload_sessions WHERE upload_id = ?", (upload_id,)).fetchone()
                    result.append(self._session_public(row))
                if _idempotency_key is not None:
                    self._connection.execute(
                        "INSERT INTO upload_idempotency(project_id,idempotency_key,request_sha256,upload_ids_json,created_at) VALUES(?,?,?,?,?)",
                        (
                            project_id,
                            _idempotency_key,
                            request_sha256,
                            _json([row["upload_id"] for row in result]),
                            now,
                        ),
                    )
                self._connection.commit()
                for expired_row in expired:
                    self._discard_parts(expired_row["upload_id"])
                return result
            except Exception:
                self._connection.rollback()
                raise

    def create_upload_batch_idempotent(
        self,
        *,
        project_id: str,
        uploads: Sequence[Mapping[str, Any]],
        idempotency_key: str,
    ) -> list[dict[str, Any]]:
        """Persist create receipts so retries remain stable across service restart."""

        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 255:
            raise ValueError("idempotency key is invalid")
        return self.create_upload_batch(
            project_id=project_id,
            uploads=uploads,
            _idempotency_key=idempotency_key,
        )

    def get_upload(self, *, project_id: str, upload_id: str, include_terminal: bool = False) -> dict[str, Any]:
        with self._upload_transition_lock(upload_id):
            return self._get_upload_locked(
                project_id=project_id,
                upload_id=upload_id,
                include_terminal=include_terminal,
            )

    def _get_upload_locked(self, *, project_id: str, upload_id: str, include_terminal: bool = False) -> dict[str, Any]:
        project_id = self._validate_project(project_id)
        with self._lock:
            row = self._expire_if_needed(self._row(project_id, upload_id))
            if row["transfer_status"] == "expired" and not include_terminal:
                raise ValueError("upload session expired")
            parts = self._connection.execute("SELECT * FROM upload_parts WHERE upload_id = ? ORDER BY part_number", (upload_id,)).fetchall()
            return self._session_public(row, parts)

    def receive_part(
        self,
        *,
        project_id: str,
        upload_id: str,
        part_number: int,
        first_byte: int,
        last_byte: int,
        total_bytes: int,
        source: BinaryIO,
        sha256: str,
    ) -> dict[str, Any]:
        with self._upload_transition_lock(upload_id):
            return self._receive_part_locked(
                project_id=project_id,
                upload_id=upload_id,
                part_number=part_number,
                first_byte=first_byte,
                last_byte=last_byte,
                total_bytes=total_bytes,
                source=source,
                sha256=sha256,
            )

    def _receive_part_locked(
        self,
        *,
        project_id: str,
        upload_id: str,
        part_number: int,
        first_byte: int,
        last_byte: int,
        total_bytes: int,
        source: BinaryIO,
        sha256: str,
    ) -> dict[str, Any]:
        if not isinstance(part_number, int) or isinstance(part_number, bool) or part_number < 0:
            raise ValueError("part_number is invalid")
        if not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None:
            raise ValueError("part sha256 is invalid")
        with self._lock:
            row = self._expire_if_needed(self._row(self._validate_project(project_id), upload_id))
            if row["transfer_status"] != "uploading":
                raise ValueError("upload session is not writable")
            expected_first = part_number * row["part_size"]
            expected_last = min(row["total_bytes"], expected_first + row["part_size"]) - 1
            if total_bytes != row["total_bytes"] or first_byte != expected_first or last_byte != expected_last or last_byte < first_byte:
                raise ValueError("part Content-Range is invalid")
            expected_bytes = last_byte - first_byte + 1
            target: Path | None = None
            temporary: Path | None = None
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._connection.execute(
                    "SELECT * FROM upload_parts WHERE upload_id = ? AND part_number = ?", (upload_id, part_number)
                ).fetchone()
                if existing is not None:
                    self._connection.commit()
                    digest, count = hashlib.sha256(), 0
                    while chunk := source.read(1024 * 1024):
                        if not isinstance(chunk, bytes):
                            raise ValueError("source must yield bytes")
                        count += len(chunk)
                        digest.update(chunk)
                    if count != existing["bytes"] or digest.hexdigest() != existing["sha256"] or sha256 != existing["sha256"]:
                        raise ValueError("duplicate upload part drifted")
                    return dict(existing)
                session_root = self.parts_root / upload_id
                _ensure_directory(session_root, root=self.data_root)
                descriptor, temporary_name = tempfile.mkstemp(prefix=f".{part_number}-", dir=session_root)
                temporary = Path(temporary_name)
                digest = hashlib.sha256()
                os.chmod(descriptor, 0o600)
                with os.fdopen(descriptor, "wb", closefd=True) as destination:
                    count = 0
                    while chunk := source.read(1024 * 1024):
                        if not isinstance(chunk, bytes):
                            raise ValueError("source must yield bytes")
                        count += len(chunk)
                        digest.update(chunk)
                        destination.write(chunk)
                    destination.flush()
                    os.fsync(destination.fileno())
                if count != expected_bytes or digest.hexdigest() != sha256:
                    raise ValueError("part bytes or SHA-256 mismatch")
                target = session_root / f"{part_number:08d}.part"
                os.replace(temporary, target)
                os.chmod(target, 0o600)
                _fsync_directory(session_root)
                relative = target.relative_to(self.data_root).as_posix()
                self._connection.execute(
                    """INSERT INTO upload_parts(upload_id, part_number, first_byte, last_byte, bytes, sha256, path)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (upload_id, part_number, first_byte, last_byte, count, sha256, relative),
                )
                self._connection.commit()
                return dict(self._connection.execute("SELECT * FROM upload_parts WHERE upload_id = ? AND part_number = ?", (upload_id, part_number)).fetchone())
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                if target is not None and target.exists():
                    committed = self._connection.execute(
                        "SELECT 1 FROM upload_parts WHERE upload_id = ? AND part_number = ?",
                        (upload_id, part_number),
                    ).fetchone()
                    if committed is None:
                        target.unlink(missing_ok=True)
                raise
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def validate_part_request(
        self,
        *,
        project_id: str,
        upload_id: str,
        part_number: int,
        first_byte: int,
        last_byte: int,
        total_bytes: int,
    ) -> int:
        with self._upload_transition_lock(upload_id):
            return self._validate_part_request_locked(
                project_id=project_id,
                upload_id=upload_id,
                part_number=part_number,
                first_byte=first_byte,
                last_byte=last_byte,
                total_bytes=total_bytes,
            )

    def _validate_part_request_locked(
        self,
        *,
        project_id: str,
        upload_id: str,
        part_number: int,
        first_byte: int,
        last_byte: int,
        total_bytes: int,
    ) -> int:
        """Authorize one exact part range before the HTTP body is consumed."""

        if not isinstance(part_number, int) or isinstance(part_number, bool) or part_number < 0:
            raise ValueError("part_number is invalid")
        with self._lock:
            row = self._expire_if_needed(
                self._row(self._validate_project(project_id), upload_id)
            )
            if row["transfer_status"] != "uploading":
                raise ValueError("upload session is not writable")
            expected_first = part_number * row["part_size"]
            expected_last = min(
                row["total_bytes"], expected_first + row["part_size"]
            ) - 1
            if (
                total_bytes != row["total_bytes"]
                or first_byte != expected_first
                or last_byte != expected_last
                or last_byte < first_byte
            ):
                raise ValueError("part Content-Range is invalid")
            return last_byte - first_byte + 1

    def _completed(self, row: sqlite3.Row) -> dict[str, Any]:
        material = self._connection.execute("SELECT * FROM materials WHERE material_id = ? AND project_id = ?", (row["completed_material_id"], row["project_id"])).fetchone()
        revision = self._connection.execute("SELECT * FROM material_revisions WHERE revision_id = ?", (material["current_revision_id"],)).fetchone()
        artifact = self.input_artifacts.describe(research_id=row["project_id"], artifact_id=row["completed_artifact_id"])
        metadata = self._connection.execute(
            "SELECT * FROM material_metadata WHERE material_id=?", (material["material_id"],)
        ).fetchone()
        material_public = dict(material)
        material_public["display_name"] = metadata["display_name"] if metadata else row["filename"]
        expected_material_id = str(
            uuid.uuid5(_MATERIAL_NAMESPACE, f"{row['project_id']}:{row['upload_id']}")
        )
        conflicts = self._connection.execute(
            """SELECT m.material_id FROM materials m
               JOIN material_metadata md ON md.material_id=m.material_id
               JOIN material_revisions r ON r.revision_id=m.current_revision_id
               WHERE m.project_id=? AND m.material_id<>? AND m.deleted_at IS NULL
                 AND md.display_name=? AND r.input_artifact_id<>?
               ORDER BY m.operational_updated_at,m.material_id""",
            (row["project_id"], material["material_id"], row["filename"], row["completed_artifact_id"]),
        ).fetchall()
        return {
            "upload": self._session_public(row),
            "artifact": artifact,
            "material": material_public,
            "revision": dict(revision),
            "deduplicated": row["completed_material_id"] != expected_material_id,
            "name_conflict": (
                {
                    "kind": "same_name_different_content",
                    "existing_material_ids": [item["material_id"] for item in conflicts],
                    "actions": ["replace_as_version", "keep_separate"],
                }
                if conflicts else None
            ),
        }

    def _finish_completion_intent(self, payload: Mapping[str, Any]) -> None:
        artifact = self.input_artifacts.describe(
            research_id=str(payload["project_id"]), artifact_id=str(payload["artifact_id"])
        )
        if artifact["sha256"] != payload["artifact_id"]:
            raise ValueError("completion intent artifact drifted")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._row(str(payload["project_id"]), str(payload["upload_id"]))
            if row["transfer_status"] == "completed":
                self._connection.commit()
                return
            if row["transfer_status"] != "uploading":
                raise ValueError("completion intent lost the active upload session")
            self._connection.execute(
                """INSERT OR IGNORE INTO materials(material_id, project_id, current_revision_id, scope,
                   deleted_at, operational_updated_at, project_record_ref_json)
                   VALUES (?, ?, NULL, ?, NULL, ?, NULL)""",
                (payload["material_id"], payload["project_id"], payload["scope"], payload["occurred_at"]),
            )
            self._connection.execute(
                """INSERT OR IGNORE INTO material_revisions(revision_id, material_id, input_artifact_id,
                   revision_number, processing_status, scientific_status, receipt_ref_json,
                   supersedes_revision_id, created_at)
                   VALUES (?, ?, ?, 1, 'pending_security', 'candidate_only', NULL, NULL, ?)""",
                (payload["revision_id"], payload["material_id"], payload["artifact_id"], payload["occurred_at"]),
            )
            self._connection.execute(
                "UPDATE materials SET current_revision_id = ? WHERE material_id = ?",
                (payload["revision_id"], payload["material_id"]),
            )
            self._connection.execute(
                """INSERT OR IGNORE INTO material_metadata(
                       material_id,display_name,media_type,byte_size,tags_json,directory,source_kind)
                   VALUES(?,?,?,?,?,'','local_upload')""",
                (
                    payload["material_id"], row["filename"],
                    artifact["detected_media_type"], artifact["byte_size"], "[]",
                ),
            )
            self._connection.execute(
                """INSERT OR IGNORE INTO material_revision_metadata(
                       revision_id,display_name,media_type,byte_size,source_kind)
                   VALUES(?,?,?,?, 'local_upload')""",
                (
                    payload["revision_id"], row["filename"],
                    artifact["detected_media_type"], artifact["byte_size"],
                ),
            )
            exists = self._connection.execute(
                "SELECT 1 FROM material_events WHERE material_id = ? AND event_type = 'research.material.upload_completed'",
                (payload["material_id"],),
            ).fetchone()
            if exists is None:
                self._connection.execute(
                    "INSERT INTO material_events(project_id, material_id, event_type, payload_json, occurred_at) VALUES (?, ?, ?, ?, ?)",
                    (payload["project_id"], payload["material_id"], "research.material.upload_completed", _json({"artifact_id": payload["artifact_id"], "revision_id": payload["revision_id"]}), payload["occurred_at"]),
                )
            self._connection.execute(
                "UPDATE upload_sessions SET transfer_status='completed', completed_artifact_id=?, completed_material_id=? WHERE upload_id=? AND transfer_status='uploading'",
                (payload["artifact_id"], payload["material_id"], payload["upload_id"]),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def complete_upload(
        self,
        *,
        project_id: str,
        upload_id: str,
        scope: str = "turn",
        explicit_duplicate: bool = False,
    ) -> dict[str, Any]:
        """Complete exactly once across concurrent store connections."""

        with self._upload_transition_lock(upload_id):
            return self._complete_upload_locked(
                project_id=project_id,
                upload_id=upload_id,
                scope=scope,
                explicit_duplicate=explicit_duplicate,
            )

    def _complete_upload_locked(
        self,
        *,
        project_id: str,
        upload_id: str,
        scope: str = "turn",
        explicit_duplicate: bool = False,
    ) -> dict[str, Any]:
        if scope not in {"turn", "project"}:
            raise ValueError("material scope is invalid")
        with self._lock:
            project_id = self._validate_project(project_id)
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._expire_if_needed(self._row(project_id, upload_id))
                if row["transfer_status"] == "completed":
                    self._connection.commit()
                    self._discard_parts(upload_id)
                    return self._completed(row)
                if row["transfer_status"] != "uploading":
                    raise ValueError("upload session cannot be completed")
                self._connection.commit()
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise
            parts = self._connection.execute("SELECT * FROM upload_parts WHERE upload_id = ? ORDER BY part_number", (upload_id,)).fetchall()
            expected_parts = 0 if row["total_bytes"] == 0 else (row["total_bytes"] + row["part_size"] - 1) // row["part_size"]
            if len(parts) != expected_parts or [part["part_number"] for part in parts] != list(range(expected_parts)):
                raise ValueError("upload session has missing parts")
            combined = tempfile.TemporaryFile(mode="w+b")
            digest = hashlib.sha256()
            total = 0
            try:
                for part in parts:
                    path = self.data_root / part["path"]
                    try:
                        resolved = path.resolve(strict=True)
                        resolved.relative_to(self.parts_root.resolve(strict=True))
                    except (OSError, ValueError) as error:
                        raise ValueError("upload part path is unsafe") from error
                    if path.is_symlink() or not resolved.is_file():
                        raise ValueError("upload part path is unsafe")
                    with resolved.open("rb") as source:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            total += len(chunk)
                            digest.update(chunk)
                            combined.write(chunk)
                actual_sha = digest.hexdigest()
                if total != row["total_bytes"]:
                    raise ValueError("whole-file byte size mismatch")
                if row["expected_sha256"] is not None and actual_sha != row["expected_sha256"]:
                    raise ValueError("whole-file SHA-256 mismatch")
            except Exception:
                combined.close()
                raise

            now = self._clock()
            if not explicit_duplicate:
                existing = self._connection.execute(
                    """SELECT m.material_id
                       FROM material_revisions r
                       JOIN materials m ON m.material_id=r.material_id
                       WHERE m.project_id=? AND r.input_artifact_id=? AND m.deleted_at IS NULL
                       ORDER BY r.created_at, m.material_id LIMIT 1""",
                    (project_id, actual_sha),
                ).fetchone()
                if existing is not None:
                    self._connection.execute(
                        """UPDATE upload_sessions SET transfer_status='completed',
                           completed_artifact_id=?, completed_material_id=?
                           WHERE upload_id=? AND transfer_status='uploading'""",
                        (actual_sha, existing["material_id"], upload_id),
                    )
                    combined.close()
                    self._discard_parts(upload_id)
                    return self._completed(self._row(project_id, upload_id))
            material_id = str(uuid.uuid5(_MATERIAL_NAMESPACE, f"{project_id}:{upload_id}"))
            revision_id = str(uuid.uuid5(_REVISION_NAMESPACE, f"{material_id}:1:{actual_sha}"))
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._row(project_id, upload_id)
                if current["transfer_status"] == "completed":
                    self._connection.commit()
                    combined.close()
                    return self._completed(current)
                intent = {"upload_id": upload_id, "project_id": project_id, "artifact_id": actual_sha,
                          "material_id": material_id, "revision_id": revision_id, "scope": scope, "occurred_at": now}
                self._connection.execute(
                    """INSERT OR IGNORE INTO materials(material_id, project_id, current_revision_id, scope,
                       deleted_at, operational_updated_at, project_record_ref_json)
                       VALUES (?, ?, NULL, ?, NULL, ?, NULL)""",
                    (material_id, project_id, scope, now),
                )
                exists = self._connection.execute(
                    "SELECT 1 FROM material_events WHERE material_id=? AND event_type='research.material.completion_intent'",
                    (material_id,),
                ).fetchone()
                if exists is None:
                    self._connection.execute(
                        "INSERT INTO material_events(project_id, material_id, event_type, payload_json, occurred_at) VALUES (?, ?, 'research.material.completion_intent', ?, ?)",
                        (project_id, material_id, _json(intent), now),
                    )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                combined.close()
                raise
            try:
                combined.seek(0)
                self.input_artifacts.put_verified(
                    research_id=project_id, source=combined, expected_sha256=actual_sha,
                    expected_bytes=total, detected_mime=row["declared_mime"],
                    original_filename=row["filename"], source_kind="local_upload",
                    source_locator={"upload_id": upload_id},
                )
            finally:
                combined.close()
            self._finish_completion_intent(intent)
            self._discard_parts(upload_id)
            updated = self._row(project_id, upload_id)
            return self._completed(updated)

    def cancel_upload(self, *, project_id: str, upload_id: str) -> dict[str, Any]:
        with self._upload_transition_lock(upload_id):
            with self._lock:
                row = self._expire_if_needed(self._row(self._validate_project(project_id), upload_id))
                if row["transfer_status"] == "completed":
                    raise ValueError("completed upload cannot be cancelled")
                if row["transfer_status"] == "uploading":
                    self._connection.execute("UPDATE upload_sessions SET transfer_status = 'cancelled' WHERE upload_id = ?", (upload_id,))
                    self._discard_parts(upload_id)
                    row = self._row(project_id, upload_id)
                return self._session_public(row)

    def get_material(
        self, *, project_id: str, material_id: str, include_deleted: bool = False
    ) -> dict[str, Any]:
        """Return one project-bound material and its current candidate revision."""

        with self._lock:
            query = "SELECT * FROM materials WHERE project_id = ? AND material_id = ?"
            if not include_deleted:
                query += " AND deleted_at IS NULL"
            row = self._connection.execute(
                query, (self._validate_project(project_id), material_id)
            ).fetchone()
            if row is None:
                raise KeyError("material is unavailable")
            revision = self._connection.execute(
                "SELECT * FROM material_revisions WHERE revision_id = ?",
                (row["current_revision_id"],),
            ).fetchone()
            return {"material": dict(row), "revision": dict(revision)}

    def get_material_revision(
        self,
        *,
        project_id: str,
        material_id: str,
        revision_id: str,
        include_deleted: bool = False,
    ) -> dict[str, Any]:
        """Return one immutable revision without following the mutable current pointer."""

        with self._lock:
            query = """SELECT m.*, r.revision_id AS selected_revision_id,
                              r.input_artifact_id, r.revision_number,
                              r.processing_status, r.scientific_status,
                              r.receipt_ref_json, r.supersedes_revision_id,
                              r.created_at AS revision_created_at
                       FROM materials m JOIN material_revisions r
                         ON r.material_id=m.material_id
                       WHERE m.project_id=? AND m.material_id=? AND r.revision_id=?"""
            if not include_deleted:
                query += " AND m.deleted_at IS NULL"
            row = self._connection.execute(
                query,
                (self._validate_project(project_id), material_id, revision_id),
            ).fetchone()
            if row is None:
                raise KeyError("material revision is unavailable")
            material = {
                key: row[key]
                for key in (
                    "material_id", "project_id", "current_revision_id", "scope",
                    "deleted_at", "operational_updated_at", "project_record_ref_json",
                )
            }
            revision = {
                "revision_id": row["selected_revision_id"],
                "material_id": row["material_id"],
                "input_artifact_id": row["input_artifact_id"],
                "revision_number": row["revision_number"],
                "processing_status": row["processing_status"],
                "scientific_status": row["scientific_status"],
                "receipt_ref_json": row["receipt_ref_json"],
                "supersedes_revision_id": row["supersedes_revision_id"],
                "created_at": row["revision_created_at"],
            }
            return {"material": material, "revision": revision}

    def reserve_authority_command(
        self,
        *,
        project_id: str,
        idempotency_key: str,
        operation: str,
        request_value: Mapping[str, Any],
    ) -> None:
        """Reserve a command identity without changing the operational projection."""

        project_id = self._validate_project(project_id)
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        request_json = _json(dict(request_value))
        request_sha256 = hashlib.sha256(request_json.encode()).hexdigest()
        with self._lock:
            legacy = self._connection.execute(
                """SELECT operation,request_sha256 FROM material_mutation_idempotency
                   WHERE project_id=? AND idempotency_key=?""",
                (project_id, idempotency_key),
            ).fetchone()
            if legacy is not None and (
                legacy["operation"] != operation
                or legacy["request_sha256"] != request_sha256
            ):
                raise ValueError(
                    "idempotency key was reused for a different material mutation"
                )
            prior = self._connection.execute(
                """SELECT operation,request_sha256 FROM material_authority_intents
                   WHERE project_id=? AND idempotency_key=?""",
                (project_id, idempotency_key),
            ).fetchone()
            if prior is not None:
                if (
                    prior["operation"] != operation
                    or prior["request_sha256"] != request_sha256
                ):
                    raise ValueError(
                        "idempotency key was reused for a different material mutation"
                    )
                return
            now = self._clock()
            self._connection.execute(
                """INSERT INTO material_authority_intents(
                       project_id,idempotency_key,operation,request_sha256,
                       request_json,previous_json,status,created_at)
                   VALUES(?,?,?,?,?,'{}','reserved',?)""",
                (
                    project_id, idempotency_key, operation, request_sha256,
                    request_json, now,
                ),
            )

    def apply_project_authority_projection(
        self, *, project_id: str, project_view: Mapping[str, Any]
    ) -> None:
        """Rebuild mutable scope/deletion fields from Project Record truth."""

        materials = project_view.get("materials")
        if not isinstance(materials, Mapping):
            raise ValueError("Project Record material projection is invalid")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                for material_id, accepted in materials.items():
                    if not isinstance(material_id, str) or not isinstance(
                        accepted, Mapping
                    ):
                        raise ValueError("Project Record material projection is invalid")
                    row = self._connection.execute(
                        "SELECT 1 FROM materials WHERE project_id=? AND material_id=?",
                        (project_id, material_id),
                    ).fetchone()
                    if row is None:
                        continue
                    self._connection.execute(
                        """UPDATE materials SET scope=?,deleted_at=?,operational_updated_at=?
                           WHERE project_id=? AND material_id=?""",
                        (
                            accepted.get("scope"), accepted.get("deleted_at"),
                            self._clock(), project_id, material_id,
                        ),
                    )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def list_materials(
        self,
        *,
        project_id: str,
        query: str | None = None,
        type_family: str | None = None,
        processing_status: str | None = None,
        scientific_status: str | None = None,
        scope: str | None = None,
        tag: str | None = None,
        directory: str | None = None,
        deleted: str = "false",
    ) -> list[dict[str, Any]]:
        """Project-scoped operational rows; callers never receive storage paths."""

        project_id = self._validate_project(project_id)
        with self._lock:
            if deleted not in {"false", "true", "all"}:
                raise ValueError("deleted filter is invalid")
            rows = self._connection.execute(
                """SELECT m.*, r.revision_id, r.revision_number,
                          r.processing_status, r.scientific_status,
                          r.input_artifact_id, r.created_at AS revision_created_at,
                          (SELECT us.created_at
                             FROM material_events ci
                             JOIN upload_sessions us
                               ON us.upload_id=json_extract(ci.payload_json,'$.upload_id')
                            WHERE ci.material_id=m.material_id
                              AND ci.event_type='research.material.completion_intent'
                              AND json_extract(ci.payload_json,'$.revision_id')=r.revision_id
                            ORDER BY ci.sequence DESC LIMIT 1) AS upload_created_at,
                          (SELECT ready.occurred_at
                             FROM material_events ready
                            WHERE ready.material_id=m.material_id
                              AND ready.event_type='research.material.processing_ready'
                              AND json_extract(ready.payload_json,'$.revision_id')=r.revision_id
                            ORDER BY ready.sequence DESC LIMIT 1) AS processing_ready_at,
                          rm.display_name AS filename, rm.media_type AS declared_mime,
                          rm.byte_size AS total_bytes, md.tags_json, md.directory,
                          rm.source_kind, 'completed' AS transfer_status
                   FROM materials m
                   JOIN material_revisions r ON r.revision_id=m.current_revision_id
                   LEFT JOIN material_revision_metadata rm ON rm.revision_id=r.revision_id
                   LEFT JOIN material_metadata md ON md.material_id=m.material_id
                   WHERE m.project_id=?
                   ORDER BY m.operational_updated_at DESC, m.material_id""",
                (project_id,),
            ).fetchall()
            result = [self._library_row(row) for row in rows]
            by_name: dict[str, list[dict[str, Any]]] = {}
            for item in result:
                by_name.setdefault(item["display_name"], []).append(item)
            for item in result:
                conflicts = [
                    candidate
                    for candidate in by_name[item["display_name"]]
                    if candidate["material_id"] != item["material_id"]
                    and candidate["current_revision"]["input_artifact_id"]
                    != item["current_revision"]["input_artifact_id"]
                    and candidate["deleted_at"] is None
                ]
                item["name_conflict"] = (
                    {
                        "kind": "same_name_different_content",
                        "existing_material_ids": [
                            candidate["material_id"] for candidate in conflicts
                        ],
                        "actions": ["replace_as_version", "keep_separate"],
                    }
                    if conflicts
                    else None
                )
            if deleted == "false":
                result = [row for row in result if row["deleted_at"] is None]
            elif deleted == "true":
                result = [row for row in result if row["deleted_at"] is not None]
            if query:
                needle = query.casefold()
                result = [row for row in result if needle in row["display_name"].casefold()]
            filters = {
                "type_family": type_family,
                "processing_status": processing_status,
                "scientific_status": scientific_status,
                "scope": scope,
            }
            for field, expected in filters.items():
                if expected is None:
                    continue
                if field in {"processing_status", "scientific_status"}:
                    result = [row for row in result if row["current_revision"][field] == expected]
                else:
                    result = [row for row in result if row[field] == expected]
            if tag is not None:
                result = [row for row in result if tag in row["tags"]]
            if directory is not None:
                result = [row for row in result if row["directory"] == directory]
            return result

    def material_detail(self, *, project_id: str, material_id: str) -> dict[str, Any]:
        """Return one project-owned material and its immutable revision history."""

        project_id = self._validate_project(project_id)
        with self._lock:
            material = self._connection.execute(
                """SELECT m.*, rm.display_name AS filename,
                          rm.media_type AS declared_mime, rm.byte_size AS total_bytes,
                          md.tags_json, md.directory, rm.source_kind,
                          'completed' AS transfer_status
                   FROM materials m
                   LEFT JOIN material_revision_metadata rm ON rm.revision_id=m.current_revision_id
                   LEFT JOIN material_metadata md ON md.material_id=m.material_id
                   WHERE m.project_id=? AND m.material_id=?""",
                (project_id, material_id),
            ).fetchone()
            if material is None:
                raise KeyError("material is unavailable")
            revisions = self._connection.execute(
                """SELECT revision_id, material_id, input_artifact_id,
                          revision_number, processing_status, scientific_status,
                          receipt_ref_json, supersedes_revision_id, created_at
                   FROM material_revisions WHERE material_id=?
                   ORDER BY revision_number DESC""",
                (material_id,),
            ).fetchall()
            public_material = {
                "material_id": material["material_id"],
                "project_id": material["project_id"],
                "display_name": material["filename"] or material["material_id"],
                "scope": material["scope"],
                "deleted_at": material["deleted_at"],
                "transfer_status": material["transfer_status"] or "uploaded",
                "type_family": self._type_family(material["declared_mime"]),
                "media_type": material["declared_mime"] or "application/octet-stream",
                "byte_size": material["total_bytes"],
                "tags": json.loads(material["tags_json"] or "[]"),
                "directory": material["directory"] or "",
                "source_kind": material["source_kind"] or "local_upload",
                "current_revision_id": material["current_revision_id"],
            }
            public_revisions = [self._revision_public(row) for row in revisions]
            return {
                "material": public_material,
                "current_revision": next(
                    revision
                    for revision in public_revisions
                    if revision["revision_id"] == material["current_revision_id"]
                ),
                "revisions": public_revisions,
            }

    def downloadable_artifact(
        self, *, project_id: str, material_id: str
    ) -> dict[str, Any]:
        """Authorize the current immutable original without disclosing its path."""

        project_id = self._validate_project(project_id)
        with self._lock:
            row = self._connection.execute(
                """SELECT r.input_artifact_id, rm.display_name AS filename
                   FROM materials m
                   JOIN material_revisions r ON r.revision_id=m.current_revision_id
                   LEFT JOIN material_revision_metadata rm ON rm.revision_id=r.revision_id
                   WHERE m.project_id=? AND m.material_id=? AND m.deleted_at IS NULL""",
                (project_id, material_id),
            ).fetchone()
            if row is None:
                raise KeyError("material is unavailable")
            metadata = self.input_artifacts.describe(
                research_id=project_id, artifact_id=row["input_artifact_id"]
            )
            return {
                "artifact_id": row["input_artifact_id"],
                "filename": row["filename"] or metadata["original_filename"],
                "byte_size": metadata["byte_size"],
            }

    @staticmethod
    def _validate_idempotency_key(idempotency_key: str) -> str:
        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key.strip()
            or len(idempotency_key) > 255
        ):
            raise ValueError("idempotency key is invalid")
        return idempotency_key.strip()

    def _mutation_replay(
        self,
        *,
        project_id: str,
        idempotency_key: str,
        operation: str,
        request_value: Mapping[str, Any],
    ) -> tuple[str, dict[str, Any] | None]:
        request_sha256 = hashlib.sha256(_json(dict(request_value)).encode()).hexdigest()
        prior = self._connection.execute(
            """SELECT operation, request_sha256, response_json
               FROM material_mutation_idempotency
               WHERE project_id=? AND idempotency_key=?""",
            (project_id, idempotency_key),
        ).fetchone()
        if prior is None:
            return request_sha256, None
        if prior["operation"] != operation or prior["request_sha256"] != request_sha256:
            raise ValueError("idempotency key was reused for a different material mutation")
        return request_sha256, json.loads(prior["response_json"])

    def _record_mutation(
        self,
        *,
        project_id: str,
        idempotency_key: str,
        operation: str,
        request_sha256: str,
        response: Mapping[str, Any],
        occurred_at: str,
    ) -> None:
        self._connection.execute(
            """INSERT INTO material_mutation_idempotency(
                   project_id,idempotency_key,operation,request_sha256,response_json,created_at)
               VALUES(?,?,?,?,?,?)""",
            (project_id, idempotency_key, operation, request_sha256, _json(dict(response)), occurred_at),
        )

    def update_material_scope(
        self, *, project_id: str, material_id: str, scope: str, idempotency_key: str
    ) -> dict[str, Any]:
        if scope not in {"turn", "project"}:
            raise ValueError("material scope is invalid")
        return self._mutate_material(
            project_id=project_id,
            material_id=material_id,
            operation="scope",
            idempotency_key=idempotency_key,
            request_value={"material_id": material_id, "scope": scope},
            event_type="research.material.scope_updated",
            event_payload={"scope": scope},
            update_sql="UPDATE materials SET scope=?, operational_updated_at=? WHERE material_id=? AND project_id=?",
            update_values=lambda now: (scope, now, material_id, project_id),
        )

    def withdraw_material(
        self, *, project_id: str, material_id: str, idempotency_key: str
    ) -> dict[str, Any]:
        now = self._clock()
        return self._mutate_material(
            project_id=project_id,
            material_id=material_id,
            operation="withdraw",
            idempotency_key=idempotency_key,
            request_value={"material_id": material_id},
            event_type="research.material.withdrawn",
            event_payload={"deleted_at": now},
            update_sql="UPDATE materials SET deleted_at=?, operational_updated_at=? WHERE material_id=? AND project_id=?",
            update_values=lambda _now: (now, now, material_id, project_id),
            now=now,
        )

    def restore_material(
        self, *, project_id: str, material_id: str, idempotency_key: str
    ) -> dict[str, Any]:
        return self._mutate_material(
            project_id=project_id,
            material_id=material_id,
            operation="restore",
            idempotency_key=idempotency_key,
            request_value={"material_id": material_id},
            event_type="research.material.restored",
            event_payload={"deleted_at": None},
            update_sql="UPDATE materials SET deleted_at=NULL, operational_updated_at=? WHERE material_id=? AND project_id=?",
            update_values=lambda now: (now, material_id, project_id),
        )

    def _mutate_material(
        self,
        *,
        project_id: str,
        material_id: str,
        operation: str,
        idempotency_key: str,
        request_value: Mapping[str, Any],
        event_type: str,
        event_payload: Mapping[str, Any],
        update_sql: str,
        update_values: Callable[[str], tuple[Any, ...]],
        now: str | None = None,
    ) -> dict[str, Any]:
        project_id = self._validate_project(project_id)
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        with self._lock:
            request_sha256, replay = self._mutation_replay(
                project_id=project_id,
                idempotency_key=idempotency_key,
                operation=operation,
                request_value=request_value,
            )
            if replay is not None:
                return replay
            row = self._connection.execute(
                "SELECT 1 FROM materials WHERE project_id=? AND material_id=?",
                (project_id, material_id),
            ).fetchone()
            if row is None:
                raise KeyError("material is unavailable")
            now = now or self._clock()
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(update_sql, update_values(now))
                self._connection.execute(
                    """INSERT INTO material_events(project_id,material_id,event_type,payload_json,occurred_at)
                       VALUES(?,?,?,?,?)""",
                    (project_id, material_id, event_type, _json(dict(event_payload)), now),
                )
                response = self.material_detail(project_id=project_id, material_id=material_id)
                self._record_mutation(
                    project_id=project_id,
                    idempotency_key=idempotency_key,
                    operation=operation,
                    request_sha256=request_sha256,
                    response=response,
                    occurred_at=now,
                )
                self._connection.commit()
                return response
            except Exception:
                self._connection.rollback()
                raise

    def create_material_version(
        self,
        *,
        project_id: str,
        material_id: str,
        source_material_id: str,
        idempotency_key: str,
        project_record_ref: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        project_id = self._validate_project(project_id)
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        request = {"material_id": material_id, "source_material_id": source_material_id}
        with self._lock:
            request_sha256, replay = self._mutation_replay(
                project_id=project_id, idempotency_key=idempotency_key,
                operation="version", request_value=request,
            )
            if replay is not None:
                return replay
            target = self._connection.execute(
                "SELECT * FROM materials WHERE project_id=? AND material_id=? AND deleted_at IS NULL",
                (project_id, material_id),
            ).fetchone()
            source = self._connection.execute(
                """SELECT r.* FROM materials m JOIN material_revisions r
                   ON r.revision_id=m.current_revision_id
                   WHERE m.project_id=? AND m.material_id=? AND m.deleted_at IS NULL""",
                (project_id, source_material_id),
            ).fetchone()
            source_metadata = self._connection.execute(
                """SELECT rm.* FROM materials m
                   JOIN material_revision_metadata rm
                     ON rm.revision_id=m.current_revision_id
                   WHERE m.project_id=? AND m.material_id=?""",
                (project_id, source_material_id),
            ).fetchone()
            if target is None or source is None:
                raise KeyError("material is unavailable")
            if source_metadata is None:
                raise ValueError("material revision metadata is unavailable")
            if source["input_artifact_id"] == self._connection.execute(
                "SELECT input_artifact_id FROM material_revisions WHERE revision_id=?",
                (target["current_revision_id"],),
            ).fetchone()[0]:
                raise ValueError("material version content is unchanged")
            revision_number = self._connection.execute(
                "SELECT COALESCE(MAX(revision_number),0)+1 FROM material_revisions WHERE material_id=?",
                (material_id,),
            ).fetchone()[0]
            revision_id = str(uuid.uuid5(
                _REVISION_NAMESPACE,
                f"{material_id}:{revision_number}:{source['input_artifact_id']}",
            ))
            now = self._clock()
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """INSERT INTO material_revisions(revision_id,material_id,input_artifact_id,
                       revision_number,processing_status,scientific_status,receipt_ref_json,
                       supersedes_revision_id,created_at) VALUES(?,?,?,?,'pending_security',
                       'candidate_only',NULL,?,?)""",
                    (revision_id, material_id, source["input_artifact_id"], revision_number, target["current_revision_id"], now),
                )
                self._connection.execute(
                    """INSERT INTO material_revision_metadata(
                           revision_id,display_name,media_type,byte_size,source_kind)
                       VALUES(?,?,?,?,?)""",
                    (
                        revision_id, source_metadata["display_name"],
                        source_metadata["media_type"], source_metadata["byte_size"],
                        source_metadata["source_kind"],
                    ),
                )
                self._connection.execute(
                    """UPDATE materials SET current_revision_id=?,operational_updated_at=?,
                       project_record_ref_json=COALESCE(?,project_record_ref_json)
                       WHERE material_id=?""",
                    (
                        revision_id,
                        now,
                        _json(dict(project_record_ref))
                        if project_record_ref is not None
                        else None,
                        material_id,
                    ),
                )
                self._connection.execute(
                    """INSERT INTO material_events(project_id,material_id,event_type,payload_json,occurred_at)
                       VALUES(?,?,'research.material.version_created',?,?)""",
                    (project_id, material_id, _json({"revision_id": revision_id, "supersedes_revision_id": target["current_revision_id"]}), now),
                )
                response = self.material_detail(project_id=project_id, material_id=material_id)
                self._record_mutation(
                    project_id=project_id, idempotency_key=idempotency_key,
                    operation="version", request_sha256=request_sha256,
                    response=response, occurred_at=now,
                )
                self._connection.commit()
                return response
            except Exception:
                self._connection.rollback()
                raise

    def reuse_output_artifact(
        self,
        *,
        project_id: str,
        source: BinaryIO,
        expected_sha256: str,
        expected_bytes: int,
        detected_mime: str,
        filename: str,
        output_artifact_id: str,
        scope: str,
        idempotency_key: str,
        project_record_ref: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        project_id = self._validate_project(project_id)
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        if scope not in {"turn", "project"}:
            raise ValueError("material scope is invalid")
        request = {
            "expected_sha256": expected_sha256, "expected_bytes": expected_bytes,
            "detected_mime": detected_mime, "filename": filename,
            "output_artifact_id": output_artifact_id, "scope": scope,
        }
        with self._lock:
            request_sha256, replay = self._mutation_replay(
                project_id=project_id, idempotency_key=idempotency_key,
                operation="reuse_output", request_value=request,
            )
            if replay is not None:
                return replay
            artifact = self.input_artifacts.reuse_verified(
                research_id=project_id, source=source,
                expected_sha256=expected_sha256, expected_bytes=expected_bytes,
                detected_mime=detected_mime, original_filename=filename,
                output_artifact_id=output_artifact_id,
            )
            now = self._clock()
            material_id = str(uuid.uuid5(_REUSE_NAMESPACE, f"{project_id}:{idempotency_key}"))
            revision_id = str(uuid.uuid5(_REVISION_NAMESPACE, f"{material_id}:1:{expected_sha256}"))
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """INSERT INTO materials(material_id,project_id,current_revision_id,scope,
                       deleted_at,operational_updated_at,project_record_ref_json)
                       VALUES(?,?,?, ?,NULL,?,NULL)""",
                    (material_id, project_id, revision_id, scope, now),
                )
                self._connection.execute(
                    """INSERT INTO material_revisions(revision_id,material_id,input_artifact_id,
                       revision_number,processing_status,scientific_status,receipt_ref_json,
                       supersedes_revision_id,created_at)
                       VALUES(?,?,?,1,'pending_security','candidate_only',NULL,NULL,?)""",
                    (revision_id, material_id, expected_sha256, now),
                )
                self._connection.execute(
                    """INSERT INTO material_metadata(
                           material_id,display_name,media_type,byte_size,tags_json,directory,source_kind)
                       VALUES(?,?,?,?,?,'','project_reuse')""",
                    (material_id, filename, detected_mime, expected_bytes, "[]"),
                )
                self._connection.execute(
                    """INSERT INTO material_revision_metadata(
                           revision_id,display_name,media_type,byte_size,source_kind)
                       VALUES(?,?,?,?, 'project_reuse')""",
                    (revision_id, filename, detected_mime, expected_bytes),
                )
                if project_record_ref is not None:
                    self._connection.execute(
                        "UPDATE materials SET project_record_ref_json=? WHERE material_id=?",
                        (_json(dict(project_record_ref)), material_id),
                    )
                self._connection.execute(
                    """INSERT INTO material_events(project_id,material_id,event_type,payload_json,occurred_at)
                       VALUES(?,?,'research.material.output_reused',?,?)""",
                    (project_id, material_id, _json({"output_artifact_id": output_artifact_id, "revision_id": revision_id}), now),
                )
                response = {
                    "artifact": artifact,
                    "material": self.material_detail(project_id=project_id, material_id=material_id)["material"],
                    "revision": dict(self._connection.execute(
                        "SELECT * FROM material_revisions WHERE revision_id=?", (revision_id,)
                    ).fetchone()),
                }
                self._record_mutation(
                    project_id=project_id, idempotency_key=idempotency_key,
                    operation="reuse_output", request_sha256=request_sha256,
                    response=response, occurred_at=now,
                )
                self._connection.commit()
                return response
            except Exception:
                self._connection.rollback()
                raise

    @staticmethod
    def _remote_intake_request(
        *,
        source_identity_sha256: str,
        role: str,
        filename: str,
        expected_media_prefix: str,
    ) -> dict[str, str]:
        if _SHA256.fullmatch(source_identity_sha256) is None:
            raise ValueError("remote source identity is invalid")
        if role not in {"image_url", "input_audio", "file"}:
            raise ValueError("remote material role is invalid")
        if not isinstance(filename, str) or not filename:
            raise ValueError("remote material filename is invalid")
        if expected_media_prefix not in {"", "image/", "audio/"}:
            raise ValueError("remote material media policy is invalid")
        return {
            "source_identity_sha256": source_identity_sha256,
            "role": role,
            "filename": filename,
            "expected_media_prefix": expected_media_prefix,
        }

    @contextmanager
    def remote_intake_lock(self, *, project_id: str, idempotency_key: str):
        """Serialize replay/fetch/publish for one remote intake across processes."""

        project_id = self._validate_project(project_id)
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        identity = hashlib.sha256(
            f"{project_id}\0{idempotency_key}".encode("utf-8")
        ).hexdigest()
        lock_path = self.remote_intake_locks_root / f"{identity}.lock"
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise ValueError("remote material intake lock is unsafe")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def replay_remote_intake(
        self,
        *,
        project_id: str,
        idempotency_key: str,
        url_sha256: str,
        role: str,
        filename: str,
        expected_media_prefix: str,
        source_identity_sha256: str | None = None,
    ) -> dict[str, Any] | None:
        project_id = self._validate_project(project_id)
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        request = self._remote_intake_request(
            source_identity_sha256=source_identity_sha256 or url_sha256,
            role=role,
            filename=filename,
            expected_media_prefix=expected_media_prefix,
        )
        with self._lock:
            _, replay = self._mutation_replay(
                project_id=project_id,
                idempotency_key=idempotency_key,
                operation="remote_url_intake",
                request_value=request,
            )
            return replay

    def replay_remote_fetch(
        self,
        *,
        project_id: str,
        idempotency_key: str,
        url_sha256: str,
        role: str,
        filename: str,
        expected_media_prefix: str,
        source_identity_sha256: str | None = None,
    ) -> dict[str, Any] | None:
        """Resolve a durable post-network artifact checkpoint without refetching."""

        project_id = self._validate_project(project_id)
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        request = self._remote_intake_request(
            source_identity_sha256=source_identity_sha256 or url_sha256,
            role=role,
            filename=filename,
            expected_media_prefix=expected_media_prefix,
        )
        request_sha256 = hashlib.sha256(_json(request).encode()).hexdigest()
        intake_identity_sha256 = hashlib.sha256(
            f"{project_id}\0{idempotency_key}\0{request_sha256}".encode("utf-8")
        ).hexdigest()
        with self._lock:
            row = self._connection.execute(
                """SELECT * FROM remote_intake_fetches
                   WHERE project_id=? AND idempotency_key=?""",
                (project_id, idempotency_key),
            ).fetchone()
            if row is None:
                recovered = self.input_artifacts.find_by_intake_identity(
                    research_id=project_id,
                    intake_identity_sha256=intake_identity_sha256,
                )
                if recovered is None:
                    return None
                artifact = recovered["artifact"]
                lineage = recovered["lineage"]
                if (
                    lineage.get("source_kind") != "qingxiaoda_url"
                    or lineage.get("original_filename") != filename
                ):
                    raise ValueError("remote fetch lineage drifted")
                self._connection.execute("BEGIN IMMEDIATE")
                try:
                    existing = self._connection.execute(
                        """SELECT * FROM remote_intake_fetches
                           WHERE project_id=? AND idempotency_key=?""",
                        (project_id, idempotency_key),
                    ).fetchone()
                    if existing is None:
                        self._connection.execute(
                            """INSERT INTO remote_intake_fetches(
                                   project_id,idempotency_key,request_sha256,artifact_id,
                                   byte_size,detected_mime,created_at)
                               VALUES(?,?,?,?,?,?,?)""",
                            (
                                project_id,
                                idempotency_key,
                                request_sha256,
                                artifact["artifact_id"],
                                artifact["byte_size"],
                                artifact["detected_media_type"],
                                self._clock(),
                            ),
                        )
                        self._connection.commit()
                        return artifact
                    self._connection.rollback()
                    row = existing
                except Exception:
                    if self._connection.in_transaction:
                        self._connection.rollback()
                    raise
            if row["request_sha256"] != request_sha256:
                raise ValueError(
                    "remote fetch idempotency key is bound to a different request"
                )
            artifact = self.input_artifacts.describe(
                research_id=project_id,
                artifact_id=row["artifact_id"],
            )
            if (
                artifact["byte_size"] != row["byte_size"]
                or artifact["detected_media_type"] != row["detected_mime"]
            ):
                raise ValueError("remote fetch checkpoint drifted")
            return artifact

    def stage_remote_fetch(
        self,
        *,
        project_id: str,
        idempotency_key: str,
        source_url: str,
        url_sha256: str,
        role: str,
        filename: str,
        expected_media_prefix: str,
        source: BinaryIO,
        expected_sha256: str,
        expected_bytes: int,
        detected_mime: str,
        source_identity_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Publish the fetched bytes before the material identity transaction."""

        project_id = self._validate_project(project_id)
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        request = self._remote_intake_request(
            source_identity_sha256=source_identity_sha256 or url_sha256,
            role=role,
            filename=filename,
            expected_media_prefix=expected_media_prefix,
        )
        if hashlib.sha256(source_url.encode("utf-8")).hexdigest() != url_sha256:
            raise ValueError("remote URL identity drifted")
        replay = self.replay_remote_fetch(
            project_id=project_id,
            idempotency_key=idempotency_key,
            url_sha256=url_sha256,
            role=role,
            filename=filename,
            expected_media_prefix=expected_media_prefix,
            source_identity_sha256=source_identity_sha256,
        )
        if replay is not None:
            return replay
        parsed_source_url = urlsplit(source_url)
        redacted_source_url = urlunsplit(
            (
                parsed_source_url.scheme,
                parsed_source_url.netloc,
                parsed_source_url.path,
                "",
                "",
            )
        )
        artifact = self.input_artifacts.put_verified(
            research_id=project_id,
            source=source,
            expected_sha256=expected_sha256,
            expected_bytes=expected_bytes,
            detected_mime=detected_mime,
            original_filename=filename,
            source_kind="qingxiaoda_url",
            source_locator={"url": redacted_source_url},
            source_identity_sha256=source_identity_sha256 or url_sha256,
            intake_identity_sha256=hashlib.sha256(
                f"{project_id}\0{idempotency_key}\0{hashlib.sha256(_json(request).encode()).hexdigest()}".encode("utf-8")
            ).hexdigest(),
        )
        request_sha256 = hashlib.sha256(_json(request).encode()).hexdigest()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._connection.execute(
                    """SELECT * FROM remote_intake_fetches
                       WHERE project_id=? AND idempotency_key=?""",
                    (project_id, idempotency_key),
                ).fetchone()
                if existing is None:
                    self._connection.execute(
                        """INSERT INTO remote_intake_fetches(
                               project_id,idempotency_key,request_sha256,artifact_id,
                               byte_size,detected_mime,created_at)
                           VALUES(?,?,?,?,?,?,?)""",
                        (
                            project_id,
                            idempotency_key,
                            request_sha256,
                            artifact["artifact_id"],
                            artifact["byte_size"],
                            artifact["detected_media_type"],
                            self._clock(),
                        ),
                    )
                elif (
                    existing["request_sha256"] != request_sha256
                    or existing["artifact_id"] != artifact["artifact_id"]
                    or existing["byte_size"] != artifact["byte_size"]
                    or existing["detected_mime"]
                    != artifact["detected_media_type"]
                ):
                    raise ValueError("remote fetch checkpoint drifted")
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return artifact

    def ingest_remote_artifact(
        self,
        *,
        project_id: str,
        idempotency_key: str,
        url_sha256: str,
        role: str,
        filename: str,
        expected_media_prefix: str,
        scope: str = "turn",
        source_identity_sha256: str | None = None,
    ) -> dict[str, Any]:
        project_id = self._validate_project(project_id)
        idempotency_key = self._validate_idempotency_key(idempotency_key)
        if scope not in {"turn", "project"}:
            raise ValueError("material scope is invalid")
        request = self._remote_intake_request(
            source_identity_sha256=source_identity_sha256 or url_sha256,
            role=role,
            filename=filename,
            expected_media_prefix=expected_media_prefix,
        )
        with self._lock:
            request_sha256, replay = self._mutation_replay(
                project_id=project_id,
                idempotency_key=idempotency_key,
                operation="remote_url_intake",
                request_value=request,
            )
            if replay is not None:
                return replay
            artifact = self.replay_remote_fetch(
                project_id=project_id,
                idempotency_key=idempotency_key,
                url_sha256=url_sha256,
                role=role,
                filename=filename,
                expected_media_prefix=expected_media_prefix,
                source_identity_sha256=source_identity_sha256,
            )
            if artifact is None:
                raise ValueError("remote fetch checkpoint is unavailable")
            expected_sha256 = artifact["artifact_id"]
            expected_bytes = artifact["byte_size"]
            detected_mime = artifact["detected_media_type"]
            intake_identity_sha256 = hashlib.sha256(
                f"{project_id}\0{idempotency_key}\0{request_sha256}".encode("utf-8")
            ).hexdigest()
            now = self._clock()
            material_id = str(
                uuid.uuid5(_REMOTE_NAMESPACE, f"{project_id}:{idempotency_key}")
            )
            revision_id = str(
                uuid.uuid5(
                    _REVISION_NAMESPACE,
                    f"{material_id}:1:{expected_sha256}",
                )
            )
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """INSERT INTO materials(material_id,project_id,current_revision_id,scope,
                       deleted_at,operational_updated_at,project_record_ref_json)
                       VALUES(?,?,?, ?,NULL,?,NULL)""",
                    (material_id, project_id, revision_id, scope, now),
                )
                self._connection.execute(
                    """INSERT INTO material_revisions(revision_id,material_id,input_artifact_id,
                       revision_number,processing_status,scientific_status,receipt_ref_json,
                       supersedes_revision_id,created_at)
                       VALUES(?,?,?,1,'pending_security','candidate_only',NULL,NULL,?)""",
                    (revision_id, material_id, expected_sha256, now),
                )
                self._connection.execute(
                    """INSERT INTO material_metadata(
                           material_id,display_name,media_type,byte_size,tags_json,directory,source_kind)
                       VALUES(?,?,?,?,?,'','qingxiaoda_url')""",
                    (material_id, filename, detected_mime, expected_bytes, "[]"),
                )
                self._connection.execute(
                    """INSERT INTO material_revision_metadata(
                           revision_id,display_name,media_type,byte_size,source_kind)
                       VALUES(?,?,?,?, 'qingxiaoda_url')""",
                    (revision_id, filename, detected_mime, expected_bytes),
                )
                self._connection.execute(
                    """INSERT INTO material_events(project_id,material_id,event_type,payload_json,occurred_at)
                       VALUES(?,?,'research.material.url_normalized',?,?)""",
                    (
                        project_id,
                        material_id,
                        _json(
                            {
                                "artifact_id": expected_sha256,
                                "revision_id": revision_id,
                                "source_identity_sha256": (
                                    source_identity_sha256 or url_sha256
                                ),
                                "intake_identity_sha256": intake_identity_sha256,
                                "original_filename": filename,
                                "role": role,
                            }
                        ),
                        now,
                    ),
                )
                response = {
                    "artifact": {
                        key: artifact[key]
                        for key in (
                            "contract",
                            "artifact_id",
                            "project_id",
                            "sha256",
                            "byte_size",
                            "detected_media_type",
                            "security_status",
                        )
                    },
                    "material_id": material_id,
                    "revision_id": revision_id,
                    "processing_status": "pending_security",
                }
                self._record_mutation(
                    project_id=project_id,
                    idempotency_key=idempotency_key,
                    operation="remote_url_intake",
                    request_sha256=request_sha256,
                    response=response,
                    occurred_at=now,
                )
                self._connection.commit()
                return response
            except Exception:
                self._connection.rollback()
                raise

    @staticmethod
    def _type_family(media_type: str | None) -> str:
        value = media_type or "application/octet-stream"
        if value.startswith("image/"):
            return "image"
        if value.startswith("audio/"):
            return "audio"
        if value in {"text/csv", "text/tab-separated-values"} or "spreadsheet" in value or "excel" in value:
            return "table"
        if value in {"application/zip", "application/octet-stream"}:
            return "package"
        return "document"

    @staticmethod
    def _revision_public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "revision_id": row["revision_id"],
            "input_artifact_id": row["input_artifact_id"],
            "revision_number": row["revision_number"],
            "processing_status": row["processing_status"],
            "scientific_status": row["scientific_status"],
            "created_at": row["created_at"],
            "supersedes_revision_id": row["supersedes_revision_id"],
        }

    def _library_row(self, row: sqlite3.Row) -> dict[str, Any]:
        upload_to_ready_ms: int | None = None
        if row["upload_created_at"] is not None and row["processing_ready_at"] is not None:
            elapsed = _parse_time(row["processing_ready_at"]) - _parse_time(
                row["upload_created_at"]
            )
            upload_to_ready_ms = max(0, round(elapsed.total_seconds() * 1000))
        return {
            "material_id": row["material_id"],
            "project_id": row["project_id"],
            "display_name": row["filename"] or row["material_id"],
            "scope": row["scope"],
            "deleted_at": row["deleted_at"],
            "transfer_status": row["transfer_status"] or "uploaded",
            "type_family": self._type_family(row["declared_mime"]),
            "media_type": row["declared_mime"] or "application/octet-stream",
            "byte_size": row["total_bytes"],
            "tags": json.loads(row["tags_json"] or "[]"),
            "directory": row["directory"] or "",
            "source_kind": row["source_kind"] or "local_upload",
            "current_revision": {
                "revision_id": row["revision_id"],
                "input_artifact_id": row["input_artifact_id"],
                "revision_number": row["revision_number"],
                "processing_status": row["processing_status"],
                "scientific_status": row["scientific_status"],
                "created_at": row["revision_created_at"],
                "upload_to_ready_ms": upload_to_ready_ms,
            },
        }

    def get_revision(self, revision_id: str) -> dict[str, Any]:
        """Return one operational revision without promoting its candidate content."""

        with self._lock:
            row = self._connection.execute(
                """SELECT r.*, m.project_id FROM material_revisions r
                   JOIN materials m ON m.material_id = r.material_id
                   WHERE r.revision_id = ? AND m.deleted_at IS NULL""",
                (revision_id,),
            ).fetchone()
            if row is None:
                raise KeyError("material revision is unavailable")
            return dict(row)

    def claim_revision(self, *, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        """Lease one pending/queued revision, recovering an expired worker claim."""

        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("material worker_id is invalid")
        if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or lease_seconds <= 0:
            raise ValueError("material lease_seconds is invalid")
        with self._lock:
            now = self._clock()
            leased_until = _plus_seconds(now, lease_seconds)
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """UPDATE material_revisions SET processing_status='queued'
                       WHERE revision_id IN (
                           SELECT revision_id FROM processing_claims WHERE leased_until <= ?
                       ) AND processing_status IN ('inspecting', 'parsing')""",
                    (now,),
                )
                expired_attempts = {
                    row["revision_id"]: row["attempt"]
                    for row in self._connection.execute(
                        "SELECT revision_id, attempt FROM processing_claims WHERE leased_until <= ?",
                        (now,),
                    ).fetchall()
                }
                self._connection.execute(
                    """DELETE FROM processing_claims WHERE leased_until <= ?""",
                    (now,),
                )
                for revision_id, attempt in expired_attempts.items():
                    self._connection.execute(
                        """INSERT INTO processing_attempts(revision_id, attempt, last_failure_category)
                           VALUES (?, ?, 'worker_lease_expired')
                           ON CONFLICT(revision_id) DO UPDATE SET
                             attempt=MAX(processing_attempts.attempt, excluded.attempt),
                             last_failure_category=excluded.last_failure_category""",
                        (revision_id, attempt),
                    )
                exhausted = self._connection.execute(
                    """SELECT r.revision_id, r.material_id, m.project_id, a.attempt
                       FROM material_revisions r
                       JOIN materials m ON m.material_id=r.material_id
                       JOIN processing_attempts a ON a.revision_id=r.revision_id
                       WHERE r.processing_status='queued' AND a.attempt >= 3"""
                ).fetchall()
                for exhausted_row in exhausted:
                    self._connection.execute(
                        "UPDATE material_revisions SET processing_status='failed' WHERE revision_id=?",
                        (exhausted_row["revision_id"],),
                    )
                    self._connection.execute(
                        """INSERT INTO material_events(project_id, material_id, event_type, payload_json, occurred_at)
                           VALUES (?, ?, 'research.material.processing_failed', ?, ?)""",
                        (
                            exhausted_row["project_id"],
                            exhausted_row["material_id"],
                            _json({
                                "revision_id": exhausted_row["revision_id"],
                                "attempt": exhausted_row["attempt"],
                                "failure_category": "retry_exhausted",
                                "candidate_only": True,
                            }),
                            now,
                        ),
                    )
                row = self._connection.execute(
                    """SELECT r.*, m.project_id FROM material_revisions r
                       JOIN materials m ON m.material_id = r.material_id
                       LEFT JOIN processing_claims c ON c.revision_id = r.revision_id
                       WHERE c.revision_id IS NULL AND m.deleted_at IS NULL
                         AND r.processing_status IN ('pending_security', 'queued')
                       ORDER BY r.created_at, r.revision_id LIMIT 1"""
                ).fetchone()
                if row is None:
                    self._connection.commit()
                    return None
                prior = self._connection.execute(
                    "SELECT attempt FROM processing_attempts WHERE revision_id=?",
                    (row["revision_id"],),
                ).fetchone()
                attempt = int(prior["attempt"] if prior is not None else 0) + 1
                self._connection.execute(
                    """INSERT INTO processing_attempts(revision_id, attempt, last_failure_category)
                       VALUES (?, ?, NULL)
                       ON CONFLICT(revision_id) DO UPDATE SET
                         attempt=excluded.attempt, last_failure_category=NULL""",
                    (row["revision_id"], attempt),
                )
                self._connection.execute(
                    "INSERT INTO processing_claims(revision_id, worker_id, leased_until, attempt) VALUES (?, ?, ?, ?)",
                    (row["revision_id"], worker_id, leased_until, attempt),
                )
                self._connection.execute(
                    "UPDATE material_revisions SET processing_status='inspecting' WHERE revision_id=?",
                    (row["revision_id"],),
                )
                self._connection.commit()
                result = dict(row)
                result.update({"worker_id": worker_id, "leased_until": leased_until, "attempt": attempt, "processing_status": "inspecting"})
                return result
            except Exception:
                self._connection.rollback()
                raise

    def mark_revision_parsing(self, *, revision_id: str, worker_id: str) -> None:
        with self._lock:
            updated = self._connection.execute(
                """UPDATE material_revisions SET processing_status='parsing'
                   WHERE revision_id=? AND EXISTS(
                       SELECT 1 FROM processing_claims c
                       WHERE c.revision_id=? AND c.worker_id=?
                   )""",
                (revision_id, revision_id, worker_id),
            ).rowcount
            if updated != 1:
                raise PermissionError("material worker does not own revision")

    def owned_revision_claim(self, *, revision_id: str, worker_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                """SELECT r.*, m.project_id, c.worker_id, c.leased_until, c.attempt
                   FROM material_revisions r
                   JOIN materials m ON m.material_id=r.material_id
                   JOIN processing_claims c ON c.revision_id=r.revision_id
                   WHERE r.revision_id=? AND c.worker_id=?""",
                (revision_id, worker_id),
            ).fetchone()
            if row is None:
                raise PermissionError("material worker does not own revision")
            return dict(row)

    def finish_revision_processing(
        self,
        *,
        revision_id: str,
        worker_id: str,
        status: str,
        receipt_ref: Mapping[str, str],
        extraction_refs: Sequence[Mapping[str, str]],
        lca_package_route: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically bind candidate processing outputs and the terminal event."""

        if status not in {"ready", "partial", "needs_user", "unsupported", "failed"}:
            raise ValueError("material processing terminal status is invalid")
        receipt_json = _json(dict(receipt_ref))
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                claim = self._connection.execute(
                    "SELECT * FROM processing_claims WHERE revision_id=? AND worker_id=?",
                    (revision_id, worker_id),
                ).fetchone()
                if claim is None:
                    raise PermissionError("material worker does not own revision")
                row = self._connection.execute(
                    """SELECT r.*, m.project_id FROM material_revisions r
                       JOIN materials m ON m.material_id=r.material_id WHERE r.revision_id=?""",
                    (revision_id,),
                ).fetchone()
                self._connection.execute(
                    "UPDATE material_revisions SET processing_status=?, receipt_ref_json=? WHERE revision_id=?",
                    (status, receipt_json, revision_id),
                )
                payload = {
                    "revision_id": revision_id,
                    "receipt_ref": dict(receipt_ref),
                    "extraction_refs": [dict(reference) for reference in extraction_refs],
                    "lca_package_route": (
                        dict(lca_package_route)
                        if lca_package_route is not None
                        else None
                    ),
                    "candidate_only": True,
                }
                self._connection.execute(
                    "INSERT INTO material_events(project_id, material_id, event_type, payload_json, occurred_at) VALUES (?, ?, ?, ?, ?)",
                    (row["project_id"], row["material_id"], f"research.material.processing_{status}", _json(payload), self._clock()),
                )
                self._connection.execute("DELETE FROM processing_claims WHERE revision_id=?", (revision_id,))
                self._connection.commit()
                return self.get_revision(revision_id)
            except Exception:
                self._connection.rollback()
                raise

    def fail_revision_processing(
        self,
        *,
        revision_id: str,
        worker_id: str,
        failure_category: str,
        detail: str,
    ) -> None:
        """Persist a categorized terminal worker failure without source contents."""

        if not failure_category or not detail:
            raise ValueError("material failure classification is required")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """SELECT r.material_id, m.project_id
                       FROM material_revisions r
                       JOIN materials m ON m.material_id=r.material_id
                       JOIN processing_claims c ON c.revision_id=r.revision_id
                       WHERE r.revision_id=? AND c.worker_id=?""",
                    (revision_id, worker_id),
                ).fetchone()
                if row is None:
                    raise PermissionError("material worker does not own revision")
                self._connection.execute(
                    "UPDATE material_revisions SET processing_status='failed' WHERE revision_id=?",
                    (revision_id,),
                )
                self._connection.execute(
                    """INSERT INTO material_events(project_id, material_id, event_type, payload_json, occurred_at)
                       VALUES (?, ?, 'research.material.processing_failed', ?, ?)""",
                    (
                        row["project_id"],
                        row["material_id"],
                        _json({
                            "revision_id": revision_id,
                            "failure_category": failure_category,
                            "detail": detail,
                            "candidate_only": True,
                        }),
                        self._clock(),
                    ),
                )
                self._connection.execute(
                    "DELETE FROM processing_claims WHERE revision_id=?",
                    (revision_id,),
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def retry_revision_processing(
        self,
        *,
        project_id: str,
        material_id: str,
        revision_id: str,
        decision_sha256: str,
    ) -> dict[str, Any]:
        """Requeue one exact terminal revision after an explicit user decision."""

        if not isinstance(decision_sha256, str) or _SHA256.fullmatch(
            decision_sha256
        ) is None:
            raise ValueError("material processing retry decision is invalid")

        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """SELECT r.*, m.project_id, m.deleted_at
                       FROM material_revisions r
                       JOIN materials m ON m.material_id=r.material_id
                       WHERE r.revision_id=? AND r.material_id=? AND m.project_id=?""",
                    (revision_id, material_id, project_id),
                ).fetchone()
                if row is None or row["deleted_at"] is not None:
                    raise KeyError("material revision is unavailable")
                prior = self._connection.execute(
                    """SELECT payload_json FROM material_events
                       WHERE project_id=? AND material_id=?
                         AND event_type='research.material.processing_retry_requested'
                       ORDER BY sequence""",
                    (project_id, material_id),
                ).fetchall()
                if any(
                    json.loads(event["payload_json"]).get("revision_id")
                    == revision_id
                    and json.loads(event["payload_json"]).get("decision_sha256")
                    == decision_sha256
                    for event in prior
                ):
                    self._connection.commit()
                    return {
                        **dict(row),
                        "retry_started": False,
                        "retry_decision_consumed": True,
                    }
                if row["processing_status"] in {
                    "pending_security",
                    "queued",
                    "inspecting",
                    "parsing",
                }:
                    self._connection.commit()
                    return {
                        **dict(row),
                        "retry_started": False,
                        "retry_decision_consumed": False,
                    }
                if row["processing_status"] != "failed":
                    raise ValueError("material revision is not retryable")
                self._connection.execute(
                    "DELETE FROM processing_claims WHERE revision_id=?",
                    (revision_id,),
                )
                self._connection.execute(
                    """INSERT INTO processing_attempts(
                           revision_id, attempt, last_failure_category
                       ) VALUES (?, 0, NULL)
                       ON CONFLICT(revision_id) DO UPDATE SET
                           attempt=0, last_failure_category=NULL""",
                    (revision_id,),
                )
                self._connection.execute(
                    """UPDATE material_revisions
                       SET processing_status='queued', receipt_ref_json=NULL
                       WHERE revision_id=?""",
                    (revision_id,),
                )
                self._connection.execute(
                    """INSERT INTO material_events(
                           project_id, material_id, event_type, payload_json, occurred_at
                       ) VALUES (?, ?, 'research.material.processing_retry_requested', ?, ?)""",
                    (
                        project_id,
                        material_id,
                        _json(
                            {
                                "revision_id": revision_id,
                                "prior_processing_status": "failed",
                                "processing_status": "queued",
                                "decision_sha256": decision_sha256,
                                "candidate_only": True,
                            }
                        ),
                        self._clock(),
                    ),
                )
                self._connection.commit()
                return {
                    **self.get_revision(revision_id),
                    "retry_started": True,
                    "retry_decision_consumed": True,
                }
            except Exception:
                self._connection.rollback()
                raise

    def append_material_event(
        self, *, project_id: str, material_id: str, event_type: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        with self._lock:
            cursor = self._connection.execute(
                "INSERT INTO material_events(project_id, material_id, event_type, payload_json, occurred_at) VALUES (?, ?, ?, ?, ?)",
                (self._validate_project(project_id), material_id, event_type, _json(dict(payload)), self._clock()),
            )
            return {"sequence": cursor.lastrowid, "event_type": event_type}

    def material_events(
        self,
        *,
        project_id: str,
        after_sequence: int = 0,
        limit: int = 512,
    ) -> list[dict[str, Any]]:
        if not isinstance(after_sequence, int) or isinstance(after_sequence, bool) or after_sequence < 0:
            raise ValueError("material event cursor is invalid")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 512:
            raise ValueError("material event limit is invalid")
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM material_events WHERE project_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                (self._validate_project(project_id), after_sequence, limit),
            ).fetchall()
            result = []
            for row in rows:
                value = dict(row); value["payload"] = json.loads(value.pop("payload_json")); result.append(value)
            return result

    def remote_source_identity(
        self, *, project_id: str, material_id: str, revision_id: str
    ) -> str:
        """Resolve the exact remote source identity bound to one material revision."""

        return self.remote_source_lineage_identity(
            project_id=project_id,
            material_id=material_id,
            revision_id=revision_id,
        )["source_identity_sha256"]

    def remote_source_lineage_identity(
        self, *, project_id: str, material_id: str, revision_id: str
    ) -> dict[str, str | None]:
        """Resolve the immutable source and intake identities for one revision."""

        project_id = self._validate_project(project_id)
        with self._lock:
            rows = self._connection.execute(
                """SELECT payload_json FROM material_events
                   WHERE project_id=? AND material_id=?
                     AND event_type='research.material.url_normalized'
                   ORDER BY sequence""",
                (project_id, material_id),
            ).fetchall()
        matches: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            if payload.get("revision_id") == revision_id:
                matches.append(payload)
        if (
            len(matches) != 1
            or not isinstance(matches[0].get("source_identity_sha256"), str)
            or _SHA256.fullmatch(matches[0]["source_identity_sha256"]) is None
        ):
            raise ValueError("remote material source identity is unavailable")
        intake_identity = matches[0].get("intake_identity_sha256")
        if intake_identity is not None and (
            not isinstance(intake_identity, str)
            or _SHA256.fullmatch(intake_identity) is None
        ):
            raise ValueError("remote material intake identity is invalid")
        original_filename = matches[0].get("original_filename")
        if original_filename is not None and (
            not isinstance(original_filename, str) or not original_filename
        ):
            raise ValueError("remote material filename identity is invalid")
        return {
            "source_identity_sha256": matches[0]["source_identity_sha256"],
            "intake_identity_sha256": intake_identity,
            "original_filename": original_filename,
        }

    def revision_source_kind(
        self, *, project_id: str, material_id: str, revision_id: str
    ) -> str:
        """Return the immutable ingress kind for one project-owned revision."""

        project_id = self._validate_project(project_id)
        with self._lock:
            row = self._connection.execute(
                """SELECT rm.source_kind FROM material_revision_metadata rm
                   JOIN material_revisions r ON r.revision_id=rm.revision_id
                   JOIN materials m ON m.material_id=r.material_id
                   WHERE m.project_id=? AND m.material_id=? AND r.revision_id=?""",
                (project_id, material_id, revision_id),
            ).fetchone()
        if row is None or row["source_kind"] not in {
            "local_upload", "qingxiaoda_url", "project_reuse"
        }:
            raise ValueError("material revision source kind is unavailable")
        return str(row["source_kind"])
