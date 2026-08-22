"""Compile governed canonical entity references for isolated scenario rows."""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from typing import Dict, List, Tuple


JsonObject = Dict[str, object]

REFERENCE_INTEGRATION_CONTRACT = (
    "lca_research_kernel.inventory_reference_integration/v1"
)
CANONICAL_DATASET_VERSION = "00.00.001"


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _canonical_uuid(value: object, label: str) -> str:
    text = _required_text(value, label)
    try:
        parsed = uuid.UUID(text)
    except ValueError as error:
        raise ValueError(f"{label} must be a UUID") from error
    if str(parsed) != text.lower():
        raise ValueError(f"{label} must use canonical lowercase UUID form")
    return text.lower()


def _single_localized_text(value: object, label: str) -> str:
    rows = [value] if isinstance(value, dict) else value
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError(f"{label} must contain exactly one localized value")
    row = rows[0]
    if not isinstance(row, dict):
        raise ValueError(f"{label} localized value must be an object")
    return _required_text(row.get("#text"), f"{label}.#text")


def _flow_records(rows: List[JsonObject]) -> Dict[str, JsonObject]:
    records: Dict[str, JsonObject] = {}
    for index, row in enumerate(rows):
        try:
            dataset = row["flowDataSet"]
            information = dataset["flowInformation"]["dataSetInformation"]
            administration = dataset["administrativeInformation"][
                "publicationAndOwnership"
            ]
        except (KeyError, TypeError) as error:
            raise ValueError(f"canonical flow row {index} is malformed") from error
        flow_id = _canonical_uuid(
            information.get("common:UUID"), f"canonical flow row {index}.UUID"
        )
        version = _required_text(
            administration.get("common:dataSetVersion"),
            f"canonical flow {flow_id}.version",
        )
        if version != CANONICAL_DATASET_VERSION:
            raise ValueError(f"canonical flow {flow_id} has unsupported version")
        if flow_id in records:
            raise ValueError(f"duplicate canonical flow UUID: {flow_id}")
        records[flow_id] = {
            "id": flow_id,
            "version": version,
            "uri": _required_text(
                administration.get("common:permanentDataSetURI"),
                f"canonical flow {flow_id}.URI",
            ),
            "name": _single_localized_text(
                information.get("name", {}).get("baseName"),
                f"canonical flow {flow_id}.name",
            ),
        }
    return records


def _source_records(
    rows: List[JsonObject],
) -> Tuple[Dict[str, JsonObject], Dict[str, JsonObject], Dict[str, JsonObject]]:
    by_id: Dict[str, JsonObject] = {}
    by_title: Dict[str, JsonObject] = {}
    by_digital_uri: Dict[str, JsonObject] = {}
    for index, row in enumerate(rows):
        try:
            dataset = row["sourceDataSet"]
            information = dataset["sourceInformation"]["dataSetInformation"]
            administration = dataset["administrativeInformation"][
                "publicationAndOwnership"
            ]
        except (KeyError, TypeError) as error:
            raise ValueError(f"canonical source row {index} is malformed") from error
        source_id = _canonical_uuid(
            information.get("common:UUID"), f"canonical source row {index}.UUID"
        )
        version = _required_text(
            administration.get("common:dataSetVersion"),
            f"canonical source {source_id}.version",
        )
        if version != CANONICAL_DATASET_VERSION:
            raise ValueError(f"canonical source {source_id} has unsupported version")
        title = _single_localized_text(
            information.get("common:shortName"),
            f"canonical source {source_id}.shortName",
        )
        digital_file = information.get("referenceToDigitalFile")
        if not isinstance(digital_file, dict):
            raise ValueError(f"canonical source {source_id} digital file is missing")
        digital_uri = _required_text(
            digital_file.get("@uri"), f"canonical source {source_id}.digital URI"
        )
        record = {
            "id": source_id,
            "version": version,
            "uri": _required_text(
                administration.get("common:permanentDataSetURI"),
                f"canonical source {source_id}.URI",
            ),
            "title": title,
            "digital_uri": digital_uri,
        }
        if source_id in by_id or title in by_title or digital_uri in by_digital_uri:
            raise ValueError("canonical source identities must be unique by ID/title/URI")
        by_id[source_id] = record
        by_title[title] = record
        by_digital_uri[digital_uri.lower()] = record
    return by_id, by_title, by_digital_uri


