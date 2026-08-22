#!/usr/bin/env python3
"""Restore hash-bound private fixture permissions after a Git checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tarfile
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence


EVIDENCE_ARCHIVE = "tests/fixtures/repository-test-evidence-v2.tar.gz"
EVIDENCE_MANIFEST = "tests/fixtures/repository-test-evidence-v2.manifest.json"
EVIDENCE_ARCHIVE_SHA256 = (
    "f3c7d0d950bf74eca943c053760581e491c9354b6981eeec31537a7c891c992f"
)
EVIDENCE_ALLOWED_PREFIXES = (
    "outputs/d18-core-lca-quality-efficiency/",
    "outputs/d18-performance-benchmark/",
    "outputs/lifepo4-two-route-v5-verified/",
    "outputs/real-question-optimization/73101eec6999b27f/question-pack/",
    "outputs/real-question-optimization/73101eec6999b27f/contamination-ledger.json",
    "outputs/real-question-optimization/adaptive-answer-held-out/runtime-data-v4/",
    "outputs/repository-test-fixtures/external-git/",
    "outputs/research-council-benchmark/",
    "outputs/tiangong-authority-following-second-case/",
)

PRIVATE_REPOSITORY_FIXTURES: tuple[tuple[str, str], ...] = (
    (
        "outputs/real-question-optimization/73101eec6999b27f/"
        "blind-execution-v4/package/package-build-receipt.json",
        "180d3653995580b05e843f0ecb687916d1f2d1df0d765aa433e60ca39f211a91",
    ),
    (
        "outputs/real-question-optimization/73101eec6999b27f/"
        "blind-execution-v4/package/package-extraction-ledger.json",
        "950e5de69b81f13413b93f19e27d9ac25b64aa3fb1e8e4cfb5c13a46139bb96b",
    ),
    (
        "outputs/real-question-optimization/73101eec6999b27f/"
        "blind-execution-v4/package/sealed-reference/sealed-reference-answer.json",
        "e666fdcd17101fd5023a771d8badce1d2a48c964587779b1809a8a09d9263b46",
    ),
)


def _validated_relative_path(value: str) -> PurePosixPath:
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError(f"fixture path must be repository-relative: {value}")
    return relative


def _read_fd(fd: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _is_allowed_evidence_path(path: str, prefixes: Sequence[str]) -> bool:
    return any(
        path == prefix if not prefix.endswith("/") else path.startswith(prefix)
        for prefix in prefixes
    )


def _ensure_safe_parent(root: Path, parent: Path) -> None:
    relative = parent.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            if current.is_symlink() or not current.is_dir():
                raise ValueError(f"evidence parent must be a real directory: {relative}")
        else:
            current.mkdir(mode=0o700)


def restore_repository_evidence(
    root: Path,
    *,
    archive_relative: str = EVIDENCE_ARCHIVE,
    expected_sha256: str = EVIDENCE_ARCHIVE_SHA256,
    manifest_relative: str = EVIDENCE_MANIFEST,
    allowed_prefixes: Sequence[str] = EVIDENCE_ALLOWED_PREFIXES,
) -> dict[str, object]:
    """Restore immutable ignored evidence from one hash-bound safe archive."""

    root = Path(root).resolve()
    archive_path = root.joinpath(*_validated_relative_path(archive_relative).parts)
    if archive_path.is_symlink() or not archive_path.is_file():
        raise ValueError("repository evidence archive must be a regular file")
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError("repository evidence archive content drift")
    manifest_path = root.joinpath(*_validated_relative_path(manifest_relative).parts)
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("repository evidence manifest must be a regular file")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("repository evidence manifest is invalid") from error
    if (
        manifest.get("contract") != "repository_test_evidence_manifest/v2"
        or manifest.get("archive") != archive_relative
        or manifest.get("archive_sha256") != expected_sha256
        or not isinstance(manifest.get("files"), list)
    ):
        raise ValueError("repository evidence manifest contract drift")
    expected_files: dict[str, dict[str, object]] = {}
    for row in manifest["files"]:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise ValueError("repository evidence manifest member is invalid")
        relative_text = _validated_relative_path(row["path"]).as_posix()
        if relative_text in expected_files:
            raise ValueError(f"duplicate repository evidence manifest member: {relative_text}")
        if (
            not _is_allowed_evidence_path(relative_text, allowed_prefixes)
            or not isinstance(row.get("bytes"), int)
            or row["bytes"] < 0
            or not isinstance(row.get("mode"), int)
            or row["mode"] < 0
            or row["mode"] > 0o777
            or not isinstance(row.get("sha256"), str)
            or len(row["sha256"]) != 64
        ):
            raise ValueError(f"invalid repository evidence manifest member: {relative_text}")
        expected_files[relative_text] = row
    if (
        manifest.get("file_count") != len(expected_files)
        or manifest.get("total_bytes")
        != sum(int(row["bytes"]) for row in expected_files.values())
    ):
        raise ValueError("repository evidence manifest aggregate drift")

    restored_count = 0
    already_present_count = 0
    seen: set[str] = set()
    with tarfile.open(archive_path, mode="r:gz") as archive:
        for member in archive.getmembers():
            relative = _validated_relative_path(member.name)
            relative_text = relative.as_posix()
            if relative_text in seen:
                raise ValueError(f"duplicate repository evidence member: {relative_text}")
            seen.add(relative_text)
            expected = expected_files.get(relative_text)
            if (
                not member.isfile()
                or expected is None
                or not _is_allowed_evidence_path(relative_text, allowed_prefixes)
                or member.size != expected["bytes"]
                or member.mode & 0o777 != expected["mode"]
            ):
                raise ValueError(f"invalid repository evidence member: {relative_text}")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"unreadable repository evidence member: {relative_text}")
            content = source.read()
            if hashlib.sha256(content).hexdigest() != expected["sha256"]:
                raise ValueError(f"repository evidence member content drift: {relative_text}")
            destination = root.joinpath(*relative.parts)
            if destination.is_symlink():
                raise ValueError(f"repository evidence must not be a symlink: {relative_text}")
            if destination.exists():
                if not destination.is_file() or destination.read_bytes() != content:
                    raise ValueError(f"repository evidence content drift: {relative_text}")
                already_present_count += 1
                continue
            _ensure_safe_parent(root, destination.parent)
            temporary = destination.with_name(
                f".{destination.name}.restore-{os.getpid()}"
            )
            try:
                with temporary.open("xb") as output:
                    output.write(content)
                    output.flush()
                    os.fsync(output.fileno())
                temporary.chmod(member.mode & 0o777 or 0o600)
                os.replace(temporary, destination)
            finally:
                if temporary.exists():
                    temporary.unlink()
            restored_count += 1
    if seen != set(expected_files):
        raise ValueError("repository evidence archive member set drift")

    return {
        "schema": "repository_evidence_restore/v2",
        "archive_sha256": expected_sha256,
        "member_count": len(seen),
        "restored_count": restored_count,
        "already_present_count": already_present_count,
    }


def prepare_private_fixtures(
    root: Path,
    *,
    fixtures: Sequence[tuple[str, str]] = PRIVATE_REPOSITORY_FIXTURES,
) -> dict[str, object]:
    """Verify immutable bytes, then set private fixtures to owner-only mode."""

    root = Path(root).resolve()
    opened: list[tuple[str, int]] = []
    try:
        for relative_text, expected_sha256 in fixtures:
            relative = _validated_relative_path(relative_text)
            path = root.joinpath(*relative.parts)
            try:
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(path, flags)
            except (FileNotFoundError, OSError) as error:
                raise ValueError(
                    f"private fixture must be an existing regular file: {relative_text}"
                ) from error
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
                os.close(fd)
                raise ValueError(
                    f"private fixture must be an existing regular file: {relative_text}"
                )
            observed_sha256 = hashlib.sha256(_read_fd(fd)).hexdigest()
            if observed_sha256 != expected_sha256:
                os.close(fd)
                raise ValueError(f"private fixture content drift: {relative_text}")
            opened.append((relative_text, fd))

        for _, fd in opened:
            os.fchmod(fd, 0o600)

        return {
            "schema": "repository_private_fixture_preparation/v1",
            "prepared_count": len(opened),
            "prepared_paths": [relative for relative, _ in opened],
        }
    finally:
        for _, fd in opened:
            try:
                os.close(fd)
            except OSError:
                pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    receipt = {
        "schema": "repository_test_fixture_bootstrap/v1",
        "evidence": restore_repository_evidence(args.root),
        "private_fixtures": prepare_private_fixtures(args.root),
    }
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
