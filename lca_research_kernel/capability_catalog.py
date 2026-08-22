"""Versioned TianGong capability catalog with exact source locks."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Mapping


CATALOG_CONTRACT = "lca_research_kernel.capability_catalog/v1"
DESCRIPTOR_CONTRACT = "lca_research_kernel.capability_descriptor/v1"
SOURCE_LOCK_CONTRACT = "lca_research_kernel.source_lock/v1"
KINDS = {
    "specification",
    "data",
    "native_tool",
    "skill",
    "mcp_tool",
    "build",
    "validation",
    "calculation",
    "evidence",
}
DISPOSITIONS = {
    "integrated",
    "conditional_runtime",
    "human_action_required",
    "upstream_owned",
    "legacy_adapter_only",
    "evaluation_fixture",
    "parked",
    "retired",
}
AVAILABILITY = {
    "available_local",
    "available_public_remote",
    "requires_runtime",
    "requires_credential",
    "unavailable",
}
FLOATING_IDENTITIES = {"latest", "main", "master", "head", "*", "develop", "trunk"}
SOURCE_FIELDS = {
    "source_id",
    "source_type",
    "owner",
    "identity",
    "version",
    "integrity",
    "source_url",
    "adoption",
}
DESCRIPTOR_FIELDS = {
    "contract",
    "capability_id",
    "kind",
    "provider",
    "authority_tier",
    "source_lock_id",
    "adapter_id",
    "transport",
    "entrypoint",
    "input_contract",
    "output_contract",
    "effect",
    "responsibility_tags",
    "credential_policy",
    "availability",
    "disposition",
    "timeout_seconds",
    "retry_policy",
    "idempotency",
    "recovery_route",
    "next_route",
    "allowed_use",
    "forbidden_claims",
    "verification",
    "supersedes",
}


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _read_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unavailable or invalid") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _validate_source(value: Mapping[str, object]) -> dict:
    if set(value) != SOURCE_FIELDS:
        raise ValueError("source lock fields are incomplete or unsupported")
    source = deepcopy(dict(value))
    for key in SOURCE_FIELDS - {"integrity", "version"}:
        _text(source.get(key), f"source lock {key}")
    version_value = source.get("version")
    if not isinstance(version_value, str) or not version_value.strip():
        raise ValueError("exact source version is required")
    version = version_value.strip()
    if version.casefold() in FLOATING_IDENTITIES:
        raise ValueError("exact source version is required")
    integrity = source.get("integrity")
    if not isinstance(integrity, str) or not integrity.strip():
        raise ValueError("source integrity is required")
    if source["adoption"] not in {"adopted", "historical_replay"}:
        raise ValueError("source adoption is unsupported")
    return source


def _validate_descriptor(
    value: Mapping[str, object], sources: Mapping[str, Mapping[str, object]]
) -> dict:
    if set(value) != DESCRIPTOR_FIELDS:
        raise ValueError("capability descriptor fields are incomplete or unsupported")
    descriptor = deepcopy(dict(value))
    if descriptor.get("contract") != DESCRIPTOR_CONTRACT:
        raise ValueError("capability descriptor contract is unsupported")
    for key in (
        "capability_id",
        "provider",
        "authority_tier",
        "source_lock_id",
        "adapter_id",
        "transport",
        "input_contract",
        "output_contract",
        "effect",
        "credential_policy",
        "idempotency",
        "recovery_route",
        "next_route",
    ):
        _text(descriptor.get(key), f"capability {key}")
    if descriptor.get("kind") not in KINDS:
        raise ValueError("capability kind is unsupported")
    if descriptor.get("disposition") not in DISPOSITIONS:
        raise ValueError("capability disposition is unsupported")
    if descriptor.get("availability") not in AVAILABILITY:
        raise ValueError("capability availability is unsupported")
    source_id = str(descriptor["source_lock_id"])
    if source_id not in sources:
        raise ValueError("capability source lock is absent")
    if not isinstance(descriptor.get("entrypoint"), (str, list, dict)):
        raise ValueError("capability entrypoint is invalid")
    for key in ("responsibility_tags", "allowed_use", "forbidden_claims", "supersedes"):
        values = descriptor.get(key)
        if not isinstance(values, list) or any(
            not isinstance(item, str) or not item.strip() for item in values
        ):
            raise ValueError(f"capability {key} is invalid")
    timeout = descriptor.get("timeout_seconds")
    if not isinstance(timeout, int) or timeout <= 0:
        raise ValueError("capability timeout is invalid")
    retry = descriptor.get("retry_policy")
    if (
        not isinstance(retry, Mapping)
        or set(retry) != {"max_attempts", "retryable_codes"}
        or not isinstance(retry.get("max_attempts"), int)
        or retry["max_attempts"] < 1
        or not isinstance(retry.get("retryable_codes"), list)
    ):
        raise ValueError("capability retry policy is invalid")
    if not isinstance(descriptor.get("verification"), Mapping):
        raise ValueError("capability verification is invalid")
    descriptor["source_lock"] = deepcopy(dict(sources[source_id]))
    return descriptor


class CapabilityCatalog:
    def __init__(
        self,
        catalog: Mapping[str, object],
        source_lock: Mapping[str, object],
    ):
        if source_lock.get("contract") != SOURCE_LOCK_CONTRACT:
            raise ValueError("source lock contract is unsupported")
        if set(source_lock) != {"contract", "lock_id", "sources"}:
            raise ValueError("source lock fields are incomplete or unsupported")
        lock_id = _text(source_lock.get("lock_id"), "source lock id")
        raw_sources = source_lock.get("sources")
        if not isinstance(raw_sources, list):
            raise ValueError("source lock sources must be a list")
        sources: dict[str, dict] = {}
        for row in raw_sources:
            if not isinstance(row, Mapping):
                raise ValueError("source lock row must be an object")
            normalized = _validate_source(row)
            source_id = str(normalized["source_id"])
            if source_id in sources:
                raise ValueError("duplicate source lock id")
            sources[source_id] = normalized
        if catalog.get("contract") != CATALOG_CONTRACT:
            raise ValueError("capability catalog contract is unsupported")
        if set(catalog) != {
            "contract",
            "catalog_id",
            "source_lock_id",
            "capabilities",
        }:
            raise ValueError("capability catalog fields are incomplete or unsupported")
        _text(catalog.get("catalog_id"), "capability catalog id")
        if catalog.get("source_lock_id") != lock_id:
            raise ValueError("capability catalog source lock id drifted")
        raw_descriptors = catalog.get("capabilities")
        if not isinstance(raw_descriptors, list):
            raise ValueError("capability catalog capabilities must be a list")
        descriptors: dict[str, dict] = {}
        for row in raw_descriptors:
            if not isinstance(row, Mapping):
                raise ValueError("capability descriptor must be an object")
            normalized = _validate_descriptor(row, sources)
            capability_id = str(normalized["capability_id"])
            if capability_id in descriptors:
                raise ValueError("duplicate capability id")
            descriptors[capability_id] = normalized
        self._catalog = deepcopy(dict(catalog))
        self._source_lock = deepcopy(dict(source_lock))
        self._descriptors = descriptors
        self.snapshot_sha256 = _canonical_sha256(
            {"catalog": self._catalog, "source_lock": self._source_lock}
        )

    @classmethod
    def from_files(cls, catalog_path: Path, source_lock_path: Path) -> "CapabilityCatalog":
        return cls(
            _read_object(Path(catalog_path), "capability catalog"),
            _read_object(Path(source_lock_path), "source lock"),
        )

    def get(self, capability_id: str) -> dict:
        if capability_id not in self._descriptors:
            raise ValueError(f"unknown capability: {capability_id}")
        return deepcopy(self._descriptors[capability_id])

    def list_descriptors(self) -> list[dict]:
        return [
            deepcopy(self._descriptors[key]) for key in sorted(self._descriptors)
        ]
