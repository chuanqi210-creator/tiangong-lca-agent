"""Project Record acceptance for the D-18 capability infrastructure."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Mapping

from .agent_bootstrap import AgentBootstrap
from .assurance import AssuranceBoundary
from .capability_catalog import CapabilityCatalog
from .capability_infrastructure_acceptance import (
    verify_capability_infrastructure,
)
from .project_record import ProjectRecord, canonical_sha256


REQUEST_CONTRACT = (
    "lca_research_kernel.capability_infrastructure_acceptance_request/v1"
)
RECEIPT_CONTRACT = (
    "lca_research_kernel.capability_infrastructure_adoption_receipt/v1"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json_atomic(path: Path, value: object) -> None:
    content = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _require_text(request: Mapping[str, object], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"capability infrastructure acceptance {key} is required")
    return value.strip()


def accept_capability_infrastructure(
    root: Path,
    workspace: Path,
    ledger_path: Path,
    request: Mapping[str, object],
) -> dict:
    """Accept one exact catalog snapshot without invoking scientific capabilities."""

    root = Path(root).resolve()
    workspace = Path(workspace).resolve()
    ledger_path = Path(ledger_path).resolve()
    if request.get("contract") != REQUEST_CONTRACT:
        raise ValueError("capability infrastructure acceptance request is unsupported")
    acceptance_id = _require_text(request, "acceptance_id")
    evaluated_at = _require_text(request, "evaluated_at")
    run_id = _require_text(request, "run_id")
    expected_ledger_sha256 = _require_text(request, "ledger_sha256")
    if _sha256(ledger_path) != expected_ledger_sha256:
        raise ValueError("capability infrastructure ledger drifted")

    infrastructure = verify_capability_infrastructure(root, ledger_path)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    manifest_path = root / ledger["startup_binding"]["pointer"]
    startup_context = AgentBootstrap.load(root, manifest_path)
    catalog_path = root / ledger["catalog"]["pointer"]
    source_lock_path = root / ledger["source_lock"]["pointer"]
    catalog = CapabilityCatalog.from_files(catalog_path, source_lock_path)

    record = ProjectRecord.open_or_create_project(
        workspace,
        project_id="d18-tiangong-capability-infrastructure",
        title="D-18 TianGong capability infrastructure",
        objective=(
            "Adopt the source-locked TianGong capability surface without "
            "starting a research case or native calculation."
        ),
        created_at=evaluated_at,
    )
    startup_event = record.record_agent_startup(
        startup_context,
        run_id=run_id,
        observed_at=evaluated_at,
    )
    summary = {
        "contract": "lca_research_kernel.capability_catalog_summary/v1",
        "catalog_id": ledger["catalog"]["pointer"],
        "catalog_snapshot_sha256": catalog.snapshot_sha256,
        "source_lock_sha256": ledger["source_lock"]["sha256"],
        "capability_count": len(catalog.list_descriptors()),
        "unclassified_count": 0,
        "ledger_sha256": expected_ledger_sha256,
        "evaluated_at": evaluated_at,
        "research_cases_executed": 0,
        "native_calculations_executed": 0,
        "executed_external_effects": 0,
    }
    assurance = AssuranceBoundary().evaluate_capability_catalog(summary)
    if assurance["decision"] != "accepted":
        raise ValueError(
            "capability infrastructure catalog was rejected: "
            + ", ".join(assurance["errors"])
        )
    adoption_event = record.adopt_capability_catalog(
        summary,
        acceptance=assurance,
        idempotency_key=f"capability-catalog:{acceptance_id}",
    )
    receipt = {
        "contract": RECEIPT_CONTRACT,
        "acceptance_id": acceptance_id,
        "evaluated_at": evaluated_at,
        "infrastructure_verification": infrastructure,
        "startup": {
            "run_id": run_id,
            "context_sha256": startup_context["context_sha256"],
            "event_sha256": startup_event["event_sha256"],
        },
        "catalog_summary": summary,
        "assurance_acceptance": assurance,
        "catalog_adoption_event_sha256": adoption_event["event_sha256"],
        "scientific_results_created": 0,
        "executed_external_effects": 0,
    }
    relative_receipt = "artifacts/capability-infrastructure-adoption.json"
    receipt_path = workspace / relative_receipt
    _write_json_atomic(receipt_path, receipt)
    receipt_sha256 = _sha256(receipt_path)
    record.register_artifact(
        relative_receipt,
        role="capability_infrastructure_adoption_receipt",
        acceptance={
            "contract": "lca_research_kernel.assurance_artifact_acceptance/v1",
            "decision": "accepted",
            "artifact_sha256": receipt_sha256,
            "evaluator": "assurance_boundary",
            "evaluated_at": evaluated_at,
            "errors": [],
        },
        idempotency_key=f"capability-catalog:{acceptance_id}:receipt",
        recorded_at=evaluated_at,
    )
    replay = record.rebuild_and_verify()
    view = record.read_current_project_view()
    if (
        view["capability_catalog"] != summary
        or view["agent_startups"].get(run_id) is None
    ):
        raise ValueError("capability infrastructure Project Record projection drifted")
    return {
        "contract": "lca_research_kernel.capability_infrastructure_acceptance/v1",
        "acceptance_id": acceptance_id,
        "status": "accepted",
        "capability_count": summary["capability_count"],
        "catalog_snapshot_sha256": summary["catalog_snapshot_sha256"],
        "source_lock_sha256": summary["source_lock_sha256"],
        "ledger_sha256": expected_ledger_sha256,
        "receipt": {
            "path": relative_receipt,
            "sha256": receipt_sha256,
        },
        "event_count": replay["event_count"],
        "last_event_sha256": replay["last_event_sha256"],
        "projection_sha256": replay["projection_sha256"],
        "replay": replay,
        "summary_sha256": canonical_sha256(summary),
        "scientific_results_created": 0,
        "executed_external_effects": 0,
    }
