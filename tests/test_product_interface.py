import ast
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "lca_research_kernel/product_interface.py"


class ProductInterfaceTests(unittest.TestCase):
    def setUp(self):
        from lca_research_kernel.assurance import AssuranceBoundary
        from lca_research_kernel.model_gateway import (
            DeterministicFakeAdapter,
            ModelGateway,
        )
        from lca_research_kernel.product_interface import ProductInterface

        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "product"
        gateway = ModelGateway(
            {"fake": DeterministicFakeAdapter({"plan": "bounded next LCA step"})},
            default_provider="fake",
        )
        self.interface = ProductInterface(
            self.root,
            model_gateway=gateway,
            assurance=AssuranceBoundary(),
            clock=lambda: "2026-07-23T06:01:00Z",
            run_id="product-interface-test-runtime",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def start_request(self):
        return {
            "contract": "lca_research_kernel.research_start/v1",
            "research_id": "research-1",
            "title": "Source-bound LCA research",
            "objective": "Interpret one accepted research brief.",
            "source_refs": [
                {"path": "inputs/brief.json", "sha256": "a" * 64}
            ],
            "received_at": "2026-07-23T06:00:00Z",
        }

    def test_start_is_the_only_required_product_action_and_hides_internal_routing(self):
        request = self.start_request()
        self.assertFalse(
            {"next_route", "capability_id", "model", "provider", "workflow"}
            & set(request)
        )
        status = self.interface.start_research(request)
        self.assertEqual(status["research_id"], "research-1")
        self.assertEqual(status["boundary"], "awaiting_domain_and_capability_integration")
        self.assertEqual(status["status"], "active")
        self.assertNotIn("next_route", status)
        before = self.interface._open_record("research-1").read_audit_trail()
        self.assertEqual(self.interface.start_research(request), status)
        after = self.interface._open_record("research-1").read_audit_trail()
        self.assertEqual(after, before)
        starts = [
            row
            for row in after
            if row["event_type"] == "research.agent.started"
        ]
        self.assertEqual(len(starts), 1)
        self.assertEqual(
            starts[0]["payload"]["startup_context"]["product_id"],
            "tiangong_native_lca_research_agent",
        )

    def test_supplied_material_stays_candidate_and_decision_is_auditable(self):
        self.interface.start_research(self.start_request())
        material = {
            "contract": "lca_research_kernel.research_material_or_decision/v1",
            "input_id": "material-1",
            "kind": "material",
            "payload": {"fact_id": "activity-data", "value": 12, "unit": "kg"},
            "source_refs": [
                {"path": "inputs/user-material.json", "sha256": "b" * 64}
            ],
            "received_at": "2026-07-23T06:02:00Z",
        }
        self.interface.supply_material_or_decision("research-1", material)
        view = self.interface._open_record("research-1").read_current_project_view()
        self.assertEqual(view["facts"]["activity-data"]["classification"], "candidate")
        decision = {
            "contract": "lca_research_kernel.research_material_or_decision/v1",
            "input_id": "decision-1",
            "kind": "decision",
            "payload": {
                "decision_id": "scope-choice",
                "decision": "retain the declared foreground boundary",
            },
            "source_refs": [
                {"path": "inputs/user-decision.json", "sha256": "c" * 64}
            ],
            "received_at": "2026-07-23T06:03:00Z",
        }
        self.interface.supply_material_or_decision("research-1", decision)
        view = self.interface._open_record("research-1").read_current_project_view()
        self.assertEqual(view["decisions"][0]["decision_id"], "scope-choice")

    def test_material_processing_recovery_decision_is_accepted_without_running_research(self):
        self.interface.start_research(self.start_request())
        supplied = {
            "contract": "lca_research_kernel.research_material_or_decision/v1",
            "input_id": "material-recovery-1",
            "kind": "decision",
            "payload": {
                "decision_id": "material-recovery-choice",
                "decision": "omit_failed_materials",
            },
            "source_refs": [
                {"path": "inputs/material-recovery.json", "sha256": "d" * 64}
            ],
            "received_at": "2026-07-23T06:03:00Z",
            "material_recovery": {
                "contract": "lca_research_service.material_processing_recovery/v1",
                "interaction_kind": "material_processing_recovery",
                "failed_materials": [{
                    "material_id": "material-a",
                    "revision_id": "revision-a",
                    "processing_status": "failed",
                }],
                "allowed_decisions": ["retry", "omit_failed_materials"],
            },
        }
        event = self.interface.record_material_recovery_decision(
            "research-1",
            supplied,
            effective_material_revision_refs=[],
        )
        view = self.interface._open_record("research-1").read_current_project_view()
        accepted = view["decisions"][-1]
        self.assertEqual(event["event_type"], "research.project.accepted_change")
        self.assertEqual(accepted["decision"], "omit_failed_materials")
        self.assertEqual(accepted["decision_kind"], "material_processing_recovery")
        self.assertEqual(accepted["failed_materials"][0]["revision_id"], "revision-a")
        self.assertEqual(accepted["effective_material_revision_refs"], [])
        self.assertEqual(
            self.interface.record_material_recovery_decision(
                "research-1",
                supplied,
                effective_material_revision_refs=[],
            ),
            event,
        )

    def test_content_bound_user_turn_resumes_same_project_idempotently(self):
        self.interface.start_research(self.start_request())
        record = self.interface._open_record("research-1")
        turn = {
            "contract": "lca_research_kernel.research_user_turn/v1",
            "input_id": "turn-1",
            "text": "LCA 是什么？",
            "source_refs": [
                {"path": "inputs/turn-1.json", "sha256": "9" * 64}
            ],
            "received_at": "2026-07-23T06:04:00Z",
        }

        status = self.interface.continue_research("research-1", turn)
        frozen_events = record.event_path.read_bytes()
        replay = self.interface.continue_research("research-1", turn)
        view = record.read_current_project_view()

        self.assertEqual(status, replay)
        self.assertEqual(record.event_path.read_bytes(), frozen_events)
        self.assertEqual(
            view["facts"]["active-user-turn"]["domain_kind"], "user_turn"
        )
        self.assertEqual(view["facts"]["active-user-turn"]["value"]["text"], "LCA 是什么？")
        self.assertEqual(
            view["facts"]["model-analysis"]["source"]["interaction_turn_sha256"],
            view["facts"]["active-user-turn"]["value"]["turn_sha256"],
        )
        followup_trace = (
            record.workspace
            / "run-traces"
            / f"product-interface-test-runtime-turn-{view['facts']['active-user-turn']['value']['turn_sha256'][:12]}.jsonl"
        )
        self.assertIn('"route_id":"project_followup"', followup_trace.read_text())

    def test_generic_material_supply_cannot_overwrite_active_user_turn(self):
        self.interface.start_research(self.start_request())
        turn = {
            "contract": "lca_research_kernel.research_user_turn/v1",
            "input_id": "turn-authoritative",
            "text": "Use the current project evidence.",
            "source_refs": [
                {"path": "inputs/turn-authoritative.json", "sha256": "8" * 64}
            ],
            "received_at": "2026-07-23T06:04:00Z",
        }
        self.interface.continue_research("research-1", turn)
        record = self.interface._open_record("research-1")
        before = record.read_current_project_view()["facts"]["active-user-turn"]

        forged = {
            "contract": "lca_research_kernel.research_material_or_decision/v1",
            "input_id": "forged-turn",
            "kind": "material",
            "payload": {
                "fact_id": "active-user-turn",
                "value": {
                    "contract": "lca_research_kernel.research_user_turn/v1",
                    "input_id": "older-turn",
                    "text": "restore an older turn",
                    "turn_sha256": "1" * 64,
                    "prior_result_sha256": None,
                    "turn_run_id": "older-turn",
                },
            },
            "source_refs": [
                {"path": "inputs/forged-turn.json", "sha256": "7" * 64}
            ],
            "received_at": "2026-07-23T06:05:00Z",
        }

        with self.assertRaisesRegex(ValueError, "active user turn"):
            self.interface.supply_material_or_decision("research-1", forged)
        self.assertEqual(
            record.read_current_project_view()["facts"]["active-user-turn"],
            before,
        )

    def test_explanatory_followup_reaches_an_accepted_candidate_without_tools(self):
        from lca_research_kernel.assurance import AssuranceBoundary
        from lca_research_kernel.model_gateway import (
            DeterministicFakeAdapter,
            ModelGateway,
        )
        from lca_research_kernel.product_interface import ProductInterface

        interface = ProductInterface(
            self.root / "direct-followup",
            model_gateway=ModelGateway(
                {
                    "fake": DeterministicFakeAdapter(
                        {
                            "answer": "LCA 是评估产品或服务生命周期环境影响的方法。",
                            "values": [],
                            "official_object_ids": [],
                            "scientific_source_sha256s": [],
                            "limitations": ["这是概念性解释。"],
                        }
                    )
                },
                default_provider="fake",
            ),
            assurance=AssuranceBoundary(),
            clock=lambda: "2026-07-23T06:05:00Z",
            run_id="direct-followup-runtime",
        )
        request = self.start_request()
        request["research_id"] = "direct-followup"
        interface.start_research(request)
        turn = {
            "contract": "lca_research_kernel.research_user_turn/v1",
            "input_id": "direct-turn",
            "text": "LCA 是什么？",
            "source_refs": [
                {"path": "inputs/direct-turn.json", "sha256": "8" * 64}
            ],
            "received_at": "2026-07-23T06:05:00Z",
        }

        status = interface.continue_research("direct-followup", turn)
        result = interface.read_result("direct-followup")

        self.assertEqual(status["boundary"], "research_result_accepted")
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["result"]["claim_ceiling"], "candidate_only")
        self.assertEqual(result["result"]["values"], [])
        self.assertEqual(result["result"]["official_object_ids"], [])
        self.assertEqual(
            result["effect_receipt"]["effect_counts"]["external_write"], 0
        )
        self.assertEqual(
            len(result["effect_receipt"]["registered_run_trace_refs"]), 2
        )

    def test_referential_followup_receives_the_immediately_prior_accepted_answer(self):
        from copy import deepcopy

        from lca_research_kernel.assurance import AssuranceBoundary
        from lca_research_kernel.model_gateway import ModelGateway
        from lca_research_kernel.product_interface import ProductInterface

        class DiscourseAwareAdapter:
            provider_id = "fake"
            model_id = "discourse-aware-test"

            def __init__(self):
                self.requests = []

            def capabilities(self):
                return {
                    "provider": self.provider_id,
                    "adapter_id": self.provider_id,
                    "configured_model_id": self.model_id,
                    "structured_output": True,
                    "streaming": True,
                    "tool_requests": True,
                    "remote_execution": False,
                    "generic_source_candidate_v1": True,
                }

            def reason(self, request):
                self.requests.append(deepcopy(dict(request)))
                items = request.get("context", {}).get("items", [])
                active_text = next(
                    (
                        item.get("content", {}).get("text")
                        for item in items
                        if item.get("identity") == "project_fact:active-user-turn"
                    ),
                    None,
                )
                prior = next(
                    (
                        item.get("content")
                        for item in items
                        if isinstance(item.get("content"), dict)
                        and item["content"].get("contract")
                        == "lca_research_kernel.research_dialogue_predecessor/v1"
                    ),
                    None,
                )
                if active_text == "请举一个具体例子。":
                    answer = (
                        "例如，同一过程数据从系统甲导入系统乙时，必填元数据、"
                        "版本和统一流标识保持不变，双方据此复算出相同结果。"
                        if isinstance(prior, dict)
                        and "必填元数据" in str(prior.get("answer"))
                        else "例如，可以比较纸杯和陶瓷杯的生命周期环境影响。"
                    )
                elif active_text is not None:
                    answer = (
                        "该规范通过必填元数据提高质量，通过版本化参数和边界记录"
                        "保障复现，并通过统一流标识实现跨系统交换。"
                    )
                else:
                    answer = "该规范定义生命周期数据的结构与交换边界。"
                return {
                    "answer": answer,
                    "values": [],
                    "official_object_ids": [],
                    "scientific_source_sha256s": [],
                    "limitations": ["这是不含正式数据值的概念性解释。"],
                    "missing_fields": [],
                    "capability_intent": None,
                }, {
                    "provider_response_id": "fake-discourse",
                    "model": self.model_id,
                    "provider": self.provider_id,
                    "adapter_id": self.provider_id,
                    "usage": {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                    },
                    "tool_requests": [],
                    "duration_ms": 0,
                    "cost": None,
                }

        adapter = DiscourseAwareAdapter()
        interface = ProductInterface(
            self.root / "referential-followup",
            model_gateway=ModelGateway({"fake": adapter}, default_provider="fake"),
            assurance=AssuranceBoundary(),
            clock=lambda: "2026-08-21T13:00:00Z",
            run_id="referential-followup-runtime",
        )
        request = self.start_request()
        request["research_id"] = "referential-followup"
        request["objective"] = "这个数据交换规范是什么？"
        interface.start_research(request)
        mechanism_status = interface.continue_research(
            "referential-followup",
            {
                "contract": "lca_research_kernel.research_user_turn/v1",
                "input_id": "mechanism-turn",
                "text": "它如何保障质量、可复现性和跨系统互操作性？",
                "source_refs": [
                    {"path": "inputs/mechanism-turn.json", "sha256": "6" * 64}
                ],
                "received_at": "2026-08-21T13:01:00Z",
            },
        )
        example_status = interface.continue_research(
            "referential-followup",
            {
                "contract": "lca_research_kernel.research_user_turn/v1",
                "input_id": "example-turn",
                "text": "请举一个具体例子。",
                "source_refs": [
                    {"path": "inputs/example-turn.json", "sha256": "7" * 64}
                ],
                "received_at": "2026-08-21T13:02:00Z",
            },
        )

        result_view = interface.read_result("referential-followup")
        self.assertEqual(
            result_view["status"],
            "accepted",
            {
                "mechanism_status": mechanism_status,
                "example_status": example_status,
                "result_view": result_view,
                "request_count": len(adapter.requests),
            },
        )
        result = result_view["result"]
        self.assertIn("从系统甲导入系统乙", result["answer"])
        example_request = adapter.requests[-1]
        prior_items = [
            item
            for item in example_request["context"]["items"]
            if isinstance(item.get("content"), dict)
            and item["content"].get("contract")
            == "lca_research_kernel.research_dialogue_predecessor/v1"
        ]
        self.assertEqual(len(prior_items), 1)
        self.assertIn("必填元数据", prior_items[0]["content"]["answer"])
        self.assertEqual(
            [item["identity"] for item in example_request["context"]["items"]],
            [
                "project_fact:active-user-turn",
                "project_result:" + prior_items[0]["content"]["prior_result_sha256"],
            ],
        )
        self.assertIn("immediately preceding", example_request["task"])
        self.assertIn("most specific subject", example_request["task"])

    def test_initial_direct_explanation_uses_typed_schema_and_reaches_an_accepted_result(self):
        from copy import deepcopy

        from lca_research_kernel.assurance import AssuranceBoundary
        from lca_research_kernel.model_gateway import ModelGateway
        from lca_research_kernel.product_interface import ProductInterface

        class SchemaAwareDirectAdapter:
            provider_id = "fake"
            model_id = "schema-aware-direct-test"

            def capabilities(self):
                return {
                    "provider": self.provider_id,
                    "adapter_id": self.provider_id,
                    "configured_model_id": self.model_id,
                    "structured_output": True,
                    "streaming": True,
                    "tool_requests": True,
                    "remote_execution": False,
                    "generic_source_candidate_v1": True,
                }

            def reason(self, request):
                required = request.get("output_schema", {}).get("required", [])
                output = (
                    {
                        "answer": (
                            "功能单位决定比较基准；分配规则决定共同过程的负荷如何分摊；"
                            "系统边界决定哪些过程进入模型。"
                        ),
                        "values": [],
                        "official_object_ids": [],
                        "scientific_source_sha256s": [],
                        "limitations": ["这是不含正式数据和计算结论的概念性解释。"],
                        "capability_intent": None,
                    }
                    if "answer" in required
                    else {"plan": "await domain integration"}
                )
                return deepcopy(output), {
                    "provider_response_id": "fake-initial-direct",
                    "model": self.model_id,
                    "provider": self.provider_id,
                    "adapter_id": self.provider_id,
                    "usage": {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                    },
                    "tool_requests": [],
                    "duration_ms": 0,
                    "cost": None,
                }

        interface = ProductInterface(
            self.root / "initial-direct",
            model_gateway=ModelGateway(
                {"fake": SchemaAwareDirectAdapter()},
                default_provider="fake",
            ),
            assurance=AssuranceBoundary(),
            clock=lambda: "2026-07-23T06:06:00Z",
            run_id="initial-direct-runtime",
        )
        request = self.start_request()
        request.update(
            {
                "research_id": "initial-direct",
                "objective": (
                    "解释功能单位、分配和系统边界如何改变 LCA 结果，并给出一个"
                    "可核对的简单示例。"
                ),
            }
        )

        status = interface.start_research(request)
        result = interface.read_result("initial-direct")

        self.assertEqual(status["boundary"], "research_result_accepted")
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["result"]["claim_ceiling"], "candidate_only")
        self.assertEqual(result["result"]["values"], [])
        self.assertEqual(result["effect_receipt"]["effect_counts"]["external_write"], 0)
        execution = result["execution_receipt"]
        self.assertEqual(
            execution["contract"],
            "lca_research_kernel.public_execution_receipt/v1",
        )
        self.assertEqual(execution["counts"]["model"], 1)
        self.assertEqual(execution["counts"]["tool"], 0)
        self.assertEqual(execution["counts"]["external_write"], 0)
        self.assertGreaterEqual(execution["first_model_request_ms"], 0)
        self.assertEqual(execution["model_calls"][0]["provider"], "fake")
        self.assertEqual(
            execution["model_calls"][0]["model"],
            "schema-aware-direct-test",
        )
        self.assertNotIn("candidate", execution["model_calls"][0])
        self.assertNotIn("provider_response_id", execution["model_calls"][0])
        self.assertTrue(execution["receipt_sha256"])

    def test_service_frozen_material_adapter_is_accepted_by_real_product_interface(self):
        from lca_research_service.app import _material_candidate_payload

        self.interface.start_research(self.start_request())
        frozen = {
            "path": "projects/research-1/materials/" + "d" * 64,
            "sha256": "d" * 64,
            "authority": "user_supplied_project_input",
            "material_id": "sha256:" + "d" * 64,
            "name": "supplement.txt",
            "media_type": "text/plain",
        }
        supplied = {
            "contract": "lca_research_kernel.research_material_or_decision/v1",
            "input_id": "typed-supplement-1",
            "kind": "material",
            "payload": _material_candidate_payload(frozen),
            "source_refs": [
                {"path": frozen["path"], "sha256": frozen["sha256"]}
            ],
            "received_at": "2026-07-23T06:02:00Z",
        }
        self.interface.supply_material_or_decision("research-1", supplied)
        fact = self.interface._open_record("research-1").read_current_project_view()[
            "facts"
        ]["user-material:" + "d" * 64]
        self.assertEqual(fact["classification"], "candidate")
        self.assertEqual(
            fact["value"]["authority"], "user_supplied_project_input"
        )

    def test_status_and_result_are_read_only_bounded_product_views(self):
        self.interface.start_research(self.start_request())
        before = self.interface._open_record("research-1").event_path.read_bytes()
        status = self.interface.read_status("research-1")
        result = self.interface.read_result("research-1")
        self.assertEqual(status["status"], "active")
        self.assertEqual(result["status"], "result_not_yet_accepted")
        self.assertEqual(self.interface._open_record("research-1").event_path.read_bytes(), before)

    def test_resume_research_advances_without_replaying_the_intake(self):
        request = self.start_request()
        self.interface.start_research(request)
        record = self.interface._open_record("research-1")
        created = [
            event
            for event in record.read_audit_trail()
            if event["event_type"] == "research.project.created"
        ]

        status = self.interface.resume_research("research-1")

        self.assertEqual(status["research_id"], "research-1")
        self.assertEqual(
            len(
                [
                    event
                    for event in record.read_audit_trail()
                    if event["event_type"] == "research.project.created"
                ]
            ),
            len(created),
        )

    def test_status_preserves_only_safe_capacity_recovery_fields(self):
        self.interface.start_research(self.start_request())
        record = self.interface._open_record("research-1")
        status = self.interface._status_view(
            record,
            {
                "boundary": "provider_capacity_deferred",
                "recovery_route": "wait-for-provider-capacity-reset",
                "retry_after_seconds": 1728.0,
                "qualified_task_count": 0,
                "capacity_receipt_ref": {
                    "path": "runtime/provider-capacity.json",
                    "sha256": "d" * 64,
                },
                "authorization": "must-not-survive",
                "x-request-id": "must-not-survive",
            },
        )
        self.assertEqual(
            status["recovery_route"],
            "wait-for-provider-capacity-reset",
        )
        self.assertEqual(status["retry_after_seconds"], 1728.0)
        self.assertEqual(status["qualified_task_count"], 0)
        self.assertEqual(
            status["capacity_receipt_ref"]["sha256"],
            "d" * 64,
        )
        self.assertNotIn("authorization", status)
        self.assertNotIn("x-request-id", status)
        self.assertEqual(
            self.interface.read_result("research-1")["status"],
            "result_not_yet_accepted",
        )
        with self.assertRaisesRegex(ValueError, "capacity receipt"):
            self.interface._status_view(
                record,
                {
                    "boundary": "provider_capacity_deferred",
                    "capacity_receipt_ref": {
                        "path": "runtime/provider-capacity.json",
                        "sha256": "d" * 64,
                        "authorization": "must-not-survive",
                    },
                },
            )

    def test_read_views_do_not_retry_a_failed_real_model_attempt(self):
        from lca_research_kernel.assurance import AssuranceBoundary
        from lca_research_kernel.model_gateway import ModelGateway, ModelGatewayError
        from lca_research_kernel.product_interface import ProductInterface

        class FailingAdapter:
            def __init__(self):
                self.calls = 0

            def capabilities(self):
                return {
                    "structured_output": True,
                    "streaming": True,
                    "tool_requests": True,
                    "remote_execution": False,
                }

            def reason(self, request):
                self.calls += 1
                raise ModelGatewayError(
                    "rate_limited",
                    "bounded provider failure",
                    retryable=True,
                    next_route="retry-or-switch-model-provider",
                )

        adapter = FailingAdapter()
        interface = ProductInterface(
            self.root / "read-only-after-failure",
            model_gateway=ModelGateway(
                {"failing": adapter}, default_provider="failing"
            ),
            assurance=AssuranceBoundary(),
            clock=lambda: "2026-07-26T12:00:00Z",
            run_id="read-only-failure-runtime",
        )
        request = self.start_request()
        request["research_id"] = "read-only-failure"

        started = interface.start_research(request)
        trace_path = (
            interface._open_record("read-only-failure").workspace
            / "run-traces/read-only-failure-runtime.jsonl"
        )
        frozen_trace = trace_path.read_bytes()
        status = interface.read_status("read-only-failure")
        result = interface.read_result("read-only-failure")

        self.assertEqual(started["boundary"], "model_failure")
        self.assertEqual(status["boundary"], "model_failure")
        self.assertEqual(result["status"], "result_not_yet_accepted")
        self.assertEqual(adapter.calls, 1)
        self.assertEqual(trace_path.read_bytes(), frozen_trace)

    def test_every_public_action_revalidates_one_idempotent_startup_receipt(self):
        self.interface.start_research(self.start_request())
        self.interface.read_status("research-1")
        self.interface.read_result("research-1")
        record = self.interface._open_record("research-1")
        starts = [
            row
            for row in record.read_audit_trail()
            if row["event_type"] == "research.agent.started"
        ]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["payload"]["run_id"], "product-interface-test-runtime")

    def test_public_actions_reuse_the_startup_timestamp_when_the_clock_advances(self):
        from lca_research_kernel.assurance import AssuranceBoundary
        from lca_research_kernel.model_gateway import (
            DeterministicFakeAdapter,
            ModelGateway,
        )
        from lca_research_kernel.product_interface import ProductInterface

        clock_calls = []

        def advancing_clock():
            observed_at = f"2026-07-23T{len(clock_calls):02d}:00:00Z"
            clock_calls.append(observed_at)
            return observed_at

        interface = ProductInterface(
            self.root / "advancing-clock",
            model_gateway=ModelGateway(
                {"fake": DeterministicFakeAdapter({"plan": "bounded next LCA step"})},
                default_provider="fake",
            ),
            assurance=AssuranceBoundary(),
            clock=advancing_clock,
            run_id="stable-startup-runtime",
        )
        self.assertEqual(clock_calls, ["2026-07-23T00:00:00Z"])
        interface.start_research(self.start_request())
        record = interface._open_record("research-1")
        before_replay = record.event_path.read_bytes()

        interface.read_status("research-1")
        interface.read_result("research-1")

        self.assertEqual(record.event_path.read_bytes(), before_replay)
        starts = [
            row
            for row in record.read_audit_trail()
            if row["event_type"] == "research.agent.started"
        ]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["occurred_at"], clock_calls[0])

    def test_reconstructed_interface_reuses_prior_startup_observation_for_same_run(self):
        from lca_research_kernel.assurance import AssuranceBoundary
        from lca_research_kernel.model_gateway import (
            DeterministicFakeAdapter,
            ModelGateway,
        )
        from lca_research_kernel.product_interface import ProductInterface

        runtime_root = self.root / "reconstructed-interface"

        def interface(observed_at):
            return ProductInterface(
                runtime_root,
                model_gateway=ModelGateway(
                    {
                        "fake": DeterministicFakeAdapter(
                            {"plan": "bounded next LCA step"}
                        )
                    },
                    default_provider="fake",
                ),
                assurance=AssuranceBoundary(),
                clock=lambda: observed_at,
                run_id="reconstructed-stable-run",
            )

        first = interface("2026-07-31T12:00:00Z")
        first.start_research(self.start_request())
        record = first._open_record("research-1")
        frozen_events = record.event_path.read_bytes()

        reconstructed = interface("2026-07-31T12:05:00Z")
        reconstructed.read_status("research-1")

        self.assertEqual(record.event_path.read_bytes(), frozen_events)
        starts = [
            row
            for row in record.read_audit_trail()
            if row["event_type"] == "research.agent.started"
        ]
        self.assertEqual(len(starts), 1)
        self.assertEqual(
            starts[0]["payload"]["observed_at"],
            "2026-07-31T12:00:00Z",
        )

    def test_model_execution_uses_separate_run_trace_and_project_record_keeps_reference(self):
        self.interface.start_research(self.start_request())
        record = self.interface._open_record("research-1")
        view = record.read_current_project_view()
        source = view["facts"]["model-analysis"]["source"]
        self.assertEqual(source["authority"], "model_candidate_only")
        trace_ref = source["run_trace_ref"]
        self.assertEqual(
            trace_ref["contract"], "lca_research_kernel.run_trace_event_ref/v1"
        )
        trace_path = (
            record.workspace
            / "run-traces"
            / "product-interface-test-runtime.jsonl"
        )
        self.assertTrue(trace_path.is_file())
        trace_text = trace_path.read_text(encoding="utf-8")
        self.assertIn("research.run.model.completed", trace_text)
        project_text = record.event_path.read_text(encoding="utf-8")
        self.assertNotIn("Return only the requested structured candidate", project_text)

    def test_tampered_injected_startup_context_is_rejected_at_construction(self):
        from lca_research_kernel.agent_bootstrap import AgentBootstrap
        from lca_research_kernel.assurance import AssuranceBoundary
        from lca_research_kernel.model_gateway import (
            DeterministicFakeAdapter,
            ModelGateway,
        )
        from lca_research_kernel.product_interface import ProductInterface

        startup = AgentBootstrap.load(
            ROOT,
            ROOT
            / "lca_research_kernel/contracts/agent-startup-manifest-v2.json",
        )
        startup["product_id"] = "generic-chatbot"
        with self.assertRaisesRegex(ValueError, "startup context drifted"):
            ProductInterface(
                self.root / "tampered",
                model_gateway=ModelGateway(
                    {"fake": DeterministicFakeAdapter({"plan": "none"})},
                    default_provider="fake",
                ),
                assurance=AssuranceBoundary(),
                clock=lambda: "2026-07-23T06:01:00Z",
                startup_context=startup,
                run_id="tampered-runtime",
            )

    def test_self_consistent_codrifted_startup_context_cannot_bypass_adopted_root(self):
        from lca_research_kernel.agent_bootstrap import (
            AgentBootstrap,
            canonical_sha256,
        )
        from lca_research_kernel.assurance import AssuranceBoundary
        from lca_research_kernel.model_gateway import (
            DeterministicFakeAdapter,
            ModelGateway,
        )
        from lca_research_kernel.product_interface import ProductInterface

        startup = AgentBootstrap.load(
            ROOT,
            ROOT
            / "lca_research_kernel/contracts/agent-startup-manifest-v2.json",
        )
        startup["manifest_sha256"] = "0" * 64
        startup.pop("context_sha256")
        startup["context_sha256"] = canonical_sha256(startup)

        with self.assertRaisesRegex(ValueError, "adopted startup manifest"):
            ProductInterface(
                self.root / "codrifted",
                model_gateway=ModelGateway(
                    {"fake": DeterministicFakeAdapter({"plan": "none"})},
                    default_provider="fake",
                ),
                assurance=AssuranceBoundary(),
                clock=lambda: "2026-07-23T06:01:00Z",
                startup_context=startup,
                run_id="codrifted-runtime",
            )

    def test_static_material_gap_is_resolved_by_the_internal_ladder(self):
        self.interface.start_research(self.start_request())
        gap_input = {
            "contract": "lca_research_kernel.research_material_or_decision/v1",
            "input_id": "gap-1",
            "kind": "material",
            "payload": {
                "contract": "lca_research_kernel.material_gap/v1",
                "gap_id": "provider-amount",
                "required_role": "provider_amount",
                "decision_relevance": "hotspot ranking",
                "user_data_status": "not_available",
                "authority_candidates": [],
                "proxy_candidates": [],
                "parameter_range": None,
                "bounded_exclusion": {
                    "reason": "No source-bound Provider amount is available.",
                    "affected_claims": ["hotspot_ranking"],
                    "retained_claims": ["explicit_gap_description"],
                },
            },
            "source_refs": [
                {"path": "inputs/gap.json", "sha256": "d" * 64}
            ],
            "received_at": "2026-07-23T06:04:00Z",
        }
        status = self.interface.supply_material_or_decision("research-1", gap_input)
        self.assertEqual(status["boundary"], "awaiting_domain_and_capability_integration")
        view = self.interface._open_record("research-1").read_current_project_view()
        resolution = view["facts"]["material-gap-resolution:provider-amount"]
        self.assertEqual(resolution["value"]["selected_rung"], "bounded_exclusion")
        self.assertEqual(resolution["value"]["claim_level"], "bounded_no_conclusion")

    def test_unknown_data_availability_pauses_as_fact_question_not_authorization(self):
        self.interface.start_research(self.start_request())
        gap_input = {
            "contract": "lca_research_kernel.research_material_or_decision/v1",
            "input_id": "gap-unknown",
            "kind": "material",
            "payload": {
                "contract": "lca_research_kernel.material_gap/v1",
                "gap_id": "provider-amount",
                "required_role": "provider_amount",
                "decision_relevance": "hotspot ranking",
                "user_data_status": "unknown",
                "authority_candidates": [],
                "proxy_candidates": [],
                "parameter_range": None,
                "bounded_exclusion": None,
            },
            "source_refs": [
                {"path": "inputs/gap.json", "sha256": "d" * 64}
            ],
            "received_at": "2026-07-23T06:04:00Z",
        }
        status = self.interface.supply_material_or_decision("research-1", gap_input)
        self.assertEqual(status["boundary"], "awaiting_user_data")
        self.assertEqual(status["interaction_kind"], "factual_data_availability")
        self.assertFalse(status["authorization_required"])
        view = self.interface._open_record("research-1").read_current_project_view()
        self.assertNotIn("material-gap-resolution:provider-amount", view["facts"])

    def test_caller_cannot_select_internal_route_or_capability(self):
        request = self.start_request()
        request["next_route"] = "caller-selected"
        with self.assertRaisesRegex(ValueError, "fields"):
            self.interface.start_research(request)
        with self.assertRaisesRegex(ValueError, "research_id"):
            self.interface.read_status("../escape")

    def test_model_failure_is_a_public_bounded_status_not_an_exception(self):
        from lca_research_kernel.assurance import AssuranceBoundary
        from lca_research_kernel.model_gateway import (
            DeterministicFakeAdapter,
            ModelGateway,
        )
        from lca_research_kernel.product_interface import ProductInterface

        failing = ProductInterface(
            self.root / "failing",
            model_gateway=ModelGateway(
                {"fake": DeterministicFakeAdapter(RuntimeError("offline"))},
                default_provider="fake",
            ),
            assurance=AssuranceBoundary(),
            clock=lambda: "2026-07-23T06:01:00Z",
        )
        status = failing.start_research(self.start_request())
        self.assertEqual(status["boundary"], "model_failure")
        self.assertEqual(status["failure_code"], "provider_failure")
        self.assertTrue(status["retryable"])
        self.assertEqual(status["executed_external_effects"], 0)
        self.assertNotIn("next_route", status)
        self.assertEqual(
            status["recovery_route"],
            "retry-or-switch-model-provider",
        )

    def test_product_path_lets_orchestrator_select_capability_without_caller_routing(self):
        from lca_research_kernel.assurance import AssuranceBoundary
        from lca_research_kernel.capability_gateway import (
            CapabilityGateway,
            LocalDeterministicCapability,
        )
        from lca_research_kernel.model_gateway import (
            DeterministicFakeAdapter,
            ModelGateway,
        )
        from lca_research_kernel.product_interface import ProductInterface

        calls = []
        model = ModelGateway(
            {
                "fake": DeterministicFakeAdapter(
                    {
                        "capability_intent": {
                            "contract": "lca_research_kernel.capability_intent/v1",
                            "purpose": "Inspect accepted local source bytes.",
                            "required_authority": "local_deterministic_execution",
                            "required_access": "local_only",
                            "effect": "local_read",
                            "input": {"source_ref": "inputs/brief.json"},
                        }
                    }
                )
            },
            default_provider="fake",
        )
        product = ProductInterface(
            self.root / "capability-product",
            model_gateway=model,
            capability_gateway=CapabilityGateway(
                {
                    "local.inspect": LocalDeterministicCapability(
                        "local.inspect",
                        lambda payload: calls.append(payload) or {"valid": True},
                        input_schema={
                            "type": "object",
                            "properties": {"source_ref": {"type": "string"}},
                            "required": ["source_ref"],
                            "additionalProperties": False,
                        },
                    )
                }
            ),
            assurance=AssuranceBoundary(),
            clock=lambda: "2026-07-23T06:01:00Z",
        )
        request = self.start_request()
        self.assertFalse({"capability_id", "next_route"} & set(request))
        status = product.start_research(request)
        self.assertEqual(status["boundary"], "awaiting_domain_integration")
        view = product._open_record("research-1").read_current_project_view()
        results = [
            fact
            for fact in view["facts"].values()
            if fact.get("domain_kind") == "capability_result"
        ]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["classification"], "candidate")
        self.assertEqual(calls, [{"source_ref": "inputs/brief.json"}])

    def test_product_interface_has_no_case_or_council_dependency(self):
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
