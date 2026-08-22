"""Content-bound release identity shared by frontend, service, and workers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from lca_research_kernel.model_runtime import load_model_runtime_contract
from lca_research_kernel.project_record import EVENT_CONTRACT, VIEW_CONTRACT


LEGACY_MANIFEST_CONTRACT = "d18.runtime_release_manifest/v1"
MANIFEST_CONTRACT = "d18.runtime_release_manifest/v2"
IDENTITY_CONTRACT = "d18.runtime_release_identity/v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_BACKEND_INPUTS = (
    "lca_research_kernel",
    "lca_research_service",
    "scripts/run_frontend_real_pilot_stack.py",
    "runtime/service-python/requirements.lock",
    "Makefile",
)
_FRONTEND_INPUTS = (
    "src",
    "public",
    "scripts",
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "next.config.ts",
    "postcss.config.mjs",
    "tsconfig.json",
)
_TIANGONG_NODE_PACKAGE_LOCK = "runtime/tiangong-node/package-lock.json"
_TIANGONG_SOURCE_LOCK = "runtime/tiangong-runtime-source-lock.json"


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _selected_files(root: Path, inputs: tuple[str, ...]) -> list[Path]:
    selected: list[Path] = []
    for relative in inputs:
        target = root / relative
        if target.is_dir():
            selected.extend(
                path
                for path in target.rglob("*")
                if path.is_file()
                and not path.is_symlink()
                and "__pycache__" not in path.parts
                and path.suffix not in {".pyc", ".pyo"}
            )
        elif target.is_file() and not target.is_symlink():
            selected.append(target)
        else:
            raise ValueError(f"release input is unavailable: {relative}")
    return sorted(selected, key=lambda path: path.relative_to(root).as_posix())


def _tree_sha256(root: Path, inputs: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for path in _selected_files(root, inputs):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _git_commit(root: Path, pathspecs: tuple[str, ...]) -> str:
    try:
        value = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--format=%H", "--", *pathspecs],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError("backend git identity is unavailable") from error
    if _GIT_COMMIT.fullmatch(value) is None:
        raise ValueError("backend git identity is invalid")
    return value


def _frontend_identity(frontend_root: Path) -> dict[str, str]:
    build_id_path = frontend_root / ".next/BUILD_ID"
    recorded_input_path = frontend_root / ".next/d18-input-digest"
    if not build_id_path.is_file() or not recorded_input_path.is_file():
        raise ValueError("frontend production build identity is unavailable")
    build_id = build_id_path.read_text(encoding="utf-8").strip()
    recorded_input = recorded_input_path.read_text(encoding="utf-8").strip()
    actual_input = _tree_sha256(frontend_root, _FRONTEND_INPUTS)
    if not build_id or _SHA256.fullmatch(recorded_input) is None:
        raise ValueError("frontend production build identity is invalid")
    if recorded_input != actual_input:
        raise ValueError("frontend production build does not match its inputs")
    return {
        "build_id": build_id,
        "input_sha256": actual_input,
    }


def _identity_basis(manifest: Mapping[str, Any]) -> dict[str, Any]:
    basis = {
        "contract": "d18.runtime_release_basis/v1",
        "backend": deepcopy(manifest["backend"]),
        "frontend": deepcopy(manifest["frontend"]),
        "contracts": deepcopy(manifest["contracts"]),
        "project_record": deepcopy(manifest["project_record"]),
        "provider": deepcopy(manifest["provider"]),
    }
    if manifest.get("contract") == MANIFEST_CONTRACT:
        basis["capability_runtime"] = deepcopy(manifest["capability_runtime"])
    return basis


def canonical_manifest_sha256(manifest: Mapping[str, Any]) -> str:
    value = deepcopy(dict(manifest))
    value.pop("manifest_sha256", None)
    return _sha256_bytes(_canonical_json(value))


def validate_runtime_release_manifest_evidence(
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a captured manifest without consulting a mutable checkout."""

    if not isinstance(manifest, Mapping):
        raise ValueError("runtime release manifest evidence is invalid")
    value = deepcopy(dict(manifest))
    contract = value.get("contract")
    expected_top_fields = {
        "contract",
        "release_id",
        "manifest_sha256",
        "backend",
        "frontend",
        "contracts",
        "project_record",
        "provider",
        "runtime",
    }
    if contract == MANIFEST_CONTRACT:
        expected_top_fields.add("capability_runtime")
    if set(value) != expected_top_fields or contract not in {
        LEGACY_MANIFEST_CONTRACT,
        MANIFEST_CONTRACT,
    }:
        raise ValueError("runtime release manifest evidence is invalid")
    expected_fields = {
        "backend": {"git_commit", "source_tree_sha256"},
        "frontend": {"build_id", "input_sha256", "source_commit"},
        "contracts": {"openapi_sha256", "compatibility_contract_sha256"},
        "project_record": {"schema_generation", "event_contract", "view_contract"},
        "provider": {
            "provider_catalog_sha256",
            "provider_catalog_file_sha256",
            "policy_sha256",
            "source_lock_sha256",
        },
        "runtime": {"deployed_at", "instance_id"},
    }
    if contract == MANIFEST_CONTRACT:
        expected_fields["capability_runtime"] = {
            "tiangong_node_package_lock_sha256",
            "tiangong_source_lock_sha256",
        }
    for section, fields in expected_fields.items():
        row = value.get(section)
        if not isinstance(row, Mapping) or set(row) != fields:
            raise ValueError(f"runtime release {section} evidence is invalid")
    backend = value["backend"]
    frontend = value["frontend"]
    contracts = value["contracts"]
    provider = value["provider"]
    project_record = value["project_record"]
    runtime = value["runtime"]
    hashes = [
        backend["source_tree_sha256"],
        frontend["input_sha256"],
        contracts["openapi_sha256"],
        contracts["compatibility_contract_sha256"],
        provider["provider_catalog_sha256"],
        provider["provider_catalog_file_sha256"],
        provider["policy_sha256"],
        provider["source_lock_sha256"],
    ]
    if contract == MANIFEST_CONTRACT:
        capability_runtime = value["capability_runtime"]
        hashes.extend(
            (
                capability_runtime["tiangong_node_package_lock_sha256"],
                capability_runtime["tiangong_source_lock_sha256"],
            )
        )
    if (
        _GIT_COMMIT.fullmatch(str(backend["git_commit"])) is None
        or _GIT_COMMIT.fullmatch(str(frontend["source_commit"])) is None
        or any(_SHA256.fullmatch(str(item)) is None for item in hashes)
        or not isinstance(frontend["build_id"], str)
        or not frontend["build_id"]
        or isinstance(project_record["schema_generation"], bool)
        or not isinstance(project_record["schema_generation"], int)
        or project_record["schema_generation"] < 1
        or not all(
            isinstance(project_record[key], str) and project_record[key]
            for key in ("event_contract", "view_contract")
        )
        or not isinstance(runtime["deployed_at"], str)
        or not runtime["deployed_at"].endswith("Z")
        or not isinstance(runtime["instance_id"], str)
        or not runtime["instance_id"]
    ):
        raise ValueError("runtime release manifest evidence is invalid")
    expected_release_id = _sha256_bytes(_canonical_json(_identity_basis(value)))
    if value.get("release_id") != expected_release_id:
        raise ValueError("runtime release evidence identity drifted")
    if value.get("manifest_sha256") != canonical_manifest_sha256(value):
        raise ValueError("runtime release evidence hash drifted")
    runtime_release_binding(value)
    return value


