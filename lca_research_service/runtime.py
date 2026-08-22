"""Thin service adapter over the sole D-18 Product Interface."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from lca_research_kernel.product_port import ProductResearchPort
from lca_research_kernel.material_resolution import MaterialResolutionBinding

from .contracts import validate_content_ref


_MATERIAL_TERMINAL_STATUSES = frozenset({"ready", "partial", "needs_user"})
_MATERIAL_ARTIFACT_MAX_BYTES = 128 * 1024 * 1024
_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ProductResultNotAccepted(ValueError):
    """The Product Interface reached a bounded state without a deliverable answer."""


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _opaque_ref(reference: Mapping[str, object]) -> dict[str, str]:
    digest = reference.get("sha256")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ValueError("material artifact reference SHA-256 is invalid")
    artifact_id = reference.get("artifact_id")
    if artifact_id is not None and artifact_id != digest:
        raise ValueError("material artifact identifier drifted")
    return {"artifact_id": digest, "sha256": digest}


def _frozen_material_revision_refs(
    material_ids: object,
    revision_refs: object,
) -> list[dict[str, Any]]:
    """Bind ordered public material IDs to their immutable revision identities."""

    if (
        not isinstance(material_ids, list)
        or not material_ids
        or not isinstance(revision_refs, list)
        or len(revision_refs) != len(material_ids)
    ):
        raise ValueError("service material revisions are not frozen")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for material_id, reference in zip(material_ids, revision_refs, strict=True):
        if (
            not isinstance(material_id, str)
            or not material_id
            or not isinstance(reference, Mapping)
            or reference.get("material_id") != material_id
            or not isinstance(reference.get("revision_id"), str)
            or not reference["revision_id"]
        ):
            raise ValueError("service material revision identities drifted")
        identity = (material_id, str(reference["revision_id"]))
        if identity in seen:
            raise ValueError("service material revision identities drifted")
        seen.add(identity)
        normalized.append(deepcopy(dict(reference)))
    return normalized


class MaterialResolutionError(ValueError):
    """Public-safe category for a rejected material identity or binding."""


class MaterialResolver:
    """Resolve operational material candidates without promoting their contents."""

    def __init__(
        self,
        *,
        data_root: object,
        material_store: object,
        input_artifact_store: object,
    ):
        self.data_root = Path(data_root).absolute()
        self.material_store = material_store
        self.input_artifact_store = input_artifact_store

    def _read_ref(
        self, reference: Mapping[str, object], *, label: str
    ) -> dict[str, Any]:
        payload = self._read_ref_bytes(reference, label=label)
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ValueError(f"{label} is invalid JSON") from error
        if not isinstance(value, dict):
            raise ValueError(f"{label} must contain an object")
        return value

    def _read_ref_bytes(
        self, reference: Mapping[str, object], *, label: str
    ) -> bytes:
        normalized = validate_content_ref(reference)
        if normalized is None:
            raise ValueError(f"{label} reference is absent")

        raw_path = normalized["path"]
        parts = raw_path.split("/")
        if (
            "\x00" in raw_path
            or "\\" in raw_path
            or raw_path.startswith("/")
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ValueError(f"{label} reference path is invalid")

        directory_flags = (
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
            | os.O_CLOEXEC
        )
        file_flags = (
            os.O_RDONLY
            | os.O_NOFOLLOW
            | os.O_CLOEXEC
            | os.O_NONBLOCK
        )
        descriptors: list[int] = []
        try:
            root_descriptor = os.open(self.data_root, directory_flags)
            descriptors.append(root_descriptor)
            root_status = os.fstat(root_descriptor)
            if not stat.S_ISDIR(root_status.st_mode):
                raise ValueError(f"{label} reference is unavailable")

            parent_descriptor = root_descriptor
            for part in parts[:-1]:
                parent_descriptor = os.open(
                    part,
                    directory_flags,
                    dir_fd=parent_descriptor,
                )
                descriptors.append(parent_descriptor)
                if not stat.S_ISDIR(os.fstat(parent_descriptor).st_mode):
                    raise ValueError(f"{label} reference is unavailable")

            file_descriptor = os.open(
                parts[-1],
                file_flags,
                dir_fd=parent_descriptor,
            )
            descriptors.append(file_descriptor)
            before = os.fstat(file_descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_size > _MATERIAL_ARTIFACT_MAX_BYTES
            ):
                raise ValueError(f"{label} reference is unavailable")

            payload = os.read(
                file_descriptor,
                _MATERIAL_ARTIFACT_MAX_BYTES + 1,
            )

            after = os.fstat(file_descriptor)
            stable_metadata = (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
            if (
                not stat.S_ISREG(after.st_mode)
                or after.st_nlink != 1
                or len(payload) != before.st_size
                or len(payload) > _MATERIAL_ARTIFACT_MAX_BYTES
                or any(
                    getattr(before, field) != getattr(after, field)
                    for field in stable_metadata
                )
            ):
                raise ValueError(f"{label} reference changed while reading")

            if hashlib.sha256(payload).hexdigest() != normalized["sha256"]:
                raise ValueError(f"{label} reference drifted")
        except OSError as error:
            raise ValueError(f"{label} reference is unavailable") from error
        finally:
            body_error = sys.exception()
            cleanup_error: OSError | None = None
            for descriptor in reversed(descriptors):
                try:
                    os.close(descriptor)
                except OSError as error:
                    if cleanup_error is None:
                        cleanup_error = error
            if cleanup_error is not None:
                if body_error is None:
                    raise ValueError(
                        f"{label} reference is unavailable"
                    ) from cleanup_error
                body_error.add_note(f"{label} descriptor cleanup also failed")

        return payload

    def resolve_project_record_content(
        self,
        project_id: str,
        content_kind: str,
        reference: Mapping[str, object],
    ) -> bytes:
        """Read one existing Task-3 content-addressed artifact for Project Record."""

        if (
            not isinstance(project_id, str)
            or _PROJECT_ID.fullmatch(project_id) is None
        ):
            raise ValueError("material project identity is absent")
        if (
            not isinstance(reference, Mapping)
            or set(reference) != {"artifact_id", "sha256"}
            or reference.get("artifact_id") != reference.get("sha256")
        ):
            raise ValueError("Project Record material content reference is invalid")
        opaque = _opaque_ref(reference)
        digest = opaque["sha256"]
        directory = {
            "material_processing_receipt": "receipts",
            "evidence_unit": "evidence-units",
        }.get(content_kind)
        if directory is None:
            raise ValueError("Project Record material content kind is unsupported")
        relative = (
            Path("projects")
            / project_id
            / "material-processing"
            / directory
            / "sha256"
            / digest[:2]
            / f"{digest}.json"
        )
        return self._read_ref_bytes(
            {"path": relative.as_posix(), "sha256": digest},
            label=f"Project Record {content_kind}",
        )

    def _read_processing_output(
        self,
        project_id: str,
        reference: Mapping[str, object],
        *,
        directory: str,
        label: str,
    ) -> dict[str, Any]:
        opaque = _opaque_ref(reference)
        digest = opaque["sha256"]
        relative = (
            Path("projects")
            / project_id
            / "material-processing"
            / directory
            / "sha256"
            / digest[:2]
            / f"{digest}.json"
        )
        return self._read_ref(
            {"path": relative.as_posix(), "sha256": digest}, label=label
        )

    def resolve_one(
        self,
        project_id: str,
        material_id: str,
        *,
        revision_id: str | None = None,
        filename: str | None = None,
        include_deleted: bool = False,
    ) -> dict[str, Any]:
        from lca_research_kernel.material_sources import (
            validate_evidence_locator,
            validate_material_revision,
            validate_processing_receipt,
            validate_research_material,
        )

        if not isinstance(project_id, str) or not project_id:
            raise ValueError("material project identity is absent")
        if not isinstance(material_id, str) or not material_id:
            raise ValueError("material identity is absent")
        stored = (
            self.material_store.get_material_revision(
                project_id=project_id,
                material_id=material_id,
                revision_id=revision_id,
                include_deleted=include_deleted,
            )
            if revision_id is not None
            else self.material_store.get_material(
                project_id=project_id,
                material_id=material_id,
                include_deleted=include_deleted,
            )
        )
        material_row = stored.get("material")
        revision_row = stored.get("revision")
        if not isinstance(material_row, Mapping) or not isinstance(
            revision_row, Mapping
        ):
            raise ValueError("operational material projection is invalid")
        if (
            material_row.get("project_id") != project_id
            or material_row.get("material_id") != material_id
            or revision_row.get("material_id") != material_id
            or (
                revision_id is None
                and material_row.get("current_revision_id")
                != revision_row.get("revision_id")
            )
        ):
            raise ValueError("material ownership or revision identity drifted")
        status = revision_row.get("processing_status")
        if status not in _MATERIAL_TERMINAL_STATUSES:
            raise ValueError("material revision is not ready for scientific use")
        artifact_id = revision_row.get("input_artifact_id")
        if not isinstance(artifact_id, str):
            raise ValueError("material input artifact identity is absent")
        artifact = self.input_artifact_store.describe(
            research_id=project_id, artifact_id=artifact_id
        )
        if filename is not None and artifact.get("original_filename") != filename:
            raise PermissionError("material filename differs from its immutable input")

        raw_receipt_ref = revision_row.get("receipt_ref_json")
        if not isinstance(raw_receipt_ref, str):
            raise ValueError("processed material receipt is absent")
        try:
            receipt_ref = json.loads(raw_receipt_ref)
        except json.JSONDecodeError as error:
            raise ValueError("processed material receipt reference is invalid") from error
        if not isinstance(receipt_ref, Mapping):
            raise ValueError("processed material receipt reference is invalid")
        receipt = validate_processing_receipt(
            self._read_ref(receipt_ref, label="material processing receipt")
        )
        if (
            receipt["revision_id"] != revision_row["revision_id"]
            or receipt["input_sha256"] != artifact_id
            or receipt["status"] != status
            or receipt.get("candidate_only") is not True
        ):
            raise ValueError("material processing receipt identity drifted")

        evidence_candidates: list[tuple[dict[str, str], dict[str, Any]]] = []
        locator_candidates: list[tuple[dict[str, str], dict[str, Any]]] = []
        for output_ref in receipt["output_refs"]:
            opaque = _opaque_ref(output_ref)
            try:
                evidence = self._read_processing_output(
                    project_id,
                    opaque,
                    directory="evidence-units",
                    label="material evidence units",
                )
            except ValueError:
                evidence = None
            if isinstance(evidence, dict):
                evidence_candidates.append((opaque, evidence))
            try:
                locator_index = self._read_processing_output(
                    project_id,
                    opaque,
                    directory="locators",
                    label="material locator index",
                )
            except ValueError:
                locator_index = None
            if isinstance(locator_index, dict):
                locator_candidates.append((opaque, locator_index))
        if len(evidence_candidates) != 1 or len(locator_candidates) != 1:
            raise ValueError("material processing outputs are incomplete or ambiguous")
        evidence_ref, evidence = evidence_candidates[0]
        locator_index_ref, locator_index = locator_candidates[0]
        if (
            evidence.get("contract") != "lca_research_service.evidence_units/v1"
            or evidence.get("revision_id") != revision_row["revision_id"]
            or evidence.get("input_sha256") != artifact_id
            or evidence.get("candidate_only") is not True
        ):
            raise ValueError("material evidence units identity drifted")
        if (
            locator_index.get("contract")
            != "lca_research_service.evidence_locator_index/v1"
            or locator_index.get("revision_id") != revision_row["revision_id"]
            or locator_index.get("candidate_only") is not True
        ):
            raise ValueError("material locator index identity drifted")
        units = evidence.get("units")
        raw_locators = locator_index.get("locators")
        if (
            not isinstance(units, list)
            or not isinstance(raw_locators, list)
            or len(units) != len(raw_locators)
            or not units
        ):
            raise ValueError("material evidence and locator cardinality drifted")
        locators: list[dict[str, Any]] = []
        bindings: list[dict[str, Any]] = []
        for unit, raw_locator in zip(units, raw_locators, strict=True):
            if not isinstance(unit, Mapping) or not isinstance(
                unit.get("source_span"), Mapping
            ):
                raise ValueError("material evidence unit source span is absent")
            locator = validate_evidence_locator(raw_locator)
            span = unit["source_span"]
            unit_hash = hashlib.sha256(_canonical_json_bytes(unit)).hexdigest()
            if (
                locator["revision_id"] != revision_row["revision_id"]
                or locator["content_hash"] != unit_hash
                or locator["locator_kind"] != span.get("kind")
                or locator["locator"] != span.get("locator")
                or locator.get("scientific_status") != "candidate"
                or locator.get("assurance_acceptance_ref") is not None
            ):
                raise ValueError("material evidence locator content drifted")
            locators.append(locator)
            bindings.append(
                {
                    "locator_id": locator["locator_id"],
                    "content_hash": unit_hash,
                    "source_span": {
                        "kind": span["kind"],
                        "locator": deepcopy(span["locator"]),
                    },
                    "evidence_unit": deepcopy(dict(unit)),
                }
            )

        created_at = revision_row.get("created_at")
        research_material = validate_research_material(
            {
                "contract": "lca_research_kernel.research_material/v1",
                "material_id": material_id,
                "project_id": project_id,
                "display_name": artifact["original_filename"],
                "material_kind": "document",
                "current_revision_id": revision_row["revision_id"],
                "scope": material_row["scope"],
                "authority": "user_supplied_project_input",
                "research_classification": "candidate",
                "labels": [],
                "created_by": "user",
                "created_at": created_at,
                "deleted_at": None,
            }
        )
        source_lineage = None
        if self.material_store.revision_source_kind(
            project_id=project_id,
            material_id=material_id,
            revision_id=revision_row["revision_id"],
        ) == "qingxiaoda_url":
            source_identity = self.material_store.remote_source_lineage_identity(
                project_id=project_id,
                material_id=material_id,
                revision_id=revision_row["revision_id"],
            )
            source_identity_sha256 = source_identity["source_identity_sha256"]
            intake_identity_sha256 = source_identity["intake_identity_sha256"]
            matches = [
                lineage
                for lineage in self.input_artifact_store.list_lineages(
                    research_id=project_id,
                    artifact_id=artifact_id,
                )
                if lineage.get("source_kind") == "qingxiaoda_url"
                and lineage.get("source_identity_sha256")
                == source_identity_sha256
                and (
                    intake_identity_sha256 is None
                    or lineage.get("intake_identity_sha256")
                    == intake_identity_sha256
                )
                and (
                    source_identity["original_filename"] is None
                    or lineage.get("original_filename")
                    == source_identity["original_filename"]
                )
            ]
            if len(matches) != 1:
                raise ValueError("remote material source lineage is ambiguous")
            lineage = matches[0]
            source_lineage_basis = {
                "contract": "lca_research_kernel.material_source_lineage/v1",
                "artifact_id": artifact_id,
                "source_kind": "qingxiaoda_url",
                "source_locator": deepcopy(lineage["source_locator"]),
                "source_identity_sha256": source_identity_sha256,
                "original_filename": lineage["original_filename"],
            }
            source_lineage = {
                **source_lineage_basis,
                "lineage_sha256": hashlib.sha256(
                    json.dumps(
                        source_lineage_basis,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
            }
            research_material = validate_research_material(
                {
                    **research_material,
                    "display_name": lineage["original_filename"],
                }
            )
        material_revision = validate_material_revision(
            {
                "contract": "lca_research_kernel.material_revision/v1",
                "revision_id": revision_row["revision_id"],
                "material_id": material_id,
                "artifact_ref": _opaque_ref(artifact["storage_ref"]),
                "parent_revision_id": revision_row.get("supersedes_revision_id"),
                "processor_profile": (
                    f"{receipt['processor']['id']}@{receipt['processor']['version']}"
                ),
                "processing_status": status,
                "extraction_refs": [
                    _opaque_ref(reference) for reference in receipt["output_refs"]
                ],
                "manifest_ref": locator_index_ref,
                "created_at": created_at,
                **(
                    {"source_lineage": source_lineage}
                    if source_lineage is not None
                    else {}
                ),
            }
        )
        return {
            "contract": (
                "lca_research_kernel.source_bound_material_revision/v1"
            ),
            "research_material": research_material,
            "material_revision": material_revision,
            "processing_receipt": receipt,
            "processing_receipt_ref": _opaque_ref(receipt_ref),
            "evidence_units_ref": evidence_ref,
            "locator_index_ref": locator_index_ref,
            "locators": locators,
            "evidence_unit_bindings": bindings,
        }

    def resolve(
        self,
        project_id: str,
        material_ids: object,
        material_revision_refs: object = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(material_ids, list):
            raise ValueError("material_ids must be a list")
        if len(material_ids) != len(set(material_ids)):
            raise ValueError("material_ids must be unique")
        if material_revision_refs is None:
            return [
                self.resolve_one(project_id, material_id)
                for material_id in material_ids
            ]
        if not isinstance(material_revision_refs, list) or len(
            material_revision_refs
        ) != len(material_ids):
            raise ValueError("material revision references are invalid")
        resolved: list[dict[str, Any]] = []
        for material_id, reference in zip(
            material_ids, material_revision_refs, strict=True
        ):
            if not isinstance(reference, Mapping) or reference.get(
                "material_id"
            ) != material_id:
                raise ValueError("material revision reference identity drifted")
            revision_id = reference.get("revision_id")
            artifact_id = reference.get("input_artifact_id")
            if not isinstance(revision_id, str) or not isinstance(artifact_id, str):
                raise ValueError("material revision reference is incomplete")
            bundle = self.resolve_one(
                project_id, material_id, revision_id=revision_id
            )
            if bundle["material_revision"]["artifact_ref"]["sha256"] != artifact_id:
                raise ValueError("material revision artifact identity drifted")
            resolved.append(bundle)
        return resolved

    def preview_projection(
        self,
        project_id: str,
        material_id: str,
        *,
        highlight_locator_id: str | None = None,
    ) -> dict[str, Any]:
        """Return one bounded inert preview and opaque locator projections."""

        bundle = self.resolve_one(project_id, material_id)
        preview_candidates: list[dict[str, Any]] = []
        for reference in bundle["processing_receipt"]["output_refs"]:
            try:
                preview = self._read_processing_output(
                    project_id,
                    reference,
                    directory="previews",
                    label="material preview",
                )
            except ValueError:
                continue
            if isinstance(preview, dict) and preview.get("kind") in {
                "text",
                "spreadsheet",
                "metadata_only",
            }:
                preview_candidates.append(preview)
        if len(preview_candidates) != 1:
            raise ValueError("material preview is incomplete or ambiguous")
        raw_preview = deepcopy(preview_candidates[0])
        raw_preview.pop("candidate_only", None)
        thumbnail = raw_preview.pop("thumbnail_ref", None)
        page_images = raw_preview.pop("page_images", None)
        if thumbnail is not None:
            raw_preview["thumbnail_available"] = True
        if isinstance(page_images, list):
            raw_preview["available_pages"] = [
                item["page"]
                for item in page_images
                if isinstance(item, Mapping) and isinstance(item.get("page"), int)
            ]
        locators = [
            {
                "locator_id": locator["locator_id"],
                "revision_id": locator["revision_id"],
                "locator_kind": locator["locator_kind"],
                "locator": deepcopy(locator["locator"]),
                "scientific_status": locator.get("scientific_status", "candidate"),
            }
            for locator in bundle["locators"]
        ]
        highlight = None
        if highlight_locator_id is not None:
            highlight = next(
                (
                    deepcopy(locator)
                    for locator in locators
                    if locator["locator_id"] == highlight_locator_id
                ),
                None,
            )
            if highlight is None:
                raise KeyError("evidence locator is unavailable")
        return {
            "contract": "lca_research_service.material_preview_projection/v1",
            "material_id": material_id,
            "revision_id": bundle["material_revision"]["revision_id"],
            "processing_status": bundle["processing_receipt"]["status"],
            "scientific_status": "candidate_only",
            "preview": raw_preview,
            "locators": locators,
            "highlight": highlight,
        }

    def evidence_projection(
        self, project_id: str, locator_id: str
    ) -> dict[str, Any]:
        """Resolve a locator inside its owning project and immutable revision chain."""

        materials = self.material_store.list_materials(
            project_id=project_id, deleted="all"
        )
        for row in materials:
            detail = self.material_store.material_detail(
                project_id=project_id, material_id=row["material_id"]
            )
            for revision in detail["revisions"]:
                try:
                    bundle = self.resolve_one(
                        project_id,
                        row["material_id"],
                        revision_id=revision["revision_id"],
                        include_deleted=True,
                    )
                except (KeyError, PermissionError, ValueError):
                    continue
                for locator, binding in zip(
                    bundle["locators"],
                    bundle["evidence_unit_bindings"],
                    strict=True,
                ):
                    if locator["locator_id"] != locator_id:
                        continue
                    unit = deepcopy(binding["evidence_unit"])
                    unit.pop("candidate_only", None)
                    return {
                        "contract": "lca_research_service.evidence_projection/v1",
                        "material_id": row["material_id"],
                        "revision_id": locator["revision_id"],
                        "locator": {
                            "locator_id": locator["locator_id"],
                            "locator_kind": locator["locator_kind"],
                            "locator": deepcopy(locator["locator"]),
                            "scientific_status": locator.get(
                                "scientific_status", "candidate"
                            ),
                        },
                        "evidence_unit": unit,
                    }
        raise KeyError("evidence locator is unavailable")

    def preview_image(
        self, project_id: str, material_id: str, *, page: int | None = None
    ) -> bytes:
        """Read a generated, re-encoded PNG preview without exposing its reference."""

        bundle = self.resolve_one(project_id, material_id)
        for reference in bundle["processing_receipt"]["output_refs"]:
            try:
                preview = self._read_processing_output(
                    project_id,
                    reference,
                    directory="previews",
                    label="material preview",
                )
            except ValueError:
                continue
            selected = preview.get("thumbnail_ref") if isinstance(preview, Mapping) else None
            if page is not None and isinstance(preview, Mapping):
                selected = next(
                    (
                        item.get("ref")
                        for item in preview.get("page_images", [])
                        if isinstance(item, Mapping) and item.get("page") == page
                    ),
                    None,
                )
            if isinstance(selected, Mapping):
                payload = self._read_ref_bytes(
                    selected, label="material generated preview image"
                )
                if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError("material generated preview image is not PNG")
                return payload
        raise KeyError("material preview image is unavailable")


def _message_text(messages: object) -> str:
    if not isinstance(messages, list):
        raise ValueError("service request messages are absent")
    selected: list[str] = []
    for message in messages:
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            selected.append(content.strip())
            continue
        if not isinstance(content, list):
            continue
        for part in content:
            if (
                isinstance(part, Mapping)
                and part.get("type") == "text"
                and isinstance(part.get("text"), str)
                and str(part["text"]).strip()
            ):
                selected.append(str(part["text"]).strip())
    if not selected:
        raise ValueError("service request has no user research objective")
    return "\n".join(selected)


def _last_user_message_text(messages: object) -> str:
    if not isinstance(messages, list):
        raise ValueError("service request messages are absent")
    for message in reversed(messages):
        if isinstance(message, Mapping) and message.get("role") == "user":
            return _message_text([message])
    raise ValueError("service request has no user research objective")


def _accepted_user_answer(result: Mapping[str, Any]) -> str:
    if result.get("status") != "accepted":
        raise ProductResultNotAccepted("Product Interface result is not accepted")
    payload = result.get("result")
    if not isinstance(payload, Mapping):
        raise ValueError("accepted Product Interface result is absent")
    delivery = result.get("delivery", payload.get("human_delivery"))
    if delivery is not None and not isinstance(delivery, Mapping):
        raise ValueError("accepted Product Interface delivery is invalid")
    candidates = []
    if isinstance(delivery, Mapping):
        candidates.append(delivery.get("answer"))
    candidates.extend(
        payload.get(key)
        for key in ("answer", "bounded_conclusion", "summary")
    )
    for candidate in candidates:
        if candidate is None:
            continue
        if not isinstance(candidate, str):
            raise ValueError("accepted Product Interface answer is invalid")
        if candidate.strip():
            return candidate
    raise ValueError("accepted Product Interface answer is absent")


class ProductInterfaceRuntime:
    """Translate service work items without owning research decisions."""

    ready = True

    def __init__(
        self,
        product_interface: ProductResearchPort,
        *,
        data_root: object,
        input_artifact_store: object | None = None,
        material_store: object | None = None,
        owns_material_store: bool = False,
        material_resolution_binding: MaterialResolutionBinding | None = None,
    ):
        self.product_interface = product_interface
        self.data_root = data_root
        self.input_artifact_store = input_artifact_store
        self.material_store = material_store
        self._owns_material_store = owns_material_store
        inherited_material_resolution = getattr(
            product_interface, "material_resolution_binding", None
        )
        self.material_resolution_binding = (
            material_resolution_binding
            if material_resolution_binding is not None
            else inherited_material_resolution
            if isinstance(inherited_material_resolution, MaterialResolutionBinding)
            else None
        )
        self._research_run_ids: dict[str, str] = {}
        self._material_resolver_instance: MaterialResolver | None = None
        if self.material_store is not None and self.input_artifact_store is not None:
            self._material_resolver()

    def _request_run_context(
        self, research_id: str, request_ref: Mapping[str, object]
    ):
        digest = request_ref.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("service request trace identity is invalid")
        run_id = "service-request-" + digest
        self._research_run_ids[research_id] = run_id
        if not getattr(self.product_interface, "run_id_was_generated", False):
            return nullcontext()
        binder = getattr(self.product_interface, "use_run_id", None)
        if not callable(binder):
            raise ValueError("Product Interface durable run identity is unavailable")
        return binder(run_id)

    def _research_run_context(self, research_id: str):
        run_id = self._research_run_ids.get(research_id)
        if run_id is None or not getattr(
            self.product_interface, "run_id_was_generated", False
        ):
            return nullcontext()
        return self.product_interface.use_run_id(run_id)

    def _material_resolver(self) -> MaterialResolver:
        if self.material_store is None or self.input_artifact_store is None:
            raise ValueError("material resolver is unavailable")
        resolver = self._material_resolver_instance
        if resolver is None:
            resolver = MaterialResolver(
                data_root=self.data_root,
                material_store=self.material_store,
                input_artifact_store=self.input_artifact_store,
            )
            self._material_resolver_instance = resolver
        binding = self.material_resolution_binding
        if binding is not None:
            def resolve_revisions(
                project_id: str,
                revision_refs: list[Mapping[str, object]],
            ) -> list[dict[str, Any]]:
                material_ids = []
                for reference in revision_refs:
                    material_id = reference.get("material_id")
                    if not isinstance(material_id, str) or not material_id:
                        raise ValueError("material revision identity is invalid")
                    material_ids.append(material_id)
                return resolver.resolve(project_id, material_ids, revision_refs)

            binding.bind(
                verified_content_resolver=(
                    resolver.resolve_project_record_content
                ),
                material_revision_resolver=resolve_revisions,
            )
        return resolver

    def _register_input_binding(
        self,
        research_id: str,
        material_ids: object,
        material_revision_refs: object,
    ) -> dict[str, str] | None:
        """Hide immutable revision identities behind one content-bound handle."""

        if not material_ids:
            return None
        frozen = _frozen_material_revision_refs(
            material_ids, material_revision_refs
        )
        binding = self.material_resolution_binding
        if binding is None:
            raise ValueError("material input binding is unavailable")
        return binding.register_material_selection(research_id, frozen)

    def bind_material_stores(
        self,
        *,
        input_artifact_store: object,
        material_store: object,
    ) -> None:
        """Bind the one operational material store and its read-only artifact resolver."""

        self.input_artifact_store = input_artifact_store
        self.material_store = material_store
        self._material_resolver_instance = None
        self._material_resolver()

    def resolve_materials(
        self,
        research_id: str,
        material_ids: object,
        material_revision_refs: object = None,
    ) -> list[dict[str, Any]]:
        """Use the same content-bound resolver for sync and worker execution."""

        try:
            return self._material_resolver().resolve(
                research_id, material_ids, material_revision_refs
            )
        except (KeyError, PermissionError, ValueError) as error:
            raise MaterialResolutionError("material_id is unavailable") from error

    def materials_are_processing(
        self,
        research_id: str,
        material_ids: object,
        material_revision_refs: object = None,
    ) -> bool:
        """Distinguish retryable processing lag from terminal material failure."""

        if (
            self.material_store is None
            or not isinstance(material_ids, list)
            or not material_ids
        ):
            return False
        pending = {"pending_security", "queued", "inspecting", "parsing"}
        if material_revision_refs is not None and (
            not isinstance(material_revision_refs, list)
            or len(material_revision_refs) != len(material_ids)
        ):
            return False
        for index, material_id in enumerate(material_ids):
            if not isinstance(material_id, str):
                return False
            try:
                reference = (
                    material_revision_refs[index]
                    if isinstance(material_revision_refs, list)
                    else None
                )
                if reference is not None:
                    if (
                        not isinstance(reference, Mapping)
                        or reference.get("material_id") != material_id
                        or not isinstance(reference.get("revision_id"), str)
                    ):
                        return False
                    current = self.material_store.get_material_revision(
                        project_id=research_id,
                        material_id=material_id,
                        revision_id=reference["revision_id"],
                    )
                else:
                    current = self.material_store.get_material(
                        project_id=research_id,
                        material_id=material_id,
                    )
            except (KeyError, PermissionError, ValueError):
                return False
            revision = current.get("revision")
            if not isinstance(revision, Mapping):
                return False
            if revision.get("processing_status") in pending:
                return True
        return False

    def material_processing_boundary(
        self,
        research_id: str,
        material_ids: object,
        material_revision_refs: object = None,
    ) -> dict[str, Any] | None:
        """Project terminal parser failures without exposing material bodies."""

        if (
            self.material_store is None
            or not isinstance(material_ids, list)
            or not material_ids
            or not isinstance(material_revision_refs, list)
            or len(material_revision_refs) != len(material_ids)
        ):
            return None
        failures: list[dict[str, str]] = []
        for material_id, reference in zip(
            material_ids, material_revision_refs, strict=True
        ):
            if (
                not isinstance(material_id, str)
                or not isinstance(reference, Mapping)
                or reference.get("material_id") != material_id
                or not isinstance(reference.get("revision_id"), str)
            ):
                return None
            try:
                stored = self.material_store.get_material_revision(
                    project_id=research_id,
                    material_id=material_id,
                    revision_id=reference["revision_id"],
                )
            except (KeyError, PermissionError, ValueError):
                return None
            revision = stored.get("revision")
            status = (
                revision.get("processing_status")
                if isinstance(revision, Mapping)
                else None
            )
            if status in {"pending_security", "queued", "inspecting", "parsing"}:
                return None
            if status in {"ready", "partial", "needs_user"}:
                continue
            if not isinstance(status, str):
                return None
            failures.append(
                {
                    "material_id": material_id,
                    "revision_id": reference["revision_id"],
                    "processing_status": status,
                }
            )
        if not failures:
            return None
        retryable = all(
            failure["processing_status"] == "failed" for failure in failures
        )
        allowed_decisions = ["omit_failed_materials", "replace_failed_materials"]
        if retryable:
            allowed_decisions.insert(0, "retry")
        return {
            "contract": "lca_research_service.material_processing_recovery/v1",
            "interaction_kind": "material_processing_recovery",
            "authorization_required": True,
            "question": "资料处理未能完成。请选择重试，或明确本轮不使用失败资料后继续。",
            "failed_materials": failures,
            "allowed_decisions": allowed_decisions,
        }

    def record_material_recovery_decision(
        self,
        research_id: str,
        external_input: Mapping[str, Any],
        effective_material_revision_refs: list[Mapping[str, Any]],
        research_request: Mapping[str, Any],
        request_ref: Mapping[str, str],
    ) -> dict[str, Any]:
        start_request = {
            "contract": "lca_research_kernel.research_start/v1",
            "research_id": research_id,
            "title": f"D-18 service research {research_id}",
            "objective": _message_text(research_request.get("messages")),
            "source_refs": [deepcopy(dict(request_ref))],
            "received_at": "request-sha256:" + str(request_ref["sha256"]),
        }
        if (
            effective_material_revision_refs
            and self.material_store is not None
            and self.input_artifact_store is not None
        ):
            self._material_resolver()
        binding = self.material_resolution_binding
        if binding is None:
            raise ValueError("material recovery input binding is unavailable")
        input_binding_ref = binding.register_material_selection(
            research_id,
            effective_material_revision_refs,
            binding_context={"research_start": start_request},
        )
        normalized_input = deepcopy(dict(external_input))
        normalized_input["input_binding_ref"] = input_binding_ref
        with self._research_run_context(research_id):
            return self.product_interface.submit_input(
                research_id,
                normalized_input,
            )

    def retry_material_processing(
        self,
        research_id: str,
        failed_materials: list[Mapping[str, Any]],
        decision_sha256: str,
    ) -> list[dict[str, Any]]:
        if self.material_store is None or not failed_materials:
            raise ValueError("material processing retry is unavailable")
        retried: list[dict[str, Any]] = []
        for failed in failed_materials:
            if not isinstance(failed, Mapping):
                raise ValueError("material processing retry identity is invalid")
            material_id = failed.get("material_id")
            revision_id = failed.get("revision_id")
            if not isinstance(material_id, str) or not isinstance(revision_id, str):
                raise ValueError("material processing retry identity is invalid")
            retried.append(
                self.material_store.retry_revision_processing(
                    project_id=research_id,
                    material_id=material_id,
                    revision_id=revision_id,
                    decision_sha256=decision_sha256,
                )
            )
        return retried

    def resolve_material_file(
        self, research_id: str, material_id: str, filename: str
    ) -> dict[str, Any]:
        try:
            return self._material_resolver().resolve_one(
                research_id, material_id, filename=filename
            )
        except (KeyError, PermissionError, ValueError) as error:
            raise MaterialResolutionError("material_id is unavailable") from error

    def close(self) -> None:
        if self._owns_material_store and self.material_store is not None:
            self.material_store.close()
            self._owns_material_store = False

    def complete(self, request: Mapping[str, Any]) -> dict[str, Any]:
        normalized = deepcopy(dict(request))
        research_id = normalized.get("research_id")
        request_ref = validate_content_ref(normalized.get("service_request_ref"))
        if not isinstance(research_id, str) or not research_id.strip():
            raise ValueError("service research_id is absent")
        if request_ref is None:
            raise ValueError("service request is not content-bound")
        durable_run_id = "service-request-" + request_ref["sha256"]
        if (
            getattr(self.product_interface, "run_id_was_generated", False)
            and self._research_run_ids.get(research_id) != durable_run_id
        ):
            with self._request_run_context(research_id, request_ref):
                return self.complete(normalized)
        objective = _message_text(normalized.get("messages"))
        received_at = "request-sha256:" + request_ref["sha256"]
        read_result = self.product_interface.read_result
        requested_material_ids = normalized.get("material_ids", [])
        source_refs = [request_ref]
        source_materials = normalized.get("source_bound_materials", [])
        if not isinstance(source_materials, list):
            raise ValueError("service source-bound materials are invalid")
        for material in source_materials:
            if not isinstance(material, Mapping):
                raise ValueError("service source-bound material is invalid")
            material_ref = validate_content_ref(
                {
                    key: material[key]
                    for key in ("path", "sha256")
                    if key in material
                }
            )
            if material_ref is None:
                raise ValueError("service source-bound material reference is absent")
            if material.get("authority") != "user_supplied_project_input":
                raise ValueError("service source material authority is invalid")
            content = material.get("content")
            if not isinstance(content, Mapping):
                raise ValueError("service source material content is absent")
            source_refs.append(
                {
                    **material_ref,
                    "authority": "user_supplied_project_input",
                    "material_id": material.get("material_id"),
                    "name": material.get("name"),
                    "media_type": material.get("media_type"),
                    "content": deepcopy(dict(content)),
                }
            )
        start_request = {
            "contract": "lca_research_kernel.research_start/v1",
            "research_id": research_id,
            "title": f"D-18 service research {research_id}",
            "objective": objective,
            "source_refs": source_refs,
            "received_at": received_at,
        }
        material_ids = requested_material_ids
        if not isinstance(material_ids, list):
            raise ValueError("service material_ids are invalid")
        turn_kind = normalized.get("service_turn_kind", "initial")
        if turn_kind not in {"initial", "continuation"}:
            raise ValueError("service turn kind is invalid")
        revision_refs = normalized.get("material_revision_refs")
        if (
            material_ids
            and self.material_store is not None
            and self.input_artifact_store is not None
        ):
            self._material_resolver()
        input_binding_ref = self._register_input_binding(
            research_id, material_ids, revision_refs
        )
        if turn_kind == "continuation":
            turn = {
                "contract": "lca_research_kernel.research_user_turn/v1",
                "input_id": "service-turn-" + request_ref["sha256"][:24],
                "text": _last_user_message_text(normalized.get("messages")),
                "source_refs": [deepcopy(dict(request_ref))],
                "received_at": received_at,
                **(
                    {"input_binding_ref": input_binding_ref}
                    if input_binding_ref is not None
                    else {}
                ),
            }
            status = self.product_interface.submit_input(
                research_id,
                turn,
            )
        else:
            if input_binding_ref is not None:
                start_request["input_binding_ref"] = input_binding_ref
            status = self.product_interface.submit_research(
                start_request,
            )
        result = read_result(research_id)
        answer = _accepted_user_answer(result)
        project_record_ref = validate_content_ref(
            status.get("project_record_ref")
        )
        if project_record_ref is None:
            raise ValueError("Product Interface omitted Project Record reference")
        return {
            "content": answer,
            "finish_reason": "stop",
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
            "project_record_ref": project_record_ref,
            "attachments": [],
        }

    def start(
        self, request: Mapping[str, Any], request_ref: Mapping[str, str]
    ) -> dict[str, Any]:
        normalized = deepcopy(dict(request))
        normalized["service_request_ref"] = deepcopy(dict(request_ref))
        research_id = str(normalized["research_id"])
        with self._request_run_context(research_id, request_ref):
            try:
                self.complete(normalized)
            except ProductResultNotAccepted:
                pass
            return self.product_interface.read_status(research_id)

    def supply(
        self,
        research_id: str,
        external_input: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload = external_input.get("payload")
        material_ids = (
            payload.get("material_ids") if isinstance(payload, Mapping) else None
        )
        revision_refs = (
            payload.get("material_revision_refs")
            if isinstance(payload, Mapping)
            else None
        )
        if (
            material_ids is not None
            and self.material_store is not None
            and self.input_artifact_store is not None
        ):
            self._material_resolver()
        input_binding_ref = (
            self._register_input_binding(
                research_id, material_ids, revision_refs
            )
            if material_ids is not None
            else None
        )
        normalized_input = deepcopy(dict(external_input))
        if input_binding_ref is not None:
            normalized_input["input_binding_ref"] = input_binding_ref
        return self.product_interface.submit_input(
            research_id,
            normalized_input,
        )

    def continue_turn(
        self, request: Mapping[str, Any], request_ref: Mapping[str, str]
    ) -> dict[str, Any]:
        normalized = deepcopy(dict(request))
        research_id = str(normalized["research_id"])
        turn = {
            "contract": "lca_research_kernel.research_user_turn/v1",
            "input_id": normalized["input_id"],
            "text": normalized["text"],
            "source_refs": [deepcopy(dict(request_ref))],
            "received_at": normalized["received_at"],
        }
        with self._request_run_context(research_id, request_ref):
            if self.material_store is not None and self.input_artifact_store is not None:
                self._material_resolver()
            return self.product_interface.submit_input(research_id, turn)

    def read_status(self, research_id: str) -> dict[str, Any]:
        with self._research_run_context(research_id):
            return self.product_interface.read_status(research_id)

    def read_result(self, research_id: str) -> dict[str, Any]:
        with self._research_run_context(research_id):
            return self.product_interface.read_result(research_id)
