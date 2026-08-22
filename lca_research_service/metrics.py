"""Bounded service metrics, safe logs, and non-scientific readiness checks."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lca_research_kernel.agent_bootstrap import AgentBootstrap
from lca_research_kernel.model_runtime import load_model_runtime_contract
from .material_sandbox import probe_material_runtime

_METRIC_DIMENSIONS = {
    "d18_jobs_total": {
        "state": {
            "queued",
            "leased",
            "running",
            "waiting_for_input",
            "cancelling",
            "cancelled",
            "completed",
            "bounded",
            "failed",
        }
    },
    "d18_job_duration_seconds": {
        "outcome": {"completed", "bounded", "failed", "cancelled"}
    },
    "d18_queue_depth": {},
    "d18_worker_leases": {"state": {"active", "expired"}},
    "d18_model_calls_total": {
        "provider": {"openai", "google", "anthropic"},
        "outcome": {"completed", "failed", "blocked", "retried"},
    },
    "d18_capability_calls_total": {
        "capability_class": {
            "tiangong_read",
            "tidas_validation",
            "openlca_ipc",
            "local_deterministic",
            "mcp_read",
        },
        "outcome": {"completed", "failed", "blocked", "retried"},
    },
    "d18_recovery_total": {"outcome": {"completed", "failed", "noop"}},
    "d18_material_intakes_total": {
        "source": {"qingxiaoda_url", "local_upload", "project_reuse"},
        "outcome": {"created", "replayed", "rejected"},
        "type_family": {"document", "image", "audio", "table", "package"},
        "size_bucket": {"empty", "lt_1mib", "lt_10mib", "gte_10mib"},
    },
    "d18_http_requests_total": {
        "route": {
            "/v1/models",
            "/v1/chat/completions",
            "/v1/jobs",
            "/v1/jobs/{job_id}",
            "/v1/jobs/{job_id}/inputs",
            "/v1/jobs/{job_id}/result",
            "/v1/jobs/{job_id}/cancel",
            "/v1/jobs/{job_id}/resume",
            "/v1/jobs/{job_id}/events",
            "/v1/research/{research_id}/material-events",
            "/v1/files/{artifact_id}",
            "/health/live",
            "/health/ready",
            "/metrics",
        },
        "status_class": {"2xx", "4xx", "5xx"},
    },
    "d18_http_overhead_seconds": {
        "route": {
            "/v1/models",
            "/v1/chat/completions",
            "/v1/jobs",
            "/v1/jobs/{job_id}",
            "/v1/jobs/{job_id}/inputs",
            "/v1/jobs/{job_id}/result",
            "/v1/jobs/{job_id}/cancel",
            "/v1/jobs/{job_id}/resume",
            "/v1/jobs/{job_id}/events",
            "/v1/research/{research_id}/material-events",
            "/v1/files/{artifact_id}",
            "/health/live",
            "/health/ready",
            "/metrics",
        }
    },
}

_SAFE_LOG_KEYS = frozenset(
    {"event", "route", "status_class", "duration_bucket", "outcome", "state"}
)


class MetricsRegistry:
    def __init__(self) -> None:
        self._values: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(
            float
        )

    @staticmethod
    def _labels(name: str, labels: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
        if name not in _METRIC_DIMENSIONS:
            raise ValueError("metric is not in the bounded D-18 schema")
        dimensions = _METRIC_DIMENSIONS[name]
        if set(labels) != set(dimensions):
            raise ValueError("metric labels do not match the bounded schema")
        for key, value in labels.items():
            if value not in dimensions[key]:
                raise ValueError("metric label value is not in the bounded schema")
        return tuple(sorted(labels.items()))

    def increment(
        self, name: str, labels: Mapping[str, str], amount: float = 1
    ) -> None:
        if amount < 0:
            raise ValueError("counter increments must be non-negative")
        self._values[(name, self._labels(name, labels))] += amount

    def set(self, name: str, labels: Mapping[str, str], value: float) -> None:
        self._values[(name, self._labels(name, labels))] = value

    def render(self) -> str:
        lines: list[str] = []
        for (name, labels), value in sorted(self._values.items()):
            suffix = (
                "{"
                + ",".join(f'{key}="{item}"' for key, item in labels)
                + "}"
                if labels
                else ""
            )
            rendered_value = str(int(value)) if float(value).is_integer() else repr(value)
            lines.append(f"{name}{suffix} {rendered_value}")
        return "\n".join(lines) + ("\n" if lines else "")


def material_size_bucket(byte_size: int) -> str:
    if not isinstance(byte_size, int) or isinstance(byte_size, bool) or byte_size < 0:
        raise ValueError("material byte size is invalid")
    if byte_size == 0:
        return "empty"
    if byte_size < 1024 * 1024:
        return "lt_1mib"
    if byte_size < 10 * 1024 * 1024:
        return "lt_10mib"
    return "gte_10mib"


def safe_log_record(value: Mapping[str, Any]) -> dict[str, str]:
    """Retain only bounded, preclassified operational dimensions."""

    result: dict[str, str] = {}
    for key in _SAFE_LOG_KEYS:
        item = value.get(key)
        if not isinstance(item, str):
            continue
        if len(item) > 64 or any(
            marker in item.casefold()
            for marker in ("http://", "https://", "bearer ", "/", "\\")
        ) and key != "route":
            continue
        if key == "route" and item not in _METRIC_DIMENSIONS[
            "d18_http_requests_total"
        ]["route"]:
            continue
        result[key] = item
    return result


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _protocol_sources_valid(root: Path) -> bool:
    contract_path = root / "lca_research_kernel/contracts/qingxiaoda-openai-compat-v1.json"
    try:
        contract = json.loads(contract_path.read_text())
        return all(
            _sha256(root / row["path"]) == row["sha256"]
            for row in contract["source_documents"]
        )
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return False


def check_service_readiness(
    *,
    repository_root: str | Path,
    control_db: str | Path,
    project_root: str | Path,
    runtime_ready: bool = True,
    material_control_db: str | Path | None = None,
    input_root: str | Path | None = None,
    material_runtime_probe: Mapping[str, Any] | None = None,
    release_manifest_valid: bool = True,
    release_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(repository_root).resolve()
    db = Path(control_db)
    projects = Path(project_root)
    material_runtime = dict(material_runtime_probe or probe_material_runtime())
    checks = {
        "startup_manifest": False,
        "model_catalog": False,
        "capability_catalog": False,
        "protocol_contract": False,
        "control_database": False,
        "project_root": False,
        "product_interface_runtime": runtime_ready is True,
        "material_control_database": material_control_db is None,
        "input_root_permissions": input_root is None,
        "material_sandbox_adapter": material_runtime.get("sandbox_ready") is True,
        "material_memory_monitor": material_runtime.get("memory_ready") is True,
        "runtime_release_manifest": release_manifest_valid is True,
    }
    try:
        AgentBootstrap.load(
            root,
            root / "lca_research_kernel/contracts/agent-startup-manifest-v2.json",
        )
        checks["startup_manifest"] = True
    except (OSError, ValueError):
        pass
    try:
        load_model_runtime_contract(root)
        checks["model_catalog"] = True
    except (OSError, ValueError):
        pass
    checks["capability_catalog"] = all(
        (root / relative).is_file()
        for relative in (
            "lca_research_kernel/contracts/tiangong-capability-catalog.json",
            "lca_research_kernel/contracts/tiangong-source-lock.json",
        )
    )
    checks["protocol_contract"] = _protocol_sources_valid(root)
    try:
        with sqlite3.connect(db) as connection:
            control_tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            checks["control_database"] = (
                connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
                and connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
                and {"jobs", "job_events", "job_ingress_idempotency"}
                <= control_tables
            )
    except sqlite3.Error:
        pass
    if material_control_db is not None:
        try:
            with sqlite3.connect(material_control_db) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                checks["material_control_database"] = (
                    connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
                    and connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
                    and {
                        "upload_sessions",
                        "upload_parts",
                        "materials",
                        "material_revisions",
                        "material_events",
                        "processing_claims",
                    }
                    <= tables
                )
        except sqlite3.Error:
            pass
    if input_root is not None:
        try:
            input_path = Path(input_root).resolve()
            input_path.mkdir(parents=True, exist_ok=True, mode=0o700)
            checks["input_root_permissions"] = (
                input_path.is_dir()
                and not input_path.is_symlink()
                and input_path.stat().st_mode & 0o077 == 0
            )
        except OSError:
            pass
    try:
        resolved = projects.resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        probe = resolved / ".d18-readiness"
        probe.touch(exist_ok=True)
        probe.unlink()
        checks["project_root"] = True
    except OSError:
        pass
    return {
        "contract": "d18_service_readiness/v1",
        "status": "ready" if all(checks.values()) else "not_ready",
        "checks": checks,
        "scientific_calculation": False,
        "external_writes": 0,
        "material_runtime": material_runtime,
        "release": dict(release_identity or {}),
    }
