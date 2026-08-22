"""Execute the exact saved USLCI reference-Flow authoring plan locally."""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
import stat
import subprocess
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_reference_flow_authoring_execution/v1"
LOGIC_VERSION = "2026-07-17.1"
PLAN_CONTRACT = "lca_research_kernel.uslci_reference_flow_authoring_plan/v1"
RUNTIME_CONTRACT = "lca_research_kernel.portable_native_tool_runtime_closure/v1"
NEXT_ROUTE = "recompile-uslci-reference-flow-dependent-process-actions"

FLOW_SPECS = {
    "high-voltage-electricity": {
        "target_flow_id": "66937f42-97d5-5822-a060-8d711ad64677",
        "flow_plan_key": "high_voltage_flow_plan",
        "flow_plan_path": "outputs/flows/high-voltage-electricity-flow-build-plan.json",
    },
    "power-plant-construction": {
        "target_flow_id": "2d727759-6023-5e1a-bb3a-286d9a9d969c",
        "flow_plan_key": "power_plant_construction_flow_plan",
        "flow_plan_path": "outputs/flows/power-plant-construction-flow-build-plan.json",
    },
}

STAGES = (
    "materialize-report-only",
    "verify-materialized-semantics",
    "apply-controlled-classification",
    "verify-controlled-classification",
    "validate-flow",
    "qa-flow",
    "save-draft-dry-run",
    "register-staged-flow",
)

REMOTE_ENV_PREFIXES = (
    "TIANGONG_LCA_API_",
    "TIANGONG_LCA_MCP_",
    "TIANGONG_LCA_REVIEW_",
    "OPENAI_",
    "ANTHROPIC_",
)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _jsonl_bytes(rows: Sequence[Mapping[str, object]]) -> bytes:
    return b"".join(_json_bytes(dict(row)) for row in rows)


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


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _parse_json(content: bytes, label: str) -> JsonObject:
    try:
        return _object(json.loads(content), label)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error


def _parse_jsonl(
    content: bytes, label: str, *, allow_empty: bool = False
) -> List[JsonObject]:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not UTF-8") from error
    result = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            result.append(_object(json.loads(line), f"{label} row {index}"))
        except json.JSONDecodeError as error:
            raise ValueError(f"{label} row {index} is not valid JSON") from error
    if not result and not allow_empty:
        raise ValueError(f"{label} is empty")
    return result


def _binding(value: object, content: bytes, label: str) -> JsonObject:
    row = _object(value, label)
    path = _text(row.get("path"), f"{label} path")
    digest = _text(row.get("sha256"), f"{label} sha256")
    size = _integer(row.get("bytes"), f"{label} bytes")
    if digest != _sha256(content) or size != len(content):
        raise ValueError(f"{label} hash or byte count drifted")
    return {"path": path, "sha256": digest, "bytes": size}


def _artifact_binding(path: str, content: bytes) -> JsonObject:
    return {"path": path, "sha256": _sha256(content), "bytes": len(content)}


def _first_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        text = value.get("#text")
        return text.strip() if isinstance(text, str) else ""
    if isinstance(value, list):
        for item in value:
            text = _first_text(item)
            if text:
                return text
    return ""


