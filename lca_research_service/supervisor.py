"""Isolated worker subprocess supervision."""

from __future__ import annotations

import subprocess
import sys
import time
import os
import hashlib
import json
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .job_store import JobStore
from .runtime_factory import required_runtime_environment_names
from .worker import (
    _write_failure_receipt,
    run_claimed_job,
    sanitized_worker_environment,
)
from .material_store import MaterialStore
from .material_worker import material_worker_argv
from .material_sandbox import material_resident_bytes, probe_material_runtime
from lca_research_kernel.run_control import (
    DEFAULT_RUN_BUDGET,
    supervisor_heartbeat_timeout_seconds,
    supervisor_timeout_seconds,
)


_DEFAULT_MATERIAL_MEMORY_BYTES = 768 * 1024 * 1024


class WorkerSupervisor:
    def __init__(
        self,
        store: JobStore,
        *,
        python: str | Path = sys.executable,
        control_db: str | Path,
        data_root: str | Path | None = None,
        repository_root: str | Path | None = None,
        worker_id: str = "d18-supervisor",
        lease_seconds: int = 60,
        popen_factory: Any = subprocess.Popen,
        runtime_environment_names: tuple[str, ...] | None = None,
        environment_source: Mapping[str, str] | None = None,
        material_store: MaterialStore | None = None,
        material_control_db: str | Path | None = None,
        material_memory_bytes: int = _DEFAULT_MATERIAL_MEMORY_BYTES,
        material_timeout_seconds: float = 120.0,
        product_timeout_seconds: float | None = None,
        product_heartbeat_timeout_seconds: float | None = None,
        material_capability_snapshot: Mapping[str, object] | None = None,
        monotonic: Any = time.monotonic,
    ) -> None:
        self.store = store
        self.python = str(python)
        self.control_db = str(control_db)
        self.data_root = str(
            Path(data_root)
            if data_root is not None
            else Path(control_db).resolve().parent
        )
        self.repository_root = str(
            Path(repository_root)
            if repository_root is not None
            else Path(__file__).resolve().parents[1]
        )
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self._popen = popen_factory
        self.runtime_environment_names = (
            required_runtime_environment_names(Path(self.repository_root))
            if runtime_environment_names is None
            else tuple(runtime_environment_names)
        )
        self.runtime_environment_names = tuple(
            dict.fromkeys(
                (*self.runtime_environment_names, "D18_MODEL_CAPACITY_QUALIFICATION_FILE")
                + ("D18_SERVICE_RUN_ID", "D18_RELEASE_MANIFEST_FILE")
            )
        )
        self.environment_source = (
            os.environ if environment_source is None else environment_source
        )
        self.material_store = material_store
        self.material_control_db = str(
            material_control_db
            if material_control_db is not None
            else (material_store.path if material_store is not None else "")
        )
        if (
            not isinstance(material_memory_bytes, int)
            or isinstance(material_memory_bytes, bool)
            or material_memory_bytes <= 0
        ):
            raise ValueError("material memory ceiling is invalid")
        self.material_memory_bytes = material_memory_bytes
        if material_timeout_seconds <= 0:
            raise ValueError("material timeout is invalid")
        self.material_timeout_seconds = float(material_timeout_seconds)
        effective_product_timeout = (
            supervisor_timeout_seconds(DEFAULT_RUN_BUDGET)
            if product_timeout_seconds is None
            else product_timeout_seconds
        )
        if effective_product_timeout <= 0:
            raise ValueError("product timeout is invalid")
        self.product_timeout_seconds = float(effective_product_timeout)
        effective_heartbeat_timeout = (
            supervisor_heartbeat_timeout_seconds(DEFAULT_RUN_BUDGET)
            if product_heartbeat_timeout_seconds is None
            else product_heartbeat_timeout_seconds
        )
        if effective_heartbeat_timeout <= 0:
            raise ValueError("product heartbeat timeout is invalid")
        self.product_heartbeat_timeout_seconds = float(
            effective_heartbeat_timeout
        )
        self._monotonic = monotonic
        self.material_capability_snapshot = (
            None
            if material_capability_snapshot is None
            else json.loads(json.dumps(material_capability_snapshot, sort_keys=True))
        )
        if material_store is not None and not probe_material_runtime()["ready"]:
            raise RuntimeError("material sandbox runtime is unavailable")
        self._processes: dict[str, Any] = {}
        self._material_started_at: dict[str, float] = {}
        self._product_started_at: dict[str, float] = {}
        self._product_heartbeat_signature: dict[str, tuple[object, object]] = {}
        self._product_heartbeat_seen_at: dict[str, float] = {}
        self._last_spawn_lane = "product"
        self._running = False
        self._failure: BaseException | None = None

    def start(self) -> None:
        self._running = True

    def serve(
        self,
        stop_event: threading.Event,
        *,
        poll_interval: float = 0.2,
    ) -> None:
        self.start()
        try:
            while not stop_event.is_set():
                self.run_once()
                stop_event.wait(max(0.01, poll_interval))
        except BaseException as error:
            self._failure = error
            raise
        finally:
            self._running = False

    def probe(self) -> None:
        """Fail readiness after the serving loop exits or crashes."""

        if self._failure is not None:
            raise RuntimeError("worker supervisor is unavailable") from self._failure
        if not self._running:
            raise RuntimeError("worker supervisor is not running")

    def run_once(self) -> dict[str, Any] | None:
        product_reaped = self._reap_product_processes()
        if product_reaped is not None:
            return product_reaped
        reaped = self._reap_material_processes()
        if reaped is not None:
            return reaped
        if any(key.startswith("material:") for key in self._processes):
            return {"status": "material_processing"}
        prefer_material = self._last_spawn_lane != "material"
        if prefer_material:
            material_claim = self.run_material_once()
            if material_claim is not None:
                self._last_spawn_lane = "material"
                return material_claim
        claimed = self.store.claim(self.worker_id, self.lease_seconds)
        if claimed is None and not prefer_material:
            material_claim = self.run_material_once()
            if material_claim is not None:
                self._last_spawn_lane = "material"
                return material_claim
        if claimed is None:
            return None
        self._last_spawn_lane = "product"
        argv = [
            self.python,
            "-m",
            "lca_research_service.worker",
            "--job-id",
            claimed["job_id"],
            "--control-db",
            self.control_db,
            "--worker-id",
            self.worker_id,
            "--data-root",
            self.data_root,
            "--repository-root",
            self.repository_root,
        ]
        try:
            process = self._popen(
                argv,
                env=sanitized_worker_environment(
                    self.environment_source,
                    allowed_runtime_names=self.runtime_environment_names,
                ),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except Exception:
            try:
                run_claimed_job(
                    self.store,
                    claimed["job_id"],
                    worker_id=self.worker_id,
                    handler=lambda _row: (_ for _ in ()).throw(
                        RuntimeError("worker process failed to start")
                    ),
                    data_root=Path(self.data_root),
                )
            except RuntimeError:
                pass
            return self.store.get(claimed["job_id"])
        self._processes[claimed["job_id"]] = process
        observed_at = self._monotonic()
        self._product_started_at[claimed["job_id"]] = observed_at
        self._product_heartbeat_signature[claimed["job_id"]] = (
            claimed.get("updated_at"),
            claimed.get("version"),
        )
        self._product_heartbeat_seen_at[claimed["job_id"]] = observed_at
        return claimed

    def _reap_product_processes(self) -> dict[str, Any] | None:
        """Bound and reap production jobs even when a child stops heartbeating."""

        for job_id, process in list(self._processes.items()):
            if job_id.startswith("material:"):
                continue
            return_code = process.poll()
            timed_out = False
            heartbeat_timed_out = False
            row = self.store.get(job_id)
            if return_code is None:
                observed_at = self._monotonic()
                started_at = self._product_started_at.get(job_id)
                signature = (row.get("updated_at"), row.get("version"))
                if self._product_heartbeat_signature.get(job_id) != signature:
                    self._product_heartbeat_signature[job_id] = signature
                    self._product_heartbeat_seen_at[job_id] = observed_at
                heartbeat_seen_at = self._product_heartbeat_seen_at.get(job_id)
                timed_out = (
                    started_at is None
                    or observed_at - started_at >= self.product_timeout_seconds
                )
                heartbeat_timed_out = (
                    row["status"] in {"leased", "running"}
                    and (
                        heartbeat_seen_at is None
                        or observed_at - heartbeat_seen_at
                        >= self.product_heartbeat_timeout_seconds
                    )
                )
                if not timed_out and not heartbeat_timed_out:
                    continue
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                process.wait()
                return_code = process.returncode
            self._processes.pop(job_id, None)
            self._product_started_at.pop(job_id, None)
            self._product_heartbeat_signature.pop(job_id, None)
            self._product_heartbeat_seen_at.pop(job_id, None)
            row = self.store.get(job_id)
            if row["status"] in {
                "waiting_for_input",
                "completed",
                "bounded",
                "failed",
                "cancelled",
            }:
                return row
            if row["status"] == "cancelling":
                return finalize_cancelled_job(
                    self.store,
                    job_id,
                    data_root=Path(self.data_root),
                )
            error: BaseException = (
                TimeoutError(
                    "product worker heartbeat timed out"
                    if heartbeat_timed_out
                    else "product worker deadline exhausted"
                )
                if timed_out or heartbeat_timed_out
                else RuntimeError(f"product worker exited with {return_code}")
            )
            if row["status"] == "leased":
                try:
                    run_claimed_job(
                        self.store,
                        job_id,
                        worker_id=self.worker_id,
                        handler=lambda _row, failure=error: (
                            _ for _ in ()
                        ).throw(failure),
                        data_root=Path(self.data_root),
                    )
                except Exception:
                    pass
                return self.store.get(job_id)
            if row["status"] == "running":
                failure_ref = _write_failure_receipt(
                    self.store,
                    job_id,
                    error,
                    data_root=Path(self.data_root),
                )
                return self.store.transition(
                    job_id,
                    "running",
                    "failed",
                    failure_ref,
                )
            raise ValueError("product worker exited from an invalid job state")
        return None

    def run_material_lane_once(self) -> dict[str, Any] | None:
        """Advance only the sandboxed material lane without leasing product work."""

        reaped = self._reap_material_processes()
        if reaped is not None:
            return reaped
        if any(key.startswith("material:") for key in self._processes):
            return {"status": "material_processing"}
        return self.run_material_once()

    def run_material_once(self) -> dict[str, Any] | None:
        """Extend the existing supervisor with one material-worker lane."""

        if self.material_store is None:
            return None
        claimed = self.material_store.claim_revision(
            worker_id=f"{self.worker_id}-material",
            lease_seconds=self.lease_seconds,
        )
        if claimed is None:
            return None
        worker_argv = [
            self.python,
            "-m",
            "lca_research_service.material_worker",
            "--revision-id",
            claimed["revision_id"],
            "--control-db",
            self.material_control_db,
            "--worker-id",
            f"{self.worker_id}-material",
            "--data-root",
            self.data_root,
        ]
        if self.material_capability_snapshot is not None:
            worker_argv.extend(
                [
                    "--capability-snapshot-json",
                    json.dumps(
                        self.material_capability_snapshot,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ]
            )
        try:
            argv = material_worker_argv(worker_argv)
            process = self._popen(
                argv,
                env=sanitized_worker_environment({
                    key: self.environment_source[key]
                    for key in ("LANG", "LC_ALL", "TZ")
                    if self.environment_source.get(key)
                }),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except Exception as error:
            self.material_store.fail_revision_processing(
                revision_id=claimed["revision_id"],
                worker_id=f"{self.worker_id}-material",
                failure_category="worker_launch_failure",
                detail=type(error).__name__,
            )
            return self.material_store.get_revision(claimed["revision_id"])
        process_key = f"material:{claimed['revision_id']}"
        self._processes[process_key] = process
        self._material_started_at[process_key] = self._monotonic()
        return claimed

    def _reap_material_processes(self) -> dict[str, Any] | None:
        if self.material_store is None:
            return None
        for key, process in list(self._processes.items()):
            if not key.startswith("material:"):
                continue
            return_code = process.poll()
            failure_category = None
            if return_code is None:
                started_at = self._material_started_at.get(key)
                if (
                    started_at is not None
                    and self._monotonic() - started_at
                    >= self.material_timeout_seconds
                ):
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    process.wait()
                    return_code = process.returncode
                    failure_category = "worker_timeout"
                else:
                    return_code = None
            if return_code is None:
                try:
                    over_memory = (
                        material_resident_bytes(process.pid)
                        > self.material_memory_bytes
                    )
                except RuntimeError:
                    return_code = process.poll()
                    if return_code is None:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                        process.wait()
                        return_code = process.returncode
                        failure_category = "memory_monitor_failure"
                    else:
                        failure_category = "worker_exit_failure"
                else:
                    if not over_memory:
                        continue
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    process.wait()
                    return_code = process.returncode
                    failure_category = "memory_limit"
            elif failure_category is None:
                failure_category = "worker_exit_failure"
            self._processes.pop(key, None)
            self._material_started_at.pop(key, None)
            revision_id = key.split(":", 1)[1]
            stored = self.material_store.get_revision(revision_id)
            if return_code != 0 and stored["processing_status"] in {"inspecting", "parsing"}:
                self.material_store.fail_revision_processing(
                    revision_id=revision_id,
                    worker_id=f"{self.worker_id}-material",
                    failure_category=str(failure_category),
                    detail=f"exit_{return_code}",
                )
                stored = self.material_store.get_revision(revision_id)
            return stored
        return None

    def stop(self, grace_seconds: float) -> None:
        self._running = False
        deadline = time.monotonic() + max(0.0, grace_seconds)
        for process in list(self._processes.values()):
            if process.poll() is None:
                process.terminate()
        for process in list(self._processes.values()):
            if process.poll() is not None:
                continue
            remaining = max(0.0, deadline - time.monotonic())
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                process.kill()

    def cancel(self, job_id: str) -> dict[str, Any]:
        row = self.store.request_cancel(job_id)
        if row["status"] == "cancelled":
            return row
        process = self._processes.get(job_id)
        if process is not None and process.poll() is None:
            process.terminate()
        return finalize_cancelled_job(
            self.store,
            job_id,
            data_root=Path(self.data_root),
        )

    def recover(self, *, now: str) -> dict[str, Any]:
        rows = self.store.recover_expired(now)
        return {"recovered_job_ids": [row["job_id"] for row in rows]}


def finalize_cancelled_job(
    store: JobStore,
    job_id: str,
    *,
    data_root: Path,
) -> dict[str, Any]:
    row = store.get(job_id)
    if row["status"] == "cancelled":
        return row
    if row["status"] != "cancelling":
        row = store.request_cancel(job_id)
    value = {
        "contract": "lca_research_service.control_receipt/v1",
        "job_id": job_id,
        "action": "cancelled",
        "external_effects": [],
    }
    payload = (
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    digest = hashlib.sha256(payload).hexdigest()
    relative = Path("jobs") / job_id / "control" / f"{digest}.json"
    root = Path(data_root).resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise ValueError("cancel receipt escaped data root")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(payload)
        temporary.replace(target)
    elif hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise ValueError("cancel receipt drifted")
    cancel_ref = {
        "path": relative.as_posix(),
        "sha256": digest,
        "role": "control_receipt",
    }
    return store.transition(job_id, "cancelling", "cancelled", cancel_ref)
