#!/usr/bin/env python3
"""Run D-18 Product Readiness journeys through public frontend/backend HTTP."""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_service.product_readiness_gate import (  # noqa: E402
    DIRECT_JOURNEY,
    MATERIAL_JOURNEY,
    MATERIAL_PROFILE_ID,
    MATERIAL_SOURCE_BYTES,
    MATERIAL_SOURCE_SHA256,
    MISSING_DATA_JOURNEY,
    NATIVE_JOURNEY,
    OFFICIAL_JOURNEY,
    canonical_sha256,
    evaluate_direct_journey,
    evaluate_material_journey,
    evaluate_missing_data_journey,
    evaluate_native_journey,
    evaluate_official_journey,
)


TERMINAL = {"completed", "bounded", "failed", "cancelled"}
DIRECT_OBJECTIVE = (
    "LCA 是什么？请用三句话解释它的用途、功能单位的作用，以及结果最重要的限制。"
)
MATERIAL_OBJECTIVE = (
    "请说明这个 TIDAS ZIP 材料包含什么，并解释材料中可以确认的生命周期模型、过程记录、功能单位、"
    "版本与限制。只能引用经过选择的材料证据，不要推断未展示的内容。"
)
OFFICIAL_OBJECTIVE = (
    "请只依据欧盟官方来源 Commission Implementing Regulation (EU) 2025/2547，"
    "说明 CBAM 嵌入排放核算中功能单位和生产过程系统边界的含义。请给出法规"
    "版本、访问时间、精确来源位置和证据边界；不要依据非官方网页，也不要推断"
    "法规未说明的内容。"
)
MISSING_DATA_OBJECTIVE = "请计算一件产品的碳足迹。"
NATIVE_OBJECTIVE = (
    "本任务不是 LCA 计算。请先通过当前已验证的 TianGong 原生只读能力实际读取 "
    "TIDAS SDK 合同，然后只报告准确包身份、版本，以及本轮未选择 LCA 方法、"
    "Provider 和单位、未生成科学计算结果。不能仅从上下文转述 SDK 信息。"
)
MATERIAL_DECLARED_MIME = "application/zip"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical(value) + b"\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("product readiness evidence must be a JSON object")
    return value


def _controlled_browser_view(capture: Mapping[str, Any]) -> dict[str, Any]:
    unsigned = {
        key: value for key, value in capture.items() if key != "capture_sha256"
    }
    browser = capture.get("browser_view")
    runtime = capture.get("browser_runtime")
    screenshot = capture.get("screenshot")
    authentication = capture.get("authentication")
    evidence = {
        key: value
        for key, value in capture.items()
        if key not in {"authentication", "capture_sha256"}
    }
    dom_snapshot = {
        "visible_text": browser.get("visible_text") if isinstance(browser, Mapping) else None,
        "links": browser.get("links", []) if isinstance(browser, Mapping) else [],
        "page_url": browser.get("page_url") if isinstance(browser, Mapping) else None,
        "title": runtime.get("title") if isinstance(runtime, Mapping) else None,
    }
    if (
        capture.get("contract") != "d18.controlled_browser_capture/v1"
        or capture.get("harness") != "ego-lite/cdp"
        or capture.get("capture_method") != "runtime_dom_and_png"
        or capture.get("capture_sha256") != canonical_sha256(unsigned)
        or not isinstance(authentication, Mapping)
        or authentication.get("contract")
        != "d18.browser_capture_authentication/v1"
        or authentication.get("algorithm") != "hmac-sha256"
        or not isinstance(authentication.get("gate_run_id"), str)
        or not authentication["gate_run_id"]
        or not isinstance(authentication.get("tag"), str)
        or authentication.get("evidence_sha256") != canonical_sha256(evidence)
        or not isinstance(browser, Mapping)
        or capture.get("release_id") != browser.get("release_id")
        or capture.get("research_id") != browser.get("research_id")
        or capture.get("job_id") != browser.get("job_id")
        or capture.get("page_url") != browser.get("page_url")
        or capture.get("dom_visible_text_sha256")
        != canonical_sha256(browser.get("visible_text"))
        or capture.get("dom_links_sha256")
        != canonical_sha256(browser.get("links", []))
        or capture.get("dom_snapshot_sha256") != canonical_sha256(dom_snapshot)
        or not isinstance(runtime, Mapping)
        or runtime.get("document_ready_state") != "complete"
        or not isinstance(runtime.get("user_agent"), str)
        or not runtime["user_agent"].strip()
        or not isinstance(screenshot, Mapping)
        or screenshot.get("media_type") != "image/png"
        or not isinstance(screenshot.get("sha256"), str)
        or not isinstance(screenshot.get("byte_size"), int)
        or screenshot["byte_size"] <= 8
    ):
        raise ValueError("controlled browser capture is invalid")
    return dict(browser)


def _load_browser_argument(
    *, raw_path: Path | None, capture_path: Path | None
) -> dict[str, Any] | None:
    if raw_path is not None and capture_path is not None:
        raise ValueError("raw and controlled browser evidence are mutually exclusive")
    if capture_path is not None:
        return _controlled_browser_view(_read_json(capture_path.resolve()))
    if raw_path is not None:
        return _read_json(raw_path.resolve())
    return None


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )


def _public_job_elapsed_ms(view: Mapping[str, Any]) -> int:
    """Measure one server-owned job phase without including browser think time."""

    created_at = view.get("created_at")
    updated_at = view.get("updated_at")
    if not isinstance(created_at, str) or not isinstance(updated_at, str):
        raise RuntimeError("public job timestamps are unavailable")
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError("public job timestamps are invalid") from exc
    elapsed_ms = int((updated - created).total_seconds() * 1000)
    if elapsed_ms < 0:
        raise RuntimeError("public job timestamps are not monotonic")
    return elapsed_ms


def _request(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    method: str = "GET",
    body: object | None = None,
    raw_body: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 30,
) -> tuple[int, bytes, Mapping[str, str]]:
    if body is not None and raw_body is not None:
        raise ValueError("HTTP request body is ambiguous")
    payload = raw_body if raw_body is not None else (None if body is None else _canonical(body))
    request = urllib.request.Request(url, data=payload, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read(), dict(response.headers.items())
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers.items())


def _json_request(*args: object, **kwargs: object) -> tuple[int, dict[str, Any]]:
    status, payload, _headers = _request(*args, **kwargs)  # type: ignore[arg-type]
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as error:
        raise RuntimeError("product endpoint returned non-JSON content") from error
    if not isinstance(value, dict):
        raise RuntimeError("product endpoint returned an invalid object")
    return status, value


