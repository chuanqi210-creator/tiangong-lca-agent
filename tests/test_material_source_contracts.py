import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from lca_research_kernel.material_sources import (
    build_material_use_decision,
    load_research_materials_policy,
    material_selection_basis,
    material_use_decision_checkpoint,
    validate_evidence_locator,
    validate_input_artifact,
    validate_material_revision,
    validate_material_use_decision,
    validate_processing_receipt,
    validate_research_material,
)
from lca_research_kernel.project_record import ProjectRecord, canonical_sha256


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def input_artifact() -> dict:
    return {
        "contract": "lca_research_kernel.input_artifact/v1",
        "artifact_id": SHA_A,
        "project_id": "project-a",
        "sha256": SHA_A,
        "byte_size": 12,
        "detected_media_type": "text/plain",
        "original_filename": "inventory.txt",
        "storage_ref": {"artifact_id": SHA_A, "sha256": SHA_A},
        "source_kind": "local_upload",
        "source_locator": {"upload_id": "upload-a"},
        "created_at": "2026-08-12T02:00:00Z",
        "security_status": "pending_security",
    }


def research_material() -> dict:
    return {
        "contract": "lca_research_kernel.research_material/v1",
        "material_id": "material-a",
        "project_id": "project-a",
        "display_name": "Inventory",
        "material_kind": "document",
        "current_revision_id": "revision-a",
        "scope": "project",
        "authority": "user_supplied_project_input",
        "research_classification": "foreground_inventory",
        "labels": ["inventory"],
        "created_by": "user",
        "created_at": "2026-08-12T02:00:00Z",
        "deleted_at": None,
    }


def material_revision() -> dict:
    return {
        "contract": "lca_research_kernel.material_revision/v1",
        "revision_id": "revision-a",
        "material_id": "material-a",
        "artifact_ref": {"artifact_id": SHA_A, "sha256": SHA_A},
        "parent_revision_id": None,
        "processor_profile": "deterministic-v1",
        "processing_status": "pending_security",
        "extraction_refs": [],
        "manifest_ref": None,
        "created_at": "2026-08-12T02:00:00Z",
    }


def locator(kind: str = "text_span", payload: dict | None = None) -> dict:
    return {
        "contract": "lca_research_kernel.evidence_locator/v1",
        "locator_id": "locator-a",
        "revision_id": "revision-a",
        "locator_kind": kind,
        "locator": payload
        or {"start_char": 0, "end_char": 9, "start_line": 1, "end_line": 1},
        "content_hash": SHA_B,
        "preview_text_or_thumbnail_ref": {"artifact_id": SHA_B, "sha256": SHA_B},
        "assurance_acceptance_ref": None,
    }


def processing_receipt() -> dict:
    return {
        "contract": "lca_research_kernel.material_processing_receipt/v1",
        "revision_id": "revision-a",
        "processor": {"id": "plain-text", "version": "1.0.0"},
        "processing_mode": "full_context",
        "status": "ready",
        "input_sha256": SHA_A,
        "detected_type": {"mime": "text/plain", "signatures": ["utf-8"]},
        "output_refs": [{"artifact_id": SHA_B, "sha256": SHA_B}],
        "completeness": {"status": "complete", "unprocessed_regions": []},
        "warnings": [],
        "failure_category": None,
        "duration_ms": 4,
        "replay_key": SHA_B,
        "candidate_only": True,
    }


def material_project_view() -> dict:
    material = research_material()
    material["scientific_status"] = "accepted_project_input"
    revision = material_revision()
    revision["processing_status"] = "ready"
    accepted_locator = locator()
    accepted_locator["scientific_status"] = "accepted_evidence"
    accepted_locator["assurance_acceptance_ref"] = {
        "artifact_id": SHA_C,
        "sha256": SHA_C,
    }
    return {
        "contract": "lca_research_kernel.project_record_view/v1",
        "project_id": "project-a",
        "materials": {"material-a": material},
        "material_revisions": {"revision-a": revision},
        "evidence_locators": {"locator-a": accepted_locator},
        "material_use_decisions": {},
        "last_event_id": "event-started",
        "last_event_sha256": SHA_A,
        "unrelated_state": {"generation": 1},
    }


