#!/usr/bin/env python3
"""Capture Product Readiness DOM and screenshot evidence through Ego Lite."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_service.product_readiness_gate import (
    MATERIAL_SOURCE_SHA256,
    PRODUCT_READINESS_JOURNEYS,
    browser_capture_authentication_message,
    canonical_sha256,
)
from lca_research_service.native_evidence_authentication import (
    open_native_evidence_authentication_ports,
)


def _read(path: Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"immutable browser evidence drifted: {path}")
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise ValueError(f"immutable browser evidence drifted: {path}")
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _release_id(observation: Mapping[str, object]) -> str:
    identities = observation.get("release_identities")
    release_ids = {
        row.get("release_id")
        for row in identities
        if isinstance(row, Mapping) and isinstance(row.get("release_id"), str)
    } if isinstance(identities, list) else set()
    if len(release_ids) != 1:
        raise ValueError("browser capture expected release is ambiguous")
    return str(next(iter(release_ids)))


def build_capture_identity_observation(
    *,
    journey: str,
    release_id: str,
    research_id: str,
    job_id: str,
) -> dict[str, object]:
    """Build the minimum journey identity needed before its final observation exists."""

    if journey not in PRODUCT_READINESS_JOURNEYS:
        raise ValueError("browser capture journey is invalid")
    if (
        len(release_id) != 64
        or any(character not in "0123456789abcdef" for character in release_id)
    ):
        raise ValueError("browser capture release identity is invalid")
    if not research_id or not job_id:
        raise ValueError("browser capture journey identity is absent")
    return {
        "contract": "d18.product_readiness_observation/v1",
        "journey": journey,
        "job_id": job_id,
        "release_identities": [
            {
                "contract": "d18.runtime_release_identity/v1",
                "release_id": release_id,
            }
        ],
        "request": {"research_id": research_id},
    }


def build_browser_capture(
    observation: Mapping[str, object],
    *,
    stage: str,
    observed: Mapping[str, object],
    screenshot_bytes: bytes,
) -> dict[str, object]:
    """Bind one actual browser DOM/screenshot to an existing product journey."""

    if stage not in {"waiting", "result"}:
        raise ValueError("browser capture stage is invalid")
    request = observation.get("request")
    research_id = request.get("research_id") if isinstance(request, Mapping) else None
    job_id = observation.get("job_id")
    release_id = _release_id(observation)
    page_url = observed.get("page_url")
    page_path = urlsplit(page_url).path if isinstance(page_url, str) else None
    if (
        observed.get("release_id") != release_id
        or observed.get("research_id") != research_id
        or observed.get("job_id") != job_id
        or page_path != f"/research/{research_id}"
    ):
        raise ValueError("browser capture identity drifted")
    visible_text = observed.get("visible_text")
    links = observed.get("links")
    runtime = observed.get("browser_runtime")
    if (
        not isinstance(visible_text, str)
        or not visible_text.strip()
        or not isinstance(links, list)
        or any(not isinstance(link, str) for link in links)
        or len(links) != len(set(links))
        or not isinstance(runtime, Mapping)
        or runtime.get("document_ready_state") != "complete"
        or not isinstance(runtime.get("user_agent"), str)
        or not runtime["user_agent"].strip()
        or not isinstance(runtime.get("title"), str)
        or not runtime["title"].strip()
        or isinstance(runtime.get("viewport_width"), bool)
        or not isinstance(runtime.get("viewport_width"), int)
        or runtime["viewport_width"] <= 0
        or isinstance(runtime.get("viewport_height"), bool)
        or not isinstance(runtime.get("viewport_height"), int)
        or runtime["viewport_height"] <= 0
        or not isinstance(screenshot_bytes, bytes)
        or not screenshot_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    ):
        raise ValueError("browser capture payload is invalid")
    browser_view = {
        "contract": "d18.browser_product_observation/v1",
        "release_id": release_id,
        "research_id": research_id,
        "job_id": job_id,
        "page_url": page_url,
        "visible_text": visible_text,
        "links": links,
    }
    upload_to_ready_ms = observed.get("upload_to_ready_ms")
    if upload_to_ready_ms is not None:
        if (
            observation.get("journey") != "medium_material_explanation"
            or stage != "result"
            or isinstance(upload_to_ready_ms, bool)
            or not isinstance(upload_to_ready_ms, int)
            or upload_to_ready_ms < 0
        ):
            raise ValueError("browser material timing payload is invalid")
        browser_view["upload_to_ready_ms"] = upload_to_ready_ms
    interaction = observed.get("interaction")
    if interaction is not None:
        if (
            stage != "result"
            or not isinstance(interaction, Mapping)
            or interaction.get("contract")
            != "d18.browser_input_interaction/v1"
            or interaction.get("interaction_kind")
            != "factual_data_availability"
            or interaction.get("decision") != "unavailable"
            or interaction.get("before_status") != "waiting_for_input"
            or interaction.get("after_status") not in {"bounded", "completed"}
            or any(
                not isinstance(interaction.get(key), str)
                or not interaction[key]
                for key in ("input_id", "decision_id")
            )
            or not isinstance(interaction.get("input_receipt_sha256"), str)
            or len(interaction["input_receipt_sha256"]) != 64
            or any(
                character not in "0123456789abcdef"
                for character in interaction["input_receipt_sha256"]
            )
            or isinstance(interaction.get("submit_to_terminal_ms"), bool)
            or not isinstance(interaction.get("submit_to_terminal_ms"), int)
            or interaction["submit_to_terminal_ms"] < 0
        ):
            raise ValueError("browser interaction payload is invalid")
        browser_view["interaction"] = dict(interaction)
    dom_snapshot = {
        "visible_text": visible_text,
        "links": links,
        "page_url": page_url,
        "title": runtime["title"],
    }
    capture: dict[str, object] = {
        "contract": "d18.controlled_browser_capture/v1",
        "harness": "ego-lite/cdp",
        "capture_method": "runtime_dom_and_png",
        "captured_at": observed.get("captured_at"),
        "journey": observation.get("journey"),
        "stage": stage,
        "release_id": release_id,
        "research_id": research_id,
        "job_id": job_id,
        "page_url": page_url,
        "browser_view": browser_view,
        "dom_visible_text_sha256": canonical_sha256(visible_text),
        "dom_links_sha256": canonical_sha256(links),
        "dom_snapshot_sha256": canonical_sha256(dom_snapshot),
        "browser_runtime": dict(runtime),
        "screenshot": {
            "media_type": "image/png",
            "byte_size": len(screenshot_bytes),
            "sha256": hashlib.sha256(screenshot_bytes).hexdigest(),
        },
    }
    capture["capture_sha256"] = canonical_sha256(capture)
    return capture


def authenticate_browser_capture(
    capture: Mapping[str, object],
    *,
    gate_run_id: str,
    signer: Callable[[bytes], str],
) -> dict[str, object]:
    if not isinstance(gate_run_id, str) or not gate_run_id:
        raise ValueError("browser gate run identity is absent")
    evidence = {
        key: value
        for key, value in capture.items()
        if key not in {"authentication", "capture_sha256"}
    }
    evidence_sha256 = canonical_sha256(evidence)
    message = browser_capture_authentication_message(
        evidence_sha256=evidence_sha256,
        gate_run_id=gate_run_id,
    )
    authenticated = dict(evidence)
    authenticated["authentication"] = {
        "contract": "d18.browser_capture_authentication/v1",
        "algorithm": "hmac-sha256",
        "gate_run_id": gate_run_id,
        "evidence_sha256": evidence_sha256,
        "tag": signer(message),
    }
    authenticated["capture_sha256"] = canonical_sha256(authenticated)
    return authenticated


def publish_browser_capture(
    observation: Mapping[str, object],
    *,
    stage: str,
    observed: Mapping[str, object],
    screenshot_bytes: bytes,
    output_root: Path,
    gate_run_id: str | None = None,
    signer: Callable[[bytes], str] | None = None,
) -> dict[str, object]:
    capture = build_browser_capture(
        observation,
        stage=stage,
        observed=observed,
        screenshot_bytes=screenshot_bytes,
    )
    if gate_run_id is not None or signer is not None:
        if gate_run_id is None or signer is None:
            raise ValueError("browser capture authentication is incomplete")
        capture = authenticate_browser_capture(
            capture,
            gate_run_id=gate_run_id,
            signer=signer,
        )
    identity = str(capture["capture_sha256"])
    directory = (
        Path(output_root).resolve()
        / str(capture["release_id"])
        / f"{capture['journey']}-{stage}"
    )
    capture_path = directory / f"{identity}.json"
    screenshot_path = directory / f"{identity}.png"
    capture_bytes = (
        json.dumps(capture, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    _write_once(screenshot_path, screenshot_bytes)
    _write_once(capture_path, capture_bytes)
    return {
        **capture,
        "capture_path": capture_path.as_posix(),
        "capture_file_sha256": hashlib.sha256(capture_bytes).hexdigest(),
        "screenshot_path": screenshot_path.as_posix(),
    }


def _parse_ego_capture_output(stdout: str) -> tuple[dict[str, object], bytes]:
    observed_marker = "D18_BROWSER_OBSERVED:"
    screenshot_marker = "D18_BROWSER_PNG:"
    observed_lines = [
        line.split(observed_marker, 1)[1]
        for line in stdout.splitlines()
        if observed_marker in line
    ]
    if len(observed_lines) != 1:
        raise RuntimeError("controlled browser observation output is ambiguous")
    try:
        observed = json.loads(observed_lines[0])
    except json.JSONDecodeError as error:
        raise RuntimeError("controlled browser observation output is invalid") from error
    if not isinstance(observed, dict):
        raise RuntimeError("controlled browser observation output is invalid")

    chunks: dict[int, str] = {}
    expected_count: int | None = None
    for line in stdout.splitlines():
        if screenshot_marker not in line:
            continue
        fields = line.split(screenshot_marker, 1)[1].split(":", 2)
        if len(fields) != 3:
            raise RuntimeError("controlled browser screenshot output is invalid")
        try:
            index = int(fields[0])
            count = int(fields[1])
        except ValueError as error:
            raise RuntimeError("controlled browser screenshot output is invalid") from error
        if count <= 0 or index < 0 or index >= count:
            raise RuntimeError("controlled browser screenshot output is invalid")
        if expected_count is None:
            expected_count = count
        if count != expected_count or index in chunks:
            raise RuntimeError("controlled browser screenshot output is ambiguous")
        chunks[index] = fields[2]
    if expected_count is None or set(chunks) != set(range(expected_count)):
        raise RuntimeError("controlled browser screenshot output is incomplete")
    try:
        screenshot = base64.b64decode(
            "".join(chunks[index] for index in range(expected_count)),
            validate=True,
        )
    except (ValueError, base64.binascii.Error) as error:
        raise RuntimeError("controlled browser screenshot output is invalid") from error
    return observed, screenshot


def run_ego_capture(
    observation: Mapping[str, object],
    *,
    frontend_url: str,
    task_space: str,
    timeout_seconds: float,
    interaction: str | None = None,
) -> tuple[dict[str, object], bytes]:
    request = observation.get("request")
    research_id = request.get("research_id") if isinstance(request, Mapping) else None
    job_id = observation.get("job_id")
    if not isinstance(research_id, str) or not isinstance(job_id, str):
        raise ValueError("browser capture journey identity is absent")
    if interaction not in {None, "missing-data-unavailable"}:
        raise ValueError("controlled browser interaction is invalid")
    page_url = f"{frontend_url.rstrip('/')}/research/{research_id}"
    material_timing_script = "const uploadToReadyMs = null"
    if observation.get("journey") == "medium_material_explanation":
        material_timing_script = f"""
