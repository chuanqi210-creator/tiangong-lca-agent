"""Pure source and scientific contracts for immutable research materials."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from copy import deepcopy
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Mapping, Sequence
from urllib.parse import unquote, urlsplit


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
POLICY_CONTRACT = "lca_research_kernel.research_materials_policy/v1"
PROCESSING_MODES = frozenset(
    {
        "full_context",
        "focused_retrieval",
        "structured_document_parse",
        "tabular_parse",
        "ocr_or_vision",
        "audio_transcription",
        "archive_manifest",
        "lca_native_import",
        "metadata_only",
    }
)
PROCESSING_STATUSES = frozenset(
    {
        "pending_security",
        "quarantined",
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
MATERIAL_USE_DECISION_V1 = "lca_research_kernel.material_use_decision/v1"
MATERIAL_USE_DECISION_V2 = "lca_research_kernel.material_use_decision/v2"
MATERIAL_SELECTION_BASIS_V1 = "lca_research_kernel.material_selection_basis/v1"
MATERIAL_USE_TASK_REF_V1 = "lca_research_kernel.material_use_task_ref/v1"
MATERIAL_USE_DECISION_IDENTITY_V2 = (
    "lca_research_kernel.material_use_decision_identity/v2"
)
MATERIAL_USE_DECISION_CHECKPOINT_V2 = (
    "lca_research_kernel.material_use_decision_checkpoint/v2"
)
SOURCE_KINDS = frozenset({"local_upload", "qingxiaoda_url", "project_reuse"})
MATERIAL_SCOPES = frozenset({"turn", "project"})
LOCATOR_KINDS = frozenset(
    {
        "text_span",
        "pdf_region",
        "document_section",
        "spreadsheet_range",
        "archive_member",
        "json_pointer",
        "xml_xpath",
        "image_region",
        "audio_segment",
        "lca_object",
    }
)
CANONICAL_ALLOWED_TYPES = {
    "text/plain": [".txt", ".md"],
    "application/json": [".json"],
    "application/xml": [".xml"],
    "text/html": [".html", ".htm"],
    "text/csv": [".csv"],
    "text/tab-separated-values": [".tsv"],
    "application/pdf": [".pdf"],
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": [".pptx"],
    "application/vnd.ms-excel": [".xls"],
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
    "image/png": [".png"],
    "image/jpeg": [".jpg", ".jpeg"],
    "image/webp": [".webp"],
    "image/tiff": [".tif", ".tiff"],
    "image/heic": [".heic"],
    "audio/mpeg": [".mp3"],
    "audio/wav": [".wav"],
    "audio/mp4": [".m4a"],
    "application/zip": [".zip"],
    "application/octet-stream": [".zolca"],
}
MIME_RE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")
A1_RANGE_RE = re.compile(
    r"^\$?[A-Z]{1,3}\$?[1-9][0-9]{0,6}(?::\$?[A-Z]{1,3}\$?[1-9][0-9]{0,6})?$"
)
R1C1_RANGE_RE = re.compile(
    r"^R[1-9][0-9]{0,6}C[1-9][0-9]{0,4}(?::R[1-9][0-9]{0,6}C[1-9][0-9]{0,4})?$"
)
XLSX_MAX_ROW = 1_048_576
XLSX_MAX_COLUMN = 16_384
XML_NAME = r"[A-Za-z_][A-Za-z0-9_.-]*(?::[A-Za-z_][A-Za-z0-9_.-]*)?"
XPATH_ELEMENT_STEP_RE = re.compile(
    rf"^(?P<name>{XML_NAME})(?:\[(?P<predicate>[1-9][0-9]*|@{XML_NAME}=(?:'[^'\s<>&]*'|\"[^\"\s<>&]*\"))\])?$"
)
XPATH_ATTRIBUTE_STEP_RE = re.compile(rf"^@{XML_NAME}$")
JSON_POINTER_RE = re.compile(r"^(?:/(?:[^~/]|~[01])*)*$")
LCA_OBJECT_TYPES = frozenset(
    {
        "process",
        "flow",
        "flow_property",
        "unit_group",
        "impact_method",
        "product_system",
        "source",
        "actor",
    }
)


def _column_number(value: str) -> int:
    result = 0
    for character in value:
        result = result * 26 + ord(character) - ord("A") + 1
    return result


def _spreadsheet_range_is_bounded_and_ordered(value: str) -> bool:
    normalized = value.replace("$", "")
    endpoints = normalized.split(":")
    coordinates: list[tuple[int, int]] = []
    if R1C1_RANGE_RE.fullmatch(value):
        for endpoint in endpoints:
            match = re.fullmatch(
                r"R([1-9][0-9]{0,6})C([1-9][0-9]{0,4})", endpoint
            )
            if match is None:
                return False
            coordinates.append((int(match[1]), int(match[2])))
    elif A1_RANGE_RE.fullmatch(value):
        for endpoint in endpoints:
            match = re.fullmatch(
                r"([A-Z]{1,3})([1-9][0-9]{0,6})", endpoint
            )
            if match is None:
                return False
            coordinates.append((int(match[2]), _column_number(match[1])))
    else:
        return False
    if any(
        row > XLSX_MAX_ROW or column > XLSX_MAX_COLUMN
        for row, column in coordinates
    ):
        return False
    if len(coordinates) == 1:
        return True
    start, end = coordinates
    return start[0] <= end[0] and start[1] <= end[1]


def _xpath_is_allowlisted_absolute(value: object) -> bool:
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or not value.startswith("/")
        or value == "/"
        or any(character.isspace() for character in value)
    ):
        return False
    steps = value[1:].split("/")
    if not steps or any(not step for step in steps) or len(steps) > 64:
        return False
    for index, step in enumerate(steps):
        if XPATH_ATTRIBUTE_STEP_RE.fullmatch(step):
            if index != len(steps) - 1:
                return False
            continue
        if XPATH_ELEMENT_STEP_RE.fullmatch(step) is None:
            return False
    return True


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _exact_fields(
    value: Mapping[str, object],
    *,
    required: set[str],
    optional: set[str] | None = None,
    label: str,
) -> None:
    optional = optional or set()
    missing = required - set(value)
    if missing:
        missing_field = sorted(missing)[0]
        suffix = " reference" if missing_field.endswith("_ref") else ""
        raise ValueError(f"{label} missing required field {missing_field}{suffix}")
    unexpected = set(value) - required - optional
    if unexpected:
        field = sorted(unexpected)[0]
        raise ValueError(f"{label} has unexpected field {field}; state axis leakage is forbidden")


def _string(value: Mapping[str, object], key: str, label: str) -> str:
    candidate = value.get(key)
    if not isinstance(candidate, str) or not candidate.strip():
        raise ValueError(f"{label} {key} must be a non-empty string")
    return candidate


def _sha256(value: Mapping[str, object], key: str, label: str) -> str:
    candidate = _string(value, key, label)
    if SHA256_RE.fullmatch(candidate) is None:
        raise ValueError(f"{label} {key} must be a lowercase SHA-256")
    return candidate


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _timestamp(value: Mapping[str, object], key: str, label: str) -> str:
    candidate = _string(value, key, label)
    try:
        from datetime import datetime

        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} {key} must be an RFC3339 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{label} {key} must include a timezone")
    return candidate


def _contract(value: Mapping[str, object], expected: str, label: str) -> None:
    if value.get("contract") != expected:
        raise ValueError(f"{label} contract is unsupported")


def _opaque_ref(value: object, label: str) -> dict:
    reference = _mapping(value, label)
    if set(reference) != {"artifact_id", "sha256"}:
        raise ValueError(f"{label} must be an opaque artifact reference without a path")
    artifact_id = _sha256(reference, "artifact_id", label)
    sha256 = _sha256(reference, "sha256", label)
    if artifact_id != sha256:
        raise ValueError(f"{label} artifact identifier must equal its SHA-256")
    return {"artifact_id": artifact_id, "sha256": sha256}


def _safe_filename(value: str, label: str) -> None:
    if value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
        raise ValueError(f"{label} must not contain a path")


def _opaque_id(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,254}", value) is None
        or value in {".", ".."}
    ):
        raise ValueError(f"{label} must be an opaque identifier without a path")
    return value


def _validate_source_locator(source_kind: str, value: object) -> None:
    locator = _mapping(value, "InputArtifact source locator")
    expected = {
        "local_upload": {"upload_id"},
        "qingxiaoda_url": {"url"},
        "project_reuse": {"artifact_id"},
    }[source_kind]
    if set(locator) != expected:
        raise ValueError(
            f"InputArtifact source locator path/reference schema for {source_kind} must contain exactly {sorted(expected)}"
        )
    if source_kind == "local_upload":
        _opaque_id(locator.get("upload_id"), "InputArtifact upload source locator")
        return
    if source_kind == "project_reuse":
        _opaque_id(
            locator.get("artifact_id"), "InputArtifact reuse source locator"
        )
        return
    url = locator.get("url")
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError("InputArtifact URL source locator is invalid")
    parsed = urlsplit(url)
    decoded_path = unquote(parsed.path)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or ".." in PurePosixPath(decoded_path).parts
        or "\\" in decoded_path
        or "\x00" in decoded_path
    ):
        raise ValueError("InputArtifact URL source locator must be a safe HTTPS URL")


def _safe_archive_member(value: str) -> None:
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        not value
        or "\x00" in value
        or posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or ".." in posix.parts
        or ".." in windows.parts
        or "\\" in value
    ):
        raise ValueError("archive member path is unsafe")


def _validate_policy(value: object) -> dict:
    policy = _mapping(value, "research materials policy")
    _exact_fields(
        policy,
        required={
            "contract",
            "capacity",
            "processing_modes",
            "allowed_types",
            "archive_limits",
            "preview_ceilings",
        },
        label="research materials policy",
    )
    _contract(policy, POLICY_CONTRACT, "research materials policy")
    capacity = _mapping(policy["capacity"], "policy capacity")
    expected_capacity = {
        "generic_file_max_bytes": 104857600,
        "direct_upload_max_bytes": 16777216,
        "multipart_part_bytes": 8388608,
        "batch_file_count": 10,
        "batch_max_bytes": 524288000,
        "project_max_bytes": 5368709120,
        "image_max_bytes": 26214400,
        "image_max_pixels": 40000000,
        "pdf_max_pages": 500,
        "pdf_vision_max_pages": 100,
        "archive_extracted_max_bytes": 524288000,
        "archive_member_count": 1000,
        "archive_ratio_max": 100,
        "archive_nesting_depth": 1,
        "extracted_model_token_ceiling": 2000000,
    }
    if dict(capacity) != expected_capacity:
        raise ValueError("research materials policy capacity drifted")
    modes = policy["processing_modes"]
    if not isinstance(modes, list) or set(modes) != PROCESSING_MODES or len(modes) != len(PROCESSING_MODES):
        raise ValueError("research materials policy processing modes drifted")
    allowed_types = _mapping(policy["allowed_types"], "policy allowed types")
    extension_owners: dict[str, str] = {}
    for mime, extensions in allowed_types.items():
        if (
            not isinstance(mime, str)
            or MIME_RE.fullmatch(mime) is None
            or not isinstance(extensions, list)
            or not extensions
            or len(extensions) != len(set(extensions))
            or any(
                not isinstance(extension, str)
                or not extension.startswith(".")
                or extension != extension.lower()
                or re.fullmatch(r"\.[a-z0-9]+", extension) is None
                for extension in extensions
            )
        ):
            raise ValueError("research materials policy MIME/extension pair is invalid")
        for extension in extensions:
            owner = extension_owners.get(extension)
            if owner is not None:
                raise ValueError(
                    "research materials policy extension authority conflicts"
                )
            extension_owners[extension] = str(mime)
    if dict(allowed_types) != CANONICAL_ALLOWED_TYPES:
        raise ValueError(
            "research materials policy MIME/extension authority drifted"
        )
    archive = _mapping(policy["archive_limits"], "policy archive limits")
    expected_archive = {
        "compressed_max_bytes": 104857600,
        "extracted_max_bytes": 524288000,
        "member_count": 1000,
        "ratio_max": 100,
        "nesting_depth": 1,
    }
    if dict(archive) != expected_archive:
        raise ValueError("research materials policy archive limits drifted")
    previews = _mapping(policy["preview_ceilings"], "policy preview ceilings")
    if not previews or any(
        not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0
        for limit in previews.values()
    ):
        raise ValueError("research materials policy preview ceilings are invalid")
    return deepcopy(dict(policy))


def load_research_materials_policy(path: Path) -> dict:
    """Load and validate a frozen research-material processing policy."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"research materials policy cannot be loaded: {error}") from error
    return _validate_policy(value)


