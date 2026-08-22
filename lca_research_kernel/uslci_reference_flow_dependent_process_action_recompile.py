"""Reconcile Process actions after local USLCI reference Flow authoring."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


JsonObject = Dict[str, object]
CONTRACT = (
    "lca_research_kernel."
    "uslci_reference_flow_dependent_process_action_recompile/v1"
)
IDENTITY_SUCCESSOR_CONTRACT = (
    "lca_research_kernel.uslci_process_identity_preflight_action/v2"
)
BUILD_SUCCESSOR_CONTRACT = (
    "lca_research_kernel.uslci_process_build_plan_template/v2"
)
LOGIC_VERSION = "2026-07-17.1"
EXPECTED_ROUTE = "recompile-uslci-reference-flow-dependent-process-actions"
NEXT_ROUTE = "execute-uslci-native-slice-process-action-plan"
HIGH_FLOW_ID = "66937f42-97d5-5822-a060-8d711ad64677"
HIGH_OLD_NAME = "Electricity, AC, 2300-7650 V, USLCI US 2023"
HIGH_NEW_NAME = "Electricity, AC, 2300-7650 V"
HIGH_PROPERTY_ID = "f6811440-ee37-11de-8a39-0800200c9a66"
CONSTRUCTION_FLOW_ID = "2d727759-6023-5e1a-bb3a-286d9a9d969c"
CONSTRUCTION_NAME = "power plant construction"
CONSTRUCTION_PROPERTY_ID = "01846770-4cfe-4a25-8ad9-919d8d378345"
REUSE_FLOW_ID = "afc21f75-af0f-528a-8aa7-f31adefe0cd7"
EXPECTED_INPUT_NAMES = (
    "execution_report",
    "downstream_transitions",
    "staged_high_voltage_flow",
    "staged_construction_flow",
    "authoring_plan_report",
    "downstream_patches",
    "preflight_report",
    "process_identity_actions",
    "process_build_plan_templates",
    "action_graph",
    "runtime_report",
    "runtime_manifest",
)


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
        raise ValueError(f"{label} is invalid JSON") from error


def _load_jsonl(content: bytes, label: str) -> List[JsonObject]:
    try:
        rows = [
            _object(json.loads(line), label)
            for line in content.decode("utf-8").splitlines()
            if line.strip()
        ]
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is invalid JSONL") from error
    if not rows:
        raise ValueError(f"{label} is empty")
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
    return {
        "path": row["path"],
        "sha256": row["sha256"],
        "bytes": row["bytes"],
    }


def _assert_nested_binding(
    report: JsonObject, key: str, binding: JsonObject, label: str
) -> None:
    nested = _object(
        _object(report.get("artifact_bindings"), f"{label} artifact bindings").get(
            key
        ),
        f"{label} {key} binding",
    )
    if (
        nested.get("sha256") != binding["sha256"]
        or nested.get("bytes") != binding["bytes"]
    ):
        raise ValueError(f"{label} {key} nested binding drifted")


def input_version_for_bindings(bindings: Sequence[JsonObject]) -> str:
    return _sha(_bytes({"logic": LOGIC_VERSION, "inputs": bindings}))[:16]


def _first_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        raw = value.get("#text")
        return raw if isinstance(raw, str) else ""
    if isinstance(value, list) and value:
        return _first_text(value[0])
    return ""


def _one(value: object, label: str) -> JsonObject:
    if isinstance(value, list):
        if len(value) != 1:
            raise ValueError(f"{label} must contain exactly one row")
        return _object(value[0], label)
    return _object(value, label)


def _verify_staged_flow(
    content: bytes,
    *,
    flow_id: str,
    name: str,
    property_id: str,
    property_name: str,
    label: str,
) -> JsonObject:
    flow = _load_json(content, label)
    root = _object(flow.get("flowDataSet"), f"{label} root")
    information = _object(root.get("flowInformation"), f"{label} information")
    dataset = _object(
        information.get("dataSetInformation"), f"{label} dataset information"
    )
    quantitative = _object(
        information.get("quantitativeReference"), f"{label} quantitative reference"
    )
    properties = _object(root.get("flowProperties"), f"{label} properties")
    prop = _one(properties.get("flowProperty"), f"{label} reference property")
    reference = _object(
        prop.get("referenceToFlowPropertyDataSet"), f"{label} property reference"
    )
    modelling = _object(
        root.get("modellingAndValidation"), f"{label} modelling and validation"
    )
    method = _object(modelling.get("LCIMethod"), f"{label} LCI method")
    names = _object(dataset.get("name"), f"{label} name")
    if (
        dataset.get("common:UUID") != flow_id
        or _first_text(names.get("baseName")) != name
        or quantitative.get("referenceToReferenceFlowProperty") != "0"
        or prop.get("@dataSetInternalID") != "0"
        or reference.get("@refObjectId") != property_id
        or _first_text(reference.get("common:shortDescription")) != property_name
        or str(prop.get("meanValue")) not in {"1", "1.0"}
        or method.get("typeOfDataSet") != "Product flow"
    ):
        raise ValueError(f"{label} identity, name, type or property drifted")
    return {
        "flow_id": flow_id,
        "name": name,
        "property_id": property_id,
        "property_name": property_name,
        "sha256": _sha(content),
    }


def _value_at(root: JsonObject, path: str) -> object:
    value: object = root
    for segment in path.split("."):
        value = _object(value, f"parent of {path}").get(segment)
    return value


def _set_value(root: JsonObject, path: str, old: object, new: object) -> None:
    segments = path.split(".")
    current = root
    for segment in segments[:-1]:
        child = current.get(segment)
        if not isinstance(child, dict):
            raise ValueError(f"parent of {path} must be an object")
        current = child
    if current.get(segments[-1]) != old:
        raise ValueError(f"patch old value drifted at {path}")
    current[segments[-1]] = new


def _key_for_action(
    *,
    kind: str,
    predecessor_key: str,
    predecessor_sha256: str,
    desired_action: JsonObject,
    staged_flow_sha256: str,
    runtime_tree_sha256: str,
) -> str:
    return _sha(
        _bytes(
            {
                "logic": LOGIC_VERSION,
                "kind": kind,
                "predecessor_action_key": predecessor_key,
                "predecessor_action_sha256": predecessor_sha256,
                "desired_action": desired_action,
                "staged_flow_sha256": staged_flow_sha256,
                "runtime_tree_sha256": runtime_tree_sha256,
            }
        )
    )[:16]


def _action_key(row: JsonObject, kind: str) -> str:
    field = "action_key" if kind == "process_identity_preflight" else "build_plan_action_key"
    return _text(row.get(field), f"{kind} action key")


def _target_flow_id(row: JsonObject) -> str:
    target = _object(row.get("target"), "Process action target")
    return _text(target.get("reference_flow_id"), "Process action reference Flow ID")


def _validate_original_graph(
    graph: JsonObject,
    identities: Sequence[JsonObject],
    templates: Sequence[JsonObject],
) -> None:
    if graph.get("ambient_workspace_inputs_allowed") is not False:
        raise ValueError("original action graph permits ambient workspace inputs")
    nodes = {
        (_text(row.get("action_key"), "graph action key"), _text(row.get("kind"), "graph kind"))
        for raw in _array(graph.get("nodes"), "graph nodes")
        for row in [_object(raw, "graph node")]
    }
    edges = {
        (
            _text(row.get("from_action_key"), "edge from"),
            _text(row.get("to_action_key"), "edge to"),
            _text(row.get("dependency"), "edge dependency"),
        )
        for raw in _array(graph.get("edges"), "graph edges")
        for row in [_object(raw, "graph edge")]
    }
    for identity, template in zip(
        sorted(identities, key=lambda row: row["process_index"]),
        sorted(templates, key=lambda row: row["process_index"]),
    ):
        identity_key = _action_key(identity, "process_identity_preflight")
        build_key = _action_key(template, "process_build_plan_validate")
        flow_key = _text(
            identity.get("depends_on_flow_action_key"), "identity Flow dependency"
        )
        required_nodes = {
            (identity_key, "process_identity_preflight"),
            (build_key, "process_build_plan_validate"),
        }
        required_edges = {
            (flow_key, identity_key, "mapped_quantitative_reference_flow_identity"),
            (identity_key, build_key, "process_identity_decision"),
            (flow_key, build_key, "reference_flow_identity_decision"),
        }
        if not required_nodes <= nodes or not required_edges <= edges:
            raise ValueError("original Process action graph dependency drifted")


def _verify_reports_and_inputs(
    *,
    bindings: Dict[str, JsonObject],
    contents: Dict[str, bytes],
) -> JsonObject:
    execution = _load_json(contents["execution_report"], "authoring execution report")
    authoring = _load_json(contents["authoring_plan_report"], "authoring plan report")
    preflight = _load_json(contents["preflight_report"], "native preflight report")
    runtime = _load_json(contents["runtime_report"], "portable runtime report")
    manifest = _load_json(contents["runtime_manifest"], "portable runtime manifest")
    if (
        execution.get("contract")
        != "lca_research_kernel.uslci_reference_flow_authoring_execution/v1"
        or execution.get("status")
        != "uslci_reference_flow_authoring_execution_completed"
        or execution.get("completed_flow_count") != 2
        or execution.get("failed_flow_count") != 0
        or execution.get("downstream_ready_for_recompile_count") != 6
        or execution.get("downstream_released_to_local_validation_count") != 2
        or execution.get("routing", {}).get("next_internal_route") != EXPECTED_ROUTE
    ):
        raise ValueError("authoring execution contract or route drifted")
    if (
        authoring.get("contract")
        != "lca_research_kernel.uslci_reference_flow_authoring_plan/v1"
        or authoring.get("status")
        != "uslci_reference_flow_authoring_plan_compiled"
        or authoring.get("downstream_name_patch_count") != 6
        or authoring.get("downstream_release_only_count") != 2
    ):
        raise ValueError("authoring plan contract drifted")
    if (
        preflight.get("contract")
        != "lca_research_kernel.uslci_native_slice_preflight_plan/v1"
        or preflight.get("status") != "uslci_native_slice_preflight_plan_compiled"
        or preflight.get("process_identity_action_count") != 5
        or preflight.get("process_build_plan_template_count") != 5
    ):
        raise ValueError("native preflight plan contract drifted")
    runtime_tree = _text(runtime.get("tree_sha256"), "runtime tree SHA-256")
    if (
        runtime.get("status") != "portable_native_tool_runtime_closure_verified"
        or runtime.get("runtime_content_closed") is not True
        or runtime.get("same_platform_relocation_proven") is not True
        or manifest.get("tree_sha256") != runtime_tree
        or preflight.get("runtime_tree_sha256") != runtime_tree
        or execution.get("runtime_tree_sha256") != runtime_tree
    ):
        raise ValueError("portable runtime identity or closure drifted")
    _assert_nested_binding(
        execution,
        "downstream_transitions",
        bindings["downstream_transitions"],
        "execution report",
    )
    _assert_nested_binding(
        execution,
        "staged_flow_high_voltage_electricity",
        bindings["staged_high_voltage_flow"],
        "execution report",
    )
    _assert_nested_binding(
        execution,
        "staged_flow_power_plant_construction",
        bindings["staged_construction_flow"],
        "execution report",
    )
    _assert_nested_binding(
        authoring,
        "downstream_patches",
        bindings["downstream_patches"],
        "authoring plan",
    )
    for key in (
        "process_identity_actions",
        "process_build_plan_templates",
        "action_graph",
    ):
        _assert_nested_binding(preflight, key, bindings[key], "native preflight plan")
    candidate_bindings = [
        _object(row, "native preflight input binding")
        for row in _array(preflight.get("input_bindings"), "native preflight inputs")
        if isinstance(row, dict)
        and row.get("sha256")
        == _object(
            _load_jsonl(contents["process_identity_actions"], "identity actions")[0],
            "identity action",
        ).get("candidate_catalog_sha256")
    ]
    if len(candidate_bindings) != 1:
        raise ValueError("local Process candidate catalog binding is missing or ambiguous")
    candidate = candidate_bindings[0]
    if not isinstance(candidate.get("path"), str) or not isinstance(
        candidate.get("bytes"), int
    ):
        raise ValueError("local Process candidate catalog binding is malformed")
    return {
        "execution": execution,
        "authoring": authoring,
        "preflight": preflight,
        "runtime": runtime,
        "runtime_manifest": manifest,
        "runtime_tree_sha256": runtime_tree,
        "candidate_catalog_binding": {
            "path": candidate["path"],
            "sha256": candidate["sha256"],
            "bytes": candidate["bytes"],
        },
    }


def _verify_patch_and_transition_sets(
    patches: Sequence[JsonObject],
    transitions: Sequence[JsonObject],
    actions: Dict[Tuple[str, str], JsonObject],
) -> None:
    if len(patches) != 8 or len(transitions) != 8:
        raise ValueError("exactly eight patch and transition rows are required")
    patch_keys = set()
    for patch in patches:
        kind = _text(patch.get("downstream_action_kind"), "patch action kind")
        key = _text(patch.get("downstream_action_key"), "patch action key")
        if (key, kind) not in actions or (key, kind) in patch_keys:
            raise ValueError("patch target action is missing, duplicated or wrong-kind")
        patch_keys.add((key, kind))
        source = actions[(key, kind)]
        if patch.get("source_action_sha256") != _sha(_bytes(source)):
            raise ValueError("patch source action hash drifted")
        target_flow_id = _target_flow_id(source)
        if patch.get("target_flow_id") != target_flow_id:
            raise ValueError("patch target Flow drifted")
        fields = [_object(row, "field patch") for row in _array(patch.get("field_patches"), "field patches")]
        if target_flow_id == HIGH_FLOW_ID:
            allowed = (
                {"target.reference_flow_name", "request.target.reference_flow_name"}
                if kind == "process_identity_preflight"
                else {
                    "target.reference_flow_name",
                    "quantitative_reference_plan.reference_flow_name",
                    "unit_of_analysis.reference_flow.name",
                }
            )
            if (
                patch.get("patch_required") is not True
                or patch.get("disposition")
                != "invalidate_and_recompile_name_bound_action"
                or {row.get("path") for row in fields} != allowed
                or any(
                    row.get("old") != HIGH_OLD_NAME
                    or row.get("new") != HIGH_NEW_NAME
                    for row in fields
                )
            ):
                raise ValueError("high-voltage patch field ownership drifted")
        elif target_flow_id == CONSTRUCTION_FLOW_ID:
            if (
                patch.get("patch_required") is not False
                or patch.get("disposition") != "release_after_entity_registered"
                or fields
            ):
                raise ValueError("construction release-only action drifted")
        else:
            raise ValueError("patch targets an unexpected reference Flow")
    transition_by_key = {
        (
            _text(row.get("downstream_action_key"), "transition action key"),
            _text(row.get("downstream_action_kind"), "transition action kind"),
        ): row
        for row in transitions
    }
    if set(transition_by_key) != patch_keys:
        raise ValueError("transition and patch action sets differ")
    for patch in patches:
        key = (patch["downstream_action_key"], patch["downstream_action_kind"])
        transition = transition_by_key[key]
        expected = (
            "ready_for_recompile"
            if patch["patch_required"]
            else "released_to_local_validation"
        )
        if (
            transition.get("target_flow_id") != patch.get("target_flow_id")
            or transition.get("prior_disposition") != patch.get("disposition")
            or transition.get("staged_flow_available") is not True
            or transition.get("catalog_entity_available") is not False
            or transition.get("transition") != expected
            or transition.get("remote_write_executed") is not False
        ):
            raise ValueError("downstream transition semantics drifted")


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


def compile_uslci_reference_flow_dependent_process_actions(
    *,
    execution_report_bytes: bytes,
    execution_report_binding: JsonObject,
    downstream_transitions_bytes: bytes,
    downstream_transitions_binding: JsonObject,
    staged_high_voltage_flow_bytes: bytes,
    staged_high_voltage_flow_binding: JsonObject,
    staged_construction_flow_bytes: bytes,
    staged_construction_flow_binding: JsonObject,
    authoring_plan_report_bytes: bytes,
    authoring_plan_report_binding: JsonObject,
    downstream_patches_bytes: bytes,
    downstream_patches_binding: JsonObject,
    preflight_report_bytes: bytes,
    preflight_report_binding: JsonObject,
    process_identity_actions_bytes: bytes,
    process_identity_actions_binding: JsonObject,
    process_build_plan_templates_bytes: bytes,
    process_build_plan_templates_binding: JsonObject,
    action_graph_bytes: bytes,
    action_graph_binding: JsonObject,
    runtime_report_bytes: bytes,
    runtime_report_binding: JsonObject,
    runtime_manifest_bytes: bytes,
    runtime_manifest_binding: JsonObject,
    out_dir: Path,
) -> JsonObject:
    arguments = locals().copy()
    contents = {name: arguments[f"{name}_bytes"] for name in EXPECTED_INPUT_NAMES}
    raw_bindings = {
        name: arguments[f"{name}_binding"] for name in EXPECTED_INPUT_NAMES
    }
    bindings = {
        name: _binding(raw_bindings[name], contents[name], name)
        for name in EXPECTED_INPUT_NAMES
    }
    verified = _verify_reports_and_inputs(bindings=bindings, contents=contents)
    high_flow = _verify_staged_flow(
        staged_high_voltage_flow_bytes,
        flow_id=HIGH_FLOW_ID,
        name=HIGH_NEW_NAME,
        property_id=HIGH_PROPERTY_ID,
        property_name="Energy",
        label="staged high-voltage Flow",
    )
    construction_flow = _verify_staged_flow(
        staged_construction_flow_bytes,
        flow_id=CONSTRUCTION_FLOW_ID,
        name=CONSTRUCTION_NAME,
        property_id=CONSTRUCTION_PROPERTY_ID,
        property_name="Number",
        label="staged construction Flow",
    )
    identities = _load_jsonl(process_identity_actions_bytes, "Process identity actions")
    templates = _load_jsonl(
        process_build_plan_templates_bytes, "Process build-plan templates"
    )
    if len(identities) != 5 or len(templates) != 5:
        raise ValueError("exactly five Process identity/build action pairs are required")
    if sorted(row.get("process_index") for row in identities) != [1, 2, 3, 4, 5]:
        raise ValueError("Process identity action indices drifted")
    if sorted(row.get("process_index") for row in templates) != [1, 2, 3, 4, 5]:
        raise ValueError("Process build action indices drifted")
    identity_by_index = {int(row["process_index"]): row for row in identities}
    template_by_index = {int(row["process_index"]): row for row in templates}
    for index in range(1, 6):
        identity = identity_by_index[index]
        template = template_by_index[index]
        if (
            identity.get("source_process_id") != template.get("source_process_id")
            or identity.get("action_key")
            != template.get("depends_on_process_action_key")
            or identity.get("depends_on_flow_action_key")
            != template.get("depends_on_flow_action_key")
            or _target_flow_id(identity) != _target_flow_id(template)
        ):
            raise ValueError("Process identity/build action pair drifted")
    graph = _load_json(action_graph_bytes, "native preflight action graph")
    _validate_original_graph(graph, identities, templates)
    patches = _load_jsonl(downstream_patches_bytes, "downstream patches")
    transitions = _load_jsonl(
        downstream_transitions_bytes, "downstream transitions"
    )
    actions: Dict[Tuple[str, str], JsonObject] = {}
    for row in identities:
        actions[(_action_key(row, "process_identity_preflight"), "process_identity_preflight")] = row
    for row in templates:
        actions[(_action_key(row, "process_build_plan_validate"), "process_build_plan_validate")] = row
    _verify_patch_and_transition_sets(patches, transitions, actions)
    target_distribution = {
        flow_id: sum(1 for row in identities if _target_flow_id(row) == flow_id)
        for flow_id in {HIGH_FLOW_ID, CONSTRUCTION_FLOW_ID, REUSE_FLOW_ID}
    }
    if target_distribution != {
        HIGH_FLOW_ID: 3,
        CONSTRUCTION_FLOW_ID: 1,
        REUSE_FLOW_ID: 1,
    }:
        raise ValueError("five-Process reference Flow distribution drifted")
    desired_generation = _sha(
        _bytes(
            {
                "logic": LOGIC_VERSION,
                "inputs": [bindings[name] for name in EXPECTED_INPUT_NAMES],
                "runtime_tree_sha256": verified["runtime_tree_sha256"],
                "staged_flows": [high_flow, construction_flow],
            }
        )
    )[:16]
    patch_by_action = {
        (row["downstream_action_key"], row["downstream_action_kind"]): row
        for row in patches
    }
    recompiled_identities: List[JsonObject] = []
    recompiled_templates: List[JsonObject] = []
    released: List[JsonObject] = []
    carried: List[JsonObject] = []
    reconciliation: List[JsonObject] = []
    desired_identity_by_index: Dict[int, JsonObject] = {}
    desired_template_by_index: Dict[int, JsonObject] = {}
    all_old_keys = {
        _action_key(row, "process_identity_preflight") for row in identities
    } | {_action_key(row, "process_build_plan_validate") for row in templates}
    for index in range(1, 6):
        identity = identity_by_index[index]
        template = template_by_index[index]
        flow_id = _target_flow_id(identity)
        staged_sha = (
            high_flow["sha256"]
            if flow_id == HIGH_FLOW_ID
            else construction_flow["sha256"]
            if flow_id == CONSTRUCTION_FLOW_ID
            else None
        )
        if flow_id == HIGH_FLOW_ID:
            identity_patch = patch_by_action[
                (identity["action_key"], "process_identity_preflight")
            ]
            identity_successor = copy.deepcopy(identity)
            for field in identity_patch["field_patches"]:
                _set_value(
                    identity_successor, field["path"], field["old"], field["new"]
                )
            predecessor_identity_key = identity["action_key"]
            predecessor_identity_sha = _sha(_bytes(identity))
            identity_successor["contract"] = IDENTITY_SUCCESSOR_CONTRACT
            identity_successor["predecessor_action_key"] = predecessor_identity_key
            identity_successor[
                "predecessor_action_sha256"
            ] = predecessor_identity_sha
            identity_successor["staged_reference_flow_sha256"] = staged_sha
            identity_successor["desired_generation"] = desired_generation
            identity_key_material = copy.deepcopy(identity_successor)
            identity_key_material.pop("action_key", None)
            successor_identity_key = _key_for_action(
                kind="process_identity_preflight",
                predecessor_key=predecessor_identity_key,
                predecessor_sha256=predecessor_identity_sha,
                desired_action=identity_key_material,
                staged_flow_sha256=str(staged_sha),
                runtime_tree_sha256=verified["runtime_tree_sha256"],
            )
            identity_successor["action_key"] = successor_identity_key
            template_patch = patch_by_action[
                (template["build_plan_action_key"], "process_build_plan_validate")
            ]
            template_successor = copy.deepcopy(template)
            for field in template_patch["field_patches"]:
                _set_value(
                    template_successor, field["path"], field["old"], field["new"]
                )
            predecessor_build_key = template["build_plan_action_key"]
            predecessor_build_sha = _sha(_bytes(template))
            template_successor["contract"] = BUILD_SUCCESSOR_CONTRACT
            template_successor[
                "predecessor_action_key"
            ] = predecessor_build_key
            template_successor[
                "predecessor_action_sha256"
            ] = predecessor_build_sha
            template_successor[
                "depends_on_process_action_key"
            ] = successor_identity_key
            template_successor["identity_decision"][
                "action_key"
            ] = successor_identity_key
            template_successor["staged_reference_flow_sha256"] = staged_sha
            template_successor["desired_generation"] = desired_generation
            build_key_material = copy.deepcopy(template_successor)
            build_key_material.pop("build_plan_action_key", None)
            successor_build_key = _key_for_action(
                kind="process_build_plan_validate",
                predecessor_key=predecessor_build_key,
                predecessor_sha256=predecessor_build_sha,
                desired_action=build_key_material,
                staged_flow_sha256=str(staged_sha),
                runtime_tree_sha256=verified["runtime_tree_sha256"],
            )
            template_successor["build_plan_action_key"] = successor_build_key
            recompiled_identities.append(identity_successor)
            recompiled_templates.append(template_successor)
            desired_identity_by_index[index] = identity_successor
            desired_template_by_index[index] = template_successor
            reconciliation.extend(
                [
                    {
                        "schema_version": 1,
                        "process_index": index,
                        "kind": "process_identity_preflight",
                        "target_flow_id": flow_id,
                        "predecessor_action_key": predecessor_identity_key,
                        "predecessor_action_sha256": predecessor_identity_sha,
                        "successor_action_key": successor_identity_key,
                        "staged_reference_flow_sha256": staged_sha,
                        "transition": "recompiled_successor",
                        "predecessor_retained": True,
                    },
                    {
                        "schema_version": 1,
                        "process_index": index,
                        "kind": "process_build_plan_validate",
                        "target_flow_id": flow_id,
                        "predecessor_action_key": predecessor_build_key,
                        "predecessor_action_sha256": predecessor_build_sha,
                        "successor_action_key": successor_build_key,
                        "staged_reference_flow_sha256": staged_sha,
                        "transition": "recompiled_successor",
                        "predecessor_retained": True,
                    },
                ]
            )
        else:
            desired_identity_by_index[index] = copy.deepcopy(identity)
            desired_template_by_index[index] = copy.deepcopy(template)
            transition = (
                "released_to_execution_plan"
                if flow_id == CONSTRUCTION_FLOW_ID
                else "carried_forward_ready"
            )
            target = released if flow_id == CONSTRUCTION_FLOW_ID else carried
            for row, kind in (
                (identity, "process_identity_preflight"),
                (template, "process_build_plan_validate"),
            ):
                key = _action_key(row, kind)
                target.append(
                    {
                        "schema_version": 1,
                        "process_index": index,
                        "kind": kind,
                        "action_key": key,
                        "action_sha256": _sha(_bytes(row)),
                        "target_flow_id": flow_id,
                        "staged_reference_flow_sha256": staged_sha,
                        "transition": transition,
                        "content_changed": False,
                    }
                )
                if flow_id == CONSTRUCTION_FLOW_ID:
                    reconciliation.append(
                        {
                            "schema_version": 1,
                            "process_index": index,
                            "kind": kind,
                            "target_flow_id": flow_id,
                            "predecessor_action_key": key,
                            "predecessor_action_sha256": _sha(_bytes(row)),
                            "successor_action_key": key,
                            "staged_reference_flow_sha256": staged_sha,
                            "transition": transition,
                            "predecessor_retained": True,
                        }
                    )
    successor_keys = {
        row["action_key"] for row in recompiled_identities
    } | {row["build_plan_action_key"] for row in recompiled_templates}
    if len(successor_keys) != 6 or successor_keys & all_old_keys:
        raise ValueError("successor action key collision or unexpected reuse")
    if len(reconciliation) != 8 or len(released) != 2 or len(carried) != 2:
        raise RuntimeError("Process action reconciliation count invariant failed")
    execution_nodes = []
    execution_edges = []
    for index in range(1, 6):
        identity = desired_identity_by_index[index]
        template = desired_template_by_index[index]
        identity_key = _action_key(identity, "process_identity_preflight")
        build_key = _action_key(template, "process_build_plan_validate")
        flow_id = _target_flow_id(identity)
        state = (
            "successor_compiled"
            if flow_id == HIGH_FLOW_ID
            else "released"
            if flow_id == CONSTRUCTION_FLOW_ID
            else "carried_forward_ready"
        )
        common = {
            "schema_version": 1,
            "process_index": index,
            "target_process_id": _object(identity["target"], "target")["id"],
            "target_flow_id": flow_id,
            "desired_state": state,
            "remote_candidates_enabled": False,
            "remote_write_allowed": False,
            "process_materialization_allowed": False,
            "process_creation_allowed": False,
            "model_connection_allowed": False,
        }
        execution_nodes.extend(
            [
                {
                    **common,
                    "action_key": identity_key,
                    "kind": "process_identity_preflight",
                    "command": "process identity-preflight",
                    "expected_outputs": [
                        "outputs/identity-decision.json",
                        "outputs/identity-candidates.jsonl",
                        "outputs/identity-candidate-sources.json",
                    ],
                },
                {
                    **common,
                    "action_key": build_key,
                    "kind": "process_build_plan_validate",
                    "command": "process build-plan validate --report-only",
                    "expected_outputs": ["outputs/build-plan-gate-report.json"],
                },
            ]
        )
        execution_edges.append(
            {
                "from_action_key": identity_key,
                "to_action_key": build_key,
                "dependency": "accepted_native_process_identity_decision",
            }
        )
    execution_plan = {
        "schema_version": 1,
        "contract": "lca_research_kernel.uslci_native_slice_process_action_plan/v1",
        "desired_generation": desired_generation,
        "runtime_tree_sha256": verified["runtime_tree_sha256"],
        "candidate_catalog_binding": verified["candidate_catalog_binding"],
        "maximum_native_concurrency": 4,
        "environment_policy": {
            "npm_offline": True,
            "remote_credentials_forwarded": False,
            "review_llm_enabled": False,
        },
        "node_count": len(execution_nodes),
        "edge_count": len(execution_edges),
        "nodes": execution_nodes,
        "edges": execution_edges,
        "native_invocation_executed_count": 0,
        "process_materialization_executed_count": 0,
        "process_creation_executed_count": 0,
        "remote_write_executed_count": 0,
    }
    execution_plan["plan_version"] = _sha(_bytes(execution_plan))[:16]
    conditions = [
        {
            "type": "Recompiled",
            "status": True,
            "count": 6,
            "observed_generation": desired_generation,
        },
        {
            "type": "Released",
            "status": True,
            "count": 2,
            "observed_generation": desired_generation,
        },
        {
            "type": "CarriedForward",
            "status": True,
            "count": 2,
            "observed_generation": desired_generation,
        },
        {
            "type": "ExecutionPlanned",
            "status": True,
            "count": 10,
            "observed_generation": desired_generation,
        },
    ]
    outputs = Path(out_dir) / "outputs"
    output_rows = {
        "recompiled_identity_actions": (
            outputs / "uslci-recompiled-process-identity-actions.jsonl",
            _write_jsonl(
                outputs / "uslci-recompiled-process-identity-actions.jsonl",
                recompiled_identities,
            ),
        ),
        "recompiled_build_templates": (
            outputs / "uslci-recompiled-process-build-plan-templates.jsonl",
            _write_jsonl(
                outputs / "uslci-recompiled-process-build-plan-templates.jsonl",
                recompiled_templates,
            ),
        ),
        "released_actions": (
            outputs / "uslci-released-process-actions.jsonl",
            _write_jsonl(outputs / "uslci-released-process-actions.jsonl", released),
        ),
        "carried_actions": (
            outputs / "uslci-carried-forward-process-actions.jsonl",
            _write_jsonl(
                outputs / "uslci-carried-forward-process-actions.jsonl", carried
            ),
        ),
        "reconciliation": (
            outputs / "uslci-reference-flow-process-action-reconciliation.jsonl",
            _write_jsonl(
                outputs / "uslci-reference-flow-process-action-reconciliation.jsonl",
                reconciliation,
            ),
        ),
        "execution_plan": (
            outputs / "uslci-native-slice-process-action-execution-plan.json",
            _write_json(
                outputs / "uslci-native-slice-process-action-execution-plan.json",
                execution_plan,
            ),
        ),
        "conditions": (
            outputs / "uslci-process-action-recompile-conditions.jsonl",
            _write_jsonl(
                outputs / "uslci-process-action-recompile-conditions.jsonl", conditions
            ),
        ),
    }
    artifact_bindings = {
        key: {
            "path": str(path.relative_to(Path(out_dir))),
            "sha256": _sha(content),
            "bytes": len(content),
        }
        for key, (path, content) in output_rows.items()
    }
    recompile_version = _sha(
        _bytes(
            {
                "logic": LOGIC_VERSION,
                "desired_generation": desired_generation,
                "execution_plan_version": execution_plan["plan_version"],
                "outputs": artifact_bindings,
            }
        )
    )[:16]
    report = {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "status": "uslci_reference_flow_dependent_process_actions_recompiled",
        "input_version": input_version_for_bindings(
            [bindings[name] for name in EXPECTED_INPUT_NAMES]
        ),
        "recompile_version": recompile_version,
        "desired_generation": desired_generation,
        "observed_generation": desired_generation,
        "upstream_flow_plan_version": verified["execution"].get("plan_version"),
        "upstream_process_plan_version": verified["preflight"].get("plan_version"),
        "runtime_tree_sha256": verified["runtime_tree_sha256"],
        "successor_action_count": 6,
        "release_only_action_count": 2,
        "carry_forward_action_count": 2,
        "reconciliation_action_count": 8,
        "execution_plan_version": execution_plan["plan_version"],
        "execution_plan_node_count": 10,
        "execution_plan_edge_count": 5,
        "native_invocation_executed_count": 0,
        "process_identity_preflight_executed_count": 0,
        "process_build_plan_validation_executed_count": 0,
        "process_materialization_executed_count": 0,
        "process_creation_executed_count": 0,
        "flow_creation_executed_count": 0,
        "model_connection_executed_count": 0,
        "model_mutation_executed_count": 0,
        "network_read_executed_count": 0,
        "remote_write_executed_count": 0,
        "external_write_executed_count": 0,
        "publication_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "input_bindings": [bindings[name] for name in EXPECTED_INPUT_NAMES],
        "artifact_bindings": artifact_bindings,
        "external_patterns": [
            {
                "project": "kubernetes/kubernetes",
                "installed": False,
                "local_landing": "desired_observed_generation_and_successor_action_reconciliation",
            },
            {
                "project": "bazelbuild/bazel",
                "installed": False,
                "local_landing": "explicit_action_inputs_and_narrow_content_addressed_invalidation",
            },
        ],
        "next_action": (
            "Execute the exact five identity and five report-only build-plan nodes "
            "from the saved Process action plan."
        ),
        "routing": {"next_internal_route": NEXT_ROUTE, "pause_internal_work": False},
    }
    report_path = outputs / "uslci-reference-flow-dependent-process-action-recompile.json"
    report_bytes = _write_json(report_path, report)
    return {
        "report": report,
        "report_path": report_path,
        "report_bytes": report_bytes,
        "recompiled_identity_actions": recompiled_identities,
        "recompiled_build_templates": recompiled_templates,
        "released_actions": released,
        "carried_actions": carried,
        "reconciliation": reconciliation,
        "execution_plan": execution_plan,
        "conditions": conditions,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    for name in EXPECTED_INPUT_NAMES:
        parser.add_argument(f"--{name.replace('_', '-')}", type=Path, required=True)
        parser.add_argument(
            f"--{name.replace('_', '-')}-binding", type=Path, required=True
        )
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    values: JsonObject = {"out_dir": args.out_dir}
    for name in EXPECTED_INPUT_NAMES:
        values[f"{name}_bytes"] = getattr(args, name).read_bytes()
        values[f"{name}_binding"] = json.loads(
            getattr(args, f"{name}_binding").read_text(encoding="utf-8")
        )
    result = compile_uslci_reference_flow_dependent_process_actions(**values)
    print(json.dumps(result["report"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
