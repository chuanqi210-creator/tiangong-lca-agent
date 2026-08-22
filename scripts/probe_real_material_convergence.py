#!/usr/bin/env python3
"""Audit one real uploaded material through the production frontend/Worker stack."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from probe_frontend_real_pilot import (
    _json_request,
    _opener,
    _write_json,
    derive_effect_evidence,
)


CONTRACT = "lca_research_agent.real_material_convergence_receipt/v1"
TERMINAL = {"completed", "bounded", "failed", "cancelled"}
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _event_rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _trace_rows(project_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((project_root / "run-traces").glob("*.jsonl")):
        rows.extend(_event_rows(path))
    return rows


def _event_snapshot(project_root: Path) -> dict[str, Any]:
    rows = _event_rows(project_root / "research-project-events.jsonl")
    counts: dict[str, int] = {}
    for row in rows:
        event_type = str(row["event_type"])
        counts[event_type] = counts.get(event_type, 0) + 1
    path = project_root / "research-project-events.jsonl"
    return {
        "event_count": len(rows),
        "event_type_counts": counts,
        "sha256": _sha256(path.read_bytes()),
    }


def _trace_snapshot(project_root: Path) -> dict[str, Any]:
    rows = _trace_rows(project_root)
    counts: dict[str, int] = {}
    for row in rows:
        event_type = str(row["event_type"])
        counts[event_type] = counts.get(event_type, 0) + 1
    paths = sorted((project_root / "run-traces").glob("*.jsonl"))
    return {
        "event_count": len(rows),
        "event_type_counts": counts,
        "trace_sha256s": [_sha256(path.read_bytes()) for path in paths],
    }


def _sample_worker_processes() -> tuple[float, int, bool]:
    output = subprocess.run(
        ["ps", "-axo", "pid,ppid,%cpu,rss,command"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    maximum_cpu = 0.0
    maximum_rss_kb = 0
    observed = False
    for line in output.splitlines()[1:]:
        fields = line.strip().split(None, 4)
        if len(fields) != 5 or "lca_research_service.worker" not in fields[4]:
            continue
        observed = True
        maximum_cpu = max(maximum_cpu, float(fields[2]))
        maximum_rss_kb = max(maximum_rss_kb, int(fields[3]))
    return maximum_cpu, maximum_rss_kb, observed


def _find_ready_material(
    opener: urllib.request.OpenerDirector,
    frontend_url: str,
    research_id: str,
    source_sha256: str,
) -> dict[str, Any] | None:
    status, library = _json_request(
        opener,
        f"{frontend_url}/api/d18/research/{research_id}/materials",
    )
    if status != 200:
        raise RuntimeError("frontend could not list project materials")
    matches = [
        row
        for row in library.get("materials", [])
        if isinstance(row, Mapping)
        and isinstance(row.get("current_revision"), Mapping)
        and row["current_revision"].get("input_artifact_id") == source_sha256
    ]
    if len(matches) > 1:
        raise RuntimeError("source artifact has ambiguous project materials")
    return dict(matches[0]) if matches else None


def _upload_material(
    opener: urllib.request.OpenerDirector,
    frontend_url: str,
    research_id: str,
    source: Path,
    payload: bytes,
    source_sha256: str,
) -> dict[str, Any]:
    status, created = _json_request(
        opener,
        f"{frontend_url}/api/d18/research/{research_id}/material-uploads",
        method="POST",
        body={
            "uploads": [
                {
                    "filename": source.name,
                    "declared_mime": "application/json",
                    "total_bytes": len(payload),
                    "expected_sha256": source_sha256,
                }
            ]
        },
        headers={"Idempotency-Key": f"material-{research_id}-{source_sha256}"},
    )
    if status != 201:
        raise RuntimeError("frontend did not create the material upload")
    upload = created["uploads"][0]
    upload_id = str(upload["upload_id"])
    part_size = int(upload["part_size"])
    for part_number, first_byte in enumerate(range(0, len(payload), part_size)):
        part = payload[first_byte : first_byte + part_size]
        last_byte = first_byte + len(part) - 1
        request = urllib.request.Request(
            f"{frontend_url}/api/d18/material-uploads/{upload_id}/parts/{part_number}",
            data=part,
            method="PUT",
        )
        for key, value in {
            "Content-Range": f"bytes {first_byte}-{last_byte}/{len(payload)}",
            "Content-Type": "application/octet-stream",
            "X-D18-Project-ID": research_id,
            "X-Part-SHA256": _sha256(part),
        }.items():
            request.add_header(key, value)
        with opener.open(request, timeout=30) as response:
            if response.status != 200:
                raise RuntimeError("frontend material part upload failed")
    status, _completed = _json_request(
        opener,
        f"{frontend_url}/api/d18/material-uploads/{upload_id}/complete",
        method="POST",
        body={"scope": "project"},
        headers={"X-D18-Project-ID": research_id},
    )
    if status != 200:
        raise RuntimeError("frontend did not complete the material upload")
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        material = _find_ready_material(
            opener, frontend_url, research_id, source_sha256
        )
        processing_status = (
            material.get("current_revision", {}).get("processing_status")
            if material is not None
            else None
        )
        if processing_status == "ready":
            return material
        if processing_status in {"failed", "rejected", "partial"}:
            raise RuntimeError(f"material processing stopped as {processing_status}")
        time.sleep(0.1)
    raise RuntimeError("material processing did not converge")


def _job_row(data_root: Path, job_id: str) -> dict[str, Any]:
    with sqlite3.connect(data_root / "control/jobs.sqlite3") as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
    if row is None:
        raise RuntimeError("job control row is unavailable")
    return dict(row)


def _material_processing_ms(data_root: Path, research_id: str) -> int | None:
    with sqlite3.connect(data_root / "control/materials.sqlite3") as connection:
        connection.row_factory = sqlite3.Row
        rows = list(
            connection.execute(
                "SELECT event_type, occurred_at FROM material_events "
                "WHERE project_id = ? ORDER BY sequence",
                (research_id,),
            )
        )
    completed_at = next(
        (
            row["occurred_at"]
            for row in rows
            if row["event_type"] == "research.material.upload_completed"
        ),
        None,
    )
    ready_at = next(
        (
            row["occurred_at"]
            for row in rows
            if row["event_type"] == "research.material.processing_ready"
        ),
        None,
    )
    if not isinstance(completed_at, str) or not isinstance(ready_at, str):
        return None
    return round((_iso(ready_at) - _iso(completed_at)).total_seconds() * 1000)


def run_probe(
    *,
    frontend_url: str,
    data_root: Path,
    source: Path,
    research_id: str,
    objective: str,
    output: Path,
    job_idempotency_key: str,
    expected_provider: str,
    expected_min_locators: int,
    maximum_first_model_ms: int,
    maximum_result_ms: int,
) -> dict[str, Any]:
    frontend_url = frontend_url.rstrip("/")
    data_root = data_root.resolve()
    source = source.resolve()
    payload = source.read_bytes()
    source_sha256 = _sha256(payload)
    opener = _opener()
    status, session = _json_request(opener, f"{frontend_url}/api/d18/session")
    if status != 200 or session.get("authenticated") is not True:
        raise RuntimeError("frontend signed session is unavailable")
    namespace = session.get("project_namespace")
    if not isinstance(namespace, str) or not research_id.startswith(namespace + "-"):
        raise RuntimeError("research identity is outside the signed namespace")

    material = _find_ready_material(
        opener, frontend_url, research_id, source_sha256
    )
    material_reused = material is not None
    if material is None:
        material = _upload_material(
            opener,
            frontend_url,
            research_id,
            source,
            payload,
            source_sha256,
        )
    current_revision = material["current_revision"]
    if current_revision.get("processing_status") != "ready":
        raise RuntimeError("material is not ready for model consumption")

    job_body = {
        "research_id": research_id,
        "messages": [{"role": "user", "content": objective}],
        "material_ids": [material["material_id"]],
    }
    started = time.monotonic()
    status, job = _json_request(
        opener,
        f"{frontend_url}/api/d18/jobs",
        method="POST",
        body=job_body,
        headers={"Idempotency-Key": job_idempotency_key},
    )
    if status != 202 or not isinstance(job.get("job_id"), str):
        raise RuntimeError("frontend did not accept the research job")
    job_id = job["job_id"]
    job_reused = job.get("status") in TERMINAL
    status_changes: list[dict[str, object]] = []
    maximum_cpu = 0.0
    maximum_rss_kb = 0
    worker_observed = False
    deadline = time.monotonic() + maximum_result_ms / 1000 + 30
    view: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status, view = _json_request(
            opener, f"{frontend_url}/api/d18/jobs/{job_id}"
        )
        if status != 200:
            raise RuntimeError("frontend could not read research status")
        current_status = view.get("status")
        if not status_changes or status_changes[-1]["status"] != current_status:
            status_changes.append(
                {
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "status": current_status,
                }
            )
        cpu, rss_kb, observed = _sample_worker_processes()
        maximum_cpu = max(maximum_cpu, cpu)
        maximum_rss_kb = max(maximum_rss_kb, rss_kb)
        worker_observed = worker_observed or observed
        if current_status in TERMINAL:
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("research job exceeded the bounded result deadline")
    if view.get("status") not in {"completed", "bounded"}:
        raise RuntimeError(f"research job ended as {view.get('status')}")
    observed_terminal_ms = round((time.monotonic() - started) * 1000)

    status, public_result = _json_request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/result"
    )
    if status != 200 or public_result.get("status") != "accepted":
        raise RuntimeError("frontend did not return an accepted public result")
    public_projection = public_result.get("result")
    if not isinstance(public_projection, Mapping):
        raise RuntimeError("public result projection is absent")

    project_root = data_root / "projects" / research_id
    trace_rows = _trace_rows(project_root)
    requested = [
        row for row in trace_rows if row["event_type"] == "research.run.model.requested"
    ]
    completed = [
        row for row in trace_rows if row["event_type"] == "research.run.model.completed"
    ]
    accepted_events = [
        row
        for row in trace_rows
        if row["event_type"] == "research.run.answer.formal_accepted"
    ]
    if len(requested) != 1 or len(completed) != 1 or len(accepted_events) != 1:
        raise RuntimeError("real material run did not use one bounded model attempt")
    job_control = _job_row(data_root, job_id)
    control_terminal_ms = round(
        (
            _iso(job_control["updated_at"])
            - _iso(job_control["created_at"])
        ).total_seconds()
        * 1000
    )
    first_model_request_ms = round(
        (_iso(requested[0]["occurred_at"]) - _iso(job_control["created_at"]))
        .total_seconds()
        * 1000
    )
    accepted_result_ms = round(
        (
            _iso(accepted_events[0]["occurred_at"])
            - _iso(job_control["created_at"])
        ).total_seconds()
        * 1000
    )
    model_payload = completed[0]["payload"]
    if model_payload.get("provider") != expected_provider:
        raise RuntimeError("real material run used an unexpected provider")
    if first_model_request_ms > maximum_first_model_ms:
        raise RuntimeError("real material run reached the model too slowly")
    if accepted_result_ms > maximum_result_ms:
        raise RuntimeError("real material run accepted its result too slowly")

    project_view = json.loads(
        (project_root / "project-record.json").read_text(encoding="utf-8")
    )
    accepted_result = project_view.get("result")
    if not isinstance(accepted_result, Mapping) or accepted_result.get(
        "status"
    ) != "formal_result_accepted":
        raise RuntimeError("Project Record has no accepted formal result")
    locator_count = len(project_view.get("evidence_locators") or {})
    if locator_count < expected_min_locators:
        raise RuntimeError("material parser produced too few evidence locators")
    limitations = public_projection.get("limitations")
    if not isinstance(limitations, list) or not limitations:
        raise RuntimeError("public result omitted the representative evidence limit")
    if not isinstance(public_projection.get("bounded_conclusion"), str):
        raise RuntimeError("public result omitted the human-readable conclusion")

    trace_before = _trace_snapshot(project_root)
    events_before = _event_snapshot(project_root)
    status, replay = _json_request(
        opener,
        f"{frontend_url}/api/d18/jobs",
        method="POST",
        body=job_body,
        headers={"Idempotency-Key": job_idempotency_key},
    )
    if status != 202 or replay.get("job_id") != job_id:
        raise RuntimeError("exact retry did not reuse the accepted job")
    time.sleep(0.5)
    trace_after = _trace_snapshot(project_root)
    events_after = _event_snapshot(project_root)
    if trace_before != trace_after or events_before != events_after:
        raise RuntimeError("exact retry repeated accepted work or effects")

    git_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    git_diff = subprocess.run(
        ["git", "diff", "--binary"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    ).stdout
    effect_evidence = derive_effect_evidence(
        project_root / "project-record.json", project_root / "run-traces"
    )
    receipt: dict[str, Any] = {
        "contract": CONTRACT,
        "status": "accepted",
        "code_basis": {
            "git_head": git_head,
            "working_diff_sha256": _sha256(git_diff),
        },
        "source": {
            "path": source.as_posix(),
            "bytes": len(payload),
            "sha256": source_sha256,
        },
        "material": {
            "material_id": material["material_id"],
            "revision_id": current_revision["revision_id"],
            "input_artifact_id": current_revision["input_artifact_id"],
            "locator_count": locator_count,
            "reused_existing_material": material_reused,
            "upload_to_ready_ms": _material_processing_ms(
                data_root, research_id
            ),
        },
        "job": {
            "research_id": research_id,
            "job_id": job_id,
            "reused_existing_job": job_reused,
            "status_changes": status_changes,
            "observed_terminal_ms": observed_terminal_ms,
            "control_terminal_ms": control_terminal_ms,
            "first_model_request_ms": first_model_request_ms,
            "accepted_result_ms": accepted_result_ms,
        },
        "runtime": {
            "provider": model_payload.get("provider"),
            "model": model_payload.get("model"),
            "model_call_count": 1,
            "tool_call_count": trace_before["event_type_counts"].get(
                "research.run.tool.completed", 0
            ),
            "provider_duration_ms": model_payload.get("duration_ms"),
            "usage": model_payload.get("usage"),
            "worker_process_observed": worker_observed,
            "worker_sample_status": (
                "sampled_during_production_worker_run"
                if worker_observed
                else "not_observed_on_idempotent_replay"
            ),
            "maximum_sampled_worker_cpu_percent": maximum_cpu,
            "maximum_sampled_worker_rss_kb": maximum_rss_kb,
        },
        "accepted_result": {
            "result_sha256": _sha256(
                json.dumps(
                    accepted_result,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ),
            "claim_ceiling": accepted_result.get("claim_ceiling"),
            "citation_count": len(public_projection.get("citations") or []),
            "limitations": limitations,
            "bounded_conclusion": public_projection["bounded_conclusion"],
        },
        "project_record": events_before,
        "run_trace": trace_before,
        "exact_retry": {
            "same_job_id": True,
            "run_trace_event_delta": 0,
            "project_record_event_delta": 0,
            "model_call_delta": 0,
            "tool_call_delta": 0,
        },
        "effect_evidence": effect_evidence,
    }
    receipt["receipt_sha256"] = _sha256(
        json.dumps(
            receipt,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    _write_json(output, receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend-url", default="http://127.0.0.1:3000")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--research-id", required=True)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--job-idempotency-key", required=True)
    parser.add_argument("--expected-provider", default="gemini")
    parser.add_argument("--expected-min-locators", type=int, default=1)
    parser.add_argument("--maximum-first-model-ms", type=int, default=20_000)
    parser.add_argument("--maximum-result-ms", type=int, default=90_000)
    args = parser.parse_args(argv)
    receipt = run_probe(
        frontend_url=args.frontend_url,
        data_root=args.data_root,
        source=args.source,
        research_id=args.research_id,
        objective=args.objective,
        output=args.output,
        job_idempotency_key=args.job_idempotency_key,
        expected_provider=args.expected_provider,
        expected_min_locators=args.expected_min_locators,
        maximum_first_model_ms=args.maximum_first_model_ms,
        maximum_result_ms=args.maximum_result_ms,
    )
    print(
        json.dumps(
            {
                "contract": receipt["contract"],
                "status": receipt["status"],
                "job_id": receipt["job"]["job_id"],
                "first_model_request_ms": receipt["job"][
                    "first_model_request_ms"
                ],
                "accepted_result_ms": receipt["job"]["accepted_result_ms"],
                "receipt_sha256": receipt["receipt_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
