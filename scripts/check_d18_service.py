#!/usr/bin/env python3
"""Mechanical local D-18 service readiness and protocol check."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SERVICE_PYTHON = ROOT / "runtime/service-python/.venv/bin/python"
if importlib.util.find_spec("fastapi") is None:
    if not SERVICE_PYTHON.is_file():
        raise SystemExit("bootstrap runtime/service-python before checking the service")
    os.execv(str(SERVICE_PYTHON), [str(SERVICE_PYTHON), *sys.argv])

from lca_research_service.app import ServiceConfig, create_app
from lca_research_service.metrics import check_service_readiness


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def check_service(root: Path, output: Path) -> dict:
    root = root.resolve()
    probe_root = output / "probe"
    app = create_app(
        ServiceConfig(
            bearer_token="local-probe-token",
            data_root=probe_root,
            allowed_attachment_hosts=("assets.example.invalid",),
            repository_root=root,
        )
    )
    readiness = check_service_readiness(
        repository_root=root,
        control_db=probe_root / "control" / "jobs.sqlite3",
        project_root=probe_root / "projects",
    )
    contract = json.loads(
        (root / "lca_research_kernel/contracts/qingxiaoda-openai-compat-v1.json").read_text()
    )
    routes = {
        f"{method} {route.path}"
        for route in app.routes
        for method in getattr(route, "methods", set())
        if route.path.startswith("/v1/")
        or route.path.startswith("/health")
        or route.path == "/metrics"
    }
    report = {
        "contract": "d18_service_health_acceptance/v1",
        "status": "accepted"
        if readiness["status"] == "ready" and routes == set(contract["endpoints"])
        else "rejected",
        "readiness": readiness,
        "protocol_routes_exact": routes == set(contract["endpoints"]),
        "protocol_contract_sha256": hashlib.sha256(
            (root / "lca_research_kernel/contracts/qingxiaoda-openai-compat-v1.json").read_bytes()
        ).hexdigest(),
        "scientific_calculations": 0,
        "credentials_used": 0,
        "external_writes": 0,
    }
    app.state.store.close()
    _write(output / "service-health.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = check_service(args.root, args.output)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
