"""Private, restart-stable authentication for gateway-authored native evidence."""

from __future__ import annotations

import fcntl
import hmac
import os
import secrets
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .input_artifacts import _ensure_directory, _fsync_directory


_KEY_BYTES = 32
_KEY_DIRECTORY = Path("control/native-evidence-authentication")
_KEY_NAME = "hmac-sha256.key"
_LOCK_NAME = "hmac-sha256.key.lock"


@dataclass(frozen=True)
class NativeEvidenceAuthenticationPorts:
    """Only these operations cross from the service keystore into the gateway."""

    sign: Callable[[bytes], str]
    verify: Callable[[bytes, str], bool]


def _validate_private_file(descriptor: int, *, label: str) -> os.stat_result:
    status = os.fstat(descriptor)
    if (
        not stat.S_ISREG(status.st_mode)
        or status.st_nlink != 1
        or status.st_uid != os.getuid()
    ):
        raise ValueError(f"native evidence {label} identity is unsafe")
    if stat.S_IMODE(status.st_mode) != 0o600:
        raise ValueError(f"native evidence {label} mode is unsafe")
    return status


def _validate_private_directory(path: Path, *, label: str) -> None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError(f"native evidence {label} identity is unsafe") from error
    try:
        status = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(status.st_mode)
            or status.st_uid != os.getuid()
            or stat.S_IMODE(status.st_mode) != 0o700
        ):
            raise ValueError(f"native evidence {label} is unsafe")
    finally:
        os.close(descriptor)


def _read_key(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError("native evidence key identity is unsafe") from error
    try:
        before = _validate_private_file(descriptor, label="key")
        key = os.read(descriptor, _KEY_BYTES + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        len(key) != _KEY_BYTES
        or before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise ValueError("native evidence key is unavailable or drifted")
    return key


def _publish_new_key(key_root: Path, key_path: Path) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=".hmac-sha256-", dir=key_root)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        key = secrets.token_bytes(_KEY_BYTES)
        view = memoryview(key)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, key_path)
        _fsync_directory(key_root)
        published_descriptor = os.open(
            key_path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            published = _validate_private_file(
                published_descriptor, label="published key"
            )
            if published.st_size != _KEY_BYTES:
                raise ValueError("native evidence published key size is unsafe")
        finally:
            os.close(published_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _open_or_create_key(data_root: Path) -> bytes:
    root = Path(data_root).absolute()
    if root.is_symlink():
        raise ValueError("native evidence data root identity is unsafe")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    _validate_private_directory(root, label="data root")
    key_root = root / _KEY_DIRECTORY
    _ensure_directory(key_root, root=root)
    _validate_private_directory(root / "control", label="control directory")
    _validate_private_directory(key_root, label="key directory")

    lock_path = key_root / _LOCK_NAME
    lock_flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        lock_descriptor = os.open(lock_path, lock_flags, 0o600)
    except OSError as error:
        raise ValueError("native evidence key lock identity is unsafe") from error
    try:
        _validate_private_file(lock_descriptor, label="key lock")
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        key_path = key_root / _KEY_NAME
        try:
            os.lstat(key_path)
        except FileNotFoundError:
            _publish_new_key(key_root, key_path)
        return _read_key(key_path)
    finally:
        fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        os.close(lock_descriptor)


def open_native_evidence_authentication_ports(
    data_root: str | Path,
) -> NativeEvidenceAuthenticationPorts:
    """Open one service-data-root key without exposing it beyond closures."""

    key = _open_or_create_key(Path(data_root))

    def sign(message: bytes) -> str:
        if not isinstance(message, bytes):
            raise TypeError("native evidence authentication message must be bytes")
        return hmac.digest(key, message, "sha256").hex()

    def verify(message: bytes, tag: str) -> bool:
        if not isinstance(message, bytes) or not isinstance(tag, str):
            return False
        try:
            candidate = bytes.fromhex(tag)
        except ValueError:
            return False
        return len(candidate) == 32 and hmac.compare_digest(
            hmac.digest(key, message, "sha256"), candidate
        )

    return NativeEvidenceAuthenticationPorts(sign=sign, verify=verify)


__all__ = [
    "NativeEvidenceAuthenticationPorts",
    "open_native_evidence_authentication_ports",
]
