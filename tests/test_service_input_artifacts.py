import hashlib
import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path


class InputArtifactStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_put_open_describe_and_reuse_verified_content(self):
        from lca_research_service.input_artifacts import InputArtifactStore

        payload = b"immutable source"
        digest = hashlib.sha256(payload).hexdigest()
        store = InputArtifactStore(self.root)
        first = store.put_verified(
            research_id="project-a",
            source=io.BytesIO(payload),
            expected_sha256=digest,
            expected_bytes=len(payload),
            detected_mime="text/plain",
            original_filename="source.txt",
            source_kind="local_upload",
            source_locator={"upload_id": "upload-1"},
        )
        second = store.put_verified(
            research_id="project-a",
            source=io.BytesIO(payload),
            expected_sha256=digest,
            expected_bytes=len(payload),
            detected_mime="text/plain",
            original_filename="source.txt",
            source_kind="local_upload",
            source_locator={"upload_id": "upload-1"},
        )

        self.assertEqual(first, second)

    def test_recovers_interrupted_pair_publication_and_concurrent_publish(self):
        from lca_research_service.input_artifacts import InputArtifactStore

        payload = b"recoverable"
        digest = hashlib.sha256(payload).hexdigest()
        directory = self.root / "projects/project-a/inputs/sha256" / digest[:2] / digest
        directory.mkdir(parents=True)
        (directory / "original").write_bytes(payload)
        stores = [InputArtifactStore(self.root), InputArtifactStore(self.root)]
        results, errors = [], []

        def publish(store, name):
            try:
                results.append(store.put_verified(
                    research_id="project-a", source=io.BytesIO(payload),
                    expected_sha256=digest, expected_bytes=len(payload),
                    detected_mime="text/plain", original_filename=name,
                    source_kind="local_upload", source_locator={"upload_id": name},
                ))
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=publish, args=(store, f"u-{index}")) for index, store in enumerate(stores)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertFalse(errors)
        self.assertEqual(results[0], results[1])

    def test_read_rejects_symlinked_ancestor(self):
        from lca_research_service.input_artifacts import InputArtifactStore

        payload = b"ancestor"
        digest = hashlib.sha256(payload).hexdigest()
        store = InputArtifactStore(self.root)
        store.put_verified(
            research_id="project-a", source=io.BytesIO(payload), expected_sha256=digest,
            expected_bytes=len(payload), detected_mime="text/plain", original_filename="a.txt",
            source_kind="local_upload", source_locator={"upload_id": "u"},
        )
        project = self.root / "projects/project-a"
        moved = self.root / "moved-project"
        project.rename(moved)
        project.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(ValueError):
            store.describe(research_id="project-a", artifact_id=digest)

    def test_rejects_hash_size_project_and_symlink_attacks(self):
        from lca_research_service.input_artifacts import InputArtifactStore

        store = InputArtifactStore(self.root)
        payload = b"abc"
        digest = hashlib.sha256(payload).hexdigest()
        common = dict(
            research_id="project-a",
            source=io.BytesIO(payload),
            expected_sha256=digest,
            expected_bytes=len(payload),
            detected_mime="text/plain",
            original_filename="a.txt",
            source_kind="local_upload",
            source_locator={"upload_id": "upload-1"},
        )
        with self.assertRaises(ValueError):
            store.put_verified(**{**common, "research_id": "../escape"})
        with self.assertRaises(ValueError):
            store.put_verified(**{**common, "expected_bytes": 4})
        with self.assertRaises(ValueError):
            store.put_verified(**{**common, "expected_sha256": "0" * 64})

        project = self.root / "projects/project-symlink"
        project.parent.mkdir(parents=True, exist_ok=True)
        outside = self.root / "outside"
        outside.mkdir()
        os.symlink(outside, project)
        with self.assertRaises((OSError, ValueError)):
            store.put_verified(**{**common, "research_id": "project-symlink"})

    def test_existing_content_is_rehashed_and_cross_project_is_hidden(self):
        from lca_research_service.input_artifacts import InputArtifactStore

        payload = b"abc"
        digest = hashlib.sha256(payload).hexdigest()
        store = InputArtifactStore(self.root)
        metadata = store.put_verified(
            research_id="project-a",
            source=io.BytesIO(payload),
            expected_sha256=digest,
            expected_bytes=3,
            detected_mime="text/plain",
            original_filename="a.txt",
            source_kind="local_upload",
            source_locator={"upload_id": "upload-1"},
        )
        original = self.root / "projects/project-a/inputs/sha256" / digest[:2] / digest / "original"
        original.chmod(0o600)
        original.write_bytes(b"drift")
        with self.assertRaises(ValueError):
            store.describe(research_id="project-a", artifact_id=digest)
        with self.assertRaises(KeyError):
            store.describe(research_id="project-b", artifact_id=metadata["artifact_id"])

    def test_same_project_content_hash_reuses_original_identity(self):
        from lca_research_service.input_artifacts import InputArtifactStore

        payload = b"shared"
        digest = hashlib.sha256(payload).hexdigest()
        store = InputArtifactStore(self.root)
        first = store.put_verified(
            research_id="project-a", source=io.BytesIO(payload),
            expected_sha256=digest, expected_bytes=len(payload),
            detected_mime="text/plain", original_filename="first.txt",
            source_kind="local_upload", source_locator={"upload_id": "upload-1"},
        )
        second = store.put_verified(
            research_id="project-a", source=io.BytesIO(payload),
            expected_sha256=digest, expected_bytes=len(payload),
            detected_mime="text/plain", original_filename="second.txt",
            source_kind="local_upload", source_locator={"upload_id": "upload-2"},
        )
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
