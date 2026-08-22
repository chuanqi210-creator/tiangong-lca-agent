import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "lca_research_kernel/contracts/model-runtime-policy.json"
SOURCE_LOCK = ROOT / "runtime/model-python/source-lock.json"
PROVIDER_CATALOG = (
    ROOT / "lca_research_kernel/contracts/model-provider-catalog.json"
)


class ModelRuntimeConfigurationTests(unittest.TestCase):
    def test_source_lock_binds_exact_sdk_and_full_hash_locked_environment(self):
        from lca_research_kernel.model_runtime import load_model_runtime_contract

        contract = load_model_runtime_contract(ROOT)
        self.assertEqual(contract["sdk_sources"]["openai"]["version"], "2.47.0")
        self.assertEqual(contract["sdk_sources"]["google-genai"]["version"], "2.13.0")
        self.assertEqual(contract["sdk_sources"]["anthropic"]["version"], "0.118.0")
        self.assertEqual(contract["sdk_sources"]["zai-sdk"]["version"], "0.2.3")
        self.assertEqual(
            contract["sdk_sources"]["zai-sdk"]["wheel_sha256"],
            "f8d6417f8cff58d5b6cba5c27577ee55aa817ae825c6c88fe883f6f6f9265d90",
        )
        self.assertEqual(
            contract["sdk_sources"]["google-genai"]["wheel_sha256"],
            "e1fd408db6864ac12f23eb7634df670934020c918112f4f886e5609d26dd92d3",
        )
        self.assertEqual(contract["python"]["requires"], ">=3.10")
        text = (ROOT / contract["requirements_lock"]["path"]).read_text(
            encoding="utf-8"
        )
        self.assertNotIn("latest", text.lower())
        self.assertIn("--hash=sha256:", text)
        self.assertIn("google-genai==2.13.0", text)
        self.assertIn("\nanthropic==0.118.0", text)
        self.assertIn("\nzai-sdk==0.2.3", text)

    def test_provider_catalog_adds_zhipu_without_retiring_existing_routes(self):
        """Catches supplier replacement or a Zhipu route bypassing admission."""

        from lca_research_kernel.model_runtime import load_model_runtime_contract

        contract = load_model_runtime_contract(ROOT)
        catalog = contract["provider_catalog"]
        providers = {
            row["provider_id"]: row for row in catalog["providers"]
        }
        self.assertEqual(
            set(providers), {"openai", "gemini", "anthropic", "zhipu"}
        )
        self.assertEqual(
            catalog["current_validation_gate"], ["openai", "gemini", "zhipu"]
        )
        self.assertEqual(providers["openai"]["admission_state"], "active_verified")
        self.assertEqual(providers["gemini"]["admission_state"], "active_verified")
        self.assertEqual(
            providers["anthropic"]["admission_state"],
            "adapter_ready_not_verified",
        )
        self.assertTrue(providers["anthropic"]["future_activation_slot"])
        self.assertFalse(providers["anthropic"]["current_validation_required"])
        self.assertEqual(
            providers["zhipu"]["admission_state"],
            "active_verified",
        )
        self.assertTrue(providers["zhipu"]["current_validation_required"])
        self.assertFalse(
            providers["zhipu"]["capabilities"]["generic_source_candidate_v1"]
        )
        self.assertEqual(
            providers["zhipu"]["capability_verified_evidence"],
            {},
        )
        self.assertEqual(
            len(providers["zhipu"]["active_verified_evidence"]),
            1,
        )
        self.assertEqual(
            providers["zhipu"]["credential_binding"],
            {
                "kind": "macos_keychain",
                "service": "codex-zhipu-api",
                "account": "current_user",
            },
        )
        self.assertEqual(
            providers["zhipu"]["model_binding"],
            {
                "kind": "catalog_default_with_environment_override",
                "environment": "ZHIPU_MODEL_ID",
                "default": "glm-5.2",
            },
        )
        for provider in providers.values():
            self.assertIn("credential_binding", provider)
            self.assertIn("model_binding", provider)
            self.assertNotIn("credential_environment", provider)
            self.assertNotIn("model_environment", provider)
        self.assertIn(
            "provider_failure",
            contract["policy"]["retry"]["retryable_codes"],
        )
        self.assertIn(
            "invalid_provider_output",
            contract["policy"]["retry"]["retryable_codes"],
        )
        self.assertEqual(
            contract["policy"]["retry"]["same_route_non_retryable_codes"],
            ["provider_precondition_failed"],
        )
        self.assertEqual(
            contract["policy"]["retry"]["rate_limit_default_delay_ms"],
            15_000,
        )
        profile = catalog["schema_profiles"]["generic_source_candidate_v1"]
        self.assertRegex(profile["schema_sha256"], r"^[0-9a-f]{64}$")
        gemini_evidence = providers["gemini"][
            "capability_verified_evidence"
        ]["generic_source_candidate_v1"]
        self.assertEqual(
            gemini_evidence["schema_sha256"],
            profile["schema_sha256"],
        )
        for provider_id in ("openai", "zhipu"):
            self.assertFalse(
                providers[provider_id]["capabilities"][
                    "generic_source_candidate_v1"
                ]
            )
            self.assertEqual(
                providers[provider_id]["capability_verified_evidence"], {}
            )

    def test_generic_source_profile_is_bound_to_schema_probe_and_review(self):
        import hashlib

        from lca_research_kernel.model_provider_catalog import canonical_sha256
        from lca_research_kernel.model_runtime import load_model_runtime_contract
        from lca_research_kernel.research_orchestrator import ResearchOrchestrator

        contract = load_model_runtime_contract(ROOT)
        catalog = contract["provider_catalog"]
        profile = catalog["schema_profiles"]["generic_source_candidate_v1"]
        self.assertEqual(
            canonical_sha256(
                ResearchOrchestrator._generic_source_candidate_schema()
            ),
            profile["schema_sha256"],
        )
        providers = {
            row["provider_id"]: row for row in catalog["providers"]
        }
        for provider_id in ("gemini",):
            evidence = providers[provider_id][
                "capability_verified_evidence"
            ]["generic_source_candidate_v1"]
            for role in ("probe_receipt", "independent_review"):
                pointer = evidence[role]
                path = ROOT / pointer["path"]
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    pointer["sha256"],
                )

    def test_gateway_factory_derives_production_and_current_probe_routes(self):
        from lca_research_kernel.model_runtime import (
            ModelRuntimeConfigurationError,
            build_production_model_gateway,
        )

        base = {
            "OPENAI_API_KEY": "not-read-during-construction",
            "GEMINI_API_KEY": "not-read-during-construction",
        }
        with self.assertRaisesRegex(
            ModelRuntimeConfigurationError, "model ID"
        ):
            build_production_model_gateway(
                ROOT, base, execution_mode="verification_probe"
            )
        configured = {
            **base,
            "OPENAI_MODEL_ID": "configured-openai-model",
            "GEMINI_MODEL_ID": "configured-gemini-model",
        }
        gateway = build_production_model_gateway(
            ROOT, configured, execution_mode="verification_probe"
        )
        self.assertEqual(
            len(gateway.policy["provider_catalog_sha256"]), 64
        )
        capabilities = gateway.capabilities()
        self.assertEqual(
            capabilities["openai-production"]["configured_model_id"],
            "configured-openai-model",
        )
        self.assertEqual(
            capabilities["gemini-production"]["configured_model_id"],
            "configured-gemini-model",
        )
        self.assertFalse(
            capabilities["openai-production"]["generic_source_candidate_v1"]
        )
        self.assertEqual(
            capabilities["openai-production"]["sdk_version"],
            "2.47.0",
        )
        self.assertTrue(
            capabilities["gemini-production"]["generic_source_candidate_v1"]
        )
        self.assertEqual(
            capabilities["zhipu-production"]["configured_model_id"],
            "glm-5.2",
        )
        self.assertEqual(
            [row["adapter_id"] for row in gateway.policy["routes"]],
            [
                "openai-production",
                "gemini-production",
                "zhipu-production",
            ],
        )
        production = build_production_model_gateway(ROOT, configured)
        self.assertEqual(
            [row["route_id"] for row in production.policy["routes"]],
            ["openai-production", "gemini-production", "zhipu-production"],
        )
        self.assertNotIn("anthropic-production", production.capabilities())
        self.assertIn("zhipu-production", production.capabilities())

    def test_capacity_qualification_prioritizes_one_admitted_provider_without_dropping_fallbacks(
        self,
    ):
        from lca_research_kernel.model_runtime import (
            build_production_model_gateway,
        )

        qualification = json.loads(
            (
                ROOT
                / "outputs/real-question-optimization/73101eec6999b27f/"
                "minimum-real-path/gemini-provider-capacity-q5-v2.json"
            ).read_text(encoding="utf-8")
        )

        def credential_provider_factory(binding, environ):
            return lambda: "synthetic-not-read-during-construction"

        gateway = build_production_model_gateway(
            ROOT,
            {
                "OPENAI_MODEL_ID": "configured-openai-model",
                "GEMINI_MODEL_ID": "configured-gemini-model",
            },
            capacity_qualification=qualification,
            credential_provider_factory=credential_provider_factory,
        )

        self.assertEqual(
            [row["route_id"] for row in gateway.policy["routes"]],
            [
                "gemini-production",
                "openai-production",
                "zhipu-production",
            ],
        )
        self.assertEqual(
            gateway.policy["capacity_qualification_sha256"],
            qualification["receipt_sha256"],
        )

    def test_capacity_qualification_drift_fails_closed(self):
        from lca_research_kernel.model_runtime import (
            ModelRuntimeConfigurationError,
            build_production_model_gateway,
        )

        qualification = json.loads(
            (
                ROOT
                / "outputs/real-question-optimization/73101eec6999b27f/"
                "minimum-real-path/zhipu-provider-capacity-qualification.json"
            ).read_text(encoding="utf-8")
        )
        qualification["provider_catalog_sha256"] = "f" * 64
        qualification["receipt_sha256"] = hashlib.sha256(
            json.dumps(
                {
                    key: value
                    for key, value in qualification.items()
                    if key != "receipt_sha256"
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        with self.assertRaisesRegex(
            ModelRuntimeConfigurationError, "catalog"
        ):
            build_production_model_gateway(
                ROOT,
                {
                    "OPENAI_MODEL_ID": "configured-openai-model",
                    "GEMINI_MODEL_ID": "configured-gemini-model",
                },
                capacity_qualification=qualification,
                credential_provider_factory=lambda binding, environ: (
                    lambda: "synthetic-not-read-during-construction"
                ),
            )

    def test_verification_probe_can_build_only_zhipu_without_other_credentials(self):
        """Catches verification paths that force unrelated paid providers."""

        from lca_research_kernel.model_runtime import (
            build_production_model_gateway,
        )

        calls = []

        def credential_provider_factory(binding, environ):
            calls.append((binding, dict(environ)))
            return lambda: "synthetic-not-read-during-construction"

        gateway = build_production_model_gateway(
            ROOT,
            {},
            execution_mode="verification_probe",
            provider_ids={"zhipu"},
            credential_provider_factory=credential_provider_factory,
        )
        self.assertEqual(
            [row["route_id"] for row in gateway.policy["routes"]],
            ["zhipu-production"],
        )
        self.assertEqual(
            gateway.capabilities()["zhipu-production"]["configured_model_id"],
            "glm-5.2",
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0]["kind"], "macos_keychain")

    def test_production_rejects_verification_provider_subset(self):
        """Catches a caller narrowing the production safety catalog."""

        from lca_research_kernel.model_runtime import (
            ModelRuntimeConfigurationError,
            build_production_model_gateway,
        )

        configured = {
            "OPENAI_API_KEY": "synthetic",
            "OPENAI_MODEL_ID": "configured-openai-model",
            "GEMINI_API_KEY": "synthetic",
            "GEMINI_MODEL_ID": "configured-gemini-model",
        }
        with self.assertRaisesRegex(
            ModelRuntimeConfigurationError, "verification"
        ):
            build_production_model_gateway(
                ROOT,
                configured,
                provider_ids={"openai"},
            )

    def test_legacy_route_order_cannot_activate_or_reorder_catalog_slots(self):
        from lca_research_kernel.model_runtime import (
            ModelRuntimeConfigurationError,
            build_production_model_gateway,
        )

        common = {
            "OPENAI_MODEL_ID": "configured-openai-model",
            "GEMINI_MODEL_ID": "configured-gemini-model",
        }
        common.update(
            {
                "OPENAI_API_KEY": "not-read-during-construction",
                "GEMINI_API_KEY": "not-read-during-construction",
            }
        )
        gateway = build_production_model_gateway(
            ROOT,
            {
                **common,
                "D18_MODEL_ROUTE_ORDER": (
                    "anthropic-production,gemini-production,openai-production"
                ),
            },
            execution_mode="verification_probe",
        )
        self.assertEqual(
            [row["route_id"] for row in gateway.policy["routes"]],
            [
                "openai-production",
                "gemini-production",
                "zhipu-production",
            ],
        )

    def test_contract_or_requirements_drift_fails_closed(self):
        from lca_research_kernel.model_runtime import load_model_runtime_contract

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "lca_research_kernel/contracts").mkdir(parents=True)
            (root / "runtime/model-python").mkdir(parents=True)
            (root / "lca_research_kernel/contracts/model-runtime-policy.json").write_bytes(
                POLICY.read_bytes()
            )
            (root / "runtime/model-python/source-lock.json").write_bytes(
                SOURCE_LOCK.read_bytes()
            )
            (
                root
                / "lca_research_kernel/contracts/model-provider-catalog.json"
            ).write_bytes(PROVIDER_CATALOG.read_bytes())
            lock = json.loads(SOURCE_LOCK.read_text(encoding="utf-8"))
            requirements = ROOT / lock["requirements_lock"]["path"]
            destination = root / lock["requirements_lock"]["path"]
            destination.write_bytes(requirements.read_bytes() + b"# drift\n")
            with self.assertRaisesRegex(ValueError, "requirements lock drifted"):
                load_model_runtime_contract(root)


if __name__ == "__main__":
    unittest.main()
