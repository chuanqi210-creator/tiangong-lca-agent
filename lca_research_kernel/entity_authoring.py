"""Compile source-bound local TIDAS source rows and native flow build plans."""

from __future__ import annotations

import copy
import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Dict, List

from .identity import IDENTITY_PREFLIGHT_CONTRACT


JsonObject = Dict[str, object]

ENTITY_AUTHORING_CONTRACT = "lca_research_kernel.inventory_entity_authoring/v1"
SOURCE_DATASET_VERSION = "00.00.001"
FLOW_DATASET_VERSION = "00.00.001"

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MASS_FLOW_PROPERTY_ID = "93a60a56-a3c8-11da-a746-0800200b9a66"
_SUPPORT_NAMESPACE = uuid.UUID("857d684f-62e5-52e5-a54f-f6d8844093df")
_FORMAT_SOURCE_ID = str(
    uuid.uuid5(_SUPPORT_NAMESPACE, "tidas-ilcd-format-source")
)
_OWNER_CONTACT_ID = str(
    uuid.uuid5(_SUPPORT_NAMESPACE, "tiangong-lca-research-controller")
)

_REQUIRED_FLOW_BINDINGS = [
    "target",
    "identity_decision.decision",
    "unit_of_analysis",
    "name_plan.base_name",
    "target.flow_type",
    "flow_property_plan.reference_property",
    "flow_property_plan.reference_unit",
]


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


def _sha256_text(value: object, label: str) -> str:
    text = _required_text(value, label)
    if not _SHA256_PATTERN.fullmatch(text):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return text


