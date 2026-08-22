#!/usr/bin/env python3
"""Rebuild the exact, case-free D-18 TianGong capability runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_kernel.runtime_bootstrap import RuntimeBootstrapPlan
from scripts.install_locked_tiangong_skills import install_skills


DEFAULT_LOCK = ROOT / "runtime/tiangong-runtime-source-lock.json"
REPO_ARTIFACTS = {
    "runtime.node-lock": "runtime/tiangong-node/package-lock.json",
    "runtime.python-lock": "runtime/tiangong-python/requirements.lock",
    "runtime.tiangong-skills": "runtime/tiangong-skills/remote-git-blobs.tsv",
}
PROBE_FILES = (
    "probe-mcp.mjs",
    "probe-mcp-openlca.mjs",
    "probe-openlca.mjs",
    "probe-tidas-sdk.mjs",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _expected_sha256(source: dict) -> str:
    integrity = str(source["integrity"])
    if not integrity.startswith("sha256-") or len(integrity) != 71:
        raise ValueError("repository artifact does not use exact sha256 integrity")
    return integrity.removeprefix("sha256-")


def _load_lock(lock_path: Path) -> dict:
    lock = json.loads(Path(lock_path).read_text(encoding="utf-8"))
    RuntimeBootstrapPlan(lock)
    return lock


def validate_runtime_lock(root: Path, lock_path: Path) -> dict:
    """Validate exact source identities and all checked-in bootstrap bytes."""

    root = Path(root).resolve()
    lock = _load_lock(lock_path)
    sources = {row["source_id"]: row for row in lock["sources"]}
    for source_id, relative in REPO_ARTIFACTS.items():
        source = sources.get(source_id)
        if source is None:
            raise ValueError(f"repository artifact source is absent: {source_id}")
        path = root / relative
        if not path.is_file() or _sha256(path) != _expected_sha256(source):
            raise ValueError(f"repository artifact drifted: {relative}")
    return {
        "contract": lock["contract"],
        "lock_id": lock["lock_id"],
        "source_count": len(sources),
        "repo_artifact_count": len(REPO_ARTIFACTS),
        "status": "valid",
    }


def compile_bootstrap_commands(root: Path, lock_path: Path) -> list[dict]:
    """Compile the immutable lock to inspectable, shell-free argv steps."""

    root = Path(root).resolve()
    lock = _load_lock(lock_path)
    runtime_root = root / lock["runtime_root"]
    commands: list[dict] = []
    for source in sorted(lock["sources"], key=lambda row: row["source_id"]):
        install = source["install"]
        mode = install["mode"]
        if mode == "host_command":
            argv = [install["command"], "--version"]
        elif mode == "npm_ci":
            argv = [
                "npm",
                "ci",
                "--prefix",
                str(runtime_root / "node"),
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
            ]
        elif mode == "uv_sync":
            argv = [
                "uv",
                "pip",
                "sync",
                str(root / install["requirements"]),
                "--python",
                str(runtime_root / "python/bin/python"),
                "--require-hashes",
                "--strict",
            ]
        elif mode == "raw_git_files":
            argv = [
                "python3",
                str(root / "scripts/install_locked_tiangong_skills.py"),
                "--manifest",
                str(root / install["manifest"]),
                "--source-snapshot",
                "<verified-source-snapshot>",
                "--output",
                str(runtime_root / "skills"),
            ]
        elif mode == "dmg_app":
            download_name = install.get(
                "download_name",
                f"{source['identity']}-{source['version']}.dmg",
            )
            argv = [
                "hdiutil",
                "attach",
                str(runtime_root / "downloads" / download_name),
                "-readonly",
                "-nobrowse",
                "-plist",
            ]
        else:
            raise ValueError(f"unsupported runtime install mode: {mode}")
        commands.append(
            {
                "source_id": source["source_id"],
                "mode": mode,
                "argv": [str(item) for item in argv],
                "shell": False,
            }
        )
    return commands


def _run(argv: Iterable[str], *, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(item) for item in argv],
        shell=False,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={"LC_ALL": "C", "PATH": os.environ.get("PATH", "")},
    )


def _verify_host_sources(lock: dict) -> list[dict]:
    observations = []
    for source in lock["sources"]:
        install = source["install"]
        if install["mode"] != "host_command":
            continue
        completed = _run([install["command"], "--version"], timeout=15)
        observed = (completed.stdout or completed.stderr).strip().splitlines()[0]
        expected = source["integrity"].removeprefix("version-output:")
        if observed != expected and not observed.startswith(f"{expected} ("):
            raise ValueError(f"host runtime version drifted: {source['source_id']}")
        observations.append(
            {
                "source_id": source["source_id"],
                "version": source["version"],
                "status": "verified",
            }
        )
    return observations


def _copy_node_runtime(root: Path, runtime_root: Path) -> None:
    destination = runtime_root / "node"
    destination.mkdir(parents=True, exist_ok=True)
    source = root / "runtime/tiangong-node"
    for name in ("package.json", "package-lock.json", *PROBE_FILES):
        shutil.copy2(source / name, destination / name)
    _run(
        [
            "npm",
            "ci",
            "--prefix",
            destination,
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
        ],
        timeout=600,
    )
    cli_main = (
        destination
        / "node_modules/@tiangong-lca/cli/dist/src/main.js"
    )
    if not cli_main.is_file():
        raise ValueError("installed TianGong CLI entry is absent")
    os.utime(cli_main, None)


def _sync_python_runtime(root: Path, runtime_root: Path, lock: dict) -> None:
    python_source = next(
        row for row in lock["sources"] if row["source_id"] == "host.python"
    )
    venv = runtime_root / "python"
    venv_python = venv / "bin/python"
    if venv_python.is_file():
        observed = _run([venv_python, "--version"], timeout=15).stdout.strip()
        expected = python_source["integrity"].removeprefix("version-output:")
        if observed != expected:
            raise ValueError("existing Python runtime version drifted")
    else:
        _run(
            [
                "uv",
                "venv",
                "--python",
                python_source["install"]["command"],
                str(venv),
            ],
            timeout=180,
        )
    requirements = root / next(
        row
        for row in lock["sources"]
        if row["source_id"] == "runtime.python-lock"
    )["install"]["requirements"]
    _run(
        [
            "uv",
            "pip",
            "sync",
            str(requirements),
            "--python",
            str(venv_python),
            "--require-hashes",
            "--strict",
        ],
        timeout=600,
    )


def _download_exact(source: dict, target: Path) -> None:
    expected_bytes = int(source["install"]["bytes"])
    expected_sha = _expected_sha256(source)
    if (
        target.is_file()
        and target.stat().st_size == expected_bytes
        and _sha256(target) == expected_sha
    ):
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{target.name}.",
        suffix=".download",
        dir=target.parent,
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
        try:
            with urllib.request.urlopen(source["source_url"], timeout=60) as response:
                shutil.copyfileobj(response, temporary, length=1024 * 1024)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
    if temporary_path.stat().st_size != expected_bytes:
        temporary_path.unlink(missing_ok=True)
        raise ValueError("openLCA download size drifted")
    if _sha256(temporary_path) != expected_sha:
        temporary_path.unlink(missing_ok=True)
        raise ValueError("openLCA download digest drifted")
    temporary_path.replace(target)


def _openlca_version(app: Path) -> str:
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    return str(
        info.get("CFBundleShortVersionString")
        or info.get("CFBundleVersion")
        or ""
    )


def _install_openlca(source: dict, runtime_root: Path) -> None:
    app = runtime_root / "openlca/openLCA.app"
    if app.is_dir() and _openlca_version(app) == source["version"]:
        return
    download_name = source["install"].get(
        "download_name",
        f"{source['identity']}-{source['version']}.dmg",
    )
    dmg = runtime_root / "downloads" / download_name
    _download_exact(source, dmg)
    with tempfile.TemporaryDirectory(prefix="d18-openlca-mount-") as mount:
        _run(
            [
                "hdiutil",
                "attach",
                str(dmg),
                "-readonly",
                "-nobrowse",
                "-mountpoint",
                mount,
            ],
            timeout=120,
        )
        try:
            source_app = Path(mount) / source["install"]["app"]
            if not source_app.is_dir():
                raise ValueError("openLCA app is absent from adopted image")
            app.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix=".openlca.install-",
                dir=app.parent,
            ) as staged_root:
                staged = Path(staged_root) / "openLCA.app"
                _run(["ditto", str(source_app), str(staged)], timeout=600)
                if app.exists():
                    shutil.rmtree(app)
                staged.rename(app)
        finally:
            _run(["hdiutil", "detach", mount], timeout=120)
    if _openlca_version(app) != source["version"]:
        raise ValueError("installed openLCA version drifted")


def bootstrap_runtime(
    root: Path,
    lock_path: Path,
    *,
    skills_source_snapshot: Path,
) -> dict:
    root = Path(root).resolve()
    validation = validate_runtime_lock(root, lock_path)
    lock = _load_lock(lock_path)
    runtime_root = root / lock["runtime_root"]
    runtime_root.mkdir(parents=True, exist_ok=True)
    host = _verify_host_sources(lock)
    _copy_node_runtime(root, runtime_root)
    _sync_python_runtime(root, runtime_root, lock)
    skills_source = next(
        row
        for row in lock["sources"]
        if row["source_id"] == "runtime.tiangong-skills"
    )
    skills_receipt = install_skills(
        manifest_path=root / skills_source["install"]["manifest"],
        source_snapshot=Path(skills_source_snapshot),
        output=runtime_root / "skills",
    )
    openlca = next(
        row
        for row in lock["sources"]
        if row["source_id"] == "runtime.openlca"
    )
    _install_openlca(openlca, runtime_root)
    return {
        "contract": "lca_research_kernel.runtime_bootstrap_receipt/v1",
        "lock_id": lock["lock_id"],
        "source_count": validation["source_count"],
        "host_sources": host,
        "skills": skills_receipt,
        "openlca_version": _openlca_version(
            runtime_root / "openlca/openLCA.app"
        ),
        "status": "installed",
        "scientific_result_created": False,
        "external_writes": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--skills-source-snapshot", type=Path)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args()
    if arguments.install:
        if arguments.skills_source_snapshot is None:
            raise SystemExit("--skills-source-snapshot is required with --install")
        report = bootstrap_runtime(
            arguments.root,
            arguments.lock,
            skills_source_snapshot=arguments.skills_source_snapshot,
        )
    else:
        report = validate_runtime_lock(arguments.root, arguments.lock)
        report["commands"] = compile_bootstrap_commands(
            arguments.root, arguments.lock
        )
    payload = json.dumps(
        report, ensure_ascii=False, sort_keys=True, indent=2
    ) + "\n"
    if arguments.report:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
