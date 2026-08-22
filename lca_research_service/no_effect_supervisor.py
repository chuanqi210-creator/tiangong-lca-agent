"""In-process, no-effect worker loop for frontend development only."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .job_store import JobStore
from .material_store import MaterialStore
from .supervisor import finalize_cancelled_job
from .supervisor import WorkerSupervisor
from .worker import run_product_job


class NoEffectWorkerSupervisor:
    """Drive the real async job state machine with an injected no-effect runtime."""

    def __init__(
        self,
        store: JobStore,
        *,
        runtime: object,
        data_root: str | Path,
        material_store: MaterialStore,
        worker_id: str = "d18-no-effect-dev-worker",
        lease_seconds: int = 60,
    ) -> None:
        self.store = store
        self.runtime = runtime
        self.data_root = Path(data_root).resolve()
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self._material_lane = WorkerSupervisor(
            store,
            control_db=store.path,
            data_root=self.data_root,
            worker_id=f"{worker_id}-sandbox",
            lease_seconds=lease_seconds,
            material_store=material_store,
            material_control_db=material_store.path,
        )
        self._running = False
        self._material_failure: BaseException | None = None

    def start(self) -> None:
        self._running = True

    def serve(
        self,
        stop_event: threading.Event,
        *,
        poll_interval: float = 0.02,
    ) -> None:
        self.start()
        self.recover(now=datetime.now(timezone.utc).isoformat())
        while not stop_event.is_set():
            try:
                self.run_once()
            except Exception:
                if getattr(self, "_material_failure", None) is not None:
                    self._running = False
                    return
                # run_product_job has already materialized a sanitized failure
                # receipt when possible; one bad fixture must not stop the loop.
                pass
            stop_event.wait(max(0.01, poll_interval))

    def run_once(self) -> dict[str, Any] | None:
        try:
            material = self._material_lane.run_material_lane_once()
        except BaseException as error:
            self._material_failure = error
            raise
        if material is not None:
            return material
        claimed = self.store.claim(self.worker_id, self.lease_seconds)
        if claimed is None:
            return None
        return run_product_job(
            self.store,
            claimed["job_id"],
            worker_id=self.worker_id,
            runtime=self.runtime,
            data_root=self.data_root,
        )

    def probe(self) -> None:
        """Fail readiness when the sandboxed material lane has stopped."""

        if self._material_failure is not None:
            raise RuntimeError("material worker lane is unavailable") from self._material_failure

    def stop(self, grace_seconds: float) -> None:
        self._material_lane.stop(grace_seconds)
        self._running = False

    def cancel(self, job_id: str) -> dict[str, Any]:
        row = self.store.request_cancel(job_id)
        if row["status"] == "cancelled":
            return row
        return finalize_cancelled_job(
            self.store,
            job_id,
            data_root=self.data_root,
        )

    def recover(self, *, now: str) -> dict[str, Any]:
        rows = self.store.recover_expired(now)
        return {"recovered_job_ids": [row["job_id"] for row in rows]}
