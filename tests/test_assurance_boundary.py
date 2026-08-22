import ast
import hashlib
import json
import unittest
from copy import deepcopy
from pathlib import Path

from lca_research_kernel.project_record import canonical_sha256


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "lca_research_kernel/assurance.py"


def operation_intent(effect="local_write", tags=()):
    payload = {"action": "bounded local operation"}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "contract": "lca_research_kernel.operation_intent/v1",
        "operation_id": "operation-1",
        "effect": effect,
        "responsibility_tags": list(tags),
        "target": "project-local",
        "scope": "research:fixture",
        "payload": payload,
        "payload_ref": {
            "path": "outputs/prepared-action.json",
            "sha256": digest,
            "version": "v1",
        },
        "verification": "verify deterministic result",
        "compensation": "remove unaccepted local output",
        "resume_route": "resume-after-action",
    }


def candidate(classification="candidate", authority="model_candidate_only"):
    return {
        "contract": "lca_research_kernel.project_candidate_change/v1",
        "candidate_id": "candidate-1",
        "change_type": "fact.upserted",
        "fact": {
            "fact_id": "analysis-1",
            "classification": classification,
            "value": {"finding": "bounded"},
            "source": {"authority": authority, "candidate_id": "model-1"},
        },
        "next_route": "continue-bounded-research",
        "proposed_at": "2026-07-23T05:00:00Z",
    }


