"""Immutable, crash-recoverable project input artifact storage."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

from lca_research_kernel.material_sources import validate_input_artifact

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_filename(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("original filename is invalid")
    name = value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if name in {"", ".", ".."}:
        raise ValueError("original filename is invalid")
    return name[:255]


def _assert_lexical_chain(path: Path, root: Path, *, allow_missing: bool) -> None:
    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError as error:
        raise ValueError("path escaped data root") from error
    current = root.absolute()
    if current.is_symlink():
        raise ValueError("data root must not be a symlink")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("path contains a symlink")
        if not current.exists():
            if allow_missing:
                continue
            raise KeyError("input artifact is unavailable")


def _ensure_directory(path: Path, *, root: Path) -> None:
    root = root.absolute()
    _assert_lexical_chain(path, root, allow_missing=True)
    current = root
    current.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(current, 0o700)
    for part in path.absolute().relative_to(root).parts:
        current = current / part
        current.mkdir(mode=0o700, exist_ok=True)
        if current.is_symlink() or not current.is_dir():
            raise ValueError("directory path is unsafe")
        os.chmod(current, 0o700)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sha256_fd(descriptor: int) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    os.lseek(descriptor, 0, os.SEEK_SET)
    while chunk := os.read(descriptor, 1024 * 1024):
        digest.update(chunk)
        size += len(chunk)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return digest.hexdigest(), size


class InputArtifactStore:
    def __init__(self, data_root: str | Path, *, clock: Callable[[], str] | None = None) -> None:
        lexical = Path(data_root).absolute()
        if lexical.is_symlink():
            raise ValueError("data root must not be a symlink")
        self.data_root = lexical
        self._clock = clock or _utc_now
        _ensure_directory(self.data_root / "projects", root=self.data_root)
        self.locks_root = self.data_root / "control" / "input-artifact-locks"
        _ensure_directory(self.locks_root, root=self.data_root)

    def _directory(self, research_id: str, artifact_id: str) -> Path:
        if not isinstance(research_id, str) or _IDENTIFIER.fullmatch(research_id) is None:
            raise ValueError("research_id is invalid")
        if not isinstance(artifact_id, str) or _SHA256.fullmatch(artifact_id) is None:
            raise ValueError("artifact_id must be a lowercase SHA-256")
        return self.data_root / "projects" / research_id / "inputs" / "sha256" / artifact_id[:2] / artifact_id

    def put_verified(self, *, research_id: str, source: BinaryIO, expected_sha256: str,
                     expected_bytes: int, detected_mime: str, original_filename: str,
                     source_kind: str, source_locator: Mapping[str, str] | None = None,
                     source_identity_sha256: str | None = None,
                     intake_identity_sha256: str | None = None) -> dict[str, Any]:
        directory = self._directory(research_id, expected_sha256)
        if not isinstance(expected_bytes, int) or isinstance(expected_bytes, bool) or expected_bytes < 0:
            raise ValueError("expected_bytes must be non-negative")
        filename = _safe_filename(original_filename)
        lock_path = self.locks_root / f"{research_id}-{expected_sha256}.lock"
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            locator = dict(source_locator or {"upload_id": expected_sha256})
            canonical = self._publish(
                directory,
                research_id,
                source,
                expected_sha256,
                expected_bytes,
                detected_mime,
                filename,
                source_kind,
                locator,
            )
            self._record_lineage(
                directory=directory,
                research_id=research_id,
                artifact_id=expected_sha256,
                source_kind=source_kind,
                source_locator=locator,
                original_filename=filename,
                source_identity_sha256=source_identity_sha256,
                intake_identity_sha256=intake_identity_sha256,
                recorded_at=canonical["created_at"],
            )
            return canonical
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def _record_lineage(
        self,
        *,
        directory: Path,
        research_id: str,
        artifact_id: str,
        source_kind: str,
        source_locator: Mapping[str, str],
        original_filename: str,
        source_identity_sha256: str | None,
        intake_identity_sha256: str | None,
        recorded_at: str,
    ) -> None:
        """Record every immutable source identity even when content bytes deduplicate."""

        locator = dict(source_locator)
        identity = source_identity_sha256 or hashlib.sha256(
            json.dumps(
                locator, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        if _SHA256.fullmatch(identity) is None:
            raise ValueError("source identity must be a lowercase SHA-256")
        intake_identity = intake_identity_sha256 or identity
        if _SHA256.fullmatch(intake_identity) is None:
            raise ValueError("intake identity must be a lowercase SHA-256")
        stable = {
            "contract": "lca_research_service.input_artifact_lineage/v3",
            "project_id": research_id,
            "artifact_id": artifact_id,
            "source_kind": source_kind,
            "source_locator": locator,
            "source_identity_sha256": identity,
            "intake_identity_sha256": intake_identity,
            "original_filename": _safe_filename(original_filename),
        }
        lineage = {**stable, "recorded_at": recorded_at}
        lineage_id = hashlib.sha256(
            json.dumps(
                lineage, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        lineage_directory = directory / "lineages"
        _ensure_directory(lineage_directory, root=self.data_root)
        target = lineage_directory / f"{lineage_id}.json"
        if target.exists() or target.is_symlink():
            if self._read_lineage_path(target, lineage_id) != lineage:
                raise ValueError("input artifact lineage drifted")
            return
        descriptor, name = tempfile.mkstemp(prefix=".lineage-", dir=lineage_directory)
        temporary = Path(name)
        try:
            os.chmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as sink:
                sink.write(
                    (
                        json.dumps(
                            lineage,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode()
                )
                sink.flush()
                os.fsync(sink.fileno())
            os.replace(temporary, target)
            os.chmod(target, 0o600)
            _fsync_directory(lineage_directory)
        finally:
            temporary.unlink(missing_ok=True)

    def _read_lineage_path(
        self, path: Path, expected_lineage_id: str
    ) -> dict[str, Any]:
        _assert_lexical_chain(path, self.data_root, allow_missing=False)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_uid != os.getuid()
                or before.st_size > 64 * 1024
            ):
                raise ValueError("input artifact lineage path is unsafe")
            payload = b""
            while len(payload) <= 64 * 1024:
                chunk = os.read(descriptor, 8192)
                if not chunk:
                    break
                payload += chunk
            after = os.fstat(descriptor)
            if (
                len(payload) > 64 * 1024
                or not stat.S_ISREG(after.st_mode)
                or after.st_nlink != 1
                or after.st_uid != os.getuid()
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            ):
                raise ValueError("input artifact lineage drifted")
        finally:
            os.close(descriptor)
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ValueError("input artifact lineage is invalid") from error
        if (
            not isinstance(value, dict)
            or hashlib.sha256(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            != expected_lineage_id
        ):
            raise ValueError("input artifact lineage digest drifted")
        return value

    def _publish(self, directory: Path, research_id: str, source: BinaryIO, digest_expected: str,
                 size_expected: int, mime: str, filename: str, source_kind: str, locator: dict) -> dict[str, Any]:
        _ensure_directory(directory, root=self.data_root)
        original, metadata_path = directory / "original", directory / "metadata.json"
        if original.exists() and not metadata_path.exists():
            _assert_lexical_chain(original, self.data_root, allow_missing=False)
            fd = os.open(original, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                actual, size = _sha256_fd(fd)
            finally:
                os.close(fd)
            if actual != digest_expected or size != size_expected:
                raise ValueError("incomplete input artifact drifted")
        elif metadata_path.exists() and not original.exists():
            metadata_path.unlink()
        if original.exists() and metadata_path.exists():
            return self.describe(research_id=research_id, artifact_id=digest_expected)

        if not original.exists():
            descriptor, name = tempfile.mkstemp(prefix=".original-", dir=directory)
            temporary = Path(name)
            digest, size = hashlib.sha256(), 0
            try:
                os.chmod(descriptor, 0o600)
                with os.fdopen(descriptor, "wb") as target:
                    while chunk := source.read(1024 * 1024):
                        if not isinstance(chunk, bytes):
                            raise ValueError("source must yield bytes")
                        size += len(chunk)
                        digest.update(chunk)
                        target.write(chunk)
                    target.flush(); os.fsync(target.fileno())
                if size != size_expected or digest.hexdigest() != digest_expected:
                    raise ValueError("input bytes or SHA-256 mismatch")
                os.replace(temporary, original); os.chmod(original, 0o600); _fsync_directory(directory)
            finally:
                temporary.unlink(missing_ok=True)

        metadata = validate_input_artifact({
            "contract": "lca_research_kernel.input_artifact/v1", "artifact_id": digest_expected,
            "project_id": research_id, "sha256": digest_expected, "byte_size": size_expected,
            "detected_media_type": mime, "original_filename": filename,
            "storage_ref": {"artifact_id": digest_expected, "sha256": digest_expected},
            "source_kind": source_kind, "source_locator": locator, "created_at": self._clock(),
            "security_status": "pending_security",
        })
        descriptor, name = tempfile.mkstemp(prefix=".metadata-", dir=directory)
        temporary = Path(name)
        try:
            os.chmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as target:
                target.write((json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n").encode())
                target.flush(); os.fsync(target.fileno())
            os.replace(temporary, metadata_path); os.chmod(metadata_path, 0o600); _fsync_directory(directory)
        finally:
            temporary.unlink(missing_ok=True)
        return metadata

    def _read(self, research_id: str, artifact_id: str) -> tuple[dict[str, Any], int]:
        directory = self._directory(research_id, artifact_id)
        original, metadata_path = directory / "original", directory / "metadata.json"
        _assert_lexical_chain(metadata_path, self.data_root, allow_missing=False)
        _assert_lexical_chain(original, self.data_root, allow_missing=False)
        meta_fd = os.open(metadata_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            with os.fdopen(meta_fd, "r", encoding="utf-8") as source:
                metadata = validate_input_artifact(json.load(source))
        except (OSError, json.JSONDecodeError, ValueError) as error:
            raise ValueError("input artifact metadata is invalid") from error
        original_fd = os.open(original, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        actual, size = _sha256_fd(original_fd)
        if metadata["project_id"] != research_id or metadata["artifact_id"] != artifact_id or actual != artifact_id or size != metadata["byte_size"]:
            os.close(original_fd)
            raise ValueError("input artifact drifted")
        return metadata, original_fd

    def describe(self, *, research_id: str, artifact_id: str) -> dict[str, Any]:
        metadata, descriptor = self._read(research_id, artifact_id)
        os.close(descriptor)
        return metadata

    def open_verified(self, *, research_id: str, artifact_id: str) -> BinaryIO:
        _, descriptor = self._read(research_id, artifact_id)
        return os.fdopen(descriptor, "rb")

    def reuse_verified(
        self,
        *,
        research_id: str,
        source: BinaryIO,
        expected_sha256: str,
        expected_bytes: int,
        detected_mime: str,
        original_filename: str,
        output_artifact_id: str,
    ) -> dict[str, Any]:
        """Freeze accepted output bytes as a new input lineage without promoting them."""

        canonical = self.put_verified(
            research_id=research_id,
            source=source,
            expected_sha256=expected_sha256,
            expected_bytes=expected_bytes,
            detected_mime=detected_mime,
            original_filename=original_filename,
            source_kind="project_reuse",
            source_locator={"artifact_id": output_artifact_id},
        )
        return {
            **canonical,
            "original_filename": _safe_filename(original_filename),
            "source_kind": "project_reuse",
            "source_locator": {"artifact_id": output_artifact_id},
        }

    def list_lineages(
        self, *, research_id: str, artifact_id: str
    ) -> list[dict[str, Any]]:
        """Return immutable provenance receipts without mutating canonical bytes metadata."""

        self.describe(research_id=research_id, artifact_id=artifact_id)
        directory = self._directory(research_id, artifact_id) / "lineages"
        if not directory.exists():
            return []
        _assert_lexical_chain(directory, self.data_root, allow_missing=False)
        result = []
        for path in sorted(directory.glob("*.json")):
            value = self._read_lineage_path(path, path.stem)
            if not isinstance(value, dict):
                raise ValueError("input artifact lineage is invalid")
            contract = value.get("contract")
            if contract == "lca_research_service.input_artifact_lineage/v1":
                valid = (
                    value.get("project_id") == research_id
                    and value.get("artifact_id") == artifact_id
                    and value.get("source_kind") == "project_reuse"
                    and isinstance(value.get("source_locator"), dict)
                )
            elif contract == "lca_research_service.input_artifact_lineage/v2":
                valid = (
                    set(value)
                    == {
                        "contract",
                        "project_id",
                        "artifact_id",
                        "source_kind",
                        "source_locator",
                        "source_identity_sha256",
                        "original_filename",
                        "recorded_at",
                    }
                    and value.get("project_id") == research_id
                    and value.get("artifact_id") == artifact_id
                    and value.get("source_kind")
                    in {"local_upload", "qingxiaoda_url", "project_reuse"}
                    and isinstance(value.get("source_locator"), dict)
                    and _SHA256.fullmatch(
                        str(value.get("source_identity_sha256", ""))
                    )
                    is not None
                    and _safe_filename(str(value.get("original_filename", "")))
                    == value.get("original_filename")
                )
            elif contract == "lca_research_service.input_artifact_lineage/v3":
                valid = (
                    set(value)
                    == {
                        "contract", "project_id", "artifact_id", "source_kind",
                        "source_locator", "source_identity_sha256",
                        "intake_identity_sha256", "original_filename", "recorded_at",
                    }
                    and value.get("project_id") == research_id
                    and value.get("artifact_id") == artifact_id
                    and value.get("source_kind")
                    in {"local_upload", "qingxiaoda_url", "project_reuse"}
                    and isinstance(value.get("source_locator"), dict)
                    and _SHA256.fullmatch(str(value.get("source_identity_sha256", "")))
                    is not None
                    and _SHA256.fullmatch(str(value.get("intake_identity_sha256", "")))
                    is not None
                    and _safe_filename(str(value.get("original_filename", "")))
                    == value.get("original_filename")
                )
            else:
                valid = False
            if not valid:
                raise ValueError("input artifact lineage is invalid")
            result.append(value)
        return result

    def find_by_intake_identity(
        self, *, research_id: str, intake_identity_sha256: str
    ) -> dict[str, Any] | None:
        """Reconcile a published artifact whose remote checkpoint was interrupted."""

        if _SHA256.fullmatch(intake_identity_sha256) is None:
            raise ValueError("intake identity must be a lowercase SHA-256")
        if not isinstance(research_id, str) or _IDENTIFIER.fullmatch(research_id) is None:
            raise ValueError("research_id is invalid")
        digest_root = (
            self.data_root / "projects" / research_id / "inputs" / "sha256"
        )
        if not digest_root.exists():
            return None
        _assert_lexical_chain(digest_root, self.data_root, allow_missing=False)
        matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for prefix in sorted(digest_root.iterdir()):
            if prefix.is_symlink() or not prefix.is_dir():
                raise ValueError("input artifact digest directory is unsafe")
            for artifact_directory in sorted(prefix.iterdir()):
                if artifact_directory.is_symlink() or not artifact_directory.is_dir():
                    raise ValueError("input artifact directory is unsafe")
                artifact_id = artifact_directory.name
                if _SHA256.fullmatch(artifact_id) is None:
                    raise ValueError("input artifact directory identity is invalid")
                for lineage in self.list_lineages(
                    research_id=research_id, artifact_id=artifact_id
                ):
                    if lineage.get("intake_identity_sha256") == intake_identity_sha256:
                        matches.append(
                            (
                                self.describe(
                                    research_id=research_id,
                                    artifact_id=artifact_id,
                                ),
                                lineage,
                            )
                        )
        if len(matches) > 1:
            raise ValueError("source identity resolves to multiple input artifacts")
        if not matches:
            return None
        artifact, lineage = matches[0]
        return {"artifact": artifact, "lineage": lineage}
