import tempfile
import unittest
from pathlib import Path


def request():
    return {
        "contract": "lca_research_kernel.model_request/v1",
        "request_id": "policy-request-1",
        "task": "Return one bounded structured candidate.",
        "context": {"source_ref": "source-1"},
        "output_schema": {
            "type": "object",
            "required": ["status"],
            "properties": {"status": {"type": "string"}},
            "additionalProperties": False,
        },
        "tools": [],
        "required_capabilities": ["structured_output", "streaming"],
    }


def policy():
    return {
        "contract": "lca_research_kernel.model_runtime_policy/v1",
        "policy_id": "policy-1",
        "routes": [
            {"route_id": "quality-primary", "adapter_id": "z-adapter", "priority": 10},
            {"route_id": "quality-fallback", "adapter_id": "a-adapter", "priority": 20},
        ],
        "retry": {
            "max_attempts_per_route": 2,
            "base_delay_ms": 0,
            "retryable_codes": [
                "rate_limited",
                "provider_server_error",
                "provider_timeout",
                "provider_transport_error",
            ],
        },
        "circuit_breaker": {"failure_threshold": 1},
    }


class ScriptedAdapter:
    provider_id = "provider-neutral-test"

    def __init__(self, adapter_id, outcomes, *, capabilities=None):
        self.adapter_id = adapter_id
        self.outcomes = list(outcomes)
        self.calls = 0
        self._capabilities = capabilities or {
            "structured_output": True,
            "streaming": True,
            "tool_requests": True,
            "remote_execution": False,
        }

    def reason(self, model_request):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome, {
            "provider_response_id": f"response-{self.calls}",
            "provider": self.provider_id,
            "model": "reported-model-id",
            "usage": {
                "input_tokens": 3,
                "output_tokens": 2,
                "total_tokens": 5,
            },
            "tool_requests": [],
        }

    def capabilities(self):
        return {
            "adapter_id": self.adapter_id,
            "provider": self.provider_id,
            **self._capabilities,
        }


class StreamingAdapter(ScriptedAdapter):
    def stream(self, model_request):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        yield {
            "type": "model_stream_started",
            "adapter_id": self.adapter_id,
            "provider": self.provider_id,
            "provider_response_id": f"stream-{self.calls}",
        }
        yield {"type": "model_output_delta", "text": '{"status":"candidate"}'}
        yield {
            "type": "model_stream_completed",
            "output": outcome,
            "metadata": {
                "provider_response_id": f"stream-{self.calls}",
                "provider": self.provider_id,
                "model": "reported-model-id",
                "usage": {
                    "input_tokens": 2,
                    "output_tokens": 2,
                    "total_tokens": 4,
                },
                "tool_requests": [],
                "duration_ms": 1,
                "cost": None,
            },
        }


