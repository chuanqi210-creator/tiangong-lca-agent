"""Compile and verify immutable TianGong authority receipts."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Dict, List, Mapping, Sequence


JsonObject = Dict[str, object]

AUTHORITY_RECEIPT_CONTRACT = "lca_research_kernel.tiangong_authority_receipt/v1"
_RECEIPT_STATUS = "accepted_immutable_authority_receipt"
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LATEST_TOKEN = re.compile(r"(^|[@/:?=&._-])latest($|[@/:?=&._-])", re.IGNORECASE)
_SECRET_KEY_TOKENS = {
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "authorization",
    "authorization_header",
    "cookie",
    "password",
    "secret",
    "secret_value",
    "token",
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return copy.deepcopy(value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _sha256(value: object, label: str) -> str:
    digest = _text(value, label)
    if not _HEX_SHA256.fullmatch(digest):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return digest


def _positive_bytes(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _utc_timestamp(value: object, label: str) -> str:
    token = _text(value, label)
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} must be a valid UTC timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{label} must have UTC offset zero")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_relative_path(value: object, label: str) -> str:
    token = _text(value, label).replace("\\", "/")
    path = PurePosixPath(token)
    if path.is_absolute() or token.startswith("/"):
        raise ValueError(f"{label} must be workspace-relative")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{label} contains path traversal")
    if str(path) != token:
        raise ValueError(f"{label} must be a normalized workspace-relative path")
    return token


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")


def _reject_secret_like_keys(value: object, path: str = "receipt") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = _normalized_key(key)
            if normalized in _SECRET_KEY_TOKENS or any(
                normalized.endswith(f"_{token}")
                for token in _SECRET_KEY_TOKENS
            ):
                raise ValueError(f"secret-like key is forbidden at {path}.{key}")
            _reject_secret_like_keys(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_secret_like_keys(child, f"{path}[{index}]")


def _reject_floating_latest(value: object, path: str = "receipt") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            _reject_floating_latest(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_floating_latest(child, f"{path}[{index}]")
    elif isinstance(value, str) and _LATEST_TOKEN.search(value.strip()):
        raise ValueError(f"floating latest is forbidden at {path}")


def _versioned_identity(value: object, label: str) -> JsonObject:
    row = _object(value, label)
    return {
        "id": _text(row.get("id"), f"{label}.id"),
        "version": _text(row.get("version"), f"{label}.version"),
    }


def _artifact_binding(value: object, label: str, *, role_required: bool) -> JsonObject:
    row = _object(value, label)
    result: JsonObject = {}
    if role_required:
        result["role"] = _text(row.get("role"), f"{label}.role")
    result.update(
        {
            "path": _safe_relative_path(row.get("path"), f"{label}.path"),
            "sha256": _sha256(row.get("sha256"), f"{label}.sha256"),
            "bytes": _positive_bytes(row.get("bytes"), f"{label}.bytes"),
            "retrieval_route": _text(
                row.get("retrieval_route"), f"{label}.retrieval_route"
            ),
        }
    )
    return result


def _normalize_capability_adoption(value: object) -> JsonObject:
    row = _object(value, "capability_adoption")
    if row.get("status") != "adopted_read_only_surface":
        raise ValueError("capability_adoption.status must be adopted_read_only_surface")
    if row.get("historical_runtime_rewritten") is not False:
        raise ValueError("historical TianGong runtime must not be rewritten")
    if row.get("remote_write_authorized") is not False:
        raise ValueError("TianGong remote write must remain unauthorized")
    return {
        "contract": _text(row.get("contract"), "capability_adoption.contract"),
        "adoption_id": _text(row.get("adoption_id"), "capability_adoption.adoption_id"),
        "adoption_content_sha256": _sha256(
            row.get("adoption_content_sha256"),
            "capability_adoption.adoption_content_sha256",
        ),
        "status": "adopted_read_only_surface",
        "current_package_spec": _text(
            row.get("current_package_spec"),
            "capability_adoption.current_package_spec",
        ),
        "current_version": _text(
            row.get("current_version"), "capability_adoption.current_version"
        ),
        "historical_package_spec": _text(
            row.get("historical_package_spec"),
            "capability_adoption.historical_package_spec",
        ),
        "historical_runtime_rewritten": False,
        "remote_write_authorized": False,
    }


def _normalize_authority(value: object, adoption: Mapping[str, object]) -> JsonObject:
    row = _object(value, "authority")
    if row.get("data_space") != "Open Data":
        raise ValueError("authority.data_space must be Open Data")
    if row.get("publication_state") != "published_reviewed":
        raise ValueError(
            "authority.publication_state must be published_reviewed"
        )

    functional_unit = _object(row.get("functional_unit"), "authority.functional_unit")
    reference_flow = _versioned_identity(
        row.get("reference_flow"), "authority.reference_flow"
    )
    if _text(
        functional_unit.get("reference_flow_id"),
        "authority.functional_unit.reference_flow_id",
    ) != reference_flow["id"]:
        raise ValueError("functional unit reference Flow identity does not join")

    method = _object(row.get("lcia_method"), "authority.lcia_method")
    runtime = _object(row.get("tool_runtime"), "authority.tool_runtime")
    if _text(runtime.get("adoption_id"), "authority.tool_runtime.adoption_id") != adoption[
        "adoption_id"
    ]:
        raise ValueError("tool runtime adoption_id does not join capability adoption")
    if _text(runtime.get("package_spec"), "authority.tool_runtime.package_spec") != adoption[
        "current_package_spec"
    ]:
        raise ValueError("tool runtime package spec does not join capability adoption")
    if _text(runtime.get("version"), "authority.tool_runtime.version") != adoption[
        "current_version"
    ]:
        raise ValueError("tool runtime version does not join capability adoption")

    predecessor_value = row.get("predecessor")
    predecessor = None
    if predecessor_value is not None:
        predecessor_row = _object(predecessor_value, "authority.predecessor")
        predecessor = {
            "receipt_id": _text(
                predecessor_row.get("receipt_id"), "authority.predecessor.receipt_id"
            ),
            "dataset_version": _text(
                predecessor_row.get("dataset_version"),
                "authority.predecessor.dataset_version",
            ),
        }

    freshness = _object(row.get("freshness"), "authority.freshness")
    normalized: JsonObject = {
        "authority_kind": _text(row.get("authority_kind"), "authority.authority_kind"),
        "data_space": "Open Data",
        "publication_state": "published_reviewed",
        "dataset": _versioned_identity(row.get("dataset"), "authority.dataset"),
        "model": _versioned_identity(row.get("model"), "authority.model"),
        "functional_unit": {
            "amount": _text(
                functional_unit.get("amount"), "authority.functional_unit.amount"
            ),
            "unit": _text(
                functional_unit.get("unit"), "authority.functional_unit.unit"
            ),
            "reference_flow_id": reference_flow["id"],
        },
        "reference_flow": reference_flow,
        "provider_snapshot": _versioned_identity(
            row.get("provider_snapshot"), "authority.provider_snapshot"
        ),
        "lcia_method": {
            "id": _text(method.get("id"), "authority.lcia_method.id"),
            "version": _text(method.get("version"), "authority.lcia_method.version"),
            "characterization_set_version": _text(
                method.get("characterization_set_version"),
                "authority.lcia_method.characterization_set_version",
            ),
        },
        "tidas_schema": _versioned_identity(
            row.get("tidas_schema"), "authority.tidas_schema"
        ),
        "tool_runtime": {
            "adoption_id": adoption["adoption_id"],
            "package_spec": adoption["current_package_spec"],
            "version": adoption["current_version"],
            "executable_sha256": _sha256(
                runtime.get("executable_sha256"),
                "authority.tool_runtime.executable_sha256",
            ),
        },
        "predecessor": predecessor,
        "version_history_pointer": _artifact_binding(
            row.get("version_history_pointer"),
            "authority.version_history_pointer",
            role_required=False,
        ),
        "freshness": {
            "status": _text(freshness.get("status"), "authority.freshness.status"),
            "checked_at_utc": _utc_timestamp(
                freshness.get("checked_at_utc"),
                "authority.freshness.checked_at_utc",
            ),
        },
    }
    return normalized


def _normalize_source_bindings(
    source_bindings: Sequence[Mapping[str, object]],
) -> List[JsonObject]:
    if isinstance(source_bindings, (str, bytes)) or not isinstance(
        source_bindings, Sequence
    ):
        raise ValueError("source_bindings must be a sequence")
    rows = [
        _artifact_binding(value, f"source_bindings[{index}]", role_required=True)
        for index, value in enumerate(source_bindings)
    ]
    if not rows:
        raise ValueError("source_bindings must not be empty")
    rows.sort(key=lambda item: (str(item["role"]), str(item["path"]), str(item["sha256"])))
    identities = [(item["role"], item["path"]) for item in rows]
    if len(identities) != len(set(identities)):
        raise ValueError("source_bindings contain duplicate role/path identities")
    return rows


def receipt_content_sha256(receipt: Mapping[str, object]) -> str:
    """Return the digest of a receipt body, excluding only its two derived fields."""

    body = _object(receipt, "receipt")
    body.pop("receipt_id", None)
    body.pop("receipt_content_sha256", None)
    return _sha256_bytes(_canonical_bytes(body))


def compile_authority_receipt(
    *,
    capability_adoption: Mapping[str, object],
    authority: Mapping[str, object],
    source_bindings: Sequence[Mapping[str, object]],
    observed_at_utc: str,
) -> JsonObject:
    """Compile one exact, immutable TianGong Open Data authority binding."""

    raw_input = {
        "capability_adoption": capability_adoption,
        "authority": authority,
        "source_bindings": source_bindings,
        "observed_at_utc": observed_at_utc,
    }
    _reject_secret_like_keys(raw_input)
    _reject_floating_latest(raw_input)

    adoption = _normalize_capability_adoption(capability_adoption)
    normalized_authority = _normalize_authority(authority, adoption)
    normalized_sources = _normalize_source_bindings(source_bindings)
    body: JsonObject = {
        "contract": AUTHORITY_RECEIPT_CONTRACT,
        "status": _RECEIPT_STATUS,
        "observed_at_utc": _utc_timestamp(observed_at_utc, "observed_at_utc"),
        "capability_adoption": adoption,
        "authority": normalized_authority,
        "source_bindings": normalized_sources,
    }
    _reject_secret_like_keys(body)
    _reject_floating_latest(body)
    digest = _sha256_bytes(_canonical_bytes(body))
    receipt = copy.deepcopy(body)
    receipt["receipt_id"] = digest[:16]
    receipt["receipt_content_sha256"] = digest
    return receipt


def verify_authority_receipt(receipt: Mapping[str, object]) -> JsonObject:
    """Verify receipt semantics, canonical shape, identity, and content digest."""

    row = _object(receipt, "receipt")
    if row.get("contract") != AUTHORITY_RECEIPT_CONTRACT:
        raise ValueError("receipt contract is unsupported")
    if row.get("status") != _RECEIPT_STATUS:
        raise ValueError("receipt status is not accepted")
    receipt_id = _text(row.get("receipt_id"), "receipt.receipt_id")
    recorded_digest = _sha256(
        row.get("receipt_content_sha256"), "receipt.receipt_content_sha256"
    )
    _reject_secret_like_keys(row)
    _reject_floating_latest(row)

    expected_digest = receipt_content_sha256(row)
    if recorded_digest != expected_digest:
        raise ValueError("receipt content digest does not match governed content")
    if receipt_id != expected_digest[:16]:
        raise ValueError("receipt_id does not match content digest")

    canonical = compile_authority_receipt(
        capability_adoption=_object(
            row.get("capability_adoption"), "receipt.capability_adoption"
        ),
        authority=_object(row.get("authority"), "receipt.authority"),
        source_bindings=row.get("source_bindings"),
        observed_at_utc=_text(row.get("observed_at_utc"), "receipt.observed_at_utc"),
    )
    if canonical != row:
        raise ValueError("receipt is not in canonical form")
    return copy.deepcopy(row)