def validate_input_artifact(value: Mapping[str, object]) -> dict:
    value = _mapping(value, "InputArtifact")
    required = {
        "contract",
        "artifact_id",
        "project_id",
        "sha256",
        "byte_size",
        "detected_media_type",
        "original_filename",
        "storage_ref",
        "source_kind",
        "source_locator",
        "created_at",
        "security_status",
    }
    _exact_fields(value, required=required, label="InputArtifact")
    _contract(value, "lca_research_kernel.input_artifact/v1", "InputArtifact")
    artifact_id = _sha256(value, "artifact_id", "InputArtifact")
    sha256 = _sha256(value, "sha256", "InputArtifact")
    if artifact_id != sha256:
        raise ValueError("InputArtifact artifact identifier must equal its SHA-256")
    _string(value, "project_id", "InputArtifact")
    byte_size = value.get("byte_size")
    if not isinstance(byte_size, int) or isinstance(byte_size, bool) or byte_size < 0:
        raise ValueError("InputArtifact byte_size must be a non-negative integer")
    media_type = _string(value, "detected_media_type", "InputArtifact")
    if "/" not in media_type or any(character.isspace() for character in media_type):
        raise ValueError("InputArtifact detected_media_type is invalid")
    filename = _string(value, "original_filename", "InputArtifact")
    _safe_filename(filename, "InputArtifact original_filename")
    storage_ref = _opaque_ref(value.get("storage_ref"), "InputArtifact storage reference")
    if storage_ref["sha256"] != sha256:
        raise ValueError("InputArtifact storage reference SHA-256 drifted")
    source_kind = _string(value, "source_kind", "InputArtifact")
    if source_kind not in SOURCE_KINDS:
        raise ValueError("InputArtifact source_kind is unsupported")
    _validate_source_locator(source_kind, value.get("source_locator"))
    _timestamp(value, "created_at", "InputArtifact")
    security_status = _string(value, "security_status", "InputArtifact")
    if security_status not in {"pending_security", "cleared", "quarantined", "rejected"}:
        raise ValueError("InputArtifact security_status is unsupported")
    return deepcopy(dict(value))


