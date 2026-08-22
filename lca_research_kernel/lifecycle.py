"""Replayable lifecycle contracts and convergence checks for Council-default."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping, MutableMapping, Optional, Sequence
from zoneinfo import ZoneInfo


CONVERGENCE_PATH = Path("docs/council-lifecycle-convergence-contract.json")
PRODUCT_ARCHITECTURE_PATH = Path("docs/council-default-product-architecture.json")
CONVERGENCE_CONTRACT = "lca_research_kernel.lifecycle_convergence_contract/v1"
PRODUCT_ARCHITECTURE_CONTRACT = (
    "lca_research_kernel.council_default_product_architecture/v1"
)
TRUE_UNSUPPORTED_CLAIMS = {
    "council_superior",
    "freshwater_is_two_route_comparative_lca",
    "majority_vote_can_correct_tiangong",
}
FORBIDDEN_LIVE_STATE_FIELDS = {
    "formal_status",
    "next_route",
    "supported_now",
    "active_module",
    "codex_goal_fit",
    "updated_at",
    "goal_status",
    "current_progress",
}
LIFECYCLE_EVENT_PREFIX = "research.lifecycle."
LIFECYCLE_PROJECTION_CONTRACT = "lca_research_kernel.lifecycle_projection/v1"
INITIALIZATION_CONTRACT = (
    "lca_research_kernel.lifecycle_control_plane_initialized/v1"
)
GOAL_OBSERVATION_CONTRACT = "lca_research_kernel.codex_goal_observation/v1"
EXTERNAL_GOAL_OBSERVATION_REQUEST_CONTRACT = (
    "lca_research_kernel.codex_goal_external_observation_request/v1"
)
EXTERNAL_GOAL_OBSERVATION_CONTRACT = (
    "lca_research_kernel.codex_goal_external_observation/v1"
)
PROJECT_GOAL_DECLARATION_REQUEST_CONTRACT = (
    "lca_research_kernel.project_goal_declaration_request/v1"
)
PROJECT_GOAL_DECLARED_CONTRACT = "lca_research_kernel.project_goal_declared/v1"
PHASE_STARTED_CONTRACT = "lca_research_kernel.lifecycle_phase_started/v1"
BEAT_REQUEST_CONTRACT = "lca_research_kernel.lifecycle_beat_request/v1"
BEAT_ACCEPTED_CONTRACT = "lca_research_kernel.lifecycle_beat_accepted/v1"
BEAT_REVIEW_REJECTION_REQUEST_CONTRACT = (
    "lca_research_kernel.lifecycle_beat_review_rejection_request/v1"
)
BEAT_REVIEW_REJECTED_CONTRACT = (
    "lca_research_kernel.lifecycle_beat_review_rejected/v1"
)
READY_CONTRACT = "lca_research_kernel.lifecycle_phase_ready_to_complete/v1"
PHASE_SUPERSEDED_CONTRACT = (
    "lca_research_kernel.lifecycle_phase_superseded/v1"
)
GOAL_ABSENCE_OBSERVATION_CONTRACT = (
    "lca_research_kernel.codex_goal_absence_observation/v1"
)
ROLLBACK_CONTRACT = "lca_research_kernel.lifecycle_rollback_observed/v1"
RECOVERY_CONTRACT = "lca_research_kernel.lifecycle_recovery_observed/v1"
PHASE_REQUEST_CONTRACT = "lca_research_kernel.lifecycle_phase_request/v1"
D18_GOAL_ID = "d18-tiangong-native-lca-agent"
ROLLBACK_COMMIT = "22d0576ad1a3f25ceef05ec5940fb02a848a27e9"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FINALIZER_GATES = {
    "focused_lifecycle_tests",
    "council_regression_tests",
    "full_repository_verification",
    "project_recovery",
    "state_convergence",
    "immutable_history",
    "exact_rollback",
    "zero_secrets_online_identity_and_forbidden_effects",
    "clean_independent_review",
}
FINALIZER_GATE_COMMANDS = {
    "focused_lifecycle_tests": [
        "python3",
        "-m",
        "unittest",
        "tests.test_lifecycle_contract",
        "tests.test_lifecycle_convergence",
        "tests.test_lifecycle_project",
        "tests.test_lifecycle_cli",
        "tests.test_lifecycle_finalizer",
        "tests.test_lifecycle_agent_contract",
    ],
    "council_regression_tests": [
        "python3",
        "-m",
        "unittest",
        "tests.test_council_default_product_contract",
        "tests.test_council_runtime",
        "tests.test_council_project_integration",
        "tests.test_council_cli",
        "tests.test_council_freshwater_acceptance",
        "tests.test_council_safety_acceptance",
        "tests.test_council_recovery_rollback",
    ],
    "full_repository_verification": ["make", "verify"],
    "project_recovery": ["python3", "tools/verify_project_state.py"],
    "state_convergence": [
        "python3",
        "-m",
        "lca_research_kernel",
        "lifecycle-verify-convergence",
        "--root",
        ".",
        "--workspace",
        "outputs/council-default-lifecycle",
    ],
    "immutable_history": [
        "python3",
        "-m",
        "unittest",
        "tests.test_lifecycle_contract.LifecycleContractTests.test_convergence_contract_freezes_history_and_transition_policy",
    ],
    "exact_rollback": [
        "python3",
        "-m",
        "unittest",
        "tests.test_lifecycle_acceptance.LifecycleAcceptanceTests.test_immutable_history_and_exact_rollback",
    ],
    "zero_secrets_online_identity_and_forbidden_effects": [
        "python3",
        "-m",
        "unittest",
        "tests.test_lifecycle_acceptance.LifecycleAcceptanceTests.test_lifecycle_history_contains_no_forbidden_effect_or_online_identity",
    ],
    "clean_independent_review": [
        "python3",
        "-m",
        "unittest",
        "tests.test_lifecycle_acceptance.LifecycleAcceptanceTests.test_clean_review_and_verification_ledger_are_accepted",
    ],
}


def finalizer_gate_commands() -> dict[str, list[str]]:
    """Return the exact, non-user-extensible command bound to each final gate."""

    return deepcopy(FINALIZER_GATE_COMMANDS)


def _parse_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} must be an RFC3339 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed


def _require_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"lifecycle payload {key} must be a non-empty string")
    return value


def _require_sha256(payload: Mapping[str, object], key: str) -> str:
    value = _require_string(payload, key)
    if SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"lifecycle payload {key} must be a lowercase SHA-256")
    return value


def _require_ref(payload: Mapping[str, object], key: str) -> dict:
    value = payload.get(key)
    if not isinstance(value, Mapping) or set(value) != {"path", "sha256"}:
        raise ValueError(f"lifecycle payload {key} must be a path/SHA-256 reference")
    path = _require_string(value, "path")
    sha256 = _require_sha256(value, "sha256")
    if Path(path).is_absolute() or ".." in Path(path).parts:
        raise ValueError(f"lifecycle payload {key} path must be repository-relative")
    return {"path": path, "sha256": sha256}


def _require_risks(payload: Mapping[str, object]) -> list[str]:
    value = payload.get("open_risks")
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError("lifecycle payload open_risks must be a list of strings")
    return list(value)


def _require_refs(payload: Mapping[str, object], key: str) -> list[dict]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"lifecycle payload {key} must be a non-empty reference list")
    return [_require_ref({key: item}, key) for item in value]


def _read_input(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid lifecycle input at {path}: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"lifecycle input at {path} must be an object")
    return payload


def _validate_repository_ref(reference: Mapping[str, object]) -> dict:
    normalized = _require_ref({"reference": reference}, "reference")
    path = REPOSITORY_ROOT / normalized["path"]
    if not path.is_file():
        raise ValueError(f"lifecycle basis artifact is absent: {normalized['path']}")
    if _sha256(path) != normalized["sha256"]:
        raise ValueError(f"lifecycle basis artifact drifted: {normalized['path']}")
    return normalized


def initial_lifecycle_projection() -> dict:
    """Return the deterministic seed used only when lifecycle events exist."""

    return {
        "contract": LIFECYCLE_PROJECTION_CONTRACT,
        "lifecycle_id": None,
        "lifecycle_state": "pending",
        "goal_observation": None,
        "formal_status": None,
        "claim_mode": None,
        "active_module": "lifecycle-control-plane-initialization",
        "accepted_artifact": None,
        "open_risks": [],
        "next_route": "initialize-lifecycle-control-plane",
        "post_completion_next_route": None,
        "current_phase_id": None,
        "ready_event_id": None,
        "rollback_event_id": None,
        "last_lifecycle_event_id": None,
        "last_observed_at": None,
    }


def _validate_goal_observation(
    payload: Mapping[str, object],
    *,
    statuses: set[str],
    sources: set[str],
) -> dict:
    if payload.get("contract") != GOAL_OBSERVATION_CONTRACT:
        raise ValueError("Goal observation contract is unsupported")
    observation = {
        "contract": GOAL_OBSERVATION_CONTRACT,
        "observation_id": _require_string(payload, "observation_id"),
        "thread_id": _require_string(payload, "thread_id"),
        "objective_sha256": _require_sha256(payload, "objective_sha256"),
        "observed_status": _require_string(payload, "observed_status"),
        "observed_at": _require_string(payload, "observed_at"),
        "source": _require_string(payload, "source"),
        "source_authority": _require_string(payload, "source_authority"),
        "expected_thread_id": _require_string(payload, "expected_thread_id"),
        "expected_predecessor_state": _require_string(
            payload, "expected_predecessor_state"
        ),
    }
    if observation["observed_status"] not in statuses:
        raise ValueError("Goal observation status is invalid for this event")
    if observation["source"] not in sources:
        raise ValueError("Goal observation source is unsupported")
    if observation["source_authority"] != "codex_platform":
        raise ValueError("Goal observation must identify Codex as external authority")
    if observation["thread_id"] != observation["expected_thread_id"]:
        raise ValueError("Goal observation thread does not match the expected thread")
    _parse_timestamp(observation["observed_at"], "Goal observation observed_at")
    return observation


def _validate_external_goal_observation(
    payload: Mapping[str, object], *, active_thread_id: object
) -> dict:
    required = {
        "contract",
        "observation_id",
        "thread_id",
        "observed_status",
        "observed_at",
        "source",
        "source_authority",
        "relation_to_project_goal",
    }
    unexpected = set(payload) - required
    missing = required - set(payload)
    if missing:
        raise ValueError(
            f"external Goal observation is missing {sorted(missing)[0]}"
        )
    if unexpected:
        raise ValueError(
            f"external Goal observation has unexpected field {sorted(unexpected)[0]}"
        )
    if payload.get("contract") != EXTERNAL_GOAL_OBSERVATION_CONTRACT:
        raise ValueError("external Goal observation contract is unsupported")
    observation = {
        "contract": EXTERNAL_GOAL_OBSERVATION_CONTRACT,
        "observation_id": _require_string(payload, "observation_id"),
        "thread_id": _require_string(payload, "thread_id"),
        "observed_status": _require_string(payload, "observed_status"),
        "observed_at": _require_string(payload, "observed_at"),
        "source": _require_string(payload, "source"),
        "source_authority": _require_string(payload, "source_authority"),
        "relation_to_project_goal": _require_string(
            payload, "relation_to_project_goal"
        ),
    }
    if observation["observed_status"] not in {"active", "blocked"}:
        raise ValueError("external Goal observation status is unsupported")
    if (
        observation["source"] != "codex_goal_get"
        or observation["source_authority"] != "codex_platform"
    ):
        raise ValueError("external Goal observation source is unsupported")
    if observation["relation_to_project_goal"] != (
        "cross_thread_point_in_time_unreconciled"
    ):
        raise ValueError("external Goal observation relation is unsupported")
    if observation["thread_id"] == active_thread_id:
        raise ValueError("external Goal observation must be cross-thread")
    _parse_timestamp(
        observation["observed_at"], "external Goal observation observed_at"
    )
    return observation


def _validate_project_goal_declaration(payload: Mapping[str, object]) -> dict:
    if payload.get("contract") != PROJECT_GOAL_DECLARED_CONTRACT:
        raise ValueError("Project Goal declaration contract is unsupported")
    declaration = {
        "contract": PROJECT_GOAL_DECLARED_CONTRACT,
        "declaration_id": _require_string(payload, "declaration_id"),
        "goal_id": _require_string(payload, "goal_id"),
        "objective": _require_string(payload, "objective"),
        "objective_sha256": _require_sha256(payload, "objective_sha256"),
        "declared_at": _require_string(payload, "declared_at"),
        "source": _require_string(payload, "source"),
        "expected_predecessor_state": _require_string(
            payload, "expected_predecessor_state"
        ),
        "target_architecture_ref": _require_ref(
            payload, "target_architecture_ref"
        ),
        "system_mapping_ref": _require_ref(payload, "system_mapping_ref"),
    }
    if "goal_generation" in payload:
        generation = payload.get("goal_generation")
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
            raise ValueError("Project Goal generation must be a positive integer")
        declaration["goal_generation"] = generation
    if declaration["source"] != "user_approved_goal":
        raise ValueError("Project Goal declaration source is unsupported")
    objective_hash = hashlib.sha256(
        declaration["objective"].encode("utf-8")
    ).hexdigest()
    if objective_hash != declaration["objective_sha256"]:
        raise ValueError("Project Goal declaration objective hash drifted")
    _parse_timestamp(declaration["declared_at"], "Project Goal declared_at")
    return declaration


def _platform_goal_fit(
    observation: object, declaration: Mapping[str, object]
) -> str:
    if not isinstance(observation, Mapping):
        return "absent"
    if observation.get("objective_sha256") == declaration.get("objective_sha256"):
        return "exact"
    if observation.get("observed_status") in {"active", "blocked"}:
        return "superseded_unfinished"
    return "absent"


def _require_initialized(state: Mapping[str, object]) -> None:
    if state.get("lifecycle_id") is None:
        raise ValueError("lifecycle control plane must be initialized first")


def _require_state(state: Mapping[str, object], *allowed: str) -> str:
    current = state.get("lifecycle_state")
    if current not in allowed:
        expected = " or ".join(allowed)
        raise ValueError(f"lifecycle transition requires lifecycle state {expected}")
    return str(current)


def _assert_monotonic_observation(
    state: Mapping[str, object], observed_at: object
) -> None:
    current = state.get("last_observed_at")
    candidate = _parse_timestamp(observed_at, "lifecycle observed_at")
    if isinstance(current, str) and candidate < _parse_timestamp(
        current, "prior lifecycle observed_at"
    ):
        raise ValueError("lifecycle observation time regressed")


def _assert_monotonic_platform_observation(
    state: Mapping[str, object], observed_at: object
) -> None:
    """Compare point-in-time platform facts only with prior platform facts."""

    candidate = _parse_timestamp(observed_at, "platform Goal observed_at")
    prior_times: list[datetime] = []
    for field in (
        "goal_observation",
        "external_goal_observation",
        "platform_absence_observation",
        "superseded_goal_observation",
        "superseded_platform_goal_observation",
    ):
        observation = state.get(field)
        if isinstance(observation, Mapping) and isinstance(
            observation.get("observed_at"), str
        ):
            prior_times.append(
                _parse_timestamp(
                    observation["observed_at"],
                    f"prior {field} observed_at",
                )
            )
    explicit = state.get("last_platform_observed_at")
    if isinstance(explicit, str):
        prior_times.append(
            _parse_timestamp(explicit, "prior platform Goal observed_at")
        )
    if prior_times and candidate < max(prior_times):
        raise ValueError("platform Goal observation time regressed")


def _advance_last_observed_at(
    state: MutableMapping[str, object], observed_at: str
) -> None:
    """Advance the logical clock without rewriting a later lifecycle time."""

    candidate = _parse_timestamp(observed_at, "lifecycle observed_at")
    current = state.get("last_observed_at")
    if not isinstance(current, str) or candidate > _parse_timestamp(
        current, "prior lifecycle observed_at"
    ):
        state["last_observed_at"] = observed_at


def reduce_lifecycle_event(
    state: MutableMapping[str, object], event: Mapping[str, object]
) -> None:
    """Apply one validated lifecycle event to the supplied projection in place."""

    event_type = event.get("event_type")
    if not isinstance(event_type, str) or not event_type.startswith(
        LIFECYCLE_EVENT_PREFIX
    ):
        return
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("lifecycle event payload must be an object")
    event_id = event.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("lifecycle event_id must be a non-empty string")

    if event_type == "research.lifecycle.control_plane_initialized":
        if state.get("lifecycle_id") is not None:
            raise ValueError("lifecycle control plane is already initialized")
        if payload.get("contract") != INITIALIZATION_CONTRACT:
            raise ValueError("lifecycle initialization contract is unsupported")
        lifecycle_id = _require_string(payload, "lifecycle_id")
        _require_ref(payload, "product_architecture_ref")
        convergence_ref = _require_ref(payload, "convergence_contract_ref")
        formal_status = _require_string(payload, "formal_status_at_migration")
        claim_mode = _require_string(payload, "claim_mode_at_migration")
        if (claim_mode, formal_status) != ("formal", "formal_operational_truth"):
            raise ValueError("lifecycle migration must preserve accepted formal truth")
        state.update(
            {
                "lifecycle_id": lifecycle_id,
                "lifecycle_state": "pending",
                "formal_status": formal_status,
                "claim_mode": claim_mode,
                "active_module": "lifecycle-control-plane-pending",
                "accepted_artifact": convergence_ref,
                "open_risks": [],
                "next_route": "observe-active-codex-goal",
                "post_completion_next_route": _require_string(
                    payload, "post_completion_next_route"
                ),
            }
        )
    elif event_type == "research.lifecycle.project_goal_declared":
        _require_initialized(state)
        predecessor = _require_state(
            state, "pending", "recovered", "goal_completed", "superseded"
        )
        declaration = _validate_project_goal_declaration(payload)
        prior_declaration = state.get("project_goal")
        prior_generation = (
            prior_declaration.get("goal_generation")
            if isinstance(prior_declaration, Mapping)
            else None
        )
        declared_generation = declaration.get("goal_generation")
        if (
            prior_generation is not None
            and (
                declared_generation is None
                or int(declared_generation) <= int(prior_generation)
            )
        ):
            raise ValueError("Project Goal generation must increase")
        if declaration["expected_predecessor_state"] != state.get(
            "lifecycle_state"
        ):
            raise ValueError("Project Goal declaration predecessor state drifted")
        if state.get("current_phase_id") is not None:
            raise ValueError("Project Goal cannot change during an active phase")
        _assert_monotonic_observation(state, declaration["declared_at"])
        state.update(
            {
                "lifecycle_state": (
                    "pending" if predecessor == "superseded" else predecessor
                ),
                "project_goal": declaration,
                "platform_goal_fit": (
                    "absent"
                    if predecessor == "superseded"
                    and isinstance(
                        state.get("platform_absence_observation"), Mapping
                    )
                    else _platform_goal_fit(
                        state.get("goal_observation"), declaration
                    )
                ),
                "active_module": "project-goal-declared",
                "accepted_artifact": declaration["target_architecture_ref"],
                "open_risks": [],
                "next_route": "start-declared-project-phase",
                "last_observed_at": declaration["declared_at"],
            }
        )
        if "goal_generation" in declaration:
            state["goal_generation"] = declaration["goal_generation"]
            state.pop("phase_generation", None)
    elif event_type == "research.lifecycle.goal_observed":
        _require_initialized(state)
        observation = _validate_goal_observation(
            payload,
            statuses={"active", "blocked"},
            sources={"codex_goal_get"},
        )
        if observation["expected_predecessor_state"] != state.get(
            "lifecycle_state"
        ):
            raise ValueError("Goal observation expected predecessor state drifted")
        active_phase = state.get("current_phase_id")
        prior = state.get("goal_observation")
        if active_phase is not None and isinstance(prior, Mapping) and (
            observation["thread_id"] != prior.get("thread_id")
        ):
            raise ValueError("active lifecycle phase cannot switch Goal thread")
        _assert_monotonic_platform_observation(
            state, observation["observed_at"]
        )
        state["goal_observation"] = observation
        _advance_last_observed_at(state, observation["observed_at"])
        declaration = state.get("project_goal")
        if isinstance(declaration, Mapping):
            state["platform_goal_fit"] = _platform_goal_fit(
                observation, declaration
            )
    elif event_type == "research.lifecycle.external_goal_observed":
        _require_initialized(state)
        _require_state(
            state,
            "pending",
            "in_progress",
            "ready_to_complete",
            "goal_completed",
            "superseded",
            "recovered",
        )
        prior = state.get("goal_observation")
        active_thread_id = (
            prior.get("thread_id") if isinstance(prior, Mapping) else None
        )
        observation = _validate_external_goal_observation(
            payload, active_thread_id=active_thread_id
        )
        _assert_monotonic_platform_observation(
            state, observation["observed_at"]
        )
        state["external_goal_observation"] = observation
        state["last_platform_observed_at"] = observation["observed_at"]
        _advance_last_observed_at(state, observation["observed_at"])
        state["platform_goal_fit"] = "superseded_unfinished"
    elif event_type == "research.lifecycle.phase_started":
        _require_initialized(state)
        _require_state(state, "pending", "recovered", "goal_completed")
        if payload.get("contract") != PHASE_STARTED_CONTRACT:
            raise ValueError("lifecycle phase-start contract is unsupported")
        declaration = state.get("project_goal")
        if isinstance(declaration, Mapping) and payload.get("goal_declaration_id"):
            if _require_string(payload, "goal_declaration_id") != declaration.get(
                "declaration_id"
            ):
                raise ValueError("phase Project Goal declaration reference drifted")
            if _require_string(payload, "project_goal_id") != declaration.get(
                "goal_id"
            ):
                raise ValueError("phase Project Goal identity drifted")
            fit = _require_string(payload, "platform_goal_fit")
            if fit != state.get("platform_goal_fit"):
                raise ValueError("phase platform Goal fit drifted")
            declaration_generation = declaration.get("goal_generation")
            if declaration_generation is not None:
                if payload.get("goal_generation") != declaration_generation:
                    raise ValueError("phase Project Goal generation drifted")
                phase_generation = payload.get("phase_generation")
                if (
                    not isinstance(phase_generation, int)
                    or isinstance(phase_generation, bool)
                    or phase_generation < 1
                ):
                    raise ValueError("phase generation must be a positive integer")
            goal_basis = "project_record_declaration"
        else:
            observation = state.get("goal_observation")
            if not isinstance(observation, Mapping) or observation.get(
                "observed_status"
            ) != "active":
                raise ValueError("phase start requires an active Goal observation")
            thread_id = _require_string(payload, "thread_id")
            if thread_id != observation.get("thread_id"):
                raise ValueError("phase thread does not match the observed Goal")
            if _require_string(payload, "goal_observation_id") != observation.get(
                "observation_id"
            ):
                raise ValueError("phase Goal observation reference drifted")
            goal_basis = "codex_goal_observation"
        basis_refs = _require_refs(payload, "basis_refs")
        if _require_string(payload, "rollback_git_commit") != ROLLBACK_COMMIT:
            raise ValueError("phase start does not bind the exact rollback baseline")
        state.update(
            {
                "lifecycle_state": "in_progress",
                "current_phase_id": _require_string(payload, "phase_id"),
                "active_module": _require_string(payload, "active_module"),
                "accepted_artifact": _require_ref(payload, "accepted_artifact"),
                "open_risks": _require_risks(payload),
                "next_route": _require_string(payload, "next_route"),
                "post_completion_next_route": _require_string(
                    payload, "post_completion_next_route"
                ),
                "ready_event_id": None,
                "rollback_event_id": None,
                "basis_refs": basis_refs,
                "rollback_git_commit": ROLLBACK_COMMIT,
            }
        )
        if "complex_runtime_pin" in payload:
            state["complex_runtime_pin"] = _require_string(
                payload, "complex_runtime_pin"
            )
            state["complex_runtime_adoption_ref"] = _require_ref(
                payload, "complex_runtime_adoption_ref"
            )
        if "goal_generation" in payload:
            state["goal_generation"] = payload["goal_generation"]
        if "phase_generation" in payload:
            state["phase_generation"] = payload["phase_generation"]
        if goal_basis == "project_record_declaration":
            state["goal_basis"] = goal_basis
    elif event_type == "research.lifecycle.beat_accepted":
        _require_initialized(state)
        _require_state(state, "in_progress")
        if payload.get("contract") != BEAT_ACCEPTED_CONTRACT:
            raise ValueError("lifecycle beat-acceptance contract is unsupported")
        if _require_string(payload, "phase_id") != state.get("current_phase_id"):
            raise ValueError("accepted beat does not belong to the active phase")
        beat_id = _require_string(payload, "beat_id")
        open_rejection = state.get("rejected_beat_review")
        closure = payload.get("closes_review_rejection")
        if isinstance(open_rejection, Mapping):
            if not isinstance(closure, Mapping):
                raise ValueError(
                    "accepted beat must explicitly close the open review rejection"
                )
            expected_closure = {
                "rejection_event_id": open_rejection.get("rejection_event_id"),
                "review_id": open_rejection.get("review_id"),
                "rejected_beat_id": open_rejection.get("rejected_beat_id"),
            }
            if set(closure) != set(expected_closure) or dict(closure) != expected_closure:
                raise ValueError("accepted beat review-rejection closure drifted")
        elif closure is not None:
            raise ValueError("accepted beat cannot close a nonexistent review rejection")
        accepted_at = _require_string(payload, "accepted_at")
        _assert_monotonic_observation(state, accepted_at)
        state.update(
            {
                "accepted_beat_id": beat_id,
                "active_module": _require_string(payload, "active_module"),
                "accepted_artifact": _require_ref(payload, "accepted_artifact"),
                "open_risks": _require_risks(payload),
                "next_route": _require_string(payload, "next_route"),
                "last_observed_at": accepted_at,
            }
        )
        state.pop("rejected_beat_review", None)
        state.pop("accepted_artifact_status", None)
    elif event_type == "research.lifecycle.beat_review_rejected":
        _require_initialized(state)
        _require_state(state, "in_progress")
        if payload.get("contract") != BEAT_REVIEW_REJECTED_CONTRACT:
            raise ValueError("lifecycle beat-review rejection contract is unsupported")
        if _require_string(payload, "phase_id") != state.get("current_phase_id"):
            raise ValueError("rejected beat review does not belong to the active phase")
        reviewed_at = _require_string(payload, "reviewed_at")
        _assert_monotonic_observation(state, reviewed_at)
        rejected_artifact = _require_ref(payload, "rejected_artifact")
        if rejected_artifact != state.get("accepted_artifact"):
            raise ValueError("rejected beat review artifact differs from current projection")
        rejected_beat_id = _require_string(payload, "rejected_beat_id")
        if rejected_beat_id != state.get("accepted_beat_id"):
            raise ValueError("rejected beat review does not bind the current accepted beat")
        if isinstance(state.get("rejected_beat_review"), Mapping):
            raise ValueError("a review rejection is already open")
        state.update(
            {
                "active_module": _require_string(payload, "active_module"),
                "open_risks": _require_risks(payload),
                "next_route": _require_string(payload, "next_route"),
                "last_observed_at": reviewed_at,
                "rejected_beat_review": {
                    "rejection_event_id": event_id,
                    "review_id": _require_string(payload, "review_id"),
                    "rejected_beat_id": rejected_beat_id,
                    "rejected_artifact": rejected_artifact,
                    "reviewed_at": reviewed_at,
                },
                "accepted_artifact_status": "independent_review_rejected",
            }
        )
    elif event_type == "research.lifecycle.phase_ready_to_complete":
        _require_initialized(state)
        _require_state(state, "in_progress")
        if payload.get("contract") != READY_CONTRACT:
            raise ValueError("lifecycle ready contract is unsupported")
        if _require_string(payload, "phase_id") != state.get("current_phase_id"):
            raise ValueError("ready phase does not match active lifecycle phase")
        observation = state.get("goal_observation")
        if not isinstance(observation, Mapping):
            raise ValueError("ready transition requires a Goal observation")
        if _require_string(payload, "thread_id") != observation.get("thread_id"):
            raise ValueError("ready thread does not match the observed Goal")
        if _require_string(payload, "goal_observation_id") != observation.get(
            "observation_id"
        ):
            raise ValueError("ready Goal observation reference drifted")
        _require_ref(payload, "gate_manifest_ref")
        ready_ref = _require_ref(payload, "ready_receipt_ref")
        _require_string(payload, "finalizer_id")
        _require_sha256(payload, "request_sha256")
        if set(payload.get("passed_gate_ids") or []) != REQUIRED_FINALIZER_GATES:
            raise ValueError("ready transition requires the complete finalizer gate set")
        registered = payload.get("registered_artifacts")
        if not isinstance(registered, list) or not registered:
            raise ValueError("ready transition requires registered finalizer artifacts")
        normalized_registered: list[dict] = []
        for artifact in registered:
            if not isinstance(artifact, Mapping):
                raise ValueError("ready registered artifact must be an object")
            artifact_id = _require_string(artifact, "artifact_id")
            path = _require_string(artifact, "path")
            workspace_path = _require_string(artifact, "workspace_path")
            if (
                Path(path).is_absolute()
                or ".." in Path(path).parts
                or Path(workspace_path).is_absolute()
                or ".." in Path(workspace_path).parts
            ):
                raise ValueError("ready registered artifact path is unsafe")
            digest = _require_sha256(artifact, "sha256")
            if not isinstance(artifact.get("bytes"), int) or artifact["bytes"] < 0:
                raise ValueError("ready registered artifact bytes must be non-negative")
            normalized_registered.append(
                {
                    "artifact_id": artifact_id,
                    "path": path,
                    "workspace_path": workspace_path,
                    "sha256": digest,
                    "bytes": artifact["bytes"],
                }
            )
        if len(normalized_registered) != len(REQUIRED_FINALIZER_GATES) + 2:
            raise ValueError("ready registered artifact set is incomplete")
        if len({row["artifact_id"] for row in normalized_registered}) != len(
            normalized_registered
        ) or len({row["path"] for row in normalized_registered}) != len(
            normalized_registered
        ):
            raise ValueError("ready registered artifact identities are duplicated")
        state.update(
            {
                "lifecycle_state": "ready_to_complete",
                "active_module": _require_string(payload, "active_module"),
                "accepted_artifact": _require_ref(payload, "accepted_artifact"),
                "open_risks": _require_risks(payload),
                "next_route": _require_string(payload, "next_route"),
                "post_completion_next_route": _require_string(
                    payload, "post_completion_next_route"
                ),
                "ready_event_id": event_id,
                "ready_receipt_ref": ready_ref,
                "gate_manifest_ref": _require_ref(payload, "gate_manifest_ref"),
                "registered_artifacts": normalized_registered,
            }
        )
    elif event_type == "research.lifecycle.phase_superseded":
        _require_initialized(state)
        predecessor = _require_state(state, "in_progress", "ready_to_complete")
        if payload.get("contract") != PHASE_SUPERSEDED_CONTRACT:
            raise ValueError("lifecycle phase-supersession contract is unsupported")
        _require_string(payload, "supersession_id")
        if predecessor == "in_progress":
            if _require_string(payload, "prior_phase_id") != state.get(
                "current_phase_id"
            ):
                raise ValueError("phase supersession prior-phase reference drifted")
            prior_artifact = _require_ref(payload, "prior_accepted_artifact_ref")
            if prior_artifact != state.get("accepted_artifact"):
                raise ValueError("phase supersession prior artifact drifted")
            reason = _require_string(payload, "reason")
            if reason == (
                "user_approved_route_rebaseline_without_platform_replacement"
            ):
                observation_value = payload.get("platform_goal_observation")
                if not isinstance(observation_value, Mapping):
                    raise ValueError("stale platform Goal observation is absent")
                observation = _validate_goal_observation(
                    observation_value,
                    statuses={"active"},
                    sources={"codex_goal_get"},
                )
                if observation["expected_predecessor_state"] != "in_progress":
                    raise ValueError("route rebaseline Goal predecessor state drifted")
                prior_observation = state.get("goal_observation")
                if not isinstance(prior_observation, Mapping):
                    raise ValueError("route rebaseline prior Goal observation is absent")
                if (
                    observation["thread_id"] != prior_observation.get("thread_id")
                    or observation["objective_sha256"]
                    != prior_observation.get("objective_sha256")
                ):
                    raise ValueError("route rebaseline platform Goal identity drifted")
                _assert_monotonic_platform_observation(
                    state, observation["observed_at"]
                )
                recorded_at = _require_string(payload, "recorded_at")
                _parse_timestamp(recorded_at, "route rebaseline recorded_at")
                logical_transition_at = _require_string(
                    payload, "logical_transition_at"
                )
                _assert_monotonic_observation(state, logical_transition_at)
                accepted_artifact = _require_ref(payload, "accepted_artifact")
                state.update(
                    {
                        "lifecycle_state": "superseded",
                        "platform_goal_fit": "superseded_unfinished",
                        "superseded_goal_observation": deepcopy(
                            dict(prior_observation)
                        ),
                        "goal_observation": observation,
                        "active_module": _require_string(
                            payload, "active_module"
                        ),
                        "accepted_artifact": accepted_artifact,
                        "open_risks": _require_risks(payload),
                        "next_route": _require_string(payload, "next_route"),
                        "current_phase_id": None,
                        "last_observed_at": logical_transition_at,
                        "last_platform_observed_at": observation[
                            "observed_at"
                        ],
                    }
                )
                state.pop("platform_absence_observation", None)
                state["last_lifecycle_event_id"] = event_id
                occurred_at = event.get("occurred_at")
                if isinstance(occurred_at, str):
                    _parse_timestamp(occurred_at, "lifecycle event occurred_at")
                    state["last_lifecycle_at"] = occurred_at
                return
            if reason != "user_approved_goal_change_with_active_replacement":
                raise ValueError("active phase supersession reason is unsupported")
            replacement = payload.get("replacement_goal_observation")
            if not isinstance(replacement, Mapping):
                raise ValueError("replacement Goal observation is absent")
            observation = _validate_goal_observation(
                replacement,
                statuses={"active"},
                sources={"codex_goal_get"},
            )
            if observation["expected_predecessor_state"] != "in_progress":
                raise ValueError("replacement Goal predecessor state drifted")
            prior_observation = state.get("goal_observation")
            if not isinstance(prior_observation, Mapping):
                raise ValueError("superseded Goal observation is absent")
            blocked_value = payload.get(
                "superseded_platform_goal_observation"
            )
            if not isinstance(blocked_value, Mapping):
                raise ValueError(
                    "superseded platform Goal observation is absent"
                )
            blocked_observation = _validate_goal_observation(
                blocked_value,
                statuses={"blocked"},
                sources={"codex_goal_get"},
            )
            if (
                blocked_observation["expected_predecessor_state"]
                != "in_progress"
            ):
                raise ValueError(
                    "superseded platform Goal predecessor state drifted"
                )
            if (
                blocked_observation["thread_id"]
                != prior_observation.get("thread_id")
                or blocked_observation["objective_sha256"]
                != prior_observation.get("objective_sha256")
            ):
                raise ValueError(
                    "superseded platform Goal identity drifted"
                )
            if observation["thread_id"] != prior_observation.get("thread_id"):
                raise ValueError("replacement Goal thread identity drifted")
            if observation["objective_sha256"] == prior_observation.get(
                "objective_sha256"
            ):
                raise ValueError("replacement Goal objective was not changed")
            recorded_at = _require_string(payload, "recorded_at")
            _parse_timestamp(recorded_at, "active phase supersession recorded_at")
            logical_transition_at = _require_string(
                payload, "logical_transition_at"
            )
            _assert_monotonic_observation(state, logical_transition_at)
            accepted_artifact = _require_ref(payload, "accepted_artifact")
            if accepted_artifact != prior_artifact:
                raise ValueError("phase supersession accepted artifact drifted")
            state.update(
                {
                    "lifecycle_state": "superseded",
                    "platform_goal_fit": "superseded_unfinished",
                    "superseded_goal_observation": deepcopy(
                        dict(prior_observation)
                    ),
                    "superseded_platform_goal_observation": (
                        blocked_observation
                    ),
                    "goal_observation": observation,
                    "active_module": _require_string(payload, "active_module"),
                    "accepted_artifact": accepted_artifact,
                    "open_risks": _require_risks(payload),
                    "next_route": _require_string(payload, "next_route"),
                    "current_phase_id": None,
                    "last_observed_at": logical_transition_at,
                    "last_platform_observed_at": observation["observed_at"],
                }
            )
            state.pop("platform_absence_observation", None)
            state["last_lifecycle_event_id"] = event_id
            occurred_at = event.get("occurred_at")
            if isinstance(occurred_at, str):
                _parse_timestamp(occurred_at, "lifecycle event occurred_at")
                state["last_lifecycle_at"] = occurred_at
            return
        if _require_string(payload, "prior_phase_id") != state.get(
            "current_phase_id"
        ):
            raise ValueError("phase supersession prior-phase reference drifted")
        if _require_string(payload, "prior_ready_event_id") != state.get(
            "ready_event_id"
        ):
            raise ValueError("phase supersession ready-event reference drifted")
        ready_receipt = _require_ref(payload, "prior_ready_receipt_ref")
        if ready_receipt != state.get("ready_receipt_ref"):
            raise ValueError("phase supersession ready receipt drifted")
        observation = payload.get("platform_absence_observation")
        if (
            not isinstance(observation, Mapping)
            or observation.get("contract") != GOAL_ABSENCE_OBSERVATION_CONTRACT
        ):
            raise ValueError("phase supersession platform absence is unsupported")
        if observation.get("observed_status") != "absent":
            raise ValueError("phase supersession requires an absent platform Goal")
        if observation.get("source") != "codex_goal_get" or observation.get(
            "source_authority"
        ) != "codex_platform":
            raise ValueError("phase supersession platform absence source is unsupported")
        if observation.get("expected_predecessor_state") != "ready_to_complete":
            raise ValueError("phase supersession absence predecessor drifted")
        observed_at = _require_string(observation, "observed_at")
        superseded_at = _require_string(payload, "superseded_at")
        if observed_at != superseded_at:
            raise ValueError("phase supersession observation time drifted")
        _assert_monotonic_platform_observation(state, observed_at)
        if _require_string(payload, "reason") != (
            "user_approved_goal_change_after_platform_absence"
        ):
            raise ValueError("phase supersession reason is unsupported")
        accepted_artifact = _require_ref(payload, "accepted_artifact")
        if accepted_artifact != ready_receipt:
            raise ValueError("phase supersession accepted artifact drifted")
        state.update(
            {
                "lifecycle_state": "superseded",
                "platform_goal_fit": "absent",
                "platform_absence_observation": deepcopy(dict(observation)),
                "active_module": _require_string(payload, "active_module"),
                "accepted_artifact": accepted_artifact,
                "open_risks": _require_risks(payload),
                "next_route": _require_string(payload, "next_route"),
                "current_phase_id": None,
            }
        )
        _advance_last_observed_at(state, superseded_at)
    elif event_type == "research.lifecycle.goal_completion_observed":
        _require_initialized(state)
        _require_state(state, "ready_to_complete")
        observation = _validate_goal_observation(
            payload,
            statuses={"complete"},
            sources={"codex_goal_get", "codex_goal_update_receipt"},
        )
        if observation["expected_predecessor_state"] != "ready_to_complete":
            raise ValueError("completion observation predecessor is unsupported")
        prior = state.get("goal_observation")
        if not isinstance(prior, Mapping) or observation["thread_id"] != prior.get(
            "thread_id"
        ):
            raise ValueError("completion observation thread drifted")
        if observation["objective_sha256"] != prior.get("objective_sha256"):
            raise ValueError("completion observation objective drifted")
        if _require_string(payload, "ready_event_id") != state.get(
            "ready_event_id"
        ):
            raise ValueError("completion observation ready-event reference drifted")
        _assert_monotonic_platform_observation(
            state, observation["observed_at"]
        )
        next_route = _require_string(payload, "next_route")
        if next_route != state.get("post_completion_next_route"):
            raise ValueError("completion route differs from the stable post-completion route")
        project_goal = state.get("project_goal")
        completed_module = "council-default-operational"
        if (
            isinstance(project_goal, Mapping)
            and project_goal.get("goal_id") == D18_GOAL_ID
        ):
            completed_module = "d18-operational-foundation"
        elif isinstance(project_goal, Mapping) and str(
            project_goal.get("goal_id", "")
        ).startswith("d18-"):
            ready_module = str(state.get("active_module", ""))
            if ready_module.endswith("-ready-to-complete"):
                completed_module = (
                    ready_module[: -len("-ready-to-complete")] + "-complete"
                )
            else:
                completed_module = "d18-operational-foundation"
        state.update(
            {
                "lifecycle_state": "goal_completed",
                "goal_observation": observation,
                "active_module": completed_module,
                "open_risks": [],
                "next_route": next_route,
                "current_phase_id": None,
            }
        )
        _advance_last_observed_at(state, observation["observed_at"])
    elif event_type == "research.lifecycle.rollback_observed":
        _require_initialized(state)
        _require_state(state, "in_progress", "ready_to_complete", "goal_completed")
        if payload.get("contract") != ROLLBACK_CONTRACT:
            raise ValueError("lifecycle rollback contract is unsupported")
        _require_string(payload, "rollback_id")
        observed_at = _require_string(payload, "observed_at")
        _assert_monotonic_observation(state, observed_at)
        if _require_string(payload, "rollback_git_commit") != ROLLBACK_COMMIT:
            raise ValueError("lifecycle rollback does not use the exact frozen baseline")
        rollback_ref = _require_ref(payload, "rollback_receipt_ref")
        state.update(
            {
                "lifecycle_state": "rollback_active",
                "formal_status": "single_controller_rollback_active",
                "claim_mode": "rollback",
                "active_module": _require_string(payload, "active_module"),
                "accepted_artifact": rollback_ref,
                "open_risks": _require_risks(payload),
                "next_route": _require_string(payload, "next_route"),
                "current_phase_id": None,
                "rollback_event_id": event_id,
                "last_observed_at": observed_at,
            }
        )
    elif event_type == "research.lifecycle.recovery_observed":
        _require_initialized(state)
        _require_state(state, "rollback_active")
        if payload.get("contract") != RECOVERY_CONTRACT:
            raise ValueError("lifecycle recovery contract is unsupported")
        _require_string(payload, "recovery_id")
        if _require_string(payload, "rollback_event_id") != state.get(
            "rollback_event_id"
        ):
            raise ValueError("lifecycle recovery rollback reference drifted")
        observed_at = _require_string(payload, "observed_at")
        _assert_monotonic_observation(state, observed_at)
        state.update(
            {
                "lifecycle_state": "recovered",
                "formal_status": "formal_operational_truth",
                "claim_mode": "formal",
                "active_module": _require_string(payload, "active_module"),
                "accepted_artifact": _require_ref(payload, "accepted_artifact"),
                "open_risks": _require_risks(payload),
                "next_route": _require_string(payload, "next_route"),
                "rollback_event_id": None,
                "last_observed_at": observed_at,
            }
        )
    else:
        raise ValueError(f"unsupported lifecycle event type: {event_type}")
    state["last_lifecycle_event_id"] = event_id
    occurred_at = event.get("occurred_at")
    if isinstance(occurred_at, str):
        _parse_timestamp(occurred_at, "lifecycle event occurred_at")
        state["last_lifecycle_at"] = occurred_at


def project_lifecycle_events(events: Sequence[Mapping[str, object]]) -> dict:
    state = initial_lifecycle_projection()
    for event in events:
        reduce_lifecycle_event(state, event)
    return state


def validate_project_lifecycle_events(events: Sequence[Mapping[str, object]]) -> dict:
    event_ids: set[str] = set()
    lifecycle_keys: set[str] = set()
    prior_occurred_at: datetime | None = None
    envelope_fields = {
        "event_id",
        "sequence",
        "event_type",
        "occurred_at",
        "actor",
        "idempotency_key",
        "payload",
    }
    for expected_sequence, event in enumerate(events, start=1):
        if set(event) != envelope_fields:
            raise ValueError("lifecycle event envelope fields are invalid")
        if event.get("sequence") != expected_sequence:
            raise ValueError("lifecycle event history sequence is not contiguous")
        event_id = event.get("event_id")
        if not isinstance(event_id, str):
            raise ValueError("lifecycle event_id must be a UUID string")
        try:
            uuid.UUID(event_id)
        except ValueError as error:
            raise ValueError("lifecycle event_id must be a UUID string") from error
        if event_id in event_ids:
            raise ValueError("lifecycle event_id is duplicated")
        event_ids.add(event_id)
        event_type = event.get("event_type")
        if isinstance(event_type, str) and event_type.startswith(
            LIFECYCLE_EVENT_PREFIX
        ):
            if event.get("actor") != "controller":
                raise ValueError("lifecycle event actor must be controller")
            idempotency_key = event.get("idempotency_key")
            if not isinstance(idempotency_key, str) or not idempotency_key.strip():
                raise ValueError("lifecycle event idempotency key is absent")
            if idempotency_key in lifecycle_keys:
                raise ValueError("lifecycle event idempotency key is duplicated")
            lifecycle_keys.add(idempotency_key)
            occurred_at = _parse_timestamp(
                event.get("occurred_at"), "lifecycle event occurred_at"
            )
            if prior_occurred_at is not None and occurred_at < prior_occurred_at:
                raise ValueError("lifecycle event occurred_at is not monotonic")
            prior_occurred_at = occurred_at
    lifecycle_events = [
        event
        for event in events
        if isinstance(event.get("event_type"), str)
        and str(event["event_type"]).startswith(LIFECYCLE_EVENT_PREFIX)
    ]
    if not lifecycle_events:
        return initial_lifecycle_projection()
    if lifecycle_events[0].get("event_type") != (
        "research.lifecycle.control_plane_initialized"
    ):
        raise ValueError("first lifecycle event must initialize the control plane")
    state = project_lifecycle_events(lifecycle_events)
    if state.get("lifecycle_id") is None:
        raise ValueError("lifecycle control plane was not initialized")
    return state


def initialize_lifecycle(project: object) -> dict:
    """Append the stable lifecycle migration seam through the Kernel port."""

    product_ref = {
        "path": PRODUCT_ARCHITECTURE_PATH.as_posix(),
        "sha256": _sha256(REPOSITORY_ROOT / PRODUCT_ARCHITECTURE_PATH),
    }
    convergence_ref = {
        "path": CONVERGENCE_PATH.as_posix(),
        "sha256": _sha256(REPOSITORY_ROOT / CONVERGENCE_PATH),
    }
    payload = {
        "contract": INITIALIZATION_CONTRACT,
        "lifecycle_id": "council-default-control-plane",
        "product_architecture_ref": product_ref,
        "convergence_contract_ref": convergence_ref,
        "formal_status_at_migration": "formal_operational_truth",
        "claim_mode_at_migration": "formal",
        "post_completion_next_route": (
            "run-next-research-project-through-council-default"
        ),
    }
    event = project._record_lifecycle_event(
        "research.lifecycle.control_plane_initialized",
        payload,
        idempotency_key="lifecycle:control-plane:v1",
    )
    project.materialize_views()
    return deepcopy(event["payload"])


def observe_goal(project: object, observation_path: Path) -> dict:
    """Import one point-in-time Codex Goal observation; never imply live access."""

    payload = _read_input(observation_path)
    status = payload.get("observed_status")
    if status == "complete":
        projection = validate_project_lifecycle_events(project.events())
        payload = deepcopy(payload)
        payload["ready_event_id"] = projection.get("ready_event_id")
        payload["next_route"] = projection.get("post_completion_next_route")
        event_type = "research.lifecycle.goal_completion_observed"
    else:
        event_type = "research.lifecycle.goal_observed"
    observation_id = _require_string(payload, "observation_id")
    event = project._record_lifecycle_event(
        event_type,
        payload,
        idempotency_key=f"lifecycle:goal-observation:{observation_id}",
    )
    project.materialize_views()
    return deepcopy(event["payload"])


def observe_external_goal(project: object, observation_path: Path) -> dict:
    """Record a cross-thread platform fact without replacing the lifecycle Goal."""

    request = _read_input(observation_path)
    if request.get("contract") != EXTERNAL_GOAL_OBSERVATION_REQUEST_CONTRACT:
        raise ValueError("external Goal observation request contract is unsupported")
    payload = deepcopy(request)
    payload["contract"] = EXTERNAL_GOAL_OBSERVATION_CONTRACT
    observation_id = _require_string(payload, "observation_id")
    projection = validate_project_lifecycle_events(project.events())
    prior = projection.get("goal_observation")
    active_thread_id = prior.get("thread_id") if isinstance(prior, Mapping) else None
    payload = _validate_external_goal_observation(
        payload, active_thread_id=active_thread_id
    )
    event = project._record_lifecycle_event(
        "research.lifecycle.external_goal_observed",
        payload,
        idempotency_key=f"lifecycle:external-goal-observation:{observation_id}",
    )
    project.materialize_views()
    return deepcopy(event["payload"])


def declare_project_goal(project: object, declaration_path: Path) -> dict:
    """Declare the repository-owned formal Goal without claiming platform control."""

    request = _read_input(declaration_path)
    if request.get("contract") != PROJECT_GOAL_DECLARATION_REQUEST_CONTRACT:
        raise ValueError("Project Goal declaration request contract is unsupported")
    projection = validate_project_lifecycle_events(project.events())
    if request.get("expected_predecessor_state") != projection.get(
        "lifecycle_state"
    ):
        raise ValueError("Project Goal declaration predecessor state drifted")
    objective = _require_string(request, "objective")
    objective_sha256 = _require_sha256(request, "objective_sha256")
    if hashlib.sha256(objective.encode("utf-8")).hexdigest() != objective_sha256:
        raise ValueError("Project Goal declaration objective hash drifted")
    architecture_ref = _validate_repository_ref(
        _require_ref(request, "target_architecture_ref")
    )
    mapping_ref = _validate_repository_ref(_require_ref(request, "system_mapping_ref"))
    goal_id = _require_string(request, "goal_id")
    declaration_basis = {
        "goal_id": goal_id,
        "objective_sha256": objective_sha256,
        "target_architecture_ref": architecture_ref,
        "system_mapping_ref": mapping_ref,
    }
    generation = request.get("goal_generation")
    if generation is not None:
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
            raise ValueError("Project Goal generation must be a positive integer")
        declaration_basis["goal_generation"] = generation
    declaration_id = "project-goal-" + hashlib.sha256(
        _canonical_json_bytes(declaration_basis)
    ).hexdigest()[:24]
    payload = {
        "contract": PROJECT_GOAL_DECLARED_CONTRACT,
        "declaration_id": declaration_id,
        "goal_id": goal_id,
        "objective": objective,
        "objective_sha256": objective_sha256,
        "declared_at": _require_string(request, "declared_at"),
        "source": _require_string(request, "source"),
        "expected_predecessor_state": _require_string(
            request, "expected_predecessor_state"
        ),
        "target_architecture_ref": architecture_ref,
        "system_mapping_ref": mapping_ref,
    }
    if generation is not None:
        payload["goal_generation"] = generation
    event = project._record_lifecycle_event(
        "research.lifecycle.project_goal_declared",
        payload,
        idempotency_key=f"lifecycle:project-goal:{goal_id}:{objective_sha256[:16]}",
    )
    project.materialize_views()
    return deepcopy(event["payload"])


def supersede_ready_phase(project: object, request_path: Path) -> dict:
    """Supersede a ready phase after a point-in-time absent platform observation."""

    payload = _read_input(request_path)
    if payload.get("contract") != PHASE_SUPERSEDED_CONTRACT:
        raise ValueError("lifecycle phase-supersession request is unsupported")
    supersession_id = _require_string(payload, "supersession_id")
    event = project._record_lifecycle_event(
        "research.lifecycle.phase_superseded",
        payload,
        idempotency_key=f"lifecycle:phase-superseded:{supersession_id}",
    )
    project.materialize_views()
    return deepcopy(event["payload"])


def supersede_active_phase_for_goal_change(
    project: object, request_path: Path
) -> dict:
    """Supersede active work after an exact replacement Goal is observed."""

    payload = _read_input(request_path)
    if payload.get("contract") != PHASE_SUPERSEDED_CONTRACT:
        raise ValueError("lifecycle phase-supersession request is unsupported")
    projection = validate_project_lifecycle_events(project.events())
    if projection.get("lifecycle_state") != "in_progress":
        raise ValueError("active phase supersession requires in-progress state")
    supersession_id = _require_string(payload, "supersession_id")
    event = project._record_lifecycle_event(
        "research.lifecycle.phase_superseded",
        payload,
        idempotency_key=f"lifecycle:phase-superseded:{supersession_id}",
    )
    project.materialize_views()
    return deepcopy(event["payload"])


def start_phase(project: object, phase_path: Path) -> dict:
    """Compile a phase against a Project Goal declaration or legacy observation."""

    request = _read_input(phase_path)
    if request.get("contract") != PHASE_REQUEST_CONTRACT:
        raise ValueError("lifecycle phase request contract is unsupported")
    projection = validate_project_lifecycle_events(project.events())
    declaration = projection.get("project_goal")
    payload_goal_fields: dict[str, object]
    if isinstance(declaration, Mapping):
        expected_goal_id = _require_string(request, "expected_goal_id")
        if expected_goal_id != declaration.get("goal_id"):
            raise ValueError("phase request Project Goal identity drifted")
        expected_objective = _require_sha256(request, "expected_objective_sha256")
        if expected_objective != declaration.get("objective_sha256"):
            raise ValueError("phase request objective hash differs from Project Goal")
        expected_fit = _require_string(request, "expected_platform_goal_fit")
        if expected_fit != projection.get("platform_goal_fit"):
            raise ValueError("phase request platform Goal fit drifted")
        declaration_generation = declaration.get("goal_generation")
        if declaration_generation is not None:
            expected_generation = request.get("expected_goal_generation")
            if expected_generation != declaration_generation:
                raise ValueError("phase request Project Goal generation drifted")
            phase_generation = request.get("phase_generation")
            if (
                not isinstance(phase_generation, int)
                or isinstance(phase_generation, bool)
                or phase_generation < 1
            ):
                raise ValueError("phase generation must be a positive integer")
        payload_goal_fields = {
            "goal_declaration_id": declaration["declaration_id"],
            "project_goal_id": declaration["goal_id"],
            "platform_goal_fit": expected_fit,
        }
        if declaration_generation is not None:
            payload_goal_fields["goal_generation"] = declaration_generation
            payload_goal_fields["phase_generation"] = phase_generation
    else:
        observation = projection.get("goal_observation")
        if not isinstance(observation, Mapping):
            raise ValueError("phase request requires a Goal observation")
        expected_thread = _require_string(request, "expected_thread_id")
        if expected_thread != observation.get("thread_id"):
            raise ValueError("phase request expected thread differs from Goal observation")
        expected_objective = _require_sha256(request, "expected_objective_sha256")
        if expected_objective != observation.get("objective_sha256"):
            raise ValueError("phase request objective hash differs from Goal observation")
        payload_goal_fields = {
            "thread_id": expected_thread,
            "goal_observation_id": observation["observation_id"],
        }
    accepted_artifact = _validate_repository_ref(
        _require_ref(request, "accepted_artifact")
    )
    basis_refs = [
        _validate_repository_ref(reference)
        for reference in _require_refs(request, "basis_refs")
    ]
    if _require_string(request, "rollback_git_commit") != ROLLBACK_COMMIT:
        raise ValueError("phase request does not bind the exact rollback baseline")
    payload = {
        "contract": PHASE_STARTED_CONTRACT,
        "phase_id": _require_string(request, "phase_id"),
        **payload_goal_fields,
        "active_module": _require_string(request, "active_module"),
        "accepted_artifact": accepted_artifact,
        "basis_refs": basis_refs,
        "rollback_git_commit": ROLLBACK_COMMIT,
        "open_risks": _require_risks(request),
        "next_route": _require_string(request, "next_route"),
        "post_completion_next_route": _require_string(
            request, "post_completion_next_route"
        ),
    }
    if "complex_runtime_pin" in request or "complex_runtime_adoption_ref" in request:
        payload["complex_runtime_pin"] = _require_string(
            request, "complex_runtime_pin"
        )
        payload["complex_runtime_adoption_ref"] = _validate_repository_ref(
            _require_ref(request, "complex_runtime_adoption_ref")
        )
    phase_id = payload["phase_id"]
    event = project._record_lifecycle_event(
        "research.lifecycle.phase_started",
        payload,
        idempotency_key=f"lifecycle:phase:{phase_id}:started",
    )
    project.materialize_views()
    return deepcopy(event["payload"])


def accept_lifecycle_beat(project: object, request_path: Path) -> dict:
    """Accept one content-bound forward artifact without ending its phase."""

    request = _read_input(request_path)
    if request.get("contract") != BEAT_REQUEST_CONTRACT:
        raise ValueError("lifecycle beat request contract is unsupported")
    events = project.events()
    projection = validate_project_lifecycle_events(events)
    if projection.get("lifecycle_state") != "in_progress":
        raise ValueError("lifecycle beat requires an active phase")
    phase_id = _require_string(request, "phase_id")
    if phase_id != projection.get("current_phase_id"):
        raise ValueError("lifecycle beat phase differs from the active phase")
    payload = {
        "contract": BEAT_ACCEPTED_CONTRACT,
        "beat_id": _require_string(request, "beat_id"),
        "phase_id": phase_id,
        "accepted_at": _require_string(request, "accepted_at"),
        "active_module": _require_string(request, "active_module"),
        "accepted_artifact": _validate_repository_ref(
            _require_ref(request, "accepted_artifact")
        ),
        "open_risks": _require_risks(request),
        "next_route": _require_string(request, "next_route"),
    }
    open_rejection = projection.get("rejected_beat_review")
    closure = request.get("closes_review_rejection")
    beat_key = f"lifecycle:phase:{phase_id}:beat:{payload['beat_id']}"
    if isinstance(open_rejection, Mapping):
        if not isinstance(closure, Mapping):
            raise ValueError(
                "accepted beat must explicitly close the open review rejection"
            )
        expected_closure = {
            "rejection_event_id": open_rejection.get("rejection_event_id"),
            "review_id": open_rejection.get("review_id"),
            "rejected_beat_id": open_rejection.get("rejected_beat_id"),
        }
        if set(closure) != set(expected_closure) or dict(closure) != expected_closure:
            raise ValueError("accepted beat review-rejection closure drifted")
        payload["closes_review_rejection"] = expected_closure
    elif closure is not None:
        if (
            not isinstance(closure, Mapping)
            or set(closure)
            != {"rejection_event_id", "review_id", "rejected_beat_id"}
            or any(not isinstance(value, str) or not value for value in closure.values())
        ):
            raise ValueError("accepted beat review-rejection closure is invalid")
        payload["closes_review_rejection"] = dict(closure)
        if not any(event.get("idempotency_key") == beat_key for event in events):
            raise ValueError("accepted beat cannot close a nonexistent review rejection")
    event = project._record_lifecycle_event(
        "research.lifecycle.beat_accepted",
        payload,
        idempotency_key=beat_key,
    )
    project.materialize_views()
    return deepcopy(event["payload"])


def reject_lifecycle_beat_review(project: object, request_path: Path) -> dict:
    """Append an independent-review correction without rewriting accepted history."""

    request = _read_input(request_path)
    if request.get("contract") != BEAT_REVIEW_REJECTION_REQUEST_CONTRACT:
        raise ValueError("lifecycle beat-review rejection request is unsupported")
    projection = validate_project_lifecycle_events(project.events())
    if projection.get("lifecycle_state") != "in_progress":
        raise ValueError("lifecycle beat-review rejection requires an active phase")
    phase_id = _require_string(request, "phase_id")
    if phase_id != projection.get("current_phase_id"):
        raise ValueError("rejected beat review phase differs from the active phase")
    payload = {
        "contract": BEAT_REVIEW_REJECTED_CONTRACT,
        "review_id": _require_string(request, "review_id"),
        "phase_id": phase_id,
        "rejected_beat_id": _require_string(request, "rejected_beat_id"),
        "rejected_artifact": _validate_repository_ref(
            _require_ref(request, "rejected_artifact")
        ),
        "reviewed_at": _require_string(request, "reviewed_at"),
        "active_module": _require_string(request, "active_module"),
        "open_risks": _require_risks(request),
        "next_route": _require_string(request, "next_route"),
    }
    event = project._record_lifecycle_event(
        "research.lifecycle.beat_review_rejected",
        payload,
        idempotency_key=f"lifecycle:phase:{phase_id}:review:{payload['review_id']}",
    )
    project.materialize_views()
    return deepcopy(event["payload"])


def bootstrap_lifecycle(
    project: object, observation_path: Path, phase_path: Path
) -> dict:
    """Initialize, observe and start through the same replayable interfaces."""

    initialize_lifecycle(project)
    observe_goal(project, observation_path)
    start_phase(project, phase_path)
    return deepcopy(project.materialize_views()["lifecycle"])


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid lifecycle JSON at {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"lifecycle JSON at {path} must be an object")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _walk_keys(value: object) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key)
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def _tree_sha256(root: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file())
    for path in files:
        payload = path.read_bytes()
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(payload).hexdigest().encode("ascii"))
        digest.update(b"\0")
        digest.update(str(len(payload)).encode("ascii"))
        digest.update(b"\n")
    return len(files), digest.hexdigest()


def load_lifecycle_contract(root: Path) -> dict:
    return _read_json(root / CONVERGENCE_PATH)


def load_product_architecture(root: Path) -> dict:
    return _read_json(root / PRODUCT_ARCHITECTURE_PATH)


def validate_stable_product_architecture(root: Path) -> list[str]:
    errors: list[str] = []
    try:
        architecture = load_product_architecture(root)
    except ValueError as error:
        return [str(error)]
    if architecture.get("schema_version") != 1 or architecture.get(
        "contract"
    ) != PRODUCT_ARCHITECTURE_CONTRACT:
        errors.append("stable Council product architecture identity is unsupported")
    if FORBIDDEN_LIVE_STATE_FIELDS.intersection(_walk_keys(architecture)):
        errors.append("stable Council product architecture contains live state")
    if architecture.get("product_direction") != "council_default":
        errors.append("stable Council product direction is absent")
    projection = architecture.get("dynamic_projection")
    if projection != {
        "authority": "derived_from_append_only_research_lifecycle_events",
        "path": "outputs/council-default-lifecycle/lifecycle-state.json",
        "sha256_embedded": False,
    }:
        errors.append("dynamic lifecycle projection seam is unsupported")
    policies = architecture.get("claim_policy")
    if not isinstance(policies, Mapping) or set(policies) != {
        "pending",
        "formal",
        "rollback",
    }:
        errors.append("state-keyed claim policy is incomplete")
    else:
        for mode, policy in policies.items():
            unsupported = set(policy.get("unsupported") or []) if isinstance(
                policy, Mapping
            ) else set()
            if not TRUE_UNSUPPORTED_CLAIMS.issubset(unsupported):
                errors.append(f"{mode} claim policy exceeds the true claim ceiling")
        if "council_already_formal_operational_truth" not in set(
            policies["pending"].get("unsupported") or []
        ):
            errors.append("pending claim policy permits premature formal truth")
        if "council_already_formal_operational_truth" not in set(
            policies["formal"].get("supported") or []
        ):
            errors.append("formal claim policy omits accepted formal truth")
    historical = architecture.get("historical_acceptance")
    if not isinstance(historical, Mapping):
        errors.append("historical Council acceptance pointers are absent")
    else:
        for label in ("legacy_product_contract", "cutover_acceptance"):
            reference = historical.get(label)
            if not isinstance(reference, Mapping):
                errors.append(f"historical {label} pointer is absent")
                continue
            path = root / str(reference.get("path", ""))
            if not path.is_file() or _sha256(path) != reference.get("sha256"):
                errors.append(f"historical {label} pointer drifted")
    rollback = architecture.get("rollback")
    if not isinstance(rollback, Mapping) or rollback.get("git_commit") != (
        "22d0576ad1a3f25ceef05ec5940fb02a848a27e9"
    ):
        errors.append("exact single-Controller rollback is absent")
    elif not rollback.get("files"):
        errors.append("exact single-Controller rollback files are absent")
    else:
        for row in rollback["files"]:
            try:
                payload = subprocess.run(
                    [
                        "git",
                        "show",
                        f"{rollback['git_commit']}:{row['path']}",
                    ],
                    cwd=root,
                    check=True,
                    capture_output=True,
                ).stdout
            except (OSError, subprocess.CalledProcessError):
                errors.append(f"rollback object is unavailable: {row.get('path')}")
                continue
            if hashlib.sha256(payload).hexdigest() != row.get("sha256"):
                errors.append(f"rollback object drifted: {row.get('path')}")
    return errors


def validate_lifecycle_contract(root: Path) -> list[str]:
    errors: list[str] = []
    try:
        contract = load_lifecycle_contract(root)
    except ValueError as error:
        return [str(error)]
    if contract.get("schema_version") != 1 or contract.get(
        "contract"
    ) != CONVERGENCE_CONTRACT:
        errors.append("lifecycle convergence contract identity is unsupported")
    if set(contract.get("event_types") or []) != {
        "research.lifecycle.control_plane_initialized",
        "research.lifecycle.project_goal_declared",
        "research.lifecycle.goal_observed",
        "research.lifecycle.phase_started",
        "research.lifecycle.beat_accepted",
        "research.lifecycle.beat_review_rejected",
        "research.lifecycle.phase_ready_to_complete",
        "research.lifecycle.phase_superseded",
        "research.lifecycle.goal_completion_observed",
        "research.lifecycle.rollback_observed",
        "research.lifecycle.recovery_observed",
    }:
        errors.append("lifecycle event family is incomplete")
    if set(contract.get("states") or {}) != {
        "pending",
        "in_progress",
        "ready_to_complete",
        "superseded",
        "goal_completed",
        "rollback_active",
        "recovered",
    }:
        errors.append("lifecycle state set is incomplete")
    stable = contract.get("stable_contracts")
    if not isinstance(stable, Mapping) or set(
        stable.get("forbidden_live_state_fields") or []
    ) != FORBIDDEN_LIVE_STATE_FIELDS:
        errors.append("stable-contract live-state prohibition is incomplete")
    immutable = contract.get("immutable_history")
    if not isinstance(immutable, Mapping):
        errors.append("immutable lifecycle history contract is absent")
        return errors
    for label in ("legacy_product_contract", "cutover_acceptance"):
        reference = immutable.get(label)
        if not isinstance(reference, Mapping):
            errors.append(f"immutable {label} pointer is absent")
            continue
        path = root / str(reference.get("path", ""))
        if not path.is_file() or _sha256(path) != reference.get("sha256"):
            errors.append(f"immutable {label} pointer drifted")
    tree = immutable.get("final_output_tree")
    if not isinstance(tree, Mapping):
        errors.append("immutable final output tree identity is absent")
    else:
        path = root / str(tree.get("path", ""))
        if not path.is_dir():
            errors.append("immutable final output tree is absent")
        else:
            count, digest = _tree_sha256(path)
            if count != tree.get("file_count") or digest != tree.get("sha256"):
                errors.append("immutable final output tree drifted")
    return errors


def _projection_datetime(projection: Mapping[str, object]) -> datetime | None:
    candidates: list[datetime] = []
    project_goal = projection.get("project_goal")
    if isinstance(project_goal, Mapping) and isinstance(
        project_goal.get("declared_at"), str
    ):
        try:
            candidates.append(
                _parse_timestamp(
                    project_goal["declared_at"], "Project Goal declared_at"
                )
            )
        except ValueError:
            pass
    observation = projection.get("goal_observation")
    if isinstance(observation, Mapping) and isinstance(
        observation.get("observed_at"), str
    ):
        try:
            candidates.append(
                _parse_timestamp(
                    observation["observed_at"], "Goal observation observed_at"
                )
            )
        except ValueError:
            pass
    external_observation = projection.get("external_goal_observation")
    if isinstance(external_observation, Mapping) and isinstance(
        external_observation.get("observed_at"), str
    ):
        try:
            candidates.append(
                _parse_timestamp(
                    external_observation["observed_at"],
                    "external Goal observation observed_at",
                )
            )
        except ValueError:
            pass
    last_lifecycle_at = projection.get("last_lifecycle_at")
    if isinstance(last_lifecycle_at, str):
        try:
            candidates.append(
                _parse_timestamp(last_lifecycle_at, "last lifecycle event occurred_at")
            )
        except ValueError:
            pass
    return max(candidates) if candidates else None


def validate_context_convergence(
    context: str,
    projection: Mapping[str, object],
    architecture: Mapping[str, object],
) -> list[str]:
    errors: list[str] = []
    project_goal = projection.get("project_goal")
    if isinstance(project_goal, Mapping):
        goal_id = project_goal.get("goal_id")
        thread_line = re.search(r"(?m)^- thread_goal: (.+)$", context)
        if (
            not isinstance(goal_id, str)
            or thread_line is None
            or goal_id not in thread_line.group(1)
        ):
            errors.append("CONTEXT thread_goal does not match Project Goal declaration")
        try:
            observed_date = _projection_date(projection)
        except ValueError:
            observed_date = None
        updated = re.search(
            r"(?m)^updated_at: (\d{4}-\d{2}-\d{2}) Asia/Shanghai$", context
        )
        if observed_date is None or updated is None or updated.group(1) != observed_date:
            errors.append("CONTEXT updated_at does not match lifecycle truth")
        required_fields = (
            "thread_goal",
            "current_basis",
            "accepted_beat",
            "active_module",
            "open_risks",
            "next_route",
        )
        for field in required_fields:
            matches = re.findall(rf"(?m)^- {re.escape(field)}: (.+)$", context)
            if len(matches) != 1:
                errors.append(f"CONTEXT must contain exactly one {field}")
        module = projection.get("active_module")
        if f"- active_module: {module}" not in context:
            errors.append("CONTEXT active_module does not match lifecycle projection")
        routes = re.findall(r"(?m)^- next_route: (.+)$", context)
        if len(routes) != 1:
            errors.append("CONTEXT must contain exactly one next_route")
        elif routes[0] != projection.get("next_route"):
            errors.append("CONTEXT next_route does not match lifecycle projection")
        fit = projection.get("platform_goal_fit")
        if f"platform_goal_fit: {fit}" not in context:
            errors.append("CONTEXT platform Goal fit does not match lifecycle projection")
        architecture_ref = project_goal.get("target_architecture_ref")
        if isinstance(architecture_ref, Mapping) and str(
            architecture_ref.get("path")
        ) not in context:
            errors.append("CONTEXT D-18 architecture pointer is absent")
        artifact = projection.get("accepted_artifact")
        artifact_line = re.search(r"(?m)^- accepted_beat: (.+)$", context)
        if isinstance(artifact, Mapping) and artifact_line is not None:
            expected_ref = f"{artifact.get('path')}#sha256={artifact.get('sha256')}"
            if expected_ref not in artifact_line.group(1):
                errors.append("CONTEXT accepted_beat does not match lifecycle projection")
        risks = projection.get("open_risks")
        risk_line = re.search(r"(?m)^- open_risks: (.+)$", context)
        expected_risks = "none" if risks == [] else " | ".join(risks or [])
        if risk_line is None or risk_line.group(1) != expected_risks:
            errors.append("CONTEXT open_risks do not match lifecycle projection")
        supported, unsupported = _d18_claim_lines(projection.get("lifecycle_state"))
        if f"- Supported: {supported}" not in context:
            errors.append(
                "CONTEXT D-18 supported claim ceiling conflicts with lifecycle state"
            )
        if f"- Unsupported: {unsupported}" not in context:
            errors.append(
                "CONTEXT D-18 unsupported claim ceiling conflicts with lifecycle state"
            )
        return errors
    observation = projection.get("goal_observation")
    if not isinstance(observation, Mapping):
        return ["lifecycle Goal observation is absent"]
    thread_id = observation.get("thread_id")
    thread_line = re.search(r"(?m)^- thread_goal: (.+)$", context)
    if (
        not isinstance(thread_id, str)
        or thread_line is None
        or thread_id not in thread_line.group(1)
    ):
        errors.append("CONTEXT thread_goal does not match lifecycle Goal observation")
    try:
        observed_date = _projection_date(projection)
    except ValueError:
        observed_date = None
    updated = re.search(r"(?m)^updated_at: (\d{4}-\d{2}-\d{2}) Asia/Shanghai$", context)
    if observed_date is None or updated is None or updated.group(1) != observed_date:
        errors.append("CONTEXT updated_at does not match the lifecycle observation")
    required_fields = (
        "thread_goal",
        "current_basis",
        "accepted_beat",
        "active_module",
        "open_risks",
        "next_route",
    )
    for field in required_fields:
        matches = re.findall(rf"(?m)^- {re.escape(field)}: (.+)$", context)
        if len(matches) != 1:
            errors.append(f"CONTEXT must contain exactly one {field}")
    module = projection.get("active_module")
    if f"- active_module: {module}" not in context:
        errors.append("CONTEXT active_module does not match lifecycle projection")
    routes = re.findall(r"(?m)^- next_route: (.+)$", context)
    if len(routes) != 1:
        errors.append("CONTEXT must contain exactly one next_route")
    elif routes[0] != projection.get("next_route"):
        errors.append("CONTEXT next_route does not match lifecycle projection")
    policies = architecture.get("claim_policy")
    mode = projection.get("claim_mode")
    policy = policies.get(mode) if isinstance(policies, Mapping) else None
    formal_claim = "Council-default is formal operational truth" in context
    if not isinstance(policy, Mapping):
        errors.append("lifecycle claim mode has no stable policy")
    elif mode == "pending" and formal_claim:
        errors.append("CONTEXT claim ceiling conflicts with lifecycle claim mode")
    elif mode == "formal" and not formal_claim:
        errors.append("CONTEXT claim ceiling conflicts with lifecycle claim mode")
    elif mode == "rollback" and "single Controller rollback is active" not in context:
        errors.append("CONTEXT claim ceiling conflicts with lifecycle claim mode")
    artifact = projection.get("accepted_artifact")
    artifact_line = re.search(r"(?m)^- accepted_beat: (.+)$", context)
    if isinstance(artifact, Mapping) and artifact_line is not None:
        expected_ref = f"{artifact.get('path')}#sha256={artifact.get('sha256')}"
        if expected_ref not in artifact_line.group(1):
            errors.append("CONTEXT accepted_beat does not match lifecycle projection")
    risks = projection.get("open_risks")
    risk_line = re.search(r"(?m)^- open_risks: (.+)$", context)
    expected_risks = "none" if risks == [] else " | ".join(risks or [])
    if risk_line is None or risk_line.group(1) != expected_risks:
        errors.append("CONTEXT open_risks do not match lifecycle projection")
    return errors


def validate_progress_convergence(
    progress: str, projection: Mapping[str, object]
) -> list[str]:
    errors: list[str] = []
    marker = "current_status_authority: lifecycle_projection_only"
    observation = projection.get("goal_observation")
    thread_id = observation.get("thread_id") if isinstance(observation, Mapping) else None
    if isinstance(thread_id, str) and thread_id in progress and marker not in progress:
        errors.append("historical progress contains the active Goal identity")
    if re.search(r"(?i)next_route\s*[:=]", progress) and marker not in progress:
        errors.append("historical progress contains a live next_route")
    return errors


def _canonical_json_bytes(payload: Mapping[str, object]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _projection_date(projection: Mapping[str, object]) -> str:
    value = _projection_datetime(projection)
    if value is None:
        raise ValueError("lifecycle projection has no valid observation or event time")
    return value.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()


def _claim_lines(mode: object) -> tuple[str, str]:
    if mode == "formal":
        supported = (
            "Council-default is formal operational truth; the single Controller "
            "fast path and exact rollback remain available."
        )
    elif mode == "pending":
        supported = (
            "Council-default is an approved target; formal operational truth has "
            "not yet been established."
        )
    elif mode == "rollback":
        supported = (
            "The single Controller rollback is active; Council history is retained "
            "for append-only recovery."
        )
    else:
        raise ValueError("lifecycle claim mode is unsupported")
    unsupported = (
        "Council is superior; Freshwater is a two-route comparative LCA; or a "
        "majority may override TianGong official facts."
    )
    return supported, unsupported


def _d18_claim_lines(state: object) -> tuple[str, str]:
    if state == "goal_completed":
        return (
            "D-18 operational foundation is accepted; Project Record is the sole "
            "accepted project truth; the prior Council runtime remains preserved "
            "as operational history and rollback evidence.",
            "D-18 does not yet support every LCA workflow; remote model execution "
            "is not verified; Council is not proven superior; a case does not define "
            "the product; and model/tool output may not directly write formal facts.",
        )
    return (
        "D-18 target architecture is approved; its migration is active; the prior "
        "Council runtime remains preserved as operational history and rollback evidence.",
        "D-18 implementation is already complete; Council is the D-18 orchestrator; "
        "a case defines the product; or model/tool output may directly write formal facts.",
    )


def render_context(
    projection: Mapping[str, object], architecture: Mapping[str, object]
) -> str:
    """Render the compact authoritative Hot projection from lifecycle truth."""

    project_goal = projection.get("project_goal")
    if isinstance(project_goal, Mapping):
        goal_id = _require_string(project_goal, "goal_id")
        state = _require_string(projection, "lifecycle_state")
        fit = _require_string(projection, "platform_goal_fit")
        artifact = _require_ref(projection, "accepted_artifact")
        architecture_ref = _require_ref(
            project_goal, "target_architecture_ref"
        )
        mapping_ref = _require_ref(project_goal, "system_mapping_ref")
        active_module = _require_string(projection, "active_module")
        next_route = _require_string(projection, "next_route")
        risks = projection.get("open_risks")
        if not isinstance(risks, list) or any(
            not isinstance(row, str) for row in risks
        ):
            raise ValueError("lifecycle projection open_risks is invalid")
        risk_text = "none" if not risks else " | ".join(risks)
        artifact_ref = f"{artifact['path']}#sha256={artifact['sha256']}"
        artifact_description = (
            f"`{artifact_ref}` remains the immutable reviewed pointer, but its "
            "current status is `independent_review_rejected`; it is not an accepted beat."
            if projection.get("accepted_artifact_status")
            == "independent_review_rejected"
            else f"`{artifact_ref}` is the current content-bound lifecycle artifact."
        )
        observation = projection.get("external_goal_observation")
        observation_kind = "cross-thread point-in-time observation"
        if not isinstance(observation, Mapping):
            observation = projection.get("goal_observation")
            observation_kind = "lifecycle Goal observation"
        observed_text = "none"
        if isinstance(observation, Mapping):
            observed_text = (
                f"{observation.get('thread_id')} / {observation.get('observed_status')} "
                f"at {observation.get('observed_at')} ({observation_kind})"
            )
        supported, unsupported = _d18_claim_lines(state)
        complex_pin = projection.get(
            "complex_runtime_pin",
            "f055155c782ae315bd2c68e7fd332b10f4b68596",
        )
        complex_ref = projection.get("complex_runtime_adoption_ref")
        if isinstance(complex_ref, Mapping):
            complex_adoption = (
                f"{complex_ref.get('path')}#sha256={complex_ref.get('sha256')}"
            )
        else:
            complex_adoption = (
                "docs/complex-runtime-adoption.json#sha256="
                "a2b8d564b9e09d5927cf248f1cef94da3f98fec64a9e7be57826242edb698423"
            )
        return f"""# LCA Research Agent — Current State

