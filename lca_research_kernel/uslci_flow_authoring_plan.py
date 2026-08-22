"""Compile a complete support-aware native Flow authoring work plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_flow_authoring_plan/v1"
LOGIC_VERSION = "2026-07-18.1"
POLICY_VERSION = "2026-07-18.1"
RESOLUTION_PLAN_CONTRACT = "lca_research_kernel.uslci_flow_identity_resolution_plan/v1"
RESOLUTION_BATCH_CONTRACT = "lca_research_kernel.uslci_flow_identity_resolution_batch/v1"
INTEGRATION_CONTRACT = "lca_research_kernel.uslci_background_inventory_integration_plan/v1"
SLICE_CONTRACT = "lca_research_kernel.uslci_native_contract_slice/v1"
REFERENCE_EXECUTION_CONTRACT = "lca_research_kernel.uslci_reference_flow_authoring_execution/v1"
EXPECTED_INPUT_NAMES = (
    "canonical_resolution_batch_index",
    "resolution_plan_report",
    "automatic_decisions",
    "resolution_actions",
    "native_evidence_profiles",
    "preflight_flow_actions",
    "integration_report",
    "flow_mapping_requirements",
    "unit_mapping_requirements",
    "selected_native_slice",
    "reference_authoring_execution_report",
    "reference_authoring_branches",
    "staged_high_voltage_flow",
    "staged_construction_flow",
    "runtime_report",
    "runtime_manifest",
)
MAX_BULK_BATCH_SIZE = 100
EXPECTED_POSITION_COUNT = 1459
EXPECTED_BATCH_DECISION_COUNT = 1451
EXPECTED_AUTHORING_INTENT_COUNT = 1456
MASS_PROPERTY_ID = "93a60a56-a3c8-11da-a746-0800200b9a66"


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


def _parse_jsonl(
    content: bytes, label: str, *, allow_empty: bool = False
) -> List[JsonObject]:
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
    if not rows and not allow_empty:
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
                "maximum_bulk_batch_size": MAX_BULK_BATCH_SIZE,
                "inputs": list(bindings),
            }
        )
    )[:16]


def select_latest_accepted_resolution_batches(
    events: Sequence[Mapping[str, object]],
) -> List[JsonObject]:
    """Select the latest accepted payload for every resolution batch index."""

    selected = {}
    for event in events:
        if event.get("event_type") != (
            "research.uslci_flow_identity_resolution_batch.completed"
        ):
            continue
        payload_value = event.get("payload")
        if not isinstance(payload_value, dict):
            continue
        payload = dict(payload_value)
        index = payload.get("batch_index")
        if isinstance(index, bool) or not isinstance(index, int) or not 1 <= index <= 15:
            continue
        expected = 100 if index < 15 else 51
        expected_remaining = 1451 - min(index * 100, 1451)
        if (
            payload.get("status")
            != "uslci_flow_identity_resolution_batch_completed"
            or payload.get("batch_action_count") != expected
            or payload.get("batch_failure_count") != 0
            or payload.get("remaining_resolution_action_count")
            != expected_remaining
        ):
            continue
        payload["source_event_sequence"] = _integer(
            event.get("sequence"), "source event sequence"
        )
        payload["source_event_id"] = _text(
            event.get("event_id"), "source event ID"
        )
        selected[index] = payload
    if set(selected) != set(range(1, 16)):
        missing = sorted(set(range(1, 16)) - set(selected))
        raise ValueError(f"accepted Flow resolution batches are incomplete: {missing}")
    return [selected[index] for index in range(1, 16)]


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


def _read_bound_artifact(
    artifact_root: Path, binding_value: object, label: str
) -> Tuple[bytes, JsonObject]:
    binding = _object(binding_value, f"{label} binding")
    relative = _safe_relative_path(binding.get("path"), f"{label} path")
    root = artifact_root.resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} escaped artifact root") from error
    if not path.is_file():
        raise ValueError(f"{label} is missing")
    content = path.read_bytes()
    return content, _binding(binding, content, f"{label} binding")


def _expected_target_type(source_type: str) -> str:
    if source_type == "PRODUCT_FLOW":
        return "Product flow"
    if source_type == "ELEMENTARY_FLOW":
        return "Elementary flow"
    if source_type == "WASTE_FLOW":
        raise ValueError("WASTE_FLOW cannot be silently authored as an Elementary flow")
    raise ValueError("source Flow type is unsupported")


def compile_authoring_intent(
    decision_value: Mapping[str, object],
    mapping_value: Mapping[str, object],
    unit_value: Mapping[str, object],
) -> JsonObject:
    """Compile one decision into a non-executing native authoring intent."""

    decision = _object(decision_value, "complete identity decision")
    mapping = _object(mapping_value, "Flow mapping requirement")
    unit = _object(unit_value, "unit mapping requirement")
    target = _object(decision.get("target"), "identity decision target")
    source_id = _text(decision.get("source_flow_id"), "decision source Flow ID")
    source_name = _text(
        decision.get("source_flow_name"), "decision source Flow name"
    )
    source_type = _text(mapping.get("source_flow_type"), "mapping source Flow type")
    target_type = _text(target.get("type_of_dataset"), "target Flow type")
    expected_target_type = _expected_target_type(source_type)
    classifications = [
        _text(value, "source classification")
        for value in _array(mapping.get("classifications"), "source classifications")
    ]
    if len(classifications) != 1:
        raise ValueError("source Flow must have one coarse classification")
    classification = classifications[0]
    allowed_classifications = {
        "ELEMENTARY_FLOW": {"elementary_emission", "elementary_resource"},
        "PRODUCT_FLOW": {"technosphere_product"},
    }
    if (
        decision.get("resolution_decision") != "create_new_intent"
        or decision.get("remote_duplicate_unknown") is not True
        or mapping.get("source_flow_id") != source_id
        or mapping.get("source_flow_name") != source_name
        or mapping.get("application_status") != "proposed_not_applied"
        or target_type != expected_target_type
        or classification not in allowed_classifications[source_type]
    ):
        raise ValueError("identity decision and Flow mapping do not form an authoring intent")
    property_name = _text(target.get("flow_property"), "target Flow property")
    reference_unit = _text(target.get("reference_unit"), "target reference unit")
    mapping_unit = _text(
        mapping.get("source_reference_unit"), "mapping source reference unit"
    )
    property_id = _text(unit.get("flow_property_id"), "FlowProperty ID")
    if (
        mapping_unit != reference_unit
        or unit.get("source_unit_name") != reference_unit
        or unit.get("target_reference_unit") != reference_unit
        or unit.get("flow_property_name") != property_name
        or unit.get("conversion_factor") != "1"
        or unit.get("application_status") != "proposed_not_applied"
    ):
        raise ValueError("authoring intent unit/property support tuple drifted")
    if property_name == "Mass" and reference_unit == "kg":
        if property_id != MASS_PROPERTY_ID:
            raise ValueError("native builtin Mass FlowProperty UUID drifted")
        support_status = "native_builtin_mass_local_probe_required"
    else:
        support_status = "source_observed_local_addressability_unverified"
    group_key = "|".join(
        (classification, target_type, property_name, reference_unit)
    )
    intent_key = _sha256(
        _json_bytes(
            {
                "contract": CONTRACT,
                "policy_version": POLICY_VERSION,
                "position": decision.get("position"),
                "source_flow_id": source_id,
                "target": target,
                "classification": classification,
                "flow_property_id": property_id,
            }
        )
    )[:16]
    return {
        "schema_version": 1,
        "authoring_intent_key": intent_key,
        "position": _integer(decision.get("position"), "decision position"),
        "source_action_key": decision.get("source_action_key"),
        "source_decision_id": decision.get("source_decision_id"),
        "source_flow_id": source_id,
        "source_flow_name": source_name,
        "source_flow_type": source_type,
        "source_observed_exchange_count": _integer(
            mapping.get("observed_exchange_count"), "source observed exchange count"
        ),
        "target": target,
        "authoring_group_key": group_key,
        "coarse_source_classification": classification,
        "exact_classification_path_required": True,
        "flow_property_id": property_id,
        "flow_property_name": property_name,
        "reference_unit": reference_unit,
        "conversion_factor": "1",
        "support_reference_status": support_status,
        "support_reference_local_addressability_verified": False,
        "authoring_status": "waiting_for_classification_and_support_probe",
        "remote_duplicate_unknown": True,
        "saved_flow_plan_compiled": False,
        "entity_creation_authorized": False,
        "entity_materialized": False,
        "publish_ready": False,
    }


def build_authoring_schedule(
    intent_values: Sequence[Mapping[str, object]],
) -> Tuple[List[JsonObject], List[JsonObject]]:
    """Select one representative per group and partition the remaining work."""

    intents = [
        _object(value, f"authoring intent {index}")
        for index, value in enumerate(intent_values, start=1)
    ]
    keys = [_text(row.get("authoring_intent_key"), "authoring intent key") for row in intents]
    if len(set(keys)) != len(keys):
        raise ValueError("authoring intent key is duplicated")
    by_group = defaultdict(list)
    for intent in intents:
        by_group[_text(intent.get("authoring_group_key"), "authoring group key")].append(
            intent
        )
    canaries = []
    canary_keys = set()
    for group_key in sorted(by_group):
        selected = min(
            by_group[group_key],
            key=lambda row: (
                _integer(row.get("position"), "authoring intent position"),
                str(row.get("authoring_intent_key")),
            ),
        )
        canary_keys.add(str(selected["authoring_intent_key"]))
        canaries.append(
            {
                "schema_version": 1,
                "canary_index": len(canaries) + 1,
                "authoring_group_key": group_key,
                "position": selected["position"],
                "authoring_intent_key": selected["authoring_intent_key"],
                "target": selected.get("target"),
                "support_reference_status": selected.get("support_reference_status"),
                "exact_classification_path_required": True,
                "status": "selected_for_support_and_classification_resolution",
            }
        )
    remaining = sorted(
        [row for row in intents if str(row["authoring_intent_key"]) not in canary_keys],
        key=lambda row: (
            str(row.get("authoring_group_key")),
            _integer(row.get("position"), "authoring intent position"),
            str(row.get("authoring_intent_key")),
        ),
    )
    batches = []

    def descriptor(selected: Sequence[JsonObject], phase: str) -> JsonObject:
        index = len(batches) + 1
        action_keys = [str(row["authoring_intent_key"]) for row in selected]
        group_counts = Counter(str(row["authoring_group_key"]) for row in selected)
        batch_key = _sha256(
            _json_bytes(
                {
                    "contract": CONTRACT,
                    "policy_version": POLICY_VERSION,
                    "batch_index": index,
                    "phase": phase,
                    "authoring_intent_keys": action_keys,
                }
            )
        )[:16]
        return {
            "schema_version": 1,
            "batch_index": index,
            "batch_key": batch_key,
            "phase": phase,
            "action_count": len(selected),
            "authoring_intent_keys": action_keys,
            "source_positions": [row["position"] for row in selected],
            "authoring_group_counts": dict(sorted(group_counts.items())),
            "maximum_bulk_batch_size": MAX_BULK_BATCH_SIZE,
            "bulk_release_requires_canary_pass": phase != "representative_canary",
            "status": (
                "selected_next"
                if phase == "representative_canary"
                else "pending_canary_analysis_gate"
            ),
        }

    canary_intents_by_key = {str(row["authoring_intent_key"]): row for row in intents}
    batches.append(
        descriptor(
            [canary_intents_by_key[str(row["authoring_intent_key"])] for row in canaries],
            "representative_canary",
        )
    )
    for offset in range(0, len(remaining), MAX_BULK_BATCH_SIZE):
        batches.append(
            descriptor(
                remaining[offset : offset + MAX_BULK_BATCH_SIZE],
                "bulk_pending_canary",
            )
        )
    return canaries, batches


def _flow_identity(flow: JsonObject) -> Tuple[str, str, str]:
    dataset = _object(flow.get("flowDataSet"), "staged Flow data set")
    information = _object(dataset.get("flowInformation"), "staged Flow information")
    data = _object(information.get("dataSetInformation"), "staged Flow data information")
    flow_id = _text(data.get("common:UUID"), "staged Flow UUID")
    name = _object(data.get("name"), "staged Flow name")
    base_names = _array(name.get("baseName"), "staged Flow base names")
    base = _object(base_names[0], "staged Flow base name")
    base_name = _text(base.get("#text"), "staged Flow base name text")
    validation = _object(dataset.get("modellingAndValidation"), "staged Flow validation")
    method = _object(validation.get("LCIMethod"), "staged Flow method")
    return flow_id, base_name, _text(method.get("typeOfDataSet"), "staged Flow type")


def _build_execution_dag(batches: Sequence[JsonObject]) -> JsonObject:
    nodes = [
        {"node_id": "resolve-canary-support-references", "kind": "policy_resolution"},
        {"node_id": "resolve-canary-classifications", "kind": "policy_resolution"},
        {"node_id": "compile-canary-saved-plans", "kind": "saved_plan_compile"},
        {"node_id": "execute-canary-native-dry-runs", "kind": "local_native_dry_run"},
        {"node_id": "evaluate-canary-analysis-gate", "kind": "analysis_gate"},
    ]
    edges = [
        {"from": "resolve-canary-support-references", "to": "compile-canary-saved-plans"},
        {"from": "resolve-canary-classifications", "to": "compile-canary-saved-plans"},
        {"from": "compile-canary-saved-plans", "to": "execute-canary-native-dry-runs"},
        {"from": "execute-canary-native-dry-runs", "to": "evaluate-canary-analysis-gate"},
    ]
    for batch in batches[1:]:
        index = int(batch["batch_index"])
        compile_node = f"compile-bulk-batch-{index}-saved-plans"
        dry_node = f"execute-bulk-batch-{index}-native-dry-runs"
        nodes.extend(
            [
                {"node_id": compile_node, "kind": "saved_plan_compile", "batch_index": index},
                {"node_id": dry_node, "kind": "local_native_dry_run", "batch_index": index},
            ]
        )
        edges.extend(
            [
                {"from": "evaluate-canary-analysis-gate", "to": compile_node},
                {"from": compile_node, "to": dry_node},
            ]
        )
    return {
        "schema_version": 1,
        "contract": CONTRACT,
        "nodes": nodes,
        "edges": edges,
        "canary_failure_effect": "invalidate_bulk_release_without_rewriting_identity_decisions",
        "entity_write_included": False,
    }


def compile_uslci_flow_authoring_plan(
    *,
    canonical_resolution_batch_index_bytes: bytes,
    canonical_resolution_batch_index_binding: Mapping[str, object],
    resolution_plan_report_bytes: bytes,
    resolution_plan_report_binding: Mapping[str, object],
    automatic_decisions_bytes: bytes,
    automatic_decisions_binding: Mapping[str, object],
    resolution_actions_bytes: bytes,
    resolution_actions_binding: Mapping[str, object],
    native_evidence_profiles_bytes: bytes,
    native_evidence_profiles_binding: Mapping[str, object],
    preflight_flow_actions_bytes: bytes,
    preflight_flow_actions_binding: Mapping[str, object],
    integration_report_bytes: bytes,
    integration_report_binding: Mapping[str, object],
    flow_mapping_requirements_bytes: bytes,
    flow_mapping_requirements_binding: Mapping[str, object],
    unit_mapping_requirements_bytes: bytes,
    unit_mapping_requirements_binding: Mapping[str, object],
    selected_native_slice_bytes: bytes,
    selected_native_slice_binding: Mapping[str, object],
    reference_authoring_execution_report_bytes: bytes,
    reference_authoring_execution_report_binding: Mapping[str, object],
    reference_authoring_branches_bytes: bytes,
    reference_authoring_branches_binding: Mapping[str, object],
    staged_high_voltage_flow_bytes: bytes,
    staged_high_voltage_flow_binding: Mapping[str, object],
    staged_construction_flow_bytes: bytes,
    staged_construction_flow_binding: Mapping[str, object],
    runtime_report_bytes: bytes,
    runtime_report_binding: Mapping[str, object],
    runtime_manifest_bytes: bytes,
    runtime_manifest_binding: Mapping[str, object],
    artifact_root: Path,
) -> JsonObject:
    contents = {
        "canonical_resolution_batch_index": canonical_resolution_batch_index_bytes,
        "resolution_plan_report": resolution_plan_report_bytes,
        "automatic_decisions": automatic_decisions_bytes,
        "resolution_actions": resolution_actions_bytes,
        "native_evidence_profiles": native_evidence_profiles_bytes,
        "preflight_flow_actions": preflight_flow_actions_bytes,
        "integration_report": integration_report_bytes,
        "flow_mapping_requirements": flow_mapping_requirements_bytes,
        "unit_mapping_requirements": unit_mapping_requirements_bytes,
        "selected_native_slice": selected_native_slice_bytes,
        "reference_authoring_execution_report": reference_authoring_execution_report_bytes,
        "reference_authoring_branches": reference_authoring_branches_bytes,
        "staged_high_voltage_flow": staged_high_voltage_flow_bytes,
        "staged_construction_flow": staged_construction_flow_bytes,
        "runtime_report": runtime_report_bytes,
        "runtime_manifest": runtime_manifest_bytes,
    }
    supplied = {
        "canonical_resolution_batch_index": canonical_resolution_batch_index_binding,
        "resolution_plan_report": resolution_plan_report_binding,
        "automatic_decisions": automatic_decisions_binding,
        "resolution_actions": resolution_actions_binding,
        "native_evidence_profiles": native_evidence_profiles_binding,
        "preflight_flow_actions": preflight_flow_actions_binding,
        "integration_report": integration_report_binding,
        "flow_mapping_requirements": flow_mapping_requirements_binding,
        "unit_mapping_requirements": unit_mapping_requirements_binding,
        "selected_native_slice": selected_native_slice_binding,
        "reference_authoring_execution_report": reference_authoring_execution_report_binding,
        "reference_authoring_branches": reference_authoring_branches_binding,
        "staged_high_voltage_flow": staged_high_voltage_flow_binding,
        "staged_construction_flow": staged_construction_flow_binding,
        "runtime_report": runtime_report_binding,
        "runtime_manifest": runtime_manifest_binding,
    }
    input_bindings = [
        _binding(supplied[name], contents[name], f"{name} binding")
        for name in EXPECTED_INPUT_NAMES
    ]
    input_version = input_version_for_bindings(input_bindings)
    batch_index_rows = _parse_jsonl(
        canonical_resolution_batch_index_bytes, "canonical resolution batch index"
    )
    resolution_plan = _parse_json(
        resolution_plan_report_bytes, "identity-resolution plan report"
    )
    automatic = _parse_jsonl(automatic_decisions_bytes, "automatic decisions")
    resolution_actions = _parse_jsonl(
        resolution_actions_bytes, "resolution actions"
    )
    profiles = _parse_jsonl(native_evidence_profiles_bytes, "native evidence profiles")
    preflight_actions = _parse_jsonl(
        preflight_flow_actions_bytes, "preflight Flow actions"
    )
    integration = _parse_json(integration_report_bytes, "integration report")
    mappings = _parse_jsonl(flow_mapping_requirements_bytes, "Flow mapping requirements")
    units = _parse_jsonl(unit_mapping_requirements_bytes, "unit mapping requirements")
    selected_slice = _parse_json(selected_native_slice_bytes, "selected native slice")
    reference_execution = _parse_json(
        reference_authoring_execution_report_bytes, "reference authoring execution"
    )
    reference_branches = _parse_jsonl(
        reference_authoring_branches_bytes, "reference authoring branches"
    )
    staged_flows = [
        (
            _parse_json(staged_high_voltage_flow_bytes, "staged high-voltage Flow"),
            input_bindings[12],
        ),
        (
            _parse_json(staged_construction_flow_bytes, "staged construction Flow"),
            input_bindings[13],
        ),
    ]
    runtime_report = _parse_json(runtime_report_bytes, "runtime report")
    runtime_manifest = _parse_json(runtime_manifest_bytes, "runtime manifest")
    if (
        resolution_plan.get("contract") != RESOLUTION_PLAN_CONTRACT
        or resolution_plan.get("status")
        != "uslci_flow_identity_resolution_plan_compiled"
        or resolution_plan.get("automatic_decision_count") != 8
        or resolution_plan.get("pending_resolution_action_count") != 1451
        or len(automatic) != 8
        or len(resolution_actions) != 1451
        or len(profiles) != 1459
        or len(preflight_actions) != 1459
        or len(batch_index_rows) != 15
    ):
        raise ValueError("identity-resolution parent contract drifted")
    if (
        integration.get("contract") != INTEGRATION_CONTRACT
        or integration.get("status")
        != "uslci_background_inventory_integration_plan_compiled"
        or integration.get("selected_native_slice_flow_count") != 1459
        or integration.get("source_flow_definition_conflict_count") != 0
        or len(mappings) != 2300
        or len(units) != 9
        or selected_slice.get("contract") != SLICE_CONTRACT
        or selected_slice.get("required_source_flow_count") != 1459
        or len(selected_slice.get("required_source_flow_ids", [])) != 1459
    ):
        raise ValueError("background integration or selected slice drifted")
    if (
        reference_execution.get("contract") != REFERENCE_EXECUTION_CONTRACT
        or reference_execution.get("status")
        != "uslci_reference_flow_authoring_execution_completed"
        or reference_execution.get("staged_flow_registration_count") != 2
        or len(reference_branches) != 2
        or runtime_report.get("status")
        != "portable_native_tool_runtime_closure_verified"
        or runtime_report.get("runtime_content_closed") is not True
        or runtime_report.get("tree_sha256") != runtime_manifest.get("tree_sha256")
    ):
        raise ValueError("reference staged Flow or runtime authority drifted")
    cli = runtime_manifest.get("tiangong_cli") or runtime_manifest.get("cli")
    cli = _object(cli, "runtime TianGong CLI")
    if cli.get("package") != "@tiangong-lca/cli" or cli.get("version") != "0.0.25":
        raise ValueError("runtime TianGong CLI identity drifted")

    verified_batch_rows = []
    batch_decisions = []
    previous_report_sha = input_bindings[1]["sha256"]
    for expected_index, index_row in enumerate(batch_index_rows, start=1):
        if index_row.get("batch_index") != expected_index:
            raise ValueError("canonical resolution batch index order drifted")
        artifacts = _object(index_row.get("artifacts"), "resolution batch artifacts")
        parsed = {}
        verified_artifacts = {}
        for role in (
            "report",
            "decisions",
            "candidate_assertions",
            "action_evidence",
            "source_archive_verifications",
            "failures",
        ):
            content, binding = _read_bound_artifact(
                artifact_root,
                artifacts.get(role),
                f"resolution batch {expected_index} {role}",
            )
            verified_artifacts[role] = binding
            if role == "report":
                parsed[role] = _parse_json(content, f"resolution batch {expected_index} report")
            else:
                parsed[role] = _parse_jsonl(
                    content,
                    f"resolution batch {expected_index} {role}",
                    allow_empty=role == "failures",
                )
        report = parsed["report"]
        action_count = 100 if expected_index < 15 else 51
        if (
            report.get("contract") != RESOLUTION_BATCH_CONTRACT
            or report.get("status")
            != "uslci_flow_identity_resolution_batch_completed"
            or report.get("batch_index") != expected_index
            or report.get("batch_action_count") != action_count
            or report.get("batch_failure_count") != 0
            or report.get("remaining_resolution_action_count")
            != 1451 - min(expected_index * 100, 1451)
            or report.get("source_resolution_plan_version")
            != resolution_plan.get("resolution_plan_version")
            or len(parsed["decisions"]) != action_count
            or len(parsed["candidate_assertions"]) != action_count * 13
            or len(parsed["action_evidence"]) != action_count
            or len(parsed["source_archive_verifications"])
            != report.get("verified_source_archive_count")
            or parsed["failures"]
            or any(
                row.get("equivalence_supported") is not False
                for row in parsed["candidate_assertions"]
            )
        ):
            raise ValueError(f"resolution batch {expected_index} evidence drifted")
        authority_binding = _array(
            report.get("input_bindings"), "resolution batch input bindings"
        )[-1]
        authority_binding = _object(authority_binding, "resolution route authority binding")
        if authority_binding.get("sha256") != previous_report_sha:
            raise ValueError("resolution batch immediate-prior authority chain drifted")
        previous_report_sha = verified_artifacts["report"]["sha256"]
        for role in (
            "decisions",
            "candidate_assertions",
            "action_evidence",
            "source_archive_verifications",
            "failures",
        ):
            claimed = _object(
                report.get("artifact_bindings", {}).get(role),
                f"resolution batch {expected_index} claimed {role}",
            )
            if (
                claimed.get("sha256") != verified_artifacts[role]["sha256"]
                or claimed.get("bytes") != verified_artifacts[role]["bytes"]
            ):
                raise ValueError("resolution batch artifact binding drifted")
        batch_decisions.extend(parsed["decisions"])
        verified_batch_rows.append(
            {
                **{key: value for key, value in index_row.items() if key != "artifacts"},
                "artifacts": verified_artifacts,
                "verification_status": "decision_assertion_and_authority_chain_verified",
            }
        )
    if len(batch_decisions) != EXPECTED_BATCH_DECISION_COUNT:
        raise ValueError("aggregate batch decision count drifted")

    action_by_key = {
        _text(row.get("resolution_action_key"), "resolution action key"): row
        for row in resolution_actions
    }
    if len(action_by_key) != len(resolution_actions):
        raise ValueError("resolution action key is duplicated")
    complete_decisions = []
    for row in automatic:
        complete_decisions.append(
            {
                "schema_version": 1,
                "position": row.get("position"),
                "source_action_key": row.get("action_key"),
                "source_decision_id": row.get("evidence", {}).get("reference_decision_id"),
                "source_flow_id": row.get("source_flow_id"),
                "source_flow_name": next(
                    (
                        action.get("source_flow_name")
                        for action in preflight_actions
                        if action.get("position") == row.get("position")
                    ),
                    None,
                ),
                "target": row.get("target"),
                "resolution_decision": row.get("resolution_decision"),
                "resolution_status": row.get("resolution_status"),
                "decision_authority": row.get("decision_authority"),
                "remote_duplicate_unknown": row.get("remote_duplicate_unknown"),
                "staged_flow_binding": row.get("staged_flow_binding"),
            }
        )
    for decision in batch_decisions:
        key = _text(decision.get("resolution_action_key"), "batch resolution action key")
        action = action_by_key.get(key)
        if (
            not isinstance(action, dict)
            or decision.get("source_action_key") != action.get("source_action_key")
            or decision.get("target") != action.get("target")
            or decision.get("resolution_decision") != "create_new_intent"
            or decision.get("remote_duplicate_unknown") is not True
        ):
            raise ValueError("batch decision no longer binds resolution action")
        complete_decisions.append(
            {
                "schema_version": 1,
                "position": decision.get("position"),
                "source_action_key": decision.get("source_action_key"),
                "source_decision_id": decision.get("decision_id"),
                "source_flow_id": action.get("source_flow_id"),
                "source_flow_name": action.get("source_flow_name"),
                "target": decision.get("target"),
                "resolution_decision": "create_new_intent",
                "resolution_status": "bounded_catalog_create_new_intent",
                "decision_authority": "aggregate_identity_resolution_batch",
                "remote_duplicate_unknown": True,
                "staged_flow_binding": None,
            }
        )
    complete_decisions.sort(key=lambda row: _integer(row.get("position"), "decision position"))
    if (
        len(complete_decisions) != EXPECTED_POSITION_COUNT
        or [row["position"] for row in complete_decisions]
        != list(range(1, EXPECTED_POSITION_COUNT + 1))
        or len({row["target"]["id"] for row in complete_decisions}) != EXPECTED_POSITION_COUNT
    ):
        raise ValueError("complete identity decision coverage drifted")

    preflight_by_position = {
        _integer(row.get("position"), "preflight action position"): row
        for row in preflight_actions
    }
    if len(preflight_by_position) != EXPECTED_POSITION_COUNT:
        raise ValueError("preflight Flow positions are incomplete")
    for decision in complete_decisions:
        action = preflight_by_position[int(decision["position"])]
        if (
            action.get("source_flow_id") != decision.get("source_flow_id")
            or action.get("source_flow_name") != decision.get("source_flow_name")
            or action.get("target") != decision.get("target")
        ):
            raise ValueError("complete decision no longer binds preflight Flow action")
    required_ids = set(selected_slice.get("required_source_flow_ids", []))
    if required_ids != {row["source_flow_id"] for row in complete_decisions}:
        raise ValueError("complete decisions do not exactly cover selected slice Flows")

    mapping_by_source = {}
    for mapping in mappings:
        source_id = _text(mapping.get("source_flow_id"), "Flow mapping source ID")
        if source_id in mapping_by_source:
            raise ValueError("Flow mapping source ID is duplicated")
        mapping_by_source[source_id] = mapping
    unit_by_tuple = {}
    for unit in units:
        key = (
            unit.get("source_unit_name"),
            unit.get("flow_property_name"),
            unit.get("target_reference_unit"),
            unit.get("conversion_factor"),
        )
        unit_by_tuple[key] = unit

    staged_by_id = {}
    for flow, binding in staged_flows:
        flow_id, base_name, flow_type = _flow_identity(flow)
        if flow_type != "Product flow":
            raise ValueError("staged reference Flow type drifted")
        staged_by_id[flow_id] = {"base_name": base_name, "binding": binding}
    branch_by_target = {
        _text(row.get("target_flow_id"), "reference branch target ID"): row
        for row in reference_branches
    }
    preserved_branches = []
    authoring_intents = []
    for decision in complete_decisions:
        target = _object(decision.get("target"), "complete decision target")
        if decision.get("resolution_decision") == "reuse":
            preserved_branches.append(
                {
                    **decision,
                    "branch_status": "preserved_reuse_existing_local",
                    "requires_new_authoring": False,
                }
            )
            continue
        if decision.get("resolution_status") == "create_new_intent_staged":
            target_id = _text(target.get("id"), "staged decision target ID")
            staged = staged_by_id.get(target_id)
            branch = branch_by_target.get(target_id)
            if (
                not isinstance(staged, dict)
                or not isinstance(branch, dict)
                or decision.get("staged_flow_binding") != staged["binding"]
                or branch.get("staged_flow_sha256") != staged["binding"]["sha256"]
                or branch.get("status") != "staged_flow_native_gates_passed"
            ):
                raise ValueError("staged reference branch drifted")
            preserved_branches.append(
                {
                    **decision,
                    "staged_entity_base_name": staged["base_name"],
                    "branch_status": "preserved_native_gated_staged_entity",
                    "requires_new_authoring": False,
                }
            )
            continue
        source_id = _text(decision.get("source_flow_id"), "decision source Flow ID")
        mapping = mapping_by_source.get(source_id)
        if not isinstance(mapping, dict):
            raise ValueError("authoring decision lacks Flow mapping requirement")
        unit_key = (
            mapping.get("source_reference_unit"),
            target.get("flow_property"),
            target.get("reference_unit"),
            "1",
        )
        unit = unit_by_tuple.get(unit_key)
        if not isinstance(unit, dict):
            raise ValueError("authoring decision lacks exact identity unit mapping")
        authoring_intents.append(compile_authoring_intent(decision, mapping, unit))
    if (
        len(preserved_branches) != 3
        or sum(row["resolution_decision"] == "reuse" for row in preserved_branches) != 1
        or len(authoring_intents) != EXPECTED_AUTHORING_INTENT_COUNT
    ):
        raise ValueError("preserved and new authoring branch partition drifted")

    authoring_group_counts = Counter(
        str(row["authoring_group_key"]) for row in authoring_intents
    )
    expected_group_counts = Counter(
        {
            "elementary_emission|Elementary flow|Mass|kg": 1396,
            "elementary_resource|Elementary flow|Mass|kg": 46,
            "elementary_resource|Elementary flow|Energy|MJ": 8,
            "elementary_resource|Elementary flow|Area*Time|m2*a": 5,
            "technosphere_product|Product flow|Energy|MJ": 1,
        }
    )
    if authoring_group_counts != expected_group_counts:
        raise ValueError("authoring group counts drifted")
    authoring_groups = []
    for index, group_key in enumerate(sorted(authoring_group_counts), start=1):
        rows = [row for row in authoring_intents if row["authoring_group_key"] == group_key]
        authoring_groups.append(
            {
                "schema_version": 1,
                "group_index": index,
                "authoring_group_key": group_key,
                "intent_count": len(rows),
                "coarse_source_classification": rows[0]["coarse_source_classification"],
                "target_flow_type": rows[0]["target"]["type_of_dataset"],
                "flow_property_id": rows[0]["flow_property_id"],
                "flow_property_name": rows[0]["flow_property_name"],
                "reference_unit": rows[0]["reference_unit"],
                "support_reference_status": rows[0]["support_reference_status"],
                "exact_classification_path_required_count": len(rows),
                "entity_creation_authorized_count": 0,
            }
        )
    support_groups = defaultdict(list)
    classification_groups = defaultdict(list)
    for intent in authoring_intents:
        support_groups[
            (
                intent["flow_property_id"],
                intent["flow_property_name"],
                intent["reference_unit"],
                intent["support_reference_status"],
            )
        ].append(intent)
        classification_groups[intent["coarse_source_classification"]].append(intent)
    support_requirements = []
    for index, (key, rows) in enumerate(sorted(support_groups.items()), start=1):
        property_id, property_name, unit_name, status = key
        support_requirements.append(
            {
                "schema_version": 1,
                "support_requirement_index": index,
                "flow_property_id": property_id,
                "flow_property_name": property_name,
                "reference_unit": unit_name,
                "intent_count": len(rows),
                "current_status": status,
                "required_probe": "native_local_support_reference_addressability",
                "remote_resolution_required_for_publication": True,
                "support_reference_application_authorized": False,
            }
        )
    classification_requirements = []
    for index, (classification, rows) in enumerate(
        sorted(classification_groups.items()), start=1
    ):
        classification_requirements.append(
            {
                "schema_version": 1,
                "classification_requirement_index": index,
                "coarse_source_classification": classification,
                "intent_count": len(rows),
                "exact_tiangong_classification_path_known": False,
                "required_resolution": "evidence_bound_exact_native_classification",
                "classification_application_authorized": False,
            }
        )
    canary_actions, authoring_batches = build_authoring_schedule(authoring_intents)
    if (
        len(authoring_groups) != 5
        or len(support_requirements) != 3
        or len(classification_requirements) != 3
        or len(canary_actions) != 5
        or len(authoring_batches) != 16
        or [row["action_count"] for row in authoring_batches]
        != [5] + [100] * 14 + [51]
    ):
        raise ValueError("authoring canary/bulk schedule drifted")
    execution_dag = _build_execution_dag(authoring_batches)
    if len(execution_dag["nodes"]) != 35 or len(execution_dag["edges"]) != 34:
        raise ValueError("authoring execution DAG drifted")
    failures: List[JsonObject] = []
    detail_rows = {
        "verified_resolution_batch_index": verified_batch_rows,
        "complete_identity_decisions": complete_decisions,
        "preserved_branches": preserved_branches,
        "authoring_intents": authoring_intents,
        "authoring_groups": authoring_groups,
        "support_requirements": support_requirements,
        "classification_requirements": classification_requirements,
        "canary_actions": canary_actions,
        "authoring_batches": authoring_batches,
        "failures": failures,
    }
    artifact_names = {
        "verified_resolution_batch_index": "verified-uslci-flow-resolution-batch-index.jsonl",
        "complete_identity_decisions": "uslci-complete-flow-identity-decisions.jsonl",
        "preserved_branches": "uslci-flow-authoring-preserved-branches.jsonl",
        "authoring_intents": "uslci-flow-authoring-intents.jsonl",
        "authoring_groups": "uslci-flow-authoring-groups.jsonl",
        "support_requirements": "uslci-flow-authoring-support-requirements.jsonl",
        "classification_requirements": "uslci-flow-authoring-classification-requirements.jsonl",
        "canary_actions": "uslci-flow-authoring-canary-actions.jsonl",
        "authoring_batches": "uslci-flow-authoring-batches.jsonl",
        "execution_dag": "uslci-flow-authoring-execution-dag.json",
        "failures": "uslci-flow-authoring-plan-failures.jsonl",
    }
    detail_bytes = {key: _jsonl_bytes(rows) for key, rows in detail_rows.items()}
    detail_bytes["execution_dag"] = _json_bytes(execution_dag, pretty=True)
    artifact_bindings = {
        key: _artifact_binding(artifact_names[key], content)
        for key, content in detail_bytes.items()
    }
    plan_version = _sha256(
        _json_bytes(
            {
                "contract": CONTRACT,
                "logic_version": LOGIC_VERSION,
                "policy_version": POLICY_VERSION,
                "input_bindings": input_bindings,
                "artifact_bindings": artifact_bindings,
            }
        )
    )[:16]
    report = {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "policy_version": POLICY_VERSION,
        "status": "uslci_flow_authoring_plan_compiled",
        "scope_id": resolution_plan.get("scope_id"),
        "revision": resolution_plan.get("revision"),
        "input_version": input_version,
        "source_resolution_plan_version": resolution_plan.get("resolution_plan_version"),
        "authoring_plan_version": plan_version,
        "runtime_tree_sha256": runtime_report.get("tree_sha256"),
        "tiangong_cli_version": "0.0.25",
        "canonical_resolution_batch_count": 15,
        "complete_identity_decision_count": len(complete_decisions),
        "reuse_branch_count": 1,
        "staged_branch_count": 2,
        "authoring_intent_count": len(authoring_intents),
        "authoring_group_count": len(authoring_groups),
        "authoring_group_counts": dict(sorted(authoring_group_counts.items())),
        "support_reference_requirement_count": len(support_requirements),
        "classification_requirement_count": len(classification_requirements),
        "exact_classification_path_required_count": len(authoring_intents),
        "native_builtin_mass_support_probe_count": sum(
            row["support_reference_status"]
            == "native_builtin_mass_local_probe_required"
            for row in authoring_intents
        ),
        "source_observed_support_addressability_unverified_count": sum(
            row["support_reference_status"]
            == "source_observed_local_addressability_unverified"
            for row in authoring_intents
        ),
        "canary_action_count": len(canary_actions),
        "authoring_batch_count": len(authoring_batches),
        "maximum_bulk_batch_size": MAX_BULK_BATCH_SIZE,
        "execution_dag_node_count": len(execution_dag["nodes"]),
        "execution_dag_edge_count": len(execution_dag["edges"]),
        "planning_failure_count": len(failures),
        "saved_flow_plan_compiled_count": 0,
        "entity_creation_authorized_count": 0,
        "publish_ready_flow_count": 0,
        "input_bindings": input_bindings,
        "artifact_bindings": artifact_bindings,
        "external_patterns": [
            {
                "project": "hashicorp/terraform",
                "installed": False,
                "local_landing": "immutable_intents_and_saved_plan_rehash_contract",
            },
            {
                "project": "helm/helm",
                "installed": False,
                "local_landing": "group_template_render_validate_dry_run_apply_separation",
            },
            {
                "project": "argoproj/argo-rollouts",
                "installed": False,
                "local_landing": "five_group_canary_analysis_and_bulk_release_invalidation",
            },
            {
                "project": "open-policy-agent/opa",
                "installed": False,
                "local_landing": "identity_decision_authority_not_recomputed_during_authoring",
            },
        ],
        "native_invocation_executed_count": 0,
        "network_read_executed_count": 0,
        "remote_candidate_search_executed_count": 0,
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
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "routing": {
            "next_internal_route": "resolve-uslci-flow-authoring-canary-supports",
            "pause_internal_work": False,
        },
        "next_action": "Resolve exact classification and support references for the five authoring canaries.",
    }
    return {**report, **detail_rows, "execution_dag": execution_dag}


def materialize_uslci_flow_authoring_plan(
    result: Mapping[str, object], out_dir: Path
) -> Dict[str, Path]:
    output = out_dir.resolve() / "outputs"
    output.mkdir(parents=True, exist_ok=True)
    names = {
        "report": "uslci-flow-authoring-plan.json",
        "verified_resolution_batch_index": "verified-uslci-flow-resolution-batch-index.jsonl",
        "complete_identity_decisions": "uslci-complete-flow-identity-decisions.jsonl",
        "preserved_branches": "uslci-flow-authoring-preserved-branches.jsonl",
        "authoring_intents": "uslci-flow-authoring-intents.jsonl",
        "authoring_groups": "uslci-flow-authoring-groups.jsonl",
        "support_requirements": "uslci-flow-authoring-support-requirements.jsonl",
        "classification_requirements": "uslci-flow-authoring-classification-requirements.jsonl",
        "canary_actions": "uslci-flow-authoring-canary-actions.jsonl",
        "authoring_batches": "uslci-flow-authoring-batches.jsonl",
        "execution_dag": "uslci-flow-authoring-execution-dag.json",
        "failures": "uslci-flow-authoring-plan-failures.jsonl",
    }
    paths = {key: output / name for key, name in names.items()}
    detail = set(names) - {"report"}
    for key in detail:
        value = result.get(key)
        if key == "execution_dag":
            if not isinstance(value, dict):
                raise ValueError("authoring execution DAG is missing")
            paths[key].write_bytes(_json_bytes(value, pretty=True))
        else:
            if not isinstance(value, list):
                raise ValueError(f"authoring plan {key} is missing")
            paths[key].write_bytes(_jsonl_bytes(value))
    report = {key: value for key, value in result.items() if key not in detail}
    paths["report"].write_bytes(_json_bytes(report, pretty=True))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in EXPECTED_INPUT_NAMES:
        option = name.replace("_", "-")
        parser.add_argument(f"--{option}", type=Path, required=True)
        parser.add_argument(f"--{option}-binding", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    values = {}
    for name in EXPECTED_INPUT_NAMES:
        path = getattr(args, name)
        values[f"{name}_bytes"] = path.read_bytes()
        values[f"{name}_binding"] = json.loads(
            getattr(args, f"{name}_binding").read_bytes()
        )
    result = compile_uslci_flow_authoring_plan(
        **values, artifact_root=args.artifact_root
    )
    materialize_uslci_flow_authoring_plan(result, args.out_dir)
    detail = {
        "verified_resolution_batch_index",
        "complete_identity_decisions",
        "preserved_branches",
        "authoring_intents",
        "authoring_groups",
        "support_requirements",
        "classification_requirements",
        "canary_actions",
        "authoring_batches",
        "execution_dag",
        "failures",
    }
    print(
        json.dumps(
            {key: value for key, value in result.items() if key not in detail},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