def _source_reference_target(
    reference: JsonObject,
    by_id: Dict[str, JsonObject],
    by_title: Dict[str, JsonObject],
    by_digital_uri: Dict[str, JsonObject],
) -> JsonObject:
    matches: Dict[str, JsonObject] = {}
    ref_id = reference.get("@refObjectId")
    if isinstance(ref_id, str) and ref_id in by_id:
        matches[str(by_id[ref_id]["id"])] = by_id[ref_id]
    uri = reference.get("@uri")
    if isinstance(uri, str) and uri.lower() in by_digital_uri:
        row = by_digital_uri[uri.lower()]
        matches[str(row["id"])] = row
    description = reference.get("common:shortDescription")
    if isinstance(description, (dict, list)):
        title = _single_localized_text(description, "source shortDescription")
        if title in by_title:
            row = by_title[title]
            matches[str(row["id"])] = row
    if len(matches) != 1:
        raise ValueError("scenario source reference did not resolve uniquely")
    return next(iter(matches.values()))


def _replace_operation(
    *,
    path: str,
    value: str,
    kind: str,
    canonical: JsonObject,
    entity_evidence: JsonObject,
) -> JsonObject:
    return {
        "op": "replace",
        "path": path,
        "value": value,
        "basis": (
            f"replace provisional {kind} reference with the validated local "
            f"canonical {CANONICAL_DATASET_VERSION} draft"
        ),
        "evidence": {
            "entity_kind": kind,
            "canonical_id": canonical["id"],
            "canonical_version": canonical["version"],
            "canonical_uri": canonical["uri"],
            **copy.deepcopy(entity_evidence),
        },
        "resolution": {
            "mode": "accepted_local_canonical_reference",
            "remote_duplicate_coverage": "missing",
            "remote_save_authorized": False,
        },
    }


def _test(path: str, value: object) -> JsonObject:
    return {"op": "test", "path": path, "value": copy.deepcopy(value)}


def _reference_path(*parts: object) -> str:
    return "/" + "/".join(str(part) for part in parts)


def _protected_projection(rows: List[JsonObject]) -> List[JsonObject]:
    projected = copy.deepcopy(rows)
    for row in projected:
        dataset = row["processDataSet"]
        dataset["modellingAndValidation"][
            "dataSourcesTreatmentAndRepresentativeness"
        ]["referenceToDataSource"] = {"governed_reference": True}
        for exchange in dataset["exchanges"]["exchange"]:
            exchange["referenceToFlowDataSet"] = {"governed_reference": True}
            source_container = exchange.get("referencesToDataSource")
            if isinstance(source_container, dict):
                source_container["referenceToDataSource"] = {
                    "governed_reference": True
                }
    return projected


def protected_scenario_sha256(rows: List[JsonObject]) -> str:
    """Hash every scenario field except the explicitly governed references."""

    return hashlib.sha256(_json_bytes(_protected_projection(rows))).hexdigest()


def apply_inventory_reference_patch(
    scenario_rows: List[JsonObject], compiled_patch: JsonObject
) -> List[JsonObject]:
    """Apply a compiled reference-only patch for deterministic expectation checks."""

    if compiled_patch.get("contract") != REFERENCE_INTEGRATION_CONTRACT:
        raise ValueError("unsupported inventory reference patch contract")
    patch_sets = compiled_patch.get("patch_sets")
    if not isinstance(patch_sets, list):
        raise ValueError("inventory reference patch sets are missing")
    result = copy.deepcopy(scenario_rows)
    for patch_set in patch_sets:
        if not isinstance(patch_set, dict):
            raise ValueError("inventory reference patch set must be an object")
        row_index = patch_set.get("row_index")
        if not isinstance(row_index, int) or not 0 <= row_index < len(result):
            raise ValueError("inventory reference patch row index is invalid")
        operations = patch_set.get("operations")
        if not isinstance(operations, list):
            raise ValueError("inventory reference operations are missing")
        row = result[row_index]
        for operation in operations:
            if not isinstance(operation, dict):
                raise ValueError("inventory reference operation must be an object")
            path = _required_text(operation.get("path"), "patch path")
            tokens = path.lstrip("/").split("/")
            current: object = row
            for token in tokens[:-1]:
                if isinstance(current, list):
                    current = current[int(token)]
                elif isinstance(current, dict):
                    current = current[token]
                else:
                    raise ValueError(f"patch path does not resolve: {path}")
            key = tokens[-1]
            if isinstance(current, list):
                index = int(key)
                actual = current[index]
            elif isinstance(current, dict):
                actual = current[key]
            else:
                raise ValueError(f"patch path does not resolve: {path}")
            if operation.get("op") == "test":
                if actual != operation.get("value"):
                    raise ValueError(f"reference patch test failed: {path}")
            elif operation.get("op") == "replace":
                if isinstance(current, list):
                    current[index] = copy.deepcopy(operation.get("value"))
                else:
                    current[key] = copy.deepcopy(operation.get("value"))
            else:
                raise ValueError("reference integration allows only test/replace")
    return result