def validate_research_material(value: Mapping[str, object]) -> dict:
    value = _mapping(value, "ResearchMaterial")
    _exact_fields(
        value,
        required={
            "contract",
            "material_id",
            "project_id",
            "display_name",
            "material_kind",
            "current_revision_id",
            "scope",
            "authority",
            "research_classification",
            "labels",
            "created_by",
            "created_at",
            "deleted_at",
        },
        label="ResearchMaterial",
    )
    _contract(value, "lca_research_kernel.research_material/v1", "ResearchMaterial")
    for key in (
        "material_id",
        "project_id",
        "display_name",
        "material_kind",
        "current_revision_id",
        "research_classification",
        "created_by",
    ):
        _string(value, key, "ResearchMaterial")
    if _string(value, "scope", "ResearchMaterial") not in MATERIAL_SCOPES:
        raise ValueError("ResearchMaterial scope is unsupported")
    authority = _string(value, "authority", "ResearchMaterial")
    if authority != "user_supplied_project_input":
        raise ValueError("user material cannot be labeled as TianGong official data")
    labels = value.get("labels")
    if not isinstance(labels, list) or any(not isinstance(label, str) or not label.strip() for label in labels):
        raise ValueError("ResearchMaterial labels must be non-empty strings")
    _timestamp(value, "created_at", "ResearchMaterial")
    if value.get("deleted_at") is not None:
        _timestamp(value, "deleted_at", "ResearchMaterial")
    return deepcopy(dict(value))


def validate_material_revision(value: Mapping[str, object]) -> dict:
    value = _mapping(value, "MaterialRevision")
    _exact_fields(
        value,
        required={
            "contract",
            "revision_id",
            "material_id",
            "artifact_ref",
            "parent_revision_id",
            "processor_profile",
            "processing_status",
            "extraction_refs",
            "manifest_ref",
            "created_at",
        },
        optional={"source_lineage"},
        label="MaterialRevision",
    )
    _contract(value, "lca_research_kernel.material_revision/v1", "MaterialRevision")
    for key in ("revision_id", "material_id", "processor_profile"):
        _string(value, key, "MaterialRevision")
    _opaque_ref(value.get("artifact_ref"), "MaterialRevision artifact reference")
    parent = value.get("parent_revision_id")
    if parent is not None and (not isinstance(parent, str) or not parent.strip()):
        raise ValueError("MaterialRevision parent_revision_id is invalid")
    status = _string(value, "processing_status", "MaterialRevision")
    if status not in PROCESSING_STATUSES:
        raise ValueError("MaterialRevision processing_status is unsupported")
    extraction_refs = value.get("extraction_refs")
    if not isinstance(extraction_refs, list):
        raise ValueError("MaterialRevision extraction_refs must be a list")
    for reference in extraction_refs:
        _opaque_ref(reference, "MaterialRevision extraction reference")
    if value.get("manifest_ref") is not None:
        _opaque_ref(value["manifest_ref"], "MaterialRevision manifest reference")
    if value.get("source_lineage") is not None:
        lineage = _mapping(
            value["source_lineage"], "MaterialRevision source lineage"
        )
        _exact_fields(
            lineage,
            required={
                "contract",
                "artifact_id",
                "source_kind",
                "source_locator",
                "source_identity_sha256",
                "original_filename",
                "lineage_sha256",
            },
            label="MaterialRevision source lineage",
        )
        _contract(
            lineage,
            "lca_research_kernel.material_source_lineage/v1",
            "MaterialRevision source lineage",
        )
        artifact_id = _sha256(
            lineage, "artifact_id", "MaterialRevision source lineage"
        )
        if artifact_id != value["artifact_ref"]["sha256"]:
            raise ValueError("MaterialRevision source lineage artifact drifted")
        source_kind = _string(
            lineage, "source_kind", "MaterialRevision source lineage"
        )
        if source_kind not in SOURCE_KINDS:
            raise ValueError("MaterialRevision source lineage kind is unsupported")
        _validate_source_locator(source_kind, lineage.get("source_locator"))
        _sha256(
            lineage,
            "source_identity_sha256",
            "MaterialRevision source lineage",
        )
        _string(
            lineage, "original_filename", "MaterialRevision source lineage"
        )
        expected_lineage_sha256 = _canonical_sha256(
            {
                key: item
                for key, item in lineage.items()
                if key != "lineage_sha256"
            }
        )
        if lineage.get("lineage_sha256") != expected_lineage_sha256:
            raise ValueError("MaterialRevision source lineage digest drifted")
    _timestamp(value, "created_at", "MaterialRevision")
    return deepcopy(dict(value))