def _release(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError("runtime release identity is unavailable")
    required = {
        "contract",
        "release_id",
        "manifest_sha256",
        "backend_commit",
        "frontend_build_id",
    }
    if set(value) != required or any(not isinstance(value[key], str) for key in required):
        raise RuntimeError("runtime release identity is invalid")
    return dict(value)


def _sse_sequences(payload: bytes) -> list[int]:
    sequences: list[int] = []
    current_id: int | None = None
    for raw in payload.decode("utf-8").splitlines():
        line = raw.strip()
        if line.startswith("id:"):
            try:
                current_id = int(line.split(":", 1)[1].strip())
            except ValueError as error:
                raise RuntimeError("SSE event id is invalid") from error
        elif not line and current_id is not None:
            sequences.append(current_id)
            current_id = None
    if current_id is not None:
        sequences.append(current_id)
    return sequences


def _sse_statuses(payload: bytes) -> list[str]:
    statuses: list[str] = []
    event_type: str | None = None
    for raw in payload.decode("utf-8").splitlines():
        line = raw.strip()
        if line.startswith("event:"):
            event_type = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and event_type == "job":
            try:
                value = json.loads(line.split(":", 1)[1].strip())
            except json.JSONDecodeError as error:
                raise RuntimeError("SSE job event is invalid") from error
            status = value.get("status") if isinstance(value, Mapping) else None
            if isinstance(status, str):
                statuses.append(status)
        elif not line:
            event_type = None
    return statuses


def run_direct_journey(
    *,
    frontend_url: str,
    backend_url: str,
    timeout_seconds: float = 30,
) -> tuple[dict[str, Any], dict[str, Any]]:
    opener = _opener()
    session_status, session = _json_request(
        opener, f"{frontend_url}/api/d18/session"
    )
    if session_status != 200 or session.get("authenticated") is not True:
        raise RuntimeError("signed frontend session is unavailable")
    namespace = session.get("project_namespace")
    if not isinstance(namespace, str) or not namespace:
        raise RuntimeError("frontend project namespace is unavailable")
    health_status, health = _json_request(opener, f"{backend_url}/health/ready")
    if health_status != 200 or health.get("status") != "ready":
        raise RuntimeError("backend readiness is unavailable")
    backend_release = _release(health.get("release"))
    request_body = {
        "research_id": (
            f"{namespace}-readiness-direct-{backend_release['release_id'][:12]}"
        ),
        "messages": [{"role": "user", "content": DIRECT_OBJECTIVE}],
    }
    idempotency_key = "readiness-a-" + hashlib.sha256(
        _canonical(request_body)
    ).hexdigest()[:24]
    started = time.monotonic()
    created_status, created = _json_request(
        opener,
        f"{frontend_url}/api/d18/jobs",
        method="POST",
        body=request_body,
        headers={"Idempotency-Key": idempotency_key},
    )
    if created_status != 202 or not isinstance(created.get("job_id"), str):
        raise RuntimeError("frontend did not accept direct readiness journey")
    job_id = created["job_id"]
    statuses = [str(created.get("status"))]
    view: dict[str, Any] = {}
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        view_status, view = _json_request(
            opener, f"{frontend_url}/api/d18/jobs/{job_id}"
        )
        if view_status != 200:
            raise RuntimeError("frontend could not read direct journey status")
        current = str(view.get("status"))
        if current != statuses[-1]:
            statuses.append(current)
        if current in TERMINAL:
            break
        time.sleep(0.1)
    wall_elapsed_ms = int((time.monotonic() - started) * 1000)
    if view.get("status") not in {"completed", "bounded"}:
        raise RuntimeError(
            f"direct journey did not return an accepted terminal: {view.get('status')}"
        )
    result_status, result = _json_request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/result"
    )
    if result_status != 200:
        raise RuntimeError("frontend did not return a direct journey result")
    event_status, event_bytes, event_headers = _request(
        opener,
        f"{frontend_url}/api/d18/jobs/{job_id}/events",
        timeout=10,
    )
    content_type = event_headers.get("Content-Type", event_headers.get("content-type", ""))
    if event_status != 200 or "text/event-stream" not in content_type:
        raise RuntimeError("frontend SSE stream is unavailable")
    retry_status, retry_created = _json_request(
        opener,
        f"{frontend_url}/api/d18/jobs",
        method="POST",
        body=request_body,
        headers={"Idempotency-Key": idempotency_key},
    )
    if retry_status != 202 or retry_created.get("job_id") != job_id:
        raise RuntimeError("frontend exact direct retry drifted")
    retry_result_status, retry_result = _json_request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/result"
    )
    retry_event_status, retry_event_bytes, _ = _request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/events", timeout=10
    )
    if retry_result_status != 200 or retry_event_status != 200:
        raise RuntimeError("frontend could not verify exact direct retry")
    execution = result.get("executionReceipt")
    retry_execution = retry_result.get("executionReceipt")
    project_record = execution.get("project_record") if isinstance(execution, Mapping) else {}
    retry_project_record = (
        retry_execution.get("project_record")
        if isinstance(retry_execution, Mapping)
        else {}
    )
    observation = {
        "contract": "d18.product_readiness_observation/v1",
        "journey": DIRECT_JOURNEY,
        "session_release": {
            "release_id": session.get("release_id"),
            "frontend_build_id": session.get("frontend_build_id"),
        },
        "release_identities": [
            backend_release,
            _release(created.get("runtime_release")),
            _release(view.get("runtime_release")),
            _release(result.get("runtime_release")),
            _release(retry_created.get("runtime_release")),
        ],
        "request": request_body,
        "job_id": job_id,
        "statuses": statuses,
        "wall_elapsed_ms": wall_elapsed_ms,
        "sse_sequences": _sse_sequences(event_bytes),
        "sse_sha256": hashlib.sha256(event_bytes).hexdigest(),
        "result_view": result,
        "retry": {
            "same_job_id": retry_created.get("job_id") == job_id,
            "idempotencyReplayEvidence": retry_created.get(
                "idempotencyReplayEvidence"
            ),
            "result_sha256_before": hashlib.sha256(_canonical(result)).hexdigest(),
            "result_sha256_after": hashlib.sha256(_canonical(retry_result)).hexdigest(),
            "execution_receipt_sha256_before": (
                execution.get("receipt_sha256") if isinstance(execution, Mapping) else None
            ),
            "execution_receipt_sha256_after": (
                retry_execution.get("receipt_sha256")
                if isinstance(retry_execution, Mapping)
                else None
            ),
            "project_record_sha256_before": project_record.get("last_event_sha256"),
            "project_record_sha256_after": retry_project_record.get("last_event_sha256"),
            "project_record_event_count_before": project_record.get("event_count"),
            "project_record_event_count_after": retry_project_record.get("event_count"),
            "sse_sha256_before": hashlib.sha256(event_bytes).hexdigest(),
            "sse_sha256_after": hashlib.sha256(retry_event_bytes).hexdigest(),
        },
    }
    return observation, evaluate_direct_journey(observation)


def _find_material(
    opener: urllib.request.OpenerDirector,
    frontend_url: str,
    research_id: str,
) -> dict[str, Any] | None:
    status, library = _json_request(
        opener,
        f"{frontend_url}/api/d18/research/{research_id}/materials",
    )
    if status != 200 or not isinstance(library.get("materials"), list):
        raise RuntimeError("frontend material library is unavailable")
    matches = [
        item
        for item in library["materials"]
        if isinstance(item, Mapping)
        and item.get("byte_size") == MATERIAL_SOURCE_BYTES
        and isinstance(item.get("current_revision"), Mapping)
        and item["current_revision"].get("input_artifact_id")
        == MATERIAL_SOURCE_SHA256
    ]
    if len(matches) > 1:
        raise RuntimeError("fixed material has ambiguous project identities")
    return dict(matches[0]) if matches else None


def _accepted_material_view(
    opener: urllib.request.OpenerDirector,
    frontend_url: str,
    research_id: str,
    material_id: str,
) -> dict[str, Any]:
    """Read the Project Record-overlaid material view after formal execution."""

    material = _find_material(opener, frontend_url, research_id)
    revision = material.get("current_revision") if isinstance(material, Mapping) else None
    if (
        not isinstance(material, Mapping)
        or material.get("material_id") != material_id
        or not isinstance(revision, Mapping)
        or revision.get("scientific_status") != "accepted_project_input"
        or revision.get("processing_status") != "ready"
    ):
        raise RuntimeError("Project Record did not accept the material revision")
    return dict(material)


def _upload_material(
    opener: urllib.request.OpenerDirector,
    frontend_url: str,
    research_id: str,
    source: Path,
    payload: bytes,
    *,
    timeout_seconds: float,
) -> tuple[dict[str, Any], int, bool]:
    existing = _find_material(opener, frontend_url, research_id)
    if existing is not None:
        return existing, 0, True
    started = time.monotonic()
    status, created = _json_request(
        opener,
        f"{frontend_url}/api/d18/research/{research_id}/material-uploads",
        method="POST",
        body={
            "uploads": [
                {
                    "filename": source.name,
                    "declared_mime": MATERIAL_DECLARED_MIME,
                    "total_bytes": len(payload),
                    "expected_sha256": MATERIAL_SOURCE_SHA256,
                }
            ]
        },
        headers={
            "Idempotency-Key": (
                f"readiness-b-upload-{research_id}-{MATERIAL_SOURCE_SHA256[:24]}"
            )
        },
    )
    uploads = created.get("uploads")
    if status != 201 or not isinstance(uploads, list) or len(uploads) != 1:
        raise RuntimeError("frontend did not create the material upload")
    upload = uploads[0]
    upload_id = upload.get("upload_id") if isinstance(upload, Mapping) else None
    part_size = upload.get("part_size") if isinstance(upload, Mapping) else None
    if (
        not isinstance(upload_id, str)
        or isinstance(part_size, bool)
        or not isinstance(part_size, int)
        or part_size < 1
    ):
        raise RuntimeError("frontend material upload contract is invalid")
    for part_number, first_byte in enumerate(range(0, len(payload), part_size)):
        part = payload[first_byte : first_byte + part_size]
        last_byte = first_byte + len(part) - 1
        part_status, _part_body, _part_headers = _request(
            opener,
            f"{frontend_url}/api/d18/material-uploads/{upload_id}/parts/{part_number}",
            method="PUT",
            raw_body=part,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Range": f"bytes {first_byte}-{last_byte}/{len(payload)}",
                "X-D18-Project-ID": research_id,
                "X-Part-SHA256": hashlib.sha256(part).hexdigest(),
            },
        )
        if part_status != 200:
            raise RuntimeError("frontend material part upload failed")
    completed_status, _completed = _json_request(
        opener,
        f"{frontend_url}/api/d18/material-uploads/{upload_id}/complete",
        method="POST",
        body={"scope": "project"},
        headers={"X-D18-Project-ID": research_id},
    )
    if completed_status != 200:
        raise RuntimeError("frontend did not complete the material upload")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        material = _find_material(opener, frontend_url, research_id)
        processing = (
            material.get("current_revision", {}).get("processing_status")
            if material is not None
            else None
        )
        if processing == "ready":
            return material, int((time.monotonic() - started) * 1000), False
        if processing in {"failed", "rejected", "partial"}:
            raise RuntimeError(f"material processing stopped as {processing}")
        time.sleep(0.1)
    raise RuntimeError("material processing did not converge")


