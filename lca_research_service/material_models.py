"""Pydantic transport models backed by kernel-owned material validators."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationInfo, model_validator

from lca_research_kernel.material_sources import (
    validate_evidence_locator,
    validate_input_artifact,
    validate_material_revision,
    validate_material_use_decision,
    validate_processing_receipt,
    validate_research_material,
)


class _KernelValidatedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @classmethod
    def _validate_kernel(cls, value: dict[str, Any], info: ValidationInfo) -> dict:
        raise NotImplementedError

    @model_validator(mode="before")
    @classmethod
    def validate_kernel_contract(cls, value: object, info: ValidationInfo) -> object:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise ValueError("material contract must be an object")
        return cls._validate_kernel(value, info)


class InputArtifactModel(_KernelValidatedModel):
    contract: str
    artifact_id: str
    project_id: str
    sha256: str
    byte_size: int
    detected_media_type: str
    original_filename: str
    storage_ref: dict[str, Any]
    source_kind: str
    source_locator: dict[str, Any]
    created_at: str
    security_status: str

    @classmethod
    def _validate_kernel(cls, value: dict[str, Any], info: ValidationInfo) -> dict:
        return validate_input_artifact(value)


class ResearchMaterialModel(_KernelValidatedModel):
    contract: str
    material_id: str
    project_id: str
    display_name: str
    material_kind: str
    current_revision_id: str
    scope: str
    authority: str
    research_classification: str
    labels: list[str]
    created_by: str
    created_at: str
    deleted_at: str | None

    @classmethod
    def _validate_kernel(cls, value: dict[str, Any], info: ValidationInfo) -> dict:
        return validate_research_material(value)


class MaterialRevisionModel(_KernelValidatedModel):
    contract: str
    revision_id: str
    material_id: str
    artifact_ref: dict[str, Any]
    parent_revision_id: str | None
    processor_profile: str
    processing_status: str
    extraction_refs: list[dict[str, Any]]
    manifest_ref: dict[str, Any] | None
    created_at: str

    @classmethod
    def _validate_kernel(cls, value: dict[str, Any], info: ValidationInfo) -> dict:
        return validate_material_revision(value)


class EvidenceLocatorModel(_KernelValidatedModel):
    contract: str
    locator_id: str
    revision_id: str
    locator_kind: str
    locator: dict[str, Any]
    content_hash: str
    preview_text_or_thumbnail_ref: dict[str, Any] | None
    assurance_acceptance_ref: dict[str, Any] | None
    scientific_status: str | None = None

    @classmethod
    def _validate_kernel(cls, value: dict[str, Any], info: ValidationInfo) -> dict:
        return validate_evidence_locator(value)


class MaterialProcessingReceiptModel(_KernelValidatedModel):
    contract: str
    revision_id: str
    processor: dict[str, Any]
    processing_mode: str
    status: str
    input_sha256: str
    detected_type: dict[str, Any]
    output_refs: list[dict[str, Any]]
    completeness: dict[str, Any]
    warnings: list[Any]
    failure_category: str | None
    duration_ms: int
    replay_key: str
    candidate_only: bool

    @classmethod
    def _validate_kernel(cls, value: dict[str, Any], info: ValidationInfo) -> dict:
        return validate_processing_receipt(value)


class MaterialUseDecisionModel(_KernelValidatedModel):
    contract: str
    task_id: str | None = None
    allowed_revision_ids: list[str]
    selected_evidence_locator_ids: list[str]
    selection_reasons: dict[str, str]
    exclusions: list[Any]
    context_mode: str
    expected_state_change: dict[str, Any]
    claim_ceiling: str | None = None

    @classmethod
    def _validate_kernel(cls, value: dict[str, Any], info: ValidationInfo) -> dict:
        context = info.context
        if not isinstance(context, dict) or not isinstance(context.get("locator_index"), dict):
            raise ValueError("MaterialUseDecision validation requires locator_index context")
        return validate_material_use_decision(
            value,
            locator_index=context["locator_index"],
        )
