#!/usr/bin/env python3
"""Run the real D-18 backend and built frontend with ephemeral signed identity."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lca_research_service.runtime_factory import required_runtime_environment_names
from lca_research_service.release_manifest import (
    build_runtime_release_manifest,
    load_runtime_release_manifest,
    runtime_release_binding,
    write_runtime_release_manifest,
)
from lca_research_kernel.capability_catalog import CapabilityCatalog
from lca_research_service.tiangong_native_adapter import (
    TianGongTidasSdkContractAdapter,
)


CONTRACT = "lca_research_agent.frontend_real_pilot_stack/v1"
SAFE_ENVIRONMENT_NAMES = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TZ")
IGNORED_ENVIRONMENT_NAMES = {"D18_MODEL_ROUTE_ORDER"}
ENVIRONMENT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
PROJECT_NAMESPACE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
DEFAULT_QUALIFICATION = Path(
    "outputs/real-question-optimization/73101eec6999b27f/minimum-real-path/"
    "gemini-provider-capacity-q5-v2.json"
)
FRONTEND_BUILD_INPUTS = (
    "src",
    "public",
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


def _private_file(path: Path) -> None:
    if not path.is_file():
        raise ValueError("credential file is unavailable")
    metadata = path.stat()
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError("credential file permissions are unsafe")


def _literal_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    if not value:
        raise ValueError("credential binding is empty")
    return value


def load_credential_environment(
    path: Path,
    allowed_names: Iterable[str],
) -> dict[str, str]:
    """Parse an inert 0600 env file without shell expansion or value logging."""

    path = Path(path).expanduser().resolve()
    _private_file(path)
    allowed = set(allowed_names)
    if not allowed or any(ENVIRONMENT_NAME.fullmatch(name) is None for name in allowed):
        raise ValueError("credential allowlist is invalid")
    loaded: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        if "=" not in line:
            raise ValueError("credential file contains an invalid line")
        name, raw_value = line.split("=", 1)
        name = name.strip()
        if name in IGNORED_ENVIRONMENT_NAMES:
            continue
        if name not in allowed:
            raise ValueError("credential file contains an undeclared binding")
        if name in loaded:
            raise ValueError("credential file contains a duplicate binding")
        loaded[name] = _literal_value(raw_value)
    missing = sorted(allowed - set(loaded))
    if "GEMINI_API_KEY" in missing or "GEMINI_MODEL_ID" in missing:
        raise ValueError("Gemini credential or model binding is absent")
    if missing:
        raise ValueError("required model runtime binding is absent")
    return loaded


def _write_private_json(path: Path, value: object) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_private_text(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, value.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class PilotIdentity:
    directory: Path
    namespace: str
    session_id: str
    grants_file: Path
    registry_file: Path
    signing_key_file: Path
    expires_at: str

    @property
    def secret_paths(self) -> tuple[Path, ...]:
        return self.grants_file, self.registry_file, self.signing_key_file

    @classmethod
    def create(
        cls,
        data_root: Path,
        *,
        namespace: str,
        now: datetime | None = None,
        ttl_seconds: int = 4 * 60 * 60,
    ) -> "PilotIdentity":
        if PROJECT_NAMESPACE.fullmatch(namespace) is None:
            raise ValueError("project namespace is invalid")
        if not 60 <= ttl_seconds <= 24 * 60 * 60:
            raise ValueError("session lifetime is invalid")
        identity_dir = (
            Path(data_root).resolve()
            / "control"
            / "ephemeral-identity"
            / secrets.token_hex(12)
        )
        identity_dir.mkdir(parents=True, mode=0o700)
        identity_dir.chmod(0o700)
        backend_token = secrets.token_urlsafe(48)
        session_id = f"pilot-{secrets.token_hex(12)}"
        signing_key = secrets.token_urlsafe(48)
        observed = now or datetime.now(timezone.utc)
        expires_at = (observed + timedelta(seconds=ttl_seconds)).isoformat().replace(
            "+00:00", "Z"
        )
        grants_file = identity_dir / "project-grants.json"
        registry_file = identity_dir / "session-registry.json"
        signing_key_file = identity_dir / "session-signing-key"
        _write_private_json(
            grants_file,
            {
                "contract": "d18_service_project_token_grants/v1",
                "grants": [
                    {
                        "project_prefix": f"{namespace}-",
                        "token": backend_token,
                    }
                ],
            },
        )
        _write_private_json(
            registry_file,
            {
                "contract": "d18_frontend_session_registry/v1",
                "sessions": [
                    {
                        "session_id": session_id,
                        "project_namespace": namespace,
                        "backend_token": backend_token,
                        "expires_at": expires_at,
                    }
                ],
            },
        )
        _write_private_text(signing_key_file, signing_key)
        return cls(
            directory=identity_dir,
            namespace=namespace,
            session_id=session_id,
            grants_file=grants_file,
            registry_file=registry_file,
            signing_key_file=signing_key_file,
            expires_at=expires_at,
        )

    def public_plan(self) -> dict[str, object]:
        return {
            "namespace": self.namespace,
            "expires_at": self.expires_at,
            "secret_file_count": len(self.secret_paths),
            "secret_file_mode": "0600",
        }

    def cleanup(self) -> None:
        for path in self.secret_paths:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        try:
            self.directory.rmdir()
        except OSError:
            pass


def _safe_data_root(root: Path, data_root: Path) -> Path:
    root = root.resolve()
    data_root = data_root.expanduser().resolve()
    temporary_roots = {
        Path(tempfile.gettempdir()).resolve(),
        Path("/tmp").resolve(),
    }
    if not data_root.is_relative_to(root) and not any(
        data_root.is_relative_to(temporary) for temporary in temporary_roots
    ):
        raise ValueError("data root must be under the repository or system temp root")
    return data_root


def build_stack_plan(
    *,
    root: Path,
    data_root: Path,
    host: str,
    backend_port: int,
    frontend_port: int,
) -> dict[str, object]:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("real pilot host must be loopback")
    if backend_port == frontend_port:
        raise ValueError("backend and frontend ports must be distinct")
    root = Path(root).resolve()
    data_root = _safe_data_root(root, Path(data_root))
    python = root / "runtime/service-python/.venv/bin/python"
    return {
        "contract": CONTRACT,
        "mode": "real_model_local_pilot",
        "host": host,
        "data_root": str(data_root),
        "backend": {
            "url": f"http://{host}:{backend_port}",
            "argv": [str(python), "-m", "lca_research_service"],
            "environment": {
                "D18_SERVICE_HOST": host,
                "D18_SERVICE_PORT": str(backend_port),
            },
        },
        "frontend": {
            "url": f"http://{host}:{frontend_port}",
            "argv": [
                "pnpm",
                "start",
                "--hostname",
                host,
                "--port",
                str(frontend_port),
            ],
        },
        "effects": {
            "model_calls": "one_real_gemini_call_expected",
            "tool_writes": 0,
            "external_writes": 0,
            "publication": 0,
        },
    }


def _safe_environment(root: Path) -> dict[str, str]:
    environment = {
        name: os.environ[name]
        for name in SAFE_ENVIRONMENT_NAMES
        if name in os.environ
    }
    environment["PYTHONPATH"] = str(root)
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def frontend_build_is_fresh(frontend_root: Path) -> bool:
    """Return whether the production build is at least as new as its inputs."""

    frontend_root = Path(frontend_root)
    build_identity = frontend_root / ".next/BUILD_ID"
    if not build_identity.is_file():
        return False
    built_at = build_identity.stat().st_mtime_ns
    for relative in FRONTEND_BUILD_INPUTS:
        candidate = frontend_root / relative
        paths = candidate.rglob("*") if candidate.is_dir() else (candidate,)
        if any(path.is_file() and path.stat().st_mtime_ns > built_at for path in paths):
            return False
    return True


def tiangong_runtime_is_ready(root: Path) -> bool:
    """Verify the exact locked TIDAS SDK before accepting pilot traffic."""

    root = Path(root).resolve()
    try:
        catalog = CapabilityCatalog.from_files(
            root / "lca_research_kernel/contracts/tiangong-capability-catalog.json",
            root / "lca_research_kernel/contracts/tiangong-source-lock.json",
        )
        descriptor = catalog.get("tiangong.native.tidas-sdk")
        adapter = TianGongTidasSdkContractAdapter(root / "runtime/tiangong-node")
        return adapter.runtime_available_for_capability(descriptor)
    except (OSError, TypeError, ValueError):
        return False


def acquire_runtime_release_manifest(
    *,
    root: Path,
    frontend_root: Path,
    manifest_path: Path,
    deployed_at: str,
    runtime_instance_id: str,
) -> dict[str, object]:
    """Reuse one deployment identity across restarts of the same data root."""

    manifest_path = Path(manifest_path)
    if manifest_path.exists():
        return load_runtime_release_manifest(
            manifest_path,
            repository_root=root,
            frontend_root=frontend_root,
        )
    manifest = build_runtime_release_manifest(
        root,
        frontend_root=frontend_root,
        deployed_at=deployed_at,
        runtime_instance_id=runtime_instance_id,
    )
    write_runtime_release_manifest(manifest_path, manifest)
    return manifest


def _require_available_port(host: str, port: int, *, label: str) -> None:
    """Fail before launch instead of mistaking an old listener for this release."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError as error:
            raise RuntimeError(
                f"local pilot {label} port is already occupied: {host}:{port}"
            ) from error