def material_use_decision_v2(project_view: dict | None = None, **overrides) -> dict:
    view = project_view or material_project_view()
    locator_index = overrides.pop("locator_index", view.get("evidence_locators", {}))
    arguments = {
        "project_view": view,
        "task_kind": "semantic_material_selection",
        "question": {"objective": "What is the functional unit?"},
        "allowed_revision_ids": ["revision-a"],
        "selected_evidence_locator_ids": ["locator-a"],
        "selection_reasons": {"locator-a": "supports the requested inventory"},
        "exclusions": [
            {"identity": "material_revision:revision-b", "reason": "superseded"}
        ],
        "context_mode": "focused_retrieval",
        "expected_state_change": {"desired_artifact": "answer"},
        "claim_ceiling": "candidate_only",
        "locator_index": locator_index,
    }
    arguments.update(overrides)
    return build_material_use_decision(**arguments)


def append_accepted_material_use_decision(
    record: ProjectRecord, decision: dict
) -> dict:
    from lca_research_kernel.assurance import AssuranceBoundary

    candidate = {
        "contract": "lca_research_kernel.project_candidate_change/v1",
        "candidate_id": "candidate-material-use",
        "change_type": "material.use_decision_recorded",
        "decision_id": decision["task_id"],
        "material_use_decision": deepcopy(decision),
        "decision_sha256": canonical_sha256(decision),
        "next_route": "consume-selected-material-evidence",
        "proposed_at": "2026-08-12T02:00:00Z",
    }
    acceptance = AssuranceBoundary().evaluate_candidate_change(
        candidate, record.read_current_project_view()
    )
    if acceptance["decision"] != "accepted":
        raise AssertionError(acceptance["errors"])
    return record.append_accepted_change(
        candidate,
        acceptance,
        idempotency_key="material-use-decision:test",
    )


def empty_material_use_decision(record: ProjectRecord) -> dict:
    return build_material_use_decision(
        project_view=record.read_current_project_view(),
        task_kind="semantic_material_selection",
        question={"objective": "What is the functional unit?"},
        allowed_revision_ids=[],
        selected_evidence_locator_ids=[],
        selection_reasons={},
        exclusions=[],
        context_mode="focused_retrieval",
        expected_state_change={"desired_artifact": "bounded-answer"},
        claim_ceiling="missing_evidence_bounded",
        locator_index={},
    )