class ModelRuntimePolicyTests(unittest.TestCase):
    def setUp(self):
        from lca_research_kernel.run_trace import RunTrace

        self.temporary = tempfile.TemporaryDirectory()
        self.trace = RunTrace(
            Path(self.temporary.name) / "run-trace.jsonl", run_id="policy-run"
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_selection_uses_capabilities_and_policy_priority_not_adapter_name(self):
        from lca_research_kernel.model_gateway import ModelGateway

        primary = ScriptedAdapter("z-adapter", [{"status": "candidate"}])
        fallback = ScriptedAdapter("a-adapter", [{"status": "fallback"}])
        gateway = ModelGateway(
            {"z-adapter": primary, "a-adapter": fallback},
            policy=policy(),
            clock=lambda: "2026-07-23T16:10:00Z",
        )
        candidate = gateway.reason(request(), trace=self.trace)
        self.assertEqual(candidate["output"], {"status": "candidate"})
        self.assertEqual(candidate["adapter_id"], "z-adapter")
        self.assertEqual(primary.calls, 1)
        self.assertEqual(fallback.calls, 0)

    def test_schema_profile_capability_excludes_an_unverified_fallback(self):
        from lca_research_kernel.model_gateway import ModelGateway

        primary = ScriptedAdapter(
            "z-adapter",
            [{"status": "must-not-run"}],
            capabilities={
                "structured_output": True,
                "streaming": True,
                "tool_requests": True,
                "remote_execution": False,
                "generic_source_candidate_v1": False,
            },
        )
        fallback = ScriptedAdapter(
            "a-adapter",
            [{"status": "profile-verified"}],
            capabilities={
                "structured_output": True,
                "streaming": True,
                "tool_requests": True,
                "remote_execution": False,
                "generic_source_candidate_v1": True,
            },
        )
        profiled = request()
        profiled["required_capabilities"].append(
            "generic_source_candidate_v1"
        )
        gateway = ModelGateway(
            {"z-adapter": primary, "a-adapter": fallback},
            policy=policy(),
        )

        candidate = gateway.reason(profiled, trace=self.trace)

        self.assertEqual(candidate["output"], {"status": "profile-verified"})
        self.assertEqual(primary.calls, 0)
        self.assertEqual(fallback.calls, 1)

    def test_unclassified_provider_exception_retries_when_policy_explicitly_allows_it(self):
        from lca_research_kernel.model_gateway import ModelGateway

        adapter = ScriptedAdapter(
            "z-adapter",
            [RuntimeError("transient provider failure"), {"status": "candidate"}],
        )
        configured = policy()
        configured["routes"] = [configured["routes"][0]]
        configured["retry"]["retryable_codes"].append("provider_failure")
        gateway = ModelGateway(
            {"z-adapter": adapter},
            policy=configured,
            clock=lambda: "2026-07-24T12:00:00Z",
        )

        candidate = gateway.reason(request(), trace=self.trace)

        self.assertEqual(candidate["output"], {"status": "candidate"})
        self.assertEqual(adapter.calls, 2)
        failed = [
            event
            for event in self.trace.events()
            if event["event_type"] == "research.run.model.failed"
        ]
        self.assertEqual(failed[0]["payload"]["failure_code"], "provider_failure")

    def test_invalid_provider_output_retries_once_without_relaxing_local_validation(self):
        from lca_research_kernel.model_gateway import ModelGateway, ModelGatewayError

        adapter = ScriptedAdapter(
            "z-adapter",
            [
                ModelGatewayError(
                    "invalid_provider_output",
                    "provider JSON violated the exact local candidate contract",
                    retryable=True,
                    next_route="retry-or-switch-model-provider",
                ),
                {"status": "candidate"},
            ],
        )
        configured = policy()
        configured["routes"] = [configured["routes"][0]]
        configured["retry"]["retryable_codes"].append(
            "invalid_provider_output"
        )
        gateway = ModelGateway(
            {"z-adapter": adapter},
            policy=configured,
            clock=lambda: "2026-08-17T12:00:00Z",
            sleep=lambda _: None,
        )

        candidate = gateway.reason(request(), trace=self.trace)

        self.assertEqual(candidate["output"], {"status": "candidate"})
        self.assertEqual(adapter.calls, 2)
        failed = [
            event
            for event in self.trace.events()
            if event["event_type"] == "research.run.model.failed"
        ]
        self.assertEqual(len(failed), 1)
        self.assertEqual(
            failed[0]["payload"]["failure_code"], "invalid_provider_output"
        )
        self.assertEqual(
            failed[0]["payload"]["retry_decision"], "retry_same_route"
        )

    def test_candidate_and_run_trace_bind_provider_catalog_and_runtime_policy_identity(self):
        from lca_research_kernel.model_gateway import ModelGateway

        configured = {
            **policy(),
            "source_lock_sha256": "a" * 64,
            "policy_sha256": "b" * 64,
            "provider_catalog_sha256": "c" * 64,
            "execution_mode": "verification_probe",
        }
        gateway = ModelGateway(
            {
                "z-adapter": ScriptedAdapter(
                    "z-adapter", [{"status": "candidate"}]
                )
            },
            policy={
                **configured,
                "routes": [configured["routes"][0]],
            },
            clock=lambda: "2026-07-24T12:00:00Z",
        )
        candidate = gateway.reason(request(), trace=self.trace)
        self.assertEqual(
            candidate["runtime_identity"]["provider_catalog_sha256"], "c" * 64
        )
        completed = [
            event
            for event in self.trace.events()
            if event["event_type"] == "research.run.model.completed"
        ][0]
        self.assertEqual(
            completed["payload"]["candidate"]["runtime_identity"],
            candidate["runtime_identity"],
        )

    def test_retryable_failure_is_bounded_then_fails_over_and_opens_circuit(self):
        from lca_research_kernel.model_gateway import ModelGateway, ModelGatewayError

        primary = ScriptedAdapter(
            "z-adapter",
            [
                ModelGatewayError(
                    "rate_limited",
                    "provider rejected the request",
                    retryable=True,
                    next_route="retry-or-switch-model-provider",
                ),
                ModelGatewayError(
                    "provider_server_error",
                    "provider failed",
                    retryable=True,
                    next_route="retry-or-switch-model-provider",
                ),
            ],
        )
        fallback = ScriptedAdapter("a-adapter", [{"status": "fallback"}])
        sleeps = []
        gateway = ModelGateway(
            {"z-adapter": primary, "a-adapter": fallback},
            policy=policy(),
            clock=lambda: "2026-07-23T16:10:00Z",
            sleep=lambda seconds: sleeps.append(seconds),
        )
        candidate = gateway.reason(request(), trace=self.trace)
        self.assertEqual(candidate["output"], {"status": "fallback"})
        self.assertEqual(primary.calls, 2)
        self.assertEqual(fallback.calls, 1)
        self.assertEqual(sleeps, [0.0])
        event_types = [event["event_type"] for event in self.trace.events()]
        self.assertIn("research.run.model.retry_scheduled", event_types)
        self.assertIn("research.run.model.circuit_opened", event_types)

    def test_every_retry_and_failover_runs_the_same_pre_attempt_guard_at_adapter_edge(self):
        from lca_research_kernel.model_gateway import ModelGateway, ModelGatewayError

        class GuardedAdapter(ScriptedAdapter):
            def reason(self, model_request):
                self.assert_guarded(model_request)
                return super().reason(model_request)

        guarded_attempts = []

        def pre_attempt(model_request, attempt):
            guarded_attempts.append(
                (
                    attempt["route_id"],
                    attempt["attempt"],
                    model_request["request_id"],
                )
            )

        primary = GuardedAdapter(
            "z-adapter",
            [
                ModelGatewayError(
                    "rate_limited",
                    "provider rejected the request",
                    retryable=True,
                    next_route="retry-or-switch-model-provider",
                ),
                ModelGatewayError(
                    "provider_server_error",
                    "provider failed",
                    retryable=True,
                    next_route="retry-or-switch-model-provider",
                ),
            ],
        )
        fallback = GuardedAdapter("a-adapter", [{"status": "fallback"}])
        primary.assert_guarded = lambda model_request: self.assertEqual(
            len(guarded_attempts), primary.calls + 1
        )
        fallback.assert_guarded = lambda model_request: self.assertEqual(
            guarded_attempts[-1][:2], ("quality-fallback", 1)
        )
        gateway = ModelGateway(
            {"z-adapter": primary, "a-adapter": fallback},
            policy=policy(),
            clock=lambda: "2026-07-23T16:10:00Z",
            sleep=lambda _: None,
        )

        candidate = gateway.reason(
            request(), trace=self.trace, pre_attempt=pre_attempt
        )

        self.assertEqual(candidate["output"], {"status": "fallback"})
        self.assertEqual(
            guarded_attempts,
            [
                ("quality-primary", 1, "policy-request-1"),
                ("quality-primary", 2, "policy-request-1"),
                ("quality-fallback", 1, "policy-request-1"),
            ],
        )

    def test_rate_limit_without_provider_hint_uses_bounded_policy_delay(self):
        from lca_research_kernel.model_gateway import ModelGateway, ModelGatewayError

        primary = ScriptedAdapter(
            "z-adapter",
            [
                ModelGatewayError(
                    "rate_limited",
                    "provider omitted a retry hint",
                    retryable=True,
                    next_route="retry-or-switch-model-provider",
                ),
                {"status": "candidate"},
            ],
        )
        sleeps = []
        configured = policy()
        configured["routes"] = [configured["routes"][0]]
        configured["retry"]["rate_limit_default_delay_ms"] = 15_000
        gateway = ModelGateway(
            {"z-adapter": primary},
            policy=configured,
            clock=lambda: "2026-07-26T12:00:00Z",
            sleep=lambda seconds: sleeps.append(seconds),
            remaining_budget_seconds=lambda: 30.0,
        )

        candidate = gateway.reason(request(), trace=self.trace)

        self.assertEqual(candidate["output"], {"status": "candidate"})
        self.assertEqual(sleeps, [15.0])
        retry = next(
            event
            for event in self.trace.events()
            if event["event_type"] == "research.run.model.retry_scheduled"
        )
        self.assertEqual(retry["payload"]["delay_ms"], 15_000)

    def test_rate_limit_default_delay_exceeding_budget_fails_over_without_sleep(self):
        from lca_research_kernel.model_gateway import ModelGateway, ModelGatewayError

        primary = ScriptedAdapter(
            "z-adapter",
            [
                ModelGatewayError(
                    "rate_limited",
                    "provider omitted a retry hint",
                    retryable=True,
                    next_route="retry-or-switch-model-provider",
                )
            ],
        )
        fallback = ScriptedAdapter("a-adapter", [{"status": "fallback"}])
        sleeps = []
        configured = policy()
        configured["retry"]["rate_limit_default_delay_ms"] = 15_000
        gateway = ModelGateway(
            {"z-adapter": primary, "a-adapter": fallback},
            policy=configured,
            clock=lambda: "2026-07-26T12:00:00Z",
            sleep=lambda seconds: sleeps.append(seconds),
            remaining_budget_seconds=lambda: 5.0,
        )

        candidate = gateway.reason(request(), trace=self.trace)

        self.assertEqual(candidate["output"], {"status": "fallback"})
        self.assertEqual(sleeps, [])
        failure = next(
            event
            for event in self.trace.events()
            if event["event_type"] == "research.run.model.failed"
        )
        self.assertEqual(
            failure["payload"]["retry_decision"],
            "skip_retry_insufficient_budget",
        )
        self.assertEqual(failure["payload"]["required_wait_seconds"], 15.0)

    def test_runtime_clones_share_open_route_health_across_project_requests(self):
        from lca_research_kernel.model_gateway import ModelGateway, ModelGatewayError
        from lca_research_kernel.run_trace import RunTrace

        primary = ScriptedAdapter(
            "z-adapter",
            [
                ModelGatewayError(
                    "provider_precondition_failed",
                    "route is unavailable in this runtime",
                    retryable=False,
                    next_route="switch-model-provider",
                ),
                {"status": "must-not-run"},
            ],
        )
        fallback = ScriptedAdapter(
            "a-adapter",
            [{"status": "first-fallback"}, {"status": "second-fallback"}],
        )
        configured = policy()
        configured["retry"]["max_attempts_per_route"] = 1
        configured["circuit_breaker"]["failure_threshold"] = 1
        parent = ModelGateway(
            {"z-adapter": primary, "a-adapter": fallback},
            policy=configured,
            clock=lambda: "2026-07-26T12:00:00Z",
        )
        first = parent.with_remaining_budget(lambda: 60.0)
        first_candidate = first.reason(request(), trace=self.trace)
        self.assertEqual(first_candidate["output"], {"status": "first-fallback"})

        second_trace = RunTrace(
            Path(self.temporary.name) / "second-trace.jsonl",
            run_id="second-policy-run",
        )
        second_request = request()
        second_request["request_id"] = "policy-request-2"
        second = parent.with_remaining_budget(lambda: 60.0)
        second_candidate = second.reason(second_request, trace=second_trace)

        self.assertEqual(second_candidate["output"], {"status": "second-fallback"})
        self.assertEqual(primary.calls, 1)
        self.assertEqual(fallback.calls, 2)

    def test_retry_after_larger_than_remaining_budget_skips_same_route_retry(self):
        from lca_research_kernel.model_gateway import ModelGateway, ModelGatewayError

        primary = ScriptedAdapter(
            "z-adapter",
            [
                ModelGatewayError(
                    "rate_limited",
                    "sanitized rate limit",
                    retryable=True,
                    next_route="retry-or-switch-model-provider",
                    retry_after_seconds=5.0,
                ),
                {"status": "must-not-run"},
            ],
        )
        fallback = ScriptedAdapter("a-adapter", [{"status": "fallback"}])
        sleeps = []
        gateway = ModelGateway(
            {"z-adapter": primary, "a-adapter": fallback},
            policy=policy(),
            clock=lambda: "2026-07-24T12:00:00Z",
            sleep=lambda seconds: sleeps.append(seconds),
            remaining_budget_seconds=lambda: 1.0,
        )

        candidate = gateway.reason(request(), trace=self.trace)

        self.assertEqual(candidate["output"], {"status": "fallback"})
        self.assertEqual(primary.calls, 1)
        self.assertEqual(fallback.calls, 1)
        self.assertEqual(sleeps, [])
        failed = [
            event["payload"]
            for event in self.trace.events()
            if event["event_type"] == "research.run.model.failed"
        ]
        self.assertEqual(failed[0]["failure_code"], "rate_limited")
        self.assertEqual(failed[0]["retry_after_seconds"], 5.0)
        self.assertEqual(failed[0]["remaining_budget_seconds"], 1.0)
        self.assertEqual(
            failed[0]["retry_decision"], "skip_retry_insufficient_budget"
        )
        failovers = [
            event["payload"]
            for event in self.trace.events()
            if event["event_type"] == "research.run.model.failover_scheduled"
        ]
        self.assertEqual(failovers[0]["from_route_id"], "quality-primary")
        self.assertEqual(failovers[0]["to_route_id"], "quality-fallback")
        self.assertEqual(
            failovers[0]["reason"], "retry_wait_exceeds_remaining_budget"
        )

    def test_non_retryable_precondition_immediately_fails_over(self):
        from lca_research_kernel.model_gateway import ModelGateway, ModelGatewayError

        primary = ScriptedAdapter(
            "z-adapter",
            [
                ModelGatewayError(
                    "provider_precondition_failed",
                    "sanitized precondition failure",
                    retryable=False,
                    next_route="switch-model-provider",
                )
            ],
        )
        fallback = ScriptedAdapter("a-adapter", [{"status": "fallback"}])
        sleeps = []
        gateway = ModelGateway(
            {"z-adapter": primary, "a-adapter": fallback},
            policy=policy(),
            clock=lambda: "2026-07-24T12:00:00Z",
            sleep=lambda seconds: sleeps.append(seconds),
            remaining_budget_seconds=lambda: 60.0,
        )

        candidate = gateway.reason(request(), trace=self.trace)

        self.assertEqual(candidate["output"], {"status": "fallback"})
        self.assertEqual(primary.calls, 1)
        self.assertEqual(fallback.calls, 1)
        self.assertEqual(sleeps, [])
        event_types = [event["event_type"] for event in self.trace.events()]
        self.assertNotIn("research.run.model.retry_scheduled", event_types)
        self.assertIn("research.run.model.failover_scheduled", event_types)
        failed = next(
            event["payload"]
            for event in self.trace.events()
            if event["event_type"] == "research.run.model.failed"
        )
        self.assertEqual(failed["failure_code"], "provider_precondition_failed")
        self.assertEqual(failed["retry_decision"], "failover_non_retryable")

    def test_explicit_catalog_route_keeps_bounded_retry_and_trace_semantics(self):
        from lca_research_kernel.model_gateway import ModelGateway, ModelGatewayError

        primary = ScriptedAdapter(
            "z-adapter",
            [
                ModelGatewayError(
                    "provider_transport_error",
                    "temporary transport failure",
                    retryable=True,
                    next_route="retry-or-switch-model-provider",
                ),
                {"status": "candidate"},
            ],
        )
        configured = {
            **policy(),
            "routes": [policy()["routes"][0]],
        }
        gateway = ModelGateway(
            {"z-adapter": primary},
            policy=configured,
            clock=lambda: "2026-07-24T12:00:00Z",
            sleep=lambda seconds: None,
        )
        explicit = request()
        explicit["provider"] = "quality-primary"
        candidate = gateway.reason(explicit, trace=self.trace)
        self.assertEqual(candidate["output"], {"status": "candidate"})
        self.assertEqual(primary.calls, 2)
        event_types = [event["event_type"] for event in self.trace.events()]
        self.assertIn("research.run.model.failed", event_types)
        self.assertIn("research.run.model.retry_scheduled", event_types)

    def test_same_request_replays_trace_candidate_without_another_provider_call(self):
        from lca_research_kernel.model_gateway import ModelGateway

        primary = ScriptedAdapter("z-adapter", [{"status": "candidate"}])
        gateway = ModelGateway(
            {"z-adapter": primary},
            policy={
                **policy(),
                "routes": [
                    {
                        "route_id": "quality-primary",
                        "adapter_id": "z-adapter",
                        "priority": 10,
                    }
                ],
            },
            clock=lambda: "2026-07-23T16:10:00Z",
        )
        first = gateway.reason(request(), trace=self.trace)
        second = gateway.reason(request(), trace=self.trace)
        self.assertEqual(first, second)
        self.assertEqual(primary.calls, 1)

    def test_failed_request_restart_continues_durable_attempt_identity(self):
        from lca_research_kernel.model_gateway import (
            ModelGateway,
            ModelGatewayError,
        )

        configured = policy()
        configured["routes"] = [configured["routes"][0]]
        configured["circuit_breaker"]["failure_threshold"] = 99
        first_adapter = ScriptedAdapter(
            "z-adapter",
            [
                ModelGatewayError(
                    "provider_transport_error",
                    "temporary transport failure",
                    retryable=True,
                    next_route="retry-or-switch-model-provider",
                ),
                ModelGatewayError(
                    "provider_transport_error",
                    "temporary transport failure",
                    retryable=True,
                    next_route="retry-or-switch-model-provider",
                ),
            ],
        )
        first = ModelGateway(
            {"z-adapter": first_adapter},
            policy=configured,
            clock=lambda: "2026-08-23T12:00:00Z",
            sleep=lambda _: None,
        )

        with self.assertRaises(ModelGatewayError):
            first.reason(request(), trace=self.trace)

        restarted_adapter = ScriptedAdapter(
            "z-adapter", [{"status": "recovered"}]
        )
        restarted = ModelGateway(
            {"z-adapter": restarted_adapter},
            policy=configured,
            clock=lambda: "2026-08-23T12:01:00Z",
            sleep=lambda _: None,
        )
        candidate = restarted.reason(request(), trace=self.trace)

        requested_attempts = [
            event["payload"]["attempt"]
            for event in self.trace.events()
            if event["event_type"] == "research.run.model.requested"
        ]
        self.assertEqual(candidate["output"], {"status": "recovered"})
        self.assertEqual(first_adapter.calls, 2)
        self.assertEqual(restarted_adapter.calls, 1)
        self.assertEqual(requested_attempts, [1, 2, 3])

    def test_ineligible_adapter_and_invalid_reported_model_fail_closed(self):
        from lca_research_kernel.model_gateway import ModelGateway, ModelGatewayError

        ineligible = ScriptedAdapter(
            "z-adapter",
            [{"status": "candidate"}],
            capabilities={
                "structured_output": True,
                "streaming": False,
                "tool_requests": True,
                "remote_execution": False,
            },
        )
        gateway = ModelGateway(
            {"z-adapter": ineligible},
            policy={
                **policy(),
                "routes": [
                    {
                        "route_id": "quality-primary",
                        "adapter_id": "z-adapter",
                        "priority": 10,
                    }
                ],
            },
            clock=lambda: "2026-07-23T16:10:00Z",
        )
        with self.assertRaises(ModelGatewayError) as captured:
            gateway.reason(request(), trace=self.trace)
        self.assertEqual(captured.exception.code, "provider_unavailable")
        self.assertEqual(ineligible.calls, 0)

        class MissingModelAdapter(ScriptedAdapter):
            def reason(self, model_request):
                output, metadata = super().reason(model_request)
                metadata["model"] = ""
                return output, metadata

        missing = MissingModelAdapter("z-adapter", [{"status": "candidate"}])
        gateway = ModelGateway(
            {"z-adapter": missing},
            policy={
                **policy(),
                "routes": [
                    {
                        "route_id": "quality-primary",
                        "adapter_id": "z-adapter",
                        "priority": 10,
                    }
                ],
            },
            clock=lambda: "2026-07-23T16:10:00Z",
        )
        with self.assertRaises(ModelGatewayError) as captured:
            gateway.reason(request(), trace=self.trace)
        self.assertEqual(captured.exception.code, "model_identity_missing")

    def test_streaming_policy_retries_then_switches_without_mixing_attempt_identity(self):
        from lca_research_kernel.model_gateway import ModelGateway, ModelGatewayError

        primary = StreamingAdapter(
            "z-adapter",
            [
                ModelGatewayError(
                    "provider_timeout",
                    "timeout",
                    retryable=True,
                    next_route="retry-or-switch-model-provider",
                ),
                ModelGatewayError(
                    "provider_server_error",
                    "server",
                    retryable=True,
                    next_route="retry-or-switch-model-provider",
                ),
            ],
        )
        fallback = StreamingAdapter(
            "a-adapter", [{"status": "fallback"}]
        )
        gateway = ModelGateway(
            {"z-adapter": primary, "a-adapter": fallback},
            policy=policy(),
            clock=lambda: "2026-07-24T01:00:00Z",
            sleep=lambda _: None,
        )
        events = list(gateway.stream(request(), trace=self.trace))
        self.assertEqual(events[-1]["type"], "candidate_final")
        self.assertEqual(events[-1]["candidate"]["output"], {"status": "fallback"})
        self.assertEqual(primary.calls, 2)
        self.assertEqual(fallback.calls, 1)
        starts = [
            row for row in events if row["type"] == "model_stream_started"
        ]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["adapter_id"], "a-adapter")
        trace_types = [row["event_type"] for row in self.trace.events()]
        self.assertIn("research.run.model.failed", trace_types)
        self.assertIn("research.run.model.retry_scheduled", trace_types)

    def test_streaming_retry_after_respects_the_same_remaining_budget_port(self):
        from lca_research_kernel.model_gateway import ModelGateway, ModelGatewayError

        primary = StreamingAdapter(
            "z-adapter",
            [
                ModelGatewayError(
                    "rate_limited",
                    "sanitized rate limit",
                    retryable=True,
                    next_route="retry-or-switch-model-provider",
                    retry_after_seconds=5.0,
                ),
                {"status": "must-not-run"},
            ],
        )
        fallback = StreamingAdapter(
            "a-adapter", [{"status": "fallback"}]
        )
        sleeps = []
        gateway = ModelGateway(
            {"z-adapter": primary, "a-adapter": fallback},
            policy=policy(),
            clock=lambda: "2026-07-24T01:00:00Z",
            sleep=lambda seconds: sleeps.append(seconds),
            remaining_budget_seconds=lambda: 1.0,
        )

        events = list(gateway.stream(request(), trace=self.trace))

        self.assertEqual(events[-1]["candidate"]["output"], {"status": "fallback"})
        self.assertEqual(primary.calls, 1)
        self.assertEqual(fallback.calls, 1)
        self.assertEqual(sleeps, [])
        failed = next(
            event["payload"]
            for event in self.trace.events()
            if event["event_type"] == "research.run.model.failed"
        )
        self.assertEqual(
            failed["retry_decision"], "skip_retry_insufficient_budget"
        )
        self.assertEqual(failed["retry_after_seconds"], 5.0)
        self.assertEqual(failed["remaining_budget_seconds"], 1.0)
        self.assertIn(
            "research.run.model.failover_scheduled",
            [event["event_type"] for event in self.trace.events()],
        )

    def test_streaming_unknown_explicit_route_is_a_typed_failure(self):
        from lca_research_kernel.model_gateway import ModelGateway, ModelGatewayError

        gateway = ModelGateway(
            {"z-adapter": StreamingAdapter("z-adapter", [{"status": "ok"}])},
            default_provider="z-adapter",
        )
        unknown = request()
        unknown["provider"] = "not-registered"
        with self.assertRaises(ModelGatewayError) as captured:
            list(gateway.stream(unknown, trace=self.trace))
        self.assertEqual(captured.exception.code, "provider_unavailable")

    def test_process_restart_restores_open_circuit_from_run_trace(self):
        from lca_research_kernel.model_gateway import ModelGateway, ModelGatewayError

        first_primary = ScriptedAdapter(
            "z-adapter",
            [
                ModelGatewayError(
                    "provider_server_error",
                    "server",
                    retryable=True,
                    next_route="retry-or-switch-model-provider",
                ),
                ModelGatewayError(
                    "provider_server_error",
                    "server",
                    retryable=True,
                    next_route="retry-or-switch-model-provider",
                ),
            ],
        )
        first_fallback = ScriptedAdapter(
            "a-adapter", [{"status": "fallback"}]
        )
        first = ModelGateway(
            {
                "z-adapter": first_primary,
                "a-adapter": first_fallback,
            },
            policy=policy(),
            clock=lambda: "2026-07-24T01:00:00Z",
            sleep=lambda _: None,
        )
        first.reason(request(), trace=self.trace)
        self.assertIn("z-adapter", first._circuit_open)

        restarted_primary = ScriptedAdapter(
            "z-adapter", [{"status": "must-not-run"}]
        )
        restarted_fallback = ScriptedAdapter(
            "a-adapter", [{"status": "after-restart"}]
        )
        restarted = ModelGateway(
            {
                "z-adapter": restarted_primary,
                "a-adapter": restarted_fallback,
            },
            policy=policy(),
            clock=lambda: "2026-07-24T01:01:00Z",
            sleep=lambda _: None,
        )
        new_request = request()
        new_request["request_id"] = "policy-request-after-restart"
        candidate = restarted.reason(new_request, trace=self.trace)
        self.assertEqual(candidate["output"], {"status": "after-restart"})
        self.assertEqual(restarted_primary.calls, 0)
        self.assertEqual(restarted_fallback.calls, 1)

    def test_production_circuit_cooldown_allows_one_later_recovery_probe(self):
        from lca_research_kernel.model_gateway import ModelGateway, ModelGatewayError

        primary = ScriptedAdapter(
            "z-adapter",
            [
                ModelGatewayError(
                    "provider_server_error",
                    "server",
                    retryable=True,
                    next_route="retry-or-switch-model-provider",
                ),
                ModelGatewayError(
                    "provider_server_error",
                    "server",
                    retryable=True,
                    next_route="retry-or-switch-model-provider",
                ),
                {"status": "recovered"},
            ],
        )
        fallback = ScriptedAdapter(
            "a-adapter",
            [
                {"status": "fallback-1"},
                {"status": "fallback-2"},
            ],
        )
        configured = policy()
        configured["circuit_breaker"]["cooldown_seconds"] = 30
        monotonic = [0.0]
        gateway = ModelGateway(
            {
                "z-adapter": primary,
                "a-adapter": fallback,
            },
            policy=configured,
            clock=lambda: "2026-07-24T01:00:00Z",
            monotonic=lambda: monotonic[0],
            sleep=lambda _: None,
        )

        gateway.reason(request(), trace=self.trace)
        self.assertEqual(primary.calls, 2)

        monotonic[0] = 10.0
        second = request()
        second["request_id"] = "policy-request-before-cooldown"
        candidate = gateway.reason(second, trace=self.trace)
        self.assertEqual(candidate["output"], {"status": "fallback-2"})
        self.assertEqual(primary.calls, 2)

        monotonic[0] = 31.0
        third = request()
        third["request_id"] = "policy-request-after-cooldown"
        candidate = gateway.reason(third, trace=self.trace)
        self.assertEqual(candidate["output"], {"status": "recovered"})
        self.assertEqual(primary.calls, 3)
        self.assertNotIn("z-adapter", gateway._circuit_open)

    def test_circuit_cooldown_must_be_a_positive_number(self):
        from lca_research_kernel.model_gateway import ModelGateway

        configured = policy()
        configured["circuit_breaker"]["cooldown_seconds"] = 0
        with self.assertRaisesRegex(ValueError, "circuit-breaker"):
            ModelGateway(
                {"z-adapter": ScriptedAdapter("z-adapter", [])},
                policy=configured,
            )

    def test_old_project_trace_cannot_reopen_a_recovered_shared_circuit(self):
        from lca_research_kernel.model_gateway import ModelGateway, ModelGatewayError
        from lca_research_kernel.run_trace import RunTrace

        primary = ScriptedAdapter(
            "z-adapter",
            [
                ModelGatewayError(
                    "provider_server_error",
                    "server",
                    retryable=True,
                    next_route="retry-or-switch-model-provider",
                ),
                ModelGatewayError(
                    "provider_server_error",
                    "server",
                    retryable=True,
                    next_route="retry-or-switch-model-provider",
                ),
                {"status": "recovered"},
                {"status": "still-recovered"},
            ],
        )
        fallback = ScriptedAdapter(
            "a-adapter", [{"status": "fallback"}]
        )
        configured = policy()
        configured["circuit_breaker"]["cooldown_seconds"] = 30
        monotonic = [0.0]
        gateway = ModelGateway(
            {
                "z-adapter": primary,
                "a-adapter": fallback,
            },
            policy=configured,
            monotonic=lambda: monotonic[0],
            sleep=lambda _: None,
        )
        gateway.reason(request(), trace=self.trace)
        second_trace = RunTrace(
            Path(self.temporary.name) / "second-trace.jsonl",
            run_id="second-run",
        )
        monotonic[0] = 31.0
        recovered_request = request()
        recovered_request["request_id"] = "recovered-request"
        gateway.reason(recovered_request, trace=second_trace)

        monotonic[0] = 32.0
        later_request = request()
        later_request["request_id"] = "later-old-trace-request"
        candidate = gateway.reason(later_request, trace=self.trace)

        self.assertEqual(candidate["output"], {"status": "still-recovered"})
        self.assertEqual(primary.calls, 4)
        self.assertNotIn("z-adapter", gateway._circuit_open)

    def test_restart_restores_newest_circuit_event_independent_of_trace_order(self):
        from lca_research_kernel.model_gateway import ModelGateway
        from lca_research_kernel.run_trace import RunTrace

        old_trace = RunTrace(
            Path(self.temporary.name) / "old-success-trace.jsonl",
            run_id="old-success-run",
        )
        old_trace.append(
            "research.run.model.completed",
            {
                "adapter_id": "z-adapter",
                "candidate": {"status": "candidate_only"},
            },
            idempotency_key="old-success",
            occurred_at="2026-07-24T01:00:00Z",
        )
        newer_trace = RunTrace(
            Path(self.temporary.name) / "newer-open-trace.jsonl",
            run_id="newer-open-run",
        )
        newer_trace.append(
            "research.run.model.circuit_opened",
            {
                "adapter_id": "z-adapter",
                "failure_count": 1,
                "opened_at": "2026-07-24T01:10:00Z",
            },
            idempotency_key="newer-open",
            occurred_at="2026-07-24T01:10:00Z",
        )
        primary = ScriptedAdapter(
            "z-adapter", [{"status": "must-not-run"}]
        )
        fallback = ScriptedAdapter(
            "a-adapter", [{"status": "newer-state-wins"}]
        )
        configured = policy()
        configured["circuit_breaker"]["cooldown_seconds"] = 600
        restarted = ModelGateway(
            {
                "z-adapter": primary,
                "a-adapter": fallback,
            },
            policy=configured,
            clock=lambda: "2026-07-24T01:11:00Z",
            sleep=lambda _: None,
        )

        restarted._restore_trace_runtime_state(old_trace)
        restarted._restore_trace_runtime_state(newer_trace)
        next_request = request()
        next_request["request_id"] = "after-out-of-order-restore"
        candidate = restarted.reason(next_request)

        self.assertEqual(candidate["output"], {"status": "newer-state-wins"})
        self.assertEqual(primary.calls, 0)
        self.assertEqual(fallback.calls, 1)


if __name__ == "__main__":
    unittest.main()
