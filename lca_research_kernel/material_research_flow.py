"""Orchestrator-owned material research transaction.

This is a private deep implementation of the D-18 Research Orchestrator.  It
keeps evidence selection, recovery, gap routing, model execution and accepted
result commitment behind one command instead of leaking those protocols into
the Product Interface.
"""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Mapping, Sequence

from .assured_project_record import AssuredProjectRecord
from .material_context_planner import MaterialContextPlanner
from .material_sources import active_user_turn_sha256
from .material_turn_executor import MaterialTurnExecutor
from .project_record import MATERIAL_EVENT_TYPES, canonical_sha256
from .research_program import validate_research_program
from .research_turn import infer_response_language
from .run_trace import RunTrace, canonical_json_bytes


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RUN_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,255}$")


class MaterialResearchFlow:
    """Advance one source-bound material request to a durable boundary."""

    def __init__(
        self,
        orchestrator: object,
        *,
        turn_compiler: object,
        run_budget: Mapping[str, object],
        planner: MaterialContextPlanner | None = None,
    ):
        self.orchestrator = orchestrator
        self.record = orchestrator.project_record
        self.assurance = orchestrator.assurance
        self.trace = orchestrator.run_trace
        self.answer_timing = orchestrator.answer_timing
        self.turn_compiler = turn_compiler
        self.run_budget = deepcopy(dict(run_budget))
        self.planner = planner or MaterialContextPlanner()
        if not isinstance(self.trace, RunTrace):
            raise ValueError("material research Run Trace is unavailable")

    def advance(
        self,
        *,
        normalized: Mapping[str, object],
        bundles: Sequence[Mapping[str, object]],
    ) -> dict:
        request = deepcopy(dict(normalized))
        material_bundles = [deepcopy(dict(bundle)) for bundle in bundles]
        research_id = request["research_id"]
        view = self.record.read_current_project_view()
        if "research-goal" not in (view.get("facts") or {}):
            self.orchestrator.accept_research_intake(
                research_id,
                {
                    "contract": "lca_research_kernel.research_intake/v1",
                    "intake_id": f"start-{research_id}",
                    "research_goal": request["objective"],
                    "source_refs": deepcopy(request["source_refs"]),
                    "received_at": request["received_at"],
                },
            )
            view = self.record.read_current_project_view()

        if self._completed_result_matches(
            request=request,
            bundles=material_bundles,
            project_view=view,
        ):
            self._validate_completed_program_replay(
                request=request,
                bundles=material_bundles,
                project_view=view,
            )
            return {
                "boundary": "research_result_accepted",
                "research_id": research_id,
            }

        self.ensure_active_turn(request)
        recovered = self.recover_use_decision(
            request=request,
            bundles=material_bundles,
        )
        if recovered is None:
            locators = self.accept_inputs(
                bundles=material_bundles,
                proposed_at=str(request["received_at"]),
            )
            use_decision, decision_event_sha256 = self.prepare_use_decision(
                request=request,
                bundles=material_bundles,
                locator_acceptance=locators,
                proposed_at=str(request["received_at"]),
            )
        else:
            _, use_decision, decision_event_sha256 = recovered

        gap_boundary = self._evidence_gap_boundary(
            request=request,
            bundles=material_bundles,
            use_decision=use_decision,
        )
        if gap_boundary is not None:
            return gap_boundary

        material_acceptance = MaterialTurnExecutor(
            self.orchestrator,
            turn_compiler=self.turn_compiler,
        ).execute(
            normalized=request,
            bundles=material_bundles,
            use_decision=use_decision,
            decision_event_sha256=decision_event_sha256,
        )
        return self._accept_answer_result(
            request=request,
            material_acceptance=material_acceptance,
            material_use_decision_id=str(use_decision["task_id"]),
            interaction_turn_sha256=canonical_sha256(request),
            interaction_run_id=self.trace.run_id,
        )

    def advance_continuation(
        self,
        *,
        normalized: Mapping[str, object],
        bundles: Sequence[Mapping[str, object]],
        interaction_turn_sha256: str,
    ) -> dict:
        """Advance a previously accepted follow-up turn over frozen materials."""

        request = deepcopy(dict(normalized))
        material_bundles = [deepcopy(dict(bundle)) for bundle in bundles]
        accepted_locators = self.accepted_locator_index(bundles=material_bundles)
        if accepted_locators is None:
            return self.orchestrator.observe_boundary(
                "awaiting_user_data",
                details={
                    "interaction_kind": "material_scope_authorization",
                    "authorization_required": True,
                    "question": (
                        "上一轮资料仅获准用于该轮。若要继续查看原文细节，"
                        "请明确将该资料授权用于整个研究项目，或在本轮重新提供资料。"
                    ),
                },
            )
        recovered = self.recover_use_decision(
            request=request,
            bundles=material_bundles,
        )
        if recovered is None:
            use_decision, decision_event_sha256 = self.prepare_use_decision(
                request=request,
                bundles=material_bundles,
                locator_acceptance=accepted_locators,
                proposed_at=str(request["received_at"]),
            )
        else:
            _, use_decision, decision_event_sha256 = recovered
        gap_boundary = self._evidence_gap_boundary(
            request=request,
            bundles=material_bundles,
            use_decision=use_decision,
        )
        if gap_boundary is not None:
            return gap_boundary
        material_acceptance = MaterialTurnExecutor(
            self.orchestrator,
            turn_compiler=self.turn_compiler,
        ).execute(
            normalized=request,
            bundles=material_bundles,
            use_decision=use_decision,
            decision_event_sha256=decision_event_sha256,
        )
        return self._accept_answer_result(
            request=request,
            material_acceptance=material_acceptance,
            material_use_decision_id=str(use_decision["task_id"]),
            interaction_turn_sha256=interaction_turn_sha256,
            interaction_run_id=self.trace.run_id,
        )

    def _completed_result_matches(
        self,
        *,
        request: Mapping[str, object],
        bundles: Sequence[Mapping[str, object]],
        project_view: Mapping[str, object],
    ) -> bool:
        result = project_view.get("result")
        if not isinstance(result, Mapping):
            return False
        expected_materials = {
            bundle["research_material"]["material_id"]: bundle["research_material"]
            for bundle in bundles
        }
        stable_fields = {
            "contract",
            "material_id",
            "project_id",
            "material_kind",
            "scope",
            "authority",
            "research_classification",
            "labels",
            "created_by",
            "created_at",
            "deleted_at",
        }
        replayed = {
            key: {
                field: value
                for field, value in material.items()
                if field in stable_fields
            }
            for key, material in (project_view.get("materials") or {}).items()
            if key in expected_materials
        }
        expected = {
            key: {
                field: value
                for field, value in material.items()
                if field in stable_fields
            }
            for key, material in expected_materials.items()
        }
        if set(replayed) != set(expected):
            raise ValueError("accepted material replay drifted")
        for material_id, expected_material in expected.items():
            observed = replayed[material_id]
            for field in (
                "contract",
                "material_id",
                "project_id",
                "material_kind",
                "authority",
                "created_by",
            ):
                if observed.get(field) != expected_material.get(field):
                    raise ValueError("accepted material replay drifted")
        requested_revisions = {
            bundle["material_revision"]["revision_id"] for bundle in bundles
        }
        return (
            result.get("material_request_sha256") == canonical_sha256(request)
            and requested_revisions.issubset(
                set((project_view.get("material_revisions") or {}).keys())
            )
        )

    def _validate_completed_program_replay(
        self,
        *,
        request: Mapping[str, object],
        bundles: Sequence[Mapping[str, object]],
        project_view: Mapping[str, object],
    ) -> None:
        result = project_view.get("result")
        decisions = project_view.get("material_use_decisions")
        decision_id = (
            result.get("material_use_decision_id")
            if isinstance(result, Mapping)
            else None
        )
        use_decision = (
            decisions.get(decision_id)
            if isinstance(decisions, Mapping) and isinstance(decision_id, str)
            else None
        )
        route = result.get("interaction_route") if isinstance(result, Mapping) else None
        run_id = result.get("interaction_run_id") if isinstance(result, Mapping) else None
        program_sha256 = (
            result.get("interaction_program_sha256")
            if isinstance(result, Mapping)
            else None
        )
        turn_frame_sha256 = (
            result.get("interaction_turn_frame_sha256")
            if isinstance(result, Mapping)
            else None
        )
        execution_head = (
            result.get("interaction_project_head_sha256")
            if isinstance(result, Mapping)
            else None
        )
        if (
            not isinstance(use_decision, Mapping)
            or not isinstance(route, Mapping)
            or not isinstance(run_id, str)
            or RUN_ID_RE.fullmatch(run_id) is None
            or not isinstance(program_sha256, str)
            or SHA256_RE.fullmatch(program_sha256) is None
            or not isinstance(turn_frame_sha256, str)
            or SHA256_RE.fullmatch(turn_frame_sha256) is None
        ):
            raise ValueError("accepted research program replay state is unavailable")
        trace = RunTrace(
            self.record.workspace / "run-traces" / f"{run_id}.jsonl",
            run_id=run_id,
        )
        trace_events = trace.events()
        program_events = [
            event
            for event in trace_events
            if event.get("event_type") == "research.run.program.compiled"
        ]
        if len(program_events) != 1:
            raise ValueError("accepted research program replay is unavailable")
        payload = program_events[0].get("payload")
        if (
            not isinstance(payload, Mapping)
            or set(payload)
            != {"contract", "program", "candidate_only", "project_truth_effect"}
            or payload.get("contract")
            != "lca_research_kernel.research_program_observation/v1"
            or payload.get("candidate_only") is not True
            or payload.get("project_truth_effect") != "none"
        ):
            raise ValueError("accepted research program observation is invalid")
        program = validate_research_program(payload.get("program"))
        if program["program_sha256"] != program_sha256:
            raise ValueError("accepted research program identity drifted")
        execution_bound = program["contract"] in {
            "lca_research_kernel.research_program/v2",
            "lca_research_kernel.research_program/v3",
        }
        if execution_bound and (
            not isinstance(execution_head, str)
            or SHA256_RE.fullmatch(execution_head) is None
        ):
            raise ValueError("accepted research program execution head is unavailable")
        if execution_head is not None and (
            not isinstance(execution_head, str)
            or SHA256_RE.fullmatch(execution_head) is None
        ):
            raise ValueError("accepted research program execution head is invalid")
        frame_events = [
            event
            for event in trace_events
            if event.get("event_type") == "research.run.turn.frame_compiled"
            and event.get("payload", {}).get("frame_sha256") == turn_frame_sha256
            and event.get("payload", {}).get("turn_sha256") == program["turn_sha256"]
            and event.get("payload", {}).get("project_state_sha256")
            == program["project_state_sha256"]
        ]
        audit = self.record.read_audit_trail()
        decision_events = [
            event
            for event in audit
            if event.get("event_sha256") == program["project_state_sha256"]
            and event.get("event_type")
            == MATERIAL_EVENT_TYPES["material.use_decision_recorded"]
            and event.get("payload", {}).get("candidate", {}).get(
                "material_use_decision"
            )
            == use_decision
        ]
        result_events = [
            event
            for event in audit
            if event.get("event_type") == "research.project.accepted_change"
            and event.get("payload", {}).get("candidate", {}).get("result") == result
        ]
        if (
            len(frame_events) != 1
            or len(decision_events) != 1
            or (
                execution_bound
                and (
                    len(result_events) != 1
                    or result_events[0].get("previous_event_sha256") != execution_head
                )
            )
        ):
            raise ValueError("accepted research program authority is unavailable")
        turn_frame = frame_events[0]["payload"]
        selected_ids = set(use_decision.get("selected_evidence_locator_ids") or [])
        selected_roles = sorted(
            {
                role
                for bundle in bundles
                for binding in bundle.get("evidence_unit_bindings") or []
                if isinstance(binding, Mapping)
                and binding.get("locator_id") in selected_ids
                and isinstance(binding.get("evidence_unit"), Mapping)
                for role in binding["evidence_unit"].get("semantic_roles") or []
                if isinstance(role, str) and role
            }
        )
        objective = str(request.get("objective", "")).strip()
        objective_projection = objective if len(objective) <= 512 else objective[:509] + "..."
        expected = use_decision.get("expected_state_change")
        expected_evidence_roles = (
            sorted(expected.get("required_evidence_roles") or [])
            if isinstance(expected, Mapping)
            else []
        )
        knowledge_roles = turn_frame.get("required_knowledge_roles")
        if (
            not isinstance(knowledge_roles, list)
            or any(not isinstance(role, str) or not role for role in knowledge_roles)
        ):
            raise ValueError("accepted research program knowledge plan is unavailable")
        expected_bindings = {
            "turn_sha256": turn_frame.get("turn_sha256"),
            "project_state_sha256": turn_frame.get("project_state_sha256"),
            "objective": objective_projection,
            "objective_sha256": canonical_sha256(objective_projection),
            "response_language": infer_response_language(objective),
            "cognitive_operation": turn_frame.get("cognitive_operation"),
            "answer_obligations": turn_frame.get("answer_obligations"),
            "referent_kind": turn_frame.get("referent_kind"),
            "referent_identity": turn_frame.get("referent_identity"),
        }
        observed_bindings = {
            "turn_sha256": program["turn_sha256"],
            "project_state_sha256": program["project_state_sha256"],
            **program["requested_outcome"],
        }
        if (
            observed_bindings != expected_bindings
            or program["route"] != route
            or program["resource_budget"] != self.run_budget
            or program["evidence_plan"]["use_decision_sha256"]
            != canonical_sha256(use_decision)
            or program["evidence_plan"]["allowed_revision_ids"]
            != sorted(use_decision.get("allowed_revision_ids") or [])
            or program["evidence_plan"]["selected_evidence_locator_ids"]
            != sorted(selected_ids)
            or program["evidence_plan"]["required_evidence_roles"]
            != expected_evidence_roles
            or program["evidence_plan"]["required_knowledge_roles"]
            != sorted(knowledge_roles)
            or program["evidence_plan"]["selected_evidence_roles"] != selected_roles
            or program["evidence_plan"]["excluded_locator_count"]
            != len(use_decision.get("exclusions") or [])
        ):
            raise ValueError("accepted research program replay drifted")
        expected_key = (
            "research-program:"
            + program["turn_sha256"]
            + ":"
            + program["project_state_sha256"]
        )
        if program_events[0].get("idempotency_key") != expected_key:
            raise ValueError("accepted research program identity drifted")

    def _accept_answer_result(
        self,
        *,
        request: Mapping[str, object],
        material_acceptance: Mapping[str, object],
        material_use_decision_id: str,
        interaction_turn_sha256: str,
        interaction_run_id: str,
    ) -> dict:
        if material_acceptance.get("decision") != "accepted":
            raise ValueError(
                "material answer candidate was rejected: "
                + ",".join(material_acceptance.get("errors") or [])
            )
        program_sha256 = material_acceptance.get("_research_program_sha256")
        program = validate_research_program(material_acceptance.get("_research_program"))
        turn_frame_sha256 = material_acceptance.get("_turn_frame_sha256")
        execution_head = material_acceptance.get("_execution_project_head_sha256")
        if (
            not isinstance(program_sha256, str)
            or SHA256_RE.fullmatch(program_sha256) is None
            or program["program_sha256"] != program_sha256
            or not isinstance(turn_frame_sha256, str)
            or SHA256_RE.fullmatch(turn_frame_sha256) is None
            or not isinstance(execution_head, str)
            or SHA256_RE.fullmatch(execution_head) is None
        ):
            raise ValueError("material answer execution identity is unavailable")
        accepted_view = self.record.read_current_project_view()
        if accepted_view.get("last_event_sha256") != execution_head:
            raise ValueError("material answer Project Record head changed")
        domain_assessment = material_acceptance["domain_assessment"]
        result = deepcopy(domain_assessment["admissible_candidate"])
        result.update(
            {
                "interaction_route": deepcopy(program["route"]),
                "material_request_sha256": canonical_sha256(request),
                "material_use_decision_id": material_use_decision_id,
                "interaction_turn_sha256": interaction_turn_sha256,
                "interaction_run_id": interaction_run_id,
                "interaction_program_sha256": program_sha256,
                "interaction_turn_frame_sha256": turn_frame_sha256,
                "interaction_project_head_sha256": execution_head,
                "domain_acceptance": deepcopy(domain_assessment),
                "permission_effect_receipt": {
                    "contract": "lca_research_kernel.formal_result_effect_receipt/v1",
                    "external_writes": 0,
                    "forbidden_effects": 0,
                },
                "human_delivery": {
                    "contract": "adaptive_human_delivery/v1",
                    "delivery_shape": "source_bound_material_answer",
                    "answer": domain_assessment["admissible_candidate"]["answer"],
                    "claim_ceiling": domain_assessment["admissible_candidate"][
                        "claim_ceiling"
                    ],
                },
            }
        )
        candidate = {
            "contract": "lca_research_kernel.project_candidate_change/v1",
            "candidate_id": "material-answer-" + canonical_sha256(result)[:24],
            "change_type": "result.accepted",
            "result": result,
            "next_route": "deliver-source-bound-material-answer",
            "proposed_at": request["received_at"],
        }
        acceptance = self.assurance.evaluate_candidate_change(candidate, accepted_view)
        if acceptance.get("decision") != "accepted":
            raise ValueError(
                "accepted material result failed Project Record assurance: "
                + ",".join(acceptance.get("errors") or [])
            )
        self.record.append_accepted_change(
            candidate,
            acceptance,
            idempotency_key="material-answer:" + canonical_sha256(result),
            expected_last_event_sha256=execution_head,
        )
        if self.answer_timing is None:
            raise ValueError("material research answer timing is unavailable")
        self.answer_timing.mark_formal_acceptance(canonical_sha256(result))
        return {
            "boundary": "research_result_accepted",
            "research_id": request["research_id"],
        }

    def ensure_active_turn(self, request: Mapping[str, object]) -> str:
        turn_sha256 = canonical_sha256(request)
        view = self.record.read_current_project_view()
        prior = (view.get("facts") or {}).get("active-user-turn")
        prior_value = prior.get("value") if isinstance(prior, Mapping) else None
        if (
            isinstance(prior_value, Mapping)
            and prior_value.get("turn_sha256") == turn_sha256
        ):
            return turn_sha256
        candidate = {
            "contract": "lca_research_kernel.project_candidate_change/v1",
            "candidate_id": "material-user-turn-" + turn_sha256[:24],
            "change_type": "user_turn.activated",
            "fact": {
                "fact_id": "active-user-turn",
                "classification": "candidate",
                "domain_kind": "user_turn",
                "value": {
                    "contract": "lca_research_kernel.research_user_turn/v1",
                    "input_id": "material-request-" + turn_sha256[:24],
                    "text": str(request["objective"]),
                    "turn_sha256": turn_sha256,
                    "prior_result_sha256": None,
                    "turn_run_id": "material-request-" + turn_sha256[:12],
                },
                "source": {
                    "authority": "user_supplied_project_input",
                    "source_refs": deepcopy(request["source_refs"]),
                    "input_id": "material-request-" + turn_sha256[:24],
                },
            },
            "next_route": "accept-material-revision",
            "proposed_at": request["received_at"],
        }
        acceptance = self.assurance.evaluate_candidate_change(
            candidate, self.record.read_current_project_view()
        )
        if acceptance.get("decision") != "accepted":
            raise ValueError(
                "material active turn failed assurance: "
                + ",".join(acceptance.get("errors") or [])
            )
        self.record.append_accepted_change(
            candidate,
            acceptance,
            idempotency_key="material-user-turn:" + turn_sha256,
        )
        return turn_sha256

    def accept_inputs(
        self,
        *,
        bundles: Sequence[Mapping[str, object]],
        proposed_at: str,
    ) -> dict[str, dict]:
        accepted_locators: dict[str, dict] = {}
        candidates: list[dict] = []
        verified_content_by_candidate_id: dict[str, object] = {}
        for bundle in bundles:
            material = deepcopy(bundle["research_material"])
            current_view = self.record.read_current_project_view()
            turn_scope_sha256 = (
                active_user_turn_sha256(current_view)
                if material.get("scope") == "turn"
                else None
            )
            revision_candidate_id = (
                "material-revision-" + bundle["material_revision"]["revision_id"]
            )
            if material["material_id"] not in current_view.get("materials", {}):
                candidates.append(
                    {
                        "contract": "lca_research_kernel.project_candidate_change/v1",
                        "candidate_id": "material-" + material["material_id"],
                        "change_type": "material.registered",
                        "material": material,
                        "scientific_status": "accepted_project_input",
                        "next_route": "accept-material-revision",
                        "proposed_at": proposed_at,
                    }
                )
            candidates.append(
                {
                    "contract": "lca_research_kernel.project_candidate_change/v1",
                    "candidate_id": revision_candidate_id,
                    "change_type": "material.revision_accepted",
                    "material_revision": deepcopy(bundle["material_revision"]),
                    **(
                        {"turn_scope_sha256": turn_scope_sha256}
                        if turn_scope_sha256 is not None
                        else {}
                    ),
                    "processing_receipt_ref": deepcopy(bundle["processing_receipt_ref"]),
                    "processing_receipt_sha256": hashlib.sha256(
                        canonical_json_bytes(bundle["processing_receipt"]) + b"\n"
                    ).hexdigest(),
                    "next_route": "accept-evidence-locators",
                    "proposed_at": proposed_at,
                }
            )
            verified_content_by_candidate_id[revision_candidate_id] = {
                "content_kind": "material_processing_receipt",
                "content_ref": deepcopy(bundle["processing_receipt_ref"]),
                "content": deepcopy(bundle["processing_receipt"]),
            }
            accepted_bundle = self.assurance.accept_evidence_locators(bundle=bundle)
            bindings = {
                binding["locator_id"]: binding
                for binding in bundle["evidence_unit_bindings"]
            }
            for locator in bundle["locators"]:
                accepted_locator = accepted_bundle.get(locator["locator_id"])
                binding = bindings.get(locator["locator_id"])
                if not isinstance(accepted_locator, Mapping) or not isinstance(
                    binding, Mapping
                ):
                    raise ValueError("source-bound evidence locator is unavailable")
                locator_id = str(accepted_locator["locator_id"])
                accepted_locators[locator_id] = deepcopy(dict(accepted_locator))
                candidate_id = "evidence-locator-" + locator_id
                candidates.append(
                    {
                        "contract": "lca_research_kernel.project_candidate_change/v1",
                        "candidate_id": candidate_id,
                        "change_type": "evidence.locator_accepted",
                        "evidence_locator": deepcopy(dict(accepted_locator)),
                        "evidence_binding": {
                            key: deepcopy(binding[key])
                            for key in ("locator_id", "content_hash", "source_span")
                        },
                        "evidence_units_ref": deepcopy(bundle["evidence_units_ref"]),
                        "next_route": "record-material-use-decision",
                        "proposed_at": proposed_at,
                    }
                )
                verified_content_by_candidate_id[candidate_id] = {
                    "content_kind": "evidence_unit",
                    "content_ref": deepcopy(bundle["evidence_units_ref"]),
                    "content": deepcopy(binding["evidence_unit"]),
                }
        AssuredProjectRecord(self.record, self.assurance).accept_material_changes(
            [
                {
                    "candidate": candidate,
                    "idempotency_key": (
                        "material-change:"
                        + candidate["change_type"]
                        + ":"
                        + candidate["candidate_id"]
                    ),
                    "verified_content": verified_content_by_candidate_id.get(
                        candidate["candidate_id"]
                    ),
                }
                for candidate in candidates
            ]
        )
        return accepted_locators

    def _evidence_gap_boundary(
        self,
        *,
        request: Mapping[str, object],
        bundles: Sequence[Mapping[str, object]],
        use_decision: Mapping[str, object],
    ) -> dict | None:
        expected = use_decision.get("expected_state_change")
        required = (
            set(expected.get("required_evidence_roles") or [])
            if isinstance(expected, Mapping)
            else set()
        )
        selected = set(use_decision.get("selected_evidence_locator_ids") or [])
        covered = {
            role
            for bundle in bundles
            for binding in bundle.get("evidence_unit_bindings") or []
            if isinstance(binding, Mapping)
            and binding.get("locator_id") in selected
            and isinstance(binding.get("evidence_unit"), Mapping)
            for role in binding["evidence_unit"].get("semantic_roles") or []
            if isinstance(role, str) and role
        }
        missing = sorted(required - covered)
        if not missing:
            return None
        language = infer_response_language(request.get("objective"))
        question = (
            "当前材料没有提供完成该研究步骤所需的字段："
            + "、".join(missing)
            + "。请补充包含这些字段的材料或明确说明它们不可获得。"
            if language == "zh-CN"
            else "The current material does not provide the evidence roles "
            + ", ".join(missing)
            + ". Please supply material containing them or confirm that they "
            "are unavailable."
        )
        return self.orchestrator.observe_boundary(
            "awaiting_user_data",
            details={
                "interaction_kind": "material_evidence_gap",
                "authorization_required": True,
                "missing_evidence_roles": missing,
                "question": question,
            },
        )

    def prepare_use_decision(
        self,
        *,
        request: Mapping[str, object],
        bundles: Sequence[Mapping[str, object]],
        locator_acceptance: Mapping[str, Mapping[str, object]],
        proposed_at: str,
    ) -> tuple[dict, str]:
        decision = self.planner.plan(
            request=request,
            bundles=bundles,
            locator_acceptance=locator_acceptance,
            project_view=self.record.read_current_project_view(),
        )
        candidate = {
            "contract": "lca_research_kernel.project_candidate_change/v1",
            "candidate_id": "material-use-" + canonical_sha256(decision)[:24],
            "change_type": "material.use_decision_recorded",
            "decision_id": decision.get("task_id") or canonical_sha256(decision),
            "material_use_decision": deepcopy(decision),
            "decision_sha256": canonical_sha256(decision),
            "next_route": "consume-selected-material-evidence",
            "proposed_at": proposed_at,
        }
        acceptance = self.assurance.evaluate_candidate_change(
            candidate, self.record.read_current_project_view()
        )
        if acceptance.get("decision") != "accepted":
            raise ValueError(
                "material use decision failed assurance: "
                + ",".join(acceptance.get("errors") or [])
            )
        event = self.record.append_accepted_change(
            candidate,
            acceptance,
            idempotency_key="material-use-decision:" + canonical_sha256(decision),
        )
        return decision, str(event["event_sha256"])

    def accepted_locator_index(
        self,
        *,
        bundles: Sequence[Mapping[str, object]],
    ) -> dict[str, dict] | None:
        resolved = self.record.resolve_current_material_model_inputs()
        if resolved is None:
            return None
        allowed_revision_ids = set(resolved.get("allowed_revision_ids") or [])
        requested_revision_ids = {
            str(bundle["material_revision"]["revision_id"]) for bundle in bundles
        }
        if requested_revision_ids != allowed_revision_ids:
            return None
        accepted_locators: dict[str, dict] = {}
        resolved_bindings = {
            binding["locator_id"]: binding
            for binding in resolved.get("material_evidence_units") or []
            if isinstance(binding, Mapping)
            and isinstance(binding.get("locator_id"), str)
        }
        expected_binding_ids: set[str] = set()
        accepted_view = self.record.read_current_project_view()
        materials = accepted_view.get("materials") or {}
        revisions = accepted_view.get("material_revisions") or {}
        locators = accepted_view.get("evidence_locators") or {}
        if not all(
            isinstance(value, Mapping)
            for value in (materials, revisions, locators)
        ):
            raise ValueError("accepted material state is invalid")
        for bundle in bundles:
            material = bundle["research_material"]
            revision = bundle["material_revision"]
            material_id = material["material_id"]
            revision_id = revision["revision_id"]
            stored_material = materials.get(material_id)
            stored_revision = revisions.get(revision_id)
            if (
                not isinstance(stored_material, Mapping)
                or stored_material.get("current_revision_id") != revision_id
                or stored_material.get("deleted_at") is not None
                or not isinstance(stored_revision, Mapping)
                or stored_revision.get("artifact_ref") != revision.get("artifact_ref")
                or stored_revision.get("processing_status")
                != revision.get("processing_status")
            ):
                raise ValueError("resolved material differs from Project Record")
            bindings = {
                binding["locator_id"]: binding
                for binding in bundle["evidence_unit_bindings"]
            }
            accepted_bundle = self.assurance.accept_evidence_locators(bundle=bundle)
            if set(bindings) != set(accepted_bundle):
                raise ValueError("resolved evidence bindings are incomplete")
            for locator_id, accepted_locator in accepted_bundle.items():
                if locators.get(locator_id) != accepted_locator:
                    raise ValueError("resolved locator differs from Project Record")
                binding = bindings.get(locator_id)
                if (
                    not isinstance(binding, Mapping)
                    or resolved_bindings.get(locator_id) != binding
                ):
                    raise ValueError("resolved evidence differs from Project Record")
                expected_binding_ids.add(locator_id)
                accepted_locators[locator_id] = deepcopy(dict(accepted_locator))
        if expected_binding_ids != set(resolved_bindings):
            raise ValueError("resolved evidence set differs from Project Record")
        return accepted_locators

    def recover_use_decision(
        self,
        *,
        request: Mapping[str, object],
        bundles: Sequence[Mapping[str, object]],
    ) -> tuple[dict[str, dict], dict, str] | None:
        view = self.record.read_current_project_view()
        task_id = "material-answer-" + canonical_sha256(
            {
                "research_id": request["research_id"],
                "objective": request["objective"],
                "revision_ids": sorted(
                    bundle["material_revision"]["revision_id"] for bundle in bundles
                ),
            }
        )[:24]
        decisions = view.get("material_use_decisions")
        accepted_decision = (
            decisions.get(task_id) if isinstance(decisions, Mapping) else None
        )
        if not isinstance(accepted_decision, Mapping):
            return None
        audit_events = self.record.read_audit_trail()
        material_identity_types = {
            MATERIAL_EVENT_TYPES["material.registered"],
            MATERIAL_EVENT_TYPES["material.revision_accepted"],
            MATERIAL_EVENT_TYPES["evidence.locator_accepted"],
        }
        events = [
            *[
                event
                for event in audit_events
                if event.get("event_type") not in material_identity_types
            ],
            *self.record.read_accepted_material_change_receipts(),
        ]
        by_identity: dict[tuple[str, str], list[Mapping[str, object]]] = {}
        for event in events:
            event_type = event.get("event_type")
            key = event.get("idempotency_key")
            if isinstance(event_type, str) and isinstance(key, str):
                by_identity.setdefault((event_type, key), []).append(event)
        accepted_locators: dict[str, dict] = {}
        for bundle in bundles:
            material = deepcopy(bundle["research_material"])
            material["scientific_status"] = "accepted_project_input"
            material_id = material["material_id"]
            if (view.get("materials") or {}).get(material_id) != material:
                raise ValueError("accepted material recovery state drifted")
            registrations = [
                event
                for event in events
                if event.get("event_type") == MATERIAL_EVENT_TYPES["material.registered"]
                and event.get("payload", {}).get("candidate", {}).get("candidate_id")
                == "material-" + material_id
            ]
            if len(registrations) != 1:
                raise ValueError("accepted material recovery identity drifted")
            revision = deepcopy(bundle["material_revision"])
            revision["processing_receipt_ref"] = deepcopy(
                bundle["processing_receipt_ref"]
            )
            revision["processing_receipt_sha256"] = hashlib.sha256(
                canonical_json_bytes(bundle["processing_receipt"]) + b"\n"
            ).hexdigest()
            revision_id = revision["revision_id"]
            if (view.get("material_revisions") or {}).get(revision_id) != revision:
                raise ValueError("accepted material revision recovery state drifted")
            turn_binding = (view.get("material_revision_turn_bindings") or {}).get(
                revision_id
            )
            revision_candidate = {
                "contract": "lca_research_kernel.project_candidate_change/v1",
                "candidate_id": "material-revision-" + revision_id,
                "change_type": "material.revision_accepted",
                "material_revision": deepcopy(bundle["material_revision"]),
                **(
                    {"turn_scope_sha256": turn_binding}
                    if turn_binding is not None
                    else {}
                ),
                "processing_receipt_ref": deepcopy(bundle["processing_receipt_ref"]),
                "processing_receipt_sha256": revision["processing_receipt_sha256"],
                "next_route": "accept-evidence-locators",
                "proposed_at": request["received_at"],
            }
            self._require_exact_event(
                events,
                events_by_identity=by_identity,
                event_type=MATERIAL_EVENT_TYPES["material.revision_accepted"],
                idempotency_key=(
                    "material-change:material.revision_accepted:"
                    + revision_candidate["candidate_id"]
                ),
                candidate=revision_candidate,
            )
            accepted_bundle = self.assurance.accept_evidence_locators(bundle=bundle)
            bindings = {
                binding["locator_id"]: binding
                for binding in bundle["evidence_unit_bindings"]
            }
            if (
                set(accepted_bundle)
                != {locator["locator_id"] for locator in bundle["locators"]}
                or set(bindings) != set(accepted_bundle)
            ):
                raise ValueError("accepted evidence recovery bundle is incomplete")
            for locator_id, accepted_locator in accepted_bundle.items():
                if (view.get("evidence_locators") or {}).get(locator_id) != accepted_locator:
                    raise ValueError("accepted evidence locator recovery state drifted")
                binding = bindings[locator_id]
                locator_candidate = {
                    "contract": "lca_research_kernel.project_candidate_change/v1",
                    "candidate_id": "evidence-locator-" + locator_id,
                    "change_type": "evidence.locator_accepted",
                    "evidence_locator": deepcopy(accepted_locator),
                    "evidence_binding": {
                        key: deepcopy(binding[key])
                        for key in ("locator_id", "content_hash", "source_span")
                    },
                    "evidence_units_ref": deepcopy(bundle["evidence_units_ref"]),
                    "next_route": "record-material-use-decision",
                    "proposed_at": request["received_at"],
                }
                self._require_exact_event(
                    events,
                    events_by_identity=by_identity,
                    event_type=MATERIAL_EVENT_TYPES["evidence.locator_accepted"],
                    idempotency_key=(
                        "material-change:evidence.locator_accepted:"
                        + locator_candidate["candidate_id"]
                    ),
                    candidate=locator_candidate,
                )
                accepted_locators[locator_id] = deepcopy(dict(accepted_locator))
        decision_events = [
            event
            for event in events
            if event.get("event_type")
            == MATERIAL_EVENT_TYPES["material.use_decision_recorded"]
            and event.get("payload", {}).get("candidate", {}).get("decision_id")
            == task_id
            and event.get("payload", {}).get("candidate", {}).get(
                "material_use_decision"
            )
            == accepted_decision
        ]
        if len(decision_events) != 1:
            raise ValueError("accepted material use decision recovery is ambiguous")
        decision_event = decision_events[0]
        if any(
            event.get("sequence", 0) > decision_event["sequence"]
            and event.get("event_type") != "research.agent.started"
            for event in events
        ):
            raise ValueError("accepted material use decision was superseded")
        selection_view = deepcopy(view)
        selection_view["last_event_sha256"] = decision_event[
            "previous_event_sha256"
        ]
        expected_decision = self.planner.plan(
            request=request,
            bundles=bundles,
            locator_acceptance=accepted_locators,
            project_view=selection_view,
        )
        if dict(accepted_decision) != expected_decision:
            raise ValueError("accepted material use decision recovery state drifted")
        decision_candidate = {
            "contract": "lca_research_kernel.project_candidate_change/v1",
            "candidate_id": "material-use-" + canonical_sha256(expected_decision)[:24],
            "change_type": "material.use_decision_recorded",
            "decision_id": task_id,
            "material_use_decision": deepcopy(expected_decision),
            "decision_sha256": canonical_sha256(expected_decision),
            "next_route": "consume-selected-material-evidence",
            "proposed_at": request["received_at"],
        }
        recovered_event = self._require_exact_event(
            events,
            events_by_identity=by_identity,
            event_type=MATERIAL_EVENT_TYPES["material.use_decision_recorded"],
            idempotency_key=(
                "material-use-decision:" + canonical_sha256(expected_decision)
            ),
            candidate=decision_candidate,
        )
        return (
            accepted_locators,
            expected_decision,
            str(recovered_event["event_sha256"]),
        )

    @staticmethod
    def _require_exact_event(
        events: Sequence[Mapping[str, object]],
        *,
        events_by_identity: Mapping[
            tuple[str, str], list[Mapping[str, object]]
        ]
        | None,
        event_type: str,
        idempotency_key: str,
        candidate: Mapping[str, object],
    ) -> Mapping[str, object]:
        matches = (
            list(events_by_identity.get((event_type, idempotency_key), []))
            if events_by_identity is not None
            else [
                event
                for event in events
                if event.get("event_type") == event_type
                and event.get("idempotency_key") == idempotency_key
            ]
        )
        observed = (
            deepcopy(matches[0].get("payload", {}).get("candidate"))
            if len(matches) == 1
            else None
        )
        expected = deepcopy(dict(candidate))
        observed_time = (
            observed.pop("proposed_at", None) if isinstance(observed, dict) else None
        )
        expected_time = expected.pop("proposed_at", None)
        if (
            len(matches) != 1
            or not isinstance(observed_time, str)
            or not observed_time
            or not isinstance(expected_time, str)
            or not expected_time
            or observed != expected
        ):
            raise ValueError("accepted material recovery event drifted")
        return matches[0]