class AssuranceBoundaryTests(unittest.TestCase):
    def setUp(self):
        from lca_research_kernel.assurance import AssuranceBoundary

        self.assurance = AssuranceBoundary()

    def test_permission_decisions_reuse_the_existing_responsibility_boundary(self):
        allowed = self.assurance.authorize_action(operation_intent())
        self.assertEqual(allowed["disposition"], "proceed")
        self.assertTrue(allowed["authorized"])
        external = self.assurance.authorize_action(
            operation_intent("external_write", ["external_message"])
        )
        self.assertEqual(external["disposition"], "prepare_handoff")
        self.assertFalse(external["authorized"])
        self.assertEqual(external["executed_external_effects"], 0)

    def test_model_or_tool_output_cannot_be_promoted_to_official_fact(self):
        rejected = self.assurance.evaluate_candidate_change(
            candidate("official", "model_candidate_only"), {"facts": {}}
        )
        self.assertEqual(rejected["decision"], "rejected")
        self.assertIn("candidate_source_cannot_create_official_fact", rejected["errors"])
        rejected = self.assurance.evaluate_candidate_change(
            candidate("official", "capability_sourced_result"), {"facts": {}}
        )
        self.assertEqual(rejected["decision"], "rejected")
        accepted_candidate = candidate()
        accepted = self.assurance.evaluate_candidate_change(
            accepted_candidate, {"facts": {}}
        )
        self.assertEqual(accepted["decision"], "accepted")
        self.assertEqual(
            accepted["candidate_sha256"], canonical_sha256(accepted_candidate)
        )

    def test_lca_material_candidate_is_checked_by_the_domain_kernel(self):
        material = candidate("official", "TianGong")
        material["fact"].update(
            {
                "domain_kind": "lca_material",
                "source_ref": {"path": "inputs/official.json", "sha256": "a" * 64},
                "authority": "TianGong",
                "identity": "process-1",
                "version": "01.01.000",
                "value": 1,
                "unit": "kg",
            }
        )
        material["fact"]["source"] = {"authority": "TianGong"}
        accepted = self.assurance.evaluate_candidate_change(material, {"facts": {}})
        self.assertEqual(accepted["decision"], "accepted")
        material["fact"].pop("version")
        rejected = self.assurance.evaluate_candidate_change(material, {"facts": {}})
        self.assertEqual(rejected["decision"], "rejected")
        self.assertIn("invalid_lca_material", rejected["errors"])

    def test_official_receipt_identity_must_match_authorized_operation_and_run(self):
        receipt = {
            "contract": "official_public_source_receipt/v1",
            "question_id": "wrong-question",
            "run_id": "wrong-run",
            "body_sha256": "a" * 64,
            "body_bytes": 4,
        }
        receipt["receipt_sha256"] = canonical_sha256(receipt)
        content = {
            "contract": "official_public_source_content/v1",
            "source_receipt": receipt,
            "cache_ref": {
                "contract": "official_public_source_cache_ref/v1",
                "path": "objects/" + "a" * 64 + ".bin",
                "sha256": "a" * 64,
                "bytes": 4,
            },
            "content_excerpt": {
                "contract": "official_public_source_excerpt/v1",
                "text": "must",
                "source_body_sha256": "a" * 64,
            },
        }
        result = {
            "contract": "lca_research_kernel.sourced_capability_result/v1",
            "capability_id": "official-public-source-read",
            "status": "sourced_result_not_fact",
            "effect": "external_read",
            "source_identity": "https://ec.europa.eu/source",
            "source_version": "sha256:" + "a" * 64,
            "content": content,
            "content_sha256": canonical_sha256(content),
            "executed_external_writes": 0,
        }
        intent = operation_intent(effect="external_read")
        intent["target"] = "official-public-source-read"
        intent["scope"] = "research:expected-question"
        intent["payload"] = {
            "question_id": "expected-question",
            "run_id": "expected-run",
        }

        acceptance = self.assurance.evaluate_sourced_capability_result(
            result,
            operation_intent=intent,
            project_id="expected-question",
            run_id="expected-run",
        )

        self.assertEqual(acceptance["decision"], "rejected")
        self.assertIn("official_source_operation_identity_drifted", acceptance["errors"])

    def test_trajectory_and_outcome_are_both_evaluated(self):
        trajectory = self.assurance.evaluate_trajectory(
            [
                {"actor": "research_orchestrator", "effect": "local_read"},
                {"actor": "assurance_boundary", "effect": "local_write"},
            ],
            {"accepted_project_record_writers": ["assurance_boundary"]},
        )
        self.assertEqual(trajectory["status"], "passed")
        failed = self.assurance.evaluate_trajectory(
            [{"actor": "model_gateway", "effect": "accepted_project_record_write"}],
            {"accepted_project_record_writers": ["assurance_boundary"]},
        )
        self.assertEqual(failed["status"], "failed")
        self.assertIn("forbidden_project_record_writer", failed["errors"])

        outcome = self.assurance.evaluate_outcome(
            {
                "goal_scope": {
                    "research_goal": "Bounded LCA",
                    "functional_unit": {
                        "amount": 1,
                        "unit": "kg product",
                        "reference_flow_id": "flow-1",
                    },
                    "system_boundary": ["foreground"],
                    "method_basis": {"authority": "TianGong", "version": "01.00.000"},
                },
                "calculation": {
                    "calculation_id": "calc-1",
                    "method": {"identity": "method-1", "version": "01.00.000"},
                    "input_fact_ids": ["official-1"],
                    "result": {"value": 1, "unit": "kg CO2 eq"},
                    "unit_groups": {"kg CO2 eq": ["impact-1"]},
                    "replay_command": "deterministic replay",
                },
                "fact_classifications": ["official", "computed"],
                "decision_relevant_missing": [],
            },
            {"required_authority": "TianGong"},
        )
        self.assertEqual(outcome["status"], "passed")
        self.assertEqual(outcome["claim_boundary"]["claim_level"], "evidence_bounded")

    def test_failure_routing_uses_the_five_complex_layers(self):
        for layer in ("prompt", "context", "harness", "loop", "model"):
            with self.subTest(layer=layer):
                route = self.assurance.route_failure(
                    {"layer": layer, "code": f"{layer}_failure"}
                )
                self.assertEqual(route["layer"], layer)
                self.assertTrue(route["next_route"])
                self.assertFalse(route["may_promote_candidate_to_fact"])

    def test_formal_results_require_claim_classes_and_complete_limited_gap(self):
        base = {
            "contract": "lca_research_kernel.project_candidate_change/v1",
            "candidate_id": "formal-result-candidate",
            "change_type": "result.accepted",
            "result": {
                "status": "formal_limited_result_accepted",
                "source_basis": [],
                "missing_facts": [],
                "claim_ceiling": "",
                "resume_conditions": [],
                "computed_values": [],
                "claim_classifications": [
                    {
                        "classification": "legal_requirement",
                        "statement": "A legal duty applies.",
                        "scientific_source_sha256s": [],
                    }
                ],
                "domain_acceptance": {
                    "contract": (
                        "lca_research_kernel."
                        "generic_source_candidate_evaluation/v1"
                    ),
                    "status": "passed",
                    "may_promote_to_official": False,
                },
            },
            "next_route": "deliver-source-bound-result",
            "proposed_at": "2026-07-26T16:00:00Z",
        }
        rejected = self.assurance.evaluate_candidate_change(
            base,
            {"facts": {}, "artifacts": {}},
        )
        self.assertEqual(rejected["decision"], "rejected")
        self.assertIn(
            "accepted_limited_result_invalid",
            rejected["errors"],
        )

        missing_class = deepcopy(base)
        missing_class["result"]["status"] = "formal_result_accepted"
        missing_class["result"]["source_basis"] = [
            {"authority": "user_supplied_project_input", "sha256": "a" * 64}
        ]
        missing_class["result"]["claim_ceiling"] = "source_bounded_interpretation"
        missing_class["result"]["claim_classifications"] = []
        missing_class["result"]["claim_statements"] = [
            {
                "modality": "legal_must",
                "statement": "A legal duty applies.",
            }
        ]
        rejected = self.assurance.evaluate_candidate_change(
            missing_class,
            {"facts": {}, "artifacts": {}},
        )
        self.assertIn(
            "accepted_result_claim_classification_missing",
            rejected["errors"],
        )

    def test_assurance_has_no_case_or_council_dependency(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        joined = "\n".join(imports).lower()
        for forbidden in ("council", "uslci", "lifepo4", "nev"):
            self.assertNotIn(forbidden, joined)


if __name__ == "__main__":
    unittest.main()
