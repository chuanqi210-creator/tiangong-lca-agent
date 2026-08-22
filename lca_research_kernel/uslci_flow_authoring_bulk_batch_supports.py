"""Resolve exact classification and support contracts for a Flow authoring batch."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lca_research_kernel.uslci_flow_authoring_canary_supports import (
    RUNTIME_FILES,
    validate_native_classification_probe,
    verify_runtime_files,
)


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_flow_authoring_bulk_batch_supports/v1"
LOGIC_VERSION = "2026-07-18.7"
POLICY_VERSION = "2026-07-18.1"
BATCH_INDEX = 2
BATCH_KEY = "0773c3803a99a87e"
NEXT_ROUTE = "compile-uslci-flow-authoring-bulk-batch-2-saved-plans"
MASS_PROPERTY_ID = "93a60a56-a3c8-11da-a746-0800200b9a66"
MASS_UNIT_ID = "20aadc24-a391-41cf-b340-3e4529f44bde"
AREA_TIME_PROPERTY_ID = "93a60a56-a3c8-21da-a746-0800200c9a66"
AREA_TIME_UNIT_ID = "c7266b67-4ea2-457f-b391-9b94e26e195a"
ENERGY_PROPERTY_ID = "f6811440-ee37-11de-8a39-0800200c9a66"
ENERGY_UNIT_ID = "52765a6c-3896-43c2-b2f4-c679acf13efe"
EXPECTED_INPUT_NAMES = (
    "authoring_plan_report",
    "authoring_intents",
    "authoring_batches",
    "elementary_source_envelope",
    "support_report",
    "support_contracts",
    "runtime_evidence",
    "route_authority",
    "runtime_report",
    "runtime_manifest",
)

CATEGORY_POLICY = {
    "Elementary flows/emission/water": {
        "code": "1.1.3",
        "leaf_label": "Emissions to water, unspecified",
        "confidence": "high",
        "review_priority": None,
        "review_reason": None,
        "rationale": (
            "The source identifies water but no fresh/sea compartment; the native "
            "unspecified-water leaf preserves that resolution."
        ),
    },
    "Elementary flows/emission/ground": {
        "code": "1.2.3",
        "leaf_label": "Emissions to soil, unspecified",
        "confidence": "high",
        "review_priority": "low",
        "review_reason": (
            "USLCI labels the compartment ground while TianGong names the nearest "
            "unspecified emission compartment soil."
        ),
        "rationale": (
            "The source identifies a ground emission without agricultural or "
            "non-agricultural detail; the native unspecified-soil leaf is the "
            "least-assumptive available path."
        ),
    },
    "Elementary flows/emission/air": {
        "code": "1.3.4",
        "leaf_label": "Emissions to air, unspecified",
        "confidence": "high",
        "review_priority": None,
        "review_reason": None,
        "rationale": (
            "The source identifies air but no urban/high-stack/upper-atmosphere "
            "compartment; the native unspecified-air leaf preserves that resolution."
        ),
    },
}

SUPPORT_PROPERTY_SPECS = {
    MASS_PROPERTY_ID: {
        "flow_property_name": "Mass",
        "flow_property_version": "03.00.003",
        "reference_unit": "kg",
        "unit_id": MASS_UNIT_ID,
        "unit_group_id": "93a60a57-a4c8-11da-a746-0800200c9a66",
        "unit_group_name": "Units of mass",
        "unit_group_version": "03.00.003",
        "native_addressability_kind": "native_builtin_and_explicit_reference",
        "source_intent_count": 1442,
        "intent_support_status": "native_builtin_mass_local_probe_required",
        "assignment_status": "resolved_by_accepted_mass_contract_reuse",
    },
    AREA_TIME_PROPERTY_ID: {
        "flow_property_name": "Area*Time",
        "flow_property_version": "03.00.003",
        "reference_unit": "m2*a",
        "unit_id": AREA_TIME_UNIT_ID,
        "unit_group_id": "93a60a57-a3c8-20da-a746-0800200c9a66",
        "unit_group_name": "Units of area*time",
        "unit_group_version": "03.00.003",
        "native_addressability_kind": "native_explicit_reference",
        "source_intent_count": 5,
        "intent_support_status": "source_observed_local_addressability_unverified",
        "assignment_status": "resolved_by_accepted_explicit_contract_reuse",
    },
    ENERGY_PROPERTY_ID: {
        "flow_property_name": "Energy",
        "flow_property_version": "00.00.000",
        "reference_unit": "MJ",
        "unit_id": ENERGY_UNIT_ID,
        "unit_group_id": "93a60a57-a3c8-11da-a746-0800200c9a66",
        "unit_group_name": "Units of energy",
        "unit_group_version": "03.00.003",
        "native_addressability_kind": "native_explicit_reference",
        "source_intent_count": 9,
        "intent_support_status": "source_observed_local_addressability_unverified",
        "assignment_status": "resolved_by_accepted_explicit_contract_reuse",
    },
}

BATCH_15_RESOURCE_POLICIES = {
    "land-occupation-from-area-time": {
        "code": "3.1",
        "leaf_label": "Land occupation",
        "confidence": "medium",
        "review_priority": "medium",
        "review_reason": (
            "Area*time supports occupation, but the source label Land use does not "
            "explicitly distinguish occupation from transformation."
        ),
        "rationale": (
            "An input resource named Land use with Area*Time is mapped to the native "
            "Land occupation branch while retaining the interpretation risk."
        ),
        "risk_type": "land_use_interpretation",
        "risk_id_prefix": "land-use-to-occupation",
    },
    "coal-energy-from-ground": {
        "code": "2.1.3",
        "leaf_label": "Non-renewable energy resources from ground",
        "confidence": "high",
        "review_priority": None,
        "review_reason": None,
        "rationale": (
            "Coal is a non-renewable ground resource and the source FlowProperty is "
            "Energy, which directly identifies the native energy-resource leaf."
        ),
        "risk_type": None,
        "risk_id_prefix": None,
    },
}

BATCH_16_ELEMENT_NAMES = frozenset(
    {
        "Beryllium",
        "Boron",
        "Cerium",
        "Cobalt",
        "Copper",
        "Gold",
        "Iron",
        "Lead",
        "Lithium",
        "Magnesium",
        "Molybdenum",
        "Nickel",
        "Palladium",
        "Platinum",
        "Silver",
        "Titanium",
        "Zinc",
        "Zirconium",
    }
)
BATCH_16_MATERIAL_NAMES = frozenset(
    {
        "Barite",
        "Bentonite",
        "Clay",
        "Diatomite",
        "Feldspar group",
        "Fluorite",
        "Garnet group",
        "Gravel",
        "Gypsum",
        "Kaolin",
        "Kyanite",
        "Limestone",
        "Mica",
        "Perlite",
        "Phosphate ore",
        "Pumice",
        "Rhenium ore",
        "Sand",
        "Sodium carbonate",
        "Sodium chloride",
        "Stone",
        "Talc",
        "Vermiculite",
        "Zeolites",
    }
)
BATCH_16_RESOURCE_POLICIES = {
    "nonrenewable-material-from-ground": {
        "code": "2.1.1",
        "leaf_label": "Non-renewable material resources from ground",
        "confidence": "medium",
        "review_priority": "medium",
        "review_reason": "Mass and the mineral/material name are explicit; non-renewability is an inferred resource property retained for review.",
        "rationale": "The exact material/mineral allowlist plus Mass and the ground compartment selects the native non-renewable material branch.",
        "risk_type": "resource_renewability_inference",
        "risk_id_prefix": "ground-material-nonrenewable",
    },
    "nonrenewable-element-from-ground": {
        "code": "2.1.2",
        "leaf_label": "Non-renewable element resources from ground",
        "confidence": "medium",
        "review_priority": "medium",
        "review_reason": "The element name and ground compartment are explicit; non-renewability is retained as a scientific inference.",
        "rationale": "The exact element allowlist plus Mass and the ground compartment selects the native non-renewable element branch.",
        "risk_type": "resource_renewability_inference",
        "risk_id_prefix": "ground-element-nonrenewable",
    },
    "renewable-material-from-water": {
        "code": "2.2.6",
        "leaf_label": "Renewable material resources from water",
        "confidence": "medium",
        "review_priority": "medium",
        "review_reason": "Water and Mass identify a material resource, but renewability can depend on the hydrological source and withdrawal rate.",
        "rationale": "Fresh or saline water with Mass in the source water compartment maps to the renewable material-water branch with retained qualification.",
        "risk_type": "resource_renewability_inference",
        "risk_id_prefix": "water-material-renewable",
    },
    "renewable-biotic-material-from-ground": {
        "code": "2.1.6",
        "leaf_label": "Renewable material resources from ground",
        "confidence": "medium",
        "review_priority": "medium",
        "review_reason": "Biomass and Mass support renewable material, but the source biotic compartment does not explicitly state the native ground origin.",
        "rationale": "Generic Biomass is conservatively routed to renewable material from ground while preserving the compartment translation risk.",
        "risk_type": "resource_compartment_interpretation",
        "risk_id_prefix": "biotic-material-to-ground",
    },
    "crude-oil-energy-from-water": {
        "code": "2.2.3",
        "leaf_label": "Non-renewable energy resources from water",
        "confidence": "medium",
        "review_priority": "medium",
        "review_reason": "The selected Energy observation maps Crude oil to non-renewable energy from the source water compartment; the same source Flow also carries an excluded Mass observation.",
        "rationale": "Crude oil, Energy and the exact source water compartment select the non-renewable energy-water branch without discarding the alternate-property evidence.",
        "risk_type": "multiple_source_flow_property_observations",
        "risk_id_prefix": "crude-oil-energy-variant",
    },
    "renewable-energy-from-air": {
        "code": "2.3.5",
        "leaf_label": "Renewable energy resources from air",
        "confidence": "medium",
        "review_priority": "medium",
        "review_reason": "Solar energy or ambient heat is treated as renewable, while the source records Energy and air but no explicit renewability field.",
        "rationale": "The exact Solar/Heat allowlist plus Energy and source air compartment selects the renewable energy-air branch.",
        "risk_type": "resource_renewability_inference",
        "risk_id_prefix": "air-energy-renewable",
    },
    "hydro-energy-from-water": {
        "code": "2.2.5",
        "leaf_label": "Renewable energy resources from water",
        "confidence": "high",
        "review_priority": None,
        "review_reason": None,
        "rationale": "Hydro, Energy and the water compartment directly identify renewable energy from water.",
        "risk_type": None,
        "risk_id_prefix": None,
    },
    "peat-energy-from-ground": {
        "code": "2.1.3",
        "leaf_label": "Non-renewable energy resources from ground",
        "confidence": "medium",
        "review_priority": "medium",
        "review_reason": "Peat is treated as non-renewable for this LCA classification, but its renewal timescale is a retained scientific interpretation.",
        "rationale": "Peat, Energy and the ground compartment select the non-renewable ground-energy branch with timescale risk retained.",
        "risk_type": "resource_renewability_inference",
        "risk_id_prefix": "peat-nonrenewable",
    },
    "natural-gas-energy-from-air": {
        "code": "2.3.3",
        "leaf_label": "Non-renewable energy resources from air",
        "confidence": "high",
        "review_priority": None,
        "review_reason": None,
        "rationale": "Natural gas is a non-renewable energy resource and the exact source compartment is air.",
        "risk_type": None,
        "risk_id_prefix": None,
    },
}

BATCH_8_NATIVE_GATE_LOGIC_VERSION = "2026-07-18.3"
BATCH_9_NATIVE_GATE_LOGIC_VERSION = "2026-07-18.4"
CURRENT_NATIVE_GATE_LOGIC_VERSION = "2026-07-18.5"
NATIVE_AUTHORITY_ZERO_EFFECT_KEYS = (
    "network_read_executed_count",
    "remote_candidate_search_executed_count",
    "remote_flow_materialization_executed_count",
    "flow_creation_executed_count",
    "save_draft_executed_count",
    "process_creation_executed_count",
    "model_connection_executed_count",
    "model_mutation_executed_count",
    "external_write_executed_count",
    "publication_executed_count",
    "comparison_executed_count",
    "lcia_executed_count",
)
NATIVE_AUTHORITY_REQUIRED_BINDINGS = (
    "terminal_dag",
    "raw_artifact_manifest",
    "raw_artifact_archive",
    "unexpected_failures",
)
STRICT_NATIVE_ROUTE_AUTHORITIES = {
    10: {
        "logic_version": BATCH_9_NATIVE_GATE_LOGIC_VERSION,
        "input_version": "4e90d3fa09d69779",
        "saved_plan_set_version": "3c3a9424a7cb7a22",
        "execution_version": "56870a987f96fb17",
        "report_sha256": "655a58b06c565f423b16919f8db06673db13e4c8fc8a2647e46b996524dddb84",
        "archive_sha256": "679e7c3b8ae9bcdcecc604ccddabf7dd4bfc699f59601307953bb8ea702e95d5",
        "archive_bytes": 7024640,
        "review_marker_count": 8,
    },
    11: {
        "logic_version": CURRENT_NATIVE_GATE_LOGIC_VERSION,
        "input_version": "57105881f484c9cc",
        "saved_plan_set_version": "b238bd643791013c",
        "execution_version": "a8deb2b37461e3f7",
        "report_sha256": "1dcbb3c5e2fa9fb865c6e10b07461f2796ea9aed7aef4e220a907a667a161913",
        "archive_sha256": "0be9978f7a38ecbdf0c8ca20b5983866204db4c78906071cd089d881ac72b06a",
        "archive_bytes": 7024640,
        "review_marker_count": 14,
    },
    12: {
        "logic_version": CURRENT_NATIVE_GATE_LOGIC_VERSION,
        "input_version": "787421515ed8d58a",
        "saved_plan_set_version": "340858c063024f8b",
        "execution_version": "f93d909c6218b6e1",
        "report_sha256": "4be9110ffaa74fbbdab8207598a9744d4ce6c3dace3ef1ef11b2704f34ae41cd",
        "archive_sha256": "59b19585422ac2a726a8d3aec5bccd0d07e059042db0fcef8c0c28dcaca19fed",
        "archive_bytes": 7024640,
        "review_marker_count": 10,
    },
    13: {
        "logic_version": CURRENT_NATIVE_GATE_LOGIC_VERSION,
        "input_version": "bd3605f17db828f7",
        "saved_plan_set_version": "e21a6c5d8654c16a",
        "execution_version": "1170c3f1be7ed8ce",
        "report_sha256": "0d7c3419d2f7310c88020be9257e84d80abcaa429a779926fe066db00d1bd1db",
        "archive_sha256": "1e22485c2a1fcb99b31ea543c9e796a02c013104588de5211f496a61347c7a67",
        "archive_bytes": 7024640,
        "review_marker_count": 12,
    },
    14: {
        "logic_version": CURRENT_NATIVE_GATE_LOGIC_VERSION,
        "input_version": "462a14a70e8352db",
        "saved_plan_set_version": "9f872eb1c10cbad6",
        "execution_version": "988668c8c56e7400",
        "report_sha256": "2a3240bab1b88d302e43e1543fd8d4c3ab8f711ef09dab4bb1c946241ae376c3",
        "archive_sha256": "aceabc033f9632c43aaca73d9ceabdea6ca8dd77a3b64ec56b6250990daa946e",
        "archive_bytes": 7024640,
        "review_marker_count": 13,
    },
    15: {
        "logic_version": CURRENT_NATIVE_GATE_LOGIC_VERSION,
        "input_version": "60f168dc364bf9cd",
        "saved_plan_set_version": "624a32d07a2c8ed6",
        "execution_version": "0b80309731f4b0b4",
        "report_sha256": "2232bf0cb539314e62004d537a3ff8245ebe46a34fdbe1e83222c9bf92acb2cf",
        "archive_sha256": "cd4de5ba9a62985f6e498f15e469f0f353f0087b5c5a74623299a24e9e19a207",
        "archive_bytes": 7024640,
        "review_marker_count": 9,
    },
    16: {
        "logic_version": "2026-07-18.6",
        "input_version": "4b850db915dc5ff5",
        "saved_plan_set_version": "534c3aa6dcc0e7b6",
        "execution_version": "4c35d988ad94df48",
        "report_sha256": "38ac5ef1311a48a5a886d430e67630be262a189c23ac3a3c6e1eeacb58a479ec",
        "archive_sha256": "bdb2149a1856c7b0814f81b46583fb8f7807a36e88125c6317978fdb0d98bc73",
        "archive_bytes": 7014400,
        "review_marker_count": 10,
    },
}


def _policy_specs_for_batch(batch_index: int) -> Dict[str, JsonObject]:
    policies = {} if batch_index == 16 else {
        f"emission:{source_category}": {
            **dict(spec),
            "policy_id": f"emission:{source_category}",
            "source_category": source_category,
            "risk_type": "classification_terminology_mapping",
            "risk_id_prefix": "ground-to-soil",
        }
        for source_category, spec in CATEGORY_POLICY.items()
    }
    if batch_index == 15:
        for policy_id, spec in BATCH_15_RESOURCE_POLICIES.items():
            policies[policy_id] = {
                **dict(spec),
                "policy_id": policy_id,
                "source_category": None,
            }
    elif batch_index == 16:
        for policy_id, spec in BATCH_16_RESOURCE_POLICIES.items():
            policies[policy_id] = {
                **dict(spec),
                "policy_id": policy_id,
                "source_category": None,
            }
    return policies


def native_probe_codes_for_batch(batch_index: int) -> Tuple[str, ...]:
    return tuple(
        sorted(
            {str(spec["code"]) for spec in _policy_specs_for_batch(batch_index).values()}
        )
    )


def native_probe_filenames_for_batch(batch_index: int) -> Tuple[str, ...]:
    return tuple(
        f"native-probes/{index:02d}-flow-elementary-{code.replace('.', '-')}.json"
        for index, code in enumerate(native_probe_codes_for_batch(batch_index), start=1)
    )


def _classification_spec(
    intent: Mapping[str, object],
    observation: Mapping[str, object],
    *,
    batch_index: int,
) -> JsonObject:
    source_category = str(observation.get("source_category"))
    if (
        intent.get("coarse_source_classification") == "elementary_emission"
        and intent.get("flow_property_id") == MASS_PROPERTY_ID
        and observation.get("direction") == "Output"
        and source_category in CATEGORY_POLICY
    ):
        return _policy_specs_for_batch(batch_index)[f"emission:{source_category}"]
    if batch_index == 15 and intent.get("coarse_source_classification") == "elementary_resource":
        if (
            intent.get("source_flow_name") == "Land use"
            and intent.get("flow_property_id") == AREA_TIME_PROPERTY_ID
            and observation.get("direction") == "Input"
            and source_category.startswith("Elementary flows/resource/ground")
        ):
            return _policy_specs_for_batch(batch_index)["land-occupation-from-area-time"]
        if (
            intent.get("source_flow_name") == "Coal"
            and intent.get("flow_property_id") == ENERGY_PROPERTY_ID
            and observation.get("direction") == "Input"
            and source_category == "Elementary flows/resource/ground"
        ):
            return _policy_specs_for_batch(batch_index)["coal-energy-from-ground"]
    if (
        batch_index == 16
        and intent.get("coarse_source_classification") == "elementary_resource"
        and observation.get("direction") == "Input"
    ):
        name = str(intent.get("source_flow_name"))
        property_id = intent.get("flow_property_id")
        policies = _policy_specs_for_batch(batch_index)
        if property_id == MASS_PROPERTY_ID:
            if source_category == "Elementary flows/resource/ground":
                if name in BATCH_16_ELEMENT_NAMES:
                    return policies["nonrenewable-element-from-ground"]
                if name in BATCH_16_MATERIAL_NAMES:
                    return policies["nonrenewable-material-from-ground"]
            if source_category == "Elementary flows/resource/water" and name in {
                "Water, fresh",
                "Water, saline",
            }:
                return policies["renewable-material-from-water"]
            if source_category == "Elementary flows/resource/biotic" and name == "Biomass":
                return policies["renewable-biotic-material-from-ground"]
        if property_id == ENERGY_PROPERTY_ID:
            exact = {
                ("Crude oil", "Elementary flows/resource/water"): "crude-oil-energy-from-water",
                ("Energy, solar", "Elementary flows/resource/air"): "renewable-energy-from-air",
                ("Energy, heat", "Elementary flows/resource/air"): "renewable-energy-from-air",
                ("Energy, hydro", "Elementary flows/resource/water"): "hydro-energy-from-water",
                ("Peat", "Elementary flows/resource/ground"): "peat-energy-from-ground",
                ("Natural gas", "Elementary flows/resource/air"): "natural-gas-energy-from-air",
            }
            policy_id = exact.get((name, source_category))
            if policy_id is not None:
                return policies[policy_id]
    raise _reject(
        f"no exact classification policy for {intent.get('authoring_intent_key')}",
        batch_index=batch_index,
    )


def _json_bytes(value: object, *, pretty: bool = False) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _jsonl_bytes(rows: Iterable[Mapping[str, object]]) -> bytes:
    return b"".join(_json_bytes(dict(row)) for row in rows)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _parse_json(content: bytes, label: str) -> JsonObject:
    try:
        return _object(json.loads(content), label)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error


def _parse_jsonl(content: bytes, label: str) -> List[JsonObject]:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not UTF-8") from error
    rows = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            rows.append(_object(json.loads(line), f"{label} row {index}"))
        except json.JSONDecodeError as error:
            raise ValueError(f"{label} row {index} is invalid JSON") from error
    return rows


def _artifact_binding(path: str, content: bytes) -> JsonObject:
    return {"path": path, "sha256": _sha256(content), "bytes": len(content)}


def _verify_binding(
    value: Mapping[str, object], content: bytes, label: str
) -> JsonObject:
    row = _object(value, label)
    path = _text(row.get("path"), f"{label} path")
    digest = _text(row.get("sha256"), f"{label} SHA-256")
    size = _integer(row.get("bytes"), f"{label} byte count")
    if digest != _sha256(content) or size != len(content):
        raise ValueError(f"{label} hash or byte count drifted")
    return {"path": path, "sha256": digest, "bytes": size}


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return dict(value)


def _array(value: object, label: str) -> List[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return list(value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value.strip()


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _reject(message: str, *, batch_index: int = BATCH_INDEX) -> ValueError:
    return ValueError(f"batch {batch_index} support policy rejected: {message}")


def _content_descriptor_is_exact(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    digest = value.get("sha256")
    size = value.get("bytes")
    return (
        isinstance(value.get("path"), str)
        and bool(value["path"])
        and isinstance(digest, str)
        and bool(digest)
        and value.get("digest") == f"sha256:{digest}"
        and isinstance(size, int)
        and not isinstance(size, bool)
        and size >= 0
        and value.get("size") == size
    )


def next_route_for_batch(batch_index: int) -> str:
    if batch_index < 2 or batch_index > 16:
        raise ValueError("Flow authoring bulk batch index must be in 2..16")
    return f"compile-uslci-flow-authoring-bulk-batch-{batch_index}-saved-plans"


def input_version_for_bindings(bindings: Sequence[Mapping[str, object]]) -> str:
    """Return an order-sensitive identity for the exact registered input set."""

    if len(bindings) != len(EXPECTED_INPUT_NAMES):
        raise ValueError("input binding count does not match expected inputs")
    normalized = []
    for index, value in enumerate(bindings):
        row = _object(value, f"input binding {index + 1}")
        normalized.append(
            {
                "role": EXPECTED_INPUT_NAMES[index],
                "path": _text(row.get("path"), "input binding path"),
                "sha256": _text(row.get("sha256"), "input binding SHA-256"),
                "bytes": _integer(row.get("bytes"), "input binding byte count"),
            }
        )
    return _sha256(
        _json_bytes(
            {
                "contract": CONTRACT,
                "logic_version": LOGIC_VERSION,
                "policy_version": POLICY_VERSION,
                "inputs": normalized,
            }
        )
    )[:16]


def select_complete_source_envelope(
    candidates: Iterable[Tuple[str, bytes]],
    required_flow_ids: Sequence[str],
) -> str:
    """Select the first registered response that covers the exact batch Flow set."""

    required = tuple(
        sorted({_text(value, "required source Flow ID") for value in required_flow_ids})
    )
    if not required:
        raise ValueError("required source Flow IDs are empty")
    required_set = set(required)
    markers = tuple(value.encode("utf-8") for value in required)
    matches = set()
    for path_value, content in candidates:
        path = _text(path_value, "registered source envelope path")
        if not isinstance(content, bytes):
            raise ValueError(f"registered source envelope is not bytes: {path}")
        if not all(marker in content for marker in markers):
            continue
        envelope = _parse_json(content, f"registered source envelope {path}")
        if envelope.get("http_status") != 200:
            continue
        payload = envelope.get("payload")
        if not isinstance(payload, dict) or not isinstance(
            payload.get("exchanges"), list
        ):
            continue
        observed = {
            flow.get("@id")
            for exchange in payload["exchanges"]
            if isinstance(exchange, dict)
            for flow in [exchange.get("flow")]
            if isinstance(flow, dict) and isinstance(flow.get("@id"), str)
        }
        if required_set <= observed:
            matches.add(path)
    if not matches:
        raise ValueError(
            "no complete registered source envelope covers the selected batch Flow set"
        )
    return min(matches)


def selected_batch_source_flow_ids(
    intents: Sequence[Mapping[str, object]],
    batches: Sequence[Mapping[str, object]],
    *,
    batch_index: int,
) -> Tuple[str, ...]:
    """Return exact source Flow IDs using the same batch admission as compilation."""

    _, selected = _select_batch(intents, batches, batch_index=batch_index)
    validated = [_validate_selected_intent(value) for value in selected]
    flow_ids = tuple(
        _text(value.get("source_flow_id"), "selected source Flow ID")
        for value in validated
    )
    if len(flow_ids) != len(set(flow_ids)):
        raise _reject(
            "selected source Flow IDs are duplicated", batch_index=batch_index
        )
    return flow_ids


def _select_batch(
    intents: Sequence[Mapping[str, object]],
    batches: Sequence[Mapping[str, object]],
    *,
    batch_index: int = BATCH_INDEX,
) -> Tuple[JsonObject, List[JsonObject]]:
    if len(intents) != 1456 or len(batches) != 16:
        raise _reject("authoring plan cardinality drifted")
    matches = [row for row in batches if row.get("batch_index") == batch_index]
    if len(matches) != 1:
        raise _reject("batch descriptor is not unique", batch_index=batch_index)
    batch = _object(matches[0], f"batch {batch_index} descriptor")
    keys = [_text(value, f"batch {batch_index} intent key") for value in _array(batch.get("authoring_intent_keys"), f"batch {batch_index} intent keys")]
    expected_count = _integer(batch.get("action_count"), "batch action count")
    declared_group_counts = _object(
        batch.get("authoring_group_counts"), "batch authoring group counts"
    )
    normalized_group_counts = {}
    for key, value in declared_group_counts.items():
        group_key = _text(key, "batch authoring group key")
        group_count = _integer(value, f"batch authoring group {group_key} count")
        if group_count <= 0:
            raise _reject("batch authoring group count is not positive", batch_index=batch_index)
        normalized_group_counts[group_key] = group_count
    if (
        (batch_index == BATCH_INDEX and batch.get("batch_key") != BATCH_KEY)
        or expected_count <= 0
        or expected_count > 100
        or len(keys) != expected_count
        or len(set(keys)) != expected_count
        or sum(normalized_group_counts.values()) != expected_count
        or batch.get("bulk_release_requires_canary_pass") is not True
        or batch.get("maximum_bulk_batch_size") != 100
    ):
        raise _reject("batch descriptor drifted", batch_index=batch_index)
    by_key: Dict[str, JsonObject] = {}
    for value in intents:
        row = _object(value, "authoring intent")
        key = row.get("authoring_intent_key")
        if key in by_key:
            raise _reject("authoring intent keys are duplicated")
        if isinstance(key, str):
            by_key[key] = row
    if any(key not in by_key for key in keys):
        raise _reject("batch intent membership is incomplete", batch_index=batch_index)
    selected = [by_key[key] for key in keys]
    positions = [_integer(row.get("position"), "selected intent position") for row in selected]
    declared_positions = [_integer(value, f"batch {batch_index} source position") for value in _array(batch.get("source_positions"), f"batch {batch_index} source positions")]
    if positions != declared_positions or len(set(positions)) != expected_count:
        raise _reject("batch positions or declared order drifted", batch_index=batch_index)
    observed_group_counts: Dict[str, int] = {}
    for row in selected:
        group_key = _text(row.get("authoring_group_key"), "selected authoring group key")
        observed_group_counts[group_key] = observed_group_counts.get(group_key, 0) + 1
    if observed_group_counts != normalized_group_counts:
        raise _reject("batch authoring group membership drifted", batch_index=batch_index)
    return batch, selected


def _validate_selected_intent(value: Mapping[str, object]) -> JsonObject:
    row = _object(value, "selected authoring intent")
    target = _object(row.get("target"), "selected target")
    property_id = row.get("flow_property_id")
    spec = SUPPORT_PROPERTY_SPECS.get(property_id)
    coarse = row.get("coarse_source_classification")
    if not isinstance(spec, dict) or coarse not in {
        "elementary_emission",
        "elementary_resource",
    }:
        raise _reject("selected intent property or elementary class is unsupported")
    property_name = spec["flow_property_name"]
    reference_unit = spec["reference_unit"]
    expected_group = f"{coarse}|Elementary flow|{property_name}|{reference_unit}"
    if (
        row.get("source_flow_type") != "ELEMENTARY_FLOW"
        or row.get("authoring_group_key") != expected_group
        or row.get("flow_property_name") != property_name
        or row.get("reference_unit") != reference_unit
        or row.get("conversion_factor") != "1"
        or row.get("authoring_status")
        != "waiting_for_classification_and_support_probe"
        or row.get("exact_classification_path_required") is not True
        or row.get("support_reference_status") != spec["intent_support_status"]
        or row.get("support_reference_local_addressability_verified") is not False
        or row.get("remote_duplicate_unknown") is not True
        or row.get("saved_flow_plan_compiled") is not False
        or row.get("entity_creation_authorized") is not False
        or row.get("entity_materialized") is not False
        or row.get("publish_ready") is not False
        or target.get("id") is None
        or target.get("name") != row.get("source_flow_name")
        or target.get("type_of_dataset") != "Elementary flow"
        or target.get("flow_property") != property_name
        or target.get("reference_unit") != reference_unit
    ):
        raise _reject("selected intent semantic contract drifted")
    return row


def _source_observations(
    envelope_value: Mapping[str, object], selected: Sequence[Mapping[str, object]]
) -> List[JsonObject]:
    envelope = _object(envelope_value, "elementary source envelope")
    if envelope.get("http_status") != 200:
        raise _reject("source envelope was not successful")
    payload = _object(envelope.get("payload"), "source envelope payload")
    exchanges = [_object(value, "source exchange") for value in _array(payload.get("exchanges"), "source exchanges")]
    required = {_text(row.get("source_flow_id"), "source Flow ID") for row in selected}
    by_id: Dict[str, List[JsonObject]] = {flow_id: [] for flow_id in required}
    for exchange in exchanges:
        flow = _object(exchange.get("flow"), "source exchange Flow")
        flow_id = flow.get("@id")
        if flow_id not in by_id:
            continue
        flow_property = _object(exchange.get("flowProperty"), "source FlowProperty")
        unit = _object(exchange.get("unit"), "source exchange unit")
        by_id[str(flow_id)].append(
            {
                "source_flow_id": flow_id,
                "source_flow_name": flow.get("name"),
                "source_flow_type": flow.get("flowType"),
                "source_category": flow.get("category"),
                "source_category_is_in_repo": flow.get("categoryIsInRepo") is True,
                "source_flow_is_in_repo": flow.get("isInRepo") is True,
                "flow_property_id": flow_property.get("@id"),
                "flow_property_name": flow_property.get("name"),
                "flow_property_is_in_repo": flow_property.get("isInRepo") is True,
                "reference_unit": flow_property.get("refUnit"),
                "exchange_unit_id": unit.get("@id"),
                "exchange_unit_name": unit.get("name"),
                "direction": "Input" if exchange.get("isInput") is True else "Output",
                "quantitative_reference": exchange.get("isQuantitativeReference") is True,
            }
        )
    intent_by_id = {str(row["source_flow_id"]): row for row in selected}
    observations = []
    for flow_id in sorted(required):
        found = by_id[flow_id]
        if not found:
            raise _reject(f"source observation is missing for {flow_id}")
        intent = intent_by_id[flow_id]
        property_spec = SUPPORT_PROPERTY_SPECS.get(intent.get("flow_property_id"))
        coarse = intent.get("coarse_source_classification")
        property_matches = [
            row
            for row in found
            if row.get("flow_property_id") == intent.get("flow_property_id")
        ]
        if not property_matches:
            raise _reject(f"source observation lost the selected FlowProperty for {flow_id}")
        unique_matches = {_sha256(_json_bytes(row)): row for row in property_matches}
        if len(unique_matches) != 1:
            raise _reject(f"source observations conflict for {flow_id}")
        observation = dict(next(iter(unique_matches.values())))
        unique_all = {_sha256(_json_bytes(row)): row for row in found}
        excluded_variants = sorted(
            (
                {
                    "flow_property_id": row.get("flow_property_id"),
                    "flow_property_name": row.get("flow_property_name"),
                    "reference_unit": row.get("reference_unit"),
                    "exchange_unit_id": row.get("exchange_unit_id"),
                    "exchange_unit_name": row.get("exchange_unit_name"),
                }
                for digest, row in unique_all.items()
                if digest not in unique_matches
            ),
            key=lambda row: (
                str(row.get("flow_property_id")),
                str(row.get("reference_unit")),
            ),
        )
        expected_category_prefix = (
            "Elementary flows/emission/"
            if coarse == "elementary_emission"
            else "Elementary flows/resource/"
        )
        expected_direction = "Output" if coarse == "elementary_emission" else "Input"
        if (
            not isinstance(property_spec, dict)
            or
            observation.get("source_flow_name") != intent.get("source_flow_name")
            or observation.get("source_flow_type") != "ELEMENTARY_FLOW"
            or not isinstance(observation.get("source_category"), str)
            or not observation["source_category"].startswith(expected_category_prefix)
            or observation.get("source_category_is_in_repo") is not True
            or observation.get("source_flow_is_in_repo") is not True
            or observation.get("flow_property_id") != intent.get("flow_property_id")
            or observation.get("flow_property_name")
            != property_spec.get("flow_property_name")
            or observation.get("flow_property_is_in_repo") is not True
            or observation.get("reference_unit") != property_spec.get("reference_unit")
            or observation.get("exchange_unit_id") != property_spec.get("unit_id")
            or observation.get("exchange_unit_name") != property_spec.get("reference_unit")
            or observation.get("direction") != expected_direction
            or observation.get("quantitative_reference") is not False
        ):
            raise _reject(f"source observation semantics drifted for {flow_id}")
        observation["source_flow_occurrence_count"] = len(found)
        observation["bound_exchange_observation_count"] = len(property_matches)
        observation["excluded_property_variant_count"] = len(excluded_variants)
        observation["excluded_property_variants"] = excluded_variants
        observation["source_observation_sha256"] = _sha256(
            _json_bytes(next(iter(unique_matches.values())))
        )
        observations.append(observation)
    return observations


def _validate_support(
    support_report_value: Mapping[str, object],
    support_contract_values: Sequence[Mapping[str, object]],
    runtime_evidence_value: Mapping[str, object],
) -> Dict[str, JsonObject]:
    report = _object(support_report_value, "support report")
    runtime = _object(runtime_evidence_value, "support runtime evidence")
    contracts = [_object(value, "support contract") for value in support_contract_values]
    if (
        report.get("contract")
        != "lca_research_kernel.uslci_flow_authoring_canary_supports/v1"
        or report.get("status") != "uslci_flow_authoring_canary_supports_resolved"
        or report.get("support_resolution_version") != "4575e0df62e45cfa"
        or report.get("support_contract_count") != 3
        or report.get("bulk_release_authorized") is not False
        or len(contracts) != len(SUPPORT_PROPERTY_SPECS)
        or runtime.get("explicit_flow_property_reference_supported") is not True
        or runtime.get("native_builtin_mass_id") != MASS_PROPERTY_ID
    ):
        raise _reject("accepted support authority drifted")
    by_property = {}
    for contract in contracts:
        property_id = contract.get("flow_property_id")
        spec = SUPPORT_PROPERTY_SPECS.get(property_id)
        if not isinstance(spec, dict) or property_id in by_property:
            raise _reject("support contract property identities drifted")
        if (
            contract.get("flow_property_name") != spec["flow_property_name"]
            or contract.get("flow_property_version") != spec["flow_property_version"]
            or contract.get("unit_group_id") != spec["unit_group_id"]
            or contract.get("unit_group_name") != spec["unit_group_name"]
            or contract.get("unit_group_version") != spec["unit_group_version"]
            or contract.get("reference_unit") != spec["reference_unit"]
            or contract.get("native_addressability_kind")
            != spec["native_addressability_kind"]
            or contract.get("source_intent_count") != spec["source_intent_count"]
            or contract.get("support_status") != "resolved_for_native_saved_plan"
            or contract.get("saved_plan_reference_authorized") is not True
            or contract.get("remote_resolution_required_for_publication") is not True
            or contract.get("entity_creation_authorized") is not False
        ):
            raise _reject(f"{spec['flow_property_name']} support contract drifted")
        normalized = dict(contract)
        normalized["support_contract_sha256"] = _sha256(_json_bytes(contract))
        normalized["support_resolution_version"] = report[
            "support_resolution_version"
        ]
        normalized["runtime_tree_sha256"] = runtime.get("runtime_tree_sha256")
        by_property[str(property_id)] = normalized
    if set(by_property) != set(SUPPORT_PROPERTY_SPECS):
        raise _reject("support contract set drifted")
    return by_property


def validate_route_authority(
    value: Mapping[str, object],
    *,
    batch_index: int = BATCH_INDEX,
    report_sha256: Optional[str] = None,
) -> None:
    authority = _object(value, "route authority")
    routing = authority.get("routing") if isinstance(authority.get("routing"), dict) else {}
    next_route = authority.get("next_internal_route") or routing.get("next_internal_route")
    if batch_index == BATCH_INDEX:
        valid = (
            authority.get("contract")
            == "lca_research_kernel.uslci_elementary_flow_native_qa_harness/v1"
            and authority.get("status")
            == "uslci_elementary_flow_native_qa_harness_resolved"
            and authority.get("typed_qa_status")
            == "typed_flow_qa_passed_with_scoped_elementary_not_applicable_findings"
            and authority.get("bulk_planning_release_authorized") is True
            and authority.get("bulk_entity_execution_authorized") is False
            and next_route == "resolve-uslci-flow-authoring-bulk-batch-2-supports"
        )
    else:
        valid = (
            authority.get("contract")
            == "lca_research_kernel.uslci_flow_authoring_bulk_batch_native_gates/v1"
            and authority.get("status")
            == "uslci_flow_authoring_bulk_batch_native_gates_completed"
            and authority.get("batch_index") == batch_index - 1
            and authority.get("successful_branch_count") == 100
            and authority.get("failed_branch_count") == 0
            and authority.get("typed_qa_pass_count") == 100
            and authority.get("batch_entity_release_authorized") is False
            and next_route
            == f"resolve-uslci-flow-authoring-bulk-batch-{batch_index}-supports"
        )
        if valid and batch_index >= 9:
            artifacts = authority.get("artifact_bindings")
            strict_marker_count = authority.get("review_marker_preserved_count")
            strict_authority = STRICT_NATIVE_ROUTE_AUTHORITIES.get(batch_index)
            expected_logic = (
                BATCH_8_NATIVE_GATE_LOGIC_VERSION
                if batch_index == 9
                else strict_authority["logic_version"]
                if strict_authority is not None
                else CURRENT_NATIVE_GATE_LOGIC_VERSION
            )
            valid = (
                authority.get("logic_version") == expected_logic
                and authority.get("policy_version") == POLICY_VERSION
                and authority.get("branch_terminal_count") == 100
                and authority.get("native_invocation_executed_count") == 500
                and authority.get("terminal_dag_node_count") == 201
                and authority.get("terminal_dag_edge_count") == 200
                and authority.get("raw_artifact_member_count") == 2900
                and authority.get("unexpected_failure_count") == 0
                and authority.get("save_draft_authorized") is False
                and authority.get("scientific_comparison_ready") is False
                and authority.get("comparative_lcia_allowed") is False
                and routing.get("pause_internal_work") is False
                and all(authority.get(key) == 0 for key in NATIVE_AUTHORITY_ZERO_EFFECT_KEYS)
                and isinstance(artifacts, dict)
                and all(
                    _content_descriptor_is_exact(artifacts.get(name))
                    for name in NATIVE_AUTHORITY_REQUIRED_BINDINGS
                )
                and (
                    strict_marker_count == 6
                    if batch_index == 9
                    else strict_marker_count
                    == strict_authority["review_marker_count"]
                    if strict_authority is not None
                    else isinstance(strict_marker_count, int)
                    and not isinstance(strict_marker_count, bool)
                    and strict_marker_count >= 0
                )
            )
            if valid and strict_authority is not None:
                archive = artifacts.get("raw_artifact_archive")
                valid = (
                    authority.get("input_version")
                    == strict_authority["input_version"]
                    and authority.get("source_saved_plan_set_version")
                    == strict_authority["saved_plan_set_version"]
                    and authority.get("native_gate_execution_version")
                    == strict_authority["execution_version"]
                    and (
                        report_sha256 or authority.get("report_sha256")
                    )
                    == strict_authority["report_sha256"]
                    and isinstance(archive, dict)
                    and archive.get("sha256")
                    == strict_authority["archive_sha256"]
                    and archive.get("bytes") == strict_authority["archive_bytes"]
                    and archive.get("size") == strict_authority["archive_bytes"]
                )
    if not valid:
        raise _reject("route authority drifted", batch_index=batch_index)


def _probe_index(
    values: Sequence[Mapping[str, object]], *, batch_index: int
) -> Dict[str, JsonObject]:
    by_code = {}
    expected = {
        str(spec["code"]): spec
        for spec in _policy_specs_for_batch(batch_index).values()
    }
    for value in values:
        row = _object(value, "native category probe")
        code = row.get("code")
        if not isinstance(code, str) or code in by_code:
            raise _reject("native category probe codes are invalid")
        by_code[code] = row
    if set(by_code) != set(expected):
        raise _reject("native category probe set drifted")
    for code, spec in expected.items():
        row = by_code[code]
        path = _array(row.get("path"), "native classification path")
        leaf = _object(path[-1] if path else None, "native classification leaf")
        if (
            row.get("category_type") != "flow-elementary"
            or row.get("schema_file") != "tidas_flows_elementary_category.json"
            or row.get("leaf_label") != spec["leaf_label"]
            or leaf.get("@catId") != code
            or leaf.get("#text") != spec["leaf_label"]
        ):
            raise _reject(f"native category path drifted for {code}")
    return by_code


def compile_bulk_batch_supports(
    *,
    plan_report: Mapping[str, object],
    intents: Sequence[Mapping[str, object]],
    batches: Sequence[Mapping[str, object]],
    source_envelope: Mapping[str, object],
    support_report: Mapping[str, object],
    support_contracts: Sequence[Mapping[str, object]],
    runtime_evidence: Mapping[str, object],
    route_authority: Mapping[str, object],
    probes: Sequence[Mapping[str, object]],
    batch_index: int = BATCH_INDEX,
    route_authority_report_sha256: Optional[str] = None,
) -> JsonObject:
    """Compile a fail-closed stable-keyed batch classification/support result."""

    report = _object(plan_report, "authoring plan report")
    if (
        report.get("contract") != "lca_research_kernel.uslci_flow_authoring_plan/v1"
        or report.get("status") != "uslci_flow_authoring_plan_compiled"
        or report.get("authoring_plan_version") != "44d477987db9e8fe"
        or report.get("authoring_intent_count") != 1456
        or report.get("authoring_batch_count") != 16
        or report.get("maximum_bulk_batch_size") != 100
    ):
        raise _reject("authoring plan authority drifted")
    validate_route_authority(
        route_authority,
        batch_index=batch_index,
        report_sha256=route_authority_report_sha256,
    )
    batch, selected_values = _select_batch(intents, batches, batch_index=batch_index)
    selected = [_validate_selected_intent(row) for row in selected_values]
    observations = _source_observations(source_envelope, selected)
    observation_by_id = {row["source_flow_id"]: row for row in observations}
    support_by_property = _validate_support(
        support_report, support_contracts, runtime_evidence
    )
    probes_by_code = _probe_index(probes, batch_index=batch_index)

    policy_rows = []
    for policy_id, spec in sorted(_policy_specs_for_batch(batch_index).items()):
        probe = probes_by_code[str(spec["code"])]
        policy_rows.append(
            {
                "schema_version": 1,
                "policy_version": POLICY_VERSION,
                "policy_id": policy_id,
                "source_category": spec.get("source_category"),
                "category_type": "flow-elementary",
                "classification_code": spec["code"],
                "classification_leaf_label": spec["leaf_label"],
                "classification_path": probe["path"],
                "native_probe_sha256": probe.get("raw_probe_sha256"),
                "confidence": spec["confidence"],
                "review_priority": spec["review_priority"],
                "rationale": spec["rationale"],
            }
        )

    decisions = []
    assignments = []
    readiness = []
    risks = []
    for batch_position, intent in enumerate(selected, start=1):
        observation = observation_by_id.get(intent["source_flow_id"])
        if not isinstance(observation, dict):
            raise _reject("selected intent lost its source observation")
        source_category = str(observation["source_category"])
        spec = _classification_spec(
            intent, observation, batch_index=batch_index
        )
        probe = probes_by_code[str(spec["code"])]
        support = support_by_property.get(str(intent.get("flow_property_id")))
        property_spec = SUPPORT_PROPERTY_SPECS.get(intent.get("flow_property_id"))
        if not isinstance(support, dict) or not isinstance(property_spec, dict):
            raise _reject("selected intent lost its support contract")
        decision_id = _sha256(
            _json_bytes(
                {
                    "policy_version": POLICY_VERSION,
                    "authoring_intent_key": intent["authoring_intent_key"],
                    "source_observation_sha256": observation["source_observation_sha256"],
                    "classification_code": spec["code"],
                    "native_probe_sha256": probe.get("raw_probe_sha256"),
                }
            )
        )[:16]
        decision = {
            "schema_version": 1,
            "batch_index": batch_index,
            "batch_position": batch_position,
            "decision_id": decision_id,
            "authoring_intent_key": intent["authoring_intent_key"],
            "position": intent["position"],
            "source_flow_id": intent["source_flow_id"],
            "source_flow_name": intent["source_flow_name"],
            "target_flow_id": _object(intent["target"], "target")["id"],
            "source_category": source_category,
            "source_observation_sha256": observation["source_observation_sha256"],
            "source_flow_occurrence_count": observation[
                "source_flow_occurrence_count"
            ],
            "excluded_property_variant_count": observation[
                "excluded_property_variant_count"
            ],
            "category_type": "flow-elementary",
            "classification_code": spec["code"],
            "classification_path": probe["path"],
            "classification_leaf_label": spec["leaf_label"],
            "native_probe_sha256": probe.get("raw_probe_sha256"),
            "decision_status": (
                "resolved_with_review_marker"
                if spec["review_priority"] is not None
                else "resolved"
            ),
            "confidence": spec["confidence"],
            "decision_rationale": spec["rationale"],
            "review_required": spec["review_priority"] is not None,
            "review_priority": spec["review_priority"],
            "saved_plan_compilation_authorized": True,
            "entity_creation_authorized": False,
        }
        assignment = {
            "schema_version": 1,
            "batch_index": batch_index,
            "batch_position": batch_position,
            "authoring_intent_key": intent["authoring_intent_key"],
            "source_flow_id": intent["source_flow_id"],
            "target_flow_id": decision["target_flow_id"],
            "flow_property_id": intent["flow_property_id"],
            "flow_property_name": intent["flow_property_name"],
            "reference_unit": intent["reference_unit"],
            "support_resolution_version": support["support_resolution_version"],
            "support_contract_sha256": support["support_contract_sha256"],
            "runtime_tree_sha256": support.get("runtime_tree_sha256"),
            "support_status": property_spec["assignment_status"],
            "saved_plan_reference_authorized": True,
            "entity_creation_authorized": False,
        }
        ready = {
            "schema_version": 1,
            "batch_index": batch_index,
            "batch_position": batch_position,
            "authoring_intent_key": intent["authoring_intent_key"],
            "source_flow_id": intent["source_flow_id"],
            "target_flow_id": decision["target_flow_id"],
            "classification_decision_id": decision_id,
            "classification_resolved": True,
            "support_resolved": True,
            "source_evidence_bound": True,
            "saved_plan_compilation_authorized": True,
            "entity_creation_authorized": False,
            "readiness_status": "ready_for_saved_plan_compilation",
        }
        decisions.append(decision)
        assignments.append(assignment)
        readiness.append(ready)
        if spec["review_priority"] is not None:
            risks.append(
                {
                    "schema_version": 1,
                    "risk_id": (
                        f"batch-{batch_index}-{spec['risk_id_prefix']}:"
                        f"{intent['authoring_intent_key']}"
                    ),
                    "batch_index": batch_index,
                    "batch_position": batch_position,
                    "authoring_intent_key": intent["authoring_intent_key"],
                    "source_flow_id": intent["source_flow_id"],
                    "source_flow_name": intent["source_flow_name"],
                    "severity": spec["review_priority"],
                    "risk_type": spec["risk_type"],
                    "source_category": source_category,
                    "classification_code": spec["code"],
                    "classification_leaf_label": spec["leaf_label"],
                    "reason": spec["review_reason"],
                    "source_flow_occurrence_count": observation[
                        "source_flow_occurrence_count"
                    ],
                    "excluded_property_variant_count": observation[
                        "excluded_property_variant_count"
                    ],
                    "excluded_property_variants": observation[
                        "excluded_property_variants"
                    ],
                    "blocks_saved_plan_compilation": False,
                    "blocks_entity_creation": True,
                    "review_status": "retained_for_later_human_review",
                }
            )

    source_counts: Dict[str, int] = {}
    code_counts = {
        str(spec["code"]): 0
        for spec in _policy_specs_for_batch(batch_index).values()
    }
    for row in decisions:
        category = str(row["source_category"])
        source_counts[category] = source_counts.get(category, 0) + 1
        code_counts[str(row["classification_code"])] += 1
    flow_property_counts: Dict[str, int] = {}
    for row in assignments:
        key = f"{row['flow_property_name']}|{row['reference_unit']}"
        flow_property_counts[key] = flow_property_counts.get(key, 0) + 1
    if sum(source_counts.values()) != len(selected) or len(decisions) != len(selected):
        raise _reject("resolved batch distribution drifted", batch_index=batch_index)
    selected_batch = {
        "schema_version": 1,
        "batch_index": batch_index,
        "batch_key": batch.get("batch_key"),
        "action_count": len(selected),
        "authoring_intent_keys": [row["authoring_intent_key"] for row in selected],
        "source_positions": [row["position"] for row in selected],
        "source_batch_descriptor_sha256": _sha256(_json_bytes(batch)),
        "stable_key_join": "authoring_intent_key",
    }
    return {
        "selected_batch": selected_batch,
        "source_observations": observations,
        "classification_policy": policy_rows,
        "native_classification_probes": [probes_by_code[code] for code in sorted(probes_by_code)],
        "classification_decisions": decisions,
        "support_assignments": assignments,
        "readiness": readiness,
        "review_markers": risks,
        "failures": [],
        "source_category_counts": source_counts,
        "classification_code_counts": dict(sorted(code_counts.items())),
        "flow_property_counts": dict(sorted(flow_property_counts.items())),
        "selected_intent_count": len(selected),
        "source_observation_count": len(observations),
        "excluded_property_variant_count": sum(
            int(row["excluded_property_variant_count"]) for row in observations
        ),
        "classification_policy_count": len(policy_rows),
        "native_classification_probe_count": len(probes_by_code),
        "classification_decision_count": len(decisions),
        "support_contract_reuse_count": len(
            {str(row["flow_property_id"]) for row in assignments}
        ),
        "support_assignment_count": len(assignments),
        "ready_intent_count": len(readiness),
        "review_marker_count": len(risks),
        "low_priority_review_marker_count": sum(
            row.get("severity") == "low" for row in risks
        ),
        "medium_priority_review_marker_count": sum(
            row.get("severity") == "medium" for row in risks
        ),
        "resolution_failure_count": 0,
        "saved_plan_compilation_authorized": True,
        "entity_creation_authorized": False,
        "mass_support_contract": support_by_property[MASS_PROPERTY_ID],
        "source_route_authority_version": (
            route_authority.get("qa_harness_execution_version")
            or route_authority.get("native_gate_execution_version")
        ),
        "source_route_authority_report_sha256": (
            route_authority_report_sha256
            or route_authority.get("report_sha256")
        ),
    }


def _run_native_probes(
    runtime_root: Path, raw_dir: Path, *, batch_index: int = BATCH_INDEX
) -> Tuple[List[JsonObject], List[JsonObject]]:
    """Run one copied-TianGong path lookup per distinct batch policy path."""

    raw_dir.mkdir(parents=True, exist_ok=True)
    node = runtime_root / RUNTIME_FILES["node"]
    cli = runtime_root / RUNTIME_FILES["tiangong_cli"]
    probes = []
    descriptors = []
    specs = sorted(
        _policy_specs_for_batch(batch_index).values(),
        key=lambda row: str(row["code"]),
    )
    for index, spec in enumerate(specs, start=1):
        code = str(spec["code"])
        completed = _run_local_classification_path(
            [
                str(node),
                str(cli),
                "dataset",
                "classification",
                "path",
                "--type",
                "flow-elementary",
                "--code",
                code,
                "--json",
            ]
        )
        if completed.returncode != 0:
            raise ValueError(
                f"native batch classification probe failed for {code}: "
                f"{completed.stderr.decode('utf-8', errors='replace').strip()}"
            )
        raw = completed.stdout
        normalized = validate_native_classification_probe(
            _parse_json(raw, f"native batch classification probe {index}"),
            category_type="flow-elementary",
            code=code,
        )
        if normalized.get("leaf_label") != spec["leaf_label"]:
            raise ValueError(f"native batch classification leaf drifted for {code}")
        filename = f"{index:02d}-flow-elementary-{code.replace('.', '-')}.json"
        (raw_dir / filename).write_bytes(raw)
        descriptor = _artifact_binding(f"native-probes/{filename}", raw)
        descriptor.update(
            {
                "probe_index": index,
                "policy_id": spec["policy_id"],
                "source_category": spec.get("source_category"),
                "category_type": "flow-elementary",
                "code": code,
                "media_type": "application/json",
            }
        )
        normalized.update(
            {
                "probe_index": index,
                "policy_id": spec["policy_id"],
                "source_category": spec.get("source_category"),
                "raw_probe_path": descriptor["path"],
                "raw_probe_sha256": descriptor["sha256"],
            }
        )
        probes.append(normalized)
        descriptors.append(descriptor)
    return probes, descriptors


def _run_local_classification_path(command: Sequence[str]):
    """Run only the credential-free offline classification-path probe."""

    allowed_codes = {
        str(spec["code"])
        for batch_index in (2, 15, 16)
        for spec in _policy_specs_for_batch(batch_index).values()
    }
    if (
        len(command) != 10
        or tuple(command[2:5]) != ("dataset", "classification", "path")
        or tuple(command[5:7]) != ("--type", "flow-elementary")
        or command[7] != "--code"
        or command[8] not in allowed_codes
        or command[9] != "--json"
    ):
        raise ValueError("copied TianGong support probe is outside the local allowlist")
    sensitive = (
        "TOKEN",
        "SECRET",
        "PASSWORD",
        "CREDENTIAL",
        "API_KEY",
        "ACCESS_KEY",
        "PRIVATE_KEY",
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not any(fragment in key.upper() for fragment in sensitive)
    }
    environment.update(
        {
            "TIANGONG_NETWORK_MODE": "offline",
            "NO_PROXY": "*",
            "no_proxy": "*",
        }
    )
    return subprocess.run(
        command,
        capture_output=True,
        check=False,
        env=environment,
    )


def _require_artifact_binding(
    owner: Mapping[str, object],
    name: str,
    actual: Mapping[str, object],
    label: str,
) -> None:
    artifacts = _object(owner.get("artifact_bindings"), f"{label} artifact bindings")
    expected = _object(artifacts.get(name), f"{label} {name} binding")
    if (
        expected.get("sha256") != actual.get("sha256")
        or expected.get("bytes") != actual.get("bytes")
    ):
        raise ValueError(f"{label} binding drifted for {name}")


def execute_and_materialize(
    *,
    contents: Mapping[str, bytes],
    bindings: Mapping[str, Mapping[str, object]],
    runtime_root: Path,
    out_dir: Path,
    batch_index: int = BATCH_INDEX,
) -> JsonObject:
    """Verify registered inputs, run native probes and write deterministic outputs."""

    if set(contents) != set(EXPECTED_INPUT_NAMES) or set(bindings) != set(
        EXPECTED_INPUT_NAMES
    ):
        raise ValueError(f"batch {batch_index} support input roles drifted")
    verified_bindings = {
        name: _verify_binding(bindings[name], contents[name], f"{name} binding")
        for name in EXPECTED_INPUT_NAMES
    }
    ordered = [verified_bindings[name] for name in EXPECTED_INPUT_NAMES]
    input_version = input_version_for_bindings(ordered)

    plan_report = _parse_json(contents["authoring_plan_report"], "authoring plan report")
    intents = _parse_jsonl(contents["authoring_intents"], "authoring intents")
    batches = _parse_jsonl(contents["authoring_batches"], "authoring batches")
    source_envelope = _parse_json(
        contents["elementary_source_envelope"], "elementary source envelope"
    )
    support_report = _parse_json(contents["support_report"], "support report")
    support_contracts = _parse_jsonl(
        contents["support_contracts"], "support contracts"
    )
    support_runtime_evidence = _parse_json(
        contents["runtime_evidence"], "support runtime evidence"
    )
    route_authority = _parse_json(contents["route_authority"], "route authority")
    runtime_report = _parse_json(contents["runtime_report"], "runtime report")
    runtime_manifest = _parse_json(contents["runtime_manifest"], "runtime manifest")

    _require_artifact_binding(
        plan_report,
        "authoring_intents",
        verified_bindings["authoring_intents"],
        "authoring plan",
    )
    _require_artifact_binding(
        plan_report,
        "authoring_batches",
        verified_bindings["authoring_batches"],
        "authoring plan",
    )
    _require_artifact_binding(
        support_report,
        "support_contracts",
        verified_bindings["support_contracts"],
        "support report",
    )
    _require_artifact_binding(
        support_report,
        "runtime_evidence",
        verified_bindings["runtime_evidence"],
        "support report",
    )
    if (
        runtime_report.get("contract")
        != "lca_research_kernel.portable_native_tool_runtime_closure/v1"
        or runtime_report.get("status")
        != "portable_native_tool_runtime_closure_verified"
        or runtime_report.get("runtime_content_closed") is not True
        or runtime_manifest.get("contract")
        != "lca_research_kernel.portable_native_tool_runtime_closure/v1"
        or runtime_manifest.get("status")
        != "portable_native_tool_runtime_closure_compiled"
        or runtime_report.get("tree_sha256") != runtime_manifest.get("tree_sha256")
        or not isinstance(runtime_manifest.get("tiangong_cli"), dict)
        or runtime_manifest.get("tiangong_cli", {}).get("package")
        != "@tiangong-lca/cli"
        or runtime_manifest.get("tiangong_cli", {}).get("version") != "0.0.25"
    ):
        raise ValueError("copied runtime report or manifest drifted")
    current_runtime_evidence = verify_runtime_files(runtime_root, runtime_manifest)
    if (
        current_runtime_evidence.get("runtime_tree_sha256")
        != support_runtime_evidence.get("runtime_tree_sha256")
        or current_runtime_evidence.get("explicit_flow_property_reference_supported")
        is not True
        or current_runtime_evidence.get("native_builtin_mass_id") != MASS_PROPERTY_ID
    ):
        raise ValueError("current runtime no longer satisfies the Mass support contract")

    outputs = out_dir / "outputs"
    native_raw_dir = outputs / "native-probes"
    probes, native_raw_bindings = _run_native_probes(
        runtime_root, native_raw_dir, batch_index=batch_index
    )
    compiled = compile_bulk_batch_supports(
        plan_report=plan_report,
        intents=intents,
        batches=batches,
        source_envelope=source_envelope,
        support_report=support_report,
        support_contracts=support_contracts,
        runtime_evidence=support_runtime_evidence,
        route_authority=route_authority,
        probes=probes,
        batch_index=batch_index,
        route_authority_report_sha256=verified_bindings["route_authority"][
            "sha256"
        ],
    )

    prefix = f"uslci-flow-authoring-bulk-batch-{batch_index}"
    output_rows = {
        "selected_batch": (
            f"{prefix}-selection.json",
            _json_bytes(compiled["selected_batch"], pretty=True),
        ),
        "source_observations": (
            f"{prefix}-source-observations.jsonl",
            _jsonl_bytes(compiled["source_observations"]),
        ),
        "classification_policy": (
            f"{prefix}-classification-policy.jsonl",
            _jsonl_bytes(compiled["classification_policy"]),
        ),
        "native_classification_probes": (
            f"{prefix}-native-classification-probes.jsonl",
            _jsonl_bytes(compiled["native_classification_probes"]),
        ),
        "classification_decisions": (
            f"{prefix}-classification-decisions.jsonl",
            _jsonl_bytes(compiled["classification_decisions"]),
        ),
        "support_assignments": (
            f"{prefix}-support-assignments.jsonl",
            _jsonl_bytes(compiled["support_assignments"]),
        ),
        "readiness": (
            f"{prefix}-readiness.jsonl",
            _jsonl_bytes(compiled["readiness"]),
        ),
        "review_markers": (
            f"{prefix}-review-markers.jsonl",
            _jsonl_bytes(compiled["review_markers"]),
        ),
        "failures": (
            f"{prefix}-support-failures.jsonl",
            b"",
        ),
    }
    artifact_bindings = {
        name: _artifact_binding(filename, content)
        for name, (filename, content) in output_rows.items()
    }
    resolution_version = _sha256(
        _json_bytes(
            {
                "contract": CONTRACT,
                "logic_version": LOGIC_VERSION,
                "policy_version": POLICY_VERSION,
                "batch_index": batch_index,
                "input_version": input_version,
                "selected_batch": compiled["selected_batch"],
                "classification_decisions": compiled["classification_decisions"],
                "support_assignments": compiled["support_assignments"],
                "readiness": compiled["readiness"],
            }
        )
    )[:16]
    zero_effects = {
        "network_read_executed_count": 0,
        "remote_candidate_search_executed_count": 0,
        "saved_flow_plan_compiled_count": 0,
        "flow_creation_executed_count": 0,
        "flow_materialization_executed_count": 0,
        "remote_flow_materialization_executed_count": 0,
        "save_draft_executed_count": 0,
        "process_creation_executed_count": 0,
        "model_connection_executed_count": 0,
        "model_mutation_executed_count": 0,
        "external_write_executed_count": 0,
        "publication_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
    }
    result = {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "policy_version": POLICY_VERSION,
        "status": f"uslci_flow_authoring_bulk_batch_{batch_index}_supports_resolved",
        "scope_id": plan_report.get("scope_id"),
        "revision": plan_report.get("revision"),
        "batch_index": batch_index,
        "batch_key": compiled["selected_batch"]["batch_key"],
        "input_version": input_version,
        "resolution_version": resolution_version,
        "source_authoring_plan_version": plan_report.get("authoring_plan_version"),
        "source_support_resolution_version": support_report.get(
            "support_resolution_version"
        ),
        "source_typed_qa_execution_version": route_authority.get(
            "qa_harness_execution_version"
        ),
        "source_route_authority_version": compiled[
            "source_route_authority_version"
        ],
        "source_route_authority_report_sha256": compiled[
            "source_route_authority_report_sha256"
        ],
        "runtime_tree_sha256": runtime_manifest.get("tree_sha256"),
        "input_bindings": verified_bindings,
        "artifact_bindings": artifact_bindings,
        "native_raw_artifact_bindings": native_raw_bindings,
        "source_category_counts": compiled["source_category_counts"],
        "classification_code_counts": compiled["classification_code_counts"],
        "flow_property_counts": compiled["flow_property_counts"],
        "selected_intent_count": compiled["selected_intent_count"],
        "source_observation_count": compiled["source_observation_count"],
        "excluded_property_variant_count": compiled[
            "excluded_property_variant_count"
        ],
        "classification_policy_count": compiled["classification_policy_count"],
        "native_classification_probe_count": compiled[
            "native_classification_probe_count"
        ],
        "native_invocation_executed_count": compiled[
            "native_classification_probe_count"
        ],
        "classification_decision_count": compiled[
            "classification_decision_count"
        ],
        "support_contract_reuse_count": compiled["support_contract_reuse_count"],
        "support_assignment_count": compiled["support_assignment_count"],
        "ready_intent_count": compiled["ready_intent_count"],
        "review_marker_count": compiled["review_marker_count"],
        "low_priority_review_marker_count": compiled[
            "low_priority_review_marker_count"
        ],
        "medium_priority_review_marker_count": compiled[
            "medium_priority_review_marker_count"
        ],
        "resolution_failure_count": compiled["resolution_failure_count"],
        "mass_support_contract_sha256": compiled["mass_support_contract"][
            "support_contract_sha256"
        ],
        "saved_plan_compilation_authorized": True,
        "entity_creation_authorized": False,
        "bulk_entity_execution_authorized": False,
        **zero_effects,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "external_patterns": [
            {
                "project": "hashicorp/terraform",
                "local_landing": "stable_authoring_intent_key_batch_instances_known_before_remote_actions",
                "installed": False,
            },
            {
                "project": "open-policy-agent/opa",
                "local_landing": "versioned_policy_input_result_decision_ledger",
                "installed": False,
            },
            {
                "project": "kubernetes/kubernetes",
                "local_landing": "single_aspect_classification_support_reconciliation_loop",
                "installed": False,
            },
        ],
        "routing": {
            "pause_internal_work": False,
            "next_internal_route": next_route_for_batch(batch_index),
            "next_action": (
                "Compile content-addressed saved TianGong Flow plans for the exact "
                f"{compiled['ready_intent_count']} ready batch-{batch_index} intents; "
                "keep entity creation and save-draft closed."
            ),
        },
    }
    outputs.mkdir(parents=True, exist_ok=True)
    for filename, content in output_rows.values():
        (outputs / filename).write_bytes(content)
    (outputs / f"{prefix}-support-resolution.json").write_bytes(
        _json_bytes(result, pretty=True)
    )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in EXPECTED_INPUT_NAMES:
        option = name.replace("_", "-")
        parser.add_argument(f"--{option}", type=Path, required=True)
        parser.add_argument(f"--{option}-binding", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--batch-index", type=int, default=BATCH_INDEX)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    contents = {}
    bindings = {}
    for name in EXPECTED_INPUT_NAMES:
        option = name
        contents[name] = getattr(args, option).read_bytes()
        bindings[name] = _parse_json(
            getattr(args, f"{option}_binding").read_bytes(), f"{name} binding"
        )
    result = execute_and_materialize(
        contents=contents,
        bindings=bindings,
        runtime_root=args.runtime_root,
        out_dir=args.out_dir,
        batch_index=args.batch_index,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
