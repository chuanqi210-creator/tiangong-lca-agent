#!/usr/bin/env python3
"""Submit and audit one real LCA intake through the signed frontend BFF."""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


CONTRACT = "lca_research_agent.frontend_real_pilot_receipt/v1"
TERMINAL = {"completed", "bounded", "failed", "cancelled"}
MATERIAL_TEXT = """产品：镀锌钢板天花板检修口。
已知规格和净重：最小规格 200 × 200 mm，净重 0.65 kg；最大规格 600 × 600 mm，净重 4.2 kg。
当前未提供镀锌钢板供应商、钢材生产路线、材料成分、生产能耗、损耗率、包装、运输距离、使用阶段、报废路线或任何排放因子。
这些内容均是用户提供的候选项目事实，不是 TianGong 官方数据。"""
OBJECTIVE = (
    "请判断现有材料是否足以启动一项筛查级产品碳足迹研究，并给出建议的研究目标、"
    "功能单位、系统边界和真正阻断结论的关键数据缺口。请明确区分用户已提供事实、"
    "建议的方法选择和仍缺失的数据；不得补造钢材排放因子、供应商数据、运输距离、"
    "能源数据或最终产品碳足迹数值。"
)


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical(value)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def build_pilot_request(namespace: str) -> dict[str, Any]:
    if not namespace or not namespace.replace("-", "").isalnum():
        raise ValueError("pilot namespace is invalid")
    material_sha = hashlib.sha256(MATERIAL_TEXT.encode("utf-8")).hexdigest()
    return {
        "research_id": f"{namespace}-ceiling-access-screening-v1",
        "messages": [{"role": "user", "content": OBJECTIVE}],
        "source_materials": [
            {
                "name": "ceiling-access-panel-known-facts.zh-CN.txt",
                "media_type": "text/plain",
                "sha256": material_sha,
                "authority": "user_supplied_project_input",
                "content": {"text": MATERIAL_TEXT},
            }
        ],
    }


def summarize_run_trace(path: Path) -> dict[str, Any]:
    model_rows: list[Mapping[str, Any]] = []
    tool_call_count = 0
    event_count = 0
    execution_started_at: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if not isinstance(event, Mapping):
            raise ValueError("Run Trace event is invalid")
        event_count += 1
        event_type = event.get("event_type")
        payload = event.get("payload")
        if event_type == "research.run.answer.requested" and isinstance(
            payload, Mapping
        ):
            entered_at = payload.get("entered_at")
            if isinstance(entered_at, str):
                datetime.fromisoformat(entered_at.replace("Z", "+00:00"))
                execution_started_at.append(entered_at)
        if event_type == "research.run.model.completed" and isinstance(
            payload, Mapping
        ):
            model_rows.append(payload)
        if event_type in {
            "research.run.tool.completed",
            "research.run.capability.completed",
        }:
            tool_call_count += 1
    providers = sorted(
        {str(row["provider"]) for row in model_rows if row.get("provider")}
    )
    models = sorted({str(row["model"]) for row in model_rows if row.get("model")})
    total_tokens = sum(
        int(row.get("usage", {}).get("total_tokens", 0))
        for row in model_rows
        if isinstance(row.get("usage"), Mapping)
    )
    return {
        "provider": providers[0] if len(providers) == 1 else providers,
        "model": models[0] if len(models) == 1 else models,
        "model_call_count": len(model_rows),
        "tool_call_count": tool_call_count,
        "total_tokens": total_tokens,
        "event_count": event_count,
        "trace_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "execution_started_at": min(execution_started_at)
        if execution_started_at
        else None,
    }


def _aggregate_traces(trace_directory: Path) -> dict[str, Any]:
    summaries = [summarize_run_trace(path) for path in sorted(trace_directory.glob("*.jsonl"))]
    providers: set[str] = set()
    models: set[str] = set()
    for summary in summaries:
        provider = summary["provider"]
        model = summary["model"]
        providers.update(provider if isinstance(provider, list) else ([provider] if provider else []))
        models.update(model if isinstance(model, list) else ([model] if model else []))
    started = sorted(
        str(row["execution_started_at"])
        for row in summaries
        if row.get("execution_started_at")
    )
    return {
        "provider": next(iter(providers)) if len(providers) == 1 else sorted(providers),
        "model": next(iter(models)) if len(models) == 1 else sorted(models),
        "model_call_count": sum(row["model_call_count"] for row in summaries),
        "tool_call_count": sum(row["tool_call_count"] for row in summaries),
        "total_tokens": sum(row["total_tokens"] for row in summaries),
        "event_count": sum(row["event_count"] for row in summaries),
        "trace_file_sha256s": [row["trace_sha256"] for row in summaries],
        "execution_started_at": started[0] if started else None,
    }