const materialsResponse = await fetch(
  '/api/d18/research/{research_id}/materials', {{cache: 'no-store'}}
)
const materialsPayload = await materialsResponse.json()
const timedMaterials = Array.isArray(materialsPayload.materials)
  ? materialsPayload.materials.filter(row =>
      row.current_revision
      && row.current_revision.input_artifact_id === '{MATERIAL_SOURCE_SHA256}'
      && row.current_revision.processing_status === 'ready'
    )
  : []
if (materialsResponse.status !== 200 || timedMaterials.length !== 1) {{
  throw new Error('material browser timing source is unavailable')
}}
const uploadToReadyMs = timedMaterials[0].current_revision.upload_to_ready_ms
if (!Number.isInteger(uploadToReadyMs) || uploadToReadyMs < 0) {{
  throw new Error('material browser timing is unavailable')
}}
"""
    interaction_script = "const browserInteraction = null"
    if interaction == "missing-data-unavailable":
        interaction_script = f"""
const beforeInteraction = await js(String.raw`(async () => {{
  const response = await fetch('/api/d18/jobs/{job_id}', {{cache: 'no-store'}})
  return await response.json()
}})()`)
if (beforeInteraction.status !== 'waiting_for_input') {{
  throw new Error('missing-data browser interaction is not at waiting boundary')
}}
await waitForElement('[aria-label="d18-decision-unavailable"]', {{timeout: 30}})
const interactionStartedAt = Date.now()
await click('[aria-label="d18-decision-unavailable"]', {{label: '提交缺失数据决定'}})
let afterInteraction = null
for (let attempt = 0; attempt < 450; attempt++) {{
  afterInteraction = await js(String.raw`(async () => {{
    const response = await fetch('/api/d18/jobs/{job_id}', {{cache: 'no-store'}})
    return await response.json()
  }})()`)
  if (['bounded', 'completed', 'failed', 'cancelled'].includes(afterInteraction.status)) {{
    break
  }}
  await wait(0.2)
}}
if (!afterInteraction || !['bounded', 'completed'].includes(afterInteraction.status)) {{
  throw new Error('missing-data browser interaction did not reach accepted terminal')
}}
const interactionReceipt = afterInteraction.input_receipt
if (!interactionReceipt || interactionReceipt.decision !== 'unavailable') {{
  throw new Error('missing-data browser interaction receipt is unavailable')
}}
const browserInteraction = {{
  contract: 'd18.browser_input_interaction/v1',
  interaction_kind: 'factual_data_availability',
  decision: 'unavailable',
  before_status: beforeInteraction.status,
  after_status: afterInteraction.status,
  input_id: interactionReceipt.input_id,
  decision_id: interactionReceipt.decision_id,
  input_receipt_sha256: interactionReceipt.receipt_sha256,
  submit_to_terminal_ms: Date.now() - interactionStartedAt,
}}
await wait(2)
"""
    script = f"""