def _validate_locator_payload(kind: str, payload_value: object) -> None:
    payload = _mapping(payload_value, f"{kind} locator")
    if not payload:
        raise ValueError(f"{kind} locator payload is missing")
    keys = set(payload)
    if kind == "text_span":
        allowed = {"start_char", "end_char", "start_line", "end_line", "start_paragraph", "end_paragraph"}
        pairs = [
            {"start_char", "end_char"},
            {"start_line", "end_line"},
            {"start_paragraph", "end_paragraph"},
        ]
        complete_pairs = [pair for pair in pairs if pair <= keys]
        if (
            not keys <= allowed
            or not complete_pairs
            or set().union(*complete_pairs) != keys
        ):
            raise ValueError("text_span locator requires an exact character, line, or paragraph range")
        if any(
            not isinstance(payload.get(key), int)
            or isinstance(payload.get(key), bool)
            or payload[key] < 0
            for key in keys
        ):
            raise ValueError("text_span locator bounds must be non-negative integers")
        for start, end in (
            ("start_char", "end_char"),
            ("start_line", "end_line"),
            ("start_paragraph", "end_paragraph"),
        ):
            if start in keys and (end not in keys or payload[start] > payload[end]):
                raise ValueError("text_span locator range is reversed or incomplete")
    elif kind in {"pdf_region", "image_region"}:
        required = {"normalized_rect"} | ({"page"} if kind == "pdf_region" else set())
        if keys != required:
            raise ValueError(f"{kind} locator requires an exact normalized region")
        rect = payload.get("normalized_rect")
        if (
            not isinstance(rect, list)
            or len(rect) != 4
            or any(not isinstance(item, (int, float)) or isinstance(item, bool) or item < 0 or item > 1 for item in rect)
        ):
            raise ValueError(f"{kind} locator normalized region is invalid")
        if rect[0] >= rect[2] or rect[1] >= rect[3]:
            raise ValueError(f"{kind} locator normalized region is empty or inverted")
        if kind == "pdf_region" and (
            not isinstance(payload.get("page"), int)
            or isinstance(payload.get("page"), bool)
            or payload["page"] < 1
        ):
            raise ValueError("pdf_region locator page must be a positive integer")
    elif kind == "document_section":
        if not {"heading_path", "paragraph_start", "paragraph_end"} <= keys or not keys <= {"heading_path", "paragraph_start", "paragraph_end"}:
            raise ValueError("document_section locator requires heading and paragraph identity")
        heading = payload.get("heading_path")
        start = payload.get("paragraph_start")
        end = payload.get("paragraph_end")
        if (
            not isinstance(heading, list)
            or not heading
            or any(not isinstance(item, str) or not item.strip() for item in heading)
            or not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start < 0
            or end < start
        ):
            raise ValueError("document_section locator identity or range is invalid")
    elif kind == "spreadsheet_range":
        if "cell_range" not in keys or not keys & {"sheet_id", "sheet_name"}:
            raise ValueError("spreadsheet_range locator requires sheet identity")
        if not keys <= {"sheet_id", "sheet_name", "cell_range"}:
            raise ValueError("spreadsheet_range locator payload is not exact")
        identities = keys & {"sheet_id", "sheet_name"}
        cell_range = payload.get("cell_range")
        if (
            len(identities) != 1
            or any(
                not isinstance(payload.get(identity), str)
                or not payload[identity].strip()
                for identity in identities
            )
            or not isinstance(cell_range, str)
            or not (
                A1_RANGE_RE.fullmatch(cell_range)
                or R1C1_RANGE_RE.fullmatch(cell_range)
            )
            or not _spreadsheet_range_is_bounded_and_ordered(cell_range)
        ):
            raise ValueError("spreadsheet_range locator sheet or cell range is invalid")
    elif kind == "archive_member":
        if keys != {"member_path"}:
            raise ValueError("archive_member locator requires one exact member path")
        member_path = payload.get("member_path")
        if not isinstance(member_path, str):
            raise ValueError("archive member path is unsafe")
        try:
            _safe_archive_member(member_path)
        except ValueError as error:
            raise ValueError("archive member path locator is unsafe") from error
    elif kind == "json_pointer":
        if keys != {"pointer"} or not isinstance(payload.get("pointer"), str) or JSON_POINTER_RE.fullmatch(payload["pointer"]) is None:
            raise ValueError("json_pointer locator is invalid")
    elif kind == "xml_xpath":
        xpath = payload.get("xpath")
        if keys != {"xpath"} or not _xpath_is_allowlisted_absolute(xpath):
            raise ValueError("xml_xpath locator is invalid")
    elif kind == "audio_segment":
        if keys != {"start_ms", "end_ms"}:
            raise ValueError("audio_segment locator requires exact start and end")
        start = payload.get("start_ms")
        end = payload.get("end_ms")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start < 0
            or end <= start
        ):
            raise ValueError("audio_segment locator range is invalid")
    elif kind == "lca_object":
        if keys != {"dataset_uuid", "version", "object_type", "field_path"}:
            raise ValueError("lca_object locator requires exact object identity and field path")
        try:
            uuid.UUID(str(payload.get("dataset_uuid")))
        except (ValueError, AttributeError) as error:
            raise ValueError("lca_object locator dataset UUID is invalid") from error
        version = payload.get("version")
        object_type = payload.get("object_type")
        field_path = payload.get("field_path")
        if (
            not isinstance(version, str)
            or not version.strip()
            or len(version) > 128
            or object_type not in LCA_OBJECT_TYPES
            or not isinstance(field_path, str)
            or field_path == ""
            or JSON_POINTER_RE.fullmatch(field_path) is None
        ):
            raise ValueError("lca_object locator version, type, or field path is invalid")
    for key, child in payload.items():
        if key not in {"heading_path", "normalized_rect"} and isinstance(child, str) and not child and kind not in {"json_pointer"}:
            raise ValueError(f"{kind} locator {key} is empty")