def derive_effect_evidence(
    project_record_path: Path, trace_directory: Path
) -> dict[str, Any]:
    """Derive zero-effect evidence from accepted Project Record and Run Trace bytes."""

    project_record_path = Path(project_record_path)
    try:
        project = json.loads(project_record_path.read_text(encoding="utf-8"))
        result = project["result"]
        permission = result["permission_effect_receipt"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError("accepted Project Record effect receipt is unavailable") from error
    if permission != {
        "contract": "lca_research_kernel.formal_result_effect_receipt/v1",
        "external_writes": 0,
        "forbidden_effects": 0,
    }:
        raise ValueError("accepted Project Record reports a forbidden effect")

    trace_hashes: list[str] = []
    for path in sorted(Path(trace_directory).glob("*.jsonl")):
        trace_hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError as error:
                raise ValueError("Run Trace effect evidence is invalid") from error
            if not isinstance(event, Mapping):
                raise ValueError("Run Trace effect evidence is invalid")
            event_type = event.get("event_type")
            payload = event.get("payload")
            if not isinstance(payload, Mapping):
                continue
            effect = payload.get("effect")
            if effect in {"external_write", "irreversible"}:
                raise ValueError("Run Trace records a forbidden effect")
            authorization = payload.get("network_authorization")
            if isinstance(authorization, Mapping) and authorization.get(
                "operation"
            ) not in {None, "external_read"}:
                raise ValueError("Run Trace records a forbidden effect")
            if isinstance(event_type, str) and any(
                marker in event_type
                for marker in (
                    ".publication.",
                    ".database_write.",
                    ".external_message.",
                    ".irreversible.",
                )
            ):
                raise ValueError("Run Trace records a forbidden effect")
    if not trace_hashes:
        raise ValueError("Run Trace effect evidence is unavailable")
    return {
        "contract": "lca_research_agent.frontend_real_pilot_effect_evidence/v1",
        "project_record_sha256": hashlib.sha256(
            project_record_path.read_bytes()
        ).hexdigest(),
        "run_trace_sha256s": trace_hashes,
        "external_write_count": 0,
        "forbidden_effect_count": 0,
    }


def project_record_ref_for_receipt(
    *,
    data_root: Path,
    project_record_path: Path,
    upstream_reference: object,
) -> dict[str, str]:
    """Bind the receipt to local Project Record bytes hidden from public views."""

    root = Path(data_root).resolve()
    path = Path(project_record_path).resolve()
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise ValueError("Project Record escaped the pilot data root") from error
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if upstream_reference is not None:
        if (
            not isinstance(upstream_reference, Mapping)
            or upstream_reference.get("sha256") != digest
        ):
            raise ValueError("public Project Record reference drifted")
    return {
        "path": relative.as_posix(),
        "sha256": digest,
        "role": "project_record_projection",
    }


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )


def _request(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    method: str = "GET",
    body: object | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 30.0,
) -> tuple[int, bytes, Mapping[str, str]]:
    payload = None if body is None else _canonical(body)
    request = urllib.request.Request(url, data=payload, method=method)
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read(), dict(response.headers.items())
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers.items())


def _json_request(*args: object, **kwargs: object) -> tuple[int, dict[str, Any]]:
    status, payload, _ = _request(*args, **kwargs)  # type: ignore[arg-type]
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as error:
        raise RuntimeError("frontend returned a non-JSON response") from error
    if not isinstance(value, dict):
        raise RuntimeError("frontend returned an invalid JSON response")
    return status, value


def _human_text(result: Mapping[str, Any]) -> str:
    accepted = result.get("result")
    if isinstance(accepted, Mapping):
        for key in ("answer", "bounded_conclusion"):
            if isinstance(accepted.get(key), str) and accepted[key].strip():
                return str(accepted[key]).strip()
        delivery = accepted.get("human_delivery")
        if isinstance(delivery, Mapping):
            for key in ("answer", "summary", "content"):
                if isinstance(delivery.get(key), str) and delivery[key].strip():
                    return str(delivery[key]).strip()
    return "Agent 已形成 Project Record 接纳结果；详细机器结果见 pilot-receipt.json。"


