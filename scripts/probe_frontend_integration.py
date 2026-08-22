#!/usr/bin/env python3
"""Run one sanitized, no-effect request through the built frontend BFF."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


CONTRACT = "lca_research_agent.frontend_live_probe/v1"
LOCAL_TOKEN = "integration-token"
SAFE_ENVIRONMENT_NAMES = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TZ")


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def _request(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 3.0,
) -> tuple[int, bytes, dict[str, str]]:
    payload = None if body is None else _canonical(body)
    request = urllib.request.Request(url, data=payload, method=method)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return (
                response.status,
                response.read(),
                {key.casefold(): value for key, value in response.headers.items()},
            )
    except urllib.error.HTTPError as error:
        return (
            error.code,
            error.read(),
            {key.casefold(): value for key, value in error.headers.items()},
        )


def _wait(url: str, deadline: float) -> None:
    while time.monotonic() < deadline:
        try:
            status, _, _ = _request(url, timeout=1.0)
            if status < 500:
                return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    raise RuntimeError("local service did not become ready")


def _terminate(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def _event_sequences(payload: str) -> list[int]:
    result: list[int] = []
    for line in payload.splitlines():
        if line.startswith("id: "):
            value = line[4:].strip()
            if value.isdigit():
                result.append(int(value))
    return result


def _safe_environment(root: Path) -> dict[str, str]:
    environment = {
        name: os.environ[name]
        for name in SAFE_ENVIRONMENT_NAMES
        if name in os.environ
    }
    environment["PYTHONPATH"] = str(root)
    return environment


def _frontend_environment(
    environment: dict[str, str], *, backend_url: str
) -> dict[str, str]:
    """Bind the no-effect probe to the mock server's explicit auth contract."""

    frontend_environment = dict(environment)
    frontend_environment["D18_AUTH_MODE"] = "local_static"
    frontend_environment["D18_BASE_URL"] = backend_url
    frontend_environment["D18_TOKEN"] = LOCAL_TOKEN
    return frontend_environment


def _project_record_ref_for_receipt(
    *, data_root: Path, state_ref: object
) -> dict[str, str]:
    """Verify the private mock projection named by the public job status."""

    if not isinstance(state_ref, dict):
        raise RuntimeError("no-effect job status omitted its Project Record state ref")
    relative = state_ref.get("path")
    expected_sha256 = state_ref.get("sha256")
    if (
        not isinstance(relative, str)
        or not relative
        or not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or state_ref.get("role") != "project_record_projection"
    ):
        raise RuntimeError("no-effect Project Record state ref is invalid")
    root = Path(data_root).resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise RuntimeError("no-effect Project Record escaped its data root") from error
    if not path.is_file():
        raise RuntimeError("no-effect Project Record projection is unavailable")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise RuntimeError("no-effect Project Record projection drifted")
    return {
        "path": relative,
        "sha256": digest,
        "role": "project_record_projection",
    }


