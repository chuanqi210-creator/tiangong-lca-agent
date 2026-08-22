"""Real-runtime health and closure helpers for the D-18 capability catalog."""

from __future__ import annotations

import hashlib
import os
import signal
import socket
import subprocess
import time
from copy import deepcopy
from pathlib import Path
from typing import Mapping

from .runtime_environment import RuntimeEnvironmentContract


RECEIPT_GROUPS = {
    "bootstrap",
    "cli",
    "mcp",
    "openlca",
    "skills",
    "tidas",
    "boundary",
}


def compile_openlca_server_argv(runtime_root: Path, *, port: int) -> list[str]:
    """Compile the isolated, loopback-only, read-only openLCA IPC server."""

    runtime_root = Path(runtime_root)
    eclipse = runtime_root / "openlca/openLCA.app/Contents/Eclipse"
    java = eclipse / "jre/Contents/Home/bin/java"
    libraries = eclipse / "plugins/olca-app_2.6.2/libs/*"
    data = runtime_root / "openlca-data"
    native = eclipse / "olca-mkl-x64_v1"
    return [
        str(java),
        "-Xmx1024M",
        "-cp",
        str(libraries),
        "org.openlca.ipc.Server",
        "-data",
        str(data),
        "-database",
        "d18-runtime-empty",
        "-port",
        str(port),
        "--readonly",
        "true",
        "-threads",
        "2",
        "-timeout",
        "30",
        "-native",
        str(native),
    ]


def compile_openlca_calculation_server_argv(
    runtime_root: Path,
    *,
    data_root: Path,
    database: str,
    port: int,
) -> list[str]:
    """Compile a writable openLCA server limited to one project-local database."""

    runtime_root = Path(runtime_root)
    data_root = Path(data_root)
    if (
        not isinstance(database, str)
        or not database
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in database)
    ):
        raise ValueError("openLCA calculation database identity is unsafe")
    eclipse = runtime_root / "openlca/openLCA.app/Contents/Eclipse"
    java = eclipse / "jre/Contents/Home/bin/java"
    libraries = eclipse / "plugins/olca-app_2.6.2/libs/*"
    native = eclipse / "olca-mkl-x64_v1"
    return [
        str(java),
        "-Xmx1024M",
        "-cp",
        str(libraries),
        "org.openlca.ipc.Server",
        "-data",
        str(data_root),
        "-database",
        database,
        "-port",
        str(port),
        "-threads",
        "2",
        "-timeout",
        "60",
        "-native",
        str(native),
    ]


class OpenLcaServer:
    """Bounded lifecycle for the isolated openLCA IPC health server."""

    def __init__(self, runtime_root: Path, *, port: int = 18080):
        self.runtime_root = Path(runtime_root)
        self.port = port
        self.process: subprocess.Popen | None = None

    def start(self, *, timeout_seconds: int = 30) -> None:
        if self.process is not None:
            raise ValueError("openLCA health server is already started")
        (self.runtime_root / "openlca-data/databases").mkdir(
            parents=True, exist_ok=True
        )
        self.process = subprocess.Popen(
            compile_openlca_server_argv(self.runtime_root, port=self.port),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=self.runtime_root,
            env={"LC_ALL": "C", "PATH": os.environ.get("PATH", "")},
        )
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("openLCA health server exited before readiness")
            try:
                with socket.create_connection(
                    ("127.0.0.1", self.port), timeout=0.25
                ):
                    return
            except OSError:
                time.sleep(0.1)
        self.stop()
        raise TimeoutError("openLCA health server readiness timed out")

    def stop(self, *, timeout_seconds: int = 15) -> None:
        process = self.process
        self.process = None
        if process is None or process.poll() is not None:
            return
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def __enter__(self) -> "OpenLcaServer":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