authority: This file is the single authoritative Hot State projection rebuilt from append-only `research.lifecycle.*` events.
updated_at: {_projection_date(projection)} Asia/Shanghai
project_nature: mixed
active_scope: project
active_work_nature: execution_delivery
phase_goal: Implement the approved D-18 TianGong-native LCA research Agent through the Project Record.
platform_goal_fit: {fit}
active_artifact_contract: {artifact_ref}
topology: single Research Orchestrator + deterministic harness; optional evaluators only
complex_runtime_pin: {complex_pin}
complex_runtime_adoption: {complex_adoption}
dynamic_truth: outputs/council-default-lifecycle/research-events.jsonl
dynamic_projection: outputs/council-default-lifecycle/lifecycle-state.json
stable_product_contract: {architecture_ref['path']}#sha256={architecture_ref['sha256']}
system_mapping: {mapping_ref['path']}#sha256={mapping_ref['sha256']}
legacy_operational_baseline: Council-default remains historical rollback evidence, not the D-18 target.

## Recovery Anchor

- thread_goal: Project Goal `{goal_id}` is content-bound in Project Record; platform observation `{observed_text}` has fit `{fit}`; lifecycle state is `{state}`.
- current_basis: D-18 architecture and mapping above, append-only lifecycle history, TianGong authority boundaries, and preserved legacy rollback evidence.
- accepted_beat: {artifact_description}
- active_module: {active_module}
- open_risks: {risk_text}
- next_route: {next_route}

