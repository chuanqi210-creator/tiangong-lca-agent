"""Validate and replay a content-addressed native model execution package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from pathlib import PurePosixPath
from typing import Dict, List, Mapping, Optional, Sequence

try:
    from .model_execution_package import (
        _exchange_semantics,
        _process_identity,
        _reference_semantics,
    )
except ImportError:  # pragma: no cover - direct script execution by activity harness
    from model_execution_package import (  # type: ignore
        _exchange_semantics,
        _process_identity,
        _reference_semantics,
    )


JsonObject = Dict[str, object]
REPLAY_CONTRACT = "lca_research_kernel.native_route_model_execution_replay/v1"
REPLAY_LOGIC_VERSION = "2026-07-17.2"
PACKAGE_CONTRACT = "lca_research_kernel.native_route_model_execution_package/v1"
TIANGONG_PACKAGE = "@tiangong-lca/cli@0.0.25"
EXPECTED_ROUTES = ("hydrothermal", "solid-state")


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _array(value: object, label: str) -> List[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _digest(value: object, label: str) -> str:
    digest = _text(value, label)
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return digest


def _safe_path(value: object, label: str) -> str:
    path = _text(value, label)
    parsed = PurePosixPath(path)
    if (
        parsed.is_absolute()
        or path != parsed.as_posix()
        or not parsed.parts
        or any(part in ("", ".", "..") for part in parsed.parts)
        or "\\" in path
    ):
        raise ValueError(f"{label} must be a safe package-relative path")
    return path


def _safe_segment(value: object, label: str) -> str:
    segment = _text(value, label)
    if PurePosixPath(segment).name != segment or segment in (".", "..") or "\\" in segment:
        raise ValueError(f"{label} must be a safe path segment")
    return segment


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


def translate_replay_command(
    accepted_command: Sequence[str], direct_runtime_prefix: Sequence[str]
) -> List[str]:
    """Replace only the already validated npm prefix with a direct runtime entry."""

    command = list(accepted_command)
    original_prefix = _native_prefix()
    replacement = list(direct_runtime_prefix)
    if command[: len(original_prefix)] != original_prefix or len(command) <= len(
        original_prefix
    ):
        raise ValueError("replay command does not contain the accepted pinned npm prefix")
    if (
        len(replacement) != 2
        or any(not isinstance(part, str) or not Path(part).is_absolute() for part in replacement)
        or any(part in ("publish", "lcia", "save-draft") for part in command[len(original_prefix) :])
    ):
        raise ValueError("direct runtime prefix or accepted command is unsafe")
    return [*replacement, *command[len(original_prefix) :]]


def _expected_steps(routes: Mapping[str, JsonObject], final_sha256: str) -> List[JsonObject]:
    steps: List[JsonObject] = []
    for route_id in EXPECTED_ROUTES:
        route = routes[route_id]
        steps.extend(
            [
                {
                    "step_id": f"{route_id}.build-resulting-process",
                    "kind": "native_command",
                    "depends_on": [],
                    "command": [
                        *_native_prefix(),
                        "lifecyclemodel",
                        "build-resulting-process",
                        "--input",
                        f"routes/{route_id}/projection-request.json",
                        "--out-dir",
                        f"replay/routes/{route_id}/native-projection",
                        "--json",
                    ],
                },
                {
                    "step_id": f"{route_id}.verify-raw-projection",
                    "kind": "kernel_semantic_verification",
                    "depends_on": [f"{route_id}.build-resulting-process"],
                    "expected_raw_sha256": route["raw_resulting_process_sha256"],
                    "expected_projection_signature": route["projection_signature"],
                },
            ]
        )
    steps.extend(
        [
            {
                "step_id": "combine.raw-resulting-processes",
                "kind": "kernel_projection_extract",
                "depends_on": [
                    "hydrothermal.verify-raw-projection",
                    "solid-state.verify-raw-projection",
                ],
                "output": "replay/raw-resulting-processes.jsonl",
            },
            {
                "step_id": "normalize.resulting-processes",
                "kind": "native_command",
                "depends_on": ["combine.raw-resulting-processes"],
                "command": [
                    *_native_prefix(),
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
                "expected_sha256": final_sha256,
            },
        ]
    )
    return steps


def _validate_dag(steps: Sequence[JsonObject]) -> None:
    step_ids = [_text(step.get("step_id"), "replay step ID") for step in steps]
    if len(step_ids) != len(set(step_ids)):
        raise ValueError("replay DAG contains duplicate step IDs")
    known = set(step_ids)
    visited = set()
    visiting = set()
    by_id = {str(step["step_id"]): step for step in steps}

    def visit(step_id: str) -> None:
        if step_id in visiting:
            raise ValueError("replay DAG dependency cycle detected")
        if step_id in visited:
            return
        visiting.add(step_id)
        dependencies = _array(by_id[step_id].get("depends_on"), f"{step_id} dependencies")
        if len(dependencies) != len(set(map(str, dependencies))):
            raise ValueError(f"{step_id} has duplicate dependencies")
        for value in dependencies:
            dependency = _text(value, f"{step_id} dependency")
            if dependency not in known:
                raise ValueError(f"{step_id} has an unknown replay DAG dependency")
            visit(dependency)
        visiting.remove(step_id)
        visited.add(step_id)

    for step_id in step_ids:
        visit(step_id)


def _verify_package_files(
    manifest: JsonObject, package_files: Mapping[str, bytes]
) -> Dict[str, JsonObject]:
    raw_bindings = _object(manifest.get("file_bindings"), "package file bindings")
    if manifest.get("file_count") != 20 or len(raw_bindings) != 20:
        raise ValueError("the replay package must bind exactly 20 files")
    if set(raw_bindings) != set(package_files):
        raise ValueError("package file set drifted from its 20-file manifest")
    bindings: Dict[str, JsonObject] = {}
    for raw_path, raw_binding in sorted(raw_bindings.items()):
        path = _safe_path(raw_path, "package file path")
        binding = _object(raw_binding, f"binding for {path}")
        if _safe_path(binding.get("path"), f"binding path for {path}") != path:
            raise ValueError(f"binding path drifted for {path}")
        digest = _digest(binding.get("sha256"), f"binding hash for {path}")
        size = binding.get("bytes")
        content = package_files[path]
        if not isinstance(content, bytes):
            raise ValueError(f"package file {path} must be bytes")
        if not isinstance(size, int) or size != len(content):
            raise ValueError(f"package file size drifted for {path}")
        if digest != _sha256(content):
            raise ValueError(f"package file hash drifted for {path}")
        bindings[path] = {
            "path": path,
            "sha256": digest,
            "bytes": size,
            "kind": binding.get("kind"),
        }
    return bindings


def compile_model_package_replay(
    manifest_bytes: bytes,
    manifest_binding: JsonObject,
    package_files: Mapping[str, bytes],
) -> JsonObject:
    """Fail closed unless the input is the exact accepted seven-step package."""

    bound = _object(manifest_binding, "package manifest binding")
    manifest_path = _safe_path(bound.get("path"), "package manifest path")
    manifest_sha256 = _digest(bound.get("sha256"), "package manifest hash")
    if _sha256(manifest_bytes) != manifest_sha256:
        raise ValueError("package manifest binding hash drifted")
    try:
        manifest = _object(json.loads(manifest_bytes), "package manifest")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("package manifest is not valid JSON") from error

    zero_effect_fields = (
        "network_read_executed_count",
        "remote_write_executed_count",
        "publication_executed_count",
        "comparison_executed_count",
        "lcia_executed_count",
        "replay_executed_count",
    )
    false_policy_fields = (
        "network_read_allowed",
        "remote_write_allowed",
        "publication_allowed",
        "comparison_allowed",
        "lcia_allowed",
        "scientific_comparison_ready",
        "comparative_lcia_allowed",
    )
    if (
        manifest.get("contract") != PACKAGE_CONTRACT
        or manifest.get("status") != "native_route_model_execution_package_verified"
        or manifest.get("tool_identity") != TIANGONG_PACKAGE
        or manifest.get("route_count") != 2
        or manifest.get("dataset_count") != 6
        or manifest.get("blocked_check_count") != 9
        or any(manifest.get(field) != 0 for field in zero_effect_fields)
        or any(manifest.get(field) is not False for field in false_policy_fields)
    ):
        raise ValueError("an accepted, zero-effect native model package is required")
    package_version = _text(manifest.get("package_version"), "package version")

    bindings = _verify_package_files(manifest, package_files)
    raw_routes = _array(manifest.get("routes"), "package routes")
    routes: Dict[str, JsonObject] = {}
    for raw_route in raw_routes:
        route = _object(raw_route, "package route")
        route_id = _safe_segment(route.get("route_id"), "route ID")
        if route_id in routes:
            raise ValueError("package route IDs must be unique")
        for key in (
            "source_process_id",
            "source_process_version",
            "model_id",
            "model_version",
            "resulting_process_id",
            "resulting_process_version",
        ):
            _safe_segment(route.get(key), f"{route_id} {key}")
        for key in (
            "raw_resulting_process_sha256",
            "resulting_process_sha256",
        ):
            _digest(route.get(key), f"{route_id} {key}")
        signature = _text(route.get("projection_signature"), f"{route_id} signature")
        if not signature.startswith("sha256:"):
            raise ValueError(f"{route_id} projection signature is not SHA-256 bound")
        routes[route_id] = dict(route)
    if tuple(sorted(routes)) != tuple(sorted(EXPECTED_ROUTES)):
        raise ValueError("the exact hydrothermal and solid-state routes are required")

    expected_paths = {
        "datasets/current-processes.jsonl",
        "datasets/lifecyclemodels.jsonl",
        "datasets/resulting-processes.jsonl",
        "datasets/all-processes.jsonl",
        "normalization/resulting-process-normalization-patch.json",
        "policy/comparison-blockers.json",
        "lineage/resulting-process-relations.jsonl",
        "replay/replay-plan.json",
    }
    for route_id, route in routes.items():
        source_id = str(route["source_process_id"])
        source_version = str(route["source_process_version"])
        expected_paths.update(
            {
                f"routes/{route_id}/projection-request.json",
                f"routes/{route_id}/inputs/lifecyclemodel.json",
                f"routes/{route_id}/inputs/processes/{source_id}_{source_version}.json",
                f"routes/{route_id}/expected/raw-resulting-process.json",
                f"routes/{route_id}/expected/resulting-process.json",
                f"routes/{route_id}/relation.json",
            }
        )
        raw_path = f"routes/{route_id}/expected/raw-resulting-process.json"
        normalized_path = f"routes/{route_id}/expected/resulting-process.json"
        if bindings.get(raw_path, {}).get("sha256") != route["raw_resulting_process_sha256"]:
            raise ValueError(f"{route_id} raw expected hash drifted from its route")
        if bindings.get(normalized_path, {}).get("sha256") != route["resulting_process_sha256"]:
            raise ValueError(f"{route_id} normalized expected hash drifted from its route")
    if set(bindings) != expected_paths:
        raise ValueError("package file paths drifted from the exact replay closure")

    try:
        replay_plan = _object(
            json.loads(package_files["replay/replay-plan.json"]), "replay plan"
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("replay plan is not valid JSON") from error
    if replay_plan != manifest.get("replay_plan"):
        raise ValueError("replay plan drifted from the package manifest")
    if (
        replay_plan.get("contract") != PACKAGE_CONTRACT
        or replay_plan.get("tool_identity") != TIANGONG_PACKAGE
        or replay_plan.get("step_count") != 7
        or any(replay_plan.get(field) is not False for field in false_policy_fields[:5])
    ):
        raise ValueError("the accepted seven-step offline replay plan is required")
    raw_steps = _array(replay_plan.get("steps"), "replay steps")
    if len(raw_steps) != 7:
        raise ValueError("the replay DAG must contain exactly seven steps")
    steps = [_object(step, "replay step") for step in raw_steps]
    _validate_dag(steps)
    expected_steps = _expected_steps(
        routes, str(bindings["datasets/resulting-processes.jsonl"]["sha256"])
    )
    if steps != expected_steps:
        raise ValueError("replay DAG command, path, dependency, or expected product drifted")

    tree_rows = [
        {
            "path": path,
            "sha256": binding["sha256"],
            "bytes": binding["bytes"],
        }
        for path, binding in sorted(bindings.items())
    ]
    input_tree_sha256 = _sha256(_json_bytes(tree_rows))
    replay_version = _sha256(
        _json_bytes(
            {
                "contract": REPLAY_CONTRACT,
                "manifest_sha256": manifest_sha256,
                "package_version": package_version,
                "replay_plan_sha256": bindings["replay/replay-plan.json"]["sha256"],
                "input_tree_sha256": input_tree_sha256,
            }
        )
    )[:16]
    return {
        "schema_version": 1,
        "contract": REPLAY_CONTRACT,
        "status": "native_route_model_execution_replay_compiled",
        "replay_logic_version": REPLAY_LOGIC_VERSION,
        "replay_version": replay_version,
        "package_version": package_version,
        "tool_identity": TIANGONG_PACKAGE,
        "manifest_binding": {"path": manifest_path, "sha256": manifest_sha256},
        "input_tree_sha256": input_tree_sha256,
        "replay_plan_sha256": bindings["replay/replay-plan.json"]["sha256"],
        "file_count": 20,
        "route_count": 2,
        "step_count": 7,
        "blocked_check_count": 9,
        "preserved_scientific_gap_count": manifest.get(
            "preserved_scientific_gap_count"
        ),
        "preserved_unresolved_field_count": manifest.get(
            "preserved_unresolved_field_count"
        ),
        "preserved_unresolved_claim_count": manifest.get(
            "preserved_unresolved_claim_count"
        ),
        "preserved_prohibited_claim_count": manifest.get(
            "preserved_prohibited_claim_count"
        ),
        "routes": [routes[route_id] for route_id in EXPECTED_ROUTES],
        "steps": steps,
        "network_read_allowed": False,
        "remote_write_allowed": False,
        "publication_allowed": False,
        "comparison_allowed": False,
        "lcia_allowed": False,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
    }


def verify_replayed_route(
    bundle: JsonObject,
    expected_raw: JsonObject,
    expected_relation: JsonObject,
    route: JsonObject,
) -> JsonObject:
    """Verify one native projection before it can enter the replay product."""

    route_id = _text(route.get("route_id"), "route ID")
    model_id = _text(route.get("model_id"), f"{route_id} model ID")
    model_version = _text(route.get("model_version"), f"{route_id} model version")
    result_id = _text(
        route.get("resulting_process_id"), f"{route_id} resulting Process ID"
    )
    result_version = _text(
        route.get("resulting_process_version"),
        f"{route_id} resulting Process version",
    )
    expected_signature = _text(
        route.get("projection_signature"), f"{route_id} projection signature"
    )
    expected_raw_sha256 = _digest(
        route.get("raw_resulting_process_sha256"), f"{route_id} raw hash"
    )

    source_model = _object(bundle.get("source_model"), "native source model")
    if (source_model.get("id"), source_model.get("version")) != (
        model_id,
        model_version,
    ):
        raise ValueError(f"{route_id} native source model identity drifted")
    resolution = _object(
        source_model.get("resolved_process_summary"),
        f"{route_id} resolved Process summary",
    )
    resolution_items = [
        _object(item, f"{route_id} resolved Process")
        for item in _array(
            resolution.get("items"), f"{route_id} resolved Process items"
        )
    ]
    if (
        resolution.get("required_process_count") != 1
        or resolution.get("resolved_process_count") != 1
        or resolution.get("local_process_dir_count") != 1
        or resolution.get("explicit_process_file_count") != 1
        or resolution.get("remote_resolution_count") != 0
        or len(resolution_items) != 1
        or resolution_items[0].get("process_id") != route.get("source_process_id")
        or resolution_items[0].get("version") != route.get("source_process_version")
        or resolution_items[0].get("resolution") != "local_file"
    ):
        raise ValueError(f"{route_id} native Process resolution identity drifted")

    projections = [
        _object(value, f"{route_id} projected Process")
        for value in _array(bundle.get("projected_processes"), "projected Processes")
    ]
    primary = [value for value in projections if value.get("role") == "primary"]
    if len(projections) != 1 or len(primary) != 1:
        raise ValueError(f"{route_id} must contain one sole primary projected Process")
    projected = primary[0]
    if (projected.get("id"), projected.get("version")) != (
        result_id,
        result_version,
    ):
        raise ValueError(f"{route_id} projected Process identity drifted")
    raw_payload = _object(projected.get("json_ordered"), f"{route_id} raw projection")
    if raw_payload != expected_raw:
        raise ValueError(f"{route_id} raw projection content drifted")
    if _sha256(_json_bytes(raw_payload)) != expected_raw_sha256:
        raise ValueError(f"{route_id} raw projection hash drifted")
    if _process_identity(raw_payload, f"{route_id} raw projection") != (
        result_id,
        result_version,
    ):
        raise ValueError(f"{route_id} raw projection semantic identity drifted")
    if _exchange_semantics(
        raw_payload, f"{route_id} raw projection"
    ) != _exchange_semantics(expected_raw, f"{route_id} expected raw projection"):
        raise ValueError(f"{route_id} raw exchange semantics drifted")
    if _reference_semantics(
        raw_payload, f"{route_id} raw projection"
    ) != _reference_semantics(expected_raw, f"{route_id} expected raw projection"):
        raise ValueError(f"{route_id} raw reference-flow semantics drifted")

    relations = [
        _object(value, f"{route_id} relation")
        for value in _array(bundle.get("relations"), "projection relations")
    ]
    if len(relations) != 1 or relations[0] != expected_relation:
        raise ValueError(f"{route_id} projection relation drifted")
    relation = relations[0]
    if (
        relation.get("lifecyclemodel_id") != model_id
        or relation.get("lifecyclemodel_version") != model_version
        or relation.get("resulting_process_id") != result_id
        or relation.get("resulting_process_version") != result_version
        or relation.get("projection_role") != "primary"
        or relation.get("is_primary") is not True
        or relation.get("projection_signature") != expected_signature
    ):
        raise ValueError(f"{route_id} relation identity or signature drifted")

    return {
        "route_id": route_id,
        "raw_resulting_process_sha256": expected_raw_sha256,
        "projection_signature": expected_signature,
        "exchange_count": len(_exchange_semantics(raw_payload, route_id)),
        "remote_resolution_count": 0,
        "semantic_equivalence_verified": True,
        "raw_payload": raw_payload,
    }


def _jsonl_rows(content: bytes, label: str) -> List[JsonObject]:
    rows: List[JsonObject] = []
    try:
        text = content.decode("utf-8")
        for index, line in enumerate(text.splitlines()):
            if not line.strip():
                raise ValueError(f"{label} contains an empty row")
            rows.append(_object(json.loads(line), f"{label} row {index}"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSONL") from error
    return rows


def verify_replay_normalization(
    normalized_rows: bytes,
    expected_rows: bytes,
    routes: Sequence[JsonObject],
    patch_report: JsonObject,
    patch_evidence: bytes,
) -> JsonObject:
    """Verify exact normalized products and lossless native patch evidence."""

    if normalized_rows != expected_rows:
        raise ValueError("normalized replay content or hash drifted")
    actual = _jsonl_rows(normalized_rows, "normalized replay rows")
    expected = _jsonl_rows(expected_rows, "expected normalized rows")
    if len(actual) != 2 or len(routes) != 2 or len(expected) != 2:
        raise ValueError("normalized replay must contain exactly two route rows")
    route_summaries = []
    for index, route in enumerate(routes):
        route_id = _text(route.get("route_id"), "normalized route ID")
        expected_identity = (
            _text(route.get("resulting_process_id"), f"{route_id} result ID"),
            _text(route.get("resulting_process_version"), f"{route_id} result version"),
        )
        if _process_identity(actual[index], f"{route_id} normalized replay") != expected_identity:
            raise ValueError(f"{route_id} normalized identity drifted")
        row_sha256 = _sha256(_json_bytes(actual[index]))
        if row_sha256 != _digest(
            route.get("resulting_process_sha256"), f"{route_id} normalized hash"
        ):
            raise ValueError(f"{route_id} normalized route hash drifted")
        if _exchange_semantics(
            actual[index], f"{route_id} normalized replay"
        ) != _exchange_semantics(expected[index], f"{route_id} expected normalized"):
            raise ValueError(f"{route_id} normalized exchange semantics drifted")
        if _reference_semantics(
            actual[index], f"{route_id} normalized replay"
        ) != _reference_semantics(expected[index], f"{route_id} expected normalized"):
            raise ValueError(f"{route_id} normalized reference-flow semantics drifted")
        route_summaries.append(
            {
                "route_id": route_id,
                "resulting_process_sha256": row_sha256,
                "semantic_equivalence_verified": True,
            }
        )

    if (
        patch_report.get("status") != "completed"
        or patch_report.get("row_count") != 2
        or patch_report.get("patch_count") != 2
        or patch_report.get("operation_count") != 60
        or patch_report.get("applied_operation_count") != 28
        or patch_report.get("evidence_count") != 28
        or patch_report.get("blockers") != []
    ):
        raise ValueError("native patch report or evidence counts drifted")
    evidence_rows = _jsonl_rows(patch_evidence, "native patch evidence")
    routes_by_id = {
        str(route.get("route_id")): route for route in routes if isinstance(route, dict)
    }
    expected_evidence_counts = {"hydrothermal": 16, "solid-state": 12}
    actual_evidence_counts = {route_id: 0 for route_id in expected_evidence_counts}
    evidence_keys = set()
    evidence_identity_valid = True
    for row in evidence_rows:
        evidence = _object(row.get("evidence"), "patch evidence source")
        route_id = str(evidence.get("route_id", ""))
        route = routes_by_id.get(route_id)
        key = (
            row.get("row_index"),
            row.get("patch_index"),
            row.get("operation_index"),
            row.get("path"),
        )
        if key in evidence_keys:
            evidence_identity_valid = False
        evidence_keys.add(key)
        if route_id in actual_evidence_counts:
            actual_evidence_counts[route_id] += 1
        expected_row_index = (
            EXPECTED_ROUTES.index(route_id) if route_id in EXPECTED_ROUTES else None
        )
        if (
            route is None
            or row.get("row_index") != expected_row_index
            or row.get("patch_index") != expected_row_index
            or row.get("dataset_id") != route.get("resulting_process_id")
            or row.get("dataset_version") != route.get("resulting_process_version")
            or evidence.get("projection_signature")
            != route.get("projection_signature")
        ):
            evidence_identity_valid = False
    if (
        len(evidence_rows) != 28
        or any(row.get("op") != "replace" for row in evidence_rows)
        or any(
            _object(row.get("resolution"), "patch evidence resolution").get(
                "scientific_value_changed"
            )
            is not False
            for row in evidence_rows
        )
        or actual_evidence_counts != expected_evidence_counts
        or not evidence_identity_valid
    ):
        raise ValueError(
            "patch evidence route/signature identity is incomplete or changes scientific values"
        )
    return {
        "normalized_rows_sha256": _sha256(normalized_rows),
        "normalized_row_count": 2,
        "patch_operation_count": 60,
        "patch_applied_operation_count": 28,
        "patch_evidence_count": 28,
        "patch_blocker_count": 0,
        "scientific_value_change_count": 0,
        "semantic_equivalence_verified": True,
        "routes": route_summaries,
    }


def _artifact_binding(path: Path, root: Path) -> JsonObject:
    content = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(content),
        "bytes": len(content),
    }


def _load_package(
    manifest_path: Path, manifest_binding: JsonObject, package_root: Path
) -> tuple[bytes, Dict[str, bytes], JsonObject]:
    manifest_bytes = manifest_path.read_bytes()
    try:
        untrusted = _object(json.loads(manifest_bytes), "package manifest")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("package manifest is not valid JSON") from error
    raw_bindings = _object(untrusted.get("file_bindings"), "package file bindings")
    package_files: Dict[str, bytes] = {}
    root = package_root.resolve()
    for raw_path in raw_bindings:
        relative = _safe_path(raw_path, "package file path")
        candidate = (root / relative).resolve()
        if root not in candidate.parents:
            raise ValueError(f"package path escapes its root: {relative}")
        if not candidate.is_file():
            raise ValueError(f"package file is missing: {relative}")
        package_files[relative] = candidate.read_bytes()
    compiled = compile_model_package_replay(
        manifest_bytes, manifest_binding, package_files
    )
    return manifest_bytes, package_files, compiled


def _source_snapshot(
    manifest_bytes: bytes, package_files: Mapping[str, bytes]
) -> JsonObject:
    rows = [
        {"path": "route-model-execution-package.json", "sha256": _sha256(manifest_bytes)}
    ] + [
        {"path": path, "sha256": _sha256(content), "bytes": len(content)}
        for path, content in sorted(package_files.items())
    ]
    return {
        "file_count": len(package_files),
        "manifest_sha256": _sha256(manifest_bytes),
        "tree_sha256": _sha256(_json_bytes(rows)),
        "files": rows,
    }


def verify_source_package_unchanged(before: JsonObject, after: JsonObject) -> bool:
    """Fail closed when any manifest or package input changed during replay."""

    if before != after:
        raise ValueError("the accepted source package changed during replay")
    return True


def _runtime_version(command: Sequence[str]) -> Optional[str]:
    try:
        completed = subprocess.run(
            list(command),
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.strip() or completed.stderr.strip() or None


def _run_native_command(
    command: Sequence[str], package_dir: Path, trace_dir: Path, step_id: str
) -> JsonObject:
    env = os.environ.copy()
    env["npm_config_offline"] = "true"
    env["npm_config_audit"] = "false"
    env["npm_config_fund"] = "false"
    completed = subprocess.run(
        list(command),
        cwd=package_dir,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    trace_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = trace_dir / f"{step_id}.stdout.txt"
    stderr_path = trace_dir / f"{step_id}.stderr.txt"
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(
            f"native replay step {step_id} failed with exit code {completed.returncode}; "
            f"see {stderr_path}"
        )
    return {
        "command_sha256": _sha256(_json_bytes(list(command))),
        "exit_code": completed.returncode,
        "stdout": _artifact_binding(stdout_path, trace_dir.parent.parent),
        "stderr": _artifact_binding(stderr_path, trace_dir.parent.parent),
    }


def execute_model_package_replay(
    manifest_path: Path,
    manifest_binding: JsonObject,
    package_root: Path,
    out_dir: Path,
    native_command_prefix: Optional[Sequence[str]] = None,
    runtime_closure: Optional[JsonObject] = None,
) -> JsonObject:
    """Execute the validated replay DAG in a copied filesystem tree."""

    manifest_path = Path(manifest_path)
    package_root = Path(package_root)
    out_dir = Path(out_dir)
    manifest_bytes, package_files, compiled = _load_package(
        manifest_path, manifest_binding, package_root
    )
    source_before = _source_snapshot(manifest_bytes, package_files)

    if out_dir.exists() and any(
        child.name != "inputs" or not child.is_dir() for child in out_dir.iterdir()
    ):
        raise ValueError("replay output directory may contain only registered inputs")
    sandbox_package = out_dir / "sandbox" / "package"
    trace_dir = out_dir / "trace" / "native"
    outputs_dir = out_dir / "outputs"
    sandbox_package.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)
    for relative, content in sorted(package_files.items()):
        target = sandbox_package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    copied_files = {
        path: (sandbox_package / path).read_bytes() for path in package_files
    }
    sandbox_snapshot = _source_snapshot(manifest_bytes, copied_files)
    if sandbox_snapshot["tree_sha256"] != source_before["tree_sha256"]:
        raise ValueError("sandbox package copy drifted before replay")

    routes = {
        str(route["route_id"]): route
        for route in compiled["routes"]
        if isinstance(route, dict)
    }
    trace: List[JsonObject] = []
    raw_payloads: Dict[str, JsonObject] = {}
    for route_id in EXPECTED_ROUTES:
        build_step_id = f"{route_id}.build-resulting-process"
        build_step = next(
            step for step in compiled["steps"] if step["step_id"] == build_step_id
        )
        accepted_command = build_step["command"]
        executed_command = (
            translate_replay_command(accepted_command, native_command_prefix)
            if native_command_prefix is not None
            else accepted_command
        )
        command_result = _run_native_command(
            executed_command, sandbox_package, trace_dir, build_step_id
        )
        bundle_path = (
            sandbox_package
            / "replay"
            / "routes"
            / route_id
            / "native-projection"
            / "process-projection-bundle.json"
        )
        if not bundle_path.is_file():
            raise ValueError(f"{route_id} native replay bundle is missing")
        trace.append(
            {
                "step_id": build_step_id,
                "kind": "native_command",
                "depends_on": [],
                "status": "completed",
                **command_result,
                **(
                    {
                        "accepted_command_sha256": _sha256(
                            _json_bytes(accepted_command)
                        ),
                        "runtime_command_translated": True,
                    }
                    if native_command_prefix is not None
                    else {}
                ),
                "outputs": [_artifact_binding(bundle_path, out_dir)],
            }
        )

        verify_step_id = f"{route_id}.verify-raw-projection"
        try:
            bundle = _object(json.loads(bundle_path.read_bytes()), f"{route_id} bundle")
            expected_raw_path = (
                sandbox_package
                / "routes"
                / route_id
                / "expected"
                / "raw-resulting-process.json"
            )
            expected_relation_path = (
                sandbox_package / "routes" / route_id / "relation.json"
            )
            expected_raw = _object(
                json.loads(expected_raw_path.read_bytes()), f"{route_id} expected raw"
            )
            expected_relation = _object(
                json.loads(expected_relation_path.read_bytes()),
                f"{route_id} expected relation",
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"{route_id} replay product is invalid JSON") from error
        route_result = verify_replayed_route(
            bundle, expected_raw, expected_relation, routes[route_id]
        )
        replay_source_model = _object(
            bundle.get("source_model"), f"{route_id} source model"
        )
        replay_resolution = _object(
            replay_source_model.get("resolved_process_summary"),
            f"{route_id} resolved Process summary",
        )
        replay_resolution_items = _array(
            replay_resolution.get("items"), f"{route_id} resolved items"
        )
        if len(replay_resolution_items) != 1:
            raise ValueError(f"{route_id} must resolve exactly one local Process")
        resolution_item = _object(
            replay_resolution_items[0], f"{route_id} resolved item"
        )
        resolved_source_path = Path(
            _text(resolution_item.get("source_path"), f"{route_id} resolved source path")
        ).resolve()
        try:
            resolved_source_path.relative_to(sandbox_package.resolve())
        except ValueError as error:
            raise ValueError(
                f"{route_id} native resolved source escaped the replay sandbox"
            ) from error
        raw_payload = _object(route_result.pop("raw_payload"), f"{route_id} raw payload")
        raw_payloads[route_id] = raw_payload
        extracted_path = (
            sandbox_package / "replay" / "routes" / route_id / "projected-process.raw.json"
        )
        extracted_path.write_bytes(_json_bytes(raw_payload))
        trace.append(
            {
                "step_id": verify_step_id,
                "kind": "kernel_semantic_verification",
                "depends_on": [build_step_id],
                "status": "completed",
                **route_result,
                "outputs": [_artifact_binding(extracted_path, out_dir)],
            }
        )

    raw_rows_path = sandbox_package / "replay" / "raw-resulting-processes.jsonl"
    raw_rows_path.write_bytes(
        b"".join(_json_bytes(raw_payloads[route_id]) for route_id in EXPECTED_ROUTES)
    )
    trace.append(
        {
            "step_id": "combine.raw-resulting-processes",
            "kind": "kernel_projection_extract",
            "depends_on": [
                "hydrothermal.verify-raw-projection",
                "solid-state.verify-raw-projection",
            ],
            "status": "completed",
            "outputs": [_artifact_binding(raw_rows_path, out_dir)],
        }
    )

    normalize_step = next(
        step
        for step in compiled["steps"]
        if step["step_id"] == "normalize.resulting-processes"
    )
    accepted_normalize_command = normalize_step["command"]
    executed_normalize_command = (
        translate_replay_command(accepted_normalize_command, native_command_prefix)
        if native_command_prefix is not None
        else accepted_normalize_command
    )
    normalize_result = _run_native_command(
        executed_normalize_command,
        sandbox_package,
        trace_dir,
        "normalize.resulting-processes",
    )
    normalized_path = sandbox_package / "replay" / "resulting-processes.jsonl"
    patch_report_path = (
        sandbox_package
        / "replay"
        / "native-normalization"
        / "outputs"
        / "dataset-patch-apply-report.json"
    )
    patch_evidence_path = patch_report_path.parent / "patch-evidence.jsonl"
    for required in (normalized_path, patch_report_path, patch_evidence_path):
        if not required.is_file():
            raise ValueError(f"native normalization product is missing: {required.name}")
    trace.append(
        {
            "step_id": "normalize.resulting-processes",
            "kind": "native_command",
            "depends_on": ["combine.raw-resulting-processes"],
            "status": "completed",
            **normalize_result,
            **(
                {
                    "accepted_command_sha256": _sha256(
                        _json_bytes(accepted_normalize_command)
                    ),
                    "runtime_command_translated": True,
                }
                if native_command_prefix is not None
                else {}
            ),
            "outputs": [
                _artifact_binding(path, out_dir)
                for path in (normalized_path, patch_report_path, patch_evidence_path)
            ],
        }
    )

    expected_normalized = package_files["datasets/resulting-processes.jsonl"]
    try:
        patch_report = _object(
            json.loads(patch_report_path.read_bytes()), "native patch report"
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("native patch report is invalid JSON") from error
    normalized_result = verify_replay_normalization(
        normalized_path.read_bytes(),
        expected_normalized,
        [routes[route_id] for route_id in EXPECTED_ROUTES],
        patch_report,
        patch_evidence_path.read_bytes(),
    )
    trace.append(
        {
            "step_id": "verify.normalized-resulting-processes",
            "kind": "kernel_semantic_verification",
            "depends_on": ["normalize.resulting-processes"],
            "status": "completed",
            **normalized_result,
            "outputs": [_artifact_binding(normalized_path, out_dir)],
        }
    )
    if len(trace) != 7 or [row["step_id"] for row in trace] != [
        step["step_id"] for step in compiled["steps"]
    ]:
        raise ValueError("executed replay trace drifted from the accepted seven-step DAG")

    manifest_after, files_after, _ = _load_package(
        manifest_path, manifest_binding, package_root
    )
    source_after = _source_snapshot(manifest_after, files_after)
    source_unchanged = verify_source_package_unchanged(source_before, source_after)

    trace_path = outputs_dir / "native-route-model-execution-replay-trace.json"
    trace_path.write_bytes(_json_bytes({"step_count": 7, "steps": trace}))
    if native_command_prefix is None:
        runtime = {
            "python_version": sys.version.split()[0],
            "python_executable": sys.executable,
            "node_version": _runtime_version(["node", "--version"]),
            "npm_version": _runtime_version(["npm", "--version"]),
            "node_executable": shutil.which("node"),
            "npm_executable": shutil.which("npm"),
            "tiangong_package": TIANGONG_PACKAGE,
            "npm_offline_enforced": True,
            "tool_runtime_embedded": False,
        }
    else:
        closure = _object(runtime_closure, "portable runtime closure")
        if (
            closure.get("contract")
            != "lca_research_kernel.portable_native_tool_runtime_closure/v1"
            or closure.get("runtime_content_closed") is not True
            or closure.get("system_runtime_closed") is not False
            or closure.get("cross_platform_portability_proven") is not False
        ):
            raise ValueError("a verified portable runtime closure is required")
        runtime = {
            "python_version": sys.version.split()[0],
            "python_executable": sys.executable,
            "node_version": _runtime_version(
                [str(native_command_prefix[0]), "--version"]
            ),
            "npm_version": None,
            "node_executable": str(native_command_prefix[0]),
            "npm_executable": None,
            "tiangong_package": TIANGONG_PACKAGE,
            "tiangong_cli_version": _runtime_version(
                [*map(str, native_command_prefix), "--version"]
            ),
            "closure_version": closure.get("closure_version"),
            "npm_invoked": False,
            "npm_offline_enforced": False,
            "network_resolution_disabled_by_direct_entry": True,
            "tool_runtime_embedded": True,
            "runtime_content_closed": True,
            "same_platform_relocation_proven": True,
        }
        if (
            runtime["node_version"] != "v24.18.0"
            or runtime["tiangong_cli_version"] != "0.0.25"
        ):
            raise ValueError("relocated Node or TianGong runtime identity drifted")
    normalized_routes = {
        str(route["route_id"]): route
        for route in normalized_result["routes"]
        if isinstance(route, dict)
    }
    report_routes = [
        {
            "route_id": route_id,
            "source_process_id": routes[route_id]["source_process_id"],
            "model_id": routes[route_id]["model_id"],
            "resulting_process_id": routes[route_id]["resulting_process_id"],
            "raw_resulting_process_sha256": routes[route_id][
                "raw_resulting_process_sha256"
            ],
            "resulting_process_sha256": normalized_routes[route_id][
                "resulting_process_sha256"
            ],
            "projection_signature": routes[route_id]["projection_signature"],
            "semantic_equivalence_verified": True,
        }
        for route_id in EXPECTED_ROUTES
    ]
    report: JsonObject = {
        "schema_version": 1,
        "contract": REPLAY_CONTRACT,
        "status": "native_route_model_execution_replay_completed",
        "replay_logic_version": REPLAY_LOGIC_VERSION,
        "replay_version": compiled["replay_version"],
        "package_version": compiled["package_version"],
        "tool_identity": TIANGONG_PACKAGE,
        "manifest_binding": compiled["manifest_binding"],
        "input_tree_sha256": compiled["input_tree_sha256"],
        "source_package_unchanged": source_unchanged,
        "source_tree_sha256_before": source_before["tree_sha256"],
        "source_tree_sha256_after": source_after["tree_sha256"],
        "sandbox_input_tree_sha256": sandbox_snapshot["tree_sha256"],
        "file_count": 20,
        "route_count": 2,
        "blocked_check_count": 9,
        "preserved_scientific_gap_count": compiled.get(
            "preserved_scientific_gap_count"
        ),
        "preserved_unresolved_field_count": compiled.get(
            "preserved_unresolved_field_count"
        ),
        "preserved_unresolved_claim_count": compiled.get(
            "preserved_unresolved_claim_count"
        ),
        "preserved_prohibited_claim_count": compiled.get(
            "preserved_prohibited_claim_count"
        ),
        "step_count": 7,
        "completed_step_count": 7,
        "raw_resulting_process_count": 2,
        "normalized_resulting_process_count": 2,
        "raw_resulting_processes_sha256": _sha256(raw_rows_path.read_bytes()),
        "resulting_processes_sha256": normalized_result[
            "normalized_rows_sha256"
        ],
        "patch_operation_count": 60,
        "patch_applied_operation_count": 28,
        "patch_evidence_count": 28,
        "patch_blocker_count": 0,
        "scientific_value_change_count": 0,
        "semantic_equivalence_verified": True,
        "routes": report_routes,
        "trace_binding": _artifact_binding(trace_path, out_dir),
        "runtime": runtime,
        "filesystem_isolation": True,
        "os_network_sandbox": False,
        **(
            {
                "runtime_content_closed": True,
                "same_platform_relocation_proven": True,
                "system_runtime_closed": False,
            }
            if native_command_prefix is not None
            else {}
        ),
        "cross_host_portability_proven": False,
        "network_read_allowed": False,
        "network_read_executed_count": 0,
        "remote_resolution_count": 0,
        "remote_write_allowed": False,
        "remote_write_executed_count": 0,
        "publication_allowed": False,
        "publication_executed_count": 0,
        "comparison_allowed": False,
        "comparison_executed_count": 0,
        "lcia_allowed": False,
        "lcia_executed_count": 0,
        "external_action_executed_count": 0,
        "current_model_mutation_executed_count": 0,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "next_action": (
            "Compile the native route-provider network gap map from current exchanges "
            "and lifecyclemodel boundaries."
            if native_command_prefix is not None
            else "Bind the host Node/npm and cached pinned TianGong package closure, "
            "then test the same replay under a portable runtime boundary."
        ),
        "routing": {
            "next_internal_route": (
                "compile-native-route-provider-network-gap-map"
                if native_command_prefix is not None
                else "bind-portable-native-tool-runtime-closure"
            ),
            "pause_internal_work": False,
            "reason": (
                "The content-bound runtime relocates on the current platform; retain "
                "system/cross-platform limits and return to route-network integration."
                if native_command_prefix is not None
                else "Exact local replay passed; bind the host Node/npm and cached pinned "
                "TianGong package closure before claiming cross-host portability."
            ),
        },
    }
    report_path = outputs_dir / "native-route-model-execution-replay.json"
    report_path.write_bytes(_json_bytes(report))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay one accepted native route-model execution package."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-binding", type=Path, required=True)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        binding = _object(
            json.loads(args.manifest_binding.read_bytes()), "manifest binding"
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("manifest binding is not valid JSON") from error
    report = execute_model_package_replay(
        args.manifest, binding, args.package_root, args.out_dir
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
