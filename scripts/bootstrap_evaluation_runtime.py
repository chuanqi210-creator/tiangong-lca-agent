#!/usr/bin/env python3
"""Bootstrap and verify the isolated, hash-locked evaluation runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK_RELATIVE = Path("runtime/evaluation-python/source-lock.json")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_lock(root: Path) -> dict:
    value = json.loads((root / LOCK_RELATIVE).read_text(encoding="utf-8"))
    if value.get("contract") != (
        "lca_research_evaluation.evaluation_runtime_source_lock/v1"
    ):
        raise ValueError("evaluation runtime source lock contract is invalid")
    requirements = value["requirements"]
    for key, digest_key in (
        ("input_path", "input_sha256"),
        ("lock_path", "lock_sha256"),
    ):
        path = root / requirements[key]
        if not path.is_file() or _sha256(path) != requirements[digest_key]:
            raise ValueError(f"evaluation runtime {key} drifted")
    model_lock = value["model_runtime_source_lock"]
    if _sha256(root / model_lock["path"]) != model_lock["sha256"]:
        raise ValueError("model runtime source lock drifted")
    return value


def compile_bootstrap_commands(
    root: Path,
    venv: Path,
    *,
    python: Path,
) -> list[list[str]]:
    root = root.resolve()
    venv = venv.resolve()
    lock = root / "runtime/evaluation-python/requirements.lock"
    return [
        [str(python), "-m", "venv", str(venv)],
        [
            str(venv / "bin/python"),
            "-m",
            "pip",
            "install",
            "--require-hashes",
            "--no-deps",
            "-r",
            str(lock),
        ],
    ]


def verify_runtime(root: Path, python: Path) -> dict:
    root = root.resolve()
    source_lock = _load_lock(root)
    command = [
        str(python),
        "-c",
        (
            "import json,sys,importlib.metadata as m;"
            "print(json.dumps({'python':list(sys.version_info[:3]),"
            "'openai':m.version('openai'),"
            "'openai-agents':m.version('openai-agents'),"
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
        env={"PATH": os.environ.get("PATH", ""), "PYTHONNOUSERSITE": "1"},
    )
    if completed.returncode != 0:
        raise ValueError("evaluation runtime import/version verification failed")
    observed = json.loads(completed.stdout)
    expected = {
        "openai": source_lock["packages"]["openai"]["version"],
        "openai-agents": source_lock["packages"]["openai-agents"]["version"],
        "google-genai": "2.13.0",
        "anthropic": "0.118.0",
        "zai-sdk": source_lock["packages"]["zai-sdk"]["version"],
    }
    if any(observed.get(name) != version for name, version in expected.items()):
        raise ValueError("evaluation runtime package version drifted")
    if observed["python"][:2] != [3, 11]:
        raise ValueError("evaluation runtime Python minor drifted")
    report = {
        "contract": "lca_research_evaluation.evaluation_runtime_health/v1",
        "status": "executable_verified",
        "python_minor": "3.11",
        "python_version": ".".join(str(item) for item in observed["python"]),
        "packages": expected,
        "source_lock_sha256": _sha256(root / LOCK_RELATIVE),
        "requirements_lock_sha256": source_lock["requirements"]["lock_sha256"],
        "network_calls_during_health_check": 0,
        "external_writes": 0,
    }
    report["health_sha256"] = hashlib.sha256(
        json.dumps(
            report, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return report


def _atomic_write(path: Path, value: object) -> None:
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


def bootstrap(root: Path, receipt: Path, *, python: Path) -> dict:
    root = root.resolve()
    _load_lock(root)
    venv = root / "runtime/evaluation-python/.venv"
    for command in compile_bootstrap_commands(root, venv, python=python):
        completed = subprocess.run(
            command,
            cwd=root,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300,
            env={"PATH": os.environ.get("PATH", ""), "PYTHONNOUSERSITE": "1"},
        )
        if completed.returncode != 0:
            raise ValueError(
                "evaluation runtime bootstrap failed: "
                + completed.stderr[-1000:]
            )
    report = verify_runtime(root, venv / "bin/python")
    _atomic_write(receipt, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=Path("python3.11"))
    args = parser.parse_args()
    report = bootstrap(
        args.root.resolve(),
        args.receipt.resolve(),
        python=args.python,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