## Stable Product Invariants

- Project Record is the only accepted project truth; model, tool, Council, conversation and memory outputs remain candidates until assurance acceptance.
- TianGong owns adopted official identity/version, TIDAS semantics, Provider connections, methods, native tools and native results.
- Research Orchestrator is the single control core; Council is optional, MCP is a capability protocol, Skills are thin, and cases are fixtures.
- Credentials, payment, publication, external/shared writes, irreversible actions and unsupported strong public claims remain human-owned.

## Claim Ceiling

- Supported: {supported}
- Unsupported: {unsupported}
"""

    observation = projection.get("external_goal_observation")
    observation_kind = "cross-thread point-in-time observation"
    if not isinstance(observation, Mapping):
        observation = projection.get("goal_observation")
        observation_kind = "lifecycle Goal observation"
    if not isinstance(observation, Mapping):
        raise ValueError("lifecycle Goal observation is absent")
    policies = architecture.get("claim_policy")
    mode = projection.get("claim_mode")
    if not isinstance(policies, Mapping) or mode not in policies:
        raise ValueError("stable architecture has no policy for lifecycle claim mode")
    thread_id = _require_string(observation, "thread_id")
    status = _require_string(observation, "observed_status")
    state = _require_string(projection, "lifecycle_state")
    artifact = _require_ref(projection, "accepted_artifact")
    active_module = _require_string(projection, "active_module")
    next_route = _require_string(projection, "next_route")
    risks = projection.get("open_risks")
    if not isinstance(risks, list) or any(not isinstance(row, str) for row in risks):
        raise ValueError("lifecycle projection open_risks is invalid")
    risk_text = "none" if not risks else " | ".join(risks)
    supported, unsupported = _claim_lines(mode)
    if state == "ready_to_complete":
        goal_fit = "active_goal_ready_to_complete_platform_completion_unobserved"
    elif state == "goal_completed":
        goal_fit = "observed_platform_goal_completion"
    elif state == "rollback_active":
        goal_fit = "rollback_active_goal_observation_point_in_time"
    else:
        goal_fit = f"point_in_time_{status}_goal_matches_lifecycle_phase"
    artifact_ref = f"{artifact['path']}#sha256={artifact['sha256']}"
    return f"""# LCA Research Agent — Current State