def validate_evidence_locator(value: Mapping[str, object]) -> dict:
    value = _mapping(value, "EvidenceLocator")
    _exact_fields(
        value,
        required={
            "contract",
            "locator_id",
            "revision_id",
            "locator_kind",
            "locator",
            "content_hash",
            "preview_text_or_thumbnail_ref",
            "assurance_acceptance_ref",
        },
        optional={"scientific_status"},
        label="EvidenceLocator",
    )
    _contract(value, "lca_research_kernel.evidence_locator/v1", "EvidenceLocator")
    _string(value, "locator_id", "EvidenceLocator")
    _string(value, "revision_id", "EvidenceLocator")
    kind = _string(value, "locator_kind", "EvidenceLocator")
    if kind not in LOCATOR_KINDS:
        raise ValueError("EvidenceLocator locator_kind is unsupported")
    _validate_locator_payload(kind, value.get("locator"))
    _sha256(value, "content_hash", "EvidenceLocator")
    if value.get("preview_text_or_thumbnail_ref") is not None:
        _opaque_ref(value["preview_text_or_thumbnail_ref"], "EvidenceLocator preview reference")
    acceptance = value.get("assurance_acceptance_ref")
    if acceptance is not None:
        _opaque_ref(acceptance, "EvidenceLocator Assurance acceptance reference")
    scientific_status = value.get("scientific_status")
    if scientific_status is not None and scientific_status not in {"candidate", "accepted_evidence", "rejected"}:
        raise ValueError("EvidenceLocator scientific_status is unsupported")
    if scientific_status == "accepted_evidence" and acceptance is None:
        raise ValueError("accepted evidence requires an Assurance acceptance reference")
    return deepcopy(dict(value))


def validate_processing_receipt(value: Mapping[str, object]) -> dict:
    value = _mapping(value, "MaterialProcessingReceipt")
    _exact_fields(
        value,
        required={
            "contract",
            "revision_id",
            "processor",
            "processing_mode",
            "status",
            "input_sha256",
            "detected_type",
            "output_refs",
            "completeness",
            "warnings",
            "failure_category",
            "duration_ms",
            "replay_key",
            "candidate_only",
        },
        label="MaterialProcessingReceipt",
    )
    _contract(
        value,
        "lca_research_kernel.material_processing_receipt/v1",
        "MaterialProcessingReceipt",
    )
    _string(value, "revision_id", "MaterialProcessingReceipt")
    processor = _mapping(value.get("processor"), "MaterialProcessingReceipt processor")
    if set(processor) != {"id", "version"}:
        raise ValueError("MaterialProcessingReceipt processor identity is invalid")
    _string(processor, "id", "MaterialProcessingReceipt processor")
    _string(processor, "version", "MaterialProcessingReceipt processor")
    mode = _string(value, "processing_mode", "MaterialProcessingReceipt")
    if mode not in PROCESSING_MODES:
        raise ValueError("MaterialProcessingReceipt processing_mode is unsupported")
    status = _string(value, "status", "MaterialProcessingReceipt")
    if status not in {"ready", "partial", "needs_user", "unsupported", "failed"}:
        raise ValueError("MaterialProcessingReceipt status is unsupported")
    _sha256(value, "input_sha256", "MaterialProcessingReceipt")
    detected_type = _mapping(value.get("detected_type"), "MaterialProcessingReceipt detected_type")
    if set(detected_type) != {"mime", "signatures"} or not isinstance(detected_type.get("signatures"), list):
        raise ValueError("MaterialProcessingReceipt detected_type is invalid")
    _string(detected_type, "mime", "MaterialProcessingReceipt detected_type")
    refs = value.get("output_refs")
    if not isinstance(refs, list):
        raise ValueError("MaterialProcessingReceipt output_refs must be a list")
    for reference in refs:
        _opaque_ref(reference, "MaterialProcessingReceipt output reference")
    completeness = _mapping(value.get("completeness"), "MaterialProcessingReceipt completeness")
    if set(completeness) != {"status", "unprocessed_regions"} or not isinstance(completeness.get("unprocessed_regions"), list):
        raise ValueError("MaterialProcessingReceipt completeness is invalid")
    if not isinstance(value.get("warnings"), list):
        raise ValueError("MaterialProcessingReceipt warnings must be a list")
    if value.get("failure_category") is not None and not isinstance(value.get("failure_category"), str):
        raise ValueError("MaterialProcessingReceipt failure_category is invalid")
    duration = value.get("duration_ms")
    if not isinstance(duration, int) or isinstance(duration, bool) or duration < 0:
        raise ValueError("MaterialProcessingReceipt duration_ms is invalid")
    _sha256(value, "replay_key", "MaterialProcessingReceipt")
    if value.get("candidate_only") is not True:
        raise ValueError("MaterialProcessingReceipt output must remain candidate_only")
    return deepcopy(dict(value))


def active_user_turn_sha256(project_view: Mapping[str, object]) -> str | None:
    """Return the accepted active turn identity, if one exists."""

    facts = project_view.get("facts") or {}
    turn = facts.get("active-user-turn") if isinstance(facts, Mapping) else None
    value = turn.get("value") if isinstance(turn, Mapping) else None
    digest = value.get("turn_sha256") if isinstance(value, Mapping) else None
    return digest if isinstance(digest, str) and SHA256_RE.fullmatch(digest) else None


def material_revision_context_eligibility(
    project_view: Mapping[str, object], revision_id: str
) -> tuple[bool, str | None]:
    """Apply the design 12.1 turn/project scope rule to one accepted revision."""

    materials = project_view.get("materials") or {}
    revisions = project_view.get("material_revisions") or {}
    bindings = project_view.get("material_revision_turn_bindings") or {}
    if not all(isinstance(value, Mapping) for value in (materials, revisions, bindings)):
        raise ValueError("material context scope state is invalid")
    revision = revisions.get(revision_id)
    material = (
        materials.get(revision.get("material_id"))
        if isinstance(revision, Mapping)
        else None
    )
    if not isinstance(material, Mapping):
        return False, "material_not_accepted"
    if material.get("scope") == "project":
        return True, None
    if material.get("scope") != "turn":
        return False, "material_not_allowed_by_scope"
    active_turn = active_user_turn_sha256(project_view)
    bound_turn = bindings.get(revision_id)
    if active_turn is None or not isinstance(bound_turn, str):
        return False, "turn_scope_binding_absent"
    if isinstance(bound_turn, str) and bound_turn == active_turn:
        return True, None
    return False, "turn_scope_not_active"


