"""Mechanical validation for the production container's locked inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


_SHA256 = re.compile(r"[0-9a-f]{64}")
_ARCHITECTURES = {"amd64", "arm64"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_container_source_lock(
    *,
    repository_root: str | Path,
    lock_path: str | Path,
    architecture: str,
    package_artifact: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(repository_root).resolve()
    if architecture not in _ARCHITECTURES:
        raise RuntimeError("unsupported container architecture")
    try:
        lock = json.loads(Path(lock_path).read_text(encoding="utf-8"))
        if lock["contract"] != "d18_service_container_source_lock/v1":
            raise RuntimeError("container source lock contract is invalid")
        requirements = lock["requirements_lock"]
        requirements_path = (root / requirements["path"]).resolve()
        if root not in requirements_path.parents:
            raise RuntimeError("requirements lock path escapes repository")
        if _sha256(requirements_path) != requirements["sha256"]:
            raise RuntimeError("requirements lock digest does not match")
        package = lock["system_packages"]["libseccomp2"]
        artifacts = package["artifacts"]
        if set(artifacts) != _ARCHITECTURES:
            raise RuntimeError("container architecture coverage is invalid")
        artifact = artifacts[architecture]
        if package.get("registry") != "https://packages.debian.org/trixie/libseccomp2":
            raise RuntimeError("container package registry is invalid")
        expected_filename = (
            f"libseccomp2_{package['version']}_{artifact['debian_architecture']}.deb"
        )
        if (
            artifact["debian_architecture"] != architecture
            or artifact["filename"] != expected_filename
            or not _SHA256.fullmatch(artifact["sha256"])
            or artifact["registry"] != package["registry"]
        ):
            raise RuntimeError("container package artifact identity is invalid")
        if package_artifact is not None:
            artifact_path = Path(package_artifact)
            if artifact_path.name != artifact["filename"]:
                raise RuntimeError("container package artifact filename does not match")
            if _sha256(artifact_path) != artifact["sha256"]:
                raise RuntimeError("container package artifact digest does not match")
    except RuntimeError:
        raise
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("container source lock is invalid") from error
    return lock


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--architecture", required=True)
    parser.add_argument("--package-artifact", type=Path)
    arguments = parser.parse_args(argv)
    verify_container_source_lock(
        repository_root=arguments.repository_root,
        lock_path=arguments.lock,
        architecture=arguments.architecture,
        package_artifact=arguments.package_artifact,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
