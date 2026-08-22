import ast
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "lca_research_kernel/project_record.py"


def canonical_sha256(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class ProjectRecordPortTests(unittest.TestCase):
    def setUp(self):
        from lca_research_kernel.project_record import ProjectRecord

        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name) / "record"
        self.record = ProjectRecord.open_or_create_project(
            self.workspace,
            project_id="d18-port-test",
            title="D-18 Project Record",
            objective="Prove one accepted project truth.",
            created_at="2026-07-23T03:00:00Z",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def candidate(self):
        return {
            "contract": "lca_research_kernel.project_candidate_change/v1",
            "candidate_id": "candidate-functional-unit",
            "change_type": "fact.upserted",
            "fact": {
                "fact_id": "functional-unit",
                "classification": "official",
                "value": 1,
                "unit": "kg product",
                "source": {
                    "authority": "TianGong",
                    "identity": "process@example-version",
                },
            },
            "next_route": "select-system-boundary",
            "proposed_at": "2026-07-23T03:01:00Z",
        }

    def acceptance(self, candidate):
        return {
            "contract": "lca_research_kernel.assurance_acceptance/v1",
            "decision": "accepted",
            "candidate_sha256": canonical_sha256(candidate),
            "evaluator": "assurance_boundary",
            "evaluated_at": "2026-07-23T03:02:00Z",
        }

    def test_port_is_case_independent_and_exposes_only_the_small_interface(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        joined = "\n".join(imports).lower()
        for forbidden in ("project", "council", "uslci", "lifepo4", "nev"):
            self.assertNotIn(forbidden, joined)
        expected = {
            "open_or_create_project",
            "read_current_project_view",
            "append_accepted_change",
            "register_artifact",
            "rebuild_and_verify",
            "read_audit_trail",
        }
        from lca_research_kernel.project_record import ProjectRecord

        self.assertTrue(expected.issubset(set(dir(ProjectRecord))))

    def test_only_content_bound_assurance_acceptance_can_change_project_truth(self):
        candidate = self.candidate()
        before = self.record.event_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "assurance acceptance"):
            self.record.append_accepted_change(candidate, None, idempotency_key="fact:fu")
        self.assertEqual(self.record.event_path.read_bytes(), before)
        drifted = self.acceptance(candidate)
        drifted["candidate_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "candidate hash"):
            self.record.append_accepted_change(candidate, drifted, idempotency_key="fact:fu")
        self.assertEqual(self.record.event_path.read_bytes(), before)

        event = self.record.append_accepted_change(
            candidate,
            self.acceptance(candidate),
            idempotency_key="fact:fu",
        )
        self.assertEqual(event["actor"], "assurance_boundary")
        self.assertEqual(event["event_type"], "research.project.accepted_change")
        view = self.record.read_current_project_view()
        self.assertEqual(view["facts"]["functional-unit"]["value"], 1)
        self.assertEqual(view["next_route"], "select-system-boundary")

    def test_same_key_is_idempotent_and_changed_content_fails(self):
        candidate = self.candidate()
        acceptance = self.acceptance(candidate)
        first = self.record.append_accepted_change(
            candidate, acceptance, idempotency_key="fact:fu"
        )
        count = len(self.record.read_audit_trail())
        self.assertEqual(
            self.record.append_accepted_change(
                candidate, acceptance, idempotency_key="fact:fu"
            ),
            first,
        )
        self.assertEqual(len(self.record.read_audit_trail()), count)
        changed = copy.deepcopy(candidate)
        changed["fact"]["value"] = 2
        with self.assertRaisesRegex(ValueError, "reused with different content"):
            self.record.append_accepted_change(
                changed, self.acceptance(changed), idempotency_key="fact:fu"
            )

    def test_expected_head_prevents_stale_assurance_from_becoming_truth(self):
        expected_head = self.record.read_current_project_view()[
            "last_event_sha256"
        ]
        concurrent = self.candidate()
        concurrent["candidate_id"] = "candidate-concurrent-boundary"
        concurrent["fact"]["fact_id"] = "system-boundary"
        concurrent["fact"]["value"] = "cradle-to-gate"
        self.record.append_accepted_change(
            concurrent,
            self.acceptance(concurrent),
            idempotency_key="fact:boundary",
        )

        stale = self.candidate()
        with self.assertRaisesRegex(ValueError, "Project Record head changed"):
            self.record.append_accepted_change(
                stale,
                self.acceptance(stale),
                idempotency_key="fact:fu",
                expected_last_event_sha256=expected_head,
            )

        self.assertNotIn(
            "functional-unit",
            self.record.read_current_project_view()["facts"],
        )

    def test_expected_head_keeps_exact_retry_idempotent(self):
        expected_head = self.record.read_current_project_view()[
            "last_event_sha256"
        ]
        candidate = self.candidate()
        acceptance = self.acceptance(candidate)

        first = self.record.append_accepted_change(
            candidate,
            acceptance,
            idempotency_key="fact:fu",
            expected_last_event_sha256=expected_head,
        )
        second = self.record.append_accepted_change(
            candidate,
            acceptance,
            idempotency_key="fact:fu",
            expected_last_event_sha256=expected_head,
        )

        self.assertEqual(second, first)
        self.assertEqual(
            len(
                [
                    event
                    for event in self.record.read_audit_trail()
                    if event["idempotency_key"] == "fact:fu"
                ]
            ),
            1,
        )

    def test_artifact_registration_is_content_addressed_and_drift_fails_closed(self):
        artifact = self.workspace / "artifacts/result.json"
        artifact.parent.mkdir(parents=True)
        artifact.write_text('{"result":1}\n', encoding="utf-8")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        acceptance = {
            "contract": "lca_research_kernel.assurance_artifact_acceptance/v1",
            "decision": "accepted",
            "artifact_sha256": digest,
            "evaluator": "assurance_boundary",
            "evaluated_at": "2026-07-23T03:03:00Z",
        }
        registered = self.record.register_artifact(
            "artifacts/result.json",
            role="research_result",
            acceptance=acceptance,
            idempotency_key="artifact:result",
            recorded_at="2026-07-23T03:04:00Z",
        )
        self.assertEqual(registered["payload"]["sha256"], digest)
        self.assertEqual(self.record.rebuild_and_verify()["status"], "passed")
        artifact.write_text('{"result":2}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "registered artifact drifted"):
            self.record.rebuild_and_verify()

    def test_artifact_derivation_rejects_unknown_material_relationships(self):
        artifact = self.workspace / "artifacts/result.json"
        artifact.parent.mkdir(parents=True)
        artifact.write_text('{"result":1}\n', encoding="utf-8")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        acceptance = {
            "contract": "lca_research_kernel.assurance_artifact_acceptance/v1",
            "decision": "accepted",
            "artifact_sha256": digest,
            "evaluator": "assurance_boundary",
            "evaluated_at": "2026-07-23T03:03:00Z",
        }

        with self.assertRaisesRegex(ValueError, "artifact derivation .* absent"):
            self.record.register_artifact(
                "artifacts/result.json",
                role="research_result",
                acceptance=acceptance,
                idempotency_key="artifact:unknown-derivation",
                recorded_at="2026-07-23T03:04:00Z",
                derived_from={
                    "material_revision_ids": ["unknown-revision"],
                    "evidence_locator_ids": [],
                },
            )

    def test_projection_is_rebuilt_from_history_and_event_mutation_is_rejected(self):
        candidate = self.candidate()
        self.record.append_accepted_change(
            candidate, self.acceptance(candidate), idempotency_key="fact:fu"
        )
        expected = self.record.read_current_project_view()
        self.record.projection_path.unlink()
        self.assertEqual(self.record.rebuild_and_verify()["status"], "passed")
        self.assertEqual(self.record.read_current_project_view(), expected)
        lines = self.record.event_path.read_text(encoding="utf-8").splitlines()
        event = json.loads(lines[-1])
        event["sequence"] = 99
        lines[-1] = json.dumps(event, ensure_ascii=False, sort_keys=True)
        self.record.event_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "sequence"):
            self.record.rebuild_and_verify()

    def test_exact_historical_accepted_result_is_resolved_after_reopen(self):
        from lca_research_kernel.project_record import ProjectRecord

        results = [
            {"status": "candidate_only", "source_sha256s": ["a" * 64]},
            {"status": "candidate_only", "source_sha256s": ["b" * 64]},
        ]
        for index, result in enumerate(results, start=1):
            candidate = {
                "contract": "lca_research_kernel.project_candidate_change/v1",
                "candidate_id": f"accepted-result-{index}",
                "change_type": "result.accepted",
                "result": result,
                "next_route": f"after-result-{index}",
                "proposed_at": f"2026-07-23T03:0{index}:00Z",
            }
            self.record.append_accepted_change(
                candidate,
                self.acceptance(candidate),
                idempotency_key=f"result:{index}",
            )

        reopened = ProjectRecord.open_or_create_project(self.workspace)
        self.assertEqual(
            reopened.resolve_accepted_result(canonical_sha256(results[0])),
            results[0],
        )
        self.assertEqual(
            reopened.read_current_project_view()["result"],
            results[1],
        )
        with self.assertRaisesRegex(ValueError, "accepted result is unavailable"):
            reopened.resolve_accepted_result("f" * 64)

    def test_agent_startup_receipt_is_idempotent_and_replayable(self):
        from lca_research_kernel.agent_bootstrap import AgentBootstrap

        startup = AgentBootstrap.load(
            ROOT,
            ROOT
            / "lca_research_kernel/contracts/agent-startup-manifest-v2.json",
        )
        first = self.record.record_agent_startup(
            startup,
            run_id="runtime-1",
            observed_at="2026-07-23T06:20:00Z",
        )
        again = self.record.record_agent_startup(
            startup,
            run_id="runtime-1",
            observed_at="2026-07-23T06:20:00Z",
        )
        self.assertEqual(first, again)
        events = self.record.read_audit_trail()
        self.assertEqual(
            [row["event_type"] for row in events].count("research.agent.started"),
            1,
        )
        view = self.record.read_current_project_view()
        self.assertEqual(view["agent_startups"]["runtime-1"]["product_id"], "tiangong_native_lca_research_agent")
        self.record.projection_path.unlink()
        self.assertEqual(self.record.rebuild_and_verify()["status"], "passed")
        self.assertEqual(
            self.record.read_current_project_view()["agent_startups"],
            view["agent_startups"],
        )

    def test_assurance_accepts_and_project_record_adopts_one_catalog_snapshot(self):
        from lca_research_kernel.assurance import AssuranceBoundary

        summary = {
            "contract": "lca_research_kernel.capability_catalog_summary/v1",
            "catalog_id": "catalog-1",
            "catalog_snapshot_sha256": "a" * 64,
            "source_lock_sha256": "b" * 64,
            "capability_count": 53,
            "unclassified_count": 0,
            "evaluated_at": "2026-07-23T06:25:00Z",
        }
        acceptance = AssuranceBoundary().evaluate_capability_catalog(summary)
        self.assertEqual(acceptance["decision"], "accepted")
        first = self.record.adopt_capability_catalog(
            summary,
            acceptance=acceptance,
            idempotency_key="catalog:catalog-1",
        )
        self.assertEqual(first["event_type"], "research.capability.catalog_adopted")
        view = self.record.read_current_project_view()
        self.assertEqual(view["capability_catalog"]["catalog_id"], "catalog-1")
        self.assertEqual(
            self.record.adopt_capability_catalog(
                summary,
                acceptance=acceptance,
                idempotency_key="catalog:catalog-1",
            ),
            first,
        )
        rejected_summary = {**summary, "unclassified_count": 1}
        rejected = AssuranceBoundary().evaluate_capability_catalog(
            rejected_summary
        )
        self.assertEqual(rejected["decision"], "rejected")
        with self.assertRaisesRegex(ValueError, "catalog assurance"):
            self.record.adopt_capability_catalog(
                rejected_summary,
                acceptance=rejected,
                idempotency_key="catalog:rejected",
            )


if __name__ == "__main__":
    unittest.main()
