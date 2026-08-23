"""Internal bounded state-transition implementation for Research Orchestrator."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Mapping

from .project_record import canonical_sha256
from .run_control import NoProgressError, RunBudgetExceeded


@dataclass(frozen=True)
class _StepOutcome:
    boundary: dict | None = None
    domain_repair_attempts_delta: int = 0


@dataclass(frozen=True)
class _ModelOutcome:
    candidate: Mapping[str, object] | None = None
    boundary: dict | None = None


class BoundedRunStateMachine:
    """Advance one Project Record until a terminal product boundary.

    This is a private deep implementation of the D-18 Research Orchestrator,
    not another controller or source of project truth. The host retains all
    authorization, Project Record, Assurance, model, capability and trace
    ownership; this class owns only bounded transition sequencing.
    """

    def __init__(self, host: object, *, max_transitions: int = 24):
        if (
            not isinstance(max_transitions, int)
            or isinstance(max_transitions, bool)
            or max_transitions < 1
        ):
            raise ValueError("run transition limit is invalid")
        self._host = host
        self._max_transitions = max_transitions

    def run(self, research_id: str) -> dict:
        host = self._host
        host._require_research_id(research_id)
        domain_repair_attempts = 0
        for _ in range(self._max_transitions):
            project_view = host.project_record.read_current_project_view()
            if self._reuse_official_source_evidence(project_view):
                continue
            action = host.select_next_action(
                project_view,
                domain_policy={"claim_ceiling": "candidate_only"},
                available_capabilities={
                    "models": host.model_gateway.capabilities(),
                    "capabilities": (
                        host.capability_gateway.list_capabilities(
                            {"research_id": research_id}
                        )
                        if host.capability_gateway is not None
                        else []
                    ),
                },
            )
            host._record_interaction_route(action["interaction_route"])
            outcome = self._advance(
                project_view,
                action,
                domain_repair_attempts=domain_repair_attempts,
            )
            if outcome.boundary is not None:
                return deepcopy(outcome.boundary)
            domain_repair_attempts += outcome.domain_repair_attempts_delta
        return self._iteration_budget_boundary()

    def _advance(
        self,
        project_view: Mapping[str, object],
        action: Mapping[str, object],
        *,
        domain_repair_attempts: int,
    ) -> _StepOutcome:
        action_name = action["action"]
        if action_name == "boundary":
            return _StepOutcome(
                boundary=self._host._boundary(
                    project_view,
                    action["boundary"],
                    details=action.get("details"),
                    decision_frame=action["decision_frame"],
                )
            )
        if action_name in {"model.reason", "model.reintegrate", "model.repair"}:
            return self._execute_model_action(project_view, action)
        if action_name == "domain.accept_direct":
            return self._accept_direct(project_view, action)
        if action_name == "domain.accept_tiangong_native":
            return self._accept_tiangong_native(project_view, action)
        if action_name == "domain.integrate":
            return self._integrate_source_candidate(
                project_view,
                action,
                domain_repair_attempts=domain_repair_attempts,
            )
        if action_name == "resolve.material_gap":
            return self._resolve_material_gap(project_view, action)
        if action_name == "capability.execute":
            outcome = self._host._execute_capability_action(action, project_view)
            return _StepOutcome(
                boundary=(
                    deepcopy(outcome["boundary"])
                    if outcome["status"] != "completed"
                    else None
                )
            )
        raise ValueError(f"unsupported orchestrator action: {action_name}")

    def _reuse_official_source_evidence(
        self, project_view: Mapping[str, object]
    ) -> bool:
        host = self._host
        change = host._official_source_evidence_reuse_change(project_view)
        if change is None:
            return False
        reuse = change["fact"]["source"]["official_source_evidence_reuse"]
        host.submit_candidate_change(
            change,
            idempotency_key=(
                "official-source-evidence-reuse:"
                + str(reuse["reuse_sha256"])[:24]
            ),
        )
        host._record_runtime_progress()
        return True

    def _execute_model_action(
        self,
        project_view: Mapping[str, object],
        action: Mapping[str, object],
    ) -> _StepOutcome:
        host = self._host
        hard_boundary = host._answer_step_boundary(
            project_view,
            "model",
            decision_frame=action["decision_frame"],
        )
        if hard_boundary is not None:
            return _StepOutcome(boundary=hard_boundary)
        host._ensure_deadline_preview(project_view)
        try:
            model_request = host._prepare_material_execution(
                action,
                execution_kind=str(action["action"]),
            )
            host._before_runtime_step(
                context_tokens=model_request["context"]["estimated_tokens"],
                additional_model_calls=1,
            )
        except (RunBudgetExceeded, NoProgressError, ValueError) as error:
            if not host._is_bounded_runtime_failure(error):
                raise
            return _StepOutcome(
                boundary=host._runtime_failure_boundary(
                    project_view, action["decision_frame"], error
                )
            )
        model_outcome = self._reason(
            model_request,
            decision_frame=action["decision_frame"],
            project_view=project_view,
        )
        if model_outcome.boundary is not None:
            return _StepOutcome(boundary=model_outcome.boundary)
        model_candidate = model_outcome.candidate
        reintegrated_fact_ids = (
            action.get("capability_result_fact_ids")
            if action["action"] == "model.reintegrate"
            else None
        )
        candidate = host._model_change(
            model_candidate,
            interaction_route=action["interaction_route"],
            interaction_turn_sha256=host._active_user_turn_sha256(project_view),
            reintegrated_capability_fact_ids=reintegrated_fact_ids,
            domain_repair_errors=(
                action.get("repair_errors")
                if action["action"] == "model.repair"
                else None
            ),
        )
        host._record_model_timing_and_preview(model_candidate)
        host.submit_candidate_change(
            candidate,
            idempotency_key=self._model_idempotency_key(
                project_view,
                action,
                reintegrated_fact_ids=reintegrated_fact_ids,
            ),
        )
        host._accept_official_source_research_requirement(
            project_view, model_candidate
        )
        host._record_runtime_progress()
        return _StepOutcome()

    def _reason(
        self,
        model_request: Mapping[str, object],
        *,
        decision_frame: Mapping[str, object],
        project_view: Mapping[str, object],
    ) -> _ModelOutcome:
        host = self._host
        request_id = str(model_request["request_id"])
        started = (
            host.answer_timing.start_activity(request_id)
            if (
                host.answer_timing is not None
                and not getattr(host.model_gateway, "handles_answer_timing", False)
            )
            else None
        )
        try:
            candidate = host.model_gateway.reason(
                model_request,
                trace=host.run_trace,
                pre_attempt=host._material_execution_guard(model_request),
            )
            host._assert_model_candidate_request_binding(candidate, model_request)
        except Exception as error:
            if host.answer_timing is not None and started is not None:
                host.answer_timing.record_activity_since(
                    request_id,
                    kind="model",
                    started_monotonic=started,
                    useful=False,
                )
            if host._is_bounded_runtime_failure(error):
                return _ModelOutcome(
                    boundary=host._runtime_failure_boundary(
                        host.project_record.read_current_project_view(),
                        decision_frame,
                        error,
                    )
                )
            failure = host.model_gateway.normalize_error(error)
            route = host.assurance.route_failure(
                {"layer": "model", "code": failure["code"]}
            )
            return _ModelOutcome(
                boundary=host._boundary(
                    project_view,
                    "model_failure",
                    details={
                        "failure_code": failure["code"],
                        "retryable": failure["retryable"],
                        "recovery_route": route["next_route"],
                        "executed_external_effects": 0,
                    },
                    decision_frame=decision_frame,
                )
            )
        if host.answer_timing is not None and started is not None:
            host.answer_timing.record_activity_since(
                request_id,
                kind="model",
                started_monotonic=started,
            )
        return _ModelOutcome(candidate=candidate)

    def _model_idempotency_key(
        self,
        project_view: Mapping[str, object],
        action: Mapping[str, object],
        *,
        reintegrated_fact_ids: object,
    ) -> str:
        host = self._host
        key = "model-analysis"
        active_turn_sha256 = host._active_user_turn_sha256(project_view)
        if active_turn_sha256 is not None:
            key = "model-analysis:" + active_turn_sha256[:24]
        if isinstance(reintegrated_fact_ids, list):
            return host._model_reintegration_idempotency_key(
                project_view,
                reintegrated_fact_ids,
            )
        if action["action"] == "model.repair":
            return (
                "model-capability-intent-repair:"
                + canonical_sha256(
                    {
                        "model_analysis": project_view["facts"].get(
                            "model-analysis"
                        ),
                        "repair_errors": action.get("repair_errors"),
                    }
                )[:24]
            )
        return key

    def _accept_direct(
        self,
        project_view: Mapping[str, object],
        action: Mapping[str, object],
    ) -> _StepOutcome:
        host = self._host
        assessment = host.assurance.domain_kernel.evaluate_direct_answer_candidate(
            action["model_analysis"]["value"],
            unexecuted_capability_claims=(
                host._unexecuted_model_capability_claims(
                    action["model_analysis"]
                )
            ),
        )
        if assessment["status"] != "passed":
            return _StepOutcome(
                boundary=host._boundary(
                    project_view,
                    "domain_rejection",
                    details={
                        "failure_code": "direct_candidate_not_admissible",
                        "domain_errors": deepcopy(assessment["errors"]),
                        "executed_external_effects": 0,
                    },
                    decision_frame=action["decision_frame"],
                )
            )
        gap_resolutions = None
        if assessment.get("missing_fields"):
            gap_resolutions = host._direct_missing_data_resolutions(
                project_view,
                action["model_analysis"],
                assessment["missing_fields"],
            )
            if gap_resolutions is None:
                host._accept_direct_missing_data_gaps(
                    project_view,
                    action["model_analysis"],
                    assessment["missing_fields"],
                )
                host._record_runtime_progress()
                return _StepOutcome()
        result_change = host._direct_result_change(
            action["model_analysis"],
            assessment,
            gap_resolutions=gap_resolutions,
            interaction_route=action["interaction_route"],
        )
        host.submit_candidate_change(
            result_change,
            idempotency_key=(
                "direct-answer-result:"
                + canonical_sha256(
                    {
                        "model_analysis": action["model_analysis"],
                        "assessment": assessment,
                    }
                )[:24]
            ),
        )
        self._mark_formal_acceptance(result_change)
        host._record_runtime_progress()
        return _StepOutcome()

    def _accept_tiangong_native(
        self,
        project_view: Mapping[str, object],
        action: Mapping[str, object],
    ) -> _StepOutcome:
        host = self._host
        sourced_result = action["capability_result"].get("value")
        if not isinstance(sourced_result, Mapping):
            raise ValueError("TianGong native sourced result is absent")
        assessment = (
            host.assurance.domain_kernel.evaluate_tiangong_native_contract_result(
                sourced_result
            )
        )
        if assessment["status"] != "passed":
            return _StepOutcome(
                boundary=host._boundary(
                    project_view,
                    "domain_rejection",
                    details={
                        "failure_code": "tiangong_native_result_not_admissible",
                        "domain_errors": deepcopy(assessment["errors"]),
                        "executed_external_effects": 0,
                    },
                    decision_frame=action["decision_frame"],
                )
            )
        result_change = host._tiangong_native_result_change(
            action["capability_result"],
            assessment,
        )
        host.submit_candidate_change(
            result_change,
            idempotency_key=(
                "tiangong-native-result:"
                + canonical_sha256(result_change["result"])
            ),
        )
        self._mark_formal_acceptance(result_change)
        host._record_runtime_progress()
        return _StepOutcome()

    def _integrate_source_candidate(
        self,
        project_view: Mapping[str, object],
        action: Mapping[str, object],
        *,
        domain_repair_attempts: int,
    ) -> _StepOutcome:
        host = self._host
        assessment = host.assurance.domain_kernel.evaluate_generic_source_candidate(
            action["source_material"]["value"],
            action["model_analysis"]["value"],
            action["capability_results"],
        )
        if assessment["status"] != "passed":
            prior_repair_attempt = host._domain_repair_attempt(
                action["model_analysis"]
            )
            resolution = host._domain_failure_resolution(
                assessment,
                prior_repair_attempt=prior_repair_attempt,
                attempts_in_this_run=domain_repair_attempts,
            )
            if resolution == "deterministic_composition":
                return self._compose_source_bound_answer(action, assessment)
            if resolution == "model_repair":
                return self._repair_source_candidate(
                    project_view,
                    action,
                    assessment,
                    prior_repair_attempt=prior_repair_attempt,
                )
            return _StepOutcome(
                boundary=host._boundary(
                    project_view,
                    "domain_rejection",
                    details={
                        "failure_code": "source_bound_candidate_not_admissible",
                        "domain_errors": deepcopy(assessment["errors"]),
                        "executed_external_effects": 0,
                    },
                    decision_frame=action["decision_frame"],
                )
            )
        result_change = host._generic_result_change(
            action["source_material"],
            action["capability_results"],
            action["model_analysis"],
            assessment,
        )
        host.submit_candidate_change(
            result_change,
            idempotency_key=(
                "generic-source-result:"
                + canonical_sha256(
                    {
                        "source_material": action["source_material"],
                        "capability_results": action["capability_results"],
                        "model_analysis": action["model_analysis"],
                        "domain_assessment": assessment,
                    }
                )[:24]
            ),
        )
        self._mark_formal_acceptance(result_change)
        host._record_runtime_progress()
        return _StepOutcome()

    def _compose_source_bound_answer(
        self,
        action: Mapping[str, object],
        assessment: Mapping[str, object],
    ) -> _StepOutcome:
        host = self._host
        change = host._source_bound_visible_answer_composition_change(
            action["model_analysis"], assessment
        )
        composition = change["fact"]["source"][
            "source_bound_visible_answer_composition"
        ]
        host.submit_candidate_change(
            change,
            idempotency_key=(
                "source-bound-visible-answer-composition:"
                + composition["composition_sha256"][:24]
            ),
        )
        if host.run_trace is not None:
            host.run_trace.append(
                "research.run.answer.source_bound_composed",
                {
                    "contract": (
                        "lca_research_kernel."
                        "source_bound_visible_answer_composition_observation/v1"
                    ),
                    "composition": deepcopy(composition),
                },
                idempotency_key=(
                    "source-bound-visible-answer-composition:"
                    + composition["composition_sha256"]
                ),
                occurred_at=host.clock(),
            )
        host._record_runtime_progress()
        return _StepOutcome()

    def _repair_source_candidate(
        self,
        project_view: Mapping[str, object],
        action: Mapping[str, object],
        assessment: Mapping[str, object],
        *,
        prior_repair_attempt: int,
    ) -> _StepOutcome:
        host = self._host
        capability_fact_ids = [
            fact["fact_id"]
            for fact in action["capability_results"]
            if isinstance(fact, Mapping)
            and isinstance(fact.get("fact_id"), str)
        ]
        repair_frame = host._architecture_frame(
            project_view,
            {
                "action": "model.repair",
                "capability_result_fact_ids": capability_fact_ids,
            },
        )
        repair_action = {
            "action": "model.repair",
            "decision_frame": repair_frame,
            "repair_errors": assessment["errors"],
        }
        repair_action["action_authorization"] = host._action_authorization(
            project_view, repair_action
        )
        try:
            request = host._prepare_material_execution(
                repair_action,
                execution_kind="model.repair",
            )
            host._before_runtime_step(
                context_tokens=request["context"]["estimated_tokens"],
                additional_model_calls=1,
            )
        except (RunBudgetExceeded, NoProgressError, ValueError) as error:
            if not host._is_bounded_runtime_failure(error):
                raise
            return _StepOutcome(
                boundary=host._runtime_failure_boundary(
                    project_view, repair_frame, error
                )
            )
        model_outcome = self._reason(
            request,
            decision_frame=repair_frame,
            project_view=project_view,
        )
        if model_outcome.boundary is not None:
            return _StepOutcome(boundary=model_outcome.boundary)
        host.submit_candidate_change(
            host._model_change(
                model_outcome.candidate,
                interaction_route=action["model_analysis"].get(
                    "source", {}
                ).get("interaction_route"),
                interaction_turn_sha256=host._active_user_turn_sha256(
                    project_view
                ),
                domain_repair_errors=assessment["errors"],
                domain_repair_attempt=prior_repair_attempt + 1,
                reintegrated_capability_fact_ids=capability_fact_ids,
            ),
            idempotency_key=(
                "model-domain-repair:"
                + canonical_sha256(
                    {
                        "model_analysis": action["model_analysis"],
                        "domain_errors": assessment["errors"],
                    }
                )[:24]
            ),
        )
        host._record_runtime_progress()
        return _StepOutcome(domain_repair_attempts_delta=1)

    def _resolve_material_gap(
        self,
        project_view: Mapping[str, object],
        action: Mapping[str, object],
    ) -> _StepOutcome:
        host = self._host
        try:
            host._before_runtime_step()
        except (RunBudgetExceeded, NoProgressError) as error:
            return _StepOutcome(
                boundary=host._runtime_failure_boundary(
                    project_view, action["decision_frame"], error
                )
            )
        resolution = action["resolution"]
        gap_id = resolution["gap_id"]
        availability_decision = action.get("availability_decision")
        resolution_basis = {
            "resolution": resolution,
            "availability_decision": availability_decision,
        }
        candidate = {
            "contract": "lca_research_kernel.project_candidate_change/v1",
            "candidate_id": (
                "gap-resolution-" + canonical_sha256(resolution_basis)[:24]
            ),
            "change_type": "fact.upserted",
            "fact": {
                "fact_id": f"material-gap-resolution:{gap_id}",
                "classification": resolution["classification"],
                "domain_kind": "material_gap_resolution",
                "value": deepcopy(resolution),
                "source": {
                    "authority": "lca_domain_kernel_gap_resolution",
                    "gap_sha256": action["gap_sha256"],
                    "availability_decision": deepcopy(availability_decision),
                },
            },
            "next_route": resolution["next_route"],
            "proposed_at": host.clock(),
        }
        host.submit_candidate_change(
            candidate,
            idempotency_key=(
                f"material-gap-resolution:{gap_id}:"
                + canonical_sha256(resolution_basis)
            ),
        )
        host._record_runtime_progress()
        return _StepOutcome()

    def _mark_formal_acceptance(
        self, result_change: Mapping[str, object]
    ) -> None:
        host = self._host
        if host.answer_timing is not None:
            host.answer_timing.mark_formal_acceptance(
                canonical_sha256(result_change["result"])
            )

    def _iteration_budget_boundary(self) -> dict:
        host = self._host
        project_view = host.project_record.read_current_project_view()
        frame = host._architecture_frame(
            project_view,
            {
                "action": "boundary",
                "boundary": "capability_no_progress",
            },
        )
        return host._boundary(
            project_view,
            "capability_no_progress",
            details={
                "failure_code": "orchestration_iteration_budget_exhausted",
                "executed_external_effects": 0,
            },
            decision_frame=frame,
        )
