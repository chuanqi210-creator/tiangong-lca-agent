"""Authenticated resumable upload routes for immutable research materials."""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .material_store import MaterialStore
from .events import project_material_events


_CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")


class UploadMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=1024)
    declared_mime: str = Field(min_length=3, max_length=255)
    total_bytes: int = Field(ge=0)
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class CreateUploadsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uploads: list[UploadMetadata] = Field(min_length=1)


class CompleteUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str = "turn"
    explicit_duplicate: bool = False


class ScopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str


class VersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_material_id: str = Field(min_length=1, max_length=255)


class ReuseOutputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1, max_length=255)
    scope: str = "project"


class ProblemDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    detail: str


class ProblemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: ProblemDetail


class MaterialRevisionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    revision_id: str
    processing_status: str
    scientific_status: str


class MaterialSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    material_id: str
    transfer_status: str
    current_revision: MaterialRevisionResponse


class MaterialListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    materials: list[MaterialSummaryResponse]


class MaterialDetailResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    material: dict[str, Any]
    current_revision: MaterialRevisionResponse
    revisions: list[MaterialRevisionResponse]


def _problem(status_code: int, code: str, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "detail": detail},
    )


def _translate(error: Exception) -> HTTPException:
    message = str(error)
    if isinstance(error, KeyError):
        return _problem(404, "upload_not_found", "Upload session is unavailable.")
    if "expired" in message or "not writable" in message:
        return _problem(410, "upload_expired", "Upload session expired.")
    if "capacity" in message or "exceeds" in message:
        return _problem(413, "upload_policy_exceeded", "Upload policy was exceeded.")
    if "SHA-256" in message or "sha256" in message:
        return _problem(422, "upload_sha256_mismatch", "Upload checksum did not match.")
    if "Content-Range" in message:
        return _problem(422, "invalid_part_range", "Content-Range is invalid.")
    if "invalid" in message:
        return _problem(422, "invalid_upload_metadata", "Upload metadata is invalid.")
    return _problem(409, "upload_state_conflict", "Upload state does not permit this operation.")


