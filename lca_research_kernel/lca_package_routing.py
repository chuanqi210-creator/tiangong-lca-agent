"""State-driven routing for candidate LCA-native research packages."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any


_FORMAT_CAPABILITIES = {
    "tiangong_tidas_package": "tiangong.validation.tidas",
    "tidas_json_ld": "tiangong.validation.tidas",
    "openlca_json_ld": "tiangong.tidas-tools.tidas-import",
    "ilcd": "tiangong.tidas-tools.tidas-import",
    "ecospold1": "tiangong.tidas-tools.tidas-import",
    "ecospold2": "tiangong.tidas-tools.tidas-import",
    "simapro_csv": "tiangong.tidas-tools.tidas-import",
    "openlca_zolca": "tiangong.calculation.openlca-ipc",
}
_LCA_EXTENSIONS = {
    ".jsonld", ".olca", ".spold", ".zolca", ".ilcd", ".csv",
}
_ALLOWED_MEMBER_DISPOSITIONS = {"derived_candidate", "input_artifact_candidate"}


def _members(package: Mapping[str, object]) -> list[dict[str, Any]]:
    rows = package.get("members")
    if rows is None and isinstance(package.get("archive_manifest"), Mapping):
        rows = package["archive_manifest"].get("members")  # type: ignore[index,union-attr]
    if not isinstance(rows, list):
        return []
    return [
        deepcopy(dict(row))
        for row in rows
        if isinstance(row, Mapping)
        and row.get("disposition") in _ALLOWED_MEMBER_DISPOSITIONS
    ]


def _sample(row: Mapping[str, object]) -> str:
    value = row.get("sample")
    return value if isinstance(value, str) else ""


def _json(row: Mapping[str, object]) -> object:
    try:
        return json.loads(_sample(row))
    except (json.JSONDecodeError, RecursionError):
        return None


def _is_openlca_object(row: Mapping[str, object], kind: str) -> bool:
    value = _json(row)
    if not isinstance(value, Mapping):
        return False
    object_type = str(value.get("@type") or value.get("type") or "").casefold()
    return object_type == kind.casefold() and isinstance(value.get("@id") or value.get("id"), str)


def _detect(package: Mapping[str, object]) -> tuple[str | None, list[str]]:
    rows = _members(package)
    signatures: list[str] = []
    single_type = package.get("detected_type")
    single_sample = package.get("sample")
    if isinstance(single_type, Mapping) and single_type.get("mime") == "application/x-sqlite3":
        sample = single_sample if isinstance(single_sample, str) else ""
        lowered = sample.casefold()
        if "sqlite format 3" in lowered and "openlca" in lowered and "tbl_processes" in lowered and "tbl_flows" in lowered:
            return "openlca_zolca", ["sqlite3_header", "openlca_schema_markers"]

    all_signatures = {
        str(signature)
        for row in rows
        for signature in (
            row.get("format_signatures")
            if isinstance(row.get("format_signatures"), list)
            else []
        )
    }
    if "tiangong_tidas_package_manifest" in all_signatures:
        return "tiangong_tidas_package", ["tiangong_tidas_package_manifest"]
    if {"tidas_context", "tidas_dataset_root"} <= all_signatures:
        return "tidas_json_ld", ["tidas_context", "tidas_dataset_root"]
    process_rows = [
        row
        for row in rows
        if str(row.get("path", "")).casefold().startswith("processes/")
        and "openlca_object:process" in row.get("format_signatures", [])
    ]
    flow_rows = [
        row
        for row in rows
        if str(row.get("path", "")).casefold().startswith("flows/")
        and "openlca_object:flow" in row.get("format_signatures", [])
    ]
    if process_rows and flow_rows:
        return "openlca_json_ld", [
            "openlca_process_object", "openlca_flow_object", "openlca_directory_roles"
        ]
    if {
        "ilcd_root:processdataset",
        "ilcd_root:flowdataset",
        "ilcd_root:sourcedataset",
        "ilcd_root:contactdataset",
    } <= all_signatures:
        return "ilcd", ["ilcd_directory_roles", "ilcd_xml_roots"]
    if "ecospold2_activity_dataset" in all_signatures:
        return "ecospold2", ["ecospold2_xml_root", "activity_dataset"]
    if "ecospold1_dataset" in all_signatures:
        return "ecospold1", ["ecospold1_xml_root", "dataset"]
    if {"simapro_version_header", "simapro_section_header"} <= all_signatures:
        return "simapro_csv", ["simapro_version_header", "simapro_section_header"]

    for row in rows:
        value = _json(row)
        if isinstance(value, Mapping):
            context = value.get("@context")
            context_text = json.dumps(context, ensure_ascii=False) if context is not None else ""
            if "tidas" in context_text.casefold() and any(key in value for key in ("processDataSet", "flowDataSet", "sourceDataSet", "contactDataSet")):
                return "tidas_json_ld", ["tidas_context", "tidas_dataset_root"]

    processes = [row for row in rows if str(row.get("path", "")).casefold().startswith("processes/") and _is_openlca_object(row, "Process")]
    flows = [row for row in rows if str(row.get("path", "")).casefold().startswith("flows/") and _is_openlca_object(row, "Flow")]
    if processes and flows:
        return "openlca_json_ld", ["openlca_process_object", "openlca_flow_object", "openlca_directory_roles"]

    ilcd_roots = {
        "processes": "processdataset",
        "flows": "flowdataset",
        "sources": "sourcedataset",
        "contacts": "contactdataset",
    }
    observed = set()
    for row in rows:
        path = str(row.get("path", "")).casefold()
        text = _sample(row).casefold()
        for directory, root in ilcd_roots.items():
            if f"/{directory}/" in f"/{path}" and f"<{root}" in text and "lca.jrc.it/ilcd" in text:
                observed.add(directory)
    if observed == set(ilcd_roots):
        return "ilcd", ["ilcd_directory_roles", "ilcd_xml_roots"]

    for row in rows:
        text = _sample(row)
        lowered = text.casefold()
        if re.search(r"<\s*ecospold(?:\s|>)", lowered):
            if "ecospold02" in lowered or "activitydataset" in lowered:
                return "ecospold2", ["ecospold2_xml_root", "activity_dataset"]
            if "<dataset" in lowered:
                return "ecospold1", ["ecospold1_xml_root", "dataset"]
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines and re.fullmatch(r"\{SimaPro\s+[^}]+\}", lines[0], re.IGNORECASE) and {line.casefold() for line in lines[1:]} & {"process", "products", "materials/fuels"}:
            return "simapro_csv", ["simapro_version_header", "simapro_section_header"]
    return None, signatures


def _catalog_rows(gateway: object) -> dict[str, dict[str, Any]]:
    if gateway is None or not hasattr(gateway, "list_capabilities"):
        return {}
    rows = gateway.list_capabilities({"route": "lca-package-candidate"})  # type: ignore[attr-defined]
    if not isinstance(rows, list):
        return {}
    return {
        str(row["capability_id"]): deepcopy(dict(row))
        for row in rows
        if isinstance(row, Mapping) and isinstance(row.get("capability_id"), str)
    }


def detect_lca_package_candidate(
    *,
    detected_type: Mapping[str, object],
    payload_sample: bytes,
) -> dict[str, Any] | None:
    """Classify a single file only when its bytes carry a native signature."""

    sample_text = payload_sample.decode("utf-8", errors="replace")
    mime = str(detected_type.get("mime") or "")
    suffix = {
        "application/json": ".jsonld",
        "application/xml": ".xml",
        "text/xml": ".xml",
        "text/csv": ".csv",
        "application/x-sqlite3": ".zolca",
    }.get(mime, "")
    synthetic = {
        "members": [
            {
                "path": "candidate" + suffix,
                "sample": sample_text,
                "detected_type": deepcopy(dict(detected_type)),
                "disposition": "input_artifact_candidate",
            }
        ]
    }
    package = {"detected_type": deepcopy(dict(detected_type)), "sample": sample_text}
    detected, signatures = _detect(package)
    if detected is None:
        detected, signatures = _detect(synthetic)
    if detected is None:
        return None
    return {
        "contract": "lca_research_kernel.lca_package_candidate/v1",
        "detected_candidate_format": detected,
        "candidate_signatures": signatures,
        "candidate_only": True,
        "scientifically_validated": False,
    }


def is_lca_extension_candidate(filename: str) -> bool:
    return Path(filename).suffix.casefold() in _LCA_EXTENSIONS


def route_lca_package(
    package: Mapping[str, object],
    *,
    capability_gateway: object,
    declared_filename: str,
) -> dict[str, Any]:
    """Return a route decision; classification and native output remain candidates."""

    detected, signatures = _detect(package)
    extension_only = detected is None and is_lca_extension_candidate(declared_filename)
    classification = (
        "candidate_detected" if detected is not None
        else "candidate_requires_confirmation" if extension_only
        else "not_lca_package_candidate"
    )
    required = _FORMAT_CAPABILITIES.get(detected or "")
    rows = _catalog_rows(capability_gateway)
    descriptor = rows.get(required or "")
    runtime_available = bool(
        descriptor and descriptor.get("runtime_available") is True
    )
    route_status = (
        "not_routed" if required is None
        else "native_capability_available" if runtime_available
        else "native_capability_unavailable"
    )
    effect = descriptor.get("effect") if descriptor else None
    degradation = (
        "preserve-manifest-and-request-native-runtime"
        if required and not runtime_available
        else "retain-candidate-until-format-confirmed"
        if extension_only
        else "none"
    )
    return {
        "contract": "lca_research_kernel.lca_package_route_decision/v1",
        "classification_status": classification,
        "detected_candidate_format": detected,
        "candidate_signatures": signatures,
        "consumed_member_hashes": sorted(
            {
                str(row["sha256"])
                for row in _members(package)
                if isinstance(row.get("sha256"), str)
            }
        ),
        "required_native_capability": required,
        "native_capability_descriptor": descriptor,
        "route_status": route_status,
        "permissions": [effect] if isinstance(effect, str) else [],
        "expected_state_change": {
            "from": "candidate_package",
            "to": "candidate_native_assessment",
            "project_record_acceptance_separate": True,
        },
        "acceptance_conditions": [
            "manifest and every consumed member hash remain exact",
            "catalog source identity and version remain exact",
            "native validator or importer emits an operation receipt",
            "LCA Domain Kernel independently assesses usability, dependencies, and validity",
            "Assurance separately authorizes any Project Record acceptance",
        ],
        "degradation_route": degradation,
        "manifest_preserved": True,
        "shadow_importer_created": False,
        "candidate_only": True,
        "scientifically_validated": False,
        "project_record_write": "not_performed",
    }
