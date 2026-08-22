import unittest
from copy import deepcopy

from tests.test_product_material_acceptance import material_bundle, model_answer


class MaterialCitationAssuranceTests(unittest.TestCase):
    def setUp(self):
        from lca_research_kernel.assurance import AssuranceBoundary

        self.assurance = AssuranceBoundary()
        self.bundle = material_bundle()
        self.locator = self.bundle["locators"][0]
        self.use_decision = {
            "contract": "lca_research_kernel.material_use_decision/v1",
            "task_id": "answer-material-question",
            "allowed_revision_ids": ["revision-1"],
            "selected_evidence_locator_ids": ["locator-page-1"],
            "selection_reasons": {
                "locator-page-1": "It directly answers the question."
            },
            "exclusions": [],
            "context_mode": "focused_retrieval",
            "expected_state_change": {"desired_artifact": "answer"},
            "claim_ceiling": "source_bounded_interpretation",
        }

    def evaluate(self, *, candidate=None, bundle=None, decision=None):
        return self.assurance.evaluate_material_answer(
            candidate or model_answer(),
            material_bundles=[bundle or self.bundle],
            use_decision=decision or self.use_decision,
        )

    def test_exact_revision_locator_and_hash_are_accepted_without_official_promotion(self):
        acceptance = self.evaluate()
        self.assertEqual(acceptance["decision"], "accepted")
        self.assertEqual(
            acceptance["scientific_status"], "accepted_project_input"
        )
        self.assertFalse(acceptance["may_satisfy_tiangong_official_requirement"])
        accepted_locator = acceptance["accepted_locators"][0]
        self.assertEqual(accepted_locator["scientific_status"], "accepted_evidence")
        self.assertIsNotNone(accepted_locator["assurance_acceptance_ref"])

    def test_wrong_locator_page_revision_content_or_scope_is_rejected(self):
        cases = {}
        wrong_page = deepcopy(self.bundle)
        wrong_page["locators"][0]["locator"]["page"] = 2
        cases["wrong page"] = (wrong_page, self.use_decision, "locator_content_drifted")

        wrong_revision = deepcopy(self.bundle)
        wrong_revision["locators"][0]["revision_id"] = "revision-other"
        cases["wrong revision"] = (
            wrong_revision,
            self.use_decision,
            "locator_revision_drifted",
        )

        wrong_hash = deepcopy(self.bundle)
        wrong_hash["material_revision"]["artifact_ref"] = {
            "artifact_id": "0" * 64,
            "sha256": "0" * 64,
        }
        cases["wrong input SHA"] = (
            wrong_hash,
            self.use_decision,
            "processing_input_sha256_drifted",
        )

        wrong_scope = deepcopy(self.use_decision)
        wrong_scope["allowed_revision_ids"] = ["revision-other"]
        cases["wrong scope"] = (
            self.bundle,
            wrong_scope,
            "selected_material_not_allowed",
        )

        for name, (bundle, decision, code) in cases.items():
            with self.subTest(name=name):
                rejected = self.evaluate(bundle=bundle, decision=decision)
                self.assertEqual(rejected["decision"], "rejected")
                self.assertIn(code, rejected["errors"])

    def test_answer_clause_must_name_selected_citation(self):
        candidate = model_answer()
        candidate["answer"] = "The material identifies the source."
        candidate["bounded_conclusion"] = candidate["answer"]
        rejected = self.evaluate(candidate=candidate)
        self.assertEqual(rejected["decision"], "rejected")
        self.assertIn("answer_citation_id_absent", rejected["errors"])

    def test_claim_text_cannot_hide_an_extra_locator_occurrence(self):
        candidate = model_answer()
        claim_text = "The material identifies [locator-page-1] the source"
        candidate["claims"][0]["text"] = claim_text
        candidate["claim_citation_bindings"][0]["claim_text"] = claim_text
        candidate["answer"] = f"{claim_text} [locator-page-1]."
        candidate["bounded_conclusion"] = candidate["answer"]

        rejected = self.evaluate(candidate=candidate)

        self.assertEqual(rejected["decision"], "rejected")
        self.assertIn("candidate_citation_set_drifted", rejected["errors"])

    def test_duplicate_tokens_in_one_binding_cannot_pass_citation_metric(self):
        candidate = model_answer()
        duplicate_tokens = ["[locator-page-1]", "[locator-page-1]"]
        candidate["claims"][0]["citation_tokens"] = duplicate_tokens
        candidate["claim_citation_bindings"][0][
            "citation_tokens"
        ] = duplicate_tokens
        candidate["answer"] = (
            "The material identifies the source "
            "[locator-page-1] [locator-page-1]."
        )
        candidate["bounded_conclusion"] = candidate["answer"]

        rejected = self.evaluate(candidate=candidate)

        self.assertEqual(rejected["decision"], "rejected")
        self.assertEqual(
            rejected["quality_metrics"]["citation_correctness"], 0.0
        )

    def test_every_locator_requires_one_exact_evidence_unit_binding(self):
        cases = {}
        absent = deepcopy(self.bundle)
        absent.pop("evidence_unit_bindings")
        cases["absent"] = absent

        incomplete = deepcopy(self.bundle)
        incomplete["evidence_unit_bindings"] = []
        cases["incomplete"] = incomplete

        extra = deepcopy(self.bundle)
        extra["evidence_unit_bindings"].append(
            {
                "locator_id": "unknown-locator",
                "content_hash": "1" * 64,
                "source_span": {
                    "kind": "pdf_region",
                    "locator": {
                        "page": 1,
                        "normalized_rect": [0.0, 0.0, 1.0, 1.0],
                    },
                },
            }
        )
        cases["extra"] = extra

        mismatched = deepcopy(self.bundle)
        mismatched["evidence_unit_bindings"][0]["evidence_unit"][
            "content"
        ] = "Tampered content."
        mismatched["locators"][0]["content_hash"] = "0" * 64
        mismatched["evidence_unit_bindings"][0]["content_hash"] = "0" * 64
        cases["mismatched"] = mismatched

        for name, bundle in cases.items():
            with self.subTest(name=name):
                rejected = self.evaluate(bundle=bundle)
                self.assertEqual(rejected["decision"], "rejected")
                self.assertTrue(
                    {"material_bundle_invalid", "locator_content_drifted"}
                    & set(rejected["errors"])
                )

    def test_unrelated_locator_mention_does_not_cite_the_supported_claim(self):
        candidate = model_answer()
        candidate["answer"] = (
            "The material identifies the source. "
            "The unrelated locator is not [locator-page-1]."
        )
        candidate["bounded_conclusion"] = candidate["answer"]
        candidate["claim_citation_bindings"] = [
            {
                "claim_text": "The material identifies the source",
                "citation_tokens": ["[locator-page-1]"],
            }
        ]
        rejected = self.evaluate(candidate=candidate)
        self.assertEqual(rejected["decision"], "rejected")
        self.assertIn("claim_citation_binding_absent", rejected["errors"])

    def test_allowed_but_unselected_locator_cannot_be_laundered_through_prose(self):
        bundle = deepcopy(self.bundle)
        second_locator = deepcopy(bundle["locators"][0])
        second_locator["locator_id"] = "locator-page-2"
        second_locator["locator"]["page"] = 2
        second_unit = deepcopy(bundle["evidence_unit_bindings"][0])
        second_unit["locator_id"] = "locator-page-2"
        second_unit["source_span"]["locator"]["page"] = 2
        second_unit["evidence_unit"]["source_span"]["locator"]["page"] = 2
        second_unit["evidence_unit"]["content"] = "Unselected material content."
        from tests.test_product_material_acceptance import _canonical_evidence_hash

        second_unit["content_hash"] = _canonical_evidence_hash(
            second_unit["evidence_unit"]
        )
        second_locator["content_hash"] = second_unit["content_hash"]
        bundle["locators"].append(second_locator)
        bundle["evidence_unit_bindings"].append(second_unit)
        candidate = model_answer()
        candidate["answer"] = (
            "The material identifies the source [locator-page-1]. "
            "See also [locator-page-2]."
        )
        candidate["bounded_conclusion"] = candidate["answer"]

        rejected = self.evaluate(candidate=candidate, bundle=bundle)

        self.assertEqual(rejected["decision"], "rejected")
        self.assertIn(
            "unselected_allowed_citation_laundering",
            rejected["errors"],
        )

    def test_answer_may_cite_the_minimal_supported_subset_of_selected_context(self):
        bundle = deepcopy(self.bundle)
        second_locator = deepcopy(bundle["locators"][0])
        second_locator["locator_id"] = "locator-page-2"
        second_locator["locator"]["page"] = 2
        second_unit = deepcopy(bundle["evidence_unit_bindings"][0])
        second_unit["locator_id"] = "locator-page-2"
        second_unit["source_span"]["locator"]["page"] = 2
        second_unit["evidence_unit"]["source_span"]["locator"]["page"] = 2
        second_unit["evidence_unit"]["content"] = "Additional selected context."
        from tests.test_product_material_acceptance import _canonical_evidence_hash

        second_unit["content_hash"] = _canonical_evidence_hash(
            second_unit["evidence_unit"]
        )
        second_locator["content_hash"] = second_unit["content_hash"]
        bundle["locators"].append(second_locator)
        bundle["evidence_unit_bindings"].append(second_unit)
        decision = deepcopy(self.use_decision)
        decision["selected_evidence_locator_ids"] = [
            "locator-page-1",
            "locator-page-2",
        ]
        decision["selection_reasons"]["locator-page-2"] = (
            "Selected as bounded context but not required by the final claim."
        )

        accepted = self.evaluate(
            candidate=model_answer(), bundle=bundle, decision=decision
        )

        self.assertEqual(accepted["decision"], "accepted")
        self.assertEqual(
            accepted["domain_assessment"]["admissible_candidate"]["citation_ids"],
            ["locator-page-1"],
        )

    def test_candidate_cannot_choose_an_unrelated_clause_as_the_only_cited_claim(self):
        candidate = model_answer()
        candidate["answer"] = (
            "The material identifies the source. "
            "The unrelated locator is not [locator-page-1]."
        )
        candidate["bounded_conclusion"] = candidate["answer"]
        candidate["claims"] = [
            {
                "claim_id": "claim-unrelated-negation",
                "text": "The unrelated locator is not",
                "requires_evidence": True,
                "citation_tokens": ["[locator-page-1]"],
            }
        ]
        candidate["claim_citation_bindings"] = [
            {
                "claim_text": "The unrelated locator is not",
                "citation_tokens": ["[locator-page-1]"],
            }
        ]
        rejected = self.evaluate(candidate=candidate)
        self.assertEqual(rejected["decision"], "rejected")
        self.assertIn("answer_claim_projection_drifted", rejected["errors"])

    def test_partial_processing_lowers_claim_ceiling_and_requires_limitation(self):
        partial = material_bundle(status="partial")
        without_limitation = model_answer(status="ready")
        rejected = self.evaluate(candidate=without_limitation, bundle=partial)
        self.assertEqual(rejected["decision"], "rejected")
        self.assertIn("processing_limitation_absent", rejected["errors"])

        accepted = self.evaluate(
            candidate=model_answer(status="partial"), bundle=partial
        )
        self.assertEqual(accepted["decision"], "accepted")
        self.assertEqual(accepted["claim_ceiling"], "calculation_structure_only")


if __name__ == "__main__":
    unittest.main()