def build_material_router(
    *,
    material_store: MaterialStore,
    policy: Mapping[str, int],
    clock: Callable[[], str],
    project_authorizer: Callable[[Request, str], None],
    material_resolver: object | None = None,
    output_artifact_resolver: Callable[[str, str], Mapping[str, Any]] | None = None,
    project_record_reader: Callable[[str], Mapping[str, Any] | None] | None = None,
    project_record_mutator: Callable[
        [str, str, str, Mapping[str, Any]], Mapping[str, Any] | None
    ]
    | None = None,
) -> APIRouter:
    """Build the upload seam while keeping storage and authorization injected."""

    del policy, clock  # Frozen inside MaterialStore; retained in this public constructor contract.
    problem_responses = {
        status: {"model": ProblemResponse}
        for status in (404, 409, 410, 413, 503)
    }
    problem_responses[422] = {
        "content": {
            "application/json": {
                "schema": {
                    "oneOf": [
                        {"$ref": "#/components/schemas/ProblemResponse"},
                        {"$ref": "#/components/schemas/HTTPValidationError"},
                    ]
                }
            }
        }
    }
    router = APIRouter(responses=problem_responses)

    def project_view(research_id: str) -> Mapping[str, Any] | None:
        return project_record_reader(research_id) if project_record_reader else None

    def overlay_authority(
        research_id: str, value: dict[str, Any]
    ) -> dict[str, Any]:
        """Project Record may override operational scope/deletion/scientific use only."""

        view = project_view(research_id)
        if not isinstance(view, Mapping):
            return value
        materials = view.get("materials")
        revisions = view.get("material_revisions")
        decisions = view.get("material_use_decisions")
        material_id = value.get("material", value).get("material_id")
        accepted_material = (
            materials.get(material_id) if isinstance(materials, Mapping) else None
        )
        rows = value.get("revisions") or [value.get("current_revision")]
        used_locator_ids = {
            locator_id
            for decision in (decisions.values() if isinstance(decisions, Mapping) else [])
            if isinstance(decision, Mapping)
            for locator_id in decision.get("selected_evidence_locator_ids", [])
            if isinstance(locator_id, str)
        }
        accepted_locators = view.get("evidence_locators")
        used_revision_ids = {
            locator.get("revision_id")
            for locator_id, locator in (
                accepted_locators.items() if isinstance(accepted_locators, Mapping) else []
            )
            if locator_id in used_locator_ids and isinstance(locator, Mapping)
        }
        for revision in rows:
            if not isinstance(revision, dict):
                continue
            revision_id = revision.get("revision_id")
            if isinstance(revisions, Mapping) and revision_id in revisions:
                revision["scientific_status"] = "accepted_project_input"
            revision["used_in_answer"] = revision_id in used_revision_ids
        current = value.get("current_revision")
        if isinstance(current, dict):
            current["allowed_in_project"] = (
                isinstance(accepted_material, Mapping)
                and accepted_material.get("deleted_at") is None
                and accepted_material.get("scope") == "project"
                and current.get("scientific_status") == "accepted_project_input"
            )
        material = value.get("material") if isinstance(value.get("material"), dict) else value
        if isinstance(accepted_material, Mapping) and isinstance(material, dict):
            material["scope"] = accepted_material.get("scope", material.get("scope"))
            material["deleted_at"] = accepted_material.get("deleted_at")
            material["project_record_authoritative"] = True
        return value

    def append_authority(
        research_id: str,
        operation: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        if project_record_mutator is None:
            return None
        return project_record_mutator(
            research_id, operation, idempotency_key, payload
        )

    def mutate_authority(
        research_id: str,
        operation: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
        fallback: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        """Commit the command to Project Record, then refresh its SQLite projection."""

        material_store.reserve_authority_command(
            project_id=research_id,
            idempotency_key=idempotency_key,
            operation=operation,
            request_value=payload,
        )
        accepted = append_authority(
            research_id, operation, idempotency_key, payload
        )
        if accepted is None:
            return fallback()
        view = project_view(research_id)
        if not isinstance(view, Mapping):
            raise ValueError("accepted Project Record projection is unavailable")
        material_store.apply_project_authority_projection(
            project_id=research_id, project_view=view
        )
        return overlay_authority(
            research_id,
            material_store.material_detail(
                project_id=research_id,
                material_id=str(payload["material_id"]),
            ),
        )

    def require_authoritative_access(
        research_id: str, material_id: str
    ) -> None:
        """Fail closed when operational availability disagrees with Project Record."""

        operational = material_store.get_material(
            project_id=research_id, material_id=material_id
        )
        view = project_view(research_id)
        if not isinstance(view, Mapping):
            return
        materials = view.get("materials")
        accepted = (
            materials.get(material_id) if isinstance(materials, Mapping) else None
        )
        if accepted is None:
            return
        if not isinstance(accepted, Mapping):
            raise KeyError("material authority projection is unavailable")
        if accepted.get("deleted_at") is not None:
            raise KeyError("material authority projection is unavailable")
        if accepted.get("scope") != operational["material"].get("scope"):
            material_store.apply_project_authority_projection(
                project_id=research_id, project_view=view
            )
            operational = material_store.get_material(
                project_id=research_id, material_id=material_id
            )
            if accepted.get("scope") != operational["material"].get("scope"):
                raise KeyError("material authority projection is unavailable")

    @router.get(
        "/v1/research/{research_id}/materials",
        response_model=MaterialListResponse,
    )
    def list_materials(
        research_id: str,
        request: Request,
        query: str | None = None,
        type_family: str | None = None,
        processing_status: str | None = None,
        scientific_status: str | None = None,
        scope: str | None = None,
        tag: str | None = None,
        directory: str | None = None,
        deleted: str = "false",
    ) -> dict[str, Any]:
        project_authorizer(request, research_id)
        try:
            rows = [
                overlay_authority(research_id, row)
                for row in material_store.list_materials(
                    project_id=research_id,
                    query=query,
                    type_family=type_family,
                    processing_status=processing_status,
                    scientific_status=None,
                    scope=None,
                    tag=tag,
                    directory=directory,
                    deleted="all",
                )
            ]
            if scientific_status is not None:
                rows = [
                    row for row in rows
                    if row["current_revision"]["scientific_status"] == scientific_status
                ]
            if scope is not None:
                rows = [row for row in rows if row["scope"] == scope]
            if deleted == "false":
                rows = [row for row in rows if row["deleted_at"] is None]
            elif deleted == "true":
                rows = [row for row in rows if row["deleted_at"] is not None]
            elif deleted != "all":
                raise ValueError("deleted filter is invalid")
            return {"materials": rows}
        except (KeyError, ValueError) as error:
            raise _translate(error) from None

    @router.get(
        "/v1/research/{research_id}/materials/{material_id}",
        response_model=MaterialDetailResponse,
    )
    def material_detail(
        research_id: str, material_id: str, request: Request
    ) -> dict[str, Any]:
        project_authorizer(request, research_id)
        try:
            return overlay_authority(
                research_id,
                material_store.material_detail(
                    project_id=research_id, material_id=material_id
                ),
            )
        except (KeyError, ValueError) as error:
            raise _problem(404, "material_not_found", "Material is unavailable.") from None

    def mutation_key(request: Request) -> str:
        key = request.headers.get("idempotency-key", "").strip()
        if not key:
            raise _problem(422, "idempotency_key_required", "Idempotency-Key is required.")
        return key

    @router.post("/v1/research/{research_id}/materials/{material_id}/scope")
    def update_scope(
        research_id: str, material_id: str, payload: ScopeRequest, request: Request
    ) -> dict[str, Any]:
        project_authorizer(request, research_id)
        try:
            key = mutation_key(request)
            command = {"material_id": material_id, "scope": payload.scope}
            return mutate_authority(
                research_id, "scope", key, command,
                lambda: material_store.update_material_scope(
                    project_id=research_id, material_id=material_id,
                    scope=payload.scope, idempotency_key=key,
                ),
            )
        except (KeyError, ValueError) as error:
            raise _translate(error) from None

    @router.post("/v1/research/{research_id}/materials/{material_id}/versions")
    def add_version(
        research_id: str, material_id: str, payload: VersionRequest, request: Request
    ) -> dict[str, Any]:
        project_authorizer(request, research_id)
        try:
            key = mutation_key(request)
            return material_store.create_material_version(
                project_id=research_id, material_id=material_id,
                source_material_id=payload.source_material_id,
                idempotency_key=key,
            )
        except (KeyError, ValueError) as error:
            raise _translate(error) from None

    @router.delete("/v1/research/{research_id}/materials/{material_id}")
    def withdraw_material(
        research_id: str, material_id: str, request: Request
    ) -> dict[str, Any]:
        project_authorizer(request, research_id)
        try:
            key = mutation_key(request)
            command = {"material_id": material_id}
            return mutate_authority(
                research_id, "withdraw", key, command,
                lambda: material_store.withdraw_material(
                    project_id=research_id, material_id=material_id,
                    idempotency_key=key,
                ),
            )
        except (KeyError, ValueError) as error:
            raise _translate(error) from None

    @router.post("/v1/research/{research_id}/materials/{material_id}/restore")
    def restore_material(
        research_id: str, material_id: str, request: Request
    ) -> dict[str, Any]:
        project_authorizer(request, research_id)
        try:
            key = mutation_key(request)
            command = {"material_id": material_id}
            return mutate_authority(
                research_id, "restore", key, command,
                lambda: material_store.restore_material(
                    project_id=research_id, material_id=material_id,
                    idempotency_key=key,
                ),
            )
        except (KeyError, ValueError) as error:
            raise _translate(error) from None

    @router.get("/v1/research/{research_id}/materials/{material_id}/download")
    def download_material(research_id: str, material_id: str, request: Request):
        project_authorizer(request, research_id)
        try:
            require_authoritative_access(research_id, material_id)
            artifact = material_store.downloadable_artifact(
                project_id=research_id, material_id=material_id
            )
            source = material_store.input_artifacts.open_verified(
                research_id=research_id, artifact_id=artifact["artifact_id"]
            )
        except (KeyError, ValueError) as error:
            raise _problem(404, "material_not_found", "Material is unavailable.") from None

        def chunks():
            try:
                while chunk := source.read(1024 * 1024):
                    yield chunk
            finally:
                source.close()

        filename = quote(str(artifact["filename"]), safe="")
        return StreamingResponse(
            chunks(),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
                "Cache-Control": "private, no-store",
            },
        )

    @router.get("/v1/research/{research_id}/materials/{material_id}/preview")
    def preview_material(
        research_id: str,
        material_id: str,
        request: Request,
        highlight_locator_id: str | None = None,
    ) -> dict[str, Any]:
        project_authorizer(request, research_id)
        if material_resolver is None:
            raise _problem(503, "preview_unavailable", "Material preview is unavailable.")
        try:
            require_authoritative_access(research_id, material_id)
            return material_resolver.preview_projection(
                research_id,
                material_id,
                highlight_locator_id=highlight_locator_id,
            )
        except (KeyError, PermissionError, ValueError):
            raise _problem(404, "material_not_found", "Material is unavailable.") from None

    @router.get("/v1/research/{research_id}/materials/{material_id}/preview-image")
    def preview_material_image(
        research_id: str,
        material_id: str,
        request: Request,
        page: int | None = None,
    ) -> Response:
        project_authorizer(request, research_id)
        if material_resolver is None:
            raise _problem(503, "preview_unavailable", "Material preview is unavailable.")
        try:
            require_authoritative_access(research_id, material_id)
            payload = material_resolver.preview_image(
                research_id, material_id, page=page
            )
        except (KeyError, PermissionError, ValueError):
            raise _problem(404, "material_not_found", "Material is unavailable.") from None
        return Response(
            payload,
            media_type="image/png",
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
                "Cache-Control": "private, no-store",
            },
        )

    @router.get("/v1/research/{research_id}/evidence/{locator_id}")
    def evidence_projection(
        research_id: str, locator_id: str, request: Request
    ) -> dict[str, Any]:
        project_authorizer(request, research_id)
        if material_resolver is None:
            raise _problem(503, "evidence_unavailable", "Evidence is unavailable.")
        try:
            return material_resolver.evidence_projection(research_id, locator_id)
        except (KeyError, PermissionError, ValueError):
            raise _problem(404, "evidence_not_found", "Evidence is unavailable.") from None

    @router.get(
        "/v1/research/{research_id}/material-events",
        responses={
            200: {
                "content": {
                    "text/event-stream": {
                        "schema": {
                            "type": "string",
                            "x-d18-event-contract": (
                                "lca_research_service.material_event/v1"
                            ),
                        }
                    }
                }
            }
        },
    )
    def material_events(research_id: str, request: Request) -> StreamingResponse:
        project_authorizer(request, research_id)
        raw_cursor = request.headers.get("last-event-id", "0").strip() or "0"
        try:
            cursor = int(raw_cursor)
            if cursor < 0:
                raise ValueError
            rows = material_store.material_events(
                project_id=research_id,
                after_sequence=cursor,
                limit=256,
            )
            stream = project_material_events(
                rows,
                heartbeat_sequence=cursor,
            )
        except ValueError:
            raise _problem(
                422,
                "invalid_material_event_cursor",
                "Last-Event-ID is invalid.",
            ) from None
        return StreamingResponse(
            stream,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
        )

    @router.post("/v1/research/{research_id}/materials/reuse-output", status_code=201)
    def reuse_output(
        research_id: str, payload: ReuseOutputRequest, request: Request
    ) -> dict[str, Any]:
        project_authorizer(request, research_id)
        key = mutation_key(request)
        if output_artifact_resolver is None:
            raise _problem(404, "artifact_not_found", "Artifact is unavailable.")
        try:
            artifact = output_artifact_resolver(research_id, payload.artifact_id)
            authority_event = append_authority(
                research_id,
                "reuse_output",
                key,
                {"artifact_id": payload.artifact_id, "scope": payload.scope},
            )
            with artifact["path"].open("rb") as source:
                return material_store.reuse_output_artifact(
                    project_id=research_id,
                    source=source,
                    expected_sha256=artifact["sha256"],
                    expected_bytes=artifact["byte_size"],
                    detected_mime=artifact["content_type"],
                    filename=artifact["file_name"],
                    output_artifact_id=payload.artifact_id,
                    scope=payload.scope,
                    idempotency_key=key,
                    project_record_ref=authority_event,
                )
        except (KeyError, PermissionError):
            raise _problem(404, "artifact_not_found", "Artifact is unavailable.") from None
        except (ValueError, OSError) as error:
            raise _translate(error) from None

    @router.post("/v1/research/{research_id}/material-uploads", status_code=201)
    def create_uploads(
        research_id: str,
        payload: CreateUploadsRequest,
        request: Request,
    ) -> dict[str, Any]:
        project_authorizer(request, research_id)
        idempotency_key = request.headers.get("idempotency-key", "").strip()
        if not idempotency_key:
            raise _problem(422, "idempotency_key_required", "Idempotency-Key is required.")
        try:
            uploads = material_store.create_upload_batch_idempotent(
                project_id=research_id,
                uploads=[item.model_dump() for item in payload.uploads],
                idempotency_key=idempotency_key,
            )
            response = {"uploads": uploads}
        except (KeyError, ValueError) as error:
            raise _translate(error) from None
        return response

    @router.get("/v1/material-uploads/{upload_id}")
    def get_upload(upload_id: str, request: Request) -> dict[str, Any]:
        project_id = _project_from_request(request)
        project_authorizer(request, project_id)
        try:
            upload = material_store.get_upload(
                project_id=project_id, upload_id=upload_id, include_terminal=True
            )
            if upload["transfer_status"] == "completed":
                return material_store.complete_upload(
                    project_id=project_id, upload_id=upload_id
                )
            return {"upload": upload}
        except (KeyError, ValueError) as error:
            raise _translate(error) from None

    @router.put("/v1/material-uploads/{upload_id}/parts/{part_number}")
    async def receive_part(
        upload_id: str,
        part_number: int,
        request: Request,
        content_range: str = Header(alias="Content-Range"),
        part_sha256: str = Header(alias="X-Part-SHA256"),
        project_id: str = Header(alias="X-D18-Project-ID"),
    ) -> dict[str, Any]:
        project_authorizer(request, project_id)
        match = _CONTENT_RANGE.fullmatch(content_range)
        if match is None:
            raise _problem(422, "invalid_part_range", "Content-Range is invalid.")
        first_byte, last_byte, total_bytes = map(int, match.groups())
        try:
            expected_bytes = material_store.validate_part_request(
                project_id=project_id,
                upload_id=upload_id,
                part_number=part_number,
                first_byte=first_byte,
                last_byte=last_byte,
                total_bytes=total_bytes,
            )
            with tempfile.TemporaryFile(mode="w+b") as source:
                received_bytes = 0
                async for chunk in request.stream():
                    received_bytes += len(chunk)
                    if received_bytes > expected_bytes:
                        raise _problem(
                            413,
                            "upload_body_too_large",
                            "Upload body exceeded the declared part range.",
                        )
                    source.write(chunk)
                source.seek(0)
                return material_store.receive_part(
                    project_id=project_id,
                    upload_id=upload_id,
                    part_number=part_number,
                    first_byte=first_byte,
                    last_byte=last_byte,
                    total_bytes=total_bytes,
                    source=source,
                    sha256=part_sha256,
                )
        except (KeyError, ValueError) as error:
            raise _translate(error) from None

    @router.post("/v1/material-uploads/{upload_id}/complete")
    def complete_upload(
        upload_id: str, payload: CompleteUploadRequest, request: Request
    ) -> dict[str, Any]:
        project_id = _project_from_request(request)
        project_authorizer(request, project_id)
        try:
            return material_store.complete_upload(
                project_id=project_id,
                upload_id=upload_id,
                scope=payload.scope,
                explicit_duplicate=payload.explicit_duplicate,
            )
        except (KeyError, ValueError) as error:
            raise _translate(error) from None

    @router.post("/v1/material-uploads/{upload_id}/cancel")
    def cancel_upload(upload_id: str, request: Request) -> dict[str, Any]:
        project_id = _project_from_request(request)
        project_authorizer(request, project_id)
        try:
            return {"upload": material_store.cancel_upload(project_id=project_id, upload_id=upload_id)}
        except (KeyError, ValueError) as error:
            raise _translate(error) from None

    return router


def _project_from_request(request: Request) -> str:
    project_id = request.headers.get("x-d18-project-id", "").strip()
    if not project_id:
        raise _problem(422, "project_id_required", "X-D18-Project-ID is required.")
    return project_id
