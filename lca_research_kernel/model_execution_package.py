"""Compile a content-addressed local route-model execution closure."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple


JsonObject = Dict[str, object]
MODEL_PACKAGE_CONTRACT = "lca_research_kernel.native_route_model_execution_package/v1"
TIANGONG_PACKAGE = "@tiangong-lca/cli@0.0.25"
EXPECTED_ROUTES = {"hydrothermal", "solid-state"}


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _hash(value: object) -> str:
    return _sha256(_json_bytes(value))


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _list(value: object, label: str) -> List[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _binding(value: object, label: str) -> JsonObject:
    row = _object(value, label)
    path = _text(row.get("path"), f"{label}.path")
    digest = _text(row.get("sha256"), f"{label}.sha256")
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError(f"{label}.sha256 must be a lowercase SHA-256")
    return {"path": path, "sha256": digest}


def _verify_binding(content: bytes, value: object, label: str) -> JsonObject:
    bound = _binding(value, label)
    if _sha256(content) != bound["sha256"]:
        raise ValueError(f"{label} hash drifted")
    return bound


def _process_root(payload: JsonObject, label: str) -> JsonObject:
    root = payload.get("processDataSet")
    if not isinstance(root, dict):
        raise ValueError(f"{label} does not contain processDataSet")
    return root


def _process_identity(payload: JsonObject, label: str) -> Tuple[str, str]:
    root = _process_root(payload, label)
    information = _object(root.get("processInformation"), f"{label} information")
    dataset = _object(information.get("dataSetInformation"), f"{label} dataset")
    administration = _object(
        root.get("administrativeInformation"), f"{label} administration"
    )
    publication = _object(
        administration.get("publicationAndOwnership"), f"{label} publication"
    )
    return (
        _text(dataset.get("common:UUID"), f"{label} UUID"),
        _text(publication.get("common:dataSetVersion"), f"{label} version"),
    )


def _exchanges(payload: JsonObject, label: str) -> List[JsonObject]:
    root = _process_root(payload, label)
    wrapper = _object(root.get("exchanges"), f"{label} exchanges")
    raw = wrapper.get("exchange")
    values = raw if isinstance(raw, list) else [raw]
    rows = [row for row in values if isinstance(row, dict)]
    if not rows:
        raise ValueError(f"{label} has no exchange")
    return rows


def _decimal(value: object, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{label} must be numeric") from error
    if not parsed.is_finite():
        raise ValueError(f"{label} must be finite")
    return parsed.normalize()


def _exchange_semantics(
    payload: JsonObject, label: str
) -> List[Tuple[str, str, Decimal, Decimal]]:
    rows = []
    for index, exchange in enumerate(_exchanges(payload, label)):
        flow = _object(
            exchange.get("referenceToFlowDataSet"), f"{label} exchange {index} flow"
        )
        mean = _decimal(exchange.get("meanAmount"), f"{label} exchange {index} mean")
        resulting = _decimal(
            exchange.get("resultingAmount", exchange.get("meanAmount")),
            f"{label} exchange {index} resulting",
        )
        rows.append(
            (
                _text(flow.get("@refObjectId"), f"{label} exchange {index} flow UUID"),
                _text(
                    exchange.get("exchangeDirection"),
                    f"{label} exchange {index} direction",
                ),
                mean,
                resulting,
            )
        )
    return sorted(rows)


def _reference_semantics(
    payload: JsonObject, label: str
) -> Tuple[str, str, Decimal, Decimal, str]:
    root = _process_root(payload, label)
    information = _object(root.get("processInformation"), f"{label} information")
    quantitative = _object(
        information.get("quantitativeReference"), f"{label} quantitative reference"
    )
    reference_id = str(quantitative.get("referenceToReferenceFlow", ""))
    exchange = next(
        (
            row
            for row in _exchanges(payload, label)
            if str(row.get("@dataSetInternalID", "")) == reference_id
        ),
        None,
    )
    if not isinstance(exchange, dict):
        raise ValueError(f"{label} reference exchange is missing")
    flow = _object(exchange.get("referenceToFlowDataSet"), f"{label} reference flow")
    mean = _decimal(exchange.get("meanAmount"), f"{label} reference mean")
    resulting = _decimal(
        exchange.get("resultingAmount", exchange.get("meanAmount")),
        f"{label} reference resulting",
    )
    return (
        _text(flow.get("@refObjectId"), f"{label} reference UUID"),
        _text(flow.get("@version"), f"{label} reference version"),
        mean,
        resulting,
        _text(exchange.get("exchangeDirection"), f"{label} reference direction"),
    )


def _shell_identity(payload: JsonObject, label: str) -> Tuple[str, str, str, str]:
    root = _object(payload.get("lifeCycleModelDataSet"), f"{label} root")
    information = _object(
        root.get("lifeCycleModelInformation"), f"{label} information"
    )
    dataset = _object(information.get("dataSetInformation"), f"{label} dataset")
    technology = _object(information.get("technology"), f"{label} technology")
    processes = _object(technology.get("processes"), f"{label} processes")
    instance = _object(processes.get("processInstance"), f"{label} instance")
    process_ref = _object(instance.get("referenceToProcess"), f"{label} Process ref")
    administration = _object(
        root.get("administrativeInformation"), f"{label} administration"
    )
    publication = _object(
        administration.get("publicationAndOwnership"), f"{label} publication"
    )
    return (
        _text(dataset.get("common:UUID"), f"{label} UUID"),
        _text(publication.get("common:dataSetVersion"), f"{label} version"),
        _text(process_ref.get("@refObjectId"), f"{label} Process UUID"),
        _text(process_ref.get("@version"), f"{label} Process version"),
    )


def _file_entry(kind: str, content: object) -> JsonObject:
    encoded = content.encode("utf-8") if isinstance(content, str) else _json_bytes(content)
    return {
        "kind": kind,
        "content": copy.deepcopy(content),
        "sha256": _sha256(encoded),
        "bytes": len(encoded),
    }


def _native_prefix() -> List[str]:
    return [
        "npm",
        "exec",
        "--yes",
        "--offline",
        f"--package={TIANGONG_PACKAGE}",
        "--",
        "tiangong-lca",
    ]


def compile_model_execution_package(
    scenario_rows: bytes,
    scenario_binding: JsonObject,
    shell_report: JsonObject,
    shell_report_binding: JsonObject,
    projection_report: JsonObject,
    projection_report_binding: JsonObject,
    readiness_report: JsonObject,
    readiness_report_binding: JsonObject,
    route_artifacts: Mapping[str, JsonObject],
    normalization_patch: JsonObject,
) -> JsonObject:
    """Compile a deterministic runtime closure without executing it."""

    scenario_ref = _verify_binding(
        scenario_rows, scenario_binding, "current Process rows binding"
    )
    shell_ref = _verify_binding(
        _json_bytes(shell_report), shell_report_binding, "shell report binding"
    )
    projection_ref = _verify_binding(
        _json_bytes(projection_report),
        projection_report_binding,
        "projection report binding",
    )
    readiness_ref = _verify_binding(
        _json_bytes(readiness_report), readiness_report_binding, "readiness binding"
    )
    if (
        shell_report.get("contract")
        != "lca_research_kernel.native_route_lifecyclemodel_shell_materialization/v1"
        or shell_report.get("status")
        != "native_route_lifecyclemodel_shells_materialized"
        or shell_report.get("tool_identity") != TIANGONG_PACKAGE
        or shell_report.get("route_count") != 2
        or shell_report.get("native_valid_count") != 2
    ):
        raise ValueError("accepted lifecyclemodel shell report is required")
    if (
        projection_report.get("contract")
        != "lca_research_kernel.native_route_resulting_process_dry_run/v1"
        or projection_report.get("status")
        != "native_route_resulting_process_dry_run_completed"
        or projection_report.get("tool_identity") != TIANGONG_PACKAGE
        or projection_report.get("route_count") != 2
        or projection_report.get("resulting_process_projected_count") != 2
        or projection_report.get("native_valid_count") != 2
        or projection_report.get("native_invalid_count") != 0
        or projection_report.get("native_qa_blocker_count") != 0
        or projection_report.get("native_dry_run_prepared_count") != 2
        or projection_report.get("remote_resolution_count") != 0
        or projection_report.get("scientific_comparison_ready") is not False
        or projection_report.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("accepted resulting Process projection report is required")
    blocked_checks = [
        row
        for row in _list(readiness_report.get("checks"), "readiness checks")
        if isinstance(row, dict) and row.get("status") == "blocked"
    ]
    if (
        readiness_report.get("contract")
        != "lca_research_kernel.current_comparison_readiness_gate/v1"
        or readiness_report.get("status")
        != "current_comparison_readiness_gate_compiled"
        or readiness_report.get("blocked_check_count") != 9
        or len(blocked_checks) != 9
        or len({row.get("check_id") for row in blocked_checks}) != 9
        or any(
            not isinstance(row.get("release_conditions"), list)
            or not row.get("release_conditions")
            or not isinstance(row.get("responsible_activity"), str)
            for row in blocked_checks
        )
        or readiness_report.get("scientific_comparison_ready") is not False
        or readiness_report.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("exactly nine retained blocked readiness checks are required")
    if (
        normalization_patch.get("contract")
        != "lca_research_kernel.native_route_resulting_process_dry_run/v1"
        or normalization_patch.get("patch_status") != "completed"
        or normalization_patch.get("normalization_kind")
        != "lossless_json_numeric_to_tidas_string"
        or normalization_patch.get("route_count") != 2
        or not isinstance(normalization_patch.get("replacement_count"), int)
        or normalization_patch.get("replacement_count", 0) <= 0
        or normalization_patch.get("replacement_count")
        != projection_report.get("native_normalization_replacement_count")
        or len(_list(normalization_patch.get("patch_sets"), "normalization patch sets"))
        != 2
        or normalization_patch.get("exchange_semantics_changed") is not False
        or normalization_patch.get("scientific_values_changed") is not False
        or normalization_patch.get("remote_write_authorized") is not False
    ):
        raise ValueError("accepted lossless normalization patch is required")
    scope_id = _text(projection_report.get("scope_id"), "projection scope_id")
    revision = _text(projection_report.get("revision"), "projection revision")
    if (
        shell_report.get("scope_id") != scope_id
        or shell_report.get("revision") != revision
        or readiness_report.get("scope_id") != scope_id
        or readiness_report.get("revision") != revision
    ):
        raise ValueError("package source scope or revision drifted")
    if set(route_artifacts) != EXPECTED_ROUTES:
        raise ValueError("both exact route artifact closures are required")

    process_rows: Dict[str, Tuple[JsonObject, bytes]] = {}
    for line in scenario_rows.splitlines(keepends=True):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError("current Process rows must be valid JSONL") from error
        if not isinstance(row, dict):
            raise ValueError("current Process row must be an object")
        process_id, _ = _process_identity(row, "current Process")
        if process_id in process_rows:
            raise ValueError("current Process identities must be unique")
        process_rows[process_id] = (row, line)
    if len(process_rows) != 2:
        raise ValueError("exactly two current Process rows are required")

    shell_route_rows = _list(shell_report.get("routes"), "shell routes")
    projection_route_rows = _list(projection_report.get("routes"), "projection routes")
    if len(shell_route_rows) != 2 or len(projection_route_rows) != 2:
        raise ValueError("source reports must contain two non-duplicate route rows")
    shell_routes = {
        str(row.get("route_id")): row
        for row in shell_route_rows
        if isinstance(row, dict)
    }
    projection_routes = {
        str(row.get("route_id")): row
        for row in projection_route_rows
        if isinstance(row, dict)
    }
    native_root = _object(
        projection_report.get("native_artifacts"), "projection native artifacts"
    )
    native_routes = _object(native_root.get("routes"), "projection route artifacts")
    patch_binding = _binding(
        native_root.get("normalization_patch"), "normalization patch artifact"
    )
    if _hash(normalization_patch) != patch_binding["sha256"]:
        raise ValueError("normalization patch hash drifted")
    if set(shell_routes) != EXPECTED_ROUTES or set(projection_routes) != EXPECTED_ROUTES:
        raise ValueError("source reports must contain both exact routes")

    package_files: Dict[str, JsonObject] = {}
    package_routes: List[JsonObject] = []
    shell_payloads: List[JsonObject] = []
    normalized_payloads: List[JsonObject] = []
    relations: List[JsonObject] = []
    prefix = _native_prefix()
    replay_steps: List[JsonObject] = []
    for route_id in sorted(EXPECTED_ROUTES):
        shell_route = shell_routes[route_id]
        projection_route = projection_routes[route_id]
        artifacts = _object(route_artifacts[route_id], f"{route_id} route artifacts")
        native_binding = _object(native_routes.get(route_id), f"{route_id} native bindings")
        process_id = _text(projection_route.get("source_process_id"), "source Process UUID")
        process_version = _text(
            projection_route.get("source_process_version"), "source Process version"
        )
        model_id = _text(projection_route.get("model_id"), "model UUID")
        model_version = _text(projection_route.get("model_version"), "model version")
        if process_id not in process_rows:
            raise ValueError(f"{route_id} current Process row is missing")
        process, process_bytes = process_rows[process_id]
        shell = _object(artifacts.get("shell"), f"{route_id} shell")
        request = _object(artifacts.get("request"), f"{route_id} request")
        bundle = _object(artifacts.get("native_bundle"), f"{route_id} native bundle")
        raw = _object(
            artifacts.get("raw_resulting_process"), f"{route_id} raw resulting Process"
        )
        normalized = _object(
            artifacts.get("resulting_process"), f"{route_id} resulting Process"
        )
        if _sha256(process_bytes) != projection_route.get("source_process_sha256"):
            raise ValueError(f"{route_id} current Process hash drifted")
        if _process_identity(process, f"{route_id} current Process") != (
            process_id,
            process_version,
        ):
            raise ValueError(f"{route_id} current Process identity drifted")
        if (
            shell_route.get("model_id") != model_id
            or shell_route.get("model_version") != model_version
            or shell_route.get("process_id") != process_id
            or shell_route.get("process_version") != process_version
            or projection_route.get("resulting_process_id") != model_id
            or projection_route.get("resulting_process_version") != model_version
        ):
            raise ValueError(f"{route_id} accepted route identity drifted")
        if _hash(shell) != shell_route.get("model_sha256"):
            raise ValueError(f"{route_id} shell hash drifted")
        if _shell_identity(shell, f"{route_id} shell") != (
            model_id,
            model_version,
            process_id,
            process_version,
        ):
            raise ValueError(f"{route_id} shell identity drifted")
        if _hash(raw) != projection_route.get("raw_projected_process_sha256"):
            raise ValueError(f"{route_id} raw resulting Process hash drifted")
        if _hash(normalized) != projection_route.get("projected_process_sha256"):
            raise ValueError(f"{route_id} resulting Process hash drifted")
        if _process_identity(raw, f"{route_id} raw resulting Process") != (
            model_id,
            model_version,
        ) or _process_identity(normalized, f"{route_id} resulting Process") != (
            model_id,
            model_version,
        ):
            raise ValueError(f"{route_id} resulting Process identity drifted")
        if _exchange_semantics(raw, f"{route_id} raw resulting Process") != _exchange_semantics(
            normalized, f"{route_id} resulting Process"
        ):
            raise ValueError(f"{route_id} normalization changed exchange semantics")
        reference = _reference_semantics(process, f"{route_id} current Process")
        if _reference_semantics(normalized, f"{route_id} resulting Process") != reference:
            raise ValueError(f"{route_id} resulting Process reference semantics drifted")
        if (
            reference[0] != projection_route.get("reference_flow_id")
            or reference[1] != projection_route.get("reference_flow_version")
            or reference[2]
            != _decimal(projection_route.get("reference_amount"), "report reference amount")
            or reference[4] != projection_route.get("reference_direction")
        ):
            raise ValueError(f"{route_id} accepted reference facts drifted")
        request_ref = _binding(native_binding.get("request"), f"{route_id} request binding")
        raw_ref = _binding(
            native_binding.get("raw_projected_process"), f"{route_id} raw binding"
        )
        normalized_ref = _binding(
            native_binding.get("projected_process"), f"{route_id} normalized binding"
        )
        native_files = _object(native_binding.get("native"), f"{route_id} native files")
        bundle_ref = _binding(
            native_files.get("process_projection_bundle"), f"{route_id} bundle binding"
        )
        for actual, expected, label in (
            (_hash(request), request_ref["sha256"], "request"),
            (_hash(raw), raw_ref["sha256"], "raw resulting Process"),
            (_hash(normalized), normalized_ref["sha256"], "resulting Process"),
        ):
            if actual != expected:
                raise ValueError(f"{route_id} {label} artifact hash drifted")
        projected_rows = _list(
            bundle.get("projected_processes"), f"{route_id} projected Processes"
        )
        if (
            len(projected_rows) != 1
            or not isinstance(projected_rows[0], dict)
            or projected_rows[0].get("id") != model_id
            or projected_rows[0].get("version") != model_version
            or projected_rows[0].get("role") != "primary"
            or projected_rows[0].get("json_ordered") != raw
        ):
            raise ValueError(f"{route_id} native bundle projection drifted")
        relation_rows = [
            row for row in _list(bundle.get("relations"), f"{route_id} relations") if isinstance(row, dict)
        ]
        if len(relation_rows) != 1:
            raise ValueError(f"{route_id} must contain one native relation")
        relation = relation_rows[0]
        if (
            relation.get("lifecyclemodel_id") != model_id
            or relation.get("lifecyclemodel_version") != model_version
            or relation.get("resulting_process_id") != model_id
            or relation.get("resulting_process_version") != model_version
            or relation.get("projection_signature")
            != projection_route.get("projection_signature")
            or relation.get("is_primary") is not True
        ):
            raise ValueError(f"{route_id} native relation drifted")
        source_files = _object(
            request.get("process_sources"), f"{route_id} process sources"
        )
        source_model = _object(request.get("source_model"), f"{route_id} source model")
        projection = _object(request.get("projection"), f"{route_id} projection")
        publish = _object(request.get("publish"), f"{route_id} publish intent")
        process_paths = source_files.get("process_json_files")
        if (
            source_model.get("id") != model_id
            or source_model.get("version") != model_version
            or source_model.get("json_ordered_path") != "inputs/lifecyclemodel.json"
            or projection.get("mode") != "primary-only"
            or projection.get("process_id") != model_id
            or projection.get("process_version") != model_version
            or not isinstance(process_paths, list)
            or len(process_paths) != 1
            or not isinstance(process_paths[0], str)
            or source_files.get("allow_remote_lookup") is not False
            or publish.get("intent") != "dry_run"
        ):
            raise ValueError(f"{route_id} replay request is not local-only")
        process_relative_path = process_paths[0]
        if process_relative_path.startswith("/") or ".." in process_relative_path.split("/"):
            raise ValueError(f"{route_id} replay Process path is unsafe")
        route_root = f"routes/{route_id}"
        entries = {
            f"{route_root}/projection-request.json": _file_entry("json", request),
            f"{route_root}/inputs/lifecyclemodel.json": _file_entry("json", shell),
            f"{route_root}/{process_relative_path}": _file_entry(
                "bytes", process_bytes.decode("utf-8")
            ),
            f"{route_root}/expected/raw-resulting-process.json": _file_entry("json", raw),
            f"{route_root}/expected/resulting-process.json": _file_entry(
                "json", normalized
            ),
            f"{route_root}/relation.json": _file_entry("json", relation),
        }
        package_files.update(entries)
        shell_payloads.append(shell)
        normalized_payloads.append(normalized)
        relations.append(relation)
        build_step_id = f"{route_id}.build-resulting-process"
        replay_steps.extend(
            [
                {
                    "step_id": build_step_id,
                    "kind": "native_command",
                    "depends_on": [],
                    "command": [
                        *prefix,
                        "lifecyclemodel",
                        "build-resulting-process",
                        "--input",
                        f"{route_root}/projection-request.json",
                        "--out-dir",
                        f"replay/routes/{route_id}/native-projection",
                        "--json",
                    ],
                },
                {
                    "step_id": f"{route_id}.verify-raw-projection",
                    "kind": "kernel_semantic_verification",
                    "depends_on": [build_step_id],
                    "expected_raw_sha256": _hash(raw),
                    "expected_projection_signature": relation.get(
                        "projection_signature"
                    ),
                },
            ]
        )
        package_routes.append(
            {
                "route_id": route_id,
                "source_process_id": process_id,
                "source_process_version": process_version,
                "source_process_sha256": _sha256(process_bytes),
                "model_id": model_id,
                "model_version": model_version,
                "model_sha256": _hash(shell),
                "resulting_process_id": model_id,
                "resulting_process_version": model_version,
                "raw_resulting_process_sha256": _hash(raw),
                "resulting_process_sha256": _hash(normalized),
                "projection_signature": relation.get("projection_signature"),
                "reference_flow_id": reference[0],
                "reference_flow_version": reference[1],
                "reference_amount": str(reference[2]),
                "exchange_count": len(_exchanges(normalized, route_id)),
                "replay_step_count": 5,
            }
        )

    package_files["datasets/current-processes.jsonl"] = _file_entry(
        "bytes", scenario_rows.decode("utf-8")
    )
    package_files["datasets/lifecyclemodels.jsonl"] = _file_entry(
        "bytes", b"".join(_json_bytes(row) for row in shell_payloads).decode("utf-8")
    )
    package_files["datasets/resulting-processes.jsonl"] = _file_entry(
        "bytes", b"".join(_json_bytes(row) for row in normalized_payloads).decode("utf-8")
    )
    package_files["datasets/all-processes.jsonl"] = _file_entry(
        "bytes",
        (
            scenario_rows
            + b"".join(_json_bytes(row) for row in normalized_payloads)
        ).decode("utf-8"),
    )
    package_files["normalization/resulting-process-normalization-patch.json"] = (
        _file_entry("json", normalization_patch)
    )
    blocker_register = {
        "contract": MODEL_PACKAGE_CONTRACT,
        "scope_id": scope_id,
        "revision": revision,
        "status": "retained_blockers",
        "blocked_check_count": 9,
        "blocked_scientific_gap_count": readiness_report.get(
            "blocked_scientific_gap_count"
        ),
        "unresolved_field_count": readiness_report.get("unresolved_field_count"),
        "unresolved_claim_count": readiness_report.get("unresolved_claim_count"),
        "prohibited_claim_count": readiness_report.get("prohibited_claim_count"),
        "checks": copy.deepcopy(blocked_checks),
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
    }
    package_files["policy/comparison-blockers.json"] = _file_entry(
        "json", blocker_register
    )
    package_files["lineage/resulting-process-relations.jsonl"] = _file_entry(
        "bytes", b"".join(_json_bytes(row) for row in relations).decode("utf-8")
    )
    replay_steps.extend(
        [
            {
                "step_id": "combine.raw-resulting-processes",
                "kind": "kernel_projection_extract",
                "depends_on": [
                    f"{route_id}.verify-raw-projection"
                    for route_id in sorted(EXPECTED_ROUTES)
                ],
                "output": "replay/raw-resulting-processes.jsonl",
            },
            {
                "step_id": "normalize.resulting-processes",
                "kind": "native_command",
                "depends_on": ["combine.raw-resulting-processes"],
                "command": [
                    *prefix,
                    "dataset",
                    "patch",
                    "apply",
                    "--input",
                    "replay/raw-resulting-processes.jsonl",
                    "--patch",
                    "normalization/resulting-process-normalization-patch.json",
                    "--out",
                    "replay/resulting-processes.jsonl",
                    "--out-dir",
                    "replay/native-normalization",
                    "--json",
                ],
            },
            {
                "step_id": "verify.normalized-resulting-processes",
                "kind": "kernel_semantic_verification",
                "depends_on": ["normalize.resulting-processes"],
                "expected_sha256": _sha256(
                    b"".join(_json_bytes(row) for row in normalized_payloads)
                ),
            },
        ]
    )
    replay_plan = {
        "schema_version": 1,
        "contract": MODEL_PACKAGE_CONTRACT,
        "tool_identity": TIANGONG_PACKAGE,
        "network_read_allowed": False,
        "remote_write_allowed": False,
        "publication_allowed": False,
        "comparison_allowed": False,
        "lcia_allowed": False,
        "step_count": len(replay_steps),
        "steps": replay_steps,
    }
    package_files["replay/replay-plan.json"] = _file_entry("json", replay_plan)
    bindings = {
        path: {
            "path": path,
            "sha256": row["sha256"],
            "bytes": row["bytes"],
            "kind": row["kind"],
        }
        for path, row in sorted(package_files.items())
    }
    identity = {
        "contract": MODEL_PACKAGE_CONTRACT,
        "tool_identity": TIANGONG_PACKAGE,
        "sources": {
            "current_processes": scenario_ref,
            "lifecyclemodel_shell_report": shell_ref,
            "resulting_process_report": projection_ref,
            "comparison_readiness_report": readiness_ref,
        },
        "files": bindings,
    }
    package_version = _hash(identity)[:16]
    return {
        "schema_version": 1,
        "contract": MODEL_PACKAGE_CONTRACT,
        "scope_id": scope_id,
        "revision": revision,
        "status": "native_route_model_execution_package_compiled",
        "package_version": package_version,
        "tool_identity": TIANGONG_PACKAGE,
        "source_bindings": identity["sources"],
        "route_count": 2,
        "dataset_count": 6,
        "routes": package_routes,
        "file_count": len(package_files),
        "file_bindings": bindings,
        "package_files": package_files,
        "replay_plan": replay_plan,
        "blocked_check_count": 9,
        "preserved_scientific_gap_count": projection_report.get(
            "preserved_scientific_gap_count"
        ),
        "preserved_unresolved_field_count": projection_report.get(
            "preserved_unresolved_field_count"
        ),
        "preserved_unresolved_claim_count": projection_report.get(
            "preserved_unresolved_claim_count"
        ),
        "preserved_prohibited_claim_count": projection_report.get(
            "preserved_prohibited_claim_count"
        ),
        "network_read_allowed": False,
        "remote_write_allowed": False,
        "publication_allowed": False,
        "comparison_allowed": False,
        "lcia_allowed": False,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
    }


def _jsonl_payloads(content: bytes, label: str) -> List[JsonObject]:
    rows: List[JsonObject] = []
    for line in content.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{label} contains invalid JSONL") from error
        if not isinstance(row, dict):
            raise ValueError(f"{label} row must be a JSON object")
        rows.append(row)
    return rows


def _safe_package_path(root: Path, relative_path: str) -> Path:
    if Path(relative_path).is_absolute() or ".." in Path(relative_path).parts:
        raise ValueError(f"unsafe package path: {relative_path}")
    resolved = (root / relative_path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"package path escaped root: {relative_path}") from error
    return resolved


def verify_model_execution_package(
    compiled: JsonObject, package_root: Path
) -> JsonObject:
    """Re-hash and semantically verify every file in a materialized closure."""

    if (
        compiled.get("contract") != MODEL_PACKAGE_CONTRACT
        or compiled.get("status")
        != "native_route_model_execution_package_compiled"
        or compiled.get("route_count") != 2
        or compiled.get("dataset_count") != 6
        or compiled.get("blocked_check_count") != 9
    ):
        raise ValueError("compiled route-model package is required")
    bindings = _object(compiled.get("file_bindings"), "package file bindings")
    files = _object(compiled.get("package_files"), "package files")
    if set(bindings) != set(files) or compiled.get("file_count") != len(files):
        raise ValueError("package file index drifted")
    byte_count = 0
    for relative_path, raw_binding in sorted(bindings.items()):
        binding = _object(raw_binding, f"{relative_path} binding")
        path = _safe_package_path(package_root, relative_path)
        if not path.is_file():
            raise ValueError(f"package file is missing: {relative_path}")
        content = path.read_bytes()
        if len(content) != binding.get("bytes"):
            raise ValueError(f"package file size drifted: {relative_path}")
        if _sha256(content) != binding.get("sha256"):
            raise ValueError(f"package file hash drifted: {relative_path}")
        byte_count += len(content)

    current_bytes = (package_root / "datasets/current-processes.jsonl").read_bytes()
    resulting_bytes = (package_root / "datasets/resulting-processes.jsonl").read_bytes()
    all_process_bytes = (package_root / "datasets/all-processes.jsonl").read_bytes()
    if all_process_bytes != current_bytes + resulting_bytes:
        raise ValueError("combined Process gate rows drifted")
    current_rows = _jsonl_payloads(current_bytes, "current Process rows")
    resulting_rows = _jsonl_payloads(resulting_bytes, "resulting Process rows")
    shell_rows = _jsonl_payloads(
        (package_root / "datasets/lifecyclemodels.jsonl").read_bytes(),
        "lifecyclemodel rows",
    )
    current_by_id = {
        _process_identity(row, "packaged current Process")[0]: row
        for row in current_rows
    }
    resulting_by_id = {
        _process_identity(row, "packaged resulting Process")[0]: row
        for row in resulting_rows
    }
    shell_by_id = {
        _shell_identity(row, "packaged lifecyclemodel")[0]: row for row in shell_rows
    }
    routes = _list(compiled.get("routes"), "package routes")
    if (
        len(current_rows) != 2
        or len(resulting_rows) != 2
        or len(shell_rows) != 2
        or len(current_by_id) != 2
        or len(resulting_by_id) != 2
        or len(shell_by_id) != 2
        or len(routes) != 2
    ):
        raise ValueError("package must retain six unique authoritative datasets")
    for raw_route in routes:
        route = _object(raw_route, "package route")
        route_id = _text(route.get("route_id"), "package route_id")
        source_id = _text(route.get("source_process_id"), "source Process UUID")
        model_id = _text(route.get("model_id"), "model UUID")
        if (
            source_id not in current_by_id
            or model_id not in resulting_by_id
            or model_id not in shell_by_id
        ):
            raise ValueError(f"{route_id} package identity join drifted")
        current = current_by_id[source_id]
        resulting = resulting_by_id[model_id]
        shell = shell_by_id[model_id]
        if _shell_identity(shell, f"{route_id} lifecyclemodel")[2:] != _process_identity(
            current, f"{route_id} current Process"
        ):
            raise ValueError(f"{route_id} lifecyclemodel Process join drifted")
        if _reference_semantics(
            resulting, f"{route_id} resulting Process"
        ) != _reference_semantics(current, f"{route_id} current Process"):
            raise ValueError(f"{route_id} reference semantics drifted")
        raw_path = package_root / f"routes/{route_id}/expected/raw-resulting-process.json"
        try:
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"{route_id} raw resulting Process is invalid") from error
        if not isinstance(raw, dict) or _exchange_semantics(
            raw, f"{route_id} raw resulting Process"
        ) != _exchange_semantics(resulting, f"{route_id} resulting Process"):
            raise ValueError(f"{route_id} normalized exchange semantics drifted")
    return {
        "status": "package_integrity_verified",
        "verified_file_count": len(bindings),
        "verified_byte_count": byte_count,
        "verified_dataset_count": 6,
        "verified_route_count": 2,
        "hash_mismatch_count": 0,
        "missing_file_count": 0,
        "semantic_mismatch_count": 0,
    }


def materialize_model_execution_package(
    compiled: JsonObject, package_root: Path
) -> JsonObject:
    """Write the immutable payload closure and immediately verify it."""

    files = _object(compiled.get("package_files"), "package files")
    package_root.mkdir(parents=True, exist_ok=True)
    for relative_path, raw_entry in sorted(files.items()):
        entry = _object(raw_entry, f"{relative_path} package entry")
        target = _safe_package_path(package_root, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        kind = entry.get("kind")
        content = entry.get("content")
        if kind == "bytes" and isinstance(content, str):
            encoded = content.encode("utf-8")
        elif kind == "json":
            encoded = _json_bytes(content)
        else:
            raise ValueError(f"unsupported package file kind: {relative_path}")
        if _sha256(encoded) != entry.get("sha256") or len(encoded) != entry.get("bytes"):
            raise ValueError(f"compiled package entry drifted: {relative_path}")
        if target.exists() and target.read_bytes() != encoded:
            raise ValueError(f"existing package file drifted: {relative_path}")
        target.write_bytes(encoded)
    return verify_model_execution_package(compiled, package_root)


def native_model_package_commands(
    package_root: Path,
    gate_root: Optional[Path] = None,
    tiangong_package: str = TIANGONG_PACKAGE,
) -> List[List[str]]:
    """Return five pinned offline structural gates for the copied closure."""

    if tiangong_package != TIANGONG_PACKAGE:
        raise ValueError(f"tiangong package must remain pinned to {TIANGONG_PACKAGE}")
    prefix = _native_prefix()
    gates = gate_root if gate_root is not None else package_root / "native-gates"
    all_processes = package_root / "datasets" / "all-processes.jsonl"
    lifecyclemodels = package_root / "datasets" / "lifecyclemodels.jsonl"
    return [
        [
            *prefix,
            "dataset",
            "validate",
            "--input",
            str(all_processes),
            "--type",
            "process",
            "--out-dir",
            str(gates / "process-validation"),
            "--json",
        ],
        [
            *prefix,
            "qa",
            "process",
            "--rows-file",
            str(all_processes),
            "--out-dir",
            str(gates / "process-qa"),
            "--json",
        ],
        [
            *prefix,
            "dataset",
            "validate",
            "--input",
            str(lifecyclemodels),
            "--type",
            "lifecyclemodel",
            "--out-dir",
            str(gates / "lifecyclemodel-validation"),
            "--json",
        ],
        [
            *prefix,
            "lifecyclemodel",
            "graph",
            "--input",
            str(lifecyclemodels),
            "--out-dir",
            str(gates / "lifecyclemodel-graph"),
            "--format",
            "all",
            "--check-connections",
            "--json",
        ],
        [
            *prefix,
            "qa",
            "lifecyclemodel",
            "--rows-file",
            str(lifecyclemodels),
            "--out-dir",
            str(gates / "lifecyclemodel-qa"),
            "--json",
        ],
    ]


def _ids(rows: object, key: str) -> List[str]:
    if not isinstance(rows, list):
        return []
    return sorted(
        str(row.get(key))
        for row in rows
        if isinstance(row, dict) and row.get(key)
    )


def verify_native_model_package_gates(
    compiled: JsonObject, native: Mapping[str, object]
) -> JsonObject:
    """Fail closed unless all five native structural gates agree with the package."""

    routes = [
        _object(row, "package route")
        for row in _list(compiled.get("routes"), "package routes")
    ]
    if (
        compiled.get("contract") != MODEL_PACKAGE_CONTRACT
        or compiled.get("tool_identity") != TIANGONG_PACKAGE
        or len(routes) != 2
        or compiled.get("blocked_check_count") != 9
    ):
        raise ValueError("compiled route-model package is required")
    model_ids = sorted(str(row.get("model_id")) for row in routes)
    process_ids = sorted(
        str(identity)
        for row in routes
        for identity in (row.get("source_process_id"), row.get("resulting_process_id"))
    )
    process_validation = _object(
        native.get("process_validation"), "native Process validation"
    )
    process_counts = _object(process_validation.get("counts"), "Process validation counts")
    process_rows = _list(process_validation.get("rows"), "Process validation rows")
    if (
        process_validation.get("status") != "completed"
        or process_validation.get("requested_type") != "process"
        or process_counts.get("total") != 4
        or process_counts.get("valid") != 4
        or process_counts.get("invalid") != 0
        or _ids(process_rows, "id") != process_ids
        or any(
            not isinstance(row, dict)
            or row.get("type") != "process"
            or row.get("status") != "valid"
            or row.get("issue_count") != 0
            for row in process_rows
        )
    ):
        raise ValueError("native Process validation must pass 4/4")
    process_qa = _object(native.get("process_qa"), "native Process QA")
    process_gate = _object(process_qa.get("ruleset_gate"), "Process QA gate")
    process_gate_counts = _object(process_gate.get("counts"), "Process QA counts")
    process_findings = _list(
        native.get("process_qa_findings"), "Process QA findings"
    )
    if (
        process_qa.get("status") != "completed_local_process_qa"
        or process_qa.get("input_mode") != "rows_file"
        or process_qa.get("process_count") != 4
        or process_qa.get("blocker_count") != 0
        or process_qa.get("rule_finding_count") != len(process_findings)
        or process_gate.get("status") != "passed"
        or process_gate_counts.get("blockers") != 0
        or process_gate.get("blockers") != []
    ):
        raise ValueError("native Process QA must pass four subjects with zero blocker")
    model_validation = _object(
        native.get("lifecyclemodel_validation"), "native lifecyclemodel validation"
    )
    model_counts = _object(model_validation.get("counts"), "model validation counts")
    model_rows = _list(model_validation.get("rows"), "model validation rows")
    if (
        model_validation.get("status") != "completed"
        or model_validation.get("requested_type") != "lifecyclemodel"
        or model_counts.get("total") != 2
        or model_counts.get("valid") != 2
        or model_counts.get("invalid") != 0
        or _ids(model_rows, "id") != model_ids
        or any(
            not isinstance(row, dict)
            or row.get("type") != "lifecyclemodel"
            or row.get("status") != "valid"
            or row.get("issue_count") != 0
            for row in model_rows
        )
    ):
        raise ValueError("native lifecyclemodel validation must pass 2/2")
    graph = _object(native.get("lifecyclemodel_graph"), "native lifecyclemodel graph")
    graph_counts = _object(graph.get("counts"), "graph counts")
    graph_models = _list(graph.get("models"), "graph models")
    graph_findings = _list(
        native.get("lifecyclemodel_graph_findings"), "graph findings"
    )
    if (
        graph.get("status") != "completed"
        or graph.get("check_connections") is not True
        or graph_counts != {"models": 2, "nodes": 2, "edges": 0, "findings": 0}
        or graph_findings
        or _ids(graph_models, "id") != model_ids
        or any(
            not isinstance(row, dict)
            or row.get("node_count") != 1
            or row.get("edge_count") != 0
            or row.get("finding_count") != 0
            for row in graph_models
        )
    ):
        raise ValueError("native lifecyclemodel graph must retain two isolated nodes")
    model_qa = _object(native.get("lifecyclemodel_qa"), "native lifecyclemodel QA")
    severity = _object(model_qa.get("severity_counts"), "lifecyclemodel QA severity")
    summaries = _list(model_qa.get("model_summaries"), "lifecyclemodel summaries")
    model_findings = _list(
        native.get("lifecyclemodel_qa_findings"), "lifecyclemodel QA findings"
    )
    if (
        model_qa.get("status") != "completed_local_lifecyclemodel_qa"
        or model_qa.get("input_mode") != "rows_file"
        or model_qa.get("model_count") != 2
        or severity.get("error") != 0
        or len(summaries) != 2
        or sorted(
            str(model_id)
            for row in summaries
            if isinstance(row, dict)
            for model_id in row.get("model_uuids", [])
        )
        != model_ids
        or any(
            not isinstance(row, dict)
            or row.get("process_instance_count") != 1
            or row.get("zero_multiplication_factor_count") != 0
            or row.get("resulting_process_uuids") != row.get("model_uuids")
            for row in summaries
        )
        or any(
            isinstance(row, dict) and row.get("severity") == "error"
            for row in model_findings
        )
    ):
        raise ValueError("native lifecyclemodel QA must pass 2/2 with zero error")
    return {
        "native_gate_status": "passed",
        "native_process_valid_count": 4,
        "native_process_invalid_count": 0,
        "native_process_qa_finding_count": len(process_findings),
        "native_process_qa_blocker_count": 0,
        "native_lifecyclemodel_valid_count": 2,
        "native_lifecyclemodel_invalid_count": 0,
        "native_graph_model_count": 2,
        "native_graph_node_count": 2,
        "native_graph_edge_count": 0,
        "native_graph_finding_count": 0,
        "native_lifecyclemodel_qa_error_count": 0,
        "native_lifecyclemodel_qa_warning_count": int(severity.get("warning", 0)),
        "network_read_executed_count": 0,
        "external_action_executed_count": 0,
        "remote_write_executed_count": 0,
        "publication_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "replay_executed_count": 0,
        "current_model_mutation_executed_count": 0,
        "claim_transition_executed_count": 0,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "routing": {
            "next_internal_route": "replay-native-route-model-execution-package",
            "pause_internal_work": False,
            "reason": (
                "The local closure is complete and structurally native-valid; replay it "
                "in isolation before any scientific comparison or LCIA."
            ),
        },
        "next_action": (
            "Replay the packaged route-model DAG in an isolated local output directory "
            "and require exact content and semantic results."
        ),
    }


def _run_native(command: Sequence[str], cwd: Path) -> None:
    environment = dict(os.environ)
    environment["npm_config_offline"] = "true"
    result = subprocess.run(
        list(command),
        cwd=str(cwd),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Pinned TianGong package gate failed: "
            + " ".join(command)
            + f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def _read_json(path: Path, label: str) -> JsonObject:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON: {path}") from error
    return _object(value, label)


def _bound_json_artifact(
    root: Path, binding_value: object, label: str
) -> JsonObject:
    binding = _binding(binding_value, f"{label} binding")
    path = _safe_package_path(root, str(binding["path"]))
    if not path.is_file():
        raise ValueError(f"{label} is missing: {path}")
    content = path.read_bytes()
    if _sha256(content) != binding["sha256"]:
        raise ValueError(f"{label} hash drifted")
    try:
        payload = json.loads(content)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error
    return _object(payload, label)


def _artifact_binding(path: Path, root: Path) -> JsonObject:
    content = path.read_bytes()
    return {
        "path": str(path.relative_to(root)),
        "sha256": _sha256(content),
        "bytes": len(content),
    }


def execute_model_execution_package(
    scenario_rows_path: Path,
    scenario_binding_path: Path,
    shell_report_path: Path,
    shell_report_binding_path: Path,
    shell_root: Path,
    projection_report_path: Path,
    projection_report_binding_path: Path,
    projection_root: Path,
    readiness_report_path: Path,
    readiness_report_binding_path: Path,
    out_dir: Path,
    tiangong_package: str = TIANGONG_PACKAGE,
) -> JsonObject:
    """Compile, write and native-verify the local route-model closure."""

    if tiangong_package != TIANGONG_PACKAGE:
        raise ValueError(f"tiangong package must remain pinned to {TIANGONG_PACKAGE}")
    scenario_rows = scenario_rows_path.read_bytes()
    scenario_binding = _read_json(scenario_binding_path, "scenario binding")
    shell_report = _read_json(shell_report_path, "lifecyclemodel shell report")
    shell_report_binding = _read_json(
        shell_report_binding_path, "lifecyclemodel shell report binding"
    )
    projection_report = _read_json(
        projection_report_path, "resulting Process report"
    )
    projection_report_binding = _read_json(
        projection_report_binding_path, "resulting Process report binding"
    )
    readiness_report = _read_json(readiness_report_path, "readiness report")
    readiness_report_binding = _read_json(
        readiness_report_binding_path, "readiness report binding"
    )
    shell_routes = {
        str(row.get("route_id")): row
        for row in _list(shell_report.get("routes"), "shell routes")
        if isinstance(row, dict)
    }
    native_root = _object(
        projection_report.get("native_artifacts"), "projection native artifacts"
    )
    native_routes = _object(native_root.get("routes"), "projection native routes")
    route_artifacts: Dict[str, JsonObject] = {}
    for route_id in sorted(EXPECTED_ROUTES):
        shell_route = _object(shell_routes.get(route_id), f"{route_id} shell route")
        shell_path = _safe_package_path(
            shell_root, _text(shell_route.get("model_path"), f"{route_id} shell path")
        )
        shell_content = shell_path.read_bytes()
        if _sha256(shell_content) != shell_route.get("model_sha256"):
            raise ValueError(f"{route_id} lifecyclemodel shell hash drifted")
        try:
            shell = json.loads(shell_content)
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"{route_id} shell is invalid JSON") from error
        route_bindings = _object(
            native_routes.get(route_id), f"{route_id} projection bindings"
        )
        native_bindings = _object(
            route_bindings.get("native"), f"{route_id} native bindings"
        )
        route_artifacts[route_id] = {
            "shell": _object(shell, f"{route_id} shell"),
            "request": _bound_json_artifact(
                projection_root, route_bindings.get("request"), f"{route_id} request"
            ),
            "raw_resulting_process": _bound_json_artifact(
                projection_root,
                route_bindings.get("raw_projected_process"),
                f"{route_id} raw resulting Process",
            ),
            "resulting_process": _bound_json_artifact(
                projection_root,
                route_bindings.get("projected_process"),
                f"{route_id} resulting Process",
            ),
            "native_bundle": _bound_json_artifact(
                projection_root,
                native_bindings.get("process_projection_bundle"),
                f"{route_id} native projection bundle",
            ),
        }
    normalization_patch = _bound_json_artifact(
        projection_root,
        native_root.get("normalization_patch"),
        "resulting Process normalization patch",
    )
    compiled = compile_model_execution_package(
        scenario_rows,
        scenario_binding,
        shell_report,
        shell_report_binding,
        projection_report,
        projection_report_binding,
        readiness_report,
        readiness_report_binding,
        route_artifacts,
        normalization_patch,
    )
    package_root = out_dir / "package"
    integrity = materialize_model_execution_package(compiled, package_root)
    gate_root = out_dir / "native-gates"
    for command in native_model_package_commands(
        package_root, gate_root, tiangong_package
    ):
        _run_native(command, out_dir)
    paths = {
        "process_validation": gate_root
        / "process-validation"
        / "outputs"
        / "validation-report.json",
        "process_qa": gate_root / "process-qa" / "process-qa-report.json",
        "process_qa_findings": gate_root
        / "process-qa"
        / "process-qa-rule-findings.jsonl",
        "process_qa_ruleset_gate": gate_root
        / "process-qa"
        / "process-qa-ruleset-gate.json",
        "lifecyclemodel_validation": gate_root
        / "lifecyclemodel-validation"
        / "outputs"
        / "validation-report.json",
        "lifecyclemodel_graph": gate_root
        / "lifecyclemodel-graph"
        / "outputs"
        / "graph-report.json",
        "lifecyclemodel_graph_findings": gate_root
        / "lifecyclemodel-graph"
        / "outputs"
        / "findings.jsonl",
        "lifecyclemodel_qa": gate_root
        / "lifecyclemodel-qa"
        / "lifecyclemodel_qa_report.json",
        "lifecyclemodel_qa_findings": gate_root
        / "lifecyclemodel-qa"
        / "findings.jsonl",
    }
    native: JsonObject = {
        "process_validation": _read_json(
            paths["process_validation"], "native Process validation"
        ),
        "process_qa": _read_json(paths["process_qa"], "native Process QA"),
        "process_qa_findings": _jsonl_payloads(
            paths["process_qa_findings"].read_bytes(), "Process QA findings"
        ),
        "lifecyclemodel_validation": _read_json(
            paths["lifecyclemodel_validation"], "native lifecyclemodel validation"
        ),
        "lifecyclemodel_graph": _read_json(
            paths["lifecyclemodel_graph"], "native lifecyclemodel graph"
        ),
        "lifecyclemodel_graph_findings": _jsonl_payloads(
            paths["lifecyclemodel_graph_findings"].read_bytes(), "graph findings"
        ),
        "lifecyclemodel_qa": _read_json(
            paths["lifecyclemodel_qa"], "native lifecyclemodel QA"
        ),
        "lifecyclemodel_qa_findings": _jsonl_payloads(
            paths["lifecyclemodel_qa_findings"].read_bytes(),
            "lifecyclemodel QA findings",
        ),
    }
    native_gates = verify_native_model_package_gates(compiled, native)
    report = {
        key: copy.deepcopy(value)
        for key, value in compiled.items()
        if key != "package_files"
    }
    report.update(native_gates)
    report.update(
        {
            "status": "native_route_model_execution_package_verified",
            "package_root": "package",
            "package_integrity": integrity,
            "native_artifacts": {
                key: _artifact_binding(path, out_dir) for key, path in paths.items()
            },
            "blocked_check_count": 9,
        }
    )
    manifest_path = package_root / "route-model-execution-package.json"
    output_path = out_dir / "outputs" / "native-route-model-execution-package.json"
    manifest_path.write_bytes(_json_bytes(report))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(_json_bytes(report))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario-rows", type=Path, required=True)
    parser.add_argument("--scenario-binding", type=Path, required=True)
    parser.add_argument("--shell-report", type=Path, required=True)
    parser.add_argument("--shell-report-binding", type=Path, required=True)
    parser.add_argument("--shell-root", type=Path, required=True)
    parser.add_argument("--projection-report", type=Path, required=True)
    parser.add_argument("--projection-report-binding", type=Path, required=True)
    parser.add_argument("--projection-root", type=Path, required=True)
    parser.add_argument("--readiness-report", type=Path, required=True)
    parser.add_argument("--readiness-report-binding", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--tiangong-package", default=TIANGONG_PACKAGE)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = execute_model_execution_package(
        args.scenario_rows,
        args.scenario_binding,
        args.shell_report,
        args.shell_report_binding,
        args.shell_root,
        args.projection_report,
        args.projection_report_binding,
        args.projection_root,
        args.readiness_report,
        args.readiness_report_binding,
        args.out_dir,
        args.tiangong_package,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
