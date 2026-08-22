#!/usr/bin/env python3
"""Create and verify the exact local official-SDK model runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _probe_python_version(command: str) -> tuple[int, int, int]:
    executable = shutil.which(command) if not Path(command).is_absolute() else command
    if not executable:
        raise FileNotFoundError(command)
    result = subprocess.run(
        [
            str(executable),
            "-c",
            (
                "import json,sys;"
                "print(json.dumps(list(sys.version_info[:3])))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    value = json.loads(result.stdout)
    if (
        not isinstance(value, list)
        or len(value) != 3
        or any(not isinstance(item, int) for item in value)
    ):
        raise RuntimeError("Python version probe returned an invalid result")
    return tuple(value)  # type: ignore[return-value]


def select_runtime_python(
    candidates: Iterable[str],
    *,
    version_probe: Callable[[str], tuple[int, int, int]] = _probe_python_version,
) -> str:
    """Select the first available exact-enough interpreter for google-genai."""

    for command in candidates:
        try:
            version = version_probe(command)
        except (FileNotFoundError, OSError, subprocess.SubprocessError, ValueError):
            continue
        if version >= (3, 10, 0):
            return command
    raise RuntimeError("Python 3.10 or newer is required for the model runtime")


def bootstrap(root: Path, *, receipt_path: Path) -> dict:
    root = root.resolve()
    sys.path.insert(0, str(root))
    from lca_research_kernel.model_runtime import load_model_runtime_contract

    contract = load_model_runtime_contract(root)
    runtime_root = root / "runtime/model-python"
    environment = runtime_root / ".venv"
    requirements = root / contract["requirements_lock"]["path"]
    python_contract = contract.get("python")
    if not isinstance(python_contract, dict):
        raise RuntimeError("model runtime Python contract is missing")
    candidates = python_contract.get("bootstrap_candidates")
    if not isinstance(candidates, list) or not all(
        isinstance(item, str) and item for item in candidates
    ):
        raise RuntimeError("model runtime Python candidates are invalid")
    creator = select_runtime_python(candidates)
    subprocess.run(
        [creator, "-m", "venv", "--clear", str(environment)],
        check=True,
        cwd=root,
    )
    python = environment / "bin/python"
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--require-hashes",
            "--no-deps",
            "-r",
            str(requirements),
        ],
        check=True,
        cwd=root,
    )
    verification = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import json, importlib.metadata as m; "
                "import openai, anthropic, zai; from google import genai; "
                "print(json.dumps({'openai':m.version('openai'),"
                "'google-genai':m.version('google-genai'),"
                "'anthropic':m.version('anthropic'),"
                "'zai-sdk':m.version('zai-sdk')}))"
            ),
        ],
        check=True,
        cwd=root,
        capture_output=True,
        text=True,
    )
    versions = json.loads(verification.stdout)
    adopted = {
        key: row["version"]
        for key, row in contract["sdk_sources"].items()
    }
    if versions != adopted:
        raise RuntimeError("installed model SDK versions do not match source lock")
    receipt = {
        "contract": "lca_research_kernel.model_runtime_health/v1",
        "status": "passed",
        "runtime_kind": "isolated_python_venv",
        "python": str(python.relative_to(root)),
        "python_requires": contract["python"]["requires"],
        "requirements_lock_sha256": _sha256(requirements),
        "source_lock_sha256": contract["source_lock_sha256"],
        "policy_sha256": contract["policy_sha256"],
        "installed_versions": versions,
        "credentials_read": False,
        "provider_calls": 0,
        "scientific_calculations": 0,
        "external_writes": 0,
    }
    _write_json(receipt_path, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--receipt",
        type=Path,
        default=Path("outputs/d18-production-runtime-core/model-sdk-health.json"),
    )
    args = parser.parse_args()
    receipt = args.receipt
    if not receipt.is_absolute():
        receipt = args.root / receipt
    value = bootstrap(args.root, receipt_path=receipt)
    print(
        json.dumps(
            {
                "status": value["status"],
                "installed_versions": value["installed_versions"],
                "provider_calls": value["provider_calls"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