def run_material_journey(
    *,
    frontend_url: str,
    backend_url: str,
    source: Path,
    timeout_seconds: float = 90,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = source.read_bytes()
    if (
        len(payload) != MATERIAL_SOURCE_BYTES
        or hashlib.sha256(payload).hexdigest() != MATERIAL_SOURCE_SHA256
    ):
        raise RuntimeError("content-bound material readiness source drifted")
    opener = _opener()
    session_status, session = _json_request(opener, f"{frontend_url}/api/d18/session")
    if session_status != 200 or session.get("authenticated") is not True:
        raise RuntimeError("signed frontend session is unavailable")
    namespace = session.get("project_namespace")
    if not isinstance(namespace, str) or not namespace:
        raise RuntimeError("frontend project namespace is unavailable")
    health_status, health = _json_request(opener, f"{backend_url}/health/ready")
    if health_status != 200 or health.get("status") != "ready":
        raise RuntimeError("backend readiness is unavailable")
    backend_release = _release(health.get("release"))
    research_id = (
        f"{namespace}-readiness-material-{backend_release['release_id'][:12]}"
    )
    material, upload_to_ready_ms, reused_material = _upload_material(
        opener,
        frontend_url,
        research_id,
        source,
        payload,
        timeout_seconds=timeout_seconds,
    )
    material_id = material.get("material_id")
    if not isinstance(material_id, str):
        raise RuntimeError("public material identity is unavailable")
    request_body = {
        "research_id": research_id,
        "material_ids": [material_id],
        "messages": [{"role": "user", "content": MATERIAL_OBJECTIVE}],
    }
    idempotency_key = "readiness-b-" + hashlib.sha256(
        _canonical(request_body)
    ).hexdigest()[:24]
    started = time.monotonic()
    created_status, created = _json_request(
        opener,
        f"{frontend_url}/api/d18/jobs",
        method="POST",
        body=request_body,
        headers={"Idempotency-Key": idempotency_key},
    )
    if created_status != 202 or not isinstance(created.get("job_id"), str):
        raise RuntimeError("frontend did not accept material readiness journey")
    job_id = created["job_id"]
    statuses = [str(created.get("status"))]
    view: dict[str, Any] = {}
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        view_status, view = _json_request(
            opener, f"{frontend_url}/api/d18/jobs/{job_id}"
        )
        if view_status != 200:
            raise RuntimeError("frontend could not read material journey status")
        current = str(view.get("status"))
        if current != statuses[-1]:
            statuses.append(current)
        if current in TERMINAL:
            break
        time.sleep(0.1)
    wall_elapsed_ms = int((time.monotonic() - started) * 1000)
    if view.get("status") not in {"completed", "bounded"}:
        raise RuntimeError(
            f"material journey did not return an accepted terminal: {view.get('status')}"
        )
    result_status, result = _json_request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/result"
    )
    if result_status != 200:
        raise RuntimeError("frontend did not return a material journey result")
    event_status, event_bytes, event_headers = _request(
        opener,
        f"{frontend_url}/api/d18/jobs/{job_id}/events",
        timeout=10,
    )
    content_type = event_headers.get("Content-Type", event_headers.get("content-type", ""))
    if event_status != 200 or "text/event-stream" not in content_type:
        raise RuntimeError("frontend SSE stream is unavailable")
    retry_status, retry_created = _json_request(
        opener,
        f"{frontend_url}/api/d18/jobs",
        method="POST",
        body=request_body,
        headers={"Idempotency-Key": idempotency_key},
    )
    if retry_status != 202:
        raise RuntimeError("frontend did not accept exact material retry")
    retry_result_status, retry_result = _json_request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/result"
    )
    retry_event_status, retry_event_bytes, _retry_event_headers = _request(
        opener,
        f"{frontend_url}/api/d18/jobs/{job_id}/events",
        timeout=10,
    )
    if retry_result_status != 200 or retry_event_status != 200:
        raise RuntimeError("frontend could not verify exact material retry")
    material = _accepted_material_view(
        opener,
        frontend_url,
        research_id,
        material_id,
    )
    revision = material.get("current_revision")
    service_upload_to_ready_ms = (
        revision.get("upload_to_ready_ms") if isinstance(revision, Mapping) else None
    )
    if (
        isinstance(service_upload_to_ready_ms, bool)
        or not isinstance(service_upload_to_ready_ms, int)
        or service_upload_to_ready_ms < 0
    ):
        raise RuntimeError("material processing receipt is unavailable")
    execution = result.get("executionReceipt")
    retry_execution = retry_result.get("executionReceipt")
    project_record = execution.get("project_record") if isinstance(execution, Mapping) else {}
    retry_project_record = (
        retry_execution.get("project_record")
        if isinstance(retry_execution, Mapping)
        else {}
    )
    observation = {
        "contract": "d18.product_readiness_observation/v1",
        "journey": MATERIAL_JOURNEY,
        "session_release": {
            "release_id": session.get("release_id"),
            "frontend_build_id": session.get("frontend_build_id"),
        },
        "release_identities": [
            backend_release,
            _release(created.get("runtime_release")),
            _release(view.get("runtime_release")),
            _release(result.get("runtime_release")),
            _release(retry_created.get("runtime_release")),
        ],
        "source": {
            "profile_id": MATERIAL_PROFILE_ID,
            "filename": source.name,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
        "material": material,
        "material_reused": reused_material,
        "upload_to_ready_ms": service_upload_to_ready_ms,
        "probe_upload_to_ready_ms": upload_to_ready_ms,
        "request": request_body,
        "job_id": job_id,
        "statuses": statuses,
        "wall_elapsed_ms": wall_elapsed_ms,
        "sse_sequences": _sse_sequences(event_bytes),
        "sse_sha256": hashlib.sha256(event_bytes).hexdigest(),
        "result_view": result,
        "retry": {
            "same_job_id": retry_created.get("job_id") == job_id,
            "idempotencyReplayEvidence": retry_created.get(
                "idempotencyReplayEvidence"
            ),
            "result_sha256_before": hashlib.sha256(_canonical(result)).hexdigest(),
            "result_sha256_after": hashlib.sha256(_canonical(retry_result)).hexdigest(),
            "execution_receipt_sha256_before": (
                execution.get("receipt_sha256") if isinstance(execution, Mapping) else None
            ),
            "execution_receipt_sha256_after": (
                retry_execution.get("receipt_sha256")
                if isinstance(retry_execution, Mapping)
                else None
            ),
            "project_record_sha256_before": project_record.get("last_event_sha256"),
            "project_record_sha256_after": retry_project_record.get("last_event_sha256"),
            "project_record_event_count_before": project_record.get("event_count"),
            "project_record_event_count_after": retry_project_record.get("event_count"),
            "sse_sha256_before": hashlib.sha256(event_bytes).hexdigest(),
            "sse_sha256_after": hashlib.sha256(retry_event_bytes).hexdigest(),
        },
    }
    return observation, evaluate_material_journey(observation)


def run_official_journey(
    *,
    frontend_url: str,
    backend_url: str,
    timeout_seconds: float = 480,
) -> tuple[dict[str, Any], dict[str, Any]]:
    opener = _opener()
    session_status, session = _json_request(opener, f"{frontend_url}/api/d18/session")
    if session_status != 200 or session.get("authenticated") is not True:
        raise RuntimeError("signed frontend session is unavailable")
    namespace = session.get("project_namespace")
    if not isinstance(namespace, str) or not namespace:
        raise RuntimeError("frontend project namespace is unavailable")
    health_status, health = _json_request(opener, f"{backend_url}/health/ready")
    if health_status != 200 or health.get("status") != "ready":
        raise RuntimeError("backend readiness is unavailable")
    backend_release = _release(health.get("release"))
    request_body = {
        "research_id": (
            f"{namespace}-readiness-official-{backend_release['release_id'][:12]}"
        ),
        "messages": [{"role": "user", "content": OFFICIAL_OBJECTIVE}],
    }
    idempotency_key = "readiness-c-" + hashlib.sha256(
        _canonical(request_body)
    ).hexdigest()[:24]
    started = time.monotonic()
    created_status, created = _json_request(
        opener,
        f"{frontend_url}/api/d18/jobs",
        method="POST",
        body=request_body,
        headers={"Idempotency-Key": idempotency_key},
    )
    if created_status != 202 or not isinstance(created.get("job_id"), str):
        raise RuntimeError("frontend did not accept official-source readiness journey")
    job_id = created["job_id"]
    statuses = [str(created.get("status"))]
    view: dict[str, Any] = {}
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        view_status, view = _json_request(
            opener, f"{frontend_url}/api/d18/jobs/{job_id}"
        )
        if view_status != 200:
            raise RuntimeError("frontend could not read official journey status")
        current = str(view.get("status"))
        if current != statuses[-1]:
            statuses.append(current)
        if current in TERMINAL:
            break
        time.sleep(0.1)
    wall_elapsed_ms = int((time.monotonic() - started) * 1000)
    if view.get("status") not in {"completed", "bounded"}:
        raise RuntimeError(
            "official journey did not return an accepted terminal: "
            + str(view.get("status"))
        )
    result_status, result = _json_request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/result"
    )
    if result_status != 200:
        raise RuntimeError("frontend did not return an official journey result")
    event_status, event_bytes, event_headers = _request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/events", timeout=10
    )
    content_type = event_headers.get("Content-Type", event_headers.get("content-type", ""))
    if event_status != 200 or "text/event-stream" not in content_type:
        raise RuntimeError("frontend SSE stream is unavailable")
    retry_status, retry_created = _json_request(
        opener,
        f"{frontend_url}/api/d18/jobs",
        method="POST",
        body=request_body,
        headers={"Idempotency-Key": idempotency_key},
    )
    if retry_status != 202:
        raise RuntimeError("frontend did not accept exact official journey retry")
    retry_result_status, retry_result = _json_request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/result"
    )
    retry_event_status, retry_event_bytes, _ = _request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/events", timeout=10
    )
    if retry_result_status != 200 or retry_event_status != 200:
        raise RuntimeError("frontend could not verify exact official journey retry")
    execution = result.get("executionReceipt")
    retry_execution = retry_result.get("executionReceipt")
    project_record = execution.get("project_record") if isinstance(execution, Mapping) else {}
    retry_project_record = (
        retry_execution.get("project_record")
        if isinstance(retry_execution, Mapping)
        else {}
    )
    observation = {
        "contract": "d18.product_readiness_observation/v1",
        "journey": OFFICIAL_JOURNEY,
        "session_release": {
            "release_id": session.get("release_id"),
            "frontend_build_id": session.get("frontend_build_id"),
        },
        "release_identities": [
            backend_release,
            _release(created.get("runtime_release")),
            _release(view.get("runtime_release")),
            _release(result.get("runtime_release")),
            _release(retry_created.get("runtime_release")),
        ],
        "request": request_body,
        "job_id": job_id,
        "statuses": statuses,
        "wall_elapsed_ms": wall_elapsed_ms,
        "sse_sequences": _sse_sequences(event_bytes),
        "sse_sha256": hashlib.sha256(event_bytes).hexdigest(),
        "result_view": result,
        "retry": {
            "same_job_id": retry_created.get("job_id") == job_id,
            "idempotencyReplayEvidence": retry_created.get(
                "idempotencyReplayEvidence"
            ),
            "result_sha256_before": hashlib.sha256(_canonical(result)).hexdigest(),
            "result_sha256_after": hashlib.sha256(_canonical(retry_result)).hexdigest(),
            "execution_receipt_sha256_before": (
                execution.get("receipt_sha256") if isinstance(execution, Mapping) else None
            ),
            "execution_receipt_sha256_after": (
                retry_execution.get("receipt_sha256")
                if isinstance(retry_execution, Mapping)
                else None
            ),
            "project_record_sha256_before": project_record.get("last_event_sha256"),
            "project_record_sha256_after": retry_project_record.get("last_event_sha256"),
            "project_record_event_count_before": project_record.get("event_count"),
            "project_record_event_count_after": retry_project_record.get("event_count"),
            "sse_sha256_before": hashlib.sha256(event_bytes).hexdigest(),
            "sse_sha256_after": hashlib.sha256(retry_event_bytes).hexdigest(),
        },
    }
    return observation, evaluate_official_journey(observation)


