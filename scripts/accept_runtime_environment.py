#!/usr/bin/env python3
"""Assurance-accept the closed D-18 runtime into a replayable Project Record."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_kernel.agent_bootstrap import AgentBootstrap
from lca_research_kernel.assurance import AssuranceBoundary
from lca_research_kernel.project_record import ProjectRecord
from lca_research_kernel.runtime_environment import RuntimeEnvironmentContract
from lca_research_kernel.runtime_environment_health import verify_matrix_receipts


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path: Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _copy_exact(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.read_bytes() != source.read_bytes():
            raise ValueError(f"Project Record artifact drifted: {destination}")
        return
    shutil.copy2(source, destination)


def accept_runtime_environment(
    root: Path,
    *,
    workspace: Path,
    summary_path: Path,
) -> dict:
    root = Path(root).resolve()
    workspace = Path(workspace)
    summary_path = Path(summary_path)
    summary = _read(summary_path)
    if (
        summary.get("contract")
        != "lca_research_kernel.runtime_environment_acceptance/v1"
        or summary.get("status") != "accepted"
        or summary.get("capability_count") != 54
        or summary.get("ambiguous_conditional_runtime") != 0
        or summary.get("case_executions") != 0
        or summary.get("lca_calculations") != 0
        or summary.get("lcia_calculations") != 0
        or summary.get("external_writes") != 0
        or summary.get("credential_values_read") != 0
    ):
        raise ValueError("runtime environment acceptance summary is not closed")
    observed_at = str(summary["observed_at"])
    matrix_ref = summary.get("matrix")
    if not isinstance(matrix_ref, dict):
        raise ValueError("runtime readiness matrix reference is absent")
    matrix_path = root / str(matrix_ref["path"])
    if _sha256(matrix_path) != matrix_ref.get("sha256"):
        raise ValueError("runtime readiness matrix drifted")
    matrix = _read(matrix_path)
    contract = RuntimeEnvironmentContract.load(root)
    errors = contract.validate_matrix(matrix, require_closed=True)
    if errors:
        raise ValueError("runtime readiness matrix is not closed: " + "; ".join(errors))
    verify_matrix_receipts(root, matrix)

    record = ProjectRecord.open_or_create_project(
        workspace,
        project_id="d18-tiangong-runtime-environment",
        title="D-18 TianGong capability runtime environment",
        objective=(
            "Close the source-locked TianGong capability runtime without "
            "starting a research case or LCA calculation."
        ),
        created_at=observed_at,
    )
    startup = AgentBootstrap.load(
        root,
        root
        / "lca_research_kernel/contracts/agent-startup-manifest-v2.json",
    )
    record.record_agent_startup(
        startup,
        run_id=(
            "d18-runtime-environment-acceptance-"
            + contract.catalog_snapshot_sha256[:16]
        ),
        observed_at=observed_at,
    )
    source_lock = root / "lca_research_kernel/contracts/tiangong-source-lock.json"
    catalog_summary = {
        "contract": "lca_research_kernel.capability_catalog_summary/v1",
        "catalog_id": (
            "lca_research_kernel/contracts/tiangong-capability-catalog.json"
        ),
        "catalog_snapshot_sha256": contract.catalog_snapshot_sha256,
        "source_lock_sha256": _sha256(source_lock),
        "capability_count": len(contract.capability_ids),
        "unclassified_count": 0,
        "evaluated_at": observed_at,
        "runtime_readiness_matrix_sha256": matrix_ref["sha256"],
        "executed_external_effects": 0,
        "native_calculations_executed": 0,
        "research_cases_executed": 0,
    }
    assurance = AssuranceBoundary()
    catalog_acceptance = assurance.evaluate_capability_catalog(catalog_summary)
    record.adopt_capability_catalog(
        catalog_summary,
        acceptance=catalog_acceptance,
        idempotency_key=(
            "capability-catalog:d18-runtime-environment:"
            + contract.catalog_snapshot_sha256[:16]
        ),
    )

    sources = (
        (
            matrix_path,
            "artifacts/runtime-readiness-matrix.json",
            "runtime_readiness_matrix",
        ),
        (
            summary_path,
            "artifacts/runtime-health-summary.json",
            "runtime_environment_acceptance",
        ),
        (
            root / "outputs/runtime-environment/runtime-health-report.zh-CN.md",
            "artifacts/runtime-health-report.zh-CN.md",
            "runtime_environment_human_report",
        ),
        (
            root / "runtime/tiangong-runtime-source-lock.json",
            "artifacts/tiangong-runtime-source-lock.json",
            "runtime_source_lock",
        ),
    )
    for source, relative, role in sources:
        target = workspace / relative
        _copy_exact(source, target)
        digest = _sha256(target)
        acceptance = {
            "contract": "lca_research_kernel.assurance_artifact_acceptance/v1",
            "decision": "accepted",
            "evaluator": "assurance_boundary",
            "evaluated_at": observed_at,
            "artifact_sha256": digest,
            "errors": [],
        }
        record.register_artifact(
            relative,
            role=role,
            acceptance=acceptance,
            idempotency_key=f"runtime-environment:{role}:{digest[:16]}",
            recorded_at=observed_at,
        )
    replay = record.rebuild_and_verify()
    view = record.read_current_project_view()
    return {
        "status": replay["status"],
        "event_count": replay["event_count"],
        "last_event_sha256": replay["last_event_sha256"],
        "projection_sha256": replay["projection_sha256"],
        "capability_count": view["capability_catalog"]["capability_count"],
        "catalog_snapshot_sha256": view["capability_catalog"][
            "catalog_snapshot_sha256"
        ],
        "registered_artifact_count": len(view["artifacts"]),
        "case_executions": 0,
        "lca_calculations": 0,
        "external_writes": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=ROOT / "outputs/runtime-environment/project-record",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=(
            ROOT
            / "outputs/runtime-environment/"
            "runtime-health-summary-source-binding-v10.json"
        ),
    )
    arguments = parser.parse_args()
    result = accept_runtime_environment(
        arguments.root,
        workspace=arguments.workspace,
        summary_path=arguments.summary,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
