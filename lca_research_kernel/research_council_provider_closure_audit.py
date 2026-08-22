"""Deterministic audit of TianGong lifecycle-model references for Council candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple
from urllib.parse import urlparse


JsonObject = Dict[str, object]

_QUALIFICATION_CONTRACT = (
    "lca_research_kernel.research_council_benchmark_qualification/v2"
)
_FRONTEND_CONTRACT = (
    "lca_research_kernel.tiangong_reference_lookup_frontend/v2"
)
_CAPTURE_CONTRACT = "lca_research_kernel.tiangong_reference_lookup_capture/v2"
_AUDIT_CONTRACT = "lca_research_kernel.tiangong_provider_closure_audit/v2"
_RPC_NAME = "search_dataset_json_uuid_mentions"
_DATA_SOURCE = "tg"
_ENTITY_KINDS = ["lifecyclemodel"]
_MAXIMUM_RESULTS = 50
_CLI_PACKAGE = "@tiangong-lca/cli"
_CLI_VERSION = "0.0.29"
_ADOPTION_ID = "4069fd724039d86c"
_ADOPTION_CONTENT_SHA256 = (
    "4069fd724039d86cf0a8d9bd95e236a9da3c9b1f7903cf7959c5f5ffcada51a7"
)
_CLI_NPM_INTEGRITY = (
    "sha512-iee5B/rzTTZxI7XWxYZnyzcEp1r8jTKw6KBn8EcwEatGpzv1xw8XJdFKv+"
    "JQ15GFeV0uJSz5AJ/AGf5X+fQ6hg=="
)
_CLI_PACKAGE_LOCK_SHA256 = (
    "d8a014be7b26295fe04b6b52102c5901c9543ed2df98a64bbdeae3adb7f8abdf"
)
_CLI_NODE_MODULES_TREE_SHA256 = (
    "9b4e599b73a2eb9186e8d1ba3b37ab32d75c8b254b2908cd8004b1d3eb72f188"
)
_CAPTURE_TOOL_SHA256 = (
    "958e2aaf62be80fccd1aad1e5b6c671443927ea2008c6440ec76aa67d61f084f"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)+$")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_SECRET_KEY_MARKERS = {
    "accesstoken",
    "apikey",
    "apitoken",
    "authorization",
    "clientsecret",
    "credential",
    "credentials",
    "password",
    "passwd",
    "privatekey",
    "refreshtoken",
    "secret",
    "token",
}
_SECRET_VALUE_PATTERNS = (
    re.compile(r"authorization\s*:", re.IGNORECASE),
    re.compile(r"bearer(?:\s+token|\s+[a-z0-9._~+/=-]+)", re.IGNORECASE),
    re.compile(r"(?:api[_ -]?key|password|secret|token)\s*[=:]", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_+/=-]{20,}(?:\.[A-Za-z0-9_+/=-]{8,})*\b"),
    re.compile(r"\bsb_(?:publishable|secret)_[A-Za-z0-9_-]{12,}\b"),
)
_ZERO_EFFECTS = {
    "remote_write_executed_count": 0,
    "shared_write_executed_count": 0,
    "publication_executed_count": 0,
    "review_submission_executed_count": 0,
    "contact_executed_count": 0,
    "payment_executed_count": 0,
    "role_administration_executed_count": 0,
    "external_message_executed_count": 0,
    "unofficial_tiangong_correction_count": 0,
    "unsupported_strong_public_claim_count": 0,
    "secret_persistence_count": 0,
}


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) for key in value
    ):
        raise ValueError(f"{field} must be an object with string keys")
    return value


def _sequence(value: object, field: str) -> Sequence[object]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return " ".join(value.split())


def _sha256(value: object, field: str) -> str:
    normalized = _text(value, field).lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError(f"{field} must be a lowercase SHA-256")
    return normalized


def _uuid(value: object, field: str) -> str:
    normalized = _text(value, field).lower()
    if not _UUID_RE.fullmatch(normalized):
        raise ValueError(f"{field} must be an exact UUID")
    return normalized


def _version(value: object, field: str) -> str:
    normalized = _text(value, field)
    if not _VERSION_RE.fullmatch(normalized):
        raise ValueError(f"{field} must be an exact dotted version")
    return normalized


def _utc(value: object, field: str) -> str:
    normalized = _text(value, field)
    if not _UTC_RE.fullmatch(normalized):
        raise ValueError(f"{field} must be an exact UTC timestamp")
    return normalized


def _positive_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _reject_secrets(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("mapping keys must be strings")
            comparable = re.sub(r"[^a-z0-9]", "", key.casefold())
            if comparable != "secretpersistencecount" and any(
                marker in comparable for marker in _SECRET_KEY_MARKERS
            ):
                raise ValueError("secret-like key is not allowed")
            _reject_secrets(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_secrets(child)
    elif isinstance(value, str) and any(
        pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS
    ):
        raise ValueError("secret-like value is not allowed")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite values are not allowed")


def _official_url(value: object, field: str) -> str:
    normalized = _text(value, field)
    parsed = urlparse(normalized)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "lca.tiangong.earth"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{field} must be a public TianGong HTTPS URL")
    return normalized


def _source_file(value: object, field: str) -> JsonObject:
    source = _mapping(value, field)
    if set(source) != {"url", "sha256", "bytes", "artifact_path"}:
        raise ValueError(f"{field} has an invalid source-file shape")
    artifact_path = _text(source["artifact_path"], f"{field}.artifact_path")
    parts = artifact_path.split("/")
    if (
        artifact_path.startswith("/")
        or "\\" in artifact_path
        or parts[0] != "outputs"
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError(f"{field}.artifact_path must be a safe outputs-relative path")
    return {
        "url": _official_url(source["url"], f"{field}.url"),
        "sha256": _sha256(source["sha256"], f"{field}.sha256"),
        "bytes": _positive_int(source["bytes"], f"{field}.bytes"),
        "artifact_path": artifact_path,
    }


def _validate_qualification(value: object) -> Tuple[JsonObject, List[JsonObject]]:
    qualification = dict(_mapping(value, "qualification"))
    if qualification.get("contract") != _QUALIFICATION_CONTRACT:
        raise ValueError("qualification contract is not the accepted v2 contract")
    digest = _sha256(
        qualification.get("qualification_content_sha256"),
        "qualification.qualification_content_sha256",
    )
    body = {
        key: child
        for key, child in qualification.items()
        if key not in {"qualification_content_sha256", "qualification_id"}
    }
    if digest != _canonical_digest(body) or qualification.get(
        "qualification_id"
    ) != digest[:16]:
        raise ValueError("qualification identity does not match its content")
    rows = []
    seen = set()
    for index, item in enumerate(
        _sequence(qualification.get("candidate_ledger"), "qualification.candidate_ledger")
    ):
        candidate = _mapping(item, f"qualification.candidate_ledger[{index}]")
        row = {
            "cluster_id": _text(
                candidate.get("cluster_id"),
                f"qualification.candidate_ledger[{index}].cluster_id",
            ),
            "process_id": _uuid(
                candidate.get("process_id"),
                f"qualification.candidate_ledger[{index}].process_id",
            ),
            "process_version": _version(
                candidate.get("process_version"),
                f"qualification.candidate_ledger[{index}].process_version",
            ),
        }
        identity = (row["process_id"], row["process_version"])
        if identity in seen:
            raise ValueError("qualification contains a duplicate candidate identity")
        seen.add(identity)
        rows.append(row)
    if not rows:
        raise ValueError("qualification candidate ledger must not be empty")
    return qualification, rows


def _frontend_module_source(text: str, module_id: str) -> str:
    marker = f"{module_id}:function"
    start = text.find(marker)
    if start < 0:
        raise ValueError("frontend reference-lookup implementation is missing")
    remainder = text[start + len(marker) :]
    next_module = re.search(r"\},\d+:function", remainder)
    end = len(text) if next_module is None else start + len(marker) + next_module.start()
    return text[start:end]


def _validate_frontend_semantics(source_bytes: Mapping[str, object]) -> None:
    try:
        main_text = source_bytes["main_bundle"].decode("utf-8")
        chunk_text = source_bytes["lifecyclemodel_chunk"].decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("frontend source files must be UTF-8 JavaScript") from error

    shared_module = _frontend_module_source(main_text, "57171")
    api_module = _frontend_module_source(main_text, "83736")
    page_module = _frontend_module_source(chunk_text, "74626")
    required_markers = (
        (shared_module, _RPC_NAME),
        (shared_module, "p_data_source:Q"),
        (shared_module, "p_limit:ae"),
        (shared_module, "p_source_entity_kinds:X"),
        (shared_module, "p_state_code_filter:S(te)"),
        (shared_module, "p_team_id_filter:W"),
        (shared_module, "p_this_user_id:"),
        (shared_module, "p_uuid:D"),
        (shared_module, "i=50"),
        (api_module, "g=t(57171)"),
        (api_module, "(0,g.xS)("),
        (api_module, 'sourceEntityKinds:["lifecyclemodel"]'),
        (page_module, "D=a(83736)"),
        (page_module, "(0,D.eh)("),
        (page_module, "pages.search.referenceLookup"),
    )
    if any(marker not in module for module, marker in required_markers):
        raise ValueError("frontend reference-lookup implementation is missing")


def _validate_frontend(
    value: object, source_bytes: Mapping[str, object]
) -> JsonObject:
    frontend = _mapping(value, "frontend_manifest")
    if set(frontend) != {
        "contract",
        "retrieved_at_utc",
        "ui_root_url",
        "main_bundle",
        "lifecyclemodel_chunk",
        "implementation",
    }:
        raise ValueError("frontend manifest has an invalid shape")
    if frontend.get("contract") != _FRONTEND_CONTRACT:
        raise ValueError("frontend manifest contract is invalid")
    main_bundle = _source_file(frontend["main_bundle"], "frontend.main_bundle")
    chunk = _source_file(
        frontend["lifecyclemodel_chunk"], "frontend.lifecyclemodel_chunk"
    )
    if set(source_bytes) != {"main_bundle", "lifecyclemodel_chunk"}:
        raise ValueError("frontend source bytes have an invalid shape")
    for key, source in (
        ("main_bundle", main_bundle),
        ("lifecyclemodel_chunk", chunk),
    ):
        raw = source_bytes[key]
        if not isinstance(raw, bytes):
            raise ValueError(f"frontend source {key} must be bytes")
        if len(raw) != source["bytes"] or hashlib.sha256(raw).hexdigest() != source["sha256"]:
            raise ValueError(f"frontend source {key} does not match its manifest")
    _validate_frontend_semantics(source_bytes)
    if not urlparse(main_bundle["url"]).path.startswith("/umi."):
        raise ValueError("frontend main bundle is not the deployed Umi bundle")
    if not urlparse(chunk["url"]).path.startswith(
        "/p__LifeCycleModels__index."
    ):
        raise ValueError("frontend chunk is not the deployed lifecycle-model page")
    implementation = _mapping(frontend["implementation"], "frontend.implementation")
    expected_implementation = {
        "page_module_id": "74626",
        "api_module_id": "83736",
        "shared_module_id": "57171",
        "rpc_name": _RPC_NAME,
        "data_source": _DATA_SOURCE,
        "source_entity_kinds": _ENTITY_KINDS,
        "maximum_results": _MAXIMUM_RESULTS,
    }
    if dict(implementation) != expected_implementation:
        raise ValueError("frontend reference-lookup implementation contract drifted")
    return {
        "contract": _FRONTEND_CONTRACT,
        "retrieved_at_utc": _utc(
            frontend["retrieved_at_utc"], "frontend.retrieved_at_utc"
        ),
        "ui_root_url": _official_url(frontend["ui_root_url"], "frontend.ui_root_url"),
        "main_bundle": main_bundle,
        "lifecyclemodel_chunk": chunk,
        "implementation": expected_implementation,
    }


def _normalize_mention(value: object, field: str) -> JsonObject:
    mention = _mapping(value, field)
    expected = {
        "source_id",
        "source_version",
        "source_entity_kind",
        "source_state_code",
        "source_modified_at",
    }
    if set(mention) != expected:
        raise ValueError(f"{field} has an invalid mention shape")
    if mention.get("source_entity_kind") != "lifecyclemodel":
        raise ValueError(f"{field} is not a lifecyclemodel reference")
    state_code = mention.get("source_state_code")
    if state_code is not None and (
        not isinstance(state_code, int) or isinstance(state_code, bool)
    ):
        raise ValueError(f"{field}.source_state_code must be an integer or null")
    modified_at = mention.get("source_modified_at")
    if modified_at is not None:
        modified_at = _utc(modified_at, f"{field}.source_modified_at")
    return {
        "source_id": _uuid(mention.get("source_id"), f"{field}.source_id"),
        "source_version": _version(
            mention.get("source_version"), f"{field}.source_version"
        ),
        "source_entity_kind": "lifecyclemodel",
        "source_state_code": state_code,
        "source_modified_at": modified_at,
    }


def _validate_capture(
    value: object,
    qualification: Mapping[str, object],
    candidates: Sequence[Mapping[str, object]],
    frontend: Mapping[str, object],
) -> Tuple[JsonObject, List[JsonObject]]:
    capture = _mapping(value, "capture")
    expected_keys = {
        "contract",
        "observed_at_utc",
        "qualification_id",
        "qualification_content_sha256",
        "frontend_main_bundle_sha256",
        "frontend_lifecyclemodel_chunk_sha256",
        "runtime",
        "request",
        "candidate_results",
        "effects",
    }
    if set(capture) != expected_keys or capture.get("contract") != _CAPTURE_CONTRACT:
        raise ValueError("capture contract or shape is invalid")
    if (
        capture.get("qualification_id") != qualification["qualification_id"]
        or capture.get("qualification_content_sha256")
        != qualification["qualification_content_sha256"]
    ):
        raise ValueError("capture is not bound to the accepted qualification")
    if (
        capture.get("frontend_main_bundle_sha256")
        != frontend["main_bundle"]["sha256"]
        or capture.get("frontend_lifecyclemodel_chunk_sha256")
        != frontend["lifecyclemodel_chunk"]["sha256"]
    ):
        raise ValueError("capture is not bound to the deployed frontend files")
    runtime = _mapping(capture["runtime"], "capture.runtime")
    expected_runtime = {
        "cli_package": _CLI_PACKAGE,
        "cli_version": _CLI_VERSION,
        "adoption_id": _ADOPTION_ID,
        "adoption_content_sha256": _ADOPTION_CONTENT_SHA256,
        "npm_integrity": _CLI_NPM_INTEGRITY,
        "package_lock_sha256": _CLI_PACKAGE_LOCK_SHA256,
        "node_modules_tree_sha256": _CLI_NODE_MODULES_TREE_SHA256,
        "capture_tool_sha256": _CAPTURE_TOOL_SHA256,
        "session_cache_disabled": True,
        "authenticated_runtime_present": True,
        "authenticated_actor_present": True,
        "public_runtime_present": True,
    }
    if dict(runtime) != expected_runtime:
        raise ValueError("capture runtime is not the pinned authenticated read runtime")
    request = _mapping(capture["request"], "capture.request")
    expected_request = {
        "rpc_name": _RPC_NAME,
        "data_source": _DATA_SOURCE,
        "limit": _MAXIMUM_RESULTS,
        "source_entity_kinds": _ENTITY_KINDS,
        "state_code_filter": None,
        "team_id_filter": None,
        "authenticated_actor_binding": "session_subject",
    }
    if dict(request) != expected_request:
        raise ValueError("capture request differs from the official UI contract")
    effects = _mapping(capture["effects"], "capture.effects")
    if dict(effects) != _ZERO_EFFECTS:
        raise ValueError("provider-closure audit must have zero prohibited effects")

    expected_candidates = {
        (str(row["process_id"]), str(row["process_version"])): row
        for row in candidates
    }
    results = []
    seen = set()
    for index, item in enumerate(
        _sequence(capture["candidate_results"], "capture.candidate_results")
    ):
        field = f"capture.candidate_results[{index}]"
        row = _mapping(item, field)
        if set(row) != {
            "cluster_id",
            "process_id",
            "process_version",
            "http_status",
            "success",
            "error_code",
            "error_message",
            "mentions",
        }:
            raise ValueError(f"{field} has an invalid result shape")
        identity = (
            _uuid(row.get("process_id"), f"{field}.process_id"),
            _version(row.get("process_version"), f"{field}.process_version"),
        )
        if identity in seen or identity not in expected_candidates:
            raise ValueError("capture candidate coverage is duplicated or out of scope")
        seen.add(identity)
        expected = expected_candidates[identity]
        cluster_id = _text(row.get("cluster_id"), f"{field}.cluster_id")
        if cluster_id != expected["cluster_id"]:
            raise ValueError("capture candidate cluster differs from qualification")
        status = row.get("http_status")
        if not isinstance(status, int) or isinstance(status, bool) or not 100 <= status <= 599:
            raise ValueError(f"{field}.http_status is invalid")
        success = row.get("success")
        if not isinstance(success, bool):
            raise ValueError(f"{field}.success must be boolean")
        error_code = row.get("error_code")
        error_message = row.get("error_message")
        if success:
            if not 200 <= status < 300 or error_code is not None or error_message is not None:
                raise ValueError("successful capture rows cannot carry an error")
        else:
            error_code = _text(error_code, f"{field}.error_code")
            error_message = _text(error_message, f"{field}.error_message")
        mentions = [
            _normalize_mention(mention, f"{field}.mentions[{mention_index}]")
            for mention_index, mention in enumerate(
                _sequence(row["mentions"], f"{field}.mentions")
            )
        ]
        if len(mentions) > _MAXIMUM_RESULTS:
            raise ValueError("capture exceeds the official reference-lookup cap")
        mentions.sort(
            key=lambda mention: (
                str(mention["source_id"]),
                str(mention["source_version"]),
            )
        )
        if len(mentions) != len(
            {
                (mention["source_id"], mention["source_version"])
                for mention in mentions
            }
        ):
            raise ValueError("capture contains duplicate lifecyclemodel mentions")
        results.append(
            {
                "cluster_id": cluster_id,
                "process_id": identity[0],
                "process_version": identity[1],
                "http_status": status,
                "success": success,
                "error_code": error_code,
                "error_message": error_message,
                "mentions": mentions,
            }
        )
    if seen != set(expected_candidates):
        raise ValueError("capture does not cover every accepted qualification candidate")
    results.sort(
        key=lambda row: (
            str(row["cluster_id"]),
            str(row["process_id"]),
            str(row["process_version"]),
        )
    )
    normalized_capture = {
        "contract": _CAPTURE_CONTRACT,
        "observed_at_utc": _utc(capture["observed_at_utc"], "capture.observed_at_utc"),
        "qualification_id": qualification["qualification_id"],
        "qualification_content_sha256": qualification[
            "qualification_content_sha256"
        ],
        "frontend_main_bundle_sha256": frontend["main_bundle"]["sha256"],
        "frontend_lifecyclemodel_chunk_sha256": frontend[
            "lifecyclemodel_chunk"
        ]["sha256"],
        "runtime": expected_runtime,
        "request": expected_request,
        "candidate_results": results,
        "effects": dict(_ZERO_EFFECTS),
    }
    return normalized_capture, results


def compile_provider_closure_reference_audit(
    *,
    qualification: object,
    capture: object,
    frontend_manifest: object,
    frontend_source_bytes: Mapping[str, object],
    capture_tool_bytes: bytes,
) -> JsonObject:
    """Compile a bounded, content-addressed provider-closure audit."""

    _reject_secrets(qualification)
    _reject_secrets(capture)
    _reject_secrets(frontend_manifest)
    if not isinstance(capture_tool_bytes, bytes):
        raise ValueError("capture tool source must be bytes")
    if hashlib.sha256(capture_tool_bytes).hexdigest() != _CAPTURE_TOOL_SHA256:
        raise ValueError("capture tool source is not the pinned audited implementation")
    normalized_qualification, candidates = _validate_qualification(qualification)
    normalized_frontend = _validate_frontend(
        frontend_manifest, _mapping(frontend_source_bytes, "frontend_source_bytes")
    )
    normalized_capture, results = _validate_capture(
        capture,
        normalized_qualification,
        candidates,
        normalized_frontend,
    )

    failed = [row for row in results if not row["success"]]
    hits = [row for row in results if row["mentions"]]
    mention_count = sum(len(row["mentions"]) for row in hits)
    clusters = sorted({str(row["cluster_id"]) for row in results})
    by_cluster = []
    for cluster_id in clusters:
        members = [row for row in results if row["cluster_id"] == cluster_id]
        by_cluster.append(
            {
                "cluster_id": cluster_id,
                "candidate_count": len(members),
                "successful_candidate_count": sum(
                    1 for row in members if row["success"]
                ),
                "failed_candidate_count": sum(
                    1 for row in members if not row["success"]
                ),
                "mention_candidate_count": sum(
                    1 for row in members if row["mentions"]
                ),
                "mention_row_count": sum(
                    len(row["mentions"]) for row in members
                ),
            }
        )

    if failed:
        closure_status = "reference_lookup_incomplete"
        disposition = "remains_unfrozen"
        next_route = "retry-reference-lookup-failures"
    elif hits:
        closure_status = "published_lifecyclemodel_reference_found"
        disposition = "requires_requalification"
        next_route = "requalify-reference-linked-candidates"
    else:
        closure_status = "published_lifecyclemodel_reference_absent"
        disposition = "remains_unfrozen"
        next_route = "await-tiangong-provider-closure-publication"

    body: JsonObject = {
        "contract": _AUDIT_CONTRACT,
        "qualification_id": normalized_qualification["qualification_id"],
        "qualification_content_sha256": normalized_qualification[
            "qualification_content_sha256"
        ],
        "frontend_manifest": normalized_frontend,
        "capture": normalized_capture,
        "candidate_count": len(results),
        "successful_candidate_count": len(results) - len(failed),
        "failed_candidate_count": len(failed),
        "mention_candidate_count": len(hits),
        "mention_row_count": mention_count,
        "cluster_summary": by_cluster,
        "closure_status": closure_status,
        "qualification_disposition": disposition,
        "claim_boundary": (
            "This audit covers the authenticated TianGong Open Data reference-lookup "
            "RPC for published lifecyclemodel JSON containing the exact candidate Process "
            "UUIDs at the captured time. It does not claim that no future model, private "
            "model, non-lifecyclemodel record, or unpublished Provider relation can exist."
        ),
        "zero_prohibited_side_effects": True,
        "effects": dict(_ZERO_EFFECTS),
        "next_route": next_route,
    }
    digest = _canonical_digest(body)
    return {
        **body,
        "audit_content_sha256": digest,
        "audit_id": digest[:16],
    }


__all__ = ["compile_provider_closure_reference_audit"]


def _read_json_file(path: Path, field: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{field} must be a readable UTF-8 JSON file") from error


def _read_bytes_file(path: Path, field: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise ValueError(f"{field} must be a readable binary file") from error


def _write_json_file(path: Path, value: object) -> None:
    resolved = path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    if resolved.exists():
        if resolved.read_bytes() != payload:
            raise ValueError("output already contains different immutable evidence")
        return
    temporary = resolved.with_name(
        f"{resolved.name}.{os.getpid()}.tmp"
    )
    temporary.write_bytes(payload)
    temporary.chmod(0o600)
    try:
        os.link(temporary, resolved)
    except FileExistsError:
        if resolved.read_bytes() != payload:
            raise ValueError("output already contains different immutable evidence")
    finally:
        temporary.unlink(missing_ok=True)


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compile a content-addressed TianGong Provider closure audit."
    )
    parser.add_argument("--qualification", required=True, type=Path)
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--frontend-manifest", type=Path)
    parser.add_argument("--frontend-main-file", type=Path)
    parser.add_argument("--frontend-chunk-file", type=Path)
    parser.add_argument("--capture-tool-file", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate-qualification-only", action="store_true")
    args = parser.parse_args(argv)
    qualification = _read_json_file(args.qualification, "qualification")
    if args.validate_qualification_only:
        _reject_secrets(qualification)
        normalized, candidates = _validate_qualification(qualification)
        print(
            json.dumps(
                {
                    "candidate_count": len(candidates),
                    "qualification_content_sha256": normalized[
                        "qualification_content_sha256"
                    ],
                    "qualification_id": normalized["qualification_id"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    if (
        args.capture is None
        or args.frontend_manifest is None
        or args.frontend_main_file is None
        or args.frontend_chunk_file is None
        or args.capture_tool_file is None
        or args.output is None
    ):
        parser.error(
            "--capture, --frontend-manifest, --frontend-main-file, "
            "--frontend-chunk-file, --capture-tool-file, and --output are required "
            "for audit compilation"
        )
    result = compile_provider_closure_reference_audit(
        qualification=qualification,
        capture=_read_json_file(args.capture, "capture"),
        frontend_manifest=_read_json_file(
            args.frontend_manifest, "frontend manifest"
        ),
        frontend_source_bytes={
            "main_bundle": _read_bytes_file(
                args.frontend_main_file, "frontend main file"
            ),
            "lifecyclemodel_chunk": _read_bytes_file(
                args.frontend_chunk_file, "frontend chunk file"
            ),
        },
        capture_tool_bytes=_read_bytes_file(
            args.capture_tool_file, "capture tool file"
        ),
    )
    _write_json_file(args.output, result)
    print(
        json.dumps(
            {
                "audit_id": result["audit_id"],
                "candidate_count": result["candidate_count"],
                "failed_candidate_count": result["failed_candidate_count"],
                "mention_candidate_count": result["mention_candidate_count"],
                "next_route": result["next_route"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
