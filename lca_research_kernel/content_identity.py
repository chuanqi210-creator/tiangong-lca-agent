"""Canonical, case-neutral content identity primitives for the D-18 kernel."""

from __future__ import annotations

import hashlib
import json


def canonical_json_bytes(value: object) -> bytes:
    """Serialize JSON-compatible content for stable hashing and persistence."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    """Return the canonical SHA-256 identity for JSON-compatible content."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
