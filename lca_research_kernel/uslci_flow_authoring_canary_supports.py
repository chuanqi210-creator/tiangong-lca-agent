"""Resolve native classification and support references for five Flow canaries."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Mapping, Sequence, Set, Tuple

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_flow_authoring_canary_supports/v1"
LOGIC_VERSION = "2026-07-18.1"
POLICY_VERSION = "2026-07-18.1"
AUTHORING_PLAN_CONTRACT = "lca_research_kernel.uslci_flow_authoring_plan/v1"
RUNTIME_MANIFEST_CONTRACT = "lca_research_kernel.portable_runtime_manifest/v1"
AUTHORITY_MANIFEST_CONTRACT = (
    "lca_research_kernel.uslci_flow_support_authority_manifest/v1"
)
NEXT_ROUTE = "compile-uslci-flow-authoring-canary-saved-plans"
EXPECTED_INPUT_NAMES = (
    "authoring_plan_report",
    "authoring_intents",
    "canary_actions",
    "support_requirements",
    "classification_requirements",
    "elementary_source_envelope",
    "product_source_envelope",
    "runtime_report",
    "runtime_manifest",
    "authority_manifest",
)

MASS_PROPERTY_ID = "93a60a56-a3c8-11da-a746-0800200b9a66"
AREA_TIME_PROPERTY_ID = "93a60a56-a3c8-21da-a746-0800200c9a66"
ENERGY_PROPERTY_ID = "f6811440-ee37-11de-8a39-0800200c9a66"

SUPPORT_SPECS = {
    MASS_PROPERTY_ID: {
        "flow_property_name": "Mass",
        "flow_property_version": "03.00.003",
        "reference_unit": "kg",
        "unit_group_id": "93a60a57-a4c8-11da-a746-0800200c9a66",
        "unit_group_name": "Units of mass",
        "unit_group_version": "03.00.003",
        "property_authority_id": "mass-flow-property",
        "unit_group_authority_id": "mass-unit-group",
        "native_addressability_kind": "native_builtin_and_explicit_reference",
    },
    AREA_TIME_PROPERTY_ID: {
        "flow_property_name": "Area*Time",
        "flow_property_version": "03.00.003",
        "reference_unit": "m2*a",
        "unit_group_id": "93a60a57-a3c8-20da-a746-0800200c9a66",
        "unit_group_name": "Units of area*time",
        "unit_group_version": "03.00.003",
        "property_authority_id": "area-time-flow-property",
        "unit_group_authority_id": "area-time-unit-group",
        "native_addressability_kind": "native_explicit_reference",
    },
    ENERGY_PROPERTY_ID: {
        "flow_property_name": "Energy",
        "flow_property_version": "00.00.000",
        "reference_unit": "MJ",
        "unit_group_id": "93a60a57-a3c8-11da-a746-0800200c9a66",
        "unit_group_name": "Units of energy",
        "unit_group_version": "03.00.003",
        "property_authority_id": "energy-flow-property",
        "unit_group_authority_id": "energy-unit-group",
        "native_addressability_kind": "native_explicit_reference",
    },
}

CLASSIFICATION_SPECS = {
    "Decabromodiphenyl oxide": {
        "category_type": "flow-elementary",
        "code": "1.2.3",
        "leaf_label": "Emissions to soil, unspecified",
        "required_source_type": "ELEMENTARY_FLOW",
        "required_category_tokens": ("elementary flows", "emission", "ground"),
        "required_direction": "Output",
        "confidence": "high",
        "review_priority": "low",
        "review_reason": (
            "USLCI uses the compartment label ground; TianGong names the nearest "
            "unspecified native compartment soil."
        ),
        "rationale": "Source emission/ground maps to the native unspecified soil-emission leaf.",
    },
    "Land use": {
        "category_type": "flow-elementary",
        "code": "3.1",
        "leaf_label": "Land occupation",
        "required_source_type": "ELEMENTARY_FLOW",
        "required_category_tokens": ("elementary flows", "resource", "ground", "forest"),
        "required_direction": "Input",
        "confidence": "medium",
        "review_priority": "medium",
        "review_reason": (
            "Area*time supports occupation, but the source label does not explicitly "
            "distinguish occupation from transformation."
        ),
        "rationale": "Area*time and terrestrial forest land use support the native land-occupation branch.",
    },
    "Energy, wind": {
        "category_type": "flow-elementary",
        "code": "2.3.5",
        "leaf_label": "Renewable energy resources from air",
        "required_source_type": "ELEMENTARY_FLOW",
        "required_category_tokens": ("elementary flows", "resource", "air"),
        "required_direction": "Input",
        "confidence": "high",
        "review_priority": None,
        "review_reason": None,
        "rationale": "Wind, air compartment and Energy jointly identify renewable energy from air.",
    },
    "Potassium": {
        "category_type": "flow-elementary",
        "code": "2.1.2",
        "leaf_label": "Non-renewable element resources from ground",
        "required_source_type": "ELEMENTARY_FLOW",
        "required_category_tokens": ("elementary flows", "resource", "ground"),
        "required_direction": "Input",
        "confidence": "medium",
        "review_priority": "medium",
        "review_reason": (
            "Element and ground are source facts; non-renewability is a retained "
            "scientific inference rather than an explicit source field."
        ),
        "rationale": "Potassium is an element resource extracted from ground and is non-renewable.",
    },
    "petroleum fuel, through transportation": {
        "category_type": "flow-product",
        "code": "33370",
        "leaf_label": "Fuel oils n.e.c.",
        "required_source_type": "PRODUCT_FLOW",
        "required_category_tokens": ("technosphere flows", "3241", "petroleum"),
        "required_direction": "Output",
        "confidence": "medium",
        "review_priority": "high",
        "review_reason": (
            "The source label includes transportation and omits petroleum grade; the "
            "CPC fuel-oil leaf is usable for the canary but requires later review."
        ),
        "rationale": "NAICS 3241 petroleum manufacturing and fuel identity support CPC fuel oils n.e.c.",
    },
}

RUNTIME_FILES = {
    "node": "bin/node",
    "tiangong_cli": "npm-runtime/node_modules/@tiangong-lca/cli/bin/tiangong-lca.js",
    "elementary_schema": (
        "npm-runtime/node_modules/@tiangong-lca/cli/assets/tidas-schemas/"
        "tidas_flows_elementary_category.json"
    ),
    "product_schema": (
        "npm-runtime/node_modules/@tiangong-lca/cli/assets/tidas-schemas/"
        "tidas_flows_product_category.json"
    ),
    "flow_build_plan": (
        "npm-runtime/node_modules/@tiangong-lca/cli/dist/src/lib/"
        "process-flow-build-plan.js"
    ),
}


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


def _jsonl_bytes(rows: Iterable[JsonObject]) -> bytes:
    return b"".join(_json_bytes(row) for row in rows)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


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
    if not rows:
        raise ValueError(f"{label} is empty")
    return rows


def _binding(value: Mapping[str, object], content: bytes, label: str) -> JsonObject:
    row = _object(value, label)
    path = _text(row.get("path"), f"{label} path")
    digest = _text(row.get("sha256"), f"{label} SHA-256")
    size = _integer(row.get("bytes"), f"{label} byte count")
    if digest != _sha256(content) or size != len(content):
        raise ValueError(f"{label} hash or byte count drifted")
    return {"path": path, "sha256": digest, "bytes": size}


def _artifact_binding(path: str, content: bytes) -> JsonObject:
    return {"path": path, "sha256": _sha256(content), "bytes": len(content)}


def input_version_for_bindings(bindings: Sequence[JsonObject]) -> str:
    return _sha256(
        _json_bytes(
            {
                "contract": CONTRACT,
                "logic_version": LOGIC_VERSION,
                "policy_version": POLICY_VERSION,
                "inputs": list(bindings),
            }
        )
    )[:16]


def build_authority_manifest() -> JsonObject:
    """Return the pinned read-only TianGong LCDN support authority requests."""

    base = "https://lcdn.tiangong.earth/datasetdetail"
    rows = []
    for property_id, spec in SUPPORT_SPECS.items():
        property_name = str(spec["flow_property_name"])
        display_name = "Area*time" if property_name == "Area*Time" else property_name
        property_version = str(spec["flow_property_version"])
        unit_group_id = str(spec["unit_group_id"])
        unit_group_name = str(spec["unit_group_name"])
        unit_group_version = str(spec["unit_group_version"])
        reference_unit = str(spec["reference_unit"])
        rows.append(
            {
                "authority_id": spec["property_authority_id"],
                "authority_kind": "flow_property",
                "url": (
                    f"{base}/flowproperty.xhtml?uuid={property_id}"
                    f"&version={property_version}"
                ),
                "media_type": "text/html",
                "expected_tokens": [
                    f"Flow Property Data set: {display_name}",
                    property_id,
                    unit_group_id,
                    f"{unit_group_name} ({reference_unit})",
                    property_version,
                ],
            }
        )
        rows.append(
            {
                "authority_id": spec["unit_group_authority_id"],
                "authority_kind": "unit_group",
                "url": (
                    f"{base}/unitgroup.xhtml?uuid={unit_group_id}"
                    f"&version={unit_group_version}"
                ),
                "media_type": "text/html",
                "expected_tokens": [
                    f"Unit Group Data set: {unit_group_name}",
                    unit_group_id,
                    reference_unit,
                    unit_group_version,
                ],
            }
        )
    rows.sort(key=lambda row: str(row["authority_id"]))
    return {
        "schema_version": 1,
        "contract": AUTHORITY_MANIFEST_CONTRACT,
        "allowed_host": "lcdn.tiangong.earth",
        "request_count": len(rows),
        "requests": rows,
    }


def extract_source_observations(
    envelope_contents: Sequence[bytes], required_flow_ids: Set[str]
) -> List[JsonObject]:
    """Extract exact, non-conflicting Flow observations from public envelopes."""

    observations: Dict[str, List[JsonObject]] = {flow_id: [] for flow_id in required_flow_ids}
    for envelope_index, content in enumerate(envelope_contents, start=1):
        envelope = _parse_json(content, f"source envelope {envelope_index}")
        if envelope.get("http_status") != 200:
            raise ValueError(f"source envelope {envelope_index} was not successful")
        payload = _object(envelope.get("payload"), f"source envelope {envelope_index} payload")
        exchanges = _array(payload.get("exchanges"), f"source envelope {envelope_index} exchanges")
        for exchange_value in exchanges:
            exchange = _object(exchange_value, "source exchange")
            flow = _object(exchange.get("flow"), "source exchange Flow")
            flow_id = flow.get("@id")
            if flow_id not in required_flow_ids:
                continue
            flow_property = _object(exchange.get("flowProperty"), "source FlowProperty")
            unit = _object(exchange.get("unit"), "source unit")
            observations[str(flow_id)].append(
                {
                    "source_flow_id": _text(flow_id, "source Flow ID"),
                    "source_flow_name": _text(flow.get("name"), "source Flow name"),
                    "source_flow_type": _text(flow.get("flowType"), "source Flow type"),
                    "source_category": _text(flow.get("category"), "source category"),
                    "source_category_is_in_repo": flow.get("categoryIsInRepo") is True,
                    "source_flow_is_in_repo": flow.get("isInRepo") is True,
                    "flow_property_id": _text(flow_property.get("@id"), "FlowProperty ID"),
                    "flow_property_name": _text(flow_property.get("name"), "FlowProperty name"),
                    "flow_property_is_in_repo": flow_property.get("isInRepo") is True,
                    "reference_unit": _text(flow_property.get("refUnit"), "reference unit"),
                    "exchange_unit_id": _text(unit.get("@id"), "exchange unit ID"),
                    "exchange_unit_name": _text(unit.get("name"), "exchange unit name"),
                    "direction": "Input" if exchange.get("isInput") is True else "Output",
                    "quantitative_reference": exchange.get("isQuantitativeReference") is True,
                    "source_envelope_index": envelope_index,
                    "source_request_url": _text(
                        envelope.get("request_url"), "source request URL"
                    ),
                }
            )
    missing = sorted(flow_id for flow_id, rows in observations.items() if not rows)
    if missing:
        raise ValueError(f"source observations are missing required Flows: {missing}")
    normalized = []
    for flow_id in sorted(observations):
        rows = observations[flow_id]
        comparable = [
            {key: value for key, value in row.items() if key not in {"source_envelope_index", "source_request_url"}}
            for row in rows
        ]
        canonical = {_sha256(_json_bytes(row)) for row in comparable}
        if len(canonical) != 1:
            raise ValueError(f"source Flow {flow_id} has conflicting observations")
        row = dict(rows[0])
        row["bound_exchange_observation_count"] = len(rows)
        row["source_observation_sha256"] = _sha256(_json_bytes(comparable[0]))
        normalized.append(row)
    return normalized


def validate_native_classification_probe(
    probe_value: Mapping[str, object], *, category_type: str, code: str
) -> JsonObject:
    """Validate one copied-TianGong classification-path response."""

    probe = _object(probe_value, "native classification probe")
    if (
        probe.get("status") != "completed"
        or probe.get("command") != "dataset classification path"
        or probe.get("category_type") != category_type
        or probe.get("code") != code
        or probe.get("blockers") != []
    ):
        if probe.get("code") != code:
            raise ValueError("native classification probe did not resolve the requested code")
        raise ValueError("native classification probe did not complete cleanly")
    expected_schema = (
        "tidas_flows_elementary_category.json"
        if category_type == "flow-elementary"
        else "tidas_flows_product_category.json"
    )
    if probe.get("schema_file") != expected_schema:
        raise ValueError("native classification probe used the wrong schema")
    path_values = _array(probe.get("path"), "native classification path")
    if not path_values:
        raise ValueError("native classification path is empty")
    path = [_object(value, "native classification node") for value in path_values]
    code_key = "@catId" if category_type == "flow-elementary" else "@classId"
    if path[-1].get(code_key) != code:
        raise ValueError("native classification path does not end at the requested code")
    leaf_label = _text(path[-1].get("#text"), "native classification leaf label")
    return {
        "category_type": category_type,
        "schema_file": expected_schema,
        "code": code,
        "path": path,
        "leaf_label": leaf_label,
    }


def validate_authority_response(
    requirement_value: Mapping[str, object],
    body: bytes,
    *,
    status: int,
    final_url: str,
    content_type: str,
) -> JsonObject:
    """Validate a read-only TianGong LCDN response and return an OCI-like descriptor."""

    requirement = _object(requirement_value, "authority requirement")
    requested_url = _text(requirement.get("url"), "authority URL")
    requested = urllib.parse.urlsplit(requested_url)
    final = urllib.parse.urlsplit(final_url)
    if requested.scheme != "https" or requested.hostname != "lcdn.tiangong.earth":
        raise ValueError("authority URL is outside the allow-listed TianGong host")
    if final.scheme != "https" or final.hostname != requested.hostname:
        raise ValueError("authority response escaped the allow-listed TianGong host")
    if status != 200:
        raise ValueError(f"authority response returned HTTP {status}")
    if "text/html" not in content_type.casefold():
        raise ValueError("authority response is not HTML")
    try:
        decoded = html.unescape(body.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise ValueError("authority response is not UTF-8") from error
    tokens = [
        _text(token, "authority expected token")
        for token in _array(requirement.get("expected_tokens"), "authority expected tokens")
    ]
    missing = [token for token in tokens if token not in decoded]
    if missing:
        raise ValueError(f"authority response is missing expected token: {missing[0]}")
    return {
        "schema_version": 1,
        "authority_id": _text(requirement.get("authority_id"), "authority ID"),
        "authority_kind": _text(requirement.get("authority_kind"), "authority kind"),
        "source": requested_url,
        "final_url": final_url,
        "media_type": "text/html",
        "response_content_type": content_type,
        "http_status": status,
        "bytes": len(body),
        "sha256": _sha256(body),
        "assertion_count": len(tokens),
        "assertions_passed": True,
    }


def compile_classification_decision(
    canary_value: Mapping[str, object],
    intent_value: Mapping[str, object],
    observation_value: Mapping[str, object],
    probe_value: Mapping[str, object],
) -> JsonObject:
    """Compile one evidence-bound classification decision with retained uncertainty."""

    canary = _object(canary_value, "canary")
    intent = _object(intent_value, "authoring intent")
    observation = _object(observation_value, "source observation")
    probe = _object(probe_value, "normalized native probe")
    target = _object(canary.get("target"), "canary target")
    name = _text(target.get("name"), "canary target name")
    if name not in CLASSIFICATION_SPECS:
        raise ValueError(f"canary classification policy is missing for {name}")
    spec = CLASSIFICATION_SPECS[name]
    source_id = _text(intent.get("source_flow_id"), "intent source Flow ID")
    if observation.get("source_flow_id") != source_id:
        raise ValueError("canary source observation does not match its intent")
    if observation.get("source_flow_name") != name:
        raise ValueError("canary source name drifted")
    if observation.get("source_flow_type") != spec["required_source_type"]:
        raise ValueError("canary source Flow type is incompatible with classification")
    if observation.get("direction") != spec["required_direction"]:
        raise ValueError("canary source direction is incompatible with classification")
    source_category = _text(observation.get("source_category"), "source category")
    folded = source_category.casefold()
    if any(str(token).casefold() not in folded for token in spec["required_category_tokens"]):
        raise ValueError("canary source category does not support classification policy")
    if (
        probe.get("category_type") != spec["category_type"]
        or probe.get("code") != spec["code"]
        or probe.get("leaf_label") != spec["leaf_label"]
    ):
        raise ValueError("native classification probe does not match canary policy")
    review_priority = spec["review_priority"]
    decision_status = "resolved" if review_priority is None else "resolved_with_review_marker"
    return {
        "schema_version": 1,
        "canary_index": _integer(canary.get("canary_index"), "canary index"),
        "authoring_intent_key": _text(
            canary.get("authoring_intent_key"), "authoring intent key"
        ),
        "position": _integer(canary.get("position"), "canary position"),
        "source_flow_id": source_id,
        "source_flow_name": name,
        "source_category": source_category,
        "category_type": spec["category_type"],
        "classification_code": spec["code"],
        "classification_path": probe.get("path"),
        "classification_leaf_label": spec["leaf_label"],
        "decision_status": decision_status,
        "confidence": spec["confidence"],
        "decision_rationale": spec["rationale"],
        "review_required": review_priority is not None,
        "review_priority": review_priority,
        "review_reason": spec["review_reason"],
        "saved_plan_compilation_authorized": True,
        "entity_creation_authorized": False,
        "bulk_release_authorized": False,
    }


def _safe_relative_path(value: object, label: str) -> str:
    raw = _text(value, label)
    path = PurePosixPath(raw)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or str(path) != raw
    ):
        raise ValueError(f"{label} is unsafe")
    return raw


def verify_runtime_files(runtime_root: Path, manifest_value: Mapping[str, object]) -> JsonObject:
    """Verify exact copied runtime files against the accepted closure manifest."""

    manifest = _object(manifest_value, "runtime manifest")
    bindings = _array(manifest.get("file_bindings"), "runtime file bindings")
    by_path = {
        row.get("path"): row
        for row in (_object(value, "runtime file binding") for value in bindings)
    }
    root = runtime_root.resolve()
    verified = {}
    for role, relative in RUNTIME_FILES.items():
        expected = by_path.get(relative)
        if not isinstance(expected, dict):
            raise ValueError(f"runtime manifest does not bind {relative}")
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as error:
            raise ValueError("runtime file escaped copied runtime root") from error
        if not path.is_file():
            raise ValueError(f"copied runtime file is missing: {relative}")
        content = path.read_bytes()
        if expected.get("sha256") != _sha256(content) or expected.get("bytes") != len(content):
            raise ValueError(f"copied runtime file drifted: {relative}")
        verified[role] = {
            "path": relative,
            "sha256": _sha256(content),
            "bytes": len(content),
        }
    builder = (root / RUNTIME_FILES["flow_build_plan"]).read_text(encoding="utf-8")
    required_builder_tokens = (
        "flowPropertyPlan.referencePropertyId",
        "flow_property_plan.reference_property_id",
        MASS_PROPERTY_ID,
    )
    if any(token not in builder for token in required_builder_tokens):
        raise ValueError("copied Flow build-plan explicit reference contract drifted")
    return {
        "schema_version": 1,
        "runtime_tree_sha256": manifest.get("tree_sha256"),
        "verified_files": verified,
        "explicit_flow_property_reference_supported": True,
        "native_builtin_mass_id": MASS_PROPERTY_ID,
    }


def _run_native_probes(runtime_root: Path, raw_dir: Path) -> Tuple[List[JsonObject], List[JsonObject]]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    node = runtime_root / RUNTIME_FILES["node"]
    cli = runtime_root / RUNTIME_FILES["tiangong_cli"]
    probes = []
    descriptors = []
    for index, (name, spec) in enumerate(CLASSIFICATION_SPECS.items(), start=1):
        category_type = str(spec["category_type"])
        code = str(spec["code"])
        completed = subprocess.run(
            [
                str(node),
                str(cli),
                "dataset",
                "classification",
                "path",
                "--type",
                category_type,
                "--code",
                code,
                "--json",
            ],
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise ValueError(
                f"native classification probe failed for {name}: "
                f"{completed.stderr.decode('utf-8', errors='replace').strip()}"
            )
        raw = completed.stdout
        probe_value = _parse_json(raw, f"native classification probe {index}")
        normalized = validate_native_classification_probe(
            probe_value, category_type=category_type, code=code
        )
        normalized["probe_index"] = index
        normalized["source_flow_name"] = name
        filename = f"{index:02d}-{category_type}-{code.replace('.', '-')}.json"
        (raw_dir / filename).write_bytes(raw)
        descriptor = _artifact_binding(f"native-probes/{filename}", raw)
        descriptor.update(
            {
                "probe_index": index,
                "source_flow_name": name,
                "category_type": category_type,
                "code": code,
                "media_type": "application/json",
            }
        )
        normalized["raw_probe_sha256"] = descriptor["sha256"]
        probes.append(normalized)
        descriptors.append(descriptor)
    return probes, descriptors


def _fetch_authorities(
    authority_manifest: Mapping[str, object], raw_dir: Path, *, timeout: float = 30.0
) -> Tuple[List[JsonObject], List[JsonObject]]:
    manifest = _object(authority_manifest, "authority manifest")
    if (
        manifest.get("contract") != AUTHORITY_MANIFEST_CONTRACT
        or manifest.get("allowed_host") != "lcdn.tiangong.earth"
        or manifest.get("request_count") != 6
    ):
        raise ValueError("support authority manifest is not accepted")
    requests = [
        _object(value, "authority request")
        for value in _array(manifest.get("requests"), "authority requests")
    ]
    raw_dir.mkdir(parents=True, exist_ok=True)
    descriptors = []
    raw_bindings = []
    for index, requirement in enumerate(requests, start=1):
        authority_id = _text(requirement.get("authority_id"), "authority ID")
        request = urllib.request.Request(
            _text(requirement.get("url"), "authority URL"),
            headers={
                "Accept": "text/html",
                "User-Agent": "TianGong-LCA-Research-Kernel/1.0 support-resolution",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
                status = int(response.status)
                final_url = response.geturl()
                content_type = response.headers.get("Content-Type", "")
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ValueError(f"TianGong LCDN authority read failed: {authority_id}") from error
        descriptor = validate_authority_response(
            requirement,
            body,
            status=status,
            final_url=final_url,
            content_type=content_type,
        )
        filename = f"{index:02d}-{authority_id}.html"
        (raw_dir / filename).write_bytes(body)
        descriptor["raw_response_path"] = f"authority-responses/{filename}"
        descriptors.append(descriptor)
        raw_binding = _artifact_binding(f"authority-responses/{filename}", body)
        raw_binding.update(
            {
                "authority_id": authority_id,
                "media_type": "text/html",
                "source": requirement["url"],
            }
        )
        raw_bindings.append(raw_binding)
    return descriptors, raw_bindings


def _compile_support_contracts(
    requirements: Sequence[Mapping[str, object]],
    authority_descriptors: Sequence[Mapping[str, object]],
    runtime_evidence: Mapping[str, object],
) -> List[JsonObject]:
    if runtime_evidence.get("explicit_flow_property_reference_supported") is not True:
        raise ValueError("native explicit FlowProperty reference contract is unavailable")
    authorities = {row.get("authority_id"): row for row in authority_descriptors}
    by_property = {row.get("flow_property_id"): row for row in requirements}
    if set(by_property) != set(SUPPORT_SPECS):
        raise ValueError("support requirements do not match the three accepted contracts")
    contracts = []
    for index, property_id in enumerate(
        (MASS_PROPERTY_ID, AREA_TIME_PROPERTY_ID, ENERGY_PROPERTY_ID), start=1
    ):
        requirement = _object(by_property[property_id], "support requirement")
        spec = SUPPORT_SPECS[property_id]
        if (
            requirement.get("flow_property_name") != spec["flow_property_name"]
            or requirement.get("reference_unit") != spec["reference_unit"]
            or requirement.get("required_probe")
            != "native_local_support_reference_addressability"
        ):
            raise ValueError(f"support requirement drifted for {property_id}")
        property_authority = authorities.get(spec["property_authority_id"])
        unit_authority = authorities.get(spec["unit_group_authority_id"])
        if not isinstance(property_authority, dict) or not isinstance(unit_authority, dict):
            raise ValueError(f"authority evidence is incomplete for {property_id}")
        contracts.append(
            {
                "schema_version": 1,
                "support_contract_index": index,
                "flow_property_id": property_id,
                "flow_property_name": spec["flow_property_name"],
                "flow_property_version": spec["flow_property_version"],
                "flow_property_uri": next(
                    row["source"]
                    for row in authority_descriptors
                    if row.get("authority_id") == spec["property_authority_id"]
                ),
                "unit_group_id": spec["unit_group_id"],
                "unit_group_name": spec["unit_group_name"],
                "unit_group_version": spec["unit_group_version"],
                "unit_group_uri": next(
                    row["source"]
                    for row in authority_descriptors
                    if row.get("authority_id") == spec["unit_group_authority_id"]
                ),
                "reference_unit": spec["reference_unit"],
                "native_addressability_kind": spec["native_addressability_kind"],
                "source_intent_count": requirement.get("intent_count"),
                "property_authority_sha256": property_authority.get("sha256"),
                "unit_group_authority_sha256": unit_authority.get("sha256"),
                "support_status": "resolved_for_native_saved_plan",
                "saved_plan_reference_authorized": True,
                "remote_resolution_required_for_publication": True,
                "entity_creation_authorized": False,
            }
        )
    return contracts


def resolve_canary_supports(
    *,
    canaries: Sequence[Mapping[str, object]],
    intents: Sequence[Mapping[str, object]],
    support_requirements: Sequence[Mapping[str, object]],
    classification_requirements: Sequence[Mapping[str, object]],
    observations: Sequence[Mapping[str, object]],
    probes: Sequence[Mapping[str, object]],
    authority_descriptors: Sequence[Mapping[str, object]],
    runtime_evidence: Mapping[str, object],
) -> Tuple[List[JsonObject], List[JsonObject], List[JsonObject], List[JsonObject]]:
    """Resolve three support contracts and five canary classification decisions."""

    if len(canaries) != 5 or len(classification_requirements) != 3:
        raise ValueError("canary or classification requirement count drifted")
    intent_by_key = {row.get("authoring_intent_key"): row for row in intents}
    observation_by_id = {row.get("source_flow_id"): row for row in observations}
    probe_by_name = {row.get("source_flow_name"): row for row in probes}
    support_contracts = _compile_support_contracts(
        support_requirements, authority_descriptors, runtime_evidence
    )
    support_by_property = {row["flow_property_id"]: row for row in support_contracts}
    decisions = []
    readiness = []
    risks = []
    for canary_value in sorted(canaries, key=lambda row: int(row.get("canary_index", 0))):
        canary = _object(canary_value, "canary")
        intent_key = canary.get("authoring_intent_key")
        intent = intent_by_key.get(intent_key)
        if not isinstance(intent, dict):
            raise ValueError("canary authoring intent is missing")
        observation = observation_by_id.get(intent.get("source_flow_id"))
        target = _object(canary.get("target"), "canary target")
        probe = probe_by_name.get(target.get("name"))
        if not isinstance(observation, dict) or not isinstance(probe, dict):
            raise ValueError("canary source or native probe evidence is missing")
        decision = compile_classification_decision(canary, intent, observation, probe)
        support = support_by_property.get(intent.get("flow_property_id"))
        if not isinstance(support, dict):
            raise ValueError("canary support contract is missing")
        if (
            observation.get("flow_property_id") != support.get("flow_property_id")
            or observation.get("flow_property_name") != support.get("flow_property_name")
            or observation.get("reference_unit") != support.get("reference_unit")
            or intent.get("reference_unit") != support.get("reference_unit")
        ):
            raise ValueError("canary support tuple conflicts across source and plan")
        decisions.append(decision)
        risk_id = None
        if decision["review_required"]:
            risk_id = f"canary-classification-review:{intent_key}"
            risks.append(
                {
                    "schema_version": 1,
                    "risk_id": risk_id,
                    "canary_index": decision["canary_index"],
                    "authoring_intent_key": intent_key,
                    "source_flow_id": decision["source_flow_id"],
                    "source_flow_name": decision["source_flow_name"],
                    "severity": decision["review_priority"],
                    "summary": decision["review_reason"],
                    "blocks_saved_plan_compilation": False,
                    "blocks_entity_creation": False,
                    "blocks_bulk_release": True,
                    "human_review_retained": True,
                }
            )
        readiness.append(
            {
                "schema_version": 1,
                "canary_index": decision["canary_index"],
                "authoring_intent_key": intent_key,
                "position": decision["position"],
                "source_flow_id": decision["source_flow_id"],
                "target_flow_id": target.get("id"),
                "classification_code": decision["classification_code"],
                "classification_leaf_label": decision["classification_leaf_label"],
                "flow_property_id": support["flow_property_id"],
                "flow_property_version": support["flow_property_version"],
                "unit_group_id": support["unit_group_id"],
                "unit_group_version": support["unit_group_version"],
                "reference_unit": support["reference_unit"],
                "review_risk_id": risk_id,
                "readiness_status": (
                    "ready_for_saved_plan_with_review_marker"
                    if risk_id
                    else "ready_for_saved_plan"
                ),
                "saved_plan_compilation_authorized": True,
                "entity_creation_authorized": False,
                "bulk_release_authorized": False,
            }
        )
    if len(decisions) != 5 or len(readiness) != 5 or len(risks) != 4:
        raise ValueError("canary resolution counts violated the accepted policy")
    return support_contracts, decisions, readiness, risks


def resolve_and_materialize(
    *,
    contents: Mapping[str, bytes],
    bindings: Mapping[str, Mapping[str, object]],
    runtime_root: Path,
    out_dir: Path,
) -> JsonObject:
    verified_bindings = [
        _binding(bindings[name], contents[name], f"{name} binding")
        for name in EXPECTED_INPUT_NAMES
    ]
    input_version = input_version_for_bindings(verified_bindings)
    report = _parse_json(contents["authoring_plan_report"], "authoring plan report")
    if (
        report.get("contract") != AUTHORING_PLAN_CONTRACT
        or report.get("status") != "uslci_flow_authoring_plan_compiled"
        or report.get("authoring_intent_count") != 1456
        or report.get("canary_action_count") != 5
        or report.get("support_reference_requirement_count") != 3
        or report.get("classification_requirement_count") != 3
        or report.get("routing", {}).get("next_internal_route")
        != "resolve-uslci-flow-authoring-canary-supports"
    ):
        raise ValueError("authoring plan is not accepted for canary support resolution")
    intents = _parse_jsonl(contents["authoring_intents"], "authoring intents")
    canaries = _parse_jsonl(contents["canary_actions"], "canary actions")
    support_requirements = _parse_jsonl(
        contents["support_requirements"], "support requirements"
    )
    classification_requirements = _parse_jsonl(
        contents["classification_requirements"], "classification requirements"
    )
    plan_artifacts = _object(report.get("artifact_bindings"), "authoring artifacts")
    for name in (
        "authoring_intents",
        "canary_actions",
        "support_requirements",
        "classification_requirements",
    ):
        expected = _object(plan_artifacts.get(name), f"authoring {name} binding")
        actual = bindings[name]
        if expected.get("sha256") != actual.get("sha256") or expected.get("bytes") != actual.get("bytes"):
            raise ValueError(f"authoring plan binding drifted for {name}")
    required_ids = {
        _text(row.get("source_flow_id"), "canary source Flow ID")
        for row in intents
        if row.get("authoring_intent_key")
        in {canary.get("authoring_intent_key") for canary in canaries}
    }
    if len(required_ids) != 5:
        raise ValueError("five canary source Flow IDs were not recovered")
    observations = extract_source_observations(
        [contents["elementary_source_envelope"], contents["product_source_envelope"]],
        required_ids,
    )
    runtime_manifest = _parse_json(contents["runtime_manifest"], "runtime manifest")
    runtime_evidence = verify_runtime_files(runtime_root, runtime_manifest)
    authority_manifest = _parse_json(contents["authority_manifest"], "authority manifest")

    outputs = out_dir / "outputs"
    native_raw_dir = outputs / "native-probes"
    authority_raw_dir = outputs / "authority-responses"
    probes, native_raw_bindings = _run_native_probes(runtime_root, native_raw_dir)
    authority_descriptors, authority_raw_bindings = _fetch_authorities(
        authority_manifest, authority_raw_dir
    )
    support_contracts, decisions, readiness, risks = resolve_canary_supports(
        canaries=canaries,
        intents=intents,
        support_requirements=support_requirements,
        classification_requirements=classification_requirements,
        observations=observations,
        probes=probes,
        authority_descriptors=authority_descriptors,
        runtime_evidence=runtime_evidence,
    )

    output_rows = {
        "source_observations": (
            "uslci-flow-canary-source-observations.jsonl",
            _jsonl_bytes(observations),
        ),
        "native_classification_probes": (
            "uslci-flow-canary-native-classification-probes.jsonl",
            _jsonl_bytes(probes),
        ),
        "authority_response_descriptors": (
            "uslci-flow-canary-authority-response-descriptors.jsonl",
            _jsonl_bytes(authority_descriptors),
        ),
        "runtime_evidence": (
            "uslci-flow-canary-runtime-evidence.json",
            _json_bytes(runtime_evidence, pretty=True),
        ),
        "support_contracts": (
            "uslci-flow-canary-support-contracts.jsonl",
            _jsonl_bytes(support_contracts),
        ),
        "classification_decisions": (
            "uslci-flow-canary-classification-decisions.jsonl",
            _jsonl_bytes(decisions),
        ),
        "canary_readiness": (
            "uslci-flow-canary-readiness.jsonl",
            _jsonl_bytes(readiness),
        ),
        "risk_markers": (
            "uslci-flow-canary-risk-markers.jsonl",
            _jsonl_bytes(risks),
        ),
        "failures": ("uslci-flow-canary-support-failures.jsonl", b""),
    }
    artifact_bindings = {
        key: _artifact_binding(filename, content)
        for key, (filename, content) in output_rows.items()
    }
    resolution_version = _sha256(
        _json_bytes(
            {
                "contract": CONTRACT,
                "logic_version": LOGIC_VERSION,
                "policy_version": POLICY_VERSION,
                "input_version": input_version,
                "support_contracts": support_contracts,
                "classification_decisions": decisions,
                "canary_readiness": readiness,
            }
        )
    )[:16]
    result = {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "policy_version": POLICY_VERSION,
        "status": "uslci_flow_authoring_canary_supports_resolved",
        "scope_id": report.get("scope_id"),
        "revision": report.get("revision"),
        "input_version": input_version,
        "source_authoring_plan_version": report.get("authoring_plan_version"),
        "support_resolution_version": resolution_version,
        "input_bindings": verified_bindings,
        "artifact_bindings": artifact_bindings,
        "native_raw_artifact_bindings": native_raw_bindings,
        "authority_raw_artifact_bindings": authority_raw_bindings,
        "source_observation_count": len(observations),
        "native_classification_probe_count": len(probes),
        "authority_response_count": len(authority_descriptors),
        "support_contract_count": len(support_contracts),
        "classification_decision_count": len(decisions),
        "ready_canary_count": len(readiness),
        "review_marker_count": len(risks),
        "high_priority_review_marker_count": sum(
            row["severity"] == "high" for row in risks
        ),
        "resolution_failure_count": 0,
        "native_invocation_executed_count": 5,
        "network_read_executed_count": 6,
        "remote_candidate_search_executed_count": 0,
        "saved_flow_plan_compiled_count": 0,
        "flow_creation_executed_count": 0,
        "flow_materialization_executed_count": 0,
        "save_draft_executed_count": 0,
        "process_creation_executed_count": 0,
        "model_connection_executed_count": 0,
        "model_mutation_executed_count": 0,
        "external_write_executed_count": 0,
        "publication_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "entity_creation_authorized_count": 0,
        "bulk_release_authorized": False,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "external_patterns": [
            {
                "project": "kubernetes/client-go",
                "local_landing": "copied_runtime_native_classification_discovery_and_invalidation",
                "installed": False,
            },
            {
                "project": "opencontainers/image-spec",
                "local_landing": "source_media_type_bytes_sha256_descriptors",
                "installed": False,
            },
            {
                "project": "open-policy-agent/opa",
                "local_landing": "evidence_policy_result_and_review_decision_records",
                "installed": False,
            },
            {
                "project": "argoproj/argo-rollouts",
                "local_landing": "canary_saved_plan_gate_without_bulk_release",
                "installed": False,
            },
        ],
        "routing": {"next_internal_route": NEXT_ROUTE, "pause_internal_work": False},
        "next_action": (
            "Compile exact native saved Flow plans for the five resolved canaries; "
            "retain review markers and keep bulk release closed."
        ),
    }
    outputs.mkdir(parents=True, exist_ok=True)
    for filename, content in output_rows.values():
        (outputs / filename).write_bytes(content)
    report_path = outputs / "uslci-flow-authoring-canary-support-resolution.json"
    report_path.write_bytes(_json_bytes(result, pretty=True))
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve TianGong classification and support references for five Flow canaries."
    )
    for name in EXPECTED_INPUT_NAMES:
        option = name.replace("_", "-")
        parser.add_argument(f"--{option}", type=Path, required=True)
        parser.add_argument(f"--{option}-binding", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    contents = {}
    bindings = {}
    for name in EXPECTED_INPUT_NAMES:
        option = name.replace("_", "-")
        source_path = getattr(args, name)
        binding_path = getattr(args, f"{name}_binding")
        contents[name] = source_path.read_bytes()
        bindings[name] = _parse_json(binding_path.read_bytes(), f"{option} binding")
    resolve_and_materialize(
        contents=contents,
        bindings=bindings,
        runtime_root=args.runtime_root,
        out_dir=args.out_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