authority: This file is the single authoritative Hot State projection rebuilt from append-only `research.lifecycle.*` events.
updated_at: {_projection_date(projection)} Asia/Shanghai
project_nature: mixed
active_scope: project
active_work_nature: execution_delivery
phase_goal: Execute the active lifecycle-bound Goal through its accepted artifact contract.
codex_goal_fit: {goal_fit}
active_artifact_contract: {artifact_ref}
topology: manager beat + deterministic harness + bounded clean evaluator; no standing lane
complex_runtime_pin: f055155c782ae315bd2c68e7fd332b10f4b68596
complex_runtime_adoption: docs/complex-runtime-adoption.json#sha256=a2b8d564b9e09d5927cf248f1cef94da3f98fec64a9e7be57826242edb698423
dynamic_truth: outputs/council-default-lifecycle/research-events.jsonl
dynamic_projection: outputs/council-default-lifecycle/lifecycle-state.json
stable_product_contract: docs/council-default-product-architecture.json
immutable_cutover_acceptance: docs/council-default-cutover-acceptance.json

## Recovery Anchor

- thread_goal: Goal `{thread_id}` has point-in-time platform status `{status}` from the {observation_kind} and lifecycle state `{state}`; this repository does not claim live Codex Goal access.
- current_basis: Append-only lifecycle events plus stable Council architecture, immutable cutover acceptance `4e82740`, TianGong authority boundaries, and exact rollback `22d0576ad1a3f25ceef05ec5940fb02a848a27e9`.
- accepted_beat: `{artifact_ref}` is the current content-bound lifecycle artifact.
- active_module: {active_module}
- open_risks: {risk_text}
- next_route: {next_route}

