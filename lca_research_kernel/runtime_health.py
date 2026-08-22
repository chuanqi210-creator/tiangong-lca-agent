"""Bounded, non-shell runtime probes for D-18 capability readiness."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .runtime_bootstrap import sanitize_probe_output


RECEIPT_CONTRACT = "lca_research_kernel.runtime_probe_receipt/v1"
FORBIDDEN_EFFECTS = {
    "research_case_execution",
    "lca_calculation",
    "lcia_calculation",
    "external_write",
    "publish",
    "payment",
    "irreversible_action",
}


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


class RuntimeProbeRunner:
    """Run an exact argv probe and persist only bounded, secret-safe evidence."""

    def __init__(
        self,
        *,
        receipt_root: Path,
        executor: Callable[..., object] = subprocess.run,
        clock: Callable[[], str] = _now,
    ):
        self.receipt_root = Path(receipt_root)
        self.executor = executor
        self.clock = clock

    def run(
        self,
        *,
        probe_id: str,
        source_lock_id: str,
        argv: Sequence[str],
        effect: str,
        timeout_seconds: int = 15,
        environment: Mapping[str, str] | None = None,
    ) -> dict:
        if effect in FORBIDDEN_EFFECTS:
            raise ValueError("runtime probe operation is forbidden")
        if (
            not isinstance(argv, Sequence)
            or isinstance(argv, (str, bytes))
            or not argv
            or not all(isinstance(item, str) and item for item in argv)
        ):
            raise ValueError("runtime probe argv is invalid")
        if timeout_seconds < 1 or timeout_seconds > 120:
            raise ValueError("runtime probe timeout is outside the bounded range")
        if environment is None:
            environment = {
                "LC_ALL": "C",
                "PATH": os.environ.get("PATH", ""),
            }
        started = time.monotonic()
        base = {
            "contract": RECEIPT_CONTRACT,
            "probe_id": probe_id,
            "source_lock_id": source_lock_id,
            "observed_at": self.clock(),
            "argv_sha256": hashlib.sha256(
                "\0".join(argv).encode("utf-8")
            ).hexdigest(),
            "effect": effect,
            "attempts": 1,
            "executed_external_writes": 0,
            "scientific_result_created": False,
        }
        try:
            completed = self.executor(
                list(argv),
                shell=False,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=dict(environment),
            )
            stdout = sanitize_probe_output(getattr(completed, "stdout", ""))
            stderr = sanitize_probe_output(getattr(completed, "stderr", ""))
            return_code = int(getattr(completed, "returncode"))
            base.update(
                {
                    "status": "passed" if return_code == 0 else "failed",
                    "duration_ms": max(
                        0, int((time.monotonic() - started) * 1000)
                    ),
                    "return_code": return_code,
                    "stdout_sha256": hashlib.sha256(
                        stdout.encode("utf-8")
                    ).hexdigest(),
                    "stdout_bytes": len(stdout.encode("utf-8")),
                    "stderr_sha256": hashlib.sha256(
                        stderr.encode("utf-8")
                    ).hexdigest(),
                    "stderr_bytes": len(stderr.encode("utf-8")),
                }
            )
            if return_code != 0:
                base["failure"] = {
                    "code": "runtime_probe_failed",
                    "retryable": False,
                    "next_route": "inspect-exact-runtime-probe",
                }
        except (FileNotFoundError, subprocess.TimeoutExpired) as error:
            if isinstance(error, FileNotFoundError):
                failure = {
                    "code": "runtime_missing",
                    "retryable": False,
                    "next_route": "install-exact-runtime",
                }
            else:
                failure = {
                    "code": "timeout",
                    "retryable": True,
                    "next_route": "retry-bounded-runtime-probe",
                }
            base.update(
                {
                    "status": "failed",
                    "duration_ms": max(
                        0, int((time.monotonic() - started) * 1000)
                    ),
                    "return_code": None,
                    "failure": failure,
                }
            )
        return self._persist(base)

    def _persist(self, receipt: dict) -> dict:
        payload = _canonical_bytes(receipt)
        digest = hashlib.sha256(payload).hexdigest()
        self.receipt_root.mkdir(parents=True, exist_ok=True)
        safe_id = "".join(
            character if character.isalnum() or character in "._-" else "-"
            for character in str(receipt["probe_id"])
        )
        path = self.receipt_root / f"{safe_id}-{digest[:16]}.json"
        if path.exists() and path.read_bytes() != payload:
            raise ValueError("runtime receipt path collision")
        path.write_bytes(payload)
        return {
            "receipt": receipt,
            "receipt_ref": path.as_posix(),
            "receipt_sha256": digest,
        }
