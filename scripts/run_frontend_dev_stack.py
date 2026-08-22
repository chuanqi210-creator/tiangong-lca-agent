#!/usr/bin/env python3
"""Run the no-effect D-18 backend and the integrated frontend without secrets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


CONTRACT = "lca_research_agent.frontend_dev_stack/v1"
LOCAL_TOKEN = "integration-token"
SAFE_ENVIRONMENT_NAMES = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TZ")
FRONTEND_BUILD_INPUTS = (
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


def _port(value: str) -> int:
    port = int(value)
    if not 1024 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1024 and 65535")
    return port


def _plan(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.root).resolve()
    data_root = Path(args.data_root).resolve()
    if not data_root.is_relative_to(root) and not data_root.is_relative_to(
        Path("/tmp").resolve()
    ):
        raise ValueError("data root must be under the repository or /tmp")
    return {
        "contract": CONTRACT,
        "mode": "no_effect_development",
        "backend": {
            "module": "lca_research_service.mock_server",
            "url": f"http://{args.host}:{args.backend_port}",
            "data_root": str(data_root),
            "auto_worker": True,
            "effects": {
                "model_calls": 0,
                "tool_calls": 0,
                "external_writes": 0,
                "scientific_results": 0,
            },
        },
        "frontend": {
            "root": str(root / "apps/web"),
            "url": f"http://{args.host}:{args.frontend_port}",
            "server_environment_names": [
                "D18_AUTH_MODE",
                "D18_BASE_URL",
                "D18_TOKEN",
            ],
        },
    }


def _terminate(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def _safe_environment(root: Path) -> dict[str, str]:
    environment = {
        name: os.environ[name]
        for name in SAFE_ENVIRONMENT_NAMES
        if name in os.environ
    }
    environment["PYTHONPATH"] = str(root)
    return environment


def frontend_build_is_fresh(frontend_root: Path) -> bool:
    """Return whether the checked production frontend reflects its inputs."""

    frontend_root = Path(frontend_root)
    build_identity = frontend_root / ".next/BUILD_ID"
    digest_path = frontend_root / ".next/d18-input-digest"
    if not build_identity.is_file() or not digest_path.is_file():
        return False
    digest = hashlib.sha256()
    paths: list[Path] = []
    for relative in FRONTEND_BUILD_INPUTS:
        candidate = frontend_root / relative
        if not candidate.exists():
            return False
        paths.extend(
            path for path in (
                candidate.rglob("*") if candidate.is_dir() else (candidate,)
            )
            if path.is_file()
        )
    for path in sorted(paths, key=lambda value: value.relative_to(frontend_root).as_posix()):
        digest.update(path.relative_to(frontend_root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest_path.read_text(encoding="utf-8").strip() == digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost"))
    parser.add_argument("--backend-port", type=_port, default=8080)
    parser.add_argument("--frontend-port", type=_port, default=3000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    plan = _plan(args)
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
        return 0

    root = Path(args.root).resolve()
    frontend_root = root / "apps/web"
    pnpm = shutil.which("pnpm")
    if pnpm is None or not frontend_build_is_fresh(frontend_root):
        print("frontend build is unavailable or stale; run make verify-frontend first", file=sys.stderr)
        return 2
    environment = _safe_environment(root)
    backend = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "lca_research_service.mock_server",
            "--data-root",
            str(Path(args.data_root).resolve()),
            "--host",
            args.host,
            "--port",
            str(args.backend_port),
        ],
        cwd=root,
        env=environment,
        stdin=subprocess.DEVNULL,
    )
    frontend_environment = dict(environment)
    frontend_environment["D18_AUTH_MODE"] = "local_static"
    frontend_environment["D18_BASE_URL"] = f"http://{args.host}:{args.backend_port}"
    frontend_environment["D18_TOKEN"] = LOCAL_TOKEN
    frontend = subprocess.Popen(
        [
            pnpm,
            "start",
            "--hostname",
            args.host,
            "--port",
            str(args.frontend_port),
        ],
        cwd=frontend_root,
        env=frontend_environment,
        stdin=subprocess.DEVNULL,
    )
    processes = [backend, frontend]

    def stop(*_: object) -> None:
        _terminate(processes)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        while all(process.poll() is None for process in processes):
            time.sleep(0.2)
    finally:
        _terminate(processes)
    return max(process.returncode or 0 for process in processes)


if __name__ == "__main__":
    raise SystemExit(main())
