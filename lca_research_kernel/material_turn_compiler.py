"""Compile one accepted material follow-up into a bounded research turn.

This is an internal deep implementation of the existing Research Orchestrator
and Context responsibilities.  It does not own project truth: every input is
read from the accepted Project Record and every model output remains a
candidate until the existing Domain Kernel and Assurance checks accept it.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Callable, Mapping

from .answer_obligations import GovernedLCAKnowledge, accepted_claim_slice
from .answer_quality import required_material_limitations
from .context_assembler import ContextAssembler
from .model_schema_profiles import (
    model_gateway_supports,
    research_initial_turn_intent_schema,
    research_initial_turn_semantic_schema,
    research_turn_intent_schema,
    research_turn_semantic_schema,
)
from .project_record import ProjectRecord, canonical_sha256
from .research_turn import (
    build_research_turn_intent_protocol,
    build_research_turn_semantic_protocol,
    compile_initial_research_turn_intent,
    compile_research_turn_intent,
    compile_research_turn_frame,
    infer_response_language,
    require_research_turn_obligations,
)
from .run_trace import RunTrace


class MaterialTurnCompiler:
    """Hide follow-up interpretation and knowledge selection behind one call."""

    def __init__(
        self,
        repository_root: Path,
        *,
        clock: Callable[[], str],
        semantic_context: object = None,
    ) -> None:
        self.repository_root = Path(repository_root)
        self.clock = clock
        self.semantic_context = semantic_context
        self.context_assembler = ContextAssembler()

    @staticmethod
    def provisional_frame(
        record: ProjectRecord,
        *,
        use_decision: Mapping[str, object],
        decision_event_sha256: str,
    ) -> dict:
        """Compile the deterministic pre-classification turn boundary."""

        view = record.read_current_project_view()
        frame_view = deepcopy(view)
        frame_view["last_event_sha256"] = decision_event_sha256
        facts = view.get("facts")
        active = facts.get("active-user-turn") if isinstance(facts, Mapping) else None
        turn = active.get("value") if isinstance(active, Mapping) else None
        if not isinstance(turn, Mapping):
            raise ValueError("accepted active research turn is unavailable")
        prior_sha256 = turn.get("prior_result_sha256")
        prior_result = (
            record.resolve_accepted_result(prior_sha256)
            if isinstance(prior_sha256, str)
            else None
        )
        return compile_research_turn_frame(
            frame_view,
            prior_result=prior_result,
            allowed_revision_ids=list(use_decision["allowed_revision_ids"]),
            semantic_proposal=None,
        )

    def compile(
        self,
        record: ProjectRecord,
        *,
        use_decision: Mapping[str, object],
        decision_event_sha256: str,
        model_gateway: object,
        trace: RunTrace,
        research_program: Mapping[str, object],
        pre_attempt: Callable[
            [Mapping[str, object], Mapping[str, object]], None
        ]
        | None = None,
    ) -> tuple[dict, dict | None, list[dict], list[dict], str]:
        """Bind one accepted turn before exposing material bodies to a model."""

        view = record.read_current_project_view()
        frame_view = deepcopy(view)
        frame_view["last_event_sha256"] = decision_event_sha256
        facts = view.get("facts")
        active = facts.get("active-user-turn") if isinstance(facts, Mapping) else None
        turn = active.get("value") if isinstance(active, Mapping) else None
        if not isinstance(turn, Mapping):
            raise ValueError("accepted active research turn is unavailable")
        prior_sha256 = turn.get("prior_result_sha256")
        prior_result = (
            record.resolve_accepted_result(prior_sha256)
            if isinstance(prior_sha256, str)
            else None
        )
        proposal = None
        response_language = infer_response_language(turn.get("text"))
        semantic_v1_enabled = model_gateway_supports(
            model_gateway, "research_turn_semantics_v1"
        )
        intent_v2_enabled = model_gateway_supports(
            model_gateway, "research_turn_intent_v2"
        )
        if semantic_v1_enabled or intent_v2_enabled:
            claims = prior_result.get("claims") if prior_result is not None else []
            if prior_result is not None and (
                not isinstance(claims, list) or not claims
            ):
                raise ValueError("accepted prior result claims are unavailable")
            prior_claims = [
                {
                    "claim_id": claim.get("claim_id"),
                    "text": claim.get("text"),
                    "citation_tokens": deepcopy(claim.get("citation_tokens")),
                }
                for claim in claims
                if isinstance(claim, Mapping)
            ]
            if len(prior_claims) != len(claims) or any(
                not isinstance(claim.get("claim_id"), str)
                or not isinstance(claim.get("text"), str)
                or not isinstance(claim.get("citation_tokens"), list)
                for claim in prior_claims
            ):
                raise ValueError("accepted prior result claims are invalid")
            prior_frame = (
                prior_result.get("turn_frame")
                if prior_result is not None
                else None
            )
            prior_frame_available = (
                isinstance(prior_frame, Mapping)
                and isinstance(prior_frame.get("cognitive_operation"), str)
                and isinstance(prior_frame.get("answer_obligations"), list)
            )
            semantic_context = self._semantic_context(
                turn=turn,
                prior_sha256=prior_sha256,
                prior_claims=prior_claims,
                claims=claims,
                prior_frame=prior_frame,
                prior_frame_available=prior_frame_available,
                decision_event_sha256=decision_event_sha256,
                intent_v2_enabled=intent_v2_enabled,
            )
            context_package = self.context_assembler.typed_package(
                research_program=research_program,
                project_id=str(view["project_id"]),
                model_context=semantic_context,
            )
            model_request = {
                "contract": "lca_research_kernel.model_request/v2",
                "request_id": "research-turn-" + str(turn["turn_sha256"])[:24],
                "task": self._semantic_task(
                    intent_v2_enabled,
                    initial_turn=prior_result is None,
                ),
                "context": context_package,
                "output_schema": (
                    (
                        research_initial_turn_intent_schema()
                        if prior_result is None
                        else research_turn_intent_schema(
                            [claim["claim_id"] for claim in prior_claims]
                        )
                    )
                    if intent_v2_enabled
                    else (
                        research_initial_turn_semantic_schema()
                        if prior_result is None
                        else research_turn_semantic_schema(
                            [claim["claim_id"] for claim in prior_claims]
                        )
                    )
                ),
                "tools": [],
                "required_capabilities": [
                    "structured_output",
                    (
                        "research_turn_intent_v2"
                        if intent_v2_enabled
                        else "research_turn_semantics_v1"
                    ),
                ],
            }
            semantic_candidate = model_gateway.reason(
                model_request,
                trace=trace,
                pre_attempt=pre_attempt,
            )
            validator = getattr(
                model_gateway, "validate_candidate_request_binding", None
            )
            if callable(validator):
                validator(semantic_candidate, model_request)
            else:
                from .model_gateway import (
                    validate_model_candidate_request_binding,
                )

                validate_model_candidate_request_binding(
                    semantic_candidate, model_request
                )
            output = semantic_candidate.get("output")
            if not isinstance(output, Mapping):
                raise ValueError("research turn semantic candidate is absent")
            if intent_v2_enabled:
                proposal = (
                    compile_initial_research_turn_intent(output)
                    if prior_result is None
                    else compile_research_turn_intent(output)
                )
            else:
                proposal = deepcopy(dict(output))
                if prior_result is None:
                    proposal["referent_claim_id"] = None
        frame = compile_research_turn_frame(
            frame_view,
            prior_result=prior_result,
            allowed_revision_ids=list(use_decision["allowed_revision_ids"]),
            semantic_proposal=proposal,
        )
        if required_material_limitations(
            use_decision, response_language=response_language
        ):
            frame = require_research_turn_obligations(
                frame,
                required_obligations=["uncertainty_and_limit"],
                project_view=frame_view,
                prior_result=prior_result,
            )
        prior_claim_slice = accepted_claim_slice(frame, prior_result)
        governed_knowledge = self._select_knowledge(
            frame, response_language=response_language
        )
        trace.append(
            "research.run.turn.frame_compiled",
            {
                "contract": "lca_research_kernel.research_turn_frame_observation/v2",
                "frame_sha256": frame["frame_sha256"],
                "turn_sha256": frame["turn_sha256"],
                "project_state_sha256": frame["project_state_sha256"],
                "cognitive_operation": frame["cognitive_operation"],
                "answer_obligations": deepcopy(frame["answer_obligations"]),
                "required_knowledge_roles": deepcopy(
                    frame["required_knowledge_roles"]
                ),
                "referent_kind": frame["referent"]["kind"],
                "referent_identity": frame["referent"]["identity"],
                "prior_claim_ids": [
                    claim["claim_id"] for claim in prior_claim_slice
                ],
                "governed_knowledge_ids": [
                    row["knowledge_id"] for row in governed_knowledge
                ],
                "response_language": response_language,
                "candidate_only": True,
                "project_truth_effect": "none",
            },
            idempotency_key="turn-frame:" + frame["frame_sha256"],
            occurred_at=self.clock(),
        )
        return (
            frame,
            prior_result,
            prior_claim_slice,
            governed_knowledge,
            response_language,
        )

    def _select_knowledge(
        self, frame: Mapping[str, object], *, response_language: str
    ) -> list[dict]:
        selector = getattr(
            self.semantic_context, "select_governed_knowledge", None
        )
        if callable(selector):
            return selector(frame, response_language=response_language)
        return GovernedLCAKnowledge.load(self.repository_root).select(
            frame, response_language=response_language
        )

    @staticmethod
    def _semantic_context(
        *,
        turn: Mapping[str, object],
        prior_sha256: str | None,
        prior_claims: list[dict],
        claims: list[object],
        prior_frame: object,
        prior_frame_available: bool,
        decision_event_sha256: str,
        intent_v2_enabled: bool,
    ) -> dict:
        return {
            "contract": "lca_research_kernel.research_turn_semantic_context/v1",
            "turn_sha256": turn.get("turn_sha256"),
            "current_text": turn.get("text"),
            "project_state_sha256": decision_event_sha256,
            "accepted_prior_result_sha256": prior_sha256,
            "accepted_prior_claims": prior_claims,
            "accepted_prior_turn_summary": {
                "status": "available" if prior_frame_available else "legacy_unavailable",
                "cognitive_operation": (
                    prior_frame["cognitive_operation"]
                    if prior_frame_available
                    else None
                ),
                "answer_obligations": (
                    deepcopy(prior_frame["answer_obligations"])
                    if prior_frame_available
                    else []
                ),
                "claim_kinds": [
                    claim.get("claim_kind")
                    for claim in claims
                    if isinstance(claim, Mapping)
                    and isinstance(claim.get("claim_kind"), str)
                ],
            },
            "research_turn_semantic_protocol": (
                None
                if intent_v2_enabled
                else build_research_turn_semantic_protocol()
            ),
            "research_turn_intent_protocol": (
                build_research_turn_intent_protocol()
                if intent_v2_enabled
                else None
            ),
            "evidence_refs": [
                {
                    "identity": "user_turn:" + str(turn.get("turn_sha256")),
                    "authority": "user_supplied_project_input",
                    "content_sha256": canonical_sha256(turn.get("text")),
                },
                *[
                    {
                        "identity": "accepted_claim:" + claim["claim_id"],
                        "authority": "accepted_project_result",
                        "content_sha256": canonical_sha256(claim),
                    }
                    for claim in prior_claims
                ],
            ],
            "authority_boundary": {
                "may_select_only_listed_claim_ids": True,
                "may_add_facts_materials_routes_permissions": False,
                "hidden_reasoning_requested": False,
            },
        }

    @staticmethod
    def _semantic_task(
        intent_v2_enabled: bool, *, initial_turn: bool = False
    ) -> str:
        if initial_turn and intent_v2_enabled:
            return (
                "Classify the initial material-backed request as exactly one "
                "research_turn_intent_protocol dialogue move. Choose by the "
                "requested state change, audience, and depth, not keywords or "
                "file names. The accepted Project Goal is the referent; no "
                "prior claim exists. Do not select obligations, answer the "
                "question, add facts, or return reasoning."
            )
        if initial_turn:
            return (
                "Classify the initial material-backed request using "
                "research_turn_semantic_protocol and only its closed operation "
                "and obligation registries. The accepted Project Goal is the "
                "referent; no prior claim exists. Do not answer the question, "
                "add facts, or return hidden reasoning."
            )
        if intent_v2_enabled:
            return (
                "Classify the current interaction as exactly one "
                "research_turn_intent_protocol dialogue move. Choose by "
                "semantic intent, audience, and requested depth, not keywords "
                "or the prior answer shape. Resolve one accepted referent "
                "claim. Do not select obligations, answer the question, add "
                "facts, or return reasoning."
            )
        return (
            "Classify the current interaction using "
            "research_turn_semantic_protocol and only its closed operation and "
            "obligation registries. Apply the dialogue rules to deepen or "
            "redirect the accepted prior answer; do not merely repeat its "
            "obligation set. Resolve exactly one referent claim ID from the "
            "accepted prior claims. Do not answer the research question, add a "
            "project fact, or return hidden reasoning."
        )