def _as_list(value: object) -> List[object]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _decimal(value: object, label: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{label} is not numeric") from error


def _execution_environment() -> Dict[str, str]:
    environment = dict(os.environ)
    for name in list(environment):
        if name.startswith(REMOTE_ENV_PREFIXES):
            environment.pop(name, None)
    environment["npm_config_offline"] = "true"
    environment["NO_UPDATE_NOTIFIER"] = "1"
    return environment


def _expected_action_key(plan_version: str, target_flow_id: str, stage: str) -> str:
    return _sha256(
        _json_bytes(
            {
                "plan_version": plan_version,
                "target_flow_id": target_flow_id,
                "stage": stage,
            }
        )
    )[:16]


def _validate_dag(dag: JsonObject, plan_version: str, runtime_tree: str) -> None:
    if (
        dag.get("contract") != f"{PLAN_CONTRACT}/execution-dag"
        or dag.get("plan_version") != plan_version
        or dag.get("runtime_tree_sha256") != runtime_tree
        or dag.get("node_count") != 17
        or dag.get("edge_count") != 16
        or dag.get("maximum_native_concurrency") != 2
        or dag.get("branch_failure_policy")
        != "retain diagnostics; do not register failed Flow or release its entity gates"
    ):
        raise ValueError("saved execution DAG identity drifted")
    boundary = _object(dag.get("side_effect_boundary"), "DAG side-effect boundary")
    if boundary != {
        "catalog_append_allowed": False,
        "remote_write_allowed": False,
        "save_draft_mode": "dry_run_only",
        "process_execution_allowed": False,
        "model_connection_allowed": False,
    }:
        raise ValueError("saved execution DAG side-effect boundary permits remote action")
    nodes = [_object(row, "DAG node") for row in _array(dag.get("nodes"), "DAG nodes")]
    edges = [_object(row, "DAG edge") for row in _array(dag.get("edges"), "DAG edges")]
    if len(nodes) != 17 or len(edges) != 16:
        raise ValueError("saved execution DAG counts drifted")
    by_id = {}
    expected_edges = []
    for slug, spec in FLOW_SPECS.items():
        prior = None
        for position, stage in enumerate(STAGES, start=1):
            node_id = f"{slug}:{stage}"
            match = [row for row in nodes if row.get("node_id") == node_id]
            if len(match) != 1:
                raise ValueError("saved execution DAG branch nodes drifted")
            node = match[0]
            if (
                node.get("branch") != slug
                or node.get("position") != position
                or node.get("stage") != stage
                or node.get("action_key")
                != _expected_action_key(plan_version, spec["target_flow_id"], stage)
                or node.get("remote_write_allowed") is not False
                or node.get("publication_allowed") is not False
            ):
                raise ValueError("saved execution DAG action or remote side-effect drifted")
            by_id[node_id] = node
            if prior:
                expected_edges.append({"from": prior, "to": node_id})
            prior = node_id
        expected_edges.append(
            {"from": prior, "to": "authoring-plan:record-project-result"}
        )
    final = [
        row
        for row in nodes
        if row.get("node_id") == "authoring-plan:record-project-result"
    ]
    expected_final_key = _sha256(
        _json_bytes({"plan_version": plan_version, "stage": "record-project-result"})
    )[:16]
    if (
        len(final) != 1
        or final[0].get("action_key") != expected_final_key
        or final[0].get("remote_write_allowed") is not False
        or final[0].get("publication_allowed") is not False
    ):
        raise ValueError("saved execution DAG aggregate node drifted")
    if sorted(edges, key=_json_bytes) != sorted(expected_edges, key=_json_bytes):
        raise ValueError("saved execution DAG dependencies drifted")


def _verify_runtime_entry(
    manifest: JsonObject, path: Path, suffix: str, label: str
) -> None:
    if not path.is_file():
        raise ValueError(f"{label} is missing")
    bindings = manifest.get("file_bindings")
    if not isinstance(bindings, list):
        # Project performs a whole-tree verification. Minimal pure-test manifests do
        # not need to duplicate the 7,877-entry runtime inventory.
        return
    matches = [
        row
        for row in bindings
        if isinstance(row, dict) and row.get("path") == suffix
    ]
    if len(matches) != 1:
        raise ValueError(f"{label} runtime binding is missing")
    row = matches[0]
    content = path.read_bytes()
    if (
        row.get("kind") != "file"
        or row.get("sha256") != _sha256(content)
        or row.get("bytes") != len(content)
    ):
        raise ValueError(f"{label} runtime binding drifted")


def _validate_inputs(
    *,
    plan_report_bytes: bytes,
    plan_report_binding: Mapping[str, object],
    plan_index_bytes: bytes,
    plan_index_binding: Mapping[str, object],
    execution_dag_bytes: bytes,
    execution_dag_binding: Mapping[str, object],
    classifications_bytes: bytes,
    classifications_binding: Mapping[str, object],
    support_assertions_bytes: bytes,
    support_assertions_binding: Mapping[str, object],
    downstream_patches_bytes: bytes,
    downstream_patches_binding: Mapping[str, object],
    high_voltage_flow_plan_bytes: bytes,
    high_voltage_flow_plan_binding: Mapping[str, object],
    power_plant_construction_flow_plan_bytes: bytes,
    power_plant_construction_flow_plan_binding: Mapping[str, object],
    runtime_report_bytes: bytes,
    runtime_report_binding: Mapping[str, object],
    runtime_manifest_bytes: bytes,
    runtime_manifest_binding: Mapping[str, object],
    native_node_path: Path,
    native_cli_entry_path: Path,
) -> JsonObject:
    raw_inputs = {
        "plan_report": (plan_report_bytes, plan_report_binding),
        "plan_index": (plan_index_bytes, plan_index_binding),
        "execution_dag": (execution_dag_bytes, execution_dag_binding),
        "classifications": (classifications_bytes, classifications_binding),
        "support_assertions": (support_assertions_bytes, support_assertions_binding),
        "downstream_patches": (downstream_patches_bytes, downstream_patches_binding),
        "high_voltage_flow_plan": (
            high_voltage_flow_plan_bytes,
            high_voltage_flow_plan_binding,
        ),
        "power_plant_construction_flow_plan": (
            power_plant_construction_flow_plan_bytes,
            power_plant_construction_flow_plan_binding,
        ),
        "runtime_report": (runtime_report_bytes, runtime_report_binding),
        "runtime_manifest": (runtime_manifest_bytes, runtime_manifest_binding),
    }
    bindings = {
        name: _binding(raw_binding, content, f"{name} binding")
        for name, (content, raw_binding) in raw_inputs.items()
    }
    report = _parse_json(plan_report_bytes, "authoring plan report")
    index = _parse_json(plan_index_bytes, "authoring plan index")
    dag = _parse_json(execution_dag_bytes, "authoring execution DAG")
    classifications = _parse_jsonl(classifications_bytes, "classification decisions")
    assertions = _parse_jsonl(support_assertions_bytes, "support assertions")
    patches = _parse_jsonl(downstream_patches_bytes, "downstream patches")
    flow_plans = {
        "high-voltage-electricity": _parse_json(
            high_voltage_flow_plan_bytes, "high-voltage Flow plan"
        ),
        "power-plant-construction": _parse_json(
            power_plant_construction_flow_plan_bytes,
            "power-plant-construction Flow plan",
        ),
    }
    runtime = _parse_json(runtime_report_bytes, "runtime report")
    manifest = _parse_json(runtime_manifest_bytes, "runtime manifest")
    if (
        report.get("contract") != PLAN_CONTRACT
        or report.get("status") != "uslci_reference_flow_authoring_plan_compiled"
        or report.get("planned_flow_count") != 2
        or report.get("classification_decision_count") != 2
        or report.get("support_reference_assertion_count") != 2
        or report.get("downstream_name_patch_count") != 6
        or report.get("downstream_release_only_count") != 2
        or report.get("execution_dag_node_count") != 17
        or report.get("execution_dag_edge_count") != 16
        or report.get("routing", {}).get("next_internal_route")
        != "execute-uslci-native-reference-flow-authoring-plan"
    ):
        raise ValueError("authoring plan does not authorize execution")
    plan_version = _text(report.get("plan_version"), "authoring plan version")
    runtime_tree = _text(
        _object(report.get("runtime"), "authoring runtime").get(
            "runtime_tree_sha256"
        ),
        "authoring runtime tree",
    )
    artifact_bindings = _object(report.get("artifact_bindings"), "plan artifact bindings")
    for name in (
        "plan_index",
        "execution_dag",
        "classifications",
        "support_assertions",
        "downstream_patches",
        "high_voltage_flow_plan",
        "power_plant_construction_flow_plan",
    ):
        expected = _object(artifact_bindings.get(name), f"plan {name} binding")
        actual = bindings[name]
        if expected.get("sha256") != actual["sha256"] or expected.get("bytes") != actual["bytes"]:
            raise ValueError(f"saved plan product drifted: {name}")
    if (
        index.get("contract") != f"{PLAN_CONTRACT}/index"
        or index.get("plan_version") != plan_version
        or index.get("runtime_tree_sha256") != runtime_tree
        or index.get("apply_requires_exact_plan_bytes") is not True
    ):
        raise ValueError("saved plan index drifted")
    ordered = [_object(row, "saved plan index row") for row in _array(index.get("ordered_flow_plans"), "saved plan index rows")]
    if len(ordered) != 2:
        raise ValueError("saved plan index must contain two Flow plans")
    for position, (slug, spec) in enumerate(FLOW_SPECS.items(), start=1):
        row = ordered[position - 1]
        plan_binding = bindings[spec["flow_plan_key"]]
        if (
            row.get("position") != position
            or row.get("slug") != slug
            or row.get("target_flow_id") != spec["target_flow_id"]
            or row.get("path") != spec["flow_plan_path"]
            or row.get("sha256") != plan_binding["sha256"]
            or row.get("bytes") != plan_binding["bytes"]
        ):
            raise ValueError("saved Flow plan index binding drifted")
    _validate_dag(dag, plan_version, runtime_tree)
    if (
        runtime.get("contract") != RUNTIME_CONTRACT
        or runtime.get("status") != "portable_native_tool_runtime_closure_verified"
        or runtime.get("runtime_content_closed") is not True
        or runtime.get("same_platform_relocation_proven") is not True
        or runtime.get("tree_sha256") != runtime_tree
        or manifest.get("contract") != RUNTIME_CONTRACT
        or manifest.get("status") != "portable_native_tool_runtime_closure_compiled"
        or manifest.get("runtime_content_closed") is not True
        or manifest.get("tree_sha256") != runtime_tree
    ):
        raise ValueError("copied TianGong runtime identity drifted")
    cli = manifest.get("tiangong_cli")
    if not isinstance(cli, dict):
        cli = manifest.get("cli")
    cli = _object(cli, "runtime TianGong CLI")
    if cli.get("package") != "@tiangong-lca/cli" or cli.get("version") != "0.0.25":
        raise ValueError("unsupported copied TianGong CLI")
    _verify_runtime_entry(manifest, native_node_path, "bin/node", "copied Node")
    _verify_runtime_entry(
        manifest,
        native_cli_entry_path,
        "npm-runtime/node_modules/@tiangong-lca/cli/bin/tiangong-lca.js",
        "copied TianGong CLI entry",
    )
    decision_by_target = {}
    for row in classifications:
        target = _text(row.get("dataset_id"), "classification target")
        if target in decision_by_target:
            raise ValueError("duplicate classification target")
        decision_by_target[target] = row
    assertion_by_target = {}
    for row in assertions:
        target = _text(row.get("target_flow_id"), "support assertion target")
        if target in assertion_by_target:
            raise ValueError("duplicate support assertion target")
        assertion_by_target[target] = row
    for slug, spec in FLOW_SPECS.items():
        plan = flow_plans[slug]
        target = _object(plan.get("target"), f"{slug} target")
        property_plan = _object(plan.get("flow_property_plan"), f"{slug} FlowProperty plan")
        native_classification = _object(
            plan.get("native_classification_plan"), f"{slug} classification plan"
        )
        generation = _object(plan.get("generation_manifest"), f"{slug} generation manifest")
        policy = _object(plan.get("execution_policy"), f"{slug} execution policy")
        decision = decision_by_target.get(spec["target_flow_id"])
        assertion = assertion_by_target.get(spec["target_flow_id"])
        if not isinstance(decision, dict) or not isinstance(assertion, dict):
            raise ValueError(f"{slug} decision or support assertion is missing")
        if (
            target.get("uuid") != spec["target_flow_id"]
            or target.get("version") != "00.00.001"
            or target.get("flow_type") != "Product flow"
            or generation.get("runtime_tree_sha256") != runtime_tree
            or generation.get("support_reference_local_entity_verified") is not False
            or generation.get("unit_group_identity_known") is not False
            or generation.get("publish_ready") is not False
            or policy.get("saved_plan_required") is not True
            or policy.get("local_staging_only") is not True
            or policy.get("save_draft_dry_run_only") is not True
            or policy.get("remote_write_allowed") is not False
            or policy.get("publication_allowed") is not False
            or policy.get("model_connection_allowed") is not False
            or decision.get("dataset_version") != "00.00.001"
            or decision.get("category_type") != "flow-product"
            or decision.get("code") != native_classification.get("leaf_code")
            or decision.get("path") != native_classification.get("path")
            or assertion.get("flow_property_id")
            != property_plan.get("reference_property_id")
            or assertion.get("flow_property_name")
            != property_plan.get("reference_property")
            or assertion.get("reference_unit") != property_plan.get("reference_unit")
            or assertion.get("source_tuple_consistent") is not True
            or assertion.get("support_reference_source_observed") is not True
            or assertion.get("support_reference_local_entity_verified") is not False
            or assertion.get("unit_group_identity_known") is not False
            or assertion.get("publish_ready") is not False
        ):
            raise ValueError(f"{slug} saved authoring semantics drifted")
    if len(patches) != 8 or sum(row.get("patch_required") is True for row in patches) != 6:
        raise ValueError("downstream action-patch ledger drifted")
    if sum(row.get("disposition") == "release_after_entity_registered" for row in patches) != 2:
        raise ValueError("downstream release-only rows drifted")
    return {
        "bindings": bindings,
        "report": report,
        "index": index,
        "dag": dag,
        "flow_plans": flow_plans,
        "flow_plan_bytes": {
            "high-voltage-electricity": high_voltage_flow_plan_bytes,
            "power-plant-construction": power_plant_construction_flow_plan_bytes,
        },
        "decisions": decision_by_target,
        "assertions": assertion_by_target,
        "patches": patches,
        "runtime": runtime,
        "runtime_manifest": manifest,
        "plan_version": plan_version,
        "runtime_tree_sha256": runtime_tree,
    }


def _flow_parts(flow: JsonObject) -> Tuple[JsonObject, JsonObject, JsonObject, JsonObject]:
    root = _object(flow.get("flowDataSet"), "TIDAS Flow root")
    information = _object(root.get("flowInformation"), "TIDAS Flow information")
    dataset = _object(information.get("dataSetInformation"), "TIDAS Flow dataset information")
    administration = _object(root.get("administrativeInformation"), "TIDAS Flow administration")
    return root, information, dataset, administration


def _verify_materialized_flow(
    flow: JsonObject, plan: JsonObject, gate: JsonObject
) -> JsonObject:
    target = _object(plan.get("target"), "saved Flow target")
    property_plan = _object(plan.get("flow_property_plan"), "saved Flow property plan")
    root, information, dataset, administration = _flow_parts(flow)
    publication = _object(
        administration.get("publicationAndOwnership"), "Flow publication and ownership"
    )
    modelling = _object(root.get("modellingAndValidation"), "Flow modelling and validation")
    method = _object(modelling.get("LCIMethod"), "Flow LCI method")
    properties_root = _object(root.get("flowProperties"), "Flow properties")
    properties = [_object(row, "Flow property") for row in _as_list(properties_root.get("flowProperty"))]
    quantitative = _object(information.get("quantitativeReference"), "Flow quantitative reference")
    names = _object(dataset.get("name"), "Flow name")
    if (
        dataset.get("common:UUID") != target.get("uuid")
        or publication.get("common:dataSetVersion") != target.get("version")
        or _first_text(names.get("baseName")) != plan.get("name_plan", {}).get("base_name")
        or method.get("typeOfDataSet") != target.get("flow_type")
        or quantitative.get("referenceToReferenceFlowProperty") != "0"
        or len(properties) != 1
    ):
        raise ValueError("materialized Flow identity, name, type or quantitative reference drifted")
    prop = properties[0]
    reference = _object(
        prop.get("referenceToFlowPropertyDataSet"), "Flow property reference"
    )
    if (
        prop.get("@dataSetInternalID") != "0"
        or reference.get("@refObjectId") != property_plan.get("reference_property_id")
        or reference.get("@version") != property_plan.get("reference_property_version")
        or _first_text(reference.get("common:shortDescription"))
        != property_plan.get("reference_property")
        or _decimal(prop.get("meanValue"), "Flow property mean value")
        != _decimal(property_plan.get("mean_value"), "planned Flow property mean value")
    ):
        raise ValueError("materialized Flow property drifted")
    if (
        gate.get("schema_version") != 1
        or gate.get("kind") != "flow"
        or gate.get("action") != "materialize"
        or gate.get("status") != "passed"
        or gate.get("report_only") is not True
        or gate.get("ruleset_id") != "flow-authoring/strict"
        or gate.get("ruleset_version") != "1"
        or gate.get("blockers") != []
        or gate.get("schema_validation", {}).get("status") != "passed"
        or gate.get("schema_validation", {}).get("issue_count") != 0
    ):
        raise ValueError("native Flow materialization gate failed")
    return {
        "target_flow_id": target["uuid"],
        "target_flow_name": plan["name_plan"]["base_name"],
        "flow_property_id": property_plan["reference_property_id"],
        "flow_property_name": property_plan["reference_property"],
        "reference_unit": property_plan["reference_unit"],
    }


def _classification_path(flow: JsonObject) -> List[JsonObject]:
    _, _, dataset, _ = _flow_parts(flow)
    information = _object(
        dataset.get("classificationInformation"), "Flow classification information"
    )
    classification = _object(
        information.get("common:classification"), "Flow classification"
    )
    return [
        _object(row, "Flow classification class")
        for row in _as_list(classification.get("common:class"))
    ]


def _without_classification(flow: JsonObject) -> JsonObject:
    result = copy.deepcopy(flow)
    _, _, dataset, _ = _flow_parts(result)
    dataset.pop("classificationInformation", None)
    return result


def _verify_classification(
    materialized: JsonObject,
    classified: JsonObject,
    decision: JsonObject,
    report: JsonObject,
    evidence: Sequence[JsonObject],
) -> None:
    if _without_classification(materialized) != _without_classification(classified):
        raise ValueError("controlled classification changed fields outside its owned subtree")
    if _classification_path(classified) != decision.get("path"):
        raise ValueError("controlled classification path drifted")
    counts = _object(report.get("counts"), "classification apply counts")
    if (
        report.get("status") != "completed"
        or report.get("command") != "dataset classification apply"
        or counts != {"rows": 1, "decisions": 1, "applied": 1, "blockers": 0}
        or report.get("blockers") != []
        or len(evidence) != 1
        or evidence[0].get("dataset_id") != decision.get("dataset_id")
        or evidence[0].get("category_type") != "flow-product"
        or evidence[0].get("applied_classification") != decision.get("path")
    ):
        raise ValueError("native controlled-classification evidence failed")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_jsonl_bytes(rows))


