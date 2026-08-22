"""Deterministic no-effect integration artifacts and protocol probe."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from .mock_server import create_mock_app
from .worker import run_product_job

BUNDLE_CONTRACT = "lca_research_service.integration_bundle/v1"
PROBE_CONTRACT = "lca_research_service.protocol_probe/v1"
_AUTH = {"Authorization": "Bearer integration-token"}


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def _write_json(path: Path, value: Any) -> str:
    payload = _canonical(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _create_and_run(client: TestClient, app: Any, research_id: str) -> dict[str, Any]:
    created = client.post(
        "/v1/jobs",
        headers={**_AUTH, "Idempotency-Key": f"fixture-{research_id}"},
        json={
            "research_id": research_id,
            "messages": [{"role": "user", "content": "固定的无效果协议探针。"}],
        },
    )
    if created.status_code != 202:
        raise RuntimeError(created.text)
    claimed = app.state.store.claim("fixture-worker", 60)
    if claimed is None:
        raise RuntimeError("fixture job was not claimable")
    return run_product_job(
        app.state.store,
        claimed["job_id"],
        worker_id="fixture-worker",
        runtime=app.state.runtime,
        data_root=app.state.config.data_root,
    )


def _stable_job(row: dict[str, Any]) -> dict[str, Any]:
    def public_ref(value: Any) -> dict[str, str] | None:
        if not isinstance(value, dict) or not isinstance(value.get("sha256"), str):
            return None
        return {"sha256": value["sha256"]}

    return {
        "job_id": row["job_id"],
        "status": row["status"],
        "request_ref": public_ref(row["request_ref"]),
        "input_ref": public_ref(row["input_ref"]),
        "state_ref": public_ref(row["state_ref"]),
        "recovery_count": row["recovery_count"],
    }


def build_integration_bundle(output_root: Path) -> dict[str, Any]:
    output_root = Path(output_root)
    modes = {
        "fixture-success": "complete",
        "fixture-waiting": "waiting",
        "fixture-bounded": "bounded",
        "fixture-failed": "failed",
        "fixture-resumed": "waiting",
    }
    artifacts: dict[str, str] = {}
    fixtures: dict[str, str] = {}
    temporary = tempfile.TemporaryDirectory()
    app = create_mock_app(Path(temporary.name), modes=modes)
    with temporary, TestClient(app) as client:
        artifacts["openapi-v1.json"] = _write_json(
            output_root / "openapi-v1.json", app.openapi()
        )
        sync = client.post(
            "/v1/chat/completions",
            headers=_AUTH,
            json={
                "research_id": "fixture-sync",
                "messages": [{"role": "user", "content": "固定同步协议探针。"}],
            },
        )
        if sync.status_code != 200:
            raise RuntimeError(sync.text)
        sync_body = sync.json()
        sync_body["id"] = "chatcmpl-deterministic-fixture"
        success = {
            "contract": "lca_research_service.fixture/v1",
            "scenario": "success",
            "http_status": sync.status_code,
            "body": sync_body,
        }
        fixtures["success"] = "fixtures/success.json"
        artifacts[fixtures["success"]] = _write_json(
            output_root / fixtures["success"], success
        )

        rows: dict[str, dict[str, Any]] = {}
        for scenario in ("waiting", "bounded", "failed"):
            rows[scenario] = _create_and_run(
                client, app, f"fixture-{scenario}"
            )
            fixture = {
                "contract": "lca_research_service.fixture/v1",
                "scenario": scenario,
                "job": _stable_job(rows[scenario]),
            }
            fixtures[scenario] = f"fixtures/{scenario}.json"
            artifacts[fixtures[scenario]] = _write_json(
                output_root / fixtures[scenario], fixture
            )

        cancelled_created = client.post(
            "/v1/jobs",
            headers={**_AUTH, "Idempotency-Key": "fixture-cancelled"},
            json={
                "research_id": "fixture-cancelled",
                "messages": [{"role": "user", "content": "固定取消协议探针。"}],
            },
        ).json()
        cancelled = client.post(
            f"/v1/jobs/{cancelled_created['job_id']}/cancel", headers=_AUTH
        ).json()
        if cancelled["status"] != "cancelled":
            cancelled = app.state.store.transition(
                cancelled["job_id"], "cancelling", "cancelled", None
            )
        fixtures["cancelled"] = "fixtures/cancelled.json"
        artifacts[fixtures["cancelled"]] = _write_json(
            output_root / fixtures["cancelled"],
            {
                "contract": "lca_research_service.fixture/v1",
                "scenario": "cancelled",
                "job": _stable_job(cancelled),
            },
        )

        waiting = rows["waiting"]
        supplied = client.post(
            f"/v1/jobs/{waiting['job_id']}/inputs",
            headers=_AUTH,
            json={
                "input_id": "fixture-input",
                "kind": "decision",
                "payload": {
                    "decision_id": "fixture-input-unavailable",
                    "decision": "unavailable",
                },
            },
        )
        if supplied.status_code != 202:
            raise RuntimeError(supplied.text)
        claimed = app.state.store.claim("fixture-resume-worker", 60)
        if claimed is None:
            raise RuntimeError("resumed job was not claimable")
        resumed = run_product_job(
            app.state.store,
            claimed["job_id"],
            worker_id="fixture-resume-worker",
            runtime=app.state.runtime,
            data_root=app.state.config.data_root,
        )
        fixtures["resumed"] = "fixtures/resumed.json"
        artifacts[fixtures["resumed"]] = _write_json(
            output_root / fixtures["resumed"],
            {
                "contract": "lca_research_service.fixture/v1",
                "scenario": "resumed",
                "job": _stable_job(resumed),
            },
        )

    manifest = {
        "contract": BUNDLE_CONTRACT,
        "protocol_version": "v1",
        "mock_contract": app.state.mock_contract,
        "fixtures": fixtures,
        "artifacts": artifacts,
        "effects": dict(app.state.mock_interface.effects),
        "claims": {
            "production_runtime": False,
            "scientific_result": False,
            "frontend_compatible_fixture": True,
        },
    }
    _write_json(output_root / "manifest.json", manifest)
    return manifest


def probe_mock_protocol(data_root: Path) -> dict[str, Any]:
    app = create_mock_app(
        Path(data_root),
        modes={"probe-waiting": "waiting"},
    )
    passed: list[str] = []
    failed: list[str] = []

    def check(label: str, condition: bool) -> None:
        (passed if condition else failed).append(label)

    with TestClient(app) as client:
        check("auth", client.get("/v1/models").status_code == 401)
        check("models", client.get("/v1/models", headers=_AUTH).status_code == 200)
        check("liveness", client.get("/health/live").status_code == 200)
        check("readiness", client.get("/health/ready").status_code == 200)
        sync = client.post(
            "/v1/chat/completions",
            headers=_AUTH,
            json={
                "research_id": "probe-sync",
                "messages": [{"role": "user", "content": "固定协议探针。"}],
            },
        )
        check("sync", sync.status_code == 200 and "x_d18" in sync.json())

        waiting = _create_and_run(client, app, "probe-waiting")
        status = client.get(f"/v1/jobs/{waiting['job_id']}", headers=_AUTH)
        check(
            "async-waiting",
            status.status_code == 200
            and status.json().get("input_required", {}).get(
                "authorization_required"
            )
            is False,
        )
        events = client.get(
            f"/v1/jobs/{waiting['job_id']}/events", headers=_AUTH
        )
        cursor = max(
            row["sequence"] for row in app.state.store.events(waiting["job_id"])
        )
        resumed_events = client.get(
            f"/v1/jobs/{waiting['job_id']}/events",
            headers={**_AUTH, "Last-Event-ID": str(cursor)},
        )
        check("SSE", "event: job" in events.text and "event: heartbeat" in events.text)
        check(
            "Last-Event-ID",
            f"id: {cursor}\n" not in resumed_events.text
            and f'"cursor":{cursor}' in resumed_events.text,
        )

        supplied = client.post(
            f"/v1/jobs/{waiting['job_id']}/inputs",
            headers=_AUTH,
            json={
                "input_id": "probe-input",
                "kind": "decision",
                "payload": {
                    "decision_id": "probe-input-unavailable",
                    "decision": "unavailable",
                },
            },
        )
        claimed = app.state.store.claim("probe-resume-worker", 60)
        resumed = (
            run_product_job(
                app.state.store,
                claimed["job_id"],
                worker_id="probe-resume-worker",
                runtime=app.state.runtime,
                data_root=app.state.config.data_root,
            )
            if claimed
            else {}
        )
        result = client.get(
            f"/v1/jobs/{waiting['job_id']}/result", headers=_AUTH
        )
        check(
            "inputs-result",
            supplied.status_code == 202
            and resumed.get("status") == "completed"
            and result.status_code == 200,
        )

        cancelled_created = client.post(
            "/v1/jobs",
            headers={**_AUTH, "Idempotency-Key": "probe-cancel"},
            json={
                "research_id": "probe-cancel",
                "messages": [{"role": "user", "content": "取消探针。"}],
            },
        ).json()
        cancelled = client.post(
            f"/v1/jobs/{cancelled_created['job_id']}/cancel", headers=_AUTH
        )
        check("cancel", cancelled.status_code == 200)

        payload = b"registered mock artifact\n"
        artifact_path = Path(data_root) / "artifacts" / "probe.txt"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(payload)
        registration = app.state.artifacts.register(
            {
                "path": "artifacts/probe.txt",
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
            content_type="text/plain",
            accepted=True,
            file_name="probe.txt",
            file_type="text",
        )
        download = client.get(registration["fileUrl"], headers=_AUTH)
        check(
            "registered-file-download",
            download.status_code == 200 and download.content == payload,
        )

    return {
        "contract": PROBE_CONTRACT,
        "status": "passed" if not failed else "failed",
        "checks_passed": sorted(passed),
        "checks_failed": sorted(failed),
        "effects": dict(app.state.mock_interface.effects),
    }