def material_selection_basis(project_view: Mapping[str, object]) -> dict:
    """Content-address the accepted material state, not the whole Project Record."""

    if not isinstance(project_view, Mapping):
        raise ValueError("material selection Project Record state is invalid")
    keys = ("materials", "material_revisions", "evidence_locators")
    present = {key for key in keys if key in project_view}
    if present and present != set(keys):
        raise ValueError("material selection Project Record state is incomplete")
    state = {}
    for key in keys:
        value = project_view.get(key, {})
        if not isinstance(value, Mapping):
            raise ValueError("material selection Project Record state is invalid")
        state[key] = deepcopy(dict(value))
    return {
        "contract": MATERIAL_SELECTION_BASIS_V1,
        "material_state_sha256": _canonical_sha256(state),
    }


def _validate_material_selection_basis(value: object) -> dict:
    basis = _mapping(value, "MaterialUseDecision selection_basis")
    _exact_fields(
        basis,
        required={"contract", "material_state_sha256"},
        label="MaterialUseDecision selection_basis",
    )
    _contract(
        basis,
        MATERIAL_SELECTION_BASIS_V1,
        "MaterialUseDecision selection_basis",
    )
    _sha256(
        basis,
        "material_state_sha256",
        "MaterialUseDecision selection_basis",
    )
    return deepcopy(dict(basis))


def _project_view_locator_index(
    project_view: Mapping[str, object],
    locator_index: Mapping[str, Mapping[str, object]],
) -> dict:
    """Bind a supplied lookup to the exact accepted locator projection."""

    material_selection_basis(project_view)
    accepted = project_view.get("evidence_locators", {})
    if not isinstance(locator_index, Mapping):
        raise ValueError("MaterialUseDecision locator index is invalid")
    if locator_index != accepted:
        raise ValueError(
            "MaterialUseDecision locator index drifted from Project Record"
        )
    return deepcopy(dict(accepted))


def _validate_material_use_task_ref(value: object) -> dict:
    task_ref = _mapping(value, "MaterialUseDecision task_ref")
    _exact_fields(
        task_ref,
        required={"contract", "project_id", "task_kind", "question_sha256"},
        label="MaterialUseDecision task_ref",
    )
    _contract(task_ref, MATERIAL_USE_TASK_REF_V1, "MaterialUseDecision task_ref")
    _string(task_ref, "project_id", "MaterialUseDecision task_ref")
    _string(task_ref, "task_kind", "MaterialUseDecision task_ref")
    _sha256(task_ref, "question_sha256", "MaterialUseDecision task_ref")
    return deepcopy(dict(task_ref))


def material_use_decision_identity(value: Mapping[str, object]) -> str:
    """Derive stable decision identity from explicit decision content only."""

    if not isinstance(value, Mapping) or value.get("contract") != MATERIAL_USE_DECISION_V2:
        raise ValueError("stable MaterialUseDecision identity requires v2")
    return "material-use-" + _canonical_sha256(
        {
            "contract": MATERIAL_USE_DECISION_IDENTITY_V2,
            "task_ref": deepcopy(value.get("task_ref")),
            "allowed_revision_ids": sorted(value.get("allowed_revision_ids") or []),
            "selected_evidence_locator_ids": sorted(
                value.get("selected_evidence_locator_ids") or []
            ),
            "selection_reasons": deepcopy(value.get("selection_reasons")),
            "exclusions": deepcopy(value.get("exclusions")),
            "context_mode": value.get("context_mode"),
            "expected_state_change": deepcopy(value.get("expected_state_change")),
            "claim_ceiling": value.get("claim_ceiling"),
        }
    )[:24]


def build_material_use_decision(
    *,
    project_view: Mapping[str, object],
    task_kind: str,
    question: object,
    allowed_revision_ids: Sequence[str],
    selected_evidence_locator_ids: Sequence[str],
    selection_reasons: Mapping[str, str],
    exclusions: Sequence[Mapping[str, object]],
    context_mode: str,
    expected_state_change: Mapping[str, object],
    claim_ceiling: str,
    locator_index: Mapping[str, Mapping[str, object]],
) -> dict:
    """Build the sole current MaterialUseDecision identity/freshness contract."""

    accepted_locator_index = _project_view_locator_index(
        project_view, locator_index
    )
    if not isinstance(task_kind, str) or not task_kind.strip():
        raise ValueError("MaterialUseDecision task kind is absent")
    try:
        question_sha256 = _canonical_sha256(question)
    except (TypeError, ValueError) as error:
        raise ValueError("MaterialUseDecision question is not canonical JSON") from error
    decision = {
        "contract": MATERIAL_USE_DECISION_V2,
        "task_id": "pending",
        "task_ref": {
            "contract": MATERIAL_USE_TASK_REF_V1,
            "project_id": str(project_view.get("project_id") or ""),
            "task_kind": task_kind,
            "question_sha256": question_sha256,
        },
        "selection_basis": material_selection_basis(project_view),
        "allowed_revision_ids": list(allowed_revision_ids),
        "selected_evidence_locator_ids": list(selected_evidence_locator_ids),
        "selection_reasons": deepcopy(dict(selection_reasons)),
        "exclusions": deepcopy(list(exclusions)),
        "context_mode": context_mode,
        "expected_state_change": deepcopy(dict(expected_state_change)),
        "claim_ceiling": claim_ceiling,
    }
    decision["task_id"] = material_use_decision_identity(decision)
    return validate_material_use_decision(
        decision, locator_index=accepted_locator_index
    )


def material_use_decision_checkpoint(
    decision: Mapping[str, object],
    *,
    accepted_decision_event_sha256: str,
    locator_index: Mapping[str, Mapping[str, object]],
) -> dict:
    normalized = validate_material_use_decision(
        decision, locator_index=locator_index
    )
    if normalized.get("contract") != MATERIAL_USE_DECISION_V2:
        raise ValueError("MaterialUseDecision checkpoint requires v2")
    if (
        not isinstance(accepted_decision_event_sha256, str)
        or SHA256_RE.fullmatch(accepted_decision_event_sha256) is None
    ):
        raise ValueError("accepted material use decision event is invalid")
    return {
        "contract": MATERIAL_USE_DECISION_CHECKPOINT_V2,
        "decision_id": normalized["task_id"],
        "decision_sha256": _canonical_sha256(normalized),
        "selection_basis": deepcopy(normalized.get("selection_basis")),
        "accepted_decision_event_sha256": accepted_decision_event_sha256,
    }