def run_native_journey(
    *,
    frontend_url: str,
    backend_url: str,
    timeout_seconds: float = 90,
) -> tuple[dict[str, Any], dict[str, Any]]:
    opener = _opener()
    session_status, session = _json_request(opener, f"{frontend_url}/api/d18/session")
    if session_status != 200 or session.get("authenticated") is not True:
        raise RuntimeError("signed frontend session is unavailable")
    namespace = session.get("project_namespace")
    if not isinstance(namespace, str) or not namespace:
        raise RuntimeError("frontend project namespace is unavailable")
    health_status, health = _json_request(opener, f"{backend_url}/health/ready")
    if health_status != 200 or health.get("status") != "ready":
        raise RuntimeError("backend readiness is unavailable")
    backend_release = _release(health.get("release"))
    request_body = {
        "research_id": f"{namespace}-readiness-native-{backend_release['release_id'][:12]}",
        "messages": [{"role": "user", "content": NATIVE_OBJECTIVE}],
    }
    idempotency_key = "readiness-e-" + hashlib.sha256(
        _canonical(request_body)
    ).hexdigest()[:24]
    started = time.monotonic()
    created_status, created = _json_request(
        opener,
        f"{frontend_url}/api/d18/jobs",
        method="POST",
        body=request_body,
        headers={"Idempotency-Key": idempotency_key},
    )
    if created_status != 202 or not isinstance(created.get("job_id"), str):
        raise RuntimeError("frontend did not accept TianGong-native readiness journey")
    job_id = created["job_id"]
    statuses = [str(created.get("status"))]
    view: dict[str, Any] = {}
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        view_status, view = _json_request(
            opener, f"{frontend_url}/api/d18/jobs/{job_id}"
        )
        if view_status != 200:
            raise RuntimeError("frontend could not read TianGong-native journey status")
        current = str(view.get("status"))
        if current != statuses[-1]:
            statuses.append(current)
        if current in TERMINAL:
            break
        time.sleep(0.1)
    wall_elapsed_ms = int((time.monotonic() - started) * 1000)
    if view.get("status") not in {"completed", "bounded"}:
        raise RuntimeError(
            "TianGong-native journey did not return an accepted terminal: "
            + str(view.get("status"))
        )
    result_status, result = _json_request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/result"
    )
    if result_status != 200:
        raise RuntimeError("frontend did not return a TianGong-native result")
    event_status, event_bytes, event_headers = _request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/events", timeout=10
    )
    content_type = event_headers.get("Content-Type", event_headers.get("content-type", ""))
    if event_status != 200 or "text/event-stream" not in content_type:
        raise RuntimeError("frontend SSE stream is unavailable")
    retry_status, retry_created = _json_request(
        opener,
        f"{frontend_url}/api/d18/jobs",
        method="POST",
        body=request_body,
        headers={"Idempotency-Key": idempotency_key},
    )
    if retry_status != 202 or retry_created.get("job_id") != job_id:
        raise RuntimeError("frontend exact TianGong-native retry drifted")
    retry_result_status, retry_result = _json_request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/result"
    )
    retry_event_status, retry_event_bytes, _ = _request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/events", timeout=10
    )
    if retry_result_status != 200 or retry_event_status != 200:
        raise RuntimeError("frontend could not verify TianGong-native retry")
    execution = result.get("executionReceipt")
    retry_execution = retry_result.get("executionReceipt")
    project_record = execution.get("project_record") if isinstance(execution, Mapping) else {}
    retry_project_record = (
        retry_execution.get("project_record")
        if isinstance(retry_execution, Mapping)
        else {}
    )
    observation = {
        "contract": "d18.product_readiness_observation/v1",
        "journey": NATIVE_JOURNEY,
        "session_release": {
            "release_id": session.get("release_id"),
            "frontend_build_id": session.get("frontend_build_id"),
        },
        "release_identities": [
            backend_release,
            _release(created.get("runtime_release")),
            _release(view.get("runtime_release")),
            _release(result.get("runtime_release")),
            _release(retry_created.get("runtime_release")),
        ],
        "request": request_body,
        "job_id": job_id,
        "statuses": statuses,
        "wall_elapsed_ms": wall_elapsed_ms,
        "sse_sequences": _sse_sequences(event_bytes),
        "sse_sha256": hashlib.sha256(event_bytes).hexdigest(),
        "result_view": result,
        "retry": {
            "same_job_id": retry_created.get("job_id") == job_id,
            "idempotencyReplayEvidence": retry_created.get(
                "idempotencyReplayEvidence"
            ),
            "result_sha256_before": hashlib.sha256(_canonical(result)).hexdigest(),
            "result_sha256_after": hashlib.sha256(_canonical(retry_result)).hexdigest(),
            "execution_receipt_sha256_before": (
                execution.get("receipt_sha256") if isinstance(execution, Mapping) else None
            ),
            "execution_receipt_sha256_after": (
                retry_execution.get("receipt_sha256")
                if isinstance(retry_execution, Mapping)
                else None
            ),
            "project_record_sha256_before": project_record.get("last_event_sha256"),
            "project_record_sha256_after": retry_project_record.get("last_event_sha256"),
            "project_record_event_count_before": project_record.get("event_count"),
            "project_record_event_count_after": retry_project_record.get("event_count"),
            "sse_sha256_before": hashlib.sha256(event_bytes).hexdigest(),
            "sse_sha256_after": hashlib.sha256(retry_event_bytes).hexdigest(),
        },
    }
    return observation, evaluate_native_journey(observation)


def _recover_browser_created_objective(
    *,
    opener: object,
    frontend_url: str,
    research_id: str,
    job_id: str,
    expected_status: str,
    journey_label: str,
) -> str:
    """Return the exact public wire objective for an already-created browser job."""

    recovery_status, recovery = _json_request(
        opener,
        f"{frontend_url}/api/d18/research/{research_id}/jobs/latest",
    )
    recovery_job = recovery.get("job") if isinstance(recovery, Mapping) else None
    recovery_messages = (
        recovery.get("user_messages") if isinstance(recovery, Mapping) else None
    )
    if (
        recovery_status != 200
        or recovery.get("contract") != "d18.public_research_recovery/v1"
        or recovery.get("research_id") != research_id
        or not isinstance(recovery_job, Mapping)
        or recovery_job.get("job_id") != job_id
        or recovery_job.get("project_id") != research_id
        or recovery_job.get("status") != expected_status
        or not isinstance(recovery_messages, list)
        or len(recovery_messages) != 1
        or not isinstance(recovery_messages[0], str)
        or not recovery_messages[0].strip()
        or recovery.get("objective") != recovery_messages[0]
    ):
        raise RuntimeError(
            f"browser-created {journey_label} request recovery is invalid"
        )
    return recovery_messages[0]