def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.root).resolve()
    frontend_root = root / "apps/web"
    data_root = Path(args.data_root).resolve()
    output = Path(args.output).resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    backend_url = f"http://127.0.0.1:{args.backend_port}"
    frontend_url = f"http://127.0.0.1:{args.frontend_port}"
    environment = _safe_environment(root)
    backend = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "lca_research_service.mock_server",
            "--data-root",
            str(data_root),
            "--host",
            "127.0.0.1",
            "--port",
            str(args.backend_port),
        ],
        cwd=root,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    frontend_environment = _frontend_environment(
        environment, backend_url=backend_url
    )
    frontend = subprocess.Popen(
        [
            "pnpm",
            "start",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(args.frontend_port),
        ],
        cwd=frontend_root,
        env=frontend_environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    processes = [backend, frontend]
    try:
        _wait(f"{backend_url}/health/live", time.monotonic() + 15)
        _wait(frontend_url, time.monotonic() + 20)
        material_text = "固定的用户材料；仅验证接线，不含科学结论。"
        material_sha = hashlib.sha256(material_text.encode()).hexdigest()
        research_id = "frontend-no-effect-probe"
        request_body = {
            "research_id": research_id,
            "messages": [{"role": "user", "content": "执行固定的无效果接线探针。"}],
            "source_materials": [
                {
                    "name": "probe.txt",
                    "media_type": "text/plain",
                    "sha256": material_sha,
                    "authority": "user_supplied_project_input",
                    "content": {"text": material_text},
                }
            ],
        }
        created_status, created_payload, _ = _request(
            f"{frontend_url}/api/d18/jobs",
            method="POST",
            body=request_body,
            headers={"Idempotency-Key": "frontend-no-effect-probe-v1"},
        )
        if created_status != 202:
            raise RuntimeError("frontend BFF did not create the no-effect job")
        created = json.loads(created_payload)
        job_id = created["job_id"]
        status_payload: dict[str, Any] = {}
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job_status, raw, _ = _request(
                f"{frontend_url}/api/d18/jobs/{job_id}"
            )
            if job_status != 200:
                raise RuntimeError("frontend BFF job status failed")
            status_payload = json.loads(raw)
            if status_payload.get("status") in {
                "completed",
                "bounded",
                "failed",
                "cancelled",
            }:
                break
            time.sleep(0.05)
        if status_payload.get("status") != "completed":
            raise RuntimeError("no-effect job did not complete")
        event_status, event_payload, event_headers = _request(
            f"{frontend_url}/api/d18/jobs/{job_id}/events"
        )
        if event_status != 200 or "text/event-stream" not in event_headers.get(
            "content-type", ""
        ):
            raise RuntimeError("frontend BFF event stream failed")
        event_text = event_payload.decode()
        sequences = _event_sequences(event_text)
        if not sequences:
            raise RuntimeError("frontend BFF event stream had no sequence")
        cursor = max(sequences)
        resumed_status, resumed_payload, _ = _request(
            f"{frontend_url}/api/d18/jobs/{job_id}/events",
            headers={"Last-Event-ID": str(cursor)},
        )
        resumed_text = resumed_payload.decode()
        if resumed_status != 200 or f"id: {cursor}\n" in resumed_text:
            raise RuntimeError("frontend BFF event cursor did not resume")
        result_status, result_payload, _ = _request(
            f"{frontend_url}/api/d18/jobs/{job_id}/result"
        )
        result = json.loads(result_payload)
        if result_status != 200 or result.get("status") != "accepted":
            raise RuntimeError("frontend BFF did not return an accepted projection")
        material_path = data_root / "projects" / research_id / "materials" / material_sha
        if not material_path.is_file() or material_path.read_text() != material_text:
            raise RuntimeError("user material was not frozen byte for byte")
        effects_status, effects_payload, _ = _request(
            f"{backend_url}/__dev/no-effect-receipt",
            headers={"Authorization": f"Bearer {LOCAL_TOKEN}"},
        )
        if effects_status != 200:
            raise RuntimeError("mock backend did not expose observed no-effect counts")
        effects_observation = json.loads(effects_payload)
        effects = effects_observation.get("effects")
        if effects != {
            "model_calls": 0,
            "tool_calls": 0,
            "external_writes": 0,
            "scientific_results": 0,
        }:
            raise RuntimeError("mock backend observed an unexpected effect")
        project_record_ref = _project_record_ref_for_receipt(
            data_root=data_root,
            state_ref=status_payload.get("state_ref"),
        )
        receipt = {
            "contract": CONTRACT,
            "status": "passed",
            "through_frontend_bff": True,
            "request_sha256": hashlib.sha256(_canonical(request_body)).hexdigest(),
            "job_id": job_id,
            "job_status": status_payload["status"],
            "result_status": result["status"],
            "project_record_ref_sha256": project_record_ref["sha256"],
            "material_sha256": material_sha,
            "event_count": len(sequences),
            "last_event_sequence": cursor,
            "sse_cursor_resumed": True,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "effects_observation_contract": effects_observation["contract"],
            "effects": effects,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(_canonical(receipt))
        return receipt
    finally:
        _terminate(processes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend-port", type=int, default=18080)
    parser.add_argument("--frontend-port", type=int, default=13000)
    args = parser.parse_args(argv)
    receipt = run_probe(args)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
