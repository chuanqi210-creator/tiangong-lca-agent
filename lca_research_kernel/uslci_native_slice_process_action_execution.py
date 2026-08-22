"""Execute the saved five-Process native identity/build-validation action plan."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_native_slice_process_action_execution/v1"
LOGIC_VERSION = "2026-07-17.1"
POLICY_VERSION = "2026-07-17.1"
EXPECTED_ROUTE = "execute-uslci-native-slice-process-action-plan"
NEXT_ROUTE = "execute-uslci-native-slice-preflight-batch-2"
MAX_CONCURRENCY = 4
EXPECTED_INPUT_NAMES = (
    "recompile_report",
    "execution_plan",
    "recompiled_identity_actions",
    "recompiled_build_templates",
    "released_actions",
    "carried_actions",
    "reconciliation",
    "conditions",
    "original_identity_actions",
    "original_build_templates",
    "candidate_catalog",
    "staged_high_voltage_flow",
    "staged_construction_flow",
    "runtime_report",
    "runtime_manifest",
)
REQUIRED_BUILD_FIELDS = [
    "target",
    "identity_decision.decision",
    "unit_of_analysis",
    "name_plan.base_name",
    "target.geography",
    "target.technology_route",
    "quantitative_reference_plan.reference_flow_id",
]
REMOTE_ENV_KEYS = {
    "TIANGONG_LCA_API_KEY_OR_SUPABASE_PUBLISHABLE_KEY",
    "TIANGONG_LCA_SUPABASE_URL",
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
}


def _bytes(value: object, pretty: bool = False) -> bytes:
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


def _sha(content: bytes) -> str:
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
        raise ValueError(f"{label} missing")
    return value.strip()


def _load_json(content: bytes, label: str) -> JsonObject:
    try:
        return _object(json.loads(content), label)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} invalid JSON") from error


def _load_jsonl(
    content: bytes, label: str, allow_empty: bool = False
) -> List[JsonObject]:
    try:
        rows = [
            _object(json.loads(line), label)
            for line in content.decode("utf-8").splitlines()
            if line.strip()
        ]
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} invalid JSONL") from error
    if not rows and not allow_empty:
        raise ValueError(f"{label} empty")
    return rows


def _binding(value: object, content: bytes, label: str) -> JsonObject:
    row = _object(value, f"{label} binding")
    if (
        not isinstance(row.get("path"), str)
        or not row["path"]
        or row.get("sha256") != _sha(content)
        or row.get("bytes") != len(content)
    ):
        raise ValueError(f"{label} binding path, hash or bytes drifted")
    return {"path": row["path"], "sha256": row["sha256"], "bytes": row["bytes"]}


def input_version_for_bindings(bindings: Sequence[JsonObject]) -> str:
    return _sha(
        _bytes(
            {
                "logic": LOGIC_VERSION,
                "policy": POLICY_VERSION,
                "inputs": bindings,
                "maximum_native_concurrency": MAX_CONCURRENCY,
            }
        )
    )[:16]


def _assert_output_binding(
    report: JsonObject, key: str, binding: JsonObject, label: str
) -> None:
    nested = _object(
        _object(report.get("artifact_bindings"), f"{label} artifact bindings").get(key),
        f"{label} {key} binding",
    )
    if nested.get("sha256") != binding["sha256"] or nested.get("bytes") != binding["bytes"]:
        raise ValueError(f"{label} {key} binding drifted")


def _contains_input(report: JsonObject, binding: JsonObject) -> bool:
    return any(
        isinstance(row, dict)
        and row.get("sha256") == binding["sha256"]
        and row.get("bytes") == binding["bytes"]
        for row in report.get("input_bindings", [])
    )


def _action_key(row: JsonObject, kind: str) -> str:
    field = "action_key" if kind == "process_identity_preflight" else "build_plan_action_key"
    return _text(row.get(field), f"{kind} key")


def _canonical_sha(row: JsonObject) -> str:
    return _sha(_bytes(row))


def _normalize(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(
        "".join(character.lower() if character.isalnum() else " " for character in value).split()
    )


def _text_set(value: object) -> set:
    if isinstance(value, str):
        token = _normalize(value)
        return {token} if token else set()
    if isinstance(value, list):
        result = set()
        for row in value:
            result |= _text_set(row)
        return result
    if isinstance(value, dict):
        result = set()
        if isinstance(value.get("#text"), str):
            result |= _text_set(value["#text"])
        return result
    return set()


def _candidate_assertions(
    *, process_index: int, target: JsonObject, candidates: Sequence[JsonObject]
) -> List[JsonObject]:
    target_names = _text_set(target.get("names"))
    target_fields = _object(target.get("fields"), "native target fields")
    assertions = []
    for candidate in candidates:
        fields = _object(candidate.get("fields"), "native candidate fields")
        checks = {
            "name_equivalent": bool(target_names & _text_set(candidate.get("names"))),
            "reference_flow_id_equivalent": bool(
                _text_set(target_fields.get("reference_flow_ids"))
                & _text_set(fields.get("reference_flow_ids"))
            ),
            "reference_flow_name_equivalent": bool(
                _text_set(target_fields.get("reference_flow_names"))
                & _text_set(fields.get("reference_flow_names"))
            ),
            "geography_equivalent": bool(
                _text_set(target_fields.get("geography"))
                & _text_set(fields.get("geography"))
            ),
            "technology_route_equivalent": bool(
                _text_set(target_fields.get("technology_route"))
                & _text_set(fields.get("technology_route"))
            ),
        }
        equivalent = all(checks.values())
        assertions.append(
            {
                "schema_version": 1,
                "process_index": process_index,
                "candidate_index": candidate.get("index"),
                "candidate_id": candidate.get("id"),
                "match_score": candidate.get("match_score"),
                "native_decision_hint": candidate.get("decision_hint"),
                "checks": checks,
                "equivalent": equivalent,
                "failure_reasons": [key for key, passed in checks.items() if not passed],
            }
        )
    return assertions


def _verify_sources(
    sources: Sequence[object], candidate_path: Path, candidate_count: int
) -> None:
    rows = [_object(row, "candidate source") for row in sources]
    exact = [
        row
        for row in rows
        if row.get("kind") == "file"
        and Path(str(row.get("path"))).resolve() == candidate_path.resolve()
    ]
    if (
        len(rows) != 1
        or len(exact) != 1
        or exact[0].get("row_count") != candidate_count
        or [Path(str(path)).resolve() for path in exact[0].get("scanned_files", [])]
        != [candidate_path.resolve()]
    ):
        raise ValueError("native Process candidate source binding drifted")


def _verify_identity_report(
    *,
    report: JsonObject,
    candidates_file: Sequence[JsonObject],
    sources_file: object,
    action: JsonObject,
    input_path: Path,
    candidate_path: Path,
    candidate_count: int,
) -> Tuple[JsonObject, List[JsonObject]]:
    target = _object(action.get("target"), "desired Process target")
    native_target = _object(report.get("target"), "native Process target")
    report_candidates = [
        _object(row, "native Process candidate")
        for row in _array(report.get("candidates"), "native Process candidates")
    ]
    if (
        report.get("schema_version") != 1
        or report.get("kind") != "process"
        or report.get("status") not in {"passed", "needs_review", "blocked"}
        or report.get("decision")
        not in {
            "reuse",
            "update_same_row",
            "version_bump",
            "create_new",
            "manual_review",
            "block_duplicate",
        }
        or Path(str(report.get("input_path"))).resolve() != input_path.resolve()
        or native_target.get("id") != target.get("id")
        or len(report_candidates) != candidate_count
        or report_candidates != list(candidates_file)
        or native_target.get("schema_validation", {}).get("status")
        not in {"not_applicable", "passed"}
        or native_target.get("schema_validation", {}).get("issue_count") != 0
    ):
        raise ValueError("native Process identity report target or output drifted")
    _verify_sources(
        _array(report.get("candidate_sources"), "candidate sources"),
        candidate_path,
        candidate_count,
    )
    if report.get("candidate_sources") != sources_file:
        raise ValueError("native Process candidate-source file drifted")
    assertions = _candidate_assertions(
        process_index=int(action["process_index"]),
        target=native_target,
        candidates=report_candidates,
    )
    native_decision = report["decision"]
    equivalent = [row for row in assertions if row["equivalent"]]
    blockers = _array(report.get("blockers"), "native Process blockers")
    if native_decision == "block_duplicate" or blockers or equivalent:
        raise RuntimeError("native Process identity evidence found a duplicate/blocker")
    if native_decision == "manual_review":
        if report.get("status") != "needs_review" or len(assertions) != candidate_count:
            raise RuntimeError("manual-review Process identity result is incomplete")
        final = "create_new"
        basis = "all_bound_candidates_failed_process_identity_equivalence"
    else:
        if report.get("status") != "passed":
            raise RuntimeError("automatic native Process identity result did not pass")
        final = native_decision
        basis = "retained_complete_native_automatic_decision"
    decision = {
        "schema_version": 1,
        "policy_version": POLICY_VERSION,
        "process_index": action["process_index"],
        "action_key": action["action_key"],
        "target_process_id": target["id"],
        "native_status": report["status"],
        "native_decision": native_decision,
        "native_confidence": report.get("confidence"),
        "candidate_count": candidate_count,
        "equivalent_candidate_count": len(equivalent),
        "failed_equivalence_candidate_count": len(assertions) - len(equivalent),
        "final_decision": final,
        "basis": basis,
        "human_review_required": False,
    }
    decision["decision_id"] = _sha(_bytes(decision))[:16]
    return decision, assertions


def _executable_plan(
    template: JsonObject, decision: JsonObject, identity_report_sha256: str
) -> JsonObject:
    plan = copy.deepcopy(template)
    target = _object(plan.get("target"), "Process build target")
    unit = _object(plan.get("unit_of_analysis"), "Process unit of analysis")
    reference = _object(unit.get("reference_flow"), "Process reference Flow")
    quantitative = _object(
        plan.get("quantitative_reference_plan"), "Process quantitative reference"
    )
    reference_unit = _text(reference.get("reference_unit"), "reference unit")
    reference["reference_amount"] = 1
    unit["decision"] = "declared_unit_dataset"
    unit["declared_unit"] = {"amount": 1, "unit": reference_unit}
    unit["target_kind"] = "process"
    plan["kind"] = "process"
    plan["ruleset"] = {"id": "process-authoring/strict", "version": "1"}
    plan["identity_decision"] = {
        "decision": decision["final_decision"],
        "decision_id": decision["decision_id"],
        "native_decision": decision["native_decision"],
        "native_report_sha256": identity_report_sha256,
    }
    source_process_id = _text(plan.get("source_process_id"), "source Process ID")
    evidence = _object(plan.get("evidence_manifest"), "Process evidence manifest")
    repository_version = _text(
        evidence.get("source_repository_version"), "USLCI repository version"
    )
    evidence["sources"] = [
        {
            "id": source_process_id,
            "source_kind": "official_uslci_process",
            "title": target.get("name"),
            "version": repository_version,
            "uri": (
                "https://github.com/linancn/TianGong-LCA-Data-Foundation/"
                f"tree/v{repository_version}"
            ),
        }
    ]
    evidence["field_bindings"] = [
        {
            "field_path": field,
            "source_ids": [source_process_id],
            "basis": "accepted source Process slice plus staged/reused quantitative-reference Flow and native identity decision",
        }
        for field in REQUIRED_BUILD_FIELDS
    ]
    unit["reference_flow"] = reference
    plan["unit_of_analysis"] = unit
    plan["evidence_manifest"] = evidence
    plan["execution_policy"] = {
        "report_only_validation": True,
        "process_materialization_allowed": False,
        "process_creation_allowed": False,
        "remote_write_allowed": False,
        "model_connection_allowed": False,
    }
    if quantitative.get("mean_amount") != 1 or quantitative.get("reference_flow_id") != reference.get("id"):
        raise ValueError("Process quantitative reference and unit artifact drifted")
    return plan


def _verify_build_gate(report: JsonObject) -> None:
    required = report.get("required_fields", {})
    if (
        report.get("schema_version") != 1
        or report.get("kind") != "process"
        or report.get("action") != "validate"
        or report.get("status") != "passed"
        or report.get("report_only") is not True
        or report.get("ruleset_id") != "process-authoring/strict"
        or report.get("ruleset_version") != "1"
        or report.get("blockers") != []
        or required.get("required") != REQUIRED_BUILD_FIELDS
        or required.get("satisfied") != REQUIRED_BUILD_FIELDS
        or required.get("missing") != []
        or report.get("schema_validation", {}).get("status") != "not_applicable"
        or report.get("schema_validation", {}).get("issue_count") != 0
        or report.get("next_action") != "materialize_payload"
    ):
        raise RuntimeError("native Process build-plan minimum gate failed")


def _environment() -> Dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in REMOTE_ENV_KEYS}
    env.update(
        {
            "npm_config_offline": "true",
            "npm_config_audit": "false",
            "npm_config_fund": "false",
            "TIANGONG_LCA_REVIEW_LLM": "off",
        }
    )
    return env


def _run_native(
    command: Sequence[str], env: Dict[str, str], root: Path, allowed: set
) -> JsonObject:
    root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic_ns()
    try:
        completed = subprocess.run(command, capture_output=True, env=env, check=False)
        code = completed.returncode
        stdout = completed.stdout.encode() if isinstance(completed.stdout, str) else bytes(completed.stdout)
        stderr = completed.stderr.encode() if isinstance(completed.stderr, str) else bytes(completed.stderr)
    except OSError as error:
        code, stdout, stderr = 127, b"", str(error).encode()
    finished = time.monotonic_ns()
    (root / "stdout.bin").write_bytes(stdout)
    (root / "stderr.bin").write_bytes(stderr)
    execution = {
        "schema_version": 1,
        "command": list(command),
        "exit_code": code,
        "duration_ms": (finished - started) // 1_000_000,
        "stdout_sha256": _sha(stdout),
        "stdout_bytes": len(stdout),
        "stderr_sha256": _sha(stderr),
        "stderr_bytes": len(stderr),
        "remote_credentials_forwarded": False,
        "remote_write_requested": False,
    }
    (root / "execution.json").write_bytes(_bytes(execution, pretty=True))
    if code not in allowed:
        raise RuntimeError(f"native Process command failed with exit code {code}")
    return execution


def _write_json(path: Path, value: object) -> bytes:
    content = _bytes(value, pretty=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content


def _write_jsonl(path: Path, rows: Iterable[JsonObject]) -> bytes:
    content = b"".join(_bytes(row) for row in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content


def _raw_products(raw_root: Path, out_dir: Path) -> Tuple[List[JsonObject], bytes, bytes]:
    rows = []
    for path in sorted(row for row in raw_root.rglob("*") if row.is_file()):
        content = path.read_bytes()
        rows.append(
            {
                "path": path.relative_to(raw_root).as_posix(),
                "bytes": len(content),
                "sha256": _sha(content),
            }
        )
    manifest_path = out_dir / "outputs/uslci-process-action-raw-manifest.jsonl"
    manifest = _write_jsonl(manifest_path, rows)
    archive_path = out_dir / "outputs/uslci-process-action-raw-execution.tar"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w", format=tarfile.PAX_FORMAT) as archive:
        for row in rows:
            path = raw_root / row["path"]
            info = archive.gettarinfo(str(path), arcname=row["path"])
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o644
            with path.open("rb") as handle:
                archive.addfile(info, handle)
    return rows, manifest, archive_path.read_bytes()


def _validate_and_reconstruct(
    contents: Dict[str, bytes], bindings: Dict[str, JsonObject]
) -> JsonObject:
    report = _load_json(contents["recompile_report"], "Process action recompile report")
    plan = _load_json(contents["execution_plan"], "Process action execution plan")
    successors_i = _load_jsonl(contents["recompiled_identity_actions"], "successor identity actions")
    successors_b = _load_jsonl(contents["recompiled_build_templates"], "successor build templates")
    released = _load_jsonl(contents["released_actions"], "released actions")
    carried = _load_jsonl(contents["carried_actions"], "carried actions")
    reconciliation = _load_jsonl(contents["reconciliation"], "reconciliation")
    conditions = _load_jsonl(contents["conditions"], "conditions")
    originals_i = _load_jsonl(contents["original_identity_actions"], "original identity actions")
    originals_b = _load_jsonl(contents["original_build_templates"], "original build templates")
    candidates = _load_jsonl(contents["candidate_catalog"], "Process candidate catalog")
    runtime = _load_json(contents["runtime_report"], "runtime report")
    manifest = _load_json(contents["runtime_manifest"], "runtime manifest")
    if (
        report.get("contract")
        != "lca_research_kernel.uslci_reference_flow_dependent_process_action_recompile/v1"
        or report.get("status")
        != "uslci_reference_flow_dependent_process_actions_recompiled"
        or _object(report.get("routing"), "recompile routing").get(
            "next_internal_route"
        )
        != EXPECTED_ROUTE
        or report.get("successor_action_count") != 6
        or report.get("release_only_action_count") != 2
        or report.get("carry_forward_action_count") != 2
        or report.get("desired_generation") != report.get("observed_generation")
        or plan.get("contract")
        != "lca_research_kernel.uslci_native_slice_process_action_plan/v1"
        or plan.get("plan_version") != report.get("execution_plan_version")
        or plan.get("desired_generation") != report.get("desired_generation")
        or plan.get("node_count") != 10
        or plan.get("edge_count") != 5
        or plan.get("maximum_native_concurrency") != MAX_CONCURRENCY
        or plan.get("native_invocation_executed_count") != 0
        or plan.get("process_materialization_executed_count") != 0
        or plan.get("process_creation_executed_count") != 0
        or plan.get("remote_write_executed_count") != 0
    ):
        raise ValueError("saved Process action plan contract or route drifted")
    for key in (
        "execution_plan",
        "recompiled_identity_actions",
        "recompiled_build_templates",
        "released_actions",
        "carried_actions",
        "reconciliation",
        "conditions",
    ):
        _assert_output_binding(report, key, bindings[key], "recompile report")
    for key in (
        "original_identity_actions",
        "original_build_templates",
        "staged_high_voltage_flow",
        "staged_construction_flow",
        "runtime_report",
        "runtime_manifest",
    ):
        if not _contains_input(report, bindings[key]):
            raise ValueError(f"recompile report omitted bound input: {key}")
    candidate_binding = _object(plan.get("candidate_catalog_binding"), "candidate binding")
    if (
        candidate_binding.get("sha256") != bindings["candidate_catalog"]["sha256"]
        or candidate_binding.get("bytes") != bindings["candidate_catalog"]["bytes"]
        or candidate_binding.get("path") != bindings["candidate_catalog"]["path"]
        or len(candidates) != 2
    ):
        raise ValueError("Process candidate catalog binding drifted")
    runtime_tree = _text(runtime.get("tree_sha256"), "runtime tree")
    if (
        runtime.get("status") != "portable_native_tool_runtime_closure_verified"
        or runtime.get("runtime_content_closed") is not True
        or runtime.get("same_platform_relocation_proven") is not True
        or manifest.get("tree_sha256") != runtime_tree
        or plan.get("runtime_tree_sha256") != runtime_tree
        or report.get("runtime_tree_sha256") != runtime_tree
    ):
        raise ValueError("runtime closure/tree drifted")
    if len(successors_i) != 3 or len(successors_b) != 3 or len(released) != 2 or len(carried) != 2 or len(reconciliation) != 8 or len(conditions) != 4:
        raise ValueError("Process action reconciliation product counts drifted")
    original_i = {int(row["process_index"]): row for row in originals_i}
    original_b = {int(row["process_index"]): row for row in originals_b}
    successor_i = {int(row["process_index"]): row for row in successors_i}
    successor_b = {int(row["process_index"]): row for row in successors_b}
    if set(original_i) != {1, 2, 3, 4, 5} or set(original_b) != {1, 2, 3, 4, 5} or set(successor_i) != {2, 3, 4} or set(successor_b) != {2, 3, 4}:
        raise ValueError("desired Process action indices drifted")
    observations = released + carried
    observation_by = {(int(row["process_index"]), row["kind"]): row for row in observations}
    desired_i = {}
    desired_b = {}
    for index in range(1, 6):
        if index in {2, 3, 4}:
            identity = successor_i[index]
            build = successor_b[index]
            if (
                identity.get("predecessor_action_key") != original_i[index].get("action_key")
                or identity.get("predecessor_action_sha256") != _canonical_sha(original_i[index])
                or build.get("predecessor_action_key") != original_b[index].get("build_plan_action_key")
                or build.get("predecessor_action_sha256") != _canonical_sha(original_b[index])
                or identity.get("target", {}).get("reference_flow_name")
                != "Electricity, AC, 2300-7650 V"
                or build.get("target", {}).get("reference_flow_name")
                != "Electricity, AC, 2300-7650 V"
                or build.get("depends_on_process_action_key") != identity.get("action_key")
            ):
                raise ValueError("high-voltage successor identity/lineage drifted")
        else:
            identity = original_i[index]
            build = original_b[index]
            for row, kind in (
                (identity, "process_identity_preflight"),
                (build, "process_build_plan_validate"),
            ):
                observation = observation_by.get((index, kind))
                if (
                    not observation
                    or observation.get("action_key") != _action_key(row, kind)
                    or observation.get("action_sha256") != _canonical_sha(row)
                    or observation.get("content_changed") is not False
                ):
                    raise ValueError("released/carried Process action content drifted")
        desired_i[index] = identity
        desired_b[index] = build
    nodes = [_object(row, "execution node") for row in _array(plan.get("nodes"), "execution nodes")]
    node_keys = {(row.get("action_key"), row.get("kind")) for row in nodes}
    expected_keys = {
        (_action_key(desired_i[index], "process_identity_preflight"), "process_identity_preflight")
        for index in range(1, 6)
    } | {
        (_action_key(desired_b[index], "process_build_plan_validate"), "process_build_plan_validate")
        for index in range(1, 6)
    }
    if node_keys != expected_keys or any(
        row.get("remote_candidates_enabled") is not False
        or row.get("remote_write_allowed") is not False
        or row.get("process_materialization_allowed") is not False
        or row.get("process_creation_allowed") is not False
        or row.get("model_connection_allowed") is not False
        for row in nodes
    ):
        raise ValueError("saved Process execution node set or side-effect policy drifted")
    edges = [_object(row, "execution edge") for row in _array(plan.get("edges"), "execution edges")]
    expected_edges = {
        (
            _action_key(desired_i[index], "process_identity_preflight"),
            _action_key(desired_b[index], "process_build_plan_validate"),
            "accepted_native_process_identity_decision",
        )
        for index in range(1, 6)
    }
    if {
        (row.get("from_action_key"), row.get("to_action_key"), row.get("dependency"))
        for row in edges
    } != expected_edges:
        raise ValueError("saved Process execution dependencies drifted")
    if {row.get("type") for row in conditions} != {"Recompiled", "Released", "CarriedForward", "ExecutionPlanned"} or any(
        row.get("status") is not True
        or row.get("observed_generation") != report.get("desired_generation")
        for row in conditions
    ):
        raise ValueError("desired/observed Process conditions drifted")
    return {
        "report": report,
        "plan": plan,
        "desired_identity": desired_i,
        "desired_build": desired_b,
        "candidates": candidates,
        "runtime_tree_sha256": runtime_tree,
    }


def _execute_branch(
    *,
    index: int,
    identity: JsonObject,
    template: JsonObject,
    candidate_path: Path,
    candidate_count: int,
    native_node_path: Path,
    native_cli_entry_path: Path,
    raw_root: Path,
) -> JsonObject:
    root = raw_root / f"process-{index}"
    inputs = root / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    request_path = inputs / "process-identity-preflight.json"
    request_path.write_bytes(_bytes(identity["request"], pretty=True))
    prefix = [str(native_node_path.resolve()), str(native_cli_entry_path.resolve())]
    env = _environment()
    identity_root = root / "native/identity-preflight"
    identity_exec = _run_native(
        [
            *prefix,
            "process",
            "identity-preflight",
            "--input",
            str(request_path.resolve()),
            "--candidate-input",
            str(candidate_path.resolve()),
            "--out-dir",
            str(identity_root.resolve()),
            "--json",
        ],
        env,
        root / "commands/identity-preflight",
        {0, 1},
    )
    decision_path = identity_root / "outputs/identity-decision.json"
    candidates_path = identity_root / "outputs/identity-candidates.jsonl"
    sources_path = identity_root / "outputs/identity-candidate-sources.json"
    if not all(path.is_file() for path in (decision_path, candidates_path, sources_path)):
        raise RuntimeError("native Process identity preflight omitted required outputs")
    native_report = _load_json(decision_path.read_bytes(), "native Process identity report")
    native_candidates = _load_jsonl(candidates_path.read_bytes(), "native Process candidates")
    sources = json.loads(sources_path.read_bytes())
    policy, assertions = _verify_identity_report(
        report=native_report,
        candidates_file=native_candidates,
        sources_file=sources,
        action=identity,
        input_path=request_path,
        candidate_path=candidate_path,
        candidate_count=candidate_count,
    )
    plan = _executable_plan(template, policy, _sha(decision_path.read_bytes()))
    build_input = inputs / "process-build-plan.json"
    build_input.write_bytes(_bytes(plan, pretty=True))
    build_root = root / "native/build-plan-validate"
    build_exec = _run_native(
        [
            *prefix,
            "process",
            "build-plan",
            "validate",
            "--input",
            str(build_input.resolve()),
            "--out-dir",
            str(build_root.resolve()),
            "--report-only",
            "--json",
        ],
        env,
        root / "commands/build-plan-validate",
        {0},
    )
    gate_path = build_root / "outputs/build-plan-gate-report.json"
    if not gate_path.is_file():
        raise RuntimeError("native Process build-plan validation omitted gate report")
    gate = _load_json(gate_path.read_bytes(), "native Process build gate")
    _verify_build_gate(gate)
    return {
        "process_index": index,
        "identity_result": {
            "schema_version": 1,
            "process_index": index,
            "action_key": identity["action_key"],
            "target_process_id": identity["target"]["id"],
            "native_status": native_report["status"],
            "native_decision": native_report["decision"],
            "native_exit_code": identity_exec["exit_code"],
            "candidate_count": len(native_candidates),
            "report_sha256": _sha(decision_path.read_bytes()),
        },
        "assertions": assertions,
        "policy": policy,
        "build_plan": plan,
        "build_gate": {
            "schema_version": 1,
            "process_index": index,
            "action_key": template["build_plan_action_key"],
            "target_process_id": identity["target"]["id"],
            "status": gate["status"],
            "blocker_count": len(gate["blockers"]),
            "required_field_count": len(gate["required_fields"]["required"]),
            "satisfied_field_count": len(gate["required_fields"]["satisfied"]),
            "report_sha256": _sha(gate_path.read_bytes()),
            "native_exit_code": build_exec["exit_code"],
        },
        "trace": [
            {
                "schema_version": 1,
                "process_index": index,
                "position": 1,
                "kind": "process_identity_preflight",
                "action_key": identity["action_key"],
                "status": "completed",
                "exit_code": identity_exec["exit_code"],
                "output_sha256": _sha(decision_path.read_bytes()),
                "remote_write_executed": False,
            },
            {
                "schema_version": 1,
                "process_index": index,
                "position": 2,
                "kind": "process_build_plan_validate",
                "action_key": template["build_plan_action_key"],
                "status": "completed",
                "exit_code": build_exec["exit_code"],
                "output_sha256": _sha(gate_path.read_bytes()),
                "remote_write_executed": False,
            },
        ],
    }


def execute_uslci_native_slice_process_action_plan(
    *,
    recompile_report_bytes: bytes,
    recompile_report_binding: JsonObject,
    execution_plan_bytes: bytes,
    execution_plan_binding: JsonObject,
    recompiled_identity_actions_bytes: bytes,
    recompiled_identity_actions_binding: JsonObject,
    recompiled_build_templates_bytes: bytes,
    recompiled_build_templates_binding: JsonObject,
    released_actions_bytes: bytes,
    released_actions_binding: JsonObject,
    carried_actions_bytes: bytes,
    carried_actions_binding: JsonObject,
    reconciliation_bytes: bytes,
    reconciliation_binding: JsonObject,
    conditions_bytes: bytes,
    conditions_binding: JsonObject,
    original_identity_actions_bytes: bytes,
    original_identity_actions_binding: JsonObject,
    original_build_templates_bytes: bytes,
    original_build_templates_binding: JsonObject,
    candidate_catalog_bytes: bytes,
    candidate_catalog_binding: JsonObject,
    staged_high_voltage_flow_bytes: bytes,
    staged_high_voltage_flow_binding: JsonObject,
    staged_construction_flow_bytes: bytes,
    staged_construction_flow_binding: JsonObject,
    runtime_report_bytes: bytes,
    runtime_report_binding: JsonObject,
    runtime_manifest_bytes: bytes,
    runtime_manifest_binding: JsonObject,
    native_node_path: Path,
    native_cli_entry_path: Path,
    out_dir: Path,
) -> JsonObject:
    arguments = locals().copy()
    contents = {name: arguments[f"{name}_bytes"] for name in EXPECTED_INPUT_NAMES}
    bindings = {
        name: _binding(arguments[f"{name}_binding"], contents[name], name)
        for name in EXPECTED_INPUT_NAMES
    }
    verified = _validate_and_reconstruct(contents, bindings)
    out_dir = Path(out_dir)
    raw_root = out_dir / "raw"
    raw_root.mkdir(parents=True, exist_ok=True)
    candidate_path = raw_root / "shared/inputs/process-candidates.jsonl"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_bytes(candidate_catalog_bytes)
    results = []
    failures = []
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
        futures = {
            pool.submit(
                _execute_branch,
                index=index,
                identity=verified["desired_identity"][index],
                template=verified["desired_build"][index],
                candidate_path=candidate_path,
                candidate_count=len(verified["candidates"]),
                native_node_path=native_node_path,
                native_cli_entry_path=native_cli_entry_path,
                raw_root=raw_root,
            ): index
            for index in range(1, 6)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results.append(future.result())
            except Exception as error:  # preserve all branch terminal outcomes
                failures.append(
                    {
                        "schema_version": 1,
                        "process_index": index,
                        "error_type": type(error).__name__,
                        "message": str(error),
                    }
                )
    results.sort(key=lambda row: row["process_index"])
    failures.sort(key=lambda row: row["process_index"])
    outputs = out_dir / "outputs"
    failure_path = outputs / "uslci-process-action-execution-failures.jsonl"
    failure_bytes = _write_jsonl(failure_path, failures)
    if failures:
        _raw_products(raw_root, out_dir)
        raise RuntimeError(f"{len(failures)} Process action branches failed")
    trace = [row for result in results for row in result["trace"]]
    identity_results = [result["identity_result"] for result in results]
    assertions = [row for result in results for row in result["assertions"]]
    policies = [result["policy"] for result in results]
    plans = [result["build_plan"] for result in results]
    gates = [result["build_gate"] for result in results]
    output_rows = {
        "trace": (
            outputs / "uslci-process-action-execution-trace.jsonl",
            _write_jsonl(outputs / "uslci-process-action-execution-trace.jsonl", trace),
        ),
        "identity_results": (
            outputs / "uslci-process-identity-results.jsonl",
            _write_jsonl(outputs / "uslci-process-identity-results.jsonl", identity_results),
        ),
        "candidate_assertions": (
            outputs / "uslci-process-candidate-assertions.jsonl",
            _write_jsonl(outputs / "uslci-process-candidate-assertions.jsonl", assertions),
        ),
        "policy_decisions": (
            outputs / "uslci-process-identity-policy-decisions.jsonl",
            _write_jsonl(outputs / "uslci-process-identity-policy-decisions.jsonl", policies),
        ),
        "executable_build_plans": (
            outputs / "uslci-executable-process-build-plans.jsonl",
            _write_jsonl(outputs / "uslci-executable-process-build-plans.jsonl", plans),
        ),
        "build_gates": (
            outputs / "uslci-process-build-gates.jsonl",
            _write_jsonl(outputs / "uslci-process-build-gates.jsonl", gates),
        ),
        "failures": (failure_path, failure_bytes),
    }
    raw_rows, raw_manifest_bytes, raw_archive_bytes = _raw_products(raw_root, out_dir)
    output_rows["raw_manifest"] = (
        outputs / "uslci-process-action-raw-manifest.jsonl",
        raw_manifest_bytes,
    )
    output_rows["raw_archive"] = (
        outputs / "uslci-process-action-raw-execution.tar",
        raw_archive_bytes,
    )
    artifact_bindings = {
        key: {
            "path": str(path.relative_to(out_dir)),
            "sha256": _sha(content),
            "bytes": len(content),
        }
        for key, (path, content) in output_rows.items()
    }
    execution_version = _sha(
        _bytes(
            {
                "logic": LOGIC_VERSION,
                "policy": POLICY_VERSION,
                "inputs": [bindings[name] for name in EXPECTED_INPUT_NAMES],
                "runtime_tree_sha256": verified["runtime_tree_sha256"],
                "outputs": artifact_bindings,
            }
        )
    )[:16]
    report = {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "policy_version": POLICY_VERSION,
        "status": "uslci_native_slice_process_action_execution_completed",
        "input_version": input_version_for_bindings(
            [bindings[name] for name in EXPECTED_INPUT_NAMES]
        ),
        "execution_version": execution_version,
        "recompile_version": verified["report"].get("recompile_version"),
        "execution_plan_version": verified["plan"].get("plan_version"),
        "runtime_tree_sha256": verified["runtime_tree_sha256"],
        "requested_process_count": 5,
        "completed_process_count": 5,
        "failed_process_count": 0,
        "native_invocation_executed_count": 10,
        "process_identity_preflight_executed_count": 5,
        "process_build_plan_validation_executed_count": 5,
        "minimum_build_plan_passed_count": 5,
        "manual_review_resolved_count": sum(
            row["native_decision"] == "manual_review" for row in policies
        ),
        "candidate_assertion_count": len(assertions),
        "equivalent_candidate_count": sum(row["equivalent"] for row in assertions),
        "execution_trace_node_count": len(trace),
        "execution_trace_completed_node_count": sum(row["status"] == "completed" for row in trace),
        "maximum_native_concurrency": MAX_CONCURRENCY,
        "raw_action_output_count": len(raw_rows),
        "process_materialization_executed_count": 0,
        "process_creation_executed_count": 0,
        "flow_creation_executed_count": 0,
        "save_draft_executed_count": 0,
        "model_connection_executed_count": 0,
        "model_mutation_executed_count": 0,
        "network_read_executed_count": 0,
        "remote_write_executed_count": 0,
        "external_write_executed_count": 0,
        "publication_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "remaining_flow_preflight_action_count": 1359,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "input_bindings": [bindings[name] for name in EXPECTED_INPUT_NAMES],
        "artifact_bindings": artifact_bindings,
        "next_action": "Execute exact USLCI native Flow identity-preflight batch 2 (rows 101–200).",
        "routing": {"next_internal_route": NEXT_ROUTE, "pause_internal_work": False},
    }
    report_path = outputs / "uslci-native-slice-process-action-execution.json"
    report_bytes = _write_json(report_path, report)
    return {
        "report": report,
        "report_path": report_path,
        "report_bytes": report_bytes,
        "trace": trace,
        "identity_results": identity_results,
        "candidate_assertions": assertions,
        "policy_decisions": policies,
        "executable_build_plans": plans,
        "build_gates": gates,
        "failures": failures,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    for name in EXPECTED_INPUT_NAMES:
        parser.add_argument(f"--{name.replace('_', '-')}", type=Path, required=True)
        parser.add_argument(f"--{name.replace('_', '-')}-binding", type=Path, required=True)
    parser.add_argument("--native-node", type=Path, required=True)
    parser.add_argument("--native-cli-entry", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    values: JsonObject = {
        "native_node_path": args.native_node,
        "native_cli_entry_path": args.native_cli_entry,
        "out_dir": args.out_dir,
    }
    for name in EXPECTED_INPUT_NAMES:
        values[f"{name}_bytes"] = getattr(args, name).read_bytes()
        values[f"{name}_binding"] = json.loads(
            getattr(args, f"{name}_binding").read_text(encoding="utf-8")
        )
    result = execute_uslci_native_slice_process_action_plan(**values)
    print(json.dumps(result["report"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