const task = await useOrCreateTaskSpace({json.dumps(task_space)})
await openOrReuseTab({json.dumps(page_url)}, {{ wait: true, timeout: 30 }})
await waitForLoad({{ timeout: 30 }})
await wait(2)
{interaction_script}
const observed = await js(String.raw`(async () => {{
  const sessionResponse = await fetch('/api/d18/session', {{cache: 'no-store'}})
  const jobResponse = await fetch('/api/d18/jobs/{job_id}', {{cache: 'no-store'}})
  {material_timing_script}
  const session = await sessionResponse.json()
  const job = await jobResponse.json()
  const value = {{
    page_url: location.href,
    visible_text: document.body.innerText,
    links: [...new Set([...document.querySelectorAll('a[href]')].map(a => a.href))].sort(),
    release_id: session.release_id,
    research_id: job.project_id,
    job_id: job.job_id,
    captured_at: new Date().toISOString(),
    browser_runtime: {{
      user_agent: navigator.userAgent,
      document_ready_state: document.readyState,
      viewport_width: window.innerWidth,
      viewport_height: window.innerHeight,
      title: document.title,
    }},
  }}
  if (uploadToReadyMs !== null) value.upload_to_ready_ms = uploadToReadyMs
  return value
}})()`)
if (browserInteraction !== null) observed.interaction = browserInteraction
const screenshot = await cdp('Page.captureScreenshot', {{
  format: 'png',
  captureBeyondViewport: false,
}})
cliLog('D18_BROWSER_OBSERVED:' + JSON.stringify(observed))
const pngChunkSize = 12000
const pngChunkCount = Math.ceil(screenshot.data.length / pngChunkSize)
for (let index = 0; index < pngChunkCount; index++) {{
  cliLog(
    'D18_BROWSER_PNG:' + index + ':' + pngChunkCount + ':'
    + screenshot.data.slice(index * pngChunkSize, (index + 1) * pngChunkSize)
  )
}}
"""
    completed = subprocess.run(
        ["ego-browser", "nodejs"],
        input=script,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "controlled browser capture failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    return _parse_ego_capture_output(
        "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observation", type=Path)
    parser.add_argument("--journey", choices=sorted(PRODUCT_READINESS_JOURNEYS))
    parser.add_argument("--release-id")
    parser.add_argument("--research-id")
    parser.add_argument("--job-id")
    parser.add_argument("--stage", choices=("waiting", "result"), required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--frontend-url", default="http://127.0.0.1:3000")
    parser.add_argument("--task-space", default="1")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument(
        "--interaction",
        choices=("missing-data-unavailable",),
        help="Perform and bind one supported browser decision before result capture.",
    )
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--gate-run-id", required=True)
    args = parser.parse_args()
    identity_values = (args.journey, args.release_id, args.research_id, args.job_id)
    if args.observation is not None:
        if any(value is not None for value in identity_values):
            parser.error("--observation cannot be combined with journey identity arguments")
        observation = _read(args.observation.resolve())
    else:
        if any(value is None for value in identity_values):
            parser.error(
                "provide --observation or all of --journey, --release-id, "
                "--research-id, and --job-id"
            )
        observation = build_capture_identity_observation(
            journey=args.journey,
            release_id=args.release_id,
            research_id=args.research_id,
            job_id=args.job_id,
        )
    observed, screenshot_bytes = run_ego_capture(
        observation,
        frontend_url=args.frontend_url,
        task_space=args.task_space,
        timeout_seconds=args.timeout_seconds,
        interaction=args.interaction,
    )
    authentication = open_native_evidence_authentication_ports(
        args.data_root.resolve()
    )
    result = publish_browser_capture(
        observation,
        stage=args.stage,
        observed=observed,
        screenshot_bytes=screenshot_bytes,
        output_root=args.output_root,
        gate_run_id=args.gate_run_id,
        signer=authentication.sign,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