def _run_native(
    *,
    node: JsonObject,
    command: List[str],
    environment: Dict[str, str],
    node_root: Path,
) -> JsonObject:
    if "--commit" in command:
        raise ValueError("native command attempted remote commit")
    node_root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic_ns()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            env=environment,
        )
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        if isinstance(stdout, str):
            stdout = stdout.encode("utf-8")
        if isinstance(stderr, str):
            stderr = stderr.encode("utf-8")
    except OSError as error:
        returncode = 127
        stdout = b""
        stderr = str(error).encode("utf-8", errors="replace")
    finished = time.monotonic_ns()
    stdout = bytes(stdout)
    stderr = bytes(stderr)
    (node_root / "stdout.bin").write_bytes(stdout)
    (node_root / "stderr.bin").write_bytes(stderr)
    execution = {
        "schema_version": 1,
        "node_id": node["node_id"],
        "action_key": node["action_key"],
        "command": command,
        "exit_code": returncode,
        "duration_ms": (finished - started) // 1_000_000,
        "stdout_sha256": _sha256(stdout),
        "stdout_bytes": len(stdout),
        "stderr_sha256": _sha256(stderr),
        "stderr_bytes": len(stderr),
        "environment_policy": {
            "npm_offline": True,
            "remote_credentials_forwarded": False,
            "review_llm_enabled": False,
        },
        "remote_write_requested": False,
    }
    _write_json(node_root / "execution.json", execution)
    if returncode != 0:
        raise RuntimeError(
            f"native node {node['node_id']} failed with exit code {returncode}"
        )
    return execution


