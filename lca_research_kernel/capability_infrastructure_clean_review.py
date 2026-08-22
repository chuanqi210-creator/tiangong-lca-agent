"""Bounded clean-context review of the D-18 capability infrastructure."""

from __future__ import annotations

import re
from pathlib import Path

from .capability_infrastructure_acceptance import (
    verify_capability_infrastructure,
)
from .project_record import ProjectRecord


SECRET_PATTERNS = (
    re.compile(rb"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
)
ONLINE_IDENTITY_PATTERN = re.compile(
    rb"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)


def _scan_generated_files(generated_root: Path) -> dict:
    scanned_files = 0
    scanned_bytes = 0
    secret_matches = 0
    identity_matches = 0
    for path in sorted(Path(generated_root).rglob("*")):
        if (
            not path.is_file()
            or path.is_symlink()
            or "final" in path.relative_to(generated_root).parts
        ):
            continue
        content = path.read_bytes()
        scanned_files += 1
        scanned_bytes += len(content)
        secret_matches += sum(
            len(pattern.findall(content)) for pattern in SECRET_PATTERNS
        )
        identity_matches += len(ONLINE_IDENTITY_PATTERN.findall(content))
    return {
        "scanned_files": scanned_files,
        "scanned_bytes": scanned_bytes,
        "secret_value_matches": secret_matches,
        "online_identity_matches": identity_matches,
    }


def review_capability_infrastructure(
    root: Path,
    ledger_path: Path,
    project_workspace: Path,
    generated_root: Path,
) -> dict:
    """Review only source-bound machine contracts and generated acceptance state."""

    infrastructure = verify_capability_infrastructure(root, ledger_path)
    record = ProjectRecord(project_workspace)
    replay = record.rebuild_and_verify()
    events = record.read_audit_trail()
    view = record.read_current_project_view()
    security = _scan_generated_files(generated_root)

    event_types = [row.get("event_type") for row in events]
    expected_events = [
        "research.project.created",
        "research.agent.started",
        "research.capability.catalog_adopted",
        "research.project.artifact_registered",
    ]
    forbidden_effects = 0
    for event in events:
        payload = event.get("payload")
        if isinstance(payload, dict) and payload.get("executed_external_effects", 0) != 0:
            forbidden_effects += 1

    p1: list[str] = []
    p2: list[str] = []
    if infrastructure.get("status") != "passed":
        p1.append("machine_infrastructure_verification_failed")
    if event_types != expected_events:
        p1.append("project_record_event_surface_drifted")
    if view.get("capability_catalog") is None:
        p1.append("catalog_not_adopted_in_project_record")
    if view.get("capability_runs"):
        p1.append("capability_execution_occurred_during_infrastructure_phase")
    if view.get("facts") or view.get("result") is not None:
        p1.append("scientific_result_created_during_infrastructure_phase")
    if forbidden_effects:
        p1.append("forbidden_effect_recorded")
    if security["secret_value_matches"]:
        p1.append("secret_value_detected")
    if security["online_identity_matches"]:
        p2.append("online_identity_detected")
    if infrastructure.get("capability_count") != 54:
        p2.append("capability_surface_count_drifted")
    if len(view.get("agent_startups", {})) != 1:
        p2.append("startup_receipt_cardinality_drifted")

    return {
        "contract": "lca_research_kernel.capability_infrastructure_clean_review/v1",
        "status": "accepted" if not p1 and not p2 else "rejected",
        "review_scope": [
            "machine startup and catalog contracts",
            "active Agent and Skill surfaces",
            "immutable legacy pointer",
            "Project Record adoption and replay",
            "generated secret, online identity, and effect surface",
        ],
        "p1_findings": p1,
        "p2_findings": p2,
        "infrastructure": infrastructure,
        "project_record": replay,
        "security_scan": security,
        "effect_scan": {
            "forbidden_effects": forbidden_effects,
            "capability_runs": len(view.get("capability_runs", {})),
            "scientific_results": int(
                bool(view.get("facts")) or view.get("result") is not None
            ),
        },
    }
