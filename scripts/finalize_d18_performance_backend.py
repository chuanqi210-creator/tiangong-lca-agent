#!/usr/bin/env python3
"""Freeze, verify, and compile the terminal D-18 performance/service tranche."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_evaluation.contracts import load_performance_program_contract
from lca_research_evaluation.finalizer import (
    build_robustness_report,
    finalize_program,
    freeze_candidate,
    materialize_final_holdout,
    review_final_ledger,
)

FINAL_CORPUS = Path("outputs/d18-performance-benchmark/final-corpus")
FINAL_OUTPUT = Path("outputs/d18-performance-benchmark/final")
SERVICE_FINAL = Path("outputs/d18-service/final")


def _write(path: Path, value: object) -> None:
    content = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def freeze(root: Path) -> dict:
    receipt = freeze_candidate(root, load_performance_program_contract(root))
    if receipt["protected_worktree_clean"] is not True:
        raise ValueError("protected product/evaluation/service worktree is not clean")
    _write(root / FINAL_CORPUS / "control/freeze-receipt.json", receipt)
    return receipt


def materialize(root: Path) -> dict:
    receipt = json.loads(
        (root / FINAL_CORPUS / "control/freeze-receipt.json").read_text()
    )
    return materialize_final_holdout(
        receipt, root=root, output=root / FINAL_CORPUS
    )


def _run_focused_robustness(root: Path) -> dict:
    command = [
        str(root / "runtime/service-python/.venv/bin/python"),
        "-m",
        "unittest",
        "tests.test_model_runtime_policy",
        "tests.test_d18_long_horizon_context",
        "tests.test_d18_tiangong_version_transition",
        "tests.test_d18_prompt_injection_and_tool_poisoning",
        "tests.test_d18_multi_layer_recovery",
        "tests.test_service_auth",
        "tests.test_service_openai_compat",
        "tests.test_service_sse",
        "tests.test_service_attachments",
        "tests.test_service_project_record_boundary",
        "tests.test_service_health",
        "tests.test_service_metrics_redaction",
        "tests.test_service_concurrency",
        "tests.test_service_restart_recovery",
        "tests.test_service_load_contract",
        "-v",
    ]
    result = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    receipt = {
        "contract": "d18_final_robustness_focused_tests/v1",
        "status": "passed" if result.returncode == 0 else "failed",
        "command": command[1:],
        "exit_code": result.returncode,
        "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(result.stderr.encode()).hexdigest(),
        "secret_values_recorded": False,
        "external_writes": 0,
    }
    _write(root / FINAL_OUTPUT / "robustness-focused-tests.json", receipt)
    if result.returncode != 0:
        raise ValueError("final focused robustness tests failed")
    return receipt


def _container_cold_start(root: Path) -> dict:
    docker = shutil.which("docker")
    receipt = {
        "contract": "d18_service_container_cold_start/v1",
        "status": "unavailable",
        "runtime": None,
        "image": None,
        "healthy": False,
        "scientific_calculations": 0,
        "external_writes": 0,
        "secret_values_recorded": False,
        "failure": "container_runtime_absent",
    }
    if docker is None:
        _write(root / SERVICE_FINAL / "container-cold-start.json", receipt)
        return receipt
    image = "d18-tiangong-lca-service:final-candidate"
    build = subprocess.run(
        [docker, "build", "-f", "deploy/Dockerfile", "-t", image, "."],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    if build.returncode != 0:
        receipt.update(
            {
                "runtime": docker,
                "failure": "container_build_failed",
                "build_stdout_sha256": hashlib.sha256(build.stdout.encode()).hexdigest(),
                "build_stderr_sha256": hashlib.sha256(build.stderr.encode()).hexdigest(),
            }
        )
        _write(root / SERVICE_FINAL / "container-cold-start.json", receipt)
        return receipt
    with tempfile.TemporaryDirectory() as temporary:
        temporary_root = Path(temporary)
        token_path = temporary_root / "token"
        token_path.write_text(secrets.token_urlsafe(48))
        token_path.chmod(0o600)
        data = temporary_root / "data"
        data.mkdir()
        name = "d18-final-" + secrets.token_hex(6)
        run = subprocess.run(
            [
                docker,
                "run",
                "-d",
                "--name",
                name,
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                "--mount",
                f"type=bind,src={token_path},dst=/run/secrets/d18_service_token,readonly",
                "--mount",
                f"type=bind,src={data},dst=/var/lib/d18",
                "-e",
                "D18_ATTACHMENT_HOSTS=assets.example.invalid",
                image,
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        healthy = False
        if run.returncode == 0:
            try:
                for _ in range(30):
                    inspect = subprocess.run(
                        [
                            docker,
                            "inspect",
                            "--format",
                            "{{.State.Health.Status}}",
                            name,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        check=False,
                    )
                    if inspect.stdout.strip() == "healthy":
                        healthy = True
                        break
                    if inspect.stdout.strip() == "unhealthy":
                        break
                    time.sleep(1)
            finally:
                subprocess.run(
                    [docker, "rm", "-f", name],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
        receipt.update(
            {
                "status": "passed" if healthy else "failed",
                "runtime": docker,
                "image": image,
                "healthy": healthy,
                "failure": None if healthy else "container_cold_start_failed",
                "build_stdout_sha256": hashlib.sha256(build.stdout.encode()).hexdigest(),
                "build_stderr_sha256": hashlib.sha256(build.stderr.encode()).hexdigest(),
                "run_stdout_sha256": hashlib.sha256(run.stdout.encode()).hexdigest(),
                "run_stderr_sha256": hashlib.sha256(run.stderr.encode()).hexdigest(),
            }
        )
    _write(root / SERVICE_FINAL / "container-cold-start.json", receipt)
    return receipt


def robustness(root: Path) -> dict:
    _run_focused_robustness(root)
    _container_cold_start(root)
    return build_robustness_report(root, root / FINAL_OUTPUT)


def finalize(root: Path) -> dict:
    review_final_ledger(root, root / FINAL_OUTPUT)
    return finalize_program(root, root / FINAL_OUTPUT)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage",
        choices=("freeze", "materialize", "robustness", "finalize"),
        required=True,
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    root = args.root.resolve()
    result = {
        "freeze": freeze,
        "materialize": materialize,
        "robustness": robustness,
        "finalize": finalize,
    }[args.stage](root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") not in {"failed", "rejected"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
