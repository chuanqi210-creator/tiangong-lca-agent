"""Compile source-bound screening inventories into isolated TIDAS patch sets."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List


JsonObject = Dict[str, Any]

SCENARIO_PATCH_CONTRACT = "lca_research_kernel.screening_scenario_patch/v1"
_EXCHANGE_COMMENT_CONTRACT = "screening_scenario_patch/v1"
_UUID_NAMESPACE = uuid.UUID("086f034c-4ac8-5f54-8f67-3be23802337e")
_IDENTITY_STATUS = (
    "provisional_local_uuidv5_pending_native_identity_preflight"
)
_PROCESS_UUID_PATH = (
    "/processDataSet/processInformation/dataSetInformation/common:UUID"
)
_PROCESS_VERSION_PATH = (
    "/processDataSet/administrativeInformation/publicationAndOwnership/"
    "common:dataSetVersion"
)
_PROCESS_URI_PATH = (
    "/processDataSet/administrativeInformation/publicationAndOwnership/"
    "common:permanentDataSetURI"
)
_BASE_NAME_PATH = (
    "/processDataSet/processInformation/dataSetInformation/name/baseName/0/#text"
)
_FUNCTIONAL_UNIT_PATH = (
    "/processDataSet/processInformation/dataSetInformation/name/"
    "functionalUnitFlowProperties/0/#text"
)
_TECHNOLOGY_PATH = (
    "/processDataSet/processInformation/technology/"
    "technologyDescriptionAndIncludedProcesses/0/#text"
)
_FUNCTIONAL_UNIT_REFERENCE_PATH = (
    "/processDataSet/processInformation/quantitativeReference/functionalUnitOrOther"
)
_GENERAL_COMMENT_APPEND_PATH = (
    "/processDataSet/processInformation/dataSetInformation/"
    "common:generalComment/-"
)
_COMPLETENESS_PATH = (
    "/processDataSet/modellingAndValidation/"
    "dataSourcesTreatmentAndRepresentativeness/"
    "dataCutOffAndCompletenessPrinciples/0/#text"
)
_REFERENCE_EXCHANGE_PATH = "/processDataSet/exchanges/exchange/0"
_EXCHANGE_APPEND_PATH = "/processDataSet/exchanges/exchange/-"


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _canonical_text(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _uuid5(name: str) -> str:
    return str(uuid.uuid5(_UUID_NAMESPACE, name))


def _required_text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")
    return value.strip()


def _required_hash(value: object, path: str) -> str:
    token = _required_text(value, path)
    if len(token) != 64 or any(char not in "0123456789abcdef" for char in token):
        raise ValueError(f"{path} must be a lowercase sha256")
    return token


def _amount_text(value: object, path: str) -> str:
    if isinstance(value, bool):
        raise ValueError(f"{path} must be a positive finite number")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{path} must be a positive finite number") from error
    if not amount.is_finite() or amount <= 0:
        raise ValueError(f"{path} must be a positive finite number")
    rendered = format(amount.normalize(), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _process_id(process: JsonObject) -> str:
    try:
        value = process["processDataSet"]["processInformation"][
            "dataSetInformation"
        ]["common:UUID"]
    except (KeyError, TypeError) as error:
        raise ValueError("Every base process must contain a process UUID") from error
    return _required_text(value, "base_process.common:UUID")


def _process_version(process: JsonObject) -> str:
    try:
        value = process["processDataSet"]["administrativeInformation"][
            "publicationAndOwnership"
        ]["common:dataSetVersion"]
    except (KeyError, TypeError) as error:
        raise ValueError("Every base process must contain a dataset version") from error
    return _required_text(value, "base_process.common:dataSetVersion")


def _source_registry(inventory_plan: JsonObject) -> List[JsonObject]:
    evidence_search = inventory_plan.get("evidence_search")
    required_sources = (
        evidence_search.get("required_sources")
        if isinstance(evidence_search, dict)
        else None
    )
    if not isinstance(required_sources, list) or not required_sources:
        raise ValueError("Inventory plan must contain at least one required source")
    registry: List[JsonObject] = []
    seen: set[str] = set()
    for index, source in enumerate(required_sources):
        if not isinstance(source, dict):
            raise ValueError(f"required_sources[{index}] must be an object")
        source_id = _required_text(
            source.get("source_id"), f"required_sources[{index}].source_id"
        )
        if source_id in seen:
            raise ValueError(f"Duplicate required source_id: {source_id}")
        seen.add(source_id)
        title = _required_text(
            source.get("title"), f"required_sources[{index}].title"
        )
        url = _required_text(source.get("url"), f"required_sources[{index}].url")
        registry.append(
            {
                "source_id": source_id,
                "tidas_source_id": _uuid5(f"source:{source_id}:{url}"),
                "title": title,
                "url": url,
                "identity_status": _IDENTITY_STATUS,
            }
        )
    return registry


def _flow_identity(flow_key: str, unit: str) -> str:
    return _uuid5(f"screening-scenario-flow:{flow_key}:{unit}")


def _register_flow(
    registry: Dict[str, JsonObject],
    *,
    flow_key: str,
    name: str,
    unit: str,
    role: str,
    source_ids: Iterable[str],
) -> JsonObject:
    candidate = {
        "flow_key": flow_key,
        "flow_id": _flow_identity(flow_key, unit),
        "name": name,
        "unit": unit,
        "role": role,
        "source_ids": sorted(set(source_ids)),
        "identity_status": _IDENTITY_STATUS,
    }
    existing = registry.get(flow_key)
    if existing is not None and any(
        existing[key] != candidate[key] for key in ("flow_id", "name", "unit", "role")
    ):
        raise ValueError(f"Conflicting flow identity for flow_key: {flow_key}")
    if existing is None:
        registry[flow_key] = candidate
    else:
        existing["source_ids"] = sorted(
            set(existing["source_ids"]) | set(candidate["source_ids"])
        )
    return registry[flow_key]


def _flow_reference(flow: JsonObject) -> JsonObject:
    return {
        "@type": "flow data set",
        "@refObjectId": flow["flow_id"],
        "@version": "00.00.000",
        "@uri": f"../flow-data-set/{flow['flow_id']}.xml",
        "common:shortDescription": {
            "@xml:lang": "en",
            "#text": flow["name"],
        },
    }


def _source_reference(source: JsonObject) -> JsonObject:
    return {
        "@type": "source data set",
        "@refObjectId": source["tidas_source_id"],
        "@version": "00.00.001",
        "@uri": source["url"],
        "common:shortDescription": {
            "@xml:lang": "en",
            "#text": source["title"],
        },
    }


def _operation_context(
    inventory_plan: JsonObject,
    *,
    route_id: str,
    source_ids: Iterable[str],
    evidence_status: str,
    derivation: str,
    allocation_basis: str,
    uncertainty_status: str,
) -> tuple[JsonObject, JsonObject]:
    evidence = {
        "scope_id": inventory_plan["scope_id"],
        "revision": inventory_plan["revision"],
        "route_id": route_id,
        "compiled_inventory_plan": {
            "path": inventory_plan["plan_path"],
            "sha256": inventory_plan["plan_sha256"],
        },
        "native_evidence_search_report": {
            "path": inventory_plan["native_evidence_search"]["report_path"],
            "sha256": inventory_plan["native_evidence_search"]["report_sha256"],
        },
        "source_ids": sorted(set(source_ids)),
        "evidence_status": evidence_status,
        "derivation": derivation,
        "allocation_basis": allocation_basis,
        "uncertainty_status": uncertainty_status,
    }
    resolution = {
        "mode": "source_bound_screening_proxy",
        "used_context_kinds": [
            "tidas_process_contract",
            "compiled_inventory_plan",
            "native_evidence_search",
        ],
    }
    return evidence, resolution


def _replace_operation(
    path: str,
    value: object,
    evidence: JsonObject,
    resolution: JsonObject,
    *,
    basis: str,
) -> JsonObject:
    return {
        "op": "replace",
        "path": path,
        "value": value,
        "basis": basis,
        "evidence": evidence,
        "resolution": resolution,
    }


def _add_operation(
    path: str,
    value: object,
    evidence: JsonObject,
    resolution: JsonObject,
    *,
    basis: str,
) -> JsonObject:
    return {
        "op": "add",
        "path": path,
        "value": value,
        "basis": basis,
        "evidence": evidence,
        "resolution": resolution,
    }


def _candidate_exchange(
    candidate: JsonObject,
    *,
    internal_id: int,
    flow: JsonObject,
    source_by_id: Dict[str, JsonObject],
) -> JsonObject:
    source_ids = candidate["source_ids"]
    primary_source = source_by_id[source_ids[0]]
    amount = _amount_text(candidate.get("amount"), "exchange_candidate.amount")
    qa_tags_by_classification = {
        ("technosphere", "Input", "kg"): (
            "[tg_io_kind_tag=raw_material][tg_io_uom_tag=kg]"
        ),
        ("energy", "Input", "MJ"): "[tg_io_kind_tag=energy][tg_io_uom_tag=MJ]",
        ("co_product", "Output", "kg"): (
            "[tg_io_kind_tag=product][tg_io_uom_tag=kg] co-product"
        ),
    }
    classification = (
        candidate["exchange_type"],
        candidate["direction"],
        candidate["unit"],
    )
    try:
        qa_tags = qa_tags_by_classification[classification]
    except KeyError as error:
        raise ValueError(
            "exchange_candidate has no supported native QA classification"
        ) from error
    comment = {
        "contract": _EXCHANGE_COMMENT_CONTRACT,
        "flow_key": candidate["flow_key"],
        "unit": candidate["unit"],
        "exchange_type": candidate["exchange_type"],
        "allocation_basis": candidate["allocation_basis"],
        "uncertainty_status": candidate["uncertainty"]["status"],
        "scientific_use": "screening_proxy_only_no_comparative_lcia",
        "qa": qa_tags,
    }
    return {
        "@dataSetInternalID": str(internal_id),
        "referenceToFlowDataSet": _flow_reference(flow),
        "exchangeDirection": candidate["direction"],
        "meanAmount": amount,
        "resultingAmount": amount,
        "dataSourceType": "Secondary",
        "dataDerivationTypeStatus": "Calculated",
        "referencesToDataSource": {
            "referenceToDataSource": _source_reference(primary_source)
        },
        "generalComment": [
            {"@xml:lang": "en", "#text": _canonical_text(comment)}
        ],
    }


def _reference_exchange(
    *,
    flow: JsonObject,
    source: JsonObject,
) -> JsonObject:
    return {
        "@dataSetInternalID": "1",
        "referenceToFlowDataSet": _flow_reference(flow),
        "exchangeDirection": "Output",
        "meanAmount": "1",
        "resultingAmount": "1",
        "dataSourceType": "Secondary",
        "dataDerivationTypeStatus": "Calculated",
        "quantitativeReference": True,
        "referencesToDataSource": {
            "referenceToDataSource": _source_reference(source)
        },
        "generalComment": [
            {
                "@xml:lang": "en",
                "#text": _canonical_text(
                    {
                        "contract": _EXCHANGE_COMMENT_CONTRACT,
                        "flow_key": flow["flow_key"],
                        "unit": flow["unit"],
                        "scientific_use": (
                            "uncoated_route_core_screening_proxy_only_no_comparative_lcia"
                        ),
                    }
                ),
            }
        ],
    }


def _set_expected_value(process: JsonObject, path: str, value: object) -> None:
    tokens = path.lstrip("/").split("/")
    current: object = process
    for token in tokens[:-1]:
        if isinstance(current, list):
            current = current[int(token)]
        else:
            current = current[token]  # type: ignore[index]
    final = tokens[-1]
    if final == "-":
        if not isinstance(current, list):
            raise ValueError(f"Append path does not target a list: {path}")
        current.append(copy.deepcopy(value))
    elif isinstance(current, list):
        current[int(final)] = copy.deepcopy(value)
    else:
        current[final] = copy.deepcopy(value)  # type: ignore[index]


def _validate_candidate(
    candidate: object,
    *,
    route_id: str,
    index: int,
    source_by_id: Dict[str, JsonObject],
) -> JsonObject:
    if not isinstance(candidate, dict):
        raise ValueError(f"{route_id}.exchange_candidates[{index}] must be an object")
    required = (
        "flow_key",
        "name",
        "unit",
        "direction",
        "exchange_type",
        "evidence_status",
        "derivation",
        "allocation_basis",
    )
    for key in required:
        _required_text(candidate.get(key), f"{route_id}.exchange_candidates[{index}].{key}")
    if candidate["direction"] not in {"Input", "Output"}:
        raise ValueError(f"{route_id}.exchange_candidates[{index}].direction is invalid")
    supported_qa_classifications = {
        ("technosphere", "Input", "kg"),
        ("energy", "Input", "MJ"),
        ("co_product", "Output", "kg"),
    }
    classification = (
        candidate["exchange_type"],
        candidate["direction"],
        candidate["unit"],
    )
    if classification not in supported_qa_classifications:
        raise ValueError(
            f"{route_id}.exchange_candidates[{index}] has no supported native QA "
            "classification for exchange_type/direction/unit"
        )
    _amount_text(candidate.get("amount"), f"{route_id}.exchange_candidates[{index}].amount")
    source_ids = candidate.get("source_ids")
    if (
        not isinstance(source_ids, list)
        or not source_ids
        or not all(isinstance(source_id, str) and source_id for source_id in source_ids)
    ):
        raise ValueError(f"{route_id}.exchange_candidates[{index}].source_ids is invalid")
    unknown_sources = set(source_ids) - set(source_by_id)
    if unknown_sources:
        raise ValueError(
            f"{route_id}.exchange_candidates[{index}] references unknown required source"
        )
    uncertainty = candidate.get("uncertainty")
    if (
        not isinstance(uncertainty, dict)
        or uncertainty.get("status") != "not_characterized_source_gap"
        or uncertainty.get("distribution") is not None
    ):
        raise ValueError(
            f"{route_id}.exchange_candidates[{index}].uncertainty must retain the source gap"
        )
    return candidate


def compile_screening_scenario_patch(
    base_processes: List[JsonObject],
    process_refs: List[JsonObject],
    inventory_plan: JsonObject,
) -> JsonObject:
    """Build deterministic native patch sets without mutating performance rows."""

    if len(base_processes) != 2 or not all(
        isinstance(process, dict) for process in base_processes
    ):
        raise ValueError("Exactly two base process rows are required")
    if len(process_refs) != 2 or not all(
        isinstance(ref, dict) for ref in process_refs
    ):
        raise ValueError("Exactly two distinct routes are required")
    route_ids = [_required_text(ref.get("route_id"), "process_ref.route_id") for ref in process_refs]
    if len(set(route_ids)) != 2:
        raise ValueError("Exactly two distinct routes are required")
    if inventory_plan.get("status") != "screening_proxy_plan_ready":
        raise ValueError("Inventory plan is not ready for screening scenario patching")
    if (
        not isinstance(inventory_plan.get("native_evidence_search"), dict)
        or inventory_plan["native_evidence_search"].get("status")
        != "completed_with_evidence"
    ):
        raise ValueError("A completed native evidence search is required")
    scope_id = _required_text(inventory_plan.get("scope_id"), "scope_id")
    revision = _required_text(inventory_plan.get("revision"), "revision")
    plan_sha256 = _required_hash(inventory_plan.get("plan_sha256"), "plan_sha256")
    _required_text(inventory_plan.get("plan_path"), "plan_path")
    _required_hash(
        inventory_plan["native_evidence_search"].get("report_sha256"),
        "native_evidence_search.report_sha256",
    )
    _required_text(
        inventory_plan["native_evidence_search"].get("report_path"),
        "native_evidence_search.report_path",
    )

    sources = _source_registry(inventory_plan)
    source_by_id = {row["source_id"]: row for row in sources}
    routes = inventory_plan.get("routes")
    if not isinstance(routes, list) or len(routes) != 2:
        raise ValueError("Inventory plan must contain exactly two routes")
    route_by_id: Dict[str, JsonObject] = {}
    for route in routes:
        if not isinstance(route, dict):
            raise ValueError("Inventory routes must be objects")
        route_id = _required_text(route.get("route_id"), "inventory_route.route_id")
        if route_id in route_by_id:
            raise ValueError(f"Duplicate inventory route_id: {route_id}")
        route_by_id[route_id] = route
    if set(route_ids) != set(route_by_id):
        raise ValueError("Process refs and inventory routes must have identical route_ids")

    flow_registry: Dict[str, JsonObject] = {}
    product_flow = _register_flow(
        flow_registry,
        flow_key="uncoated-lifepo4-route-core",
        name="Uncoated LiFePO4 route core, screening proxy",
        unit="kg",
        role="reference_product",
        source_ids=source_by_id,
    )
    expected_rows: List[JsonObject] = []
    patch_sets: List[JsonObject] = []
    scenario_processes: List[JsonObject] = []

    for row_index, (base_process, process_ref) in enumerate(
        zip(base_processes, process_refs)
    ):
        route_id = route_ids[row_index]
        original_process_id = _process_id(base_process)
        registered_process_id = _required_text(
            process_ref.get("process_id"), f"process_refs[{row_index}].process_id"
        )
        if original_process_id != registered_process_id:
            raise ValueError(
                f"process_id for route '{route_id}' does not match the base row"
            )
        version = _process_version(base_process)
        route = route_by_id[route_id]
        candidates = route.get("exchange_candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError(f"Route '{route_id}' has no exchange candidates")
        validated_candidates = [
            _validate_candidate(
                candidate,
                route_id=route_id,
                index=index,
                source_by_id=source_by_id,
            )
            for index, candidate in enumerate(candidates)
        ]
        route_sources = sorted(
            {
                source_id
                for candidate in validated_candidates
                for source_id in candidate["source_ids"]
            }
        )
        scenario_id = _uuid5(
            f"screening-scenario-process:{scope_id}:{revision}:{route_id}:{plan_sha256}"
        )
        base_name = (
            "Lithium iron phosphate production, "
            f"{route_id}, uncoated route-core screening proxy"
        )
        functional_unit = (
            "1 kg uncoated LiFePO4 route core (screening proxy; not performance-aligned)"
        )
        technology = (
            f"Isolated {route_id} screening scenario compiled from source-bound route-core "
            "evidence. It does not represent the final carbon-coated performance-aligned "
            "product and must not be used for comparative LCIA."
        )
        completeness = (
            "Screening inventory only. Flow identities are provisional pending native "
            "identity preflight; route alignment, emissions, uncertainty, and carbon-coating "
            "gaps remain open. Comparative LCIA is prohibited."
        )
        scenario_comment = {
            "@xml:lang": "en",
            "#text": _canonical_text(
                {
                    "contract": SCENARIO_PATCH_CONTRACT,
                    "scenario_kind": "isolated_uncoated_route_core_screening_proxy",
                    "base_process_id": original_process_id,
                    "compiled_inventory_plan_sha256": plan_sha256,
                    "flow_identity_status": _IDENTITY_STATUS,
                    "scientific_comparison_ready": False,
                }
            ),
        }
        metadata_evidence, metadata_resolution = _operation_context(
            inventory_plan,
            route_id=route_id,
            source_ids=route_sources,
            evidence_status="source_bound_scenario_isolation",
            derivation="deterministic_clone_and_relabel",
            allocation_basis=_required_text(
                route.get("inventory_model_basis"),
                f"routes[{route_id}].inventory_model_basis",
            ),
            uncertainty_status="not_characterized_source_gap",
        )
        reference_exchange = _reference_exchange(
            flow=product_flow,
            source=source_by_id[route_sources[0]],
        )
        operation_values = [
            (_PROCESS_UUID_PATH, scenario_id, "replace"),
            (_PROCESS_URI_PATH, f"urn:uuid:{scenario_id}", "replace"),
            (_BASE_NAME_PATH, base_name, "replace"),
            (_FUNCTIONAL_UNIT_PATH, functional_unit, "replace"),
            (
                _FUNCTIONAL_UNIT_REFERENCE_PATH,
                [{"@xml:lang": "en", "#text": functional_unit}],
                "add",
            ),
            (_TECHNOLOGY_PATH, technology, "replace"),
            (_GENERAL_COMMENT_APPEND_PATH, scenario_comment, "add"),
            (_COMPLETENESS_PATH, completeness, "replace"),
            (_REFERENCE_EXCHANGE_PATH, reference_exchange, "replace"),
        ]
        operations: List[JsonObject] = [
            {
                "op": "test",
                "path": _PROCESS_UUID_PATH,
                "value": original_process_id,
            },
            {
                "op": "test",
                "path": _PROCESS_VERSION_PATH,
                "value": version,
            },
        ]
        expected = copy.deepcopy(base_process)
        for path, value, operation in operation_values:
            if operation == "replace":
                operations.append(
                    _replace_operation(
                        path,
                        value,
                        metadata_evidence,
                        metadata_resolution,
                        basis="isolate screening proxy from performance-aligned base process",
                    )
                )
            else:
                operations.append(
                    _add_operation(
                        path,
                        value,
                        metadata_evidence,
                        metadata_resolution,
                        basis="retain machine-readable screening scenario boundary",
                    )
                )
            _set_expected_value(expected, path, value)

        for internal_id, candidate in enumerate(validated_candidates, start=2):
            flow = _register_flow(
                flow_registry,
                flow_key=candidate["flow_key"],
                name=candidate["name"],
                unit=candidate["unit"],
                role=candidate["exchange_type"],
                source_ids=candidate["source_ids"],
            )
            exchange = _candidate_exchange(
                candidate,
                internal_id=internal_id,
                flow=flow,
                source_by_id=source_by_id,
            )
            evidence, resolution = _operation_context(
                inventory_plan,
                route_id=route_id,
                source_ids=candidate["source_ids"],
                evidence_status=candidate["evidence_status"],
                derivation=candidate["derivation"],
                allocation_basis=candidate["allocation_basis"],
                uncertainty_status=candidate["uncertainty"]["status"],
            )
            operations.append(
                _add_operation(
                    _EXCHANGE_APPEND_PATH,
                    exchange,
                    evidence,
                    resolution,
                    basis=(
                        f"source-bound {route_id} {candidate['flow_key']} screening exchange"
                    ),
                )
            )
            _set_expected_value(expected, _EXCHANGE_APPEND_PATH, exchange)

        expected_raw_input = Decimal("0")
        expected_product_output = Decimal("1")
        expected_energy_excluded = Decimal("0")
        for candidate in validated_candidates:
            amount = Decimal(str(candidate["amount"]))
            if candidate["direction"] == "Input":
                if candidate["exchange_type"] == "energy":
                    expected_energy_excluded += amount
                else:
                    expected_raw_input += amount
            else:
                expected_product_output += amount

        expected_rows.append(expected)
        patch_sets.append(
            {
                "row_index": row_index,
                "dataset_id": original_process_id,
                "dataset_version": version,
                "operations": operations,
            }
        )
        scenario_processes.append(
            {
                "route_id": route_id,
                "base_process_id": original_process_id,
                "scenario_process_id": scenario_id,
                "dataset_version": version,
                "exchange_count": len(validated_candidates) + 1,
                "positive_input_exchange_count": sum(
                    candidate["direction"] == "Input"
                    for candidate in validated_candidates
                ),
                "positive_output_exchange_count": 1
                + sum(
                    candidate["direction"] == "Output"
                    for candidate in validated_candidates
                ),
                "expected_qa_totals": {
                    "raw_input": float(expected_raw_input),
                    "product_output": float(expected_product_output),
                    "energy_excluded": float(expected_energy_excluded),
                },
                "evidence_alignment": route.get("evidence_alignment"),
                "inventory_model_basis": route.get("inventory_model_basis"),
            }
        )

    expected_rows_sha256 = hashlib.sha256(_json_bytes(expected_rows)).hexdigest()
    return {
        "schema_version": 1,
        "contract": SCENARIO_PATCH_CONTRACT,
        "scope_id": scope_id,
        "revision": revision,
        "scenario_kind": "isolated_uncoated_route_core_screening_proxy",
        "patch_status": "completed",
        "scientific_comparison_ready": False,
        "flow_identity_gate": "pending_native_identity_preflight",
        "source_identity_gate": "pending_native_identity_preflight",
        "compiled_inventory_plan": {
            "path": inventory_plan["plan_path"],
            "sha256": plan_sha256,
        },
        "native_evidence_search_report": {
            "path": inventory_plan["native_evidence_search"]["report_path"],
            "sha256": inventory_plan["native_evidence_search"]["report_sha256"],
        },
        "source_registry": sources,
        "flow_registry": sorted(flow_registry.values(), key=lambda row: row["flow_key"]),
        "scenario_processes": scenario_processes,
        "patch_sets": patch_sets,
        "expected_scenario_rows": expected_rows,
        "expected_scenario_rows_sha256": expected_rows_sha256,
        "next_action": (
            "Run native identity preflight for every provisional flow/source, then replace "
            "local references without changing source-bound amounts."
        ),
    }


def screening_scenario_rows_are_valid(
    rows: List[JsonObject], compiled_patch: JsonObject
) -> bool:
    """Require native patch output to equal the isolated compiled scenario rows."""

    expected = compiled_patch.get("expected_scenario_rows")
    expected_sha256 = compiled_patch.get("expected_scenario_rows_sha256")
    if (
        compiled_patch.get("contract") != SCENARIO_PATCH_CONTRACT
        or compiled_patch.get("patch_status") != "completed"
        or compiled_patch.get("scientific_comparison_ready") is not False
        or not isinstance(expected, list)
        or len(expected) != 2
        or not isinstance(rows, list)
        or len(rows) != 2
        or not isinstance(expected_sha256, str)
    ):
        return False
    if hashlib.sha256(_json_bytes(expected)).hexdigest() != expected_sha256:
        return False
    if rows != expected:
        return False
    scenario_processes = compiled_patch.get("scenario_processes")
    if not isinstance(scenario_processes, list) or len(scenario_processes) != 2:
        return False
    expected_ids = {
        row.get("scenario_process_id")
        for row in scenario_processes
        if isinstance(row, dict)
    }
    actual_ids = {_process_id(row) for row in rows}
    if len(expected_ids) != 2 or actual_ids != expected_ids:
        return False
    for row in rows:
        try:
            data_set_information = row["processDataSet"]["processInformation"][
                "dataSetInformation"
            ]
            functional_unit = data_set_information["name"][
                "functionalUnitFlowProperties"
            ][0]["#text"]
            exchanges = row["processDataSet"]["exchanges"]["exchange"]
        except (KeyError, IndexError, TypeError):
            return False
        if (
            functional_unit
            != "1 kg uncoated LiFePO4 route core (screening proxy; not performance-aligned)"
            or not isinstance(exchanges, list)
            or len(exchanges) < 2
            or exchanges[0].get("quantitativeReference") is not True
        ):
            return False
        for exchange in exchanges[1:]:
            if not isinstance(exchange, dict):
                return False
            try:
                amount = float(exchange["meanAmount"])
                resulting = float(exchange["resultingAmount"])
                comment = json.loads(exchange["generalComment"][0]["#text"])
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
                return False
            if (
                not math.isfinite(amount)
                or amount <= 0
                or amount != resulting
                or comment.get("contract") != _EXCHANGE_COMMENT_CONTRACT
                or comment.get("scientific_use")
                != "screening_proxy_only_no_comparative_lcia"
                or "uncertaintyDistributionType" in exchange
            ):
                return False
    return True
