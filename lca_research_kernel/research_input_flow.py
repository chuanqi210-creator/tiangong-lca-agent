"""Orchestrator-owned product input transitions.

This private implementation keeps Project Record interpretation, candidate
compilation, and continuation routing behind Research Orchestrator commands.
The Product Interface validates external envelopes and presents outcomes; it
does not need to understand accepted-state internals.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Mapping, Sequence

from .content_identity import canonical_sha256
from .material_research_flow import MaterialResearchFlow


class ResearchInputFlow:
    """Compile and commit user-controlled state transitions atomically."""

    def __init__(self, orchestrator: object):
        self.orchestrator = orchestrator
        self.record = orchestrator.project_record

    def accept_product_input(
        self,
        *,
        research_id: str,
        supplied: Mapping[str, object],
    ) -> dict:
        """Accept one material fact or explicit decision, then resume."""

        self.orchestrator._require_research_id(research_id)
        normalized = deepcopy(dict(supplied))
        view = self.record.read_current_project_view()
        if normalized["kind"] == "decision" and self._is_exact_decision_replay(
            normalized, view
        ):
            return self.orchestrator.run_until_boundary(research_id)

        candidate = (
            self._material_candidate(normalized, view)
            if normalized["kind"] == "material"
            else self._decision_candidate(normalized, view)
        )
        self.orchestrator.submit_candidate_change(
            candidate,
            idempotency_key=f"product-input:{normalized['input_id']}",
        )
        return self.orchestrator.run_until_boundary(research_id)

    def accept_material_recovery(
        self,
        *,
        supplied: Mapping[str, object],
        recovery: Mapping[str, object],
        effective_material_revision_refs: Sequence[Mapping[str, object]],
    ) -> dict:
        """Accept one explicit recovery choice without starting research."""

        normalized = deepcopy(dict(supplied))
        boundary = deepcopy(dict(recovery))
        payload = normalized["payload"]
        if normalized["kind"] == "decision":
            action = payload.get("decision")
            decision_id = payload.get("decision_id")
        elif normalized["kind"] == "material":
            action = "replace_failed_materials"
            decision_id = f"{normalized['input_id']}:replace-failed-materials"
        else:
            raise ValueError("material recovery input kind is unsupported")
        allowed = boundary.get("allowed_decisions")
        if (
            not isinstance(allowed, list)
            or not isinstance(action, str)
            or action not in set(allowed)
            or not isinstance(decision_id, str)
            or not decision_id
        ):
            raise ValueError("material recovery decision is unsupported")

        accepted_decision = {
            "contract": (
                "lca_research_kernel.material_processing_recovery_decision/v1"
            ),
            "decision_id": decision_id,
            "decision": action,
            "decision_kind": "material_processing_recovery",
            "failed_materials": deepcopy(boundary["failed_materials"]),
            "effective_material_revision_refs": [
                deepcopy(dict(reference))
                for reference in effective_material_revision_refs
            ],
            "source": {
                "authority": "user_explicit_decision",
                "source_refs": deepcopy(normalized["source_refs"]),
                "input_id": normalized["input_id"],
            },
        }
        view = self.record.read_current_project_view()
        candidate = {
            "contract": "lca_research_kernel.project_candidate_change/v1",
            "candidate_id": (
                "material-recovery-" + canonical_sha256(accepted_decision)[:24]
            ),
            "change_type": "decision.recorded",
            "decision": accepted_decision,
            "next_route": view["next_route"],
            "proposed_at": normalized["received_at"],
        }
        return self.orchestrator.submit_candidate_change(
            candidate,
            idempotency_key=f"material-recovery:{normalized['input_id']}",
        )

    def advance_product_continuation(
        self,
        *,
        research_id: str,
        normalized: Mapping[str, object],
        material_bundles: Sequence[Mapping[str, object]],
        turn_sha256: str,
        turn_run_id: str,
        turn_compiler: object,
        run_budget: Mapping[str, object],
    ) -> dict:
        """Activate one exact user turn and route it to the bounded path."""

        self.orchestrator._require_research_id(research_id)
        turn = deepcopy(dict(normalized))
        bundles = [deepcopy(dict(bundle)) for bundle in material_bundles]
        view = self.record.read_current_project_view()
        if self._turn_needs_activation(turn, turn_sha256, view):
            self.orchestrator.submit_candidate_change(
                self._user_turn_candidate(
                    turn=turn,
                    turn_sha256=turn_sha256,
                    turn_run_id=turn_run_id,
                    view=view,
                ),
                idempotency_key=f"user-turn:{turn['input_id']}",
            )

        if bundles:
            current = self.record.read_current_project_view()
            request = {
                "contract": "lca_research_kernel.research_start/v1",
                "research_id": research_id,
                "title": str(current["title"]),
                "objective": turn["text"],
                "source_refs": deepcopy(turn["source_refs"]),
                "received_at": turn["received_at"],
            }
            return MaterialResearchFlow(
                self.orchestrator,
                turn_compiler=turn_compiler,
                run_budget=run_budget,
            ).advance_continuation(
                normalized=request,
                bundles=bundles,
                interaction_turn_sha256=turn_sha256,
            )

        if self.orchestrator.answer_timing is None:
            raise ValueError("product continuation answer timing is unavailable")
        self.orchestrator.answer_timing.enter_request()
        return self.orchestrator.run_until_boundary(research_id)

    def _is_exact_decision_replay(
        self,
        supplied: Mapping[str, object],
        view: Mapping[str, object],
    ) -> bool:
        for accepted_decision in view.get("decisions", []):
            source = (
                accepted_decision.get("source")
                if isinstance(accepted_decision, Mapping)
                else None
            )
            if (
                not isinstance(source, Mapping)
                or source.get("input_id") != supplied["input_id"]
            ):
                continue
            accepted_public_payload = {
                key: deepcopy(value)
                for key, value in accepted_decision.items()
                if key not in {"decision_kind", "gap_ids", "source"}
            }
            if accepted_public_payload != supplied["payload"]:
                raise ValueError("research input_id was reused with different content")
            return True
        return False

    def _material_candidate(
        self,
        supplied: Mapping[str, object],
        view: Mapping[str, object],
    ) -> dict:
        payload = supplied["payload"]
        is_gap = payload.get("contract") == "lca_research_kernel.material_gap/v1"
        fact_id = (
            f"material-gap:{payload.get('gap_id')}"
            if is_gap
            else payload.get("fact_id")
        )
        if not isinstance(fact_id, str) or not fact_id.strip():
            raise ValueError("supplied material fact_id is absent")
        if fact_id == "active-user-turn":
            raise ValueError(
                "active user turn cannot be overwritten through material supply"
            )
        return {
            "contract": "lca_research_kernel.project_candidate_change/v1",
            "candidate_id": "supplied-material-" + canonical_sha256(supplied)[:24],
            "change_type": "fact.upserted",
            "fact": {
                **deepcopy(dict(payload)),
                "fact_id": fact_id,
                "classification": "missing" if is_gap else "candidate",
                "domain_kind": "material_gap" if is_gap else "supplied_material",
                "value": deepcopy(dict(payload)) if is_gap else payload.get("value"),
                "source": {
                    "authority": "user_supplied_candidate",
                    "source_refs": deepcopy(supplied["source_refs"]),
                    "input_id": supplied["input_id"],
                },
            },
            "next_route": view["next_route"],
            "proposed_at": supplied["received_at"],
        }

    def _decision_candidate(
        self,
        supplied: Mapping[str, object],
        view: Mapping[str, object],
    ) -> dict:
        payload = deepcopy(dict(supplied["payload"]))
        if not isinstance(payload.get("decision_id"), str) or not isinstance(
            payload.get("decision"), str
        ):
            raise ValueError("supplied decision identity or value is absent")
        if payload.get("decision") == "unavailable":
            pending_gap_ids = self._pending_gap_ids(view)
            if pending_gap_ids:
                payload["decision_kind"] = "factual_data_availability"
                payload["gap_ids"] = pending_gap_ids
        return {
            "contract": "lca_research_kernel.project_candidate_change/v1",
            "candidate_id": "supplied-decision-" + canonical_sha256(supplied)[:24],
            "change_type": "decision.recorded",
            "decision": {
                **payload,
                "source": {
                    "authority": "user_explicit_decision",
                    "source_refs": deepcopy(supplied["source_refs"]),
                    "input_id": supplied["input_id"],
                },
            },
            "next_route": view["next_route"],
            "proposed_at": supplied["received_at"],
        }

    @staticmethod
    def _pending_gap_ids(view: Mapping[str, object]) -> list[str]:
        pending: list[str] = []
        facts = view.get("facts")
        if not isinstance(facts, Mapping):
            return pending
        for fact in facts.values():
            gap = (
                fact.get("value")
                if isinstance(fact, Mapping)
                and fact.get("domain_kind") == "material_gap"
                else None
            )
            gap_id = gap.get("gap_id") if isinstance(gap, Mapping) else None
            resolution = (
                facts.get("material-gap-resolution:" + gap_id)
                if isinstance(gap_id, str)
                else None
            )
            if isinstance(gap_id, str) and not isinstance(resolution, Mapping):
                pending.append(gap_id)
        return sorted(set(pending))

    @staticmethod
    def _turn_needs_activation(
        turn: Mapping[str, object],
        turn_sha256: str,
        view: Mapping[str, object],
    ) -> bool:
        prior = (view.get("facts") or {}).get("active-user-turn")
        prior_value = prior.get("value") if isinstance(prior, Mapping) else None
        if isinstance(prior_value, Mapping):
            if prior_value.get("input_id") == turn["input_id"]:
                if prior_value.get("turn_sha256") != turn_sha256:
                    raise ValueError("user turn input_id was reused with drifted content")
            else:
                prior_value = None
        return not isinstance(prior_value, Mapping) or prior_value.get(
            "turn_sha256"
        ) != turn_sha256

    @staticmethod
    def _user_turn_candidate(
        *,
        turn: Mapping[str, object],
        turn_sha256: str,
        turn_run_id: str,
        view: Mapping[str, object],
    ) -> dict:
        return {
            "contract": "lca_research_kernel.project_candidate_change/v1",
            "candidate_id": "user-turn-" + turn_sha256[:24],
            "change_type": "user_turn.activated",
            "fact": {
                "fact_id": "active-user-turn",
                "classification": "candidate",
                "domain_kind": "user_turn",
                "value": {
                    "contract": "lca_research_kernel.research_user_turn/v1",
                    "input_id": turn["input_id"],
                    "text": turn["text"],
                    "turn_sha256": turn_sha256,
                    "prior_result_sha256": (
                        canonical_sha256(view["result"])
                        if view.get("result") is not None
                        else None
                    ),
                    "turn_run_id": turn_run_id,
                },
                "source": {
                    "authority": "user_supplied_project_input",
                    "source_refs": deepcopy(turn["source_refs"]),
                    "input_id": turn["input_id"],
                },
            },
            "next_route": "reason-about-user-turn",
            "proposed_at": turn["received_at"],
        }
