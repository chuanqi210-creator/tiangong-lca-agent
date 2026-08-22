"""Stable, arm-neutral request and result contracts for Evaluation Lab runners."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


RUNNER_ARMS = (
    "raw_model_tool_loop",
    "openai_agents_reference",
    "d18_product_agent",
)
_SECRET_FIELD_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "auth_token",
    "credential",
    "password",
    "secret",
)


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def evaluation_candidate_schema() -> dict:
    """One arm-neutral structured candidate shape for real benchmark calls."""

    return {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["candidate_only"]},
            "bounded_conclusion": {"type": "string"},
            "missing_fields": {"type": "array", "items": {"type": "string"}},
            "limitations": {"type": "array", "items": {"type": "string"}},
            "scientific_source_sha256s": {
                "type": "array",
                "items": {"type": "string"},
            },
            "runtime_provenance_sha256s": {
                "type": "array",
                "items": {"type": "string"},
            },
            "official_object_ids": {
                "type": "array",
                "items": {"type": "string"},
            },
            "values": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "identity": {"type": "string"},
                        "value": {"type": "number"},
                        "unit": {"type": "string"},
                    },
                    "required": ["identity", "value", "unit"],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "status",
            "bounded_conclusion",
            "missing_fields",
            "limitations",
            "scientific_source_sha256s",
            "runtime_provenance_sha256s",
            "official_object_ids",
            "values",
        ],
        "additionalProperties": False,
    }


def _contains_secret_field(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if any(fragment in normalized for fragment in _SECRET_FIELD_FRAGMENTS):
                return True
            if _contains_secret_field(nested):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_secret_field(item) for item in value)
    return False


def _atomic_json(path: Path, value: object) -> dict[str, str]:
    content = json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return {
        "path": path.as_posix(),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


@dataclass(frozen=True)
class RunnerRequest:
    task_id: str
    run_id: str
    model_id: str
    model_settings: Mapping[str, object]
    material_refs: tuple[Mapping[str, object], ...]
    tool_descriptors: tuple[Mapping[str, object], ...]
    permission_policy: Mapping[str, object]
    budgets: Mapping[str, object]

    def __post_init__(self) -> None:
        for name in ("task_id", "run_id", "model_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"runner request {name} is absent")
        if not self.material_refs:
            raise ValueError("runner request materials are absent")
        payload = self.canonical_payload()
        if _contains_secret_field(payload):
            raise ValueError("runner request must not contain credentials")
        try:
            canonical_json(payload)
        except (TypeError, ValueError) as error:
            raise ValueError("runner request is not canonical JSON") from error

    def canonical_payload(self) -> dict:
        return {
            "contract": "lca_research_evaluation.runner_request/v1",
            "task_id": self.task_id,
            "run_id": self.run_id,
            "model_id": self.model_id,
            "model_settings": deepcopy(dict(self.model_settings)),
            "material_refs": deepcopy([dict(item) for item in self.material_refs]),
            "tool_descriptors": deepcopy(
                [dict(item) for item in self.tool_descriptors]
            ),
            "permission_policy": deepcopy(dict(self.permission_policy)),
            "budgets": deepcopy(dict(self.budgets)),
        }

    @property
    def request_sha256(self) -> str:
        return canonical_sha256(self.canonical_payload())


@dataclass(frozen=True)
class RunnerResult:
    arm: str
    task_id: str
    run_id: str
    status: str
    final_candidate_ref: Mapping[str, object] | None
    trajectory_ref: Mapping[str, object]
    usage: Mapping[str, object]
    timing: Mapping[str, object]
    effect_counts: Mapping[str, int]

    def as_dict(self) -> dict:
        return {
            "contract": "lca_research_evaluation.runner_result/v1",
            "arm": self.arm,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "status": self.status,
            "final_candidate_ref": (
                None
                if self.final_candidate_ref is None
                else deepcopy(dict(self.final_candidate_ref))
            ),
            "trajectory_ref": deepcopy(dict(self.trajectory_ref)),
            "usage": deepcopy(dict(self.usage)),
            "timing": deepcopy(dict(self.timing)),
            "effect_counts": deepcopy(dict(self.effect_counts)),
        }


def persist_runtime_response(
    *,
    arm: str,
    request: RunnerRequest,
    workspace: Path,
    response: Mapping[str, object],
) -> RunnerResult:
    """Normalize one arm response without promoting it to Project Record truth."""

    if arm not in RUNNER_ARMS:
        raise ValueError("evaluation runner arm is unsupported")
    if not isinstance(response, Mapping):
        raise ValueError("evaluation runtime response must be an object")
    candidate = response.get("candidate")
    if not isinstance(candidate, Mapping):
        raise ValueError("evaluation runtime candidate is absent")
    normalized_candidate = deepcopy(dict(candidate))
    if normalized_candidate.get("status") != "candidate_only":
        raise ValueError("evaluation runtime output exceeded candidate-only ceiling")
    if _contains_secret_field(response):
        raise ValueError("evaluation runtime output contains a secret-bearing field")

    workspace = Path(workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    candidate_ref = _atomic_json(workspace / "candidate.json", normalized_candidate)
    tool_calls = response.get("tool_calls", [])
    if not isinstance(tool_calls, list):
        raise ValueError("evaluation runtime tool_calls must be a list")
    trajectory = {
        "contract": "lca_research_evaluation.runner_trajectory/v1",
        "arm": arm,
        "task_id": request.task_id,
        "run_id": request.run_id,
        "request_sha256": request.request_sha256,
        "actual_model": response.get("actual_model"),
        "tool_calls": deepcopy(tool_calls),
        "candidate_sha256": candidate_ref["sha256"],
        "candidate_only": True,
        "project_record_write_count": int(
            response.get("project_record_write_count", 0)
        ),
        "boundary": response.get("boundary"),
        "failure_code": response.get("failure_code"),
        "recovery_route": response.get("recovery_route"),
        "effect_counts": {
            "external_write": int(
                (response.get("effect_counts") or {}).get("external_write", 0)  # type: ignore[union-attr]
            ),
            "local_write": int(
                (response.get("effect_counts") or {}).get("local_write", 2)  # type: ignore[union-attr]
            ),
        },
    }
    if trajectory["effect_counts"]["external_write"] != 0:
        raise ValueError("evaluation arm attempted an external write")
    trajectory_ref = _atomic_json(workspace / "trajectory.json", trajectory)
    return RunnerResult(
        arm=arm,
        task_id=request.task_id,
        run_id=request.run_id,
        status="candidate",
        final_candidate_ref=candidate_ref,
        trajectory_ref=trajectory_ref,
        usage=deepcopy(dict(response.get("usage") or {})),
        timing=deepcopy(dict(response.get("timing") or {})),
        effect_counts=deepcopy(trajectory["effect_counts"]),
    )


__all__ = [
    "RUNNER_ARMS",
    "RunnerRequest",
    "RunnerResult",
    "canonical_sha256",
    "evaluation_candidate_schema",
    "persist_runtime_response",
]
