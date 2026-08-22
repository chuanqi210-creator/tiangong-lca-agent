"""Exact, argv-only bootstrap primitives for the D-18 capability runtime."""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from pathlib import Path
from typing import Mapping


LOCK_CONTRACT = "lca_research_kernel.runtime_source_lock/v1"
FLOATING = {"latest", "main", "master", "head", "*", "develop", "trunk"}
SENSITIVE_OUTPUT = re.compile(
    r"(?i)(authorization\s*:\s*bearer|api[_-]?key\s*=|"
    r"(?:password|secret|token)\s*=)"
)


def verify_sha256(path: Path, expected: str) -> str:
    actual = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError("runtime download digest drifted")
    return actual


def sanitize_probe_output(value: object, *, limit: int = 4096) -> str:
    text = str(value).strip()
    if SENSITIVE_OUTPUT.search(text):
        raise ValueError("runtime probe produced sensitive output")
    return text[:limit]


class RuntimeBootstrapPlan:
    """Validated immutable sources compiled to non-shell bootstrap steps."""

    def __init__(self, lock: Mapping[str, object]):
        if lock.get("contract") != LOCK_CONTRACT:
            raise ValueError("runtime source lock contract is unsupported")
        if set(lock) != {"contract", "lock_id", "runtime_root", "sources"}:
            raise ValueError("runtime source lock fields are incomplete")
        runtime_root = lock.get("runtime_root")
        if not isinstance(runtime_root, str) or not runtime_root.strip():
            raise ValueError("runtime root is absent")
        self.runtime_root = Path(runtime_root)
        sources = lock.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError("runtime sources are absent")
        self.sources: dict[str, dict] = {}
        for raw in sources:
            source = self._validate_source(raw)
            source_id = source["source_id"]
            if source_id in self.sources:
                raise ValueError("duplicate runtime source id")
            self.sources[source_id] = source
        self.steps = self._compile_steps()

    def _validate_source(self, raw: object) -> dict:
        if not isinstance(raw, Mapping):
            raise ValueError("runtime source row is invalid")
        required = {
            "source_id",
            "kind",
            "identity",
            "version",
            "integrity",
            "source_url",
            "install",
        }
        if set(raw) != required:
            raise ValueError("runtime source fields are incomplete")
        source = deepcopy(dict(raw))
        for key in required - {"install"}:
            if not isinstance(source.get(key), str) or not str(source[key]).strip():
                if key == "integrity":
                    raise ValueError("runtime source integrity is required")
                raise ValueError(f"runtime source {key} is required")
        if str(source["version"]).casefold() in FLOATING:
            raise ValueError("exact runtime source version is required")
        if not isinstance(source.get("install"), Mapping):
            raise ValueError("runtime source install contract is absent")
        return source

    def _compile_steps(self) -> list[dict]:
        steps: list[dict] = []
        for source_id in sorted(self.sources):
            source = self.sources[source_id]
            install = source["install"]
            mode = install.get("mode")
            if mode == "host_command":
                argv = [str(install["command"]), "--version"]
            elif mode == "npm_ci":
                argv = [
                    "npm",
                    "ci",
                    "--prefix",
                    (self.runtime_root / "node").as_posix(),
                    "--ignore-scripts",
                    "--no-audit",
                    "--no-fund",
                ]
            elif mode == "python_wheel":
                argv = [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    (self.runtime_root / "python/bin/python").as_posix(),
                    str(install["wheel"]),
                ]
            elif mode == "uv_sync":
                argv = [
                    "uv",
                    "pip",
                    "sync",
                    str(install["requirements"]),
                    "--python",
                    (self.runtime_root / "python/bin/python").as_posix(),
                    "--require-hashes",
                    "--strict",
                ]
            elif mode == "raw_git_files":
                argv = [
                    "python3",
                    "scripts/install_locked_tiangong_skills.py",
                    "--manifest",
                    str(install["manifest"]),
                    "--source-snapshot",
                    "<verified-source-snapshot>",
                    "--output",
                    (self.runtime_root / "skills").as_posix(),
                ]
            elif mode == "dmg_app":
                argv = [
                    "hdiutil",
                    "attach",
                    (
                        self.runtime_root
                        / "downloads"
                        / f"{source['identity']}-{source['version']}.dmg"
                    ).as_posix(),
                    "-readonly",
                    "-nobrowse",
                ]
            else:
                raise ValueError(f"unsupported runtime install mode: {mode}")
            steps.append(
                {
                    "source_id": source_id,
                    "mode": mode,
                    "argv": argv,
                    "shell": False,
                }
            )
        return steps
