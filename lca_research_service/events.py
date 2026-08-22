"""Safe OpenAI-compatible SSE projections."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

from .contracts import validate_content_ref
from .openai_compat import FINISH_REASONS, normalize_usage


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _frame(payload: Mapping[str, Any]) -> str:
    return "data: " + json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n\n"


def _event_frame(
    payload: Mapping[str, Any],
    *,
    event: str,
    event_id: int | None = None,
) -> str:
    prefix = ""
    if event_id is not None:
        prefix += f"id: {event_id}\n"
    prefix += f"event: {event}\n"
    return prefix + _frame(payload)


def render_completion_sse(
    *,
    completion_id: str,
    model: str,
    content_chunks: Iterable[str],
    finish_reason: str,
    usage: Mapping[str, Any],
    attachments: list[dict[str, Any]],
    project_record_ref: Mapping[str, Any],
) -> Iterator[str]:
    if finish_reason not in FINISH_REASONS:
        raise ValueError("finish reason is outside the compatibility contract")
    base = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": 0,
        "model": model,
    }
    yield _frame(
        {
            **base,
            "choices": [
                {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
            ],
        }
    )
    for chunk in content_chunks:
        if chunk:
            yield _frame(
                {
                    **base,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": chunk},
                            "finish_reason": None,
                        }
                    ],
                }
            )
    final = {
        **base,
        "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
        "usage": normalize_usage(usage),
    }
    if attachments:
        final["x_soda"] = {"attachments": attachments}
    validated = validate_content_ref(project_record_ref)
    if validated is None:
        raise ValueError("Project Record reference is invalid")
    final["x_d18"] = {"projectRecordRef": {"sha256": validated["sha256"]}}
    yield _frame(final)
    yield "data: [DONE]\n\n"


def render_protocol_probe_sse(
    *,
    completion_id: str,
    model: str,
    release_id: str,
) -> Iterator[str]:
    """Render the bounded Qingxiaoda handshake without claiming a research result."""

    if _SHA256.fullmatch(release_id) is None:
        raise ValueError("protocol probe requires a bound release identity")
    base = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": 0,
        "model": model,
    }
    yield _frame(
        {
            **base,
            "choices": [
                {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
            ],
        }
    )
    yield _frame(
        {
            **base,
            "choices": [
                {"index": 0, "delta": {"content": "OK"}, "finish_reason": None}
            ],
        }
    )
    yield _frame(
        {
            **base,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": normalize_usage(
                {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
            ),
            "x_d18": {
                "protocolProbe": {
                    "contract": "d18.protocol_probe/v1",
                    "releaseId": release_id,
                }
            },
        }
    )
    yield "data: [DONE]\n\n"


_PUBLIC_PROGRESS = {
    "created": "已排队",
    "claimed": "已开始",
    "transitioned": "正在处理",
    "lease_recovered": "正在恢复",
    "cancel_requested": "正在取消",
    "input_supplied": "已收到补充材料",
}

_PUBLIC_MATERIAL_PROGRESS = {
    "research.material.completion_intent": "正在固定材料",
    "research.material.upload_completed": "材料已上传",
    "research.material.url_normalized": "链接材料已固定",
    "research.material.processing_started": "正在安全解析",
    "research.material.processing_ready": "材料可用于研究",
    "research.material.processing_partial": "材料部分可用于研究",
    "research.material.processing_needs_user": "材料需要用户补充",
    "research.material.processing_unsupported": "材料格式暂不支持",
    "research.material.processing_failed": "材料处理受限",
    "research.material.scope_updated": "材料范围已更新",
    "research.material.version_created": "材料版本已更新",
    "research.material.withdrawn": "材料已停止用于后续研究",
    "research.material.restored": "材料已恢复用于后续研究",
    "research.material.output_reused": "项目成果已作为候选材料",
}

_PUBLIC_MATERIAL_PAYLOAD_KEYS = frozenset(
    {
        "revision_id",
        "supersedes_revision_id",
        "scope",
        "deleted_at",
        "status",
        "failure_category",
        "role",
    }
)
_PUBLIC_MATERIAL_FAILURES = frozenset(
    {
        "format_failure",
        "memory_limit",
        "memory_monitor_failure",
        "retry_exhausted",
        "worker_exit_failure",
        "worker_launch_failure",
        "worker_timeout",
    }
)
_PUBLIC_MATERIAL_STATUSES = frozenset(
    {
        "pending_security",
        "queued",
        "inspecting",
        "parsing",
        "ready",
        "partial",
        "needs_user",
        "unsupported",
        "failed",
    }
)

_PUBLIC_MATERIAL_SECURITY = {
    "research.material.completion_intent": "pending",
    "research.material.upload_completed": "pending",
    "research.material.url_normalized": "pending",
    "research.material.processing_started": "inspecting",
    "research.material.processing_ready": "checked",
    "research.material.processing_partial": "checked",
    "research.material.processing_needs_user": "checked",
    "research.material.processing_unsupported": "checked",
    "research.material.processing_failed": "inconclusive",
}

_PUBLIC_MATERIAL_CITATION_STATUS = {
    "research.material.withdrawn": (
        "future_use_revoked_historical_replay_preserved"
    ),
    "research.material.restored": "eligible_for_future_use",
    "research.material.version_created": (
        "new_revision_current_prior_citations_replayable"
    ),
}


def project_job_events(
    events: Iterable[Mapping[str, Any]],
    *,
    heartbeat_sequence: int,
) -> Iterator[str]:
    """Project bounded operational labels, never internal prompts or payloads."""

    for event in events:
        event_type = str(event.get("event_type", ""))
        sequence = int(event.get("sequence", 0))
        yield _event_frame(
            {
                "event": event_type,
                "sequence": sequence,
                "status": event.get("to_status"),
                "label": _PUBLIC_PROGRESS.get(event_type, "状态已更新"),
            },
            event="job",
            event_id=sequence,
        )
    yield _event_frame(
        {"cursor": heartbeat_sequence, "status": "connected"},
        event="heartbeat",
    )
    yield "data: [DONE]\n\n"


def project_material_events(
    events: Iterable[Mapping[str, Any]],
    *,
    heartbeat_sequence: int,
) -> Iterator[str]:
    """Render one bounded, cursor-resumable material-state snapshot."""

    previous = heartbeat_sequence
    for row in events:
        sequence = int(row.get("sequence", 0))
        event_type = str(row.get("event_type", ""))
        if sequence <= previous or event_type not in _PUBLIC_MATERIAL_PROGRESS:
            raise ValueError("material event stream is invalid")
        payload = row.get("payload")
        public_payload = {
            key: value
            for key, value in (payload.items() if isinstance(payload, Mapping) else [])
            if key in _PUBLIC_MATERIAL_PAYLOAD_KEYS
            and (
                value is None
                or isinstance(value, (str, int, float, bool))
            )
            and (
                key != "failure_category"
                or value in _PUBLIC_MATERIAL_FAILURES
            )
            and (
                key != "status"
                or value in _PUBLIC_MATERIAL_STATUSES
            )
        }
        public_event = {
            "event": event_type,
            "sequence": sequence,
            "materialId": row.get("material_id"),
            "label": _PUBLIC_MATERIAL_PROGRESS[event_type],
            "details": public_payload,
        }
        if event_type in _PUBLIC_MATERIAL_SECURITY:
            public_event["securityStatus"] = _PUBLIC_MATERIAL_SECURITY[event_type]
        if event_type in _PUBLIC_MATERIAL_CITATION_STATUS:
            public_event["citationStatus"] = _PUBLIC_MATERIAL_CITATION_STATUS[
                event_type
            ]
        yield _event_frame(
            public_event,
            event="material",
            event_id=sequence,
        )
        previous = sequence
    yield _event_frame(
        {"cursor": previous, "status": "connected"},
        event="heartbeat",
    )
    yield "data: [DONE]\n\n"
