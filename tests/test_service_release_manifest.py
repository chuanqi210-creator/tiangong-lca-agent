import json
import hashlib
import hmac
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


class ServiceReleaseManifestTests(unittest.TestCase):
    def test_release_identity_binds_the_real_pilot_launcher(self):
        from lca_research_service.release_manifest import _BACKEND_INPUTS

        self.assertIn("scripts/run_frontend_real_pilot_stack.py", _BACKEND_INPUTS)

    def test_exact_job_retry_returns_service_authenticated_replay_evidence(self):
        from fastapi.testclient import TestClient
        from lca_research_service.app import ServiceConfig, create_app
        from lca_research_service.native_evidence_authentication import (
            open_native_evidence_authentication_ports,
        )
        from lca_research_service.product_evidence import (
            validate_service_idempotency_replay_evidence,
        )
        from lca_research_service.release_manifest import (
            build_runtime_release_manifest,
            write_runtime_release_manifest,
        )

        manifest = build_runtime_release_manifest(
            ROOT,
            frontend_root=ROOT / "apps/web",
            deployed_at="2026-08-20T00:00:00Z",
            runtime_instance_id="service-replay-http-a",
        )
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary)
            manifest_path = data_root / "runtime-release.json"
            write_runtime_release_manifest(manifest_path, manifest)
            app = create_app(
                ServiceConfig(
                    bearer_token="token",
                    data_root=data_root,
                    allowed_attachment_hosts=("assets.example.invalid",),
                    repository_root=ROOT,
                    release_manifest_file=manifest_path,
                ),
                runtime=object(),
            )
            headers = {
                "Authorization": "Bearer token",
                "Idempotency-Key": "exact-retry-a",
            }
            request = {
                "research_id": "research-replay-a",
                "messages": [{"role": "user", "content": "解释 LCA。"}],
            }
            with TestClient(app) as client:
                first = client.post("/v1/jobs", headers=headers, json=request)
                replay = client.post("/v1/jobs", headers=headers, json=request)
            self.assertEqual(first.status_code, 202, first.text)
            self.assertEqual(replay.status_code, 202, replay.text)
            self.assertIsNone(first.json()["idempotencyReplayEvidence"])
            evidence = replay.json()["idempotencyReplayEvidence"]
            verified = validate_service_idempotency_replay_evidence(
                evidence,
                job_id=first.json()["job_id"],
                verifier=open_native_evidence_authentication_ports(data_root).verify,
            )
            self.assertEqual(verified["research_id"], "research-replay-a")

    def test_service_product_evidence_binds_release_result_record_and_material_count(self):
        from lca_research_service.product_evidence import (
            build_service_idempotency_replay_evidence,
            build_service_product_evidence,
            canonical_sha256,
            validate_service_idempotency_replay_evidence,
            validate_service_product_evidence,
        )
        from lca_research_service.release_manifest import (
            build_runtime_release_manifest,
            runtime_release_binding,
        )

        manifest = build_runtime_release_manifest(
            ROOT,
            frontend_root=ROOT / "apps/web",
            deployed_at="2026-08-20T00:00:00Z",
            runtime_instance_id="service-product-evidence-a",
        )
        key = b"service-product-evidence-test-key"
        signer = lambda message: hmac.digest(key, message, "sha256").hex()
        verifier = lambda message, tag: hmac.compare_digest(
            signer(message), tag
        )
        result = {
            "sourceUse": [
                {
                    "materialId": "material-a",
                    "revisionId": "revision-a",
                    "status": "used",
                }
            ],
            "officialSources": [],
        }
        project_view = {
            "project_id": "research-a",
            "last_event_sha256": "e" * 64,
            "materials": {
                "material-a": {
                    "material_id": "material-a",
                    "current_revision_id": "revision-a",
                }
            },
            "material_revisions": {
                "revision-a": {
                    "material_id": "material-a",
                    "artifact_ref": {"sha256": "a" * 64},
                    "processing_receipt_sha256": "b" * 64,
                }
            },
            "evidence_locators": {
                "locator-a": {
                    "revision_id": "revision-a",
                    "scientific_status": "accepted_evidence",
                },
                "locator-b": {
                    "revision_id": "revision-a",
                    "scientific_status": "accepted_evidence",
                },
            },
        }
        execution = {
            "contract": "lca_research_kernel.public_execution_receipt/v1",
            "project_record": {
                "last_event_sha256": "e" * 64,
                "event_count": 11,
            },
        }
        execution["receipt_sha256"] = canonical_sha256(execution)
        evidence = build_service_product_evidence(
            job_id="job-a",
            release_manifest=manifest,
            result=result,
            execution_receipt=execution,
            project_view=project_view,
            job_request_release=runtime_release_binding(manifest),
            material_detail=lambda material_id: {
                "material": {
                    "material_id": material_id,
                    "current_revision_id": "revision-a",
                    "byte_size": 239_000,
                },
                "revisions": [
                    {
                        "revision_id": "revision-a",
                        "input_artifact_id": "a" * 64,
                    }
                ],
            },
            signer=signer,
        )
        view = {
            "result": result,
            "executionReceipt": execution,
            "runtime_release": evidence["release_identity"],
        }

        verified = validate_service_product_evidence(
            evidence,
            job_id="job-a",
            result_view=view,
            verifier=verifier,
        )
        self.assertEqual(verified["materials"][0]["qualified_locator_count"], 2)
        self.assertEqual(verified["materials"][0]["input_bytes"], 239_000)
        tampered = json.loads(json.dumps(evidence))
        tampered["materials"][0]["qualified_locator_count"] = 3
        with self.assertRaisesRegex(ValueError, "authentication"):
            validate_service_product_evidence(
                tampered,
                job_id="job-a",
                result_view=view,
                verifier=verifier,
            )
        replay = build_service_idempotency_replay_evidence(
            job={
                "job_id": "job-a",
                "project_id": "research-a",
                "request_sha256": "c" * 64,
                "request_ref": {"sha256": "d" * 64},
                "idempotency_key": "research-a:exact-request-a",
            },
            release_manifest=manifest,
            signer=signer,
        )
        self.assertEqual(
            validate_service_idempotency_replay_evidence(
                replay,
                job_id="job-a",
                verifier=verifier,
            )["release_identity"],
            runtime_release_binding(manifest),
        )

    def test_service_product_evidence_accepts_real_no_material_project_shape(self):
        """An omitted material projection means no materials, not corrupt evidence."""

        from lca_research_service.product_evidence import (
            build_service_product_evidence,
            canonical_sha256,
            validate_service_product_evidence,
        )
        from lca_research_service.release_manifest import (
            build_runtime_release_manifest,
            runtime_release_binding,
        )

        manifest = build_runtime_release_manifest(
            ROOT,
            frontend_root=ROOT / "apps/web",
            deployed_at="2026-08-20T00:00:00Z",
            runtime_instance_id="service-product-evidence-no-material",
        )
        key = b"service-product-evidence-no-material-key"
        signer = lambda message: hmac.digest(key, message, "sha256").hex()
        verifier = lambda message, tag: hmac.compare_digest(signer(message), tag)
        result = {"sourceUse": [], "officialSources": []}
        project_view = {
            "project_id": "research-no-material",
            "last_event_sha256": "f" * 64,
        }
        execution = {
            "contract": "lca_research_kernel.public_execution_receipt/v1",
            "project_record": {
                "last_event_sha256": "f" * 64,
                "event_count": 5,
            },
        }
        execution["receipt_sha256"] = canonical_sha256(execution)

        evidence = build_service_product_evidence(
            job_id="job-no-material",
            release_manifest=manifest,
            result=result,
            execution_receipt=execution,
            project_view=project_view,
            job_request_release=runtime_release_binding(manifest),
            material_detail=lambda material_id: self.fail(
                f"material detail unexpectedly requested for {material_id}"
            ),
            signer=signer,
        )

        verified = validate_service_product_evidence(
            evidence,
            job_id="job-no-material",
            result_view={
                "result": result,
                "executionReceipt": execution,
                "runtime_release": evidence["release_identity"],
            },
            verifier=verifier,
        )
        self.assertEqual(verified["materials"], [])

    def test_service_product_evidence_accepts_real_material_store_revision_identity(self):
        """The signed result path must consume the real material-detail projection."""

        from lca_research_service.input_artifacts import InputArtifactStore
        from lca_research_service.material_store import MaterialStore
        from lca_research_service.product_evidence import (
            build_service_product_evidence,
            canonical_sha256,
        )
        from lca_research_service.release_manifest import (
            build_runtime_release_manifest,
            runtime_release_binding,
        )

        payload = b'{"functional_unit":"1 kg product"}'
        input_sha256 = hashlib.sha256(payload).hexdigest()
        manifest = build_runtime_release_manifest(
            ROOT,
            frontend_root=ROOT / "apps/web",
            deployed_at="2026-08-20T00:00:00Z",
            runtime_instance_id="service-product-evidence-real-material",
        )
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary)
            artifacts = InputArtifactStore(data_root)
            store = MaterialStore(
                data_root / "control/materials.sqlite3",
                input_artifacts=artifacts,
                data_root=data_root,
            )
            try:
                upload = store.create_upload(
                    project_id="research-real-material",
                    filename="inventory.json",
                    declared_mime="application/json",
                    total_bytes=len(payload),
                    expected_sha256=input_sha256,
                )
                store.receive_part(
                    project_id="research-real-material",
                    upload_id=upload["upload_id"],
                    part_number=0,
                    first_byte=0,
                    last_byte=len(payload) - 1,
                    total_bytes=len(payload),
                    source=io.BytesIO(payload),
                    sha256=input_sha256,
                )
                completed = store.complete_upload(
                    project_id="research-real-material",
                    upload_id=upload["upload_id"],
                    scope="project",
                )
                material_id = completed["material"]["material_id"]
                revision_id = completed["revision"]["revision_id"]
                project_view = {
                    "project_id": "research-real-material",
                    "last_event_sha256": "e" * 64,
                    "materials": {
                        material_id: {
                            "material_id": material_id,
                            "current_revision_id": revision_id,
                        }
                    },
                    "material_revisions": {
                        revision_id: {
                            "material_id": material_id,
                            "artifact_ref": {"sha256": input_sha256},
                            "processing_receipt_sha256": "b" * 64,
                        }
                    },
                    "evidence_locators": {
                        "locator-a": {
                            "revision_id": revision_id,
                            "scientific_status": "accepted_evidence",
                        }
                    },
                }
                result = {
                    "sourceUse": [
                        {
                            "materialId": material_id,
                            "revisionId": revision_id,
                            "status": "used",
                        }
                    ],
                    "officialSources": [],
                }
                execution = {
                    "contract": "lca_research_kernel.public_execution_receipt/v1",
                    "project_record": {
                        "last_event_sha256": "e" * 64,
                        "event_count": 11,
                    },
                }
                execution["receipt_sha256"] = canonical_sha256(execution)

                evidence = build_service_product_evidence(
                    job_id="job-real-material",
                    release_manifest=manifest,
                    result=result,
                    execution_receipt=execution,
                    project_view=project_view,
                    job_request_release=runtime_release_binding(manifest),
                    material_detail=lambda requested_material_id: store.material_detail(
                        project_id="research-real-material",
                        material_id=requested_material_id,
                    ),
                    signer=lambda message: hmac.digest(
                        b"service-product-evidence-real-material-key",
                        message,
                        "sha256",
                    ).hex(),
                )
            finally:
                store.close()

        self.assertEqual(evidence["materials"][0]["input_sha256"], input_sha256)
        self.assertEqual(evidence["materials"][0]["input_bytes"], len(payload))

    def test_manifest_binds_backend_frontend_contract_record_and_provider(self):
        from lca_research_service.release_manifest import (
            build_runtime_release_manifest,
            validate_runtime_release_manifest,
        )

        manifest = build_runtime_release_manifest(
            ROOT,
            frontend_root=ROOT / "apps/web",
            deployed_at="2026-08-17T00:00:00Z",
            runtime_instance_id="release-test-a",
        )
        validated = validate_runtime_release_manifest(
            manifest,
            repository_root=ROOT,
            frontend_root=ROOT / "apps/web",
        )

        self.assertEqual(manifest, validated)
        self.assertEqual(manifest["contract"], "d18.runtime_release_manifest/v2")
        self.assertRegex(manifest["release_id"], r"^[0-9a-f]{64}$")
        self.assertRegex(manifest["manifest_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            manifest["project_record"]["event_contract"],
            "lca_research_kernel.project_record_event/v1",
        )
        self.assertEqual(
            manifest["project_record"]["view_contract"],
            "lca_research_kernel.project_record_view/v1",
        )
        self.assertEqual(manifest["project_record"]["schema_generation"], 1)
        self.assertRegex(
            manifest["provider"]["provider_catalog_sha256"], r"^[0-9a-f]{64}$"
        )
        self.assertEqual(
            manifest["frontend"]["build_id"],
            (ROOT / "apps/web/.next/BUILD_ID").read_text().strip(),
        )
        self.assertEqual(
            manifest["capability_runtime"]["tiangong_node_package_lock_sha256"],
            hashlib.sha256(
                (ROOT / "runtime/tiangong-node/package-lock.json").read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(
            manifest["capability_runtime"]["tiangong_source_lock_sha256"],
            hashlib.sha256(
                (ROOT / "runtime/tiangong-runtime-source-lock.json").read_bytes()
            ).hexdigest(),
        )

    def test_loader_rejects_self_consistent_but_drifted_tiangong_runtime_identity(self):
        from lca_research_service.release_manifest import (
            _identity_basis,
            build_runtime_release_manifest,
            canonical_manifest_sha256,
            load_runtime_release_manifest,
        )

        manifest = build_runtime_release_manifest(
            ROOT,
            frontend_root=ROOT / "apps/web",
            deployed_at="2026-08-17T00:00:00Z",
            runtime_instance_id="release-test-tiangong-runtime",
        )
        original_release_id = manifest["release_id"]
        manifest["capability_runtime"]["tiangong_node_package_lock_sha256"] = (
            "f" * 64
        )
        manifest["release_id"] = hashlib.sha256(
            (
                json.dumps(
                    _identity_basis(manifest),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
        ).hexdigest()
        self.assertNotEqual(manifest["release_id"], original_release_id)
        manifest["manifest_sha256"] = canonical_manifest_sha256(manifest)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "release.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "capability_runtime"):
                load_runtime_release_manifest(
                    path,
                    repository_root=ROOT,
                    frontend_root=ROOT / "apps/web",
                )

    def test_loader_rejects_self_consistent_but_drifted_provider_identity(self):
        from lca_research_service.release_manifest import (
            build_runtime_release_manifest,
            canonical_manifest_sha256,
            load_runtime_release_manifest,
        )

        manifest = build_runtime_release_manifest(
            ROOT,
            frontend_root=ROOT / "apps/web",
            deployed_at="2026-08-17T00:00:00Z",
            runtime_instance_id="release-test-b",
        )
        manifest["provider"]["provider_catalog_sha256"] = "f" * 64
        manifest["manifest_sha256"] = canonical_manifest_sha256(manifest)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "release.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "provider"):
                load_runtime_release_manifest(
                    path,
                    repository_root=ROOT,
                    frontend_root=ROOT / "apps/web",
                )

    def test_request_binding_rejects_a_different_release_before_execution(self):
        from lca_research_service.release_manifest import (
            require_request_release,
            runtime_release_binding,
        )

        release = {
            "contract": "d18.runtime_release_manifest/v1",
            "release_id": "a" * 64,
            "manifest_sha256": "b" * 64,
            "backend": {"git_commit": "d" * 40},
            "frontend": {"build_id": "frontend-a"},
        }
        request = {"runtime_release": runtime_release_binding(release)}
        self.assertEqual(require_request_release(request, release)["release_id"], "a" * 64)
        request["runtime_release"]["release_id"] = "c" * 64
        with self.assertRaisesRegex(ValueError, "release"):
            require_request_release(request, release)

    def test_production_worker_rejects_release_drift_before_runtime_factory(self):
        from lca_research_service.job_store import JobStore
        from lca_research_service.release_manifest import (
            build_runtime_release_manifest,
            runtime_release_binding,
            write_runtime_release_manifest,
        )
        from lca_research_service.worker import main

        manifest = build_runtime_release_manifest(
            ROOT,
            frontend_root=ROOT / "apps/web",
            deployed_at="2026-08-17T00:00:00Z",
            runtime_instance_id="release-worker-a",
        )
        binding = runtime_release_binding(manifest)
        binding["release_id"] = "f" * 64
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary)
            request = {"research_id": "pilot-a-job", "runtime_release": binding}
            payload = (json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n").encode()
            digest = hashlib.sha256(payload).hexdigest()
            relative = Path("projects/pilot-a-job/intake") / f"{digest}.json"
            target = data_root / relative
            target.parent.mkdir(parents=True)
            target.write_bytes(payload)
            store = JobStore(data_root / "control/jobs.sqlite3")
            job = store.create(
                digest,
                "pilot-a-job:release-drift",
                {"path": relative.as_posix(), "sha256": digest},
                project_id="pilot-a-job",
            )
            store.claim("worker-a", 60)
            store.close()
            manifest_path = data_root / "control/runtime-release-manifest.json"
            write_runtime_release_manifest(manifest_path, manifest)
            with mock.patch.dict(
                os.environ,
                {"D18_RELEASE_MANIFEST_FILE": str(manifest_path)},
                clear=False,
            ), mock.patch("lca_research_service.worker.RuntimeFactory") as factory:
                status = main(
                    [
                        "--job-id",
                        job["job_id"],
                        "--control-db",
                        str(data_root / "control/jobs.sqlite3"),
                        "--worker-id",
                        "worker-a",
                        "--data-root",
                        str(data_root),
                        "--repository-root",
                        str(ROOT),
                    ]
                )
            self.assertEqual(status, 1)
            factory.assert_not_called()
            with JobStore(data_root / "control/jobs.sqlite3") as reopened:
                self.assertEqual(reopened.get(job["job_id"])["status"], "failed")


if __name__ == "__main__":
    unittest.main()
