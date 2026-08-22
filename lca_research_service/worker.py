"""Worker entry point for one isolated D-18 job."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from datetime import datetime
from typing import Any

from .contracts import TERMINAL_STATES
from .failures import normalize_failure
from .job_store import JobStore
from .runtime_factory import RuntimeFactory
from .runtime import MaterialResolutionError
from .input_artifacts import _ensure_directory, _fsync_directory
from .release_manifest import (
    load_runtime_release_manifest,
    require_request_release,
)

_PASSTHROUGH_ENVIRONMENT = ("PATH", "LANG", "LC_ALL", "TZ")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def sanitized_worker_environment(
    source: Mapping[str, str] | None = None,
    *,
    allowed_runtime_names: tuple[str, ...] = (),
) -> dict[str, str]:
    source = os.environ if source is None else source
    result = {
        key: source[key]
        for key in _PASSTHROUGH_ENVIRONMENT
        if key in source and source[key]
    }
    for key in allowed_runtime_names:
        if (
            not isinstance(key, str)
            or not key
            or not key.replace("_", "").isalnum()
            or not key[0].isalpha()
        ):
            raise ValueError("runtime environment name is invalid")
        if key in source and source[key]:
            result[key] = source[key]
    result["PYTHONNOUSERSITE"] = "1"
    return result


def _write_failure_receipt(
    store: JobStore,
    job_id: str,
    error: BaseException,
    *,
    data_root: Path | None,
) -> dict[str, str]:
    root = (
        Path(data_root).resolve()
        if data_root is not None
        else (
            store.path.parent.parent.resolve()
            if store.path.parent.name == "control"
            else store.path.parent.resolve()
        )
    )
    value = {
        **normalize_failure(error, layer="worker"),
        "job_id": job_id,
    }
    payload = (
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    digest = hashlib.sha256(payload).hexdigest()
    relative = Path("jobs") / job_id / "failures" / f"{digest}.json"
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise ValueError("failure receipt path escaped data root")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise ValueError("failure receipt drifted")
    if not target.exists():
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(payload)
        temporary.replace(target)
    return {
        "path": relative.as_posix(),
        "sha256": digest,
        "role": "failure_receipt",
    }


def run_claimed_job(
    store: JobStore,
    job_id: str,
    *,
    worker_id: str,
    handler: Callable[[dict[str, Any]], Mapping[str, Any]],
    data_root: Path | None = None,
) -> dict[str, Any]:
    row = store.get(job_id)
    if row["status"] in TERMINAL_STATES | {"waiting_for_input"}:
        return row
    if row["status"] != "leased" or row["worker_id"] != worker_id:
        raise PermissionError("worker does not own the claimed job")
    running_ref = {
        "path": f"jobs/{job_id}/run-trace.jsonl",
        "sha256": hashlib.sha256(f"{job_id}:running".encode()).hexdigest(),
        "role": "run_trace",
    }
    running = store.transition(job_id, "leased", "running", running_ref)
    lease_expires_at = running.get("lease_expires_at")
    updated_at = running.get("updated_at")
    heartbeat_interval = 10.0
    if isinstance(lease_expires_at, str) and isinstance(updated_at, str):
        duration = (
            datetime.fromisoformat(lease_expires_at.replace("Z", "+00:00"))
            - datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        ).total_seconds()
        heartbeat_interval = max(0.1, min(10.0, duration / 3))
    heartbeat_stop = threading.Event()
    heartbeat_errors: list[BaseException] = []

    def renew_lease() -> None:
        while not heartbeat_stop.wait(heartbeat_interval):
            try:
                store.heartbeat(job_id, worker_id)
            except BaseException as error:
                heartbeat_errors.append(error)
                heartbeat_stop.set()

    heartbeat = threading.Thread(target=renew_lease, daemon=True)
    heartbeat.start()
    try:
        outcome = handler(running)
        if heartbeat_errors:
            raise RuntimeError("worker lease heartbeat failed")
        target = outcome.get("target")
        ref = outcome.get("ref")
        if target not in {
            "queued",
            "waiting_for_input",
            "completed",
            "bounded",
            "failed",
        }:
            raise ValueError("worker handler returned an invalid terminal target")
        return store.transition(job_id, "running", target, ref)
    except Exception as error:
        current = store.get(job_id)
        if current["status"] != "running":
            raise
        failure_ref = _write_failure_receipt(
            store, job_id, error, data_root=data_root
        )
        store.transition(job_id, "running", "failed", failure_ref)
        raise
    finally:
        heartbeat_stop.set()
        heartbeat.join(timeout=max(1.0, heartbeat_interval * 2))


def _load_json_ref(root: Path, reference: Mapping[str, Any]) -> dict[str, Any]:
    path = reference.get("path")
    digest = reference.get("sha256")
    if not isinstance(path, str) or not isinstance(digest, str):
        raise ValueError("worker content reference is invalid")
    target = (root / path).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise ValueError("worker content reference is unavailable")
    payload = target.read_bytes()
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("worker content reference drifted")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("worker content reference must contain an object")
    return value


def _write_recovery_artifact(
    root: Path,
    project_id: str,
    job_id: str,
    role: str,
    value: Mapping[str, Any],
) -> dict[str, str]:
    if (
        _SAFE_IDENTIFIER.fullmatch(project_id) is None
        or _SAFE_IDENTIFIER.fullmatch(job_id) is None
    ):
        raise ValueError("job recovery identity is invalid")
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    digest = hashlib.sha256(payload).hexdigest()
    relative = (
        Path("projects") / project_id / "jobs" / job_id / "recovery" /
        f"{digest}.json"
    )
    target = root / relative
    _ensure_directory(target.parent, root=root)

    def verify_published() -> None:
        descriptor = os.open(
            target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_mode & 0o077
                or before.st_size != len(payload)
            ):
                raise ValueError("job recovery artifact drifted")
            chunks: list[bytes] = []
            remaining = len(payload) + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(remaining, 1024 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_nlink,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_nlink,
            ) or hashlib.sha256(b"".join(chunks)).hexdigest() != digest:
                raise ValueError("job recovery artifact drifted")
        finally:
            os.close(descriptor)

    if target.exists() or target.is_symlink():
        verify_published()
    else:
        descriptor, name = tempfile.mkstemp(prefix=".recovery-", dir=target.parent)
        temporary = Path(name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as sink:
                sink.write(payload)
                sink.flush()
                os.fsync(sink.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
            _fsync_directory(target.parent)
        finally:
            temporary.unlink(missing_ok=True)
        verify_published()
    return {"path": relative.as_posix(), "sha256": digest, "role": role}


def _material_recovery_request(
    request: Mapping[str, Any],
    external_input: Mapping[str, Any],
) -> dict[str, Any] | None:
    recovery = external_input.get("material_recovery")
    if not isinstance(recovery, Mapping) or recovery.get("contract") != (
        "lca_research_service.material_processing_recovery/v1"
    ):
        return None
    failed = recovery.get("failed_materials")
    if not isinstance(failed, list) or not failed:
        raise ValueError("material recovery failures are absent")
    failed_ids = {
        row.get("material_id")
        for row in failed
        if isinstance(row, Mapping) and isinstance(row.get("material_id"), str)
    }
    if len(failed_ids) != len(failed):
        raise ValueError("material recovery identities are invalid")
    effective = dict(request)
    material_ids = effective.get("material_ids", [])
    revision_refs = effective.get("material_revision_refs", [])
    if not isinstance(material_ids, list) or not isinstance(revision_refs, list):
        raise ValueError("material recovery request is invalid")
    if len(material_ids) != len(revision_refs):
        raise ValueError("material recovery request is unbound")
    payload = external_input.get("payload")
    kind = external_input.get("kind")
    if kind == "decision":
        decision = payload.get("decision") if isinstance(payload, Mapping) else None
        if decision == "retry":
            return {
                "action": "retry",
                "request": effective,
                "failed_materials": failed,
            }
        if decision != "omit_failed_materials":
            raise ValueError("material recovery decision is unsupported")
        retained = [
            (material_id, reference)
            for material_id, reference in zip(material_ids, revision_refs, strict=True)
            if material_id not in failed_ids
        ]
    elif kind == "material":
        new_ids = payload.get("material_ids") if isinstance(payload, Mapping) else None
        new_refs = (
            payload.get("material_revision_refs")
            if isinstance(payload, Mapping)
            else None
        )
        if not isinstance(new_ids, list) or not isinstance(new_refs, list):
            raise ValueError("replacement material recovery is unbound")
        retained = [
            (material_id, reference)
            for material_id, reference in zip(material_ids, revision_refs, strict=True)
            if material_id not in failed_ids
        ] + list(zip(new_ids, new_refs, strict=True))
    else:
        raise ValueError("material recovery input kind is unsupported")
    effective["material_ids"] = [row[0] for row in retained]
    effective["material_revision_refs"] = [row[1] for row in retained]
    return {
        "action": (
            "omit_failed_materials"
            if kind == "decision"
            else "replace_failed_materials"
        ),
        "request": effective,
        "failed_materials": failed,
    }


def _product_outcome(
    row: dict[str, Any],
    *,
    runtime: object,
    data_root: Path,
) -> dict[str, Any]:
    """Resume through the runtime's shared Product Interface/material resolver."""

    request_ref = row.get("request_ref")
    if not isinstance(request_ref, Mapping):
        raise ValueError("job request reference is absent")
    request = _load_json_ref(data_root, request_ref)
    research_id = request.get("research_id")
    if not isinstance(research_id, str) or not research_id:
        raise ValueError("job research_id is absent")
    input_ref = row.get("input_ref")
    active_request = request
    try:
        if request.get("contract") == (
            "lca_research_service.research_continuation_job/v1"
        ):
            status = runtime.continue_turn(request, request_ref)
        elif isinstance(input_ref, Mapping):
            external_input = _load_json_ref(data_root, input_ref)
            recovered_request = _material_recovery_request(request, external_input)
            if recovered_request is None:
                status = runtime.supply(research_id, external_input)
            else:
                active_request = recovered_request["request"]
                effective_refs = active_request.get("material_revision_refs", [])
                recorder = getattr(runtime, "record_material_recovery_decision", None)
                if not callable(recorder):
                    raise ValueError("material recovery audit is unavailable")
                recorder(
                    research_id,
                    external_input,
                    effective_refs,
                    request,
                    request_ref,
                )
                if recovered_request["action"] == "retry":
                    pending_probe = getattr(runtime, "materials_are_processing", None)
                    if callable(pending_probe) and pending_probe(
                        research_id,
                        active_request.get("material_ids", []),
                        effective_refs,
                    ):
                        return {"target": "queued", "ref": request_ref}
                    boundary_probe = getattr(
                        runtime, "material_processing_boundary", None
                    )
                    boundary = (
                        boundary_probe(
                            research_id,
                            active_request.get("material_ids", []),
                            effective_refs,
                        )
                        if callable(boundary_probe)
                        else None
                    )
                    if isinstance(boundary, Mapping):
                        retryer = getattr(runtime, "retry_material_processing", None)
                        if not callable(retryer):
                            raise ValueError("material processing retry is unavailable")
                        retried = retryer(
                            research_id,
                            recovered_request["failed_materials"],
                            str(input_ref["sha256"]),
                        )
                        if (
                            isinstance(retried, list)
                            and retried
                            and all(
                                isinstance(item, Mapping)
                                and item.get("retry_started") is True
                                for item in retried
                            )
                        ):
                            return {"target": "queued", "ref": request_ref}
                        recovery_ref = _write_recovery_artifact(
                            data_root,
                            research_id,
                            str(row["job_id"]),
                            "material_processing_recovery",
                            boundary,
                        )
                        return {
                            "target": "waiting_for_input",
                            "ref": recovery_ref,
                        }
                recovered_ref = _write_recovery_artifact(
                    data_root,
                    research_id,
                    str(row["job_id"]),
                    "material_recovery_request",
                    active_request,
                )
                status = runtime.start(active_request, recovered_ref)
        else:
            status = runtime.start(request, request_ref)
    except MaterialResolutionError:
        pending_probe = getattr(runtime, "materials_are_processing", None)
        revision_refs = active_request.get("material_revision_refs")
        still_processing = (
            pending_probe(
                research_id,
                active_request.get("material_ids", []),
                revision_refs,
            )
            if callable(pending_probe) and revision_refs is not None
            else (
                pending_probe(research_id, active_request.get("material_ids", []))
                if callable(pending_probe)
                else False
            )
        )
        if still_processing:
            return {"target": "queued", "ref": request_ref}
        boundary_probe = getattr(runtime, "material_processing_boundary", None)
        boundary = (
            boundary_probe(
                research_id,
                active_request.get("material_ids", []),
                revision_refs,
            )
            if callable(boundary_probe) and revision_refs is not None
            else None
        )
        if isinstance(boundary, Mapping):
            recovery_ref = _write_recovery_artifact(
                data_root,
                research_id,
                str(row["job_id"]),
                "material_processing_recovery",
                boundary,
            )
            return {"target": "waiting_for_input", "ref": recovery_ref}
        raise
    result = runtime.read_result(research_id)
    reference = result.get("project_record_ref") or status.get(
        "project_record_ref"
    )
    if result.get("status") == "accepted":
        accepted = result.get("result")
        if not isinstance(accepted, Mapping):
            raise ValueError("accepted Product Interface result is invalid")
        target = (
            "bounded"
            if accepted.get("status") == "formal_limited_result_accepted"
            or accepted.get("claim_ceiling")
            == "bounded_missing_data_conclusion"
            else "completed"
        )
        return {"target": target, "ref": reference}
    if (
        status.get("boundary") == "awaiting_user_data"
        and (
            (
                status.get("interaction_kind") == "factual_data_availability"
                and status.get("authorization_required") is False
            )
            or (
                status.get("interaction_kind") == "material_scope_authorization"
                and status.get("authorization_required") is True
            )
        )
        and isinstance(status.get("question"), str)
        and status["question"].strip()
    ):
        return {"target": "waiting_for_input", "ref": reference}
    return {"target": "failed", "ref": reference}


