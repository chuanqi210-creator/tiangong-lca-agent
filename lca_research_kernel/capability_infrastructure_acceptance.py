"""Deterministic acceptance checks for the D-18 capability infrastructure."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Mapping

from .capability_catalog import CapabilityCatalog, KINDS


class CapabilityInfrastructureError(ValueError):
    """Raised when the adopted infrastructure cannot be reproduced."""


def _read_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CapabilityInfrastructureError(f"{label} is unavailable or invalid") from error
    if not isinstance(value, dict):
        raise CapabilityInfrastructureError(f"{label} must be an object")
    return value


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise CapabilityInfrastructureError(f"artifact is unavailable: {path}") from error


def _resolve(root: Path, pointer: object, label: str) -> Path:
    if not isinstance(pointer, str) or not pointer.strip():
        raise CapabilityInfrastructureError(f"{label} pointer is invalid")
    root = root.resolve()
    path = (root / pointer).resolve()
    if root != path and root not in path.parents:
        raise CapabilityInfrastructureError(f"{label} pointer escapes the workspace")
    return path


def _require_sha(root: Path, row: Mapping[str, object], label: str) -> Path:
    path = _resolve(root, row.get("pointer"), label)
    expected = row.get("sha256")
    actual = _sha256(path)
    if expected != actual:
        raise CapabilityInfrastructureError(
            f"{label}.sha256 drifted: expected {expected}, observed {actual}"
        )
    return path


def _git_object_bytes(root: Path, object_name: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(root), "show", object_name],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise CapabilityInfrastructureError("legacy git object is unavailable")
    return completed.stdout


def _validate_active_surface(path: Path, *, line_limit: int, forbidden: tuple[str, ...]) -> None:
    text = path.read_text(encoding="utf-8")
    if len(text.splitlines()) > line_limit:
        raise CapabilityInfrastructureError(f"active surface is not thin: {path}")
    found = [term for term in forbidden if term in text]
    if found:
        raise CapabilityInfrastructureError(
            f"active surface contains historical or case routing: {found}"
        )


def verify_capability_infrastructure(root: Path, ledger_path: Path) -> dict:
    """Verify current D-18 capability infrastructure without executing a capability."""

    root = Path(root).resolve()
    ledger = _read_object(Path(ledger_path), "capability infrastructure ledger")
    if ledger.get("contract") != "lca_agent_capability_infrastructure/v1":
        raise CapabilityInfrastructureError("capability infrastructure contract drifted")
    if ledger.get("status") not in {
        "implementation_ready_pending_final_acceptance",
        "adopted",
    }:
        raise CapabilityInfrastructureError("capability infrastructure status is invalid")

    startup_path = _require_sha(root, ledger["startup_binding"], "startup_binding")
    source_lock_path = _require_sha(root, ledger["source_lock"], "source_lock")
    catalog_path = _require_sha(root, ledger["catalog"], "catalog")
    _require_sha(root, ledger["inventory"], "inventory")

    startup = _read_object(startup_path, "startup manifest")
    if startup.get("contract") != "lca_research_kernel.agent_startup_manifest/v1":
        raise CapabilityInfrastructureError("startup contract drifted")

    catalog = CapabilityCatalog.from_files(catalog_path, source_lock_path)
    descriptors = catalog.list_descriptors()
    if len(descriptors) != ledger["catalog"].get("capability_count"):
        raise CapabilityInfrastructureError("catalog capability count drifted")
    if catalog.snapshot_sha256 != ledger["catalog"].get("semantic_snapshot_sha256"):
        raise CapabilityInfrastructureError("catalog semantic snapshot drifted")
    if ledger["catalog"].get("unclassified_count") != 0:
        raise CapabilityInfrastructureError("catalog contains unclassified capabilities")
    by_kind = dict(sorted(Counter(row["kind"] for row in descriptors).items()))
    by_disposition = dict(
        sorted(Counter(row["disposition"] for row in descriptors).items())
    )
    if set(by_kind) != KINDS or by_kind != ledger["catalog"].get("by_kind"):
        raise CapabilityInfrastructureError("catalog kind coverage drifted")
    if by_disposition != ledger["catalog"].get("by_disposition"):
        raise CapabilityInfrastructureError("catalog disposition coverage drifted")

    active = ledger.get("active_surfaces", {})
    controller_path = _require_sha(root, active["controller"], "controller")
    skill_path = _require_sha(root, active["skill"], "skill")
    _validate_active_surface(
        controller_path,
        line_limit=220,
        forbidden=(
            "LiFePO4",
            "USLCI",
            "Freshwater",
            "Experimental Research Director Routing Contract",
            "Historical implementation appendix",
        ),
    )
    _validate_active_surface(
        skill_path,
        line_limit=180,
        forbidden=(
            "LiFePO4",
            "USLCI",
            "Freshwater",
            "next_route",
            "council-",
            "tidas-context",
            "materialize-processes",
            "publish",
        ),
    )

    legacy = ledger["legacy_surfaces"]["evidence_to_tidas_lca"]
    object_name = legacy.get("git_object")
    if not isinstance(object_name, str) or ":" not in object_name:
        raise CapabilityInfrastructureError("legacy git object identity is invalid")
    legacy_bytes = _git_object_bytes(root, object_name)
    if hashlib.sha256(legacy_bytes).hexdigest() != legacy.get("sha256"):
        raise CapabilityInfrastructureError("legacy git object drifted")
    if legacy.get("mutation_policy") != "do_not_modify_live_dirty_bytes":
        raise CapabilityInfrastructureError("legacy live-byte policy drifted")

    effects = ledger.get("effects_observed")
    if not isinstance(effects, Mapping):
        raise CapabilityInfrastructureError("effects ledger is invalid")
    prohibited = {str(key): value for key, value in effects.items() if value != 0}
    if prohibited:
        raise CapabilityInfrastructureError(f"prohibited effect observed: {prohibited}")

    return {
        "status": "passed",
        "capability_count": len(descriptors),
        "unclassified_count": 0,
        "catalog_snapshot_sha256": catalog.snapshot_sha256,
        "prohibited_effect_count": 0,
        "legacy_object_status": "matched",
    }
