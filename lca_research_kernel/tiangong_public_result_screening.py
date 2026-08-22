"""Compile fail-closed screening evidence from TianGong public result surfaces."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import PurePosixPath
from typing import Dict, List, Mapping, Optional, Sequence, Tuple


JsonObject = Dict[str, object]

PUBLIC_RESULT_ROOT_LEDGER_CONTRACT = (
    "lca_research_kernel.tiangong_public_result_root_ledger/v1"
)
LCIA_METHOD_UNIT_INDEX_CONTRACT = (
    "lca_research_kernel.tiangong_lcia_method_unit_index/v1"
)
PUBLIC_RESULT_SCREENING_BATCH_CONTRACT = (
    "lca_research_kernel.tiangong_public_result_screening_batch/v1"
)
BOUND_PUBLIC_LCIA_RESULT_CONTRACT = (
    "lca_research_kernel.tiangong_bound_public_lcia_result/v1"
)
CANDIDATE_NATIVE_EVIDENCE_CONTRACT = (
    "lca_research_kernel.tiangong_candidate_native_evidence/v1"
)

_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EXACT_DATASET_REF = re.compile(
    r"^(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})"
    r"@(?P<version>[0-9]{2}\.[0-9]{2}\.[0-9]{3})$",
    re.IGNORECASE,
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return copy.deepcopy(value)


def _text(value: object) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _binding(value: object, label: str) -> JsonObject:
    row = _object(value, label)
    role = _text(row.get("role"))
    path = _text(row.get("path"))
    sha256 = _text(row.get("sha256"))
    if role is None or path is None or sha256 is None:
        raise ValueError(f"{label} is incomplete")
    pure = PurePosixPath(path.replace("\\", "/"))
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"{label}.path must be workspace-relative")
    if not _HEX_SHA256.fullmatch(sha256):
        raise ValueError(f"{label}.sha256 must be a lowercase SHA-256")
    return {"role": role, "path": str(pure), "sha256": sha256}


def _exact_ref(value: object, label: str) -> Tuple[str, str, str]:
    token = _text(value)
    match = _EXACT_DATASET_REF.fullmatch(token or "")
    if match is None:
        raise ValueError(f"{label} must be an exact UUID@NN.NN.NNN reference")
    dataset_id = match.group("id").lower()
    version = match.group("version")
    return dataset_id, version, f"{dataset_id}@{version}"


def _extract_roots(command: object) -> List[JsonObject]:
    if not isinstance(command, list) or not command:
        raise ValueError("snapshotBuilder.command must be a non-empty array")
    roots: List[JsonObject] = []
    for index, token in enumerate(command):
        if token != "--root-process":
            continue
        if index + 1 >= len(command):
            raise ValueError("--root-process is missing its exact reference")
        dataset_id, version, exact = _exact_ref(
            command[index + 1], f"snapshotBuilder.command[{index + 1}]"
        )
        roots.append(
            {"process_id": dataset_id, "version": version, "exact_ref": exact}
        )
    if not roots:
        raise ValueError("snapshotBuilder.command contains no root processes")
    exact_refs = [row["exact_ref"] for row in roots]
    if len(exact_refs) != len(set(exact_refs)):
        raise ValueError("snapshotBuilder.command contains duplicate root processes")
    return sorted(roots, key=lambda row: str(row["exact_ref"]))


def _snapshot_risk(stdout_tail: object) -> JsonObject:
    text = stdout_tail if isinstance(stdout_tail, str) else ""

    def match(pattern: str) -> Optional[str]:
        found = re.search(pattern, text)
        return found.group(1) if found is not None else None

    missing = match(r"missing_count=([0-9]+)")
    unique = match(r"unique_match=([0-9]+(?:\.[0-9]+)?)")
    any_match = match(r"any_match=([0-9]+(?:\.[0-9]+)?)")
    return {
        "matrix_readiness": match(r"\[matrix_readiness\]\s+status=([^\s]+)")
        or "unknown",
        "unique_match_percent": float(unique) if unique is not None else None,
        "any_match_percent": float(any_match) if any_match is not None else None,
        "missing_flow_meta_count": int(missing) if missing is not None else None,
        "scope": "whole_snapshot",
        "applied_to_each_root": False,
        "interpretation": (
            "Whole-snapshot readiness is retained as a risk and does not determine "
            "any individual root's nine-gate outcome."
        ),
    }


def compile_public_result_root_ledger(
    *,
    public_result_response: Mapping[str, object],
    source_binding: Mapping[str, object],
) -> JsonObject:
    """Extract an immutable, deterministic exact-root ledger from one public package."""

    response = _object(public_result_response, "public_result_response")
    if response.get("ok") is not True:
        raise ValueError("public result response is not successful")
    data = _object(response.get("data"), "public_result_response.data")
    publication = _object(data.get("publication"), "publication")
    if (
        publication.get("visibilityScope") != "public"
        or publication.get("status") != "current"
    ):
        raise ValueError("public result publication must be current and public")
    package = _object(data.get("package"), "package")
    package_id = _text(package.get("packageId", package.get("id")))
    snapshot_id = _text(package.get("snapshotId"))
    publication_id = _text(
        publication.get("publicationId", publication.get("id"))
    )
    if package_id is None or snapshot_id is None or publication_id is None:
        raise ValueError("public result package identity is incomplete")
    artifact_manifest = _object(data.get("artifactManifest"), "artifactManifest")
    snapshot_builder = _object(
        artifact_manifest.get("snapshotBuilder"), "artifactManifest.snapshotBuilder"
    )
    roots = _extract_roots(snapshot_builder.get("command"))
    eligible_count = _positive_int(
        package.get("eligibleInputCount"), "package.eligibleInputCount"
    )
    included_count = _positive_int(
        package.get("includedInputCount"), "package.includedInputCount"
    )
    if eligible_count != included_count or included_count != len(roots):
        raise ValueError("package input counts drift from extracted exact roots")
    version_distribution = dict(
        sorted(Counter(str(row["version"]) for row in roots).items())
    )
    body: JsonObject = {
        "contract": PUBLIC_RESULT_ROOT_LEDGER_CONTRACT,
        "status": "compiled",
        "publication_id": publication_id,
        "package_id": package_id,
        "snapshot_id": snapshot_id,
        "root_process_count": len(roots),
        "root_processes": roots,
        "version_distribution": version_distribution,
        "source_binding": _binding(source_binding, "source_binding"),
        "snapshot_risk": _snapshot_risk(snapshot_builder.get("stdout_tail")),
        "all_roots_individually_admitted": False,
        "next_route": "screen-public-result-root-processes-with-user-read",
    }
    body["ledger_id"] = _digest(body)[:16]
    return body


def _english_text(value: object, label: str) -> str:
    rows = value if isinstance(value, list) else [value]
    for row in rows:
        if (
            isinstance(row, dict)
            and row.get("@xml:lang") == "en"
            and _text(row.get("#text")) is not None
        ):
            return str(row["#text"]).strip()
    raise ValueError(f"{label} English text is missing")


def compile_lcia_method_unit_index(
    *,
    method_list: Mapping[str, object],
    cache_manifest: Mapping[str, object],
    source_bindings: Sequence[Mapping[str, object]],
    repository: str,
    commit: str,
) -> JsonObject:
    """Bind LCIA names and units only through exact official method ID/version pairs."""

    listing = _object(method_list, "method_list")
    manifest = _object(cache_manifest, "cache_manifest")
    if manifest.get("schema_version") != "lcia.static_cache_bundle.v1":
        raise ValueError("LCIA cache manifest schema is unsupported")
    if manifest.get("release_ready") is not True:
        raise ValueError("LCIA cache manifest is not release_ready")
    bundle_version = _text(manifest.get("bundle_version"))
    if bundle_version is None:
        raise ValueError("LCIA bundle version is missing")
    repository_value = _text(repository)
    if repository_value is None:
        raise ValueError("LCIA source repository is missing")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("LCIA source commit must be an exact lowercase Git commit")

    bindings = sorted(
        [_binding(value, f"source_bindings[{index}]") for index, value in enumerate(source_bindings)],
        key=lambda row: (str(row["role"]), str(row["path"]), str(row["sha256"])),
    )
    by_role = {str(row["role"]): row for row in bindings}
    if set(by_role) != {"lcia_cache_manifest", "lcia_method_list"}:
        raise ValueError("LCIA source bindings must contain exactly list and cache manifest")
    manifest_files = _object(manifest.get("files"), "cache_manifest.files")
    declared_list = _object(manifest_files.get("list"), "cache_manifest.files.list")
    declared_list_sha = _text(declared_list.get("sha256"))
    if declared_list_sha != by_role["lcia_method_list"]["sha256"]:
        raise ValueError("LCIA method list hash does not match cache manifest")

    files = listing.get("files")
    methods = manifest.get("methods")
    if not isinstance(files, list) or not files:
        raise ValueError("LCIA method list files are missing")
    if not isinstance(methods, list) or not methods:
        raise ValueError("LCIA cache manifest methods are missing")
    metadata = _object(listing.get("metadata"), "method_list.metadata")
    if _positive_int(metadata.get("totalFiles"), "method_list.metadata.totalFiles") != len(files):
        raise ValueError("LCIA method list count drifted")

    list_rows = {}
    for index, value in enumerate(files):
        row = _object(value, f"method_list.files[{index}]")
        method_id = _text(row.get("id"))
        version = _text(row.get("version"))
        if method_id is None or version is None:
            raise ValueError("LCIA method identity is incomplete")
        _, _, exact = _exact_ref(f"{method_id}@{version}", "LCIA method")
        name = _english_text(row.get("description"), f"{exact}.description")
        if not isinstance(row.get("referenceQuantity"), dict):
            raise ValueError(f"{exact} reference unit is missing")
        reference_quantity = _object(
            row.get("referenceQuantity"), f"{exact}.referenceQuantity"
        )
        unit = _english_text(
            reference_quantity.get("common:shortDescription"),
            f"{exact}.reference unit",
        )
        quantity_id = _text(reference_quantity.get("@refObjectId"))
        if quantity_id is None:
            raise ValueError(f"{exact} reference quantity ID is missing")
        filename = _text(row.get("filename"))
        if filename is None:
            raise ValueError(f"{exact} filename is missing")
        if filename in list_rows:
            raise ValueError("LCIA method list contains a duplicate artifact filename")
        list_rows[filename] = {
            "artifact_locator_id": method_id.lower(),
            "artifact_version": version,
            "artifact_exact_ref": exact,
            "name": name,
            "reference_quantity_id": quantity_id,
            "reference_unit": unit,
            "artifact_filename": filename,
        }

    alias_rows = manifest.get("identity_aliases")
    aliases = alias_rows if isinstance(alias_rows, list) else []
    aliases_by_identity = {}
    for index, value in enumerate(aliases):
        row = _object(value, f"cache_manifest.identity_aliases[{index}]")
        method_id = _text(row.get("method_id"))
        version = _text(row.get("method_version"))
        locator = _text(row.get("artifact_locator_id"))
        evidence = row.get("evidence")
        if (
            method_id is None
            or version is None
            or locator is None
            or row.get("status") != "verified"
            or not isinstance(evidence, dict)
            or not _HEX_SHA256.fullmatch(str(evidence.get("sha256", "")))
        ):
            raise ValueError("LCIA identity alias is not exactly verified")
        _, _, exact = _exact_ref(f"{method_id}@{version}", "LCIA identity alias")
        aliases_by_identity[exact] = copy.deepcopy(row)

    compiled: List[JsonObject] = []
    used_filenames = set()
    for index, value in enumerate(methods):
        row = _object(value, f"cache_manifest.methods[{index}]")
        method_id = _text(row.get("method_id"))
        version = _text(row.get("method_version"))
        locator = _text(row.get("artifact_locator_id"))
        filename = _text(row.get("artifact_filename"))
        if method_id is None or version is None or locator is None or filename is None:
            raise ValueError("LCIA cache manifest membership is incomplete")
        _, _, exact = _exact_ref(f"{method_id}@{version}", "LCIA cache method")
        artifact = list_rows.get(filename)
        if (
            artifact is None
            or artifact["artifact_locator_id"] != locator.lower()
            or artifact["artifact_version"] != version
        ):
            raise ValueError("LCIA method membership drifted between list and cache manifest")
        alias = None
        if method_id.lower() != locator.lower():
            alias = aliases_by_identity.get(exact)
            if alias is None or str(alias.get("artifact_locator_id", "")).lower() != locator.lower():
                raise ValueError("LCIA method membership lacks a verified identity alias")
        used_filenames.add(filename)
        compiled.append(
            {
                "method_id": method_id.lower(),
                "version": version,
                "exact_ref": exact,
                "name": artifact["name"],
                "reference_quantity_id": artifact["reference_quantity_id"],
                "reference_unit": artifact["reference_unit"],
                "artifact_locator_id": artifact["artifact_locator_id"],
                "artifact_filename": filename,
                "identity_alias_verified": alias is not None,
                "identity_alias_evidence": copy.deepcopy(alias.get("evidence")) if alias else None,
                "binding_mode": "exact_method_id_and_version",
            }
        )
    if used_filenames != set(list_rows):
        raise ValueError("LCIA method membership drifted between list and cache manifest")

    compiled.sort(key=lambda row: str(row["exact_ref"]))
    body: JsonObject = {
        "contract": LCIA_METHOD_UNIT_INDEX_CONTRACT,
        "status": "compiled",
        "bundle_version": bundle_version,
        "release_ready": True,
        "method_membership_status": manifest.get("method_membership_status"),
        "method_count": len(compiled),
        "methods": compiled,
        "source_revision": {"repository": repository_value, "commit": commit},
        "source_bindings": bindings,
        "cross_version_fallback_allowed": False,
        "unknown_unit_fallback_allowed": False,
    }
    body["index_id"] = _digest(body)[:16]
    return body


def _artifact_identity(value: object, label: str) -> JsonObject:
    artifact = _object(value, label)
    artifact_format = _text(artifact.get("artifactFormat"))
    sha256 = _text(artifact.get("artifactSha256"))
    byte_size = artifact.get("artifactByteSize")
    if (
        artifact_format is None
        or sha256 is None
        or not _HEX_SHA256.fullmatch(sha256)
        or isinstance(byte_size, bool)
        or not isinstance(byte_size, int)
        or byte_size < 1
    ):
        raise ValueError(f"{label} identity is incomplete")
    return {
        "format": artifact_format,
        "sha256": sha256,
        "byte_size": byte_size,
    }


def compile_bound_public_lcia_result(
    *,
    candidate: Mapping[str, object],
    public_result_response: Mapping[str, object],
    public_result_binding: Mapping[str, object],
    root_ledger: Mapping[str, object],
    lcia_method_unit_index: Mapping[str, object],
    lcia_index_binding: Mapping[str, object],
) -> JsonObject:
    """Bind one native public result to exact Process and official LCIA identities."""

    candidate_row = _object(candidate, "candidate")
    _, _, candidate_exact = _exact_ref(
        candidate_row.get("exact_ref"), "candidate.exact_ref"
    )
    ledger = _object(root_ledger, "root_ledger")
    if ledger.get("contract") != PUBLIC_RESULT_ROOT_LEDGER_CONTRACT:
        raise ValueError("root ledger contract is unsupported")
    roots = ledger.get("root_processes")
    if not isinstance(roots, list) or not roots:
        raise ValueError("root ledger processes are missing")
    ledger_refs = {
        _exact_ref(_object(root, "root process").get("exact_ref"), "root process")[2]
        for root in roots
    }
    if candidate_exact not in ledger_refs:
        raise ValueError("candidate is not a member of the exact root ledger")

    response = _object(public_result_response, "public_result_response")
    if response.get("ok") is not True:
        raise ValueError("public result response is not successful")
    data = _object(response.get("data"), "public_result_response.data")
    publication = _object(data.get("publication"), "publication")
    package = _object(data.get("package"), "package")
    process = _object(data.get("process"), "process")
    _, _, response_exact = _exact_ref(
        f"{process.get('processId')}@{process.get('processVersion')}",
        "public result Process",
    )
    if response_exact != candidate_exact:
        raise ValueError("public result Process identity drifted")
    if (
        publication.get("visibilityScope") != "public"
        or publication.get("status") != "current"
        or _text(package.get("packageId", package.get("id")))
        != ledger.get("package_id")
        or _text(package.get("snapshotId")) != ledger.get("snapshot_id")
    ):
        raise ValueError("public result publication or package identity drifted")

    artifact_manifest = _object(data.get("artifactManifest"), "artifactManifest")
    snapshot_builder = _object(
        artifact_manifest.get("snapshotBuilder"), "artifactManifest.snapshotBuilder"
    )
    command = snapshot_builder.get("command")
    response_roots = {str(row["exact_ref"]) for row in _extract_roots(command)}
    if response_roots != ledger_refs:
        raise ValueError("public result snapshot root membership drifted")
    provider_rules = [
        command[index + 1]
        for index, token in enumerate(command[:-1])
        if token == "--provider-rule"
    ]
    if provider_rules != ["split_by_process_volume"]:
        raise ValueError("public result provider rule is unsupported")

    index = _object(lcia_method_unit_index, "lcia_method_unit_index")
    if (
        index.get("contract") != LCIA_METHOD_UNIT_INDEX_CONTRACT
        or index.get("release_ready") is not True
        or index.get("cross_version_fallback_allowed") is not False
        or index.get("unknown_unit_fallback_allowed") is not False
    ):
        raise ValueError("LCIA method/unit index is not fail-closed")
    method_rows = index.get("methods")
    if not isinstance(method_rows, list) or not method_rows:
        raise ValueError("LCIA method/unit index is empty")
    methods_by_result_id: Dict[str, JsonObject] = {}
    for position, raw_method in enumerate(method_rows):
        method = _object(raw_method, f"methods[{position}]")
        method_id = _text(method.get("method_id"))
        locator_id = _text(method.get("artifact_locator_id"))
        if method_id is None or locator_id is None:
            raise ValueError("LCIA method identity is incomplete")
        result_ids = [method_id.lower()]
        if locator_id.lower() != method_id.lower():
            if method.get("identity_alias_verified") is not True:
                raise ValueError("LCIA method alias is not verified")
            result_ids.append(locator_id.lower())
        for result_id in result_ids:
            if result_id in methods_by_result_id:
                raise ValueError("LCIA result identity maps to multiple methods")
            methods_by_result_id[result_id] = method

    values = data.get("values")
    row_count = data.get("rowCount")
    if (
        not isinstance(values, list)
        or isinstance(row_count, bool)
        or not isinstance(row_count, int)
        or row_count != len(values)
        or row_count != len(method_rows)
    ):
        raise ValueError("public LCIA result row count drifted")
    compiled_results = []
    seen_impacts = set()
    seen_indices = set()
    for position, raw_value in enumerate(values):
        value_row = _object(raw_value, f"values[{position}]")
        impact_id = _text(value_row.get("impact_id"))
        impact_index = value_row.get("impact_index")
        value = value_row.get("value")
        if impact_id is None or impact_id.lower() in seen_impacts:
            raise ValueError("public LCIA method identity is missing or duplicated")
        method = methods_by_result_id.get(impact_id.lower())
        if method is None:
            raise ValueError("public LCIA method is absent from the official exact index")
        if (
            isinstance(impact_index, bool)
            or not isinstance(impact_index, int)
            or impact_index < 0
            or impact_index in seen_indices
        ):
            raise ValueError("public LCIA impact index is invalid")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise ValueError("public LCIA value is not finite numeric evidence")
        original_unit = _text(value_row.get("unit"))
        resolved_unit = _text(method.get("reference_unit"))
        if (
            original_unit is None
            or resolved_unit is None
            or original_unit not in {"unknown", resolved_unit}
        ):
            raise ValueError("public LCIA unit conflicts with the official exact index")
        seen_impacts.add(impact_id.lower())
        seen_indices.add(impact_index)
        alias_used = impact_id.lower() != str(method.get("method_id", "")).lower()
        compiled_results.append(
            {
                "impact_index": impact_index,
                "public_impact_id": impact_id.lower(),
                "method_id": method.get("method_id"),
                "method_version": method.get("version"),
                "method_exact_ref": method.get("exact_ref"),
                "name": method.get("name"),
                "value": value,
                "original_unit": original_unit,
                "resolved_unit": resolved_unit,
                "unit_binding_source": "official_static_lcia_bundle_exact_identity",
                "identity_alias_used": alias_used,
                "identity_alias_evidence": (
                    copy.deepcopy(method.get("identity_alias_evidence"))
                    if alias_used
                    else None
                ),
                "validation_status": "exactly_bound",
            }
        )
    if seen_indices != set(range(row_count)):
        raise ValueError("public LCIA impact indices are not contiguous")
    compiled_results.sort(key=lambda row: int(row["impact_index"]))

    result_artifact = _artifact_identity(data.get("resultArtifact"), "resultArtifact")
    query_artifact = _artifact_identity(data.get("queryArtifact"), "queryArtifact")
    body: JsonObject = {
        "contract": BOUND_PUBLIC_LCIA_RESULT_CONTRACT,
        "status": "exact_public_lcia_bound",
        "process_exact_ref": candidate_exact,
        "publication_id": publication.get("publicationId", publication.get("id")),
        "package_id": ledger.get("package_id"),
        "snapshot_id": ledger.get("snapshot_id"),
        "provider_rule": provider_rules[0],
        "input_manifest_hash": artifact_manifest.get("inputManifestHash"),
        "method_index_id": index.get("index_id"),
        "method_result_count": len(compiled_results),
        "method_results": compiled_results,
        "result_artifact": result_artifact,
        "query_artifact": query_artifact,
        "source_bindings": sorted(
            [
                _binding(public_result_binding, "public_result_binding"),
                _binding(lcia_index_binding, "lcia_index_binding"),
            ],
            key=lambda row: (str(row["role"]), str(row["path"])),
        ),
        "snapshot_risk": copy.deepcopy(ledger.get("snapshot_risk")),
        "cross_version_fallback_used": False,
        "guessed_unit_used": False,
        "remote_write_executed_count": 0,
        "next_route": "evaluate-nine-gate-candidate-observation",
    }
    body["binding_id"] = _digest(body)[:16]
    return body


def _exact_flow_rows(flow_rows: Sequence[Mapping[str, object]]) -> Dict[str, JsonObject]:
    by_exact = {}
    for index, value in enumerate(flow_rows):
        row = _object(value, f"flow_rows[{index}]")
        flow = _object(row.get("flow"), f"flow_rows[{index}].flow")
        dataset = _object(flow.get("flowDataSet"), f"flow_rows[{index}].flowDataSet")
        information = _object(dataset.get("flowInformation"), "flowInformation")
        dataset_information = _object(
            information.get("dataSetInformation"), "flow dataSetInformation"
        )
        admin = _object(dataset.get("administrativeInformation"), "flow admin")
        publication = _object(
            admin.get("publicationAndOwnership"), "flow publicationAndOwnership"
        )
        flow_id = _text(row.get("id", dataset_information.get("common:UUID")))
        version = _text(
            row.get("version", publication.get("common:dataSetVersion"))
        )
        if flow_id is None or version is None:
            raise ValueError("Flow identity is incomplete")
        _, _, exact = _exact_ref(f"{flow_id}@{version}", "Flow identity")
        if (
            _text(dataset_information.get("common:UUID")) != flow_id
            or _text(publication.get("common:dataSetVersion")) != version
            or row.get("state_code") not in {100, "100"}
        ):
            raise ValueError("Flow wrapper and native identity drifted")
        modelling = dataset.get("modellingAndValidation")
        modelling = modelling if isinstance(modelling, dict) else {}
        method = modelling.get("LCIMethod")
        method = method if isinstance(method, dict) else {}
        flow_type = _text(method.get("typeOfDataSet"))
        if flow_type not in {"Product flow", "Elementary flow", "Waste flow"}:
            raise ValueError("Flow type is unsupported or missing")
        if exact in by_exact:
            raise ValueError("Flow rows contain a duplicate exact identity")
        by_exact[exact] = {"row": row, "flow_type": flow_type}
    return by_exact


def compile_candidate_native_evidence(
    *,
    candidate: Mapping[str, object],
    process_capture_report: Mapping[str, object],
    flow_capture_report: Mapping[str, object],
    flow_rows: Sequence[Mapping[str, object]],
    flow_valid_exact_refs: Sequence[str],
    bound_public_result: Mapping[str, object],
    root_ledger: Mapping[str, object],
    source_bindings: Sequence[Mapping[str, object]],
) -> JsonObject:
    """Compile candidate-scoped closure without promoting whole-snapshot readiness."""

    candidate_row = _object(candidate, "candidate")
    _, _, candidate_exact = _exact_ref(
        candidate_row.get("exact_ref"), "candidate.exact_ref"
    )
    process_report = _object(process_capture_report, "process_capture_report")
    resolved_processes = process_report.get("resolved_exact_processes")
    if (
        not isinstance(resolved_processes, list)
        or candidate_exact not in resolved_processes
        or process_report.get("native_validation_invalid_count") != 0
        or not isinstance(process_report.get("native_validation_valid_count"), int)
        or process_report.get("native_validation_valid_count", 0) < 1
    ):
        raise ValueError("candidate exact Process validation is incomplete")

    reference_flow = _object(candidate_row.get("reference_flow"), "reference_flow")
    input_flows = candidate_row.get("input_flows")
    if not isinstance(input_flows, list) or not input_flows:
        raise ValueError("candidate input Flow evidence is missing")
    candidate_flows = [reference_flow] + [
        _object(value, f"input_flows[{index}]")
        for index, value in enumerate(input_flows)
    ]
    candidate_flow_refs = []
    for index, flow in enumerate(candidate_flows):
        _, _, exact = _exact_ref(flow.get("exact_ref"), f"candidate_flows[{index}]")
        candidate_flow_refs.append(exact)
    if len(candidate_flow_refs) != len(set(candidate_flow_refs)):
        raise ValueError("candidate Flow references must be unique")

    flow_report = _object(flow_capture_report, "flow_capture_report")
    resolved_flows = flow_report.get("resolved_exact_flows")
    if not isinstance(resolved_flows, list) or not set(candidate_flow_refs).issubset(
        set(resolved_flows)
    ):
        raise ValueError("candidate exact Flow capture is incomplete")
    valid_refs = set()
    for index, value in enumerate(flow_valid_exact_refs):
        _, _, exact = _exact_ref(value, f"flow_valid_exact_refs[{index}]")
        valid_refs.add(exact)
    if not set(candidate_flow_refs).issubset(valid_refs):
        raise ValueError("candidate exact Flow validation is incomplete")
    flows_by_exact = _exact_flow_rows(flow_rows)
    if not set(candidate_flow_refs).issubset(set(flows_by_exact)):
        raise ValueError("candidate exact Flow rows are incomplete")
    if flows_by_exact[candidate_flow_refs[0]]["flow_type"] != "Product flow":
        raise ValueError("candidate reference Flow must be a Product flow")

    ledger = _object(root_ledger, "root_ledger")
    if ledger.get("contract") != PUBLIC_RESULT_ROOT_LEDGER_CONTRACT:
        raise ValueError("root ledger contract is unsupported")
    roots = ledger.get("root_processes")
    if not isinstance(roots, list) or not roots:
        raise ValueError("root ledger processes are missing")
    root_refs = {
        _exact_ref(_object(root, "root process").get("exact_ref"), "root process")[2]
        for root in roots
    }
    if candidate_exact not in root_refs:
        raise ValueError("candidate is outside the exact root ledger")

    provider_refs = set()
    provider_relations = []
    for index, flow in enumerate(candidate_flows[1:]):
        exact = candidate_flow_refs[index + 1]
        flow_type = str(flows_by_exact[exact]["flow_type"])
        providers = flow.get("provider_candidates")
        providers = providers if isinstance(providers, list) else []
        exact_providers = []
        for provider_index, provider in enumerate(providers):
            _, _, provider_exact = _exact_ref(
                provider, f"input_flows[{index}].provider_candidates[{provider_index}]"
            )
            exact_providers.append(provider_exact)
        exact_providers = sorted(set(exact_providers))
        if flow_type != "Elementary flow" and not exact_providers:
            raise ValueError("non-elementary input Flow lacks an exact root Provider")
        if not set(exact_providers).issubset(root_refs):
            raise ValueError("candidate Provider is outside the exact root ledger")
        provider_refs.update(exact_providers)
        provider_relations.append(
            {
                "input_flow_exact_ref": exact,
                "flow_type": flow_type,
                "provider_exact_refs": exact_providers,
                "provider_required": flow_type != "Elementary flow",
                "relation_complete": flow_type == "Elementary flow" or bool(exact_providers),
            }
        )

    result = _object(bound_public_result, "bound_public_result")
    if (
        result.get("contract") != BOUND_PUBLIC_LCIA_RESULT_CONTRACT
        or result.get("status") != "exact_public_lcia_bound"
        or result.get("process_exact_ref") != candidate_exact
        or result.get("snapshot_id") != ledger.get("snapshot_id")
        or result.get("provider_rule") != "split_by_process_volume"
        or not isinstance(result.get("method_result_count"), int)
        or result.get("method_result_count", 0) < 1
    ):
        raise ValueError("candidate native public result binding is incomplete")
    bindings = sorted(
        [
            _binding(value, f"source_bindings[{index}]")
            for index, value in enumerate(source_bindings)
        ],
        key=lambda row: (str(row["role"]), str(row["path"]), str(row["sha256"])),
    )
    if not bindings:
        raise ValueError("candidate evidence source bindings are required")

    retained_risks = [copy.deepcopy(ledger.get("snapshot_risk"))]
    retained_risks.append(
        {
            "scope": "candidate_provider_weights",
            "status": "not_exposed_by_public_result_api",
            "interpretation": (
                "The exact Provider identity set and split_by_process_volume rule are "
                "inspectable; the resolved numeric weight vector is not exposed and "
                "remains a limitation for independent matrix reconstruction."
            ),
        }
    )
    body: JsonObject = {
        "contract": CANDIDATE_NATIVE_EVIDENCE_CONTRACT,
        "status": "candidate_native_evidence_ready",
        "candidate_exact_ref": candidate_exact,
        "root_ledger_id": ledger.get("ledger_id"),
        "snapshot_id": ledger.get("snapshot_id"),
        "process_native_schema_valid": True,
        "functional_unit_reference_flow_valid": True,
        "candidate_flow_exact_refs": candidate_flow_refs,
        "candidate_closure": {
            "scope": "candidate_root_under_exact_native_snapshot",
            "inspectable": True,
            "executable": True,
            "complete": True,
            "immediate_non_elementary_inputs_have_providers": True,
            "native_result_method_count": result.get("method_result_count"),
            "whole_snapshot_readiness_promoted": False,
        },
        "provider_snapshot": {
            "id": ledger.get("snapshot_id"),
            "version": result.get("input_manifest_hash") or ledger.get("ledger_id"),
            "provider_rule": "split_by_process_volume",
            "inspectable": True,
            "provider_exact_refs": sorted(provider_refs),
            "provider_relation_count": sum(
                len(row["provider_exact_refs"]) for row in provider_relations
            ),
            "relations": provider_relations,
            "weight_vector_exposed": False,
        },
        "bound_public_result_id": result.get("binding_id"),
        "source_bindings": bindings,
        "retained_risks": retained_risks,
        "admission_criteria_lowered": False,
        "remote_write_executed_count": 0,
        "next_route": "compile-nine-gate-open-data-selection",
    }
    body["candidate_evidence_id"] = _digest(body)[:16]
    return body


def _rows(value: object) -> List[JsonObject]:
    if isinstance(value, list):
        raw_rows = value
    elif isinstance(value, dict):
        raw_rows = [value] if value else []
    else:
        return []
    return [copy.deepcopy(row) for row in raw_rows if isinstance(row, dict)]


def _localized_text(value: object) -> Optional[str]:
    rows = _rows(value)
    for language in ("en", "zh"):
        for row in rows:
            if row.get("@xml:lang") == language and _text(row.get("#text")):
                return _text(row.get("#text"))
    for row in rows:
        if _text(row.get("#text")):
            return _text(row.get("#text"))
    return _text(value)


def _classes(value: object) -> List[JsonObject]:
    rows = _rows(value)
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("@level", "")),
            str(row.get("@classId", "")),
            str(row.get("#text", "")),
        ),
    )


def _reference_ids(value: object) -> List[str]:
    raw = value if isinstance(value, list) else [value]
    return sorted(
        {
            str(item).strip()
            for item in raw
            if isinstance(item, (str, int)) and str(item).strip()
        }
    )


def _candidate_metadata(row: JsonObject, exact_ref: str, terms: Sequence[str]) -> JsonObject:
    process = row.get("process")
    dataset = process.get("processDataSet") if isinstance(process, dict) else None
    dataset = dataset if isinstance(dataset, dict) else {}
    information = dataset.get("processInformation")
    information = information if isinstance(information, dict) else {}
    data_information = information.get("dataSetInformation")
    data_information = data_information if isinstance(data_information, dict) else {}
    names = data_information.get("name")
    names = names if isinstance(names, dict) else {}
    name = _localized_text(names.get("baseName"))
    classification_information = data_information.get("classificationInformation")
    classification_information = (
        classification_information
        if isinstance(classification_information, dict)
        else {}
    )
    classification = classification_information.get("common:classification")
    classification = classification if isinstance(classification, dict) else {}
    class_rows = _classes(classification.get("common:class"))
    sector = next(
        (_text(item.get("#text")) for item in class_rows if str(item.get("@level")) == "0"),
        None,
    )
    leaf_class = next(
        (_text(item.get("#text")) for item in reversed(class_rows) if _text(item.get("#text"))),
        None,
    )
    quantitative = information.get("quantitativeReference")
    quantitative = quantitative if isinstance(quantitative, dict) else {}
    reference_ids = _reference_ids(quantitative.get("referenceToReferenceFlow"))
    exchanges_container = dataset.get("exchanges")
    exchanges_container = exchanges_container if isinstance(exchanges_container, dict) else {}
    exchanges = _rows(exchanges_container.get("exchange"))
    reference_exchange = next(
        (
            exchange
            for exchange in exchanges
            if str(exchange.get("@dataSetInternalID", "")) in reference_ids
        ),
        None,
    )
    flow_reference = (
        reference_exchange.get("referenceToFlowDataSet")
        if isinstance(reference_exchange, dict)
        else None
    )
    flow_reference = flow_reference if isinstance(flow_reference, dict) else {}
    flow_id = _text(flow_reference.get("@refObjectId"))
    flow_version = _text(flow_reference.get("@version"))
    flow_name = _localized_text(flow_reference.get("common:shortDescription"))
    amount = (
        reference_exchange.get("resultingAmount", reference_exchange.get("meanAmount"))
        if isinstance(reference_exchange, dict)
        else None
    )
    admin = dataset.get("administrativeInformation")
    admin = admin if isinstance(admin, dict) else {}
    publication = admin.get("publicationAndOwnership")
    publication = publication if isinstance(publication, dict) else {}
    internal_version = _text(publication.get("common:dataSetVersion"))
    process_id, version, _ = _exact_ref(exact_ref, "root process")
    internal_id = _text(data_information.get("common:UUID"))
    reference_exact = None
    if flow_id is not None and flow_version is not None:
        try:
            _, _, reference_exact = _exact_ref(
                f"{flow_id}@{flow_version}", f"{exact_ref} reference flow"
            )
        except ValueError:
            reference_exact = None
    identity_text = " ".join(
        token for token in (name, sector, leaf_class, flow_name) if token
    ).casefold()
    matched_terms = sorted(
        {
            term.strip()
            for term in terms
            if isinstance(term, str)
            and term.strip()
            and term.strip().casefold() in identity_text
        },
        key=str.casefold,
    )
    amount_present = (
        isinstance(amount, (int, float))
        and not isinstance(amount, bool)
        or isinstance(amount, str)
        and re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", amount.strip())
        is not None
    )
    input_flows = []
    for exchange in exchanges:
        if exchange.get("exchangeDirection") != "Input":
            continue
        input_reference = exchange.get("referenceToFlowDataSet")
        input_reference = input_reference if isinstance(input_reference, dict) else {}
        input_id = _text(input_reference.get("@refObjectId"))
        input_version = _text(input_reference.get("@version"))
        input_exact = None
        if input_id is not None and input_version is not None:
            try:
                _, _, input_exact = _exact_ref(
                    f"{input_id}@{input_version}", f"{exact_ref} input flow"
                )
            except ValueError:
                input_exact = None
        input_amount = exchange.get("resultingAmount", exchange.get("meanAmount"))
        input_flows.append(
            {
                "exchange_internal_id": _text(exchange.get("@dataSetInternalID")),
                "id": input_id.lower() if input_id else None,
                "version": input_version,
                "exact_ref": input_exact,
                "name": _localized_text(input_reference.get("common:shortDescription")),
                "amount": input_amount,
                "provider_candidates": [],
            }
        )
    input_flows.sort(
        key=lambda flow: (
            str(flow.get("exact_ref", "")),
            str(flow.get("exchange_internal_id", "")),
        )
    )
    metadata_complete = (
        internal_id is not None
        and internal_id.lower() == process_id
        and internal_version == version
        and name is not None
        and sector is not None
        and len(reference_ids) == 1
        and isinstance(reference_exchange, dict)
        and reference_exchange.get("exchangeDirection") == "Output"
        and amount_present
        and reference_exact is not None
        and flow_name is not None
    )
    return {
        "process_id": process_id,
        "version": version,
        "exact_ref": exact_ref,
        "state_code": row.get("state_code"),
        "modified_at": row.get("modified_at"),
        "name": name,
        "sector": sector,
        "leaf_class": leaf_class,
        "reference_flow": {
            "id": flow_id.lower() if flow_id else None,
            "version": flow_version,
            "exact_ref": reference_exact,
            "name": flow_name,
            "amount": amount if amount_present else None,
            "unit_status": "requires_exact_flow_read",
        },
        "input_flows": input_flows,
        "exchange_count": len(exchanges),
        "provider_relationship_metadata_available": False,
        "material_difference_hint": not matched_terms,
        "matched_prior_case_terms": matched_terms,
        "metadata_complete": metadata_complete,
        "exact_process_read_required": True,
        "schema_validation_required": True,
        "public_result_exact_read_required": True,
        "nine_gate_admission_status": "not_evaluated",
    }


def compile_published_process_screening_batch(
    *,
    root_ledger: Mapping[str, object],
    process_catalog: Mapping[str, object],
    process_catalog_binding: Mapping[str, object],
    previous_case_terms: Sequence[str],
    cursor: int,
    batch_size: int,
    schema_valid_exact_refs: Optional[Sequence[str]] = None,
    schema_validation_binding: Optional[Mapping[str, object]] = None,
) -> JsonObject:
    """Intersect the exact roots with published metadata and emit one bounded batch."""

    ledger = _object(root_ledger, "root_ledger")
    if ledger.get("contract") != PUBLIC_RESULT_ROOT_LEDGER_CONTRACT:
        raise ValueError("root ledger contract is unsupported")
    roots = ledger.get("root_processes")
    if not isinstance(roots, list) or not roots:
        raise ValueError("root ledger processes are missing")
    root_refs = []
    for index, value in enumerate(roots):
        root = _object(value, f"root_processes[{index}]")
        _, _, exact = _exact_ref(root.get("exact_ref"), f"root_processes[{index}]")
        root_refs.append(exact)
    if len(root_refs) != len(set(root_refs)):
        raise ValueError("root ledger exact references are not unique")

    catalog = _object(process_catalog, "process_catalog")
    if catalog.get("package_spec") != "@tiangong-lca/cli@0.0.29":
        raise ValueError("process catalog must use fixed @tiangong-lca/cli@0.0.29")
    rows = catalog.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("published process catalog rows are missing")
    by_exact = {}
    for index, value in enumerate(rows):
        row = _object(value, f"process_catalog.rows[{index}]")
        if row.get("state_code") != 100:
            raise ValueError("published process catalog contains a non-reviewed row")
        process_id = _text(row.get("id"))
        version = _text(row.get("version"))
        if process_id is None or version is None:
            raise ValueError("published process catalog identity is incomplete")
        _, _, exact = _exact_ref(
            f"{process_id}@{version}", f"process_catalog.rows[{index}]"
        )
        if exact in by_exact:
            raise ValueError("published process catalog contains duplicate exact rows")
        by_exact[exact] = row
    missing = sorted(set(root_refs) - set(by_exact))
    missing_rows = [
        {
            "exact_ref": exact,
            "failed_gate": "published_open_data",
            "reason": "exact root is absent from the state_code=100 reviewed catalog",
            "failed_closed": True,
        }
        for exact in missing
    ]

    terms = sorted(
        {term.strip() for term in previous_case_terms if isinstance(term, str) and term.strip()},
        key=str.casefold,
    )
    if not terms:
        raise ValueError("previous case terms are required")
    candidates = [
        _candidate_metadata(by_exact[exact], exact, terms)
        for exact in sorted(set(root_refs) & set(by_exact))
    ]
    providers_by_flow: Dict[str, List[str]] = {}
    for candidate in candidates:
        reference_exact = candidate["reference_flow"].get("exact_ref")
        if reference_exact is None:
            continue
        providers_by_flow.setdefault(str(reference_exact), []).append(
            str(candidate["exact_ref"])
        )
    for provider_refs in providers_by_flow.values():
        provider_refs.sort()
    for candidate in candidates:
        input_flows = candidate["input_flows"]
        for input_flow in input_flows:
            input_flow["provider_candidates"] = copy.deepcopy(
                providers_by_flow.get(str(input_flow.get("exact_ref")), [])
            )
        provider_counts = [
            len(input_flow["provider_candidates"]) for input_flow in input_flows
        ]
        candidate["provider_relationship_metadata_available"] = all(
            input_flow.get("exact_ref") is not None for input_flow in input_flows
        )
        candidate["all_input_flows_have_root_provider_candidates"] = bool(
            input_flows
        ) and all(count > 0 for count in provider_counts)
        candidate["missing_provider_input_count"] = sum(
            count == 0 for count in provider_counts
        )
        candidate["root_provider_relation_count"] = sum(provider_counts)
        candidate["max_provider_candidate_count"] = (
            max(provider_counts) if provider_counts else None
        )
    complete = [candidate for candidate in candidates if candidate["metadata_complete"]]
    schema_validation_applied = schema_valid_exact_refs is not None
    schema_refs = set()
    validation_binding = None
    if schema_validation_applied:
        if schema_validation_binding is None:
            raise ValueError("schema_validation_binding is required with valid exact refs")
        validation_binding = _binding(
            schema_validation_binding, "schema_validation_binding"
        )
        for index, value in enumerate(schema_valid_exact_refs or []):
            _, _, exact = _exact_ref(value, f"schema_valid_exact_refs[{index}]")
            if exact in schema_refs:
                raise ValueError("schema-valid exact references must be unique")
            schema_refs.add(exact)
        if not schema_refs.issubset(set(root_refs) & set(by_exact)):
            raise ValueError("schema-valid exact references must belong to the reviewed root intersection")
    elif schema_validation_binding is not None:
        raise ValueError("schema validation binding requires schema-valid exact refs")
    for candidate in candidates:
        candidate["native_schema_valid"] = (
            candidate["exact_ref"] in schema_refs if schema_validation_applied else None
        )
    schema_eligible = (
        [candidate for candidate in complete if candidate["exact_ref"] in schema_refs]
        if schema_validation_applied
        else complete
    )
    distinct = [
        candidate for candidate in schema_eligible if candidate["material_difference_hint"]
    ]
    distinct.sort(
        key=lambda row: (
            -int(row.get("all_input_flows_have_root_provider_candidates") is True),
            int(row.get("max_provider_candidate_count") or 10**9),
            int(row.get("root_provider_relation_count", 0)),
            -int(row.get("sector") != "Manufacturing"),
            int(row.get("exchange_count", 0)),
            str(row.get("exact_ref", "")),
        )
    )
    if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
        raise ValueError("cursor must be a non-negative integer")
    size = _positive_int(batch_size, "batch_size")
    if cursor > len(distinct) or (cursor == len(distinct) and distinct):
        raise ValueError("cursor exceeds the deterministic candidate surface")
    batch = copy.deepcopy(distinct[cursor : cursor + size])
    next_cursor = cursor + len(batch)
    if next_cursor >= len(distinct):
        next_cursor = None
    status = "batch_ready" if batch else "no_screenable_root"
    body: JsonObject = {
        "contract": PUBLIC_RESULT_SCREENING_BATCH_CONTRACT,
        "status": status,
        "package_id": ledger.get("package_id"),
        "root_ledger_id": ledger.get("ledger_id"),
        "root_process_count": len(root_refs),
        "published_catalog_row_count": len(rows),
        "exact_catalog_intersection_count": len(candidates),
        "missing_reviewed_exact_root_count": len(missing_rows),
        "missing_reviewed_exact_roots": missing_rows,
        "extra_catalog_row_count": len(set(by_exact) - set(root_refs)),
        "metadata_complete_root_count": len(complete),
        "metadata_incomplete_root_count": len(candidates) - len(complete),
        "native_schema_validation_applied": schema_validation_applied,
        "native_schema_valid_root_count": len(schema_refs) if schema_validation_applied else None,
        "schema_validation_binding": validation_binding,
        "materially_distinct_candidate_count": len(distinct),
        "root_provider_complete_candidate_count": sum(
            candidate["all_input_flows_have_root_provider_candidates"] is True
            for candidate in distinct
        ),
        "excluded_prior_domain_count": len(schema_eligible) - len(distinct),
        "previous_case_terms": terms,
        "rank_order": [
            "all_input_flows_have_root_provider_candidates_desc",
            "max_provider_candidate_count_asc",
            "root_provider_relation_count_asc",
            "non_manufacturing_sector_desc",
            "exchange_count_asc",
            "exact_process_ref_asc",
        ],
        "cursor": cursor,
        "batch_size": size,
        "batch_count": len(batch),
        "next_cursor": next_cursor,
        "candidates": batch,
        "process_catalog_binding": _binding(
            process_catalog_binding, "process_catalog_binding"
        ),
        "admission_criteria_lowered": False,
        "provider_relationship_assumed": False,
        "next_route": "evaluate-public-result-root-batch",
    }
    body["batch_id"] = _digest(body)[:16]
    return body
