#!/usr/bin/env python3
"""Bootstrap and verify the exact D-18 service runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
SOURCE_LOCK = Path("runtime/service-python/source-lock.json")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unavailable or invalid") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain an object")
    return value


def load_service_runtime_contract(root: Path) -> dict:
    root = Path(root).resolve()
    source_path = root / SOURCE_LOCK
    contract = _read_object(source_path, "service runtime source lock")
    if contract.get("contract") != "lca_research_service.runtime_source_lock/v1":
        raise ValueError("service runtime source lock contract is invalid")
    requirements = contract.get("requirements")
    if not isinstance(requirements, dict):
        raise ValueError("service runtime requirements contract is absent")
    for key, digest_key in (
        ("input_path", "input_sha256"),
        ("lock_path", "lock_sha256"),
    ):
        relative = Path(str(requirements.get(key, "")))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("service runtime requirements path is unsafe")
        path = root / relative
        if (
            not path.is_file()
            or _sha256(path) != requirements.get(digest_key)
        ):
            raise ValueError(f"service runtime {key} drifted")
    requirement_text = (root / requirements["input_path"]).read_text(
        encoding="utf-8"
    )
    for raw_line in requirement_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-r "):
            continue
        if "==" not in line or line.rsplit("==", 1)[1].casefold() in {
            "latest",
            "head",
            "main",
            "master",
            "*",
        }:
            raise ValueError("service runtime contains a floating requirement")
    lock_text = (root / requirements["lock_path"]).read_text(encoding="utf-8")
    rows = re.findall(r"(?m)^([a-zA-Z0-9_.-]+)==[^\s\\]+ \\$", lock_text)
    if len(rows) != requirements.get("resolved_package_count"):
        raise ValueError("service runtime resolved package count drifted")
    if lock_text.count("--hash=sha256:") < len(rows):
        raise ValueError("service runtime requirements lack hashes")
    model_ref = contract.get("model_runtime_source_lock")
    if not isinstance(model_ref, dict):
        raise ValueError("model runtime source-lock reference is absent")
    model_path = root / str(model_ref.get("path", ""))
    if not model_path.is_file() or _sha256(model_path) != model_ref.get("sha256"):
        raise ValueError("model runtime source lock drifted")
    model_lock = _read_object(model_path, "model runtime source lock")
    expected_model_versions = {
        name: row["version"]
        for name, row in model_lock.get("sdk_sources", {}).items()
        if isinstance(row, dict) and isinstance(row.get("version"), str)
    }
    if contract.get("model_sdk_versions") != expected_model_versions:
        raise ValueError("service and model runtime SDK identities differ")
    python = contract.get("python")
    if (
        not isinstance(python, dict)
        or python.get("minor") != "3.11"
        or python.get("requires") != ">=3.10"
    ):
        raise ValueError("service runtime Python contract is invalid")
    packages = contract.get("packages")
    if not isinstance(packages, dict) or set(packages) != {
        "fastapi",
        "uvicorn",
        "httpx",
    }:
        raise ValueError("service runtime top-level package lock is incomplete")
    for name, row in packages.items():
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("version"), str)
            or not isinstance(row.get("license"), str)
            or not str(row.get("official_registry", "")).startswith(
                f"https://pypi.org/project/{name}/"
            )
            or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("wheel_sha256", "")))
        ):
            raise ValueError("service runtime package source lock is invalid")
    parser_packages = contract.get("parser_packages")
    expected_parsers = {
        "pypdf": "pypdf",
        "Pillow": "pillow",
        "python-docx": "python-docx",
        "python-pptx": "python-pptx",
        "openpyxl": "openpyxl",
    }
    if not isinstance(parser_packages, dict) or set(parser_packages) != set(expected_parsers):
        raise ValueError("service runtime parser package source lock is incomplete")
    input_versions = {}
    for line in requirement_text.splitlines():
        if "==" in line and not line.lstrip().startswith("-"):
            name, version = line.strip().split("==", 1)
            input_versions[name.casefold()] = version
    lock_blocks = {}
    for match in re.finditer(
        r"(?ms)^([a-zA-Z0-9_.-]+)==([^\s\\]+) \\\n(?P<body>(?:    --hash=sha256:[0-9a-f]{64}(?: \\\n|\n))+)",
        lock_text,
    ):
        lock_blocks[(match.group(1).casefold(), match.group(2))] = set(
            re.findall(r"--hash=sha256:([0-9a-f]{64})", match.group("body"))
        )
    for name, normalized in expected_parsers.items():
        row = parser_packages[name]
        wheel_filename = str(row.get("wheel_filename", "")) if isinstance(row, dict) else ""
        version = str(row.get("version", "")) if isinstance(row, dict) else ""
        normalized_distribution = normalized.replace("-", "_").casefold()
        if (
            not isinstance(row, dict)
            or set(row) != {"version", "license", "official_registry", "wheel_filename", "wheel_sha256"}
            or version != input_versions.get(name.casefold())
            or row.get("official_registry") != f"https://pypi.org/project/{name}/{version}/"
            or re.fullmatch(
                rf"{re.escape(normalized_distribution)}-{re.escape(version)}"
                r"(?:-[0-9][a-z0-9_.]*)?-[a-z0-9_.]+-[a-z0-9_.]+-[a-z0-9_.]+\.whl",
                wheel_filename.casefold(),
            ) is None
            or row.get("wheel_sha256") not in lock_blocks.get((name.casefold(), version), set())
            or not isinstance(row.get("license"), str)
            or not row["license"]
        ):
            raise ValueError("service runtime parser package source lock is invalid")
    return contract


def compile_bootstrap_commands(
    root: Path,
    venv: Path,
    *,
    python: Path,
) -> list[list[str]]:
    root = Path(root).resolve()
    venv = Path(venv).resolve()
    lock = root / "runtime/service-python/requirements.lock"
    return [
        [str(python), "-m", "venv", "--clear", str(venv)],
        [
            str(venv / "bin/python"),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--require-hashes",
            "--no-deps",
            "-r",
            str(lock),
        ],
    ]


def verify_service_runtime(root: Path, python: Path) -> dict:
    root = Path(root).resolve()
    contract = load_service_runtime_contract(root)
    command = [
        str(python),
        "-c",
        (
            "import json,sys,importlib.metadata as m;"
            "import fastapi,uvicorn,httpx,openai,anthropic,zai;"
            "from google import genai;"
            "print(json.dumps({'python':list(sys.version_info[:3]),"
            "'fastapi':m.version('fastapi'),'uvicorn':m.version('uvicorn'),"
            "'httpx':m.version('httpx'),'openai':m.version('openai'),"
            "'google-genai':m.version('google-genai'),"
            "'anthropic':m.version('anthropic'),"
            "'zai-sdk':m.version('zai-sdk')},sort_keys=True))"
        ),
    ]
    completed = subprocess.run(
        command,
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONNOUSERSITE": "1",
        },
    )
    if completed.returncode != 0:
        raise ValueError("service runtime import/version verification failed")
    observed = json.loads(completed.stdout)
    expected = {
        **{
            name: row["version"]
            for name, row in contract["packages"].items()
        },
        **contract["model_sdk_versions"],
    }
    if any(observed.get(name) != version for name, version in expected.items()):
        raise ValueError("service runtime package version drifted")
    if observed["python"][:2] != [3, 11]:
        raise ValueError("service runtime Python minor drifted")
    report = {
        "contract": "lca_research_service.runtime_health/v1",
        "status": "executable_verified",
        "python_minor": "3.11",
        "python_version": ".".join(str(item) for item in observed["python"]),
        "packages": expected,
        "source_lock_sha256": _sha256(root / SOURCE_LOCK),
        "requirements_lock_sha256": contract["requirements"]["lock_sha256"],
        "model_runtime_source_lock_sha256": contract[
            "model_runtime_source_lock"
        ]["sha256"],
        "network_calls_during_health_check": 0,
        "credentials_read": False,
        "scientific_calculations": 0,
        "external_writes": 0,
    }
    report["health_sha256"] = hashlib.sha256(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return report


def _atomic(path: Path, value: object) -> None:
    content = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _select_python(candidates: Sequence[str]) -> Path:
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if not resolved:
            continue
        completed = subprocess.run(
            [resolved, "-c", "import sys;print('.'.join(map(str,sys.version_info[:3])))"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if completed.returncode != 0:
            continue
        version = tuple(int(item) for item in completed.stdout.strip().split("."))
        if version >= (3, 10, 0):
            return Path(resolved)
    raise ValueError("Python 3.10 or newer is required for the service runtime")


def resolve_receipt_path(root: Path, receipt: Path) -> Path:
    root = Path(root).resolve()
    supplied = Path(receipt)
    if supplied.is_absolute():
        return supplied.resolve()
    resolved = (root / supplied).resolve()
    resolved.relative_to(root)
    return resolved


def bootstrap_service_runtime(root: Path, receipt_path: Path) -> dict:
    root = Path(root).resolve()
    contract = load_service_runtime_contract(root)
    candidates = contract["python"].get("bootstrap_candidates")
    if not isinstance(candidates, list) or not all(
        isinstance(item, str) and item for item in candidates
    ):
        raise ValueError("service runtime Python candidates are invalid")
    python = _select_python(candidates)
    environment = root / "runtime/service-python/.venv"
    for command in compile_bootstrap_commands(
        root, environment, python=python
    ):
        completed = subprocess.run(
            command,
            cwd=root,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=600,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONNOUSERSITE": "1",
            },
        )
        if completed.returncode != 0:
            raise ValueError(
                "service runtime bootstrap failed: " + completed.stderr[-1000:]
            )
    report = verify_service_runtime(root, environment / "bin/python")
    _atomic(Path(receipt_path), report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    receipt = resolve_receipt_path(root, args.receipt)
    report = bootstrap_service_runtime(root, receipt)
    print(
        json.dumps(
            {
                "status": report["status"],
                "python_minor": report["python_minor"],
                "packages": report["packages"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