def run_product_job(
    store: JobStore,
    job_id: str,
    *,
    worker_id: str,
    runtime: object,
    data_root: Path,
) -> dict[str, Any]:
    return run_claimed_job(
        store,
        job_id,
        worker_id=worker_id,
        handler=lambda row: _product_outcome(
            row, runtime=runtime, data_root=Path(data_root)
        ),
        data_root=Path(data_root),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--control-db", type=Path, required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    args = parser.parse_args(argv)
    with JobStore(args.control_db) as store:
        try:
            manifest_path = os.environ.get("D18_RELEASE_MANIFEST_FILE")
            if not manifest_path:
                raise ValueError("runtime release manifest is unavailable")
            manifest = load_runtime_release_manifest(
                manifest_path,
                repository_root=args.repository_root,
                frontend_root=args.repository_root / "apps/web",
            )
            row = store.get(args.job_id)
            request_ref = row.get("request_ref")
            if not isinstance(request_ref, Mapping):
                raise ValueError("job request reference is absent")
            request = _load_json_ref(args.data_root, request_ref)
            require_request_release(request, manifest)
        except (KeyError, OSError, TypeError, ValueError) as error:
            try:
                run_claimed_job(
                    store,
                    args.job_id,
                    worker_id=args.worker_id,
                    handler=lambda _row, failure=error: (
                        _ for _ in ()
                    ).throw(failure),
                    data_root=args.data_root,
                )
            except (PermissionError, RuntimeError, ValueError):
                pass
            return 1
        binding = RuntimeFactory(
            repository_root=args.repository_root,
            data_root=args.data_root,
        ).build()
        if not binding.ready or binding.runtime is None:
            try:
                run_claimed_job(
                    store,
                    args.job_id,
                    worker_id=args.worker_id,
                    handler=lambda _row: (_ for _ in ()).throw(
                        RuntimeError("Product Interface runtime is unavailable")
                    ),
                    data_root=args.data_root,
                )
            except RuntimeError:
                return 1
            return 1
        run_product_job(
            store,
            args.job_id,
            worker_id=args.worker_id,
            runtime=binding.runtime,
            data_root=args.data_root,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
