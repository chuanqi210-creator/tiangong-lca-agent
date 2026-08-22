import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


def input_artifact(payload: bytes, *, mime: str, filename: str) -> dict:
    digest = hashlib.sha256(payload).hexdigest()
    return {
        "contract": "lca_research_kernel.input_artifact/v1",
        "artifact_id": digest,
        "project_id": "project-a",
        "sha256": digest,
        "byte_size": len(payload),
        "detected_media_type": mime,
        "original_filename": filename,
        "storage_ref": {"artifact_id": digest, "sha256": digest},
        "source_kind": "local_upload",
        "source_locator": {"upload_id": "upload-a"},
        "created_at": "2026-08-13T00:00:00Z",
        "security_status": "pending_security",
    }


class MaterialProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_text_processing_writes_candidate_receipt_and_exact_source_spans(self):
        from lca_research_kernel.material_sources import (
            validate_evidence_locator,
            validate_processing_receipt,
        )
        from lca_research_service.material_processing import process_input_artifact

        payload = "alpha\nbeta <unsafe>\n".encode()
        outcome = process_input_artifact(
            input_artifact(payload, mime="text/plain", filename="notes.txt"),
            io.BytesIO(payload),
            revision_id="revision-a",
            data_root=self.root,
        )

        validate_processing_receipt(outcome.receipt)
        self.assertEqual(
            set(outcome),
            {
                "contract",
                "revision_id",
                "processor",
                "processing_mode",
                "status",
                "detected_type",
                "evidence_units_ref",
                "locators",
                "warnings",
                "candidate_only",
            },
        )
        self.assertEqual(outcome["contract"], outcome.receipt["contract"])
        self.assertEqual(outcome["evidence_units_ref"], outcome.evidence_units_ref)
        self.assertTrue(outcome.receipt["candidate_only"])
        self.assertEqual(outcome.receipt["status"], "ready")
        self.assertEqual(outcome.receipt["processing_mode"], "structured_document_parse")
        self.assertEqual(outcome.evidence_units_ref["sha256"], outcome.receipt["output_refs"][0]["sha256"])
        self.assertGreaterEqual(len(outcome.locators), 2)
        for locator in outcome.locators:
            validate_evidence_locator(locator)
            self.assertEqual(locator["revision_id"], "revision-a")
            self.assertEqual(locator["locator_kind"], "text_span")
            self.assertIsNone(locator["assurance_acceptance_ref"])
            self.assertEqual(locator["scientific_status"], "candidate")

        evidence_path = self.root / outcome.evidence_units_ref["path"]
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(evidence["input_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertTrue(all(unit.get("source_span") for unit in evidence["units"]))
        self.assertTrue(all(unit["candidate_only"] for unit in evidence["units"]))

    def test_detection_uses_bytes_and_rejects_extension_only_claims(self):
        from lca_research_service.material_processing import process_input_artifact

        payload = b"this is not a PDF"
        outcome = process_input_artifact(
            input_artifact(payload, mime="application/pdf", filename="claim.pdf"),
            io.BytesIO(payload),
            revision_id="revision-extension",
            data_root=self.root,
        )
        self.assertEqual(outcome.receipt["status"], "unsupported")
        self.assertIn("signature", " ".join(map(str, outcome.receipt["warnings"])).casefold())
        self.assertEqual(outcome.locators, [])

    def test_registry_rejects_processors_that_omit_source_spans(self):
        from lca_research_service.document_processors import ProcessorRegistry
        from lca_research_service.material_processing import process_input_artifact

        payload = b"plain"
        registry = ProcessorRegistry()
        registry.register(
            "text/plain",
            lambda _artifact, _stream, _detection: {
                "processor": {"id": "broken", "version": "1"},
                "processing_mode": "structured_document_parse",
                "status": "ready",
                "units": [{"kind": "text", "content": "plain"}],
                "warnings": [],
                "detected_type": {"mime": "text/plain", "signatures": ["utf8"]},
            },
        )
        with self.assertRaisesRegex(ValueError, "source span"):
            process_input_artifact(
                input_artifact(payload, mime="text/plain", filename="plain.txt"),
                io.BytesIO(payload), revision_id="revision-broken",
                data_root=self.root, registry=registry,
            )

    def test_ready_processor_must_return_at_least_one_source_bound_unit(self):
        from lca_research_service.document_processors import ProcessorRegistry
        from lca_research_service.material_processing import process_input_artifact

        payload = b"plain"
        registry = ProcessorRegistry()
        registry.register(
            "text/plain",
            lambda _artifact, _stream, detection: {
                "processor": {"id": "empty", "version": "1"},
                "processing_mode": "structured_document_parse",
                "status": "ready",
                "units": [],
                "warnings": [],
                "detected_type": detection,
            },
        )
        with self.assertRaisesRegex(ValueError, "source-bound evidence"):
            process_input_artifact(
                input_artifact(payload, mime="text/plain", filename="plain.txt"),
                io.BytesIO(payload), revision_id="revision-empty",
                data_root=self.root, registry=registry,
            )

    def test_tiangong_lifecycle_package_produces_citable_bounded_summaries(self):
        """Dropping package summary locators must break the public research path."""

        from lca_research_service.material_processing import process_input_artifact

        model_uuid = "eec0e09b-6fd5-41c9-9a12-41300c9e6cfa"
        model_version = "01.01.011"
        model_path = f"lifecyclemodels/{model_uuid}_{model_version}.json"
        package_manifest = {
            "format": "tiangong-tidas-package",
            "version": 2,
            "exported_at": "2026-08-12T07:48:13Z",
            "scope": "selected_roots",
            "roots": [
                {
                    "table": "lifecyclemodels",
                    "id": model_uuid,
                    "version": model_version,
                }
            ],
            "entries": [
                {
                    "table": "lifecyclemodels",
                    "id": model_uuid,
                    "version": model_version,
                    "file_path": model_path,
                    "rule_verification": True,
                },
                {
                    "table": "processes",
                    "id": "21084c23-87a9-4e84-af4e-d8b45550a1a2",
                    "version": "01.01.009",
                    "file_path": (
                        "processes/21084c23-87a9-4e84-af4e-d8b45550a1a2_"
                        "01.01.009.json"
                    ),
                    "rule_verification": True,
                },
            ],
        }
        lifecycle_model = {
            "lifeCycleModelDataSet": {
                "lifeCycleModelInformation": {
                    "dataSetInformation": {
                        "common:UUID": model_uuid,
                        "name": {
                            "baseName": [
                                {"#text": "交流电生产", "@xml:lang": "zh"},
                                {
                                    "#text": "Alternating current production",
                                    "@xml:lang": "en",
                                },
                            ],
                            "functionalUnitFlowProperties": [
                                {"#text": "1-35千伏", "@xml:lang": "zh"}
                            ],
                            "mixAndLocationTypes": [
                                {"#text": "消费组合", "@xml:lang": "zh"}
                            ],
                            "treatmentStandardsRoutes": [
                                {"#text": "电力混合", "@xml:lang": "zh"}
                            ],
                        },
                    },
                    "quantitativeReference": {
                        "referenceToReferenceProcess": 0
                    },
                    "technology": {
                        "processes": {
                            "processInstance": [
                                {
                                    "@dataSetInternalID": "0",
                                    "@multiplicationFactor": "1",
                                    "referenceToProcess": {
                                        "@refObjectId": (
                                            "21084c23-87a9-4e84-af4e-d8b45550a1a2"
                                        ),
                                        "@version": "01.01.009",
                                        "common:shortDescription": [
                                            {
                                                "#text": "交流电生产; 电力混合; 消费组合; 1-35千伏",
                                                "@xml:lang": "zh",
                                            }
                                        ],
                                    },
                                }
                            ]
                        }
                    },
                },
                "administrativeInformation": {
                    "common:commissionerAndGoal": {
                        "common:intendedApplications": [
                            {
                                "#text": "用于直接消耗中压电力的活动。",
                                "@xml:lang": "zh",
                            }
                        ]
                    },
                    "publicationAndOwnership": {
                        "common:dataSetVersion": model_version
                    },
                },
            }
        }
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(package_manifest, ensure_ascii=False),
            )
            archive.writestr(
                model_path, json.dumps(lifecycle_model, ensure_ascii=False)
            )
            archive.writestr(
                package_manifest["entries"][1]["file_path"],
                json.dumps({"processDataSet": {"common:UUID": package_manifest["entries"][1]["id"]}}),
            )
        payload = stream.getvalue()

        outcome = process_input_artifact(
            input_artifact(
                payload,
                mime="application/zip",
                filename=f"tidas-package-lifecyclemodels-{model_uuid}-{model_version}.zip",
            ),
            io.BytesIO(payload),
            revision_id="revision-tidas-package",
            data_root=self.root,
        )
        evidence = json.loads(
            (self.root / outcome.evidence_units_ref["path"]).read_text()
        )

        self.assertEqual(outcome.receipt["status"], "ready")
        self.assertEqual(
            outcome.lca_package_route["detected_candidate_format"],
            "tiangong_tidas_package",
        )
        self.assertEqual(len(outcome.locators), 3)
        self.assertEqual(
            [locator["locator"]["member_path"] for locator in outcome.locators[:2]],
            ["manifest.json", model_path],
        )
        self.assertEqual(
            [unit["kind"] for unit in evidence["units"][:2]],
            ["tidas_package_summary", "tidas_lifecycle_model_summary"],
        )
        self.assertEqual(
            evidence["units"][2]["kind"], "tidas_object_graph_slice"
        )
        self.assertEqual(
            outcome.locators[2]["locator"]["member_path"], model_path
        )
        self.assertEqual(
            evidence["units"][0]["content"]["entry_counts"],
            {"lifecyclemodels": 1, "processes": 1},
        )
        self.assertEqual(
            evidence["units"][0]["source_identity_anchors"],
            ["tiangong-tidas-package", model_uuid, model_version],
        )
        self.assertEqual(
            evidence["units"][0]["semantic_roles"],
            ["lca_native_package", "source_identity", "version"],
        )
        self.assertEqual(
            evidence["units"][1]["content"],
            {
                "contract": "lca_research_service.tidas_lifecycle_model_summary/v1",
                "dataset_uuid": model_uuid,
                "version": model_version,
                "name": {
                    "base_name": {"en": "Alternating current production", "zh": "交流电生产"},
                    "functional_unit_flow_properties": {"zh": "1-35千伏"},
                    "mix_and_location_types": {"zh": "消费组合"},
                    "treatment_standards_routes": {"zh": "电力混合"},
                },
                "intended_applications": {"zh": "用于直接消耗中压电力的活动。"},
                "quantitative_reference": {"reference_process_internal_id": 0},
                "process_instances": [
                    {
                        "internal_id": "0",
                        "multiplication_factor": "1",
                        "process_uuid": "21084c23-87a9-4e84-af4e-d8b45550a1a2",
                        "version": "01.01.009",
                        "description": {
                            "zh": "交流电生产; 电力混合; 消费组合; 1-35千伏"
                        },
                    }
                ],
                "candidate_only": True,
                "scientifically_validated": False,
            },
        )
        self.assertEqual(
            evidence["units"][1]["source_identity_anchors"],
            [
                model_uuid,
                model_version,
                "Alternating current production",
                "交流电生产",
                "21084c23-87a9-4e84-af4e-d8b45550a1a2",
                "交流电生产; 电力混合; 消费组合; 1-35千伏",
            ],
        )
        self.assertEqual(
            evidence["units"][1]["semantic_roles"],
            ["lca_native_package", "process", "source_identity", "version"],
        )

        from lca_research_kernel.answer_quality import evaluate_answer_quality
        from lca_research_kernel.project_record import canonical_sha256

        frame = {
            "answer_obligations": ["direct_resolution", "source_boundary"],
            "referent": {"kind": "project_goal", "identity": "project-a"},
        }
        frame["frame_sha256"] = canonical_sha256(frame)
        locator_ids = [row["locator_id"] for row in outcome.locators[:2]]
        claim = {
            "claim_id": "claim-tidas-identity",
            "claim_kind": "source_fact",
            "text": (
                "该 TIDAS 包的根生命周期模型 UUID 为 "
                f"{model_uuid}，版本为 {model_version}。"
            ),
            "citation_tokens": [f"[{value}]" for value in locator_ids],
            "knowledge_refs": [],
            "referent_ids": ["project-a"],
            "not_project_fact": False,
            "satisfies_obligations": ["direct_resolution", "source_boundary"],
        }

        quality = evaluate_answer_quality(
            [claim],
            turn_frame=frame,
            selected_locator_ids=locator_ids,
            selected_evidence_units=[
                {
                    "locator_id": locator["locator_id"],
                    "evidence_unit": unit,
                }
                for locator, unit in zip(outcome.locators[:2], evidence["units"][:2])
            ],
            governed_knowledge=[],
            response_language="zh-CN",
        )

        self.assertEqual(quality["disposition"], "accept")


if __name__ == "__main__":
    unittest.main()