def classify_openlca_observation(
    direct: Mapping[str, object],
    mcp: Mapping[str, object],
) -> dict:
    if direct.get("contract") != "lca_research_kernel.openlca_ipc_probe/v1":
        raise ValueError("openLCA direct probe contract drifted")
    if mcp.get("contract") != "lca_research_kernel.mcp_openlca_read_probe/v1":
        raise ValueError("openLCA MCP probe contract drifted")
    for value in (direct, mcp):
        if (
            value.get("externalWrites") != 0
            or value.get("calculations") != 0
            or value.get("scientificResultsCreated") != 0
        ):
            raise ValueError("openLCA health probe crossed the effect boundary")
    if direct.get("readOnlyCalls") != 2:
        raise ValueError("openLCA direct read coverage drifted")
    impact = direct.get("impactMethods")
    processes = direct.get("processes")
    if not isinstance(impact, Mapping) or not isinstance(processes, Mapping):
        raise ValueError("openLCA direct list result is absent")
    calls = mcp.get("calls")
    if not isinstance(calls, list) or len(calls) != 2:
        raise ValueError("openLCA MCP read coverage drifted")
    counts = (impact.get("count"), processes.get("count"))
    if not all(isinstance(value, int) and value >= 0 for value in counts):
        raise ValueError("openLCA descriptor counts are invalid")
    data_state = (
        "empty_database_read_path_verified"
        if counts == (0, 0)
        else "descriptor_read_path_verified"
    )
    return {
        "status": "passed",
        "data_state": data_state,
        "direct_read_calls": 2,
        "mcp_read_calls": 2,
        "scientific_result_created": False,
        "calculation_executed": False,
        "external_writes": 0,
    }


def receipt_group_for(descriptor: Mapping[str, object]) -> str:
    if descriptor.get("disposition") == "human_action_required":
        return "boundary"
    capability_id = str(descriptor.get("capability_id"))
    adapter = descriptor.get("adapter_id")
    if adapter == "skill.metadata":
        return "skills"
    if "openlca" in capability_id or capability_id.startswith(
        "tiangong.calculation."
    ):
        return "openlca"
    if adapter in {"mcp.tool", "mcp.prompt", "mcp.resource"}:
        return "mcp"
    if (
        capability_id.startswith("tiangong.tidas-tools.")
        or capability_id.startswith("tiangong.validation.")
        or capability_id.startswith("tiangong.specification.")
        or capability_id == "tiangong.native.tidas-sdk"
    ):
        return "tidas"
    if capability_id.startswith("tiangong.build.") or capability_id == (
        "tiangong.native.cli-contract"
    ):
        return "cli"
    return "bootstrap"


def build_closed_readiness_matrix(
    contract: RuntimeEnvironmentContract,
    receipt_evidence: Mapping[str, Mapping[str, object]],
    *,
    observed_at: str,
) -> dict:
    missing = RECEIPT_GROUPS - set(receipt_evidence)
    if missing:
        raise ValueError(f"runtime receipt groups are absent: {sorted(missing)}")
    matrix = contract.new_matrix(observed_at=observed_at)
    for row in matrix["capabilities"]:
        descriptor = contract.descriptors[row["capability_id"]]
        group = receipt_group_for(descriptor)
        evidence = deepcopy(dict(receipt_evidence[group]))
        if descriptor["disposition"] == "human_action_required":
            row["readiness_status"] = "boundary_verified_nonlocal"
            row["evidence"] = {
                gate: deepcopy(evidence) for gate in contract.boundary_gates
            }
        else:
            row["readiness_status"] = "executable_verified"
            row["evidence"] = {
                gate: deepcopy(evidence) for gate in contract.executable_gates
            }
    errors = contract.validate_matrix(matrix, require_closed=True)
    if errors:
        raise ValueError("runtime readiness matrix is not closed: " + "; ".join(errors))
    return matrix


def verify_receipt_reference(
    root: Path, evidence: Mapping[str, object]
) -> None:
    reference = evidence.get("receipt_ref")
    expected = evidence.get("receipt_sha256")
    if not isinstance(reference, str) or not reference:
        raise ValueError("runtime receipt reference is absent")
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError("runtime receipt digest is invalid")
    path = Path(root) / reference
    if not path.is_file():
        raise ValueError(f"runtime receipt is absent: {reference}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"runtime receipt digest drifted: {reference}")


def verify_matrix_receipts(root: Path, matrix: Mapping[str, object]) -> None:
    rows = matrix.get("capabilities")
    if not isinstance(rows, list):
        raise ValueError("runtime readiness matrix rows are absent")
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("runtime readiness matrix row is invalid")
        evidence = row.get("evidence")
        if not isinstance(evidence, Mapping):
            raise ValueError("runtime readiness evidence is absent")
        for value in evidence.values():
            if not isinstance(value, Mapping):
                raise ValueError("runtime readiness receipt is invalid")
            key = (
                str(value.get("receipt_ref")),
                str(value.get("receipt_sha256")),
            )
            if key not in seen:
                verify_receipt_reference(root, value)
                seen.add(key)
