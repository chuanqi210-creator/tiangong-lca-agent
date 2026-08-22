#!/usr/bin/env python3
"""Read-only proof for the final D-18 Linux container boundary."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from lca_research_service.material_sandbox import probe_material_runtime


_CREDENTIAL_NAMES = frozenset(
    {
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY",
        "D18_SERVICE_BEARER_TOKEN",
        "TIANGONG_TOKEN",
    }
)
_EXPECTED_PROBE = {
    "adapter_contract": "material_sandbox_adapter/v1",
    "adapter_id": "linux-libseccomp",
    "adapter_version": "1",
    "memory_monitor": "linux-rlimit-as-proc-rss",
    "sandbox_ready": True,
    "memory_ready": True,
    "runtime_euid": 10001,
    "ready": True,
}


def build_container_runtime_report(
    *,
    environ: Mapping[str, str] | None = None,
    euid: int | None = None,
    probe: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    environment = os.environ if environ is None else environ
    runtime_euid = os.geteuid() if euid is None else euid
    observed_probe = probe_material_runtime() if probe is None else dict(probe)
    if runtime_euid != 10001:
        raise RuntimeError("D-18 container final user identity is invalid")
    if any(environment.get(name) for name in _CREDENTIAL_NAMES):
        raise RuntimeError("D-18 container provider credentials are configured")
    if any(observed_probe.get(key) != value for key, value in _EXPECTED_PROBE.items()):
        raise RuntimeError("D-18 container material sandbox is not qualified")
    return {
        "contract": "d18_container_runtime_probe/v1",
        "status": "ready",
        "runtime_euid": runtime_euid,
        "adapter_contract": observed_probe["adapter_contract"],
        "adapter_id": observed_probe["adapter_id"],
        "adapter_version": observed_probe["adapter_version"],
        "memory_monitor": observed_probe["memory_monitor"],
        "credentials_configured": False,
        "network_calls": 0,
        "scientific_writes": 0,
    }


def main(
    *,
    environ: Mapping[str, str] | None = None,
    euid: int | None = None,
    probe: Mapping[str, Any] | None = None,
) -> int:
    report = build_container_runtime_report(
        environ=environ,
        euid=euid,
        probe=probe,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