## Stable Product Invariants

- TianGong owns official identity/version, TIDAS semantics, Provider connections, LCIA methods, native tools and native results; Council cannot correct them by vote.
- Research Kernel owns append-only event history, Artifact Bus registration, content addressing, idempotency, projections, replay, recovery and effect boundaries.
- Credentials, payment, contracts, publication, external/shared writes, irreversible actions and unsupported strong public claims remain human-owned.
- Stable rules live in `docs/council-default-product-architecture.json`; cutover records and registered acceptance outputs are immutable history, not live state.

## Claim Ceiling

- Supported: {supported}
- Unsupported: {unsupported}
"""


def render_lifecycle_progress(projection: Mapping[str, object]) -> str:
    project_goal = projection.get("project_goal")
    if isinstance(project_goal, Mapping):
        artifact = _require_ref(projection, "accepted_artifact")
        risks = projection.get("open_risks") or []
        risk_lines = "\n".join(f"- {risk}" for risk in risks) or "- none"
        return f"""# D-18 Lifecycle Status

- Project Goal: `{project_goal.get('goal_id')}` declared at `{project_goal.get('declared_at')}`
- Platform Goal fit: `{projection.get('platform_goal_fit')}`
- Lifecycle state: `{projection.get('lifecycle_state')}`
- Active module: `{projection.get('active_module')}`
- Accepted artifact: `{artifact['path']}#sha256={artifact['sha256']}`
- Next route: `{projection.get('next_route')}`