def _finalize_browser_started_journey(
    *,
    frontend_url: str,
    backend_url: str,
    browser_view: Mapping[str, Any],
    journey: str,
    objective: str,
    journey_label: str,
    evaluator: object,
    material_ids: list[str] | None = None,
    observation_extras: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Observe and replay the exact public job that the real browser created."""

    if browser_view.get("contract") != "d18.browser_product_observation/v1":
        raise RuntimeError(f"{journey_label} browser observation contract is invalid")
    research_id = browser_view.get("research_id")
    job_id = browser_view.get("job_id")
    if not isinstance(research_id, str) or not research_id:
        raise RuntimeError(f"{journey_label} browser research identity is unavailable")
    if not isinstance(job_id, str) or not job_id:
        raise RuntimeError(f"{journey_label} browser job identity is unavailable")

    opener = _opener()
    session_status, session = _json_request(
        opener, f"{frontend_url}/api/d18/session"
    )
    if session_status != 200 or session.get("authenticated") is not True:
        raise RuntimeError("signed frontend session is unavailable")
    namespace = session.get("project_namespace")
    if (
        not isinstance(namespace, str)
        or not research_id.startswith(f"{namespace}-")
    ):
        raise RuntimeError(
            f"{journey_label} browser project is outside the active session"
        )
    health_status, health = _json_request(opener, f"{backend_url}/health/ready")
    if health_status != 200 or health.get("status") != "ready":
        raise RuntimeError("backend readiness is unavailable")
    backend_release = _release(health.get("release"))

    view_status, view = _json_request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}"
    )
    if (
        view_status != 200
        or view.get("project_id") != research_id
        or view.get("status") not in {"completed", "bounded"}
    ):
        raise RuntimeError(f"browser-created {journey_label} job is unavailable")
    wire_objective = _recover_browser_created_objective(
        opener=opener,
        frontend_url=frontend_url,
        research_id=research_id,
        job_id=job_id,
        expected_status=str(view.get("status")),
        journey_label=journey_label,
    )
    if objective not in wire_objective:
        raise RuntimeError(
            f"browser-created {journey_label} objective does not match the journey"
        )
    result_status, result = _json_request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/result"
    )
    if result_status != 200:
        raise RuntimeError(
            f"frontend did not return the browser-created {journey_label} result"
        )
    event_status, event_bytes, event_headers = _request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/events", timeout=10
    )
    content_type = event_headers.get("Content-Type", event_headers.get("content-type", ""))
    if event_status != 200 or "text/event-stream" not in content_type:
        raise RuntimeError("frontend SSE stream is unavailable")

    request_body = {
        "research_id": research_id,
        "messages": [{"role": "user", "content": wire_objective}],
        "source_materials": [],
        "material_ids": list(material_ids or []),
    }
    retry_status, retry_created = _json_request(
        opener,
        f"{frontend_url}/api/d18/jobs",
        method="POST",
        body=request_body,
        headers={"Idempotency-Key": f"web-{research_id}"},
    )
    if retry_status != 202 or retry_created.get("job_id") != job_id:
        raise RuntimeError(
            f"frontend exact browser-created {journey_label} retry drifted"
        )
    retry_result_status, retry_result = _json_request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/result"
    )
    retry_event_status, retry_event_bytes, _ = _request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/events", timeout=10
    )
    if retry_result_status != 200 or retry_event_status != 200:
        raise RuntimeError(
            f"frontend could not verify browser-created {journey_label} retry"
        )

    execution = result.get("executionReceipt")
    retry_execution = retry_result.get("executionReceipt")
    project_record = execution.get("project_record") if isinstance(execution, Mapping) else {}
    retry_project_record = (
        retry_execution.get("project_record")
        if isinstance(retry_execution, Mapping)
        else {}
    )
    elapsed = execution.get("elapsed_ms") if isinstance(execution, Mapping) else {}
    wall_elapsed_ms = (
        elapsed.get("request_to_user_delivery")
        if isinstance(elapsed, Mapping)
        else None
    )
    if isinstance(wall_elapsed_ms, bool) or not isinstance(wall_elapsed_ms, int):
        raise RuntimeError(
            f"browser-created {journey_label} elapsed time is unavailable"
        )
    observation = {
        "contract": "d18.product_readiness_observation/v1",
        "journey": journey,
        "session_release": {
            "release_id": session.get("release_id"),
            "frontend_build_id": session.get("frontend_build_id"),
        },
        "release_identities": [
            backend_release,
            _release(view.get("runtime_release")),
            _release(result.get("runtime_release")),
            _release(retry_created.get("runtime_release")),
        ],
        "request": request_body,
        "job_id": job_id,
        "statuses": [str(view.get("status"))],
        "wall_elapsed_ms": wall_elapsed_ms,
        "sse_sequences": _sse_sequences(event_bytes),
        "sse_sha256": hashlib.sha256(event_bytes).hexdigest(),
        "result_view": result,
        "browser_view": dict(browser_view),
        "retry": {
            "same_job_id": retry_created.get("job_id") == job_id,
            "idempotencyReplayEvidence": retry_created.get(
                "idempotencyReplayEvidence"
            ),
            "result_sha256_before": hashlib.sha256(_canonical(result)).hexdigest(),
            "result_sha256_after": hashlib.sha256(_canonical(retry_result)).hexdigest(),
            "execution_receipt_sha256_before": (
                execution.get("receipt_sha256") if isinstance(execution, Mapping) else None
            ),
            "execution_receipt_sha256_after": (
                retry_execution.get("receipt_sha256")
                if isinstance(retry_execution, Mapping)
                else None
            ),
            "project_record_sha256_before": project_record.get("last_event_sha256"),
            "project_record_sha256_after": retry_project_record.get("last_event_sha256"),
            "project_record_event_count_before": project_record.get("event_count"),
            "project_record_event_count_after": retry_project_record.get("event_count"),
            "sse_sha256_before": hashlib.sha256(event_bytes).hexdigest(),
            "sse_sha256_after": hashlib.sha256(retry_event_bytes).hexdigest(),
        },
    }
    if observation_extras:
        overlap = set(observation).intersection(observation_extras)
        if overlap:
            raise RuntimeError(
                f"{journey_label} observation extras overlap: {sorted(overlap)}"
            )
        observation.update(dict(observation_extras))
    if not callable(evaluator):
        raise RuntimeError("browser journey evaluator is unavailable")
    return observation, evaluator(observation)


def finalize_direct_browser_started_journey(
    *,
    frontend_url: str,
    backend_url: str,
    browser_view: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Finalize the exact direct-answer job created by the real browser."""

    return _finalize_browser_started_journey(
        frontend_url=frontend_url,
        backend_url=backend_url,
        browser_view=browser_view,
        journey=DIRECT_JOURNEY,
        objective=DIRECT_OBJECTIVE,
        journey_label="direct-answer",
        evaluator=evaluate_direct_journey,
    )


def finalize_material_browser_started_journey(
    *,
    frontend_url: str,
    backend_url: str,
    browser_view: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Finalize the exact content-bound material job created by the real browser."""

    research_id = browser_view.get("research_id")
    if not isinstance(research_id, str) or not research_id:
        raise RuntimeError("material browser research identity is unavailable")
    material_opener = _opener()
    session_status, session = _json_request(
        material_opener, f"{frontend_url}/api/d18/session"
    )
    namespace = session.get("project_namespace") if isinstance(session, Mapping) else None
    if (
        session_status != 200
        or session.get("authenticated") is not True
        or not isinstance(namespace, str)
        or not research_id.startswith(f"{namespace}-")
    ):
        raise RuntimeError("material browser project is outside the active session")
    material = _find_material(material_opener, frontend_url, research_id)
    if material is None:
        raise RuntimeError("browser-created fixed material is unavailable")
    material_id = material.get("material_id")
    revision = material.get("current_revision")
    if (
        not isinstance(material_id, str)
        or material.get("byte_size") != MATERIAL_SOURCE_BYTES
        or not isinstance(material.get("display_name"), str)
        or not material["display_name"].strip()
        or not isinstance(revision, Mapping)
        or revision.get("input_artifact_id") != MATERIAL_SOURCE_SHA256
        or revision.get("processing_status") != "ready"
        or revision.get("scientific_status") != "accepted_project_input"
    ):
        raise RuntimeError("browser-created fixed material identity drifted")
    upload_to_ready_ms = browser_view.get("upload_to_ready_ms")
    if (
        isinstance(upload_to_ready_ms, bool)
        or not isinstance(upload_to_ready_ms, int)
        or upload_to_ready_ms < 0
    ):
        raise RuntimeError("browser material processing time is unavailable")
    return _finalize_browser_started_journey(
        frontend_url=frontend_url,
        backend_url=backend_url,
        browser_view=browser_view,
        journey=MATERIAL_JOURNEY,
        objective=MATERIAL_OBJECTIVE,
        journey_label="material",
        evaluator=evaluate_material_journey,
        material_ids=[material_id],
        observation_extras={
            "source": {
                "profile_id": MATERIAL_PROFILE_ID,
                "filename": material["display_name"],
                "bytes": MATERIAL_SOURCE_BYTES,
                "sha256": MATERIAL_SOURCE_SHA256,
            },
            "material": material,
            "material_reused": False,
            "upload_to_ready_ms": upload_to_ready_ms,
        },
    )


def finalize_official_browser_started_journey(
    *,
    frontend_url: str,
    backend_url: str,
    browser_view: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Finalize the exact official-source job created by the real browser."""

    return _finalize_browser_started_journey(
        frontend_url=frontend_url,
        backend_url=backend_url,
        browser_view=browser_view,
        journey=OFFICIAL_JOURNEY,
        objective=OFFICIAL_OBJECTIVE,
        journey_label="official-source",
        evaluator=evaluate_official_journey,
    )


def finalize_native_browser_started_journey(
    *,
    frontend_url: str,
    backend_url: str,
    browser_view: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Finalize the exact TianGong-native job created by the real browser."""

    return _finalize_browser_started_journey(
        frontend_url=frontend_url,
        backend_url=backend_url,
        browser_view=browser_view,
        journey=NATIVE_JOURNEY,
        objective=NATIVE_OBJECTIVE,
        journey_label="TianGong-native",
        evaluator=evaluate_native_journey,
    )


def observe_browser_started_missing_data_waiting(
    *,
    frontend_url: str,
    backend_url: str,
    browser_waiting: Mapping[str, Any],
) -> dict[str, Any]:
    """Capture the exact browser-created job at its waiting boundary."""

    if browser_waiting.get("contract") != "d18.browser_product_observation/v1":
        raise RuntimeError("missing-data browser observation contract is invalid")
    research_id = browser_waiting.get("research_id")
    job_id = browser_waiting.get("job_id")
    if not isinstance(research_id, str) or not research_id:
        raise RuntimeError("missing-data browser research identity is unavailable")
    if not isinstance(job_id, str) or not job_id:
        raise RuntimeError("missing-data browser job identity is unavailable")

    opener = _opener()
    session_status, session = _json_request(
        opener, f"{frontend_url}/api/d18/session"
    )
    if session_status != 200 or session.get("authenticated") is not True:
        raise RuntimeError("signed frontend session is unavailable")
    namespace = session.get("project_namespace")
    if (
        not isinstance(namespace, str)
        or not research_id.startswith(f"{namespace}-")
    ):
        raise RuntimeError("missing-data browser project is outside the active session")
    health_status, health = _json_request(opener, f"{backend_url}/health/ready")
    if health_status != 200 or health.get("status") != "ready":
        raise RuntimeError("backend readiness is unavailable")
    backend_release = _release(health.get("release"))
    view_status, waiting = _json_request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}"
    )
    if (
        view_status != 200
        or waiting.get("project_id") != research_id
        or waiting.get("status") != "waiting_for_input"
    ):
        raise RuntimeError("browser-created missing-data job is not waiting")
    event_status, event_bytes, event_headers = _request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/events", timeout=10
    )
    content_type = event_headers.get("Content-Type", event_headers.get("content-type", ""))
    if event_status != 200 or "text/event-stream" not in content_type:
        raise RuntimeError("frontend SSE stream is unavailable")
    progress = waiting.get("progress_receipt")
    waiting_record = (
        progress.get("project_record") if isinstance(progress, Mapping) else None
    )
    if not isinstance(waiting_record, Mapping):
        raise RuntimeError("waiting execution receipt is unavailable")
    wire_objective = _recover_browser_created_objective(
        opener=opener,
        frontend_url=frontend_url,
        research_id=research_id,
        job_id=job_id,
        expected_status="waiting_for_input",
        journey_label="missing-data",
    )
    waiting_elapsed_ms = _public_job_elapsed_ms(waiting)
    request_body = {
        "research_id": research_id,
        "messages": [{"role": "user", "content": wire_objective}],
        "source_materials": [],
        "material_ids": [],
    }
    return {
        "contract": "d18.product_readiness_observation/v1",
        "journey": MISSING_DATA_JOURNEY,
        "session_release": {
            "release_id": session.get("release_id"),
            "frontend_build_id": session.get("frontend_build_id"),
        },
        "release_identities": [
            backend_release,
            _release(waiting.get("runtime_release")),
        ],
        "request": request_body,
        "idempotency_key": f"web-{research_id}",
        "job_id": job_id,
        "statuses": ["waiting_for_input"],
        "waiting_elapsed_ms": waiting_elapsed_ms,
        "waiting_view": waiting,
        "waiting_project_record": dict(waiting_record),
        "waiting_execution_receipt": dict(progress),
        "waiting_sse_sequences": _sse_sequences(event_bytes),
        "waiting_sse_statuses": _sse_statuses(event_bytes),
        "waiting_sse_sha256": hashlib.sha256(event_bytes).hexdigest(),
        "browser_waiting": dict(browser_waiting),
    }


def finalize_browser_started_missing_data_journey(
    observation: Mapping[str, Any],
    *,
    frontend_url: str,
    browser_result: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Finalize the exact browser-resumed missing-data job without recreating it."""

    updated = json.loads(json.dumps(observation))
    job_id = updated.get("job_id")
    request_body = updated.get("request")
    idempotency_key = updated.get("idempotency_key")
    research_id = (
        request_body.get("research_id") if isinstance(request_body, Mapping) else None
    )
    if (
        not isinstance(job_id, str)
        or not isinstance(research_id, str)
        or not isinstance(request_body, Mapping)
        or not isinstance(idempotency_key, str)
    ):
        raise RuntimeError("missing-data waiting observation is incomplete")
    opener = _opener()
    session_status, session = _json_request(
        opener, f"{frontend_url}/api/d18/session"
    )
    expected_release_id = (
        updated.get("session_release", {}).get("release_id")
        if isinstance(updated.get("session_release"), Mapping)
        else None
    )
    if (
        session_status != 200
        or session.get("authenticated") is not True
        or session.get("release_id") != expected_release_id
    ):
        raise RuntimeError("missing-data resume release identity drifted")
    view_status, view = _json_request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}"
    )
    if (
        view_status != 200
        or view.get("project_id") != research_id
        or view.get("status") not in {"bounded", "completed"}
        or not isinstance(view.get("input_receipt"), Mapping)
    ):
        raise RuntimeError("browser-resumed missing-data job is unavailable")
    input_receipt = view["input_receipt"]
    browser_interaction = browser_result.get("interaction")
    if (
        not isinstance(browser_interaction, Mapping)
        or browser_interaction.get("contract")
        != "d18.browser_input_interaction/v1"
        or browser_interaction.get("interaction_kind")
        != "factual_data_availability"
        or browser_interaction.get("decision") != "unavailable"
        or browser_interaction.get("before_status") != "waiting_for_input"
        or browser_interaction.get("after_status") != view.get("status")
        or browser_interaction.get("input_id") != input_receipt.get("input_id")
        or browser_interaction.get("decision_id")
        != input_receipt.get("decision_id")
        or browser_interaction.get("input_receipt_sha256")
        != input_receipt.get("receipt_sha256")
        or isinstance(browser_interaction.get("submit_to_terminal_ms"), bool)
        or not isinstance(browser_interaction.get("submit_to_terminal_ms"), int)
        or browser_interaction["submit_to_terminal_ms"] < 0
    ):
        raise RuntimeError("missing-data browser interaction evidence is invalid")
    result_status, result = _json_request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/result"
    )
    if result_status != 200:
        raise RuntimeError("frontend did not return the missing-data result")
    event_status, event_bytes, event_headers = _request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/events", timeout=10
    )
    content_type = event_headers.get("Content-Type", event_headers.get("content-type", ""))
    if event_status != 200 or "text/event-stream" not in content_type:
        raise RuntimeError("frontend SSE stream is unavailable")

    retry_status, retry_created = _json_request(
        opener,
        f"{frontend_url}/api/d18/jobs",
        method="POST",
        body=request_body,
        headers={"Idempotency-Key": idempotency_key},
    )
    if retry_status != 202 or retry_created.get("job_id") != job_id:
        raise RuntimeError("frontend exact missing-data retry drifted")
    retry_result_status, retry_result = _json_request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/result"
    )
    retry_event_status, retry_event_bytes, _ = _request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/events", timeout=10
    )
    if retry_result_status != 200 or retry_event_status != 200:
        raise RuntimeError("frontend could not verify missing-data exact retry")
    waiting_sequences = updated.get("waiting_sse_sequences")
    if not isinstance(waiting_sequences, list) or not waiting_sequences:
        raise RuntimeError("missing-data waiting SSE cursor is unavailable")
    reconnect_status, reconnect_bytes, reconnect_headers = _request(
        opener,
        f"{frontend_url}/api/d18/jobs/{job_id}/events",
        headers={"Last-Event-ID": str(waiting_sequences[-1])},
        timeout=10,
    )
    reconnect_content_type = reconnect_headers.get(
        "Content-Type", reconnect_headers.get("content-type", "")
    )
    if reconnect_status != 200 or "text/event-stream" not in reconnect_content_type:
        raise RuntimeError("frontend SSE cursor recovery is unavailable")

    execution = result.get("executionReceipt")
    retry_execution = retry_result.get("executionReceipt")
    project_record = execution.get("project_record") if isinstance(execution, Mapping) else {}
    retry_project_record = (
        retry_execution.get("project_record")
        if isinstance(retry_execution, Mapping)
        else {}
    )
    elapsed = execution.get("elapsed_ms") if isinstance(execution, Mapping) else None
    final_elapsed_ms = (
        elapsed.get("request_to_user_delivery")
        if isinstance(elapsed, Mapping)
        else None
    )
    if isinstance(final_elapsed_ms, bool) or not isinstance(final_elapsed_ms, int):
        raise RuntimeError("missing-data final elapsed time is unavailable")
    statuses = list(updated.get("statuses") or [])
    if not statuses or statuses[-1] != str(view.get("status")):
        statuses.append(str(view.get("status")))
    updated.update(
        {
            "release_identities": [
                *list(updated.get("release_identities") or []),
                _release(view.get("runtime_release")),
                _release(result.get("runtime_release")),
                _release(retry_created.get("runtime_release")),
            ],
            "statuses": statuses,
            "wall_elapsed_ms": final_elapsed_ms,
            "resume_elapsed_ms": browser_interaction["submit_to_terminal_ms"],
            "supply": {
                "job_id": job_id,
                "input_receipt": dict(input_receipt),
            },
            "sse_sequences": _sse_sequences(event_bytes),
            "sse_sha256": hashlib.sha256(event_bytes).hexdigest(),
            "result_view": result,
            "browser_result": dict(browser_result),
            "reconnect": {
                "after_sequence": waiting_sequences[-1],
                "sequences": _sse_sequences(reconnect_bytes),
                "statuses": _sse_statuses(reconnect_bytes),
            },
            "retry": {
                "same_job_id": retry_created.get("job_id") == job_id,
                "idempotencyReplayEvidence": retry_created.get(
                    "idempotencyReplayEvidence"
                ),
                "result_sha256_before": hashlib.sha256(_canonical(result)).hexdigest(),
                "result_sha256_after": hashlib.sha256(_canonical(retry_result)).hexdigest(),
                "execution_receipt_sha256_before": (
                    execution.get("receipt_sha256")
                    if isinstance(execution, Mapping)
                    else None
                ),
                "execution_receipt_sha256_after": (
                    retry_execution.get("receipt_sha256")
                    if isinstance(retry_execution, Mapping)
                    else None
                ),
                "project_record_sha256_before": project_record.get("last_event_sha256"),
                "project_record_sha256_after": retry_project_record.get("last_event_sha256"),
                "project_record_event_count_before": project_record.get("event_count"),
                "project_record_event_count_after": retry_project_record.get("event_count"),
                "sse_sha256_before": hashlib.sha256(event_bytes).hexdigest(),
                "sse_sha256_after": hashlib.sha256(retry_event_bytes).hexdigest(),
            },
        }
    )
    receipt = evaluate_missing_data_journey(updated)
    return updated, receipt


