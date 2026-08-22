import hashlib
import io
import sqlite3
import tempfile
import unittest
import threading
import os
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 8, 12, tzinfo=timezone.utc)

    def __call__(self):
        return self.value.isoformat().replace("+00:00", "Z")


class MaterialStoreTests(unittest.TestCase):
    def setUp(self):
        from lca_research_service.input_artifacts import InputArtifactStore
        from lca_research_service.material_store import MaterialStore

        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.clock = MutableClock()
        self.artifacts = InputArtifactStore(self.root, clock=self.clock)
        self.store = MaterialStore(
            self.root / "control/materials.sqlite3",
            input_artifacts=self.artifacts,
            data_root=self.root,
            clock=self.clock,
            policy={
                "generic_file_max_bytes": 32,
                "part_bytes": 4,
                "batch_file_count": 2,
                "batch_max_bytes": 24,
                "project_max_bytes": 20,
                "session_seconds": 60,
            },
        )

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def _upload(self, payload=b"abcdefgh", filename="../unsafe.txt"):
        digest = hashlib.sha256(payload).hexdigest()
        session = self.store.create_upload(
            project_id="project-a",
            filename=filename,
            declared_mime="text/plain",
            total_bytes=len(payload),
            expected_sha256=digest,
        )
        return payload, digest, session

    def test_out_of_order_parts_duplicates_and_idempotent_completion(self):
        payload, digest, session = self._upload()
        upload_id = session["upload_id"]
        second = self.store.receive_part(
            project_id="project-a", upload_id=upload_id, part_number=1,
            first_byte=4, last_byte=7, total_bytes=8,
            source=io.BytesIO(payload[4:]), sha256=hashlib.sha256(payload[4:]).hexdigest(),
        )
        first = self.store.receive_part(
            project_id="project-a", upload_id=upload_id, part_number=0,
            first_byte=0, last_byte=3, total_bytes=8,
            source=io.BytesIO(payload[:4]), sha256=hashlib.sha256(payload[:4]).hexdigest(),
        )
        self.assertEqual(second["part_number"], 1)
        self.assertEqual(first, self.store.receive_part(
            project_id="project-a", upload_id=upload_id, part_number=0,
            first_byte=0, last_byte=3, total_bytes=8,
            source=io.BytesIO(payload[:4]), sha256=hashlib.sha256(payload[:4]).hexdigest(),
        ))
        with self.assertRaises(ValueError):
            self.store.receive_part(
                project_id="project-a", upload_id=upload_id, part_number=0,
                first_byte=0, last_byte=3, total_bytes=8,
                source=io.BytesIO(b"drft"), sha256=hashlib.sha256(b"drft").hexdigest(),
            )
        completed = self.store.complete_upload(project_id="project-a", upload_id=upload_id)
        self.assertEqual(completed, self.store.complete_upload(project_id="project-a", upload_id=upload_id))
        self.assertEqual(completed["artifact"]["sha256"], digest)
        self.assertEqual(completed["material"]["project_id"], "project-a")
        self.assertEqual(completed["material"]["display_name"], "../unsafe.txt")
        self.assertEqual(completed["artifact"]["original_filename"], "unsafe.txt")
        self.assertEqual(completed["revision"]["processing_status"], "pending_security")
        self.assertEqual(completed["revision"]["scientific_status"], "candidate_only")

    def test_completion_scope_is_not_transfer_session_state(self):
        payload = b"four"
        digest = hashlib.sha256(payload).hexdigest()
        session = self.store.create_upload(
            project_id="project-a", filename="turn.txt", declared_mime="text/plain",
            total_bytes=4, expected_sha256=digest,
        )
        self.store.receive_part(
            project_id="project-a", upload_id=session["upload_id"], part_number=0,
            first_byte=0, last_byte=3, total_bytes=4, source=io.BytesIO(payload),
            sha256=digest,
        )
        completed = self.store.complete_upload(
            project_id="project-a", upload_id=session["upload_id"], scope="project"
        )
        self.assertEqual(completed["material"]["scope"], "project")

    def test_part_integrity_missing_parts_whole_hash_and_range(self):
        payload, _, session = self._upload()
        upload_id = session["upload_id"]
        with self.assertRaises(ValueError):
            self.store.receive_part(
                project_id="project-a", upload_id=upload_id, part_number=0,
                first_byte=1, last_byte=4, total_bytes=8,
                source=io.BytesIO(payload[:4]), sha256=hashlib.sha256(payload[:4]).hexdigest(),
            )
        with self.assertRaises(ValueError):
            self.store.receive_part(
                project_id="project-a", upload_id=upload_id, part_number=0,
                first_byte=0, last_byte=3, total_bytes=8,
                source=io.BytesIO(payload[:4]), sha256="0" * 64,
            )
        self.store.receive_part(
            project_id="project-a", upload_id=upload_id, part_number=0,
            first_byte=0, last_byte=3, total_bytes=8,
            source=io.BytesIO(payload[:4]), sha256=hashlib.sha256(payload[:4]).hexdigest(),
        )
        with self.assertRaises(ValueError):
            self.store.complete_upload(project_id="project-a", upload_id=upload_id)

        bad = self.store.create_upload(
            project_id="project-a", filename="bad.txt", declared_mime="text/plain",
            total_bytes=4, expected_sha256="0" * 64,
        )
        self.store.receive_part(
            project_id="project-a", upload_id=bad["upload_id"], part_number=0,
            first_byte=0, last_byte=3, total_bytes=4, source=io.BytesIO(b"real"),
            sha256=hashlib.sha256(b"real").hexdigest(),
        )
        with self.assertRaises(ValueError):
            self.store.complete_upload(project_id="project-a", upload_id=bad["upload_id"])

    def test_cancel_expiry_cross_project_capacity_and_restart(self):
        _, _, first = self._upload(payload=b"1234", filename="first.txt")
        with self.assertRaises(KeyError):
            self.store.get_upload(project_id="project-b", upload_id=first["upload_id"])
        cancelled = self.store.cancel_upload(project_id="project-a", upload_id=first["upload_id"])
        self.assertEqual(cancelled["transfer_status"], "cancelled")
        self.assertFalse((self.root / "control/material-upload-parts" / first["upload_id"]).exists())

        _, _, expiring = self._upload(payload=b"5678", filename="second.txt")
        self.clock.value += timedelta(seconds=61)
        with self.assertRaises(ValueError):
            self.store.get_upload(project_id="project-a", upload_id=expiring["upload_id"])

        from lca_research_service.material_store import MaterialStore
        self.store.close()
        self.store = MaterialStore(
            self.root / "control/materials.sqlite3", input_artifacts=self.artifacts,
            data_root=self.root, clock=self.clock,
            policy={"generic_file_max_bytes": 32, "part_bytes": 4,
                    "batch_file_count": 2, "batch_max_bytes": 24,
                    "project_max_bytes": 20, "session_seconds": 60},
        )
        self.assertEqual(
            self.store.get_upload(project_id="project-a", upload_id=first["upload_id"], include_terminal=True)["transfer_status"],
            "cancelled",
        )
        with self.assertRaises(ValueError):
            self.store.create_upload(project_id="project-a", filename="large", declared_mime="text/plain", total_bytes=33)
        with self.assertRaises(ValueError):
            self.store.create_upload_batch(project_id="project-a", uploads=[
                {"filename": "a", "declared_mime": "text/plain", "total_bytes": 13},
                {"filename": "b", "declared_mime": "text/plain", "total_bytes": 13},
            ])

    def test_schema_wal_foreign_keys_and_part_symlink_escape(self):
        with sqlite3.connect(self.store.path) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            columns = [row[1] for row in connection.execute("PRAGMA table_info(upload_sessions)")]
        self.assertTrue({"upload_sessions", "upload_parts", "materials", "material_revisions", "material_events", "processing_claims"} <= tables)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{self.store.path}{suffix}")
            if sidecar.exists():
                self.assertEqual(sidecar.stat().st_mode & 0o777, 0o600)
        self.assertEqual(columns, ["upload_id", "project_id", "filename", "declared_mime", "total_bytes", "expected_sha256", "part_size", "transfer_status", "created_at", "expires_at", "completed_artifact_id", "completed_material_id"])

        payload, _, session = self._upload(payload=b"four")
        session_root = self.root / "control/material-upload-parts" / session["upload_id"]
        session_root.parent.mkdir(parents=True, exist_ok=True)
        outside = self.root / "outside"
        outside.mkdir()
        session_root.symlink_to(outside, target_is_directory=True)
        with self.assertRaises((OSError, ValueError)):
            self.store.receive_part(
                project_id="project-a", upload_id=session["upload_id"], part_number=0,
                first_byte=0, last_byte=3, total_bytes=4, source=io.BytesIO(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            )

    def test_project_quota_counts_active_uploads(self):
        self.store.create_upload(project_id="project-a", filename="a", declared_mime="text/plain", total_bytes=12)
        with self.assertRaises(ValueError):
            self.store.create_upload(project_id="project-a", filename="b", declared_mime="text/plain", total_bytes=9)

    def test_database_symlink_is_rejected_before_sqlite_open(self):
        from lca_research_service.material_store import MaterialStore
        other_root = self.root / "db-symlink"
        other_root.mkdir()
        artifacts = type(self.artifacts)(other_root)
        control = other_root / "control"
        control.mkdir(exist_ok=True)
        real = control / "real.sqlite3"
        real.touch()
        link = control / "link.sqlite3"
        link.symlink_to(real)
        with self.assertRaises(ValueError):
            MaterialStore(link, input_artifacts=artifacts, data_root=other_root)

    def test_two_connections_receive_same_part_without_deleting_winner(self):
        from lca_research_service.material_store import MaterialStore
        payload, _, session = self._upload(payload=b"four")
        other = MaterialStore(self.store.path, input_artifacts=self.artifacts, data_root=self.root, clock=self.clock, policy=self.store.policy)
        results, errors = [], []
        def receive(store):
            try:
                results.append(store.receive_part(project_id="project-a", upload_id=session["upload_id"], part_number=0, first_byte=0, last_byte=3, total_bytes=4, source=io.BytesIO(payload), sha256=hashlib.sha256(payload).hexdigest()))
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=receive, args=(store,)) for store in (self.store, other)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        other.close()
        self.assertFalse(errors)
        self.assertEqual(results[0], results[1])
        self.assertTrue((self.root / results[0]["path"]).is_file())

    def test_restart_reconciles_terminal_parts_and_unreferenced_orphan(self):
        payload, digest, session = self._upload(payload=b"four")
        receipt = self.store.receive_part(project_id="project-a", upload_id=session["upload_id"], part_number=0, first_byte=0, last_byte=3, total_bytes=4, source=io.BytesIO(payload), sha256=digest)
        self.store._connection.execute("UPDATE upload_sessions SET transfer_status = 'cancelled' WHERE upload_id = ?", (session["upload_id"],))
        orphan_dir = self.root / "projects/project-a/inputs/sha256" / digest[:2] / digest
        orphan_dir.mkdir(parents=True, exist_ok=True)
        (orphan_dir / "original").write_bytes(payload)
        (orphan_dir / "metadata.json").write_text("{}")
        from lca_research_service.material_store import MaterialStore
        self.store.close()
        self.store = MaterialStore(self.root / "control/materials.sqlite3", input_artifacts=self.artifacts, data_root=self.root, clock=self.clock, policy={"generic_file_max_bytes": 32, "part_bytes": 4, "batch_file_count": 2, "batch_max_bytes": 24, "project_max_bytes": 20, "session_seconds": 60})
        self.assertFalse((self.root / receipt["path"]).exists())
        self.assertTrue(orphan_dir.exists())

    def test_migrates_legacy_v1_scope_schema_preserving_upload_and_parts(self):
        from lca_research_service.material_store import MaterialStore
        legacy_root = self.root / "legacy"
        artifacts = type(self.artifacts)(legacy_root)
        db = legacy_root / "control/materials.sqlite3"
        db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db) as connection:
            connection.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE upload_sessions(upload_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, filename TEXT NOT NULL, declared_mime TEXT NOT NULL, total_bytes INTEGER NOT NULL, expected_sha256 TEXT, part_size INTEGER NOT NULL, transfer_status TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, completed_artifact_id TEXT, completed_material_id TEXT, scope TEXT NOT NULL);
            CREATE TABLE upload_parts(upload_id TEXT NOT NULL REFERENCES upload_sessions(upload_id) ON DELETE CASCADE, part_number INTEGER NOT NULL, first_byte INTEGER NOT NULL, last_byte INTEGER NOT NULL, bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, path TEXT NOT NULL, PRIMARY KEY(upload_id, part_number));
            CREATE TABLE materials(material_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, current_revision_id TEXT, scope TEXT NOT NULL, deleted_at TEXT, operational_updated_at TEXT NOT NULL, project_record_ref_json TEXT);
            CREATE TABLE material_revisions(revision_id TEXT PRIMARY KEY, material_id TEXT NOT NULL REFERENCES materials(material_id), input_artifact_id TEXT NOT NULL, revision_number INTEGER NOT NULL, processing_status TEXT NOT NULL, scientific_status TEXT NOT NULL, receipt_ref_json TEXT, supersedes_revision_id TEXT REFERENCES material_revisions(revision_id), created_at TEXT NOT NULL);
            CREATE TABLE material_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, material_id TEXT NOT NULL, event_type TEXT NOT NULL, payload_json TEXT NOT NULL, occurred_at TEXT NOT NULL);
            CREATE TABLE processing_claims(revision_id TEXT PRIMARY KEY, worker_id TEXT NOT NULL, leased_until TEXT NOT NULL, attempt INTEGER NOT NULL);
            INSERT INTO upload_sessions VALUES('u','p','a','text/plain',4,NULL,4,'uploading','2026-08-12T00:00:00Z','2026-08-13T00:00:00Z',NULL,NULL,'turn');
            INSERT INTO upload_parts VALUES('u',0,0,3,4,'abcd','control/material-upload-parts/u/00000000.part');
            PRAGMA user_version=1;
            """)
        migrated = MaterialStore(db, input_artifacts=artifacts, data_root=legacy_root)
        with sqlite3.connect(db) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            columns = [row[1] for row in connection.execute("PRAGMA table_info(upload_sessions)")]
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            part_count = connection.execute("SELECT COUNT(*) FROM upload_parts WHERE upload_id='u'").fetchone()[0]
            foreign_key = connection.execute("PRAGMA foreign_key_list(upload_parts)").fetchone()
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            connection.execute("DELETE FROM upload_sessions WHERE upload_id='u'")
            cascaded = connection.execute("SELECT COUNT(*) FROM upload_parts WHERE upload_id='u'").fetchone()[0]
        migrated.close()
        self.assertNotIn("scope", columns)
        from lca_research_service.material_store import CURRENT_SCHEMA_VERSION

        self.assertEqual(version, CURRENT_SCHEMA_VERSION)
        self.assertEqual(part_count, 1)
        self.assertEqual(foreign_key[2], "upload_sessions")
        self.assertEqual(foreign_key[6].upper(), "CASCADE")
        self.assertEqual(violations, [])
        self.assertEqual(cascaded, 0)

    def test_mid_migration_failure_restores_exact_legacy_database_and_connection_state(self):
        from lca_research_service.material_store import MaterialStore
        legacy_root = self.root / "legacy-failure"
        artifacts = type(self.artifacts)(legacy_root)
        db = legacy_root / "control/materials.sqlite3"
        db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db) as connection:
            connection.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE upload_sessions(upload_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, filename TEXT NOT NULL, declared_mime TEXT NOT NULL, total_bytes INTEGER NOT NULL, expected_sha256 TEXT, part_size INTEGER NOT NULL, transfer_status TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, completed_artifact_id TEXT, completed_material_id TEXT, scope TEXT NOT NULL);
            CREATE TABLE upload_parts(upload_id TEXT NOT NULL REFERENCES upload_sessions(upload_id) ON DELETE CASCADE, part_number INTEGER NOT NULL, first_byte INTEGER NOT NULL, last_byte INTEGER NOT NULL, bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, path TEXT NOT NULL, PRIMARY KEY(upload_id, part_number));
            CREATE TABLE materials(material_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, current_revision_id TEXT, scope TEXT NOT NULL, deleted_at TEXT, operational_updated_at TEXT NOT NULL, project_record_ref_json TEXT);
            CREATE TABLE material_revisions(revision_id TEXT PRIMARY KEY, material_id TEXT NOT NULL REFERENCES materials(material_id), input_artifact_id TEXT NOT NULL, revision_number INTEGER NOT NULL, processing_status TEXT NOT NULL, scientific_status TEXT NOT NULL, receipt_ref_json TEXT, supersedes_revision_id TEXT REFERENCES material_revisions(revision_id), created_at TEXT NOT NULL);
            CREATE TABLE material_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, material_id TEXT NOT NULL, event_type TEXT NOT NULL, payload_json TEXT NOT NULL, occurred_at TEXT NOT NULL);
            CREATE TABLE processing_claims(revision_id TEXT PRIMARY KEY, worker_id TEXT NOT NULL, leased_until TEXT NOT NULL, attempt INTEGER NOT NULL);
            INSERT INTO upload_sessions VALUES('u','p','a','text/plain',4,NULL,4,'uploading','2026-08-12T00:00:00Z','2026-08-13T00:00:00Z',NULL,NULL,'turn');
            INSERT INTO upload_parts VALUES('u',0,0,3,4,'abcd','control/material-upload-parts/u/00000000.part');
            PRAGMA user_version=1;
            """)
            original_schema = [tuple(row) for row in connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
            )]
            original_sessions = [tuple(row) for row in connection.execute(
                "SELECT * FROM upload_sessions ORDER BY upload_id"
            )]
            original_parts = [tuple(row) for row in connection.execute(
                "SELECT * FROM upload_parts ORDER BY upload_id, part_number"
            )]
            original_version = connection.execute("PRAGMA user_version").fetchone()[0]

        real_connect = sqlite3.connect
        captured = []

        def connect_with_mid_migration_failure(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            captured.append(connection)
            denied = False

            def deny_first_legacy_drop(action, arg1, _arg2, _database, _trigger):
                nonlocal denied
                if action == sqlite3.SQLITE_DROP_TABLE and arg1 == "upload_parts_legacy" and not denied:
                    denied = True
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            connection.set_authorizer(deny_first_legacy_drop)
            return connection

        with mock.patch(
            "lca_research_service.material_store.sqlite3.connect",
            side_effect=connect_with_mid_migration_failure,
        ):
            with self.assertRaises(sqlite3.DatabaseError):
                MaterialStore(db, input_artifacts=artifacts, data_root=legacy_root)

        failed_connection = captured[0]
        try:
            self.assertFalse(failed_connection.in_transaction)
            self.assertEqual(failed_connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(
                [tuple(row) for row in failed_connection.execute(
                    "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
                )],
                original_schema,
            )
            self.assertEqual(
                [tuple(row) for row in failed_connection.execute(
                    "SELECT * FROM upload_sessions ORDER BY upload_id"
                )],
                original_sessions,
            )
            self.assertEqual(
                [tuple(row) for row in failed_connection.execute(
                    "SELECT * FROM upload_parts ORDER BY upload_id, part_number"
                )],
                original_parts,
            )
            self.assertEqual(
                failed_connection.execute("PRAGMA user_version").fetchone()[0],
                original_version,
            )
        finally:
            failed_connection.close()

    def test_filesystem_failure_rolls_back_part_transaction_for_both_connections(self):
        from lca_research_service.material_store import MaterialStore
        payload, _, session = self._upload(payload=b"four")
        session_root = self.root / "control/material-upload-parts" / session["upload_id"]
        outside = self.root / "bad-target"; outside.mkdir()
        session_root.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.store.receive_part(project_id="project-a", upload_id=session["upload_id"], part_number=0, first_byte=0, last_byte=3, total_bytes=4, source=io.BytesIO(payload), sha256=hashlib.sha256(payload).hexdigest())
        session_root.unlink()
        other = MaterialStore(self.store.path, input_artifacts=self.artifacts, data_root=self.root, clock=self.clock, policy=self.store.policy)
        self.store.create_upload(project_id="project-a", filename="same", declared_mime="text/plain", total_bytes=1)
        other.create_upload(project_id="project-b", filename="other", declared_mime="text/plain", total_bytes=1)
        other.close()

    def test_restart_resumes_active_and_repeats_completed_upload(self):
        payload, digest, session = self._upload(payload=b"four")
        self.store.receive_part(project_id="project-a", upload_id=session["upload_id"], part_number=0, first_byte=0, last_byte=3, total_bytes=4, source=io.BytesIO(payload), sha256=digest)
        from lca_research_service.material_store import MaterialStore
        self.store.close()
        self.store = MaterialStore(self.root / "control/materials.sqlite3", input_artifacts=self.artifacts, data_root=self.root, clock=self.clock, policy={"generic_file_max_bytes": 32, "part_bytes": 4, "batch_file_count": 2, "batch_max_bytes": 24, "project_max_bytes": 20, "session_seconds": 60})
        self.assertEqual(len(self.store.get_upload(project_id="project-a", upload_id=session["upload_id"])["parts"]), 1)
        completed = self.store.complete_upload(project_id="project-a", upload_id=session["upload_id"])
        self.store.close()
        self.store = MaterialStore(self.root / "control/materials.sqlite3", input_artifacts=self.artifacts, data_root=self.root, clock=self.clock, policy={"generic_file_max_bytes": 32, "part_bytes": 4, "batch_file_count": 2, "batch_max_bytes": 24, "project_max_bytes": 20, "session_seconds": 60})
        self.assertEqual(completed, self.store.complete_upload(project_id="project-a", upload_id=session["upload_id"]))

    def test_two_connections_complete_simultaneously_with_same_identity(self):
        from lca_research_service.material_store import MaterialStore
        payload, digest, session = self._upload(payload=b"four")
        self.store.receive_part(project_id="project-a", upload_id=session["upload_id"], part_number=0, first_byte=0, last_byte=3, total_bytes=4, source=io.BytesIO(payload), sha256=digest)
        other = MaterialStore(self.store.path, input_artifacts=self.artifacts, data_root=self.root, clock=self.clock, policy=self.store.policy)
        barrier = threading.Barrier(2); results, errors = [], []
        def complete(store):
            try:
                barrier.wait(); results.append(store.complete_upload(project_id="project-a", upload_id=session["upload_id"]))
            except Exception as error: errors.append(error)
        threads = [threading.Thread(target=complete, args=(store,)) for store in (self.store, other)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        other.close()
        self.assertFalse(errors)
        self.assertEqual(results[0], results[1])

    def test_restart_reconciles_completion_intent_only_after_rehashing_bytes(self):
        payload, digest, session = self._upload(payload=b"four")
        self.store.receive_part(project_id="project-a", upload_id=session["upload_id"], part_number=0, first_byte=0, last_byte=3, total_bytes=4, source=io.BytesIO(payload), sha256=digest)
        material_id = "intent-material"
        intent = {"upload_id": session["upload_id"], "project_id": "project-a", "artifact_id": digest, "material_id": material_id, "revision_id": "intent-revision", "scope": "turn", "occurred_at": self.clock()}
        self.store._connection.execute("INSERT INTO materials VALUES(?, ?, NULL, 'turn', NULL, ?, NULL)", (material_id, "project-a", self.clock()))
        self.store._connection.execute("INSERT INTO material_events(project_id, material_id, event_type, payload_json, occurred_at) VALUES(?, ?, 'research.material.completion_intent', ?, ?)", ("project-a", material_id, __import__('json').dumps(intent), self.clock()))
        from lca_research_service.material_store import MaterialStore
        self.store.close()
        self.store = MaterialStore(self.root / "control/materials.sqlite3", input_artifacts=self.artifacts, data_root=self.root, clock=self.clock, policy={"generic_file_max_bytes": 32, "part_bytes": 4, "batch_file_count": 2, "batch_max_bytes": 24, "project_max_bytes": 20, "session_seconds": 60})
        self.assertEqual(self.store.get_upload(project_id="project-a", upload_id=session["upload_id"])["transfer_status"], "uploading")
        artifact = self.artifacts.put_verified(research_id="project-a", source=io.BytesIO(payload), expected_sha256=digest, expected_bytes=4, detected_mime="text/plain", original_filename="four", source_kind="local_upload", source_locator={"upload_id": session["upload_id"]})
        self.store.close()
        self.store = MaterialStore(self.root / "control/materials.sqlite3", input_artifacts=self.artifacts, data_root=self.root, clock=self.clock, policy={"generic_file_max_bytes": 32, "part_bytes": 4, "batch_file_count": 2, "batch_max_bytes": 24, "project_max_bytes": 20, "session_seconds": 60})
        self.assertEqual(self.store.get_upload(project_id="project-a", upload_id=session["upload_id"], include_terminal=True)["transfer_status"], "completed")


if __name__ == "__main__":
    unittest.main()
