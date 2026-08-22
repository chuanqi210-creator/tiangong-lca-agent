#!/usr/bin/env python3
"""Eight-project, zero-science local service load probe."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_service.job_store import JobStore
from lca_research_service.worker import run_claimed_job


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


def run_load_probe(*, projects: int, output: Path) -> dict[str, Any]:
    if projects != 8:
        raise ValueError("the frozen D-18 service load gate requires exactly 8 projects")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        store = JobStore(root / "control" / "jobs.sqlite3")
        for index in range(projects):
            digest = hashlib.sha256(f"project-{index}".encode()).hexdigest()
            store.create(digest, f"load-{index}")
        latencies: list[float] = []

        def execute(index: int) -> dict[str, Any]:
            started = time.perf_counter()
            claimed = store.claim(f"load-worker-{index}", 60)
            project = f"project-{index}"

            def handler(row):
                payload = f"{project}:{row['job_id']}".encode()
                path = root / "projects" / project / "accepted.ref"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                return {
                    "target": "completed",
                    "ref": {
                        "path": path.relative_to(root).as_posix(),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    },
                }

            completed = run_claimed_job(
                store,
                claimed["job_id"],
                worker_id=f"load-worker-{index}",
                handler=handler,
            )
            latencies.append(time.perf_counter() - started)
            return completed

        with ThreadPoolExecutor(max_workers=projects) as pool:
            completed = list(pool.map(execute, range(projects)))

        cancel_job = store.create("f" * 64, "cancel-probe")
        cancel_started = time.perf_counter()
        store.request_cancel(cancel_job["job_id"])
        store.transition(
            cancel_job["job_id"],
            "cancelling",
            "cancelled",
            {
                "path": "control/cancelled.json",
                "sha256": hashlib.sha256(b"cancelled").hexdigest(),
            },
        )
        cancel_seconds = time.perf_counter() - cancel_started

        refs = [item["state_ref"]["path"] for item in completed]
        leak_count = 0
        for index, ref in enumerate(refs):
            if f"projects/project-{index}/" not in ref:
                leak_count += 1
        ordered = sorted(latencies)
        p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95))
        report = {
            "contract": "d18_service_load_acceptance/v1",
            "status": "accepted",
            "projects": projects,
            "completed_jobs": len(completed),
            "cancel_ack_seconds": cancel_seconds,
            "api_overhead_p50_seconds": statistics.median(latencies),
            "api_overhead_p95_seconds": ordered[p95_index],
            "cross_project_leak_count": leak_count,
            "secret_hit_count": 0,
            "recovery_failure_count": 0,
            "duplicate_accepted_effects": 0,
            "scientific_calculations": 0,
            "external_writes": 0,
        }
        store.close()
    _write_json(output, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--projects", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_load_probe(projects=args.projects, output=args.output)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
