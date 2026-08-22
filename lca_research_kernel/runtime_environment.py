"""Fail-closed contract for D-18 capability runtime environment closure."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Mapping

from .capability_catalog import CapabilityCatalog


CONTRACT_PATH = Path("lca_research_kernel/contracts/runtime-environment-contract.json")
CONTRACT_ID = "lca_research_kernel.runtime_environment_contract/v1"
MATRIX_ID = "lca_research_kernel.capability_runtime_readiness_matrix/v1"
ROW_FIELDS = {
    "capability_id",
    "catalog_disposition",
    "source_lock_id",
    "adapter_id",
    "effect",
    "readiness_status",
    "evidence",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unavailable or invalid") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


class RuntimeEnvironmentContract:
    """Exact catalog binding and readiness-matrix validator."""

    def __init__(
        self,
        *,
        root: Path,
        contract: Mapping[str, object],
        catalog: CapabilityCatalog,
    ):
        self.root = Path(root)
        self.contract = deepcopy(dict(contract))
        self.catalog = catalog
        self.descriptors = {
            row["capability_id"]: row for row in catalog.list_descriptors()
        }
        self.capability_ids = set(self.descriptors)
        self.catalog_snapshot_sha256 = catalog.snapshot_sha256
        self.source_lock_file_sha256 = str(
            self.contract["source_lock"]["file_sha256"]  # type: ignore[index]
        )
        self.executable_gates = tuple(self.contract["executable_gates"])  # type: ignore[arg-type]
        self.boundary_gates = tuple(self.contract["boundary_gates"])  # type: ignore[arg-type]
        self.forbidden_operations = tuple(self.contract["forbidden_operations"])  # type: ignore[arg-type]

    @classmethod
    def load(cls, root: Path) -> "RuntimeEnvironmentContract":
        root = Path(root).resolve()
        contract = _read_object(root / CONTRACT_PATH, "runtime environment contract")
        if contract.get("contract") != CONTRACT_ID:
            raise ValueError("runtime environment contract identity is unsupported")
        expected_fields = {
            "contract",
            "contract_id",
            "catalog",
            "source_lock",
            "runtime_root",
            "readiness_statuses",
            "executable_gates",
            "boundary_gates",
            "forbidden_operations",
            "completion_policy",
            "evidence_policy",
            "completion_next_route",
            "waiting_next_route",
        }
        if set(contract) != expected_fields:
            raise ValueError("runtime environment contract fields are incomplete")
        catalog_ref = contract.get("catalog")
        source_ref = contract.get("source_lock")
        if not isinstance(catalog_ref, Mapping) or not isinstance(
            source_ref, Mapping
        ):
            raise ValueError("runtime environment source references are absent")
        catalog_path = root / str(catalog_ref.get("path", ""))
        source_path = root / str(source_ref.get("path", ""))
        if _sha256(catalog_path) != catalog_ref.get("file_sha256"):
            raise ValueError("runtime environment catalog file drifted")
        if _sha256(source_path) != source_ref.get("file_sha256"):
            raise ValueError("runtime environment source lock file drifted")
        catalog = CapabilityCatalog.from_files(catalog_path, source_path)
        if catalog.snapshot_sha256 != catalog_ref.get("snapshot_sha256"):
            raise ValueError("runtime environment catalog snapshot drifted")
        return cls(root=root, contract=contract, catalog=catalog)

    def new_matrix(self, *, observed_at: str) -> dict:
        rows = []
        for capability_id in sorted(self.descriptors):
            descriptor = self.descriptors[capability_id]
            rows.append(
                {
                    "capability_id": capability_id,
                    "catalog_disposition": descriptor["disposition"],
                    "source_lock_id": descriptor["source_lock_id"],
                    "adapter_id": descriptor["adapter_id"],
                    "effect": descriptor["effect"],
                    "readiness_status": "unverified",
                    "evidence": {},
                }
            )
        return {
            "contract": MATRIX_ID,
            "contract_id": self.contract["contract_id"],
            "catalog_snapshot_sha256": self.catalog_snapshot_sha256,
            "source_lock_file_sha256": self.source_lock_file_sha256,
            "observed_at": observed_at,
            "capabilities": rows,
        }

    def validate_matrix(
        self, matrix: Mapping[str, object], *, require_closed: bool = False
    ) -> list[str]:
        errors: list[str] = []
        if matrix.get("contract") != MATRIX_ID:
            errors.append("runtime matrix contract identity is unsupported")
        if matrix.get("contract_id") != self.contract.get("contract_id"):
            errors.append("runtime matrix contract id drifted")
        if matrix.get("catalog_snapshot_sha256") != self.catalog_snapshot_sha256:
            errors.append("runtime matrix catalog snapshot drifted")
        if matrix.get("source_lock_file_sha256") != self.source_lock_file_sha256:
            errors.append("runtime matrix source lock drifted")
        rows = matrix.get("capabilities")
        if not isinstance(rows, list):
            return [*errors, "runtime matrix capabilities are absent"]
        indexed: dict[str, Mapping[str, object]] = {}
        for raw in rows:
            if not isinstance(raw, Mapping):
                errors.append("runtime matrix row is invalid")
                continue
            capability_id = raw.get("capability_id")
            if not isinstance(capability_id, str):
                errors.append("runtime matrix capability id is absent")
                continue
            if capability_id in indexed:
                errors.append(f"duplicate runtime matrix capability: {capability_id}")
                continue
            indexed[capability_id] = raw
        if set(indexed) != self.capability_ids:
            errors.append("runtime matrix capability coverage drifted")
        policy = self.contract["completion_policy"]
        statuses = set(self.contract["readiness_statuses"])
        for capability_id in sorted(self.capability_ids.intersection(indexed)):
            row = indexed[capability_id]
            descriptor = self.descriptors[capability_id]
            if set(row) != ROW_FIELDS:
                errors.append(f"{capability_id} runtime row fields drifted")
                continue
            for field in (
                "catalog_disposition",
                "source_lock_id",
                "adapter_id",
                "effect",
            ):
                expected = descriptor[field if field != "catalog_disposition" else "disposition"]
                if row.get(field) != expected:
                    errors.append(f"{capability_id} {field} drifted")
            status = row.get("readiness_status")
            if status not in statuses:
                errors.append(f"{capability_id} readiness status is unsupported")
                continue
            expected_status = policy[descriptor["disposition"]]  # type: ignore[index]
            if status == "boundary_verified_nonlocal" and expected_status != status:
                errors.append(
                    f"{capability_id} is locally applicable and cannot be closed as nonlocal"
                )
            if (
                descriptor["disposition"] == "human_action_required"
                and status == "executable_verified"
            ):
                errors.append(
                    f"{capability_id} is human-owned and cannot be executed autonomously"
                )
            evidence = row.get("evidence")
            if not isinstance(evidence, Mapping):
                errors.append(f"{capability_id} runtime evidence is absent")
                continue
            if status == "executable_verified":
                self._validate_evidence(
                    capability_id, evidence, self.executable_gates, errors
                )
            elif status == "boundary_verified_nonlocal":
                self._validate_evidence(
                    capability_id, evidence, self.boundary_gates, errors
                )
            elif status == "inactive_verified":
                self._validate_evidence(
                    capability_id,
                    evidence,
                    ("immutable_pointer", "disposition_reason"),
                    errors,
                )
            if require_closed and status != expected_status:
                errors.append(
                    f"{capability_id} is not closed: expected {expected_status}, got {status}"
                )
        return errors

    def _validate_evidence(
        self,
        capability_id: str,
        evidence: Mapping[str, object],
        gates: tuple[str, ...],
        errors: list[str],
    ) -> None:
        accepted_kind = self.contract["evidence_policy"]["accepted_kind"]  # type: ignore[index]
        for gate in gates:
            value = evidence.get(gate)
            if not isinstance(value, Mapping):
                errors.append(f"{capability_id} {gate} evidence is absent")
                continue
            if value.get("kind") != accepted_kind:
                errors.append(
                    f"{capability_id} {gate} evidence is not a real runtime probe"
                )
            reference = value.get("receipt_ref")
            digest = value.get("receipt_sha256")
            if not isinstance(reference, str) or not reference:
                errors.append(f"{capability_id} {gate} receipt reference is absent")
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                errors.append(f"{capability_id} {gate} receipt digest is invalid")
