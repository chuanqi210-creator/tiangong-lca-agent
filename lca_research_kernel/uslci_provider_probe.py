"""Probe current public USLCI electricity Provider candidates without mutation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, MutableMapping, Sequence


JsonObject = Dict[str, object]

USLCI_PROVIDER_PROBE_CONTRACT = (
    "lca_research_kernel.uslci_electricity_provider_probe/v1"
)
USLCI_PROVIDER_PROBE_LOGIC_VERSION = "2026-07-17.1"
PROVIDER_EVIDENCE_CONTRACT = (
    "lca_research_kernel.provider_process_evidence_requirements/v1"
)
SELECTED_SLICE_CONTRACT = "lca_research_kernel.selected_provider_acquisition_slice/v1"
EXPECTED_ROUTE = "probe-uslci-electricity-provider-candidates"
EXPECTED_LOCAL_FLOW_NAME = "Electricity, process"
FLCAC_REPOSITORY_ID = "Federal_LCA_Commons/US_electricity_baseline"
FLCAC_GROUP = "Federal_LCA_Commons"
FLCAC_REPOSITORY = "US_electricity_baseline"
US_LOCATION = "United States of America (the)"
SELECTED_CANDIDATE_NAME = "Electricity; at user; consumption mix - US - US"
EXPECTED_RAW_ROUTES = (
    "github_release",
    "repository_descriptor",
    "candidate_search",
    "candidate_process",
    "dependency_process",
)
USER_AGENT = (
    "LCAResearchKernel/1.0 uslci-provider-probe "
    "(https://github.com/tiangong-lca)"
)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _jsonl_bytes(rows: Sequence[Mapping[str, object]]) -> bytes:
    return b"".join(_canonical_bytes(row) for row in rows)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional text must be a string or null")
    text = value.strip()
    return text or None


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return dict(value)


def _array(value: object, label: str) -> List[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return list(value)


def _load_json(content: bytes, label: str) -> JsonObject:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error
    return _object(value, label)


def _load_jsonl(content: bytes, label: str) -> List[JsonObject]:
    rows = []
    try:
        text = content.decode("utf-8")
    except UnicodeError as error:
        raise ValueError(f"{label} is not UTF-8") from error
    for index, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(_object(json.loads(line), f"{label} row {index}"))
        except json.JSONDecodeError as error:
            raise ValueError(f"{label} row {index} is not valid JSON") from error
    if not rows:
        raise ValueError(f"{label} must contain rows")
    return rows


def _verify_binding(binding: object, content: bytes, label: str) -> JsonObject:
    row = _object(binding, f"{label} binding")
    path = _text(row.get("path"), f"{label} binding path")
    sha256 = _text(row.get("sha256"), f"{label} binding sha256")
    if sha256 != _sha256(content):
        raise ValueError(f"{label} binding hash mismatch")
    byte_count = row.get("bytes")
    if byte_count is not None and byte_count != len(content):
        raise ValueError(f"{label} binding byte count mismatch")
    return {"path": path, "sha256": sha256, "bytes": len(content)}


def _reference_text(value: object, key: str, label: str) -> str:
    return _text(_object(value, label).get(key), f"{label}.{key}")


def build_initial_requests(selected_slice: Mapping[str, object]) -> List[JsonObject]:
    """Build the three fixed credential-free requests for the selected slice."""

    row = _object(selected_slice, "selected Provider acquisition slice")
    if row.get("contract") != SELECTED_SLICE_CONTRACT:
        raise ValueError("selected Provider acquisition slice contract drifted")
    if row.get("slice_id") != EXPECTED_ROUTE:
        raise ValueError("selected Provider acquisition slice route drifted")
    requirement_id = _text(
        row.get("requirement_id"), "selected Provider requirement identity"
    )
    if not requirement_id.startswith("provider-requirement:solid-state:"):
        raise ValueError("selected Provider requirement is not the solid-state route")
    _text(row.get("flow_id"), "selected Provider target Flow identity")
    if row.get("flow_name") != EXPECTED_LOCAL_FLOW_NAME:
        raise ValueError("selected Provider target Flow name drifted")
    if row.get("credential_required") is not False:
        raise ValueError("selected Provider route must remain credential-free")
    if row.get("external_write") is not False:
        raise ValueError("selected Provider route must remain read-only")

    search_query = urllib.parse.urlencode(
        {
            "query": "electricity",
            "repositoryId": FLCAC_REPOSITORY_ID,
            "type": "PROCESS",
            "location": US_LOCATION,
            "page": "1",
            "pageSize": "100",
        }
    )
    return [
        {
            "route_id": "github_release",
            "method": "GET",
            "request_url": (
                "https://api.github.com/repos/FLCAC-admin/uslci-content/releases/latest"
            ),
            "accept": "application/vnd.github+json",
        },
        {
            "route_id": "repository_descriptor",
            "method": "GET",
            "request_url": (
                "https://lcacommons.gov/lca-collaboration/ws/public/repository/"
                f"{FLCAC_GROUP}/{FLCAC_REPOSITORY}"
            ),
            "accept": "application/json",
        },
        {
            "route_id": "candidate_search",
            "method": "GET",
            "request_url": (
                "https://lcacommons.gov/lca-collaboration/ws/public/search?"
                + search_query
            ),
            "accept": "application/json",
        },
    ]


def _canonical_search_name(name: str, location: str) -> str:
    suffix = f" - {location}"
    if name.endswith(suffix):
        return name[: -len(suffix)]
    return name


def _search_candidates(payload: object) -> List[JsonObject]:
    result = _object(payload, "candidate search payload")
    info = _object(result.get("resultInfo"), "candidate search resultInfo")
    data = _array(result.get("data"), "candidate search data")
    if info.get("indexing") is not False:
        raise ValueError("candidate search index is not stable")
    if info.get("count") != 4 or info.get("totalCount") != 4:
        raise ValueError(
            "candidate search is ambiguous: expected exactly four bounded results"
        )
    if info.get("currentPage") != 1 or info.get("pageCount") != 1:
        raise ValueError("candidate search pagination drifted")
    candidates = []
    seen = set()
    for item in data:
        row = _object(item, "candidate search row")
        if row.get("type") != "PROCESS":
            raise ValueError("candidate search returned a non-Process row")
        candidate_id = _text(row.get("refId"), "candidate Process ID")
        if candidate_id in seen:
            raise ValueError("candidate search contains duplicate Process IDs")
        seen.add(candidate_id)
        versions = _array(row.get("versions"), "candidate versions")
        matching = []
        for version in versions:
            candidate = _object(version, "candidate version")
            repos = [
                _object(repo, "candidate repository")
                for repo in _array(candidate.get("repos"), "candidate repositories")
            ]
            if any(repo.get("path") == FLCAC_REPOSITORY_ID for repo in repos):
                matching.append(candidate)
        if len(matching) != 1:
            raise ValueError("candidate must have exactly one current repository version")
        version = matching[0]
        location = _text(version.get("location"), "candidate location")
        if location != US_LOCATION:
            raise ValueError("candidate search geography drifted")
        name = _canonical_search_name(
            _text(version.get("name"), "candidate name"), location
        )
        if not name.startswith("Electricity; at ") or "consumption mix - US - US" not in name:
            raise ValueError("candidate search returned an out-of-slice Process")
        if version.get("processType") != "UNIT_PROCESS":
            raise ValueError("candidate Process type drifted")
        candidates.append(
            {
                "candidate_id": candidate_id,
                "object_id": _text(version.get("objectId"), "candidate object ID"),
                "name": name,
                "location": location,
                "valid_from_year": version.get("validFromYear"),
                "valid_until_year": version.get("validUntilYear"),
                "process_type": version.get("processType"),
                "level": "at_user" if "; at user;" in name else "at_grid",
                "residual_mix": "; residual consumption mix" in name,
                "selected": name == SELECTED_CANDIDATE_NAME,
            }
        )
    selected = [row for row in candidates if row["selected"]]
    if len(selected) != 1:
        raise ValueError("exactly one non-residual at-user candidate is required; selection is ambiguous")
    return sorted(candidates, key=lambda row: (str(row["name"]), str(row["candidate_id"])))


def build_candidate_request(search_payload: object) -> JsonObject:
    candidates = _search_candidates(search_payload)
    selected = next(row for row in candidates if row["selected"])
    candidate_id = selected["candidate_id"]
    return {
        "route_id": "candidate_process",
        "method": "GET",
        "request_url": (
            "https://lcacommons.gov/lca-collaboration/ws/public/browse/"
            f"{FLCAC_GROUP}/{FLCAC_REPOSITORY}/PROCESS/{candidate_id}"
        ),
        "accept": "application/json",
    }


def _ref(value: object, label: str) -> JsonObject:
    row = _object(value, label)
    return {
        "type": row.get("@type"),
        "id": _text(row.get("@id"), f"{label} ID"),
        "name": _text(row.get("name"), f"{label} name"),
    }


def _normalize_exchange(value: object, label: str) -> JsonObject:
    row = _object(value, label)
    if row.get("@type") != "Exchange":
        raise ValueError(f"{label} must be an openLCA Exchange")
    internal_id = row.get("internalId")
    if not isinstance(internal_id, int) or internal_id < 1:
        raise ValueError(f"{label} internalId must be positive")
    amount = row.get("amount")
    if not isinstance(amount, (int, float)) or isinstance(amount, bool):
        raise ValueError(f"{label} amount must be numeric")
    amount = float(amount)
    if not math.isfinite(amount):
        raise ValueError(f"{label} amount must be finite")
    is_input = row.get("isInput")
    if not isinstance(is_input, bool):
        raise ValueError(f"{label} isInput must be explicit")
    flow = _object(row.get("flow"), f"{label} Flow")
    unit = _object(row.get("unit"), f"{label} Unit")
    flow_property = _object(row.get("flowProperty"), f"{label} FlowProperty")
    provider = row.get("defaultProvider")
    normalized = {
        "internal_id": internal_id,
        "direction": "Input" if is_input else "Output",
        "is_quantitative_reference": row.get("isQuantitativeReference") is True,
        "is_avoided_product": row.get("isAvoidedProduct") is True,
        "amount": amount,
        "flow": {
            "flow_id": _text(flow.get("@id"), f"{label} Flow ID"),
            "flow_name": _text(flow.get("name"), f"{label} Flow name"),
            "flow_type": _text(flow.get("flowType"), f"{label} Flow type"),
            "reference_unit": _optional_text(flow.get("refUnit")),
        },
        "unit": {
            "unit_id": _text(unit.get("@id"), f"{label} Unit ID"),
            "unit_name": _text(unit.get("name"), f"{label} Unit name"),
        },
        "flow_property": {
            "flow_property_id": _text(
                flow_property.get("@id"), f"{label} FlowProperty ID"
            ),
            "flow_property_name": _text(
                flow_property.get("name"), f"{label} FlowProperty name"
            ),
            "reference_unit": _optional_text(flow_property.get("refUnit")),
        },
        "default_provider": _ref(provider, f"{label} default Provider")
        if provider is not None
        else None,
        "uncertainty_reported": row.get("uncertainty") is not None,
        "dq_entry": row.get("dqEntry"),
        "description": row.get("description"),
    }
    return normalized


def _normalize_process(
    payload: object,
    label: str,
    *,
    allowed_process_types=("UNIT_PROCESS",),
) -> JsonObject:
    row = _object(payload, label)
    if row.get("@type") != "Process":
        raise ValueError(f"{label} must be an openLCA Process")
    process_id = _text(row.get("@id"), f"{label} ID")
    process_name = _text(row.get("name"), f"{label} name")
    if row.get("processType") not in allowed_process_types:
        allowed = ", ".join(sorted(allowed_process_types))
        raise ValueError(f"{label} process type must be one of: {allowed}")
    location = _object(row.get("location"), f"{label} location")
    location_name = _text(location.get("name"), f"{label} location name")
    exchanges = [
        _normalize_exchange(exchange, f"{label} exchange {index}")
        for index, exchange in enumerate(
            _array(row.get("exchanges"), f"{label} exchanges"), start=1
        )
    ]
    if not exchanges:
        raise ValueError(f"{label} must contain exchanges")
    internal_ids = [exchange["internal_id"] for exchange in exchanges]
    if len(internal_ids) != len(set(internal_ids)):
        raise ValueError(f"{label} exchange internal IDs must be unique")
    references = [exchange for exchange in exchanges if exchange["is_quantitative_reference"]]
    if len(references) != 1:
        raise ValueError(f"{label} must have exactly one reference exchange")
    reference = references[0]
    if reference["direction"] != "Output":
        raise ValueError(f"{label} reference exchange must be an Output")
    if reference["amount"] <= 0:
        raise ValueError(f"{label} reference exchange amount must be positive")
    if reference["flow"]["flow_type"] != "PRODUCT_FLOW":
        raise ValueError(f"{label} reference exchange must output a product Flow")
    documentation = _object(row.get("processDocumentation"), f"{label} documentation")
    valid_from = _text(documentation.get("validFrom"), f"{label} validFrom")
    valid_until = _text(documentation.get("validUntil"), f"{label} validUntil")
    sources = [
        _ref(source, f"{label} source {index}")
        for index, source in enumerate(
            _array(documentation.get("sources"), f"{label} sources"), start=1
        )
    ]
    if not sources:
        raise ValueError(f"{label} must identify at least one source")
    restrictions = _text(
        documentation.get("restrictionsDescription"), f"{label} restrictions"
    )
    copyright_protected = documentation.get("isCopyrightProtected")
    if not isinstance(copyright_protected, bool):
        raise ValueError(f"{label} copyright state must be explicit")
    inputs = [exchange for exchange in exchanges if exchange["direction"] == "Input"]
    outputs = [exchange for exchange in exchanges if exchange["direction"] == "Output"]
    default_providers = [
        exchange["default_provider"]
        for exchange in inputs
        if exchange["default_provider"] is not None
    ]
    return {
        "process_id": process_id,
        "process_name": process_name,
        "process_version": _text(row.get("version"), f"{label} version"),
        "process_type": row.get("processType"),
        "location": {
            "location_id": _text(location.get("@id"), f"{label} location ID"),
            "location_name": location_name,
            "category": location.get("category"),
        },
        "category": row.get("category"),
        "valid_from": valid_from,
        "valid_until": valid_until,
        "time_description": documentation.get("timeDescription"),
        "technology_description": documentation.get("technologyDescription"),
        "inventory_method_description": documentation.get(
            "inventoryMethodDescription"
        ),
        "sampling_description": documentation.get("samplingDescription"),
        "intended_application": documentation.get("intendedApplication"),
        "sources": sources,
        "data_set_owner": _ref(
            documentation.get("dataSetOwner"), f"{label} data set owner"
        ),
        "copyright_protected": copyright_protected,
        "restrictions_description": restrictions,
        "access_state": (
            "source_observed_no_restrictions"
            if not copyright_protected and restrictions.casefold() == "no restrictions"
            else "requires_human_license_review"
        ),
        "exchange_count": len(exchanges),
        "input_exchange_count": len(inputs),
        "output_exchange_count": len(outputs),
        "default_provider_count": len(default_providers),
        "reference_exchange": reference,
        "default_providers": default_providers,
        "exchanges": exchanges,
    }


def build_dependency_request(candidate_payload: object) -> JsonObject:
    process = _normalize_process(candidate_payload, "selected candidate Process")
    providers = process["default_providers"]
    if len(providers) != 1:
        raise ValueError("selected candidate must declare exactly one default Provider dependency")
    provider_id = providers[0]["id"]
    return {
        "route_id": "dependency_process",
        "method": "GET",
        "request_url": (
            "https://lcacommons.gov/lca-collaboration/ws/public/browse/"
            f"{FLCAC_GROUP}/{FLCAC_REPOSITORY}/PROCESS/{provider_id}"
        ),
        "accept": "application/json",
    }


def _valid_envelope(value: object, route_id: str) -> JsonObject:
    envelope = _object(value, f"{route_id} envelope")
    if envelope.get("route_id") != route_id:
        raise ValueError(f"{route_id} envelope route identity drifted")
    if envelope.get("http_status") != 200 or envelope.get("error") is not None:
        raise ValueError(f"{route_id} public read failed")
    response_sha256 = _text(
        envelope.get("response_sha256"), f"{route_id} response SHA-256"
    )
    if len(response_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in response_sha256
    ):
        raise ValueError(f"{route_id} response SHA-256 is malformed")
    response_bytes = envelope.get("response_bytes")
    if not isinstance(response_bytes, int) or response_bytes < 2:
        raise ValueError(f"{route_id} response byte count is malformed")
    _text(envelope.get("request_url"), f"{route_id} request URL")
    _text(envelope.get("fetched_at"), f"{route_id} fetched_at")
    payload = _object(envelope.get("payload"), f"{route_id} payload")
    envelope["payload"] = payload
    return envelope


def _provider_inputs(
    *,
    provider_evidence_report_bytes: bytes,
    provider_evidence_report_binding: object,
    evidence_expectations_bytes: bytes,
    evidence_expectations_binding: object,
    selected_slice_bytes: bytes,
    selected_slice_binding: object,
) -> tuple[JsonObject, JsonObject, JsonObject, List[JsonObject]]:
    report_binding = _verify_binding(
        provider_evidence_report_binding,
        provider_evidence_report_bytes,
        "Provider evidence report",
    )
    expectations_binding = _verify_binding(
        evidence_expectations_binding,
        evidence_expectations_bytes,
        "Provider evidence expectations",
    )
    slice_binding = _verify_binding(
        selected_slice_binding, selected_slice_bytes, "selected Provider slice"
    )
    report = _load_json(provider_evidence_report_bytes, "Provider evidence report")
    if report.get("contract") != PROVIDER_EVIDENCE_CONTRACT:
        raise ValueError("Provider evidence report contract drifted")
    if report.get("status") != "provider_process_evidence_requirements_compiled":
        raise ValueError("Provider evidence report is not accepted")
    selected_requirement_id = _text(
        report.get("selected_requirement_id"),
        "Provider evidence report selected requirement",
    )
    routing = _object(report.get("routing"), "Provider evidence routing")
    if routing.get("next_internal_route") != EXPECTED_ROUTE:
        raise ValueError("Provider evidence next route drifted")
    artifacts = _object(report.get("artifact_bindings"), "Provider evidence artifacts")
    if _object(
        artifacts.get("evidence_requirements"), "expectation artifact"
    ).get("sha256") != expectations_binding["sha256"]:
        raise ValueError("Provider evidence expectation artifact binding drifted")
    if _object(artifacts.get("selected_slice"), "selected slice artifact").get(
        "sha256"
    ) != slice_binding["sha256"]:
        raise ValueError("selected Provider slice artifact binding drifted")
    expectations = _load_jsonl(
        evidence_expectations_bytes, "Provider evidence expectations"
    )
    selected_expectations = [
        row
        for row in expectations
        if row.get("requirement_id") == selected_requirement_id
    ]
    if len(selected_expectations) != 1:
        raise ValueError("selected Provider expectation suite is missing or duplicated")
    suite = selected_expectations[0]
    reference = _object(
        suite.get("required_reference_flow"), "required local reference Flow"
    )
    _text(reference.get("flow_id"), "required local Flow identity")
    if reference.get("flow_name") != EXPECTED_LOCAL_FLOW_NAME:
        raise ValueError("required local Flow name drifted")
    if suite.get("provider_process_build_authorized") is not False:
        raise ValueError("Provider build must remain unauthorized")
    selected_slice = _load_json(selected_slice_bytes, "selected Provider slice")
    build_initial_requests(selected_slice)
    if selected_slice.get("requirement_id") != selected_requirement_id:
        raise ValueError("selected Provider requirement identity drifted")
    if selected_slice.get("flow_id") != reference.get("flow_id"):
        raise ValueError("selected Provider target Flow identity drifted")
    return (
        report,
        selected_slice,
        suite,
        [report_binding, expectations_binding, slice_binding],
    )


def compile_uslci_provider_probe(
    *,
    provider_evidence_report_bytes: bytes,
    provider_evidence_report_binding: object,
    evidence_expectations_bytes: bytes,
    evidence_expectations_binding: object,
    selected_slice_bytes: bytes,
    selected_slice_binding: object,
    raw_envelopes: Mapping[str, object],
) -> JsonObject:
    """Compile strict, current USLCI candidate observations and retained blockers."""

    report, selected_slice, suite, input_bindings = _provider_inputs(
        provider_evidence_report_bytes=provider_evidence_report_bytes,
        provider_evidence_report_binding=provider_evidence_report_binding,
        evidence_expectations_bytes=evidence_expectations_bytes,
        evidence_expectations_binding=evidence_expectations_binding,
        selected_slice_bytes=selected_slice_bytes,
        selected_slice_binding=selected_slice_binding,
    )
    if set(raw_envelopes) != set(EXPECTED_RAW_ROUTES):
        raise ValueError("exactly five expected public response envelopes are required")
    envelopes = {
        route_id: _valid_envelope(raw_envelopes[route_id], route_id)
        for route_id in EXPECTED_RAW_ROUTES
    }

    release = envelopes["github_release"]["payload"]
    tag = _text(release.get("tag_name"), "USLCI release tag")
    if not tag.startswith("v1."):
        raise ValueError("USLCI release tag is unsupported")
    repository = envelopes["repository_descriptor"]["payload"]
    if repository.get("group") != FLCAC_GROUP or repository.get("name") != FLCAC_REPOSITORY:
        raise ValueError("FLCAC repository identity drifted")
    settings = _object(repository.get("settings"), "FLCAC repository settings")
    repository_version = _text(
        settings.get("version"), "FLCAC repository version"
    )
    if tag[1:] != repository_version:
        raise ValueError("USLCI release and FLCAC repository version disagree")
    if "2023" not in _text(settings.get("description"), "repository description"):
        raise ValueError("FLCAC repository time coverage drifted")
    if "ElectricityLCI" not in _text(
        settings.get("sourceInfo"), "repository source information"
    ):
        raise ValueError("FLCAC repository source identity drifted")

    candidates = _search_candidates(envelopes["candidate_search"]["payload"])
    selected_candidate = next(row for row in candidates if row["selected"])
    candidate_process = _normalize_process(
        envelopes["candidate_process"]["payload"], "selected candidate Process"
    )
    if candidate_process["process_id"] != selected_candidate["candidate_id"]:
        raise ValueError("selected candidate Process identity mismatch")
    if candidate_process["process_name"] != selected_candidate["name"]:
        raise ValueError("selected candidate Process name mismatch")
    if candidate_process["location"]["location_name"] != selected_candidate["location"]:
        raise ValueError("selected candidate Process geography mismatch")
    if not candidate_process["valid_from"].startswith(
        str(selected_candidate["valid_from_year"])
    ) or not candidate_process["valid_until"].startswith(
        str(selected_candidate["valid_until_year"])
    ):
        raise ValueError("selected candidate Process time mismatch")
    providers = candidate_process["default_providers"]
    if len(providers) != 1:
        raise ValueError("selected candidate must declare exactly one default Provider dependency")
    dependency_ref = providers[0]
    dependency_process = _normalize_process(
        envelopes["dependency_process"]["payload"], "selected dependency Process"
    )
    if dependency_process["process_id"] != dependency_ref["id"]:
        raise ValueError("selected dependency Process identity mismatch")
    if dependency_process["process_name"] != dependency_ref["name"]:
        raise ValueError("selected dependency Process name mismatch")
    dependency_candidates = [
        row for row in candidates if row["candidate_id"] == dependency_process["process_id"]
    ]
    if len(dependency_candidates) != 1:
        raise ValueError("selected dependency is not in the bounded candidate set")

    local_reference = _object(
        suite.get("required_reference_flow"), "required local reference Flow"
    )
    candidate_reference = candidate_process["reference_exchange"]
    dependency_reference = dependency_process["reference_exchange"]
    exact_match = candidate_reference["flow"]["flow_id"] == local_reference["flow_id"]
    if exact_match:
        raise ValueError(
            "foreign candidate unexpectedly equals the local Flow identity; explicit mapping audit required"
        )
    mapping_gap = {
        "contract": "lca_research_kernel.provider_flow_mapping_gap/v1",
        "requirement_id": selected_slice["requirement_id"],
        "local_flow": dict(local_reference),
        "candidate_output_flow": {
            **candidate_reference["flow"],
            "amount": candidate_reference["amount"],
            "unit": candidate_reference["unit"],
            "flow_property": candidate_reference["flow_property"],
        },
        "dependency_output_flow": {
            **dependency_reference["flow"],
            "amount": dependency_reference["amount"],
            "unit": dependency_reference["unit"],
            "flow_property": dependency_reference["flow_property"],
        },
        "exact_flow_id_match": False,
        "mapping_status": "explicit_mapping_required",
        "identity_rewrite_allowed": False,
        "unit_conversion_applied": False,
        "consumer_amount_reused_as_provider_reference": False,
        "consumer_geography": _object(
            suite.get("consumer_context"), "consumer context"
        ).get("geography"),
        "provider_geography": candidate_process["location"]["location_name"],
        "geography_applicability_blocked": True,
        "time_applicability_blocked": True,
        "technology_applicability_blocked": True,
        "connection_allowed": False,
    }
    normalization_slice = {
        "contract": "lca_research_kernel.selected_provider_normalization_slice/v1",
        "slice_id": "compile-uslci-electricity-provider-normalization-plan",
        "requirement_id": selected_slice["requirement_id"],
        "candidate_process_id": candidate_process["process_id"],
        "dependency_process_id": dependency_process["process_id"],
        "source_repository": FLCAC_REPOSITORY_ID,
        "source_repository_version": repository_version,
        "source_process_version": candidate_process["process_version"],
        "source_access_state": candidate_process["access_state"],
        "candidate_geography": candidate_process["location"]["location_name"],
        "candidate_time": {
            "valid_from": candidate_process["valid_from"],
            "valid_until": candidate_process["valid_until"],
        },
        "normalization_plan_allowed": True,
        "process_creation_allowed": False,
        "flow_identity_rewrite_allowed": False,
        "model_connection_allowed": False,
        "model_mutation_allowed": False,
        "remote_write_allowed": False,
        "comparison_allowed": False,
        "lcia_allowed": False,
        "required_outputs": [
            "reversible_openlca_to_tidas_field_map",
            "explicit_flow_and_unit_mapping_proposal",
            "geography_time_technology_proxy_assessment",
            "native_identity_and_build_plan_requests_without_invocation",
        ],
        "retained_blockers": [
            "US_geography_is_not_CN_applicability",
            "foreign_flow_UUID_requires_explicit_mapping_not_identity_rewrite",
            "source_reference_unit_and_exchange_unit_require_explicit_conversion",
            "at_user_wrapper_depends_on_at_grid_and_further_provider_graph",
            "candidate_discovery_does_not_authorize_connection",
        ],
    }
    process_observations = [
        {"role": "selected_at_user_candidate", **candidate_process},
        {"role": "declared_at_grid_dependency", **dependency_process},
    ]
    response_bindings = [
        {
            "route_id": route_id,
            "request_url": envelopes[route_id]["request_url"],
            "fetched_at": envelopes[route_id]["fetched_at"],
            "sha256": envelopes[route_id]["response_sha256"],
            "bytes": envelopes[route_id]["response_bytes"],
        }
        for route_id in EXPECTED_RAW_ROUTES
    ]
    probe_version_source = {
        "logic_version": USLCI_PROVIDER_PROBE_LOGIC_VERSION,
        "requirements_version": report.get("requirements_version"),
        "input_bindings": input_bindings,
        "response_bindings": response_bindings,
        "release_tag": tag,
        "repository_version": repository_version,
        "selected_candidate_id": candidate_process["process_id"],
        "selected_dependency_id": dependency_process["process_id"],
    }
    probe_version = _sha256(_canonical_bytes(probe_version_source))[:16]
    return {
        "schema_version": 1,
        "contract": USLCI_PROVIDER_PROBE_CONTRACT,
        "logic_version": USLCI_PROVIDER_PROBE_LOGIC_VERSION,
        "status": "uslci_electricity_provider_candidates_probed",
        "scope_id": report.get("scope_id"),
        "revision": report.get("revision"),
        "requirements_version": report.get("requirements_version"),
        "probe_version": probe_version,
        "input_bindings": input_bindings,
        "response_bindings": response_bindings,
        "release_tag": tag,
        "release_published_at": release.get("published_at"),
        "repository_version": repository_version,
        "repository_description": settings.get("description"),
        "repository_source": settings.get("sourceInfo"),
        "repository_citation": settings.get("citation"),
        "candidate_count": len(candidates),
        "selected_candidate_count": 1,
        "selected_candidate_id": candidate_process["process_id"],
        "selected_candidate_name": candidate_process["process_name"],
        "selected_dependency_id": dependency_process["process_id"],
        "selected_dependency_name": dependency_process["process_name"],
        "fetched_process_count": 2,
        "candidate_exchange_count": candidate_process["exchange_count"],
        "candidate_input_exchange_count": candidate_process["input_exchange_count"],
        "candidate_output_exchange_count": candidate_process["output_exchange_count"],
        "dependency_exchange_count": dependency_process["exchange_count"],
        "dependency_input_exchange_count": dependency_process["input_exchange_count"],
        "dependency_output_exchange_count": dependency_process["output_exchange_count"],
        "public_read_count": len(EXPECTED_RAW_ROUTES),
        "successful_public_read_count": len(EXPECTED_RAW_ROUTES),
        "credential_required_read_count": 0,
        "external_write_executed_count": 0,
        "exact_local_flow_identity_match": False,
        "flow_mapping_status": "explicit_mapping_required",
        "provider_build_authorized_count": 0,
        "process_creation_executed_count": 0,
        "model_connection_executed_count": 0,
        "model_mutation_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "candidate_observations": candidates,
        "process_observations": process_observations,
        "flow_mapping_gap": mapping_gap,
        "selected_normalization_slice": normalization_slice,
        "external_patterns": [
            {
                "project": "iterative/dvc",
                "mechanism": "versioned_source_plus_content_hash_and_impacted_rerun",
                "local_landing": "raw_response_bindings_and_probe_version",
                "installed": False,
            },
            {
                "project": "OpenLineage/OpenLineage",
                "mechanism": "job_run_dataset_and_facet_separation",
                "local_landing": "public_read_candidate_dependency_and_mapping_lineage",
                "installed": False,
            },
            {
                "project": "GreenDelta/olca-schema",
                "mechanism": "explicit_jsonld_identity_direction_reference_and_provider_fields",
                "local_landing": "strict_openlca_process_exchange_parser",
                "installed": False,
            },
        ],
        "routing": {
            "next_internal_route": "compile-uslci-electricity-provider-normalization-plan",
            "pause_internal_work": False,
            "reason": (
                "One current official candidate and its declared dependency are source-bound; "
                "foreign Flow, unit, geography, time and technology mappings remain blocked."
            ),
        },
        "next_action": (
            "Compile a reversible openLCA-to-TIDAS normalization plan for the selected "
            "USLCI electricity candidate without creating or connecting a Process."
        ),
    }


def materialize_uslci_provider_probe(
    result: Mapping[str, object], out_dir: Path
) -> Dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    detailed_keys = {
        "candidate_observations",
        "process_observations",
        "flow_mapping_gap",
        "selected_normalization_slice",
    }
    candidates = [
        _object(row, "candidate observation")
        for row in _array(result.get("candidate_observations"), "candidate observations")
    ]
    processes = [
        _object(row, "Process observation")
        for row in _array(result.get("process_observations"), "Process observations")
    ]
    mapping = _object(result.get("flow_mapping_gap"), "Flow mapping gap")
    selected = _object(
        result.get("selected_normalization_slice"), "selected normalization slice"
    )
    payloads = {
        "candidate_observations": _jsonl_bytes(candidates),
        "process_observations": _jsonl_bytes(processes),
        "flow_mapping_gap": _json_bytes(mapping),
        "selected_normalization_slice": _json_bytes(selected),
    }
    filenames = {
        "candidate_observations": "uslci-electricity-candidate-observations.jsonl",
        "process_observations": "uslci-electricity-process-observations.jsonl",
        "flow_mapping_gap": "uslci-electricity-flow-mapping-gap.json",
        "selected_normalization_slice": "selected-uslci-provider-normalization-slice.json",
    }
    paths = {}
    artifact_bindings = {}
    for key, content in payloads.items():
        path = out_dir / filenames[key]
        path.write_bytes(content)
        paths[key] = path
        artifact_bindings[key] = {
            "path": path.name,
            "sha256": _sha256(content),
            "bytes": len(content),
        }
    report = {key: value for key, value in result.items() if key not in detailed_keys}
    report["artifact_bindings"] = artifact_bindings
    report_path = out_dir / "uslci-electricity-provider-probe.json"
    report_path.write_bytes(_json_bytes(report))
    return {"report": report_path, **paths}


def _fetch_one(
    request: Mapping[str, object], *, attempts: int, timeout: int
) -> tuple[JsonObject, bytes | None]:
    payload = None
    body = None
    error_text = None
    http_status = None
    attempt_count = 0
    for attempt in range(1, attempts + 1):
        attempt_count = attempt
        try:
            http_request = urllib.request.Request(
                _text(request.get("request_url"), "request URL"),
                method="GET",
                headers={
                    "Accept": _text(request.get("accept"), "request Accept"),
                    "User-Agent": USER_AGENT,
                },
            )
            with urllib.request.urlopen(http_request, timeout=timeout) as response:
                body = response.read()
                http_status = response.getcode()
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("public response JSON must be an object")
            error_text = None
            break
        except urllib.error.HTTPError as error:
            http_status = error.code
            error_text = f"HTTP {error.code}"
            if error.code not in {408, 429, 500, 502, 503, 504}:
                break
        except (
            urllib.error.URLError,
            TimeoutError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
        ) as error:
            error_text = f"{type(error).__name__}: {error}"
        if attempt < attempts:
            time.sleep(0.5 * attempt)
    envelope = {
        "schema_version": 1,
        "route_id": request.get("route_id"),
        "request_url": request.get("request_url"),
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "http_status": http_status,
        "attempt_count": attempt_count,
        "response_sha256": _sha256(body) if body is not None else None,
        "response_bytes": len(body) if body is not None else 0,
        "error": error_text,
        "payload": payload,
    }
    return envelope, body


def fetch_uslci_provider_probe(
    selected_slice: Mapping[str, object],
    raw_root: Path,
    *,
    attempts: int = 3,
    timeout: int = 30,
) -> Dict[str, JsonObject]:
    """Execute five sequential public reads and persist a raw envelope for each."""

    if attempts < 1 or timeout < 1:
        raise ValueError("attempt and timeout limits must be positive")
    raw_root.mkdir(parents=True, exist_ok=True)
    requests = build_initial_requests(selected_slice)
    raw: MutableMapping[str, JsonObject] = {}

    def execute(request: JsonObject) -> JsonObject:
        envelope, _body = _fetch_one(request, attempts=attempts, timeout=timeout)
        route_id = _text(request.get("route_id"), "route ID")
        raw[route_id] = envelope
        index = EXPECTED_RAW_ROUTES.index(route_id) + 1
        path = raw_root / f"{index:02d}-{route_id}.json"
        path.write_bytes(_json_bytes(envelope))
        return envelope

    for request in requests:
        envelope = execute(request)
        _valid_envelope(envelope, request["route_id"])
    candidate_request = build_candidate_request(raw["candidate_search"]["payload"])
    candidate_envelope = execute(candidate_request)
    _valid_envelope(candidate_envelope, "candidate_process")
    dependency_request = build_dependency_request(candidate_envelope["payload"])
    dependency_envelope = execute(dependency_request)
    _valid_envelope(dependency_envelope, "dependency_process")
    return dict(raw)


def _read_binding(path: Path) -> JsonObject:
    return _load_json(path.read_bytes(), f"binding {path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider-evidence-report", type=Path, required=True)
    parser.add_argument("--provider-evidence-report-binding", type=Path, required=True)
    parser.add_argument("--evidence-expectations", type=Path, required=True)
    parser.add_argument("--evidence-expectations-binding", type=Path, required=True)
    parser.add_argument("--selected-slice", type=Path, required=True)
    parser.add_argument("--selected-slice-binding", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=30)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    selected_slice_bytes = args.selected_slice.read_bytes()
    selected_slice = _load_json(selected_slice_bytes, "selected Provider slice")
    raw = fetch_uslci_provider_probe(
        selected_slice,
        args.out_dir / "raw-public-responses",
        attempts=args.attempts,
        timeout=args.timeout,
    )
    result = compile_uslci_provider_probe(
        provider_evidence_report_bytes=args.provider_evidence_report.read_bytes(),
        provider_evidence_report_binding=_read_binding(
            args.provider_evidence_report_binding
        ),
        evidence_expectations_bytes=args.evidence_expectations.read_bytes(),
        evidence_expectations_binding=_read_binding(
            args.evidence_expectations_binding
        ),
        selected_slice_bytes=selected_slice_bytes,
        selected_slice_binding=_read_binding(args.selected_slice_binding),
        raw_envelopes=raw,
    )
    materialize_uslci_provider_probe(result, args.out_dir / "outputs")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