def _node_by_stage(dag: JsonObject, slug: str) -> Dict[str, JsonObject]:
    return {
        str(row["stage"]): row
        for row in dag["nodes"]
        if row.get("branch") == slug
    }


def _trace_row(
    node: JsonObject,
    *,
    status: str,
    output_sha256: Optional[str] = None,
    native_execution: Optional[JsonObject] = None,
) -> JsonObject:
    row = {
        "schema_version": 1,
        "node_id": node["node_id"],
        "branch": node["branch"],
        "position": node["position"],
        "stage": node["stage"],
        "action_key": node["action_key"],
        "status": status,
        "native": node["native"],
        "remote_write_executed": False,
        "publication_executed": False,
    }
    if output_sha256 is not None:
        row["output_sha256"] = output_sha256
    if native_execution is not None:
        row["exit_code"] = native_execution["exit_code"]
        row["stdout_sha256"] = native_execution["stdout_sha256"]
        row["stderr_sha256"] = native_execution["stderr_sha256"]
    return row


def _execute_branch(
    *,
    slug: str,
    plan: JsonObject,
    plan_bytes: bytes,
    decision: JsonObject,
    assertion: JsonObject,
    dag: JsonObject,
    native_node_path: Path,
    native_cli_entry_path: Path,
    raw_root: Path,
    staged_output: Path,
) -> JsonObject:
    nodes = _node_by_stage(dag, slug)
    root = raw_root / slug
    inputs = root / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    plan_path = inputs / "flow-build-plan.json"
    decision_path = inputs / "classification-decision.jsonl"
    plan_path.write_bytes(plan_bytes)
    _write_jsonl(decision_path, [decision])
    prefix = [str(native_node_path.resolve()), str(native_cli_entry_path.resolve())]
    environment = _execution_environment()
    trace = []
    native_count = 0

    materialize_root = root / "native/materialize"
    node = nodes["materialize-report-only"]
    execution = _run_native(
        node=node,
        command=[
            *prefix,
            "flow",
            "build-plan",
            "materialize",
            "--input",
            str(plan_path.resolve()),
            "--out-dir",
            str(materialize_root.resolve()),
            "--report-only",
            "--json",
        ],
        environment=environment,
        node_root=root / "commands/materialize-report-only",
    )
    native_count += 1
    materialized_path = materialize_root / "outputs/materialized-flow.json"
    gate_path = materialize_root / "outputs/build-plan-gate-report.json"
    if not materialized_path.is_file() or not gate_path.is_file():
        raise RuntimeError("native materialization omitted required outputs")
    materialized_bytes = materialized_path.read_bytes()
    materialized = _parse_json(materialized_bytes, f"{slug} materialized Flow")
    gate = _parse_json(gate_path.read_bytes(), f"{slug} materialization gate")
    trace.append(
        _trace_row(
            node,
            status="completed",
            output_sha256=_sha256(materialized_bytes),
            native_execution=execution,
        )
    )

    node = nodes["verify-materialized-semantics"]
    semantics = _verify_materialized_flow(materialized, plan, gate)
    _write_json(root / "verification/materialized-semantics.json", semantics)
    trace.append(
        _trace_row(
            node,
            status="completed",
            output_sha256=_sha256(_json_bytes(semantics)),
        )
    )

    classified_path = root / "classified-flow.jsonl"
    classification_root = root / "native/classification"
    node = nodes["apply-controlled-classification"]
    execution = _run_native(
        node=node,
        command=[
            *prefix,
            "dataset",
            "classification",
            "apply",
            "--input",
            str(materialized_path.resolve()),
            "--decisions",
            str(decision_path.resolve()),
            "--out",
            str(classified_path.resolve()),
            "--type",
            "flow-product",
            "--out-dir",
            str(classification_root.resolve()),
            "--json",
        ],
        environment=environment,
        node_root=root / "commands/apply-controlled-classification",
    )
    native_count += 1
    classified_rows = _parse_jsonl(
        classified_path.read_bytes(), f"{slug} classified Flow"
    )
    if len(classified_rows) != 1:
        raise ValueError("controlled classification must emit exactly one Flow")
    classified = classified_rows[0]
    report_path = classification_root / "outputs/classification-apply-report.json"
    evidence_path = classification_root / "outputs/classification-apply-evidence.jsonl"
    classification_report = _parse_json(
        report_path.read_bytes(), f"{slug} classification report"
    )
    classification_evidence = _parse_jsonl(
        evidence_path.read_bytes(), f"{slug} classification evidence"
    )
    trace.append(
        _trace_row(
            node,
            status="completed",
            output_sha256=_sha256(classified_path.read_bytes()),
            native_execution=execution,
        )
    )

    node = nodes["verify-controlled-classification"]
    _verify_classification(
        materialized,
        classified,
        decision,
        classification_report,
        classification_evidence,
    )
    classification_verification = {
        "schema_version": 1,
        "target_flow_id": decision["dataset_id"],
        "classification_only_delta": True,
        "applied_path": decision["path"],
        "leaf_code": decision["code"],
    }
    _write_json(
        root / "verification/controlled-classification.json",
        classification_verification,
    )
    trace.append(
        _trace_row(
            node,
            status="completed",
            output_sha256=_sha256(_json_bytes(classification_verification)),
        )
    )

    validation_root = root / "native/validation"
    node = nodes["validate-flow"]
    execution = _run_native(
        node=node,
        command=[
            *prefix,
            "dataset",
            "validate",
            "--input",
            str(classified_path.resolve()),
            "--type",
            "flow",
            "--out-dir",
            str(validation_root.resolve()),
            "--json",
        ],
        environment=environment,
        node_root=root / "commands/validate-flow",
    )
    native_count += 1
    validation_path = validation_root / "outputs/validation-report.json"
    validation = _parse_json(validation_path.read_bytes(), f"{slug} validation report")
    counts = _object(validation.get("counts"), "Flow validation counts")
    rows = _array(validation.get("rows"), "Flow validation rows")
    if (
        validation.get("status") != "completed"
        or validation.get("requested_type") != "flow"
        or counts.get("total") != 1
        or counts.get("valid") != 1
        or counts.get("invalid") != 0
        or counts.get("by_type", {}).get("flow") != 1
        or len(rows) != 1
        or rows[0].get("id") != decision["dataset_id"]
        or rows[0].get("version") != "00.00.001"
        or rows[0].get("type") != "flow"
        or rows[0].get("status") != "valid"
        or rows[0].get("issue_count") != 0
    ):
        raise ValueError("native Flow validation failed")
    trace.append(
        _trace_row(
            node,
            status="completed",
            output_sha256=_sha256(validation_path.read_bytes()),
            native_execution=execution,
        )
    )

    qa_root = root / "native/qa"
    node = nodes["qa-flow"]
    execution = _run_native(
        node=node,
        command=[
            *prefix,
            "qa",
            "flow",
            "--rows-file",
            str(classified_path.resolve()),
            "--out-dir",
            str(qa_root.resolve()),
            "--json",
        ],
        environment=environment,
        node_root=root / "commands/qa-flow",
    )
    native_count += 1
    qa_path = qa_root / "flow_qa_report.json"
    qa = _parse_json(qa_path.read_bytes(), f"{slug} QA report")
    if (
        qa.get("status") != "completed_local_flow_qa"
        or qa.get("flow_count") != 1
        or qa.get("blocker_count") != 0
        or qa.get("ruleset_gate", {}).get("status") != "passed"
    ):
        raise ValueError("native Flow QA failed")
    trace.append(
        _trace_row(
            node,
            status="completed",
            output_sha256=_sha256(qa_path.read_bytes()),
            native_execution=execution,
        )
    )

    save_root = root / "native/save-draft"
    node = nodes["save-draft-dry-run"]
    execution = _run_native(
        node=node,
        command=[
            *prefix,
            "dataset",
            "save-draft",
            "--input",
            str(classified_path.resolve()),
            "--type",
            "flow",
            "--out-dir",
            str(save_root.resolve()),
            "--dry-run",
            "--json",
        ],
        environment=environment,
        node_root=root / "commands/save-draft-dry-run",
    )
    native_count += 1
    save_path = save_root / "outputs/dataset-save-draft/summary.json"
    save = _parse_json(save_path.read_bytes(), f"{slug} save-draft report")
    save_counts = _object(save.get("counts"), "Flow save-draft counts")
    if (
        save.get("status") != "completed"
        or save.get("requested_type") != "flow"
        or save.get("commit") is not False
        or save.get("mode") != "dry_run"
        or save_counts.get("selected") != 1
        or save_counts.get("prepared") != 1
        or save_counts.get("executed") != 0
        or save_counts.get("failed") != 0
        or save_counts.get("by_table", {}).get("flows") != 1
    ):
        raise ValueError("native Flow save-draft dry-run failed or executed a write")
    trace.append(
        _trace_row(
            node,
            status="completed",
            output_sha256=_sha256(save_path.read_bytes()),
            native_execution=execution,
        )
    )

    staged_bytes = _json_bytes(classified)
    staged_output.parent.mkdir(parents=True, exist_ok=True)
    staged_output.write_bytes(staged_bytes)
    node = nodes["register-staged-flow"]
    staged_registration = {
        "schema_version": 1,
        "target_flow_id": decision["dataset_id"],
        "target_flow_name": plan["name_plan"]["base_name"],
        "staged_flow_sha256": _sha256(staged_bytes),
        "local_staging_only": True,
        "catalog_entity_created": False,
        "remote_write_executed": False,
        "support_reference_local_entity_verified": False,
        "unit_group_identity_known": False,
        "publish_ready": False,
    }
    _write_json(root / "verification/staged-registration.json", staged_registration)
    trace.append(
        _trace_row(
            node,
            status="completed",
            output_sha256=_sha256(staged_bytes),
        )
    )
    return {
        "schema_version": 1,
        "slug": slug,
        "target_flow_id": decision["dataset_id"],
        "target_flow_name": plan["name_plan"]["base_name"],
        "status": "staged_flow_native_gates_passed",
        "native_invocation_count": native_count,
        "completed_stage_count": len(trace),
        "staged_flow_path": f"outputs/flows/{slug}.json",
        "staged_flow_sha256": _sha256(staged_bytes),
        "staged_flow_bytes": len(staged_bytes),
        "classification_leaf_code": decision["code"],
        "flow_property_id": assertion["flow_property_id"],
        "reference_unit": assertion["reference_unit"],
        "validation_valid_count": 1,
        "qa_blocker_count": 0,
        "save_draft_prepared_count": 1,
        "save_draft_executed_count": 0,
        "catalog_entity_created": False,
        "remote_write_executed": False,
        "trace": trace,
        "staged_flow": classified,
    }