def compile_inventory_reference_integration(
    scenario_rows: List[JsonObject],
    canonical_flow_rows: List[JsonObject],
    canonical_source_rows: List[JsonObject],
    scenario_processes: List[JsonObject],
    *,
    entity_evidence: JsonObject,
) -> JsonObject:
    """Compile evidence-bearing patches that change references and nothing else."""

    if len(scenario_rows) != 2 or len(scenario_processes) != 2:
        raise ValueError("reference integration requires exactly two scenario rows")
    flow_by_id = _flow_records(copy.deepcopy(canonical_flow_rows))
    source_by_id, source_by_title, source_by_digital_uri = _source_records(
        copy.deepcopy(canonical_source_rows)
    )
    if len(flow_by_id) != 11 or len(source_by_id) != 4:
        raise ValueError("reference integration requires 11 flows and four sources")
    scenario_by_id = {
        _required_text(row.get("scenario_process_id"), "scenario_process_id"): row
        for row in scenario_processes
    }
    if len(scenario_by_id) != 2:
        raise ValueError("scenario_process_id values must be unique")

    patch_sets: List[JsonObject] = []
    reference_counts = {"flow": 0, "source": 0, "process_source": 0}
    referenced_flow_ids = set()
    referenced_source_ids = set()
    for row_index, row in enumerate(scenario_rows):
        try:
            dataset = row["processDataSet"]
            process_id = dataset["processInformation"]["dataSetInformation"][
                "common:UUID"
            ]
            version = dataset["administrativeInformation"][
                "publicationAndOwnership"
            ]["common:dataSetVersion"]
            exchanges = dataset["exchanges"]["exchange"]
            process_source = dataset["modellingAndValidation"][
                "dataSourcesTreatmentAndRepresentativeness"
            ]["referenceToDataSource"]
        except (KeyError, TypeError) as error:
            raise ValueError(f"scenario row {row_index} is malformed") from error
        process_id = _canonical_uuid(process_id, f"scenario row {row_index}.UUID")
        if process_id not in scenario_by_id:
            raise ValueError("scenario row is not bound to accepted scenario state")
        if version != scenario_by_id[process_id].get("dataset_version"):
            raise ValueError("scenario row version drifted")
        if not isinstance(exchanges, list) or not exchanges:
            raise ValueError("scenario row exchanges are missing")
        if not isinstance(process_source, dict):
            raise ValueError("scenario process source reference is missing")

        operations: List[JsonObject] = [
            _test(
                _reference_path(
                    "processDataSet",
                    "processInformation",
                    "dataSetInformation",
                    "common:UUID",
                ),
                process_id,
            ),
            _test(
                _reference_path(
                    "processDataSet",
                    "administrativeInformation",
                    "publicationAndOwnership",
                    "common:dataSetVersion",
                ),
                version,
            ),
        ]
        for exchange_index, exchange in enumerate(exchanges):
            if not isinstance(exchange, dict):
                raise ValueError("scenario exchange must be an object")
            flow_reference = exchange.get("referenceToFlowDataSet")
            if not isinstance(flow_reference, dict):
                raise ValueError("scenario exchange flow reference is missing")
            flow_id = _canonical_uuid(
                flow_reference.get("@refObjectId"),
                f"scenario row {row_index} exchange {exchange_index}.flow UUID",
            )
            canonical_flow = flow_by_id.get(flow_id)
            if canonical_flow is None:
                raise ValueError(f"scenario references unknown canonical flow {flow_id}")
            if _single_localized_text(
                flow_reference.get("common:shortDescription"),
                "flow shortDescription",
            ) != canonical_flow["name"]:
                raise ValueError(f"scenario flow name drifted for {flow_id}")
            current_flow_version = _required_text(
                flow_reference.get("@version"), f"scenario flow {flow_id}.version"
            )
            current_flow_uri = _required_text(
                flow_reference.get("@uri"), f"scenario flow {flow_id}.URI"
            )
            flow_path = _reference_path(
                "processDataSet",
                "exchanges",
                "exchange",
                exchange_index,
                "referenceToFlowDataSet",
            )
            operations.extend(
                [
                    _test(f"{flow_path}/@refObjectId", flow_id),
                    _test(f"{flow_path}/@version", current_flow_version),
                    _test(f"{flow_path}/@uri", current_flow_uri),
                ]
            )
            for key, target in (
                ("@version", str(canonical_flow["version"])),
                ("@uri", str(canonical_flow["uri"])),
            ):
                if flow_reference.get(key) != target:
                    operations.append(
                        _replace_operation(
                            path=f"{flow_path}/{key}",
                            value=target,
                            kind="flow",
                            canonical=canonical_flow,
                            entity_evidence=entity_evidence,
                        )
                    )
            reference_counts["flow"] += 1
            referenced_flow_ids.add(flow_id)

            source_container = exchange.get("referencesToDataSource")
            if not isinstance(source_container, dict):
                raise ValueError("scenario exchange source reference is missing")
            source_reference = source_container.get("referenceToDataSource")
            if not isinstance(source_reference, dict):
                raise ValueError("scenario exchange source reference is malformed")
            canonical_source = _source_reference_target(
                source_reference,
                source_by_id,
                source_by_title,
                source_by_digital_uri,
            )
            current_source_id = _canonical_uuid(
                source_reference.get("@refObjectId"),
                "scenario exchange source UUID",
            )
            current_source_version = _required_text(
                source_reference.get("@version"),
                "scenario exchange source version",
            )
            current_source_uri = _required_text(
                source_reference.get("@uri"), "scenario exchange source URI"
            )
            source_path = _reference_path(
                "processDataSet",
                "exchanges",
                "exchange",
                exchange_index,
                "referencesToDataSource",
                "referenceToDataSource",
            )
            operations.extend(
                [
                    _test(
                        f"{source_path}/@refObjectId",
                        current_source_id,
                    ),
                    _test(f"{source_path}/@version", current_source_version),
                    _test(f"{source_path}/@uri", current_source_uri),
                ]
            )
            for key, target in (
                ("@refObjectId", str(canonical_source["id"])),
                ("@version", str(canonical_source["version"])),
                ("@uri", str(canonical_source["uri"])),
            ):
                if source_reference.get(key) != target:
                    operations.append(
                        _replace_operation(
                            path=f"{source_path}/{key}",
                            value=target,
                            kind="source",
                            canonical=canonical_source,
                            entity_evidence=entity_evidence,
                        )
                    )
            reference_counts["source"] += 1
            referenced_source_ids.add(str(canonical_source["id"]))

        canonical_process_source = _source_reference_target(
            process_source,
            source_by_id,
            source_by_title,
            source_by_digital_uri,
        )
        current_process_source_id = _canonical_uuid(
            process_source.get("@refObjectId"), "scenario process source UUID"
        )
        current_process_source_version = _required_text(
            process_source.get("@version"), "scenario process source version"
        )
        current_process_source_uri = _required_text(
            process_source.get("@uri"), "scenario process source URI"
        )
        process_source_path = _reference_path(
            "processDataSet",
            "modellingAndValidation",
            "dataSourcesTreatmentAndRepresentativeness",
            "referenceToDataSource",
        )
        operations.extend(
            [
                _test(
                    f"{process_source_path}/@refObjectId",
                    current_process_source_id,
                ),
                _test(
                    f"{process_source_path}/@version",
                    current_process_source_version,
                ),
                _test(f"{process_source_path}/@uri", current_process_source_uri),
            ]
        )
        for key, target in (
            ("@refObjectId", str(canonical_process_source["id"])),
            ("@version", str(canonical_process_source["version"])),
            ("@uri", str(canonical_process_source["uri"])),
        ):
            if process_source.get(key) != target:
                operations.append(
                    _replace_operation(
                        path=f"{process_source_path}/{key}",
                        value=target,
                        kind="source",
                        canonical=canonical_process_source,
                        entity_evidence=entity_evidence,
                    )
                )
        reference_counts["process_source"] += 1
        referenced_source_ids.add(str(canonical_process_source["id"]))
        patch_sets.append(
            {
                "row_index": row_index,
                "dataset_id": process_id,
                "dataset_version": version,
                "operations": operations,
            }
        )

    if referenced_flow_ids != set(flow_by_id):
        raise ValueError("scenario rows do not reference all canonical flows")
    if not referenced_source_ids.issubset(set(source_by_id)):
        raise ValueError("scenario rows contain unresolved canonical sources")
    return {
        "schema_version": 1,
        "contract": REFERENCE_INTEGRATION_CONTRACT,
        "patch_status": "completed",
        "scenario_kind": "isolated_uncoated_route_core_screening_proxy",
        "patch_sets": patch_sets,
        "scenario_processes": copy.deepcopy(scenario_processes),
        "reference_counts": reference_counts,
        "canonical_entity_counts": {
            "flow": len(flow_by_id),
            "source": len(source_by_id),
        },
        "referenced_source_count": len(referenced_source_ids),
        "protected_scenario_sha256_before": protected_scenario_sha256(
            scenario_rows
        ),
        "scientific_values_changed": False,
        "remote_save_authorized": False,
        "scientific_comparison_ready": False,
    }
