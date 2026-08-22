"""Compile exact, inspectable identity inputs for screening inventory entities."""

from __future__ import annotations

import copy
import re
import uuid
from typing import Dict, List
from urllib.parse import urlsplit, urlunsplit


JsonObject = Dict[str, object]

IDENTITY_PREFLIGHT_CONTRACT = (
    "lca_research_kernel.inventory_identity_preflight/v1"
)
PROVISIONAL_IDENTITY_STATUS = (
    "provisional_local_uuidv5_pending_native_identity_preflight"
)

_FLOW_PROPERTIES = {
    "kg": "Mass",
    "MJ": "Energy",
}
_FLOW_KEY_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SOURCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9]+(?:[A-Za-z0-9._:-]*[A-Za-z0-9])?$")
_SOURCE_CAPABILITY_STATUS = (
    "native_source_identity_preflight_unavailable_in_cli_0_0_25"
)


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _uuid_text(value: object, label: str) -> str:
    text = _required_text(value, label)
    try:
        parsed = uuid.UUID(text)
    except ValueError as error:
        raise ValueError(f"{label} must be a UUID") from error
    if str(parsed) != text.lower():
        raise ValueError(f"{label} must use canonical lowercase UUID form")
    return text.lower()


def _canonical_source_identifier(url: str) -> tuple[str, str]:
    parsed = urlsplit(url.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("source url must be an absolute HTTP(S) URL")
    host = parsed.netloc.lower()
    path = re.sub(r"/{2,}", "/", parsed.path).rstrip("/")
    if host in {"doi.org", "dx.doi.org"}:
        doi = path.lstrip("/").lower()
        if not doi.startswith("10.") or "/" not in doi:
            raise ValueError("DOI source url does not contain a canonical DOI")
        return "doi", f"doi:{doi}"
    normalized = urlunsplit(
        (parsed.scheme.lower(), host, path or "/", parsed.query, "")
    ).rstrip("/")
    return "url", f"url:{normalized}"


def _compile_flow_request(
    flow: JsonObject,
    *,
    remote_candidates: bool,
) -> JsonObject:
    flow_key = _required_text(flow.get("flow_key"), "flow.flow_key")
    if not _FLOW_KEY_PATTERN.fullmatch(flow_key):
        raise ValueError(f"{flow_key}.flow_key must be a safe lowercase slug")
    flow_id = _uuid_text(flow.get("flow_id"), f"{flow_key}.flow_id")
    name = _required_text(flow.get("name"), f"{flow_key}.name")
    role = _required_text(flow.get("role"), f"{flow_key}.role")
    unit = _required_text(flow.get("unit"), f"{flow_key}.unit")
    if flow.get("identity_status") != PROVISIONAL_IDENTITY_STATUS:
        raise ValueError(f"{flow_key}.identity_status is not provisional")
    if unit not in _FLOW_PROPERTIES:
        raise ValueError(f"{flow_key} has unsupported flow unit '{unit}'")
    source_ids = flow.get("source_ids")
    if not isinstance(source_ids, list) or not source_ids or not all(
        isinstance(source_id, str) and source_id for source_id in source_ids
    ):
        raise ValueError(f"{flow_key}.source_ids must be non-empty strings")
    if len(set(source_ids)) != len(source_ids):
        raise ValueError(f"{flow_key}.source_ids must be unique")
    request = {
        "schema_version": 1,
        "flow_key": flow_key,
        "target": {
            "id": flow_id,
            "name": name,
            "type_of_dataset": "Product flow",
            "flow_property": _FLOW_PROPERTIES[unit],
            "reference_unit": unit,
        },
        "candidates": [],
        "remote_candidate_search": {
            "enabled": remote_candidates,
            "data_source": "tg" if remote_candidates else None,
            "limit": 20 if remote_candidates else None,
        },
        "candidate_coverage": {
            "native_remote_search": (
                "requested_tiangong_public"
                if remote_candidates
                else "not_requested_credentials_unavailable"
            ),
            "local_candidate_sources": [],
        },
    }
    return {
        "flow_key": flow_key,
        "flow_id": flow_id,
        "name": name,
        "role": role,
        "unit": unit,
        "source_ids": sorted(set(source_ids)),
        "request": request,
    }


def _compile_source_assessment(source: JsonObject) -> JsonObject:
    source_id = _required_text(source.get("source_id"), "source.source_id")
    if not _SOURCE_ID_PATTERN.fullmatch(source_id):
        raise ValueError(f"{source_id}.source_id must be a safe identifier token")
    tidas_source_id = _uuid_text(
        source.get("tidas_source_id"), f"{source_id}.tidas_source_id"
    )
    title = _required_text(source.get("title"), f"{source_id}.title")
    url = _required_text(source.get("url"), f"{source_id}.url")
    if source.get("identity_status") != PROVISIONAL_IDENTITY_STATUS:
        raise ValueError(f"{source_id}.identity_status is not provisional")
    identifier_kind, canonical_identifier = _canonical_source_identifier(url)
    return {
        "source_id": source_id,
        "tidas_source_id": tidas_source_id,
        "title": title,
        "url": url,
        "identifier_kind": identifier_kind,
        "canonical_identifier": canonical_identifier,
        "identity_status": (
            "exact_identifier_bound_pending_native_source_identity_capability"
        ),
    }


def compile_inventory_identity_preflight(
    flow_registry: JsonObject,
    source_registry: JsonObject,
    *,
    remote_candidates: bool,
) -> JsonObject:
    """Compile native flow requests and an honest source capability assessment."""

    if not isinstance(remote_candidates, bool):
        raise ValueError("remote_candidates must be a boolean")

    flows = flow_registry.get("flows")
    sources = source_registry.get("sources")
    if not isinstance(flows, list) or not flows or not all(
        isinstance(flow, dict) for flow in flows
    ):
        raise ValueError("flow registry must contain non-empty flow objects")
    if not isinstance(sources, list) or not sources or not all(
        isinstance(source, dict) for source in sources
    ):
        raise ValueError("source registry must contain non-empty source objects")

    flow_requests = sorted(
        (
            _compile_flow_request(
                copy.deepcopy(flow), remote_candidates=remote_candidates
            )
            for flow in flows
        ),
        key=lambda row: str(row["flow_key"]),
    )
    flow_ids = [row["flow_id"] for row in flow_requests]
    flow_keys = [row["flow_key"] for row in flow_requests]
    if len(set(flow_ids)) != len(flow_ids) or len(set(flow_keys)) != len(
        flow_keys
    ):
        raise ValueError("flow registry contains duplicate identity keys")

    source_assessments = sorted(
        (_compile_source_assessment(copy.deepcopy(source)) for source in sources),
        key=lambda row: str(row["source_id"]),
    )
    source_ids = [row["tidas_source_id"] for row in source_assessments]
    source_keys = [row["source_id"] for row in source_assessments]
    identifiers = [row["canonical_identifier"] for row in source_assessments]
    if len(set(source_keys)) != len(source_keys):
        raise ValueError("source registry contains duplicate source IDs")
    if len(set(source_ids)) != len(source_ids):
        raise ValueError("source registry contains duplicate TIDAS source UUIDs")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("canonical source identifier collision")
    registered_source_ids = set(source_keys)
    missing_source_ids = sorted(
        {
            source_id
            for flow in flow_requests
            for source_id in flow["source_ids"]
            if source_id not in registered_source_ids
        }
    )
    if missing_source_ids:
        raise ValueError(
            "flow registry references unknown source IDs: "
            + ", ".join(missing_source_ids)
        )

    return {
        "schema_version": 1,
        "contract": IDENTITY_PREFLIGHT_CONTRACT,
        "flow_count": len(flow_requests),
        "source_count": len(source_assessments),
        "remote_candidates_requested": remote_candidates,
        "flow_requests": flow_requests,
        "source_assessments": source_assessments,
        "source_identity_capability": {
            "status": _SOURCE_CAPABILITY_STATUS,
            "native_command": None,
            "available_native_neighbors": [
                "dataset contract get --type source",
                "dataset validate --type source",
                "dataset curation-queue --type support",
                "dataset save-draft --type source",
            ],
            "fallback": (
                "retain deterministic TIDAS UUID plus collision-checked DOI/URL key; "
                "do not claim native deduplication"
            ),
        },
        "engineering_basis": {
            "native": "TianGong flow identity-preflight",
            "external_migration": (
                "identifier-first blocking before probabilistic or fuzzy entity resolution"
            ),
            "reference_projects": [
                "dedupeio/dedupe",
                "moj-analytical-services/splink",
            ],
        },
    }