def _raw_manifest(root: Path) -> List[JsonObject]:
    result = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        content = path.read_bytes()
        result.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": _sha256(content),
                "bytes": len(content),
            }
        )
    return result


def _raw_archive(root: Path, manifest: Sequence[Mapping[str, object]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for row in manifest:
            relative = _text(row.get("path"), "raw manifest path")
            content = (root / relative).read_bytes()
            info = tarfile.TarInfo(relative)
            info.size = len(content)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def execute_uslci_reference_flow_authoring_plan(
    *,
    plan_report_bytes: bytes,
    plan_report_binding: Mapping[str, object],
    plan_index_bytes: bytes,
    plan_index_binding: Mapping[str, object],
    execution_dag_bytes: bytes,
    execution_dag_binding: Mapping[str, object],
    classifications_bytes: bytes,
    classifications_binding: Mapping[str, object],
    support_assertions_bytes: bytes,
    support_assertions_binding: Mapping[str, object],
    downstream_patches_bytes: bytes,
    downstream_patches_binding: Mapping[str, object],
    high_voltage_flow_plan_bytes: bytes,
    high_voltage_flow_plan_binding: Mapping[str, object],
    power_plant_construction_flow_plan_bytes: bytes,
    power_plant_construction_flow_plan_binding: Mapping[str, object],
    runtime_report_bytes: bytes,
    runtime_report_binding: Mapping[str, object],
    runtime_manifest_bytes: bytes,
    runtime_manifest_binding: Mapping[str, object],
    native_node_path: Path,
    native_cli_entry_path: Path,
    out_dir: Path,
) -> JsonObject:
    """Execute both exact branches and write compact replay artifacts."""

    validated = _validate_inputs(
        plan_report_bytes=plan_report_bytes,
        plan_report_binding=plan_report_binding,
        plan_index_bytes=plan_index_bytes,
        plan_index_binding=plan_index_binding,
        execution_dag_bytes=execution_dag_bytes,
        execution_dag_binding=execution_dag_binding,
        classifications_bytes=classifications_bytes,
        classifications_binding=classifications_binding,
        support_assertions_bytes=support_assertions_bytes,
        support_assertions_binding=support_assertions_binding,
        downstream_patches_bytes=downstream_patches_bytes,
        downstream_patches_binding=downstream_patches_binding,
        high_voltage_flow_plan_bytes=high_voltage_flow_plan_bytes,
        high_voltage_flow_plan_binding=high_voltage_flow_plan_binding,
        power_plant_construction_flow_plan_bytes=power_plant_construction_flow_plan_bytes,
        power_plant_construction_flow_plan_binding=power_plant_construction_flow_plan_binding,
        runtime_report_bytes=runtime_report_bytes,
        runtime_report_binding=runtime_report_binding,
        runtime_manifest_bytes=runtime_manifest_bytes,
        runtime_manifest_binding=runtime_manifest_binding,
        native_node_path=native_node_path,
        native_cli_entry_path=native_cli_entry_path,
    )
    output = Path(out_dir)
    outputs = output / "outputs"
    raw_root = output / "raw"
    outputs.mkdir(parents=True, exist_ok=True)
    raw_root.mkdir(parents=True, exist_ok=True)
    results = []
    failures = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {}
        for slug, spec in FLOW_SPECS.items():
            future = executor.submit(
                _execute_branch,
                slug=slug,
                plan=validated["flow_plans"][slug],
                plan_bytes=validated["flow_plan_bytes"][slug],
                decision=validated["decisions"][spec["target_flow_id"]],
                assertion=validated["assertions"][spec["target_flow_id"]],
                dag=validated["dag"],
                native_node_path=native_node_path,
                native_cli_entry_path=native_cli_entry_path,
                raw_root=raw_root,
                staged_output=outputs / "flows" / f"{slug}.json",
            )
            futures[future] = slug
        for future in as_completed(futures):
            slug = futures[future]
            try:
                results.append(future.result())
            except Exception as error:  # branch diagnostics are part of the contract
                failures.append(
                    {
                        "schema_version": 1,
                        "slug": slug,
                        "target_flow_id": FLOW_SPECS[slug]["target_flow_id"],
                        "status": "branch_failed",
                        "error_type": type(error).__name__,
                        "error": str(error),
                        "staged_flow_registered": False,
                        "downstream_transition_released": False,
                    }
                )
    results.sort(key=lambda row: list(FLOW_SPECS).index(str(row["slug"])))
    failures.sort(key=lambda row: list(FLOW_SPECS).index(str(row["slug"])))
    trace = []
    staged_flows = []
    for row in results:
        trace.extend(row.pop("trace"))
        staged_flows.append(row.pop("staged_flow"))
    if not failures:
        final_node = next(
            row
            for row in validated["dag"]["nodes"]
            if row.get("node_id") == "authoring-plan:record-project-result"
        )
        aggregate_digest = _sha256(
            _json_bytes(
                {
                    "plan_version": validated["plan_version"],
                    "staged_flows": [row["staged_flow_sha256"] for row in results],
                }
            )
        )
        trace.append(
            _trace_row(
                final_node,
                status="completed",
                output_sha256=aggregate_digest,
            )
        )
    trace_order = {
        row["node_id"]: index
        for index, row in enumerate(validated["dag"]["nodes"])
    }
    trace.sort(key=lambda row: trace_order.get(str(row["node_id"]), 10_000))
    successful_targets = {row["target_flow_id"] for row in results}
    transitions = []
    for patch_row in validated["patches"]:
        if patch_row.get("target_flow_id") not in successful_targets:
            continue
        patch_required = patch_row.get("patch_required") is True
        transitions.append(
            {
                "schema_version": 1,
                "downstream_action_key": patch_row["downstream_action_key"],
                "downstream_action_kind": patch_row["downstream_action_kind"],
                "target_flow_id": patch_row["target_flow_id"],
                "prior_disposition": patch_row["disposition"],
                "staged_flow_available": True,
                "transition": (
                    "ready_for_recompile"
                    if patch_required
                    else "released_to_local_validation"
                ),
                "downstream_execution_ready": not patch_required,
                "catalog_entity_available": False,
                "remote_write_executed": False,
            }
        )
    transitions.sort(key=lambda row: str(row["downstream_action_key"]))

    trace_path = outputs / "uslci-reference-flow-authoring-execution-trace.jsonl"
    results_path = outputs / "uslci-reference-flow-authoring-execution-branches.jsonl"
    transitions_path = outputs / "uslci-reference-flow-downstream-gate-transitions.jsonl"
    failures_path = outputs / "uslci-reference-flow-authoring-execution-failures.jsonl"
    _write_jsonl(trace_path, trace)
    _write_jsonl(results_path, results)
    _write_jsonl(transitions_path, transitions)
    _write_jsonl(failures_path, failures)
    manifest = _raw_manifest(raw_root)
    manifest_path = outputs / "uslci-reference-flow-authoring-action-output-manifest.jsonl"
    _write_jsonl(manifest_path, manifest)
    archive_bytes = _raw_archive(raw_root, manifest)
    archive_path = outputs / "uslci-reference-flow-authoring-raw-execution.tar"
    archive_path.write_bytes(archive_bytes)
    output_bindings = {
        "execution_trace": _artifact_binding(
            "outputs/uslci-reference-flow-authoring-execution-trace.jsonl",
            trace_path.read_bytes(),
        ),
        "branch_results": _artifact_binding(
            "outputs/uslci-reference-flow-authoring-execution-branches.jsonl",
            results_path.read_bytes(),
        ),
        "downstream_transitions": _artifact_binding(
            "outputs/uslci-reference-flow-downstream-gate-transitions.jsonl",
            transitions_path.read_bytes(),
        ),
        "action_output_manifest": _artifact_binding(
            "outputs/uslci-reference-flow-authoring-action-output-manifest.jsonl",
            manifest_path.read_bytes(),
        ),
        "failures": _artifact_binding(
            "outputs/uslci-reference-flow-authoring-execution-failures.jsonl",
            failures_path.read_bytes(),
        ),
        "raw_archive": _artifact_binding(
            "outputs/uslci-reference-flow-authoring-raw-execution.tar",
            archive_bytes,
        ),
    }
    for slug in FLOW_SPECS:
        path = outputs / "flows" / f"{slug}.json"
        if path.is_file():
            output_bindings[f"staged_flow_{slug.replace('-', '_')}"] = _artifact_binding(
                f"outputs/flows/{slug}.json", path.read_bytes()
            )
    successful = not failures and len(results) == 2 and len(trace) == 17
    execution_version = _sha256(
        _json_bytes(
            {
                "logic_version": LOGIC_VERSION,
                "plan_version": validated["plan_version"],
                "runtime_tree_sha256": validated["runtime_tree_sha256"],
                "products": output_bindings,
            }
        )
    )[:16]
    report = {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "status": (
            "uslci_reference_flow_authoring_execution_completed"
            if successful
            else "uslci_reference_flow_authoring_execution_failed"
        ),
        "scope_id": validated["report"].get("scope_id"),
        "revision": validated["report"].get("revision"),
        "plan_version": validated["plan_version"],
        "execution_version": execution_version,
        "runtime_tree_sha256": validated["runtime_tree_sha256"],
        "requested_flow_count": 2,
        "completed_flow_count": len(results),
        "failed_flow_count": len(failures),
        "native_invocation_executed_count": sum(
            int(row["native_invocation_count"]) for row in results
        ),
        "native_flow_materialization_executed_count": len(results),
        "controlled_classification_apply_executed_count": len(results),
        "flow_validation_executed_count": len(results),
        "flow_qa_executed_count": len(results),
        "save_draft_dry_run_executed_count": len(results),
        "staged_flow_registration_count": len(results),
        "catalog_flow_creation_executed_count": 0,
        "flow_creation_executed_count": 0,
        "remote_write_executed_count": 0,
        "publication_executed_count": 0,
        "process_identity_preflight_executed_count": 0,
        "process_build_plan_validation_executed_count": 0,
        "process_creation_executed_count": 0,
        "model_connection_executed_count": 0,
        "model_mutation_executed_count": 0,
        "external_write_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "network_read_executed_count": 0,
        "maximum_native_concurrency": 2,
        "execution_trace_node_count": len(trace),
        "execution_trace_completed_node_count": sum(
            row.get("status") == "completed" for row in trace
        ),
        "downstream_transition_count": len(transitions),
        "downstream_ready_for_recompile_count": sum(
            row["transition"] == "ready_for_recompile" for row in transitions
        ),
        "downstream_released_to_local_validation_count": sum(
            row["transition"] == "released_to_local_validation"
            for row in transitions
        ),
        "support_reference_local_entity_verified_count": 0,
        "unit_group_identity_known_count": 0,
        "publish_ready_flow_count": 0,
        "raw_action_output_count": len(manifest),
        "artifact_bindings": output_bindings,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "next_action": (
            "Recompile the six high-voltage name-bound Process actions and release the two construction actions to exact local validation."
            if successful
            else "Diagnose failed authoring branches and start a new immutable execution attempt."
        ),
        "routing": {
            "next_internal_route": (
                NEXT_ROUTE
                if successful
                else "retry-uslci-native-reference-flow-authoring-plan-failures"
            ),
            "pause_internal_work": False,
        },
        "generated_at_utc": datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
    }
    report_path = outputs / "uslci-reference-flow-authoring-execution.json"
    _write_json(report_path, report)
    result = {
        "report": report,
        "branch_results": results,
        "execution_trace": trace,
        "downstream_transitions": transitions,
        "failures": failures,
        "staged_flows": staged_flows,
        "raw_manifest": manifest,
        "output_paths": {
            "report": report_path,
            "execution_trace": trace_path,
            "branch_results": results_path,
            "downstream_transitions": transitions_path,
            "action_output_manifest": manifest_path,
            "failures": failures_path,
            "raw_archive": archive_path,
            **{
                f"staged_flow_{slug.replace('-', '_')}": outputs
                / "flows"
                / f"{slug}.json"
                for slug in FLOW_SPECS
                if (outputs / "flows" / f"{slug}.json").is_file()
            },
        },
    }
    if not successful:
        raise RuntimeError(
            f"reference Flow authoring branch failure count: {len(failures)}"
        )
    return result


def _read_binding(path: Path) -> JsonObject:
    return _parse_json(path.read_bytes(), f"binding {path}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Execute the exact saved USLCI reference-Flow authoring plan."
    )
    names = (
        "plan-report",
        "plan-index",
        "execution-dag",
        "classifications",
        "support-assertions",
        "downstream-patches",
        "high-voltage-flow-plan",
        "power-plant-construction-flow-plan",
        "runtime-report",
        "runtime-manifest",
    )
    for name in names:
        parser.add_argument(f"--{name}", type=Path, required=True)
        parser.add_argument(f"--{name}-binding", type=Path, required=True)
    parser.add_argument("--native-node", type=Path, required=True)
    parser.add_argument("--native-cli-entry", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    kwargs = {}
    for name in (
        "plan_report",
        "plan_index",
        "execution_dag",
        "classifications",
        "support_assertions",
        "downstream_patches",
        "high_voltage_flow_plan",
        "power_plant_construction_flow_plan",
        "runtime_report",
        "runtime_manifest",
    ):
        path = getattr(args, name)
        kwargs[f"{name}_bytes"] = path.read_bytes()
        kwargs[f"{name}_binding"] = _read_binding(getattr(args, f"{name}_binding"))
    execute_uslci_reference_flow_authoring_plan(
        **kwargs,
        native_node_path=args.native_node,
        native_cli_entry_path=args.native_cli_entry,
        out_dir=args.out_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
