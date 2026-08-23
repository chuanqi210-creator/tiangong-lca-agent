"""Private state-change capability action planning for Research Orchestrator."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Mapping

from .model_request_compiler import TIANGONG_NATIVE_STATE_CHANGES
from .model_schema_profiles import (
    PUBLIC_SOURCE_INTENT_CONTRACT,
    PUBLIC_SOURCE_INTENT_FIELDS,
    TIANGONG_NATIVE_INTENT_CONTRACT,
    TIANGONG_NATIVE_INTENT_FIELDS,
)
from .official_operation_identity import official_public_source_operation_id
from .project_record import canonical_sha256


GENERIC_INTENT_CONTRACT = "lca_research_kernel.capability_intent/v1"


@dataclass(frozen=True)
class _PreparedCapability:
    intent: dict
    capability: dict
    operation_basis: dict
    source_intent: bool
    original_contract: str


class CapabilityActionPlanner:
    """Compile one accepted intent into one bounded D-18 action.

    This is a private implementation seam. It does not own routing authority,
    execute capabilities, or write Project Record state.
    """

    def __init__(self, host: object):
        self._host = host

    def select(
        self,
        value: object,
        project_view: Mapping[str, object],
        available: object,
        *,
        model_analysis: object = None,
    ) -> dict:
        facts = project_view.get("facts")
        if not isinstance(facts, Mapping) or not isinstance(value, Mapping):
            return self._boundary("invalid_capability_intent")

        prepared = self._prepare(value, project_view, available)
        if isinstance(prepared, dict):
            return prepared

        current_fact_ids: list[str] = []
        if prepared.source_intent:
            current_fact_ids = self._current_source_fact_ids(
                facts,
                prepared.capability,
            )
            reuse = self._public_source_reuse_route(
                facts,
                prepared,
                current_fact_ids=current_fact_ids,
                model_analysis=model_analysis,
            )
            if reuse is not None:
                return reuse

        operation_id = self._operation_id(prepared)
        fact_id = f"capability-result:{operation_id}"
        if fact_id in facts:
            return self._existing_result_route(
                facts[fact_id],
                prepared,
                fact_id=fact_id,
                current_fact_ids=current_fact_ids,
                model_analysis=model_analysis,
            )

        if prepared.source_intent:
            retry_route = self._prior_failure_route(
                operation_id,
                prepared.capability,
                current_fact_ids=current_fact_ids,
                model_analysis=model_analysis,
            )
            if retry_route is not None:
                return retry_route

        return {
            "action": "capability.execute",
            "capability_intent": prepared.intent,
            "capability": prepared.capability,
            "operation_id": operation_id,
            "fact_id": fact_id,
        }

    def _prepare(
        self,
        value: Mapping[str, object],
        project_view: Mapping[str, object],
        available: object,
    ) -> _PreparedCapability | dict:
        contract = value.get("contract")
        if contract == PUBLIC_SOURCE_INTENT_CONTRACT:
            return self._prepare_public_source(value, project_view, available)
        if contract == TIANGONG_NATIVE_INTENT_CONTRACT:
            return self._prepare_tiangong_native(value, project_view, available)
        if contract == GENERIC_INTENT_CONTRACT:
            return self._prepare_generic(value, project_view, available)
        return self._boundary("invalid_capability_intent")

    def _prepare_public_source(
        self,
        value: Mapping[str, object],
        project_view: Mapping[str, object],
        available: object,
    ) -> _PreparedCapability | dict:
        if (
            set(value) != PUBLIC_SOURCE_INTENT_FIELDS
            or any(
                not isinstance(value.get(key), str)
                or not str(value[key]).strip()
                for key in ("purpose", "method", "url", "parse_location")
            )
            or str(value["method"]).upper() not in {"GET", "HEAD"}
            or (
                value.get("extraction") is not None
                and not isinstance(value.get("extraction"), Mapping)
            )
            or not isinstance(value.get("industry_summary"), bool)
        ):
            return self._boundary("invalid_capability_intent")

        matches = self._matching_capabilities(
            available,
            authority="official_public_source",
            access="read_only",
            effect="external_read",
            public_credential_free=True,
        )
        if not matches:
            return self._boundary("capability_unavailable")
        capability = matches[0]
        failure_code = self._host._public_source_intent_failure_code(
            value,
            capability,
        )
        if failure_code is not None:
            return self._boundary(
                "invalid_capability_intent",
                failure_code=failure_code,
            )

        project_id = project_view.get("project_id")
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("public source project identity is absent")
        payload = {
            "contract": "official_public_source_request/v1",
            "question_id": project_id,
            "run_id": self._host._runtime_run_id(project_id),
            "method": str(value["method"]).upper(),
            "url": str(value["url"]),
            "parse_location": str(value["parse_location"]).strip(),
            "extraction": deepcopy(value.get("extraction")),
            "industry_summary": value["industry_summary"],
        }
        intent = {
            "contract": GENERIC_INTENT_CONTRACT,
            "purpose": str(value["purpose"]).strip(),
            "required_authority": "official_public_source",
            "required_access": "read_only",
            "effect": "external_read",
            "input": payload,
        }
        operation_basis = {
            "evidence_request": {
                key: deepcopy(payload[key])
                for key in (
                    "method",
                    "url",
                    "parse_location",
                    "extraction",
                    "industry_summary",
                )
            },
            "question_id": project_id,
            "capability_id": capability["capability_id"],
        }
        return _PreparedCapability(
            intent=intent,
            capability=capability,
            operation_basis=operation_basis,
            source_intent=True,
            original_contract=PUBLIC_SOURCE_INTENT_CONTRACT,
        )

    def _prepare_tiangong_native(
        self,
        value: Mapping[str, object],
        project_view: Mapping[str, object],
        available: object,
    ) -> _PreparedCapability | dict:
        state_change = value.get("requested_state_change")
        capability_id = TIANGONG_NATIVE_STATE_CHANGES.get(str(state_change))
        if (
            set(value) != TIANGONG_NATIVE_INTENT_FIELDS
            or not isinstance(value.get("purpose"), str)
            or not str(value["purpose"]).strip()
            or capability_id is None
        ):
            return self._boundary("invalid_capability_intent")
        matches = [
            deepcopy(dict(row))
            for row in available or []
            if isinstance(row, Mapping)
            and row.get("capability_id") == capability_id
            and row.get("provider") == "TianGong"
            and row.get("effect") == "local_read"
            and row.get("access") == "read_only"
        ]
        if len(matches) != 1:
            return self._boundary("capability_unavailable")
        capability = matches[0]
        if capability.get("runtime_available") is not True:
            failure_code = (
                "tiangong_credential_required"
                if capability.get("credential_policy")
                == "user_provisioned_identity_required"
                else "tiangong_runtime_unavailable"
            )
            return self._boundary(
                "capability_unavailable",
                capability_id=capability_id,
                failure_code=failure_code,
                recovery_route=capability.get("recovery_route"),
            )

        if state_change == "discover_lifecycle_models":
            query = self._tiangong_search_query(project_view)
            if query is None:
                return self._boundary(
                    "invalid_capability_intent",
                    failure_code="tiangong_search_query_absent",
                )
            payload = {
                "contract": capability["input_contract"],
                "query": query,
            }
        else:
            payload = {
                "contract": capability["input_contract"],
                "requested_state_change": state_change,
            }
        intent = {
            "contract": GENERIC_INTENT_CONTRACT,
            "purpose": str(value["purpose"]).strip(),
            "required_authority": capability["authority"],
            "required_access": capability["access"],
            "effect": capability["effect"],
            "input": payload,
        }
        return _PreparedCapability(
            intent=intent,
            capability=capability,
            operation_basis={
                "requested_state_change": state_change,
                "capability_id": capability_id,
                "project_id": project_view.get("project_id"),
            },
            source_intent=False,
            original_contract=TIANGONG_NATIVE_INTENT_CONTRACT,
        )

    def _prepare_generic(
        self,
        value: Mapping[str, object],
        project_view: Mapping[str, object],
        available: object,
    ) -> _PreparedCapability | dict:
        intent = deepcopy(dict(value))
        required = (
            "purpose",
            "required_authority",
            "required_access",
            "effect",
        )
        if any(
            not isinstance(intent.get(key), str) or not intent[key].strip()
            for key in required
        ) or not isinstance(intent.get("input"), Mapping):
            return self._boundary("invalid_capability_intent")
        matches = self._matching_capabilities(
            available,
            authority=intent["required_authority"],
            access=intent["required_access"],
            effect=intent["effect"],
        )
        if not matches:
            return self._boundary("capability_unavailable")
        capability = matches[0]
        if (
            capability.get("authority") == "official_public_source"
            and intent["input"].get("contract")
            == "official_public_source_request/v1"
        ):
            project_id = project_view.get("project_id")
            if not isinstance(project_id, str) or not project_id:
                raise ValueError("official source project identity is absent")
            normalized = deepcopy(dict(intent["input"]))
            normalized["question_id"] = project_id
            normalized["run_id"] = self._host._runtime_run_id(project_id)
            intent["input"] = normalized
        return _PreparedCapability(
            intent=intent,
            capability=capability,
            operation_basis={
                "intent": intent,
                "capability_id": capability["capability_id"],
            },
            source_intent=False,
            original_contract=GENERIC_INTENT_CONTRACT,
        )

    @staticmethod
    def _matching_capabilities(
        available: object,
        *,
        authority: object,
        access: object,
        effect: object,
        public_credential_free: bool | None = None,
    ) -> list[dict]:
        matches = [
            deepcopy(dict(row))
            for row in available or []
            if isinstance(row, Mapping)
            and row.get("authority") == authority
            and row.get("access") == access
            and row.get("effect") == effect
            and (
                public_credential_free is None
                or row.get("public_credential_free") is public_credential_free
            )
        ]
        return sorted(matches, key=lambda row: str(row.get("capability_id")))

    @staticmethod
    def _tiangong_search_query(
        project_view: Mapping[str, object],
    ) -> str | None:
        facts = project_view.get("facts")
        if not isinstance(facts, Mapping):
            return None
        active_turn = facts.get("active-user-turn")
        turn_value = (
            active_turn.get("value") if isinstance(active_turn, Mapping) else None
        )
        goal_fact = facts.get("research-goal")
        query = (
            turn_value.get("text")
            if isinstance(turn_value, Mapping)
            else goal_fact.get("value")
            if isinstance(goal_fact, Mapping)
            else None
        )
        return query.strip() if isinstance(query, str) and query.strip() else None

    def _current_source_fact_ids(
        self,
        facts: Mapping[str, object],
        capability: Mapping[str, object],
    ) -> list[str]:
        return sorted(
            fact_id
            for fact_id, fact in facts.items()
            if isinstance(fact_id, str)
            and isinstance(fact, Mapping)
            and fact.get("domain_kind") == "capability_result"
            and self._host._capability_result_matches_source_lock(
                fact,
                capability,
            )
        )

    def _public_source_reuse_route(
        self,
        facts: Mapping[str, object],
        prepared: _PreparedCapability,
        *,
        current_fact_ids: list[str],
        model_analysis: object,
    ) -> dict | None:
        stable_payload = {
            key: deepcopy(item)
            for key, item in prepared.intent["input"].items()
            if key != "run_id"
        }
        matching_fact_ids = sorted(
            fact_id
            for fact_id, fact in facts.items()
            if isinstance(fact_id, str)
            and isinstance(fact, Mapping)
            and fact.get("domain_kind") == "capability_result"
            and isinstance(fact.get("source"), Mapping)
            and fact["source"].get("capability_id")
            == prepared.capability.get("capability_id")
            and self._host._capability_result_matches_source_lock(
                fact,
                prepared.capability,
            )
            and isinstance(fact["source"].get("operation_payload"), Mapping)
            and {
                key: deepcopy(item)
                for key, item in fact["source"]["operation_payload"].items()
                if key != "run_id"
            }
            == stable_payload
        )
        if not matching_fact_ids:
            return None
        reintegrated = self._reintegrated_fact_ids(model_analysis)
        if isinstance(reintegrated, list) and any(
            fact_id in reintegrated for fact_id in matching_fact_ids
        ):
            return self._boundary(
                "capability_no_progress",
                failure_code="repeated_public_source_intent",
            )
        return {
            "action": "model.reintegrate",
            "capability_result_fact_ids": current_fact_ids,
            "context_capability_ids": [
                str(prepared.capability["capability_id"])
            ],
        }

    @staticmethod
    def _operation_id(prepared: _PreparedCapability) -> str:
        payload = prepared.intent.get("input")
        if (
            prepared.capability.get("authority") == "official_public_source"
            and isinstance(payload, Mapping)
            and payload.get("contract") == "official_public_source_request/v1"
        ):
            return official_public_source_operation_id(
                payload,
                capability_id=str(prepared.capability["capability_id"]),
            )
        return "capability-" + canonical_sha256(prepared.operation_basis)[:24]

    def _existing_result_route(
        self,
        fact: object,
        prepared: _PreparedCapability,
        *,
        fact_id: str,
        current_fact_ids: list[str],
        model_analysis: object,
    ) -> dict:
        if prepared.original_contract == TIANGONG_NATIVE_INTENT_CONTRACT:
            if (
                not isinstance(fact, Mapping)
                or not self._host._capability_result_matches_source_lock(
                    fact,
                    prepared.capability,
                )
            ):
                raise ValueError("TianGong native result source lock drifted")
            return {
                "action": "domain.accept_tiangong_native",
                "capability_result": deepcopy(dict(fact)),
                "capability": prepared.capability,
            }
        if prepared.source_intent:
            reintegrated = self._reintegrated_fact_ids(model_analysis)
            if isinstance(reintegrated, list) and fact_id in reintegrated:
                return self._boundary(
                    "capability_no_progress",
                    failure_code="repeated_public_source_intent",
                )
            return {
                "action": "model.reintegrate",
                "capability_result_fact_ids": current_fact_ids,
                "context_capability_ids": [
                    str(prepared.capability["capability_id"])
                ],
            }
        return {
            "action": "boundary",
            "boundary": "awaiting_domain_integration",
        }

    def _prior_failure_route(
        self,
        operation_id: str,
        capability: Mapping[str, object],
        *,
        current_fact_ids: list[str],
        model_analysis: object,
    ) -> dict | None:
        failure = self._host._prior_nonretryable_capability_failure(operation_id)
        if failure is None:
            return None
        repair_code = "capability_nonretryable:" + str(failure["failure_code"])
        prior_errors = (
            model_analysis.get("source", {}).get("domain_repair_errors")
            if isinstance(model_analysis, Mapping)
            else None
        )
        if isinstance(prior_errors, list) and repair_code in prior_errors:
            return self._boundary(
                "capability_no_progress",
                failure_code="repeated_nonretryable_capability_intent",
            )
        return {
            "action": "model.repair",
            "repair_errors": [repair_code],
            "capability_result_fact_ids": current_fact_ids,
            "context_capability_ids": [str(capability["capability_id"])],
        }

    @staticmethod
    def _reintegrated_fact_ids(model_analysis: object) -> object:
        return (
            model_analysis.get("source", {}).get(
                "reintegrated_capability_fact_ids"
            )
            if isinstance(model_analysis, Mapping)
            else None
        )

    @staticmethod
    def _boundary(boundary: str, **details: object) -> dict:
        return {
            "action": "boundary",
            "boundary": boundary,
            "details": {**details, "executed_external_effects": 0},
        }
