"""FastAPI service control plane over the D-18 Product Interface boundary."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
import threading
import uuid
from copy import deepcopy
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse

from .attachments import ArtifactRegistry, AttachmentIntake, AttachmentIntakeError
from .auth import BearerAuthenticator
from .contracts import TERMINAL_STATES, validate_content_ref
from .events import project_job_events, render_completion_sse, render_protocol_probe_sse
from .job_store import JobStore
from .metrics import (
    MetricsRegistry,
    check_service_readiness,
    material_size_bucket,
    safe_log_record,
)
from .openai_compat import (
    ChatCompletionRequest,
    ContinuationCreatedResponse,
    ContinuationRequest,
    JobInputRequest,
    JobCreatedResponse,
    JobRequest,
    JobViewResponse,
    PublicResearchRecoveryResponse,
    ResearchResultResponse,
    ResultViewResponse,
    build_completion,
    is_qingxiaoda_connection_probe,
    require_accepted_runtime_result,
)
from .runtime_factory import RuntimeBinding, RuntimeFactory
from .runtime import (
    MaterialResolutionError,
    MaterialResolver,
    ProductInterfaceRuntime,
)
from .input_artifacts import InputArtifactStore
from .material_store import MaterialStore
from .runtime_boundaries import normalize_runtime_boundary
from .supervisor import WorkerSupervisor, finalize_cancelled_job
from .material_sandbox import probe_material_runtime
from .native_evidence_authentication import open_native_evidence_authentication_ports
from .product_evidence import (
    build_service_idempotency_replay_evidence,
    build_service_product_evidence,
)
from .release_manifest import (
    load_runtime_release_manifest,
    require_request_release,
    runtime_release_binding,
    validate_runtime_release_manifest,
)

_SAFE_RESEARCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_LOGGER = logging.getLogger("lca_research_service")
MAX_INLINE_SOURCE_MATERIAL_BYTES = 10 * 1024 * 1024


class MaterialProcessingPending(RuntimeError):
    """The immutable input exists but is not ready for synchronous research use."""


def _freeze_material_revision_refs(
    material_store: MaterialStore,
    project_id: str,
    material_ids: list[str],
) -> list[dict[str, str]]:
    """Bind a request to exact immutable revisions before it enters the queue."""

    if (
        not material_ids
        or len(material_ids) != len(set(material_ids))
        or any(not isinstance(material_id, str) or not material_id for material_id in material_ids)
    ):
        raise MaterialResolutionError("material_ids are invalid")
    frozen: list[dict[str, str]] = []
    for material_id in material_ids:
        try:
            current = material_store.get_material(
                project_id=project_id,
                material_id=material_id,
            )
        except (KeyError, PermissionError, ValueError) as error:
            raise MaterialResolutionError("material_id is unavailable") from error
        revision = current.get("revision")
        if not isinstance(revision, Mapping):
            raise MaterialResolutionError("material revision is unavailable")
        revision_id = revision.get("revision_id")
        artifact_id = revision.get("input_artifact_id")
        if not isinstance(revision_id, str) or not isinstance(artifact_id, str):
            raise MaterialResolutionError("material revision is unavailable")
        frozen.append(
            {
                "material_id": material_id,
                "revision_id": revision_id,
                "input_artifact_id": artifact_id,
            }
        )
    return frozen


@dataclass(frozen=True)
class ServiceConfig:
    bearer_token: str | None
    data_root: Path
    allowed_attachment_hosts: tuple[str, ...]
    project_bearer_tokens: tuple[tuple[str, str], ...] = ()
    project_token_grants: tuple[tuple[str, str], ...] = ()
    models: tuple[str, ...] = ("d18-research-director",)
    repository_root: Path = Path(__file__).resolve().parents[1]
    release_manifest_file: Path | None = None
    synchronous_material_wait_seconds: float = 0.0
    synchronous_material_poll_seconds: float = 0.1


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def _write_request_artifact(
    root: Path, research_id: str, request: dict[str, Any]
) -> tuple[str, dict[str, str]]:
    if not _SAFE_RESEARCH_ID.fullmatch(research_id):
        raise HTTPException(status_code=422, detail="invalid research_id")
    payload = _canonical_json(request)
    digest = hashlib.sha256(payload).hexdigest()
    relative = Path("projects") / research_id / "intake" / f"{digest}.json"
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()):
        raise HTTPException(status_code=422, detail="research path escaped data root")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise HTTPException(status_code=409, detail="request artifact drift")
    if not target.exists():
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(payload)
        temporary.replace(target)
    return digest, {"path": relative.as_posix(), "sha256": digest}


def _read_content_ref(root: Path, reference: Any) -> dict[str, Any]:
    normalized = validate_content_ref(reference)
    if normalized is None:
        raise ValueError("content reference is absent")
    target = (root / normalized["path"]).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise ValueError("content reference is unavailable")
    payload = target.read_bytes()
    if hashlib.sha256(payload).hexdigest() != normalized["sha256"]:
        raise ValueError("content reference drifted")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("content reference must contain an object")
    return value


def _research_id_for_completion(request: ChatCompletionRequest) -> str:
    request_dict = request.model_dump(mode="json")
    request_dict.pop("source_materials", None)
    canonical = _canonical_json(request_dict)
    session_id = request.session_id.strip() if request.session_id else ""
    research_id = request.research_id or (
        "chat-session-"
        + hashlib.sha256(
            ("qingxiaoda-session\0" + session_id).encode("utf-8")
        ).hexdigest()[:24]
        if session_id
        else "chat-" + hashlib.sha256(canonical).hexdigest()[:24]
    )
    if not _SAFE_RESEARCH_ID.fullmatch(research_id):
        raise HTTPException(status_code=422, detail="invalid research_id")
    return research_id


def _source_bound_completion_request(
    request: ChatCompletionRequest,
    *,
    root: Path,
    allowed_hosts: tuple[str, ...],
    attachment_client: Any | None,
    attachment_resolver: Callable[[str], list[str]] | None,
    artifact_registry: ArtifactRegistry,
    material_resolver: Callable[[str, object], list[dict[str, Any]]] | None = None,
    material_file_resolver: Callable[[str, str, str], dict[str, Any]] | None = None,
    qingxiaoda_file_resolver: (
        Callable[[str, str, str], Mapping[str, Any]] | None
    ) = None,
    material_store: MaterialStore | None = None,
    request_idempotency_key: str | None = None,
    metric_registry: MetricsRegistry | None = None,
    allow_pending_materials: bool = True,
) -> dict[str, Any]:
    request_dict = request.model_dump(mode="json")
    source_materials = request_dict.pop("source_materials", [])
    research_id = _research_id_for_completion(request)
    project_root = (root / "projects" / research_id).resolve()
    if not project_root.is_relative_to(root):
        raise HTTPException(status_code=422, detail="research path escaped data root")
    intake_options: dict[str, Any] = {
        "project_root": project_root,
        "allowed_hosts": allowed_hosts,
    }
    if attachment_client is not None:
        intake_options["client"] = attachment_client
    if attachment_resolver is not None:
        intake_options["resolver"] = attachment_resolver
    intake = AttachmentIntake(**intake_options)
    source_bound: list[dict[str, Any]] = []
    material_ids = list(request_dict.get("material_ids", []))
    newly_normalized_material_ids: set[str] = set()
    for message_index, message in enumerate(request_dict["messages"]):
        content = message["content"]
        if not isinstance(content, list):
            continue
        for part_index, part in enumerate(content):
            part_type = part["type"]
            if part_type not in {"image_url", "input_audio", "file"}:
                continue
            payload = part[part_type]
            source_identity_sha256: str | None = None
            if "file_id" in payload:
                file_id = payload["file_id"]
                filename = payload["filename"]
                if material_file_resolver is not None:
                    try:
                        material_file_resolver(
                            research_id,
                            file_id,
                            filename,
                        )
                    except MaterialResolutionError:
                        pass
                    else:
                        if file_id not in material_ids:
                            material_ids.append(file_id)
                        continue
                try:
                    bound = artifact_registry.bind_input(
                        file_id,
                        filename=filename,
                        project_id=research_id,
                    )
                except (KeyError, PermissionError):
                    bound = None
                if bound is not None:
                    source_bound.append(
                        {
                            "role": "previously_registered_file",
                            **bound,
                        }
                    )
                    part[part_type] = {
                        "file_ref": bound["ref"],
                        "filename": bound["filename"],
                    }
                    continue
                if qingxiaoda_file_resolver is None:
                    raise MaterialResolutionError("file_id is unavailable")
                try:
                    resolved_file = qingxiaoda_file_resolver(
                        research_id, file_id, filename
                    )
                except Exception as error:
                    raise MaterialResolutionError("file_id is unavailable") from error
                if (
                    not isinstance(resolved_file, Mapping)
                    or set(resolved_file) - {"url", "filename"}
                    or not isinstance(resolved_file.get("url"), str)
                    or not resolved_file["url"]
                ):
                    raise MaterialResolutionError("file_id is unavailable")
                resolved_filename = resolved_file.get("filename", filename)
                if not isinstance(resolved_filename, str) or not resolved_filename:
                    raise MaterialResolutionError("file_id is unavailable")
                payload = {
                    "url": resolved_file["url"],
                    "filename": resolved_filename,
                }
                source_identity_sha256 = hashlib.sha256(
                    ("qingxiaoda-file-id\0" + file_id).encode("utf-8")
                ).hexdigest()
            media_prefix = {
                "image_url": "image/",
                "input_audio": "audio/",
                "file": "",
            }[part_type]
            if material_store is None or not request_idempotency_key:
                receipt = intake.freeze(
                    payload["url"], expected_media_prefix=media_prefix
                )
                source_bound.append({"role": part_type, **receipt})
                bound_payload = {"file_ref": receipt["ref"]}
                for key in ("format", "filename"):
                    if key in payload:
                        bound_payload[key] = payload[key]
                part[part_type] = bound_payload
                continue
            url_sha256 = hashlib.sha256(payload["url"].encode("utf-8")).hexdigest()
            source_identity_sha256 = source_identity_sha256 or url_sha256
            filename = payload.get("filename") or f"{part_type}.bin"
            intake_key = "url-" + hashlib.sha256(
                (
                    f"{request_idempotency_key}:{message_index}:{part_index}:"
                    f"{part_type}:{source_identity_sha256}:{filename}:{media_prefix}"
                ).encode("utf-8")
            ).hexdigest()
            with material_store.remote_intake_lock(
                project_id=research_id,
                idempotency_key=intake_key,
            ):
                normalized = material_store.replay_remote_intake(
                    project_id=research_id,
                    idempotency_key=intake_key,
                    url_sha256=url_sha256,
                    role=part_type,
                    filename=filename,
                    expected_media_prefix=media_prefix,
                    source_identity_sha256=source_identity_sha256,
                )
                intake_outcome = "replayed" if normalized is not None else "created"
                if normalized is None:
                    staged = material_store.replay_remote_fetch(
                        project_id=research_id,
                        idempotency_key=intake_key,
                        url_sha256=url_sha256,
                        role=part_type,
                        filename=filename,
                        expected_media_prefix=media_prefix,
                        source_identity_sha256=source_identity_sha256,
                    )
                    if staged is None:
                        try:
                            fetched = intake.fetch_durable(
                                payload["url"],
                                expected_media_prefix=media_prefix,
                                checkpoint_identity=intake_key,
                            )
                        except AttachmentIntakeError as error:
                            if metric_registry is not None:
                                metric_registry.increment(
                                    "d18_material_intakes_total",
                                    {
                                        "source": "qingxiaoda_url",
                                        "outcome": "rejected",
                                        "type_family": {
                                            "image_url": "image",
                                            "input_audio": "audio",
                                            "file": "document",
                                        }[part_type],
                                        "size_bucket": "empty",
                                    },
                                )
                            raise MaterialResolutionError(
                                "remote material intake was rejected"
                            ) from error
                        staged = material_store.stage_remote_fetch(
                            project_id=research_id,
                            idempotency_key=intake_key,
                            source_url=payload["url"],
                            url_sha256=url_sha256,
                            role=part_type,
                            filename=filename,
                            expected_media_prefix=media_prefix,
                            source=BytesIO(fetched.content),
                            expected_sha256=fetched.sha256,
                            expected_bytes=fetched.byte_size,
                            detected_mime=fetched.media_type,
                            source_identity_sha256=source_identity_sha256,
                        )
                    intake.discard_durable_fetch(intake_key)
                    normalized = material_store.ingest_remote_artifact(
                        project_id=research_id,
                        idempotency_key=intake_key,
                        url_sha256=url_sha256,
                        role=part_type,
                        filename=filename,
                        expected_media_prefix=media_prefix,
                        source_identity_sha256=source_identity_sha256,
                    )
            if metric_registry is not None:
                artifact = normalized.get("artifact")
                if not isinstance(artifact, Mapping):
                    raise ValueError("normalized remote artifact is invalid")
                metric_registry.increment(
                    "d18_material_intakes_total",
                    {
                        "source": "qingxiaoda_url",
                        "outcome": intake_outcome,
                        "type_family": material_store._type_family(
                            artifact.get("detected_media_type")
                        ),
                        "size_bucket": material_size_bucket(
                            artifact.get("byte_size")
                        ),
                    },
                )
            material_id = normalized.get("material_id")
            if not isinstance(material_id, str) or not material_id:
                raise ValueError("normalized remote material identity is invalid")
            if material_id not in material_ids:
                material_ids.append(material_id)
            newly_normalized_material_ids.add(material_id)
            bound_payload = {"material_id": material_id}
            for key in ("format", "filename"):
                if key in payload:
                    bound_payload[key] = payload[key]
            part[part_type] = bound_payload
    source_bound_materials = _freeze_source_materials(
        root,
        research_id,
        source_materials,
    )
    if source_bound:
        request_dict["source_bound_attachments"] = source_bound
    if source_bound_materials:
        request_dict["source_bound_materials"] = source_bound_materials
    request_dict["research_id"] = research_id
    if material_ids:
        material_revision_refs = (
            _freeze_material_revision_refs(material_store, research_id, material_ids)
            if material_store is not None
            else []
        )
        existing_material_ids = [
            material_id
            for material_id in material_ids
            if material_id not in newly_normalized_material_ids
        ]
        if allow_pending_materials and material_store is not None:
            ready_material_ids: list[str] = []
            for material_id in existing_material_ids:
                try:
                    current = material_store.get_material(
                        project_id=research_id,
                        material_id=material_id,
                    )
                except (KeyError, PermissionError, ValueError) as error:
                    raise MaterialResolutionError(
                        "material_id is unavailable"
                    ) from error
                revision = current.get("revision")
                status = (
                    revision.get("processing_status")
                    if isinstance(revision, Mapping)
                    else None
                )
                if status in {"pending_security", "queued", "inspecting", "parsing"}:
                    continue
                ready_material_ids.append(material_id)
            existing_material_ids = ready_material_ids
        if newly_normalized_material_ids and not allow_pending_materials:
            for material_id in sorted(newly_normalized_material_ids):
                current = material_store.get_material(
                    project_id=research_id,
                    material_id=material_id,
                )
                revision = current.get("revision")
                status = (
                    revision.get("processing_status")
                    if isinstance(revision, Mapping)
                    else None
                )
                if status in {"ready", "partial", "needs_user"}:
                    existing_material_ids.append(material_id)
                elif status in {"pending_security", "queued", "inspecting", "parsing"}:
                    raise MaterialProcessingPending(
                        "remote material is still processing"
                    )
                else:
                    raise MaterialResolutionError(
                        "remote material cannot be used for research"
                    )
        if existing_material_ids and material_resolver is None:
            raise ValueError("material resolver is unavailable")
        if existing_material_ids:
            material_resolver(research_id, existing_material_ids)
        request_dict["material_ids"] = material_ids
        if material_revision_refs:
            request_dict["material_revision_refs"] = material_revision_refs
    return request_dict


def _freeze_source_materials(
    root: Path,
    research_id: str,
    materials: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    total_bytes = 0
    frozen: list[dict[str, Any]] = []
    project_root = (root / "projects" / research_id).resolve()
    if not project_root.is_relative_to(root.resolve()):
        raise HTTPException(status_code=422, detail="research path escaped data root")
    for material in materials:
        content = material["content"]
        text = content["text"]
        payload = text.encode("utf-8")
        total_bytes += len(payload)
        if total_bytes > MAX_INLINE_SOURCE_MATERIAL_BYTES:
            raise HTTPException(
                status_code=422,
                detail="source material aggregate exceeds 10 MiB",
            )
        digest = hashlib.sha256(payload).hexdigest()
        if digest != material["sha256"]:
            raise HTTPException(status_code=422, detail="source material sha256 mismatch")
        relative = Path("projects") / research_id / "materials" / digest
        target = (root / relative).resolve()
        if not target.is_relative_to(project_root):
            raise HTTPException(status_code=422, detail="source material path escaped project")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise HTTPException(status_code=409, detail="source material artifact drift")
        if not target.exists():
            temporary = target.with_suffix(".tmp")
            temporary.write_bytes(payload)
            temporary.replace(target)
        frozen.append(
            {
                "path": relative.as_posix(),
                "sha256": digest,
                "authority": "user_supplied_project_input",
                "material_id": f"sha256:{digest}",
                "name": material["name"],
                "media_type": material["media_type"],
                "content": {
                    "contract": (
                        "lca_research_kernel.user_supplied_source_material/v1"
                    ),
                    "name": material["name"],
                    "media_type": material["media_type"],
                    "text": text,
                },
            }
        )
    return frozen


def _registered_output_attachments(
    attachments: list[dict[str, Any]],
    registry: ArtifactRegistry,
    *,
    project_id: str,
) -> list[dict[str, Any]]:
    public: list[dict[str, Any]] = []
    for attachment in attachments:
        if set(attachment) != {
            "ref",
            "content_type",
            "accepted",
            "file_name",
            "file_type",
        }:
            raise ValueError("output attachment is not a registered artifact candidate")
        public.append(
            registry.register(
                attachment["ref"],
                content_type=attachment["content_type"],
                accepted=attachment["accepted"] is True,
                file_name=attachment["file_name"],
                file_type=attachment["file_type"],
                project_id=project_id,
            )
        )
    return public


def _material_candidate_payload(frozen: dict[str, Any]) -> dict[str, Any]:
    """Adapt one frozen user source into the stable Product Interface fact shape."""

    return {
        "fact_id": f"user-material:{frozen['sha256']}",
        "value": {
            "material_id": frozen["material_id"],
            "name": frozen["name"],
            "media_type": frozen["media_type"],
            "authority": frozen["authority"],
            "content_ref": {
                "path": frozen["path"],
                "sha256": frozen["sha256"],
            },
        },
    }


_PUBLIC_LOCATOR_KEYS = {
    "text_span": {"start_char", "end_char", "start_line", "end_line"},
    "json_pointer": {"pointer"},
    "xml_xpath": {"xpath"},
    "document_section": {
        "heading_path",
        "paragraph_start",
        "paragraph_end",
        "slide",
    },
    "spreadsheet_range": {"sheet_name", "cell_range"},
    "pdf_region": {"page", "bbox", "normalized_rect"},
    "image_region": {"bbox", "normalized_rect"},
    "audio_segment": {"start_ms", "end_ms"},
    "archive_member": {"member_path", "member_index"},
    "lca_object": {
        "dataset_uuid",
        "version",
        "process_id",
        "flow_id",
        "unit",
        "provider",
    },
}


def _public_locator(locator: Mapping[str, Any]) -> dict[str, Any]:
    kind = locator.get("locator_kind")
    value = locator.get("locator")
    if kind not in _PUBLIC_LOCATOR_KEYS or not isinstance(value, Mapping):
        raise ValueError("accepted citation locator kind is unsupported")
    allowed = _PUBLIC_LOCATOR_KEYS[kind]
    if not set(value).issubset(allowed):
        raise ValueError("accepted citation locator contains private fields")
    public: dict[str, Any] = {"kind": kind}
    for key in sorted(value):
        item = value[key]
        if isinstance(item, (str, int, float)) and not isinstance(item, bool):
            public[key] = item
        elif isinstance(item, list) and all(
            isinstance(part, (str, int, float)) and not isinstance(part, bool)
            for part in item
        ):
            public[key] = deepcopy(item)
        else:
            raise ValueError("accepted citation locator value is invalid")
    return public


def _public_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        raise ValueError("accepted result string list is invalid")
    return list(value)


def _public_sha256_list(value: object) -> list[str]:
    public = _public_string_list(value)
    if any(re.fullmatch(r"[0-9a-f]{64}", item) is None for item in public):
        raise ValueError("accepted result source digest is invalid")
    return public


def _public_missing_fields(result: Mapping[str, Any]) -> list[str]:
    value = result.get("missing_fields", result.get("missing_facts", []))
    if not isinstance(value, list):
        raise ValueError("accepted result missing fields are invalid")
    public = []
    for item in value:
        if isinstance(item, str):
            public.append(item)
        elif isinstance(item, Mapping):
            if (
                set(item) == {"processing_status", "reason"}
                and item.get("processing_status") == "answer_quality_bounded"
                and isinstance(item.get("reason"), str)
                and re.fullmatch(
                    r"answer_obligation_unsatisfied:[a-z][a-z0-9_]*",
                    item["reason"],
                )
                is not None
            ):
                continue
            identity = next(
                (
                    item.get(key)
                    for key in ("fact_id", "identity", "name", "field")
                    if isinstance(item.get(key), str) and item.get(key)
                ),
                None,
            )
            if identity is None:
                raise ValueError("accepted result missing fact lacks public identity")
            public.append(identity)
        else:
            raise ValueError("accepted result missing fact is invalid")
    return public


def _public_domain_acceptance(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("accepted result domain acceptance is invalid")
    public: dict[str, Any] = {}
    for key in ("status", "claim_ceiling"):
        item = value.get(key)
        if item is not None:
            if not isinstance(item, str):
                raise ValueError("accepted result domain status is invalid")
            public[key] = item
    for key in ("may_promote_to_official",):
        item = value.get(key)
        if item is not None:
            if not isinstance(item, bool):
                raise ValueError("accepted result domain authority is invalid")
            public[key] = item
    dropped = value.get("dropped_numeric_claim_count")
    if dropped is not None:
        if not isinstance(dropped, int) or isinstance(dropped, bool) or dropped < 0:
            raise ValueError("accepted result dropped claim count is invalid")
        public["dropped_numeric_claim_count"] = dropped
    public["errors"] = _public_string_list(value.get("errors", []))
    return public


def _public_result_projection(project_view: Mapping[str, Any]) -> dict[str, Any]:
    """Render one strict, user-facing projection from accepted Project Record state."""

    result = project_view.get("result")
    if not isinstance(result, Mapping):
        raise ValueError("accepted Project Record result is unavailable")
    status = result.get("status")
    claim_ceiling = result.get("claim_ceiling")
    if not isinstance(status, str) or not isinstance(claim_ceiling, str):
        raise ValueError("accepted result status is invalid")

    materials = project_view.get("materials") or {}
    revisions = project_view.get("material_revisions") or {}
    locators = project_view.get("evidence_locators") or {}
    decisions = project_view.get("material_use_decisions") or {}
    if not all(
        isinstance(value, Mapping)
        for value in (materials, revisions, locators, decisions)
    ):
        raise ValueError("accepted Project Record material projection is invalid")
    project_id = project_view.get("project_id")
    raw_citations = result.get("citations", [])
    if not isinstance(raw_citations, list):
        raise ValueError("accepted result citations are invalid")

    public_citations = []
    public_citation_by_locator: dict[str, dict[str, Any]] = {}
    cited_locator_ids: set[str] = set()
    cited_revision_ids: set[str] = set()
    for candidate in raw_citations:
        if not isinstance(candidate, Mapping):
            raise ValueError("accepted result citation is invalid")
        locator_id = candidate.get("locator_id")
        accepted = locators.get(locator_id)
        if not isinstance(locator_id, str) or not isinstance(accepted, Mapping):
            raise ValueError("accepted result citation locator is unavailable")
        if locator_id in cited_locator_ids:
            raise ValueError("accepted result citation is duplicated")
        revision_id = accepted.get("revision_id")
        revision = revisions.get(revision_id)
        material = (
            materials.get(revision.get("material_id"))
            if isinstance(revision, Mapping)
            else None
        )
        if not all(
            isinstance(value, Mapping)
            for value in (revision, material)
        ):
            raise ValueError("accepted result citation source is unavailable")
        if candidate.get("citation_id") != locator_id:
            raise ValueError("accepted result citation identity drifted")
        if candidate.get("revision_id") != revision_id:
            raise ValueError("accepted result citation revision drifted")
        if candidate.get("content_hash") != accepted.get("content_hash"):
            raise ValueError("accepted result citation content hash drifted")
        if accepted.get("scientific_status") != "accepted_evidence":
            raise ValueError("accepted result citation is not accepted evidence")
        material_id = material.get("material_id")
        display_name = material.get("display_name")
        authority = material.get("authority")
        processing_status = revision.get("processing_status")
        if not all(
            isinstance(value, str) and value
            for value in (
                project_id,
                material_id,
                revision_id,
                display_name,
                authority,
                processing_status,
            )
        ):
            raise ValueError("accepted result citation public identity is invalid")
        if material.get("project_id") not in {None, project_id}:
            raise ValueError("accepted result citation escaped its project")
        public_identity = _canonical_json(
            {
                "project_id": project_id,
                "material_id": material_id,
                "revision_id": revision_id,
                "locator_id": locator_id,
                "content_hash": accepted.get("content_hash"),
            }
        )
        public_citation = {
                "citationId": "cit_" + hashlib.sha256(public_identity).hexdigest()[:24],
                "materialId": material_id,
                "revisionId": revision_id,
                "displayName": display_name,
                "locator": _public_locator(accepted),
                "evidenceUrl": (
                    f"/v1/research/{quote(project_id, safe='')}/evidence/"
                    f"{quote(locator_id, safe='')}"
                ),
                "sourceAuthority": authority,
                "processingStatus": processing_status,
            }
        public_citations.append(public_citation)
        public_citation_by_locator[locator_id] = public_citation
        cited_locator_ids.add(locator_id)
        cited_revision_ids.add(revision_id)

    raw_citation_ids = result.get("citation_ids", [])
    if raw_citation_ids is not None:
        if not isinstance(raw_citation_ids, list) or not all(
            isinstance(item, str) for item in raw_citation_ids
        ):
            raise ValueError("accepted result citation identities are invalid")
        if set(raw_citation_ids) != cited_locator_ids or len(raw_citation_ids) != len(
            cited_locator_ids
        ):
            raise ValueError("accepted result citation projection drifted")
        public_citations = [
            public_citation_by_locator[locator_id]
            for locator_id in raw_citation_ids
        ]

    source_basis = result.get("source_basis", [])
    if not isinstance(source_basis, list):
        raise ValueError("accepted result source basis is invalid")
    basis_revision_ids = {
        item.get("revision_id")
        for item in source_basis
        if isinstance(item, Mapping) and isinstance(item.get("revision_id"), str)
    }
    decision_id = result.get("material_use_decision_id")
    decision_candidates = (
        [decisions.get(decision_id)]
        if isinstance(decision_id, str)
        else list(decisions.values())
    )
    relevant_decisions = []
    for decision in decision_candidates:
        if not isinstance(decision, Mapping):
            raise ValueError("accepted material use decision is invalid")
        selected = set(decision.get("selected_evidence_locator_ids") or [])
        allowed = set(decision.get("allowed_revision_ids") or [])
        if cited_locator_ids.intersection(selected) or basis_revision_ids.intersection(
            allowed
        ):
            relevant_decisions.append(decision)
    if (cited_locator_ids or basis_revision_ids) and len(relevant_decisions) != 1:
        raise ValueError("accepted result material use decision is ambiguous")
    allowed_revision_ids = set(cited_revision_ids)
    selected_locator_ids = set(cited_locator_ids)
    exclusion_reasons: dict[str, str] = {}
    conflict_visible = False
    for decision in relevant_decisions:
        allowed_revision_ids.update(decision.get("allowed_revision_ids") or [])
        selected_locator_ids.update(
            decision.get("selected_evidence_locator_ids") or []
        )
        conflict_visible = conflict_visible or (
            decision.get("claim_ceiling") == "conflicting_evidence_bounded"
        )
        for exclusion in decision.get("exclusions") or []:
            if not isinstance(exclusion, Mapping):
                raise ValueError("accepted material exclusion is invalid")
            identity = exclusion.get("identity")
            reason = exclusion.get("reason")
            if isinstance(identity, str) and isinstance(reason, str):
                exclusion_reasons[identity] = reason

    public_source_use = []
    for revision_id in sorted(allowed_revision_ids):
        revision = revisions.get(revision_id)
        material = (
            materials.get(revision.get("material_id"))
            if isinstance(revision, Mapping)
            else None
        )
        if not isinstance(revision, Mapping) or not isinstance(material, Mapping):
            raise ValueError("accepted material use revision is unavailable")
        material_id = material.get("material_id")
        display_name = material.get("display_name")
        authority = material.get("authority")
        processing_status = revision.get("processing_status")
        if not all(
            isinstance(value, str) and value
            for value in (
                material_id,
                display_name,
                authority,
                processing_status,
            )
        ):
            raise ValueError("accepted material use public identity is invalid")
        revision_locator_ids = {
            locator_id
            for locator_id, locator in locators.items()
            if isinstance(locator, Mapping)
            and locator.get("revision_id") == revision_id
        }
        selected_count = len(revision_locator_ids.intersection(selected_locator_ids))
        cited_count = len(revision_locator_ids.intersection(cited_locator_ids))
        if processing_status != "ready":
            use_status = "unavailable_partial"
            reason = "material_processing_not_ready"
        elif conflict_visible and selected_count:
            use_status = "conflict"
            reason = "accepted_result_contains_conflicting_evidence"
        elif cited_count:
            use_status = "used"
            reason = "cited_in_accepted_result"
        elif selected_count:
            use_status = "available_not_used"
            reason = "selected_for_context_not_used_in_accepted_result"
        else:
            use_status = "available_not_used"
            reason = exclusion_reasons.get(
                f"material_revision:{revision_id}", "allowed_but_not_selected"
            )
        public_source_use.append(
            {
                "materialId": material_id,
                "revisionId": revision_id,
                "displayName": display_name,
                "status": use_status,
                "reason": reason,
                "sourceAuthority": authority,
                "processingStatus": processing_status,
                "selectedLocatorCount": selected_count,
                "citedLocatorCount": cited_count,
            }
        )

    limitations = _public_string_list(result.get("limitations", []))
    missing_fields = _public_missing_fields(result)
    public_limitations: list[dict[str, Any]] = [
        {
            "code": "accepted_result_limitation",
            "message": limitation,
            "blocksStrongerConclusion": claim_ceiling != "candidate_only",
        }
        for limitation in limitations
    ]
    public_limitations.extend(
        {
            "code": "missing_required_fact",
            "message": missing,
            "blocksStrongerConclusion": True,
        }
        for missing in missing_fields
    )
    for source in public_source_use:
        if source["status"] == "unavailable_partial":
            public_limitations.append(
                {
                    "code": "material_processing_incomplete",
                    "message": "A project material was only partially available.",
                    "blocksStrongerConclusion": True,
                    "materialId": source["materialId"],
                    "revisionId": source["revisionId"],
                }
            )
    if conflict_visible:
        public_limitations.append(
            {
                "code": "material_evidence_conflict",
                "message": "Accepted material evidence contains an unresolved conflict.",
                "blocksStrongerConclusion": True,
            }
        )

    route = project_view.get("next_route")
    if route is not None and not isinstance(route, str):
        raise ValueError("accepted Project Record route is invalid")
    interaction_route = result.get("interaction_route")
    route_family = (
        interaction_route.get("route_family")
        if isinstance(interaction_route, Mapping)
        else None
    )
    if route_family not in {
        None,
        "direct_fast_path",
        "evidence_research",
        "full_lca_workflow",
    }:
        raise ValueError("accepted result route family is invalid")
    used_revision_ids = {
        source["revisionId"]
        for source in public_source_use
        if source["status"] in {"used", "conflict"}
    }
    methods = sorted(
        {
            revision["processor_profile"]
            for revision_id in used_revision_ids
            if isinstance((revision := revisions.get(revision_id)), Mapping)
            and isinstance(revision.get("processor_profile"), str)
            and revision["processor_profile"]
        }
    )
    completed_state_changes = []
    if relevant_decisions:
        completed_state_changes.append("selected_material_evidence")
    completed_state_changes.append("accepted_research_result")

    values = result.get("values", result.get("computed_values", []))
    if not isinstance(values, list):
        raise ValueError("accepted result values are invalid")
    public_values = []
    for value in values:
        if not isinstance(value, Mapping):
            raise ValueError("accepted result value is invalid")
        identity, unit, number = (
            value.get("identity"),
            value.get("unit"),
            value.get("value"),
        )
        if (
            not isinstance(identity, str)
            or not isinstance(unit, str)
            or not isinstance(number, (int, float))
            or isinstance(number, bool)
        ):
            raise ValueError("accepted result public value is invalid")
        public_values.append({"identity": identity, "unit": unit, "value": number})

    official_refs = result.get("official_source_refs", [])
    if not isinstance(official_refs, list):
        raise ValueError("accepted official source references are invalid")
    public_official_sources = []
    observed_official_source_ids: set[str] = set()
    official_source_ids_by_hash: dict[str, list[str]] = {}
    for reference in official_refs:
        if not isinstance(reference, Mapping):
            raise ValueError("accepted official source reference is invalid")
        capability_fact_id = reference.get("capability_fact_id")
        source_id = reference.get("source_id", capability_fact_id)
        source_authority = reference.get(
            "source_authority", "official_public_source"
        )
        requested_url = reference.get("requested_url")
        final_url = reference.get("final_url")
        accessed_at = reference.get("accessed_at")
        body_sha256 = reference.get("body_sha256")
        receipt_sha256 = reference.get("receipt_sha256")
        parse_location = reference.get("parse_location")
        snapshot_version = reference.get("source_version")
        declared_version = reference.get("declared_version", snapshot_version)
        if (
            reference.get("contract")
            != "lca_research_kernel.official_source_ref/v1"
            or not all(
                isinstance(value, str) and value
                for value in (
                    source_id,
                    source_authority,
                    requested_url,
                    final_url,
                    accessed_at,
                    parse_location,
                    snapshot_version,
                    declared_version,
                )
            )
            or source_id in observed_official_source_ids
            or not str(requested_url).startswith("https://")
            or not str(final_url).startswith("https://")
            or not isinstance(body_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", body_sha256) is None
            or not isinstance(receipt_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", receipt_sha256) is None
            or snapshot_version != f"sha256:{body_sha256}"
        ):
            raise ValueError("accepted official source reference drifted")
        observed_official_source_ids.add(str(source_id))
        official_source_ids_by_hash.setdefault(body_sha256, []).append(
            str(source_id)
        )
        public_official_sources.append(
            {
                "sourceId": source_id,
                "authority": "official_public_source",
                "sourceAuthority": source_authority,
                "requestedUrl": requested_url,
                "finalUrl": final_url,
                "accessedAt": accessed_at,
                "bodySha256": body_sha256,
                "receiptSha256": receipt_sha256,
                "parseLocation": parse_location,
                "sourceVersion": declared_version,
                "snapshotVersion": snapshot_version,
            }
        )
    public_official_sources.sort(key=lambda item: str(item["sourceId"]))

    public_official_claims = []
    raw_claims = result.get("claim_statements")
    if raw_claims is None:
        raw_claims = []
    if not isinstance(raw_claims, list):
        raise ValueError("accepted official claim statements are invalid")
    for claim in raw_claims:
        if not isinstance(claim, Mapping):
            raise ValueError("accepted official claim statement is invalid")
        statement = claim.get("statement")
        modality = claim.get("modality")
        raw_hashes = claim.get("scientific_source_sha256s")
        if (
            not isinstance(statement, str)
            or not statement.strip()
            or not isinstance(modality, str)
            or not modality
            or not isinstance(raw_hashes, list)
            or len(raw_hashes) != len(set(raw_hashes))
            or any(
                not isinstance(value, str)
                or re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in raw_hashes
            )
        ):
            raise ValueError("accepted official claim statement drifted")
        source_hashes = sorted(
            value for value in raw_hashes if value in official_source_ids_by_hash
        )
        if not source_hashes:
            continue
        source_ids = sorted(
            {
                source_id
                for source_hash in source_hashes
                for source_id in official_source_ids_by_hash[source_hash]
            }
        )
        claim_identity = {
            "statement": statement.strip(),
            "modality": modality,
            "sourceIds": source_ids,
            "sourceSha256s": source_hashes,
        }
        public_official_claims.append(
            {
                "claimId": "claim_"
                + hashlib.sha256(_canonical_json(claim_identity)).hexdigest()[:24],
                **claim_identity,
            }
        )
    public_official_claims.sort(key=lambda item: str(item["claimId"]))
    if public_official_sources and not public_official_claims:
        raise ValueError("accepted official sources have no public claim binding")

    raw_native_authority = result.get("native_authority")
    raw_scientific_boundary = result.get("scientific_boundary")
    public_native_authority = None
    public_scientific_boundary = None
    if raw_native_authority is not None or raw_scientific_boundary is not None:
        if (
            claim_ceiling != "capability_contract_only"
            or not isinstance(raw_native_authority, Mapping)
            or not isinstance(raw_scientific_boundary, Mapping)
            or raw_native_authority.get("provider") != "TianGong"
            or raw_native_authority.get("capability_id")
            != "tiangong.native.tidas-sdk"
            or raw_native_authority.get("source_identity")
            != "@tiangong-lca/tidas-sdk"
            or raw_native_authority.get("source_version") != "0.1.45"
            or not isinstance(raw_native_authority.get("source_integrity"), str)
            or not raw_native_authority["source_integrity"].startswith("sha512-")
            or not isinstance(raw_native_authority.get("content_sha256"), str)
            or re.fullmatch(
                r"[0-9a-f]{64}", raw_native_authority["content_sha256"]
            )
            is None
            or raw_scientific_boundary
            != {
                "lca_method": "not_selected",
                "exchange_provider": "not_selected",
                "quantity_unit": "not_evaluated",
                "scientific_result_created": False,
            }
        ):
            raise ValueError("accepted TianGong native boundary drifted")
        public_native_authority = {
            "provider": "TianGong",
            "capabilityId": raw_native_authority["capability_id"],
            "sourceIdentity": raw_native_authority["source_identity"],
            "sourceVersion": raw_native_authority["source_version"],
            "sourceIntegrity": raw_native_authority["source_integrity"],
            "contentSha256": raw_native_authority["content_sha256"],
        }
        public_scientific_boundary = {
            "lcaMethod": raw_scientific_boundary["lca_method"],
            "exchangeProvider": raw_scientific_boundary["exchange_provider"],
            "quantityUnit": raw_scientific_boundary["quantity_unit"],
            "scientificResultCreated": raw_scientific_boundary[
                "scientific_result_created"
            ],
        }

    answer = result.get("answer")
    summary = result.get("summary")
    bounded_conclusion = result.get("bounded_conclusion")
    if answer is not None and not isinstance(answer, str):
        raise ValueError("accepted result answer is invalid")
    if summary is not None and not isinstance(summary, str):
        raise ValueError("accepted result summary is invalid")
    if bounded_conclusion is not None and not isinstance(bounded_conclusion, str):
        raise ValueError("accepted result bounded conclusion is invalid")
    def public_answer_text(value: str | None) -> str | None:
        if value is None:
            return None
        for ordinal, locator_id in enumerate(raw_citation_ids, start=1):
            value = value.replace(f"[{locator_id}]", f"[{ordinal}]")
        return value

    return {
        "status": status,
        "claim_ceiling": claim_ceiling,
        "answer": public_answer_text(answer),
        "summary": public_answer_text(summary),
        "bounded_conclusion": public_answer_text(bounded_conclusion),
        "values": public_values,
        "limitations": limitations,
        "missing_fields": missing_fields,
        "domain_acceptance": _public_domain_acceptance(
            result.get("domain_acceptance")
        ),
        "official_object_ids": _public_string_list(
            result.get("official_object_ids", [])
        ),
        "source_sha256s": _public_sha256_list(
            result.get("scientific_source_sha256s", [])
        ),
        "officialSources": public_official_sources,
        "officialClaimBindings": public_official_claims,
        "nativeAuthority": public_native_authority,
        "scientificBoundary": public_scientific_boundary,
        "citations": public_citations,
        "sourceUse": public_source_use,
        "materialLimitations": public_limitations,
        "route": route,
        "routeFamily": route_family,
        "methods": methods,
        "completedStateChanges": completed_state_changes,
    }


def create_app(
    config: ServiceConfig,
    *,
    runtime: Any | None = None,
    attachment_client: Any | None = None,
    attachment_resolver: Callable[[str], list[str]] | None = None,
    qingxiaoda_file_resolver: (
        Callable[[str, str, str], Mapping[str, Any]] | None
    ) = None,
    worker_supervisor: Any | None = None,
    worker_supervisor_factory: Callable[[JobStore, MaterialStore], Any] | None = None,
) -> FastAPI:
    if (
        not math.isfinite(config.synchronous_material_wait_seconds)
        or not 0 <= config.synchronous_material_wait_seconds <= 110
        or not math.isfinite(config.synchronous_material_poll_seconds)
        or not 0 < config.synchronous_material_poll_seconds <= 5
    ):
        raise ValueError("synchronous material wait policy is invalid")
    injected_runtime = runtime is not None
    root = Path(config.data_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    store = JobStore(root / "control" / "jobs.sqlite3")
    input_artifact_store = InputArtifactStore(root)
    material_store = MaterialStore(
        root / "control" / "materials.sqlite3",
        input_artifacts=input_artifact_store,
        data_root=root,
    )
    library_material_resolver = MaterialResolver(
        data_root=root,
        material_store=material_store,
        input_artifact_store=input_artifact_store,
    )
    from lca_research_kernel.project_record import ProjectRecord

    def open_resolver_aware_project_record(workspace: Path) -> ProjectRecord:
        return ProjectRecord.open_or_create_project(
            workspace,
            verified_content_resolver=(
                library_material_resolver.resolve_project_record_content
            ),
        )

    project_tokens = dict(config.project_bearer_tokens)
    if len(project_tokens) != len(config.project_bearer_tokens):
        raise ValueError("project bearer token identifiers must be unique")
    authenticator = BearerAuthenticator(
        config.bearer_token,
        project_tokens=project_tokens,
        project_token_grants=config.project_token_grants,
    )
    artifact_registry = ArtifactRegistry(
        root, project_record_factory=open_resolver_aware_project_record
    )
    artifact_registry.rebuild_from_project_records()
    metric_registry = MetricsRegistry()
    release_manifest: dict[str, Any] | None = None
    if config.release_manifest_file is not None:
        release_manifest = load_runtime_release_manifest(
            config.release_manifest_file,
            repository_root=config.repository_root,
            frontend_root=config.repository_root / "apps/web",
        )
    elif injected_runtime:
        release_manifest = {
            "contract": "d18.runtime_release_manifest/v1",
            "release_id": "0" * 64,
            "manifest_sha256": "1" * 64,
            "backend": {"git_commit": "0" * 64},
            "frontend": {"build_id": "injected-runtime"},
        }
    release_identity = (
        runtime_release_binding(release_manifest)
        if release_manifest is not None
        else {
            "contract": "d18.runtime_release_identity/v1",
            "release_id": "unbound",
        }
    )
    product_evidence_signer = None
    if config.release_manifest_file is not None and release_manifest is not None:
        product_evidence_signer = open_native_evidence_authentication_ports(root).sign
    if runtime is None:
        runtime_binding = RuntimeFactory(
            repository_root=config.repository_root,
            data_root=root,
            input_artifact_store=input_artifact_store,
            material_store=material_store,
        ).build()
    else:
        runtime_binding = RuntimeBinding(runtime=runtime, status="ready")
    runtime = runtime_binding.runtime
    material_runtime_probe = probe_material_runtime()
    if isinstance(runtime, ProductInterfaceRuntime):
        if (
            runtime.input_artifact_store is not None
            and runtime.input_artifact_store is not input_artifact_store
        ):
            raise ValueError("injected runtime uses a different input artifact store")
        if runtime.material_store is not None and runtime.material_store is not material_store:
            raise ValueError("injected runtime uses a different material store")
        runtime.bind_material_stores(
            input_artifact_store=input_artifact_store,
            material_store=material_store,
        )
    from lca_research_kernel.assurance import AssuranceBoundary
    material_authority_lock = threading.RLock()

    def open_existing_project_record(project_id: str) -> ProjectRecord | None:
        workspace = root / "projects" / project_id
        if not (workspace / ProjectRecord.EVENT_FILE).is_file():
            return None
        return open_resolver_aware_project_record(workspace)

    def read_material_authority(project_id: str) -> dict[str, Any] | None:
        with material_authority_lock:
            record = open_existing_project_record(project_id)
            if record is None:
                return None
            view = record.read_current_project_view()
            material_store.apply_project_authority_projection(
                project_id=project_id, project_view=view
            )
            return view

    def mutate_material_authority(
        project_id: str,
        operation: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Append an accepted authority event only for an already registered material."""

        with material_authority_lock:
            record = open_existing_project_record(project_id)
            if record is None:
                return None
            view = record.read_current_project_view()
            material_id = payload.get("material_id")
            if operation not in {"reuse_output"} and material_id not in view.get("materials", {}):
                return None
            event_key = f"material-library:{project_id}:{idempotency_key}"
            prior = next(
                (
                    event
                    for event in record.read_audit_trail()
                    if event.get("idempotency_key") == event_key
                ),
                None,
            )
            if prior is not None:
                return prior
            change_type = {
                "scope": "material.scope_updated",
                "withdraw": "material.withdrawn",
                "restore": "material.restored",
            }.get(operation)
            if change_type is None:
                if operation in {"version", "reuse_output"}:
                    return {
                        "status": "candidate_only",
                        "authority": (
                            "operational_material_version"
                            if operation == "version"
                            else "accepted_output_reuse_candidate_input"
                        ),
                        "project_id": project_id,
                        "material_id": material_id,
                        "source_material_id": payload.get("source_material_id"),
                        "artifact_id": payload.get("artifact_id"),
                        "idempotency_key": idempotency_key,
                    }
                return None
            proposed_at = material_store._clock()
            candidate = {
                "contract": "lca_research_kernel.project_candidate_change/v1",
                "candidate_id": hashlib.sha256(event_key.encode()).hexdigest(),
                "change_type": change_type,
                "material_id": material_id,
                "next_route": "select-minimal-material-context-and-render-auditable-citations",
                "proposed_at": proposed_at,
            }
            if operation == "scope":
                candidate["scope"] = payload["scope"]
            elif operation == "withdraw":
                candidate["deleted_at"] = proposed_at
            else:
                candidate["deleted_at"] = None
            acceptance = AssuranceBoundary().evaluate_candidate_change(
                candidate, view
            )
            if acceptance["decision"] != "accepted":
                raise ValueError("material authority mutation failed assurance")
            return record.append_accepted_change(
                candidate, acceptance, idempotency_key=event_key
            )

    def resolve_reusable_output(
        project_id: str, artifact_id: str
    ) -> dict[str, Any]:
        return artifact_registry.resolve(artifact_id, project_id=project_id)
    if worker_supervisor is not None and worker_supervisor_factory is not None:
        raise ValueError("worker supervisor and factory are mutually exclusive")
    supervisor = worker_supervisor
    if worker_supervisor_factory is not None:
        supervisor = worker_supervisor_factory(store, material_store)
    if (
        supervisor is None
        and not injected_runtime
        and runtime_binding.ready
        and material_runtime_probe["ready"]
    ):
        supervisor = WorkerSupervisor(
            store,
            control_db=store.path,
            data_root=root,
            repository_root=config.repository_root,
            material_store=material_store,
            material_control_db=material_store.path,
            material_capability_snapshot=runtime_binding.material_capability_snapshot,
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        stop_event = threading.Event()
        thread = None
        if supervisor is not None:
            starter = getattr(supervisor, "start", None)
            if callable(starter):
                starter()
            thread = threading.Thread(
                target=supervisor.serve,
                args=(stop_event,),
                daemon=True,
            )
            thread.start()
        try:
            yield
        finally:
            if supervisor is not None:
                stop_event.set()
                supervisor.stop(5.0)
                if thread is not None:
                    thread.join(timeout=5.0)
            store.close()
            material_store.close()

    app = FastAPI(
        title="D-18 TianGong-native LCA Research Agent",
        version="1",
        lifespan=lifespan,
    )
    app.state.store = store
    app.state.input_artifact_store = input_artifact_store
    app.state.material_store = material_store
    app.state.runtime = runtime
    app.state.runtime_binding = runtime_binding
    app.state.supervisor = supervisor
    app.state.artifacts = artifact_registry
    app.state.config = config
    app.state.metrics = metric_registry
    app.state.qingxiaoda_file_resolver = qingxiaoda_file_resolver
    app.state.release_manifest = release_manifest
    app.state.release_identity = release_identity

    @app.middleware("http")
    async def observe_http(request: Request, call_next):
        started = time.perf_counter()
        expected_release = request.headers.get("X-D18-Release-ID")
        if expected_release and expected_release != release_identity.get("release_id"):
            response = JSONResponse(
                {
                    "code": "runtime_release_mismatch",
                    "detail": "frontend and backend releases differ",
                },
                status_code=409,
            )
        else:
            response = await call_next(request)
        response.headers["X-D18-Release-ID"] = str(
            release_identity.get("release_id", "unbound")
        )
        frontend_build_id = release_identity.get("frontend_build_id")
        if isinstance(frontend_build_id, str):
            response.headers["X-D18-Frontend-Build-ID"] = frontend_build_id
        duration = time.perf_counter() - started
        route_object = request.scope.get("route")
        route = getattr(route_object, "path", request.url.path)
        status_class = f"{response.status_code // 100}xx"
        try:
            metric_registry.increment(
                "d18_http_requests_total",
                {"route": route, "status_class": status_class},
            )
            metric_registry.set(
                "d18_http_overhead_seconds", {"route": route}, duration
            )
            bucket = (
                "lt_250ms"
                if duration < 0.250
                else "lt_2s"
                if duration < 2.0
                else "gte_2s"
            )
            _LOGGER.info(
                json.dumps(
                    safe_log_record(
                        {
                            "event": "request_completed",
                            "route": route,
                            "status_class": status_class,
                            "duration_bucket": bucket,
                        }
                    ),
                    sort_keys=True,
                )
            )
        except ValueError:
            _LOGGER.warning('{"event":"unclassified_route_rejected"}')
        return response

    def authorized(request: Request) -> None:
        authenticator.require(request)

    def authorize_project(request: Request, project_id: str) -> None:
        authenticator.require(request, project_id=project_id)

    def job_release_identity(row: Mapping[str, Any]) -> dict[str, str] | None:
        request_ref = row.get("request_ref")
        if not isinstance(request_ref, Mapping):
            return None
        try:
            request_value = _read_content_ref(root, request_ref)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None
        observed = request_value.get("runtime_release")
        return deepcopy(dict(observed)) if isinstance(observed, Mapping) else None

    def with_job_release(row: Mapping[str, Any]) -> dict[str, Any]:
        value = deepcopy(dict(row))
        value["runtime_release"] = job_release_identity(row)
        return value

    def job_input_receipt(row: Mapping[str, Any]) -> dict[str, Any] | None:
        """Project a content-bound decision receipt without exposing input paths."""

        input_ref = row.get("input_ref")
        if not isinstance(input_ref, Mapping):
            return None
        try:
            frozen = _read_content_ref(root, input_ref)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None
        payload = frozen.get("payload")
        if (
            frozen.get("contract")
            != "lca_research_kernel.research_material_or_decision/v1"
            or frozen.get("kind") != "decision"
            or not isinstance(frozen.get("input_id"), str)
            or not isinstance(payload, Mapping)
            or not isinstance(payload.get("decision_id"), str)
            or not isinstance(payload.get("decision"), str)
            or not isinstance(input_ref.get("sha256"), str)
        ):
            return None
        receipt = {
            "contract": "d18.public_job_input_receipt/v1",
            "input_id": frozen["input_id"],
            "kind": "decision",
            "decision_id": payload["decision_id"],
            "decision": payload["decision"],
            "input_sha256": input_ref["sha256"],
        }
        receipt["receipt_sha256"] = hashlib.sha256(
            json.dumps(
                receipt,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return receipt

    from .material_api import build_material_router

    app.include_router(
        build_material_router(
            material_store=material_store,
            policy=material_store.policy,
            clock=material_store._clock,
            project_authorizer=authorize_project,
            material_resolver=library_material_resolver,
            output_artifact_resolver=resolve_reusable_output,
            project_record_reader=read_material_authority,
            project_record_mutator=mutate_material_authority,
        )
    )

    @app.get("/v1/models", dependencies=[Depends(authorized)])
    def models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {"id": model, "object": "model", "owned_by": "d18"}
                for model in config.models
            ],
        }

    @app.post("/v1/chat/completions", dependencies=[Depends(authorized)])
    def chat(request: ChatCompletionRequest, http_request: Request):
        if runtime is None:
            raise HTTPException(
                status_code=503, detail="Product Interface unavailable"
            )
        try:
            authorize_project(
                http_request, _research_id_for_completion(request)
            )
            if is_qingxiaoda_connection_probe(
                request,
                allowed_models=config.models,
            ):
                return StreamingResponse(
                    render_protocol_probe_sse(
                        completion_id="chatcmpl-" + uuid.uuid4().hex,
                        model=request.model or config.models[0],
                        release_id=release_identity["release_id"],
                    ),
                    media_type="text/event-stream",
                )
            material_wait_deadline = (
                time.monotonic() + config.synchronous_material_wait_seconds
            )
            while True:
                try:
                    product_request = _source_bound_completion_request(
                        request,
                        root=root,
                        allowed_hosts=config.allowed_attachment_hosts,
                        attachment_client=attachment_client,
                        attachment_resolver=attachment_resolver,
                        artifact_registry=artifact_registry,
                        material_resolver=(
                            runtime.resolve_materials
                            if isinstance(runtime, ProductInterfaceRuntime)
                            else None
                        ),
                        material_file_resolver=(
                            runtime.resolve_material_file
                            if isinstance(runtime, ProductInterfaceRuntime)
                            else None
                        ),
                        qingxiaoda_file_resolver=qingxiaoda_file_resolver,
                        material_store=material_store,
                        request_idempotency_key=(
                            "chat-"
                            + hashlib.sha256(
                                _canonical_json(request.model_dump(mode="json"))
                            ).hexdigest()
                        ),
                        metric_registry=metric_registry,
                        allow_pending_materials=False,
                    )
                    break
                except MaterialProcessingPending:
                    remaining = material_wait_deadline - time.monotonic()
                    if remaining <= 0:
                        raise
                    time.sleep(
                        min(config.synchronous_material_poll_seconds, remaining)
                    )
            if request.session_id and request.session_id.strip() and sum(
                message.role == "user" for message in request.messages
            ) > 1:
                product_request["service_turn_kind"] = "continuation"
            product_request["runtime_release"] = deepcopy(release_identity)
            _, request_ref = _write_request_artifact(
                root,
                str(product_request["research_id"]),
                product_request,
            )
            product_request["service_request_ref"] = request_ref
            accepted = require_accepted_runtime_result(
                runtime.complete(product_request)
            )
            accepted["attachments"] = _registered_output_attachments(
                accepted["attachments"],
                artifact_registry,
                project_id=str(product_request["research_id"]),
            )
        except HTTPException:
            raise
        except MaterialProcessingPending:
            raise HTTPException(
                status_code=409,
                detail="material processing is pending; retry this request after processing",
            ) from None
        except MaterialResolutionError:
            raise HTTPException(
                status_code=422, detail="material_id is unavailable"
            ) from None
        except Exception:
            raise HTTPException(
                status_code=502, detail="accepted Project Record result unavailable"
            ) from None
        completion_id = "chatcmpl-" + uuid.uuid4().hex
        model = request.model or config.models[0]
        if request.stream:
            return StreamingResponse(
                render_completion_sse(
                    completion_id=completion_id,
                    model=model,
                    content_chunks=[accepted["content"]],
                    finish_reason=accepted["finish_reason"],
                    usage=accepted["usage"],
                    attachments=accepted["attachments"],
                    project_record_ref=accepted["project_record_ref"],
                ),
                media_type="text/event-stream",
            )
        return build_completion(
            completion_id=completion_id,
            model=model,
            content=accepted["content"],
            finish_reason=accepted["finish_reason"],
            usage=accepted["usage"],
            attachments=accepted["attachments"],
            project_record_ref=accepted["project_record_ref"],
        )

    @app.post(
        "/v1/jobs",
        status_code=202,
        dependencies=[Depends(authorized)],
        response_model=JobCreatedResponse,
    )
    def create_job(
        request: JobRequest,
        http_request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        if runtime is None:
            raise HTTPException(
                status_code=503, detail="Product Interface unavailable"
            )
        if not idempotency_key or len(idempotency_key) > 255:
            raise HTTPException(status_code=422, detail="Idempotency-Key is required")
        if _SAFE_RESEARCH_ID.fullmatch(request.research_id) is None:
            raise HTTPException(status_code=422, detail="invalid research_id")
        authorize_project(http_request, request.research_id)
        scoped_idempotency_key = f"{request.research_id}:{idempotency_key}"
        ingress_sha256 = hashlib.sha256(
            _canonical_json(request.model_dump(mode="json"))
        ).hexdigest()
        try:
            existing_job_id = store.reserve_ingress(
                idempotency_key=scoped_idempotency_key,
                project_id=request.research_id,
                request_sha256=ingress_sha256,
            )
            if existing_job_id is not None:
                existing = store.get(existing_job_id)
                existing_request = _read_content_ref(root, existing["request_ref"])
                if release_manifest is not None:
                    require_request_release(existing_request, release_manifest)
                response = {
                    "job_id": existing["job_id"],
                    "status": existing["status"],
                    "request_ref": existing["request_ref"],
                    "runtime_release": deepcopy(release_identity),
                }
                if product_evidence_signer is not None and release_manifest is not None:
                    response["idempotencyReplayEvidence"] = (
                        build_service_idempotency_replay_evidence(
                            job=existing,
                            release_manifest=release_manifest,
                            signer=product_evidence_signer,
                        )
                    )
                return response
            product_request = _source_bound_completion_request(
                ChatCompletionRequest(
                    research_id=request.research_id,
                    messages=request.messages,
                    source_materials=request.source_materials,
                    material_ids=request.material_ids,
                ),
                root=root,
                allowed_hosts=config.allowed_attachment_hosts,
                attachment_client=attachment_client,
                attachment_resolver=attachment_resolver,
                artifact_registry=artifact_registry,
                material_resolver=(
                    runtime.resolve_materials
                    if isinstance(runtime, ProductInterfaceRuntime)
                    else None
                ),
                material_file_resolver=(
                    runtime.resolve_material_file
                    if isinstance(runtime, ProductInterfaceRuntime)
                    else None
                ),
                qingxiaoda_file_resolver=qingxiaoda_file_resolver,
                material_store=material_store,
                request_idempotency_key=idempotency_key,
                metric_registry=metric_registry,
            )
            product_request["runtime_release"] = deepcopy(release_identity)
        except MaterialResolutionError:
            raise HTTPException(
                status_code=422, detail="material_id is unavailable"
            ) from None
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        digest, request_ref = _write_request_artifact(
            root, request.research_id, product_request
        )
        try:
            job = store.create(
                digest,
                scoped_idempotency_key,
                request_ref,
                project_id=request.research_id,
            )
            store.bind_ingress_job(
                idempotency_key=scoped_idempotency_key,
                job_id=job["job_id"],
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        return {
            "job_id": job["job_id"],
            "status": job["status"],
            "request_ref": request_ref,
            "runtime_release": deepcopy(release_identity),
        }

    @app.post(
        "/v1/jobs/{job_id}/continuations",
        status_code=202,
        dependencies=[Depends(authorized)],
        response_model=ContinuationCreatedResponse,
    )
    def create_continuation(
        job_id: str,
        continuation: ContinuationRequest,
        request: Request,
    ) -> dict[str, Any]:
        if runtime is None:
            raise HTTPException(
                status_code=503, detail="Product Interface unavailable"
            )
        try:
            parent = store.get(job_id)
            project_id = str(parent["project_id"])
            authorize_project(request, project_id)
            if (
                parent["status"] not in TERMINAL_STATES
                and parent["status"] != "waiting_for_input"
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "continuation requires a terminal or waiting parent job"
                    ),
                )
            parent_request = _read_content_ref(root, parent["request_ref"])
            if release_manifest is not None:
                require_request_release(parent_request, release_manifest)
            value = {
                "contract": (
                    "lca_research_service.research_continuation_job/v1"
                ),
                "research_id": project_id,
                "parent_job_id": job_id,
                "input_id": continuation.input_id,
                "text": continuation.text,
                "received_at": parent["updated_at"],
                "runtime_release": deepcopy(release_identity),
            }
            digest, continuation_ref = _write_request_artifact(
                root, project_id, value
            )
            try:
                child = store.create(
                    digest,
                    f"{project_id}:continuation:{continuation.input_id}",
                    continuation_ref,
                    project_id=project_id,
                )
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from None
            return {
                "job_id": child["job_id"],
                "status": child["status"],
                "request_ref": continuation_ref,
                "parent_job_id": job_id,
                "project_id": project_id,
                "runtime_release": deepcopy(release_identity),
            }
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found") from None

    @app.get(
        "/v1/research/{project_id}/jobs/latest",
        dependencies=[Depends(authorized)],
        response_model=PublicResearchRecoveryResponse,
        response_model_exclude_none=True,
    )
    def recover_research_job(project_id: str, request: Request) -> dict[str, Any]:
        if _SAFE_RESEARCH_ID.fullmatch(project_id) is None:
            raise HTTPException(status_code=422, detail="invalid research_id")
        authorize_project(request, project_id)
        row = store.latest_for_project(project_id)
        if row is None:
            raise HTTPException(status_code=404, detail="research job not found")
        try:
            frozen = _read_content_ref(root, row["request_ref"])
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            raise HTTPException(
                status_code=503,
                detail="research recovery record unavailable",
            ) from None
        if frozen.get("research_id") != project_id:
            raise HTTPException(
                status_code=503,
                detail="research recovery identity drifted",
            )
        user_messages: list[str] = []
        messages = frozen.get("messages")
        if isinstance(messages, list):
            for message in messages:
                if not isinstance(message, Mapping) or message.get("role") != "user":
                    continue
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    user_messages.append(content)
                elif isinstance(content, list):
                    text = "\n".join(
                        str(part["text"])
                        for part in content
                        if isinstance(part, Mapping)
                        and part.get("type") == "text"
                        and isinstance(part.get("text"), str)
                        and part["text"].strip()
                    )
                    if text:
                        user_messages.append(text)
        objective = user_messages[0] if user_messages else project_id
        return {
            "contract": "d18.public_research_recovery/v1",
            "research_id": project_id,
            "objective": objective,
            "user_messages": user_messages or [objective],
            "job": {
                "job_id": row["job_id"],
                "status": row["status"],
                "project_id": project_id,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "runtime_release": job_release_identity(row),
            },
        }

    @app.get(
        "/v1/jobs/{job_id}",
        dependencies=[Depends(authorized)],
        response_model=JobViewResponse,
    )
    def get_job(job_id: str, request: Request) -> dict[str, Any]:
        try:
            row = store.get(job_id)
            authorize_project(request, str(row["project_id"]))
            row["runtime_release"] = job_release_identity(row)
            input_receipt = job_input_receipt(row)
            if input_receipt is not None:
                row["input_receipt"] = input_receipt
            if row["status"] == "waiting_for_input" and runtime is not None:
                request_value = _read_content_ref(root, row["request_ref"])
                state_ref = row.get("state_ref")
                if (
                    isinstance(state_ref, Mapping)
                    and state_ref.get("role") == "material_processing_recovery"
                ):
                    status = _read_content_ref(root, state_ref)
                else:
                    status = runtime.read_status(request_value["research_id"])
                row["input_required"] = {
                    key: status[key]
                    for key in (
                        "interaction_kind",
                        "authorization_required",
                        "question_contract",
                        "question_count",
                        "question",
                        "missing_fields",
                    )
                    if key in status
                }
                if isinstance(status.get("execution_receipt"), Mapping):
                    row["progress_receipt"] = deepcopy(
                        dict(status["execution_receipt"])
                    )
            elif row["status"] == "failed":
                boundary_source: dict[str, Any] | None = None
                state_ref = row.get("state_ref")
                if (
                    isinstance(state_ref, dict)
                    and state_ref.get("role") == "failure_receipt"
                ):
                    try:
                        boundary_source = _read_content_ref(root, state_ref)
                    except (OSError, TypeError, ValueError, json.JSONDecodeError):
                        boundary_source = None
                if boundary_source is None and runtime is not None:
                    try:
                        request_value = _read_content_ref(root, row["request_ref"])
                        boundary_source = runtime.read_status(
                            request_value["research_id"]
                        )
                    except Exception:
                        boundary_source = None
                row["runtime_boundary"] = normalize_runtime_boundary(
                    boundary_source
                    or {
                        "boundary": "runtime_failure",
                        "failure_code": "runtime_boundary_projection_failed",
                        "retryable": True,
                        "recovery_route": "retry-bounded-service-operation",
                    }
                )
            return row
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found") from None

    @app.post(
        "/v1/jobs/{job_id}/inputs",
        status_code=202,
        dependencies=[Depends(authorized)],
        response_model=JobCreatedResponse,
    )
    def supply_job_input(
        job_id: str, request: JobInputRequest, http_request: Request
    ) -> dict[str, Any]:
        if runtime is None:
            raise HTTPException(
                status_code=503, detail="Product Interface unavailable"
            )
        try:
            row = store.get(job_id)
            authorize_project(http_request, str(row["project_id"]))
            if row["status"] != "waiting_for_input":
                raise HTTPException(status_code=409, detail="job is not waiting")
            original = _read_content_ref(root, row["request_ref"])
            if release_manifest is not None:
                require_request_release(original, release_manifest)
            research_id = str(original["research_id"])
            if research_id != str(row["project_id"]):
                raise ValueError("job request escaped its authorized project")
            raw = request.model_dump(mode="json")
            payload = raw["payload"]
            material_recovery: dict[str, Any] | None = None
            state_ref = row.get("state_ref")
            if (
                isinstance(state_ref, Mapping)
                and state_ref.get("role") == "material_processing_recovery"
            ):
                observed = _read_content_ref(root, state_ref)
                if observed.get("contract") == (
                    "lca_research_service.material_processing_recovery/v1"
                ):
                    material_recovery = observed
            frozen_ref: dict[str, str] | None = None
            if request.kind == "material":
                if payload.get("material_ids"):
                    if not isinstance(runtime, ProductInterfaceRuntime):
                        raise HTTPException(
                            status_code=422,
                            detail="material resolver is unavailable",
                        )
                    revision_refs = _freeze_material_revision_refs(
                        material_store,
                        research_id,
                        list(payload["material_ids"]),
                    )
                    payload = {
                        "material_ids": list(payload["material_ids"]),
                        "material_revision_refs": revision_refs,
                    }
                    raw["payload"] = deepcopy(payload)
                else:
                    frozen_materials = _freeze_source_materials(
                        root,
                        research_id,
                        [payload["source_material"]],
                    )
                    frozen = frozen_materials[0]
                    payload = _material_candidate_payload(frozen)
                    frozen_ref = {
                        "path": frozen["path"],
                        "sha256": frozen["sha256"],
                    }
            _, raw_ref = _write_request_artifact(root, research_id, raw)
            source_refs = [raw_ref]
            if frozen_ref is not None:
                source_refs.append(frozen_ref)
            external_input = {
                "contract": (
                    "lca_research_kernel.research_material_or_decision/v1"
                ),
                "input_id": request.input_id,
                "kind": request.kind,
                "payload": payload,
                "source_refs": source_refs,
                "received_at": runtime.product_interface.clock(),
            }
            if material_recovery is not None:
                external_input["material_recovery"] = material_recovery
            _, input_ref = _write_request_artifact(
                root, research_id, external_input
            )
            queued = store.queue_input(job_id, input_ref)
            return {
                "job_id": queued["job_id"],
                "status": queued["status"],
                "input_ref": input_ref,
                "runtime_release": deepcopy(release_identity),
            }
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found") from None

    @app.get(
        "/v1/jobs/{job_id}/result",
        dependencies=[Depends(authorized)],
        response_model=ResultViewResponse,
    )
    def get_job_result(job_id: str, request: Request) -> dict[str, Any]:
        if runtime is None:
            raise HTTPException(
                status_code=503, detail="Product Interface unavailable"
            )
        try:
            row = store.get(job_id)
            authorize_project(request, str(row["project_id"]))
            if row["status"] not in {"completed", "bounded"}:
                raise HTTPException(status_code=409, detail="result unavailable")
            original = _read_content_ref(root, row["request_ref"])
            research_id = str(original["research_id"])
            runtime_result = runtime.read_result(research_id)
            if runtime_result.get("status") != "accepted":
                raise HTTPException(
                    status_code=409, detail="accepted result unavailable"
                )
            record = open_existing_project_record(research_id)
            if record is None:
                nested = runtime_result.get("result")
                if not isinstance(nested, Mapping):
                    raise ValueError("accepted result projection is unavailable")
                project_view: Mapping[str, Any] = {
                    "project_id": research_id,
                    "materials": {},
                    "material_revisions": {},
                    "evidence_locators": {},
                    "material_use_decisions": {},
                    "result": nested,
                }
            else:
                project_view = record.read_current_project_view()
                nested = project_view.get("result")
                if not isinstance(nested, Mapping):
                    raise ValueError("accepted Project Record result is unavailable")
            candidates = nested.get("attachments", [])
            if not isinstance(candidates, list):
                raise ValueError("accepted attachments are invalid")
            # Sign and return the exact response-model shape.  Pydantic adds
            # optional ``None`` fields to nested result objects during HTTP
            # serialization; signing the pre-serialization mapping would bind
            # a different document from the bytes observed by the client.
            public_result = ResearchResultResponse.model_validate(
                _public_result_projection(project_view)
            ).model_dump(mode="json")
            execution_receipt = deepcopy(runtime_result.get("execution_receipt"))
            response = {
                "contract": "d18.public_result_projection/v1",
                "research_id": research_id,
                "status": "accepted",
                "result": public_result,
                "attachments": _registered_output_attachments(
                    candidates,
                    artifact_registry,
                    project_id=research_id,
                ),
                "runtime_release": (
                    deepcopy(dict(original["runtime_release"]))
                    if isinstance(original.get("runtime_release"), Mapping)
                    else None
                ),
                "executionReceipt": execution_receipt,
            }
            if (
                product_evidence_signer is not None
                and release_manifest is not None
                and isinstance(execution_receipt, Mapping)
            ):
                response["productEvidence"] = build_service_product_evidence(
                    job_id=job_id,
                    release_manifest=release_manifest,
                    result=public_result,
                    execution_receipt=execution_receipt,
                    project_view=project_view,
                    job_request_release=original.get("runtime_release", {}),
                    material_detail=lambda material_id: material_store.material_detail(
                        project_id=research_id, material_id=material_id
                    ),
                    signer=product_evidence_signer,
                )
            return response
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found") from None
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=502, detail="accepted result projection is invalid"
            ) from exc

    @app.post(
        "/v1/jobs/{job_id}/cancel",
        dependencies=[Depends(authorized)],
        response_model=JobViewResponse,
    )
    def cancel_job(job_id: str, request: Request) -> dict[str, Any]:
        try:
            authorize_project(request, str(store.get(job_id)["project_id"]))
            if supervisor is not None:
                return with_job_release(supervisor.cancel(job_id))
            return with_job_release(
                finalize_cancelled_job(store, job_id, data_root=root)
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found") from None

    @app.post(
        "/v1/jobs/{job_id}/resume",
        dependencies=[Depends(authorized)],
        response_model=JobViewResponse,
    )
    def resume_job(job_id: str, request: Request) -> dict[str, Any]:
        try:
            row = store.get(job_id)
            authorize_project(request, str(row["project_id"]))
            if row["status"] != "waiting_for_input":
                raise HTTPException(status_code=409, detail="job is not waiting")
            request_value = _read_content_ref(root, row["request_ref"])
            if release_manifest is not None:
                require_request_release(request_value, release_manifest)
            return with_job_release(
                store.transition(job_id, "waiting_for_input", "queued", None)
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found") from None

    @app.get("/v1/jobs/{job_id}/events", dependencies=[Depends(authorized)])
    def job_events(
        job_id: str,
        request: Request,
        after: int = 0,
        last_event_id: str | None = Header(
            default=None, alias="Last-Event-ID"
        ),
    ):
        cursor = after
        if last_event_id is not None:
            try:
                cursor = max(cursor, int(last_event_id))
            except ValueError:
                raise HTTPException(
                    status_code=422, detail="Last-Event-ID must be an integer"
                ) from None
        if cursor < 0:
            raise HTTPException(status_code=422, detail="event cursor is invalid")
        try:
            authorize_project(request, str(store.get(job_id)["project_id"]))
            events = store.events(job_id, after=cursor)
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found") from None
        heartbeat_cursor = (
            int(events[-1]["sequence"]) if events else cursor
        )
        return StreamingResponse(
            project_job_events(
                events, heartbeat_sequence=heartbeat_cursor
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/v1/files/{artifact_id}", dependencies=[Depends(authorized)])
    def file_download(artifact_id: str, request: Request):
        try:
            artifact = artifact_registry.resolve(artifact_id)
            authorize_project(request, str(artifact["project_id"]))
        except (KeyError, PermissionError):
            raise HTTPException(status_code=404, detail="artifact unavailable") from None
        return FileResponse(
            artifact["path"],
            media_type=artifact["content_type"],
            filename=artifact_id,
        )

    @app.get("/health/live")
    def live() -> dict[str, Any]:
        return {"status": "live", "release": deepcopy(release_identity)}

    @app.get("/health/ready")
    def ready() -> JSONResponse:
        release_manifest_valid = injected_runtime
        if config.release_manifest_file is not None and release_manifest is not None:
            try:
                validate_runtime_release_manifest(
                    release_manifest,
                    repository_root=config.repository_root,
                    frontend_root=config.repository_root / "apps/web",
                )
            except ValueError:
                release_manifest_valid = False
            else:
                release_manifest_valid = True
        report = check_service_readiness(
            repository_root=config.repository_root,
            control_db=store.path,
            project_root=root / "projects",
            runtime_ready=runtime_binding.ready,
            material_control_db=material_store.path,
            input_root=root / "projects",
            material_runtime_probe=material_runtime_probe,
            release_manifest_valid=release_manifest_valid,
            release_identity=release_identity,
        )
        probe = getattr(qingxiaoda_file_resolver, "probe", None)
        if callable(probe):
            try:
                probe()
            except (OSError, ValueError):
                report["checks"]["qingxiaoda_file_bindings"] = False
                report["status"] = "unavailable"
            else:
                report["checks"]["qingxiaoda_file_bindings"] = True
        supervisor_probe = getattr(supervisor, "probe", None)
        if callable(supervisor_probe):
            try:
                supervisor_probe()
            except (OSError, RuntimeError, ValueError):
                report["checks"]["worker_supervisor"] = False
                report["status"] = "unavailable"
            else:
                report["checks"]["worker_supervisor"] = True
        return JSONResponse(report, status_code=200 if report["status"] == "ready" else 503)

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics() -> str:
        return metric_registry.render()

    return app
