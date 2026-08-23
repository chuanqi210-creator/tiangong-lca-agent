import copy
import hashlib
import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sha256(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def file_sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_tracked(relative_path):
    return (
        subprocess.run(
            [
                "git",
                "-C",
                str(ROOT),
                "ls-files",
                "--error-unmatch",
                relative_path,
            ],
            check=False,
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )


class ModelProviderCatalogTests(unittest.TestCase):
    def setUp(self):
        self.adopted_catalog = json.loads(
            (
                ROOT
                / "lca_research_kernel/contracts/model-provider-catalog.json"
            ).read_text(encoding="utf-8")
        )
        self.catalog = copy.deepcopy(self.adopted_catalog)
        for row in self.catalog["providers"]:
            if row["provider_id"] in self.catalog["current_validation_gate"]:
                row["admission_state"] = "adapter_ready_not_verified"
                row["active_verified_evidence"] = []
        self.catalog_sha = sha256(self.catalog)

    def receipt(self, provider):
        value = {
            "contract": "lca_research_kernel.model_provider_probe_receipt/v1",
            "status": "passed",
            "provider": provider,
            "actual_model": f"{provider}-provider-reported-model",
            "provider_response_id": f"{provider}-response",
            "real_call": True,
            "structured_output_valid": True,
            "external_writes": 0,
            "scientific_result_created": False,
            "provider_catalog_sha256": self.catalog_sha,
        }
        value["receipt_sha256"] = sha256(value)
        return value

    def review(self, provider, receipt):
        value = {
            "contract": "lca_research_kernel.model_provider_admission_review/v1",
            "status": "passed",
            "provider": provider,
            "probe_receipt_sha256": receipt["receipt_sha256"],
            "provider_catalog_sha256": self.catalog_sha,
            "p1": 0,
            "p2": 0,
            "external_effects": 0,
        }
        value["review_sha256"] = sha256(value)
        return value

    def test_independently_accepted_provider_is_promoted_without_changing_other_slots(self):
        from lca_research_kernel.model_provider_catalog import admit_provider

        receipt = self.receipt("gemini")
        promoted = admit_provider(
            self.catalog,
            provider_id="gemini",
            receipt=receipt,
            independent_review=self.review("gemini", receipt),
        )
        states = {
            row["provider_id"]: row["admission_state"]
            for row in promoted["providers"]
        }
        self.assertEqual(states["gemini"], "active_verified")
        self.assertEqual(states["openai"], "adapter_ready_not_verified")
        self.assertEqual(states["anthropic"], "adapter_ready_not_verified")
        anthropic_before = next(
            row for row in self.catalog["providers"] if row["provider_id"] == "anthropic"
        )
        anthropic_after = next(
            row for row in promoted["providers"] if row["provider_id"] == "anthropic"
        )
        self.assertEqual(anthropic_after, anthropic_before)

    def test_missing_or_mismatched_probe_and_review_fail_closed(self):
        from lca_research_kernel.model_provider_catalog import admit_provider

        receipt = self.receipt("openai")
        for bad_receipt, bad_review in (
            (None, self.review("openai", receipt)),
            (receipt, None),
            (
                {**receipt, "provider_catalog_sha256": "0" * 64},
                self.review("openai", receipt),
            ),
            (
                receipt,
                {**self.review("openai", receipt), "p1": 1},
            ),
        ):
            with self.subTest(
                receipt=bad_receipt is not None,
                review=bad_review is not None,
            ), self.assertRaises(ValueError):
                admit_provider(
                    copy.deepcopy(self.catalog),
                    provider_id="openai",
                    receipt=bad_receipt,
                    independent_review=bad_review,
                )

    def test_batch_admission_uses_one_frozen_catalog_and_leaves_future_slot_pending(self):
        from lca_research_kernel.model_provider_catalog import admit_providers

        admissions = {}
        for provider in ("openai", "gemini"):
            receipt = self.receipt(provider)
            admissions[provider] = {
                "receipt": receipt,
                "independent_review": self.review(provider, receipt),
            }
        promoted = admit_providers(self.catalog, admissions)
        states = {
            row["provider_id"]: row["admission_state"]
            for row in promoted["providers"]
        }
        self.assertEqual(
            states,
            {
                "openai": "active_verified",
                "gemini": "active_verified",
                "anthropic": "adapter_ready_not_verified",
                "zhipu": "adapter_ready_not_verified",
            },
        )
        replayed = admit_providers(promoted, admissions)
        self.assertEqual(replayed, promoted)

    def test_formal_rebaseline_binds_real_receipts_reviews_and_future_slot(self):
        receipt = json.loads(
            (
                ROOT
                / "outputs/d18-production-runtime-core/final/provider-rebaseline-v1.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            receipt["contract"],
            "lca_research_kernel.model_provider_rebaseline_receipt/v1",
        )
        self.assertEqual(receipt["status"], "accepted_pending_final_verification")
        for provider_id in ("openai", "gemini"):
            provider = receipt["providers"][provider_id]
            probe_ref = provider["receipt"]
            review_ref = provider["review"]
            probe_path = ROOT / probe_ref["path"]
            review_path = ROOT / review_ref["path"]
            probe = json.loads(probe_path.read_text(encoding="utf-8"))
            review = json.loads(review_path.read_text(encoding="utf-8"))
            self.assertEqual(file_sha256(probe_path), probe_ref["file_sha256"])
            self.assertEqual(file_sha256(review_path), review_ref["file_sha256"])
            self.assertEqual(probe["receipt_sha256"], probe_ref["receipt_sha256"])
            self.assertEqual(review["review_sha256"], review_ref["review_sha256"])
            self.assertEqual(probe["provider"], provider_id)
            self.assertEqual(probe["actual_model"], provider["actual_model"])
            self.assertEqual(probe["usage"]["total_tokens"], provider["total_tokens"])
            self.assertEqual(probe["tool_requests"], 0)
            self.assertEqual(probe["external_writes"], 0)
            self.assertFalse(probe["scientific_result_created"])
            catalog_row = next(
                row
                for row in self.adopted_catalog["providers"]
                if row["provider_id"] == provider_id
            )
            self.assertEqual(catalog_row["admission_state"], "active_verified")
            self.assertEqual(
                catalog_row["active_verified_evidence"],
                [
                    {
                        "admitted_from_catalog_sha256": receipt[
                            "provider_catalog"
                        ]["pre_admission_semantic_sha256"],
                        "independent_review_sha256": review["review_sha256"],
                        "probe_receipt_sha256": probe["receipt_sha256"],
                    }
                ],
            )
        anthropic = receipt["providers"]["anthropic"]
        self.assertEqual(
            anthropic["admission_state"], "adapter_ready_not_verified"
        )
        self.assertFalse(anthropic["current_validation_required"])
        self.assertTrue(anthropic["future_activation_slot"])
        self.assertIsNone(anthropic["real_probe_receipt"])

    def test_research_intelligence_profiles_are_admitted_only_with_exact_evidence(self):
        from lca_research_kernel.model_schema_profiles import profile_probe_spec

        profiles = self.adopted_catalog["schema_profiles"]
        self.assertEqual(
            set(profiles),
            {
                "generic_source_candidate_v1",
                "research_turn_intent_v2",
                "research_turn_intent_v3",
                "research_turn_semantics_v1",
                "material_answer_v2",
            },
        )
        for profile_id in (
            "research_turn_semantics_v1",
            "research_turn_intent_v2",
            "research_turn_intent_v3",
            "material_answer_v2",
        ):
            spec = profile_probe_spec(profile_id)
            self.assertEqual(
                profiles[profile_id]["schema_sha256"],
                sha256(spec["output_schema"]),
            )
            for provider in self.adopted_catalog["providers"]:
                if provider["provider_id"] == "gemini":
                    self.assertIs(provider["capabilities"][profile_id], True)
                    evidence = provider["capability_verified_evidence"][
                        profile_id
                    ]
                    self.assertEqual(
                        evidence["schema_sha256"],
                        profiles[profile_id]["schema_sha256"],
                    )
                    for label in ("probe_receipt", "independent_review"):
                        pointer = evidence[label]
                        path = ROOT / pointer["path"]
                        self.assertTrue(path.is_file())
                        self.assertEqual(file_sha256(path), pointer["sha256"])
                        self.assertTrue(
                            git_tracked(pointer["path"]),
                            f"runtime evidence is not Git-tracked: {pointer['path']}",
                        )
                    receipt = json.loads(
                        (
                            ROOT
                            / evidence["probe_receipt"]["path"]
                        ).read_text(encoding="utf-8")
                    )
                    transitive_paths = [
                        receipt["raw_baseline_acceptance"]["path"],
                        *[
                            ref["path"]
                            for ref in receipt["validated_event_refs"]
                        ],
                    ]
                    for relative_path in transitive_paths:
                        self.assertTrue(
                            git_tracked(relative_path),
                            f"transitive runtime evidence is not Git-tracked: {relative_path}",
                        )
                else:
                    self.assertIs(provider["capabilities"][profile_id], False)
                    self.assertNotIn(
                        profile_id,
                        provider["capability_verified_evidence"],
                    )


if __name__ == "__main__":
    unittest.main()
