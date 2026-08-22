"""Stable contracts for the D-18 service control plane."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

TERMINAL_STATES = frozenset({"completed", "bounded", "failed", "cancelled"})
ACTIVE_STATES = frozenset(
    {"queued", "leased", "running", "waiting_for_input", "cancelling"}
)
JOB_STATES = ACTIVE_STATES | TERMINAL_STATES

ALLOWED_TRANSITIONS = {
    "queued": frozenset({"leased", "cancelling"}),
    "leased": frozenset({"running", "cancelling", "queued"}),
    "running": frozenset(
        {"waiting_for_input", "completed", "bounded", "failed", "cancelling", "queued"}
    ),
    "waiting_for_input": frozenset(
        {"queued", "completed", "bounded", "failed", "cancelling"}
    ),
    "cancelling": frozenset({"cancelled"}),
    "completed": frozenset(),
    "bounded": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}

REF_KEYS = frozenset({"path", "sha256", "role"})


def validate_content_ref(value: Mapping[str, Any] | None) -> dict[str, str] | None:
    """Return a normalized content reference or fail closed.

    The control plane intentionally cannot hold result bodies, prompts, or
    materials.  References are relative paths plus a content digest.
    """

    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("reference must be a mapping")
    if set(value) - REF_KEYS:
        raise ValueError("reference contains non-control metadata")
    path = value.get("path")
    digest = value.get("sha256")
    role = value.get("role")
    if not isinstance(path, str) or not path or path.startswith("/"):
        raise ValueError("reference path must be a non-empty relative path")
    if ".." in path.split("/"):
        raise ValueError("reference path must not escape its project")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        raise ValueError("reference sha256 must be lowercase hexadecimal")
    if role is not None and (not isinstance(role, str) or not role):
        raise ValueError("reference role must be a non-empty string")
    normalized = {"path": path, "sha256": digest}
    if role is not None:
        normalized["role"] = role
    return normalized


def require_sha256(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value
