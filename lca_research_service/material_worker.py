"""Bounded worker for one candidate material revision."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import socket
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .input_artifacts import InputArtifactStore
from .material_processing import process_input_artifact
from .material_sandbox import (
    apply_material_memory_limit,
    enter_material_sandbox,
    probe_material_runtime,
)
from .material_store import MaterialStore


class MaterialCapabilitySnapshot:
    """Read-only list interface over one verified serializable snapshot."""

    def __init__(self, value: Mapping[str, object]):
        unsigned = dict(value)
        claimed = unsigned.pop("snapshot_sha256", None)
        encoded = json.dumps(
            unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        if (
            unsigned.get("contract")
            != "lca_research_kernel.material_capability_snapshot/v1"
            or claimed != hashlib.sha256(encoded).hexdigest()
            or unsigned.get("candidate_only") is not True
            or not isinstance(unsigned.get("catalog_snapshot_sha256"), str)
            or not isinstance(unsigned.get("capabilities"), list)
        ):
            raise ValueError("material capability snapshot is invalid")
        rows = unsigned["capabilities"]
        if any(
            not isinstance(row, Mapping)
            or not isinstance(row.get("capability_id"), str)
            or not isinstance(row.get("runtime_available"), bool)
            or not isinstance(row.get("source_lock"), Mapping)
            for row in rows
        ):
            raise ValueError("material capability snapshot is incomplete")
        self._rows = list(rows)

    def list_capabilities(self, _context: Mapping[str, object]) -> list[dict]:
        return json.loads(json.dumps(self._rows))


def load_material_capability_snapshot(serialized: str | None) -> object:
    if serialized is None:
        snapshot = {
            "contract": "lca_research_kernel.material_capability_snapshot/v1",
            "catalog_snapshot_sha256": "unavailable",
            "capabilities": [],
            "candidate_only": True,
        }
        snapshot["snapshot_sha256"] = hashlib.sha256(
            json.dumps(
                snapshot,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return MaterialCapabilitySnapshot(snapshot)
    return MaterialCapabilitySnapshot(json.loads(serialized))


def material_worker_argv(command: list[str]) -> list[str]:
    """Validate the qualified platform boundary before launching its bootstrap."""

    if not probe_material_runtime()["ready"]:
        raise RuntimeError("an OS material sandbox is unavailable")
    if not command or not all(isinstance(item, str) and item for item in command):
        raise ValueError("material worker command is invalid")
    return list(command)


def sanitized_material_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if source is None else source
    result = {key: source[key] for key in ("LANG", "LC_ALL", "TZ") if source.get(key)}
    result["PYTHONNOUSERSITE"] = "1"
    return result


def apply_material_resource_limits(
    *, cpu_seconds: int = 30, memory_bytes: int = 768 * 1024 * 1024, output_bytes: int = 128 * 1024 * 1024
) -> None:
    apply_material_memory_limit(memory_bytes)
    limits = (
        (resource.RLIMIT_CPU, cpu_seconds),
        (resource.RLIMIT_FSIZE, output_bytes),
        (resource.RLIMIT_NOFILE, 64),
    )
    for kind, ceiling in limits:
        soft, hard = resource.getrlimit(kind)
        bounded = ceiling if hard == resource.RLIM_INFINITY else min(ceiling, hard)
        resource.setrlimit(kind, (bounded, bounded))


def disable_material_network() -> None:
    def denied(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("material processors have no network permission")
    socket.socket = denied  # type: ignore[assignment]
    socket.create_connection = denied  # type: ignore[assignment]


def run_claimed_material(
    store: MaterialStore,
    claim: Mapping[str, Any],
    *,
    worker_id: str,
    capability_snapshot: object = None,
) -> dict[str, Any]:
    if claim.get("worker_id") != worker_id:
        raise PermissionError("material worker claim identity drifted")
    revision_id = str(claim["revision_id"])
    project_id = str(claim["project_id"])
    artifact_id = str(claim["input_artifact_id"])
    if capability_snapshot is None:
        capability_snapshot = load_material_capability_snapshot(None)
    store.append_material_event(
        project_id=project_id, material_id=str(claim["material_id"]),
        event_type="research.material.processing_started",
        payload={"revision_id": revision_id, "attempt": claim["attempt"], "candidate_only": True},
    )
    try:
        store.mark_revision_parsing(revision_id=revision_id, worker_id=worker_id)
        metadata = store.input_artifacts.describe(research_id=project_id, artifact_id=artifact_id)
        with store.input_artifacts.open_verified(research_id=project_id, artifact_id=artifact_id) as source:
            outcome = process_input_artifact(
                metadata,
                source,
                revision_id=revision_id,
                data_root=store.data_root,
                capability_gateway=capability_snapshot,
            )
    except Exception as error:
        store.fail_revision_processing(
            revision_id=revision_id,
            worker_id=worker_id,
            failure_category="format_failure",
            detail=type(error).__name__,
        )
        raise
    extraction_refs = [
        {"artifact_id": reference["sha256"], "sha256": reference["sha256"]}
        for reference in outcome.receipt["output_refs"]
    ]
    updated = store.finish_revision_processing(
        revision_id=revision_id, worker_id=worker_id, status=outcome.receipt["status"],
        receipt_ref=outcome.receipt_ref, extraction_refs=extraction_refs,
        lca_package_route=outcome.lca_package_route,
    )
    route = outcome.lca_package_route
    return {
        **updated,
        "receipt_ref": outcome.receipt_ref,
        "locators": outcome.locators,
        "lca_package_route": route,
    }


def run_material_once(
    store: MaterialStore,
    *,
    worker_id: str,
    lease_seconds: int = 60,
    capability_snapshot: object = None,
) -> dict[str, Any] | None:
    claim = store.claim_revision(worker_id=worker_id, lease_seconds=lease_seconds)
    if claim is None:
        return None
    return run_claimed_material(
        store,
        claim,
        worker_id=worker_id,
        capability_snapshot=capability_snapshot,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-db", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--revision-id", required=True)
    parser.add_argument("--capability-snapshot-json")
    args = parser.parse_args(argv)
    environment = sanitized_material_environment()
    os.environ.clear(); os.environ.update(environment)
    enter_material_sandbox()
    disable_material_network()
    apply_material_resource_limits()
    artifacts = InputArtifactStore(args.data_root)
    store = MaterialStore(args.control_db, input_artifacts=artifacts, data_root=args.data_root)
    try:
        owned = store.owned_revision_claim(
            revision_id=args.revision_id, worker_id=args.worker_id
        )
        result = run_claimed_material(
            store,
            owned,
            worker_id=args.worker_id,
            capability_snapshot=load_material_capability_snapshot(
                args.capability_snapshot_json
            ),
        )
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