def _timestamp_text(value: object) -> str:
    text = _required_text(value, "authored_at_utc")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("authored_at_utc must be ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("authored_at_utc must include a UTC offset")
    return (
        parsed.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _localized(text: str) -> JsonObject:
    return {"#text": text, "@xml:lang": "en"}


def _global_reference(
    *,
    kind: str,
    ref_id: str,
    version: str,
    uri: str,
    description: str,
) -> JsonObject:
    return {
        "@type": kind,
        "@refObjectId": ref_id,
        "@version": version,
        "@uri": uri,
        "common:shortDescription": _localized(description),
    }


def _source_contract(manifest: JsonObject) -> JsonObject:
    if manifest.get("kind") != "source":
        raise ValueError("source contract manifest kind must be source")
    result: JsonObject = {
        "sdk_package": _required_text(
            manifest.get("sdk_package"), "source contract sdk_package"
        )
    }
    for key in ("schema",):
        record = manifest.get(key)
        if not isinstance(record, dict):
            raise ValueError(f"source contract {key} record is missing")
        result[f"{key}_sha256"] = _sha256_text(
            record.get("sha256"), f"source contract {key}.sha256"
        )
    for key in ("methodology", "ruleset"):
        record = manifest.get(key)
        if record is None:
            result[f"{key}_sha256"] = None
            result[f"{key}_status"] = "unavailable_in_native_source_contract"
        elif isinstance(record, dict):
            result[f"{key}_sha256"] = _sha256_text(
                record.get("sha256"), f"source contract {key}.sha256"
            )
            result[f"{key}_status"] = "available"
        else:
            raise ValueError(f"source contract {key} record is invalid")
    return result


def _source_payload(
    source: JsonObject,
    *,
    authored_at_utc: str,
    source_contract: JsonObject,
) -> JsonObject:
    source_id = _required_text(source.get("source_id"), "source.source_id")
    tidas_source_id = _uuid_text(
        source.get("tidas_source_id"), f"{source_id}.tidas_source_id"
    )
    title = _required_text(source.get("title"), f"{source_id}.title")
    url = _required_text(source.get("url"), f"{source_id}.url")
    canonical_identifier = _required_text(
        source.get("canonical_identifier"), f"{source_id}.canonical_identifier"
    )
    identifier_kind = _required_text(
        source.get("identifier_kind"), f"{source_id}.identifier_kind"
    )
    if identifier_kind not in {"doi", "url"}:
        raise ValueError(f"{source_id}.identifier_kind is unsupported")
    if source.get("identity_status") != (
        "exact_identifier_bound_pending_native_source_identity_capability"
    ):
        raise ValueError(f"{source_id}.identity_status is not exact-bound")

    format_reference = _global_reference(
        kind="source data set",
        ref_id=_FORMAT_SOURCE_ID,
        version="00.00.000",
        uri=f"https://data.tiangong.earth/sources/{_FORMAT_SOURCE_ID}.xml",
        description="TIDAS / ILCD data set format",
    )
    owner_reference = _global_reference(
        kind="contact data set",
        ref_id=_OWNER_CONTACT_ID,
        version="00.00.000",
        uri=f"https://data.tiangong.earth/contacts/{_OWNER_CONTACT_ID}.xml",
        description="TianGong LCA research controller",
    )
    contract_digest = str(source_contract["schema_sha256"])[:16]
    return {
        "sourceDataSet": {
            "@xmlns:common": "http://lca.jrc.it/ILCD/Common",
            "@xmlns": "http://lca.jrc.it/ILCD/Source",
            "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "@version": "1.1",
            "@xsi:schemaLocation": (
                "http://lca.jrc.it/ILCD/Source "
                "../../schemas/ILCD_SourceDataSet.xsd"
            ),
            "sourceInformation": {
                "dataSetInformation": {
                    "common:UUID": tidas_source_id,
                    "common:shortName": [_localized(title)],
                    "classificationInformation": {
                        "common:classification": {
                            "common:class": {
                                "@level": "0",
                                "@classId": "5",
                                "#text": "Publications and communications",
                            }
                        }
                    },
                    "sourceCitation": f"{title}. {canonical_identifier}",
                    "publicationType": (
                        "Article in periodical"
                        if identifier_kind == "doi"
                        else "Other unpublished and grey literature"
                    ),
                    "sourceDescriptionOrComment": [
                        _localized(
                            "Exact identifier-bound screening inventory evidence; "
                            "remote source duplicate resolution is not available in "
                            f"TianGong CLI 0.0.25. Contract {contract_digest}."
                        )
                    ],
                    "referenceToDigitalFile": {"@uri": url},
                }
            },
            "administrativeInformation": {
                "dataEntryBy": {
                    "common:timeStamp": authored_at_utc,
                    "common:referenceToDataSetFormat": format_reference,
                },
                "publicationAndOwnership": {
                    "common:dataSetVersion": SOURCE_DATASET_VERSION,
                    "common:permanentDataSetURI": (
                        f"https://data.tiangong.earth/sources/{tidas_source_id}.xml"
                    ),
                    "common:referenceToOwnershipOfDataSet": owner_reference,
                },
            },
        }
    }


def _flow_role_treatment(role: str) -> str:
    return {
        "energy": "Process energy carrier, route-core screening",
        "reference_product": "Uncoated route-core reference product",
        "co_product": "Route-core co-product",
        "technosphere": "Route-core technosphere input",
    }.get(role, "Route-core screening flow")


def _flow_classification(role: str) -> List[str]:
    leaf = {
        "energy": "Energy carriers and services",
        "reference_product": "Materials and products",
        "co_product": "Materials and co-products",
        "technosphere": "Materials and products",
    }.get(role, "Unspecified product flows")
    return ["Technosphere flows", leaf]


def _flow_property_plan(unit: str) -> JsonObject:
    if unit == "kg":
        return {
            "reference_property": "Mass",
            "reference_property_id": _MASS_FLOW_PROPERTY_ID,
            "reference_property_version": "00.00.000",
            "reference_property_uri": (
                f"../flow-properties/{_MASS_FLOW_PROPERTY_ID}.xml"
            ),
            "reference_unit": "kg",
            "mean_value": 1.0,
            "support_reference_status": "native_cli_builtin_mass_uuid",
        }
    if unit == "MJ":
        return {
            "reference_property": "Energy",
            "reference_unit": "MJ",
            "mean_value": 1.0,
            "support_reference_status": (
                "native_cli_deterministic_reference_pending_remote_resolution"
            ),
        }
    raise ValueError(f"unsupported flow authoring unit: {unit}")


def _flow_plan(
    flow: JsonObject,
    decision: JsonObject,
    source_by_id: Dict[str, JsonObject],
    *,
    authored_at_utc: str,
    source_contract: JsonObject,
) -> JsonObject:
    flow_key = _required_text(flow.get("flow_key"), "flow.flow_key")
    flow_id = _uuid_text(flow.get("flow_id"), f"{flow_key}.flow_id")
    if decision.get("flow_key") != flow_key or decision.get("flow_id") != flow_id:
        raise ValueError(f"{flow_key} identity decision does not bind the flow")
    if decision.get("decision") != "create_new" or decision.get("status") != "passed":
        raise ValueError(f"{flow_key} is not an accepted create_new decision")
    if decision.get("blocker_count") != 0:
        raise ValueError(f"{flow_key} identity decision contains blockers")
    report_sha256 = _sha256_text(
        decision.get("report_sha256"), f"{flow_key}.report_sha256"
    )
    name = _required_text(flow.get("name"), f"{flow_key}.name")
    role = _required_text(flow.get("role"), f"{flow_key}.role")
    unit = _required_text(flow.get("unit"), f"{flow_key}.unit")
    source_ids = flow.get("source_ids")
    if not isinstance(source_ids, list) or not source_ids:
        raise ValueError(f"{flow_key}.source_ids must be a non-empty list")
    try:
        sources = [source_by_id[str(source_id)] for source_id in source_ids]
    except KeyError as error:
        raise ValueError(f"{flow_key} references an unknown source") from error
    evidence_sources = [
        {
            "id": source["tidas_source_id"],
            "source_id": source["source_id"],
            "version": SOURCE_DATASET_VERSION,
            "title": source["title"],
            "url": source["url"],
            "canonical_identifier": source["canonical_identifier"],
            "uri": (
                f"../sources/{source['tidas_source_id']}_{SOURCE_DATASET_VERSION}.xml"
            ),
        }
        for source in sources
    ]
    evidence_source_ids = [str(row["id"]) for row in evidence_sources]
    field_bindings = [
        {
            "field_path": path,
            "source_ids": evidence_source_ids,
            "basis": (
                "exact identifier-bound source plus accepted native flow identity "
                f"report {report_sha256[:16]}"
            ),
        }
        for path in _REQUIRED_FLOW_BINDINGS
    ]
    property_plan = _flow_property_plan(unit)
    return {
        "schema_version": 1,
        "kind": "flow",
        "flow_key": flow_key,
        "target": {
            "uuid": flow_id,
            "version": FLOW_DATASET_VERSION,
            "flow_type": "Product flow",
            "classification_path": _flow_classification(role),
            "permanent_uri": (
                f"https://data.tiangong.earth/flows/{flow_id}.xml"
            ),
            "general_comment": (
                "Source-bound route-core screening flow. Remote duplicate coverage "
                "and remote support-reference resolution remain open."
            ),
        },
        "identity_decision": {
            "decision": "create_new",
            "target_id": flow_id,
            "confidence": decision.get("confidence"),
            "report_sha256": report_sha256,
        },
        "unit_of_analysis": {
            "decision": "declared_unit_dataset",
            "target_kind": "flow",
            "reference_flow": {
                "id": flow_id,
                "reference_unit": unit,
                "reference_amount": 1.0,
                "flow_property": property_plan["reference_property"],
            },
            "declared_unit": {"amount": 1.0, "unit": unit},
        },
        "name_plan": {
            "base_name": name,
            "treatment_standards_routes": _flow_role_treatment(role),
            "mix_and_location_types": "Technology-specific, at process",
        },
        "flow_property_plan": property_plan,
        "evidence_manifest": {
            "summary": (
                "Exact source identifiers and the native flow identity decision bind "
                "all authoring fields."
            ),
            "sources": evidence_sources,
            "field_bindings": field_bindings,
        },
        "administrative_information": {
            "time_stamp": authored_at_utc,
            "version": FLOW_DATASET_VERSION,
        },
        "ruleset": {"id": "flow-authoring/strict", "version": "1"},
        "generation_manifest": {
            "contract": ENTITY_AUTHORING_CONTRACT,
            "identity_contract": IDENTITY_PREFLIGHT_CONTRACT,
            "source_schema_sha256": source_contract["schema_sha256"],
            "source_ids": [str(row["source_id"]) for row in evidence_sources],
            "remote_duplicate_coverage": "missing",
        },
    }


def compile_inventory_entity_authoring(
    compiled_identity_inputs: JsonObject,
    identity_result: JsonObject,
    source_contract_manifest: JsonObject,
    *,
    authored_at_utc: str,
) -> JsonObject:
    """Compile deterministic source rows and native flow build plans."""

    authored_at = _timestamp_text(authored_at_utc)
    source_contract = _source_contract(copy.deepcopy(source_contract_manifest))
    if compiled_identity_inputs.get("contract") != IDENTITY_PREFLIGHT_CONTRACT:
        raise ValueError("compiled identity inputs use an unsupported contract")
    if identity_result.get("contract") != IDENTITY_PREFLIGHT_CONTRACT:
        raise ValueError("identity result uses an unsupported contract")
    if identity_result.get("status") != (
        "flow_preflight_complete_source_capability_gap"
    ):
        raise ValueError("identity result is not ready for entity authoring")
    if identity_result.get("flow_resolution_required_count") != 0:
        raise ValueError("identity result still requires flow resolution")
    flow_requests = compiled_identity_inputs.get("flow_requests")
    source_assessments = compiled_identity_inputs.get("source_assessments")
    decisions = identity_result.get("flow_decisions")
    accepted_sources = identity_result.get("source_assessments")
    if not isinstance(flow_requests, list) or not all(
        isinstance(row, dict) for row in flow_requests
    ):
        raise ValueError("compiled identity flow requests are invalid")
    if not isinstance(source_assessments, list) or not all(
        isinstance(row, dict) for row in source_assessments
    ):
        raise ValueError("compiled identity source assessments are invalid")
    if not isinstance(decisions, list) or not all(
        isinstance(row, dict) for row in decisions
    ):
        raise ValueError("identity flow decisions are invalid")
    if accepted_sources != source_assessments:
        raise ValueError("identity source assessments drifted from compiled inputs")
    if len(flow_requests) != len(decisions):
        raise ValueError("flow request and decision counts differ")
    source_by_id = {
        _required_text(row.get("source_id"), "source.source_id"): row
        for row in source_assessments
    }
    if len(source_by_id) != len(source_assessments):
        raise ValueError("source assessments contain duplicate IDs")
    decision_by_key = {
        _required_text(row.get("flow_key"), "decision.flow_key"): row
        for row in decisions
    }
    if len(decision_by_key) != len(decisions):
        raise ValueError("flow decisions contain duplicate keys")

    source_rows = [
        {
            "source_id": source_id,
            "tidas_source_id": source["tidas_source_id"],
            "canonical_identifier": source["canonical_identifier"],
            "payload": _source_payload(
                source,
                authored_at_utc=authored_at,
                source_contract=source_contract,
            ),
        }
        for source_id, source in sorted(source_by_id.items())
    ]
    flow_plans = []
    for flow in sorted(flow_requests, key=lambda row: str(row.get("flow_key"))):
        flow_key = _required_text(flow.get("flow_key"), "flow.flow_key")
        if flow_key not in decision_by_key:
            raise ValueError(f"missing identity decision for {flow_key}")
        flow_plans.append(
            {
                "flow_key": flow_key,
                "flow_id": flow["flow_id"],
                "plan": _flow_plan(
                    flow,
                    decision_by_key[flow_key],
                    source_by_id,
                    authored_at_utc=authored_at,
                    source_contract=source_contract,
                ),
            }
        )
    return {
        "schema_version": 1,
        "contract": ENTITY_AUTHORING_CONTRACT,
        "authored_at_utc": authored_at,
        "source_contract": source_contract,
        "source_count": len(source_rows),
        "flow_count": len(flow_plans),
        "source_rows": source_rows,
        "flow_plans": flow_plans,
        "support_reference_status": {
            "format_source": "local_reference_pending_remote_resolution",
            "owner_contact": "local_reference_pending_remote_resolution",
            "mass_flow_property": "native_cli_builtin_uuid",
            "energy_flow_property": (
                "native_cli_deterministic_reference_pending_remote_resolution"
            ),
        },
        "readiness": {
            "local_schema_authoring": "ready",
            "remote_save": "prohibited_support_and_duplicate_coverage_missing",
            "scenario_reference_rewrite": "pending_native_materialize_validate_qa",
            "comparative_lcia": "prohibited",
        },
    }
