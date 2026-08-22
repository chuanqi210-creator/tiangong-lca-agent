"""Manifest-first, bounded inspection for untrusted archive inputs."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import struct
import tempfile
import uuid
import zipfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .tidas_object_graph_parser import parse_tidas_object_graph_slice

MAX_COMPRESSED_BYTES = 100 * 1024 * 1024
MAX_EXTRACTED_BYTES = 500 * 1024 * 1024
MAX_MEMBER_COUNT = 1000
MAX_RATIO = 100
MAX_NESTING_DEPTH = 1
MAX_MEMBER_SAMPLE_BYTES = 64 * 1024
MAX_STRUCTURED_MEMBER_BYTES = 16 * 1024 * 1024

_DRIVE = re.compile(r"^[A-Za-z]:")
_EXECUTABLE_SUFFIXES = {
    ".app", ".bat", ".bin", ".cmd", ".com", ".dll", ".dylib", ".exe",
    ".jar", ".js", ".msi", ".ps1", ".py", ".sh", ".so", ".vbs",
}
_MACRO_NAMES = {"vbaproject.bin", "xl/macrosheets", "word/vba", "ppt/vba"}
_ARCHIVE_SUFFIXES = {".zip", ".rar", ".7z", ".tar", ".tgz", ".gz", ".bz2", ".xz"}
_TIDAS_PACKAGE_TABLES = {
    "contacts",
    "flowproperties",
    "flows",
    "lifecyclemodels",
    "lciamethods",
    "processes",
    "sources",
    "unitgroups",
}


class ArchiveInspectionError(ValueError):
    """The archive cannot be safely inspected under the frozen policy."""


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _normalized_name(name: str) -> tuple[str, str]:
    if "\x00" in name:
        raise ArchiveInspectionError("archive member name contains NUL")
    if name.startswith(("/", "\\")):
        raise ArchiveInspectionError("archive member path is absolute")
    if _DRIVE.match(name) or PureWindowsPath(name).drive:
        raise ArchiveInspectionError("archive member path has a Windows drive")
    if "\\" in name:
        raise ArchiveInspectionError("archive member path uses unsafe separators")
    posix = PurePosixPath(name)
    if not name or posix.is_absolute() or any(part in {"", ".", ".."} for part in posix.parts):
        raise ArchiveInspectionError("archive member path traversal is unsafe")
    normalized = "/".join(posix.parts)
    return normalized, normalized.casefold()


def _unix_mode(info: zipfile.ZipInfo) -> int:
    return (info.external_attr >> 16) & 0xFFFF if info.create_system == 3 else 0


def _has_hardlink_marker(info: zipfile.ZipInfo) -> bool:
    """Reject the Unix extra-field link-count shape instead of interpreting it."""

    offset = 0
    while offset + 4 <= len(info.extra):
        identifier, length = struct.unpack_from("<HH", info.extra, offset)
        offset += 4
        body = info.extra[offset : offset + length]
        offset += length
        if identifier == 0x000D and len(body) >= 4:
            return struct.unpack_from("<I", body, 0)[0] > 1
    return False


def _validate_member_identity(info: zipfile.ZipInfo) -> tuple[str, str]:
    name, normalized = _normalized_name(info.filename)
    mode = _unix_mode(info)
    kind = stat.S_IFMT(mode) if mode else 0
    if kind and kind not in {stat.S_IFREG, stat.S_IFDIR}:
        raise ArchiveInspectionError("archive special link or device member is forbidden")
    if _has_hardlink_marker(info):
        raise ArchiveInspectionError("archive hardlink member is forbidden")
    return name, normalized


def _detect_member_type(name: str, content: bytes) -> dict[str, Any]:
    from .document_processors import detect_document_type

    detection = detect_document_type(content)
    if content.startswith(b"SQLite format 3\x00"):
        detection = {"mime": "application/x-sqlite3", "signatures": ["sqlite3_header"]}
    return detection


def _format_signatures(name: str, sample: bytes) -> list[str]:
    signatures: list[str] = []
    text = sample.decode("utf-8", errors="replace")
    lowered = text.casefold()
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        value = None
    if isinstance(value, Mapping):
        if _tidas_package_manifest_summary(value) is not None:
            signatures.append("tiangong_tidas_package_manifest")
        if _tidas_lifecycle_model_summary(value) is not None:
            signatures.append("tiangong_tidas_lifecycle_model")
        context = value.get("@context")
        context_text = json.dumps(context, ensure_ascii=False) if context is not None else ""
        if "tidas" in context_text.casefold() and any(
            key in value
            for key in ("processDataSet", "flowDataSet", "sourceDataSet", "contactDataSet")
        ):
            signatures.extend(["tidas_context", "tidas_dataset_root"])
        object_type = value.get("@type") or value.get("type")
        object_id = value.get("@id") or value.get("id")
        if isinstance(object_type, str) and isinstance(object_id, str):
            signatures.extend([f"openlca_object:{object_type.casefold()}", "openlca_object_id"])
    for root in ("processdataset", "flowdataset", "sourcedataset", "contactdataset"):
        if f"<{root}" in lowered and "lca.jrc.it/ilcd" in lowered:
            signatures.extend([f"ilcd_root:{root}", "ilcd_namespace"])
    if re.search(r"<\s*ecospold(?:\s|>)", lowered):
        signatures.append("ecospold_xml_root")
        if "ecospold02" in lowered or "activitydataset" in lowered:
            signatures.append("ecospold2_activity_dataset")
        elif "<dataset" in lowered:
            signatures.append("ecospold1_dataset")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and re.fullmatch(r"\{SimaPro\s+[^}]+\}", lines[0], re.IGNORECASE):
        signatures.append("simapro_version_header")
        if {line.casefold() for line in lines[1:]} & {"process", "products", "materials/fuels"}:
            signatures.append("simapro_section_header")
    if sample.startswith(b"SQLite format 3\x00") and "openlca" in lowered:
        signatures.append("openlca_sqlite_schema")
    return sorted(set(signatures))


def _multilingual(value: object) -> dict[str, str]:
    rows = value if isinstance(value, list) else [value]
    result: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        text = row.get("#text")
        language = row.get("@xml:lang") or row.get("@lang") or "und"
        if isinstance(text, str) and text.strip() and isinstance(language, str):
            result[language] = text.strip()
    return dict(sorted(result.items()))


def _tidas_package_manifest_summary(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    if value.get("format") != "tiangong-tidas-package" or value.get("version") != 2:
        return None
    exported_at = value.get("exported_at")
    scope = value.get("scope")
    roots = value.get("roots")
    entries = value.get("entries")
    if (
        not isinstance(exported_at, str)
        or not exported_at.strip()
        or not isinstance(scope, str)
        or not scope.strip()
        or not isinstance(roots, list)
        or not roots
        or not isinstance(entries, list)
    ):
        return None

    normalized_roots: list[dict[str, str]] = []
    for root in roots:
        if not isinstance(root, Mapping):
            return None
        table = root.get("table")
        identifier = root.get("id")
        version = root.get("version")
        if (
            table not in _TIDAS_PACKAGE_TABLES
            or not isinstance(identifier, str)
            or not isinstance(version, str)
        ):
            return None
        try:
            uuid.UUID(identifier)
        except ValueError:
            return None
        normalized_roots.append(
            {"table": table, "id": identifier, "version": version}
        )

    entry_counts: dict[str, int] = {}
    verified = 0
    for entry in entries:
        if not isinstance(entry, Mapping):
            return None
        table = entry.get("table")
        identifier = entry.get("id")
        version = entry.get("version")
        file_path = entry.get("file_path")
        rule_verification = entry.get("rule_verification")
        if (
            table not in _TIDAS_PACKAGE_TABLES
            or not isinstance(identifier, str)
            or not isinstance(version, str)
            or not isinstance(file_path, str)
            or not isinstance(rule_verification, bool)
        ):
            return None
        try:
            uuid.UUID(identifier)
            normalized_path, _ = _normalized_name(file_path)
        except (ValueError, ArchiveInspectionError):
            return None
        if normalized_path != file_path:
            return None
        entry_counts[table] = entry_counts.get(table, 0) + 1
        verified += int(rule_verification)

    return {
        "contract": "lca_research_service.tidas_package_manifest_summary/v1",
        "format": "tiangong-tidas-package",
        "package_version": 2,
        "exported_at": exported_at,
        "scope": scope,
        "roots": normalized_roots,
        "entry_count": len(entries),
        "entry_counts": dict(sorted(entry_counts.items())),
        "rule_verified_entry_count": verified,
    }


def _tidas_lifecycle_model_summary(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    dataset = value.get("lifeCycleModelDataSet")
    if not isinstance(dataset, Mapping):
        return None
    information = dataset.get("lifeCycleModelInformation")
    administration = dataset.get("administrativeInformation")
    if not isinstance(information, Mapping) or not isinstance(administration, Mapping):
        return None
    dataset_information = information.get("dataSetInformation")
    quantitative_reference = information.get("quantitativeReference")
    technology = information.get("technology")
    publication = administration.get("publicationAndOwnership")
    commissioner = administration.get("common:commissionerAndGoal")
    if (
        not isinstance(dataset_information, Mapping)
        or not isinstance(quantitative_reference, Mapping)
        or not isinstance(technology, Mapping)
        or not isinstance(publication, Mapping)
    ):
        return None
    identifier = dataset_information.get("common:UUID")
    version = publication.get("common:dataSetVersion")
    name = dataset_information.get("name")
    if not isinstance(identifier, str) or not isinstance(version, str) or not isinstance(name, Mapping):
        return None
    try:
        uuid.UUID(identifier)
    except ValueError:
        return None

    name_summary: dict[str, dict[str, str]] = {}
    for source_key, target_key in (
        ("baseName", "base_name"),
        ("functionalUnitFlowProperties", "functional_unit_flow_properties"),
        ("mixAndLocationTypes", "mix_and_location_types"),
        ("treatmentStandardsRoutes", "treatment_standards_routes"),
    ):
        translations = _multilingual(name.get(source_key))
        if translations:
            name_summary[target_key] = translations

    processes = technology.get("processes")
    instances_value = processes.get("processInstance") if isinstance(processes, Mapping) else []
    instances = instances_value if isinstance(instances_value, list) else [instances_value]
    process_summaries: list[dict[str, Any]] = []
    for instance in instances:
        if not isinstance(instance, Mapping):
            continue
        reference = instance.get("referenceToProcess")
        if not isinstance(reference, Mapping):
            continue
        process_uuid = reference.get("@refObjectId")
        process_version = reference.get("@version")
        if not isinstance(process_uuid, str) or not isinstance(process_version, str):
            continue
        try:
            uuid.UUID(process_uuid)
        except ValueError:
            continue
        summary: dict[str, Any] = {
            "internal_id": str(instance.get("@dataSetInternalID", "")),
            "multiplication_factor": str(instance.get("@multiplicationFactor", "")),
            "process_uuid": process_uuid,
            "version": process_version,
        }
        description = _multilingual(reference.get("common:shortDescription"))
        if description:
            summary["description"] = description
        process_summaries.append(summary)

    intended = (
        _multilingual(commissioner.get("common:intendedApplications"))
        if isinstance(commissioner, Mapping)
        else {}
    )
    return {
        "contract": "lca_research_service.tidas_lifecycle_model_summary/v1",
        "dataset_uuid": identifier,
        "version": version,
        "name": name_summary,
        "intended_applications": intended,
        "quantitative_reference": {
            "reference_process_internal_id": quantitative_reference.get(
                "referenceToReferenceProcess"
            )
        },
        "process_instances": process_summaries,
        "candidate_only": True,
        "scientifically_validated": False,
    }


def _structured_member_content(
    name: str, source_fd: int, observed: int, sample: bytes
) -> bytes:
    if Path(name).suffix.casefold() not in {".json", ".jsonld"}:
        return sample
    if observed > MAX_STRUCTURED_MEMBER_BYTES:
        return sample
    os.lseek(source_fd, 0, os.SEEK_SET)
    content = bytearray()
    while len(content) < observed:
        chunk = os.read(source_fd, min(1024 * 1024, observed - len(content)))
        if not chunk:
            break
        content.extend(chunk)
    if len(content) != observed:
        raise ArchiveInspectionError("archive structured member read drifted")
    return bytes(content)


def _structured_member_metadata(content: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return None
    return _tidas_package_manifest_summary(value) or _tidas_lifecycle_model_summary(value)


def _structured_member_object_graph_slice(
    content: bytes,
) -> dict[str, Any] | None:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return None
    return parse_tidas_object_graph_slice(
        value,
        lifecycle_summary=_tidas_lifecycle_model_summary,
        multilingual=_multilingual,
    )


def _tidas_package_root_members(content: bytes) -> list[dict[str, str]] | None:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return None
    summary = _tidas_package_manifest_summary(value)
    entries = value.get("entries") if isinstance(value, Mapping) else None
    if summary is None or not isinstance(entries, list):
        return None
    bindings: list[dict[str, str]] = []
    for root in summary["roots"]:
        matches = [
            entry
            for entry in entries
            if isinstance(entry, Mapping)
            and entry.get("table") == root["table"]
            and entry.get("id") == root["id"]
            and entry.get("version") == root["version"]
            and isinstance(entry.get("file_path"), str)
        ]
        if len(matches) != 1:
            return None
        bindings.append(
            {
                **root,
                "member_path": str(matches[0]["file_path"]),
            }
        )
    return bindings


def _declared_family(name: str) -> set[str] | None:
    suffix = Path(name).suffix.casefold()
    return {
        ".json": {"application/json"},
        ".jsonld": {"application/json", "application/ld+json"},
        ".xml": {"application/xml", "text/xml"},
        ".spold": {"application/xml", "text/xml"},
        ".csv": {"text/csv", "text/plain"},
        ".tsv": {"text/tab-separated-values", "text/plain"},
        ".txt": {"text/plain"},
        ".md": {"text/plain", "text/markdown"},
        ".zolca": {"application/x-sqlite3"},
    }.get(suffix)


def _active_reasons(name: str, detection: Mapping[str, object]) -> list[str]:
    lowered = name.casefold()
    suffix = Path(lowered).suffix
    reasons = []
    if suffix in _EXECUTABLE_SUFFIXES:
        reasons.append("executable")
    if any(marker in lowered for marker in _MACRO_NAMES) or suffix in {".docm", ".xlsm", ".pptm"}:
        reasons.append("macro")
    family = _declared_family(name)
    if family is not None and detection.get("mime") not in family:
        reasons.append("member_mime_drift")
    return reasons


def _name_rejection_reasons(name: str) -> list[str]:
    lowered = name.casefold()
    suffix = Path(lowered).suffix
    reasons = []
    if suffix in _EXECUTABLE_SUFFIXES:
        reasons.append("executable")
    if any(marker in lowered for marker in _MACRO_NAMES) or suffix in {
        ".docm", ".xlsm", ".pptm"
    }:
        reasons.append("macro")
    return sorted(set(reasons))


def _open_root_directory(root: Path) -> int:
    if root.is_symlink() or not root.exists():
        raise ArchiveInspectionError("archive candidate root identity is unsafe")
    descriptor = os.open(
        root,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    status = os.fstat(descriptor)
    if not stat.S_ISDIR(status.st_mode):
        os.close(descriptor)
        raise ArchiveInspectionError("archive candidate root identity is unsafe")
    return descriptor


def _open_child_directory(parent_fd: int, name: str) -> int:
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
    except OSError as error:
        raise ArchiveInspectionError(
            "archive candidate directory identity is unsafe"
        ) from error
    status = os.fstat(descriptor)
    if not stat.S_ISDIR(status.st_mode):
        os.close(descriptor)
        raise ArchiveInspectionError("archive candidate directory identity is unsafe")
    return descriptor


def _open_candidate_parent(root_fd: int, digest: str) -> int:
    current = os.dup(root_fd)
    try:
        for name in ("archive-members", "sha256", digest[:2]):
            child = _open_child_directory(current, name)
            os.close(current)
            current = child
        return current
    except Exception:
        os.close(current)
        raise


def _publish_candidate(root_fd: int, source_fd: int, digest: str) -> dict[str, str]:
    parent_fd = _open_candidate_parent(root_fd, digest)
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            target_fd = os.open(digest, flags, 0o600, dir_fd=parent_fd)
        except FileExistsError:
            try:
                target_fd = os.open(
                    digest,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_fd,
                )
            except OSError as error:
                raise ArchiveInspectionError(
                    "archive candidate target identity is unsafe"
                ) from error
            try:
                status = os.fstat(target_fd)
                if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
                    raise ArchiveInspectionError("archive candidate target identity is unsafe")
                existing = hashlib.sha256()
                while chunk := os.read(target_fd, 1024 * 1024):
                    existing.update(chunk)
                if existing.hexdigest() != digest:
                    raise ArchiveInspectionError("archive candidate target drifted")
            finally:
                os.close(target_fd)
        else:
            try:
                os.lseek(source_fd, 0, os.SEEK_SET)
                while chunk := os.read(source_fd, 1024 * 1024):
                    view = memoryview(chunk)
                    while view:
                        written = os.write(target_fd, view)
                        view = view[written:]
                os.fsync(target_fd)
            except Exception:
                os.close(target_fd)
                os.unlink(digest, dir_fd=parent_fd)
                raise
            else:
                os.close(target_fd)
                _fsync_directory_fd(parent_fd)
    finally:
        os.close(parent_fd)
    return {"artifact_id": digest, "sha256": digest}


def _fsync_directory_fd(descriptor: int) -> None:
    os.fsync(descriptor)


def _check_nested_depth(name: str, source_fd: int) -> int:
    suffix = Path(name).suffix.casefold()
    if suffix not in _ARCHIVE_SUFFIXES:
        return 0
    if suffix != ".zip":
        return 1
    try:
        os.lseek(source_fd, 0, os.SEEK_SET)
        with os.fdopen(os.dup(source_fd), "rb") as nested_source:
            with zipfile.ZipFile(nested_source) as nested:
                for child in nested.infolist():
                    if child.is_dir():
                        continue
                    child_name, _ = _validate_member_identity(child)
                    if Path(child_name).suffix.casefold() in _ARCHIVE_SUFFIXES:
                        raise ArchiveInspectionError("archive nesting depth exceeds policy")
    except ArchiveInspectionError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as error:
        raise ArchiveInspectionError("nested archive cannot be safely inspected") from error
    return 1


def _unsupported_manifest(
    *, input_sha256: str, kind: str, status: str, reason: str
) -> dict[str, Any]:
    return {
        "contract": "lca_research_service.archive_inspection/v1",
        "status": status,
        "archive_manifest": {
            "contract": "lca_research_service.archive_manifest/v1",
            "input_sha256": input_sha256,
            "archive_kind": kind,
            "inspection_order": "signature_only_no_decompression",
            "members": [],
            "rejected_reasons": [reason],
            "candidate_only": True,
            "scientifically_validated": False,
        },
        "candidate_only": True,
        "scientifically_validated": False,
    }


def inspect_archive(
    payload: bytes,
    *,
    input_sha256: str,
    original_filename: str,
    declared_media_type: str,
    candidate_root: str | Path,
) -> dict[str, Any]:
    """Inspect central metadata first and publish only individually allowed bytes."""

    if not isinstance(payload, bytes) or hashlib.sha256(payload).hexdigest() != input_sha256:
        raise ArchiveInspectionError("archive input identity drifted")
    if len(payload) > MAX_COMPRESSED_BYTES:
        raise ArchiveInspectionError("archive compressed size exceeds policy")
    if payload.startswith(b"Rar!\x1a\x07"):
        return _unsupported_manifest(
            input_sha256=input_sha256,
            kind="rar",
            status="needs_user",
            reason="RAR requires a user-provided supported representation",
        )
    if payload.startswith(b"7z\xbc\xaf'\x1c"):
        return _unsupported_manifest(
            input_sha256=input_sha256,
            kind="7z",
            status="unsupported",
            reason="7z decompression is outside the bounded runtime",
        )
    if not payload.startswith(b"PK"):
        raise ArchiveInspectionError("archive signature is unsupported")
    central_names = _raw_central_names(payload)
    if not central_names:
        raise ArchiveInspectionError("archive central directory is corrupt")
    if b"\x00" in central_names:
        raise ArchiveInspectionError("archive member name contains NUL")
    raw_central = _raw_central_entries(payload)

    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
        infos = archive.infolist()
    except (OSError, zipfile.BadZipFile) as error:
        raise ArchiveInspectionError("archive central directory is corrupt") from error
    try:
        if len(infos) != len(raw_central):
            raise ArchiveInspectionError("archive central directory entry count drifted")
        if len(infos) > MAX_MEMBER_COUNT:
            raise ArchiveInspectionError("archive member count exceeds policy")
        projected = sum(info.file_size for info in infos if not info.is_dir())
        if projected > MAX_EXTRACTED_BYTES:
            raise ArchiveInspectionError("archive extracted projection exceeds policy")
        identities: list[tuple[zipfile.ZipInfo, str]] = []
        seen: set[str] = set()
        for info, central in zip(infos, raw_central, strict=True):
            name, normalized = _validate_member_identity(info)
            is_directory = info.is_dir()
            if is_directory:
                normalized = normalized.rstrip("/")
            if normalized in seen:
                raise ArchiveInspectionError("archive has a duplicate normalized member name")
            seen.add(normalized)
            if not is_directory:
                compressed = info.compress_size
                if info.file_size and compressed == 0:
                    raise ArchiveInspectionError("archive compression ratio exceeds policy")
                if compressed and info.file_size / compressed > MAX_RATIO:
                    raise ArchiveInspectionError("archive compression ratio exceeds policy")
            _validate_local_header(payload, info, central)
            identities.append((info, name))

        members = []
        encrypted_seen = False
        root = Path(candidate_root).absolute()
        root_fd = _open_root_directory(root)
        actual_extracted_bytes = 0
        try:
            for info, name in identities:
                row: dict[str, Any] = {
                    "path": name,
                    "is_directory": info.is_dir(),
                    "size": info.file_size,
                    "compressed_size": info.compress_size,
                    "crc32": f"{info.CRC:08x}",
                    "header_offset": info.header_offset,
                    "compression_method": info.compress_type,
                    "encrypted": bool(info.flag_bits & 1),
                    "rejected_reasons": [],
                    "candidate_only": True,
                }
                if info.is_dir():
                    row.update({"disposition": "directory", "sha256": None, "candidate_ref": None, "detected_type": None})
                    members.append(row)
                    continue
                if info.flag_bits & 1:
                    encrypted_seen = True
                    row.update({"disposition": "rejected", "sha256": None, "candidate_ref": None, "detected_type": None})
                    row["rejected_reasons"].append("encrypted")
                    members.append(row)
                    continue
                name_reasons = _name_rejection_reasons(name)
                if name_reasons:
                    row.update(
                        {
                            "disposition": "rejected",
                            "sha256": None,
                            "candidate_ref": None,
                            "detected_type": None,
                            "format_signatures": [],
                            "nesting_depth": 0,
                            "rejected_reasons": name_reasons,
                        }
                    )
                    members.append(row)
                    continue
                with tempfile.TemporaryFile(mode="w+b") as temporary:
                    digest_builder = hashlib.sha256()
                    observed = 0
                    sample = bytearray()
                    try:
                        source = archive.open(info, "r")
                    except (OSError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as error:
                        raise ArchiveInspectionError("archive member header or content is invalid") from error
                    with source:
                        while True:
                            remaining = MAX_EXTRACTED_BYTES - actual_extracted_bytes
                            chunk = source.read(min(1024 * 1024, remaining + 1))
                            if not chunk:
                                break
                            if len(chunk) > remaining:
                                raise ArchiveInspectionError(
                                    "archive actual extracted bytes exceed policy"
                                )
                            observed += len(chunk)
                            if observed > info.file_size or observed > MAX_EXTRACTED_BYTES:
                                raise ArchiveInspectionError("archive member exceeds central directory bounds")
                            digest_builder.update(chunk)
                            temporary.write(chunk)
                            actual_extracted_bytes += len(chunk)
                            if len(sample) < MAX_MEMBER_SAMPLE_BYTES:
                                sample.extend(chunk[: MAX_MEMBER_SAMPLE_BYTES - len(sample)])
                    temporary.flush()
                    os.fsync(temporary.fileno())
                    if observed != info.file_size:
                        raise ArchiveInspectionError("archive member content differs from central directory")
                    nesting_depth = _check_nested_depth(name, temporary.fileno())
                    inspected_content = _structured_member_content(
                        name, temporary.fileno(), observed, bytes(sample)
                    )
                    detection = _detect_member_type(name, inspected_content)
                    reasons = _active_reasons(name, detection)
                    digest = digest_builder.hexdigest()
                    signatures = _format_signatures(name, inspected_content)
                    metadata = _structured_member_metadata(inspected_content)
                    object_graph_slice = _structured_member_object_graph_slice(
                        inspected_content
                    )
                    root_members = _tidas_package_root_members(inspected_content)
                    row.update(
                        {
                            "sha256": digest,
                            "detected_type": detection,
                            "format_signatures": signatures,
                            "nesting_depth": nesting_depth,
                            "rejected_reasons": reasons,
                        }
                    )
                    if metadata is not None:
                        row["package_metadata"] = metadata
                    if object_graph_slice is not None:
                        row["object_graph_slice"] = object_graph_slice
                    if root_members is not None:
                        row["package_root_members"] = root_members
                    if reasons:
                        row.update({"disposition": "rejected", "candidate_ref": None})
                    else:
                        row.update({"disposition": "derived_candidate", "candidate_ref": _publish_candidate(root_fd, temporary.fileno(), digest)})
                    members.append(row)
        finally:
            os.close(root_fd)
        manifest = {
            "contract": "lca_research_service.archive_manifest/v1",
            "input_sha256": input_sha256,
            "archive_kind": "zip",
            "inspection_order": "central_directory_then_member_headers_then_content",
            "member_count": len(infos),
            "projected_extracted_bytes": projected,
            "actual_extracted_bytes": actual_extracted_bytes,
            "members": members,
            "rejected_reasons": [],
            "candidate_only": True,
            "scientifically_validated": False,
        }
        return {
            "contract": "lca_research_service.archive_inspection/v1",
            "status": (
                "needs_user"
                if encrypted_seen
                else "partial"
                if any(row["disposition"] == "rejected" for row in members)
                else "ready"
            ),
            "archive_manifest": manifest,
            "candidate_only": True,
            "scientifically_validated": False,
        }
    finally:
        archive.close()


def _raw_central_names(payload: bytes) -> bytes:
    """Return raw central filenames so zipfile cannot truncate an embedded NUL."""

    return b"".join(entry["name"] for entry in _raw_central_entries(payload))


def _extra_fields(extra: bytes, *, label: str) -> list[tuple[int, bytes]]:
    fields: list[tuple[int, bytes]] = []
    offset = 0
    while offset < len(extra):
        if offset + 4 > len(extra):
            raise ArchiveInspectionError(f"archive {label} extra field is corrupt")
        field_id, length = struct.unpack_from("<HH", extra, offset)
        offset += 4
        end = offset + length
        if end > len(extra):
            raise ArchiveInspectionError(f"archive {label} extra field is corrupt")
        fields.append((field_id, extra[offset:end]))
        offset = end
    return fields


def _resolve_zip64_values(
    extra: bytes,
    *,
    label: str,
    file_size: int,
    compressed_size: int,
    header_offset: int | None = None,
    disk_start: int | None = None,
) -> tuple[int, int, int | None, int | None]:
    sentinels = (
        file_size == 0xFFFFFFFF,
        compressed_size == 0xFFFFFFFF,
        header_offset == 0xFFFFFFFF,
        disk_start == 0xFFFF,
    )
    zip64_fields = [
        value
        for field_id, value in _extra_fields(extra, label=label)
        if field_id == 0x0001
    ]
    if len(zip64_fields) > 1:
        raise ArchiveInspectionError(f"archive {label} ZIP64 extra field is ambiguous")
    if not any(sentinels):
        if zip64_fields:
            raise ArchiveInspectionError(
                f"archive {label} ZIP64 extra field is ambiguous"
            )
        return file_size, compressed_size, header_offset, disk_start
    if len(zip64_fields) != 1:
        raise ArchiveInspectionError(f"archive {label} ZIP64 extra field is absent")
    field = zip64_fields[0]
    cursor = 0
    resolved: list[int | None] = [
        file_size,
        compressed_size,
        header_offset,
        disk_start,
    ]
    widths = (8, 8, 8, 4)
    for index, required in enumerate(sentinels):
        if not required:
            continue
        width = widths[index]
        if cursor + width > len(field):
            raise ArchiveInspectionError(f"archive {label} ZIP64 extra field is corrupt")
        resolved[index] = int.from_bytes(field[cursor : cursor + width], "little")
        cursor += width
    if cursor != len(field):
        raise ArchiveInspectionError(f"archive {label} ZIP64 extra field is ambiguous")
    return resolved[0], resolved[1], resolved[2], resolved[3]  # type: ignore[return-value]


def _central_directory_bounds(payload: bytes) -> tuple[int, int, int]:
    lower = max(0, len(payload) - (65535 + 22))
    eocd = -1
    candidate = payload.rfind(b"PK\x05\x06", lower)
    while candidate >= lower:
        if candidate + 22 <= len(payload):
            length = struct.unpack_from("<H", payload, candidate + 20)[0]
            if candidate + 22 + length == len(payload):
                eocd = candidate
                break
        candidate = payload.rfind(b"PK\x05\x06", lower, candidate)
    if eocd < 0:
        raise ArchiveInspectionError("archive central directory is corrupt")
    disk, central_disk, disk_entries, total_entries = struct.unpack_from(
        "<HHHH", payload, eocd + 4
    )
    central_size, central_offset = struct.unpack_from("<II", payload, eocd + 12)
    comment_length = struct.unpack_from("<H", payload, eocd + 20)[0]
    if eocd + 22 + comment_length != len(payload) or disk != 0 or central_disk != 0:
        raise ArchiveInspectionError("archive central directory is corrupt")
    zip64 = any(
        value == sentinel
        for value, sentinel in (
            (disk_entries, 0xFFFF),
            (total_entries, 0xFFFF),
            (central_size, 0xFFFFFFFF),
            (central_offset, 0xFFFFFFFF),
        )
    )
    if zip64:
        locator = eocd - 20
        if locator < 0 or payload[locator : locator + 4] != b"PK\x06\x07":
            raise ArchiveInspectionError("archive ZIP64 central directory is corrupt")
        zip64_offset = struct.unpack_from("<Q", payload, locator + 8)[0]
        if (
            zip64_offset + 12 > locator
            or payload[zip64_offset : zip64_offset + 4] != b"PK\x06\x06"
        ):
            raise ArchiveInspectionError("archive ZIP64 central directory is corrupt")
        record_size = struct.unpack_from("<Q", payload, zip64_offset + 4)[0]
        if record_size < 44 or zip64_offset + 12 + record_size != locator:
            raise ArchiveInspectionError("archive ZIP64 central directory is corrupt")
        disk, central_disk = struct.unpack_from("<II", payload, zip64_offset + 16)
        disk_entries, total_entries, central_size, central_offset = struct.unpack_from(
            "<QQQQ", payload, zip64_offset + 24
        )
        if disk != 0 or central_disk != 0 or disk_entries != total_entries:
            raise ArchiveInspectionError("archive ZIP64 central directory is unsupported")
        central_end = zip64_offset
    else:
        if disk_entries != total_entries:
            raise ArchiveInspectionError("archive multi-disk central directory is unsupported")
        central_end = eocd
    central_start = central_end - central_size
    if central_start < 0 or central_start + central_size != central_end:
        raise ArchiveInspectionError("archive central directory is corrupt")
    # ZIP64 offsets can require a prepended-data correction; the record and
    # locator still determine the exact central region we walk.
    if (not zip64 and central_offset != central_start) or central_offset > central_start:
        raise ArchiveInspectionError("archive central directory offset is corrupt")
    return central_start, central_end, int(total_entries)


def _raw_central_entries(payload: bytes) -> list[dict[str, Any]]:
    start, central_end, expected_entries = _central_directory_bounds(payload)
    entries: list[dict[str, Any]] = []
    offset = start
    while offset < central_end:
        if payload[offset : offset + 4] != b"PK\x01\x02":
            raise ArchiveInspectionError("archive central directory is corrupt")
        if offset + 46 > len(payload):
            raise ArchiveInspectionError("archive central directory is corrupt")
        name_length, extra_length, comment_length = struct.unpack_from(
            "<HHH", payload, offset + 28
        )
        entry_end = offset + 46 + name_length + extra_length + comment_length
        if entry_end > central_end:
            raise ArchiveInspectionError("archive central directory is corrupt")
        extra_start = offset + 46 + name_length
        file_size, compressed_size, header_offset, disk_start = _resolve_zip64_values(
            payload[extra_start : extra_start + extra_length],
            label="central member",
            file_size=struct.unpack_from("<I", payload, offset + 24)[0],
            compressed_size=struct.unpack_from("<I", payload, offset + 20)[0],
            header_offset=struct.unpack_from("<I", payload, offset + 42)[0],
            disk_start=struct.unpack_from("<H", payload, offset + 34)[0],
        )
        if disk_start != 0:
            raise ArchiveInspectionError(
                "archive multi-disk central member is unsupported"
            )
        entries.append(
            {
                "name": payload[offset + 46 : offset + 46 + name_length],
                "flags": struct.unpack_from("<H", payload, offset + 8)[0],
                "compression": struct.unpack_from("<H", payload, offset + 10)[0],
                "crc32": struct.unpack_from("<I", payload, offset + 16)[0],
                "compressed_size": compressed_size,
                "file_size": file_size,
                "header_offset": header_offset,
                "disk_start": disk_start,
            }
        )
        offset = entry_end
    if offset != central_end or len(entries) != expected_entries:
        raise ArchiveInspectionError("archive central directory is corrupt")
    return entries


def _validate_local_header(
    payload: bytes, info: zipfile.ZipInfo, central: Mapping[str, object]
) -> None:
    offset = info.header_offset
    if offset < 0 or offset + 30 > len(payload) or payload[offset : offset + 4] != b"PK\x03\x04":
        raise ArchiveInspectionError("archive local member header is corrupt")
    flags, compression = struct.unpack_from("<HH", payload, offset + 6)
    crc32, compressed_size, file_size = struct.unpack_from("<III", payload, offset + 14)
    name_length, extra_length = struct.unpack_from("<HH", payload, offset + 26)
    end = offset + 30 + name_length + extra_length
    if end > len(payload):
        raise ArchiveInspectionError("archive local member header is corrupt")
    name = payload[offset + 30 : offset + 30 + name_length]
    extra = payload[offset + 30 + name_length : end]
    file_size, compressed_size, _, _ = _resolve_zip64_values(
        extra,
        label="local member",
        file_size=file_size,
        compressed_size=compressed_size,
    )
    if name != central["name"] or flags != central["flags"] or compression != central["compression"]:
        raise ArchiveInspectionError("archive local and central member headers drifted")
    if not flags & 0x08 and (
        crc32 != central["crc32"]
        or compressed_size != central["compressed_size"]
        or file_size != central["file_size"]
    ):
        raise ArchiveInspectionError("archive local and central member headers drifted")
    if info.header_offset != central["header_offset"]:
        raise ArchiveInspectionError("archive local and central member headers drifted")
