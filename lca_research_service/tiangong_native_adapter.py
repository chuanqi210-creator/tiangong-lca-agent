"""Locked local-file adapter for the adopted TianGong TIDAS SDK contract."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Callable, Mapping


_PACKAGE = "@tiangong-lca/tidas-sdk"
_CAPABILITY = "tiangong.native.tidas-sdk"
_INPUT_CONTRACT = "tiangong.native.tidas-sdk.input/v1"
_MAX_METADATA_BYTES = 4 * 1024 * 1024


def _read_regular_file(root: Path, relative: Path) -> bytes:
    root = Path(root).resolve(strict=True)
    candidate = root / relative
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError("TianGong runtime metadata escaped its root") from error
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(candidate, flags)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > _MAX_METADATA_BYTES
        ):
            raise ValueError("TianGong runtime metadata file is unsafe")
        content = bytearray()
        while len(content) <= _MAX_METADATA_BYTES:
            chunk = os.read(
                descriptor,
                min(64 * 1024, _MAX_METADATA_BYTES + 1 - len(content)),
            )
            if not chunk:
                break
            content.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(content) != before.st_size
            or len(content) > _MAX_METADATA_BYTES
            or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_nlink,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_nlink,
            )
        ):
            raise ValueError("TianGong runtime metadata changed while reading")
        return bytes(content)
    finally:
        os.close(descriptor)


class TianGongTidasSdkContractAdapter:
    """Read one exact installed SDK identity without producing LCA science."""

    def __init__(
        self,
        runtime_node_root: Path,
        *,
        runtime_timeout_seconds: float = 30.0,
        remaining_budget: Callable[[], float] | None = None,
        answer_timing: object = None,
    ):
        self.runtime_node_root = Path(runtime_node_root).resolve(strict=False)
        self.runtime_timeout_seconds = float(runtime_timeout_seconds)
        self.remaining_budget = remaining_budget
        self.answer_timing = answer_timing

    def supports_capability(self, descriptor: Mapping[str, object]) -> bool:
        """Advertise only the one source-bound contract this adapter implements."""

        return (
            descriptor.get("capability_id") == _CAPABILITY
            and descriptor.get("adapter_id") == "static.tidas-sdk"
            and descriptor.get("input_contract") == _INPUT_CONTRACT
        )

    def runtime_available_for_capability(
        self, descriptor: Mapping[str, object]
    ) -> bool:
        if not self.supports_capability(descriptor):
            return False
        try:
            self._verified_runtime_metadata(descriptor)
        except (OSError, ValueError):
            return False
        return True

    def with_runtime_timeout(
        self, seconds: float
    ) -> "TianGongTidasSdkContractAdapter":
        return TianGongTidasSdkContractAdapter(
            self.runtime_node_root,
            runtime_timeout_seconds=float(seconds),
            remaining_budget=self.remaining_budget,
            answer_timing=self.answer_timing,
        )

    def with_remaining_budget(
        self, remaining: Callable[[], float]
    ) -> "TianGongTidasSdkContractAdapter":
        return TianGongTidasSdkContractAdapter(
            self.runtime_node_root,
            runtime_timeout_seconds=self.runtime_timeout_seconds,
            remaining_budget=remaining,
            answer_timing=self.answer_timing,
        )

    def with_answer_timing(
        self, timing: object
    ) -> "TianGongTidasSdkContractAdapter":
        return TianGongTidasSdkContractAdapter(
            self.runtime_node_root,
            runtime_timeout_seconds=self.runtime_timeout_seconds,
            remaining_budget=self.remaining_budget,
            answer_timing=timing,
        )

    def execute(
        self,
        descriptor: Mapping[str, object],
        payload: Mapping[str, object],
        *,
        execution_identity: Mapping[str, object] | None = None,
        operation_receipt: Mapping[str, object] | None = None,
    ) -> dict:
        if self.runtime_timeout_seconds <= 0:
            raise ValueError("TianGong SDK runtime timeout is exhausted")
        if self.remaining_budget is not None and self.remaining_budget() <= 0:
            raise ValueError("TianGong SDK answer budget is exhausted")
        source = descriptor.get("source_lock")
        if (
            not self.supports_capability(descriptor)
            or descriptor.get("effect") != "local_read"
            or descriptor.get("input_contract") != _INPUT_CONTRACT
            or not isinstance(source, Mapping)
            or source.get("source_id") != "tiangong.sdk.npm"
            or source.get("identity") != _PACKAGE
            or source.get("version") != "0.1.45"
            or not isinstance(source.get("integrity"), str)
            or not source["integrity"].startswith("sha512-")
        ):
            raise ValueError("TianGong SDK source lock drifted")
        if (
            set(payload) != {"contract", "requested_state_change"}
            or payload.get("contract") != _INPUT_CONTRACT
            or payload.get("requested_state_change")
            != "inspect_tidas_sdk_contract"
        ):
            raise ValueError("TianGong SDK request contract drifted")

        lock_bytes, package_bytes = self._verified_runtime_metadata(descriptor)
        source = descriptor["source_lock"]
        content = {
            "contract": (
                "lca_research_kernel.tiangong_native_sdk_contract_observation/v1"
            ),
            "provider": "TianGong",
            "package_identity": source["identity"],
            "package_version": source["version"],
            "package_integrity": source["integrity"],
            "package_json_sha256": hashlib.sha256(package_bytes).hexdigest(),
            "package_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
            "runtime_verified": True,
            "requested_state_change": payload["requested_state_change"],
            "method_boundary": "not_selected",
            "provider_boundary": "not_selected",
            "unit_boundary": "not_evaluated",
            "scientific_result_created": False,
        }
        return {
            "content": content,
            "source_identity": source["identity"],
            "source_version": source["version"],
        }

    def _verified_runtime_metadata(
        self, descriptor: Mapping[str, object]
    ) -> tuple[bytes, bytes]:
        source = descriptor.get("source_lock")
        if (
            not self.supports_capability(descriptor)
            or not isinstance(source, Mapping)
            or source.get("source_id") != "tiangong.sdk.npm"
            or source.get("identity") != _PACKAGE
            or source.get("version") != "0.1.45"
            or not isinstance(source.get("integrity"), str)
            or not source["integrity"].startswith("sha512-")
        ):
            raise ValueError("TianGong SDK source lock drifted")
        lock_bytes = _read_regular_file(
            self.runtime_node_root, Path("package-lock.json")
        )
        package_bytes = _read_regular_file(
            self.runtime_node_root,
            Path("node_modules/@tiangong-lca/tidas-sdk/package.json"),
        )
        try:
            lock = json.loads(lock_bytes)
            package = json.loads(package_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("TianGong SDK runtime metadata is invalid") from error
        packages = lock.get("packages") if isinstance(lock, Mapping) else None
        installed = (
            packages.get("node_modules/@tiangong-lca/tidas-sdk")
            if isinstance(packages, Mapping)
            else None
        )
        root_package = packages.get("") if isinstance(packages, Mapping) else None
        dependencies = (
            root_package.get("dependencies")
            if isinstance(root_package, Mapping)
            else None
        )
        if (
            not isinstance(package, Mapping)
            or package.get("name") != source["identity"]
            or package.get("version") != source["version"]
            or not isinstance(installed, Mapping)
            or installed.get("version") != source["version"]
            or installed.get("integrity") != source["integrity"]
            or not isinstance(dependencies, Mapping)
            or dependencies.get(_PACKAGE) != source["version"]
        ):
            raise ValueError("TianGong SDK package identity or version drifted")
        return lock_bytes, package_bytes