## Open risks

{risk_lines}
"""
    observation = projection.get("goal_observation")
    if not isinstance(observation, Mapping):
        raise ValueError("lifecycle Goal observation is absent")
    artifact = _require_ref(projection, "accepted_artifact")
    risks = projection.get("open_risks") or []
    risk_lines = "\n".join(f"- {risk}" for risk in risks) or "- none"
    return f"""# Council Lifecycle Status

- Goal observation: `{observation.get('thread_id')}` / `{observation.get('observed_status')}` at `{observation.get('observed_at')}`
- Lifecycle state: `{projection.get('lifecycle_state')}`
- Formal status: `{projection.get('formal_status')}`
- Active module: `{projection.get('active_module')}`
- Accepted artifact: `{artifact['path']}#sha256={artifact['sha256']}`
- Next route: `{projection.get('next_route')}`

## Open risks

{risk_lines}
"""


def validate_lifecycle_projection(
    root: Path, projection: Mapping[str, object]
) -> list[str]:
    errors: list[str] = []
    required = set(
        load_lifecycle_contract(root).get("projection", {}).get(
            "required_fields", []
        )
    )
    missing = required.difference(projection)
    if missing:
        errors.append(f"lifecycle projection is missing fields: {sorted(missing)}")
        return errors
    if projection.get("contract") != LIFECYCLE_PROJECTION_CONTRACT:
        errors.append("lifecycle projection contract is unsupported")
    mapping = {
        "pending": "approved_pending_cutover",
        "formal": "formal_operational_truth",
        "rollback": "single_controller_rollback_active",
    }
    if mapping.get(projection.get("claim_mode")) != projection.get("formal_status"):
        errors.append("lifecycle projection claim and formal status conflict")
    immutable_paths = {
        "docs/council-default-product-contract.json",
        "docs/council-default-cutover-acceptance.json",
    }
    artifact = projection.get("accepted_artifact")
    try:
        accepted = _require_ref({"accepted_artifact": artifact}, "accepted_artifact")
    except ValueError as error:
        errors.append(str(error))
    else:
        if accepted["path"] in immutable_paths:
            errors.append("immutable historical acceptance is used as live state")
        path = root / accepted["path"]
        if not path.is_file():
            errors.append("lifecycle accepted artifact is absent")
        elif _sha256(path) != accepted["sha256"]:
            errors.append("lifecycle accepted artifact drifted")
    for reference in projection.get("basis_refs") or []:
        try:
            _require_ref({"basis_ref": reference}, "basis_ref")
        except ValueError as error:
            errors.append(str(error))
    rollback = projection.get("rollback_git_commit")
    if rollback is not None and rollback != ROLLBACK_COMMIT:
        errors.append("lifecycle projection rollback pointer drifted")
    registered = projection.get("registered_artifacts")
    if projection.get("ready_event_id") is not None or registered is not None:
        if not isinstance(registered, list) or not registered:
            errors.append("lifecycle ready registered artifacts are absent")
        else:
            for artifact in registered:
                if not isinstance(artifact, Mapping):
                    errors.append("lifecycle ready registered artifact is invalid")
                    continue
                relative = artifact.get("path")
                if not isinstance(relative, str):
                    errors.append("lifecycle ready registered artifact path is invalid")
                    continue
                path = root / relative
                if not path.is_file():
                    errors.append(f"lifecycle registered artifact is absent: {relative}")
                elif (
                    _sha256(path) != artifact.get("sha256")
                    or path.stat().st_size != artifact.get("bytes")
                ):
                    errors.append(f"lifecycle registered artifact drifted: {relative}")
    return errors


def lifecycle_derivative_bytes(
    root: Path, project: object, projection: Mapping[str, object]
) -> dict[Path, bytes]:
    architecture = load_product_architecture(root)
    snapshot = project._project(project.events())
    lifecycle_snapshot = {
        "contract": "lca_research_kernel.lifecycle_workspace_projection/v1",
        "project_id": snapshot.get("project_id"),
        "title": snapshot.get("title"),
        "objective": snapshot.get("objective"),
        "last_event_id": snapshot.get("last_event_id"),
        "lifecycle": deepcopy(dict(projection)),
    }
    machine_snapshot = {
        **lifecycle_snapshot,
        "contract": "lca_research_kernel.lifecycle_workspace_machine_context/v1",
    }
    return {
        project.workspace / project.SNAPSHOT_FILE: _canonical_json_bytes(
            lifecycle_snapshot
        ),
        project.workspace / project.MACHINE_FILE: _canonical_json_bytes(machine_snapshot),
        project.workspace / project.HUMAN_FILE: render_lifecycle_progress(
            projection
        ).encode("utf-8"),
        project.workspace / "lifecycle-state.json": _canonical_json_bytes(projection),
        project.workspace / "human-status.md": render_lifecycle_progress(projection).encode(
            "utf-8"
        ),
        root / "CONTEXT.md": render_context(projection, architecture).encode("utf-8"),
    }


def write_lifecycle_derivatives(root: Path, project: object) -> dict:
    snapshot = project._project(project.events())
    projection = snapshot.get("lifecycle")
    if not isinstance(projection, Mapping):
        raise ValueError("Project has no lifecycle projection")
    derivatives = lifecycle_derivative_bytes(root, project, projection)
    with tempfile.TemporaryDirectory(dir=root) as temporary:
        staging = Path(temporary)
        staged: list[tuple[Path, Path]] = []
        for index, (destination, content) in enumerate(derivatives.items()):
            candidate = staging / str(index)
            candidate.write_bytes(content)
            staged.append((candidate, destination))
        for candidate, destination in staged:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(candidate, destination)
    return deepcopy(dict(projection))


def verify_state_convergence(root: Path, workspace: Path) -> list[str]:
    """Read-only convergence audit across events and every current projection."""

    errors = [
        *validate_lifecycle_contract(root),
        *validate_stable_product_architecture(root),
    ]
    try:
        from .project import ResearchProject

        project = ResearchProject.open(workspace)
        snapshot = project._project(project.events())
    except (OSError, ValueError) as error:
        return [*errors, f"lifecycle event replay failed: {error}"]
    projection = snapshot.get("lifecycle")
    if not isinstance(projection, Mapping):
        return [*errors, "lifecycle projection is absent after replay"]
    errors.extend(validate_lifecycle_projection(root, projection))
    expected = lifecycle_derivative_bytes(root, project, projection)
    for path, content in expected.items():
        if not path.is_file():
            errors.append(f"derived lifecycle surface is absent: {path.relative_to(root)}")
        elif path.read_bytes() != content:
            errors.append(f"derived lifecycle surface drifted: {path.relative_to(root)}")
    context_path = root / "CONTEXT.md"
    if context_path.is_file():
        errors.extend(
            validate_context_convergence(
                context_path.read_text(encoding="utf-8"),
                projection,
                load_product_architecture(root),
            )
        )
    progress_path = root / ".superpowers/sdd/progress.md"
    if not progress_path.is_file():
        errors.append("historical SDD progress is absent")
    else:
        errors.extend(
            validate_progress_convergence(
                progress_path.read_text(encoding="utf-8"), projection
            )
        )
    return errors


def _immutable_history_snapshot(
    root: Path,
) -> tuple[dict[Path, bytes], dict[Path, tuple[Path, ...]]]:
    contract = load_lifecycle_contract(root)
    immutable = contract.get("immutable_history") or {}
    paths: list[Path] = []
    for label in ("legacy_product_contract", "cutover_acceptance"):
        reference = immutable.get(label)
        if isinstance(reference, Mapping):
            path = root / str(reference.get("path", ""))
            if path.is_file():
                paths.append(path)
    tree = immutable.get("final_output_tree")
    tree_inventories: dict[Path, tuple[Path, ...]] = {}
    if isinstance(tree, Mapping):
        directory = root / str(tree.get("path", ""))
        if directory.is_dir():
            tree_files = tuple(
                sorted(path for path in directory.rglob("*") if path.is_file())
            )
            paths.extend(tree_files)
            tree_inventories[directory] = tree_files
    return {path: path.read_bytes() for path in paths}, tree_inventories


def _restore_and_reject_immutable_drift(
    snapshot: tuple[Mapping[Path, bytes], Mapping[Path, tuple[Path, ...]]],
) -> None:
    files, tree_inventories = snapshot
    drifted = [
        path
        for path, content in files.items()
        if not path.is_file() or path.read_bytes() != content
    ]
    added: list[Path] = []
    for directory, expected in tree_inventories.items():
        current = {
            path for path in directory.rglob("*") if path.is_file()
        }
        added.extend(sorted(current - set(expected)))
    if not drifted and not added:
        return
    for path in added:
        path.unlink()
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or path.read_bytes() != content:
            path.write_bytes(content)
    raise ValueError("finalizer command mutated immutable history")


def _allowed_finalizer_command(
    command: Sequence[object], contract: Mapping[str, object]
) -> bool:
    if not isinstance(command, list) or not command or any(
        not isinstance(part, str) or not part for part in command
    ):
        return False
    finalizer = contract.get("finalizer")
    prefixes = (
        finalizer.get("allowed_command_prefixes", [])
        if isinstance(finalizer, Mapping)
        else []
    )
    return any(command[: len(prefix)] == prefix for prefix in prefixes)


def _sanitized_subprocess_environment() -> tuple[dict[str, str], list[str]]:
    sensitive_markers = (
        "TOKEN",
        "SECRET",
        "PASSWORD",
        "CREDENTIAL",
        "COOKIE",
        "SESSION",
        "API_KEY",
        "AUTH",
    )
    environment: dict[str, str] = {}
    sensitive_values: list[str] = []
    for key, value in os.environ.items():
        if any(marker in key.upper() for marker in sensitive_markers):
            if len(value) >= 8:
                sensitive_values.append(value)
            continue
        environment[key] = value
    environment.update(
        {
            "NO_PROXY": "",
            "no_proxy": "",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "http://127.0.0.1:9",
            "LCA_LIFECYCLE_FINALIZER": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return environment, sensitive_values


@contextmanager
def _network_disabled_subprocess_environment():
    """Yield a sanitized Python environment whose socket egress fails closed."""

    environment, sensitive_values = _sanitized_subprocess_environment()
    with tempfile.TemporaryDirectory() as temporary:
        guard = Path(temporary) / "sitecustomize.py"
        guard.write_text(
            """import socket

