"""Immediate, source-bound URL attachment intake and safe output lookup."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import mimetypes
import os
import socket
import ssl
import stat
import tempfile
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.parse import urljoin

import httpx
import httpcore

from .contracts import validate_content_ref


class AttachmentIntakeError(ValueError):
    """A remote attachment was rejected before becoming an InputArtifact."""


@dataclass(frozen=True)
class FetchedAttachment:
    original_host: str
    final_host: str
    media_type: str
    byte_size: int
    sha256: str
    content: bytes


class QingxiaodaFileBindingResolver:
    """Resolve an opaque platform file id through an owner-only proxy projection."""

    CONTRACT = "lca_research_service.qingxiaoda_file_bindings/v1"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).absolute()

    def __call__(
        self, project_id: str, file_id: str, filename: str
    ) -> dict[str, str]:
        rows = self._load()
        matches = []
        for row in rows:
            if row["project_id"] == project_id and row["file_id"] == file_id:
                matches.append(row)
        if len(matches) != 1:
            raise KeyError("Qingxiaoda file id is unavailable")
        row = matches[0]
        if filename and row["filename"] != filename:
            raise ValueError("Qingxiaoda filename binding drifted")
        return {"url": row["url"], "filename": row["filename"]}

    def probe(self) -> dict[str, Any]:
        rows = self._load()
        return {"ready": True, "binding_count": len(rows)}

    def _load(self) -> list[dict[str, str]]:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.path, flags)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077
                or metadata.st_size > 1024 * 1024
            ):
                raise ValueError("Qingxiaoda file bindings are unsafe")
            payload = os.read(descriptor, metadata.st_size + 1)
            after = os.fstat(descriptor)
            if (
                len(payload) != metadata.st_size
                or (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            ):
                raise ValueError("Qingxiaoda file bindings drifted")
        finally:
            os.close(descriptor)
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ValueError("Qingxiaoda file bindings are invalid") from error
        rows = value.get("bindings") if isinstance(value, dict) else None
        if (
            not isinstance(value, dict)
            or set(value) != {"contract", "bindings"}
            or value.get("contract") != self.CONTRACT
            or not isinstance(rows, list)
        ):
            raise ValueError("Qingxiaoda file bindings are invalid")
        for row in rows:
            if (
                not isinstance(row, dict)
                or set(row) != {"project_id", "file_id", "url", "filename"}
                or any(not isinstance(row.get(key), str) or not row[key] for key in row)
            ):
                raise ValueError("Qingxiaoda file bindings are invalid")
        identities = [(row["project_id"], row["file_id"]) for row in rows]
        if len(identities) != len(set(identities)):
            raise ValueError("Qingxiaoda file bindings are ambiguous")
        return rows


def _default_resolver(host: str) -> list[str]:
    return sorted(
        {
            item[4][0]
            for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        }
    )


def _public_addresses(
    host: str,
    resolver: Callable[[str], list[str]],
) -> list[str]:
    try:
        addresses = resolver(host)
    except OSError as error:
        raise ValueError("attachment host resolution failed") from error
    if not addresses:
        raise ValueError("attachment host did not resolve")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as error:
            raise ValueError("attachment host resolution is invalid") from error
        if not ip.is_global:
            raise ValueError("attachment URL resolved to a non-public address")
    return sorted(set(addresses))


class _PinnedNetworkBackend(httpcore.SyncBackend):
    """Resolve and pin the actual peer address at connect time."""

    def __init__(self, resolver: Callable[[str], list[str]]) -> None:
        self._resolver = resolver

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ):
        address = _public_addresses(host, self._resolver)[0]
        return super().connect_tcp(
            address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class _PinnedHTTPTransport(httpx.HTTPTransport):
    def __init__(self, resolver: Callable[[str], list[str]]) -> None:
        self._pool = httpcore.ConnectionPool(
            ssl_context=ssl.create_default_context(),
            max_connections=10,
            max_keepalive_connections=0,
            keepalive_expiry=0,
            http1=True,
            http2=False,
            retries=0,
            network_backend=_PinnedNetworkBackend(resolver),
        )


class _PinnedHTTPClient:
    """One-response client that deterministically closes its pinned pool."""

    def __init__(self, resolver: Callable[[str], list[str]]) -> None:
        self._resolver = resolver

    @contextmanager
    def stream(self, method: str, url: str, *, follow_redirects: bool):
        with httpx.Client(
            timeout=httpx.Timeout(20.0, connect=10.0),
            transport=_PinnedHTTPTransport(self._resolver),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            with client.stream(
                method,
                url,
                follow_redirects=follow_redirects,
            ) as response:
                yield response


def validate_remote_url(
    url: str,
    *,
    allowed_hosts: Iterable[str],
    resolver: Callable[[str], list[str]] = _default_resolver,
) -> str:
    if not isinstance(url, str) or len(url) > 4096:
        raise ValueError("attachment URL is invalid")
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise ValueError("attachment URL must use HTTPS")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("attachment URL port is invalid") from error
    if port not in {None, 443}:
        raise ValueError("attachment URL must use the HTTPS service port")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("attachment URL credentials are forbidden")
    host = (parsed.hostname or "").casefold().rstrip(".")
    allowed = {item.casefold().rstrip(".") for item in allowed_hosts}
    if not host or host not in allowed:
        raise ValueError("attachment URL host is not allowed")
    _public_addresses(host, resolver)
    return host


class AttachmentIntake:
    def __init__(
        self,
        *,
        project_root: str | Path,
        allowed_hosts: Iterable[str],
        max_bytes: int = 10 * 1024 * 1024,
        client: Any | None = None,
        resolver: Callable[[str], list[str]] = _default_resolver,
    ) -> None:
        raw_root = Path(project_root)
        if ".." in raw_root.parts:
            raise ValueError("project root path escape is forbidden")
        self.project_root = raw_root.resolve()
        self.project_root.mkdir(parents=True, exist_ok=True)
        self.allowed_hosts = tuple(allowed_hosts)
        if not self.allowed_hosts:
            raise ValueError("attachment host allowlist must be non-empty")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.max_bytes = max_bytes
        self.resolver = resolver
        self.client = client or _PinnedHTTPClient(resolver)

    def fetch_durable(
        self,
        url: str,
        *,
        expected_media_prefix: str,
        checkpoint_identity: str,
    ) -> FetchedAttachment:
        """Return only after the completed network read has a durable replay copy."""

        identity = hashlib.sha256(checkpoint_identity.encode("utf-8")).hexdigest()
        cache = self.project_root / ".remote-intake-cache"
        if cache.is_symlink():
            raise AttachmentIntakeError("remote intake cache is unsafe")
        cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(cache, 0o700)
        body_path = cache / f"{identity}.body"
        metadata_path = cache / f"{identity}.json"
        intent_path = cache / f"{identity}.intent"
        request_sha256 = hashlib.sha256(url.encode("utf-8")).hexdigest()
        if body_path.exists() and metadata_path.exists():
            return self._read_durable_fetch(
                body_path,
                metadata_path,
                request_sha256=request_sha256,
                expected_media_prefix=expected_media_prefix,
            )
        if metadata_path.exists() and not body_path.exists():
            raise AttachmentIntakeError("durable remote intake is incomplete")
        if intent_path.exists():
            intent = self._read_fetch_intent(
                intent_path,
                request_sha256=request_sha256,
                expected_media_prefix=expected_media_prefix,
            )
            if body_path.exists():
                body = self._read_cache_file(body_path, self.max_bytes)
                if len(body) == intent["expected_bytes"]:
                    metadata = self._completed_fetch_metadata(intent, body)
                    self._publish_cache_file(
                        metadata_path,
                        (
                            json.dumps(
                                metadata, sort_keys=True, separators=(",", ":")
                            )
                            + "\n"
                        ).encode(),
                    )
                    intent_path.unlink()
                    self._fsync_cache(cache)
                    return self._read_durable_fetch(
                        body_path,
                        metadata_path,
                        request_sha256=request_sha256,
                        expected_media_prefix=expected_media_prefix,
                    )
                if len(body) > intent["expected_bytes"]:
                    raise AttachmentIntakeError("durable remote intake drifted")
                body_path.unlink()
            intent_path.unlink()
            self._fsync_cache(cache)
        elif body_path.exists():
            raise AttachmentIntakeError("durable remote intake is incomplete")
        try:
            fetched = self._fetch(
                url,
                expected_media_prefix=expected_media_prefix,
                durable_body_path=body_path,
                durable_intent_path=intent_path,
                request_sha256=request_sha256,
            )
        except AttachmentIntakeError:
            raise
        except (httpx.HTTPError, OSError, ValueError) as error:
            raise AttachmentIntakeError(
                "remote attachment intake was rejected"
            ) from error
        metadata = {
            "contract": "lca_research_service.remote_fetch_checkpoint/v1",
            "request_sha256": request_sha256,
            "original_host": fetched.original_host,
            "final_host": fetched.final_host,
            "media_type": fetched.media_type,
            "byte_size": fetched.byte_size,
            "sha256": fetched.sha256,
        }
        self._publish_cache_file(
            metadata_path,
            (json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        )
        intent_path.unlink()
        self._fsync_cache(cache)
        return self._read_durable_fetch(
            body_path,
            metadata_path,
            request_sha256=request_sha256,
            expected_media_prefix=expected_media_prefix,
        )

    def discard_durable_fetch(self, checkpoint_identity: str) -> None:
        identity = hashlib.sha256(checkpoint_identity.encode("utf-8")).hexdigest()
        cache = self.project_root / ".remote-intake-cache"
        if not cache.exists():
            return
        if cache.is_symlink() or not cache.is_dir():
            raise AttachmentIntakeError("remote intake cache is unsafe")
        for suffix in (".json", ".body", ".intent"):
            path = cache / f"{identity}{suffix}"
            if path.is_symlink():
                raise AttachmentIntakeError("remote intake cache is unsafe")
            path.unlink(missing_ok=True)
        self._fsync_cache(cache)

    @staticmethod
    def _fsync_cache(cache: Path) -> None:
        directory_fd = os.open(cache, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    @staticmethod
    def _publish_cache_file(path: Path, payload: bytes) -> None:
        descriptor, name = tempfile.mkstemp(prefix=".remote-", dir=path.parent)
        temporary = Path(name)
        try:
            os.chmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as target:
                target.write(payload)
                target.flush()
                os.fsync(target.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
            directory_fd = os.open(
                path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def _read_durable_fetch(
        self,
        body_path: Path,
        metadata_path: Path,
        *,
        request_sha256: str,
        expected_media_prefix: str,
    ) -> FetchedAttachment:
        if not body_path.exists() or not metadata_path.exists():
            raise AttachmentIntakeError("durable remote intake is incomplete")
        values = [
            self._read_cache_file(metadata_path, 64 * 1024),
            self._read_cache_file(body_path, self.max_bytes),
        ]
        try:
            metadata = json.loads(values[0])
        except json.JSONDecodeError as error:
            raise AttachmentIntakeError("durable remote intake is invalid") from error
        body = values[1]
        if (
            not isinstance(metadata, dict)
            or set(metadata)
            != {"contract", "request_sha256", "original_host", "final_host", "media_type", "byte_size", "sha256"}
            or metadata.get("contract")
            != "lca_research_service.remote_fetch_checkpoint/v1"
            or metadata.get("request_sha256") != request_sha256
            or not isinstance(metadata.get("media_type"), str)
            or not metadata["media_type"].startswith(expected_media_prefix)
            or metadata.get("byte_size") != len(body)
            or metadata.get("sha256") != hashlib.sha256(body).hexdigest()
        ):
            raise AttachmentIntakeError("durable remote intake drifted")
        return FetchedAttachment(
            original_host=str(metadata["original_host"]),
            final_host=str(metadata["final_host"]),
            media_type=metadata["media_type"],
            byte_size=len(body),
            sha256=metadata["sha256"],
            content=body,
        )

    @staticmethod
    def _completed_fetch_metadata(intent: Mapping[str, Any], body: bytes) -> dict[str, Any]:
        return {
            "contract": "lca_research_service.remote_fetch_checkpoint/v1",
            "request_sha256": intent["request_sha256"],
            "original_host": intent["original_host"],
            "final_host": intent["final_host"],
            "media_type": intent["media_type"],
            "byte_size": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }

    def _read_fetch_intent(
        self,
        path: Path,
        *,
        request_sha256: str,
        expected_media_prefix: str,
    ) -> dict[str, Any]:
        try:
            intent = json.loads(self._read_cache_file(path, 64 * 1024))
        except json.JSONDecodeError as error:
            raise AttachmentIntakeError("durable remote intake intent is invalid") from error
        if (
            not isinstance(intent, dict)
            or set(intent)
            != {
                "contract",
                "request_sha256",
                "original_host",
                "final_host",
                "media_type",
                "expected_bytes",
            }
            or intent.get("contract")
            != "lca_research_service.remote_fetch_intent/v1"
            or intent.get("request_sha256") != request_sha256
            or not isinstance(intent.get("media_type"), str)
            or not intent["media_type"].startswith(expected_media_prefix)
            or not isinstance(intent.get("expected_bytes"), int)
            or isinstance(intent.get("expected_bytes"), bool)
            or not 0 <= intent["expected_bytes"] <= self.max_bytes
        ):
            raise AttachmentIntakeError("durable remote intake intent drifted")
        return intent

    @staticmethod
    def _read_cache_file(path: Path, ceiling: int) -> bytes:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_uid != os.getuid()
                or before.st_size > ceiling
            ):
                raise AttachmentIntakeError("durable remote intake is unsafe")
            payload = b""
            while len(payload) <= ceiling:
                chunk = os.read(
                    descriptor, min(1024 * 1024, ceiling + 1 - len(payload))
                )
                if not chunk:
                    break
                payload += chunk
            after = os.fstat(descriptor)
            if (
                len(payload) != before.st_size
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            ):
                raise AttachmentIntakeError("durable remote intake drifted")
            return payload
        finally:
            os.close(descriptor)

    def fetch(
        self,
        url: str,
        *,
        expected_media_prefix: str,
    ) -> FetchedAttachment:
        try:
            return self._fetch(
                url,
                expected_media_prefix=expected_media_prefix,
            )
        except AttachmentIntakeError:
            raise
        except (httpx.HTTPError, OSError, ValueError) as error:
            raise AttachmentIntakeError("remote attachment intake was rejected") from error

    def _fetch(
        self,
        url: str,
        *,
        expected_media_prefix: str,
        durable_body_path: Path | None = None,
        durable_intent_path: Path | None = None,
        request_sha256: str | None = None,
    ) -> FetchedAttachment:
        durable = durable_body_path is not None
        if durable != (durable_intent_path is not None and request_sha256 is not None):
            raise ValueError("durable fetch checkpoint is incomplete")
        original_host = validate_remote_url(
            url, allowed_hosts=self.allowed_hosts, resolver=self.resolver
        )
        current_url = url
        body = bytearray()
        final_host = original_host
        content_type = ""
        for redirect_count in range(6):
            final_host = validate_remote_url(
                current_url,
                allowed_hosts=self.allowed_hosts,
                resolver=self.resolver,
            )
            with self.client.stream(
                "GET", current_url, follow_redirects=False
            ) as response:
                status_code = int(getattr(response, "status_code", 200))
                location = response.headers.get("location")
                if 300 <= status_code < 400:
                    if not location or redirect_count == 5:
                        raise ValueError("attachment redirect is invalid")
                    next_url = urljoin(current_url, location)
                    validate_remote_url(
                        next_url,
                        allowed_hosts=self.allowed_hosts,
                        resolver=self.resolver,
                    )
                    current_url = next_url
                    continue
                response.raise_for_status()
                response_url = str(getattr(response, "url", current_url))
                final_host = validate_remote_url(
                    response_url,
                    allowed_hosts=self.allowed_hosts,
                    resolver=self.resolver,
                )
                content_type = response.headers.get("content-type", "").split(
                    ";", 1
                )[0]
                if not content_type.startswith(expected_media_prefix):
                    raise ValueError(
                        "attachment MIME type does not match its content role"
                    )
                length = response.headers.get("content-length")
                if length is not None and int(length) > self.max_bytes:
                    raise ValueError("attachment exceeds the byte limit")
                expected_bytes = int(length) if length is not None else None
                if durable and expected_bytes is None:
                    raise ValueError(
                        "durable attachment intake requires content-length"
                    )
                body_descriptor: int | None = None
                digest = hashlib.sha256()
                byte_size = 0
                if durable:
                    intent = {
                        "contract": "lca_research_service.remote_fetch_intent/v1",
                        "request_sha256": request_sha256,
                        "original_host": original_host,
                        "final_host": final_host,
                        "media_type": content_type,
                        "expected_bytes": expected_bytes,
                    }
                    self._publish_cache_file(
                        durable_intent_path,
                        (
                            json.dumps(intent, sort_keys=True, separators=(",", ":"))
                            + "\n"
                        ).encode(),
                    )
                    body_descriptor = os.open(
                        durable_body_path,
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                    )
                try:
                    for chunk in response.iter_bytes():
                        byte_size += len(chunk)
                        if byte_size > self.max_bytes:
                            raise ValueError("attachment exceeds the byte limit")
                        digest.update(chunk)
                        if body_descriptor is None:
                            body.extend(chunk)
                        else:
                            view = memoryview(chunk)
                            while view:
                                written = os.write(body_descriptor, view)
                                if written <= 0:
                                    raise OSError("durable attachment write failed")
                                view = view[written:]
                            os.fsync(body_descriptor)
                    if expected_bytes is not None and byte_size != expected_bytes:
                        raise ValueError("attachment byte count drifted")
                    if body_descriptor is not None:
                        os.fsync(body_descriptor)
                        os.close(body_descriptor)
                        body_descriptor = None
                finally:
                    if body_descriptor is not None:
                        os.close(body_descriptor)
                break
        else:
            raise ValueError("attachment redirect limit exceeded")
        payload = bytes(body)
        if not durable:
            digest = hashlib.sha256(payload)
            byte_size = len(payload)
        return FetchedAttachment(
            original_host=original_host,
            final_host=final_host,
            media_type=content_type,
            byte_size=byte_size,
            sha256=digest.hexdigest(),
            content=b"" if durable else payload,
        )

    def freeze(
        self,
        url: str,
        *,
        expected_media_prefix: str,
    ) -> dict[str, Any]:
        fetched = self.fetch(url, expected_media_prefix=expected_media_prefix)
        digest = fetched.sha256
        content_type = fetched.media_type
        extension = mimetypes.guess_extension(content_type) or ".bin"
        relative = Path("intake") / "attachments" / f"{digest}{extension}"
        target = (self.project_root / relative).resolve()
        if not target.is_relative_to(self.project_root):
            raise ValueError("attachment target escaped the project root")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError("existing attachment content drifted")
        else:
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_bytes(fetched.content)
            temporary.replace(target)
        return {
            "contract": "d18_source_bound_attachment/v1",
            "original_host": fetched.original_host,
            "final_host": fetched.final_host,
            "media_type": content_type,
            "bytes": fetched.byte_size,
            "ref": {"path": relative.as_posix(), "sha256": digest},
        }


class ArtifactRegistry:
    """A read-only projection of accepted Project Record registrations."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        project_record_factory: Callable[[Path], object] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self._project_record_factory = project_record_factory
        self._entries: dict[str, dict[str, Any]] = {}

    def register(
        self,
        ref: Mapping[str, Any],
        *,
        content_type: str,
        accepted: bool,
        file_name: str | None = None,
        file_type: str | None = None,
        expires_at: str | None = None,
        project_id: str = "default",
    ) -> dict[str, Any]:
        if not accepted:
            raise PermissionError("only accepted Project Record artifacts may be served")
        normalized = validate_content_ref(ref)
        if normalized is None:
            raise ValueError("artifact reference is required")
        if file_name is None:
            file_name = Path(normalized["path"]).name
        if (
            not isinstance(file_name, str)
            or not file_name
            or "/" in file_name
            or "\\" in file_name
        ):
            raise ValueError("artifact file name is invalid")
        if file_type is None:
            file_type = "file"
        if not isinstance(file_type, str) or not file_type:
            raise ValueError("artifact file type is invalid")
        artifact_id = hashlib.sha256(
            (normalized["path"] + ":" + normalized["sha256"]).encode()
        ).hexdigest()[:32]
        self._entries[artifact_id] = {
            "ref": normalized,
            "content_type": content_type,
            "expires_at": expires_at,
            "file_name": file_name,
            "file_type": file_type,
            "project_id": project_id,
        }
        url = f"/v1/files/{artifact_id}"
        return {
            "id": artifact_id,
            "url": url,
            "fileUrl": url,
            "fileName": file_name,
            "fileType": file_type,
            "mimeType": content_type,
        }

    def rebuild_from_project_records(self) -> list[dict[str, Any]]:
        """Rebuild the disposable registry projection from accepted Project Records."""

        from lca_research_kernel.project_record import ProjectRecord

        rebuilt: list[dict[str, Any]] = []
        projects_root = self.project_root / "projects"
        if not projects_root.is_dir():
            return rebuilt
        self._entries.clear()
        for workspace in sorted(projects_root.iterdir()):
            if (
                workspace.is_symlink()
                or not workspace.is_dir()
                or not (workspace / ProjectRecord.EVENT_FILE).is_file()
            ):
                continue
            record = (
                self._project_record_factory(workspace)
                if self._project_record_factory is not None
                else ProjectRecord.open_or_create_project(workspace)
            )
            view = record.read_current_project_view()
            project_id = view.get("project_id")
            artifacts = view.get("artifacts")
            if not isinstance(project_id, str) or not isinstance(artifacts, Mapping):
                raise ValueError("Project Record artifact projection is invalid")
            for artifact in artifacts.values():
                if not isinstance(artifact, Mapping):
                    raise ValueError("Project Record artifact projection is invalid")
                relative = artifact.get("path")
                digest = artifact.get("sha256")
                role = artifact.get("role")
                if not all(isinstance(value, str) for value in (relative, digest, role)):
                    raise ValueError("Project Record artifact projection is invalid")
                full_ref = {
                    "path": (
                        Path("projects") / project_id / str(relative)
                    ).as_posix(),
                    "sha256": digest,
                }
                content_type = (
                    mimetypes.guess_type(str(relative))[0]
                    or "application/octet-stream"
                )
                rebuilt.append(
                    self.register(
                        full_ref,
                        content_type=content_type,
                        accepted=True,
                        file_name=Path(str(relative)).name,
                        file_type=str(role),
                        project_id=project_id,
                    )
                )
        return rebuilt

    def resolve(
        self, artifact_id: str, *, project_id: str | None = None
    ) -> dict[str, Any]:
        try:
            entry = self._entries[artifact_id]
        except KeyError as error:
            raise KeyError("artifact is not registered") from error
        if project_id is not None and entry["project_id"] != project_id:
            raise PermissionError("artifact belongs to another project")
        expires_at = entry["expires_at"]
        if expires_at is not None:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expiry <= datetime.now(timezone.utc):
                raise PermissionError("artifact download registration expired")
        path = (self.project_root / entry["ref"]["path"]).resolve()
        if not path.is_relative_to(self.project_root) or not path.is_file():
            raise PermissionError("artifact path is outside the registered project")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry["ref"]["sha256"]:
            raise PermissionError("artifact content drifted")
        return {
            "path": path,
            "content_type": entry["content_type"],
            "sha256": digest,
            "project_id": entry["project_id"],
            "file_name": entry["file_name"],
            "file_type": entry["file_type"],
            "byte_size": path.stat().st_size,
            "registry_artifact_id": artifact_id,
        }

    def bind_input(
        self, artifact_id: str, *, filename: str, project_id: str
    ) -> dict[str, Any]:
        resolved = self.resolve(artifact_id, project_id=project_id)
        entry = self._entries[artifact_id]
        if filename != entry["file_name"]:
            raise PermissionError("file input name differs from its registration")
        return {
            "contract": "d18_registered_input_artifact/v1",
            "file_id": artifact_id,
            "filename": filename,
            "file_type": entry["file_type"],
            "media_type": entry["content_type"],
            "ref": dict(entry["ref"]),
            "bytes": resolved["path"].stat().st_size,
        }
