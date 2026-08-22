"""Strict, small Qingxiaoda/OpenAI compatibility types and projections."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from .contracts import validate_content_ref

FINISH_REASONS = frozenset({"stop", "length", "tool_calls", "content_filter"})
ROLES = frozenset({"system", "user", "assistant"})
PART_TYPES = frozenset({"text", "image_url", "input_audio", "file"})
MAX_SOURCE_MATERIALS = 32


def _validate_material_ids(values: list[str]) -> list[str]:
    if len(values) != len(set(values)) or any(
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 255
        or "/" in value
        or "\\" in value
        for value in values
    ):
        raise ValueError("material_ids are invalid")
    return values


class SourceMaterialContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source material text is empty")
        return value


class SourceMaterialInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=3, max_length=127)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    authority: Literal["user_supplied_project_input"]
    content: SourceMaterialContent

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("source material name is unsafe")
        return value

    @field_validator("media_type")
    @classmethod
    def validate_media_type(cls, value: str) -> str:
        if "/" not in value or any(character.isspace() for character in value):
            raise ValueError("source material media type is invalid")
        return value


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str | list[dict[str, Any]]

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        schema = handler(core_schema)
        content = schema.get("properties", {}).get("content", {})
        variants = content.get("anyOf", [])
        array_schema = next(
            (item for item in variants if item.get("type") == "array"), None
        )
        if array_schema is not None:
            array_schema["items"] = {
                "oneOf": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["type", "text"],
                        "properties": {
                            "type": {"const": "text"},
                            "text": {"type": "string"},
                        },
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["type", "image_url"],
                        "properties": {
                            "type": {"const": "image_url"},
                            "image_url": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["url"],
                                "properties": {"url": {"type": "string"}},
                            },
                        },
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["type", "input_audio"],
                        "properties": {
                            "type": {"const": "input_audio"},
                            "input_audio": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["url", "format"],
                                "properties": {
                                    "url": {"type": "string"},
                                    "format": {"type": "string"},
                                },
                            },
                        },
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["type", "file"],
                        "properties": {
                            "type": {"const": "file"},
                            "file": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["filename"],
                                "properties": {
                                    "url": {"type": "string"},
                                    "file_id": {"type": "string"},
                                    "filename": {"type": "string"},
                                },
                                "oneOf": [
                                    {"required": ["url"]},
                                    {"required": ["file_id"]},
                                ],
                            },
                        },
                    },
                ]
            }
        return schema

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str | list[dict[str, Any]]):
        if isinstance(value, str):
            return value
        for part in value:
            if not isinstance(part, dict) or part.get("type") not in PART_TYPES:
                raise ValueError("unsupported content part")
            part_type = part["type"]
            if part_type == "text":
                if set(part) != {"type", "text"} or not isinstance(part["text"], str):
                    raise ValueError("invalid text part")
                continue
            payload = part.get(part_type)
            if not isinstance(payload, dict):
                raise ValueError("multimodal parts require an object payload")
            if part_type == "image_url":
                if set(payload) != {"url"} or not isinstance(payload["url"], str):
                    raise ValueError("multimodal content must use a URL")
            elif part_type == "input_audio":
                if set(payload) != {"url", "format"} or not all(
                    isinstance(payload.get(key), str) and payload[key].strip()
                    for key in ("url", "format")
                ):
                    raise ValueError("input audio requires URL and format")
                if len(payload["format"]) > 32:
                    raise ValueError("input audio format is invalid")
            elif part_type == "file":
                allowed = set(payload) & {"url", "file_id"}
                if len(allowed) != 1 or set(payload) != allowed | {"filename"}:
                    raise ValueError(
                        "file requires filename and exactly one url or file_id"
                    )
                if not isinstance(payload[next(iter(allowed))], str):
                    raise ValueError("file reference must be a string")
                filename = payload.get("filename")
                if (
                    not isinstance(filename, str)
                    or not filename.strip()
                    or len(filename) > 255
                    or "/" in filename
                    or "\\" in filename
                ):
                    raise ValueError("file filename is invalid")
            if any("base64" in str(item).casefold() for item in payload.values()):
                raise ValueError("base64 content is forbidden")
        return value


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    model: str | None = None
    messages: list[ChatMessage] = Field(min_length=1)
    stream: StrictBool = False
    max_tokens: int | None = Field(default=None, ge=1)
    stop: str | list[str] | None = None
    session_id: str | None = Field(
        default=None,
        alias="sessionId",
        max_length=255,
        exclude=True,
    )
    research_id: str | None = None
    source_materials: list[SourceMaterialInput] = Field(
        default_factory=list,
        max_length=MAX_SOURCE_MATERIALS,
        deprecated=True,
    )
    material_ids: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("material_ids")
    @classmethod
    def validate_material_ids(cls, values: list[str]) -> list[str]:
        return _validate_material_ids(values)


def is_qingxiaoda_connection_probe(
    request: ChatCompletionRequest,
    *,
    allowed_models: tuple[str, ...],
) -> bool:
    """Recognize Qingxiaoda's exact non-scientific compatibility probe."""

    if request.model not in (None, "") and request.model not in allowed_models:
        return False
    if request.research_id is not None and re.fullmatch(
        r"chat-[0-9a-f]{24}", request.research_id
    ) is None:
        return False
    if (
        request.stream is not True
        or request.max_tokens != 1
        or request.stop is not None
        or request.session_id is not None
        or request.__dict__.get("source_materials")
        or request.material_ids
        or len(request.messages) != 1
    ):
        return False
    message = request.messages[0]
    return message.role == "user" and message.content == "hi"


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    research_id: str
    messages: list[ChatMessage] = Field(min_length=1)
    source_materials: list[SourceMaterialInput] = Field(
        default_factory=list,
        max_length=MAX_SOURCE_MATERIALS,
        deprecated=True,
    )
    material_ids: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("material_ids")
    @classmethod
    def validate_material_ids(cls, values: list[str]) -> list[str]:
        return _validate_material_ids(values)


class MaterialSupplementPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_material: SourceMaterialInput | None = Field(
        default=None, deprecated=True
    )
    material_ids: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("material_ids")
    @classmethod
    def validate_material_ids(cls, values: list[str]) -> list[str]:
        return _validate_material_ids(values)

    @model_validator(mode="after")
    def validate_material_route(self):
        if (self.source_material is None) == (not self.material_ids):
            raise ValueError(
                "material input requires exactly one inline source or material_ids"
            )
        return self


class DecisionInputPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str = Field(min_length=1, max_length=255)
    decision: str = Field(min_length=1, max_length=4096)


class JobInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_id: str = Field(min_length=1)
    kind: Literal["material", "decision"]
    payload: MaterialSupplementPayload | DecisionInputPayload

    @model_validator(mode="after")
    def validate_payload_kind(self):
        if self.kind == "material" and not isinstance(
            self.payload, MaterialSupplementPayload
        ):
            raise ValueError("material input requires one typed source material")
        if self.kind == "decision" and not isinstance(
            self.payload, DecisionInputPayload
        ):
            raise ValueError("decision input requires a typed decision")
        return self


class ContinuationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_id: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=32_768)

    @field_validator("input_id", "text")
    @classmethod
    def validate_non_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("continuation value is empty")
        return value


JobStatus = Literal[
    "queued",
    "leased",
    "running",
    "waiting_for_input",
    "cancelling",
    "completed",
    "bounded",
    "failed",
    "cancelled",
]


class ContentRefResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class InputRequiredResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interaction_kind: str | None = None
    authorization_required: bool | None = None
    question_contract: Literal["d18.missing_fact_question/v1"] | None = None
    question_count: int | None = Field(default=None, ge=1, le=1)
    question: str | None = None
    missing_fields: list[str] | None = None


class RuntimeBoundaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Literal[
        "temporarily_unavailable", "invalid_input", "unexpected_failure"
    ]
    boundary: str
    failure_code: str
    retryable: bool
    recovery_route: str
    retry_after_seconds: int | None = Field(default=None, ge=0, le=86_400)


class RuntimeReleaseIdentityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: Literal["d18.runtime_release_identity/v1"]
    release_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    backend_commit: str = Field(pattern=r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
    frontend_build_id: str = Field(min_length=1, max_length=255)


class ServiceReplayEvidenceAuthenticationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: Literal["d18.service_idempotency_replay_authentication/v1"]
    algorithm: Literal["hmac-sha256"]
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tag: str = Field(pattern=r"^[0-9a-f]{64}$")


class ServiceIdempotencyReplayEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: Literal["d18.service_idempotency_replay_evidence/v1"]
    job_id: str
    research_id: str
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_ref_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    release_identity: RuntimeReleaseIdentityResponse
    authentication: ServiceReplayEvidenceAuthenticationResponse


class JobCreatedResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    job_id: str
    status: JobStatus
    request_ref: ContentRefResponse | None = None
    input_ref: ContentRefResponse | None = None
    runtime_release: RuntimeReleaseIdentityResponse | None = None
    idempotencyReplayEvidence: ServiceIdempotencyReplayEvidenceResponse | None = None


class PublicJobInputReceiptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: Literal["d18.public_job_input_receipt/v1"]
    input_id: str
    kind: Literal["decision"]
    decision_id: str
    decision: str
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ContinuationCreatedResponse(JobCreatedResponse):
    parent_job_id: str
    project_id: str


class JobViewResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    job_id: str
    status: JobStatus
    project_id: str | None = None
    input_required: InputRequiredResponse | None = None
    progress_receipt: dict[str, Any] | None = None
    input_receipt: PublicJobInputReceiptResponse | None = None
    runtime_boundary: RuntimeBoundaryResponse | None = None
    runtime_release: RuntimeReleaseIdentityResponse | None = None


class PublicResearchRecoveryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: Literal["d18.public_research_recovery/v1"]
    research_id: str
    objective: str
    user_messages: list[str]
    job: JobViewResponse


class PublicAttachmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    url: str
    fileUrl: str
    fileName: str
    fileType: str
    mimeType: str


class PublicDomainAcceptanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = None
    claim_ceiling: str | None = None
    may_promote_to_official: bool | None = None
    dropped_numeric_claim_count: int | None = None
    errors: list[str] = Field(default_factory=list)


class PublicCitationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citationId: str
    materialId: str
    revisionId: str
    displayName: str
    locator: dict[str, Any]
    evidenceUrl: str
    sourceAuthority: str
    processingStatus: str


class PublicOfficialSourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sourceId: str
    authority: Literal["official_public_source"]
    sourceAuthority: str
    requestedUrl: str
    finalUrl: str
    accessedAt: str
    bodySha256: str
    receiptSha256: str
    parseLocation: str
    sourceVersion: str
    snapshotVersion: str


class PublicOfficialClaimBindingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claimId: str
    statement: str
    modality: str
    sourceIds: list[str]
    sourceSha256s: list[str]


class PublicNativeAuthorityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["TianGong"]
    capabilityId: Literal["tiangong.native.tidas-sdk"]
    sourceIdentity: Literal["@tiangong-lca/tidas-sdk"]
    sourceVersion: str
    sourceIntegrity: str
    contentSha256: str


class PublicScientificBoundaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lcaMethod: Literal["not_selected"]
    exchangeProvider: Literal["not_selected"]
    quantityUnit: Literal["not_evaluated"]
    scientificResultCreated: Literal[False]


class PublicSourceUseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    materialId: str
    revisionId: str
    displayName: str
    status: Literal[
        "used",
        "available_not_used",
        "unavailable_partial",
        "conflict",
    ]
    reason: str
    sourceAuthority: str
    processingStatus: str
    selectedLocatorCount: int = 0
    citedLocatorCount: int = 0


class PublicMaterialLimitationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    blocksStrongerConclusion: bool
    materialId: str | None = None
    revisionId: str | None = None


class ResearchResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    claim_ceiling: str
    answer: str | None = None
    summary: str | None = None
    bounded_conclusion: str | None = None
    values: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    domain_acceptance: PublicDomainAcceptanceResponse | None = None
    official_object_ids: list[str] = Field(default_factory=list)
    source_sha256s: list[str] = Field(default_factory=list)
    officialSources: list[PublicOfficialSourceResponse] = Field(
        default_factory=list
    )
    officialClaimBindings: list[PublicOfficialClaimBindingResponse] = Field(
        default_factory=list
    )
    nativeAuthority: PublicNativeAuthorityResponse | None = None
    scientificBoundary: PublicScientificBoundaryResponse | None = None
    citations: list[PublicCitationResponse] = Field(default_factory=list)
    sourceUse: list[PublicSourceUseResponse] = Field(default_factory=list)
    materialLimitations: list[PublicMaterialLimitationResponse] = Field(
        default_factory=list
    )
    route: str | None = None
    routeFamily: Literal[
        "direct_fast_path", "evidence_research", "full_lca_workflow"
    ] | None = None
    methods: list[str] = Field(default_factory=list)
    completedStateChanges: list[str] = Field(default_factory=list)


class PublicExecutionCountsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: int = Field(ge=0)
    tool: int = Field(ge=0)
    external_read: int = Field(ge=0)
    external_write: int = Field(ge=0)
    useless_retry: int = Field(ge=0)


class PublicModelCallResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    adapter_id: str
    duration_ms: int | float = Field(ge=0)
    usage: dict[str, int]


class PublicRunTraceRefResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    last_event_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    trace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_count: int = Field(ge=1)


class PublicAcceptedChangeCountsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formal_result: int = Field(ge=0)
    gap_resolution: int = Field(ge=0)


class PublicProjectRecordReceiptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    last_event_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_count: int = Field(ge=1)
    accepted_change_counts: PublicAcceptedChangeCountsResponse | None = None


class PublicRunControlReceiptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heartbeat_count: int = Field(ge=0)
    cpu_sample_count: int = Field(ge=0)
    max_observed_cpu_ms: int | None = Field(default=None, ge=0)
    max_observed_elapsed_ms: int | None = Field(default=None, ge=0)
    max_checkpoint_event_count: int | None = Field(default=None, ge=0)


class PublicExecutionReceiptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: Literal["lca_research_kernel.public_execution_receipt/v1"]
    research_id: str
    counts: PublicExecutionCountsResponse
    model_calls: list[PublicModelCallResponse]
    first_model_request_ms: int | None = Field(default=None, ge=0)
    elapsed_ms: dict[str, int | None]
    durations_ms: dict[str, int]
    trace_refs: list[PublicRunTraceRefResponse]
    project_record: PublicProjectRecordReceiptResponse
    run_control: PublicRunControlReceiptResponse
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ServiceProductEvidenceAuthenticationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: Literal["d18.service_product_evidence_authentication/v1"]
    algorithm: Literal["hmac-sha256"]
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tag: str = Field(pattern=r"^[0-9a-f]{64}$")


class ServiceProductMaterialEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_id: str
    revision_id: str
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_bytes: int = Field(ge=1)
    processing_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    qualified_locator_count: int = Field(ge=1)


class ServiceProductRecordEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    last_event_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_count: int = Field(ge=1)


class ServiceReleaseRoleEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal[
        "backend_service",
        "accepted_job_request",
        "worker_execution",
        "result_projection",
    ]
    identity: RuntimeReleaseIdentityResponse


class ServiceProductEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: Literal["d18.service_product_evidence/v1"]
    job_id: str
    research_id: str
    release_manifest: dict[str, Any]
    release_identity: RuntimeReleaseIdentityResponse
    release_roles: list[ServiceReleaseRoleEvidenceResponse] = Field(min_length=4, max_length=4)
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    project_record: ServiceProductRecordEvidenceResponse
    materials: list[ServiceProductMaterialEvidenceResponse]
    official_sources: list[PublicOfficialSourceResponse]
    authentication: ServiceProductEvidenceAuthenticationResponse


class ResultViewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: str = "d18.public_result_projection/v1"
    research_id: str | None = None
    status: str
    result: ResearchResultResponse | None = None
    attachments: list[PublicAttachmentResponse] = Field(default_factory=list)
    runtime_release: RuntimeReleaseIdentityResponse | None = None
    executionReceipt: PublicExecutionReceiptResponse | None = None
    productEvidence: ServiceProductEvidenceResponse | None = None


def normalize_usage(value: Mapping[str, Any]) -> dict[str, int]:
    required = ("prompt_tokens", "completion_tokens", "total_tokens")
    result: dict[str, int] = {}
    for key in required:
        item = value.get(key)
        if not isinstance(item, int) or item < 0:
            raise ValueError("usage must contain non-negative integer token counts")
        result[key] = item
    if result["total_tokens"] != result["prompt_tokens"] + result["completion_tokens"]:
        raise ValueError("usage total must equal prompt plus completion tokens")
    return result


def require_accepted_runtime_result(result: Mapping[str, Any]) -> dict[str, Any]:
    content = result.get("content")
    if not isinstance(content, str):
        raise ValueError("accepted result content is missing")
    finish_reason = result.get("finish_reason")
    if finish_reason not in FINISH_REASONS:
        raise ValueError("accepted result finish reason is invalid")
    project_ref = validate_content_ref(result.get("project_record_ref"))
    if project_ref is None:
        raise ValueError("accepted result requires a Project Record reference")
    attachments = result.get("attachments", [])
    if not isinstance(attachments, list):
        raise ValueError("attachments must be a list")
    return {
        "content": content,
        "finish_reason": finish_reason,
        "usage": normalize_usage(result.get("usage", {})),
        "project_record_ref": project_ref,
        "attachments": attachments,
    }


def build_completion(
    *,
    completion_id: str,
    model: str,
    content: str,
    finish_reason: str,
    usage: Mapping[str, Any],
    attachments: list[dict[str, Any]],
    project_record_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if finish_reason not in FINISH_REASONS:
        raise ValueError("finish reason is outside the compatibility contract")
    body: dict[str, Any] = {
        "id": completion_id,
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": normalize_usage(usage),
    }
    if attachments:
        body["x_soda"] = {"attachments": attachments}
    if project_record_ref is not None:
        validated = validate_content_ref(project_record_ref)
        if validated is None:
            raise ValueError("Project Record reference is invalid")
        body["x_d18"] = {
            "projectRecordRef": {"sha256": validated["sha256"]}
        }
    return body