def _blocked(*args, **kwargs):
    raise RuntimeError("lifecycle finalizer network access is disabled")

socket.create_connection = _blocked
socket.getaddrinfo = _blocked
socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
socket.socket.sendto = _blocked
""",
            encoding="utf-8",
        )
        existing = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            temporary if not existing else temporary + os.pathsep + existing
        )
        yield environment, sensitive_values


def _network_denied_command(command: Sequence[str]) -> list[str]:
    """Wrap a gate in the host OS sandbox; absence is a fail-closed condition."""

    sandbox_exec = Path("/usr/bin/sandbox-exec")
    if not sandbox_exec.is_file():
        raise ValueError("OS network-denial sandbox is unavailable")
    profile = "(version 1)(allow default)(deny network*)"
    return [sandbox_exec.as_posix(), "-p", profile, *command]


def _finalizer_request_sha256(request: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _existing_finalizer_event(
    events: Sequence[Mapping[str, object]], finalizer_id: str
) -> Optional[Mapping[str, object]]:
    matches = [
        event
        for event in events
        if event.get("event_type") == "research.lifecycle.phase_ready_to_complete"
        and isinstance(event.get("payload"), Mapping)
        and event["payload"].get("finalizer_id") == finalizer_id
    ]
    if len(matches) > 1:
        raise ValueError("lifecycle history contains duplicate finalizer events")
    return matches[0] if matches else None


def _safe_output_prefix(request: Mapping[str, object], finalizer_id: str) -> Path:
    value = _require_string(request, "output_prefix")
    path = Path(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or len(path.parts) != 2
        or path.parts[0] != "finalizer"
        or path.parts[1] != finalizer_id
    ):
        raise ValueError("finalizer output_prefix is outside the bounded workspace path")
    return path


def _execute_finalizer_gates(
    *,
    root: Path,
    workspace: Path,
    gates: Sequence[Mapping[str, object]],
    immutable_snapshot: tuple[
        Mapping[Path, bytes], Mapping[Path, tuple[Path, ...]]
    ],
    output_prefix: Path,
) -> tuple[dict[str, bytes], list[dict]]:
    staged_bytes: dict[str, bytes] = {}
    transcripts: list[dict] = []
    with _network_disabled_subprocess_environment() as (
        environment,
        sensitive_values,
    ):
        for gate in gates:
            command = list(gate["command"])
            result = subprocess.run(
                _network_denied_command(command),
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=1800,
                check=False,
            )
            _restore_and_reject_immutable_drift(immutable_snapshot)
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            if any(secret in stdout or secret in stderr for secret in sensitive_values):
                raise ValueError("finalizer gate output contained a removed secret value")
            transcript = {
                "contract": "lca_research_kernel.lifecycle_gate_transcript/v1",
                "gate_id": gate["gate_id"],
                "command": command,
                "execution_guard": "darwin_sandbox_exec_deny_network/v1",
                "exit_code": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
                "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
                "status": "passed" if result.returncode == 0 else "failed",
            }
            relative = (
                output_prefix / "gates" / f"{gate['gate_id']}.json"
            ).as_posix()
            staged_bytes[relative] = _canonical_json_bytes(transcript)
            transcripts.append(
                {
                    "gate_id": gate["gate_id"],
                    "path": (workspace / relative).relative_to(root).as_posix(),
                    "sha256": hashlib.sha256(staged_bytes[relative]).hexdigest(),
                    "bytes": len(staged_bytes[relative]),
                    "status": transcript["status"],
                }
            )
            if result.returncode != 0:
                raise ValueError(f"finalizer gate failed: {gate['gate_id']}")
    return staged_bytes, transcripts


def finalize_lifecycle_phase(root: Path, project: object, request_path: Path) -> dict:
    """Run bounded gates and append one idempotent ready-to-complete event."""

    root = root.resolve()
    workspace = project.workspace.resolve()
    request = _read_input(request_path)
    contract = load_lifecycle_contract(root)
    finalizer_contract = contract.get("finalizer")
    if not isinstance(finalizer_contract, Mapping) or request.get(
        "contract"
    ) != finalizer_contract.get("request_contract"):
        raise ValueError("lifecycle finalizer request contract is unsupported")
    finalizer_id = _require_string(request, "finalizer_id")
    request_sha256 = _finalizer_request_sha256(request)
    events = project.events()
    existing = _existing_finalizer_event(events, finalizer_id)
    if existing is not None:
        payload = existing["payload"]
        if payload.get("request_sha256") != request_sha256:
            raise ValueError("finalizer_id was reused with a different request")
        receipt_ref = _require_ref(payload, "ready_receipt_ref")
        receipt_path = root / receipt_ref["path"]
        if not receipt_path.is_file() or _sha256(receipt_path) != receipt_ref["sha256"]:
            raise ValueError("accepted lifecycle ready receipt is absent or drifted")
        current = project._project(events)["lifecycle"]
        projection_errors = validate_lifecycle_projection(root, current)
        if projection_errors:
            raise ValueError(
                "accepted lifecycle finalizer artifacts drifted: "
                + "; ".join(projection_errors)
            )
        write_lifecycle_derivatives(root, project)
        return _read_json(receipt_path)

    projection = validate_project_lifecycle_events(events)
    _require_state(projection, "in_progress")
    observation = projection.get("goal_observation")
    if not isinstance(observation, Mapping):
        raise ValueError("finalizer requires an active Goal observation")
    if _require_string(request, "phase_id") != projection.get("current_phase_id"):
        raise ValueError("finalizer phase does not match the active lifecycle phase")
    if _require_string(request, "thread_id") != observation.get("thread_id"):
        raise ValueError("finalizer thread does not match the active Goal")
    if _require_string(request, "goal_observation_id") != observation.get(
        "observation_id"
    ):
        raise ValueError("finalizer Goal observation reference drifted")
    finalized_at = _require_string(request, "finalized_at")
    _parse_timestamp(finalized_at, "finalizer finalized_at")
    accepted_artifact = _require_ref(request, "accepted_artifact")
    accepted_path = root / accepted_artifact["path"]
    if not accepted_path.is_file() or _sha256(accepted_path) != accepted_artifact["sha256"]:
        raise ValueError("finalizer accepted artifact is absent or drifted")
    output_prefix = _safe_output_prefix(request, finalizer_id)
    gates = request.get("gates")
    if not isinstance(gates, list):
        raise ValueError("finalizer gates must be a list")
    gate_ids = [gate.get("gate_id") for gate in gates if isinstance(gate, Mapping)]
    if (
        len(gate_ids) != len(gates)
        or set(gate_ids) != REQUIRED_FINALIZER_GATES
        or len(gate_ids) != len(REQUIRED_FINALIZER_GATES)
    ):
        raise ValueError("finalizer required gate set is incomplete or duplicated")
    for gate in gates:
        if not _allowed_finalizer_command(gate.get("command"), contract):
            raise ValueError(f"finalizer command is not allowlisted: {gate.get('gate_id')}")
        if gate.get("command") != FINALIZER_GATE_COMMANDS.get(gate.get("gate_id")):
            raise ValueError(
                f"finalizer command does not match the fixed gate: {gate.get('gate_id')}"
            )

    immutable_snapshot = _immutable_history_snapshot(root)
    staged_bytes, transcripts = _execute_finalizer_gates(
        root=root,
        workspace=workspace,
        gates=gates,
        immutable_snapshot=immutable_snapshot,
        output_prefix=output_prefix,
    )

    manifest = {
        "contract": "lca_research_kernel.lifecycle_gate_manifest/v1",
        "finalizer_id": finalizer_id,
        "request_sha256": request_sha256,
        "gate_count": len(transcripts),
        "gates": transcripts,
        "all_passed": all(row["status"] == "passed" for row in transcripts),
        "immutable_history_unchanged": True,
    }
    manifest_relative = (output_prefix / "lifecycle-gate-manifest.json").as_posix()
    staged_bytes[manifest_relative] = _canonical_json_bytes(manifest)
    manifest_ref = {
        "path": (workspace / manifest_relative).relative_to(root).as_posix(),
        "sha256": hashlib.sha256(staged_bytes[manifest_relative]).hexdigest(),
    }
    receipt = {
        "contract": "lca_research_kernel.lifecycle_ready_receipt/v1",
        "finalizer_id": finalizer_id,
        "request_sha256": request_sha256,
        "phase_id": projection["current_phase_id"],
        "thread_id": observation["thread_id"],
        "goal_observation_id": observation["observation_id"],
        "finalized_at": finalized_at,
        "status": "ready_to_complete",
        "gate_manifest_ref": manifest_ref,
        "accepted_artifact": accepted_artifact,
        "post_completion_next_route": _require_string(
            request, "post_completion_next_route"
        ),
        "self_commit_reference_embedded": False,
    }
    receipt_relative = (output_prefix / "lifecycle-ready-receipt.json").as_posix()
    staged_bytes[receipt_relative] = _canonical_json_bytes(receipt)
    receipt_ref = {
        "path": (workspace / receipt_relative).relative_to(root).as_posix(),
        "sha256": hashlib.sha256(staged_bytes[receipt_relative]).hexdigest(),
    }
    registered_artifacts: list[dict] = []
    for relative, content in staged_bytes.items():
        path = workspace / relative
        digest = hashlib.sha256(content).hexdigest()
        if "/gates/" in f"/{relative}":
            kind = "lifecycle_gate_transcript"
        elif relative.endswith("lifecycle-gate-manifest.json"):
            kind = "lifecycle_gate_manifest"
        else:
            kind = "lifecycle_ready_receipt"
        registered_artifacts.append(
            {
                "artifact_id": hashlib.sha256(
                    f"{finalizer_id}:{relative}:{digest}".encode("utf-8")
                ).hexdigest()[:24],
                "activity_id": finalizer_id,
                "skill_ref": "lifecycle-finalizer/v1",
                "kind": kind,
                "path": path.relative_to(root).as_posix(),
                "workspace_path": relative,
                "sha256": digest,
                "bytes": len(content),
            }
        )
    registered_artifacts.sort(key=lambda row: row["workspace_path"])
    ready_payload = {
        "contract": READY_CONTRACT,
        "finalizer_id": finalizer_id,
        "request_sha256": request_sha256,
        "phase_id": projection["current_phase_id"],
        "thread_id": observation["thread_id"],
        "goal_observation_id": observation["observation_id"],
        "active_module": _require_string(request, "active_module"),
        "accepted_artifact": receipt_ref,
        "open_risks": _require_risks(request),
        "next_route": _require_string(request, "ready_next_route"),
        "post_completion_next_route": receipt["post_completion_next_route"],
        "gate_manifest_ref": manifest_ref,
        "ready_receipt_ref": receipt_ref,
        "passed_gate_ids": sorted(REQUIRED_FINALIZER_GATES),
        "registered_artifacts": registered_artifacts,
    }
    import uuid

    deterministic_event_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"lifecycle-finalizer:{finalizer_id}:{request_sha256}",
        )
    )
    candidate_event = {
        "event_id": deterministic_event_id,
        "sequence": len(events) + 1,
        "event_type": "research.lifecycle.phase_ready_to_complete",
        "occurred_at": finalized_at,
        "actor": "controller",
        "idempotency_key": f"lifecycle:finalizer:{finalizer_id}:ready",
        "payload": ready_payload,
    }
    candidate_events = [*events, candidate_event]
    validate_project_lifecycle_events(candidate_events)
    candidate_snapshot = project._project(candidate_events)
    candidate_projection = candidate_snapshot["lifecycle"]
    render_context(candidate_projection, load_product_architecture(root))
    render_lifecycle_progress(candidate_projection)
    _restore_and_reject_immutable_drift(immutable_snapshot)

    destination_root = workspace / output_prefix
    if destination_root.exists():
        import shutil

        shutil.rmtree(destination_root)
    try:
        for relative, content in staged_bytes.items():
            destination = workspace / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        project._record_lifecycle_event(
            "research.lifecycle.phase_ready_to_complete",
            ready_payload,
            idempotency_key=f"lifecycle:finalizer:{finalizer_id}:ready",
            event_id=deterministic_event_id,
            occurred_at=finalized_at,
        )
    except Exception:
        if destination_root.exists():
            import shutil

            shutil.rmtree(destination_root)
        raise
    write_lifecycle_derivatives(root, project)
    current = project._project(project.events())["lifecycle"]
    projection_errors = validate_lifecycle_projection(root, current)
    context_errors = validate_context_convergence(
        (root / "CONTEXT.md").read_text(encoding="utf-8"),
        current,
        load_product_architecture(root),
    )
    if projection_errors or context_errors:
        raise ValueError(
            "finalizer convergence failed: "
            + "; ".join([*projection_errors, *context_errors])
        )
    return receipt
