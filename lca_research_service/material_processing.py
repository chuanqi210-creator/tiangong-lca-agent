"""Content-addressed publication of candidate evidence units and receipts."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from lca_research_kernel.material_sources import (
    load_research_materials_policy,
    validate_evidence_locator,
    validate_input_artifact,
    validate_processing_receipt,
)

from .document_processors import (
    ProcessorRegistry,
    default_processor_registry,
    detect_document_type,
    render_pdf_page_preview,
)
from .archive_inspection import inspect_archive
from .input_artifacts import _ensure_directory, _fsync_directory
from .material_preview import build_preview, reencode_image_preview


_LOCATOR_NAMESPACE = uuid.UUID("d047e8c8-97b4-46c9-a5a9-910523141f75")


@dataclass(frozen=True)
class ProcessingOutcome(Mapping[str, Any]):
    """Full Task-0 receipt plus the Task-3 processor result projection."""

    receipt: dict[str, Any]
    receipt_ref: dict[str, str]
    evidence_units_ref: dict[str, str]
    preview_ref: dict[str, str] | None
    locators: list[dict[str, Any]]
    archive_manifest_ref: dict[str, str] | None = None
    lca_package_route: dict[str, Any] | None = None

    def _projection(self) -> dict[str, Any]:
        return {
            key: deepcopy(self.receipt[key])
            for key in (
                "contract",
                "revision_id",
                "processor",
                "processing_mode",
                "status",
                "detected_type",
                "warnings",
                "candidate_only",
            )
        } | {
            "evidence_units_ref": deepcopy(self.evidence_units_ref),
            "locators": deepcopy(self.locators),
        }

    def __getitem__(self, key: str) -> Any:
        return self._projection()[key]

    def __iter__(self):
        return iter(self._projection())

    def __len__(self) -> int:
        return len(self._projection())


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _publish(root: Path, relative_root: Path, payload: bytes, suffix: str) -> dict[str, str]:
    digest = hashlib.sha256(payload).hexdigest()
    relative = relative_root / digest[:2] / f"{digest}.{suffix}"
    target = (root / relative).absolute()
    try:
        target.relative_to(root.absolute())
    except ValueError as error:
        raise ValueError("processing artifact path escaped data root") from error
    _ensure_directory(target.parent, root=root)
    if target.exists():
        if target.is_symlink() or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError("processing artifact drifted")
    else:
        descriptor, name = tempfile.mkstemp(prefix=".candidate-", dir=target.parent)
        temporary = Path(name)
        try:
            os.chmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as destination:
                destination.write(payload); destination.flush(); os.fsync(destination.fileno())
            os.replace(temporary, target); os.chmod(target, 0o600); _fsync_directory(target.parent)
        finally:
            temporary.unlink(missing_ok=True)
    return {"path": relative.as_posix(), "sha256": digest}


def _opaque(reference: Mapping[str, str]) -> dict[str, str]:
    return {"artifact_id": reference["sha256"], "sha256": reference["sha256"]}


def _ordered_identity_anchors(values: list[object]) -> list[str]:
    """Keep exact parser-observed identities without promoting scientific meaning."""

    anchors: list[str] = []
    for value in values:
        if isinstance(value, str) and value.strip() and value.strip() not in anchors:
            anchors.append(value.strip())
    return anchors


def _bounded_tidas_graph_contents(
    value: Mapping[str, Any], *, max_objects: int = 64, max_relations: int = 64
) -> list[dict[str, Any]]:
    """Split one source member without losing its exact source binding.

    Objects and relations are chunked independently so every object definition is
    emitted exactly once. Cross-chunk and cross-member endpoints are resolved only
    after Project Record acceptance; the chunks never create product truth.
    """

    objects = value.get("objects")
    relations = value.get("relations")
    if not isinstance(objects, list) or not isinstance(relations, list):
        return []
    object_by_ref = {
        row.get("object_ref"): deepcopy(dict(row))
        for row in objects
        if isinstance(row, Mapping) and isinstance(row.get("object_ref"), str)
    }
    relation_rows = [
        deepcopy(dict(row)) for row in relations if isinstance(row, Mapping)
    ]
    object_refs = sorted(object_by_ref)
    object_chunks = [
        object_refs[offset : offset + max_objects]
        for offset in range(0, len(object_refs), max_objects)
    ]
    relation_chunks = [
        relation_rows[offset : offset + max_relations]
        for offset in range(0, len(relation_rows), max_relations)
    ]
    chunk_count = max(len(object_chunks), len(relation_chunks))

    base = {
        "contract": value.get("contract"),
        "source_lock_ids": deepcopy(value.get("source_lock_ids")),
        "candidate_only": True,
        "scientifically_validated": False,
    }
    return [
        {
            **base,
            "objects": [
                object_by_ref[ref]
                for ref in (object_chunks[index] if index < len(object_chunks) else [])
            ],
            "relations": (
                relation_chunks[index] if index < len(relation_chunks) else []
            ),
        }
        for index in range(chunk_count)
    ]


def _tidas_object_graph_units(
    members: list[object],
    *,
    root_member_paths: set[str],
) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    rows = sorted(
        (
            row
            for row in members
            if isinstance(row, Mapping)
            and isinstance(row.get("path"), str)
            and isinstance(row.get("object_graph_slice"), Mapping)
        ),
        key=lambda row: row["path"],
    )
    for row in rows:
        graph_slice = row["object_graph_slice"]
        for chunk in _bounded_tidas_graph_contents(graph_slice):
            is_package_root = row["path"] in root_member_paths
            identities = _ordered_identity_anchors(
                [
                    value
                    for obj in chunk["objects"]
                    if isinstance(obj, Mapping)
                    for value in (obj.get("identity"), obj.get("version"))
                ]
            )
            units.append(
                {
                    "kind": "tidas_object_graph_slice",
                    "content": chunk,
                    "semantic_roles": sorted(
                        {
                            "lca_native_object_graph",
                            *(
                                ["lca_native_package_root"]
                                if is_package_root
                                else []
                            ),
                        }
                    ),
                    "source_identity_anchors": identities,
                    "source_span": {
                        "kind": "archive_member",
                        "locator": {"member_path": row["path"]},
                    },
                    "provenance": {
                        "original_value": None,
                        "original_unit": None,
                        "transformations": [
                            "bounded_source_bound_tidas_object_graph_slice"
                        ],
                        "assumptions": [],
                        "missing_data": [],
                        "validation_status": "candidate_unvalidated",
                    },
                    "candidate_only": True,
                }
            )
    return units


def _tidas_package_units(
    manifest: Mapping[str, Any], route: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    if route.get("detected_candidate_format") != "tiangong_tidas_package":
        return [], []
    members = manifest.get("members")
    if not isinstance(members, list):
        return [], ["TianGong TIDAS package manifest has no member index"]
    rows = {
        row.get("path"): row
        for row in members
        if isinstance(row, Mapping) and isinstance(row.get("path"), str)
    }
    manifest_rows = [
        row
        for row in rows.values()
        if "tiangong_tidas_package_manifest"
        in (
            row.get("format_signatures")
            if isinstance(row.get("format_signatures"), list)
            else []
        )
        and isinstance(row.get("package_metadata"), Mapping)
    ]
    if len(manifest_rows) != 1:
        return [], ["TianGong TIDAS package manifest is absent or ambiguous"]
    manifest_row = manifest_rows[0]
    package_summary = deepcopy(dict(manifest_row["package_metadata"]))
    package_roots = package_summary.get("roots")
    package_identity_anchors = _ordered_identity_anchors(
        [package_summary.get("format")]
        + [
            value
            for root in (
                package_roots if isinstance(package_roots, list) else []
            )
            if isinstance(root, Mapping)
            for value in (root.get("id"), root.get("version"))
        ]
    )
    root_members = manifest_row.get("package_root_members")
    units: list[dict[str, Any]] = [
        {
            "kind": "tidas_package_summary",
            "content": package_summary,
            "semantic_roles": [
                "lca_native_package",
                "source_identity",
                "version",
            ],
            "source_identity_anchors": package_identity_anchors,
            "source_span": {
                "kind": "archive_member",
                "locator": {"member_path": manifest_row["path"]},
            },
            "provenance": {
                "original_value": None,
                "original_unit": None,
                "transformations": ["bounded_tidas_package_manifest_summary"],
                "assumptions": [],
                "missing_data": [],
                "validation_status": "candidate_unvalidated",
            },
            "candidate_only": True,
        }
    ]
    warnings: list[str] = (
        []
        if isinstance(root_members, list)
        else ["TianGong TIDAS package root bindings are absent"]
    )
    root_members = root_members if isinstance(root_members, list) else []
    roots = package_summary.get("roots")
    for root in roots if isinstance(roots, list) else []:
        if not isinstance(root, Mapping) or root.get("table") != "lifecyclemodels":
            continue
        root_bindings = [
            binding
            for binding in root_members
            if isinstance(binding, Mapping)
            and binding.get("table") == root.get("table")
            and binding.get("id") == root.get("id")
            and binding.get("version") == root.get("version")
            and isinstance(binding.get("member_path"), str)
        ]
        if len(root_bindings) != 1:
            warnings.append(
                "TianGong TIDAS root lifecycle model member is absent or ambiguous"
            )
            continue
        member_path = root_bindings[0]["member_path"]
        member_row = rows.get(member_path)
        expected = [
            row
            for row in [member_row]
            if isinstance(row, Mapping)
            if isinstance(row.get("package_metadata"), Mapping)
            and row["package_metadata"].get("contract")
            == "lca_research_service.tidas_lifecycle_model_summary/v1"
            and row["package_metadata"].get("dataset_uuid") == root.get("id")
            and row["package_metadata"].get("version") == root.get("version")
        ]
        if len(expected) != 1:
            warnings.append(
                "TianGong TIDAS root lifecycle model is absent or identity-drifted"
            )
            continue
        row = expected[0]
        model_summary = deepcopy(dict(row["package_metadata"]))
        model_name = model_summary.get("name")
        base_name = (
            model_name.get("base_name")
            if isinstance(model_name, Mapping)
            else None
        )
        process_instances = model_summary.get("process_instances")
        model_identity_anchors = _ordered_identity_anchors(
            [model_summary.get("dataset_uuid"), model_summary.get("version")]
            + (
                list(base_name.values())
                if isinstance(base_name, Mapping)
                else []
            )
            + [
                value
                for instance in (
                    process_instances
                    if isinstance(process_instances, list)
                    else []
                )
                if isinstance(instance, Mapping)
                for value in (
                    instance.get("process_uuid"),
                    *(
                        instance["description"].values()
                        if isinstance(instance.get("description"), Mapping)
                        else []
                    ),
                )
            ]
        )
        units.append(
            {
                "kind": "tidas_lifecycle_model_summary",
                "content": model_summary,
                "semantic_roles": [
                    "lca_native_package",
                    "process",
                    "source_identity",
                    "version",
                ],
                "source_identity_anchors": model_identity_anchors,
                "source_span": {
                    "kind": "archive_member",
                    "locator": {"member_path": row["path"]},
                },
                "provenance": {
                    "original_value": None,
                    "original_unit": None,
                    "transformations": ["bounded_tidas_lifecycle_model_summary"],
                    "assumptions": [],
                    "missing_data": [],
                    "validation_status": "candidate_unvalidated",
                },
                "candidate_only": True,
            }
        )
    if len(units) == 1 and any(
        isinstance(root, Mapping) and root.get("table") == "lifecyclemodels"
        for root in (roots if isinstance(roots, list) else [])
    ):
        warnings.append("TianGong TIDAS package root model summary is unavailable")
    root_member_paths = {
        str(binding["member_path"])
        for binding in root_members
        if isinstance(binding, Mapping)
        and isinstance(binding.get("member_path"), str)
    }
    units.extend(
        _tidas_object_graph_units(
            members,
            root_member_paths=root_member_paths,
        )
    )
    return units, warnings


def process_input_artifact(
    artifact_value: Mapping[str, Any],
    stream: BinaryIO,
    *,
    revision_id: str,
    data_root: str | Path,
    registry: ProcessorRegistry | None = None,
    capability_gateway: object = None,
) -> ProcessingOutcome:
    """Process one immutable input locally; publish candidate outputs only."""

    artifact = validate_input_artifact(artifact_value)
    if not isinstance(revision_id, str) or not revision_id:
        raise ValueError("revision_id is required")
    chunks, digest, size = [], hashlib.sha256(), 0
    while chunk := stream.read(1024 * 1024):
        if not isinstance(chunk, bytes):
            raise ValueError("InputArtifact stream must yield bytes")
        size += len(chunk); digest.update(chunk); chunks.append(chunk)
    payload = b"".join(chunks)
    if size != artifact["byte_size"] or digest.hexdigest() != artifact["sha256"]:
        raise ValueError("InputArtifact stream drifted")

    detection = detect_document_type(payload)
    declared = artifact["detected_media_type"]
    text_family = {
        "text/plain",
        "text/markdown",
        "application/json",
        "application/xml",
        "text/html",
        "text/csv",
        "text/tab-separated-values",
    }
    if detection["mime"] == "text/plain" and declared in {
        "application/json",
        "text/csv",
        "text/tab-separated-values",
    }:
        detection = {
            "mime": declared,
            "signatures": ["utf8", "declared_ambiguous_text_family"],
        }
    mismatched = detection["mime"] != declared and not (
        detection["mime"] in text_family and declared in text_family
    )
    archive_mimes = {
        "application/zip",
        "application/vnd.rar",
        "application/x-7z-compressed",
    }
    declared_archive_mimes = archive_mimes | {
        "application/x-rar-compressed",
        "application/x-7z-compressed",
    }
    root = Path(data_root).absolute()
    _ensure_directory(root, root=root)
    archive_manifest_ref: dict[str, str] | None = None
    lca_package_route: dict[str, Any] | None = None
    if detection["mime"] in archive_mimes:
        if declared not in declared_archive_mimes:
            result = {
                "processor": {"id": "signature-gate", "version": "1.0.0"},
                "processing_mode": "metadata_only",
                "status": "unsupported",
                "units": [],
                "warnings": [
                    f"declared type {declared} rejected: byte signature detected {detection['mime']}"
                ],
                "detected_type": detection,
            }
        else:
            candidate_root = (
                root
                / "projects"
                / artifact["project_id"]
                / "material-processing/derived-candidates"
            )
            _ensure_directory(candidate_root, root=root)
            inspection = inspect_archive(
                payload,
                input_sha256=artifact["sha256"],
                original_filename=artifact["original_filename"],
                declared_media_type=artifact["detected_media_type"],
                candidate_root=candidate_root,
            )
            manifest = inspection["archive_manifest"]
            archive_manifest_ref = _publish(
                root,
                Path("projects")
                / artifact["project_id"]
                / "material-processing/archive-manifests/sha256",
                _canonical(manifest),
                "json",
            )
            from lca_research_kernel.lca_package_routing import route_lca_package

            lca_package_route = route_lca_package(
                manifest,
                capability_gateway=capability_gateway,
                declared_filename=artifact["original_filename"],
            )
            lca_package_route["archive_manifest_ref"] = _opaque(
                archive_manifest_ref
            )
            lca_package_route["input_artifact_sha256"] = artifact["sha256"]
            lca_package_route["member_hashes"] = deepcopy(
                lca_package_route["consumed_member_hashes"]
            )
            package_units, package_warnings = _tidas_package_units(
                manifest, lca_package_route
            )
            unit = {
                "kind": "archive_manifest",
                "content": manifest,
                "provenance": {
                    "original_value": None,
                    "original_unit": None,
                    "transformations": ["central_directory_manifest"],
                    "assumptions": [],
                    "missing_data": [],
                    "validation_status": "candidate_unvalidated",
                },
                "candidate_only": True,
            }
            result = {
                "processor": {"id": "manifest-first-archive", "version": "1.0.0"},
                "processing_mode": "archive_manifest",
                "status": (
                    "partial"
                    if package_warnings and inspection["status"] == "ready"
                    else inspection["status"]
                ),
                "units": package_units or [unit],
                "warnings": [
                    "archive inspection and package recognition do not scientifically validate content"
                ] + package_warnings,
                "detected_type": detection,
            }
    elif mismatched:
        result = {
            "processor": {"id": "signature-gate", "version": "1.0.0"},
            "processing_mode": "metadata_only",
            "status": "unsupported",
            "units": [],
            "warnings": [f"declared type {declared} rejected: byte signature detected {detection['mime']}"],
            "detected_type": detection,
        }
    else:
        result = (registry or default_processor_registry()).process(artifact, payload, detection)

    if lca_package_route is None and detection["mime"] not in archive_mimes:
        from lca_research_kernel.lca_package_routing import (
            detect_lca_package_candidate,
            is_lca_extension_candidate,
            route_lca_package,
        )

        single_candidate = detect_lca_package_candidate(
            detected_type=detection,
            payload_sample=payload[: 64 * 1024],
        )
        extension_candidate = is_lca_extension_candidate(
            artifact["original_filename"]
        )
        if single_candidate is not None or extension_candidate:
            single_manifest = {
                "contract": "lca_research_service.single_file_manifest/v1",
                "input_sha256": artifact["sha256"],
                "detected_type": deepcopy(detection),
                "sample": payload[: 64 * 1024].decode("utf-8", errors="replace"),
                "members": [
                    {
                        "path": artifact["original_filename"],
                        "sha256": artifact["sha256"],
                        "detected_type": deepcopy(detection),
                        "sample": payload[: 64 * 1024].decode(
                            "utf-8", errors="replace"
                        ),
                        "disposition": "input_artifact_candidate",
                    }
                ],
                "candidate_only": True,
            }
            lca_package_route = route_lca_package(
                single_manifest,
                capability_gateway=capability_gateway,
                declared_filename=artifact["original_filename"],
            )
            if mismatched and not extension_candidate:
                lca_package_route = None
            if result.get("status") == "ready" and not result.get("units"):
                result["status"] = "partial"
                result["warnings"].append(
                    "native package candidate has no ordinary-document evidence units"
                )

    units = result.get("units")
    if not isinstance(units, list):
        raise ValueError("processor units must be a list")
    for unit in units:
        if result["processing_mode"] == "archive_manifest" and unit.get("kind") == "archive_manifest":
            continue
        if not isinstance(unit, Mapping) or not isinstance(unit.get("source_span"), Mapping):
            raise ValueError("processor evidence unit omitted source span")
        source_span = unit["source_span"]
        if set(source_span) != {"kind", "locator"}:
            raise ValueError("processor evidence unit source span is invalid")
    if result.get("status") == "ready" and not units:
        raise ValueError("ready processor omitted source-bound evidence units")

    policy_path = Path(__file__).resolve().parents[1] / "lca_research_kernel/contracts/research-materials-policy-v1.json"
    preview_ceilings = load_research_materials_policy(policy_path)["preview_ceilings"]
    evidence = {
        "contract": "lca_research_service.evidence_units/v1",
        "revision_id": revision_id,
        "input_sha256": artifact["sha256"],
        "detected_type": deepcopy(result["detected_type"]),
        "units": deepcopy(units),
        "candidate_only": True,
    }
    evidence_ref = _publish(root, Path("projects") / artifact["project_id"] / "material-processing/evidence-units/sha256", _canonical(evidence), "json")

    preview_ref: dict[str, str] | None = None
    preview = build_preview(units, detected_mime=detection["mime"], ceilings=preview_ceilings)
    if detection["mime"] in {"image/png", "image/jpeg"} and result["status"] in {"ready", "partial"}:
        try:
            thumbnail_ref = _publish(root, Path("projects") / artifact["project_id"] / "material-processing/previews/sha256", reencode_image_preview(payload, max_edge=preview_ceilings["thumbnail_edge_pixels"]), "png")
            preview["thumbnail_ref"] = thumbnail_ref
        except Exception:
            result["warnings"].append("image preview unavailable; metadata only")
    elif detection["mime"] == "application/pdf" and result["status"] in {"ready", "partial"}:
        rendered = render_pdf_page_preview(payload, page_number=1)
        if rendered is None:
            preview["page_images"] = []
            preview["render_status"] = "metadata_or_text_only"
        else:
            page_ref = _publish(root, Path("projects") / artifact["project_id"] / "material-processing/previews/sha256", rendered, "png")
            preview["page_images"] = [{"page": 1, "ref": page_ref}]
    preview_ref = _publish(root, Path("projects") / artifact["project_id"] / "material-processing/previews/sha256", _canonical(preview), "json")

    locators = []
    for index, unit in enumerate(units):
        if unit.get("kind") == "archive_manifest":
            continue
        span = unit["source_span"]
        content_hash = hashlib.sha256(_canonical(unit)).hexdigest()
        locator_id = str(uuid.uuid5(_LOCATOR_NAMESPACE, f"{revision_id}:{index}:{content_hash}"))
        locators.append(validate_evidence_locator({
            "contract": "lca_research_kernel.evidence_locator/v1",
            "locator_id": locator_id,
            "revision_id": revision_id,
            "locator_kind": span["kind"],
            "locator": deepcopy(span["locator"]),
            "content_hash": content_hash,
            "preview_text_or_thumbnail_ref": _opaque(preview_ref),
            "assurance_acceptance_ref": None,
            "scientific_status": "candidate",
        }))
    locator_ref = _publish(root, Path("projects") / artifact["project_id"] / "material-processing/locators/sha256", _canonical({"contract": "lca_research_service.evidence_locator_index/v1", "revision_id": revision_id, "locators": locators, "candidate_only": True}), "json")

    replay_key = hashlib.sha256(f"{artifact['sha256']}:{result['processor']['id']}:{result['processor']['version']}".encode()).hexdigest()
    output_refs = [_opaque(evidence_ref), _opaque(locator_ref)]
    if preview_ref is not None:
        output_refs.append(_opaque(preview_ref))
    if archive_manifest_ref is not None:
        output_refs.append(_opaque(archive_manifest_ref))
    receipt = validate_processing_receipt({
        "contract": "lca_research_kernel.material_processing_receipt/v1",
        "revision_id": revision_id,
        "processor": deepcopy(result["processor"]),
        "processing_mode": result["processing_mode"],
        "status": result["status"],
        "input_sha256": artifact["sha256"],
        "detected_type": deepcopy(result["detected_type"]),
        "output_refs": output_refs,
        "completeness": {"status": "complete" if result["status"] == "ready" else "bounded", "unprocessed_regions": [] if result["status"] == "ready" else list(result["warnings"])},
        "warnings": list(result["warnings"]),
        "failure_category": "format_failure" if result["status"] == "failed" else None,
        "duration_ms": 0,
        "replay_key": replay_key,
        "candidate_only": True,
    })
    receipt_ref = _publish(root, Path("projects") / artifact["project_id"] / "material-processing/receipts/sha256", _canonical(receipt), "json")
    return ProcessingOutcome(
        receipt=receipt,
        receipt_ref=receipt_ref,
        evidence_units_ref=evidence_ref,
        preview_ref=preview_ref,
        locators=locators,
        archive_manifest_ref=archive_manifest_ref,
        lca_package_route=lca_package_route,
    )