def run_probe(
    *,
    frontend_url: str,
    data_root: Path,
    output_directory: Path,
    recover_job_id: str | None = None,
    timeout_seconds: float = 600.0,
) -> dict[str, Any]:
    started_wall = time.time()
    started = time.monotonic()
    opener = _opener()
    status, session = _json_request(opener, f"{frontend_url}/api/d18/session")
    if status != 200 or session.get("authenticated") is not True:
        raise RuntimeError("frontend session was not established")
    namespace = session.get("project_namespace")
    if not isinstance(namespace, str):
        raise RuntimeError("frontend session omitted project namespace")
    request_body = build_pilot_request(namespace)
    research_id = request_body["research_id"]
    output_directory = Path(output_directory).resolve()
    _write_json(output_directory / "pilot-request.json", request_body)

    if recover_job_id is None:
        idempotency = "pilot-" + hashlib.sha256(_canonical(request_body)).hexdigest()[:24]
        created_status, created = _json_request(
            opener,
            f"{frontend_url}/api/d18/jobs",
            method="POST",
            body=request_body,
            headers={"Idempotency-Key": idempotency},
        )
        if created_status != 202 or not isinstance(created.get("job_id"), str):
            raise RuntimeError("frontend did not accept the pilot job")
        job_id = created["job_id"]
    else:
        job_id = recover_job_id

    deadline = time.monotonic() + timeout_seconds
    supplied = False
    view: dict[str, Any] = {}
    while time.monotonic() < deadline:
        job_status, view = _json_request(
            opener, f"{frontend_url}/api/d18/jobs/{job_id}"
        )
        if job_status != 200:
            raise RuntimeError("frontend could not read pilot status")
        current = view.get("status")
        if current == "waiting_for_input" and not supplied:
            supplied_status, _ = _json_request(
                opener,
                f"{frontend_url}/api/d18/jobs/{job_id}/inputs",
                method="POST",
                body={
                    "input_id": "pilot-data-unavailable-v1",
                    "kind": "decision",
                    "payload": {
                        "decision_id": "missing-data-availability",
                        "decision": "当前无法补充这些缺失数据",
                    },
                },
            )
            if supplied_status != 202:
                raise RuntimeError("frontend did not accept the missing-data response")
            supplied = True
        elif current in TERMINAL:
            break
        time.sleep(0.25)
    else:
        raise RuntimeError("pilot did not reach a bounded terminal state")
    if view.get("status") not in {"completed", "bounded"}:
        raise RuntimeError(f"pilot ended without an accepted result: {view.get('status')}")

    result_status, result = _json_request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/result"
    )
    if result_status != 200 or result.get("status") != "accepted":
        raise RuntimeError("frontend did not return the accepted Project Record result")
    event_status, event_bytes, event_headers = _request(
        opener, f"{frontend_url}/api/d18/jobs/{job_id}/events"
    )
    if event_status != 200 or "text/event-stream" not in event_headers.get(
        "Content-Type", event_headers.get("content-type", "")
    ):
        raise RuntimeError("frontend event stream is unavailable")

    project_root = Path(data_root).resolve() / "projects" / research_id
    trace_summary = _aggregate_traces(project_root / "run-traces")
    project_record_path = project_root / "project-record.json"
    effect_evidence = derive_effect_evidence(
        project_record_path, project_root / "run-traces"
    )
    project_record_ref = project_record_ref_for_receipt(
        data_root=data_root,
        project_record_path=project_record_path,
        upstream_reference=result.get("project_record_ref"),
    )
    receipt = {
        "contract": CONTRACT,
        "status": "accepted",
        "research_id": research_id,
        "job_id": job_id,
        "job_status": view["status"],
        "request_sha256": hashlib.sha256(_canonical(request_body)).hexdigest(),
        "project_record_ref": project_record_ref,
        "run_trace": trace_summary,
        "effect_evidence": effect_evidence,
        "supplemental_input_supplied": supplied,
        "recovery_only": recover_job_id is not None,
        "event_stream_sha256": hashlib.sha256(event_bytes).hexdigest(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "observation_started_at_unix": round(started_wall, 3),
    }
    receipt["receipt_sha256"] = hashlib.sha256(_canonical(receipt)).hexdigest()
    _write_json(output_directory / "pilot-receipt.json", receipt)
    human = _human_text(result)
    (output_directory / "human-result.zh-CN.md").write_text(
        "# 首次网页辅助 LCA 试运行结果\n\n" + human + "\n",
        encoding="utf-8",
    )
    manifest = {
        "contract": "lca_research_agent.frontend_real_pilot_evidence_manifest/v1",
        "files": [
            {
                "path": path.relative_to(output_directory).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in sorted(output_directory.rglob("*"))
            if path.is_file() and path.name != "evidence-manifest.json"
        ],
    }
    manifest["manifest_sha256"] = hashlib.sha256(_canonical(manifest)).hexdigest()
    _write_json(output_directory / "evidence-manifest.json", manifest)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend-url", default="http://127.0.0.1:3000")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--recover-job-id")
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    args = parser.parse_args(argv)
    receipt = run_probe(
        frontend_url=args.frontend_url.rstrip("/"),
        data_root=args.data_root,
        output_directory=args.output,
        recover_job_id=args.recover_job_id,
        timeout_seconds=args.timeout_seconds,
    )
    print(
        json.dumps(
            {
                "contract": receipt["contract"],
                "status": receipt["status"],
                "research_id": receipt["research_id"],
                "job_id": receipt["job_id"],
                "provider": receipt["run_trace"]["provider"],
                "model_call_count": receipt["run_trace"]["model_call_count"],
                "receipt_sha256": receipt["receipt_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
