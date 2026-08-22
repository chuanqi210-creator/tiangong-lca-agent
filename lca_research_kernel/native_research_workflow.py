"""State-driven D-18 native research workflow without product-specific routing."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Mapping, Sequence

WORKFLOW_CONTRACT = "lca_research_kernel.native_research_workflow/v1"
STATE_CONTRACT = "lca_research_kernel.native_research_state/v1"
EVIDENCE_CONTRACT = "lca_research_kernel.workflow_evidence/v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_STATES = (
    "engagement_frozen",
    "goal_and_scope_bounded",
    "data_classified_and_gaps_explicit",
    "architecture_decision_framed",
    "candidate_execution_completed",
    "candidate_integrated",
    "domain_and_permission_assessed",
    "candidate_outcome_or_bounded_limitation",
)


def _sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


class NativeResearchWorkflow:
    def __init__(self, contract: Mapping[str, object]):
        self.contract = deepcopy(dict(contract))
        self.state_ids = tuple(contract["states"])  # type: ignore[arg-type]
        self.transitions = tuple(
            deepcopy(list(contract["transitions"]))  # type: ignore[arg-type]
        )
        self._by_from = {row["from_state"]: row for row in self.transitions}

    @classmethod
    def load(cls, path: Path) -> "NativeResearchWorkflow":
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("native research workflow contract is unavailable") from error
        if not isinstance(value, Mapping) or value.get("contract") != WORKFLOW_CONTRACT:
            raise ValueError("native research workflow contract is invalid")
        if tuple(value.get("states") or []) != EXPECTED_STATES:
            raise ValueError("native research workflow states drifted")
        transitions = value.get("transitions")
        if not isinstance(transitions, list) or len(transitions) != 7:
            raise ValueError("native research workflow transitions are invalid")
        owners = {
            "product_interface",
            "research_orchestrator",
            "lca_domain_kernel",
            "assurance_boundary",
        }
        for index, row in enumerate(transitions):
            if (
                not isinstance(row, Mapping)
                or row.get("from_state") != EXPECTED_STATES[index]
                or row.get("to_state") != EXPECTED_STATES[index + 1]
                or row.get("owner") not in owners
                or any(
                    not isinstance(row.get(key), str) or not row[key]
                    for key in (
                        "transition_id",
                        "required_evidence_role",
                        "expected_delta",
                    )
                )
            ):
                raise ValueError("native research workflow transition drifted")
        invariants = value.get("invariants")
        if not isinstance(invariants, Mapping) or not all(
            item is True for item in invariants.values()
        ):
            raise ValueError("native research workflow invariants are invalid")
        return cls(value)

    def next_activity(
        self,
        state: Mapping[str, object],
        *,
        available_capabilities: Sequence[Mapping[str, object]],
    ) -> dict:
        current = self._validate_state(state)
        state_id = current["state_id"]
        if state_id == EXPECTED_STATES[-1]:
            return {
                "contract": "lca_research_kernel.workflow_activity/v1",
                "status": "terminal",
                "from_state": state_id,
                "to_state": state_id,
                "selected_capabilities": [],
                "next_route": "deliver-candidate-outcome-or-bounded-limitation",
            }
        transition = self._by_from[state_id]
        required = transition["required_evidence_role"]
        if required not in current["accepted_evidence_roles"]:
            return {
                "contract": "lca_research_kernel.workflow_activity/v1",
                "status": "bounded_limitation",
                "from_state": state_id,
                "to_state": state_id,
                "missing_roles": [required],
                "selected_capabilities": [],
                "expected_state_change": transition["expected_delta"],
                "next_route": "retain-source-bound-evidence-gap",
            }
        return {
            "contract": "lca_research_kernel.workflow_activity/v1",
            "status": "ready",
            "transition_id": transition["transition_id"],
            "owner": transition["owner"],
            "from_state": state_id,
            "to_state": transition["to_state"],
            "required_evidence_role": required,
            "selected_capabilities": [],
            "expected_state_change": transition["expected_delta"],
            "next_route": transition["transition_id"],
        }

    def accept_transition(
        self,
        state: Mapping[str, object],
        evidence: Mapping[str, object],
    ) -> dict:
        current = self._validate_state(state)
        if current["state_id"] == EXPECTED_STATES[-1]:
            return current
        if (
            not isinstance(evidence, Mapping)
            or evidence.get("contract") != EVIDENCE_CONTRACT
            or evidence.get("status") != "accepted"
            or evidence.get("scientific_result") is not False
        ):
            raise ValueError("workflow transition requires non-scientific accepted evidence")
        reference = evidence.get("artifact_ref")
        if (
            not isinstance(reference, Mapping)
            or not isinstance(reference.get("path"), str)
            or not isinstance(reference.get("sha256"), str)
            or SHA256_RE.fullmatch(reference["sha256"]) is None
        ):
            raise ValueError("workflow evidence artifact reference is invalid")
        transition = self._by_from[current["state_id"]]
        if evidence.get("role") != transition["required_evidence_role"]:
            raise ValueError("workflow evidence role does not match current transition")
        accepted = list(current["accepted_evidence_roles"])
        if evidence["role"] not in accepted:
            accepted.append(evidence["role"])
        result = {
            **current,
            "state_id": transition["to_state"],
            "accepted_evidence_roles": accepted,
            "last_transition": {
                "transition_id": transition["transition_id"],
                "evidence_ref": deepcopy(dict(reference)),
                "evidence_sha256": _sha256(evidence),
            },
            "scientific_result_created": False,
        }
        result["state_sha256"] = self.state_sha256(result)
        return result

    def state_sha256(self, value: Mapping[str, object]) -> str:
        normalized = deepcopy(dict(value))
        normalized.pop("state_sha256", None)
        return _sha256(normalized)

    def _validate_state(self, value: Mapping[str, object]) -> dict:
        if (
            not isinstance(value, Mapping)
            or value.get("contract") != STATE_CONTRACT
            or value.get("state_id") not in EXPECTED_STATES
        ):
            raise ValueError("native research workflow state is invalid")
        state = deepcopy(dict(value))
        for key in (
            "accepted_evidence_roles",
            "candidate_evidence_roles",
            "missing_evidence_roles",
        ):
            rows = state.get(key)
            if not isinstance(rows, list) or any(
                not isinstance(item, str) or not item for item in rows
            ):
                raise ValueError("native research workflow evidence roles are invalid")
        reference = state.get("project_record_ref")
        if (
            not isinstance(reference, Mapping)
            or not isinstance(reference.get("last_event_id"), str)
            or not isinstance(reference.get("last_event_sha256"), str)
            or SHA256_RE.fullmatch(reference["last_event_sha256"]) is None
        ):
            raise ValueError("native research workflow Project Record reference is invalid")
        return state
