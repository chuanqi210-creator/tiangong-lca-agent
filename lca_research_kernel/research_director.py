"""Continuous Research Director orchestration over formal Research Kernel truth."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
from typing import Mapping, Optional, TYPE_CHECKING

from .council_runtime import ROUTE_SIGNAL_KEYS
from .director_work_item import (
    compile_cell_request,
    compile_route_evidence,
    compile_work_item,
)
from .responsibility_boundary import (
    ResponsibilityDecision,
    classify_operation,
    compile_handoff,
    validate_responsibility_events,
)
from .workflow_registry import (
    execute_selected_workflow,
    load_workflow_registry,
    select_workflow,
    validate_workflow_registry,
    workflow_registry_identity,
)

if TYPE_CHECKING:
    from .project import ResearchProject


DIRECTOR_CONTRACT = "lca_research_kernel.research_director_contract/v1"
DIRECTOR_STATES = {
    "continue_internal",
    "dispatch_internal_work",
    "human_action_required",
    "completed",
    "bounded_blocker",
}
DIRECTOR_CONTRACT_PATH = Path("docs/research-director-contract.json")
ROUTE_EVIDENCE_FIELDS = {"level", "reason", "source_refs"}
AUTONOMOUS_RESPONSIBILITIES = {
    "local_read",
    "local_write",
    "scientific_and_engineering_judgment",
    "public_external_read",
    "internal_council_routing",
    "test_build_replay_recovery",
    "free_standard_oss_dependency",
    "local_git_checkpoint",
    "reversible_retry_replan_rollback",
    "internal_state_writeback",
}
HUMAN_RESPONSIBILITIES = {
    "credentials_or_login",
    "payment_or_subscription",
    "contract_or_nonstandard_license",
    "publication_or_public_voice",
    "external_or_shared_write",
    "external_message",
    "irreversible_action",
    "main_goal_change",
    "unsupported_strong_public_claim",
}
ALLOWED_INTERNAL_EFFECTS = {"local_read", "local_write", "external_read"}
FORBIDDEN_DIRECT_EFFECTS = {"external_write", "irreversible"}
HANDOFF_FIELDS = {
    "action_id",
    "responsibility_category",
    "target",
    "scope",
    "payload_ref",
    "payload_sha256",
    "payload_version",
    "reason",
    "expected_external_effect",
    "verification_plan",
    "compensation_plan",
    "resume_route",
}
PROJECTION_FIELDS = {
    "work_item_id",
    "result_state",
    "route",
    "agenda_revision",
    "cell_revision",
    "accepted_result_ref",
    "handoff_ref",
    "blocked_branches",
    "next_route",
}
VERTICAL_ACCEPTANCE_CASES = {
    "deterministic_fast_path",
    "disputed_decision_cell",
    "external_write_zero_effect_handoff",
    "interruption_and_replay",
}
FORBIDDEN_LIVE_STATE_FIELDS = {
    "current_goal",
    "current_route",
    "current_date",
    "current_acceptance_sha256",
}


def load_director_contract(root: Path) -> dict:
    """Load the repository's stable Research Director contract."""

    return json.loads((root / DIRECTOR_CONTRACT_PATH).read_text(encoding="utf-8"))


def _all_keys(value: object):
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield key
            yield from _all_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _all_keys(child)


def validate_director_contract(root: Path) -> list[str]:
    """Fail closed when a load-bearing Director invariant drifts."""

    try:
        contract = load_director_contract(root)
    except (OSError, json.JSONDecodeError) as error:
        return [f"Research Director contract is unreadable: {error}"]

    errors: list[str] = []
    if contract.get("contract") != DIRECTOR_CONTRACT:
        errors.append("Research Director contract identity drifted")
    if contract.get("product_entry") != "ResearchDirector.run_until_boundary":
        errors.append("Research Director product entry drifted")
    if contract.get("work_item_contract") != (
        "lca_research_kernel.research_director_work_item/v1"
    ):
        errors.append("Research Director work-item contract drifted")
    if contract.get("result_contract") != (
        "lca_research_kernel.research_director_result/v1"
    ):
        errors.append("Research Director result contract drifted")
    if set(contract.get("result_states") or []) != DIRECTOR_STATES:
        errors.append("Research Director result states drifted")

    route = contract.get("route_evidence")
    if not isinstance(route, Mapping):
        errors.append("Research Director route evidence is missing")
    else:
        if set(route.get("signal_keys") or []) != set(ROUTE_SIGNAL_KEYS):
            errors.append("Research Director Council signals drifted")
        if set(route.get("evidence_fields") or []) != ROUTE_EVIDENCE_FIELDS:
            errors.append("Research Director signal evidence fields drifted")
        if route.get("missing_evidence") != (
            "unknown_selects_bounded_decision_cell"
        ):
            errors.append("Research Director missing-evidence rule drifted")

    responsibility = contract.get("responsibility_policy")
    if not isinstance(responsibility, Mapping):
        errors.append("Research Director responsibility policy is missing")
    else:
        if set(responsibility.get("autonomous") or []) != AUTONOMOUS_RESPONSIBILITIES:
            errors.append("Research Director autonomous boundary drifted")
        if set(responsibility.get("human_owned") or []) != HUMAN_RESPONSIBILITIES:
            errors.append("Research Director human boundary drifted")

    effects = contract.get("effect_policy")
    if not isinstance(effects, Mapping):
        errors.append("Research Director effect policy is missing")
    else:
        if set(effects.get("allowed_internal_effects") or []) != ALLOWED_INTERNAL_EFFECTS:
            errors.append("Research Director internal effects drifted")
        if set(effects.get("forbidden_direct_effects") or []) != FORBIDDEN_DIRECT_EFFECTS:
            errors.append("Research Director forbidden effects drifted")

    handoff = contract.get("handoff_policy")
    if not isinstance(handoff, Mapping) or set(handoff.get("required_fields") or []) != HANDOFF_FIELDS:
        errors.append("Research Director handoff fields drifted")
    elif handoff.get("idempotency") != (
        "same_action_payload_scope_returns_same_handoff_without_history_growth"
    ):
        errors.append("Research Director handoff idempotency drifted")

    progress = contract.get("progress_policy")
    if progress != {
        "transition_cap": 8,
        "identical_no_forward_change_before_replan": 2,
        "route_equivalent_failures_before_park": 3,
        "bounded_blocker_only_when_no_independent_route_remains": True,
    }:
        errors.append("Research Director progress policy drifted")
    if contract.get("workflow_policy") != {
        "registry_contract": "lca_research_kernel.research_workflow_registry/v1",
        "registry_path": "docs/research-workflow-registry.json",
        "selection_owner": "ResearchDirector",
        "caller_selects_capability_command_or_next_route": False,
        "execution_input_modes": [
            "project_state_only",
            "director_work_item_context",
        ],
        "context_bound_fields": [
            "work_item_id",
            "work_item_content_sha256",
            "lifecycle_basis_ref",
            "authority_receipt_ref",
            "input_artifacts",
            "outcome_contract",
        ],
        "legacy_project_methods_and_cli": "diagnostic_and_recovery_only",
        "workflow_receipts_are_project_truth": False,
    }:
        errors.append("Research Director workflow policy drifted")
    if set(contract.get("projection_fields") or []) != PROJECTION_FIELDS:
        errors.append("Research Director projection fields drifted")
    if set(contract.get("vertical_acceptance_cases") or []) != VERTICAL_ACCEPTANCE_CASES:
        errors.append("Research Director vertical acceptance cases drifted")
    if FORBIDDEN_LIVE_STATE_FIELDS.intersection(_all_keys(contract)):
        errors.append("Research Director stable contract contains live state")
    return errors


