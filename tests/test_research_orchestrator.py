import ast
import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from lca_research_kernel.project_record import canonical_sha256


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "lca_research_kernel/research_orchestrator.py"


class AcceptingAssurance:
    def evaluate_candidate_change(self, candidate, project_view):
        return {
            "contract": "lca_research_kernel.assurance_acceptance/v1",
            "decision": "accepted",
            "candidate_sha256": canonical_sha256(candidate),
            "evaluator": "assurance_boundary",
            "evaluated_at": candidate["proposed_at"],
        }


class RejectingAssurance:
    def evaluate_candidate_change(self, candidate, project_view):
        return {
            "contract": "lca_research_kernel.assurance_acceptance/v1",
            "decision": "rejected",
            "candidate_sha256": canonical_sha256(candidate),
            "evaluator": "assurance_boundary",
            "evaluated_at": candidate["proposed_at"],
        }


class ResearchOrchestratorTests(unittest.TestCase):
    def setUp(self):
        from lca_research_kernel.model_gateway import (
            DeterministicFakeAdapter,
            ModelGateway,
        )
        from lca_research_kernel.project_record import ProjectRecord
        from lca_research_kernel.research_orchestrator import ResearchOrchestrator

        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name) / "record"
        self.record = ProjectRecord.open_or_create_project(
            self.workspace,
            project_id="generic-research-1",
            title="Generic research",
            objective="Exercise the D-18 single control spine.",
            created_at="2026-07-23T04:00:00Z",
        )
        self.gateway = ModelGateway(
            {"fake": DeterministicFakeAdapter({"interpretation": "bounded candidate"})},
            default_provider="fake",
        )
        self.orchestrator = ResearchOrchestrator(
            self.record,
            model_gateway=self.gateway,
            assurance=AcceptingAssurance(),
            clock=lambda: "2026-07-23T04:01:00Z",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def intake(self):
        return {
            "contract": "lca_research_kernel.research_intake/v1",
            "intake_id": "intake-1",
            "research_goal": "Assess one source-bound LCA research question.",
            "source_refs": [
                {"path": "inputs/research-brief.json", "sha256": "a" * 64}
            ],
            "received_at": "2026-07-23T04:01:00Z",
        }

    def test_constructor_binds_timing_and_native_trace_to_capability_gateway(self):
        from lca_research_kernel.research_orchestrator import ResearchOrchestrator
        from lca_research_kernel.run_trace import RunTrace

        class Timing:
            @staticmethod
            def start_activity():
                return 0.0

            @staticmethod
            def record_activity_since(*args, **kwargs):
                return None

            @staticmethod
            def remaining_seconds():
                return 60.0

        class RecordingCapabilityGateway:
            catalog = object()

            def __init__(self):
                self.timing = None
                self.trace = None
                self.clock = None

            def with_answer_timing(self, timing):
                self.timing = timing
                return self

            def with_native_evidence_trace(self, trace, *, clock):
                self.trace = trace
                self.clock = clock
                return self

        trace = RunTrace(
            Path(self.temporary.name) / "constructor-bindings.jsonl",
            run_id="constructor-bindings",
        )
        capability_gateway = RecordingCapabilityGateway()
        timing = Timing()

        orchestrator = ResearchOrchestrator(
            self.record,
            model_gateway=self.gateway,
            capability_gateway=capability_gateway,
            assurance=AcceptingAssurance(),
            clock=lambda: "2026-07-23T04:01:00Z",
            run_trace=trace,
            answer_timing=timing,
        )

        self.assertIs(orchestrator.capability_gateway, capability_gateway)
        self.assertIs(capability_gateway.timing, timing)
        self.assertIs(capability_gateway.trace, trace)
        self.assertTrue(callable(capability_gateway.clock))

    def test_adaptive_plan_is_rechecked_at_the_runtime_side_effect_edge(self):
        view = self.record.read_current_project_view()
        prepared = {
            "adaptive_answer_plan": {
                "project_state_sha256": view["last_event_sha256"],
                "plan_sha256": "a" * 64,
            }
        }
        guard = self.orchestrator._material_execution_guard(prepared)
        self.assertIsNotNone(guard)
        self.orchestrator.submit_candidate_change(
            {
                "contract": "lca_research_kernel.project_candidate_change/v1",
                "candidate_id": "state-change-before-adapter",
                "change_type": "route.updated",
                "next_route": "changed-before-adapter",
                "proposed_at": "2026-07-23T04:01:00Z",
            },
            idempotency_key="state-change-before-adapter",
        )

        with self.assertRaisesRegex(ValueError, "execution basis drifted"):
            guard(prepared, {"route_id": "fake", "attempt": 1})

    @staticmethod
    def _activate_typed_material_requirement(product, record, project_id):
        request = {
            "contract": "lca_research_kernel.research_start/v1",
            "research_id": project_id,
            "title": "Generic material requirement",
            "objective": "Assess the accepted project requirement.",
            "source_refs": [
                {"path": "intake/request.json", "sha256": "9" * 64}
            ],
            "received_at": "2026-07-23T04:01:00Z",
        }
        product._ensure_material_active_turn(record, request)
        candidate = {
            "contract": "lca_research_kernel.project_candidate_change/v1",
            "candidate_id": "missing-functional-unit-requirement",
            "change_type": "fact.upserted",
            "fact": {
                "fact_id": "functional-unit-requirement",
                "classification": "candidate",
                "domain_kind": "research_requirement",
                "semantic_role": "functional_unit",
                "value": {"reason": "required for the current state change"},
                "source": {
                    "authority": "accepted_project_truth",
                    "version": "requirement-v1",
                    "content_sha256": "8" * 64,
                },
            },
            "next_route": "resolve-functional-unit-requirement",
            "proposed_at": "2026-07-23T04:01:00Z",
        }
        acceptance = product.assurance.evaluate_candidate_change(
            candidate, record.read_current_project_view()
        )
        record.append_accepted_change(
            candidate,
            acceptance,
            idempotency_key="missing-functional-unit-requirement",
        )

    def test_run_and_resume_reach_real_boundaries_without_caller_routing(self):
        initial = self.orchestrator.run_until_boundary("generic-research-1")
        self.assertEqual(initial["boundary"], "awaiting_research_intake")
        self.assertEqual(len(self.record.read_audit_trail()), 1)

        result = self.orchestrator.resume("generic-research-1", self.intake())
        self.assertEqual(result["boundary"], "awaiting_domain_and_capability_integration")
        view = self.record.read_current_project_view()
        self.assertEqual(view["facts"]["research-goal"]["classification"], "official")
        analysis = view["facts"]["model-analysis"]
        self.assertEqual(analysis["classification"], "candidate")
        self.assertEqual(analysis["value"], {"interpretation": "bounded candidate"})
        self.assertEqual(len(self.record.read_audit_trail()), 3)
        self.assertEqual(
            self.orchestrator.run_until_boundary("generic-research-1"), result
        )
        self.assertEqual(len(self.record.read_audit_trail()), 3)

    def test_tiangong_model_discovery_routes_to_exact_catalog_capability_before_lca_gaps(self):
        """A discovery question must select TianGong search, not ask for FU/scope."""

        from lca_research_kernel.capability_catalog import CapabilityCatalog
        from lca_research_kernel.capability_gateway import CapabilityGateway
        from lca_research_kernel.model_gateway import (
            DeterministicFakeAdapter,
            ModelGateway,
        )
        from lca_research_kernel.research_orchestrator import ResearchOrchestrator

        catalog = CapabilityCatalog.from_files(
            ROOT / "lca_research_kernel/contracts/tiangong-capability-catalog.json",
            ROOT / "lca_research_kernel/contracts/tiangong-source-lock.json",
        )
        model = ModelGateway(
            {
                "fake": DeterministicFakeAdapter(
                    {
                        "answer": "需要先查询 TianGong 当前公开模型。",
                        "values": [],
                        "official_object_ids": [],
                        "scientific_source_sha256s": [],
                        "limitations": ["查询能力需要可用的 TianGong 只读运行时。"],
                        "missing_fields": [],
                        "capability_intent": {
                            "contract": (
                                "lca_research_kernel.tiangong_native_intent/v1"
                            ),
                            "purpose": "查找当前公开的动力电池回收生命周期模型。",
                            "requested_state_change": "discover_lifecycle_models",
                        },
                    }
                )
            },
            default_provider="fake",
        )
        orchestrator = ResearchOrchestrator(
            self.record,
            model_gateway=model,
            capability_gateway=CapabilityGateway(catalog),
            assurance=AcceptingAssurance(),
            clock=lambda: "2026-07-23T04:01:00Z",
        )
        intake = deepcopy(self.intake())
        intake["research_goal"] = (
            "帮我从 TianGong 找出当前公开、适合动力电池回收建模的生命周期模型。"
        )

        result = orchestrator.resume("generic-research-1", intake)

        self.assertEqual(result["boundary"], "capability_unavailable")
        self.assertEqual(result["executed_external_effects"], 0)
        view = self.record.read_current_project_view()
        intent = view["facts"]["model-analysis"]["value"]["capability_intent"]
        self.assertEqual(
            intent["requested_state_change"], "discover_lifecycle_models"
        )
        self.assertFalse(
            any(
                fact.get("domain_kind") == "material_gap"
                for fact in view["facts"].values()
                if isinstance(fact, dict)
            )
        )
    def test_assurance_rejection_cannot_write_or_advance_project_truth(self):
        from lca_research_kernel.research_orchestrator import ResearchOrchestrator

        rejecting = ResearchOrchestrator(
            self.record,
            model_gateway=self.gateway,
            assurance=RejectingAssurance(),
            clock=lambda: "2026-07-23T04:01:00Z",
        )
        before = self.record.event_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "did not accept"):
            rejecting.resume("generic-research-1", self.intake())
        self.assertEqual(self.record.event_path.read_bytes(), before)

    def test_model_gateway_receives_a_narrow_remaining_budget_port(self):
        from lca_research_kernel.research_orchestrator import ResearchOrchestrator
        from lca_research_kernel.run_control import RunController

        class BudgetAwareGateway:
            def __init__(self):
                self.remaining_budget = None

            def with_remaining_budget(self, callback):
                self.remaining_budget = callback
                return self

        gateway = BudgetAwareGateway()
        controller = RunController(
            {
                "contract": "lca_research_kernel.run_budget/v1",
                "max_elapsed_ms": 10000,
                "max_steps": 4,
                "max_model_calls": 2,
                "max_tool_calls": 2,
                "max_context_tokens": 1000,
                "max_repeated_fingerprint": 2,
            },
            run_id="budget-port-run",
            monotonic=lambda: 0.0,
        )

        ResearchOrchestrator(
            self.record,
            model_gateway=gateway,
            assurance=AcceptingAssurance(),
            clock=lambda: "2026-07-23T04:01:00Z",
            run_controller=controller,
        )

        self.assertIsNotNone(gateway.remaining_budget)
        self.assertEqual(gateway.remaining_budget(), 10.0)

    def test_every_provider_attempt_is_guarded_by_the_same_run_control_policy(self):
        from lca_research_kernel.research_orchestrator import ResearchOrchestrator
        from lca_research_kernel.run_control import RunBudgetExceeded, RunController
        from lca_research_kernel.run_trace import RunTrace

        trace = RunTrace(
            Path(self.temporary.name) / "attempt-guard.jsonl",
            run_id="attempt-guard-run",
        )
        controller = RunController(
            {
                "contract": "lca_research_kernel.run_control_policy/v2",
                "max_elapsed_ms": 10000,
                "max_cpu_ms": 5000,
                "max_steps": 4,
                "max_model_calls": 1,
                "max_tool_calls": 2,
                "max_events": 20,
                "max_context_tokens": 1000,
                "max_repeated_fingerprint": 2,
                "heartbeat_interval_ms": 1000,
                "heartbeat_timeout_ms": 3000,
            },
            run_id="attempt-guard-run",
            monotonic=lambda: 0.0,
            process_time=lambda: 0.0,
        )
        orchestrator = ResearchOrchestrator(
            self.record,
            model_gateway=self.gateway,
            assurance=AcceptingAssurance(),
            clock=lambda: "2026-07-23T04:01:00Z",
            run_trace=trace,
            run_controller=controller,
        )
        request = {
            "contract": "lca_research_kernel.model_request/v1",
            "request_id": "attempt-guard-request",
        }
        guard = orchestrator._material_execution_guard(request)
        self.assertTrue(callable(guard))
        trace.append(
            "research.run.model.requested",
            {"request_id": request["request_id"], "attempt": 1},
            idempotency_key="attempt-1",
            occurred_at="2026-07-23T04:01:00Z",
        )
        guard(request, {"route_id": "fake", "attempt": 1})
        self.assertTrue(
            any(
                event["event_type"] == "research.run.heartbeat"
                for event in trace.events()
            )
        )
        trace.append(
            "research.run.model.requested",
            {"request_id": request["request_id"], "attempt": 2},
            idempotency_key="attempt-2",
            occurred_at="2026-07-23T04:01:01Z",
        )
        with self.assertRaisesRegex(RunBudgetExceeded, "model call"):
            guard(request, {"route_id": "fake", "attempt": 2})

    def test_selection_uses_project_truth_and_not_case_or_council_state(self):
        selection = self.orchestrator.select_next_action(
            self.record.read_current_project_view(),
            domain_policy={"claim_ceiling": "candidate_only"},
            available_capabilities={"model": ["fake"]},
        )
        self.assertEqual(selection["action"], "boundary")
        self.assertEqual(selection["boundary"], "awaiting_research_intake")
        frame = selection["decision_frame"]
        self.assertEqual(
            frame["contract"],
            "lca_research_kernel.architecture_decision_frame/v1",
        )
        self.assertEqual(frame["selected_modules"], ["product_interface"])
        self.assertEqual(
            frame["selection_basis"], "state_transition_and_responsibility"
        )
        self.assertEqual(
            frame["expected_state_change"]["from_state"], "project_created"
        )
        self.assertEqual(
            frame["expected_state_change"]["to_state"],
            "research_intake_accepted",
        )
        with self.assertRaisesRegex(ValueError, "research_id"):
            self.orchestrator.run_until_boundary("another-project")

    def test_semantic_runtime_replaces_broad_material_and_empty_method_selection(self):
        from lca_research_kernel.research_orchestrator import ResearchOrchestrator
        from lca_research_kernel.semantic_context import ResearchSemanticContext

        semantic_context = ResearchSemanticContext.from_files(
            ROOT,
            source_lock_path=ROOT / "lca_research_kernel/contracts/tiangong-source-lock.json",
            capability_catalog_path=ROOT / "lca_research_kernel/contracts/tiangong-capability-catalog.json",
            skill_registry_path=ROOT / "lca_research_kernel/contracts/research-skill-registry.json",
        )
        orchestrator = ResearchOrchestrator(
            self.record,
            model_gateway=self.gateway,
            assurance=AcceptingAssurance(),
            clock=lambda: "2026-07-23T04:01:00Z",
            semantic_context=semantic_context,
        )

        def accept_fact(fact_id, classification, domain_kind, value, source):
            candidate = {
                "contract": "lca_research_kernel.project_candidate_change/v1",
                "candidate_id": "candidate-" + fact_id,
                "change_type": "fact.upserted",
                "fact": {
                    "fact_id": fact_id,
                    "classification": classification,
                    "domain_kind": domain_kind,
                    "value": value,
                    "source": source,
                },
                "next_route": "reason-from-source-bound-material",
                "proposed_at": "2026-07-23T04:01:00Z",
            }
            orchestrator.submit_candidate_change(
                candidate, idempotency_key="semantic-test:" + fact_id
            )

        accept_fact(
            "research-goal",
            "official",
            "research_goal",
            "Audit one source-bound LCA statement.",
            {
                "authority": "user_supplied_project_input",
                "version": "brief-v1",
                "content_sha256": "1" * 64,
            },
        )
        accept_fact(
            "research-source-material",
            "candidate",
            "source_bound_research_material",
            {
                "contract": "lca_research_kernel.source_bound_research_material/v1",
                "materials": [
                    {
                        "path": "inputs/source.json",
                        "sha256": "2" * 64,
                        "authority": "TianGong",
                        "content": {"statement": "source-bound statement"},
                    }
                ],
            },
            {
                "authority": "user_supplied_project_input",
                "version": "material-v1",
                "content_sha256": "2" * 64,
            },
        )
        accept_fact(
            "irrelevant-source-material",
            "official",
            "official_source_material",
            {"statement": "unrelated market statistic"},
            {
                "authority": "TianGong",
                "version": "unrelated-v1",
                "content_sha256": "3" * 64,
            },
        )

        view = self.record.read_current_project_view()
        action = orchestrator.select_next_action(
            view,
            domain_policy={"claim_ceiling": "candidate_only"},
            available_capabilities={"models": ["fake"], "capabilities": []},
        )

        self.assertEqual(action["action"], "model.reason")
        self.assertEqual(
            action["semantic_context_plan"]["selected_skill_ids"],
            ["lca-evidence-and-conclusion-audit"],
        )
        self.assertEqual(
            action["interaction_route"]["selected_method_ids"],
            ["lca-evidence-and-conclusion-audit"],
        )
        self.assertEqual(
            action["decision_frame"]["current_problem"]["context_selectors"][
                "fact_ids"
            ],
            ["research-goal", "research-source-material"],
        )
        request = orchestrator._model_request(
            view,
            decision_frame=action["decision_frame"],
            semantic_plan=action["semantic_context_plan"],
        )
        identities = {row["identity"] for row in request["context"]["items"]}
        self.assertIn("project_fact:research-source-material", identities)
        self.assertIn("research_skill:lca-evidence-and-conclusion-audit", identities)
        self.assertNotIn("project_fact:irrelevant-source-material", identities)

    def test_problem_signature_uses_typed_project_state_not_objective_keywords(self):
        from lca_research_kernel.semantic_context import ResearchSemanticContext

        view = self.record.read_current_project_view()
        view["objective"] = "What is the functional unit and system boundary?"
        frame = self.orchestrator._architecture_frame(
            view, {"action": "model.reason"}
        )

        wording_only = self.orchestrator._semantic_expected_state_change(
            view, frame
        )
        self.assertNotIn(
            "functional_unit", wording_only["required_evidence_roles"]
        )
        self.assertNotIn(
            "system_boundary", wording_only["required_evidence_roles"]
        )

        typed = deepcopy(view)
        typed["objective"] = "Assess the accepted project requirement."
        typed["facts"]["functional-unit-gap"] = {
            "fact_id": "functional-unit-gap",
            "classification": "missing",
            "semantic_role": "functional_unit",
            "value": {"reason": "required for the current state change"},
            "source": {"authority": "accepted_project_truth"},
        }
        state_driven = self.orchestrator._semantic_expected_state_change(
            typed, frame
        )
        self.assertIn(
            "functional_unit", state_driven["required_evidence_roles"]
        )

        typed["facts"]["functional-unit-gap"] = {
            **typed["facts"]["functional-unit-gap"],
            "classification": "official",
            "value": {"amount": 1, "unit": "kg"},
        }
        resolved = self.orchestrator._semantic_expected_state_change(typed, frame)
        self.assertIn("functional_unit", resolved["required_evidence_roles"])
        semantic_context = ResearchSemanticContext.from_files(
            ROOT,
            source_lock_path=ROOT
            / "lca_research_kernel/contracts/tiangong-source-lock.json",
            capability_catalog_path=ROOT
            / "lca_research_kernel/contracts/tiangong-capability-catalog.json",
            skill_registry_path=ROOT
            / "lca_research_kernel/contracts/research-skill-registry.json",
        )
        plan = semantic_context.plan(
            project_view=typed,
            expected_state_change=resolved,
            available_capabilities=[],
            context_budget=12000,
            work_budget={
                "max_workers": 1,
                "max_model_calls": 1,
                "max_tool_calls": 0,
            },
            generation=7,
        )
        self.assertEqual(
            plan["next_route"], "advance-after-required-evidence-accepted"
        )
        self.assertEqual(plan["stop_condition"], "expected_state_change_completed")

        typed["facts"]["functional-unit-conflict"] = {
            "fact_id": "functional-unit-conflict",
            "classification": "official",
            "semantic_role": "functional_unit",
            "value": {"amount": 1, "unit": "item"},
            "source": {"authority": "accepted_project_truth"},
        }
        conflicted = self.orchestrator._semantic_expected_state_change(typed, frame)
        conflict_plan = semantic_context.plan(
            project_view=typed,
            expected_state_change=conflicted,
            available_capabilities=[],
            context_budget=12000,
            work_budget={
                "max_workers": 1,
                "max_model_calls": 1,
                "max_tool_calls": 0,
            },
            generation=8,
        )
        self.assertIn("functional_unit", conflict_plan["problem_signature"]["missing_roles"])
        self.assertNotEqual(
            conflict_plan["next_route"], "advance-after-required-evidence-accepted"
        )
        self.assertNotEqual(
            conflict_plan["stop_condition"], "expected_state_change_completed"
        )

    def test_typed_skill_resource_requirement_drives_real_l3_disclosure(self):
        from lca_research_kernel.semantic_context import ResearchSemanticContext

        view = self.record.read_current_project_view()
        frame = self.orchestrator._architecture_frame(
            view, {"action": "model.reason"}
        )
        view["facts"]["provider-gap"] = {
            "fact_id": "provider-gap",
            "classification": "missing",
            "semantic_role": "provider_relation",
            "value": {"reason": "provider closure is required"},
            "source": {"authority": "accepted_project_truth"},
        }
        view["facts"]["provider-resource-requirement"] = {
            "fact_id": "provider-resource-requirement",
            "classification": "official",
            "domain_kind": "research_skill_resource_requirement",
            "value": {
                "contract": (
                    "lca_research_kernel.research_skill_resource_requirement/v1"
                ),
                "skill_id": "lci-provider-linkage-and-completeness",
                "resource_ids": ["provider-relationship-evidence-checklist"],
            },
            "source": {"authority": "accepted_project_truth"},
        }

        expected = self.orchestrator._semantic_expected_state_change(view, frame)

        self.assertEqual(
            expected["required_skill_resources"],
            {
                "lci-provider-linkage-and-completeness": [
                    "provider-relationship-evidence-checklist"
                ]
            },
        )
        semantic_context = ResearchSemanticContext.from_files(
            ROOT,
            source_lock_path=ROOT
            / "lca_research_kernel/contracts/tiangong-source-lock.json",
            capability_catalog_path=ROOT
            / "lca_research_kernel/contracts/tiangong-capability-catalog.json",
            skill_registry_path=ROOT
            / "lca_research_kernel/contracts/research-skill-registry.json",
        )
        plan = semantic_context.plan(
            project_view=view,
            expected_state_change=expected,
            available_capabilities=[],
            context_budget=12000,
            work_budget={
                "max_workers": 1,
                "max_model_calls": 1,
                "max_tool_calls": 0,
            },
            generation=7,
        )
        disclosure = plan["skill_disclosures"][0]
        self.assertEqual(disclosure["level"], "L3")
        self.assertEqual(
            [row["resource_id"] for row in disclosure["resource_contents"]],
            ["provider-relationship-evidence-checklist"],
        )
        self.assertTrue(disclosure["resource_contents"][0]["content"].strip())

    def test_generic_model_route_accepts_and_consumes_only_selected_material_evidence(self):
        """Bypassing the material-use gate or selected-only assembly must fail here."""

        from lca_research_kernel.assurance import AssuranceBoundary
        from lca_research_kernel.product_interface import ProductInterface
        from lca_research_kernel.project_record import ProjectRecord
        from lca_research_kernel.research_orchestrator import ResearchOrchestrator
        from lca_research_kernel.run_trace import RunTrace
        from lca_research_kernel.semantic_context import ResearchSemanticContext
        from tests.test_product_material_acceptance import (
            ProductMaterialAcceptanceTests,
            material_bundle,
        )

        bundle = material_bundle()
        bundle["evidence_unit_bindings"][0]["evidence_unit"]["semantic_roles"] = [
            "functional_unit"
        ]
        bundle["evidence_unit_bindings"][0]["evidence_unit"]["content"] = (
            "The functional unit is one tonne."
        )
        selected_hash = hashlib.sha256(
            json.dumps(
                bundle["evidence_unit_bindings"][0]["evidence_unit"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            + b"\n"
        ).hexdigest()
        bundle["evidence_unit_bindings"][0]["content_hash"] = selected_hash
        bundle["locators"][0]["content_hash"] = selected_hash
        excluded = deepcopy(bundle["locators"][0])
        excluded["locator_id"] = "locator-system-boundary"
        excluded["locator"]["page"] = 2
        excluded_binding = deepcopy(bundle["evidence_unit_bindings"][0])
        excluded_binding["locator_id"] = excluded["locator_id"]
        excluded_binding["source_span"]["locator"]["page"] = 2
        excluded_binding["evidence_unit"] = {
            "kind": "document_text",
            "content": "The system boundary is cradle-to-gate.",
            "semantic_roles": ["system_boundary"],
            "candidate_only": True,
            "source_span": deepcopy(excluded_binding["source_span"]),
        }
        excluded_hash = hashlib.sha256(
            json.dumps(
                excluded_binding["evidence_unit"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            + b"\n"
        ).hexdigest()
        excluded_binding["content_hash"] = excluded_hash
        excluded["content_hash"] = excluded_hash
        bundle["locators"].append(excluded)
        bundle["evidence_unit_bindings"].append(excluded_binding)
        evidence_envelope = {
            "contract": "lca_research_service.evidence_units/v1",
            "revision_id": bundle["material_revision"]["revision_id"],
            "input_sha256": bundle["material_revision"]["artifact_ref"]["sha256"],
            "detected_type": deepcopy(bundle["processing_receipt"]["detected_type"]),
            "units": [
                deepcopy(binding["evidence_unit"])
                for binding in bundle["evidence_unit_bindings"]
            ],
            "candidate_only": True,
        }
        evidence_sha = hashlib.sha256(
            json.dumps(
                evidence_envelope,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            + b"\n"
        ).hexdigest()
        evidence_ref = {"artifact_id": evidence_sha, "sha256": evidence_sha}
        bundle["evidence_units_ref"] = evidence_ref
        bundle["material_revision"]["extraction_refs"] = [evidence_ref]
        bundle["processing_receipt"]["output_refs"] = [evidence_ref]
        receipt_sha = hashlib.sha256(
            json.dumps(
                bundle["processing_receipt"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            + b"\n"
        ).hexdigest()
        bundle["processing_receipt_ref"] = {
            "artifact_id": receipt_sha,
            "sha256": receipt_sha,
        }

        semantic_context = ResearchSemanticContext.from_files(
            ROOT,
            source_lock_path=ROOT / "lca_research_kernel/contracts/tiangong-source-lock.json",
            capability_catalog_path=ROOT / "lca_research_kernel/contracts/tiangong-capability-catalog.json",
            skill_registry_path=ROOT / "lca_research_kernel/contracts/research-skill-registry.json",
        )
        resolver = ProductMaterialAcceptanceTests._material_resolver([bundle])
        product = ProductInterface(
            self.workspace,
            model_gateway=self.gateway,
            assurance=AssuranceBoundary(),
            clock=lambda: "2026-07-23T04:01:00Z",
            run_id="generic-material-setup",
            verified_content_resolver=resolver,
        )
        record = ProjectRecord.open_or_create_project(
            self.workspace / "material-route",
            project_id="generic-material-route",
            title="Generic material route",
            objective="What is the functional unit?",
            created_at="2026-07-23T04:00:00Z",
            verified_content_resolver=resolver,
        )
        bundle["research_material"]["project_id"] = "generic-material-route"
        # The resolver fixture checks the original project identity, so expose
        # the same exact content under this test's accepted project identity.
        base_resolver = resolver
        product._verified_content_resolver = lambda _project_id, kind, ref: base_resolver(
            "material-project", kind, ref
        )
        record._verified_content_resolver = product._verified_content_resolver
        self._activate_typed_material_requirement(
            product, record, "generic-material-route"
        )
        product._accept_material_inputs(
            record,
            bundles=[bundle],
            proposed_at="2026-07-23T04:01:00Z",
        )
        trace = RunTrace(
            self.workspace / "generic-material-route.jsonl",
            run_id="generic-material-route",
        )
        captured_requests = []
        adapter = self.gateway.adapters["fake"]
        adapter.response = {
            "answer": "The selected material states a one-tonne functional unit.",
            "values": [],
            "official_object_ids": [],
            "scientific_source_sha256s": [],
            "limitations": ["This remains a candidate source-bound explanation."],
            "capability_intent": None,
        }
        original_reason = adapter.reason

        def capture_request(request):
            captured_requests.append(deepcopy(dict(request)))
            return original_reason(request)

        adapter.reason = capture_request
        orchestrator = ResearchOrchestrator(
            record,
            model_gateway=self.gateway,
            assurance=AssuranceBoundary(),
            clock=lambda: "2026-07-23T04:01:00Z",
            semantic_context=semantic_context,
            run_trace=trace,
        )
        orchestrator.submit_candidate_change(
            {
                "contract": "lca_research_kernel.project_candidate_change/v1",
                "candidate_id": "candidate-research-goal",
                "change_type": "fact.upserted",
                "fact": {
                    "fact_id": "research-goal",
                    "classification": "official",
                    "domain_kind": "research_goal",
                    "value": "What is the functional unit?",
                    "source": {
                        "authority": "user_supplied_project_input",
                        "version": "brief-v1",
                        "content_sha256": "1" * 64,
                    },
                },
                "next_route": "reason-from-selected-material-evidence",
                "proposed_at": "2026-07-23T04:01:00Z",
            },
            idempotency_key="generic-material-route:goal",
        )
        public_action = orchestrator.select_next_action(
            record.read_current_project_view(),
            domain_policy={"claim_ceiling": "candidate_only"},
            available_capabilities={"models": ["fake"], "capabilities": []},
        )
        self.assertNotIn("material_evidence_units", public_action)
        self.assertNotIn("one tonne", json.dumps(public_action, ensure_ascii=False))
        self.assertNotIn(
            "cradle-to-gate", json.dumps(public_action, ensure_ascii=False)
        )
        mutated = False

        def mutate_at_provider_edge(_request, _attempt):
            nonlocal mutated
            if not mutated:
                mutated = True
                accepted = record.read_current_project_view()
                withdrawn_candidate = {
                    "contract": "lca_research_kernel.project_candidate_change/v1",
                    "candidate_id": "withdraw-before-provider",
                    "change_type": "material.withdrawn",
                    "material_id": "material-1",
                    "deleted_at": "2026-07-23T04:01:00Z",
                    "next_route": "hold-material-execution",
                    "proposed_at": "2026-07-23T04:01:00Z",
                }
                acceptance = AssuranceBoundary().evaluate_candidate_change(
                    withdrawn_candidate, accepted
                )
                self.assertEqual(acceptance.get("errors"), [])
                record.append_accepted_change(
                    withdrawn_candidate,
                    acceptance,
                    idempotency_key="withdraw-before-provider",
                )
            orchestrator._assert_material_execution_fresh(_request)

        orchestrator._material_execution_guard = lambda _prepared: (
            mutate_at_provider_edge
        )
        blocked = orchestrator.run_until_boundary("generic-material-route")
        self.assertEqual(blocked["boundary"], "model_failure")
        self.assertEqual(blocked["executed_external_effects"], 0)
        self.assertEqual(captured_requests, [])

        # Restore the accepted in-scope revision and prove the real path executes.
        restored_view = record.read_current_project_view()
        restored_candidate = {
            "contract": "lca_research_kernel.project_candidate_change/v1",
            "candidate_id": "restore-before-provider",
            "change_type": "material.restored",
            "material_id": "material-1",
            "deleted_at": None,
            "next_route": "reason-from-selected-material-evidence",
            "proposed_at": "2026-07-23T04:01:00Z",
        }
        restored_acceptance = AssuranceBoundary().evaluate_candidate_change(
            restored_candidate, restored_view
        )
        self.assertEqual(restored_acceptance.get("errors"), [])
        record.append_accepted_change(
            restored_candidate,
            restored_acceptance,
            idempotency_key="restore-before-provider",
        )
        orchestrator._material_execution_guard = lambda prepared: (
            lambda attempted, _attempt: orchestrator._assert_material_execution_fresh(
                prepared
            )
        )
        result = orchestrator.run_until_boundary("generic-material-route")
        self.assertEqual(
            result["boundary"], "research_result_accepted"
        )

        events = trace.events()
        gate = next(
            event
            for event in events
            if event["event_type"]
            == "research.run.material.use_decision_consumed"
        )
        model_requested = next(
            event
            for event in events
            if event["event_type"] == "research.run.model.requested"
        )
        self.assertLess(gate["sequence"], model_requested["sequence"])
        self.assertEqual(len(captured_requests), 1)
        request = captured_requests[0]

        locator_items = [
            item
            for item in request["context"]["items"]
            if item["identity"].startswith("evidence_locator:")
        ]
        self.assertEqual(
            [item["identity"] for item in locator_items],
            ["evidence_locator:locator-page-1"],
        )
        self.assertIn("one tonne", json.dumps(locator_items, ensure_ascii=False))
        self.assertNotIn("cradle-to-gate", json.dumps(request, ensure_ascii=False))
        self.assertEqual(
            request["decision_frame"]["current_problem"]["context_selectors"][
                "evidence_locator_ids"
            ],
            ["locator-page-1"],
        )
        decision = request["material_use_decision"]
        accepted_view = record.read_current_project_view()
        accepted = accepted_view["material_use_decisions"]
        self.assertEqual(accepted[decision["task_id"]], decision)
        self.assertEqual(
            request["material_use_decision_checkpoint"][
                "accepted_decision_event_sha256"
            ],
            gate["payload"]["project_record_event_sha256"],
        )
        model_context = next(
            event
            for event in events
            if event["event_type"] == "research.run.context.assembled"
        )
        self.assertLess(gate["sequence"], model_context["sequence"])
        self.assertEqual(
            gate["payload"]["project_record_event_sha256"],
            request["material_use_decision_checkpoint"][
                "accepted_decision_event_sha256"
            ],
        )

    def test_generic_model_route_rejects_one_sided_material_inputs(self):
        """Removing either explicit material input must fail before planning."""

        view = self.record.read_current_project_view()
        frame = self.orchestrator._architecture_frame(
            view,
            {"action": "model.reason"},
        )
        for incomplete in (
            {"material_evidence_units": [{"locator_id": "locator-a"}]},
            {"allowed_revision_ids": ["revision-a"]},
        ):
            with self.subTest(incomplete=incomplete):
                with self.assertRaisesRegex(
                    ValueError,
                    "material evidence units and allowed revision ids must be supplied together",
                ):
                    self.orchestrator._model_request(
                        view,
                        decision_frame=frame,
                        **incomplete,
                    )

    def test_material_semantic_plan_drift_fails_before_project_record_write(self):
        """A caller plan cannot choose a different locator and then write it."""

        from lca_research_kernel.assurance import AssuranceBoundary
        from lca_research_kernel.product_interface import ProductInterface
        from lca_research_kernel.project_record import ProjectRecord
        from lca_research_kernel.research_orchestrator import ResearchOrchestrator
        from lca_research_kernel.semantic_context import ResearchSemanticContext
        from tests.test_product_material_acceptance import (
            ProductMaterialAcceptanceTests,
            material_bundle,
        )

        bundle = material_bundle()
        bundle["evidence_unit_bindings"][0]["evidence_unit"][
            "semantic_roles"
        ] = ["functional_unit"]
        selected_hash = hashlib.sha256(
            json.dumps(
                bundle["evidence_unit_bindings"][0]["evidence_unit"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            + b"\n"
        ).hexdigest()
        bundle["evidence_unit_bindings"][0]["content_hash"] = selected_hash
        bundle["locators"][0]["content_hash"] = selected_hash
        evidence_envelope = {
            "contract": "lca_research_service.evidence_units/v1",
            "revision_id": "revision-1",
            "input_sha256": "a" * 64,
            "detected_type": deepcopy(bundle["processing_receipt"]["detected_type"]),
            "units": [deepcopy(bundle["evidence_unit_bindings"][0]["evidence_unit"])],
            "candidate_only": True,
        }
        evidence_sha = hashlib.sha256(
            json.dumps(
                evidence_envelope,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            + b"\n"
        ).hexdigest()
        evidence_ref = {"artifact_id": evidence_sha, "sha256": evidence_sha}
        bundle["evidence_units_ref"] = evidence_ref
        bundle["material_revision"]["extraction_refs"] = [evidence_ref]
        bundle["processing_receipt"]["output_refs"] = [evidence_ref]
        receipt_sha = hashlib.sha256(
            json.dumps(
                bundle["processing_receipt"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            + b"\n"
        ).hexdigest()
        bundle["processing_receipt_ref"] = {
            "artifact_id": receipt_sha,
            "sha256": receipt_sha,
        }
        resolver = ProductMaterialAcceptanceTests._material_resolver([bundle])
        record = ProjectRecord.open_or_create_project(
            self.workspace / "stale-material-plan",
            project_id="material-project",
            title="Material plan",
            objective="What is the functional unit?",
            created_at="2026-07-23T04:00:00Z",
            verified_content_resolver=resolver,
        )
        product = ProductInterface(
            self.workspace,
            model_gateway=self.gateway,
            assurance=AssuranceBoundary(),
            clock=lambda: "2026-07-23T04:01:00Z",
            verified_content_resolver=resolver,
        )
        self._activate_typed_material_requirement(
            product, record, "material-project"
        )
        product._accept_material_inputs(
            record,
            bundles=[bundle],
            proposed_at="2026-07-23T04:01:00Z",
        )
        orchestrator = ResearchOrchestrator(
            record,
            model_gateway=self.gateway,
            assurance=AssuranceBoundary(),
            clock=lambda: "2026-07-23T04:01:00Z",
            semantic_context=ResearchSemanticContext.from_files(
                ROOT,
                source_lock_path=ROOT / "lca_research_kernel/contracts/tiangong-source-lock.json",
                capability_catalog_path=ROOT / "lca_research_kernel/contracts/tiangong-capability-catalog.json",
                skill_registry_path=ROOT / "lca_research_kernel/contracts/research-skill-registry.json",
            ),
        )
        view = record.read_current_project_view()
        frame = orchestrator._architecture_frame(view, {"action": "model.reason"})
        plan = orchestrator._semantic_context_plan(
            view,
            frame,
            [],
            material_evidence_units=tuple(bundle["evidence_unit_bindings"]),
            allowed_revision_ids=("revision-1",),
        )
        before = record.event_path.read_bytes()
        request = orchestrator._model_request(
            view,
            decision_frame=frame,
            semantic_plan=plan,
        )
        self.assertEqual(
            request["material_use_decision"]["selected_evidence_locator_ids"],
            ["locator-page-1"],
        )
        after_canonical = record.event_path.read_bytes()

        drifted = deepcopy(plan)
        drifted["selected_evidence_locator_ids"] = []
        drifted["material_use_decision"]["selected_evidence_locator_ids"] = []
        drifted["material_use_decision"]["selection_reasons"] = {}
        drifted["material_use_decision"]["task_id"] = "material-use-drifted"
        drifted.pop("plan_sha256")
        drifted["plan_sha256"] = canonical_sha256(drifted)
        with self.assertRaisesRegex(ValueError, "semantic material plan drifted"):
            orchestrator._model_request(
                view,
                decision_frame=frame,
                semantic_plan=drifted,
            )

        self.assertEqual(record.event_path.read_bytes(), after_canonical)

    def test_generic_model_route_rejects_empty_material_route_without_write(self):
        """Explicit empty material lists are invalid; omission is nonmaterial."""

        view = self.record.read_current_project_view()
        frame = self.orchestrator._architecture_frame(
            view, {"action": "model.reason"}
        )
        before = self.record.event_path.read_bytes()

        with self.assertRaisesRegex(ValueError, "material model inputs are empty"):
            self.orchestrator._model_request(
                view,
                decision_frame=frame,
                material_evidence_units=[],
                allowed_revision_ids=[],
            )

        self.assertEqual(self.record.event_path.read_bytes(), before)

    def test_semantic_runtime_dispatches_only_explicit_independent_worker_slices(self):
        from lca_research_kernel.artifact_workers import ArtifactWorkerCoordinator
        from lca_research_kernel.research_orchestrator import ResearchOrchestrator
        from lca_research_kernel.semantic_context import ResearchSemanticContext

        class Runner:
            def __init__(self):
                self.calls = []

            def run(self, contract):
                self.calls.append(contract)
                return {
                    "contract": "lca_research_kernel.research_worker_output/v1",
                    "slice_id": contract["slice_id"],
                    "worker_role": contract["worker_role"],
                    "content": {
                        "finding": contract["evidence_dimension"],
                        "limitations": [],
                    },
                    "source_refs": contract["graph_slice"]["node_ids"],
                }

        runner = Runner()
        semantic_context = ResearchSemanticContext.from_files(
            ROOT,
            source_lock_path=ROOT / "lca_research_kernel/contracts/tiangong-source-lock.json",
            capability_catalog_path=ROOT / "lca_research_kernel/contracts/tiangong-capability-catalog.json",
            skill_registry_path=ROOT / "lca_research_kernel/contracts/research-skill-registry.json",
            domain_rule_registry_path=ROOT
            / "lca_research_kernel/contracts/domain-rule-registry.json",
        )
        orchestrator = ResearchOrchestrator(
            self.record,
            model_gateway=self.gateway,
            assurance=AcceptingAssurance(),
            clock=lambda: "2026-07-23T04:01:00Z",
            semantic_context=semantic_context,
            worker_coordinator=ArtifactWorkerCoordinator(
                runner=runner,
                clock=lambda: "2026-07-23T04:01:00Z",
                max_workers=2,
            ),
        )

        def accept_fact(fact_id, role, dimension):
            fact = {
                "fact_id": fact_id,
                "classification": "missing",
                "semantic_role": role,
                "independent_evidence_dimension": dimension,
                "value": {"required": True},
                "source": {
                    "authority": "project_record",
                    "version": "event-gap",
                    "content_sha256": ("4" if role == "functional_unit" else "5") * 64,
                },
            }
            orchestrator.submit_candidate_change(
                {
                    "contract": "lca_research_kernel.project_candidate_change/v1",
                    "candidate_id": "candidate-" + fact_id,
                    "change_type": "fact.upserted",
                    "fact": fact,
                    "next_route": "resolve-research-scope",
                    "proposed_at": "2026-07-23T04:01:00Z",
                },
                idempotency_key="parallel-test:" + fact_id,
            )

        orchestrator.submit_candidate_change(
            {
                "contract": "lca_research_kernel.project_candidate_change/v1",
                "candidate_id": "candidate-research-goal",
                "change_type": "fact.upserted",
                "fact": {
                    "fact_id": "research-goal",
                    "classification": "official",
                    "domain_kind": "research_goal",
                    "value": "Resolve two independent scope evidence gaps.",
                    "source": {
                        "authority": "user_supplied_project_input",
                        "version": "brief-v1",
                        "content_sha256": "1" * 64,
                    },
                },
                "next_route": "resolve-research-scope",
                "proposed_at": "2026-07-23T04:01:00Z",
            },
            idempotency_key="parallel-test:goal",
        )
        accept_fact("functional-unit-gap", "functional_unit", "scope-evidence")
        accept_fact("system-boundary-gap", "system_boundary", "boundary-evidence")

        view = self.record.read_current_project_view()
        action = orchestrator.select_next_action(
            view,
            domain_policy={"claim_ceiling": "candidate_only"},
            available_capabilities={"models": ["fake"], "capabilities": []},
        )
        self.assertEqual(
            action["semantic_context_plan"]["work_topology"]["topology"],
            "parallel_research",
        )

        request = orchestrator._model_request(
            view,
            decision_frame=action["decision_frame"],
            semantic_plan=action["semantic_context_plan"],
        )
        self.assertEqual(
            request["contract"], "lca_research_kernel.model_request/v2"
        )
        worker_items = [
            row
            for row in request["context"]["model_context"]["items"]
            if row["identity"].startswith("research_worker_artifact:")
        ]
        self.assertEqual(len(runner.calls), 2)
        self.assertTrue(
            all("context_package" in contract for contract in runner.calls)
        )
        self.assertEqual(len(worker_items), 2)
        self.assertTrue(
            all(row["trust_level"] == "candidate" for row in worker_items)
        )

    def test_every_returned_route_carries_a_valid_architecture_decision_frame(self):
        initial = self.orchestrator.run_until_boundary("generic-research-1")
        self.assertEqual(
            initial["decision_frame"]["next_route"], "await-research-intake"
        )
        completed = self.orchestrator.resume("generic-research-1", self.intake())
        self.assertEqual(
            completed["decision_frame"]["selected_modules"],
            ["lca_domain_kernel"],
        )
        self.assertEqual(
            completed["decision_frame"]["expected_state_change"]["to_state"],
            "domain_assessment_ready",
        )

    def test_model_request_does_not_invent_or_select_provider_and_model_identity(self):
        view = self.record.read_current_project_view()
        view["facts"]["research-goal"] = {
            "fact_id": "research-goal",
            "classification": "official",
            "value": "One source-bound objective.",
            "source": {
                "authority": "user_supplied_project_input",
                "source_refs": [
                    {"path": "inputs/research-brief.json", "sha256": "a" * 64}
                ],
            },
        }
        request = self.orchestrator._model_request(view)
        self.assertNotIn("provider", request)
        self.assertNotIn("model", request)
        self.assertEqual(
            request["context"]["contract"],
            "lca_research_kernel.model_context/v1",
        )
        self.assertEqual(
            [item["identity"] for item in request["context"]["items"]],
            ["project_fact:research-goal"],
        )
        self.assertEqual(
            request["required_capabilities"],
            ["structured_output", "streaming", "tool_requests"],
        )

    def test_model_reintegration_gets_a_bounded_larger_timeout(self):
        view = self.record.read_current_project_view()
        view["facts"]["research-goal"] = {
            "fact_id": "research-goal",
            "classification": "official",
            "value": "Reintegrate one accepted source result.",
            "source": {
                "authority": "user_supplied_project_input",
                "source_refs": [
                    {"path": "inputs/research-brief.json", "sha256": "a" * 64}
                ],
            },
        }
        view["facts"]["capability-result:official-read"] = {
            "fact_id": "capability-result:official-read",
            "classification": "candidate",
            "domain_kind": "capability_result",
            "value": {"content_excerpt": {"text": "Official source excerpt."}},
            "source": {
                "authority": "capability_sourced_result",
                "version": "receipt-v1",
            },
        }
        view["facts"]["capability-result:stale-read"] = {
            "fact_id": "capability-result:stale-read",
            "classification": "candidate",
            "domain_kind": "capability_result",
            "value": {"content_excerpt": {"text": "Stale source excerpt."}},
            "source": {
                "authority": "capability_sourced_result",
                "version": "receipt-v0",
            },
        }
        frame = self.orchestrator._architecture_frame(
            view,
            {
                "action": "model.reintegrate",
                "capability_result_fact_ids": [
                    "capability-result:official-read",
                ],
                "context_capability_ids": [],
            },
        )

        request = self.orchestrator._model_request(view, decision_frame=frame)

        self.assertEqual(request["runtime_timeout_seconds"], 120.0)
        self.assertEqual(
            [item["identity"] for item in request["context"]["items"]],
            [
                "project_fact:capability-result:official-read",
                "project_fact:research-goal",
            ],
        )

    def test_domain_repair_state_allows_two_bounded_attempts_across_resume(self):
        self.assertEqual(
            self.orchestrator._domain_repair_attempt(
                {"source": {"authority": "model_candidate_only"}}
            ),
            0,
        )
        self.assertEqual(
            self.orchestrator._domain_repair_attempt(
                {
                    "source": {
                        "domain_repair_errors": ["source_hash_drifted"],
                    }
                }
            ),
            1,
        )
        self.assertEqual(
            self.orchestrator._domain_repair_attempt(
                {
                    "source": {
                        "domain_repair_errors": ["source_hash_drifted"],
                        "domain_repair_attempt": 2,
                    }
                }
            ),
            2,
        )
        self.assertTrue(
            self.orchestrator._may_attempt_domain_repair(
                {"status": "failed", "errors": ["any_first_error"]},
                prior_repair_attempt=0,
                attempts_in_this_run=0,
            )
        )
        self.assertTrue(
            self.orchestrator._may_attempt_domain_repair(
                {
                    "status": "failed",
                    "errors": ["candidate_claim_evidence_basis_drifted"],
                },
                prior_repair_attempt=1,
                attempts_in_this_run=1,
            )
        )
        self.assertFalse(
            self.orchestrator._may_attempt_domain_repair(
                {
                    "status": "failed",
                    "errors": ["candidate_claim_modality_contradicted"],
                },
                prior_repair_attempt=1,
                attempts_in_this_run=1,
            )
        )

    def test_unbound_legal_claim_repair_requires_exact_binding_or_a_limited_conclusion(self):
        view = self.record.read_current_project_view()
        view["facts"]["research-goal"] = {
            "fact_id": "research-goal",
            "classification": "official",
            "value": "Assess a source-bound legal question.",
            "source": {
                "authority": "user_supplied_project_input",
                "source_refs": [
                    {"path": "inputs/research-brief.json", "sha256": "a" * 64}
                ],
            },
        }
        view["facts"]["research-source-material"] = {
            "fact_id": "research-source-material",
            "classification": "official",
            "value": {
                "contract": "lca_research_kernel.source_bound_research_material/v1",
                "materials": [
                    {
                        "authority": "user_supplied_project_input",
                        "sha256": "b" * 64,
                        "content": {"question": "What is legally required?"},
                    }
                ],
            },
            "source": {"authority": "user_supplied_project_input"},
        }

        request = self.orchestrator._model_request(
            view,
            repair_errors=["candidate_user_visible_legal_claim_unbound"],
        )

        self.assertIn(
            "For candidate_user_visible_legal_claim_unbound",
            request["task"],
        )
        self.assertIn(
            "remove or soften the unsupported legal claim",
            request["task"],
        )
        self.assertIn(
            "return an explicit limited conclusion and name the remaining gap",
            request["task"],
        )
        self.assertIn(
            "If the rejected claim concerns an operational data, monitoring, "
            "reporting, allocation, or verification duty while the selected "
            "evidence only defines scope or terms, do not merely soften the "
            "answer",
            request["task"],
        )
        self.assertIn(
            "request the exact indexed implementation or methodology source "
            "that governs that duty",
            request["task"],
        )

        modality_request = self.orchestrator._model_request(
            view,
            repair_errors=["candidate_claim_modality_contradicted"],
        )
        self.assertIn(
            "include the contiguous governing parent clause",
            modality_request["task"],
        )
        self.assertIn(
            "remove or soften the claim",
            modality_request["task"],
        )

        evidence_request = self.orchestrator._model_request(
            view,
            repair_errors=["candidate_claim_evidence_basis_drifted"],
        )
        self.assertIn(
            "copy every evidence_basis.literal byte-for-byte",
            evidence_request["task"],
        )
        self.assertIn(
            "Do not paraphrase, translate, normalize, or substitute even one word",
            evidence_request["task"],
        )

    def test_visible_answer_composition_never_masks_an_additional_domain_error(self):
        exact = {
            "status": "failed",
            "errors": ["candidate_user_visible_legal_claim_unbound"],
        }
        self.assertTrue(
            self.orchestrator._may_compose_source_bound_visible_answer(exact)
        )
        self.assertTrue(
            self.orchestrator._may_compose_source_bound_visible_answer(
                {
                    **exact,
                    "errors": [
                        *exact["errors"],
                        "candidate_recommendations_absent",
                    ],
                }
            )
        )
        for additional_error in (
            "candidate_claim_modality_contradicted",
            "candidate_source_hash_drifted",
            "critical_gap_not_declared",
        ):
            assessment = {
                **exact,
                "errors": [*exact["errors"], additional_error],
            }
            self.assertFalse(
                self.orchestrator._may_compose_source_bound_visible_answer(
                    assessment
                )
            )

    def test_presentation_only_failure_prefers_deterministic_composition(self):
        assessment = {
            "status": "failed",
            "errors": ["candidate_user_visible_legal_clause_unbound"],
        }

        resolution = self.orchestrator._domain_failure_resolution(
            assessment,
            prior_repair_attempt=0,
            attempts_in_this_run=0,
        )

        self.assertEqual(resolution, "deterministic_composition")

    def test_recommendation_translated_as_mandate_falls_back_to_exact_source_literal(self):
        literal = (
            "The functional unit should be the tonnes of goods under the same CN code."
        )
        claim = {
            "statement": "功能单位应当是同一 CN 编码下的货物吨数。",
            "modality": "recommended_practice",
            "scientific_source_sha256s": ["a" * 64],
            "evidence_basis": {
                "literal": literal,
                "source_sha256": "a" * 64,
            },
        }
        model_fact = {
            "value": {
                "answer": claim["statement"],
                "bounded_conclusion": "现有官方摘录只支持来源绑定陈述。",
                "claim_statements": [claim],
                "limitations": ["完整实施条款尚未核对。"],
                "missing_fields": ["full_implementation_rules"],
                "recommendations": [],
            },
            "source": {"authority": "model_candidate"},
        }
        assessment = {
            "status": "failed",
            "errors": [
                "candidate_claim_modality_contradicted",
                "candidate_user_visible_legal_claim_unbound",
                "candidate_user_visible_legal_clause_unbound",
            ],
            "admissible_candidate": deepcopy(model_fact["value"]),
            "claim_modality_resolutions": [{
                "status": "contradicted",
                "declared_modality": "recommended_practice",
                "normalized_modality": None,
                "rule_id": "statement_adds_mandate",
            }],
        }

        resolution = self.orchestrator._domain_failure_resolution(
            assessment,
            prior_repair_attempt=0,
            attempts_in_this_run=0,
        )
        change = self.orchestrator._source_bound_visible_answer_composition_change(
            model_fact,
            assessment,
        )

        self.assertEqual(resolution, "deterministic_composition")
        candidate = change["fact"]["value"]
        self.assertEqual(candidate["claim_statements"][0]["statement"], literal)
        self.assertEqual(
            candidate["claim_statements"][0]["modality"],
            "recommended_practice",
        )
        self.assertIn(literal, candidate["answer"])
        self.assertNotIn("应当", candidate["answer"])

    def test_recommendation_translated_as_permission_cannot_borrow_another_claims_condition(self):
        recommendation_literal = (
            "For those cases, that supplementary unit should constitute "
            "the functional unit."
        )
        conditional_literal = (
            "Where goods are produced using different production routes, "
            "the production process should encompass all production routes."
        )
        permission_statement = "对于某些化肥，可使用补充单位作为功能单位。"
        conditional_statement = (
            "当同一货物使用不同生产路线时，"
            "生产过程应覆盖所有生产路线。"
        )
        claims = [
            {
                "statement": permission_statement,
                "modality": "recommended_practice",
                "scientific_source_sha256s": ["a" * 64],
                "evidence_basis": {
                    "literal": recommendation_literal,
                    "source_sha256": "a" * 64,
                },
            },
            {
                "statement": conditional_statement,
                "modality": "conditional_trigger",
                "scientific_source_sha256s": ["a" * 64],
                "evidence_basis": {
                    "literal": conditional_literal,
                    "source_sha256": "a" * 64,
                },
            },
        ]
        model_fact = {
            "value": {
                "answer": permission_statement + conditional_statement,
                "bounded_conclusion": "现有官方摘录只支持来源绑定陈述。",
            },
            "source": {"authority": "model_candidate"},
        }
        assessment = {
            "status": "failed",
            "errors": ["candidate_user_visible_legal_clause_unbound"],
            "admissible_candidate": {
                "answer": model_fact["value"]["answer"],
                "bounded_conclusion": model_fact["value"]["bounded_conclusion"],
                "claim_statements": claims,
                "limitations": [],
                "missing_fields": [],
                "recommendations": [],
            },
            "claim_modality_resolutions": [
                {
                    "status": "resolved",
                    "normalized_modality": "recommended_practice",
                },
                {
                    "status": "resolved",
                    "normalized_modality": "conditional_trigger",
                },
            ],
        }

        change = self.orchestrator._source_bound_visible_answer_composition_change(
            model_fact, assessment
        )

        candidate = change["fact"]["value"]
        self.assertEqual(
            candidate["claim_statements"][0]["statement"],
            recommendation_literal,
        )
        self.assertNotIn("可使用补充单位", candidate["answer"])
        self.assertEqual(
            change["fact"]["source"]["source_bound_visible_answer_composition"][
                "source_literal_fallback_count"
            ],
            1,
        )

    def test_visible_answer_composition_preserves_a_safe_epistemic_boundary(self):
        bounded = (
            "证据不足：现有官方文献未包含过渡期表格在正式实施期"
            "是否仍可直接使用的明确规定。"
        )
        model_fact = {
            "value": {
                "answer": "经营者可以继续使用该表格。",
                "bounded_conclusion": bounded,
            },
            "source": {"authority": "model_candidate"},
        }
        claim = {
            "statement": "The earlier method applied until 31 December 2025.",
            "modality": "official_factual",
            "scientific_source_sha256s": ["a" * 64],
            "evidence_basis": {
                "literal": "The earlier method applied until 31 December 2025.",
                "source_sha256": "a" * 64,
            },
        }
        assessment = {
            "status": "failed",
            "errors": ["candidate_user_visible_legal_claim_unbound"],
            "admissible_candidate": {
                "answer": model_fact["value"]["answer"],
                "bounded_conclusion": bounded,
                "claim_statements": [claim],
                "limitations": [],
                "missing_fields": ["current_method_status"],
                "recommendations": [],
            },
            "claim_modality_resolutions": [
                {
                    "status": "resolved",
                    "normalized_modality": "official_factual",
                }
            ],
        }

        change = self.orchestrator._source_bound_visible_answer_composition_change(
            model_fact, assessment
        )

        candidate = change["fact"]["value"]
        self.assertTrue(candidate["answer"].startswith(bounded))
        self.assertEqual(candidate["bounded_conclusion"], bounded)
        self.assertNotIn("经营者可以继续使用", candidate["answer"])

    def test_visible_answer_composition_removes_an_unbound_same_modality_clause(self):
        statement = "授权 CBAM 申报人须在每年 9 月 30 日前提交年度申报。"
        unsupported = "欧盟海关必须在每一批货物放行前查验 CBAM 数据。"
        boundary = "现有证据尚无法确定海关是否必须逐票查验。"
        claim = {
            "statement": statement,
            "modality": "legal_must",
            "scientific_source_sha256s": ["a" * 64],
            "evidence_basis": {
                "literal": "Each declarant shall submit an annual declaration.",
                "source_sha256": "a" * 64,
            },
        }
        model_fact = {
            "value": {
                "answer": statement + unsupported,
                "bounded_conclusion": boundary,
            },
            "source": {"authority": "model_candidate"},
        }
        assessment = {
            "status": "failed",
            "errors": ["candidate_user_visible_legal_clause_unbound"],
            "admissible_candidate": {
                "answer": model_fact["value"]["answer"],
                "bounded_conclusion": boundary,
                "claim_statements": [claim],
                "limitations": [],
                "missing_fields": ["customs_clearance_rule"],
                "recommendations": [],
            },
            "claim_modality_resolutions": [
                {"status": "resolved", "normalized_modality": "legal_must"}
            ],
        }

        change = self.orchestrator._source_bound_visible_answer_composition_change(
            model_fact, assessment
        )

        candidate = change["fact"]["value"]
        self.assertIn(statement, candidate["answer"])
        self.assertNotIn(unsupported, candidate["answer"])
        self.assertEqual(candidate["bounded_conclusion"], boundary)
        self.assertEqual(
            change["fact"]["source"]["source_bound_visible_answer_composition"][
                "removed_unsupported_claim_count"
            ],
            1,
        )

    def test_visible_answer_composition_leads_with_first_admitted_claim_without_gap(self):
        first = "当采用实际排放值时，申报人应确保申报排放经过认可核查员核查。"
        second = "运营商可以向申报人披露已经核查的排放信息。"
        claims = [
            {
                "statement": first,
                "modality": "conditional_trigger",
                "scientific_source_sha256s": ["a" * 64],
                "evidence_basis": {
                    "literal": "When actual emissions are used, the declarant shall ensure verification.",
                    "source_sha256": "a" * 64,
                },
            },
            {
                "statement": second,
                "modality": "officially_permitted",
                "scientific_source_sha256s": ["a" * 64],
                "evidence_basis": {
                    "literal": "The operator may disclose verified information.",
                    "source_sha256": "a" * 64,
                },
            },
        ]
        model_fact = {
            "value": {
                "answer": "是，第三国生产商必须把所有数据交给进口商。" + first + second,
                "bounded_conclusion": "是，第三国生产商必须提交数据。",
            },
            "source": {"authority": "model_candidate"},
        }
        assessment = {
            "status": "failed",
            "errors": ["candidate_user_visible_legal_clause_unbound"],
            "admissible_candidate": {
                "answer": model_fact["value"]["answer"],
                "bounded_conclusion": model_fact["value"]["bounded_conclusion"],
                "claim_statements": claims,
                "limitations": [],
                "missing_fields": [],
                "recommendations": [],
            },
            "claim_modality_resolutions": [
                {
                    "status": "resolved",
                    "normalized_modality": "conditional_trigger",
                },
                {
                    "status": "resolved",
                    "normalized_modality": "officially_permitted",
                },
            ],
        }

        change = self.orchestrator._source_bound_visible_answer_composition_change(
            model_fact, assessment
        )

        candidate = change["fact"]["value"]
        self.assertTrue(
            candidate["answer"].startswith(first),
            candidate["answer"],
        )
        self.assertEqual(candidate["bounded_conclusion"], first)
        self.assertNotIn("第三国生产商必须提交数据", candidate["answer"])

    def test_reintegration_idempotency_binds_the_requesting_analysis_state(self):
        facts = {
            "capability-result:one": {
                "fact_id": "capability-result:one",
                "value": {"content": "same evidence"},
            },
            "model-analysis": {
                "fact_id": "model-analysis",
                "value": {"capability_intent": {"purpose": "first request"}},
            },
        }
        first = self.orchestrator._model_reintegration_idempotency_key(
            {"facts": facts},
            ["capability-result:one"],
        )
        replay = self.orchestrator._model_reintegration_idempotency_key(
            {"facts": facts},
            ["capability-result:one"],
        )
        changed_facts = dict(facts)
        changed_facts["model-analysis"] = {
            "fact_id": "model-analysis",
            "value": {"capability_intent": {"purpose": "repaired request"}},
        }
        repaired = self.orchestrator._model_reintegration_idempotency_key(
            {"facts": changed_facts},
            ["capability-result:one"],
        )

        self.assertEqual(first, replay)
        self.assertNotEqual(first, repaired)

    def test_state_changing_model_step_is_budgeted_and_trace_recoverable(self):
        from lca_research_kernel.run_control import RunController
        from lca_research_kernel.run_trace import RunTrace

        trace = RunTrace(
            self.workspace / "run-traces" / "bounded.jsonl",
            run_id="bounded-run",
        )
        budget = {
            "contract": "lca_research_kernel.run_budget/v1",
            "max_elapsed_ms": 60000,
            "max_steps": 8,
            "max_model_calls": 2,
            "max_tool_calls": 2,
            "max_context_tokens": 6000,
            "max_repeated_fingerprint": 2,
        }
        orchestrator = self.orchestrator.__class__(
            self.record,
            model_gateway=self.gateway,
            assurance=AcceptingAssurance(),
            clock=lambda: "2026-07-23T04:01:00Z",
            run_trace=trace,
            run_controller=RunController(
                budget,
                run_id="bounded-run",
                monotonic=lambda: 0.0,
            ),
        )
        result = orchestrator.resume("generic-research-1", self.intake())
        self.assertEqual(
            result["boundary"], "awaiting_domain_and_capability_integration"
        )
        event_types = [event["event_type"] for event in trace.events()]
        self.assertIn("research.run.step.started", event_types)
        self.assertIn("research.run.progress.observed", event_types)
        recovered = RunController.restore(
            budget,
            run_id="bounded-run",
            trace=trace,
            monotonic=lambda: 0.0,
        )
        self.assertEqual(recovered.steps, 1)

    def test_interruption_recovery_replays_and_does_not_repeat_model_acceptance(self):
        from lca_research_kernel.project_record import ProjectRecord
        from lca_research_kernel.research_orchestrator import ResearchOrchestrator

        expected = self.orchestrator.resume("generic-research-1", self.intake())
        count = len(self.record.read_audit_trail())
        reopened = ProjectRecord.open_or_create_project(self.workspace)
        recovered = ResearchOrchestrator(
            reopened,
            model_gateway=self.gateway,
            assurance=AcceptingAssurance(),
            clock=lambda: "2026-07-23T04:01:00Z",
        )
        self.assertEqual(recovered.run_until_boundary("generic-research-1"), expected)
        self.assertEqual(len(reopened.read_audit_trail()), count)

    def test_model_failure_becomes_a_bounded_route_without_candidate_promotion(self):
        from lca_research_kernel.assurance import AssuranceBoundary
        from lca_research_kernel.model_gateway import (
            DeterministicFakeAdapter,
            ModelGateway,
        )
        from lca_research_kernel.research_orchestrator import ResearchOrchestrator

        failing_gateway = ModelGateway(
            {"fake": DeterministicFakeAdapter(RuntimeError("offline"))},
            default_provider="fake",
        )
        failing = ResearchOrchestrator(
            self.record,
            model_gateway=failing_gateway,
            assurance=AssuranceBoundary(),
            clock=lambda: "2026-07-23T04:01:00Z",
        )
        boundary = failing.resume("generic-research-1", self.intake())
        self.assertEqual(boundary["boundary"], "model_failure")
        self.assertEqual(boundary["failure_code"], "provider_failure")
        self.assertTrue(boundary["retryable"])
        self.assertEqual(boundary["recovery_route"], "retry-or-switch-model-provider")
        self.assertEqual(boundary["executed_external_effects"], 0)
        view = self.record.read_current_project_view()
        self.assertNotIn("model-analysis", view["facts"])
        count = len(self.record.read_audit_trail())
        self.assertEqual(failing.run_until_boundary("generic-research-1"), boundary)
        self.assertEqual(len(self.record.read_audit_trail()), count)

    def test_source_bound_capability_is_authorized_executed_and_accepted_as_candidate(self):
        from lca_research_kernel.assurance import AssuranceBoundary
        from lca_research_kernel.capability_gateway import (
            CapabilityGateway,
            LocalDeterministicCapability,
        )
        from lca_research_kernel.model_gateway import (
            DeterministicFakeAdapter,
            ModelGateway,
        )
        from lca_research_kernel.research_orchestrator import ResearchOrchestrator
        from lca_research_kernel.run_trace import RunTrace

        calls = []
        gateway = CapabilityGateway(
            {
                "local.inspect": LocalDeterministicCapability(
                    "local.inspect",
                    lambda payload: calls.append(payload) or {"rows": 3},
                    input_schema={
                        "type": "object",
                        "properties": {"artifact_ref": {"type": "string"}},
                        "required": ["artifact_ref"],
                        "additionalProperties": False,
                    },
                )
            }
        )
        model = ModelGateway(
            {
                "fake": DeterministicFakeAdapter(
                    {
                        "capability_intent": {
                            "contract": "lca_research_kernel.capability_intent/v1",
                            "purpose": "Inspect one source-bound local input.",
                            "required_authority": "local_deterministic_execution",
                            "required_access": "local_only",
                            "effect": "local_read",
                            "input": {"artifact_ref": "inputs/source.json"},
                        }
                    }
                )
            },
            default_provider="fake",
        )
        trace = RunTrace(
            self.workspace / "full-lca-capability-route.jsonl",
            run_id="full-lca-capability-route",
        )
        orchestrator = ResearchOrchestrator(
            self.record,
            model_gateway=model,
            capability_gateway=gateway,
            assurance=AssuranceBoundary(),
            clock=lambda: "2026-07-23T04:01:00Z",
            run_trace=trace,
        )
        status = orchestrator.resume("generic-research-1", self.intake())
        self.assertEqual(status["boundary"], "awaiting_domain_integration")
        results = [
            fact
            for fact in self.record.read_current_project_view()["facts"].values()
            if fact.get("domain_kind") == "capability_result"
        ]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["classification"], "candidate")
        self.assertEqual(results[0]["source"]["authority"], "capability_sourced_result")
        self.assertEqual(calls, [{"artifact_ref": "inputs/source.json"}])
        route_events = [
            event
            for event in trace.events()
            if event["event_type"] == "research.run.interaction.routed"
        ]
        full_route = next(
            event
            for event in route_events
            if event["payload"]["route"]["route_family"]
            == "full_lca_workflow"
        )
        tool_completed = next(
            event
            for event in trace.events()
            if event["event_type"] == "research.run.capability.completed"
        )
        self.assertLess(full_route["sequence"], tool_completed["sequence"])
        event_count = len(self.record.read_audit_trail())
        self.assertEqual(orchestrator.run_until_boundary("generic-research-1"), status)
        self.assertEqual(calls, [{"artifact_ref": "inputs/source.json"}])
        self.assertEqual(len(self.record.read_audit_trail()), event_count)

    def test_nonmaterial_capability_fails_closed_when_payload_contains_material_refs(self):
        from lca_research_kernel.assurance import AssuranceBoundary
        from lca_research_kernel.capability_gateway import (
            CapabilityGateway,
            LocalDeterministicCapability,
        )
        from lca_research_kernel.research_orchestrator import ResearchOrchestrator

        calls = []
        orchestrator = ResearchOrchestrator(
            self.record,
            model_gateway=self.gateway,
            capability_gateway=CapabilityGateway(
                {
                    "local.inspect": LocalDeterministicCapability(
                        "local.inspect",
                        lambda payload: calls.append(payload) or {"rows": 1},
                        input_schema={
                            "type": "object",
                            "properties": {"artifact_ref": {"type": "string"}},
                            "required": ["artifact_ref"],
                            "additionalProperties": False,
                        },
                    )
                }
            ),
            assurance=AssuranceBoundary(),
            clock=lambda: "2026-07-23T04:01:00Z",
        )
        result = orchestrator.execute_bounded_capability(
            "generic-research-1",
            capability_id="local.inspect",
            effect="local_read",
            purpose="Inspect a caller-selected locator.",
            payload={"locator_id": "forged-locator"},
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(calls, [])

    def test_unknown_capability_input_contract_cannot_claim_nonmaterial_by_omission(self):
        from lca_research_kernel.assurance import AssuranceBoundary
        from lca_research_kernel.capability_gateway import CapabilityGateway
        from lca_research_kernel.research_orchestrator import ResearchOrchestrator

        calls = []

        class UnclassifiedInputCapability:
            def describe(self):
                return {
                    "capability_id": "local.unclassified",
                    "authority": "local_deterministic_execution",
                    "access": "local_only",
                    "effect": "local_read",
                    "result_status": "sourced_result_not_fact",
                    "input_contract": "third_party.inspect.input/v1",
                }

            def execute(self, payload):
                calls.append(payload)
                return {"content": {"rows": 1}}

        orchestrator = ResearchOrchestrator(
            self.record,
            model_gateway=self.gateway,
            capability_gateway=CapabilityGateway(
                {"local.unclassified": UnclassifiedInputCapability()}
            ),
            assurance=AssuranceBoundary(),
            clock=lambda: "2026-07-23T04:01:00Z",
        )

        result = orchestrator.execute_bounded_capability(
            "generic-research-1",
            capability_id="local.unclassified",
            effect="local_read",
            purpose="Inspect a nested caller-selected material locator.",
            payload={"selection": {"locators": ["locator-page-1"]}},
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["boundary"]["executed_external_effects"], 0)
        self.assertEqual(calls, [])

    def test_unapproved_capability_effect_returns_zero_effect_handoff(self):
        from lca_research_kernel.assurance import AssuranceBoundary
        from lca_research_kernel.capability_gateway import CapabilityGateway
        from lca_research_kernel.model_gateway import (
            DeterministicFakeAdapter,
            ModelGateway,
        )
        from lca_research_kernel.research_orchestrator import ResearchOrchestrator

        calls = []

        class ExternalWriter:
            def describe(self):
                return {
                    "capability_id": "external.publish",
                    "authority": "external_service",
                    "access": "write",
                    "effect": "external_write",
                    "result_status": "sourced_result_not_fact",
                }

            def execute(self, payload):
                calls.append(payload)
                return {"content": {"sent": True}}

        model = ModelGateway(
            {
                "fake": DeterministicFakeAdapter(
                    {
                        "capability_intent": {
                            "contract": "lca_research_kernel.capability_intent/v1",
                            "purpose": "Publish a result.",
                            "required_authority": "external_service",
                            "required_access": "write",
                            "effect": "external_write",
                            "input": {"message": "must not be sent"},
                        }
                    }
                )
            },
            default_provider="fake",
        )
        orchestrator = ResearchOrchestrator(
            self.record,
            model_gateway=model,
            capability_gateway=CapabilityGateway({"external.publish": ExternalWriter()}),
            assurance=AssuranceBoundary(),
            clock=lambda: "2026-07-23T04:01:00Z",
        )
        status = orchestrator.resume("generic-research-1", self.intake())
        self.assertEqual(status["boundary"], "human_action_required")
        self.assertTrue(status["authorization_required"])
        self.assertEqual(status["executed_external_effects"], 0)
        self.assertEqual(calls, [])

    def test_orchestrator_has_no_case_or_council_dependency(self):
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

    def test_orchestrator_routes_the_native_workflow_from_state_not_caller_tool_choice(self):
        activity = self.orchestrator.route_native_workflow(
            {
                "contract": "lca_research_kernel.native_research_state/v1",
                "state_id": "engagement_frozen",
                "accepted_evidence_roles": ["engagement"],
                "candidate_evidence_roles": [],
                "missing_evidence_roles": [],
                "project_record_ref": {
                    "last_event_id": "event-1",
                    "last_event_sha256": "a" * 64,
                },
            }
        )
        self.assertEqual(activity["to_state"], "goal_and_scope_bounded")
        self.assertEqual(activity["selected_capabilities"], [])


if __name__ == "__main__":
    unittest.main()