def _wait(
    url: str,
    deadline: float,
    *,
    process: subprocess.Popen[bytes],
    label: str,
) -> None:
    while time.monotonic() < deadline:
        returncode = process.poll()
        if returncode is not None:
            raise RuntimeError(
                f"local pilot {label} process exited before readiness: {returncode}"
            )
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status < 500 and process.poll() is None:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    raise RuntimeError("local pilot service did not become ready")


def _read_json(url: str) -> dict[str, object]:
    try:
        with urllib.request.urlopen(url, timeout=3.0) as response:
            value = json.loads(response.read())
    except urllib.error.HTTPError as error:
        public_code = None
        try:
            failure = json.loads(error.read())
            candidate = failure.get("code") if isinstance(failure, dict) else None
            if (
                isinstance(candidate, str)
                and re.fullmatch(r"[a-z0-9_]{1,80}", candidate) is not None
            ):
                public_code = candidate
        except (OSError, json.JSONDecodeError):
            pass
        suffix = f": {public_code}" if public_code is not None else ""
        raise RuntimeError(f"local pilot release handshake failed{suffix}") from error
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise RuntimeError("local pilot release handshake failed") from error
    if not isinstance(value, dict):
        raise RuntimeError("local pilot release handshake is invalid")
    return value


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


def _run_stack(
    *,
    plan: dict[str, object],
    root: Path,
    frontend_root: Path,
    backend_environment: dict[str, str],
    frontend_environment: dict[str, str],
    pnpm: str,
    identity: PilotIdentity,
    release_identity: dict[str, str],
) -> int:
    """Start both processes and always remove credentials and partial children."""

    processes: list[subprocess.Popen[bytes]] = []
    stop_requested = False
    try:
        backend = subprocess.Popen(
            plan["backend"]["argv"],  # type: ignore[index,arg-type]
            cwd=root,
            env=backend_environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        processes.append(backend)
        frontend_argv = list(plan["frontend"]["argv"])  # type: ignore[index]
        frontend_argv[0] = pnpm
        frontend = subprocess.Popen(
            frontend_argv,
            cwd=frontend_root,
            env=frontend_environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        processes.append(frontend)

        def stop(*_: object) -> None:
            nonlocal stop_requested
            stop_requested = True
            _terminate(processes)

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        _wait(
            f"{plan['backend']['url']}/health/ready",  # type: ignore[index]
            time.monotonic() + 30,
            process=backend,
            label="backend",
        )
        _wait(
            str(plan["frontend"]["url"]),  # type: ignore[index]
            time.monotonic() + 30,
            process=frontend,
            label="frontend",
        )
        backend_health = _read_json(
            f"{plan['backend']['url']}/health/ready"  # type: ignore[index]
        )
        frontend_session = _read_json(
            f"{plan['frontend']['url']}/api/d18/session"  # type: ignore[index]
        )
        if (
            backend_health.get("release") != release_identity
            or frontend_session.get("release_id") != release_identity["release_id"]
            or frontend_session.get("frontend_build_id")
            != release_identity["frontend_build_id"]
        ):
            raise RuntimeError("frontend, backend, and worker release identities differ")
        print(
            json.dumps(
                {
                    "contract": CONTRACT,
                    "status": "ready",
                    "backend_url": plan["backend"]["url"],  # type: ignore[index]
                    "frontend_url": plan["frontend"]["url"],  # type: ignore[index]
                    "identity": identity.public_plan(),
                    "release": release_identity,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        while all(process.poll() is None for process in processes):
            time.sleep(0.2)
        if stop_requested:
            return 0
        for label, process in zip(("backend", "frontend"), processes):
            returncode = process.poll()
            if returncode is not None:
                raise RuntimeError(
                    f"local pilot {label} process exited after readiness: "
                    f"{returncode}"
                )
        raise RuntimeError("local pilot process state became inconsistent")
    finally:
        _terminate(processes)
        identity.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--credential-env-file", type=Path, required=True)
    parser.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1")
    parser.add_argument("--backend-port", type=_port, default=8080)
    parser.add_argument("--frontend-port", type=_port, default=3000)
    parser.add_argument("--namespace", default="pilot-a")
    parser.add_argument("--capacity-qualification", type=Path, default=DEFAULT_QUALIFICATION)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    plan = build_stack_plan(
        root=root,
        data_root=args.data_root,
        host=args.host,
        backend_port=args.backend_port,
        frontend_port=args.frontend_port,
    )
    _require_available_port(args.host, args.backend_port, label="backend")
    _require_available_port(args.host, args.frontend_port, label="frontend")
    qualification = args.capacity_qualification
    if not qualification.is_absolute():
        qualification = root / qualification
    qualification = qualification.resolve()
    if not qualification.is_file() or not qualification.is_relative_to(root):
        raise ValueError("capacity qualification is unavailable")
    allowed = required_runtime_environment_names(root)
    credentials = load_credential_environment(args.credential_env_file, allowed)
    if args.dry_run:
        print(
            json.dumps(
                {
                    **plan,
                    "credential_environment_names": sorted(credentials),
                    "capacity_qualification": str(qualification.relative_to(root)),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0

    frontend_root = root / "apps/web"
    pnpm = shutil.which("pnpm")
    python = Path(plan["backend"]["argv"][0])  # type: ignore[index]
    if pnpm is None or not python.is_file() or not frontend_build_is_fresh(frontend_root):
        raise ValueError("fresh frontend build or pinned Python runtime is unavailable")
    if not tiangong_runtime_is_ready(root):
        raise ValueError(
            "locked TianGong TIDAS SDK runtime is unavailable; "
            "run npm ci in runtime/tiangong-node"
        )
    data_root = Path(str(plan["data_root"]))
    data_root.mkdir(parents=True, exist_ok=True)
    identity = PilotIdentity.create(data_root, namespace=args.namespace)
    try:
        deployed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        release_manifest_file = (
            data_root / "control" / "runtime-release-manifest.json"
        )
        release_manifest = acquire_runtime_release_manifest(
            root=root,
            frontend_root=frontend_root,
            manifest_path=release_manifest_file,
            deployed_at=deployed_at,
            runtime_instance_id=identity.session_id,
        )
        release_identity = runtime_release_binding(release_manifest)
        common = _safe_environment(root)
        backend_environment = {
            **common,
            **credentials,
            **plan["backend"]["environment"],  # type: ignore[index]
            "D18_SERVICE_PROJECT_TOKEN_GRANTS_FILE": str(identity.grants_file),
            "D18_SERVICE_DATA_ROOT": str(data_root),
            "D18_ATTACHMENT_HOSTS": "127.0.0.1,localhost",
            "D18_MODEL_CAPACITY_QUALIFICATION_FILE": str(qualification),
            "D18_SERVICE_RUN_ID": f"frontend-pilot-{args.namespace}-v1",
            "D18_RELEASE_MANIFEST_FILE": str(release_manifest_file),
        }
        frontend_environment = {
            **common,
            "D18_BASE_URL": plan["backend"]["url"],  # type: ignore[index]
            "D18_AUTH_MODE": "signed_session",
            "D18_SESSION_REGISTRY_FILE": str(identity.registry_file),
            "D18_SESSION_SIGNING_KEY_FILE": str(identity.signing_key_file),
            "D18_LOCAL_PILOT_SESSION_ID": identity.session_id,
            "D18_RELEASE_ID": release_identity["release_id"],
            "D18_FRONTEND_BUILD_ID": release_identity["frontend_build_id"],
        }
        return _run_stack(
            plan=plan,
            root=root,
            frontend_root=frontend_root,
            backend_environment=backend_environment,
            frontend_environment=frontend_environment,
            pnpm=pnpm,
            identity=identity,
            release_identity=release_identity,
        )
    finally:
        identity.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
