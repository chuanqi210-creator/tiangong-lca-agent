import ast
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "lca_research_kernel/model_gateway.py"


class NetworkEnvironment:
    policy_sha256 = "a" * 64

    def authorize_network(self, host, operation):
        return {
            "host": host,
            "operation": operation,
            "receipt_sha256": "b" * 64,
        }


def request(provider="fake", model="deterministic-test"):
    return {
        "contract": "lca_research_kernel.model_request/v1",
        "request_id": "request-1",
        "provider": provider,
        "model": model,
        "task": "Classify the supplied evidence without changing project truth.",
        "context": {"evidence_ids": ["source-1"], "facts": []},
        "output_schema": {
            "type": "object",
            "required": ["classification"],
        },
    }


class ModelGatewayTests(unittest.TestCase):
    def test_capability_variants_preserve_failover_with_route_specific_schema(self):
        from lca_research_kernel.model_gateway import ModelGateway, ModelGatewayError
        from lca_research_kernel.run_trace import RunTrace

        class Adapter:
            provider_id = "example"

            def __init__(self, *, profile: bool, failure: bool = False):
                self.profile = profile
                self.failure = failure
                self.calls = []

            def capabilities(self):
                return {
                    "provider": self.provider_id,
                    "adapter_id": self.provider_id,
                    "configured_model_id": self.provider_id + "-model",
                    "structured_output": True,
                    "material_answer_v2": self.profile,
                    "remote_execution": False,
                }

            def reason(self, model_request):
                self.calls.append(model_request)
                if self.failure:
                    raise ModelGatewayError(
                        "provider_failure",
                        "synthetic primary failure",
                        retryable=False,
                        next_route="switch-model-provider",
                    )
                return (
                    {"schema_used": model_request["context"]["schema_used"]},
                    {
                        "provider": self.provider_id,
                        "adapter_id": self.provider_id,
                        "provider_response_id": self.provider_id + "-1",
                        "model": self.provider_id + "-model",
                        "usage": {
                            "input_tokens": 1,
                            "output_tokens": 1,
                            "total_tokens": 2,
                        },
                        "tool_requests": [],
                        "duration_ms": 1,
                        "cost": None,
                    },
                )

        primary = Adapter(profile=True, failure=True)
        fallback = Adapter(profile=False)
        primary.provider_id = "primary"
        fallback.provider_id = "fallback"
        policy = {
                "contract": "lca_research_kernel.model_runtime_policy/v1",
                "policy_id": "schema-failover",
                "routes": [
                    {"route_id": "primary", "adapter_id": "primary", "priority": 1},
                    {"route_id": "fallback", "adapter_id": "fallback", "priority": 2},
                ],
                "retry": {
                    "max_attempts_per_route": 1,
                    "base_delay_ms": 0,
                    "retryable_codes": [],
                },
                "circuit_breaker": {"failure_threshold": 3},
            }

        def gateway():
            return ModelGateway(
                {"primary": primary, "fallback": fallback},
                policy=policy,
            )

        common = {
            "contract": "lca_research_kernel.model_request/v1",
            "request_id": "material-answer-1",
            "task": "Return the requested synthetic schema.",
            "output_schema": {"type": "object"},
            "tools": [],
        }
        variants = [
                {
                    **common,
                    "context": {"schema_used": "v2"},
                    "required_capabilities": [
                        "structured_output",
                        "material_answer_v2",
                    ],
                },
                {
                    **common,
                    "context": {"schema_used": "v1"},
                    "required_capabilities": ["structured_output"],
                },
            ]

        with tempfile.TemporaryDirectory() as temporary:
            trace = RunTrace(
                Path(temporary) / "trace.jsonl",
                run_id="schema-failover-replay",
            )
            candidate = gateway().reason_with_capability_variants(
                variants,
                trace=trace,
            )
            replayed = gateway().reason_with_capability_variants(
                variants,
                trace=trace,
            )

        self.assertEqual(candidate["adapter_id"], "fallback")
        self.assertEqual(candidate["output"], {"schema_used": "v1"})
        self.assertEqual(replayed, candidate)
        self.assertEqual(len(primary.calls), 1)
        self.assertEqual(len(fallback.calls), 1)
        self.assertEqual(primary.calls[0]["context"]["schema_used"], "v2")
        self.assertEqual(fallback.calls[0]["context"]["schema_used"], "v1")

    def test_candidate_and_trace_share_one_logical_request_identity(self):
        from lca_research_kernel.model_gateway import (
            DeterministicFakeAdapter,
            ModelGateway,
        )
        from lca_research_kernel.run_trace import RunTrace

        model_request = {
            **request(),
            "runtime_timeout_seconds": 7.5,
            "adaptive_answer_plan": {
                "plan_sha256": "d" * 64,
                "project_state_sha256": "e" * 64,
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            trace = RunTrace(
                Path(temporary) / "trace.jsonl",
                run_id="logical-request-identity",
            )
            candidate = ModelGateway(
                {
                    "fake": DeterministicFakeAdapter(
                        {"classification": "candidate"}
                    )
                },
                default_provider="fake",
            ).reason(model_request, trace=trace)
            events = trace.events()

        requested = next(
            event
            for event in events
            if event["event_type"] == "research.run.model.requested"
        )
        completed = next(
            event
            for event in events
            if event["event_type"] == "research.run.model.completed"
        )
        self.assertEqual(
            candidate["request_sha256"],
            requested["payload"]["request_sha256"],
        )
        self.assertEqual(
            completed["payload"]["request_sha256"],
            candidate["request_sha256"],
        )
        self.assertEqual(
            requested["payload"]["adaptive_plan_sha256"], "d" * 64
        )
        self.assertEqual(
            requested["payload"]["adaptive_project_state_sha256"], "e" * 64
        )

    def test_capacity_qualification_uses_one_case_neutral_candidate_call(self):
        from lca_research_kernel.model_gateway import ModelGateway

        class CapacityAdapter:
            provider_id = "example"

            def __init__(self):
                self.calls = []

            def capabilities(self):
                return {
                    "provider": "example",
                    "adapter_id": "example-production",
                    "configured_model_id": "example-model",
                    "structured_output": True,
                    "generic_source_candidate_v1": True,
                    "sdk_version": "1.2.3",
                    "remote_execution": False,
                }

            def reason(self, model_request):
                self.calls.append(model_request)
                return (
                    {
                        "status": "candidate_only",
                        "source_requests": [],
                        "limitations": [],
                    },
                    {
                        "provider": "example",
                        "adapter_id": "example-production",
                        "provider_response_id": "capacity-1",
                        "model": "example-model",
                        "usage": {
                            "input_tokens": 1,
                            "output_tokens": 1,
                            "total_tokens": 2,
                        },
                        "tool_requests": [],
                        "duration_ms": 1,
                        "cost": None,
                        "rate_limit_observation": {
                            "x-ratelimit-limit-requests": "50",
                            "x-ratelimit-remaining-requests": "9",
                        },
                    },
                )

        adapter = CapacityAdapter()
        gateway = ModelGateway(
            {"example-production": adapter},
            policy={
                "contract": "lca_research_kernel.model_runtime_policy/v1",
                "policy_id": "capacity-test",
                "routes": [
                    {
                        "route_id": "example-production",
                        "adapter_id": "example-production",
                        "priority": 1,
                    }
                ],
                "retry": {
                    "max_attempts_per_route": 2,
                    "base_delay_ms": 1,
                    "retryable_codes": ["rate_limited"],
                },
                "circuit_breaker": {"failure_threshold": 2},
                "source_lock_sha256": "a" * 64,
                "policy_sha256": "b" * 64,
                "provider_catalog_sha256": "c" * 64,
                "execution_mode": "production",
            },
            clock=lambda: "2026-07-26T14:50:00Z",
        )
        receipt = gateway.qualify_capacity(
            {
                "contract": "lca_research_kernel.model_request/v1",
                "request_id": "capacity-probe-1",
                "task": (
                    "Validate the generic structured source-candidate schema "
                    "without producing a scientific result."
                ),
                "context": {
                    "synthetic": True,
                    "scientific_result_allowed": False,
                    "external_write_allowed": False,
                },
                "output_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "status",
                        "source_requests",
                        "limitations",
                    ],
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["candidate_only"],
                        },
                        "source_requests": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["purpose", "url", "method"],
                                "properties": {
                                    "purpose": {"type": "string"},
                                    "url": {"type": "string"},
                                    "method": {
                                        "type": "string",
                                        "enum": ["GET", "HEAD"],
                                    },
                                },
                            },
                        },
                        "limitations": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
                "required_capabilities": [
                    "structured_output",
                    "generic_source_candidate_v1",
                ],
                "tools": [],
                "runtime_purpose": "provider_capacity_qualification",
            },
            requested_task_count=3,
            estimated_calls_per_task=4,
            trace=None,
        )
        self.assertEqual(receipt["status"], "qualified")
        self.assertEqual(receipt["qualified_task_count"], 2)
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(adapter.calls[0]["tools"], [])
        self.assertFalse(
            adapter.calls[0]["context"]["scientific_result_allowed"]
        )

    def test_capacity_qualification_rejects_case_content_and_skips_ineligible_route(self):
        from lca_research_kernel.model_gateway import (
            ModelGateway,
            ModelGatewayError,
        )

        class IneligibleAdapter:
            provider_id = "example"

            def __init__(self):
                self.calls = []

            def capabilities(self):
                return {
                    "provider": "example",
                    "sdk_version": "1.2.3",
                    "structured_output": True,
                    "generic_source_candidate_v1": False,
                    "remote_execution": False,
                }

            def reason(self, model_request):
                self.calls.append(model_request)
                raise AssertionError("ineligible route must not be called")

        adapter = IneligibleAdapter()
        gateway = ModelGateway(
            {"example-production": adapter},
            policy={
                "contract": "lca_research_kernel.model_runtime_policy/v1",
                "policy_id": "capacity-test",
                "routes": [
                    {
                        "route_id": "example-production",
                        "adapter_id": "example-production",
                        "priority": 1,
                    }
                ],
                "retry": {
                    "max_attempts_per_route": 2,
                    "base_delay_ms": 1,
                    "retryable_codes": ["rate_limited"],
                },
                "circuit_breaker": {"failure_threshold": 2},
                "source_lock_sha256": "a" * 64,
                "policy_sha256": "b" * 64,
                "provider_catalog_sha256": "c" * 64,
                "execution_mode": "production",
            },
            clock=lambda: "2026-07-26T14:50:00Z",
        )
        neutral = {
            "contract": "lca_research_kernel.model_request/v1",
            "request_id": "capacity-probe-1",
            "task": (
                "Validate the generic structured source-candidate schema "
                "without producing a scientific result."
            ),
            "context": {
                "synthetic": True,
                "scientific_result_allowed": False,
                "external_write_allowed": False,
            },
            "output_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["status", "source_requests", "limitations"],
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["candidate_only"],
                    },
                    "source_requests": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["purpose", "url", "method"],
                            "properties": {
                                "purpose": {"type": "string"},
                                "url": {"type": "string"},
                                "method": {
                                    "type": "string",
                                    "enum": ["GET", "HEAD"],
                                },
                            },
                        },
                    },
                    "limitations": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
            "required_capabilities": [
                "structured_output",
                "generic_source_candidate_v1",
            ],
            "tools": [],
            "runtime_purpose": "provider_capacity_qualification",
        }
        receipt = gateway.qualify_capacity(
            neutral,
            requested_task_count=3,
            estimated_calls_per_task=4,
            trace=None,
        )
        self.assertEqual(receipt["failure_code"], "schema_profile_unavailable")
        self.assertEqual(adapter.calls, [])

        contaminated = {**neutral, "context": {**neutral["context"], "question_id": "Q2"}}
        with self.assertRaisesRegex(ModelGatewayError, "case-neutral"):
            gateway.qualify_capacity(
                contaminated,
                requested_task_count=3,
                estimated_calls_per_task=4,
                trace=None,
            )

    def test_fake_adapter_is_deterministic_and_candidate_only(self):
        from lca_research_kernel.model_gateway import (
            DeterministicFakeAdapter,
            ModelGateway,
        )

        gateway = ModelGateway(
            {"fake": DeterministicFakeAdapter({"classification": "candidate"})},
            default_provider="fake",
        )
        first = gateway.reason(request())
        second = gateway.reason(request())
        self.assertEqual(first, second)
        self.assertEqual(first["contract"], "lca_research_kernel.model_candidate/v1")
        self.assertEqual(first["status"], "candidate_only")
        self.assertEqual(first["output"], {"classification": "candidate"})
        self.assertEqual(first["provider"], "fake")
        self.assertEqual([event["type"] for event in gateway.stream(request())], ["candidate_final"])

    def test_openai_adapter_normalizes_responses_without_exposing_credentials(self):
        from lca_research_kernel.model_gateway import ModelGateway, OpenAIAdapter

        calls = []

        class Responses:
            def create(self, **payload):
                calls.append(payload)
                return type(
                    "Response",
                    (),
                    {
                        "id": "response-1",
                        "model": "gpt-test",
                        "output_text": json.dumps({"classification": "official"}),
                        "output": [],
                        "usage": None,
                    },
                )()

        gateway = ModelGateway(
            {
                "openai": OpenAIAdapter(
                    adapter_id="openai",
                    model_id="gpt-test",
                    api_key_provider=lambda: "test-secret",
                    client_factory=lambda **kwargs: type(
                        "Client", (), {"responses": Responses()}
                    )(),
                )
            },
            default_provider="openai",
        ).with_execution_environment(NetworkEnvironment())
        candidate = gateway.reason(request("openai", "gpt-test"))
        self.assertEqual(candidate["output"], {"classification": "official"})
        self.assertEqual(candidate["provider_response_id"], "response-1")
        self.assertEqual(calls[0]["model"], "gpt-test")
        self.assertNotIn("test-secret", json.dumps(calls[0]))

    def test_anthropic_adapter_uses_the_same_candidate_contract(self):
        from lca_research_kernel.model_gateway import AnthropicAdapter, ModelGateway

        calls = []

        class Messages:
            def create(self, **payload):
                calls.append(payload)
                return type(
                    "Message",
                    (),
                    {
                        "id": "message-1",
                        "model": "claude-test",
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {"classification": "assumption"}
                                ),
                            }
                        ],
                        "usage": None,
                    },
                )()

        gateway = ModelGateway(
            {
                "anthropic": AnthropicAdapter(
                    adapter_id="anthropic",
                    model_id="claude-test",
                    api_key_provider=lambda: "test-secret",
                    client_factory=lambda **kwargs: type(
                        "Client", (), {"messages": Messages()}
                    )(),
                )
            },
            default_provider="anthropic",
        ).with_execution_environment(NetworkEnvironment())
        candidate = gateway.reason(request("anthropic", "claude-test"))
        self.assertEqual(candidate["status"], "candidate_only")
        self.assertEqual(candidate["output"], {"classification": "assumption"})
        self.assertNotIn("test-secret", json.dumps(calls[0]))

    def test_errors_are_typed_for_retry_switch_or_human_boundary(self):
        from lca_research_kernel.model_gateway import (
            DeterministicFakeAdapter,
            ModelGateway,
            ModelGatewayError,
            OpenAIAdapter,
        )

        missing = ModelGateway(
            {
                "openai": OpenAIAdapter(
                    adapter_id="openai",
                    model_id="gpt-test",
                    api_key_provider=lambda: "",
                )
            },
            default_provider="openai",
        ).with_execution_environment(NetworkEnvironment())
        with self.assertRaises(ModelGatewayError) as captured:
            missing.reason(request("openai", "gpt-test"))
        normalized = missing.normalize_error(captured.exception)
        self.assertEqual(normalized["code"], "credentials_missing")
        self.assertEqual(normalized["next_route"], "prepare-human-credential-handoff")
        self.assertFalse(normalized["retryable"])

        failing = ModelGateway(
            {"fake": DeterministicFakeAdapter(ValueError("fixture failure"))},
            default_provider="fake",
        )
        with self.assertRaises(ModelGatewayError) as captured:
            failing.reason(request())
        normalized = failing.normalize_error(captured.exception)
        self.assertEqual(normalized["code"], "provider_failure")
        self.assertTrue(normalized["retryable"])
        self.assertEqual(normalized["next_route"], "retry-or-switch-model-provider")

        limited = ModelGatewayError(
            "rate_limited",
            "sanitized",
            retryable=True,
            next_route="retry-or-switch-model-provider",
            retry_after_seconds=3.25,
        )
        self.assertEqual(
            failing.normalize_error(limited)["retry_after_seconds"], 3.25
        )

    def test_module_has_no_project_case_or_assurance_write_dependency(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        joined = "\n".join(imports).lower()
        for forbidden in ("project_record", "project", "council", "lifepo4", "nev", "assurance"):
            self.assertNotIn(forbidden, joined)


if __name__ == "__main__":
    unittest.main()