def run_missing_data_to_waiting(
    *,
    frontend_url: str,
    backend_url: str,
    timeout_seconds: float = 30,
) -> dict[str, Any]:
    """Run Journey D to its public waiting boundary without auto-answering it."""

    opener = _opener()
    session_status, session = _json_request(opener, f"{frontend_url}/api/d18/session")
    if session_status != 200 or session.get("authenticated") is not True:
        raise RuntimeError("signed frontend session is unavailable")
    namespace = session.get("project_namespace")
    if not isinstance(namespace, str) or not namespace:
        raise RuntimeError("frontend project namespace is unavailable")
    health_status, health = _json_request(opener, f"{backend_url}/health/ready")
    if health_status != 200 or health.get("status") != "ready":
        raise RuntimeError("backend readiness is unavailable")
    backend_release = _release(health.get("release"))
    request_body = {
        "research_id": (
            f"{namespace}-readiness-missing-{backend_release['release_id'][:12]}"
        ),
        "messages": [{"role": "user", "content": MISSING_DATA_OBJECTIVE}],
    }
    idempotency_key = "readiness-d-" + hashlib.sha256(
        _canonical(request_body)
    ).hexdigest()[:24]
    started = time.monotonic()
    created_status, created = _json_request(
        opener,
        f"{frontend_url}/api/d18/jobs",
        method="POST",
        body=request_body,
        headers={"Idempotency-Key": idempotency_key},
    )
    if created_status != 202 or not isinstance(created.get("job_id"), str):
        raise RuntimeError("frontend did not accept missing-data readiness journey")
    job_id = created["job_id"]
    statuses = [str(created.get("status"))]
    waiting: dict[str, Any] = {}
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        view_status, waiting = _json_request(
            opener, f"{frontend_url}/api/d18/jobs/{job_id}"
        )
        if view_status != 200:
            raise RuntimeError("frontend could not read missing-data journey status")
        current = str(waiting.get("status"))
        if current != statuses[-1]:
            statuses.append(current)
        if current == "waiting_for_input":
            break
        if current in TERMINAL:
            raise RuntimeError(
                "missing-data journey terminated before asking for input: " + current
            )
        time.sleep(0.1)
    if waiting.get("status") != "waiting_for_input":
        raise RuntimeError("missing-data journey did not reach waiting_for_input")
    event_status, event_bytes, event_headers = _request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/events", timeout=10
    )
    content_type = event_headers.get("Content-Type", event_headers.get("content-type", ""))
    if event_status != 200 or "text/event-stream" not in content_type:
        raise RuntimeError("frontend SSE stream is unavailable")
    progress = waiting.get("progress_receipt")
    waiting_record = (
        progress.get("project_record") if isinstance(progress, Mapping) else None
    )
    if not isinstance(waiting_record, Mapping):
        raise RuntimeError("waiting Project Record receipt is unavailable")
    return {
        "contract": "d18.product_readiness_observation/v1",
        "journey": MISSING_DATA_JOURNEY,
        "session_release": {
            "release_id": session.get("release_id"),
            "frontend_build_id": session.get("frontend_build_id"),
        },
        "release_identities": [
            backend_release,
            _release(created.get("runtime_release")),
            _release(waiting.get("runtime_release")),
        ],
        "request": request_body,
        "idempotency_key": idempotency_key,
        "job_id": job_id,
        "statuses": statuses,
        "waiting_elapsed_ms": int((time.monotonic() - started) * 1000),
        "waiting_view": waiting,
        "waiting_project_record": dict(waiting_record),
        "waiting_execution_receipt": dict(progress),
        "waiting_sse_sequences": _sse_sequences(event_bytes),
        "waiting_sse_statuses": _sse_statuses(event_bytes),
        "waiting_sse_sha256": hashlib.sha256(event_bytes).hexdigest(),
    }