def _validate_material_use_decision_checkpoint_proof(
    checkpoint: Mapping[str, object],
    *,
    decision: Mapping[str, object],
    project_view: Mapping[str, object],
    locator_index: Mapping[str, Mapping[str, object]],
    accepted_decision_event: Mapping[str, object],
) -> dict:
    value = _mapping(checkpoint, "MaterialUseDecision checkpoint")
    _exact_fields(
        value,
        required={
            "contract",
            "decision_id",
            "decision_sha256",
            "selection_basis",
            "accepted_decision_event_sha256",
        },
        label="MaterialUseDecision checkpoint",
    )
    _contract(value, MATERIAL_USE_DECISION_CHECKPOINT_V2, "MaterialUseDecision checkpoint")
    _string(value, "decision_id", "MaterialUseDecision checkpoint")
    _sha256(value, "decision_sha256", "MaterialUseDecision checkpoint")
    _sha256(
        value,
        "accepted_decision_event_sha256",
        "MaterialUseDecision checkpoint",
    )
    basis = _validate_material_selection_basis(value.get("selection_basis"))
    accepted_locator_index = _project_view_locator_index(
        project_view, locator_index
    )
    normalized = validate_material_use_decision(
        decision, locator_index=accepted_locator_index
    )
    if normalized["contract"] != MATERIAL_USE_DECISION_V2:
        raise ValueError("MaterialUseDecision checkpoint requires v2")
    expected_id = material_use_decision_identity(normalized)
    if (
        value.get("decision_id") != expected_id
        or normalized.get("task_id") != expected_id
        or value.get("decision_sha256") != _canonical_sha256(normalized)
        or basis != normalized.get("selection_basis")
        or basis != material_selection_basis(project_view)
        or normalized.get("task_ref", {}).get("project_id")
        != project_view.get("project_id")
    ):
        raise ValueError("material use decision checkpoint state drifted")
    if not isinstance(accepted_decision_event, Mapping):
        raise ValueError(
            "accepted material use decision event proof is invalid"
        )
    stored_event_sha256 = accepted_decision_event.get("event_sha256")
    unsigned_event = {
        key: item
        for key, item in accepted_decision_event.items()
        if key != "event_sha256"
    }
    payload = accepted_decision_event.get("payload")
    candidate = payload.get("candidate") if isinstance(payload, Mapping) else None
    acceptance = (
        payload.get("assurance_acceptance")
        if isinstance(payload, Mapping)
        else None
    )
    if (
        stored_event_sha256 != _canonical_sha256(unsigned_event)
        or stored_event_sha256
        != value.get("accepted_decision_event_sha256")
        or accepted_decision_event.get("event_type")
        != "research.project.material.use_decision_recorded"
        or not isinstance(candidate, Mapping)
        or candidate.get("change_type") != "material.use_decision_recorded"
        or not isinstance(acceptance, Mapping)
        or acceptance.get("decision") != "accepted"
        or acceptance.get("evaluator") != "assurance_boundary"
        or acceptance.get("candidate_sha256") != _canonical_sha256(candidate)
        or candidate.get("decision_id") != expected_id
        or candidate.get("material_use_decision") != normalized
        or candidate.get("decision_sha256") != _canonical_sha256(normalized)
    ):
        raise ValueError("accepted material use decision event proof drifted")
    return deepcopy(dict(value))


def validate_material_use_decision(
    value: Mapping[str, object], *, locator_index: Mapping[str, Mapping[str, object]]
) -> dict:
    value = _mapping(value, "MaterialUseDecision")
    contract = value.get("contract")
    required = {
        "contract",
        "allowed_revision_ids",
        "selected_evidence_locator_ids",
        "selection_reasons",
        "exclusions",
        "context_mode",
        "expected_state_change",
    }
    optional = {"task_id", "claim_ceiling"}
    if contract == MATERIAL_USE_DECISION_V2:
        required.update({"task_id", "task_ref", "selection_basis"})
        optional = {"claim_ceiling"}
    elif contract != MATERIAL_USE_DECISION_V1:
        raise ValueError("MaterialUseDecision contract is unsupported")
    _exact_fields(value, required=required, optional=optional, label="MaterialUseDecision")
    if "task_id" in value:
        _string(value, "task_id", "MaterialUseDecision")
    allowed = value.get("allowed_revision_ids")
    selected = value.get("selected_evidence_locator_ids")
    if not isinstance(allowed, list) or any(not isinstance(item, str) or not item for item in allowed):
        raise ValueError("MaterialUseDecision allowed_revision_ids are invalid")
    if not isinstance(selected, list) or any(not isinstance(item, str) or not item for item in selected):
        raise ValueError("MaterialUseDecision selected locator ids are invalid")
    if len(set(allowed)) != len(allowed) or len(set(selected)) != len(selected):
        raise ValueError("MaterialUseDecision identifiers must be unique")
    allowed_set = set(allowed)
    if not isinstance(locator_index, Mapping):
        raise ValueError("MaterialUseDecision locator index is invalid")
    for locator_id in selected:
        locator_value = locator_index.get(locator_id)
        if not isinstance(locator_value, Mapping):
            raise ValueError("selected evidence locator is unknown")
        revision_id = locator_value.get("revision_id")
        if revision_id not in allowed_set:
            raise ValueError("selected material is not allowed")
        if locator_value.get("scientific_status") == "accepted_evidence" and not locator_value.get("assurance_acceptance_ref"):
            raise ValueError("accepted evidence requires an Assurance acceptance reference")
    reasons = _mapping(value.get("selection_reasons"), "MaterialUseDecision selection_reasons")
    if set(reasons) != set(selected) or any(not isinstance(reason, str) or not reason.strip() for reason in reasons.values()):
        raise ValueError("MaterialUseDecision selection reasons must cover selected locators exactly")
    if not isinstance(value.get("exclusions"), list):
        raise ValueError("MaterialUseDecision exclusions must be a list")
    mode = _string(value, "context_mode", "MaterialUseDecision")
    if mode not in PROCESSING_MODES:
        raise ValueError("MaterialUseDecision context_mode is unsupported")
    if not _mapping(value.get("expected_state_change"), "MaterialUseDecision expected_state_change"):
        raise ValueError("MaterialUseDecision expected_state_change is empty")
    if "claim_ceiling" in value:
        _string(value, "claim_ceiling", "MaterialUseDecision")
    if contract == MATERIAL_USE_DECISION_V2:
        normalized_task_ref = _validate_material_use_task_ref(value.get("task_ref"))
        _validate_material_selection_basis(value.get("selection_basis"))
        if normalized_task_ref.get("project_id") == "":
            raise ValueError("MaterialUseDecision task project is absent")
        if value.get("task_id") != material_use_decision_identity(value):
            raise ValueError("MaterialUseDecision stable identity drifted")
    return deepcopy(dict(value))


