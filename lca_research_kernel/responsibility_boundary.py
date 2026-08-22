"""Fail-closed responsibility classification for Research Director operations."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional


OPERATION_INTENT_CONTRACT = "lca_research_kernel.operation_intent/v1"
HANDOFF_CONTRACT = "lca_research_kernel.responsibility_handoff/v1"
INTERNAL_EFFECTS = {"local_read", "local_write", "external_read"}
DIRECT_HUMAN_EFFECTS = {"external_write", "irreversible"}
AUTONOMOUS_TAGS = {
    "public_credential_free",
    "scientific_judgment",
    "local_git_checkpoint",
    "free_standard_oss_dependency",
    "internal_council_routing",
    "test_build_replay_recovery",
    "reversible_rollback",
    "internal_state_writeback",
}
HUMAN_TAGS = {
    "credentials",
    "login",
    "payment",
    "subscription",
    "contract_acceptance",
    "nonstandard_license",
    "publication",
    "public_voice",
    "external_or_shared_write",
    "external_message",
    "main_goal_change",
    "unsupported_strong_public_claim",
}
SECRET_FIELD_NAMES = {
    "api_key",
    "access_token",
    "password",
    "passwd",
    "secret",
    "secret_key",
    "private_key",
    "refresh_token",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ResponsibilityDecision:
    disposition: str
    category: str
    reason: str


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _reject_secret_fields(value: object, path: str = "intent") -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key).strip().casefold().replace("-", "_")
            if key in SECRET_FIELD_NAMES:
                raise ValueError(f"secret-looking field is forbidden at {path}.{raw_key}")
            _reject_secret_fields(child, f"{path}.{raw_key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_fields(child, f"{path}[{index}]")


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _payload_ref(value: object, payload: Mapping[str, object]) -> dict:
    if not isinstance(value, Mapping) or set(value) != {"path", "sha256", "version"}:
        raise ValueError("payload_ref must contain path, sha256 and version")
    raw_path = _text(value.get("path"), "payload_ref.path")
    path = Path(raw_path)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("payload_ref.path must be repository-relative without traversal")
    digest = _text(value.get("sha256"), "payload_ref.sha256")
    if SHA256_RE.fullmatch(digest) is None:
        raise ValueError("payload_ref.sha256 must be a lowercase SHA-256")
    if digest != _sha256(payload):
        raise ValueError("payload SHA-256 drifted")
    return {
        "path": path.as_posix(),
        "sha256": digest,
        "version": _text(value.get("version"), "payload_ref.version"),
    }


def validate_operation_intent(intent: Mapping[str, object]) -> dict:
    """Normalize an operation intent without weakening unknown responsibility."""

    if not isinstance(intent, Mapping):
        raise ValueError("operation intent must be an object")
    required = {
        "contract",
        "operation_id",
        "effect",
        "responsibility_tags",
        "target",
        "scope",
        "payload",
        "payload_ref",
        "verification",
        "compensation",
        "resume_route",
    }
    if set(intent) != required:
        raise ValueError("operation intent fields are incomplete or unsupported")
    if intent.get("contract") != OPERATION_INTENT_CONTRACT:
        raise ValueError("operation intent contract is unsupported")
    _reject_secret_fields(intent.get("payload"), "intent.payload")
    payload = intent.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("operation intent payload must be an object")
    raw_tags = intent.get("responsibility_tags")
    if not isinstance(raw_tags, list) or any(
        not isinstance(tag, str) or not tag.strip() for tag in raw_tags
    ):
        raise ValueError("responsibility_tags must be a list of strings")
    tags = sorted({tag.strip() for tag in raw_tags})
    return {
        "contract": OPERATION_INTENT_CONTRACT,
        "operation_id": _text(intent.get("operation_id"), "operation_id"),
        "effect": _text(intent.get("effect"), "effect"),
        "responsibility_tags": tags,
        "target": _text(intent.get("target"), "target"),
        "scope": _text(intent.get("scope"), "scope"),
        "payload": dict(payload),
        "payload_ref": _payload_ref(intent.get("payload_ref"), payload),
        "verification": _text(intent.get("verification"), "verification"),
        "compensation": (
            _text(intent.get("compensation"), "compensation")
            if intent.get("compensation") is not None
            else None
        ),
        "resume_route": _text(intent.get("resume_route"), "resume_route"),
    }


def classify_operation(intent: Mapping[str, object]) -> ResponsibilityDecision:
    """Assign an exact autonomous or human-owned responsibility disposition."""

    normalized = validate_operation_intent(intent)
    effect = str(normalized["effect"])
    tags = set(normalized["responsibility_tags"])
    human_tags = tags & HUMAN_TAGS
    unknown_tags = tags - AUTONOMOUS_TAGS - HUMAN_TAGS
    if human_tags:
        category = sorted(human_tags)[0]
        return ResponsibilityDecision(
            "prepare_handoff",
            category,
            "This exact action makes the user bear an external, identity, or obligation responsibility.",
        )
    if unknown_tags:
        return ResponsibilityDecision(
            "prepare_handoff",
            "unknown",
            "Unknown responsibility tags fail closed.",
        )
    if effect in DIRECT_HUMAN_EFFECTS:
        return ResponsibilityDecision(
            "prepare_handoff",
            effect,
            "This exact action makes the user bear an external or irreversible responsibility.",
        )
    if effect == "external_read" and "public_credential_free" not in tags:
        return ResponsibilityDecision(
            "prepare_handoff",
            "identity_bearing_external_read",
            "External reading is autonomous only when credential-free public access is proven.",
        )
    if effect not in INTERNAL_EFFECTS:
        return ResponsibilityDecision(
            "prepare_handoff", "unknown", "Unknown effect fails closed."
        )
    return ResponsibilityDecision(
        "proceed", "internal", "Within approved internal autonomy."
    )


def compile_handoff(
    intent: Mapping[str, object],
    decision: ResponsibilityDecision,
    *,
    previous: Optional[Mapping[str, object]] = None,
) -> dict:
    """Compile a deterministic zero-effect responsibility handoff."""

    normalized = validate_operation_intent(intent)
    expected = classify_operation(normalized)
    if decision != expected:
        raise ValueError("responsibility decision does not match the validated intent")
    if decision.disposition != "prepare_handoff":
        raise ValueError("only a prepare_handoff decision can compile a handoff")
    payload_ref = normalized["payload_ref"]
    body = {
        "contract": HANDOFF_CONTRACT,
        "action_id": normalized["operation_id"],
        "responsibility_category": decision.category,
        "target": normalized["target"],
        "scope": normalized["scope"],
        "payload_ref": payload_ref["path"],
        "payload_sha256": payload_ref["sha256"],
        "payload_version": payload_ref["version"],
        "reason": decision.reason,
        "expected_external_effect": normalized["effect"],
        "verification_plan": normalized["verification"],
        "compensation_plan": normalized["compensation"],
        "resume_route": normalized["resume_route"],
        "executed_external_effects": 0,
    }
    content_sha256 = _sha256(body)
    compiled = {
        **body,
        "handoff_id": f"handoff-{content_sha256[:24]}",
        "content_sha256": content_sha256,
    }
    if previous is not None and previous.get("action_id") == body["action_id"]:
        if dict(previous) == compiled:
            return dict(previous)
        raise ValueError("changed payload or scope requires a new action_id")
    return compiled


def validate_responsibility_events(
    events: list[Mapping[str, object]], project: object = None
) -> list[str]:
    """Validate handoff registrations and append-only responsibility identities."""

    errors: list[str] = []
    registered: dict[str, Mapping[str, object]] = {}
    actions: dict[str, Mapping[str, object]] = {}
    for index, event in enumerate(events):
        event_type = event.get("event_type")
        payload = event.get("payload")
        if event_type == "artifact.registered" and isinstance(payload, Mapping):
            path = payload.get("path")
            if isinstance(path, str):
                registered[path] = payload
            continue
        if event_type != "research.responsibility.handoff_prepared":
            continue
        if event.get("actor") != "controller" or not isinstance(
            event.get("idempotency_key"), str
        ):
            errors.append(f"handoff event {index} lacks controller idempotency")
            continue
        required = {
            "contract",
            "work_item_id",
            "work_item_revision",
            "action_id",
            "responsibility_category",
            "handoff_ref",
            "executed_external_effects",
            "next_route",
        }
        if not isinstance(payload, Mapping) or set(payload) != required:
            errors.append(f"handoff event {index} fields are invalid")
            continue
        if payload.get("contract") != (
            "lca_research_kernel.responsibility_handoff_prepared/v1"
        ):
            errors.append(f"handoff event {index} contract drifted")
        if payload.get("executed_external_effects") != 0:
            errors.append(f"handoff event {index} records an external effect")
        reference = payload.get("handoff_ref")
        if not isinstance(reference, Mapping) or set(reference) != {
            "role",
            "path",
            "sha256",
            "bytes",
        }:
            errors.append(f"handoff event {index} reference is invalid")
            continue
        path = reference.get("path")
        registration = registered.get(str(path))
        if (
            reference.get("role") != "responsibility_handoff"
            or not isinstance(registration, Mapping)
            or registration.get("artifact_role") != "responsibility_handoff"
            or any(
                registration.get(field) != reference.get(field)
                for field in ("sha256", "bytes")
            )
        ):
            errors.append(f"handoff event {index} is not Artifact Bus bound")
            continue
        if project is not None:
            workspace_path = registration.get("workspace_path")
            candidate = project.workspace / str(workspace_path)
            if not candidate.is_file():
                errors.append(f"handoff event {index} artifact is missing")
            else:
                content = candidate.read_bytes()
                if (
                    hashlib.sha256(content).hexdigest() != reference.get("sha256")
                    or len(content) != reference.get("bytes")
                ):
                    errors.append(f"handoff event {index} artifact drifted")
        action_id = str(payload.get("action_id"))
        previous = actions.get(action_id)
        if previous is not None and previous != payload:
            errors.append(f"handoff action {action_id} was reused with changed content")
        actions[action_id] = payload
    return errors