def resume_missing_data_journey(
    observation: Mapping[str, Any],
    *,
    frontend_url: str,
    browser_waiting: Mapping[str, Any],
    timeout_seconds: float = 90,
) -> dict[str, Any]:
    """Resume the exact waiting job and capture its public terminal/retry evidence."""

    updated = json.loads(json.dumps(observation))
    job_id = updated.get("job_id")
    request_body = updated.get("request")
    idempotency_key = updated.get("idempotency_key")
    if (
        not isinstance(job_id, str)
        or not isinstance(request_body, Mapping)
        or not isinstance(idempotency_key, str)
    ):
        raise RuntimeError("missing-data waiting observation is incomplete")
    opener = _opener()
    session_status, session = _json_request(
        opener, f"{frontend_url}/api/d18/session"
    )
    if session_status != 200 or session.get("authenticated") is not True:
        raise RuntimeError("signed frontend session is unavailable")
    expected_release_id = (
        updated.get("session_release", {}).get("release_id")
        if isinstance(updated.get("session_release"), Mapping)
        else None
    )
    if session.get("release_id") != expected_release_id:
        raise RuntimeError("missing-data resume release identity drifted")
    input_body = {
        "input_id": f"{job_id}:missing-data-unavailable",
        "kind": "decision",
        "payload": {
            "decision_id": f"{job_id}:availability",
            "decision": "unavailable",
        },
    }
    started = time.monotonic()
    supplied_status, supplied = _json_request(
        opener,
        f"{frontend_url}/api/d18/jobs/{job_id}/inputs",
        method="POST",
        body=input_body,
    )
    if supplied_status != 202 or supplied.get("job_id") != job_id:
        raise RuntimeError("frontend did not resume the same missing-data job")
    statuses = list(updated.get("statuses") or [])
    view: dict[str, Any] = {}
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        view_status, view = _json_request(
            opener, f"{frontend_url}/api/d18/jobs/{job_id}"
        )
        if view_status != 200:
            raise RuntimeError("frontend could not read resumed missing-data status")
        current = str(view.get("status"))
        if not statuses or current != statuses[-1]:
            statuses.append(current)
        if current in TERMINAL:
            break
        time.sleep(0.1)
    if view.get("status") not in {"bounded", "completed"}:
        raise RuntimeError(
            "missing-data journey did not produce an accepted terminal: "
            + str(view.get("status"))
        )
    if not isinstance(view.get("input_receipt"), Mapping):
        raise RuntimeError("missing-data decision receipt is unavailable")
    result_status, result = _json_request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/result"
    )
    if result_status != 200:
        raise RuntimeError("frontend did not return a missing-data result")
    event_status, event_bytes, event_headers = _request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/events", timeout=10
    )
    content_type = event_headers.get("Content-Type", event_headers.get("content-type", ""))
    if event_status != 200 or "text/event-stream" not in content_type:
        raise RuntimeError("frontend SSE stream is unavailable")
    retry_status, retry_created = _json_request(
        opener,
        f"{frontend_url}/api/d18/jobs",
        method="POST",
        body=request_body,
        headers={"Idempotency-Key": idempotency_key},
    )
    if retry_status != 202 or retry_created.get("job_id") != job_id:
        raise RuntimeError("frontend exact retry did not recover the same job")
    retry_result_status, retry_result = _json_request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/result"
    )
    retry_event_status, retry_event_bytes, _ = _request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/events", timeout=10
    )
    if retry_result_status != 200 or retry_event_status != 200:
        raise RuntimeError("frontend could not verify missing-data exact retry")
    waiting_sequences = updated.get("waiting_sse_sequences")
    if not isinstance(waiting_sequences, list) or not waiting_sequences:
        raise RuntimeError("missing-data waiting SSE cursor is unavailable")
    reconnect_status, reconnect_bytes, reconnect_headers = _request(
        opener,
        f"{frontend_url}/api/d18/jobs/{job_id}/events",
        headers={"Last-Event-ID": str(waiting_sequences[-1])},
        timeout=10,
    )
    reconnect_content_type = reconnect_headers.get(
        "Content-Type", reconnect_headers.get("content-type", "")
    )
    if reconnect_status != 200 or "text/event-stream" not in reconnect_content_type:
        raise RuntimeError("frontend SSE cursor recovery is unavailable")
    execution = result.get("executionReceipt")
    retry_execution = retry_result.get("executionReceipt")
    project_record = execution.get("project_record") if isinstance(execution, Mapping) else {}
    retry_project_record = (
        retry_execution.get("project_record")
        if isinstance(retry_execution, Mapping)
        else {}
    )
    elapsed = execution.get("elapsed_ms") if isinstance(execution, Mapping) else None
    final_elapsed_ms = (
        elapsed.get("request_to_user_delivery")
        if isinstance(elapsed, Mapping)
        else None
    )
    if isinstance(final_elapsed_ms, bool) or not isinstance(final_elapsed_ms, int):
        raise RuntimeError("missing-data final elapsed time is unavailable")
    updated.update(
        {
            "release_identities": [
                *list(updated.get("release_identities") or []),
                _release(supplied.get("runtime_release")),
                _release(view.get("runtime_release")),
                _release(result.get("runtime_release")),
                _release(retry_created.get("runtime_release")),
            ],
            "statuses": statuses,
            "wall_elapsed_ms": final_elapsed_ms,
            "supply": {
                "job_id": job_id,
                "input_receipt": dict(view["input_receipt"]),
            },
            "sse_sequences": _sse_sequences(event_bytes),
            "sse_sha256": hashlib.sha256(event_bytes).hexdigest(),
            "result_view": result,
            "browser_waiting": dict(browser_waiting),
            "reconnect": {
                "after_sequence": waiting_sequences[-1],
                "sequences": _sse_sequences(reconnect_bytes),
                "statuses": _sse_statuses(reconnect_bytes),
            },
            "retry": {
                "same_job_id": retry_created.get("job_id") == job_id,
                "idempotencyReplayEvidence": retry_created.get(
                    "idempotencyReplayEvidence"
                ),
                "result_sha256_before": hashlib.sha256(_canonical(result)).hexdigest(),
                "result_sha256_after": hashlib.sha256(_canonical(retry_result)).hexdigest(),
                "execution_receipt_sha256_before": (
                    execution.get("receipt_sha256")
                    if isinstance(execution, Mapping)
                    else None
                ),
                "execution_receipt_sha256_after": (
                    retry_execution.get("receipt_sha256")
                    if isinstance(retry_execution, Mapping)
                    else None
                ),
                "project_record_sha256_before": project_record.get("last_event_sha256"),
                "project_record_sha256_after": retry_project_record.get("last_event_sha256"),
                "project_record_event_count_before": project_record.get("event_count"),
                "project_record_event_count_after": retry_project_record.get("event_count"),
                "sse_sha256_before": hashlib.sha256(event_bytes).hexdigest(),
                "sse_sha256_after": hashlib.sha256(retry_event_bytes).hexdigest(),
            },
        }
    )
    return updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend-url", default="http://127.0.0.1:3000")
    parser.add_argument("--backend-url", default="http://127.0.0.1:8080")
    parser.add_argument(
        "--journey",
        choices=("direct", "material", "official", "missing", "native"),
        default="direct",
    )
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=30)
    parser.add_argument("--existing-observation", type=Path)
    parser.add_argument("--browser-observation", type=Path)
    parser.add_argument("--browser-waiting-observation", type=Path)
    parser.add_argument("--browser-capture", type=Path)
    parser.add_argument("--browser-waiting-capture", type=Path)
    parser.add_argument(
        "--missing-phase",
        choices=("waiting", "resume", "finalize"),
        default="waiting",
    )
    args = parser.parse_args(argv)
    output = args.output.resolve()
    try:
        browser_observation = _load_browser_argument(
            raw_path=args.browser_observation,
            capture_path=args.browser_capture,
        )
        browser_waiting_observation = _load_browser_argument(
            raw_path=args.browser_waiting_observation,
            capture_path=args.browser_waiting_capture,
        )
    except ValueError as error:
        parser.error(str(error))
    if args.journey == "missing":
        if args.missing_phase == "waiting":
            if (
                args.existing_observation is not None
                or browser_observation is not None
            ):
                parser.error(
                    "missing waiting phase accepts only an optional browser waiting observation"
                )
            if browser_waiting_observation is not None:
                observation = observe_browser_started_missing_data_waiting(
                    frontend_url=args.frontend_url.rstrip("/"),
                    backend_url=args.backend_url.rstrip("/"),
                    browser_waiting=browser_waiting_observation,
                )
            else:
                observation = run_missing_data_to_waiting(
                    frontend_url=args.frontend_url.rstrip("/"),
                    backend_url=args.backend_url.rstrip("/"),
                    timeout_seconds=args.timeout_seconds,
                )
            _write_json(output / "journey-d-waiting-observation.json", observation)
            print(
                json.dumps(
                    {
                        "status": (
                            "waiting_for_user_resume"
                            if browser_waiting_observation is not None
                            else "waiting_for_browser_observation"
                        ),
                        "job_id": observation["job_id"],
                        "research_id": observation["request"]["research_id"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        if args.existing_observation is None:
            parser.error("missing resume/finalize requires --existing-observation")
        observation = _read_json(args.existing_observation.resolve())
        if args.missing_phase == "resume":
            if browser_waiting_observation is None:
                parser.error("missing resume requires --browser-waiting-observation")
            observation = resume_missing_data_journey(
                observation,
                frontend_url=args.frontend_url.rstrip("/"),
                browser_waiting=browser_waiting_observation,
                timeout_seconds=args.timeout_seconds,
            )
            _write_json(output / "journey-d-api-observation.json", observation)
            print(
                json.dumps(
                    {
                        "status": "waiting_for_result_browser_observation",
                        "job_id": observation["job_id"],
                        "research_id": observation["request"]["research_id"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        if browser_observation is None:
            parser.error("missing finalize requires --browser-observation")
        browser_result = browser_observation
        if browser_waiting_observation is not None:
            observation["browser_waiting"] = browser_waiting_observation
        if observation.get("supply") is None:
            observation, receipt = finalize_browser_started_missing_data_journey(
                observation,
                frontend_url=args.frontend_url.rstrip("/"),
                browser_result=browser_result,
            )
        else:
            observation["browser_result"] = browser_result
            receipt = evaluate_missing_data_journey(observation)
        _write_json(output / "journey-d-observation.json", observation)
        _write_json(output / "journey-d-receipt.json", receipt)
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        return 0 if receipt["status"] == "passed" else 1
    if args.existing_observation is not None or browser_observation is not None:
        if (
            args.journey in {"direct", "material", "official", "native"}
            and args.existing_observation is None
            and browser_observation is not None
        ):
            finalize = {
                "direct": finalize_direct_browser_started_journey,
                "material": finalize_material_browser_started_journey,
                "official": finalize_official_browser_started_journey,
                "native": finalize_native_browser_started_journey,
            }[args.journey]
            observation, receipt = finalize(
                frontend_url=args.frontend_url.rstrip("/"),
                backend_url=args.backend_url.rstrip("/"),
                browser_view=browser_observation,
            )
            stem = {
                "direct": "journey-a",
                "material": "journey-b",
                "official": "journey-c",
                "native": "journey-e",
            }[args.journey]
            _write_json(output / f"{stem}-observation.json", observation)
            _write_json(output / f"{stem}-receipt.json", receipt)
            print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
            return 0 if receipt["status"] == "passed" else 1
        if (
            args.journey not in {"direct", "material", "official", "native"}
            or args.existing_observation is None
            or browser_observation is None
        ):
            parser.error(
                "browser finalization requires one browser-started product journey or both legacy evidence files"
            )
        observation = _read_json(args.existing_observation.resolve())
        if args.journey == "material":
            request = observation.get("request")
            material = observation.get("material")
            research_id = (
                request.get("research_id") if isinstance(request, Mapping) else None
            )
            material_id = (
                material.get("material_id") if isinstance(material, Mapping) else None
            )
            if not isinstance(research_id, str) or not isinstance(material_id, str):
                raise RuntimeError("material observation identity is unavailable")
            material_opener = _opener()
            session_status, session = _json_request(
                material_opener,
                f"{args.frontend_url.rstrip('/')}/api/d18/session",
            )
            namespace = (
                session.get("project_namespace")
                if isinstance(session, Mapping)
                else None
            )
            if (
                session_status != 200
                or session.get("authenticated") is not True
                or not isinstance(namespace, str)
                or not research_id.startswith(f"{namespace}-")
            ):
                raise RuntimeError("material observation is outside the active session")
            observation["material"] = _accepted_material_view(
                material_opener,
                args.frontend_url.rstrip("/"),
                research_id,
                material_id,
            )
            revision = observation["material"].get("current_revision")
            service_upload_to_ready_ms = (
                revision.get("upload_to_ready_ms")
                if isinstance(revision, Mapping)
                else None
            )
            if (
                isinstance(service_upload_to_ready_ms, bool)
                or not isinstance(service_upload_to_ready_ms, int)
                or service_upload_to_ready_ms < 0
            ):
                raise RuntimeError("material processing receipt is unavailable")
            observation["probe_upload_to_ready_ms"] = observation.get(
                "upload_to_ready_ms"
            )
            observation["upload_to_ready_ms"] = service_upload_to_ready_ms
        observation["browser_view"] = browser_observation
        evaluator = {
            "direct": evaluate_direct_journey,
            "material": evaluate_material_journey,
            "official": evaluate_official_journey,
            "native": evaluate_native_journey,
        }[args.journey]
        stem = {
            "direct": "journey-a",
            "material": "journey-b",
            "official": "journey-c",
            "native": "journey-e",
        }[args.journey]
        receipt = evaluator(observation)
        _write_json(output / f"{stem}-observation.json", observation)
        _write_json(output / f"{stem}-receipt.json", receipt)
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        return 0 if receipt["status"] == "passed" else 1
    if args.journey == "direct":
        observation, receipt = run_direct_journey(
            frontend_url=args.frontend_url.rstrip("/"),
            backend_url=args.backend_url.rstrip("/"),
            timeout_seconds=args.timeout_seconds,
        )
        stem = "journey-a"
    elif args.journey == "material":
        if args.source is None:
            parser.error("material journey requires --source")
        observation, receipt = run_material_journey(
            frontend_url=args.frontend_url.rstrip("/"),
            backend_url=args.backend_url.rstrip("/"),
            source=args.source.resolve(),
            timeout_seconds=args.timeout_seconds,
        )
        stem = "journey-b"
    elif args.journey == "official":
        observation, receipt = run_official_journey(
            frontend_url=args.frontend_url.rstrip("/"),
            backend_url=args.backend_url.rstrip("/"),
            timeout_seconds=args.timeout_seconds,
        )
        stem = "journey-c"
    else:
        observation, receipt = run_native_journey(
            frontend_url=args.frontend_url.rstrip("/"),
            backend_url=args.backend_url.rstrip("/"),
            timeout_seconds=args.timeout_seconds,
        )
        stem = "journey-e"
    _write_json(output / f"{stem}-observation.json", observation)
    _write_json(output / f"{stem}-receipt.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