INTERNAL_RESULT_CONTRACT = (
    "lca_research_kernel.research_director_internal_result/v1"
)
DIRECTOR_RESULT_CONTRACT = "lca_research_kernel.research_director_result/v1"
INTERNAL_PACKET_CONTRACT = (
    "lca_research_kernel.research_director_internal_work_packet/v1"
)


def _canonical_bytes(value: object, *, pretty: bool = False) -> bytes:
    if pretty:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _read_request(project: "ResearchProject", path: Path) -> tuple[dict, dict]:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(project.workspace.resolve())
    except ValueError as error:
        raise ValueError("Director request must be inside the Project workspace") from error
    if not resolved.is_file():
        raise ValueError("Director request is absent")
    content = resolved.read_bytes()
    try:
        value = json.loads(content)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Director request is not valid JSON") from error
    if not isinstance(value, dict):
        raise ValueError("Director request must be an object")
    return value, {
        "path": relative.as_posix(),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _load_work_item_ref(project: "ResearchProject", reference: object) -> tuple[dict, Path]:
    if not isinstance(reference, Mapping) or set(reference) != {"path", "sha256"}:
        raise ValueError("internal result work_item_ref is invalid")
    raw_path = reference.get("path")
    if not isinstance(raw_path, str):
        raise ValueError("internal result work_item_ref path is invalid")
    path = (project.workspace / raw_path).resolve()
    try:
        path.relative_to(project.workspace.resolve())
    except ValueError as error:
        raise ValueError("internal result work_item_ref escapes the Project workspace") from error
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != reference.get("sha256"):
        raise RuntimeError("internal result work_item_ref drifted")
    value, _ = _read_request(project, path)
    return value, path


def restore_director_truth(project: "ResearchProject") -> dict:
    """Replay Council, lifecycle, and Artifact Bus truth from accepted events."""

    from .council_project import (
        initial_council_projection,
        reduce_council_event,
        validate_project_council_events,
    )
    from .lifecycle import project_lifecycle_events

    events = list(project.events())
    errors = validate_project_council_events(project, events)
    if errors:
        raise ValueError("formal Council event history is invalid: " + "; ".join(errors))
    council = initial_council_projection()
    registered = {}
    for event in events:
        if event.get("event_type") == "artifact.registered" and isinstance(
            event.get("payload"), Mapping
        ):
            payload = event["payload"]
            if isinstance(payload.get("path"), str):
                registered[payload["path"]] = dict(payload)
        reduce_council_event(project, council, event)
    return {
        "event_count": len(events),
        "council": council,
        "lifecycle": project_lifecycle_events(events),
        "registered_artifacts": registered,
    }


def _registered_value(
    project: "ResearchProject", registration: Mapping[str, object]
) -> dict:
    relative = registration.get("workspace_path")
    if not isinstance(relative, str):
        raise RuntimeError("Director registration lacks workspace_path")
    path = (project.workspace / relative).resolve()
    try:
        path.relative_to(project.workspace.resolve())
    except ValueError as error:
        raise RuntimeError("Director registration escapes the Project workspace") from error
    if not path.is_file():
        raise RuntimeError("Director registered artifact is missing")
    content = path.read_bytes()
    if (
        hashlib.sha256(content).hexdigest() != registration.get("sha256")
        or len(content) != registration.get("bytes")
    ):
        raise RuntimeError("Director registered artifact drifted")
    try:
        value = json.loads(content)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Director registered artifact is invalid JSON") from error
    if not isinstance(value, dict):
        raise RuntimeError("Director registered artifact must be an object")
    return value


def project_director_events(
    project: "ResearchProject", events: list[Mapping[str, object]]
) -> Optional[dict]:
    """Derive Director status only when Director artifacts or handoffs exist."""

    errors = validate_responsibility_events(events, project)
    if errors:
        raise ValueError("responsibility event history is invalid: " + "; ".join(errors))
    relevant_registrations = []
    latest_by_role: dict[str, tuple[Mapping[str, object], dict]] = {}
    for event in events:
        if event.get("event_type") != "artifact.registered":
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        role = payload.get("artifact_role")
        if role not in {
            "director_internal_packet",
            "director_failure_observation",
            "responsibility_handoff",
        }:
            continue
        value = _registered_value(project, payload)
        latest_by_role[str(role)] = (payload, value)
        relevant_registrations.append((payload, value))
    handoff_event = next(
        (
            event
            for event in reversed(events)
            if event.get("event_type")
            == "research.responsibility.handoff_prepared"
        ),
        None,
    )
    if not relevant_registrations and handoff_event is None:
        return None

    packet_row = latest_by_role.get("director_internal_packet")
    failure_row = latest_by_role.get("director_failure_observation")
    packet = packet_row[1] if packet_row is not None else {}
    failure = failure_row[1] if failure_row is not None else {}
    work_item_id = str(
        (handoff_event or {}).get("payload", {}).get("work_item_id")
        or packet.get("work_item_id")
        or failure.get("work_item_id")
        or "unknown"
    )
    work_item_revision = str(
        (handoff_event or {}).get("payload", {}).get("work_item_revision")
        or packet.get("work_item_content_sha256")
        or "unknown"
    )
    route = packet.get("route")
    no_progress = 0
    if failure:
        fingerprint = failure.get("failure_fingerprint")
        no_progress = sum(
            value.get("failure_class") == "no_progress"
            and value.get("failure_fingerprint") == fingerprint
            for payload, value in relevant_registrations
            if payload.get("artifact_role") == "director_failure_observation"
        )

    human_handoff_ref = None
    if handoff_event is not None:
        human_handoff_ref = dict(handoff_event["payload"]["handoff_ref"])
        state = "human_action_required"
        next_route = handoff_event["payload"]["next_route"]
    else:
        accepted = any(
            event.get("event_type") == "research.council.outcome_accepted"
            and isinstance(event.get("payload"), Mapping)
            and event["payload"].get("agenda_revision")
            == packet.get("agenda_revision")
            for event in events
        )
        if accepted:
            state = "completed"
            next_route = "compile-next-council-agenda"
        elif failure and sum(
            value.get("failure_fingerprint") == failure.get("failure_fingerprint")
            for payload, value in relevant_registrations
            if payload.get("artifact_role") == "director_failure_observation"
        ) >= 3 and not failure.get("independent_branches"):
            state = "bounded_blocker"
            next_route = "park-bounded-director-blocker"
        else:
            state = "dispatch_internal_work"
            next_route = (
                failure.get("next_route")
                if failure
                else "resume-research-director-with-internal-result"
            )

    latest_registration = relevant_registrations[-1][0]
    last_ref = {
        "role": latest_registration["artifact_role"],
        "path": latest_registration["path"],
        "sha256": latest_registration["sha256"],
        "bytes": latest_registration["bytes"],
    }
    return {
        "contract": "lca_research_kernel.research_director_projection/v1",
        "work_item_id": work_item_id,
        "work_item_revision": work_item_revision,
        "route": route,
        "state": state,
        "last_forward_artifact_ref": last_ref,
        "no_progress_rounds": no_progress,
        "human_handoff_ref": human_handoff_ref,
        "next_route": next_route,
    }


class ResearchDirector:
    """One product entry that advances existing Council ports to a true boundary."""

    def __init__(self, project: "ResearchProject") -> None:
        self.project = project

    def _write_request(self, work_item_id: str, operation: str, value: Mapping[str, object]) -> Path:
        digest = _sha256(value)
        relative = (
            Path("director")
            / "requests"
            / work_item_id
            / f"{operation}-{digest[:16]}.json"
        )
        path = self.project.workspace / relative
        content = _canonical_bytes(value, pretty=True)
        if path.exists():
            if not path.is_file() or path.read_bytes() != content:
                raise RuntimeError(f"deterministic Director request drifted: {relative}")
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, path)
        return path

    def _persist_director_artifact(
        self, work_item_id: str, role: str, value: Mapping[str, object]
    ) -> dict:
        content = _canonical_bytes(value, pretty=True)
        digest = hashlib.sha256(content).hexdigest()
        relative = (
            Path("director") / "artifacts" / role / f"{digest[:16]}.json"
        )
        path = self.project.workspace / relative
        if path.exists():
            if not path.is_file() or path.read_bytes() != content:
                raise RuntimeError(f"Director artifact drifted: {relative}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.tmp")
            temporary.write_bytes(content)
            os.replace(temporary, path)
        artifact_id = f"director-{role}-{digest[:16]}"
        payload = {
            "artifact_id": artifact_id,
            "activity_id": f"research-director:{work_item_id}",
            "skill_ref": "run-lca-research-project",
            "kind": "director_runtime_artifact",
            "artifact_role": role,
            "path": relative.as_posix(),
            "workspace_path": relative.as_posix(),
            "sha256": digest,
            "bytes": len(content),
        }
        self.project.record(
            "artifact.registered",
            payload,
            actor="controller",
            idempotency_key=f"artifact:{artifact_id}",
        )
        self.project.materialize_views()
        return {
            "role": role,
            "path": relative.as_posix(),
            "sha256": digest,
            "bytes": len(content),
        }

    def _registered_director_values(self, role: str) -> list[dict]:
        values = []
        seen = set()
        for event in self.project.events():
            if event.get("event_type") != "artifact.registered":
                continue
            payload = event.get("payload")
            if not isinstance(payload, Mapping) or payload.get("artifact_role") != role:
                continue
            relative = payload.get("workspace_path")
            if not isinstance(relative, str) or relative in seen:
                continue
            path = (self.project.workspace / relative).resolve()
            try:
                path.relative_to(self.project.workspace.resolve())
            except ValueError as error:
                raise RuntimeError("registered Director artifact escapes the Project workspace") from error
            if not path.is_file():
                raise RuntimeError("registered Director artifact is missing")
            content = path.read_bytes()
            if (
                hashlib.sha256(content).hexdigest() != payload.get("sha256")
                or len(content) != payload.get("bytes")
            ):
                raise RuntimeError("registered Director artifact drifted")
            try:
                value = json.loads(content)
            except (UnicodeError, json.JSONDecodeError) as error:
                raise RuntimeError("registered Director artifact is invalid JSON") from error
            if not isinstance(value, dict):
                raise RuntimeError("registered Director artifact must be an object")
            seen.add(relative)
            values.append(value)
        return values

    def _persist_handoff(
        self,
        work_item_id: str,
        work_item_revision: str,
        handoff: Mapping[str, object],
    ) -> dict:
        for event in self.project.events():
            if event.get("event_type") != "research.responsibility.handoff_prepared":
                continue
            payload = event.get("payload")
            if not isinstance(payload, Mapping) or payload.get("action_id") != handoff.get("action_id"):
                continue
            reference = payload.get("handoff_ref")
            if not isinstance(reference, Mapping):
                raise RuntimeError("existing responsibility handoff reference is invalid")
            registration = next(
                (
                    row.get("payload")
                    for row in self.project.events()
                    if row.get("event_type") == "artifact.registered"
                    and isinstance(row.get("payload"), Mapping)
                    and row["payload"].get("path") == reference.get("path")
                ),
                None,
            )
            if not isinstance(registration, Mapping):
                raise RuntimeError("existing responsibility handoff is unregistered")
            existing = _registered_value(self.project, registration)
            if existing != dict(handoff):
                raise ValueError("changed payload or scope requires a new action_id")
            return dict(reference)

        content = _canonical_bytes(handoff, pretty=True)
        digest = hashlib.sha256(content).hexdigest()
        relative = (
            Path("director")
            / "artifacts"
            / "responsibility_handoff"
            / f"{digest[:16]}.json"
        )
        artifact_id = f"director-responsibility-handoff-{digest[:16]}"
        artifact_payload = {
            "artifact_id": artifact_id,
            "activity_id": f"research-director:{work_item_id}",
            "skill_ref": "run-lca-research-project",
            "kind": "director_runtime_artifact",
            "artifact_role": "responsibility_handoff",
            "path": relative.as_posix(),
            "workspace_path": relative.as_posix(),
            "sha256": digest,
            "bytes": len(content),
        }
        handoff_ref = {
            "role": "responsibility_handoff",
            "path": relative.as_posix(),
            "sha256": digest,
            "bytes": len(content),
        }
        event_payload = {
            "contract": "lca_research_kernel.responsibility_handoff_prepared/v1",
            "work_item_id": work_item_id,
            "work_item_revision": work_item_revision,
            "action_id": handoff["action_id"],
            "responsibility_category": handoff["responsibility_category"],
            "handoff_ref": handoff_ref,
            "executed_external_effects": 0,
            "next_route": handoff["resume_route"],
        }
        candidate = list(self.project.events())
        sequence = len(candidate)
        candidate.extend(
            [
                {
                    "event_id": f"preflight-{sequence + 1}",
                    "sequence": sequence + 1,
                    "event_type": "artifact.registered",
                    "occurred_at": "preflight",
                    "actor": "controller",
                    "idempotency_key": f"artifact:{artifact_id}",
                    "payload": artifact_payload,
                },
                {
                    "event_id": f"preflight-{sequence + 2}",
                    "sequence": sequence + 2,
                    "event_type": "research.responsibility.handoff_prepared",
                    "occurred_at": "preflight",
                    "actor": "controller",
                    "idempotency_key": f"responsibility-handoff:{handoff['action_id']}:{digest[:16]}",
                    "payload": event_payload,
                },
            ]
        )
        errors = validate_responsibility_events(candidate)
        if errors:
            raise ValueError("responsibility handoff preflight failed: " + "; ".join(errors))
        path = self.project.workspace / relative
        if path.exists():
            if not path.is_file() or path.read_bytes() != content:
                raise RuntimeError("responsibility handoff artifact path drifted")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.tmp")
            temporary.write_bytes(content)
            os.replace(temporary, path)
        self.project.record(
            "artifact.registered",
            artifact_payload,
            actor="controller",
            idempotency_key=f"artifact:{artifact_id}",
        )
        self.project.record(
            "research.responsibility.handoff_prepared",
            event_payload,
            actor="controller",
            idempotency_key=f"responsibility-handoff:{handoff['action_id']}:{digest[:16]}",
        )
        errors = validate_responsibility_events(
            list(self.project.events()), self.project
        )
        if errors:
            raise RuntimeError("persisted responsibility handoff failed validation: " + "; ".join(errors))
        self.project.materialize_views()
        return handoff_ref

    def _packet(self, body: Mapping[str, object]) -> dict:
        digest = _sha256(body)
        return {
            **body,
            "packet_id": f"packet-{digest[:24]}",
            "packet_sha256": digest,
        }

    def _register_packet(self, work_item_id: str, packet: Mapping[str, object]) -> dict:
        reference = self._persist_director_artifact(
            work_item_id, "director_internal_packet", packet
        )
        return {**packet, "registered_packet_ref": reference}

    def _resolve_packet(
        self,
        initial: Mapping[str, object],
        packet_id: object,
        packet_sha256: object,
        work_item_ref: Mapping[str, object],
    ) -> dict:
        if (
            initial.get("packet_id") == packet_id
            and initial.get("packet_sha256") == packet_sha256
        ):
            return dict(initial)
        for packet in self._registered_director_values("director_internal_packet"):
            if packet.get("packet_id") != packet_id or packet.get("packet_sha256") != packet_sha256:
                continue
            if packet.get("work_item_ref") != dict(work_item_ref):
                raise ValueError("registered Director packet belongs to another work item")
            return packet
        raise ValueError("Research Director internal result binds a stale packet")

    def _result(self, *, state: str, route: Optional[str], work_item_id: str, **fields: object) -> dict:
        if state not in DIRECTOR_STATES:
            raise ValueError("Research Director result state is unsupported")
        body = {
            "contract": DIRECTOR_RESULT_CONTRACT,
            "state": state,
            "requires_human": state == "human_action_required",
            "route": route,
            "work_item_id": work_item_id,
            **fields,
        }
        return {**body, "result_content_sha256": _sha256(body)}

    def _accepted_outcome(self, agenda_revision: str) -> Optional[dict]:
        for event in reversed(list(self.project.events())):
            if event.get("event_type") != "research.council.outcome_accepted":
                continue
            payload = event.get("payload")
            if isinstance(payload, Mapping) and payload.get("agenda_revision") == agenda_revision:
                return dict(payload)
        return None

    def _prepare_start(self, work_item: Mapping[str, object], work_item_ref: Mapping[str, object]) -> dict:
        compiled = compile_work_item(self.project, work_item)
        work_item_id = str(compiled["work_item_id"])
        responsibility = classify_operation(compiled["operation_intent"])
        if responsibility.disposition == "prepare_handoff":
            handoff = compile_handoff(compiled["operation_intent"], responsibility)
            handoff_ref = self._persist_handoff(
                work_item_id,
                str(compiled["work_item_content_sha256"]),
                handoff,
            )
            return self._result(
                state="human_action_required",
                route=None,
                work_item_id=work_item_id,
                handoff=handoff,
                handoff_ref=handoff_ref,
                next_route=handoff["resume_route"],
                transition_count=0,
            )

        repository_root = Path(__file__).resolve().parents[1]
        registry_errors = validate_workflow_registry(repository_root)
        if registry_errors:
            raise ValueError(
                "stable workflow registry is invalid: " + "; ".join(registry_errors)
            )
        registry = load_workflow_registry(repository_root)
        workflow = select_workflow(
            registry,
            compiled,
            self.project.materialize_views(),
        )

        core = compiled["agenda"]["research_agenda"]
        agenda_request = {
            "goal": core["goal"],
            "authority_receipt_ref": core["authority_receipt_ref"],
            "facts": core["facts"],
            "hypotheses": core["hypotheses"],
            "risks": core["risks"],
            "plan": core["plan"],
        }
        agenda_payload = self.project.compile_council_agenda(
            self._write_request(work_item_id, "compile-agenda", agenda_request)
        )
        route_request = {
            "contract": "lca_research_kernel.council_route_request/v1",
            "agenda_ref": agenda_payload["agenda_ref"],
            "signals": compiled["signals"],
        }
        route_payload = self.project.route_council_decision(
            self._write_request(work_item_id, "select-route", route_request)
        )
        accepted = self._accepted_outcome(str(agenda_payload["agenda_revision"]))
        if accepted is not None:
            return self._result(
                state="completed",
                route=str(route_payload["route"]),
                work_item_id=work_item_id,
                accepted_result_ref=accepted["outcome_ref"],
                next_route=(
                    workflow["post_completion_next_route"]
                    if workflow is not None
                    else "compile-next-council-agenda"
                ),
                transition_count=3,
            )

        allowed_effects = [compiled["operation_intent"]["effect"]]
        common = {
            "contract": INTERNAL_PACKET_CONTRACT,
            "work_item_id": work_item_id,
            "work_item_ref": dict(work_item_ref),
            "work_item_content_sha256": compiled["work_item_content_sha256"],
            "agenda_ref": agenda_payload["agenda_ref"],
            "agenda_revision": agenda_payload["agenda_revision"],
            "route_ref": route_payload["route_decision_ref"],
            "route": route_payload["route"],
            "input_refs": compiled["input_artifacts"],
            "allowed_effects": allowed_effects,
            "output_contract": compiled["outcome_contract"]["contract"],
            "stop_condition": compiled["stop_policy"]["worker_stop_condition"],
        }
        if workflow is not None:
            if route_payload["route"] != "single_controller_fast_path":
                raise ValueError(
                    "stable workflow requires source-bound single-controller fast path evidence"
                )
            execution_context = {
                "work_item_id": compiled["work_item_id"],
                "work_item_content_sha256": compiled["work_item_content_sha256"],
                "lifecycle_basis_ref": compiled["lifecycle_basis_ref"],
                "authority_receipt_ref": compiled["authority_receipt_ref"],
                "input_artifacts": compiled["input_artifacts"],
                "outcome_contract": compiled["outcome_contract"],
            }
            workflow_result = execute_selected_workflow(
                project=self.project,
                registry=registry,
                registry_identity=workflow_registry_identity(repository_root),
                workflow=workflow,
                work_item_id=work_item_id,
                work_item_content_sha256=str(
                    compiled["work_item_content_sha256"]
                ),
                existing_receipts=self._registered_director_values(
                    "director_workflow_receipt"
                ),
                persist_receipt=self._persist_director_artifact,
                execution_context=execution_context,
            )
            workflow_receipt_ref = self._persist_director_artifact(
                work_item_id,
                "director_workflow_receipt",
                workflow_result["workflow_receipt"],
            )
            accepted = self._complete_fast_path(
                work_item_id,
                {
                    **common,
                    "route": "single_controller_fast_path",
                },
                {
                    "statement": (
                        "The source-bound stable research workflow completed all "
                        "declared acceptance predicates."
                    ),
                    "result": {
                        "kind": "fast_path_result",
                        "value": {
                            key: value
                            for key, value in workflow_result.items()
                            if key != "workflow_receipt"
                        }
                        | {"workflow_receipt_ref": workflow_receipt_ref},
                    },
                },
            )
            return self._result(
                state="completed",
                route="single_controller_fast_path",
                work_item_id=work_item_id,
                accepted_result_ref=accepted["outcome_ref"],
                next_route=workflow["post_completion_next_route"],
                transition_count=3,
            )
        if route_payload["route"] == "single_controller_fast_path":
            packet = self._packet(
                {**common, "kind": "execute_local_operation", "operation_intent": compiled["operation_intent"]}
            )
        else:
            cell_request = {
                "contract": "lca_research_kernel.council_cell_request/v1",
                "agenda_ref": agenda_payload["agenda_ref"],
                "authority_ref": compiled["authority_receipt_ref"],
                "route_ref": route_payload["route_decision_ref"],
                "cell_request": compiled["cell_request"],
            }
            cell_payload = self.project.open_council_cell(
                self._write_request(work_item_id, "open-cell", cell_request)
            )
            packet = self._packet(
                {
                    **common,
                    "kind": "execute_decision_cell",
                    "cell_ref": cell_payload["cell_ref"],
                    "cell_revision": cell_payload["cell_revision"],
                    "responsibilities": compiled["cell_request"]["roles"],
                    "validation_conditions": compiled["cell_request"]["validation_conditions"],
                    "claims": compiled["cell_request"]["claims"],
                    "flip_conditions": compiled["cell_request"]["flip_conditions"],
                }
            )
        packet = self._register_packet(work_item_id, packet)
        return self._result(
            state="dispatch_internal_work",
            route=str(route_payload["route"]),
            work_item_id=work_item_id,
            directive=packet,
            next_route="resume-research-director-with-internal-result",
            transition_count=4 if route_payload["route"] != "single_controller_fast_path" else 3,
        )

    def _continuation_packet(
        self,
        work_item_id: str,
        packet: Mapping[str, object],
        *,
        progress_action: str,
        selected_independent_branch: Optional[str] = None,
    ) -> dict:
        generated = {"packet_id", "packet_sha256", "registered_packet_ref"}
        body = {key: value for key, value in packet.items() if key not in generated}
        body.update(
            {
                "kind": (
                    "execute_independent_branch"
                    if selected_independent_branch is not None
                    else "retry_or_replan_internal_work"
                ),
                "continuation_of_packet_id": packet.get("packet_id"),
                "progress_action": progress_action,
            }
        )
        if selected_independent_branch is not None:
            body["selected_independent_branch"] = selected_independent_branch
        return self._register_packet(work_item_id, self._packet(body))

    def _failure_observation(
        self,
        work_item_id: str,
        packet: Mapping[str, object],
        value: object,
    ) -> tuple[dict, int]:
        required = {
            "kind",
            "attempt_id",
            "failure_class",
            "observed_outcome",
            "next_route",
            "branch_id",
            "independent_branches",
            "forward_artifact_refs",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise ValueError("Director failure observation fields are invalid")
        if value.get("kind") != "failure_observation":
            raise ValueError("Director failure observation kind is unsupported")
        failure_class = value.get("failure_class")
        if failure_class not in {"transient_failure", "no_progress", "route_failure"}:
            raise ValueError("Director failure class is unsupported")
        for key in ("attempt_id", "observed_outcome", "next_route", "branch_id"):
            if not isinstance(value.get(key), str) or not str(value.get(key)).strip():
                raise ValueError(f"Director failure {key} must be a non-empty string")
        independent = value.get("independent_branches")
        if not isinstance(independent, list) or any(
            not isinstance(row, str) or not row.strip() for row in independent
        ):
            raise ValueError("Director independent_branches must be strings")
        forward = value.get("forward_artifact_refs")
        if not isinstance(forward, list):
            raise ValueError("Director forward_artifact_refs must be a list")
        fingerprint_basis = {
            "work_item_content_sha256": packet.get("work_item_content_sha256"),
            "route": packet.get("route"),
            "input_refs": packet.get("input_refs"),
            "observed_outcome": value["observed_outcome"],
            "failure_class": failure_class,
            "next_route": value["next_route"],
            "forward_artifact_refs": forward,
        }
        fingerprint = _sha256(fingerprint_basis)
        observation = {
            "contract": "lca_research_kernel.research_director_failure_observation/v1",
            "work_item_id": work_item_id,
            "packet_id": packet.get("packet_id"),
            "attempt_id": value["attempt_id"],
            "failure_fingerprint": fingerprint,
            "failure_class": failure_class,
            "observed_outcome": value["observed_outcome"],
            "next_route": value["next_route"],
            "branch_id": value["branch_id"],
            "independent_branches": list(independent),
            "forward_artifact_refs": list(forward),
        }
        self._persist_director_artifact(
            work_item_id, "director_failure_observation", observation
        )
        count = sum(
            row.get("failure_fingerprint") == fingerprint
            for row in self._registered_director_values(
                "director_failure_observation"
            )
        )
        return observation, count

    def _handle_failure(
        self,
        work_item_id: str,
        packet: Mapping[str, object],
        value: object,
    ) -> dict:
        observation, count = self._failure_observation(
            work_item_id, packet, value
        )
        failure_class = str(observation["failure_class"])
        if count == 1:
            action = "retry"
        elif count == 2:
            action = "replan"
        else:
            independent = observation["independent_branches"]
            if independent:
                selected = sorted(independent)[0]
                directive = self._continuation_packet(
                    work_item_id,
                    packet,
                    progress_action="park_and_continue",
                    selected_independent_branch=selected,
                )
                return self._result(
                    state="dispatch_internal_work",
                    route=str(packet.get("route")),
                    work_item_id=work_item_id,
                    directive=directive,
                    progress_action="park_and_continue",
                    failure_count=count,
                    blocked_branches=[observation["branch_id"]],
                    selected_independent_branch=selected,
                    next_route="execute-independent-director-branch",
                    transition_count=1,
                )
            return self._result(
                state="bounded_blocker",
                route=str(packet.get("route")),
                work_item_id=work_item_id,
                failure_count=count,
                failure_class=failure_class,
                blocked_branches=[observation["branch_id"]],
                exhausted_alternatives=True,
                next_route="park-bounded-director-blocker",
                transition_count=1,
            )
        directive = self._continuation_packet(
            work_item_id, packet, progress_action=action
        )
        return self._result(
            state="dispatch_internal_work",
            route=str(packet.get("route")),
            work_item_id=work_item_id,
            directive=directive,
            progress_action=action,
            failure_count=count,
            next_route=(
                "retry-current-internal-work"
                if action == "retry"
                else "replan-current-internal-work"
            ),
            transition_count=1,
        )

    def _handle_goal_change(
        self, work_item_id: str, packet: Mapping[str, object], value: object
    ) -> dict:
        required = {
            "kind",
            "source",
            "observed_at",
            "expected_goal",
            "observed_goal",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise ValueError("Goal-change observation fields are invalid")
        if value.get("kind") != "goal_change_observation":
            raise ValueError("Goal-change observation kind is unsupported")
        if value.get("expected_goal") == value.get("observed_goal"):
            raise ValueError("Goal-change observation does not report a change")
        return self._result(
            state="human_action_required",
            route=str(packet.get("route")),
            work_item_id=work_item_id,
            responsibility_category="main_goal_change",
            goal_observation=dict(value),
            executed_external_effects=0,
            next_route="await-main-goal-rebaseline",
            transition_count=0,
        )

    @staticmethod
    def _indexes(values: list[dict], indexes: object, label: str) -> list[str]:
        if not isinstance(indexes, list) or not indexes or any(
            isinstance(index, bool) or not isinstance(index, int) for index in indexes
        ):
            raise ValueError(f"{label} must be a non-empty list of indexes")
        try:
            return [str(values[index]["worker_result_id"]) for index in indexes]
        except (IndexError, KeyError) as error:
            raise ValueError(f"{label} contains an invalid result index") from error

    def _complete_cell(self, work_item_id: str, packet: Mapping[str, object], result: Mapping[str, object]) -> dict:
        if result.get("kind") != "cell_batch_result":
            raise ValueError("Decision Cell internal result kind is unsupported")
        raw_results = result.get("worker_results")
        if not isinstance(raw_results, list) or not raw_results:
            raise ValueError("Decision Cell internal result requires worker_results")
        registered = []
        for index, worker in enumerate(raw_results):
            request = {
                "contract": "lca_research_kernel.council_worker_result_request/v1",
                "cell_ref": packet["cell_ref"],
                "result": worker,
            }
            registered.append(
                self.project.register_council_worker_result(
                    self._write_request(work_item_id, f"register-worker-{index + 1}", request)
                )
            )
        incumbent = result.get("incumbent")
        if not isinstance(incumbent, Mapping):
            raise ValueError("Decision Cell internal result requires an incumbent")
        validation = incumbent.get("validation")
        if not isinstance(validation, Mapping):
            raise ValueError("Decision Cell incumbent validation is missing")
        formal_results = []
        for row in validation.get("results", []):
            if not isinstance(row, Mapping):
                raise ValueError("Decision Cell validation result is invalid")
            formal_results.append(
                {
                    "condition_id": row.get("condition_id"),
                    "status": row.get("status"),
                    "evidence_worker_result_ids": self._indexes(
                        registered,
                        row.get("evidence_result_indexes"),
                        "validation evidence_result_indexes",
                    ),
                }
            )
        acceptance_indexes = [validation.get("independent_acceptance_result_index")]
        acceptance_id = self._indexes(
            registered, acceptance_indexes, "independent acceptance result index"
        )[0]
        formal_incumbent = {
            "candidate_id": incumbent.get("candidate_id"),
            "conclusion": incumbent.get("conclusion"),
            "claim_ids": incumbent.get("claim_ids"),
            "evidence_worker_result_ids": self._indexes(
                registered,
                incumbent.get("evidence_result_indexes"),
                "incumbent evidence_result_indexes",
            ),
            "dissent_worker_result_ids": self._indexes(
                registered,
                incumbent.get("dissent_result_indexes"),
                "incumbent dissent_result_indexes",
            ),
            "validation": {
                "declared_conditions": validation.get("declared_conditions"),
                "results": formal_results,
                "independent_acceptance_worker_result_id": acceptance_id,
            },
        }
        advance_request = {
            "contract": "lca_research_kernel.council_advance_request/v1",
            "cell_ref": packet["cell_ref"],
            "worker_result_refs": [row["worker_result_ref"] for row in registered],
            "incumbent_candidate": formal_incumbent,
        }
        update = self.project.advance_council_cell(
            self._write_request(work_item_id, "advance-cell", advance_request)
        )
        close_request = {
            "contract": "lca_research_kernel.council_close_request/v1",
            "cell_ref": packet["cell_ref"],
            "cell_update_ref": update["cell_update_ref"],
            "close_reason": result.get("close_reason"),
        }
        closed = self.project.close_council_cell(
            self._write_request(work_item_id, "close-cell", close_request)
        )
        accept_request = {
            "contract": "lca_research_kernel.council_accept_request/v1",
            "agenda_ref": packet["agenda_ref"],
            "route_ref": packet["route_ref"],
            "cell_ref": packet["cell_ref"],
            "accepted_result_ref": closed["cell_close_ref"],
        }
        return self.project.accept_council_outcome(
            self._write_request(work_item_id, "accept-outcome", accept_request)
        )

    def _complete_fast_path(self, work_item_id: str, packet: Mapping[str, object], request: Mapping[str, object]) -> dict:
        result = request.get("result")
        if not isinstance(result, Mapping) or result.get("kind") != "fast_path_result" or not isinstance(result.get("value"), Mapping):
            raise ValueError("fast-path internal result is unsupported")
        completion = {
            "contract": "lca_research_kernel.council_fast_path_request/v1",
            "agenda_ref": packet["agenda_ref"],
            "route_ref": packet["route_ref"],
            "source_refs": packet["input_refs"],
            "allowed_effects": packet["allowed_effects"],
            "statement": request.get("statement"),
            "result": result["value"],
        }
        completed = self.project.complete_council_fast_path(
            self._write_request(work_item_id, "complete-fast-path", completion)
        )
        acceptance = {
            "contract": "lca_research_kernel.council_accept_request/v1",
            "agenda_ref": packet["agenda_ref"],
            "route_ref": packet["route_ref"],
            "cell_ref": None,
            "accepted_result_ref": completed["result_ref"],
        }
        return self.project.accept_council_outcome(
            self._write_request(work_item_id, "accept-outcome", acceptance)
        )

    def advance(self, request_path: Path) -> dict:
        """Advance one work item or typed internal result through existing ports."""

        request, request_ref = _read_request(self.project, request_path)
        restore_director_truth(self.project)
        if request.get("contract") == "lca_research_kernel.research_director_work_item/v1":
            return self._prepare_start(request, request_ref)
        if request.get("contract") != INTERNAL_RESULT_CONTRACT:
            raise ValueError("Research Director request contract is unsupported")
        if set(request) != {
            "contract",
            "work_item_ref",
            "packet_id",
            "packet_sha256",
            "status",
            "statement",
            "result",
        }:
            raise ValueError("Research Director internal result fields are invalid")
        status = request.get("status")
        if status not in {"completed", "failed", "goal_changed"}:
            raise ValueError("Research Director internal result status is unsupported")
        work_item, work_item_path = _load_work_item_ref(
            self.project, request.get("work_item_ref")
        )
        started = self._prepare_start(work_item, request["work_item_ref"])
        if started["state"] == "completed":
            return started
        packet = started.get("directive")
        if not isinstance(packet, Mapping):
            return started
        packet = self._resolve_packet(
            packet,
            request.get("packet_id"),
            request.get("packet_sha256"),
            request["work_item_ref"],
        )
        work_item_id = str(started["work_item_id"])
        if status == "failed":
            return self._handle_failure(work_item_id, packet, request.get("result"))
        if status == "goal_changed":
            return self._handle_goal_change(
                work_item_id, packet, request.get("result")
            )
        if packet["route"] == "single_controller_fast_path":
            accepted = self._complete_fast_path(work_item_id, packet, request)
        else:
            result = request.get("result")
            if not isinstance(result, Mapping):
                raise ValueError("Decision Cell internal result must be an object")
            accepted = self._complete_cell(work_item_id, packet, result)
        return self._result(
            state="completed",
            route=str(packet["route"]),
            work_item_id=work_item_id,
            accepted_result_ref=accepted["outcome_ref"],
            next_route="compile-next-council-agenda",
            transition_count=3,
        )

    def run_until_boundary(
        self, request_path: Path, *, max_transitions: int = 32
    ) -> dict:
        """Advance deterministic work and stop only at a typed execution boundary."""

        if isinstance(max_transitions, bool) or not isinstance(max_transitions, int) or max_transitions <= 0:
            raise ValueError("max_transitions must be a positive integer")
        result = self.advance(request_path)
        used = result.get("transition_count", 0)
        if (
            result.get("state") == "dispatch_internal_work"
            and isinstance(used, int)
            and used > max_transitions
        ):
            return self._result(
                state="continue_internal",
                route=result.get("route"),
                work_item_id=str(result["work_item_id"]),
                pending_directive=result["directive"],
                next_route="reenter-research-director",
                transition_count=max_transitions,
            )
        return result
