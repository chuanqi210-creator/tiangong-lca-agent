#!/usr/bin/env python3
"""Install an exact TianGong Skills Git object from verified blob bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path


MANIFEST_CONTRACT = "lca_research_kernel.tiangong_skills_git_blob_manifest/v1"
INSTALL_CONTRACT = "lca_research_kernel.tiangong_skills_install/v1"


def _git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_manifest(path: Path) -> tuple[dict[str, str], list[tuple[Path, str]]]:
    metadata: dict[str, str] = {}
    rows: list[tuple[Path, str]] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        if not raw:
            continue
        if raw.startswith("# "):
            key, separator, value = raw[2:].partition("=")
            if not separator:
                raise ValueError("skills manifest metadata is invalid")
            metadata[key] = value
            continue
        path_text, separator, blob_sha = raw.partition("\t")
        if not separator:
            raise ValueError("skills manifest row is invalid")
        relative = Path(path_text)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != path_text
        ):
            raise ValueError("unsafe skills path")
        if (
            len(blob_sha) != 40
            or any(character not in "0123456789abcdef" for character in blob_sha)
        ):
            raise ValueError("skills blob identity is invalid")
        rows.append((relative, blob_sha))
    if metadata.get("contract") != MANIFEST_CONTRACT:
        raise ValueError("skills manifest contract is unsupported")
    if metadata.get("repository") != "tiangong-lca/skills":
        raise ValueError("skills repository identity drifted")
    commit = metadata.get("commit", "")
    if len(commit) != 40 or any(
        character not in "0123456789abcdef" for character in commit
    ):
        raise ValueError("skills commit identity is invalid")
    if not rows or len({path for path, _ in rows}) != len(rows):
        raise ValueError("skills manifest coverage is invalid")
    return metadata, sorted(rows, key=lambda item: item[0].as_posix())


def install_skills(
    *,
    manifest_path: Path,
    source_snapshot: Path,
    output: Path,
) -> dict:
    manifest_path = Path(manifest_path).resolve()
    source_snapshot = Path(source_snapshot).resolve()
    output = Path(output).resolve()
    metadata, rows = _load_manifest(manifest_path)
    if not source_snapshot.is_dir():
        raise ValueError("skills source snapshot is unavailable")
    receipt = {
        "contract": INSTALL_CONTRACT,
        "repository": metadata["repository"],
        "commit": metadata["commit"],
        "manifest_sha256": _sha256(manifest_path),
        "installed_files": len(rows),
        "skill_count": sum(path.name == "SKILL.md" for path, _ in rows),
        "tree_identity_sha256": hashlib.sha256(
            "\n".join(
                f"{path.as_posix()}\t{blob}" for path, blob in rows
            ).encode("utf-8")
        ).hexdigest(),
        "source_mode": "local_snapshot_with_remote_git_blob_equivalence",
    }
    for relative, expected_blob in rows:
        source = source_snapshot / relative
        if not source.is_file():
            raise ValueError(f"skills source file is absent: {relative}")
        if _git_blob_sha1(source.read_bytes()) != expected_blob:
            raise ValueError(f"skills blob identity drifted: {relative}")
    parent = output.parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output.name}.install-", dir=parent
    ) as temporary:
        staged = Path(temporary) / output.name
        for relative, _ in rows:
            target = staged / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_snapshot / relative, target)
        receipt_path = staged / ".d18-install-receipt.json"
        receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n",
            encoding="utf-8",
        )
        if output.exists():
            existing_receipt = output / receipt_path.name
            if (
                existing_receipt.is_file()
                and existing_receipt.read_bytes() == receipt_path.read_bytes()
                and all(
                    (output / relative).is_file()
                    and _git_blob_sha1((output / relative).read_bytes()) == blob
                    for relative, blob in rows
                )
            ):
                return receipt
            backup = parent / f".{output.name}.previous"
            if backup.exists():
                shutil.rmtree(backup)
            output.rename(backup)
            staged.rename(output)
            shutil.rmtree(backup)
        else:
            staged.rename(output)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--source-snapshot", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    result = install_skills(
        manifest_path=arguments.manifest,
        source_snapshot=arguments.source_snapshot,
        output=arguments.output,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
