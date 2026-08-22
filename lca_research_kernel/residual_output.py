"""Compile conservation-bound residual outputs without inferring composition."""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Tuple


JsonObject = Dict[str, object]

RESIDUAL_OUTPUT_CONTRACT = "lca_research_kernel.mass_residual_output/v1"
RESIDUAL_FLOW_VERSION = "00.00.001"
MASS_FLOW_PROPERTY_ID = "93a60a56-a3c8-11da-a746-0800200b9a66"
RESIDUAL_NAMESPACE = uuid.UUID("c51af30f-d0e8-5f86-a613-587e0d7639a7")
SELECTED_GAP_ID = "mass-conserving-uncharacterized-outputs"
SOURCE_ID = "argonne-dunn-2015-lfp-lci"


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _decimal(value: object, label: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{label} must be numeric") from error
    if not result.is_finite() or result < 0:
        raise ValueError(f"{label} must be finite and non-negative")
    return result


def _amount_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _localized(text: str) -> JsonObject:
    return {"#text": text, "@xml:lang": "en"}


def _single_localized_text(value: object, label: str) -> str:
    rows = [value] if isinstance(value, dict) else value
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError(f"{label} must contain exactly one localized value")
    return _required_text(rows[0].get("#text"), f"{label}.#text")


def _comment(exchange: JsonObject) -> JsonObject:
    value = exchange.get("generalComment")
    rows = [value] if isinstance(value, dict) else value
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("exchange must contain exactly one generalComment")
    text = _required_text(rows[0].get("#text"), "exchange generalComment.#text")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError("exchange generalComment is not canonical JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("exchange generalComment JSON must be an object")
    return payload


def _process_id(row: JsonObject) -> str:
    try:
        value = row["processDataSet"]["processInformation"]["dataSetInformation"][
            "common:UUID"
        ]
    except (KeyError, TypeError) as error:
        raise ValueError("scenario process row is malformed") from error
    return _required_text(value, "scenario process UUID")


def _profile(row: JsonObject) -> JsonObject:
    try:
        exchanges = row["processDataSet"]["exchanges"]["exchange"]
    except (KeyError, TypeError) as error:
        raise ValueError("scenario exchanges are missing") from error
    if not isinstance(exchanges, list) or not exchanges:
        raise ValueError("scenario exchanges must be a non-empty list")
    raw = Decimal("0")
    product = Decimal("0")
    energy = Decimal("0")
    internal_ids = []
    positive_input_count = 0
    positive_output_count = 0
    for exchange in exchanges:
        if not isinstance(exchange, dict):
            raise ValueError("scenario exchange must be an object")
        internal_id = int(_required_text(exchange.get("@dataSetInternalID"), "internal ID"))
        internal_ids.append(internal_id)
        amount = _decimal(exchange.get("resultingAmount"), "resultingAmount")
        if amount != _decimal(exchange.get("meanAmount"), "meanAmount"):
            raise ValueError("meanAmount and resultingAmount must be identical")
        direction = exchange.get("exchangeDirection")
        if amount > 0 and direction == "Input":
            positive_input_count += 1
        elif amount > 0 and direction == "Output":
            positive_output_count += 1
        qa = _required_text(_comment(exchange).get("qa", "reference"), "exchange qa tag")
        if direction == "Input" and "[tg_io_kind_tag=raw_material]" in qa:
            raw += amount
        elif direction == "Input" and "[tg_io_kind_tag=energy]" in qa:
            energy += amount
        elif direction == "Output" and (
            "[tg_io_kind_tag=product]" in qa
            or exchange.get("quantitativeReference") is True
        ):
            product += amount
    if len(internal_ids) != len(set(internal_ids)) or min(internal_ids) <= 0:
        raise ValueError("scenario exchange internal IDs must be unique positive integers")
    return {
        "raw_input": raw,
        "product_output": product,
        "energy_excluded": energy,
        "next_internal_id": max(internal_ids) + 1,
        "exchange_count": len(exchanges),
        "positive_input_exchange_count": positive_input_count,
        "positive_output_exchange_count": positive_output_count,
    }


def compile_residual_definitions(
    scenario_rows: List[JsonObject],
    scenario_processes: List[JsonObject],
    route_qa: List[JsonObject],
    gap_result: JsonObject,
    source_assessments: List[JsonObject],
    *,
    scope_id: str,
    revision: str,
) -> JsonObject:
    """Derive one positive mass residual per route from accepted current amounts."""

    scope = _required_text(scope_id, "scope_id")
    rev = _required_text(revision, "revision")
    selected = gap_result.get("selected_gap")
    if not isinstance(selected, dict) or selected.get("gap_id") != SELECTED_GAP_ID:
        raise ValueError("current scientific-gap selection is not the residual-output slice")
    if selected.get("actionability") != "ready":
        raise ValueError("selected residual-output slice is not ready")
    if len(scenario_rows) != 2 or len(scenario_processes) != 2 or len(route_qa) != 2:
        raise ValueError("residual derivation requires exactly two routes")
    rows_by_id = {_process_id(row): row for row in scenario_rows}
    if len(rows_by_id) != 2:
        raise ValueError("scenario process identities must be unique")
    qa_by_route = {
        _required_text(row.get("route_id"), "QA route_id"): row
        for row in route_qa
        if isinstance(row, dict)
    }
    source_by_id = {
        _required_text(row.get("source_id"), "source_id"): row
        for row in source_assessments
        if isinstance(row, dict)
    }
    if SOURCE_ID not in source_by_id:
        raise ValueError("Argonne source assessment is required for residual derivation")
    source = source_by_id[SOURCE_ID]
    definitions: List[JsonObject] = []
    for process_ref in sorted(scenario_processes, key=lambda row: str(row.get("route_id"))):
        route_id = _required_text(process_ref.get("route_id"), "route_id")
        process_id = _required_text(
            process_ref.get("scenario_process_id"), "scenario_process_id"
        )
        if route_id not in {"solid-state", "hydrothermal"}:
            raise ValueError(f"unsupported residual route: {route_id}")
        if process_id not in rows_by_id or route_id not in qa_by_route:
            raise ValueError("scenario and QA route bindings do not close")
        qa = qa_by_route[route_id]
        if qa.get("scenario_process_id") != process_id or qa.get("blocker_count") != 0:
            raise ValueError("route QA is not accepted for residual derivation")
        profile = _profile(rows_by_id[process_id])
        for key in ("raw_input", "product_output", "energy_excluded"):
            if profile[key] != _decimal(qa.get(key), f"QA {key}"):
                raise ValueError(f"scenario {key} does not match native QA")
        residual = profile["raw_input"] - profile["product_output"]
        if residual <= 0:
            raise ValueError(f"{route_id} does not have a positive mass residual")
        flow_key = f"{route_id}-uncharacterized-mass-residual-proxy"
        flow_id = str(
            uuid.uuid5(
                RESIDUAL_NAMESPACE,
                f"{scope}:{rev}:{route_id}:{SELECTED_GAP_ID}:{RESIDUAL_OUTPUT_CONTRACT}",
            )
        )
        name = (
            f"Uncharacterized process mass residual, {route_id} route proxy"
        )
        definitions.append(
            {
                "route_id": route_id,
                "scenario_process_id": process_id,
                "flow_key": flow_key,
                "flow_id": flow_id,
                "name": name,
                "flow_type": "Product flow",
                "flow_semantics": "uncharacterized_technosphere_output_proxy",
                "unit": "kg",
                "amount": _amount_text(residual),
                "derivation": {
                    "method": "accepted_raw_mass_minus_accepted_product_coproduct_mass",
                    "raw_input_kg": _amount_text(profile["raw_input"]),
                    "product_coproduct_output_kg": _amount_text(profile["product_output"]),
                    "residual_output_kg": _amount_text(residual),
                    "energy_excluded_mj": _amount_text(profile["energy_excluded"]),
                    "native_qa_report_sha256": _required_text(
                        qa.get("report_sha256"), "QA report_sha256"
                    ),
                },
                "next_internal_id": profile["next_internal_id"],
                "previous_exchange_count": profile["exchange_count"],
                "positive_input_exchange_count": profile[
                    "positive_input_exchange_count"
                ],
                "positive_output_exchange_count": profile[
                    "positive_output_exchange_count"
                ],
                "source": copy.deepcopy(source),
                "composition_status": "unknown_not_inferred_from_mass_conservation",
                "environmental_compartment": None,
                "characterization_status": "prohibited_until_species_resolved_evidence",
                "replacement_trigger": "species_resolved_primary_or_validated_engineering_evidence",
                "identity_request": {
                    "schema_version": 1,
                    "flow_key": flow_key,
                    "target": {
                        "id": flow_id,
                        "name": name,
                        "type_of_dataset": "Product flow",
                        "flow_property": "Mass",
                        "reference_unit": "kg",
                    },
                    "candidates": [],
                    "candidate_coverage": {
                        "local_candidate_sources": [],
                        "native_remote_search": "not_requested_credentials_unavailable",
                    },
                    "remote_candidate_search": {
                        "enabled": False,
                        "data_source": None,
                        "limit": None,
                    },
                },
            }
        )
    return {
        "schema_version": 1,
        "contract": RESIDUAL_OUTPUT_CONTRACT,
        "scope_id": scope,
        "revision": rev,
        "selected_gap_id": SELECTED_GAP_ID,
        "status": "two_positive_conservation_residuals_compiled",
        "flow_count": 2,
        "exchange_count": 2,
        "definitions": definitions,
        "scientific_boundary": {
            "quantity_basis": "mass conservation over accepted modeled kg exchanges",
            "composition_inferred": False,
            "phase_inferred": False,
            "environmental_compartment_inferred": False,
            "characterization_allowed": False,
            "comparative_lcia_allowed": False,
        },
    }


def compile_residual_flow_plan(
    definition: JsonObject,
    identity_decision: JsonObject,
    *,
    authored_at_utc: str,
    source_schema_sha256: str,
) -> JsonObject:
    """Bind an accepted native identity decision into a native Flow Build Plan."""

    try:
        timestamp = datetime.fromisoformat(authored_at_utc.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise ValueError("authored_at_utc must be ISO-8601") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("authored_at_utc must contain an offset")
    timestamp_text = (
        timestamp.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    flow_id = _required_text(definition.get("flow_id"), "flow_id")
    flow_key = _required_text(definition.get("flow_key"), "flow_key")
    name = _required_text(definition.get("name"), "name")
    if (
        identity_decision.get("decision") != "create_new"
        or identity_decision.get("status") != "passed"
        or identity_decision.get("target", {}).get("id") != flow_id
        or identity_decision.get("target", {}).get("names") != [name]
        or identity_decision.get("blockers") != []
    ):
        raise ValueError("native residual flow identity decision is not accepted")
    report_sha256 = _required_text(
        identity_decision.get("report_sha256"), "identity report_sha256"
    )
    source = definition.get("source")
    if not isinstance(source, dict) or source.get("source_id") != SOURCE_ID:
        raise ValueError("residual flow definition lacks its exact source binding")
    source_uuid = _required_text(source.get("tidas_source_id"), "source UUID")
    source_record = {
        "id": source_uuid,
        "source_id": SOURCE_ID,
        "version": RESIDUAL_FLOW_VERSION,
        "title": _required_text(source.get("title"), "source title"),
        "url": _required_text(source.get("url"), "source URL"),
        "canonical_identifier": _required_text(
            source.get("canonical_identifier"), "source canonical_identifier"
        ),
        "uri": f"../sources/{source_uuid}_{RESIDUAL_FLOW_VERSION}.xml",
    }
    required_paths = [
        "target",
        "identity_decision.decision",
        "unit_of_analysis",
        "name_plan.base_name",
        "target.flow_type",
        "flow_property_plan.reference_property",
        "flow_property_plan.reference_unit",
    ]
    return {
        "schema_version": 1,
        "kind": "flow",
        "flow_key": flow_key,
        "target": {
            "uuid": flow_id,
            "version": RESIDUAL_FLOW_VERSION,
            "flow_type": "Product flow",
            "classification_path": [
                "Technosphere flows",
                "Uncharacterized residual outputs",
            ],
            "permanent_uri": f"https://data.tiangong.earth/flows/{flow_id}.xml",
            "general_comment": (
                "Conservation-derived technosphere output proxy with unknown "
                "composition, phase, disposition and environmental compartment; "
                "not characterizable and not comparison-ready."
            ),
        },
        "identity_decision": {
            "decision": "create_new",
            "target_id": flow_id,
            "confidence": identity_decision.get("confidence"),
            "report_sha256": report_sha256,
        },
        "unit_of_analysis": {
            "decision": "declared_unit_dataset",
            "target_kind": "flow",
            "reference_flow": {
                "id": flow_id,
                "reference_unit": "kg",
                "reference_amount": 1.0,
                "flow_property": "Mass",
            },
            "declared_unit": {"amount": 1.0, "unit": "kg"},
        },
        "name_plan": {
            "base_name": name,
            "treatment_standards_routes": "Composition-unknown route residual proxy",
            "mix_and_location_types": "Technology-specific, at process",
        },
        "flow_property_plan": {
            "reference_property": "Mass",
            "reference_property_id": MASS_FLOW_PROPERTY_ID,
            "reference_property_version": "00.00.000",
            "reference_property_uri": f"../flow-properties/{MASS_FLOW_PROPERTY_ID}.xml",
            "reference_unit": "kg",
            "mean_value": 1.0,
            "support_reference_status": "native_cli_builtin_mass_uuid",
        },
        "evidence_manifest": {
            "summary": (
                "Accepted source-bound scenario quantities, conservation derivation, "
                "and native flow identity decision bind this proxy."
            ),
            "sources": [source_record],
            "field_bindings": [
                {
                    "field_path": path,
                    "source_ids": [source_uuid],
                    "basis": (
                        "source-bound route inventory plus accepted conservation "
                        f"derivation and native identity report {report_sha256[:16]}"
                    ),
                }
                for path in required_paths
            ],
        },
        "administrative_information": {
            "time_stamp": timestamp_text,
            "version": RESIDUAL_FLOW_VERSION,
        },
        "ruleset": {"id": "flow-authoring/strict", "version": "1"},
        "generation_manifest": {
            "contract": RESIDUAL_OUTPUT_CONTRACT,
            "identity_contract": "native-flow-identity-preflight/cli-0.0.25",
            "source_schema_sha256": _required_text(
                source_schema_sha256, "source_schema_sha256"
            ),
            "source_ids": [SOURCE_ID],
            "remote_duplicate_coverage": "missing",
            "composition_status": definition["composition_status"],
            "characterization_status": definition["characterization_status"],
        },
    }


def _flow_record(row: JsonObject) -> JsonObject:
    try:
        dataset = row["flowDataSet"]
        information = dataset["flowInformation"]["dataSetInformation"]
        publication = dataset["administrativeInformation"]["publicationAndOwnership"]
        flow_type = dataset["modellingAndValidation"]["LCIMethod"]["typeOfDataSet"]
    except (KeyError, TypeError) as error:
        raise ValueError("residual flow row is malformed") from error
    flow_id = _required_text(information.get("common:UUID"), "flow UUID")
    return {
        "id": flow_id,
        "name": _single_localized_text(information.get("name", {}).get("baseName"), "flow name"),
        "version": _required_text(publication.get("common:dataSetVersion"), "flow version"),
        "uri": _required_text(publication.get("common:permanentDataSetURI"), "flow URI"),
        "flow_type": flow_type,
    }


def _source_reference(source: JsonObject) -> JsonObject:
    source_id = _required_text(source.get("tidas_source_id"), "source UUID")
    return {
        "@type": "source data set",
        "@refObjectId": source_id,
        "@version": RESIDUAL_FLOW_VERSION,
        "@uri": f"https://data.tiangong.earth/sources/{source_id}.xml",
        "common:shortDescription": _localized(
            _required_text(source.get("title"), "source title")
        ),
    }


def _flow_reference(flow: JsonObject) -> JsonObject:
    return {
        "@type": "flow data set",
        "@refObjectId": flow["id"],
        "@version": flow["version"],
        "@uri": flow["uri"],
        "common:shortDescription": _localized(str(flow["name"])),
    }


def _residual_exchange(
    definition: JsonObject, flow: JsonObject
) -> JsonObject:
    comment = {
        "contract": RESIDUAL_OUTPUT_CONTRACT,
        "flow_key": definition["flow_key"],
        "unit": "kg",
        "exchange_type": "uncharacterized_technosphere_output_proxy",
        "qa": "[tg_io_kind_tag=product][tg_io_uom_tag=kg] residual proxy",
        "composition_status": "unknown",
        "characterization": "prohibited",
        "scientific_use": "mass_balance_visibility_only_no_comparative_lcia",
    }
    comment_text = json.dumps(comment, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(comment_text) > 500:
        raise ValueError("residual exchange comment exceeds TIDAS limit")
    return {
        "@dataSetInternalID": str(definition["next_internal_id"]),
        "referenceToFlowDataSet": _flow_reference(flow),
        "exchangeDirection": "Output",
        "meanAmount": definition["amount"],
        "resultingAmount": definition["amount"],
        "dataSourceType": "Secondary",
        "dataDerivationTypeStatus": "Calculated",
        "referencesToDataSource": {
            "referenceToDataSource": _source_reference(definition["source"])
        },
        "generalComment": [_localized(comment_text)],
    }


def compile_residual_patch(
    scenario_rows: List[JsonObject],
    definitions: JsonObject,
    validated_flow_rows: List[JsonObject],
    *,
    evidence: JsonObject,
) -> JsonObject:
    """Compile two evidence-bearing append-only TianGong dataset patch sets."""

    if definitions.get("contract") != RESIDUAL_OUTPUT_CONTRACT:
        raise ValueError("unsupported residual definition contract")
    if len(scenario_rows) != 2 or len(validated_flow_rows) != 2:
        raise ValueError("residual patch requires two scenarios and two flows")
    rows_by_id = {_process_id(row): row for row in scenario_rows}
    row_index_by_id = {
        _process_id(row): index for index, row in enumerate(scenario_rows)
    }
    flow_records = {_flow_record(row)["id"]: _flow_record(row) for row in validated_flow_rows}
    patch_sets = []
    scenario_processes = []
    for definition in definitions["definitions"]:
        process_id = definition["scenario_process_id"]
        flow_id = definition["flow_id"]
        if process_id not in rows_by_id or flow_id not in flow_records:
            raise ValueError("residual patch definition did not resolve scenario/flow")
        flow = flow_records[flow_id]
        if (
            flow["name"] != definition["name"]
            or flow["version"] != RESIDUAL_FLOW_VERSION
            or flow["flow_type"] != "Product flow"
        ):
            raise ValueError("validated residual flow drifted from definition")
        row = rows_by_id[process_id]
        try:
            version = row["processDataSet"]["administrativeInformation"][
                "publicationAndOwnership"
            ]["common:dataSetVersion"]
            exchanges = row["processDataSet"]["exchanges"]["exchange"]
        except (KeyError, TypeError) as error:
            raise ValueError("scenario process metadata is malformed") from error
        if len(exchanges) != definition["previous_exchange_count"]:
            raise ValueError("scenario exchange count drifted before residual patch")
        exchange = _residual_exchange(definition, flow)
        operation_evidence = {
            "selected_gap_id": SELECTED_GAP_ID,
            "definition_contract": RESIDUAL_OUTPUT_CONTRACT,
            "route_id": definition["route_id"],
            "derivation": copy.deepcopy(definition["derivation"]),
            "flow_id": flow_id,
            "flow_version": flow["version"],
            **copy.deepcopy(evidence),
        }
        patch_sets.append(
            {
                "row_index": row_index_by_id[process_id],
                "dataset_id": process_id,
                "dataset_version": version,
                "operations": [
                    {
                        "op": "test",
                        "path": "/processDataSet/processInformation/dataSetInformation/common:UUID",
                        "value": process_id,
                    },
                    {
                        "op": "test",
                        "path": "/processDataSet/administrativeInformation/publicationAndOwnership/common:dataSetVersion",
                        "value": version,
                    },
                    {
                        "op": "test",
                        "path": (
                            "/processDataSet/exchanges/exchange/"
                            f"{len(exchanges) - 1}/@dataSetInternalID"
                        ),
                        "value": exchanges[-1]["@dataSetInternalID"],
                    },
                    {
                        "op": "add",
                        "path": "/processDataSet/exchanges/exchange/-",
                        "value": exchange,
                        "basis": (
                            "Expose the exact positive mass-conservation residual "
                            "as an uncharacterized technosphere output proxy."
                        ),
                        "evidence": operation_evidence,
                        "resolution": {
                            "mode": "conservation_quantity_only",
                            "composition_status": definition["composition_status"],
                            "environmental_compartment": None,
                            "characterization_status": definition[
                                "characterization_status"
                            ],
                            "replacement_trigger": definition["replacement_trigger"],
                            "comparative_lcia_allowed": False,
                        },
                    },
                ],
            }
        )
        scenario_processes.append(
            {
                "route_id": definition["route_id"],
                "scenario_process_id": process_id,
                "dataset_version": version,
                "residual_flow_id": flow_id,
                "residual_amount_kg": definition["amount"],
                "positive_input_exchange_count": definition[
                    "positive_input_exchange_count"
                ],
                "positive_output_exchange_count": (
                    definition["positive_output_exchange_count"] + 1
                ),
                "expected_qa_totals": {
                    "raw_input": float(definition["derivation"]["raw_input_kg"]),
                    "product_output": float(definition["derivation"]["raw_input_kg"]),
                    "energy_excluded": float(
                        definition["derivation"]["energy_excluded_mj"]
                    ),
                    "finding_count": 0,
                    "blocker_count": 0,
                },
            }
        )
    return {
        "schema_version": 1,
        "contract": RESIDUAL_OUTPUT_CONTRACT,
        "patch_status": "completed",
        "scenario_kind": "route_core_screening_proxy_with_uncharacterized_mass_residuals",
        "patch_sets": patch_sets,
        "scenario_processes": scenario_processes,
        "residual_exchange_count": 2,
        "protected_scenario_sha256_before": hashlib.sha256(
            _json_bytes(scenario_rows)
        ).hexdigest(),
        "scientific_boundary": copy.deepcopy(definitions["scientific_boundary"]),
        "scientific_comparison_ready": False,
    }


def apply_residual_patch(
    scenario_rows: List[JsonObject], patch: JsonObject
) -> List[JsonObject]:
    """Apply the narrow residual patch for deterministic native-output comparison."""

    if patch.get("contract") != RESIDUAL_OUTPUT_CONTRACT:
        raise ValueError("unsupported residual patch contract")
    result = copy.deepcopy(scenario_rows)
    index_by_id = {_process_id(row): index for index, row in enumerate(result)}
    for patch_set in patch["patch_sets"]:
        process_id = patch_set["dataset_id"]
        if process_id not in index_by_id:
            raise ValueError("residual patch targets an unknown process")
        row = result[index_by_id[process_id]]
        for operation in patch_set["operations"]:
            if operation["op"] == "test":
                continue
            if operation["op"] != "add" or operation["path"] != (
                "/processDataSet/exchanges/exchange/-"
            ):
                raise ValueError("residual patch contains a non-append mutation")
            row["processDataSet"]["exchanges"]["exchange"].append(
                copy.deepcopy(operation["value"])
            )
    return result


def protected_residual_sha256(rows: List[JsonObject]) -> str:
    """Hash scenario content after removing only contract-marked residual exchanges."""

    projected = copy.deepcopy(rows)
    for row in projected:
        exchanges = row["processDataSet"]["exchanges"]["exchange"]
        row["processDataSet"]["exchanges"]["exchange"] = [
            exchange
            for exchange in exchanges
            if _comment(exchange).get("contract") != RESIDUAL_OUTPUT_CONTRACT
        ]
    return hashlib.sha256(_json_bytes(projected)).hexdigest()
