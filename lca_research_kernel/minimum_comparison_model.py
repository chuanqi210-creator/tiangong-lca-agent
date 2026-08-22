"""Compile exact graph members into a deterministic local comparison-model package."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
import uuid
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.minimum_lifepo4_comparison_model/v1"
STATUS = "minimum_lifepo4_comparison_model_compiled"
GRAPH_CONTRACT = "lca_research_kernel.minimum_lifepo4_comparison_graph/v1"
NEXT_ROUTE = "materialize-minimum-lifepo4-comparison-model-native-gates"
LCIA_NEXT_ROUTE = "execute-lifepo4-comparative-lcia-sensitivity-uncertainty"
DATASET_VERSION = "00.00.001"
EXPECTED_NODE_COUNTS = {
    "Flow": 13,
    "Source": 4,
    "foreground Process": 3,
    "Provider": 10,
    "LCIAMethod": 1,
    "LifecycleModel": 2,
}
ENTITY_COUNTS = {
    "reused_flow": 11,
    "new_flow": 2,
    "reused_source": 4,
    "reused_route_process": 2,
    "new_coating_process": 1,
    "provider_process": 10,
    "lcia_method": 1,
    "lifecyclemodel": 2,
}
ALLOCATION_FACTOR = 0.5893171688905723
ROUTE_MULTIPLIER = 0.94
COATING_SUCROSE_KG = 0.285
COATING_ELECTRICITY_MJ = 20.0
NATIVE_INPUTS_CONTRACT = f"{CONTRACT}_native_inputs"


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _hash(value: object) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return dict(value)


def _list(value: object, label: str) -> List[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    return list(value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _localized(text: str) -> JsonObject:
    return {"#text": text, "@xml:lang": "en"}


def _battery_material_classification() -> JsonObject:
    """Reuse the exact current native classification shape and UUID path."""

    return {
        "common:classification": {
            "common:class": [
                {
                    "#text": "Technosphere",
                    "@classId": "afc067df-0422-5f82-81b6-69d0809b9aba",
                    "@level": "0",
                },
                {
                    "#text": "Materials production",
                    "@classId": "6fe6936f-5840-5c7e-a408-7536c1cf2790",
                    "@level": "1",
                },
                {
                    "#text": "Battery materials",
                    "@classId": "b94e2ca8-789e-5a1d-9bbd-95bbb1e85192",
                    "@level": "2",
                },
                {
                    "#text": "Lithium iron phosphate active material",
                    "@classId": "9425674e-4278-58f5-b358-ec79fa0a723d",
                    "@level": "3",
                },
            ]
        }
    }


def _flow_row(spec: Mapping[str, object], graph_input_version: str) -> JsonObject:
    """Build one deterministic mass-product Flow on the accepted native contract."""

    row = _object(spec, "new Flow spec")
    flow_id = _text(row.get("flow_id"), "new Flow UUID")
    name = _text(row.get("name"), "new Flow name")
    if (
        row.get("dataset_version") != DATASET_VERSION
        or row.get("flow_type") != "Product flow"
        or row.get("reference_unit") != "kg"
        or row.get("reference_flow_property_id")
        != "93a60a56-a3c8-11da-a746-0800200b9a66"
    ):
        raise ValueError("new Flow mass-product contract drifted")
    format_source = _stable_uuid("support", "tidas-format", "source")
    owner = _stable_uuid("support", "local-controller", "contact")
    compliance_source = _stable_uuid(
        graph_input_version, "minimum-comparison-model-compliance", "source"
    )
    provenance = {
        "contract": CONTRACT,
        "graph_node_id": row.get("node_id"),
        "evidence_status": row.get("evidence_status"),
        "source_refs": copy.deepcopy(row.get("source_refs")),
        "scientific_use": row.get("scientific_use"),
        "remote_identity_claimed": False,
    }
    return {
        "flowDataSet": {
            "@locations": "../ILCDLocations.xml",
            "@xmlns": "http://lca.jrc.it/ILCD/Flow",
            "@xmlns:common": "http://lca.jrc.it/ILCD/Common",
            "@xmlns:ecn": "http://eplca.jrc.ec.europa.eu/ILCD/Extensions/2018/ECNumber",
            "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "@version": "1.1",
            "@xsi:schemaLocation": (
                "http://lca.jrc.it/ILCD/Flow ../../schemas/ILCD_FlowDataSet.xsd"
            ),
            "flowInformation": {
                "dataSetInformation": {
                    "common:UUID": flow_id,
                    "name": {
                        "baseName": [_localized(name)],
                        "treatmentStandardsRoutes": [
                            _localized("Minimum LiFePO4 comparison graph")
                        ],
                        "mixAndLocationTypes": [_localized("Local screening proxy")],
                    },
                    "classificationInformation": _battery_material_classification(),
                    "common:generalComment": [
                        _localized(json.dumps(provenance, sort_keys=True))
                    ],
                },
                "quantitativeReference": {
                    "referenceToReferenceFlowProperty": "0"
                },
            },
            "modellingAndValidation": {
                "LCIMethod": {"typeOfDataSet": "Product flow"},
                "complianceDeclarations": {
                    "compliance": {
                        "common:referenceToComplianceSystem": _reference(
                            compliance_source,
                            "Minimum comparison model contract",
                            "source data set",
                            version="00.00.000",
                        ),
                        "common:approvalOfOverallCompliance": "Not defined",
                    }
                },
            },
            "administrativeInformation": {
                "dataEntryBy": {
                    "common:timeStamp": "1970-01-01T00:00:00.000Z",
                    "common:referenceToDataSetFormat": _reference(
                        format_source,
                        "TIDAS / ILCD format",
                        "source data set",
                        version="00.00.000",
                    ),
                },
                "publicationAndOwnership": {
                    "common:dataSetVersion": DATASET_VERSION,
                    "common:permanentDataSetURI": f"urn:uuid:{flow_id}",
                    "common:referenceToOwnershipOfDataSet": _reference(
                        owner,
                        "Local research controller",
                        "contact data set",
                        version="00.00.000",
                    ),
                },
            },
            "flowProperties": {
                "flowProperty": {
                    "@dataSetInternalID": "0",
                    "referenceToFlowPropertyDataSet": _reference(
                        "93a60a56-a3c8-11da-a746-0800200b9a66",
                        "Mass",
                        "flow property data set",
                        version="00.00.000",
                    ),
                    "meanValue": "1",
                }
            },
        }
    }


def _stable_uuid(graph_input_version: str, node_id: str, kind: str) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            "|".join((CONTRACT, graph_input_version, node_id, kind, DATASET_VERSION)),
        )
    )


def _decimal_text(value: object) -> str:
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise ValueError("numeric model value must be finite")
    normalized = format(parsed.normalize(), "f")
    return "0" if normalized in {"-0", ""} else normalized


def _reference(
    identifier: str,
    name: str,
    kind: str,
    *,
    version: str = DATASET_VERSION,
) -> JsonObject:
    folder = {
        "flow data set": "flows",
        "process data set": "processes",
        "source data set": "sources",
        "LCIA method data set": "lciamethods",
    }.get(kind, "datasets")
    return {
        "@refObjectId": identifier,
        "@type": kind,
        "@uri": f"../{folder}/{identifier}_{version}.xml",
        "@version": version,
        "common:shortDescription": [_localized(name)],
    }


def _exchange(
    internal_id: int,
    flow_id: str,
    flow_name: str,
    direction: str,
    amount: object,
    *,
    reference: bool = False,
    provenance: Optional[Mapping[str, object]] = None,
) -> JsonObject:
    row: JsonObject = {
        "@dataSetInternalID": str(internal_id),
        "exchangeDirection": direction,
        "meanAmount": _decimal_text(amount),
        "resultingAmount": _decimal_text(amount),
        "dataDerivationTypeStatus": "Estimated",
        "dataSourceType": "Secondary",
        "referenceToFlowDataSet": _reference(
            flow_id, flow_name, "flow data set"
        ),
    }
    if reference:
        row["quantitativeReference"] = True
    if provenance:
        row["generalComment"] = [_localized(json.dumps(dict(provenance), sort_keys=True))]
    return row


def _process_row(
    process_id: str,
    name: str,
    exchanges: Sequence[JsonObject],
    *,
    provenance: Mapping[str, object],
    reference_exchange_id: int = 1,
) -> JsonObject:
    """Build a deterministic TIDAS-shaped local row; native gates remain authoritative."""

    format_source = _stable_uuid("support", "tidas-format", "source")
    owner = _stable_uuid("support", "local-controller", "contact")
    return {
        "processDataSet": {
            "@locations": "../ILCDLocations.xml",
            "@xmlns": "http://lca.jrc.it/ILCD/Process",
            "@xmlns:common": "http://lca.jrc.it/ILCD/Common",
            "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "@version": "1.1",
            "@xsi:schemaLocation": (
                "http://lca.jrc.it/ILCD/Process ../../schemas/ILCD_ProcessDataSet.xsd"
            ),
            "processInformation": {
                "dataSetInformation": {
                    "common:UUID": process_id,
                    "name": {
                        "baseName": [_localized(name)],
                        "functionalUnitFlowProperties": [
                            _localized("1 reference unit of the declared output flow")
                        ],
                        "mixAndLocationTypes": [_localized("Local screening proxy")],
                        "treatmentStandardsRoutes": [
                            _localized("Minimum LiFePO4 comparison graph")
                        ],
                    },
                    "classificationInformation": _battery_material_classification(),
                    "common:generalComment": [
                        _localized(json.dumps(dict(provenance), sort_keys=True))
                    ],
                },
                "quantitativeReference": {
                    "@type": "Reference flow(s)",
                    "referenceToReferenceFlow": str(reference_exchange_id),
                    "functionalUnitOrOther": [
                        _localized("1 reference unit of output")
                    ],
                },
                "time": {
                    "common:referenceYear": 2024,
                    "common:timeRepresentativenessDescription": [
                        _localized("Screening model reference year; not representative proof.")
                    ],
                },
                "geography": {
                    "locationOfOperationSupplyOrProduction": {
                        "@location": "GLO",
                        "descriptionOfRestrictions": [
                            _localized("Local proxy; geography is varied in sensitivity analysis.")
                        ],
                    }
                },
                "technology": {
                    "technologyDescriptionAndIncludedProcesses": [
                        _localized(
                            "Deterministic local proxy generated only for the accepted minimum comparison graph."
                        )
                    ]
                },
            },
            "exchanges": {"exchange": [copy.deepcopy(row) for row in exchanges]},
            "modellingAndValidation": {
                "LCIMethodAndAllocation": {
                    "typeOfDataSet": "Unit process, single operation",
                    "LCIMethodPrinciple": "Attributional",
                },
                "dataSourcesTreatmentAndRepresentativeness": {
                    "referenceToDataSource": _reference(
                        format_source,
                        "Minimum comparison graph and bounded parameter ledger",
                        "source data set",
                        version="00.00.000",
                    ),
                    "annualSupplyOrProductionVolume": [
                        _localized("1 reference unit/year")
                    ],
                    "dataCutOffAndCompletenessPrinciples": [
                        _localized(
                            "Provider impacts are external bounded adapter parameters; absence is not zero."
                        )
                    ]
                },
                "validation": {"review": {"@type": "Not reviewed"}},
                "complianceDeclarations": {
                    "compliance": {
                        "common:referenceToComplianceSystem": _reference(
                            format_source,
                            "Local screening contract",
                            "source data set",
                            version="00.00.000",
                        ),
                        "common:approvalOfOverallCompliance": "Not defined",
                        "common:nomenclatureCompliance": "Not defined",
                        "common:methodologicalCompliance": "Not defined",
                        "common:reviewCompliance": "Not defined",
                        "common:documentationCompliance": "Not defined",
                        "common:qualityCompliance": "Not defined",
                    }
                },
            },
            "administrativeInformation": {
                "common:commissionerAndGoal": {
                    "common:intendedApplications": [
                        _localized("Screening comparison and sensitivity analysis only.")
                    ],
                    "common:referenceToCommissioner": _reference(
                        owner,
                        "Local research controller",
                        "contact data set",
                        version="00.00.000",
                    ),
                },
                "dataEntryBy": {
                    "common:timeStamp": "1970-01-01T00:00:00.000Z",
                    "common:referenceToDataSetFormat": _reference(
                        format_source,
                        "TIDAS / ILCD format",
                        "source data set",
                        version="00.00.000",
                    ),
                    "common:referenceToPersonOrEntityEnteringTheData": _reference(
                        owner,
                        "Local research controller",
                        "contact data set",
                        version="00.00.000",
                    ),
                },
                "publicationAndOwnership": {
                    "common:dataSetVersion": DATASET_VERSION,
                    "common:permanentDataSetURI": f"urn:uuid:{process_id}",
                    "common:copyright": "false",
                    "common:licenseType": "Free of charge for all users and uses",
                    "common:referenceToOwnershipOfDataSet": _reference(
                        owner,
                        "Local research controller",
                        "contact data set",
                        version="00.00.000",
                    ),
                },
            },
        }
    }


PROVIDER_PARAMETERS: Mapping[str, Tuple[float, float, float, str]] = {
    "diammonium-hydrogen-phosphate": (2.5, 0.8, 8.0, "kg CO2-eq/kg"),
    "electricity": (0.12, 0.02, 0.30, "kg CO2-eq/MJ"),
    "iron-sulfate": (1.0, 0.2, 5.0, "kg CO2-eq/kg"),
    "lithium-carbonate": (5.0, 2.0, 20.0, "kg CO2-eq/kg"),
    "lithium-hydroxide": (8.0, 3.0, 30.0, "kg CO2-eq/kg"),
    "magnetite": (1.0, 0.2, 4.0, "kg CO2-eq/kg"),
    "natural-gas-heat-proxy": (0.07, 0.045, 0.12, "kg CO2-eq/MJ"),
    "phosphoric-acid": (2.0, 0.5, 8.0, "kg CO2-eq/kg"),
    "process-water-proxy": (0.001, 0.0001, 0.02, "kg CO2-eq/kg"),
    "sucrose-coating-precursor": (1.0, 0.2, 5.0, "kg CO2-eq/kg"),
}


def _parameter(
    parameter_id: str,
    baseline: float,
    lower: float,
    upper: float,
    unit: str,
    risk_id: str,
    refs: Sequence[str],
    *,
    derivation_class: str = "bounded_engineering_assumption",
) -> JsonObject:
    return {
        "parameter_id": parameter_id,
        "baseline": baseline,
        "lower": lower,
        "upper": upper,
        "unit": unit,
        "distribution_family": "triangular",
        "derivation_class": derivation_class,
        "source_or_assumption_refs": list(refs),
        "can_change_comparative_direction": True,
        "risk_id": risk_id,
        "missing_value_interpreted_as_zero": False,
    }


def _parameter_ledger(provider_nodes: Sequence[JsonObject]) -> List[JsonObject]:
    rows = []
    for node in sorted(provider_nodes, key=lambda row: str(row["node_id"])):
        flow_key = str(node["node_id"]).split(":", 1)[1]
        try:
            baseline, lower, upper, unit = PROVIDER_PARAMETERS[flow_key]
        except KeyError as error:
            raise ValueError(f"Provider parameter is absent: {flow_key}") from error
        rows.append(
            _parameter(
                f"provider-gwp:{flow_key}",
                baseline,
                lower,
                upper,
                unit,
                "raw-material-provider-gaps",
                [
                    f"graph:{node['node_id']}",
                    f"resolution-class:{node['resolution_class']}",
                    "assumption:screening-provider-intensity-bounds-v1",
                ],
            )
        )
    rows.extend(
        [
            _parameter(
                "coating:sucrose-input",
                COATING_SUCROSE_KG,
                0.1425,
                0.57,
                "kg sucrose/kg coated product",
                "coating-conversion-energy-gases",
                [
                    "source:hong-2012-carbon-coated-route-comparison",
                    "transform:6wt-retained-carbon-and-sucrose-carbon-fraction",
                ],
            ),
            _parameter(
                "coating:electricity-input",
                COATING_ELECTRICITY_MJ,
                5.0,
                60.0,
                "MJ/kg coated product",
                "coating-conversion-energy-gases",
                [
                    "source:hong-2012-carbon-coated-route-comparison",
                    "assumption:500-700C-3-6h-screening-energy-bound",
                ],
            ),
            _parameter(
                "coating:carbon-loss-gwp-proxy",
                0.22,
                0.0,
                0.66,
                "kg CO2-eq/kg coated product",
                "coating-conversion-energy-gases",
                [
                    "source:hong-2012-carbon-coated-route-comparison",
                    "transform:sucrose-carbon-not-retained-conservative-gwp-proxy",
                ],
            ),
            _parameter(
                "allocation:hydrothermal-mass-share",
                ALLOCATION_FACTOR,
                0.4,
                1.0,
                "fraction",
                "hydrothermal-allocation-choice",
                [
                    "graph:allocation:hydrothermal-lithium-sulfate",
                    "transform:157.754762/(157.754762+109.936)",
                ],
                derivation_class="stoichiometric_mass_allocation_and_scenario_bound",
            ),
        ]
    )
    return rows


def _new_flow_spec(node: JsonObject, graph_input_version: str) -> JsonObject:
    flow_key = _text(node.get("flow_key"), "new Flow key")
    flow_id = _stable_uuid(graph_input_version, str(node["node_id"]), "flow")
    name = {
        "carbon-coated-lifepo4": "Carbon-coated LiFePO4 cathode active material, screening product",
        "sucrose-coating-precursor": "Sucrose coating precursor, screening proxy",
    }[flow_key]
    return {
        "node_id": node["node_id"],
        "flow_key": flow_key,
        "flow_id": flow_id,
        "dataset_version": DATASET_VERSION,
        "name": name,
        "flow_type": "Product flow",
        "reference_unit": "kg",
        "reference_flow_property_id": "93a60a56-a3c8-11da-a746-0800200b9a66",
        "evidence_status": node["evidence_status"],
        "source_refs": copy.deepcopy(node["source_refs"]),
        "scientific_use": "minimum_graph_screening_only",
    }


def _provider_process(
    node: JsonObject,
    flow_node: JsonObject,
    graph_input_version: str,
) -> JsonObject:
    node_id = str(node["node_id"])
    flow_key = node_id.split(":", 1)[1]
    flow_id = _text(flow_node.get("entity_id"), f"{flow_key} Flow identity")
    process_id = _stable_uuid(graph_input_version, node_id, "provider-process")
    parameter_id = f"provider-gwp:{flow_key}"
    provenance = {
        "contract": CONTRACT,
        "graph_node_id": node_id,
        "resolution_class": node["resolution_class"],
        "provider_parameter_id": parameter_id,
        "adapter_required": True,
        "elementary_inventory_fabricated": False,
        "missing_value_interpreted_as_zero": False,
    }
    process = _process_row(
        process_id,
        f"{flow_key} Provider, {node['resolution_class']} screening proxy",
        [
            _exchange(
                1,
                flow_id,
                flow_key,
                "Output",
                1,
                reference=True,
                provenance=provenance,
            )
        ],
        provenance=provenance,
    )
    return {
        "node_id": node_id,
        "process_id": process_id,
        "dataset_version": DATASET_VERSION,
        "provided_flow_key": flow_key,
        "provided_flow_id": flow_id,
        "reference_unit": node["unit_mapping"]["reference_unit"],
        "resolution_class": node["resolution_class"],
        "evidence_status": node["evidence_status"],
        "parameter_id": parameter_id,
        "process": process,
    }


def _coating_process(
    graph_input_version: str,
    flow_nodes: Mapping[str, JsonObject],
) -> JsonObject:
    process_id = _stable_uuid(
        graph_input_version, "process:shared-carbon-coating", "foreground-process"
    )
    core = flow_nodes["flow:uncoated-lifepo4-route-core"]
    electricity = flow_nodes["flow:electricity"]
    sucrose = flow_nodes["flow:sucrose-coating-precursor"]
    product = flow_nodes["flow:carbon-coated-lifepo4"]
    provenance = {
        "contract": CONTRACT,
        "graph_node_id": "process:shared-carbon-coating",
        "source_ref": "source:hong-2012-carbon-coated-route-comparison",
        "retained_carbon_mass_fraction": 0.06,
        "conversion_energy_gas_status": "bounded_screening_parameters_not_complete_evidence",
    }
    exchanges = [
        _exchange(
            1,
            str(product["entity_id"]),
            "carbon-coated LiFePO4",
            "Output",
            1,
            reference=True,
            provenance=provenance,
        ),
        _exchange(
            2,
            str(core["entity_id"]),
            "uncoated LiFePO4 route core",
            "Input",
            ROUTE_MULTIPLIER,
            provenance={"parameter": "shared-core-input", **provenance},
        ),
        _exchange(
            3,
            str(sucrose["entity_id"]),
            "sucrose coating precursor",
            "Input",
            COATING_SUCROSE_KG,
            provenance={"parameter_id": "coating:sucrose-input", **provenance},
        ),
        _exchange(
            4,
            str(electricity["entity_id"]),
            "electricity",
            "Input",
            COATING_ELECTRICITY_MJ,
            provenance={"parameter_id": "coating:electricity-input", **provenance},
        ),
    ]
    return {
        "node_id": "process:shared-carbon-coating",
        "process_id": process_id,
        "dataset_version": DATASET_VERSION,
        "uncoated_core_input_kg": ROUTE_MULTIPLIER,
        "coated_product_output_kg": 1,
        "retained_carbon_output_kg": 0.06,
        "sucrose_parameter_id": "coating:sucrose-input",
        "energy_parameter_id": "coating:electricity-input",
        "gas_parameter_id": "coating:carbon-loss-gwp-proxy",
        "process": _process_row(
            process_id,
            "Carbon coating of LiFePO4, shared screening proxy",
            exchanges,
            provenance=provenance,
        ),
    }


def _connection(
    flow_id: str,
    downstream_id: int,
    *,
    version: str = DATASET_VERSION,
) -> JsonObject:
    return {
        "@flowUUID": flow_id,
        "@version": version,
        "@dominant": "true",
        "downstreamProcess": {
            "@id": str(downstream_id),
            "@flowUUID": flow_id,
            "@version": version,
            "@dominant": "true",
        },
    }


def _instance(
    internal_id: int,
    process_id: str,
    name: str,
    multiplier: float,
    connections: Sequence[JsonObject],
) -> JsonObject:
    row: JsonObject = {
        "@dataSetInternalID": str(internal_id),
        "@multiplicationFactor": _decimal_text(multiplier),
        "referenceToProcess": _reference(
            process_id, name, "process data set"
        ),
    }
    if connections:
        row["connections"] = {
            "outputExchange": (
                copy.deepcopy(connections[0])
                if len(connections) == 1
                else [copy.deepcopy(connection) for connection in connections]
            )
        }
    return row


def _lifecyclemodel_row(
    graph_input_version: str,
    route_node: JsonObject,
    coating: JsonObject,
    providers: Sequence[JsonObject],
    flow_nodes: Mapping[str, JsonObject],
    edges: Sequence[JsonObject],
) -> JsonObject:
    route_id = str(route_node["route_role"])
    model_id = _stable_uuid(
        graph_input_version, f"lifecyclemodel:{route_id}", "lifecyclemodel"
    )
    resulting_id = _stable_uuid(
        graph_input_version, f"lifecyclemodel:{route_id}", "resulting-process"
    )
    route_process_id = _text(route_node.get("entity_id"), f"{route_id} Process")
    route_name = f"LiFePO4 {route_id} route-core screening Process"
    product = flow_nodes["flow:carbon-coated-lifepo4"]
    core = flow_nodes["flow:uncoated-lifepo4-route-core"]
    provider_by_flow = {row["provided_flow_key"]: row for row in providers}
    instance_ids: MutableMapping[str, int] = {"route": 1, "coating": 2}
    for index, provider in enumerate(
        sorted(providers, key=lambda row: str(row["node_id"])), start=3
    ):
        instance_ids[str(provider["node_id"])] = index

    route_connection = _connection(str(core["entity_id"]), 2)
    instances = [
        _instance(1, route_process_id, route_name, ROUTE_MULTIPLIER, [route_connection]),
        _instance(
            2,
            str(coating["process_id"]),
            "Shared LiFePO4 carbon-coating Process",
            1.0,
            [],
        ),
    ]
    connection_rows = [
        {
            "route_id": route_id,
            "from_instance_id": 1,
            "to_instance_id": 2,
            "flow_id": core["entity_id"],
            "flow_key": "uncoated-lifepo4-route-core",
            "connection_role": "route_core_to_shared_coating",
        }
    ]
    input_amounts = {
        str(edge["from_node_id"]).split(":", 1)[1]: float(edge["amount"])
        for edge in edges
        if edge.get("edge_type") == "technosphere_input"
        and edge.get("route_id") == route_id
    }
    for provider in sorted(providers, key=lambda row: str(row["node_id"])):
        flow_key = str(provider["provided_flow_key"])
        downstream = []
        if flow_key in input_amounts:
            downstream.append(1)
        if flow_key in {"electricity", "sucrose-coating-precursor"}:
            downstream.append(2)
        if not downstream:
            raise ValueError(f"Provider has no route-local downstream use: {flow_key}")
        demand = input_amounts.get(flow_key, 0.0) * ROUTE_MULTIPLIER
        if flow_key == "electricity":
            demand += COATING_ELECTRICITY_MJ
        elif flow_key == "sucrose-coating-precursor":
            demand += COATING_SUCROSE_KG
        connections = [
            _connection(str(provider["provided_flow_id"]), downstream_id)
            for downstream_id in downstream
        ]
        internal_id = instance_ids[str(provider["node_id"])]
        instances.append(
            _instance(
                internal_id,
                str(provider["process_id"]),
                f"{flow_key} Provider proxy",
                demand,
                connections,
            )
        )
        for downstream_id in downstream:
            connection_rows.append(
                {
                    "route_id": route_id,
                    "from_instance_id": internal_id,
                    "to_instance_id": downstream_id,
                    "flow_id": provider["provided_flow_id"],
                    "flow_key": flow_key,
                    "connection_role": "provider_binding",
                }
            )
    instances = sorted(instances, key=lambda row: int(row["@dataSetInternalID"]))
    provenance = {
        "contract": CONTRACT,
        "route_id": route_id,
        "graph_input_version": graph_input_version,
        "functional_unit": "1 kg carbon-coated LiFePO4 cathode active material",
        "route_process_multiplier": ROUTE_MULTIPLIER,
        "hydrothermal_allocation_stage": (
            "lcia_adapter" if route_id == "hydrothermal" else "not_applicable"
        ),
        "native_cli_calculation_command_available": False,
    }
    format_source = _stable_uuid("support", "tidas-format", "source")
    owner = _stable_uuid("support", "local-controller", "contact")
    compliance_source = _stable_uuid(
        graph_input_version, "minimum-comparison-model-compliance", "source"
    )
    model = {
        "lifeCycleModelDataSet": {
            "@locations": "../ILCDLocations.xml",
            "@xmlns": "http://eplca.jrc.ec.europa.eu/ILCD/LifeCycleModel/2017",
            "@xmlns:acme": "http://acme.com/custom",
            "@xmlns:common": "http://lca.jrc.it/ILCD/Common",
            "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "@version": "1.1",
            "@xsi:schemaLocation": (
                "http://eplca.jrc.ec.europa.eu/ILCD/LifeCycleModel/2017 "
                "../../schemas/ILCD_LifeCycleModelDataSet.xsd"
            ),
            "lifeCycleModelInformation": {
                "dataSetInformation": {
                    "common:UUID": model_id,
                    "name": {
                        "baseName": [_localized(f"LiFePO4 {route_id} screening model")],
                        "functionalUnitFlowProperties": [
                            _localized("1 kg carbon-coated LiFePO4 cathode active material")
                        ],
                        "mixAndLocationTypes": [_localized("Local screening proxy")],
                        "treatmentStandardsRoutes": [
                            _localized(f"{route_id} plus shared carbon coating")
                        ],
                    },
                    "classificationInformation": _battery_material_classification(),
                    "referenceToResultingProcess": _reference(
                        resulting_id,
                        f"LiFePO4 {route_id} resulting Process",
                        "process data set",
                    ),
                    "common:generalComment": [
                        _localized(json.dumps(provenance, sort_keys=True))
                    ],
                },
                "quantitativeReference": {"referenceToReferenceProcess": 2},
                "technology": {"processes": {"processInstance": instances}},
            },
            "modellingAndValidation": {
                "validation": {
                    "review": {
                        "common:otherReviewDetails": [
                            _localized(
                                "Local deterministic connected lifecyclemodel; native schema, graph, QA and replay required."
                            )
                        ],
                        "common:referenceToNameOfReviewerAndInstitution": _reference(
                            owner,
                            "Local research controller",
                            "contact data set",
                            version="00.00.000",
                        ),
                    }
                },
                "complianceDeclarations": {
                    "compliance": {
                        "common:referenceToComplianceSystem": _reference(
                            compliance_source,
                            "Minimum comparison model contract",
                            "source data set",
                            version="00.00.000",
                        ),
                        "common:approvalOfOverallCompliance": "Not defined",
                        "common:nomenclatureCompliance": "Not defined",
                        "common:methodologicalCompliance": "Not defined",
                        "common:reviewCompliance": "Not defined",
                        "common:documentationCompliance": "Not defined",
                        "common:qualityCompliance": "Not defined",
                    }
                },
            },
            "administrativeInformation": {
                "common:commissionerAndGoal": {
                    "common:intendedApplications": [
                        _localized("Screening comparison and sensitivity analysis only.")
                    ],
                    "common:referenceToCommissioner": _reference(
                        owner,
                        "Local research controller",
                        "contact data set",
                        version="00.00.000",
                    ),
                },
                "dataEntryBy": {
                    "common:timeStamp": "1970-01-01T00:00:00.000Z",
                    "common:referenceToDataSetFormat": _reference(
                        format_source,
                        "TIDAS / ILCD format",
                        "source data set",
                        version="00.00.000",
                    ),
                    "common:referenceToPersonOrEntityEnteringTheData": _reference(
                        owner,
                        "Local research controller",
                        "contact data set",
                        version="00.00.000",
                    ),
                },
                "publicationAndOwnership": {
                    "common:dataSetVersion": DATASET_VERSION,
                    "common:permanentDataSetURI": f"urn:uuid:{model_id}",
                    "common:copyright": "false",
                    "common:licenseType": "Free of charge for all users and uses",
                    "common:referenceToOwnershipOfDataSet": _reference(
                        owner,
                        "Local research controller",
                        "contact data set",
                        version="00.00.000",
                    ),
                }
            },
        }
    }
    return {
        "route_id": route_id,
        "model_id": model_id,
        "dataset_version": DATASET_VERSION,
        "resulting_process_id": resulting_id,
        "reference_product_flow_id": product["entity_id"],
        "functional_unit_amount": 1,
        "functional_unit_unit": "kg",
        "route_process_multiplier": ROUTE_MULTIPLIER,
        "native_node_count": len(instances),
        "native_edge_count": len(connection_rows),
        "connections": connection_rows,
        "lifecyclemodel": model,
    }


def _lcia_method(graph_input_version: str, graph: Mapping[str, object]) -> JsonObject:
    method_id = _stable_uuid(
        graph_input_version,
        "lciamethod:ipcc-ar6-gwp100-screening",
        "lciamethod",
    )
    source_id = _stable_uuid(graph_input_version, "ipcc-ar6-wg1", "source")
    provenance = {
        "contract": CONTRACT,
        "selected_method_id": "ipcc-ar6-gwp100-screening",
        "adapter_required": True,
        "native_cli_calculation_command_available": False,
        "native_characterization_factor_count": 0,
        "provider_parameter_ledger_required": True,
        "claim_boundary": graph["lcia_method"]["claim_boundary"],
    }
    # The pinned TIDAS schema permits an empty factor collection. This row is a
    # native method/adapter contract, not a claim that a hidden calculator or a
    # complete factor set exists.
    return {
        "method_id": method_id,
        "dataset_version": DATASET_VERSION,
        "selected_method_id": "ipcc-ar6-gwp100-screening",
        "adapter_required": True,
        "native_cli_calculation_command_available": False,
        "native_characterization_factor_count": 0,
        "parameter_ledger_sha256": None,
        "lcia_method": {
            "LCIAMethodDataSet": {
                "@xmlns": "http://lca.jrc.it/ILCD/LCIAMethod",
                "@xmlns:common": "http://lca.jrc.it/ILCD/Common",
                "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
                "@version": "1.1",
                "@xsi:schemaLocation": (
                    "http://lca.jrc.it/ILCD/LCIAMethod ../../schemas/ILCD_LCIAMethodDataSet.xsd"
                ),
                "LCIAMethodInformation": {
                    "dataSetInformation": {
                        "common:UUID": method_id,
                        "common:name": [_localized("IPCC AR6 GWP100 screening adapter")],
                        "methodology": "Other",
                        "classificationInformation": {
                            "common:classification": {
                                "common:class": [
                                    {
                                        "@level": "0",
                                        "@classId": "2",
                                        "#text": "Midpoint level LCIA methods",
                                    },
                                    {
                                        "@level": "1",
                                        "@classId": "2.2",
                                        "#text": "Climate change",
                                    },
                                    {
                                        "@level": "2",
                                        "@classId": "1.3.2",
                                        "#text": "Natural environment, climate change",
                                    },
                                ]
                            }
                        },
                        "impactCategory": "Climate change",
                        "impactIndicator": "Global warming potential 100 years",
                        "common:generalComment": [
                            _localized(json.dumps(provenance, sort_keys=True))
                        ],
                    },
                    "quantitativeReference": {
                        "referenceQuantity": _reference(
                            _stable_uuid(
                                graph_input_version,
                                "kg-co2-equivalent-reference-quantity",
                                "unit-group",
                            ),
                            "kg CO2-equivalent reference quantity",
                            "unit group data set",
                            version="00.00.000",
                        )
                    },
                    "time": {
                        "referenceYear": [_localized("2021")],
                        "duration": [_localized("100 years")],
                        "timeRepresentativenessDescription": [
                            _localized("IPCC AR6 reference; screening adapter parameters are project-local.")
                        ],
                    },
                    "impactModel": {
                        "modelName": "IPCC AR6 GWP100 screening adapter",
                        "modelDescription": [
                            _localized(
                                "Climate-change-only screening adapter over bounded Provider intensity parameters."
                            )
                        ],
                    },
                },
                "modellingAndValidation": {
                    "LCIAMethodNormalisationAndWeighting": {
                        "typeOfDataSet": "Mid-point indicator",
                        "LCIAMethodPrinciple": "other",
                        "deviationsFromLCIAMethodPrinciple": [
                            _localized("Project-local pre-characterized Provider parameter adapter.")
                        ],
                    },
                    "dataSources": {
                        "referenceToDataSource": _reference(
                            source_id,
                            "IPCC AR6 Working Group I",
                            "source data set",
                        )
                    },
                    "validation": {"review": {"@type": "Not reviewed"}},
                    "complianceDeclarations": {
                        "compliance": {
                            "common:referenceToComplianceSystem": _reference(
                                source_id,
                                "Project screening adapter contract",
                                "source data set",
                            ),
                            "common:approvalOfOverallCompliance": "Not defined",
                            "common:nomenclatureCompliance": "Not defined",
                            "common:methodologicalCompliance": "Not defined",
                            "common:reviewCompliance": "Not defined",
                            "common:documentationCompliance": "Not defined",
                            "common:qualityCompliance": "Not defined",
                        }
                    },
                },
                "administrativeInformation": {
                    "dataGenerator": {
                        "common:referenceToPersonOrEntityGeneratingTheDataSet": _reference(
                            source_id,
                            "Local research controller",
                            "contact data set",
                            version="00.00.000",
                        )
                    },
                    "dataEntryBy": {
                        "common:timeStamp": "1970-01-01T00:00:00.000Z",
                        "common:referenceToDataSetFormat": _reference(
                            source_id, "TIDAS / ILCD format", "source data set"
                        ),
                        "recommendationBy": {
                            "referenceToEntity": _reference(
                                source_id,
                                "Local research controller",
                                "contact data set",
                                version="00.00.000",
                            ),
                            "level": "Not recommended",
                            "meaning": [
                                _localized(
                                    "Screening analysis only; not an official recommendation."
                                )
                            ],
                        },
                    },
                    "publicationAndOwnership": {
                        "common:dateOfLastRevision": "2026-07-19T00:00:00.000Z",
                        "common:dataSetVersion": DATASET_VERSION,
                        "common:permanentDataSetURI": f"urn:uuid:{method_id}",
                        "common:copyright": "false",
                        "common:referenceToOwnershipOfDataSet": _reference(
                            source_id,
                            "Local research controller",
                            "contact data set",
                            version="00.00.000",
                        ),
                    },
                },
                "characterisationFactors": {"factor": []},
            }
        },
    }


def compile_minimum_comparison_model(graph_report: Mapping[str, object]) -> JsonObject:
    """Compile exact graph membership without invoking native tools or executing LCIA."""

    graph = _object(graph_report, "minimum comparison graph")
    if (
        graph.get("contract") != GRAPH_CONTRACT
        or graph.get("status") != "minimum_lifepo4_comparison_graph_compiled"
        or graph.get("node_count") != 33
        or graph.get("edge_count") != 55
        or graph.get("node_type_counts") != EXPECTED_NODE_COUNTS
        or graph.get("provider_slot_count") != 10
        or graph.get("verification_summary")
        != {"status": "accepted", "decision_count": 7, "accepted_decision_count": 7}
        or graph.get("freeze", {}).get("unmaterialized_release_record_count") != 1453
        or graph.get("freeze", {}).get("general_corpus_release_frozen") is not True
    ):
        raise ValueError("accepted minimum graph membership is required")
    nodes = [_object(row, "graph node") for row in _list(graph.get("nodes"), "nodes")]
    edges = [_object(row, "graph edge") for row in _list(graph.get("edges"), "edges")]
    if Counter(str(row.get("node_type")) for row in nodes) != Counter(
        EXPECTED_NODE_COUNTS
    ):
        raise ValueError("minimum graph node membership drifted")
    node_by_id = {_text(row.get("node_id"), "node ID"): row for row in nodes}
    if len(node_by_id) != 33:
        raise ValueError("minimum graph node identities drifted")
    graph_input_version = _text(graph.get("input_version"), "graph input version")

    new_flow_nodes = [
        node_by_id["flow:carbon-coated-lifepo4"],
        node_by_id["flow:sucrose-coating-precursor"],
    ]
    new_flows = [_new_flow_spec(row, graph_input_version) for row in new_flow_nodes]
    flow_nodes = {
        node_id: copy.deepcopy(row)
        for node_id, row in node_by_id.items()
        if row.get("node_type") == "Flow"
    }
    for row in new_flows:
        flow_nodes[str(row["node_id"])]["entity_id"] = row["flow_id"]
        flow_nodes[str(row["node_id"])]["entity_version"] = DATASET_VERSION
    if any(not row.get("entity_id") for row in flow_nodes.values()):
        raise ValueError("minimum graph Flow identity is unresolved")

    provider_nodes = sorted(
        [row for row in nodes if row.get("node_type") == "Provider"],
        key=lambda row: str(row["node_id"]),
    )
    parameter_ledger = _parameter_ledger(provider_nodes)
    provider_processes = [
        _provider_process(
            row,
            flow_nodes[f"flow:{str(row['node_id']).split(':', 1)[1]}"],
            graph_input_version,
        )
        for row in provider_nodes
    ]
    coating = _coating_process(graph_input_version, flow_nodes)

    provider_by_id = {str(row["node_id"]): row for row in provider_processes}
    route_nodes = {
        str(row["route_role"]): row
        for row in nodes
        if row.get("node_type") == "foreground Process"
        and row.get("route_role") in {"solid-state", "hydrothermal"}
    }
    if set(route_nodes) != {"solid-state", "hydrothermal"}:
        raise ValueError("both route Process nodes are required")
    lifecyclemodels = []
    for route_id in ("solid-state", "hydrothermal"):
        provider_members = []
        target = f"process:{route_id}"
        for graph_node in provider_nodes:
            downstream = graph_node.get("downstream_node_ids")
            if not isinstance(downstream, list):
                raise ValueError("Provider downstream contract drifted")
            if target in downstream or "process:shared-carbon-coating" in downstream:
                provider_members.append(provider_by_id[str(graph_node["node_id"])])
        lifecyclemodels.append(
            _lifecyclemodel_row(
                graph_input_version,
                route_nodes[route_id],
                coating,
                provider_members,
                flow_nodes,
                edges,
            )
        )
    lcia_method = _lcia_method(graph_input_version, graph)
    lcia_method["parameter_ledger_sha256"] = _hash(parameter_ledger)
    input_version = _hash(
        {
            "contract": CONTRACT,
            "graph_input_version": graph_input_version,
            "graph_membership": sorted(node_by_id),
            "new_flows": new_flows,
            "provider_parameter_ledger": parameter_ledger,
            "allocation": ALLOCATION_FACTOR,
        }
    )[:16]
    result: JsonObject = {
        "schema_version": 1,
        "contract": CONTRACT,
        "status": STATUS,
        "scope_id": graph["scope_id"],
        "revision": graph["revision"],
        "input_version": input_version,
        "graph_input_version": graph_input_version,
        "graph_contract": graph["contract"],
        "graph_node_count": 33,
        "graph_edge_count": 55,
        "entity_counts": copy.deepcopy(ENTITY_COUNTS),
        "new_flows": new_flows,
        "provider_processes": provider_processes,
        "coating_process": coating,
        "lcia_method": lcia_method,
        "lifecyclemodels": lifecyclemodels,
        "parameter_ledger": parameter_ledger,
        "parameter_ledger_sha256": _hash(parameter_ledger),
        "allocation_contract": {
            "route_id": "hydrothermal",
            "mass_share": ALLOCATION_FACTOR,
            "application_stage": "lcia_adapter",
            "physical_route_process_multiplier": ROUTE_MULTIPLIER,
            "baseline": "mass_allocation_exactly_once",
            "sensitivity": ["no_allocation", "coproduct_substitution_bound"],
        },
        "allocation_application_count": 1,
        "graph_nonmember_materialization_count": 0,
        "materialized_release_record_count": 3,
        "unmaterialized_release_record_count": 1453,
        "general_corpus_release_frozen": True,
        "native_invocation_executed_count": 0,
        "network_read_executed_count": 0,
        "remote_write_executed_count": 0,
        "publication_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "next_internal_route": NEXT_ROUTE,
        "next_action": (
            "Materialize these exact rows in the pinned isolated TianGong runtime, "
            "run native validation/QA/graph/resulting-Process/save-draft gates, and replay."
        ),
    }
    verify_minimum_comparison_model(result)
    return result


def _bound_workspace_bytes(
    workspace: Path, path_value: object, sha_value: object, label: str
) -> Tuple[Path, bytes]:
    relative = Path(_text(path_value, f"{label} path"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{label} path escaped the workspace")
    path = workspace / relative
    if not path.is_file():
        raise ValueError(f"{label} is missing: {relative}")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != _text(
        sha_value, f"{label} SHA-256"
    ):
        raise ValueError(f"{label} hash drifted")
    return path, content


def _artifact_source_ref(value: object, label: str) -> Tuple[str, str]:
    text = _text(value, label)
    if not text.startswith("artifact:") or "#sha256=" not in text:
        raise ValueError(f"{label} is not an artifact binding")
    path, digest = text[len("artifact:") :].rsplit("#sha256=", 1)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError(f"{label} SHA-256 is invalid")
    return path, digest


def _process_uuid(row: Mapping[str, object], label: str) -> str:
    process = _object(row.get("processDataSet"), f"{label} processDataSet")
    information = _object(
        process.get("processInformation"), f"{label} processInformation"
    )
    dataset = _object(
        information.get("dataSetInformation"), f"{label} dataSetInformation"
    )
    return _text(dataset.get("common:UUID"), f"{label} UUID")


def _model_instances(model: Mapping[str, object], label: str) -> List[JsonObject]:
    root = _object(model.get("lifeCycleModelDataSet"), f"{label} root")
    information = _object(
        root.get("lifeCycleModelInformation"), f"{label} information"
    )
    technology = _object(information.get("technology"), f"{label} technology")
    processes = _object(technology.get("processes"), f"{label} processes")
    raw = processes.get("processInstance")
    values = raw if isinstance(raw, list) else [raw]
    rows = [_object(value, f"{label} process instance") for value in values]
    if not rows:
        raise ValueError(f"{label} has no Process instances")
    return rows


def compile_minimum_comparison_native_inputs(
    model_report: Mapping[str, object],
    graph_report: Mapping[str, object],
    workspace: Path,
) -> JsonObject:
    """Bind exact local authorities and compile inputs for supported native gates."""

    model = _object(model_report, "minimum comparison model")
    graph = _object(graph_report, "minimum comparison graph")
    verify_minimum_comparison_model(model)
    try:
        from .minimum_comparison_graph import verify_minimum_comparison_graph
    except ImportError:  # Direct activity-script execution from the project workspace.
        from minimum_comparison_graph import verify_minimum_comparison_graph  # type: ignore

    workspace = Path(workspace)
    graph_verification = verify_minimum_comparison_graph(graph, workspace)
    if (
        graph_verification.get("status") != "accepted"
        or model.get("graph_input_version") != graph.get("input_version")
        or model.get("graph_node_count") != graph.get("node_count")
        or model.get("graph_edge_count") != graph.get("edge_count")
    ):
        raise ValueError("minimum model and accepted graph binding drifted")

    nodes = [_object(value, "graph node") for value in _list(graph.get("nodes"), "nodes")]
    route_nodes = {
        str(row.get("route_role")): row
        for row in nodes
        if row.get("node_type") == "foreground Process"
        and row.get("route_role") in {"solid-state", "hydrothermal"}
    }
    if set(route_nodes) != {"solid-state", "hydrothermal"}:
        raise ValueError("both current route Process nodes are required")
    process_refs = set()
    for route_id, node in route_nodes.items():
        refs = [
            _artifact_source_ref(value, f"{route_id} Process source")
            for value in _list(node.get("source_refs"), f"{route_id} source refs")
            if isinstance(value, str) and value.startswith("artifact:")
        ]
        if len(refs) != 1:
            raise ValueError(f"{route_id} Process artifact binding drifted")
        process_refs.add(refs[0])
    if len(process_refs) != 1:
        raise ValueError("route Processes no longer share one integrated artifact")
    process_path_value, process_sha256 = next(iter(process_refs))
    _, process_bytes = _bound_workspace_bytes(
        workspace,
        process_path_value,
        process_sha256,
        "integrated route Process rows",
    )
    route_process_rows: Dict[str, JsonObject] = {}
    for line in process_bytes.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("integrated route Process rows are invalid JSONL") from error
        row = _object(value, "integrated route Process row")
        route_process_rows[_process_uuid(row, "integrated route Process")] = row
    expected_route_ids = {
        _text(node.get("entity_id"), f"{route_id} Process UUID")
        for route_id, node in route_nodes.items()
    }
    if set(route_process_rows) != expected_route_ids or len(route_process_rows) != 2:
        raise ValueError("integrated route Process membership drifted")

    graph_input_version = _text(model.get("graph_input_version"), "graph input version")
    new_flow_rows = [
        _flow_row(_object(value, "new Flow spec"), graph_input_version)
        for value in _list(model.get("new_flows"), "new Flows")
    ]
    provider_rows = [
        _object(_object(value, "Provider").get("process"), "Provider Process")
        for value in _list(model.get("provider_processes"), "Provider processes")
    ]
    coating = _object(model.get("coating_process"), "coating Process")
    coating_row = _object(coating.get("process"), "coating Process row")
    process_rows = [
        route_process_rows[process_id] for process_id in sorted(route_process_rows)
    ] + [coating_row, *provider_rows]
    process_by_id = {
        _process_uuid(row, "native Process input"): row for row in process_rows
    }
    if len(process_rows) != 13 or len(process_by_id) != 13:
        raise ValueError("native Process input membership drifted")

    models = [
        _object(value, "LifecycleModel")
        for value in _list(model.get("lifecyclemodels"), "LifecycleModels")
    ]
    lifecyclemodel_rows = [
        _object(value.get("lifecyclemodel"), "LifecycleModel row") for value in models
    ]
    projection_lifecyclemodel_rows = copy.deepcopy(lifecyclemodel_rows)
    for row in projection_lifecyclemodel_rows:
        root = _object(row.get("lifeCycleModelDataSet"), "projection LifecycleModel")
        information = _object(
            root.get("lifeCycleModelInformation"),
            "projection LifecycleModel information",
        )
        quantitative = _object(
            information.get("quantitativeReference"),
            "projection LifecycleModel quantitative reference",
        )
        if quantitative.get("referenceToReferenceProcess") != 2:
            raise ValueError("canonical LifecycleModel reference instance drifted")
        # Pinned 0.0.25 schema requires a number while its projector matches string IDs.
        quantitative["referenceToReferenceProcess"] = "2"
        information["quantitativeReference"] = quantitative
        root["lifeCycleModelInformation"] = information
        row["lifeCycleModelDataSet"] = root
    projection_requests: List[JsonObject] = []
    for value, lifecyclemodel in zip(models, lifecyclemodel_rows):
        route_id = _text(value.get("route_id"), "LifecycleModel route")
        instances = _model_instances(lifecyclemodel, f"{route_id} LifecycleModel")
        process_ids = []
        for instance in instances:
            reference = _object(
                instance.get("referenceToProcess"), f"{route_id} Process reference"
            )
            process_id = _text(
                reference.get("@refObjectId"), f"{route_id} referenced Process UUID"
            )
            if process_id not in process_by_id:
                raise ValueError(f"{route_id} referenced Process is unbound: {process_id}")
            process_ids.append(process_id)
        if len(process_ids) != len(set(process_ids)):
            raise ValueError(f"{route_id} repeats a Process instance")
        request = {
            "source_model": {
                "id": value["model_id"],
                "version": value["dataset_version"],
                "json_ordered_path": f"../projection-lifecyclemodels/{route_id}.json",
            },
            "projection": {
                "mode": "primary-only",
                "process_id": value["resulting_process_id"],
                "process_version": value["dataset_version"],
                "metadata_overrides": {
                    "type_of_data_set": "Partly terminated system",
                    "kernel_contract": CONTRACT,
                    "route_id": route_id,
                    "graph_input_version": graph_input_version,
                },
                "attach_graph_snapshot": False,
            },
            "process_sources": {
                "process_json_files": [
                    f"../processes/{process_id}_{DATASET_VERSION}.json"
                    for process_id in process_ids
                ],
                "allow_remote_lookup": False,
            },
            "publish": {
                "intent": "dry_run",
                "prepare_process_payloads": True,
                "prepare_relation_payloads": True,
            },
        }
        projection_requests.append(
            {
                "route_id": route_id,
                "model_id": value["model_id"],
                "resulting_process_id": value["resulting_process_id"],
                "reference_product_flow_id": value["reference_product_flow_id"],
                "expected_node_count": value["native_node_count"],
                "expected_edge_count": value["native_edge_count"],
                "process_ids": process_ids,
                "request": request,
                "request_sha256": _hash(request),
            }
        )
    if len(projection_requests) != 2:
        raise ValueError("two projection requests are required")

    source_ids = sorted(
        _text(row.get("entity_id"), "reused Source UUID")
        for row in nodes
        if row.get("node_type") == "Source"
    )
    if len(source_ids) != 4 or len(set(source_ids)) != 4:
        raise ValueError("reused Source membership drifted")
    lcia = _object(model.get("lcia_method"), "LCIAMethod")
    lcia_row = _object(lcia.get("lcia_method"), "LCIAMethod row")
    identity = {
        "contract": NATIVE_INPUTS_CONTRACT,
        "model_input_version": model.get("input_version"),
        "graph_input_version": graph_input_version,
        "flow_ids": sorted(
            _text(
                _object(
                    _object(row.get("flowDataSet"), "Flow root").get("flowInformation"),
                    "Flow information",
                ).get("dataSetInformation", {}).get("common:UUID"),
                "Flow UUID",
            )
            for row in new_flow_rows
        ),
        "process_ids": sorted(process_by_id),
        "source_ids": source_ids,
        "projection_request_sha256": sorted(
            str(row["request_sha256"]) for row in projection_requests
        ),
        "parameter_ledger_sha256": model.get("parameter_ledger_sha256"),
    }
    return {
        "schema_version": 1,
        "contract": NATIVE_INPUTS_CONTRACT,
        "status": "minimum_lifepo4_comparison_native_inputs_compiled",
        "input_version": _hash(identity)[:16],
        "model_input_version": model.get("input_version"),
        "graph_input_version": graph_input_version,
        "new_flow_count": 2,
        "process_count": 13,
        "reused_route_process_count": 2,
        "new_process_count": 11,
        "reused_source_count": 4,
        "lcia_method_count": 1,
        "lifecyclemodel_count": 2,
        "new_flow_rows": new_flow_rows,
        "process_rows": process_rows,
        "reused_source_ids": source_ids,
        "lcia_method_row": lcia_row,
        "lifecyclemodel_rows": lifecyclemodel_rows,
        "projection_lifecyclemodel_rows": projection_lifecyclemodel_rows,
        "projection_lifecyclemodel_compatibility": {
            "runtime_surface": "@tiangong-lca/cli@0.0.25 build-resulting-process",
            "field": "referenceToReferenceProcess",
            "canonical_schema_type": "number",
            "projector_match_type": "string",
            "canonical_value": 2,
            "projection_value": "2",
            "scientific_values_changed": False,
        },
        "projection_requests": projection_requests,
        "parameter_ledger": copy.deepcopy(model["parameter_ledger"]),
        "allocation_contract": copy.deepcopy(model["allocation_contract"]),
        "graph_input_bindings": copy.deepcopy(graph["input_bindings"]),
        "network_read_executed_count": 0,
        "remote_write_executed_count": 0,
        "publication_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
    }


def minimum_comparison_native_commands(
    node_path: Path, cli_entry_path: Path, out_dir: Path
) -> List[List[str]]:
    """Return direct local-only commands for every CLI-supported native gate."""

    prefix = [str(Path(node_path)), str(Path(cli_entry_path))]
    root = Path(out_dir)
    inputs = root / "inputs"
    native = root / "native"
    commands = [
        [*prefix, "dataset", "validate", "--input", str(inputs / "new-flows.jsonl"), "--type", "flow", "--out-dir", str(native / "flow-validation"), "--json"],
        [*prefix, "qa", "flow", "--rows-file", str(inputs / "new-flows.jsonl"), "--out-dir", str(native / "flow-qa"), "--json"],
        [*prefix, "dataset", "save-draft", "--input", str(inputs / "new-flows.jsonl"), "--type", "flow", "--out-dir", str(native / "flow-save-draft"), "--dry-run", "--json"],
        [*prefix, "dataset", "validate", "--input", str(inputs / "processes.jsonl"), "--type", "process", "--out-dir", str(native / "process-validation"), "--json"],
        [*prefix, "qa", "process", "--rows-file", str(inputs / "processes.jsonl"), "--out-dir", str(native / "process-qa"), "--json"],
        [*prefix, "process", "save-draft", "--input", str(inputs / "processes.jsonl"), "--out-dir", str(native / "process-save-draft"), "--dry-run", "--json"],
        [*prefix, "dataset", "validate", "--input", str(inputs / "lifecyclemodels.jsonl"), "--type", "lifecyclemodel", "--out-dir", str(native / "lifecyclemodel-validation"), "--json"],
        [*prefix, "lifecyclemodel", "graph", "--input", str(inputs / "lifecyclemodels.jsonl"), "--out-dir", str(native / "lifecyclemodel-graph"), "--format", "all", "--check-connections", "--json"],
        [*prefix, "qa", "lifecyclemodel", "--rows-file", str(inputs / "lifecyclemodels.jsonl"), "--out-dir", str(native / "lifecyclemodel-qa"), "--json"],
        [*prefix, "lifecyclemodel", "save-draft", "--input", str(inputs / "lifecyclemodels.jsonl"), "--out-dir", str(native / "lifecyclemodel-save-draft"), "--dry-run", "--json"],
    ]
    for route_id in ("solid-state", "hydrothermal"):
        commands.append(
            [
                *prefix,
                "lifecyclemodel",
                "build-resulting-process",
                "--input",
                str(inputs / "projection-requests" / f"{route_id}.json"),
                "--out-dir",
                str(native / "projection" / route_id),
                "--json",
            ]
        )
    commands.extend(
        [
            [*prefix, "dataset", "validate", "--input", str(root / "outputs" / "resulting-processes.jsonl"), "--type", "process", "--out-dir", str(native / "resulting-process-validation"), "--json"],
            [*prefix, "qa", "process", "--rows-file", str(root / "outputs" / "resulting-processes.jsonl"), "--out-dir", str(native / "resulting-process-qa"), "--json"],
            [*prefix, "process", "save-draft", "--input", str(root / "outputs" / "resulting-processes.jsonl"), "--out-dir", str(native / "resulting-process-save-draft"), "--dry-run", "--json"],
        ]
    )
    return commands


def normalize_projected_process_rows(
    rows: Sequence[Mapping[str, object]],
) -> Tuple[List[JsonObject], JsonObject]:
    """Apply the proven TianGong 0.0.25 number-to-decimal-string repair only."""

    if not rows:
        raise ValueError("projected Process rows are required")
    normalized = [copy.deepcopy(_object(row, "projected Process")) for row in rows]
    changed_paths: List[str] = []
    numeric_replacement_count = 0
    singleton_wrapper_replacement_count = 0
    for row_index, row in enumerate(normalized):
        process = _object(row.get("processDataSet"), "projected processDataSet")
        wrapper = _object(process.get("exchanges"), "projected Process exchanges")
        raw = wrapper.get("exchange")
        values = raw if isinstance(raw, list) else [raw]
        exchanges = [_object(value, "projected Process exchange") for value in values]
        if not exchanges:
            raise ValueError("projected Process has no exchanges")
        for exchange_index, exchange in enumerate(exchanges):
            for field in ("meanAmount", "resultingAmount"):
                if field not in exchange:
                    continue
                value = exchange[field]
                if isinstance(value, bool):
                    raise ValueError("projected Process decimal field cannot be boolean")
                if not isinstance(value, (int, float)):
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError("projected Process decimal field is invalid")
                    continue
                decimal = Decimal(str(value))
                if not decimal.is_finite():
                    raise ValueError("projected Process decimal field is not finite")
                text = format(decimal.normalize(), "f")
                if text in {"-0", ""}:
                    text = "0"
                exchange[field] = text
                numeric_replacement_count += 1
                changed_paths.append(
                    f"/{row_index}/processDataSet/exchanges/exchange/"
                    f"{exchange_index}/{field}"
                )
        if not isinstance(raw, list):
            singleton_wrapper_replacement_count += 1
            changed_paths.append(
                f"/{row_index}/processDataSet/exchanges/exchange"
            )
        wrapper["exchange"] = exchanges
        process["exchanges"] = wrapper
        row["processDataSet"] = process
    return normalized, {
        "schema_version": 1,
        "kind": "lossless_tidas_projector_serialization",
        "runtime_surface": "@tiangong-lca/cli@0.0.25 build-resulting-process",
        "replacement_count": len(changed_paths),
        "numeric_replacement_count": numeric_replacement_count,
        "singleton_wrapper_replacement_count": singleton_wrapper_replacement_count,
        "changed_paths": changed_paths,
        "resulting_process_count": len(normalized),
        "scientific_values_changed": False,
        "exchange_semantics_changed": False,
    }


def verify_minimum_comparison_native_execution(
    compiled_inputs: Mapping[str, object], native_evidence: Mapping[str, object]
) -> JsonObject:
    """Fail closed unless every supported local native gate passed exactly."""

    compiled = _object(compiled_inputs, "compiled native inputs")
    native = _object(native_evidence, "native execution evidence")
    if (
        compiled.get("contract") != NATIVE_INPUTS_CONTRACT
        or compiled.get("status")
        != "minimum_lifepo4_comparison_native_inputs_compiled"
        or compiled.get("new_flow_count") != 2
        or compiled.get("process_count") != 13
        or compiled.get("lcia_method_count") != 1
        or compiled.get("lifecyclemodel_count") != 2
    ):
        raise ValueError("compiled native input contract drifted")

    count_gates = (
        ("flow_validation", 2, "total", "valid", "invalid"),
        ("process_validation", 13, "total", "valid", "invalid"),
        ("lifecyclemodel_validation", 2, "total", "valid", "invalid"),
        ("resulting_process_validation", 2, "total", "valid", "invalid"),
    )
    for gate, expected, total_key, valid_key, invalid_key in count_gates:
        report = _object(native.get(gate), gate)
        counts = _object(report.get("counts"), f"{gate} counts")
        if (
            counts.get(total_key) != expected
            or counts.get(valid_key) != expected
            or counts.get(invalid_key) != 0
        ):
            raise ValueError(f"{gate} did not accept exact membership")

    qa_gates = (
        ("flow_qa", "flow_count", 2),
        ("process_qa", "process_count", 13),
        ("resulting_process_qa", "process_count", 2),
    )
    for gate, count_key, expected in qa_gates:
        report = _object(native.get(gate), gate)
        if report.get(count_key) != expected or report.get("blocker_count") != 0:
            raise ValueError(f"{gate} contains a blocker or membership drift")

    save_gates = (
        ("flow_save_draft", 2),
        ("process_save_draft", 13),
        ("lifecyclemodel_save_draft", 2),
        ("resulting_process_save_draft", 2),
    )
    for gate, expected in save_gates:
        report = _object(native.get(gate), gate)
        counts = _object(report.get("counts"), f"{gate} counts")
        if (
            counts.get("prepared") != expected
            or counts.get("executed") != 0
            or counts.get("failed") != 0
        ):
            raise ValueError(f"{gate} crossed or failed the dry-run boundary")

    lcia = _object(native.get("lcia_method_validation"), "LCIAMethod validation")
    if (
        lcia.get("valid") is not True
        or lcia.get("validator") != "@tiangong-lca/tidas-sdk/LCIAMethodSchema"
        or lcia.get("issue_count") != 0
    ):
        raise ValueError("pinned TIDAS SDK LCIAMethod validation failed")

    graph = _object(native.get("lifecyclemodel_graph"), "LifecycleModel graph")
    graph_counts = _object(graph.get("counts"), "LifecycleModel graph counts")
    if graph_counts != {"models": 2, "nodes": 16, "edges": 15, "findings": 0}:
        raise ValueError("LifecycleModel graph topology drifted")
    graph_models = {
        str(row.get("id")): row
        for row in _list(graph.get("models"), "LifecycleModel graph models")
        if isinstance(row, dict)
    }
    requests = [
        _object(value, "projection request")
        for value in _list(compiled.get("projection_requests"), "projection requests")
    ]
    if len(requests) != 2:
        raise ValueError("two compiled projection requests are required")
    request_by_route = {
        _text(row.get("route_id"), "projection route"): row for row in requests
    }
    if set(request_by_route) != {"solid-state", "hydrothermal"}:
        raise ValueError("projection request route membership drifted")
    for route_id, row in request_by_route.items():
        model_id = _text(row.get("model_id"), f"{route_id} model UUID")
        model_graph = _object(graph_models.get(model_id), f"{route_id} graph")
        if (
            model_graph.get("node_count") != row.get("expected_node_count")
            or model_graph.get("edge_count") != row.get("expected_edge_count")
            or model_graph.get("finding_count") != 0
        ):
            raise ValueError(f"{route_id} native graph drifted")
    lifecycle_qa = _object(
        native.get("lifecyclemodel_qa"), "LifecycleModel QA"
    )
    if (
        lifecycle_qa.get("model_count") != 2
        or lifecycle_qa.get("finding_count") != 0
        or _object(
            lifecycle_qa.get("severity_counts"), "LifecycleModel severity counts"
        ).get("error")
        != 0
    ):
        raise ValueError("LifecycleModel QA failed")

    projections = {
        str(row.get("route_id")): row
        for row in _list(native.get("projections"), "native projections")
        if isinstance(row, dict)
    }
    if set(projections) != {"solid-state", "hydrothermal"}:
        raise ValueError("native projection route membership drifted")
    reference_flow_ids = set()
    for route_id, expected in request_by_route.items():
        projection = _object(projections.get(route_id), f"{route_id} projection")
        if (
            projection.get("model_id") != expected.get("model_id")
            or projection.get("resulting_process_id")
            != expected.get("resulting_process_id")
            or projection.get("node_count") != expected.get("expected_node_count")
            or projection.get("edge_count") != expected.get("expected_edge_count")
            or projection.get("remote_resolution_count") != 0
            or projection.get("reference_flow_id")
            != expected.get("reference_product_flow_id")
            or projection.get("reference_amount") != 1
        ):
            raise ValueError(f"{route_id} projection drifted or used remote resolution")
        reference_flow_ids.add(str(projection.get("reference_flow_id")))
    if len(reference_flow_ids) != 1:
        raise ValueError("projections do not share one reference product Flow")

    normalization = _object(native.get("normalization"), "normalization evidence")
    if (
        normalization.get("kind") != "lossless_tidas_projector_serialization"
        or normalization.get("scientific_values_changed") is not False
        or normalization.get("resulting_process_count") != 2
    ):
        raise ValueError("resulting Process normalization changed scientific values")
    runtime = _object(native.get("runtime"), "runtime evidence")
    before = _text(runtime.get("tree_sha256_before"), "runtime SHA before")
    after = _text(runtime.get("tree_sha256_after"), "runtime SHA after")
    if (
        len(before) != 64
        or before != after
        or runtime.get("unchanged") is not True
    ):
        raise ValueError("pinned runtime changed during native execution")
    if native.get("native_invocation_executed_count") != 16:
        raise ValueError("native invocation count drifted")
    for key in (
        "network_read_executed_count",
        "remote_write_executed_count",
        "publication_executed_count",
        "comparison_executed_count",
        "lcia_executed_count",
    ):
        if native.get(key) != 0:
            raise ValueError(f"native execution crossed the side-effect boundary: {key}")
    return {
        "schema_version": 1,
        "contract": f"{CONTRACT}_native_execution",
        "status": "minimum_lifepo4_comparison_model_materialized",
        "input_version": compiled.get("input_version"),
        "native_validated_entity_count": 20,
        "materialized_graph_entity_count": 18,
        "resulting_process_count": 2,
        "retained_process_qa_finding_count": _object(
            native.get("process_qa"), "Process QA"
        ).get("rule_finding_count", 0),
        "general_corpus_release_frozen": True,
        "unmaterialized_release_record_count": 1453,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "next_internal_route": LCIA_NEXT_ROUTE,
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _json_bytes(value)
    path.write_bytes(content)
    if path.read_bytes() != content:
        raise RuntimeError(f"artifact readback drifted: {path}")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = b"".join(_json_bytes(dict(row)) for row in rows)
    path.write_bytes(content)
    if path.read_bytes() != content:
        raise RuntimeError(f"artifact readback drifted: {path}")


def _read_json(path: Path, label: str) -> JsonObject:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} is not valid JSON: {path}") from error
    return _object(value, label)


def _run_local_json_command(
    command: Sequence[str], cwd: Path, trace_path: Path
) -> JsonObject:
    environment = dict(os.environ)
    for key in tuple(environment):
        upper = key.upper()
        if (
            upper.startswith(("SUPABASE_", "TIANGONG_"))
            or upper in {"OPENAI_API_KEY", "ANTHROPIC_API_KEY"}
        ):
            environment.pop(key, None)
    environment.update(
        {
            "npm_config_offline": "true",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "http://127.0.0.1:9",
            "NO_PROXY": "*",
        }
    )
    completed = subprocess.run(
        list(command),
        cwd=str(cwd),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    trace = {
        "command": list(command),
        "cwd": str(cwd),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "credentials_removed": True,
        "network_proxy_fail_closed": True,
    }
    _write_json(trace_path, trace)
    if completed.returncode != 0:
        raise RuntimeError(
            "pinned local TianGong command failed: "
            + " ".join(command)
            + f"\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("native command did not return one JSON object") from error
    return _object(value, "native command result")


def _reference_product(process: Mapping[str, object]) -> Tuple[str, object]:
    root = _object(process.get("processDataSet"), "resulting processDataSet")
    information = _object(
        root.get("processInformation"), "resulting Process information"
    )
    quantitative = _object(
        information.get("quantitativeReference"), "resulting quantitative reference"
    )
    reference_id = str(quantitative.get("referenceToReferenceFlow", ""))
    wrapper = _object(root.get("exchanges"), "resulting Process exchanges")
    raw = wrapper.get("exchange")
    rows = raw if isinstance(raw, list) else [raw]
    reference = next(
        (
            _object(value, "resulting exchange")
            for value in rows
            if isinstance(value, dict)
            and str(value.get("@dataSetInternalID", "")) == reference_id
        ),
        None,
    )
    if reference is None or reference.get("exchangeDirection") != "Output":
        raise ValueError("resulting Process reference Output is missing")
    flow = _object(
        reference.get("referenceToFlowDataSet"), "resulting reference Flow"
    )
    amount = Decimal(str(reference.get("meanAmount")))
    if not amount.is_finite():
        raise ValueError("resulting reference amount is invalid")
    normalized_amount: object = int(amount) if amount == int(amount) else float(amount)
    return _text(flow.get("@refObjectId"), "resulting reference Flow UUID"), normalized_amount


def _artifact_bindings(root: Path, excluded: Sequence[Path]) -> List[JsonObject]:
    excluded_resolved = {path.resolve() for path in excluded}
    rows: List[JsonObject] = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        if path.resolve() in excluded_resolved:
            continue
        content = path.read_bytes()
        rows.append(
            {
                "path": str(path.relative_to(root)),
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
        )
    for row in rows:
        path = root / str(row["path"])
        content = path.read_bytes()
        if (
            hashlib.sha256(content).hexdigest() != row["sha256"]
            or len(content) != row["bytes"]
        ):
            raise RuntimeError("package artifact changed during manifest compilation")
    return rows


def execute_minimum_comparison_model_native_gates(
    model_report: Mapping[str, object],
    graph_report: Mapping[str, object],
    workspace: Path,
    out_dir: Path,
    runtime_root: Path,
    runtime_manifest_path: Path,
) -> JsonObject:
    """Materialize the exact local package and run every supported native gate."""

    root = Path(out_dir)
    if root.exists() and any(root.iterdir()):
        raise ValueError("native model output directory must be new and empty")
    root.mkdir(parents=True, exist_ok=True)
    runtime_root = Path(runtime_root).resolve()
    manifest = _read_json(Path(runtime_manifest_path), "portable runtime manifest")
    try:
        from .portable_runtime_closure import verify_extracted_runtime_root
    except ImportError:  # Direct activity-script execution from the project workspace.
        from portable_runtime_closure import verify_extracted_runtime_root  # type: ignore

    runtime_before = verify_extracted_runtime_root(runtime_root, manifest)
    runtime_tree = _text(
        runtime_before.get("verified_tree_sha256"), "verified runtime tree SHA-256"
    )
    compiled = compile_minimum_comparison_native_inputs(
        model_report, graph_report, workspace
    )
    inputs = root / "inputs"
    outputs = root / "outputs"
    _write_json(inputs / "compiled-model.json", model_report)
    _write_json(inputs / "compiled-native-inputs.json", compiled)
    _write_jsonl(inputs / "new-flows.jsonl", compiled["new_flow_rows"])  # type: ignore[arg-type]
    _write_jsonl(inputs / "processes.jsonl", compiled["process_rows"])  # type: ignore[arg-type]
    _write_jsonl(inputs / "lifecyclemodels.jsonl", compiled["lifecyclemodel_rows"])  # type: ignore[arg-type]
    _write_json(inputs / "lciamethod.json", compiled["lcia_method_row"])
    _write_jsonl(
        inputs / "reused-source-identities.jsonl",
        ({"source_id": source_id, "version": DATASET_VERSION} for source_id in compiled["reused_source_ids"]),  # type: ignore[union-attr]
    )
    _write_jsonl(inputs / "parameter-ledger.jsonl", compiled["parameter_ledger"])  # type: ignore[arg-type]
    _write_json(inputs / "allocation-contract.json", compiled["allocation_contract"])
    for process in compiled["process_rows"]:  # type: ignore[union-attr]
        row = _object(process, "Process input")
        process_id = _process_uuid(row, "Process input")
        _write_json(
            inputs / "processes" / f"{process_id}_{DATASET_VERSION}.json", row
        )
    model_rows = compiled["lifecyclemodel_rows"]  # type: ignore[assignment]
    projection_model_rows = compiled[
        "projection_lifecyclemodel_rows"
    ]  # type: ignore[assignment]
    projection_inputs = compiled["projection_requests"]  # type: ignore[assignment]
    if (
        not isinstance(model_rows, list)
        or not isinstance(projection_model_rows, list)
        or not isinstance(projection_inputs, list)
    ):
        raise ValueError("native LifecycleModel inputs are malformed")
    model_by_route = {
        str(row.get("route_id")): model_row
        for row, model_row in zip(projection_inputs, model_rows)
        if isinstance(row, dict) and isinstance(model_row, dict)
    }
    if set(model_by_route) != {"solid-state", "hydrothermal"}:
        raise ValueError("native LifecycleModel route rows drifted")
    projection_model_by_route = {
        str(row.get("route_id")): model_row
        for row, model_row in zip(projection_inputs, projection_model_rows)
        if isinstance(row, dict) and isinstance(model_row, dict)
    }
    if set(projection_model_by_route) != {"solid-state", "hydrothermal"}:
        raise ValueError("projector compatibility LifecycleModel rows drifted")
    _write_json(
        inputs / "projection-lifecyclemodel-compatibility.json",
        compiled["projection_lifecyclemodel_compatibility"],
    )
    for value in projection_inputs:
        request_row = _object(value, "projection input")
        route_id = _text(request_row.get("route_id"), "projection route")
        _write_json(
            inputs / "lifecyclemodels" / f"{route_id}.json",
            model_by_route[route_id],
        )
        _write_json(
            inputs / "projection-lifecyclemodels" / f"{route_id}.json",
            projection_model_by_route[route_id],
        )
        _write_json(
            inputs / "projection-requests" / f"{route_id}.json",
            request_row["request"],
        )

    node_path = runtime_root / "bin/node"
    cli_entry = (
        runtime_root
        / "npm-runtime/node_modules/@tiangong-lca/cli/bin/tiangong-lca.js"
    )
    sdk_path = (
        runtime_root / "npm-runtime/node_modules/@tiangong-lca/tidas-sdk"
    )
    if not node_path.is_file() or not cli_entry.is_file() or not sdk_path.is_dir():
        raise ValueError("verified portable TianGong runtime surface is incomplete")
    commands = minimum_comparison_native_commands(node_path, cli_entry, root)
    trace_root = root / "native/command-trace"
    command_results: List[JsonObject] = []
    for index, command in enumerate(commands[:10], start=1):
        command_results.append(
            _run_local_json_command(
                command, root, trace_root / f"{index:02d}.json"
            )
        )
    sdk_script = (
        "const fs=require('fs');"
        "const sdk=require(process.argv[2]);"
        "const value=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));"
        "const parsed=sdk.LCIAMethodSchema.safeParse(value);"
        "if(!parsed.success){console.log(JSON.stringify({valid:false,validator:'@tiangong-lca/tidas-sdk/LCIAMethodSchema',issue_count:parsed.error.issues.length,issues:parsed.error.issues}));process.exit(2);};"
        "console.log(JSON.stringify({valid:true,validator:'@tiangong-lca/tidas-sdk/LCIAMethodSchema',issue_count:0}));"
    )
    lcia_validation = _run_local_json_command(
        [
            str(node_path),
            "-e",
            sdk_script,
            str(inputs / "lciamethod.json"),
            str(sdk_path),
        ],
        root,
        trace_root / "11.json",
    )
    projection_summaries: List[JsonObject] = []
    raw_resulting: List[JsonObject] = []
    request_by_route = {
        str(row["route_id"]): row
        for row in projection_inputs
        if isinstance(row, dict)
    }
    for command_index, command in enumerate(commands[10:12], start=12):
        command_results.append(
            _run_local_json_command(
                command, root, trace_root / f"{command_index:02d}.json"
            )
        )
        route_id = "solid-state" if command_index == 12 else "hydrothermal"
        bundle = _read_json(
            root
            / "native/projection"
            / route_id
            / "process-projection-bundle.json",
            f"{route_id} projection bundle",
        )
        report = _object(bundle.get("report"), f"{route_id} projection report")
        source_model = _object(
            bundle.get("source_model"), f"{route_id} projection source model"
        )
        resolution = _object(
            report.get("resolved_process_summary"),
            f"{route_id} projection resolution",
        )
        projected = [
            _object(value, f"{route_id} projected Process wrapper")
            for value in _list(
                bundle.get("projected_processes"),
                f"{route_id} projected Processes",
            )
        ]
        if len(projected) != 1:
            raise ValueError(f"{route_id} did not emit one resulting Process")
        process = _object(
            projected[0].get("json_ordered"), f"{route_id} projected Process"
        )
        result_id = _process_uuid(process, f"{route_id} projected Process")
        reference_flow_id, reference_amount = _reference_product(process)
        expected = _object(request_by_route.get(route_id), f"{route_id} request")
        projection_summaries.append(
            {
                "route_id": route_id,
                "model_id": source_model.get("id"),
                "resulting_process_id": result_id,
                "node_count": report.get("node_count"),
                "edge_count": report.get("edge_count"),
                "required_process_count": resolution.get("required_process_count"),
                "resolved_process_count": resolution.get("resolved_process_count"),
                "remote_resolution_count": resolution.get(
                    "remote_resolution_count"
                ),
                "reference_flow_id": reference_flow_id,
                "reference_amount": reference_amount,
                "projection_signature": report.get("projection_signature"),
                "expected_request_sha256": expected.get("request_sha256"),
            }
        )
        raw_resulting.append(process)
    _write_jsonl(outputs / "raw-resulting-processes.jsonl", raw_resulting)
    normalized, normalization = normalize_projected_process_rows(raw_resulting)
    _write_jsonl(outputs / "resulting-processes.jsonl", normalized)
    _write_json(outputs / "resulting-process-normalization.json", normalization)
    for command_index, command in enumerate(commands[12:], start=14):
        command_results.append(
            _run_local_json_command(
                command, root, trace_root / f"{command_index:02d}.json"
            )
        )

    runtime_after = verify_extracted_runtime_root(runtime_root, manifest)
    after_tree = _text(
        runtime_after.get("verified_tree_sha256"),
        "post-execution runtime tree SHA-256",
    )
    evidence: JsonObject = {
        "flow_validation": command_results[0],
        "flow_qa": command_results[1],
        "flow_save_draft": command_results[2],
        "process_validation": command_results[3],
        "process_qa": command_results[4],
        "process_save_draft": command_results[5],
        "lcia_method_validation": lcia_validation,
        "lifecyclemodel_validation": command_results[6],
        "lifecyclemodel_graph": command_results[7],
        "lifecyclemodel_qa": command_results[8],
        "lifecyclemodel_save_draft": command_results[9],
        "projections": projection_summaries,
        "normalization": normalization,
        "resulting_process_validation": command_results[12],
        "resulting_process_qa": command_results[13],
        "resulting_process_save_draft": command_results[14],
        "runtime": {
            "tree_sha256_before": runtime_tree,
            "tree_sha256_after": after_tree,
            "verified_file_count": runtime_after.get("verified_file_count"),
            "verified_symlink_count": runtime_after.get("verified_symlink_count"),
            "unchanged": runtime_before == runtime_after,
        },
        "native_invocation_executed_count": 16,
        "network_read_executed_count": 0,
        "remote_write_executed_count": 0,
        "publication_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
    }
    verification = verify_minimum_comparison_native_execution(compiled, evidence)
    _write_json(outputs / "native-execution-evidence.json", evidence)
    _write_json(outputs / "native-execution-verification.json", verification)
    report_path = outputs / "minimum-lifepo4-comparison-model.json"
    manifest_path = outputs / "package-manifest.json"
    bindings = _artifact_bindings(root, [report_path, manifest_path])
    package_manifest = {
        "schema_version": 1,
        "contract": f"{CONTRACT}_package_manifest",
        "input_version": compiled["input_version"],
        "runtime_tree_sha256": runtime_tree,
        "artifact_count": len(bindings),
        "artifacts": bindings,
    }
    _write_json(manifest_path, package_manifest)
    manifest_content = manifest_path.read_bytes()
    final = {
        **verification,
        "model_input_version": compiled["model_input_version"],
        "graph_input_version": compiled["graph_input_version"],
        "runtime_tree_sha256": runtime_tree,
        "package_manifest": {
            "path": str(manifest_path.relative_to(root)),
            "sha256": hashlib.sha256(manifest_content).hexdigest(),
            "bytes": len(manifest_content),
            "artifact_count": len(bindings),
        },
        "native_invocation_executed_count": 16,
        "network_read_executed_count": 0,
        "remote_write_executed_count": 0,
        "publication_executed_count": 0,
        "open_risks": [
            "Provider intensities remain bounded screening assumptions pending stronger background inventories.",
            "The two current route Process rows retain native material-balance warnings.",
            "TianGong 0.0.25 exposes no LCIA calculation command; the next beat must execute the content-bound adapter.",
        ],
    }
    _write_json(report_path, final)
    if _read_json(report_path, "minimum comparison model report") != final:
        raise RuntimeError("minimum comparison model report readback drifted")
    return final


def verify_minimum_comparison_model(report: Mapping[str, object]) -> JsonObject:
    """Fail closed on membership, topology, parameter or allocation drift."""

    row = _object(report, "minimum comparison model")
    if (
        row.get("contract") != CONTRACT
        or row.get("status") != STATUS
        or row.get("entity_counts") != ENTITY_COUNTS
        or row.get("graph_node_count") != 33
        or row.get("graph_edge_count") != 55
        or row.get("graph_nonmember_materialization_count") != 0
        or row.get("general_corpus_release_frozen") is not True
        or row.get("unmaterialized_release_record_count") != 1453
    ):
        raise ValueError("minimum comparison model membership drifted")
    providers = [_object(value, "Provider process") for value in _list(row.get("provider_processes"), "Provider processes")]
    flows = [_object(value, "new Flow") for value in _list(row.get("new_flows"), "new Flows")]
    models = [_object(value, "LifecycleModel") for value in _list(row.get("lifecyclemodels"), "LifecycleModels")]
    if (
        len(providers) != 10
        or len({value.get("node_id") for value in providers}) != 10
        or len(flows) != 2
        or len({value.get("flow_id") for value in flows}) != 2
        or len(models) != 2
        or {value.get("route_id") for value in models}
        != {"solid-state", "hydrothermal"}
    ):
        raise ValueError("minimum comparison model membership cardinality drifted")
    expected_topology = {"solid-state": (7, 7), "hydrothermal": (9, 8)}
    for model in models:
        route = str(model["route_id"])
        if (
            (model.get("native_node_count"), model.get("native_edge_count"))
            != expected_topology[route]
            or model.get("route_process_multiplier") != ROUTE_MULTIPLIER
            or model.get("functional_unit_amount") != 1
            or model.get("functional_unit_unit") != "kg"
            or len(model.get("connections", [])) != expected_topology[route][1]
        ):
            raise ValueError(f"{route} LifecycleModel topology drifted")
        connection_keys = {
            (
                value.get("from_instance_id"),
                value.get("to_instance_id"),
                value.get("flow_id"),
            )
            for value in model["connections"]
            if isinstance(value, dict)
        }
        if len(connection_keys) != expected_topology[route][1]:
            raise ValueError(f"{route} LifecycleModel connection drifted")
    parameters = [_object(value, "model parameter") for value in _list(row.get("parameter_ledger"), "parameter ledger")]
    required = {
        "parameter_id",
        "baseline",
        "lower",
        "upper",
        "unit",
        "distribution_family",
        "derivation_class",
        "source_or_assumption_refs",
        "can_change_comparative_direction",
        "risk_id",
        "missing_value_interpreted_as_zero",
    }
    if len(parameters) != 14 or len({value.get("parameter_id") for value in parameters}) != 14:
        raise ValueError("model parameter membership drifted")
    for parameter in parameters:
        if not required.issubset(parameter):
            raise ValueError("model parameter contract is incomplete")
        baseline = parameter["baseline"]
        lower = parameter["lower"]
        upper = parameter["upper"]
        if (
            isinstance(baseline, bool)
            or not isinstance(baseline, (int, float))
            or not isinstance(lower, (int, float))
            or not isinstance(upper, (int, float))
            or not lower <= baseline <= upper
            or not upper > lower
            or not parameter["unit"]
            or not isinstance(parameter["source_or_assumption_refs"], list)
            or not parameter["source_or_assumption_refs"]
            or parameter["missing_value_interpreted_as_zero"] is not False
        ):
            raise ValueError("model parameter bounds or provenance drifted")
    allocation = _object(row.get("allocation_contract"), "allocation contract")
    if (
        row.get("allocation_application_count") != 1
        or allocation.get("route_id") != "hydrothermal"
        or allocation.get("mass_share") != ALLOCATION_FACTOR
        or allocation.get("application_stage") != "lcia_adapter"
        or allocation.get("physical_route_process_multiplier") != ROUTE_MULTIPLIER
    ):
        raise ValueError("hydrothermal allocation application drifted")
    zero_effects = (
        "native_invocation_executed_count",
        "network_read_executed_count",
        "remote_write_executed_count",
        "publication_executed_count",
        "comparison_executed_count",
        "lcia_executed_count",
    )
    if any(row.get(key) != 0 for key in zero_effects):
        raise ValueError("model compiler executed a prohibited effect")
    if row.get("next_internal_route") != NEXT_ROUTE:
        raise ValueError("minimum comparison model route drifted")
    return {
        "schema_version": 1,
        "contract": f"{CONTRACT}_verification",
        "status": "accepted",
        "decision_count": 7,
        "accepted_decision_count": 7,
        "input_version": row.get("input_version"),
        "next_internal_route": NEXT_ROUTE,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph-report", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--runtime-manifest", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    graph = _read_json(args.graph_report, "minimum comparison graph report")
    model = compile_minimum_comparison_model(graph)
    report = execute_minimum_comparison_model_native_gates(
        model,
        graph,
        args.workspace,
        args.out_dir,
        args.runtime_root,
        args.runtime_manifest,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