def validate_source_bound_material_revision(
    value: Mapping[str, object],
) -> dict:
    """Validate the metadata-only handoff from material processing to research."""

    value = _mapping(value, "SourceBoundMaterialRevision")
    _exact_fields(
        value,
        required={
            "contract",
            "research_material",
            "material_revision",
            "processing_receipt",
            "processing_receipt_ref",
            "evidence_units_ref",
            "locators",
            "evidence_unit_bindings",
        },
        optional={"locator_index_ref"},
        label="SourceBoundMaterialRevision",
    )
    _contract(
        value,
        "lca_research_kernel.source_bound_material_revision/v1",
        "SourceBoundMaterialRevision",
    )
    material = validate_research_material(value.get("research_material"))
    revision = validate_material_revision(value.get("material_revision"))
    receipt = validate_processing_receipt(value.get("processing_receipt"))
    receipt_ref = _opaque_ref(
        value.get("processing_receipt_ref"),
        "SourceBoundMaterialRevision processing receipt reference",
    )
    evidence_ref = _opaque_ref(
        value.get("evidence_units_ref"),
        "SourceBoundMaterialRevision evidence units reference",
    )
    if revision["material_id"] != material["material_id"]:
        raise ValueError("material revision identity drifted")
    if revision["revision_id"] != material["current_revision_id"]:
        raise ValueError("current material revision identity drifted")
    if receipt["revision_id"] != revision["revision_id"]:
        raise ValueError("processing receipt revision identity drifted")
    if receipt["status"] != revision["processing_status"]:
        raise ValueError("processing receipt status drifted")
    if receipt["input_sha256"] != revision["artifact_ref"]["sha256"]:
        raise ValueError("processing input SHA-256 drifted")
    if evidence_ref not in revision["extraction_refs"]:
        raise ValueError("evidence units reference is not an accepted extraction")
    if evidence_ref not in receipt["output_refs"]:
        raise ValueError("evidence units reference is absent from processing receipt")

    locators_value = value.get("locators")
    if not isinstance(locators_value, list) or not locators_value:
        raise ValueError("SourceBoundMaterialRevision locators must be a non-empty list")
    locators = [validate_evidence_locator(locator) for locator in locators_value]
    locator_index: dict[str, dict] = {}
    for locator in locators:
        if locator["revision_id"] != revision["revision_id"]:
            raise ValueError("locator revision identity drifted")
        if locator["locator_id"] in locator_index:
            raise ValueError("locator identity is duplicated")
        locator_index[locator["locator_id"]] = locator

    locator_index_ref = value.get("locator_index_ref")
    if locator_index_ref is not None:
        locator_index_ref = _opaque_ref(
            locator_index_ref,
            "SourceBoundMaterialRevision locator index reference",
        )
        if locator_index_ref not in receipt["output_refs"]:
            raise ValueError("locator index reference is absent from processing receipt")

    bindings_value = value.get("evidence_unit_bindings")
    bindings = []
    if not isinstance(bindings_value, list) or not bindings_value:
        raise ValueError("evidence unit bindings must be a non-empty list")
    observed: set[str] = set()
    for raw in bindings_value:
        binding = _mapping(raw, "EvidenceUnitBinding")
        _exact_fields(
            binding,
            required={"locator_id", "content_hash", "source_span", "evidence_unit"},
            label="EvidenceUnitBinding",
        )
        locator_id = _opaque_id(
            binding.get("locator_id"), "EvidenceUnitBinding locator_id"
        )
        content_hash = _sha256(binding, "content_hash", "EvidenceUnitBinding")
        evidence_unit = _mapping(
            binding.get("evidence_unit"), "EvidenceUnitBinding evidence_unit"
        )
        canonical_unit_hash = hashlib.sha256(
            (
                json.dumps(
                    evidence_unit,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
        ).hexdigest()
        if canonical_unit_hash != content_hash:
            raise ValueError("evidence unit content hash drifted")
        span = _mapping(binding.get("source_span"), "EvidenceUnitBinding source_span")
        if evidence_unit.get("source_span") != span:
            raise ValueError("evidence unit source span drifted")
        if set(span) != {"kind", "locator"}:
            raise ValueError("EvidenceUnitBinding source span is invalid")
        kind = _string(span, "kind", "EvidenceUnitBinding source span")
        if kind not in LOCATOR_KINDS:
            raise ValueError("EvidenceUnitBinding locator kind is unsupported")
        _validate_locator_payload(kind, span.get("locator"))
        locator = locator_index.get(locator_id)
        if locator is None:
            raise ValueError("evidence unit binding locator is unknown")
        if (
            locator["content_hash"] != content_hash
            or locator["locator_kind"] != kind
            or locator["locator"] != span["locator"]
        ):
            raise ValueError("locator content drifted from evidence unit binding")
        if locator_id in observed:
            raise ValueError("evidence unit binding locator is duplicated")
        observed.add(locator_id)
        bindings.append(deepcopy(dict(binding)))
    if observed != set(locator_index):
        raise ValueError("evidence unit bindings do not cover locators exactly")

    normalized = deepcopy(dict(value))
    normalized["research_material"] = material
    normalized["material_revision"] = revision
    normalized["processing_receipt"] = receipt
    normalized["processing_receipt_ref"] = receipt_ref
    normalized["evidence_units_ref"] = evidence_ref
    normalized["locators"] = locators
    if locator_index_ref is not None:
        normalized["locator_index_ref"] = locator_index_ref
    normalized["evidence_unit_bindings"] = bindings
    return normalized