class MaterialSourceContractTests(unittest.TestCase):
    def test_valid_contracts_are_copied_and_accepted(self):
        values = [
            (validate_input_artifact, input_artifact()),
            (validate_research_material, research_material()),
            (validate_material_revision, material_revision()),
            (validate_evidence_locator, locator()),
            (validate_processing_receipt, processing_receipt()),
        ]
        for validator, value in values:
            with self.subTest(validator=validator.__name__):
                self.assertEqual(validator(value), value)
                self.assertIsNot(validator(value), value)

    def test_input_references_are_opaque_and_required(self):
        cases = [
            ("missing", lambda value: value.pop("storage_ref")),
            (
                "path-bearing storage reference",
                lambda value: value.__setitem__(
                    "storage_ref", {"path": "/tmp/private", "sha256": SHA_A}
                ),
            ),
            (
                "path-bearing source locator",
                lambda value: value.__setitem__(
                    "source_locator", {"path": "../../private"}
                ),
            ),
        ]
        for label, mutate in cases:
            value = input_artifact()
            mutate(value)
            with self.subTest(label=label), self.assertRaisesRegex(
                ValueError, "reference|path"
            ):
                validate_input_artifact(value)

    def test_source_locator_schema_rejects_hidden_or_path_bearing_values(self):
        cases = [
            ("nested path", "local_upload", {"metadata": {"path": "../../secret"}}),
            ("absolute POSIX path", "local_upload", {"upload_id": "/tmp/private"}),
            ("absolute Windows path", "local_upload", {"upload_id": "C:\\private"}),
            ("file URL", "qingxiaoda_url", {"url": "file:///etc/passwd"}),
            ("URL traversal", "qingxiaoda_url", {"url": "https://example.test/../private"}),
            ("reuse traversal", "project_reuse", {"artifact_id": "../artifact"}),
            ("unexpected field", "local_upload", {"upload_id": "upload-a", "path": "x"}),
        ]
        for label, source_kind, source_locator in cases:
            value = input_artifact()
            value["source_kind"] = source_kind
            value["source_locator"] = source_locator
            with self.subTest(label=label), self.assertRaisesRegex(
                ValueError, "source locator|HTTPS|path"
            ):
                validate_input_artifact(value)

    def test_each_source_kind_accepts_only_its_exact_opaque_locator(self):
        cases = [
            ("local_upload", {"upload_id": "upload-a"}),
            (
                "qingxiaoda_url",
                {"url": "https://files.example.test/material.pdf"},
            ),
            ("project_reuse", {"artifact_id": SHA_B}),
        ]
        for source_kind, source_locator in cases:
            value = input_artifact()
            value["source_kind"] = source_kind
            value["source_locator"] = source_locator
            with self.subTest(source_kind=source_kind):
                self.assertEqual(validate_input_artifact(value), value)

    def test_sha256_fields_reject_non_sha256_values(self):
        cases = [
            (validate_input_artifact, input_artifact(), "sha256"),
            (validate_evidence_locator, locator(), "content_hash"),
            (validate_processing_receipt, processing_receipt(), "input_sha256"),
            (validate_processing_receipt, processing_receipt(), "replay_key"),
        ]
        for validator, value, field in cases:
            value[field] = "not-a-sha"
            with self.subTest(field=field), self.assertRaisesRegex(
                ValueError, "SHA-256"
            ):
                validator(value)

    def test_state_axes_do_not_leak_between_contracts(self):
        cases = [
            (validate_input_artifact, input_artifact(), "transfer_status", "uploaded"),
            (
                validate_input_artifact,
                input_artifact(),
                "scientific_status",
                "accepted_evidence",
            ),
            (
                validate_research_material,
                research_material(),
                "processing_status",
                "ready",
            ),
            (
                validate_material_revision,
                material_revision(),
                "scientific_status",
                "accepted_evidence",
            ),
        ]
        for validator, value, field, leaked in cases:
            value[field] = leaked
            with self.subTest(field=field), self.assertRaisesRegex(
                ValueError, "unexpected field|state axis"
            ):
                validator(value)

    def test_locator_requires_revision_and_exact_kind_payload(self):
        missing_revision = locator()
        missing_revision.pop("revision_id")
        with self.assertRaisesRegex(ValueError, "revision_id"):
            validate_evidence_locator(missing_revision)

        invalid_text = locator(payload={"start_char": 0})
        with self.assertRaisesRegex(ValueError, "text_span locator"):
            validate_evidence_locator(invalid_text)

        spreadsheet = locator("spreadsheet_range", {"cell_range": "A1:B2"})
        with self.assertRaisesRegex(ValueError, "sheet identity"):
            validate_evidence_locator(spreadsheet)

        archive = locator("archive_member", {"member_path": "../secret.txt"})
        with self.assertRaisesRegex(ValueError, "archive member path"):
            validate_evidence_locator(archive)

    def test_locator_domains_and_boundaries_are_exact_for_every_kind(self):
        valid = {
            "text_span": {"start_char": 0, "end_char": 1},
            "pdf_region": {"page": 1, "normalized_rect": [0.0, 0.1, 0.9, 1.0]},
            "document_section": {
                "heading_path": ["Methods", "Inventory"],
                "paragraph_start": 0,
                "paragraph_end": 2,
            },
            "spreadsheet_range": {"sheet_name": "Inventory", "cell_range": "A1:B20"},
            "archive_member": {"member_path": "data/inventory.csv"},
            "json_pointer": {"pointer": "/inventory/0/name"},
            "xml_xpath": {"xpath": "/process/exchanges/exchange[1]"},
            "image_region": {"normalized_rect": [0.0, 0.0, 0.5, 0.5]},
            "audio_segment": {"start_ms": 0, "end_ms": 1000},
            "lca_object": {
                "dataset_uuid": "123e4567-e89b-12d3-a456-426614174000",
                "version": "1.0.0",
                "object_type": "process",
                "field_path": "/exchanges/0/amount",
            },
        }
        for kind, payload in valid.items():
            with self.subTest(kind=kind):
                validate_evidence_locator(locator(kind, payload))

        invalid = [
            ("text string bounds", "text_span", {"start_char": "0", "end_char": 1}),
            ("text negative", "text_span", {"start_char": -1, "end_char": 1}),
            ("text reversed", "text_span", {"start_char": 2, "end_char": 1}),
            ("text partial extra range", "text_span", {"start_char": 0, "end_char": 2, "end_line": 1}),
            ("PDF page type", "pdf_region", {"page": "1", "normalized_rect": [0, 0, 1, 1]}),
            ("PDF page zero", "pdf_region", {"page": 0, "normalized_rect": [0, 0, 1, 1]}),
            ("PDF inverted region", "pdf_region", {"page": 1, "normalized_rect": [0.8, 0, 0.2, 1]}),
            ("document empty heading", "document_section", {"heading_path": [], "paragraph_start": 0, "paragraph_end": 1}),
            ("document reversed", "document_section", {"heading_path": ["A"], "paragraph_start": 2, "paragraph_end": 1}),
            ("spreadsheet malformed", "spreadsheet_range", {"sheet_name": "S", "cell_range": "not-a-range"}),
            ("spreadsheet unbounded", "spreadsheet_range", {"sheet_name": "S", "cell_range": "A:A"}),
            ("spreadsheet reversed", "spreadsheet_range", {"sheet_name": "S", "cell_range": "B20:A1"}),
            ("archive absolute", "archive_member", {"member_path": "/etc/passwd"}),
            ("JSON malformed", "json_pointer", {"pointer": "inventory/0"}),
            ("JSON bad escape", "json_pointer", {"pointer": "/bad~2escape"}),
            ("XPath relative", "xml_xpath", {"xpath": "//exchange"}),
            ("XPath unsafe function", "xml_xpath", {"xpath": "/process/*[contains(.,'x')]"}),
            ("image degenerate", "image_region", {"normalized_rect": [0.5, 0, 0.5, 1]}),
            ("audio reversed", "audio_segment", {"start_ms": 1000, "end_ms": 999}),
            ("audio negative", "audio_segment", {"start_ms": -1, "end_ms": 2}),
            ("LCA bad UUID", "lca_object", {"dataset_uuid": "uuid", "version": "1", "object_type": "process", "field_path": "/x"}),
            ("LCA bad field", "lca_object", {"dataset_uuid": "123e4567-e89b-12d3-a456-426614174000", "version": "1", "object_type": "process", "field_path": "relative"}),
        ]
        for label, kind, payload in invalid:
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, "locator"):
                validate_evidence_locator(locator(kind, payload))

    def test_spreadsheet_coordinates_enforce_xlsx_endpoint_limits(self):
        valid_ranges = [
            "R1",
            "R1:R10",
            "R1:S2",
            "$R$1:$S$2",
            "XFD1048576",
            "A1:XFD1048576",
            "R1048576C16384",
            "R1C1:R1048576C16384",
        ]
        for cell_range in valid_ranges:
            with self.subTest(valid=cell_range):
                validate_evidence_locator(
                    locator(
                        "spreadsheet_range",
                        {"sheet_name": "Inventory", "cell_range": cell_range},
                    )
                )

        invalid_ranges = [
            "XFE1",
            "A1:XFE1048576",
            "A1048577",
            "A1:XFD1048577",
            "R1C16385",
            "R1C1:R1048576C16385",
            "R1048577C1",
            "R1C1:R1048577C16384",
        ]
        for cell_range in invalid_ranges:
            with self.subTest(invalid=cell_range), self.assertRaisesRegex(
                ValueError, "spreadsheet_range locator"
            ):
                validate_evidence_locator(
                    locator(
                        "spreadsheet_range",
                        {"sheet_name": "Inventory", "cell_range": cell_range},
                    )
                )

    def test_xpath_locator_uses_small_absolute_allowlisted_grammar(self):
        valid = [
            "/process/exchanges/exchange[1]",
            "/process/exchanges/exchange[@id='x-1']",
            "/process/exchanges/exchange/@amount",
            "/ns:process/ns:exchange[12]/@unit",
            "/process/exchange[@id=\"x-2\"]/@amount",
        ]
        for xpath in valid:
            with self.subTest(valid=xpath):
                validate_evidence_locator(locator("xml_xpath", {"xpath": xpath}))

        invalid = [
            "process/exchange",
            "/",
            "//exchange",
            "/process//exchange",
            "/process/[",
            "/process/exchange[]",
            "/process/exchange[0]",
            "/process/exchange[-1]",
            "/process/exchange[1",
            "/process/exchange1]",
            "/process/ /exchange",
            "/process/\t/exchange",
            "/process/ex change",
            "/process/*",
            "/process/exchange[@id]",
            "/process/exchange[@id = 'x']",
            "/process/exchange[text()='x']",
            "/process/exchange|/flow",
            "/process/..",
        ]
        for xpath in invalid:
            with self.subTest(invalid=xpath), self.assertRaisesRegex(
                ValueError, "xml_xpath locator"
            ):
                validate_evidence_locator(locator("xml_xpath", {"xpath": xpath}))

    def test_accepted_evidence_requires_assurance_acceptance(self):
        value = locator()
        value["scientific_status"] = "accepted_evidence"
        with self.assertRaisesRegex(ValueError, "Assurance acceptance"):
            validate_evidence_locator(value)

    def test_user_material_cannot_claim_tiangong_official_authority(self):
        value = research_material()
        value["authority"] = "tiangong_official_data"
        with self.assertRaisesRegex(ValueError, "user material.*TianGong"):
            validate_research_material(value)

    def test_selected_locator_revision_must_be_allowed(self):
        value = {
            "contract": "lca_research_kernel.material_use_decision/v1",
            "allowed_revision_ids": ["rev-a"],
            "selected_evidence_locator_ids": ["loc-from-rev-b"],
            "selection_reasons": {"loc-from-rev-b": "relevant"},
            "exclusions": [],
            "context_mode": "focused_retrieval",
            "expected_state_change": {"desired_artifact": "answer"},
            "claim_ceiling": "candidate_only",
        }
        with self.assertRaisesRegex(ValueError, "selected material is not allowed"):
            validate_material_use_decision(
                value,
                locator_index={"loc-from-rev-b": {"revision_id": "rev-b"}},
            )

    def test_material_use_decision_accepts_exact_allowed_selection(self):
        value = {
            "contract": "lca_research_kernel.material_use_decision/v1",
            "allowed_revision_ids": ["rev-a"],
            "selected_evidence_locator_ids": ["loc-a"],
            "selection_reasons": {"loc-a": "supports the requested inventory"},
            "exclusions": [],
            "context_mode": "focused_retrieval",
            "expected_state_change": {"desired_artifact": "answer"},
            "claim_ceiling": "candidate_only",
        }
        self.assertEqual(
            validate_material_use_decision(
                value, locator_index={"loc-a": {"revision_id": "rev-a"}}
            ),
            value,
        )

    def test_v2_identity_is_stable_across_unrelated_and_decision_events(self):
        view = material_project_view()
        original = material_use_decision_v2(view)
        for mutate in (
            lambda candidate: candidate.__setitem__(
                "unrelated_state", {"generation": 2, "status": "started"}
            ),
            lambda candidate: candidate.__setitem__(
                "last_event_sha256", "d" * 64
            ),
            lambda candidate: candidate["material_use_decisions"].__setitem__(
                original["task_id"], deepcopy(original)
            ),
        ):
            candidate = deepcopy(view)
            mutate(candidate)
            self.assertEqual(
                material_use_decision_v2(candidate)["task_id"],
                original["task_id"],
            )
            self.assertEqual(
                material_selection_basis(candidate), original["selection_basis"]
            )

    def test_v2_identity_changes_for_every_explicit_decision_input(self):
        baseline = material_use_decision_v2()
        cases = {
            "task kind": {"task_kind": "citation_material_selection"},
            "question": {"question": {"objective": "What is the boundary?"}},
            "allowed revisions": {"allowed_revision_ids": ["revision-a", "revision-b"]},
            "selected locator": {
                "selected_evidence_locator_ids": [],
                "selection_reasons": {},
            },
            "selection reason": {
                "selection_reasons": {"locator-a": "needed for functional unit"}
            },
            "exclusion": {"exclusions": []},
            "context mode": {"context_mode": "full_context"},
            "expected change": {
                "expected_state_change": {"desired_artifact": "scope"}
            },
            "claim ceiling": {"claim_ceiling": "source_bound_bounded"},
        }
        for label, overrides in cases.items():
            with self.subTest(label=label):
                changed = material_use_decision_v2(**overrides)
                self.assertNotEqual(changed["task_id"], baseline["task_id"])

    def test_material_mutation_changes_basis_but_not_decision_identity(self):
        original_view = material_project_view()
        original = material_use_decision_v2(original_view)
        mutated_view = deepcopy(original_view)
        mutated_view["evidence_locators"]["locator-a"]["content_hash"] = "d" * 64
        mutated = material_use_decision_v2(mutated_view)
        self.assertEqual(mutated["task_id"], original["task_id"])
        self.assertNotEqual(mutated["selection_basis"], original["selection_basis"])

    def test_v2_builder_rejects_locator_index_outside_committed_project_view(self):
        view = material_project_view()
        outside = deepcopy(view["evidence_locators"]["locator-a"])
        outside["locator_id"] = "locator-outside"

        with self.assertRaisesRegex(ValueError, "locator index.*Project Record"):
            material_use_decision_v2(
                view,
                selected_evidence_locator_ids=["locator-outside"],
                selection_reasons={"locator-outside": "not committed"},
                locator_index={"locator-outside": outside},
            )

    def test_v1_remains_replay_valid_without_rewriting(self):
        historical = {
            "contract": "lca_research_kernel.material_use_decision/v1",
            "task_id": "historical-material-use",
            "allowed_revision_ids": ["revision-a"],
            "selected_evidence_locator_ids": ["locator-a"],
            "selection_reasons": {"locator-a": "historical reason"},
            "exclusions": [],
            "context_mode": "focused_retrieval",
            "expected_state_change": {"desired_artifact": "answer"},
            "claim_ceiling": "candidate_only",
        }
        self.assertEqual(
            validate_material_use_decision(
                historical,
                locator_index=material_project_view()["evidence_locators"],
            ),
            historical,
        )

    def test_checkpoint_requires_replay_verified_project_record_membership(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "project-a"
            record = ProjectRecord.open_or_create_project(
                workspace,
                project_id="project-a",
                title="Material checkpoint",
                objective="What is the functional unit?",
                created_at="2026-08-12T02:00:00Z",
            )
            selection_view = record.read_current_project_view()
            decision = empty_material_use_decision(record)
            accepted_event = append_accepted_material_use_decision(record, decision)
            checkpoint = material_use_decision_checkpoint(
                decision,
                accepted_decision_event_sha256=accepted_event["event_sha256"],
                locator_index={},
            )

            self.assertEqual(
                record.validate_material_use_decision_checkpoint(
                    checkpoint,
                    decision=decision,
                    project_view=selection_view,
                    locator_index={},
                ),
                checkpoint,
            )

            reopened = ProjectRecord.open_or_create_project(workspace)
            self.assertEqual(
                reopened.validate_material_use_decision_checkpoint(
                    checkpoint,
                    decision=decision,
                    project_view=selection_view,
                    locator_index={},
                ),
                checkpoint,
            )

    def test_material_execution_snapshot_resolves_one_current_authority_view(self):
        with tempfile.TemporaryDirectory() as temporary:
            record = ProjectRecord.open_or_create_project(
                Path(temporary) / "project-a",
                project_id="project-a",
                title="Material execution",
                objective="Explain the accepted material.",
                created_at="2026-08-12T02:00:00Z",
            )
            decision = empty_material_use_decision(record)
            accepted_event = append_accepted_material_use_decision(record, decision)
            replay_count = 0
            original_replay = record._replay

            def counted_replay(*args, **kwargs):
                nonlocal replay_count
                replay_count += 1
                return original_replay(*args, **kwargs)

            record._replay = counted_replay  # type: ignore[method-assign]
            snapshot = record.resolve_material_execution(
                decision_id=decision["task_id"],
                decision_event_sha256=accepted_event["event_sha256"],
                expected_decision_sha256=canonical_sha256(decision),
            )

            self.assertEqual(snapshot.decision, decision)
            self.assertEqual(
                snapshot.decision_event_sha256, accepted_event["event_sha256"]
            )
            self.assertEqual(
                snapshot.current_head_sha256,
                snapshot.project_view["last_event_sha256"],
            )
            self.assertEqual(replay_count, 1)

    def test_material_execution_snapshot_fails_closed_after_state_change(self):
        from lca_research_kernel.assurance import AssuranceBoundary

        with tempfile.TemporaryDirectory() as temporary:
            record = ProjectRecord.open_or_create_project(
                Path(temporary) / "project-a",
                project_id="project-a",
                title="Material execution",
                objective="Explain the accepted material.",
                created_at="2026-08-12T02:00:00Z",
            )
            decision = empty_material_use_decision(record)
            accepted_event = append_accepted_material_use_decision(record, decision)
            snapshot = record.resolve_material_execution(
                decision_id=decision["task_id"],
                decision_event_sha256=accepted_event["event_sha256"],
                expected_decision_sha256=canonical_sha256(decision),
            )
            candidate = {
                "contract": "lca_research_kernel.project_candidate_change/v1",
                "candidate_id": "advance-after-material-decision",
                "change_type": "route.updated",
                "next_route": "changed-after-material-decision",
                "proposed_at": "2026-08-12T02:00:00Z",
            }
            acceptance = AssuranceBoundary().evaluate_candidate_change(
                candidate, record.read_current_project_view()
            )
            record.append_accepted_change(
                candidate,
                acceptance,
                idempotency_key="advance-after-material-decision",
            )

            with self.assertRaisesRegex(
                ValueError, "not accepted before consumption"
            ):
                record.assert_material_execution_current(snapshot)

    def test_checkpoint_rejects_nonmember_foreign_and_corrupt_audit_events(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = ProjectRecord.open_or_create_project(
                root / "project-a",
                project_id="project-a",
                title="Material checkpoint",
                objective="What is the functional unit?",
                created_at="2026-08-12T02:00:00Z",
            )
            selection_view = record.read_current_project_view()
            decision = empty_material_use_decision(record)
            accepted_event = append_accepted_material_use_decision(record, decision)
            checkpoint = material_use_decision_checkpoint(
                decision,
                accepted_decision_event_sha256=accepted_event["event_sha256"],
                locator_index={},
            )

            forged = deepcopy(accepted_event)
            forged["sequence"] = 99
            forged["event_id"] = "synthetic-event"
            forged["event_sha256"] = canonical_sha256(
                {key: value for key, value in forged.items() if key != "event_sha256"}
            )
            forged_checkpoint = deepcopy(checkpoint)
            forged_checkpoint["accepted_decision_event_sha256"] = forged[
                "event_sha256"
            ]
            with self.assertRaisesRegex(ValueError, "accepted.*not.*Project Record"):
                record.validate_material_use_decision_checkpoint(
                    forged_checkpoint,
                    decision=decision,
                    project_view=selection_view,
                    locator_index={},
                )

            foreign = ProjectRecord.open_or_create_project(
                root / "project-b",
                project_id="project-b",
                title="Other checkpoint",
                objective="What is the functional unit?",
                created_at="2026-08-12T02:00:00Z",
            )
            foreign_event = append_accepted_material_use_decision(
                foreign, decision
            )
            foreign_checkpoint = deepcopy(checkpoint)
            foreign_checkpoint["accepted_decision_event_sha256"] = foreign_event[
                "event_sha256"
            ]
            with self.assertRaisesRegex(ValueError, "another Project Record"):
                foreign.validate_material_use_decision_checkpoint(
                    foreign_checkpoint,
                    decision=decision,
                    project_view=selection_view,
                    locator_index={},
                )

            lines = record.event_path.read_text(encoding="utf-8").splitlines()
            corrupted = json.loads(lines[0])
            corrupted["payload"]["title"] = "corrupted"
            lines[0] = json.dumps(corrupted, ensure_ascii=False, sort_keys=True)
            record.event_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "content hash|hash chain"):
                record.validate_material_use_decision_checkpoint(
                    checkpoint,
                    decision=decision,
                    project_view=selection_view,
                    locator_index={},
                )

    def test_v2_checkpoint_still_fails_closed_on_shape_and_state_drift(self):
        view = material_project_view()
        decision = material_use_decision_v2(view)
        malformed = deepcopy(decision)
        malformed["task_id"] = "material-use-tampered"
        with self.assertRaisesRegex(ValueError, "identity"):
            validate_material_use_decision(
                malformed, locator_index=view["evidence_locators"]
            )
        malformed = deepcopy(decision)
        malformed["selection_basis"]["material_state_sha256"] = "not-a-sha"
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            validate_material_use_decision(
                malformed, locator_index=view["evidence_locators"]
            )

        with self.assertRaisesRegex(ValueError, "requires v2"):
            material_use_decision_checkpoint(
                {
                    "contract": "lca_research_kernel.material_use_decision/v1",
                    "task_id": "historical-material-use",
                    "allowed_revision_ids": ["revision-a"],
                    "selected_evidence_locator_ids": ["locator-a"],
                    "selection_reasons": {"locator-a": "historical reason"},
                    "exclusions": [],
                    "context_mode": "focused_retrieval",
                    "expected_state_change": {"desired_artifact": "answer"},
                    "claim_ceiling": "candidate_only",
                },
                accepted_decision_event_sha256="e" * 64,
                locator_index=view["evidence_locators"],
            )

    def test_v2_builder_fails_closed_on_partial_material_state(self):
        empty = {
            "contract": "lca_research_kernel.project_record_view/v1",
            "project_id": "project-a",
        }
        self.assertEqual(material_selection_basis(empty)["contract"], "lca_research_kernel.material_selection_basis/v1")
        incomplete = material_project_view()
        incomplete.pop("evidence_locators")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            material_use_decision_v2(incomplete, locator_index={})
        with self.assertRaisesRegex(ValueError, "canonical JSON"):
            material_use_decision_v2(question={"invalid": float("nan")})

    def test_policy_loader_fails_closed_and_returns_copy(self):
        policy_path = (
            Path(__file__).resolve().parents[1]
            / "lca_research_kernel/contracts/research-materials-policy-v1.json"
        )
        first = load_research_materials_policy(policy_path)
        self.assertEqual(first["capacity"]["generic_file_max_bytes"], 104857600)
        self.assertEqual(first["capacity"]["multipart_part_bytes"], 8388608)
        self.assertIn("structured_document_parse", first["processing_modes"])
        self.assertIn("application/pdf", first["allowed_types"])
        self.assertEqual(first["archive_limits"]["member_count"], 1000)
        self.assertGreater(first["preview_ceilings"]["text_characters"], 0)

        first["capacity"]["generic_file_max_bytes"] = 1
        self.assertEqual(
            load_research_materials_policy(policy_path)["capacity"][
                "generic_file_max_bytes"
            ],
            104857600,
        )

        with tempfile.TemporaryDirectory() as temporary:
            invalid = Path(temporary) / "invalid.json"
            invalid.write_text(json.dumps({"contract": "wrong"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "policy"):
                load_research_materials_policy(invalid)

    def test_policy_rejects_mime_extension_authority_drift_and_conflicts(self):
        policy_path = (
            Path(__file__).resolve().parents[1]
            / "lca_research_kernel/contracts/research-materials-policy-v1.json"
        )
        canonical = json.loads(policy_path.read_text(encoding="utf-8"))
        mutations = []
        replaced = deepcopy(canonical)
        replaced["allowed_types"] = {"application/x-executable": [".exe"]}
        mutations.append(("replaced authority", replaced))
        removed = deepcopy(canonical)
        removed["allowed_types"].pop("application/pdf")
        mutations.append(("removed mapping", removed))
        added = deepcopy(canonical)
        added["allowed_types"]["application/x-new"] = [".new"]
        mutations.append(("added mapping", added))
        duplicate = deepcopy(canonical)
        duplicate["allowed_types"]["text/plain"] = [".txt", ".txt"]
        mutations.append(("duplicate in list", duplicate))
        conflict = deepcopy(canonical)
        conflict["allowed_types"]["application/pdf"].append(".txt")
        mutations.append(("extension conflict", conflict))
        malformed = deepcopy(canonical)
        malformed["allowed_types"]["application pdf"] = malformed["allowed_types"].pop("application/pdf")
        mutations.append(("malformed MIME", malformed))

        with tempfile.TemporaryDirectory() as temporary:
            for index, (label, value) in enumerate(mutations):
                path = Path(temporary) / f"policy-{index}.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.subTest(label=label), self.assertRaisesRegex(
                    ValueError, "MIME|extension|authority|drifted"
                ):
                    load_research_materials_policy(path)


if __name__ == "__main__":
    unittest.main()