def build_runtime_release_manifest(
    repository_root: str | Path,
    *,
    frontend_root: str | Path,
    deployed_at: str,
    runtime_instance_id: str,
) -> dict[str, Any]:
    root = Path(repository_root).resolve()
    frontend = Path(frontend_root).resolve()
    if not isinstance(deployed_at, str) or not deployed_at.endswith("Z"):
        raise ValueError("release deployment time is invalid")
    if (
        not isinstance(runtime_instance_id, str)
        or not runtime_instance_id
        or len(runtime_instance_id) > 128
        or re.fullmatch(r"[A-Za-z0-9._-]+", runtime_instance_id) is None
    ):
        raise ValueError("runtime instance identity is invalid")
    openapi = root / "outputs/d18-service/integration/openapi-v1.json"
    compatibility = (
        root / "lca_research_kernel/contracts/qingxiaoda-openai-compat-v1.json"
    )
    if not openapi.is_file() or not compatibility.is_file():
        raise ValueError("release protocol contract is unavailable")
    tiangong_node_package_lock = root / _TIANGONG_NODE_PACKAGE_LOCK
    tiangong_source_lock = root / _TIANGONG_SOURCE_LOCK
    if not tiangong_node_package_lock.is_file() or not tiangong_source_lock.is_file():
        raise ValueError("TianGong capability runtime lock is unavailable")
    model_runtime = load_model_runtime_contract(root)
    manifest: dict[str, Any] = {
        "contract": MANIFEST_CONTRACT,
        "release_id": "",
        "backend": {
            "git_commit": _git_commit(root, _BACKEND_INPUTS),
            "source_tree_sha256": _tree_sha256(root, _BACKEND_INPUTS),
        },
        "frontend": {
            **_frontend_identity(frontend),
            "source_commit": _git_commit(root, ("apps/web",)),
        },
        "contracts": {
            "openapi_sha256": _sha256_file(openapi),
            "compatibility_contract_sha256": _sha256_file(compatibility),
        },
        "project_record": {
            "schema_generation": 1,
            "event_contract": EVENT_CONTRACT,
            "view_contract": VIEW_CONTRACT,
        },
        "provider": {
            "provider_catalog_sha256": model_runtime["provider_catalog_sha256"],
            "provider_catalog_file_sha256": model_runtime[
                "provider_catalog_file_sha256"
            ],
            "policy_sha256": model_runtime["policy_sha256"],
            "source_lock_sha256": model_runtime["source_lock_sha256"],
        },
        "capability_runtime": {
            "tiangong_node_package_lock_sha256": _sha256_file(
                tiangong_node_package_lock
            ),
            "tiangong_source_lock_sha256": _sha256_file(tiangong_source_lock),
        },
        "runtime": {
            "deployed_at": deployed_at,
            "instance_id": runtime_instance_id,
        },
    }
    manifest["release_id"] = _sha256_bytes(_canonical_json(_identity_basis(manifest)))
    manifest["manifest_sha256"] = canonical_manifest_sha256(manifest)
    return manifest


