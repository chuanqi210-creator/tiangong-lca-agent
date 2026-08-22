"""Small, case-independent Project Record port for the D-18 runtime."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

import fcntl

from .content_identity import canonical_json_bytes, canonical_sha256
from .change_contracts import (
    ACCEPTANCE_CONTRACT,
    CANDIDATE_CONTRACT,
    MATERIAL_LOCATOR_AUTHORITY_BINDING,
    material_locator_authority_view as _material_locator_authority_view,
    validate_project_candidate,
)
from .accepted_state import apply_accepted_candidate


EVENT_CONTRACT = "lca_research_kernel.project_record_event/v1"
VIEW_CONTRACT = "lca_research_kernel.project_record_view/v1"
ARTIFACT_ACCEPTANCE_CONTRACT = (
    "lca_research_kernel.assurance_artifact_acceptance/v1"
)

MATERIAL_EVENT_TYPES = {
    "material.registered": "research.project.material.registered",
    "material.revision_accepted": "research.project.material.revision_accepted",
    "material.scope_updated": "research.project.material.scope_updated",
    "material.withdrawn": "research.project.material.withdrawn",
    "material.restored": "research.project.material.restored",
    "evidence.locator_accepted": "research.project.evidence.locator_accepted",
    "material.use_decision_recorded": (
        "research.project.material.use_decision_recorded"
    ),
}
MATERIAL_PACKAGE_EVENT_TYPE = "research.project.material.package_accepted"
MATERIAL_PACKAGE_CONTRACT_V1 = (
    "lca_research_kernel.project_material_package_accepted/v1"
)
MATERIAL_PACKAGE_CONTRACT = (
    "lca_research_kernel.project_material_package_accepted/v2"
)
MATERIAL_PACKAGE_INDEX_CONTRACT = (
    "lca_research_kernel.project_material_package_index/v1"
)
MATERIAL_PACKAGE_INDEX_DIRECTORY = "material-package-index"
MATERIAL_PACKAGE_INDEX_MAX_BYTES = 128 * 1024 * 1024
PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class MaterialExecutionSnapshot:
    """One replay-verified material decision and its current execution head."""

    project_id: str
    decision_id: str
    decision_event_sha256: str
    decision_sha256: str
    decision_sequence: int
    current_head_sha256: str
    project_view: Mapping[str, Any]
    decision: Mapping[str, Any]
    accepted_event: Mapping[str, Any]


def _require_string(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"Project Record {key} must be a non-empty string")
    return item


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


class ProjectRecord:
    """Append-only accepted truth with deterministic replay and thin ports."""

    EVENT_FILE = "research-project-events.jsonl"
    PROJECTION_FILE = "project-record.json"
    WRITE_LOCK_FILE = ".project-record-write.lock"

    def __init__(
        self,
        workspace: Path,
        *,
        verified_content_resolver: Callable[
            [str, str, Mapping[str, object]], bytes
        ]
        | None = None,
    ):
        self.workspace = Path(workspace)
        self.event_path = self.workspace / self.EVENT_FILE
        self.projection_path = self.workspace / self.PROJECTION_FILE
        self._verified_content_resolver = verified_content_resolver
        self._verified_view_signature: tuple[int, int, int, int, int] | None = None
        self._verified_view_cache: dict[str, Any] | None = None
        self._verified_package_dependencies: list[
            tuple[dict[str, str], str, int]
        ] = []
        self._verified_container_dependencies: list[
            tuple[str, str, dict[str, str]]
        ] = []
        self._replayed_package_dependencies: list[
            tuple[dict[str, str], str, int]
        ] = []
        self._replayed_container_dependencies: list[
            tuple[str, str, dict[str, str]]
        ] = []
        self._replayed_cacheable = True

    @contextmanager
    def _exclusive_write_lock(self):
        """Serialize every Project Record read/validate/commit transaction."""

        self.workspace.mkdir(parents=True, exist_ok=True)
        lock_path = self.workspace / self.WRITE_LOCK_FILE
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) != 0o600
            ):
                raise ValueError("Project Record write lock is unsafe")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            after = os.fstat(descriptor)
            if (
                (before.st_dev, before.st_ino)
                != (after.st_dev, after.st_ino)
                or not stat.S_ISREG(after.st_mode)
                or after.st_nlink != 1
                or after.st_uid != os.getuid()
                or stat.S_IMODE(after.st_mode) != 0o600
            ):
                raise ValueError("Project Record write lock drifted")
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    @staticmethod
    def _validate_material_package_index_directory(path: Path) -> None:
        try:
            observed = path.lstat()
        except OSError as error:
            raise ValueError(
                "Project Record material package index is unavailable"
            ) from error
        if (
            not stat.S_ISDIR(observed.st_mode)
            or observed.st_uid != os.getuid()
            or stat.S_IMODE(observed.st_mode) != 0o700
        ):
            raise ValueError("Project Record material package index is unsafe")

    def _material_package_index_dir_fd(self, *, create: bool) -> int:
        directory = self.workspace / MATERIAL_PACKAGE_INDEX_DIRECTORY
        if create:
            try:
                directory.mkdir(mode=0o700)
            except FileExistsError:
                pass
        self._validate_material_package_index_directory(directory)
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            return os.open(directory, flags)
        except OSError as error:
            raise ValueError(
                "Project Record material package index is unavailable"
            ) from error

    def _write_material_package_index(
        self, entries: list[dict[str, Any]]
    ) -> tuple[str, dict[str, str]]:
        package_id = canonical_sha256(entries)
        index = {
            "contract": MATERIAL_PACKAGE_INDEX_CONTRACT,
            "package_id": package_id,
            "entries": entries,
        }
        content = canonical_json_bytes(index) + b"\n"
        if len(content) > MATERIAL_PACKAGE_INDEX_MAX_BYTES:
            raise ValueError("Project Record material package index is too large")
        digest = hashlib.sha256(content).hexdigest()
        reference = {"artifact_id": digest, "sha256": digest}
        directory_fd = self._material_package_index_dir_fd(create=True)
        temporary_name = f".{digest}.{uuid.uuid4().hex}.tmp"
        descriptor: int | None = None
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(
                temporary_name,
                flags,
                0o600,
                dir_fd=directory_fd,
            )
            written = 0
            while written < len(content):
                count = os.write(descriptor, content[written:])
                if count <= 0:
                    raise OSError("material package index write made no progress")
                written += count
            os.fsync(descriptor)
            observed = os.fstat(descriptor)
            if (
                not stat.S_ISREG(observed.st_mode)
                or observed.st_nlink != 1
                or observed.st_uid != os.getuid()
                or stat.S_IMODE(observed.st_mode) != 0o600
                or observed.st_size != len(content)
            ):
                raise ValueError("Project Record material package index is unsafe")
            os.close(descriptor)
            descriptor = None
            try:
                os.link(
                    temporary_name,
                    f"{digest}.json",
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                pass
            os.unlink(temporary_name, dir_fd=directory_fd)
            os.fsync(directory_fd)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            os.close(directory_fd)
        self._read_material_package_index(
            reference,
            expected_package_id=package_id,
            expected_entry_count=len(entries),
        )
        return package_id, reference

    def _read_material_package_index(
        self,
        reference: object,
        *,
        expected_package_id: object,
        expected_entry_count: object,
    ) -> list[dict[str, Any]]:
        if (
            not isinstance(reference, Mapping)
            or set(reference) != {"artifact_id", "sha256"}
            or reference.get("artifact_id") != reference.get("sha256")
            or not isinstance(reference.get("sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", str(reference.get("sha256")))
            is None
            or not isinstance(expected_package_id, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_package_id) is None
            or not isinstance(expected_entry_count, int)
            or expected_entry_count < 1
        ):
            raise ValueError("Project Record material package index reference is invalid")
        digest = str(reference["sha256"])
        directory_fd = self._material_package_index_dir_fd(create=False)
        descriptor: int | None = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(
                f"{digest}.json",
                flags,
                dir_fd=directory_fd,
            )
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_size > MATERIAL_PACKAGE_INDEX_MAX_BYTES
            ):
                raise ValueError("Project Record material package index is unsafe")
            remaining = before.st_size + 1
            content = bytearray()
            observed_digest = hashlib.sha256()
            while remaining > 0:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                content.extend(chunk)
                observed_digest.update(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
            if (
                len(content) != before.st_size
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or after.st_nlink != 1
                or observed_digest.hexdigest() != digest
            ):
                raise ValueError("Project Record material package index drifted")
        except OSError as error:
            raise ValueError(
                "Project Record material package index is unavailable"
            ) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(directory_fd)
        try:
            index = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Project Record material package index is invalid") from error
        if (
            not isinstance(index, Mapping)
            or set(index) != {"contract", "package_id", "entries"}
            or index.get("contract") != MATERIAL_PACKAGE_INDEX_CONTRACT
            or index.get("package_id") != expected_package_id
            or canonical_json_bytes(index) + b"\n" != content
        ):
            raise ValueError("Project Record material package index is invalid")
        entries = index.get("entries")
        if not isinstance(entries, list) or len(entries) != expected_entry_count:
            raise ValueError("Project Record material package index is incomplete")
        if canonical_sha256(entries) != expected_package_id:
            raise ValueError("Project Record material package identity drifted")
        return deepcopy(entries)

    def _material_package_entries(
        self, payload: Mapping[str, object]
    ) -> list[dict[str, Any]]:
        contract = payload.get("contract")
        if contract == MATERIAL_PACKAGE_CONTRACT_V1:
            if set(payload) != {"contract", "package_id", "entries"}:
                raise ValueError("Project Record material package contract is invalid")
            entries = payload.get("entries")
            if not isinstance(entries, list) or not entries:
                raise ValueError("Project Record material package entries are invalid")
            if payload.get("package_id") != canonical_sha256(entries):
                raise ValueError("Project Record material package identity drifted")
            return deepcopy(entries)
        if contract == MATERIAL_PACKAGE_CONTRACT:
            if set(payload) != {
                "contract",
                "package_id",
                "entry_count",
                "entries_ref",
            }:
                raise ValueError("Project Record material package contract is invalid")
            return self._read_material_package_index(
                payload.get("entries_ref"),
                expected_package_id=payload.get("package_id"),
                expected_entry_count=payload.get("entry_count"),
            )
        raise ValueError("Project Record material package contract is invalid")

    @staticmethod
    def material_locator_authority_view(
        project_view: Mapping[str, object], revision_id: str
    ) -> dict[str, object]:
        return _material_locator_authority_view(project_view, revision_id)

    @classmethod
    def open_or_create_project(
        cls,
        workspace: Path,
        *,
        project_id: Optional[str] = None,
        title: Optional[str] = None,
        objective: Optional[str] = None,
        created_at: Optional[str] = None,
        verified_content_resolver: Callable[
            [str, str, Mapping[str, object]], bytes
        ]
        | None = None,
    ) -> "ProjectRecord":
        record = cls(
            workspace,
            verified_content_resolver=verified_content_resolver,
        )
        if record.event_path.is_file():
            with record._exclusive_write_lock():
                view = record._replay(record._read_events())
                expected = {
                    "project_id": project_id,
                    "title": title,
                    "objective": objective,
                }
                for key, value in expected.items():
                    if value is not None and view.get(key) != value:
                        raise ValueError(f"existing Project Record {key} differs")
                record._write_projection(view)
            return record
        if not all(
            isinstance(value, str) and value.strip()
            for value in (project_id, title, objective, created_at)
        ):
            raise ValueError(
                "new Project Record requires project_id, title, objective and created_at"
            )
        record.workspace.mkdir(parents=True, exist_ok=True)
        payload = {
            "contract": "lca_research_kernel.project_created/v1",
            "project_id": project_id,
            "title": title,
            "objective": objective,
            "created_at": created_at,
            "next_route": "intake-research-goal",
        }
        record._append_event(
            "research.project.created",
            payload,
            actor="product_interface",
            idempotency_key=f"project:{project_id}:created",
            occurred_at=created_at,
        )
        return record

    def read_current_project_view(self) -> dict[str, Any]:
        for _ in range(2):
            before = self._event_file_signature()
            if (
                before is not None
                and before == self._verified_view_signature
                and self._verified_view_cache is not None
            ):
                self._validate_cached_dependencies()
                if self._event_file_signature() == before:
                    return deepcopy(self._verified_view_cache)
                continue
            view = self._replay(self._read_events())
            after = self._event_file_signature()
            if before == after and after is not None:
                self._cache_verified_view(view, after)
                return deepcopy(view)
        raise ValueError("Project Record changed while its view was rebuilt")

    def _cache_verified_view(
        self,
        view: Mapping[str, object],
        signature: tuple[int, int, int, int, int],
    ) -> None:
        if not self._replayed_cacheable:
            self._verified_view_signature = None
            self._verified_view_cache = None
            self._verified_package_dependencies = []
            self._verified_container_dependencies = []
            return
        self._verified_view_signature = signature
        self._verified_view_cache = deepcopy(dict(view))
        self._verified_package_dependencies = deepcopy(
            self._replayed_package_dependencies
        )
        self._verified_container_dependencies = deepcopy(
            self._replayed_container_dependencies
        )

    def _validate_cached_dependencies(self) -> None:
        for reference, package_id, entry_count in self._verified_package_dependencies:
            self._read_material_package_index(
                reference,
                expected_package_id=package_id,
                expected_entry_count=entry_count,
            )
        for project_id, content_kind, reference in self._verified_container_dependencies:
            if self._verified_content_resolver is None:
                raise ValueError("verified content resolver is unavailable")
            try:
                payload = self._verified_content_resolver(
                    project_id,
                    content_kind,
                    reference,
                )
            except Exception as error:
                raise ValueError("verified content artifact is unavailable") from error
            if (
                not isinstance(payload, bytes)
                or hashlib.sha256(payload).hexdigest() != reference["sha256"]
            ):
                raise ValueError("verified content container drifted")

    def _event_file_signature(self) -> tuple[int, int, int, int, int] | None:
        try:
            observed = self.event_path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(observed.st_mode):
            raise ValueError("Project Record event stream is not a regular file")
        return (
            observed.st_dev,
            observed.st_ino,
            observed.st_size,
            observed.st_mtime_ns,
            observed.st_ctime_ns,
        )

    def resolve_current_material_model_inputs(self) -> dict | None:
        """Resolve accepted current evidence through the trusted content seam."""

        from .material_sources import material_revision_context_eligibility

        view = self.read_current_project_view()
        materials = view.get("materials") or {}
        revisions = view.get("material_revisions") or {}
        locators = view.get("evidence_locators") or {}
        if not all(
            isinstance(value, Mapping)
            for value in (materials, revisions, locators)
        ):
            raise ValueError("Project Record material state is invalid")
        allowed_revision_ids = sorted(
            revision_id
            for revision_id, revision in revisions.items()
            if isinstance(revision, Mapping)
            and isinstance(materials.get(revision.get("material_id")), Mapping)
            and materials[revision["material_id"]].get("current_revision_id")
            == revision_id
            and materials[revision["material_id"]].get("deleted_at") is None
            and material_revision_context_eligibility(view, revision_id)[0]
            and revision.get("processing_status")
            in {"ready", "partial", "needs_user"}
        )
        if not allowed_revision_ids:
            return None
        allowed = set(allowed_revision_ids)
        verified_content_cache: dict[tuple[str, ...], object] = {}
        events = self._read_events()
        locator_events: dict[str, list[dict[str, Any]]] = {}
        for event in self._material_change_receipts_from_events(events):
            if event.get("event_type") != MATERIAL_EVENT_TYPES[
                "evidence.locator_accepted"
            ]:
                continue
            locator_id = (
                event.get("payload", {})
                .get("candidate", {})
                .get("evidence_locator", {})
                .get("locator_id")
            )
            if isinstance(locator_id, str):
                locator_events.setdefault(locator_id, []).append(event)
        bindings = []
        for locator_id, locator in sorted(locators.items()):
            if (
                not isinstance(locator, Mapping)
                or locator.get("revision_id") not in allowed
                or locator.get("scientific_status") != "accepted_evidence"
            ):
                continue
            matches = locator_events.get(locator_id, [])
            if len(matches) != 1:
                raise ValueError("accepted material evidence event is ambiguous")
            candidate = matches[0]["payload"]["candidate"]
            revision = revisions[locator["revision_id"]]
            evidence_unit = self._resolve_verified_content(
                str(view["project_id"]),
                "evidence_unit",
                candidate["evidence_units_ref"],
                expected_content_sha256=str(locator["content_hash"]),
                expected_revision_id=str(locator["revision_id"]),
                expected_input_sha256=str(
                    revision["artifact_ref"]["sha256"]
                ),
                verified_content_cache=verified_content_cache,
            )
            binding = candidate.get("evidence_binding")
            if not isinstance(binding, Mapping):
                raise ValueError("accepted material evidence binding is absent")
            bindings.append(
                {**deepcopy(dict(binding)), "evidence_unit": evidence_unit}
            )
        if not bindings:
            raise ValueError("accepted material evidence is unavailable")
        return {
            "material_evidence_units": bindings,
            "allowed_revision_ids": allowed_revision_ids,
        }

    def _resolve_verified_content(
        self,
        project_id: str,
        content_kind: str,
        reference: Mapping[str, object],
        *,
        expected_content_sha256: str,
        expected_revision_id: str | None = None,
        expected_input_sha256: str | None = None,
        verified_content_cache: dict[tuple[str, ...], object] | None = None,
    ) -> object:
        """Resolve an existing Task-3 artifact without creating a second truth."""

        if (
            not isinstance(project_id, str)
            or PROJECT_ID_RE.fullmatch(project_id) is None
            or content_kind not in {"material_processing_receipt", "evidence_unit"}
            or not isinstance(reference, Mapping)
            or set(reference) != {"artifact_id", "sha256"}
            or reference.get("artifact_id") != reference.get("sha256")
            or not isinstance(reference.get("sha256"), str)
            or len(reference["sha256"]) != 64
            or any(
                character not in "0123456789abcdef"
                for character in reference["sha256"]
            )
        ):
            raise ValueError("verified content reference is invalid")
        container_key = (
            "verified-container",
            project_id,
            content_kind,
            str(reference["sha256"]),
        )
        value = (
            verified_content_cache.get(container_key)
            if verified_content_cache is not None
            else None
        )
        if value is None:
            if self._verified_content_resolver is None:
                raise ValueError("verified content resolver is unavailable")
            try:
                payload = self._verified_content_resolver(
                    project_id,
                    content_kind,
                    reference,
                )
            except Exception as error:
                raise ValueError("verified content artifact is unavailable") from error
            if not isinstance(payload, bytes):
                raise ValueError("verified content resolver must return bytes")
            if hashlib.sha256(payload).hexdigest() != reference["sha256"]:
                raise ValueError("verified content container drifted")
            try:
                value = json.loads(payload)
            except json.JSONDecodeError as error:
                raise ValueError("verified content artifact is invalid") from error
            if not isinstance(value, Mapping):
                raise ValueError("verified content artifact must contain an object")
            if payload != canonical_json_bytes(value) + b"\n":
                raise ValueError("verified content artifact is not canonical")
            if verified_content_cache is not None:
                verified_content_cache[container_key] = value
        if content_kind == "material_processing_receipt" and hashlib.sha256(
            canonical_json_bytes(value) + b"\n"
        ).hexdigest() == expected_content_sha256:
            return deepcopy(dict(value))
        units = value.get("units")
        if (
            content_kind == "evidence_unit"
            and value.get("contract") == "lca_research_service.evidence_units/v1"
            and value.get("revision_id") == expected_revision_id
            and value.get("input_sha256") == expected_input_sha256
            and value.get("candidate_only") is True
            and isinstance(units, list)
        ):
            index_key = (
                "verified-unit-index",
                project_id,
                str(reference["sha256"]),
            )
            unit_index = (
                verified_content_cache.get(index_key)
                if verified_content_cache is not None
                else None
            )
            if unit_index is None:
                built_index: dict[str, list[Mapping[str, object]]] = {}
                for unit in units:
                    if isinstance(unit, Mapping):
                        unit_sha256 = hashlib.sha256(
                            canonical_json_bytes(unit) + b"\n"
                        ).hexdigest()
                        built_index.setdefault(unit_sha256, []).append(unit)
                unit_index = built_index
                if verified_content_cache is not None:
                    verified_content_cache[index_key] = unit_index
            matches = unit_index.get(expected_content_sha256, [])
            if len(matches) == 1:
                return deepcopy(dict(matches[0]))
        raise ValueError("verified content artifact drifted")

    def _resolve_legacy_v2_mirror(
        self,
        reference: Mapping[str, object],
        *,
        expected_content_sha256: str,
    ) -> object:
        """Read a frozen v2 mirror without ever creating or modifying it."""

        if (
            not isinstance(reference, Mapping)
            or set(reference) != {"path", "sha256"}
            or reference.get("path")
            != f"verified-content/{reference.get('sha256')}.json"
            or reference.get("sha256") != expected_content_sha256
        ):
            raise ValueError("legacy verified content reference is invalid")
        workspace_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        workspace_flags |= getattr(os, "O_CLOEXEC", 0) | getattr(
            os, "O_NOFOLLOW", 0
        )
        try:
            workspace_fd = os.open(self.workspace, workspace_flags)
            try:
                directory_fd = os.open(
                    "verified-content", workspace_flags, dir_fd=workspace_fd
                )
            finally:
                os.close(workspace_fd)
            try:
                file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                file_flags |= getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(
                    f"{reference['sha256']}.json",
                    file_flags,
                    dir_fd=directory_fd,
                )
                try:
                    status = os.fstat(descriptor)
                    if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
                        raise ValueError("legacy verified content mirror is unsafe")
                    chunks = []
                    while chunk := os.read(descriptor, 65536):
                        chunks.append(chunk)
                finally:
                    os.close(descriptor)
            finally:
                os.close(directory_fd)
        except OSError as error:
            raise ValueError("legacy verified content mirror is unavailable") from error
        payload = b"".join(chunks)
        if hashlib.sha256(payload).hexdigest() != expected_content_sha256:
            raise ValueError("legacy verified content mirror drifted")
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ValueError("legacy verified content mirror is invalid") from error
        if payload != canonical_json_bytes(value) + b"\n":
            raise ValueError("legacy verified content mirror is not canonical")
        return value

    def record_agent_startup(
        self,
        startup_context: Mapping[str, object],
        *,
        run_id: str,
        observed_at: str,
    ) -> dict[str, Any]:
        from .agent_bootstrap import validate_startup_context

        context = validate_startup_context(startup_context)
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("Agent startup run_id is required")
        if not isinstance(observed_at, str) or not observed_at.strip():
            raise ValueError("Agent startup observed_at is required")
        payload = {
            "contract": "lca_research_kernel.agent_startup_receipt/v1",
            "run_id": run_id.strip(),
            "observed_at": observed_at.strip(),
            "startup_context": context,
            "scientific_result_created": False,
            "executed_external_effects": 0,
        }
        return self._append_event(
            "research.agent.started",
            payload,
            actor="product_interface",
            idempotency_key=(
                f"agent-start:{run_id.strip()}:{context['context_sha256'][:16]}"
            ),
            occurred_at=observed_at.strip(),
        )

    def record_capability_event(
        self,
        event_type: str,
        payload: Mapping[str, object],
        *,
        idempotency_key: str,
        occurred_at: str,
    ) -> dict[str, Any]:
        allowed = {
            "research.capability.requested",
            "research.capability.completed",
            "research.capability.failed",
            "research.capability.blocked",
        }
        if event_type not in allowed:
            raise ValueError("Project Record capability event type is unsupported")
        if (
            not isinstance(payload, Mapping)
            or payload.get("contract")
            != "lca_research_kernel.capability_run_event/v1"
        ):
            raise ValueError("Project Record capability event contract is unsupported")
        return self._append_event(
            event_type,
            payload,
            actor="assurance_boundary",
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
        )

    def adopt_capability_catalog(
        self,
        summary: Mapping[str, object],
        *,
        acceptance: Mapping[str, object],
        idempotency_key: str,
    ) -> dict[str, Any]:
        if (
            not isinstance(summary, Mapping)
            or summary.get("contract")
            != "lca_research_kernel.capability_catalog_summary/v1"
        ):
            raise ValueError("Project Record capability catalog summary is unsupported")
        if (
            not isinstance(acceptance, Mapping)
            or acceptance.get("contract")
            != "lca_research_kernel.capability_catalog_acceptance/v1"
            or acceptance.get("decision") != "accepted"
            or acceptance.get("evaluator") != "assurance_boundary"
            or acceptance.get("summary_sha256") != canonical_sha256(summary)
        ):
            raise ValueError("Project Record requires a catalog assurance acceptance")
        evaluated_at = _require_string(acceptance, "evaluated_at")
        payload = {
            "contract": "lca_research_kernel.capability_catalog_adopted/v1",
            "summary": deepcopy(dict(summary)),
            "assurance_acceptance": deepcopy(dict(acceptance)),
        }
        return self._append_event(
            "research.capability.catalog_adopted",
            payload,
            actor="assurance_boundary",
            idempotency_key=idempotency_key,
            occurred_at=evaluated_at,
        )

    def append_accepted_change(
        self,
        candidate: Mapping[str, object],
        acceptance: Optional[Mapping[str, object]],
        *,
        idempotency_key: str,
        expected_last_event_sha256: str | None = None,
    ) -> dict[str, Any]:
        content_bound_change = candidate.get("change_type") in {
            "material.revision_accepted",
            "evidence.locator_accepted",
        }
        normalized_candidate = self._validate_candidate(
            candidate,
            require_material_bindings=True,
            require_evidence_units_ref=content_bound_change,
        )
        project_view = self.read_current_project_view()
        normalized_acceptance = self._validate_acceptance(
            acceptance,
            normalized_candidate,
            project_view=project_view,
            require_project_binding=(
                normalized_candidate["change_type"] in MATERIAL_EVENT_TYPES
            ),
            require_content_binding=(
                normalized_candidate["change_type"]
                in {"material.revision_accepted", "evidence.locator_accepted"}
            ),
        )
        payload = {
            "contract": "lca_research_kernel.project_change_accepted/v1",
            "candidate": normalized_candidate,
            "assurance_acceptance": normalized_acceptance,
        }
        event_type = MATERIAL_EVENT_TYPES.get(
            normalized_candidate["change_type"],
            "research.project.accepted_change",
        )
        if expected_last_event_sha256 is None:
            return self._append_event(
                event_type,
                payload,
                actor="assurance_boundary",
                idempotency_key=idempotency_key,
                occurred_at=normalized_acceptance["evaluated_at"],
            )
        if not isinstance(expected_last_event_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", expected_last_event_sha256
        ):
            raise ValueError("expected Project Record head is invalid")
        with self._exclusive_write_lock():
            events = self._read_events()
            for event in events:
                if event.get("idempotency_key") != idempotency_key:
                    continue
                if (
                    event.get("event_type") == event_type
                    and event.get("payload") == payload
                    and event.get("previous_event_sha256")
                    == expected_last_event_sha256
                ):
                    return self._append_event_locked(
                        event_type,
                        payload,
                        actor="assurance_boundary",
                        idempotency_key=idempotency_key,
                        occurred_at=normalized_acceptance["evaluated_at"],
                    )
                raise ValueError(
                    f"Idempotency key '{idempotency_key}' was reused with different content"
                )
            current_head = events[-1]["event_sha256"] if events else None
            if current_head != expected_last_event_sha256:
                raise ValueError("Project Record head changed before acceptance")
            return self._append_event_locked(
                event_type,
                payload,
                actor="assurance_boundary",
                idempotency_key=idempotency_key,
                occurred_at=normalized_acceptance["evaluated_at"],
            )

    def append_accepted_material_changes(
        self,
        entries: list[Mapping[str, object]],
        *,
        expected_last_event_sha256: str,
    ) -> list[dict[str, Any]]:
        """Atomically append one ordered, already assured material change set.

        Individual events remain independently replayable.  The current view,
        verified artifact containers, and final projection are each rebuilt at
        most once for the whole set so material size cannot multiply replay
        work by locator count.
        """

        with self._exclusive_write_lock():
            return self._append_accepted_material_changes_locked(
                entries,
                expected_last_event_sha256=expected_last_event_sha256,
            )

    def _material_change_receipts_from_events(
        self,
        events: list[Mapping[str, object]],
    ) -> list[dict[str, Any]]:
        receipts: list[dict[str, Any]] = []
        identity_event_types = {
            MATERIAL_EVENT_TYPES["material.registered"],
            MATERIAL_EVENT_TYPES["material.revision_accepted"],
            MATERIAL_EVENT_TYPES["evidence.locator_accepted"],
        }
        for event in events:
            if event.get("event_type") in identity_event_types:
                receipts.append(deepcopy(dict(event)))
                continue
            if event.get("event_type") != MATERIAL_PACKAGE_EVENT_TYPE:
                continue
            payload = event.get("payload")
            if not isinstance(payload, Mapping):
                raise ValueError("Project Record material package payload is invalid")
            package_entries = self._material_package_entries(payload)
            for index, entry in enumerate(package_entries):
                if not isinstance(entry, Mapping):
                    raise ValueError(
                        "Project Record material package entry is invalid"
                    )
                entry_payload = entry.get("payload")
                acceptance = (
                    entry_payload.get("assurance_acceptance")
                    if isinstance(entry_payload, Mapping)
                    else None
                )
                occurred_at = (
                    acceptance.get("evaluated_at")
                    if isinstance(acceptance, Mapping)
                    else event.get("occurred_at")
                )
                receipts.append(
                    {
                        "contract": EVENT_CONTRACT,
                        "sequence": event.get("sequence"),
                        "event_id": f"{event.get('event_id')}:{index}",
                        "event_type": entry.get("event_type"),
                        "occurred_at": occurred_at,
                        "actor": event.get("actor"),
                        "idempotency_key": entry.get("idempotency_key"),
                        "payload": deepcopy(entry.get("payload")),
                        "previous_event_sha256": event.get(
                            "previous_event_sha256"
                        ),
                        "event_sha256": event.get("event_sha256"),
                        "package_entry_index": index,
                    }
                )
        return receipts

    def read_accepted_material_change_receipts(self) -> list[dict[str, Any]]:
        """Read logical material acceptances from old or package events."""

        events = self._read_events()
        self._replay(events)
        return self._material_change_receipts_from_events(events)

    def _append_accepted_material_changes_locked(
        self,
        entries: list[Mapping[str, object]],
        *,
        expected_last_event_sha256: str,
    ) -> list[dict[str, Any]]:

        if not isinstance(entries, list) or not entries:
            raise ValueError("accepted material change batch is empty")
        if not isinstance(expected_last_event_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", expected_last_event_sha256
        ):
            raise ValueError("expected Project Record head is invalid")

        verified_content_cache: dict[tuple[str, ...], object] = {}
        events = self._read_events()
        view = self._replay(
            events,
            verified_content_cache=verified_content_cache,
        )
        if view.get("last_event_sha256") != expected_last_event_sha256:
            raise ValueError("Project Record head changed before material acceptance")
        existing_by_key = {
            event["idempotency_key"]: event
            for event in self._material_change_receipts_from_events(events)
        }
        accepted_events: list[dict[str, Any]] = []
        package_entries: list[dict[str, Any]] = []
        appended = False

        for entry in entries:
            if not isinstance(entry, Mapping) or set(entry) != {
                "candidate",
                "idempotency_key",
                "verified_content",
                "acceptance",
            }:
                raise ValueError("accepted material change batch entry is invalid")
            idempotency_key = entry.get("idempotency_key")
            if not isinstance(idempotency_key, str) or not idempotency_key.strip():
                raise ValueError("Project Record idempotency key is required")
            normalized_candidate = self._validate_candidate(
                entry.get("candidate"),  # type: ignore[arg-type]
                require_material_bindings=True,
                require_evidence_units_ref=True,
            )
            change_type = normalized_candidate["change_type"]
            if change_type not in {
                "material.registered",
                "material.revision_accepted",
                "evidence.locator_accepted",
            }:
                raise ValueError("accepted material change batch type is unsupported")

            existing = existing_by_key.get(idempotency_key)
            expected_event_type = MATERIAL_EVENT_TYPES[change_type]
            if existing is not None:
                if (
                    existing.get("event_type") != expected_event_type
                    or existing.get("payload", {}).get("candidate")
                    != normalized_candidate
                ):
                    raise ValueError(
                        f"Idempotency key '{idempotency_key}' was reused with different content"
                    )
                accepted_events.append(deepcopy(existing))
                continue

            supplied_verified_content = entry.get("verified_content")
            evaluation_view: Mapping[str, object] = view
            if change_type == "evidence.locator_accepted":
                evaluation_view = self.material_locator_authority_view(
                    view,
                    str(normalized_candidate["evidence_locator"]["revision_id"]),
                )
            acceptance = entry.get("acceptance")
            if not isinstance(acceptance, Mapping) or acceptance.get(
                "decision"
            ) != "accepted":
                errors = (
                    acceptance.get("errors")
                    if isinstance(acceptance, Mapping)
                    else ["assurance_acceptance_absent"]
                )
                raise ValueError(
                    "material candidate failed assurance: "
                    + ",".join(str(error) for error in errors)
                )

            content_bound_change = change_type in {
                "material.revision_accepted",
                "evidence.locator_accepted",
            }
            normalized_acceptance = self._validate_acceptance(
                acceptance,
                normalized_candidate,
                project_view=view,
                require_project_binding=True,
                require_content_binding=content_bound_change,
                verified_content_cache=verified_content_cache,
            )
            payload = {
                "contract": "lca_research_kernel.project_change_accepted/v1",
                "candidate": normalized_candidate,
                "assurance_acceptance": normalized_acceptance,
            }
            package_entries.append(
                {
                    "event_type": expected_event_type,
                    "idempotency_key": idempotency_key,
                    "payload": payload,
                }
            )
            logical_event = {
                "contract": EVENT_CONTRACT,
                "sequence": None,
                "event_id": None,
                "event_type": expected_event_type,
                "occurred_at": normalized_acceptance["evaluated_at"],
                "actor": "assurance_boundary",
                "idempotency_key": idempotency_key,
                "payload": payload,
                "previous_event_sha256": None,
                "event_sha256": None,
            }
            existing_by_key[idempotency_key] = logical_event
            apply_accepted_candidate(view, normalized_candidate)
            accepted_events.append(deepcopy(logical_event))
            appended = True

        if appended:
            package_id, entries_ref = self._write_material_package_index(
                package_entries
            )
            previous = events[-1]["event_sha256"] if events else None
            sequence = len(events) + 1
            package_payload = {
                "contract": MATERIAL_PACKAGE_CONTRACT,
                "package_id": package_id,
                "entry_count": len(package_entries),
                "entries_ref": entries_ref,
            }
            package_idempotency_key = "material-package:" + package_id
            identity_basis = {
                "sequence": sequence,
                "event_type": MATERIAL_PACKAGE_EVENT_TYPE,
                "actor": "assurance_boundary",
                "idempotency_key": package_idempotency_key,
                "payload": package_payload,
                "previous_event_sha256": previous,
            }
            package_event = {
                "contract": EVENT_CONTRACT,
                "sequence": sequence,
                "event_id": str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        canonical_sha256(identity_basis),
                    )
                ),
                "event_type": MATERIAL_PACKAGE_EVENT_TYPE,
                "occurred_at": package_entries[-1]["payload"][
                    "assurance_acceptance"
                ]["evaluated_at"],
                "actor": "assurance_boundary",
                "idempotency_key": package_idempotency_key,
                "payload": package_payload,
                "previous_event_sha256": previous,
            }
            package_event["event_sha256"] = canonical_sha256(package_event)
            events.append(package_event)
            final_view = self._replay(
                events,
                verified_content_cache=verified_content_cache,
            )
            content = b"".join(
                canonical_json_bytes(row) + b"\n" for row in events
            )
            _atomic_write(self.event_path, content)
            self._write_projection(final_view)
            accepted_events = [
                *[
                    event
                    for event in accepted_events
                    if event.get("event_sha256") is not None
                ],
                *self._material_change_receipts_from_events([package_event]),
            ]
        else:
            # A prior attempt may have committed the event log before a
            # projection write failed.  Exact idempotent retry repairs that
            # derived projection without appending another event.
            self._write_projection(view)
        return accepted_events

    def register_artifact(
        self,
        relative_path: str,
        *,
        role: str,
        acceptance: Mapping[str, object],
        idempotency_key: str,
        recorded_at: str,
        derived_from: Optional[Mapping[str, object]] = None,
    ) -> dict[str, Any]:
        path = self._safe_artifact_path(relative_path)
        if not path.is_file():
            raise ValueError(f"Project Record artifact is absent: {relative_path}")
        payload_bytes = path.read_bytes()
        digest = hashlib.sha256(payload_bytes).hexdigest()
        normalized = self._validate_artifact_acceptance(acceptance, digest)
        payload = {
            "contract": "lca_research_kernel.project_artifact_registered/v1",
            "path": Path(relative_path).as_posix(),
            "role": role,
            "sha256": digest,
            "bytes": len(payload_bytes),
            "recorded_at": recorded_at,
            "assurance_acceptance": normalized,
        }
        if derived_from is not None:
            payload["derived_from"] = self._normalize_artifact_derivation(
                derived_from, self.read_current_project_view()
            )
        return self._append_event(
            "research.project.artifact_registered",
            payload,
            actor="assurance_boundary",
            idempotency_key=idempotency_key,
            occurred_at=recorded_at,
        )

    @staticmethod
    def _normalize_artifact_derivation(
        value: Mapping[str, object], project_view: Mapping[str, object]
    ) -> dict[str, list[str]]:
        if not isinstance(value, Mapping) or set(value) != {
            "material_revision_ids",
            "evidence_locator_ids",
        }:
            raise ValueError("Project Record artifact derivation contract is invalid")
        normalized: dict[str, list[str]] = {}
        for key in ("material_revision_ids", "evidence_locator_ids"):
            rows = value.get(key)
            if not isinstance(rows, list) or any(
                not isinstance(row, str) or not row for row in rows
            ):
                raise ValueError(f"Project Record artifact derivation {key} is invalid")
            normalized[key] = sorted(set(rows))
        if not any(normalized.values()):
            raise ValueError("Project Record artifact derivation is empty")
        revisions = project_view.get("material_revisions") or {}
        locators = project_view.get("evidence_locators") or {}
        if not isinstance(revisions, Mapping) or not isinstance(locators, Mapping):
            raise ValueError("Project Record material derivation state is invalid")
        for revision_id in normalized["material_revision_ids"]:
            if revision_id not in revisions:
                raise ValueError(
                    "Project Record artifact derivation material revision is absent"
                )
        for locator_id in normalized["evidence_locator_ids"]:
            locator = locators.get(locator_id)
            if not isinstance(locator, Mapping):
                raise ValueError(
                    "Project Record artifact derivation evidence locator is absent"
                )
            if locator.get("revision_id") not in revisions:
                raise ValueError(
                    "Project Record artifact derivation locator revision is absent"
                )
        return normalized

    def rebuild_and_verify(self) -> dict[str, Any]:
        with self._exclusive_write_lock():
            events = self._read_events()
            view = self._replay(events)
            for artifact in view["artifacts"].values():
                path = self._safe_artifact_path(artifact["path"])
                if (
                    not path.is_file()
                    or path.stat().st_size != artifact["bytes"]
                    or hashlib.sha256(path.read_bytes()).hexdigest() != artifact["sha256"]
                ):
                    raise ValueError(
                        f"registered artifact drifted: {artifact['path']}"
                    )
            self._write_projection(view)
        return {
            "status": "passed",
            "event_count": len(events),
            "last_event_sha256": events[-1]["event_sha256"],
            "projection_sha256": canonical_sha256(view),
        }

    def read_audit_trail(self) -> list[dict[str, Any]]:
        return deepcopy(self._read_events())

    def resolve_accepted_result(self, result_sha256: str) -> dict[str, Any]:
        """Resolve one historical result only from this replay-verified record."""

        if (
            not isinstance(result_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", result_sha256) is None
        ):
            raise ValueError("accepted result SHA-256 is invalid")
        events = self._read_events()
        self._replay(events)
        matches = []
        for event in events:
            if (
                event.get("event_type") != "research.project.accepted_change"
                or event.get("actor") != "assurance_boundary"
            ):
                continue
            payload = event.get("payload")
            candidate = (
                payload.get("candidate") if isinstance(payload, Mapping) else None
            )
            if (
                not isinstance(candidate, Mapping)
                or candidate.get("change_type") != "result.accepted"
                or not isinstance(candidate.get("result"), Mapping)
            ):
                continue
            result = candidate["result"]
            if canonical_sha256(result) == result_sha256:
                matches.append(deepcopy(dict(result)))
        if len(matches) != 1:
            raise ValueError("accepted result is unavailable or ambiguous")
        return matches[0]

    def resolve_accepted_material_use_decision_event(
        self,
        event_sha256: str,
        decision_id: str,
    ) -> dict[str, Any]:
        """Resolve one accepted decision only from this replay-verified record."""

        if (
            not isinstance(event_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", event_sha256) is None
            or not isinstance(decision_id, str)
            or not decision_id.strip()
        ):
            raise ValueError("accepted material use decision event identity is invalid")
        events = self._read_events()
        view = self._replay(events)
        matches = [
            event
            for event in events
            if event.get("event_sha256") == event_sha256
            and event.get("event_type")
            == MATERIAL_EVENT_TYPES["material.use_decision_recorded"]
            and event.get("payload", {}).get("candidate", {}).get("decision_id")
            == decision_id
        ]
        if len(matches) != 1:
            raise ValueError(
                "accepted material use decision event is not in this Project Record"
            )
        decision = matches[0]["payload"]["candidate"].get(
            "material_use_decision"
        )
        task_ref = decision.get("task_ref") if isinstance(decision, Mapping) else None
        if (
            not isinstance(decision, Mapping)
            or decision.get("task_id") != decision_id
            or (
                decision.get("contract")
                == "lca_research_kernel.material_use_decision/v2"
                and (
                    not isinstance(task_ref, Mapping)
                    or task_ref.get("project_id") != view.get("project_id")
                )
            )
        ):
            raise ValueError(
                "accepted material use decision event belongs to another Project Record"
            )
        return deepcopy(matches[0])

    def resolve_material_execution(
        self,
        *,
        decision_id: str,
        decision_event_sha256: str,
        expected_decision_sha256: str | None = None,
    ) -> MaterialExecutionSnapshot:
        """Resolve the only current material-execution authority in one replay.

        This is the internal boundary for consumers that previously combined a
        projected view with a separate audit scan.  Any accepted state change
        after the decision invalidates execution; startup receipts are the only
        coordination-only exception.
        """

        if (
            not isinstance(decision_id, str)
            or not decision_id.strip()
            or not isinstance(decision_event_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", decision_event_sha256) is None
            or (
                expected_decision_sha256 is not None
                and (
                    not isinstance(expected_decision_sha256, str)
                    or re.fullmatch(r"[0-9a-f]{64}", expected_decision_sha256)
                    is None
                )
            )
        ):
            raise ValueError("material execution identity is invalid")
        for _ in range(2):
            before = self._event_file_signature()
            events = self._read_events()
            view = self._replay(events)
            after = self._event_file_signature()
            if before != after or after is None:
                continue
            matches = [
                event
                for event in events
                if event.get("event_sha256") == decision_event_sha256
                and event.get("event_type")
                == MATERIAL_EVENT_TYPES["material.use_decision_recorded"]
                and event.get("payload", {}).get("candidate", {}).get(
                    "decision_id"
                )
                == decision_id
            ]
            accepted_decisions = view.get("material_use_decisions")
            decision = (
                matches[0].get("payload", {}).get("candidate", {}).get(
                    "material_use_decision"
                )
                if len(matches) == 1
                else None
            )
            accepted_decision = (
                accepted_decisions.get(decision_id)
                if isinstance(accepted_decisions, Mapping)
                else None
            )
            decision_sequence = (
                matches[0].get("sequence") if len(matches) == 1 else None
            )
            later_state_events = (
                [
                    event
                    for event in events
                    if event.get("sequence", 0) > decision_sequence
                    and event.get("event_type") != "research.agent.started"
                ]
                if isinstance(decision_sequence, int)
                else []
            )
            observed_decision_sha256 = (
                canonical_sha256(decision)
                if isinstance(decision, Mapping)
                else None
            )
            if (
                not isinstance(decision, Mapping)
                or accepted_decision != decision
                or later_state_events
                or (
                    expected_decision_sha256 is not None
                    and observed_decision_sha256 != expected_decision_sha256
                )
            ):
                raise ValueError(
                    "material use decision is not accepted before consumption"
                )
            current_head = view.get("last_event_sha256")
            project_id = view.get("project_id")
            if (
                not isinstance(current_head, str)
                or re.fullmatch(r"[0-9a-f]{64}", current_head) is None
                or not isinstance(project_id, str)
                or not project_id
            ):
                raise ValueError("material execution Project Record head is invalid")
            self._cache_verified_view(view, after)
            return MaterialExecutionSnapshot(
                project_id=project_id,
                decision_id=decision_id,
                decision_event_sha256=decision_event_sha256,
                decision_sha256=str(observed_decision_sha256),
                decision_sequence=decision_sequence,
                current_head_sha256=current_head,
                project_view=deepcopy(view),
                decision=deepcopy(dict(decision)),
                accepted_event=deepcopy(matches[0]),
            )
        raise ValueError("Project Record changed while material execution was resolved")

    def assert_material_execution_current(
        self, snapshot: MaterialExecutionSnapshot
    ) -> MaterialExecutionSnapshot:
        """Revalidate a material execution immediately before a side edge."""

        if not isinstance(snapshot, MaterialExecutionSnapshot):
            raise ValueError("material execution snapshot is invalid")
        current = self.resolve_material_execution(
            decision_id=snapshot.decision_id,
            decision_event_sha256=snapshot.decision_event_sha256,
            expected_decision_sha256=snapshot.decision_sha256,
        )
        if current.project_id != snapshot.project_id:
            raise ValueError(
                "material use decision is not accepted before consumption"
            )
        return current

    def validate_material_use_decision_checkpoint(
        self,
        checkpoint: Mapping[str, object],
        *,
        decision: Mapping[str, object],
        project_view: Mapping[str, object],
        locator_index: Mapping[str, Mapping[str, object]],
    ) -> dict[str, Any]:
        """Validate checkpoint state against one replay-verified audit event."""

        from .material_sources import (
            _validate_material_use_decision_checkpoint_proof,
        )

        event_sha256 = checkpoint.get("accepted_decision_event_sha256")
        decision_id = decision.get("task_id")
        if not isinstance(event_sha256, str) or not isinstance(decision_id, str):
            raise ValueError("material use decision checkpoint identity is invalid")
        accepted_event = self.resolve_accepted_material_use_decision_event(
            event_sha256,
            decision_id,
        )
        return _validate_material_use_decision_checkpoint_proof(
            checkpoint,
            decision=decision,
            project_view=project_view,
            locator_index=locator_index,
            accepted_decision_event=accepted_event,
        )

    def _validate_candidate(
        self,
        value: Mapping[str, object],
        *,
        require_material_bindings: bool = False,
        require_evidence_units_ref: bool = False,
        allow_legacy_active_turn_fact: bool = False,
    ) -> dict[str, Any]:
        return validate_project_candidate(
            value,
            require_material_bindings=require_material_bindings,
            require_evidence_units_ref=require_evidence_units_ref,
            allow_legacy_active_turn_fact=allow_legacy_active_turn_fact,
        )

    def _validate_acceptance(
        self,
        value: Optional[Mapping[str, object]],
        candidate: Mapping[str, object],
        *,
        project_view: Mapping[str, object] | None = None,
        require_project_binding: bool = False,
        require_content_binding: bool = False,
        verified_content_cache: dict[tuple[str, ...], object] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping) or value.get("contract") != ACCEPTANCE_CONTRACT:
            raise ValueError("Project Record requires an assurance acceptance")
        normalized = deepcopy(dict(value))
        if normalized.get("decision") != "accepted" or normalized.get(
            "evaluator"
        ) != "assurance_boundary":
            raise ValueError("Project Record requires an assurance acceptance")
        if normalized.get("candidate_sha256") != canonical_sha256(candidate):
            raise ValueError("assurance candidate hash drifted")
        observed_project_sha256 = normalized.get("project_view_sha256")
        if require_project_binding and not isinstance(
            observed_project_sha256, str
        ):
            raise ValueError("assurance Project Record binding is absent")
        project_binding = normalized.get("project_view_binding")
        if project_binding is not None and project_binding != (
            MATERIAL_LOCATOR_AUTHORITY_BINDING
        ):
            raise ValueError("assurance Project Record binding is unsupported")
        project_binding_view = project_view
        if project_binding == MATERIAL_LOCATOR_AUTHORITY_BINDING:
            if candidate.get("change_type") != "evidence.locator_accepted":
                raise ValueError("assurance locator authority binding is misplaced")
            locator = candidate.get("evidence_locator")
            if not isinstance(locator, Mapping):
                raise ValueError("assurance locator authority subject is absent")
            project_binding_view = self.material_locator_authority_view(
                project_view or {},
                str(locator.get("revision_id")),
            )
        if observed_project_sha256 is not None and (
            project_binding_view is None
            or observed_project_sha256 != canonical_sha256(project_binding_view)
        ):
            raise ValueError("assurance Project Record binding drifted")
        change_type = candidate.get("change_type")
        manifest = normalized.get("verified_content_manifest")
        if require_content_binding and change_type in {
            "material.revision_accepted",
            "evidence.locator_accepted",
        }:
            manifest_contract = (
                manifest.get("contract") if isinstance(manifest, Mapping) else None
            )
            expected_fields = {
                "contract",
                "content_kind",
                "subject_id",
                "content_ref",
                "content_sha256",
            }
            if manifest_contract == "lca_research_kernel.verified_content_manifest/v2":
                expected_fields.add("verified_artifact_ref")
            if (
                not isinstance(manifest, Mapping)
                or set(manifest) != expected_fields
                or manifest_contract
                not in {
                    "lca_research_kernel.verified_content_manifest/v1",
                    "lca_research_kernel.verified_content_manifest/v2",
                    "lca_research_kernel.verified_content_manifest/v3",
                }
                or not isinstance(manifest.get("content_ref"), Mapping)
                or set(manifest["content_ref"]) != {"artifact_id", "sha256"}
                or not isinstance(manifest.get("content_sha256"), str)
                or len(manifest["content_sha256"]) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in manifest["content_sha256"]
                )
            ):
                raise ValueError("assurance verified content manifest is invalid")
            if change_type == "material.revision_accepted" and (
                manifest.get("content_kind") != "material_processing_receipt"
                or manifest.get("subject_id")
                != candidate["material_revision"]["revision_id"]
                or manifest.get("content_ref")
                != candidate["processing_receipt_ref"]
                or manifest.get("content_sha256")
                != candidate["processing_receipt_sha256"]
            ):
                raise ValueError("assurance receipt content manifest drifted")
            if change_type == "evidence.locator_accepted" and (
                manifest.get("content_kind") != "evidence_unit"
                or manifest.get("subject_id")
                != candidate["evidence_locator"]["locator_id"]
                or manifest.get("content_sha256")
                != candidate["evidence_locator"]["content_hash"]
                or (
                    manifest_contract
                    == "lca_research_kernel.verified_content_manifest/v3"
                    and manifest.get("content_ref")
                    != candidate.get("evidence_units_ref")
                )
            ):
                raise ValueError("assurance evidence content manifest drifted")

            revision = (
                candidate["material_revision"]
                if change_type == "material.revision_accepted"
                else project_view.get("material_revisions", {}).get(
                    candidate["evidence_locator"]["revision_id"]
                )
            )
            resolved_content: object | None = None
            if manifest_contract == "lca_research_kernel.verified_content_manifest/v2":
                resolved_content = self._resolve_legacy_v2_mirror(
                    manifest["verified_artifact_ref"],
                    expected_content_sha256=manifest["content_sha256"],
                )
            elif manifest_contract == "lca_research_kernel.verified_content_manifest/v3":
                resolved_content = self._resolve_verified_content(
                    project_view["project_id"],
                    manifest["content_kind"],
                    manifest["content_ref"],
                    expected_content_sha256=manifest["content_sha256"],
                    expected_revision_id=(
                        revision.get("revision_id")
                        if isinstance(revision, Mapping)
                        else None
                    ),
                    expected_input_sha256=(
                        revision.get("artifact_ref", {}).get("sha256")
                        if isinstance(revision, Mapping)
                        else None
                    ),
                    verified_content_cache=verified_content_cache,
                )
            if resolved_content is not None:
                resolved_sha256 = hashlib.sha256(
                    canonical_json_bytes(resolved_content) + b"\n"
                ).hexdigest()
                if resolved_sha256 != manifest.get("content_sha256"):
                    raise ValueError("assurance verified content artifact drifted")
            if resolved_content is not None and change_type == "material.revision_accepted":
                from .material_sources import validate_processing_receipt

                receipt = validate_processing_receipt(resolved_content)
                revision = candidate["material_revision"]
                if (
                    receipt["revision_id"] != revision["revision_id"]
                    or receipt["input_sha256"]
                    != revision["artifact_ref"]["sha256"]
                    or receipt["status"] != revision["processing_status"]
                    or receipt["output_refs"] != revision["extraction_refs"]
                    or manifest["content_ref"]
                    != candidate["processing_receipt_ref"]
                ):
                    raise ValueError("assurance verified receipt semantics drifted")
            elif resolved_content is not None and change_type == "evidence.locator_accepted":
                locator = candidate["evidence_locator"]
                binding = candidate["evidence_binding"]
                revisions = (
                    project_view.get("material_revisions", {})
                    if isinstance(project_view, Mapping)
                    else {}
                )
                revision = revisions.get(locator["revision_id"])
                if (
                    not isinstance(resolved_content, Mapping)
                    or resolved_content.get("source_span")
                    != binding.get("source_span")
                    or not isinstance(revision, Mapping)
                    or manifest["content_ref"]
                    not in revision.get("extraction_refs", [])
                    or (
                        manifest_contract
                        == "lca_research_kernel.verified_content_manifest/v3"
                        and manifest["content_ref"]
                        != candidate.get("evidence_units_ref")
                    )
                ):
                    raise ValueError("assurance verified evidence semantics drifted")
            expected_acceptance_sha256 = canonical_sha256(
                {
                    "candidate_sha256": normalized["candidate_sha256"],
                    "project_view_sha256": observed_project_sha256,
                    "verified_content_manifest": manifest,
                }
            )
            if (
                normalized.get("content_acceptance_sha256")
                != expected_acceptance_sha256
            ):
                raise ValueError("assurance verified content digest drifted")
        _require_string(normalized, "evaluated_at")
        return normalized

    def _validate_artifact_acceptance(
        self, value: Mapping[str, object], digest: str
    ) -> dict[str, Any]:
        if (
            not isinstance(value, Mapping)
            or value.get("contract") != ARTIFACT_ACCEPTANCE_CONTRACT
            or value.get("decision") != "accepted"
            or value.get("evaluator") != "assurance_boundary"
        ):
            raise ValueError("Project Record requires an artifact assurance acceptance")
        normalized = deepcopy(dict(value))
        if normalized.get("artifact_sha256") != digest:
            raise ValueError("assurance artifact hash drifted")
        _require_string(normalized, "evaluated_at")
        return normalized

    def _safe_artifact_path(self, relative_path: str) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("Project Record artifact path is unsafe")
        path = self.workspace / relative
        try:
            path.resolve().relative_to(self.workspace.resolve())
        except ValueError as error:
            raise ValueError("Project Record artifact path escapes its workspace") from error
        return path

    def _append_event(
        self,
        event_type: str,
        payload: Mapping[str, object],
        *,
        actor: str,
        idempotency_key: str,
        occurred_at: str,
    ) -> dict[str, Any]:
        with self._exclusive_write_lock():
            return self._append_event_locked(
                event_type,
                payload,
                actor=actor,
                idempotency_key=idempotency_key,
                occurred_at=occurred_at,
            )

    def _append_event_locked(
        self,
        event_type: str,
        payload: Mapping[str, object],
        *,
        actor: str,
        idempotency_key: str,
        occurred_at: str,
    ) -> dict[str, Any]:
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError("Project Record idempotency key is required")
        events = self._read_events()
        normalized_payload = deepcopy(dict(payload))
        for event in events:
            if event["idempotency_key"] == idempotency_key:
                if event["event_type"] == event_type and event["payload"] == normalized_payload:
                    self._write_projection(self._replay(events))
                    return deepcopy(event)
                raise ValueError(
                    f"Idempotency key '{idempotency_key}' was reused with different content"
                )
        previous = events[-1]["event_sha256"] if events else None
        sequence = len(events) + 1
        identity_basis = {
            "sequence": sequence,
            "event_type": event_type,
            "actor": actor,
            "idempotency_key": idempotency_key,
            "payload": normalized_payload,
            "previous_event_sha256": previous,
        }
        event = {
            "contract": EVENT_CONTRACT,
            "sequence": sequence,
            "event_id": str(
                uuid.uuid5(uuid.NAMESPACE_URL, canonical_sha256(identity_basis))
            ),
            "event_type": event_type,
            "occurred_at": occurred_at,
            "actor": actor,
            "idempotency_key": idempotency_key,
            "payload": normalized_payload,
            "previous_event_sha256": previous,
        }
        event["event_sha256"] = canonical_sha256(event)
        candidate_events = [*events, event]
        view = self._replay(candidate_events)
        content = b"".join(canonical_json_bytes(row) + b"\n" for row in candidate_events)
        _atomic_write(self.event_path, content)
        self._write_projection(view)
        return deepcopy(event)

    def _read_events(self) -> list[dict[str, Any]]:
        if not self.event_path.is_file():
            return []
        events = []
        for line_number, line in enumerate(
            self.event_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"Project Record event JSON is invalid at line {line_number}"
                ) from error
            if not isinstance(value, dict):
                raise ValueError("Project Record event must be an object")
            events.append(value)
        return events

    def _apply_material_package_payload(
        self,
        view: dict[str, Any],
        payload: Mapping[str, object],
        *,
        actor: object,
        idempotency_keys: set[str],
        verified_content_cache: dict[tuple[str, ...], object],
    ) -> None:
        if actor != "assurance_boundary":
            raise ValueError("accepted material package bypassed assurance")
        entries = self._material_package_entries(payload)
        for entry in entries:
            if not isinstance(entry, Mapping) or set(entry) != {
                "event_type",
                "idempotency_key",
                "payload",
            }:
                raise ValueError("Project Record material package entry is invalid")
            idempotency_key = entry.get("idempotency_key")
            if (
                not isinstance(idempotency_key, str)
                or not idempotency_key.strip()
                or idempotency_key in idempotency_keys
            ):
                raise ValueError(
                    "Project Record material package idempotency is invalid"
                )
            idempotency_keys.add(idempotency_key)
            accepted_payload = entry.get("payload")
            if (
                not isinstance(accepted_payload, Mapping)
                or set(accepted_payload)
                != {"contract", "candidate", "assurance_acceptance"}
                or accepted_payload.get("contract")
                != "lca_research_kernel.project_change_accepted/v1"
            ):
                raise ValueError(
                    "Project Record material package acceptance is invalid"
                )
            candidate = accepted_payload.get("candidate")
            acceptance = accepted_payload.get("assurance_acceptance")
            normalized = self._validate_candidate(  # type: ignore[arg-type]
                candidate,
                require_material_bindings=True,
                require_evidence_units_ref=True,
            )
            change_type = normalized["change_type"]
            if change_type not in {
                "material.registered",
                "material.revision_accepted",
                "evidence.locator_accepted",
            }:
                raise ValueError(
                    "Project Record material package change type is unsupported"
                )
            if entry.get("event_type") != MATERIAL_EVENT_TYPES[change_type]:
                raise ValueError("Project Record material package event type drifted")
            self._validate_acceptance(  # type: ignore[arg-type]
                acceptance,
                normalized,
                project_view=view,
                require_project_binding=True,
                require_content_binding=(
                    change_type
                    in {"material.revision_accepted", "evidence.locator_accepted"}
                ),
                verified_content_cache=verified_content_cache,
            )
            apply_accepted_candidate(view, normalized)

    def _replay(
        self,
        events: list[dict[str, Any]],
        *,
        verified_content_cache: dict[tuple[str, ...], object] | None = None,
    ) -> dict[str, Any]:
        if not events:
            raise ValueError("Project Record history is empty")
        if verified_content_cache is None:
            verified_content_cache = {}
        cacheable_dependencies = True
        view: dict[str, Any] = {
            "contract": VIEW_CONTRACT,
            "project_id": None,
            "title": None,
            "objective": None,
            "status": "pending",
            "facts": {},
            "artifacts": {},
            "agent_startups": {},
            "capability_runs": {},
            "capability_catalog": None,
            "decisions": [],
            "risks": {},
            "result": None,
            "next_route": None,
            "last_event_id": None,
            "last_event_sha256": None,
        }
        prior_hash = None
        keys: set[str] = set()
        for expected_sequence, event in enumerate(events, start=1):
            if event.get("contract") != EVENT_CONTRACT:
                raise ValueError("Project Record event contract is unsupported")
            if event.get("sequence") != expected_sequence:
                raise ValueError("Project Record event sequence is not contiguous")
            key = event.get("idempotency_key")
            if not isinstance(key, str) or key in keys:
                raise ValueError("Project Record idempotency key is invalid or duplicated")
            keys.add(key)
            if event.get("previous_event_sha256") != prior_hash:
                raise ValueError("Project Record event hash chain drifted")
            stored_hash = event.get("event_sha256")
            unsigned = {key: value for key, value in event.items() if key != "event_sha256"}
            if stored_hash != canonical_sha256(unsigned):
                raise ValueError("Project Record event content hash drifted")
            prior_hash = stored_hash
            event_type = event.get("event_type")
            payload = event.get("payload")
            if not isinstance(payload, Mapping):
                raise ValueError("Project Record event payload is invalid")
            if expected_sequence == 1:
                if event_type != "research.project.created":
                    raise ValueError("Project Record must start with project creation")
                view.update(
                    {
                        "project_id": _require_string(payload, "project_id"),
                        "title": _require_string(payload, "title"),
                        "objective": _require_string(payload, "objective"),
                        "status": "active",
                        "next_route": _require_string(payload, "next_route"),
                    }
                )
            elif event_type == MATERIAL_PACKAGE_EVENT_TYPE:
                self._apply_material_package_payload(
                    view,
                    payload,
                    actor=event.get("actor"),
                    idempotency_keys=keys,
                    verified_content_cache=verified_content_cache,
                )
            elif event_type == "research.project.accepted_change" or event_type in (
                MATERIAL_EVENT_TYPES.values()
            ):
                candidate = payload.get("candidate")
                acceptance = payload.get("assurance_acceptance")
                if event.get("actor") != "assurance_boundary":
                    raise ValueError("accepted Project Record change bypassed assurance")
                strict_material_binding = (
                    isinstance(acceptance, Mapping)
                    and isinstance(acceptance.get("project_view_sha256"), str)
                )
                manifest = (
                    acceptance.get("verified_content_manifest")
                    if isinstance(acceptance, Mapping)
                    else None
                )
                manifest_contract = (
                    manifest.get("contract")
                    if isinstance(manifest, Mapping)
                    else None
                )
                if manifest_contract == (
                    "lca_research_kernel.verified_content_manifest/v2"
                ):
                    cacheable_dependencies = False
                content_digest_present = (
                    isinstance(acceptance, Mapping)
                    and isinstance(acceptance.get("content_acceptance_sha256"), str)
                )
                known_content_binding = manifest_contract in {
                    "lca_research_kernel.verified_content_manifest/v1",
                    "lca_research_kernel.verified_content_manifest/v2",
                    "lca_research_kernel.verified_content_manifest/v3",
                }
                if (manifest is None) != (
                    not content_digest_present
                ) or (manifest is not None and not known_content_binding):
                    raise ValueError(
                        "assurance verified content manifest is unsupported"
                    )
                normalized = self._validate_candidate(  # type: ignore[arg-type]
                    candidate,
                    require_material_bindings=strict_material_binding,
                    require_evidence_units_ref=(
                        manifest_contract
                        == "lca_research_kernel.verified_content_manifest/v3"
                    ),
                    allow_legacy_active_turn_fact=True,
                )
                self._validate_acceptance(  # type: ignore[arg-type]
                    acceptance,
                    normalized,
                    project_view=view,
                    require_project_binding=strict_material_binding,
                    require_content_binding=(
                        known_content_binding and content_digest_present
                    ),
                    verified_content_cache=verified_content_cache,
                )
                change_type = normalized["change_type"]
                expected_event_type = MATERIAL_EVENT_TYPES.get(
                    change_type, "research.project.accepted_change"
                )
                if event_type != expected_event_type:
                    raise ValueError("accepted Project Record material event type drifted")
                apply_accepted_candidate(view, normalized)
            elif event_type == "research.agent.started":
                if event.get("actor") != "product_interface":
                    raise ValueError("Agent startup receipt actor is invalid")
                if payload.get("contract") != (
                    "lca_research_kernel.agent_startup_receipt/v1"
                ):
                    raise ValueError("Agent startup receipt contract is unsupported")
                from .agent_bootstrap import validate_startup_context

                context = validate_startup_context(
                    payload.get("startup_context")  # type: ignore[arg-type]
                )
                run_id = _require_string(payload, "run_id")
                observed_at = _require_string(payload, "observed_at")
                if (
                    payload.get("scientific_result_created") is not False
                    or payload.get("executed_external_effects") != 0
                ):
                    raise ValueError("Agent startup receipt effect boundary drifted")
                receipt = {
                    "run_id": run_id,
                    "observed_at": observed_at,
                    "product_id": context["product_id"],
                    "manifest_sha256": context["manifest_sha256"],
                    "context_sha256": context["context_sha256"],
                }
                prior = view["agent_startups"].get(run_id)
                if prior is not None and prior != receipt:
                    raise ValueError("Agent startup run_id was reused with drifted context")
                view["agent_startups"][run_id] = receipt
            elif event_type in {
                "research.capability.requested",
                "research.capability.completed",
                "research.capability.failed",
                "research.capability.blocked",
            }:
                if event.get("actor") != "assurance_boundary":
                    raise ValueError("capability run event bypassed assurance")
                if payload.get("contract") != (
                    "lca_research_kernel.capability_run_event/v1"
                ):
                    raise ValueError("capability run event contract is unsupported")
                request_id = _require_string(payload, "request_id")
                capability_id = _require_string(payload, "capability_id")
                stage = _require_string(payload, "stage")
                expected_stage = str(event_type).rsplit(".", 1)[-1]
                if stage != expected_stage:
                    raise ValueError("capability run event stage drifted")
                runs = view["capability_runs"]
                if stage == "requested":
                    if request_id in runs:
                        raise ValueError("capability request was duplicated")
                    runs[request_id] = {
                        "capability_id": capability_id,
                        "input_sha256": _require_string(payload, "input_sha256"),
                        "catalog_snapshot_sha256": _require_string(
                            payload, "catalog_snapshot_sha256"
                        ),
                        "source_lock_id": _require_string(payload, "source_lock_id"),
                        "terminal_status": None,
                        "receipt": None,
                    }
                else:
                    run = runs.get(request_id)
                    if not isinstance(run, dict):
                        raise ValueError("capability terminal event lacks request")
                    if run["capability_id"] != capability_id:
                        raise ValueError("capability terminal identity drifted")
                    if run["terminal_status"] is not None:
                        raise ValueError("capability run has multiple terminal events")
                    receipt = payload.get("receipt")
                    if not isinstance(receipt, Mapping):
                        raise ValueError("capability terminal receipt is absent")
                    if receipt.get("executed_external_effects") != 0:
                        raise ValueError("capability receipt external effects drifted")
                    run["terminal_status"] = stage
                    run["receipt"] = deepcopy(dict(receipt))
            elif event_type == "research.capability.catalog_adopted":
                if event.get("actor") != "assurance_boundary":
                    raise ValueError("capability catalog adoption bypassed assurance")
                if payload.get("contract") != (
                    "lca_research_kernel.capability_catalog_adopted/v1"
                ):
                    raise ValueError("capability catalog adoption contract is unsupported")
                summary = payload.get("summary")
                acceptance = payload.get("assurance_acceptance")
                if (
                    not isinstance(summary, Mapping)
                    or not isinstance(acceptance, Mapping)
                    or acceptance.get("decision") != "accepted"
                    or acceptance.get("evaluator") != "assurance_boundary"
                    or acceptance.get("summary_sha256") != canonical_sha256(summary)
                ):
                    raise ValueError("capability catalog adoption acceptance drifted")
                adopted = deepcopy(dict(summary))
                prior = view.get("capability_catalog")
                if prior is not None and prior != adopted:
                    raise ValueError("Project Record capability catalog changed in place")
                view["capability_catalog"] = adopted
            elif event_type == "research.project.artifact_registered":
                if event.get("actor") != "assurance_boundary":
                    raise ValueError("Project Record artifact bypassed assurance")
                self._validate_artifact_acceptance(
                    payload.get("assurance_acceptance"),  # type: ignore[arg-type]
                    _require_string(payload, "sha256"),
                )
                artifact = {
                    "path": payload["path"],
                    "role": _require_string(payload, "role"),
                    "sha256": payload["sha256"],
                    "bytes": payload.get("bytes"),
                }
                derived_from = payload.get("derived_from")
                if derived_from is not None:
                    artifact["derived_from"] = self._normalize_artifact_derivation(
                        derived_from, view  # type: ignore[arg-type]
                    )
                view["artifacts"][_require_string(payload, "path")] = artifact
            else:
                raise ValueError(f"Project Record event type is unsupported: {event_type}")
            view["last_event_id"] = event.get("event_id")
            view["last_event_sha256"] = stored_hash
        package_dependencies: list[tuple[dict[str, str], str, int]] = []
        for event in events:
            if event.get("event_type") != MATERIAL_PACKAGE_EVENT_TYPE:
                continue
            payload = event.get("payload")
            if (
                isinstance(payload, Mapping)
                and payload.get("contract") == MATERIAL_PACKAGE_CONTRACT
                and isinstance(payload.get("entries_ref"), Mapping)
            ):
                package_dependencies.append(
                    (
                        deepcopy(dict(payload["entries_ref"])),
                        str(payload["package_id"]),
                        int(payload["entry_count"]),
                    )
                )
        container_dependencies = sorted(
            {
                (
                    str(key[1]),
                    str(key[2]),
                    str(key[3]),
                )
                for key in verified_content_cache
                if isinstance(key, tuple)
                and len(key) == 4
                and key[0] == "verified-container"
            }
        )
        self._replayed_package_dependencies = package_dependencies
        self._replayed_container_dependencies = [
            (
                project_id,
                content_kind,
                {"artifact_id": digest, "sha256": digest},
            )
            for project_id, content_kind, digest in container_dependencies
        ]
        self._replayed_cacheable = cacheable_dependencies
        return view

    def _write_projection(self, view: Mapping[str, object]) -> None:
        _atomic_write(self.projection_path, canonical_json_bytes(view) + b"\n")
        signature = self._event_file_signature()
        if signature is not None:
            self._cache_verified_view(view, signature)
