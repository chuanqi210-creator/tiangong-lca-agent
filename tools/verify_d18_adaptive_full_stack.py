#!/usr/bin/env python3
"""Fail-closed verifier for the adaptive D-18 full-stack disposition ledger."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse


CONTRACT = "lca_research_agent.adaptive_full_stack_disposition/v1"
LEDGER_PATH = Path("docs/d18-adaptive-full-stack-disposition.json")
ARCHITECTURE_PATH = Path("docs/lca-agent-top-level-architecture.json")
DISPOSITIONS = {"retain", "deepen", "adapt", "replace", "freeze", "retire"}
SUPPORTING_SCOPES = {"frontend", "service_control_plane", "run_control_context"}
REQUIRED_SCOPE_FIELDS = {
    "scope_id",
    "d18_module",
    "disposition",
    "current_status",
    "current_assets",
    "retain",
    "problems",
    "external_patterns",
    "acceptance_predicates",
    "next_internal_action",
    "current_claims",
}
REQUIRED_PATTERN_FIELDS = {
    "source_id",
    "mechanism",
    "local_migration",
    "rejected_transfer",
}


def _load_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"unable to load {path.name}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _nonempty_strings(values: object, *, field: str) -> list[str]:
    if (
        not isinstance(values, list)
        or not values
        or any(not isinstance(value, str) or not value.strip() for value in values)
    ):
        raise ValueError(f"{field} must be a non-empty string list")
    return list(values)


def verify_disposition(repository_root: Path) -> dict[str, object]:
    root = Path(repository_root).resolve()
    architecture = _load_object(root / ARCHITECTURE_PATH)
    ledger = _load_object(root / LEDGER_PATH)
    if ledger.get("contract") != CONTRACT:
        raise ValueError("adaptive disposition contract is invalid")
    if ledger.get("status") != "approved_for_incremental_implementation":
        raise ValueError("adaptive disposition status is invalid")
    if set(ledger.get("disposition_enum") or []) != DISPOSITIONS:
        raise ValueError("adaptive disposition enum drifted")

    architecture_modules = {
        row["id"]
        for row in architecture.get("modules", [])
        if isinstance(row, Mapping) and isinstance(row.get("id"), str)
    }
    if len(architecture_modules) != 7:
        raise ValueError("D-18 architecture module registry is invalid")
    expected_scopes = architecture_modules | SUPPORTING_SCOPES
    declared_scopes = set(ledger.get("mandatory_scopes") or [])
    if declared_scopes != expected_scopes:
        raise ValueError("mandatory adaptive scopes drifted")

    source_rows = ledger.get("external_sources")
    if not isinstance(source_rows, list) or not source_rows:
        raise ValueError("external source registry is absent")
    sources: dict[str, str] = {}
    for row in source_rows:
        if not isinstance(row, Mapping) or set(row) != {
            "source_id",
            "title",
            "url",
            "authority",
        }:
            raise ValueError("external source row is invalid")
        source_id = row.get("source_id")
        url = row.get("url")
        if (
            not isinstance(source_id, str)
            or not source_id
            or source_id in sources
            or not isinstance(url, str)
            or urlparse(url).scheme != "https"
            or not urlparse(url).netloc
            or row.get("authority") != "official_documentation"
        ):
            raise ValueError("external source identity is invalid")
        sources[source_id] = url

    scopes = ledger.get("scopes")
    if not isinstance(scopes, list) or len(scopes) != len(expected_scopes):
        raise ValueError("adaptive scope list is incomplete")
    seen: set[str] = set()
    for row in scopes:
        if not isinstance(row, Mapping) or set(row) != REQUIRED_SCOPE_FIELDS:
            raise ValueError("adaptive scope fields are invalid")
        scope_id = row.get("scope_id")
        if (
            not isinstance(scope_id, str)
            or scope_id not in expected_scopes
            or scope_id in seen
        ):
            raise ValueError("adaptive scope identity is invalid")
        seen.add(scope_id)
        if row.get("d18_module") is not (scope_id in architecture_modules):
            raise ValueError("adaptive D-18 module flag is invalid")
        if row.get("disposition") not in DISPOSITIONS:
            raise ValueError("adaptive scope disposition is invalid")
        if not isinstance(row.get("current_status"), str) or not row["current_status"]:
            raise ValueError("adaptive current status is absent")
        for field in ("current_assets", "retain", "problems", "acceptance_predicates"):
            _nonempty_strings(row.get(field), field=f"{scope_id}.{field}")
        if (
            not isinstance(row.get("next_internal_action"), str)
            or not row["next_internal_action"]
        ):
            raise ValueError("adaptive next internal action is absent")
        claims = row.get("current_claims")
        if not isinstance(claims, Mapping):
            raise ValueError("adaptive current claims are invalid")
        if claims.get("metadata_only_skills_executable") is True:
            raise ValueError("metadata-only Skills cannot be claimed executable")

        patterns = row.get("external_patterns")
        if not isinstance(patterns, list) or not patterns:
            raise ValueError("each adaptive scope requires an external pattern")
        for pattern in patterns:
            if not isinstance(pattern, Mapping) or set(pattern) != REQUIRED_PATTERN_FIELDS:
                raise ValueError("external pattern fields are invalid")
            if pattern.get("source_id") not in sources or any(
                not isinstance(pattern.get(field), str) or not pattern[field].strip()
                for field in ("mechanism", "local_migration", "rejected_transfer")
            ):
                raise ValueError("external pattern migration is invalid")

    if seen != expected_scopes:
        raise ValueError("adaptive scopes were not fully classified")
    if ledger.get("next_route") != (
        "implement-first-state-driven-interaction-vertical"
    ):
        raise ValueError("adaptive disposition next route is invalid")
    return {
        "status": "valid",
        "d18_module_count": len(architecture_modules),
        "supporting_scope_count": len(SUPPORTING_SCOPES),
        "external_source_count": len(sources),
        "scope_count": len(scopes),
        "next_route": ledger["next_route"],
    }


def main() -> int:
    result = verify_disposition(Path(__file__).resolve().parents[1])
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