def validate_runtime_release_manifest(
    manifest: Mapping[str, Any],
    *,
    repository_root: str | Path,
    frontend_root: str | Path,
) -> dict[str, Any]:
    if not isinstance(manifest, Mapping):
        raise ValueError("runtime release manifest is invalid")
    value = deepcopy(dict(manifest))
    if value.get("contract") != MANIFEST_CONTRACT:
        raise ValueError("runtime release manifest contract is invalid")
    release_id = value.get("release_id")
    manifest_sha256 = value.get("manifest_sha256")
    if not isinstance(release_id, str) or _SHA256.fullmatch(release_id) is None:
        raise ValueError("runtime release identity is invalid")
    if (
        not isinstance(manifest_sha256, str)
        or _SHA256.fullmatch(manifest_sha256) is None
        or canonical_manifest_sha256(value) != manifest_sha256
    ):
        raise ValueError("runtime release manifest hash drifted")
    runtime = value.get("runtime")
    if not isinstance(runtime, Mapping):
        raise ValueError("runtime release deployment identity is invalid")
    expected = build_runtime_release_manifest(
        repository_root,
        frontend_root=frontend_root,
        deployed_at=str(runtime.get("deployed_at", "")),
        runtime_instance_id=str(runtime.get("instance_id", "")),
    )
    for section in (
        "backend",
        "frontend",
        "contracts",
        "project_record",
        "provider",
        "capability_runtime",
    ):
        if value.get(section) != expected[section]:
            raise ValueError(f"runtime release {section} identity drifted")
    if value != expected:
        raise ValueError("runtime release identity drifted")
    return value


def load_runtime_release_manifest(
    path: str | Path,
    *,
    repository_root: str | Path,
    frontend_root: str | Path,
) -> dict[str, Any]:
    target = Path(path)
    if not target.is_file() or target.is_symlink():
        raise ValueError("runtime release manifest is unavailable")
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("runtime release manifest is invalid") from error
    return validate_runtime_release_manifest(
        value,
        repository_root=repository_root,
        frontend_root=frontend_root,
    )


def write_runtime_release_manifest(path: str | Path, manifest: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = _canonical_json(dict(manifest))
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        written = 0
        while written < len(payload):
            written += os.write(descriptor, payload[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, target)
    target.chmod(0o600)


def runtime_release_binding(manifest: Mapping[str, Any]) -> dict[str, str]:
    backend = manifest.get("backend")
    frontend = manifest.get("frontend")
    if not isinstance(backend, Mapping) or not isinstance(frontend, Mapping):
        raise ValueError("runtime release manifest is incomplete")
    value = {
        "contract": IDENTITY_CONTRACT,
        "release_id": manifest.get("release_id"),
        "manifest_sha256": manifest.get("manifest_sha256"),
        "backend_commit": backend.get("git_commit"),
        "frontend_build_id": frontend.get("build_id"),
    }
    if (
        any(not isinstance(item, str) or not item for item in value.values())
        or _SHA256.fullmatch(value["release_id"]) is None
        or _SHA256.fullmatch(value["manifest_sha256"]) is None
        or _GIT_COMMIT.fullmatch(value["backend_commit"]) is None
    ):
        raise ValueError("runtime release identity is invalid")
    return value


def require_request_release(
    request: Mapping[str, Any], manifest: Mapping[str, Any]
) -> dict[str, str]:
    observed = request.get("runtime_release")
    expected = runtime_release_binding(manifest)
    if not isinstance(observed, Mapping) or dict(observed) != expected:
        raise ValueError("job runtime release does not match the worker release")
    return expected
