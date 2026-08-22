"""Compile exact native authoring plans for resolved USLCI reference Flows."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_reference_flow_authoring_plan/v1"
LOGIC_VERSION = "2026-07-17.1"
RESOLUTION_CONTRACT = "lca_research_kernel.uslci_reference_flow_resolution/v1"
INTEGRATION_CONTRACT = (
    "lca_research_kernel.uslci_background_inventory_integration_plan/v1"
)
PROCESS_IDENTITY_CONTRACT = (
    "lca_research_kernel.uslci_process_identity_preflight_action/v1"
)
PROCESS_BUILD_PLAN_CONTRACT = (
    "lca_research_kernel.uslci_process_build_plan_template/v1"
)


TARGET_SPECS: Dict[str, JsonObject] = {
    "fc406690-160c-37d5-bf36-added9542164": {
        "slug": "high-voltage-electricity",
        "authored_name": "Electricity, AC, 2300-7650 V",
        "property_name": "Energy",
        "property_id": "f6811440-ee37-11de-8a39-0800200c9a66",
        "reference_unit": "MJ",
        "observed_units": [
            {
                "unit_id": "92e3bd49-8ed5-4885-9db6-fc88c7afcfcb",
                "unit_name": "MWh",
            }
        ],
        "classification": [
            {"@level": "0", "@classId": "1", "#text": "Ores and minerals; electricity, gas and water"},
            {"@level": "1", "@classId": "17", "#text": "Electricity, town gas, steam and hot water"},
            {"@level": "2", "@classId": "171", "#text": "Electrical energy"},
            {"@level": "3", "@classId": "1710", "#text": "Electrical energy"},
        ],
        "treatment": "Alternating current, high voltage",
        "mix_location": "USLCI source geography retained in evidence, not Flow identity",
    },
    "d1fbd92e-7558-345c-a1d0-1f417667a579": {
        "slug": "power-plant-construction",
        "authored_name": "power plant construction",
        "property_name": "Number",
        "property_id": "01846770-4cfe-4a25-8ad9-919d8d378345",
        "reference_unit": "Item(s)",
        "observed_units": [
            {
                "unit_id": "6dabe201-aaac-4509-92f0-d00c26cb72ab",
                "unit_name": "Item(s)",
            }
        ],
        "classification": [
            {"@level": "0", "@classId": "5", "#text": "Constructions and construction services"},
            {"@level": "1", "@classId": "53", "#text": "Constructions"},
            {"@level": "2", "@classId": "532", "#text": "Civil engineering works"},
            {"@level": "3", "@classId": "5326", "#text": "Mines and industrial plants"},
        ],
        "treatment": "Power-generation infrastructure",
        "mix_location": "USLCI source geography retained in evidence, not Flow identity",
    },
}


ZERO_SIDE_EFFECTS = (
    "network_read_executed_count",
    "remote_candidate_search_executed_count",
    "native_invocation_executed_count",
    "flow_creation_executed_count",
    "flow_draft_save_executed_count",
    "process_identity_preflight_executed_count",
    "process_build_plan_validation_executed_count",
    "process_creation_executed_count",
    "model_connection_executed_count",
    "model_mutation_executed_count",
    "external_write_executed_count",
    "comparison_executed_count",
    "lcia_executed_count",
)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _jsonl_bytes(rows: Sequence[JsonObject]) -> bytes:
    return b"".join(_json_bytes(row) for row in rows)


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


def _parse_jsonl(content: bytes, label: str, *, allow_empty: bool = False) -> List[JsonObject]:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not UTF-8") from error
    rows: List[JsonObject] = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            rows.append(_object(json.loads(line), f"{label} row {index}"))
        except json.JSONDecodeError as error:
            raise ValueError(f"{label} row {index} is not valid JSON") from error
    if not rows and not allow_empty:
        raise ValueError(f"{label} is empty")
    return rows


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


def _iso_millis(value: object) -> str:
    token = _text(value, "planning anchor occurred_at")
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("planning anchor occurred_at is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError("planning anchor occurred_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _path_value(root: JsonObject, path: str) -> object:
    current: object = root
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            raise ValueError(f"downstream action field is missing: {path}")
        current = current[segment]
    return current


def _input_binding_list(
    named: Sequence[Tuple[str, JsonObject]], exchange: Sequence[JsonObject]
) -> List[JsonObject]:
    rows = [dict(binding, role=name) for name, binding in named]
    rows.extend(dict(binding, role="exchange_observations") for binding in exchange)
    return sorted(rows, key=lambda row: (str(row["role"]), str(row["path"])))


def _runtime_identity(report: JsonObject, manifest: JsonObject) -> JsonObject:
    if report.get("status") != "portable_native_tool_runtime_closure_verified":
        raise ValueError("copied runtime report is not verified")
    if report.get("runtime_content_closed") is not True:
        raise ValueError("copied runtime content is not closed")
    tree = _text(report.get("tree_sha256"), "runtime report tree SHA")
    if manifest.get("tree_sha256") != tree:
        raise ValueError("copied runtime report and manifest disagree")
    cli = manifest.get("tiangong_cli")
    if not isinstance(cli, dict):
        cli = manifest.get("cli")
    cli = _object(cli, "runtime TianGong CLI identity")
    if cli.get("package") != "@tiangong-lca/cli" or cli.get("version") != "0.0.25":
        raise ValueError("unsupported TianGong CLI runtime")
    return {
        "runtime_tree_sha256": tree,
        "tiangong_cli_package": "@tiangong-lca/cli",
        "tiangong_cli_version": "0.0.25",
        "same_platform_relocation_proven": report.get(
            "same_platform_relocation_proven"
        )
        is True,
        "cross_platform_portability_proven": False,
    }


def _validate_resolution(
    report: JsonObject, decisions: Sequence[JsonObject], gates: Sequence[JsonObject]
) -> List[JsonObject]:
    if (
        report.get("contract") != RESOLUTION_CONTRACT
        or report.get("status") != "uslci_reference_flow_resolution_compiled"
        or report.get("create_new_intent_count") != 2
        or report.get("waiting_for_flow_authoring_dependency_count") != 8
        or report.get("routing", {}).get("next_internal_route")
        != "compile-uslci-native-reference-flow-authoring-plan"
    ):
        raise ValueError("reference Flow resolution is not the accepted parent route")
    policy = _text(report.get("policy_version"), "resolution policy version")
    selected: List[JsonObject] = []
    seen_sources = set()
    for row in decisions:
        source_id = _text(row.get("source_flow_id"), "decision source Flow ID")
        if source_id not in TARGET_SPECS:
            continue
        if source_id in seen_sources:
            raise ValueError("duplicate target reference Flow decision")
        seen_sources.add(source_id)
        if (
            row.get("resolution_decision") != "create_new_intent"
            or row.get("resolution_status")
            != "resolved_new_local_identity_intent"
            or row.get("remote_duplicate_unknown") is not True
            or row.get("flow_creation_authorized") is not False
            or row.get("flow_materialized") is not False
            or row.get("policy_version") != policy
            or row.get("type_of_dataset") != "Product flow"
            or row.get("proposed_local_uuid_preserved") is not True
            or row.get("target_flow_id") == source_id
        ):
            raise ValueError("reference Flow create-new decision drifted")
        spec = TARGET_SPECS[source_id]
        if (
            row.get("flow_property") != spec["property_name"]
            or row.get("reference_unit") != spec["reference_unit"]
            or row.get("source_flow_name") is None
            or row.get("target_flow_name") is None
        ):
            raise ValueError("reference Flow decision semantics drifted")
        selected.append(row)
    if set(seen_sources) != set(TARGET_SPECS):
        raise ValueError("the two authoring decisions are not both present")
    selected.sort(key=lambda row: _integer(row.get("position"), "decision position"))
    waiting = [
        row
        for row in gates
        if row.get("entity_availability_gate") == "waiting_for_flow_authoring"
    ]
    if len(gates) != 10 or len(waiting) != 8:
        raise ValueError("reference Flow dependency gates drifted")
    if {
        row.get("from_action_key") for row in waiting
    } != {row.get("action_key") for row in selected}:
        raise ValueError("waiting dependency gates do not match authoring decisions")
    return selected


def _validate_integration(
    report: JsonObject,
    mappings: Sequence[JsonObject],
    source_inventory: Sequence[JsonObject],
) -> Dict[str, JsonObject]:
    if (
        report.get("contract") != INTEGRATION_CONTRACT
        or report.get("status")
        != "uslci_background_inventory_integration_plan_compiled"
        or report.get("source_flow_definition_conflict_count") != 0
        or report.get("source_artifact_count") != len(source_inventory)
    ):
        raise ValueError("background integration parent contract drifted")
    selected: Dict[str, JsonObject] = {}
    for row in mappings:
        source_id = row.get("source_flow_id")
        if source_id in TARGET_SPECS:
            if source_id in selected:
                raise ValueError("duplicate Flow mapping requirement")
            spec = TARGET_SPECS[str(source_id)]
            if (
                row.get("source_flow_type") != "PRODUCT_FLOW"
                or row.get("source_reference_unit") != spec["reference_unit"]
                or row.get("application_status") != "proposed_not_applied"
            ):
                raise ValueError("Flow mapping semantics drifted")
            selected[str(source_id)] = row
    if set(selected) != set(TARGET_SPECS):
        raise ValueError("authoring Flow mapping requirements are incomplete")
    return selected


def _inventory_exchange_bindings(
    inventory: Sequence[JsonObject], supplied: Sequence[JsonObject]
) -> List[Tuple[JsonObject, JsonObject]]:
    supplied_by_path: Dict[str, JsonObject] = {}
    for artifact in supplied:
        binding = _object(artifact.get("binding"), "exchange observation binding")
        path = _text(binding.get("path"), "exchange observation path")
        if path in supplied_by_path:
            raise ValueError("duplicate exchange observation path")
        supplied_by_path[path] = artifact
    expected = [row for row in inventory if row.get("role") == "exchange_observations"]
    expected_paths = {_text(row.get("path"), "source inventory exchange path") for row in expected}
    if expected_paths != set(supplied_by_path):
        raise ValueError("registered exchange observation artifact selection drifted")
    result = []
    for row in expected:
        path = _text(row.get("path"), "source inventory exchange path")
        artifact = supplied_by_path[path]
        content = artifact.get("content")
        if not isinstance(content, bytes):
            raise ValueError("exchange observation content must be bytes")
        verified = _binding(artifact.get("binding"), content, f"exchange observation {path}")
        if (
            row.get("verified") is not True
            or row.get("sha256") != verified["sha256"]
            or row.get("bytes") != verified["bytes"]
        ):
            raise ValueError("source inventory exchange binding drifted")
        parsed = _parse_jsonl(content, f"exchange observation {path}", allow_empty=True)
        if row.get("expected_row_count") != len(parsed):
            raise ValueError("exchange observation row count drifted")
        result.append((row, {"binding": verified, "rows": parsed}))
    return result


def _support_assertions(
    decisions: Sequence[JsonObject],
    mappings: Mapping[str, JsonObject],
    exchange_artifacts: Sequence[Tuple[JsonObject, JsonObject]],
) -> List[JsonObject]:
    observations: Dict[str, List[Tuple[str, int, JsonObject]]] = {
        source_id: [] for source_id in TARGET_SPECS
    }
    for inventory, artifact in exchange_artifacts:
        artifact_key = _text(inventory.get("artifact_key"), "source artifact key")
        for index, row in enumerate(_array(artifact.get("rows"), "exchange rows")):
            observation = _object(row, "exchange observation")
            flow = _object(observation.get("flow"), "exchange observation Flow")
            source_id = flow.get("flow_id")
            if source_id in observations:
                observations[str(source_id)].append((artifact_key, index, observation))
    result: List[JsonObject] = []
    for decision in decisions:
        source_id = _text(decision.get("source_flow_id"), "decision source Flow ID")
        spec = TARGET_SPECS[source_id]
        rows = observations[source_id]
        if not rows:
            raise ValueError("source Flow has no normalized exchange observation")
        flow_tuples = set()
        property_tuples = set()
        unit_tuples = set()
        classifications = set()
        quantitative_count = 0
        artifact_keys = set()
        normalized_rows = []
        for artifact_key, index, row in rows:
            flow = _object(row.get("flow"), "exchange Flow")
            prop = _object(row.get("flow_property"), "exchange Flow property")
            unit = _object(row.get("unit"), "exchange unit")
            flow_tuples.add(
                (
                    flow.get("flow_id"),
                    flow.get("flow_name"),
                    flow.get("flow_type"),
                    flow.get("reference_unit"),
                )
            )
            property_tuples.add(
                (
                    prop.get("flow_property_id"),
                    prop.get("flow_property_name"),
                    prop.get("reference_unit"),
                )
            )
            unit_tuples.add((unit.get("unit_id"), unit.get("unit_name")))
            classifications.add(row.get("classification"))
            quantitative_count += row.get("is_quantitative_reference") is True
            artifact_keys.add(artifact_key)
            normalized_rows.append(
                {
                    "artifact_key": artifact_key,
                    "row_index": index,
                    "flow": flow,
                    "flow_property": prop,
                    "unit": unit,
                    "classification": row.get("classification"),
                    "direction": row.get("direction"),
                    "is_quantitative_reference": row.get(
                        "is_quantitative_reference"
                    ),
                }
            )
        if len(flow_tuples) != 1 or len(property_tuples) != 1:
            raise ValueError("conflicting source Flow support tuple")
        expected_flow = (
            source_id,
            decision.get("source_flow_name"),
            "PRODUCT_FLOW",
            spec["reference_unit"],
        )
        expected_property = (
            spec["property_id"],
            spec["property_name"],
            spec["reference_unit"],
        )
        expected_units = {
            (row["unit_id"], row["unit_name"])
            for row in _array(spec["observed_units"], "expected observed units")
        }
        if (
            flow_tuples != {expected_flow}
            or property_tuples != {expected_property}
            or unit_tuples != expected_units
            or classifications != {"technosphere_product"}
        ):
            raise ValueError("conflicting source Flow support tuple")
        mapping_count = _integer(
            mappings[source_id].get("observed_exchange_count"),
            "mapping observation count",
        )
        if mapping_count < len(rows):
            raise ValueError("mapping observation count is smaller than verified observations")
        semantic_digest = _sha256(
            _jsonl_bytes(
                sorted(
                    normalized_rows,
                    key=lambda row: _json_bytes(row),
                )
            )
        )
        result.append(
            {
                "schema_version": 1,
                "source_flow_id": source_id,
                "target_flow_id": decision["target_flow_id"],
                "source_flow_name": decision["source_flow_name"],
                "flow_type": "PRODUCT_FLOW",
                "flow_property_id": spec["property_id"],
                "flow_property_name": spec["property_name"],
                "reference_unit": spec["reference_unit"],
                "observed_unit_ids": sorted(value[0] for value in unit_tuples),
                "observed_unit_names": sorted(value[1] for value in unit_tuples),
                "normalized_observation_count": len(rows),
                "observed_exchange_count": len(rows),
                "mapping_observation_count": mapping_count,
                "quantitative_reference_observation_count": quantitative_count,
                "source_artifact_keys": sorted(artifact_keys),
                "semantic_observations_sha256": semantic_digest,
                "source_tuple_consistent": True,
                "support_reference_source_observed": True,
                "support_reference_local_entity_verified": False,
                "unit_group_identity_known": False,
                "publish_ready": False,
            }
        )
    return result


def _evidence_manifest(
    decision: JsonObject,
    assertion: JsonObject,
    resolution: JsonObject,
    integration: JsonObject,
    planning_anchor: JsonObject,
) -> JsonObject:
    source_id = _text(decision.get("source_flow_id"), "source Flow ID")
    basis = (
        "accepted reference identity decision plus consistent normalized USLCI "
        "Flow/property/unit observations"
    )
    fields = (
        "target",
        "identity_decision.decision",
        "unit_of_analysis",
        "name_plan.base_name",
        "target.flow_type",
        "flow_property_plan.reference_property",
        "flow_property_plan.reference_unit",
    )
    return {
        "summary": (
            "Source-bound USLCI reference Flow authoring plan. Remote duplicate "
            "coverage and local Flow-property/unit-group availability remain open."
        ),
        "sources": [
            {
                "id": source_id,
                "source_kind": "uslci_flow_identity",
                "title": decision["source_flow_name"],
                "version": integration["source_repository_version"],
                "uri": (
                    "https://github.com/linancn/TianGong-LCA-Data-Foundation/"
                    f"tree/v{integration['source_repository_version']}"
                ),
            }
        ],
        "field_bindings": [
            {
                "field_path": field,
                "source_ids": [source_id],
                "basis": basis,
                "decision_id": decision["decision_id"],
                "semantic_observations_sha256": assertion[
                    "semantic_observations_sha256"
                ],
            }
            for field in fields
        ],
        "lineage": {
            "resolution_version": resolution["resolution_version"],
            "resolution_policy_version": resolution["policy_version"],
            "integration_plan_version": integration["plan_version"],
            "planning_anchor_event_id": planning_anchor["event_id"],
        },
    }


def _flow_plan(
    decision: JsonObject,
    assertion: JsonObject,
    resolution: JsonObject,
    integration: JsonObject,
    runtime: JsonObject,
    planning_anchor: JsonObject,
    authored_at: str,
) -> JsonObject:
    source_id = _text(decision.get("source_flow_id"), "source Flow ID")
    spec = TARGET_SPECS[source_id]
    classification = _array(spec["classification"], "classification path")
    target_id = _text(decision.get("target_flow_id"), "target Flow ID")
    authored_name = _text(spec["authored_name"], "authored Flow name")
    evidence = _evidence_manifest(
        decision, assertion, resolution, integration, planning_anchor
    )
    return {
        "schema_version": 1,
        "kind": "flow",
        "ruleset": {"id": "flow-authoring/strict", "version": "1"},
        "target": {
            "uuid": target_id,
            "version": "00.00.001",
            "flow_type": "Product flow",
            "classification_path": [row["#text"] for row in classification],
            "permanent_uri": f"https://data.tiangong.earth/flows/{target_id}.xml",
            "general_comment": (
                f"Source-bound USLCI {integration['source_repository_version']} "
                "reference Flow. U.S. source geography and time are provenance, not "
                "part of the technical Flow base name. Remote duplicate and local "
                "support-reference resolution remain open."
            ),
        },
        "identity_decision": {
            "decision": "create_new",
            "translation_from": "create_new_intent",
            "source_decision_id": decision["decision_id"],
            "source_policy_version": decision["policy_version"],
            "target_id": target_id,
            "remote_duplicate_unknown": True,
        },
        "unit_of_analysis": {
            "decision": "declared_unit_dataset",
            "target_kind": "flow",
            "declared_unit": {"amount": 1, "unit": spec["reference_unit"]},
            "reference_flow": {
                "id": target_id,
                "name": authored_name,
                "reference_amount": 1,
                "reference_unit": spec["reference_unit"],
                "flow_property": spec["property_name"],
            },
        },
        "name_plan": {
            "base_name": authored_name,
            "treatment_standards_routes": spec["treatment"],
            "mix_and_location_types": spec["mix_location"],
        },
        "flow_property_plan": {
            "reference_property": spec["property_name"],
            "reference_property_id": spec["property_id"],
            "reference_property_version": "00.00.000",
            "reference_unit": spec["reference_unit"],
            "mean_value": 1,
            "support_reference_status": (
                "source_identifier_observed_local_entity_unverified"
            ),
        },
        "native_classification_plan": {
            "category_type": "flow-product",
            "leaf_code": classification[-1]["@classId"],
            "path": classification,
            "apply_required_after_materialization": True,
            "reason": (
                "native build-plan materialization accepts labels; native "
                "classification apply owns bundled controlled class codes"
            ),
        },
        "generation_manifest": {
            "contract": CONTRACT,
            "logic_version": LOGIC_VERSION,
            "source_repository_version": integration["source_repository_version"],
            "source_flow_id": source_id,
            "source_flow_name": decision["source_flow_name"],
            "proposed_name": decision["target_flow_name"],
            "authored_name": authored_name,
            "name_policy_correction_applied": (
                authored_name != decision["target_flow_name"]
            ),
            "remote_duplicate_unknown": True,
            "support_reference_source_observed": True,
            "support_reference_local_entity_verified": False,
            "unit_group_identity_known": False,
            "publish_ready": False,
            "runtime_tree_sha256": runtime["runtime_tree_sha256"],
            "planning_anchor_event_id": planning_anchor["event_id"],
        },
        "evidence_manifest": evidence,
        "administrative_information": {
            "time_stamp": authored_at,
            "version": "00.00.001",
        },
        "execution_policy": {
            "saved_plan_required": True,
            "local_staging_only": True,
            "flow_creation_authorized_in_this_activity": False,
            "save_draft_dry_run_only": True,
            "remote_write_allowed": False,
            "publication_allowed": False,
            "model_connection_allowed": False,
        },
    }


def _downstream_patches(
    decisions: Sequence[JsonObject],
    gates: Sequence[JsonObject],
    identity_actions: Sequence[JsonObject],
    build_templates: Sequence[JsonObject],
) -> List[JsonObject]:
    identity_by_key = {
        _text(row.get("action_key"), "Process identity action key"): row
        for row in identity_actions
    }
    build_by_key = {
        _text(row.get("build_plan_action_key"), "Process build-plan action key"): row
        for row in build_templates
    }
    decisions_by_action = {
        _text(row.get("action_key"), "Flow action key"): row for row in decisions
    }
    result: List[JsonObject] = []
    for gate in sorted(gates, key=lambda row: str(row.get("to_action_key"))):
        if gate.get("entity_availability_gate") != "waiting_for_flow_authoring":
            continue
        from_key = _text(gate.get("from_action_key"), "dependency source action")
        to_key = _text(gate.get("to_action_key"), "dependency target action")
        if from_key not in decisions_by_action:
            raise ValueError("waiting dependency has no create-new decision")
        decision = decisions_by_action[from_key]
        source_id = _text(decision.get("source_flow_id"), "source Flow ID")
        spec = TARGET_SPECS[source_id]
        old_name = _text(decision.get("target_flow_name"), "old target Flow name")
        new_name = _text(spec.get("authored_name"), "authored target Flow name")
        kind = gate.get("downstream_action_kind")
        if kind == "process_identity_preflight":
            action = identity_by_key.get(to_key)
            name_paths = (
                "target.reference_flow_name",
                "request.target.reference_flow_name",
            )
            expected_contract = PROCESS_IDENTITY_CONTRACT
        elif kind == "process_build_plan_validate":
            action = build_by_key.get(to_key)
            name_paths = (
                "target.reference_flow_name",
                "quantitative_reference_plan.reference_flow_name",
                "unit_of_analysis.reference_flow.name",
            )
            expected_contract = PROCESS_BUILD_PLAN_CONTRACT
        else:
            raise ValueError("unsupported downstream action kind")
        if not isinstance(action, dict):
            raise ValueError("downstream action is missing")
        if (
            action.get("contract") != expected_contract
            or action.get("depends_on_flow_action_key") != from_key
        ):
            raise ValueError("downstream action dependency drifted")
        target_id = _text(decision.get("target_flow_id"), "target Flow ID")
        if kind == "process_identity_preflight":
            if action.get("reference_flow_target_id") != target_id:
                raise ValueError("downstream action target Flow ID drifted")
        patches = []
        for path in name_paths:
            current = _path_value(action, path)
            if current != old_name:
                raise ValueError("downstream action target Flow name drifted")
            if old_name != new_name:
                patches.append({"path": path, "old": old_name, "new": new_name})
        patch_required = old_name != new_name
        result.append(
            {
                "schema_version": 1,
                "from_flow_action_key": from_key,
                "downstream_action_key": to_key,
                "downstream_action_kind": kind,
                "source_flow_id": source_id,
                "target_flow_id": target_id,
                "old_reference_flow_name": old_name,
                "new_reference_flow_name": new_name,
                "patch_required": patch_required,
                "field_patches": patches,
                "source_action_sha256": _sha256(_json_bytes(action)),
                "disposition": (
                    "invalidate_and_recompile_name_bound_action"
                    if patch_required
                    else "release_after_entity_registered"
                ),
                "old_action_retained_as_history": True,
                "downstream_execution_ready": False,
            }
        )
    if len(result) != 8:
        raise ValueError("expected eight waiting downstream actions")
    return result


def _classification_decisions(
    decisions: Sequence[JsonObject], resolution: JsonObject, integration: JsonObject
) -> List[JsonObject]:
    result = []
    for decision in decisions:
        source_id = _text(decision.get("source_flow_id"), "source Flow ID")
        spec = TARGET_SPECS[source_id]
        path = _array(spec["classification"], "classification path")
        result.append(
            {
                "schema_version": 1,
                "dataset_id": decision["target_flow_id"],
                "dataset_version": "00.00.001",
                "category_type": "flow-product",
                "code": path[-1]["@classId"],
                "path": path,
                "basis": "Bundled TianGong product-flow classification schema",
                "evidence": {
                    "resolution_version": resolution["resolution_version"],
                    "integration_plan_version": integration["plan_version"],
                    "source_flow_id": source_id,
                    "source_classification": "technosphere_product",
                },
            }
        )
    return result


def _execution_dag(
    plan_version: str,
    decisions: Sequence[JsonObject],
    runtime: JsonObject,
) -> JsonObject:
    stage_specs = (
        ("materialize-report-only", "flow build-plan materialize --report-only"),
        ("verify-materialized-semantics", "kernel semantic verifier"),
        ("apply-controlled-classification", "dataset classification apply"),
        ("verify-controlled-classification", "kernel classification verifier"),
        ("validate-flow", "dataset validate --type flow"),
        ("qa-flow", "qa flow"),
        ("save-draft-dry-run", "dataset save-draft --dry-run"),
        ("register-staged-flow", "Research Kernel artifact registration"),
    )
    nodes = []
    edges = []
    branch_terminals = []
    for decision in decisions:
        spec = TARGET_SPECS[_text(decision.get("source_flow_id"), "source Flow ID")]
        slug = _text(spec.get("slug"), "Flow slug")
        prior = None
        for index, (stage, command) in enumerate(stage_specs, start=1):
            action_key = _sha256(
                _json_bytes(
                    {
                        "plan_version": plan_version,
                        "target_flow_id": decision["target_flow_id"],
                        "stage": stage,
                    }
                )
            )[:16]
            node = {
                "node_id": f"{slug}:{stage}",
                "branch": slug,
                "position": index,
                "stage": stage,
                "action_key": action_key,
                "command": command,
                "native": command.startswith(("flow ", "dataset ", "qa ")),
                "effect": (
                    "local_write"
                    if stage
                    in {
                        "materialize-report-only",
                        "apply-controlled-classification",
                        "validate-flow",
                        "qa-flow",
                        "save-draft-dry-run",
                        "register-staged-flow",
                    }
                    else "read_only"
                ),
                "remote_write_allowed": False,
                "publication_allowed": False,
            }
            nodes.append(node)
            if prior:
                edges.append({"from": prior, "to": node["node_id"]})
            prior = str(node["node_id"])
        branch_terminals.append(prior)
    final = {
        "node_id": "authoring-plan:record-project-result",
        "branch": "aggregate",
        "position": 1,
        "stage": "record-project-result",
        "action_key": _sha256(
            _json_bytes({"plan_version": plan_version, "stage": "record-project-result"})
        )[:16],
        "command": "Research Kernel event/projection commit",
        "native": False,
        "effect": "local_write",
        "remote_write_allowed": False,
        "publication_allowed": False,
    }
    nodes.append(final)
    edges.extend({"from": terminal, "to": final["node_id"]} for terminal in branch_terminals)
    return {
        "schema_version": 1,
        "contract": f"{CONTRACT}/execution-dag",
        "plan_version": plan_version,
        "runtime_tree_sha256": runtime["runtime_tree_sha256"],
        "node_count": len(nodes),
        "edge_count": len(edges),
        "maximum_native_concurrency": 2,
        "branch_failure_policy": (
            "retain diagnostics; do not register failed Flow or release its entity gates"
        ),
        "nodes": nodes,
        "edges": edges,
        "side_effect_boundary": {
            "catalog_append_allowed": False,
            "remote_write_allowed": False,
            "save_draft_mode": "dry_run_only",
            "process_execution_allowed": False,
            "model_connection_allowed": False,
        },
    }


def compile_uslci_reference_flow_authoring_plan(
    *,
    resolution_report_bytes: bytes,
    resolution_report_binding: Mapping[str, object],
    decisions_bytes: bytes,
    decisions_binding: Mapping[str, object],
    dependency_gates_bytes: bytes,
    dependency_gates_binding: Mapping[str, object],
    process_identity_actions_bytes: bytes,
    process_identity_actions_binding: Mapping[str, object],
    process_build_plan_templates_bytes: bytes,
    process_build_plan_templates_binding: Mapping[str, object],
    integration_report_bytes: bytes,
    integration_report_binding: Mapping[str, object],
    flow_mapping_requirements_bytes: bytes,
    flow_mapping_requirements_binding: Mapping[str, object],
    source_inventory_bytes: bytes,
    source_inventory_binding: Mapping[str, object],
    runtime_report_bytes: bytes,
    runtime_report_binding: Mapping[str, object],
    runtime_manifest_bytes: bytes,
    runtime_manifest_binding: Mapping[str, object],
    planning_anchor_bytes: bytes,
    planning_anchor_binding: Mapping[str, object],
    exchange_observation_artifacts: Sequence[Mapping[str, object]],
) -> JsonObject:
    named_inputs = (
        ("resolution_report", resolution_report_bytes, resolution_report_binding),
        ("decisions", decisions_bytes, decisions_binding),
        ("dependency_gates", dependency_gates_bytes, dependency_gates_binding),
        (
            "process_identity_actions",
            process_identity_actions_bytes,
            process_identity_actions_binding,
        ),
        (
            "process_build_plan_templates",
            process_build_plan_templates_bytes,
            process_build_plan_templates_binding,
        ),
        ("integration_report", integration_report_bytes, integration_report_binding),
        (
            "flow_mapping_requirements",
            flow_mapping_requirements_bytes,
            flow_mapping_requirements_binding,
        ),
        ("source_inventory", source_inventory_bytes, source_inventory_binding),
        ("runtime_report", runtime_report_bytes, runtime_report_binding),
        ("runtime_manifest", runtime_manifest_bytes, runtime_manifest_binding),
        ("planning_anchor", planning_anchor_bytes, planning_anchor_binding),
    )
    verified_named = [
        (name, _binding(binding, content, f"{name} binding"))
        for name, content, binding in named_inputs
    ]
    resolution = _parse_json(resolution_report_bytes, "reference resolution report")
    decisions = _parse_jsonl(decisions_bytes, "reference decisions")
    gates = _parse_jsonl(dependency_gates_bytes, "reference dependency gates")
    identity_actions = _parse_jsonl(
        process_identity_actions_bytes, "Process identity actions"
    )
    build_templates = _parse_jsonl(
        process_build_plan_templates_bytes, "Process build-plan templates"
    )
    integration = _parse_json(integration_report_bytes, "background integration report")
    mappings = _parse_jsonl(
        flow_mapping_requirements_bytes, "Flow mapping requirements"
    )
    source_inventory = _parse_jsonl(source_inventory_bytes, "source artifact inventory")
    runtime_report = _parse_json(runtime_report_bytes, "copied runtime report")
    runtime_manifest = _parse_json(runtime_manifest_bytes, "copied runtime manifest")
    planning_anchor = _parse_json(planning_anchor_bytes, "planning anchor")
    repository_versions = {
        row.get("evidence_manifest", {}).get("source_repository_version")
        for row in build_templates
        if isinstance(row.get("evidence_manifest"), dict)
        and isinstance(
            row.get("evidence_manifest", {}).get("source_repository_version"), str
        )
    }
    if len(repository_versions) != 1:
        raise ValueError("Process build-plan templates do not bind one source repository version")
    source_repository_version = repository_versions.pop()
    if (
        integration.get("source_repository_version") is not None
        and integration.get("source_repository_version") != source_repository_version
    ):
        raise ValueError("source repository version drifted")
    integration = {
        **integration,
        "source_repository_version": source_repository_version,
    }
    selected_decisions = _validate_resolution(resolution, decisions, gates)
    selected_mappings = _validate_integration(integration, mappings, source_inventory)
    runtime = _runtime_identity(runtime_report, runtime_manifest)
    if (
        planning_anchor.get("event_type")
        != "research.uslci_reference_flow_resolution.compiled"
        or planning_anchor.get("resolution_version")
        != resolution.get("resolution_version")
    ):
        raise ValueError("planning anchor does not bind the accepted resolution event")
    _integer(planning_anchor.get("event_sequence"), "planning anchor event sequence")
    _text(planning_anchor.get("event_id"), "planning anchor event ID")
    authored_at = _iso_millis(planning_anchor.get("occurred_at"))
    selected_exchange = _inventory_exchange_bindings(
        source_inventory,
        [_object(row, "exchange observation artifact") for row in exchange_observation_artifacts],
    )
    exchange_bindings = [
        _object(artifact["binding"], "verified exchange observation binding")
        for _, artifact in selected_exchange
    ]
    assertions = _support_assertions(
        selected_decisions, selected_mappings, selected_exchange
    )
    assertion_by_source = {row["source_flow_id"]: row for row in assertions}
    semantic_basis = {
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "resolution_version": resolution["resolution_version"],
        "resolution_policy_version": resolution["policy_version"],
        "integration_plan_version": integration["plan_version"],
        "source_repository_version": integration["source_repository_version"],
        "runtime": runtime,
        "planning_anchor": {
            "event_id": planning_anchor["event_id"],
            "event_sequence": planning_anchor["event_sequence"],
            "occurred_at": authored_at,
        },
        "decisions": [
            {
                key: row.get(key)
                for key in (
                    "position",
                    "action_key",
                    "decision_id",
                    "source_flow_id",
                    "source_flow_name",
                    "target_flow_id",
                    "target_flow_name",
                    "flow_property",
                    "reference_unit",
                )
            }
            for row in selected_decisions
        ],
        "support_assertions": assertions,
    }
    plan_version = _sha256(_json_bytes(semantic_basis))[:16]
    flow_plans: Dict[str, JsonObject] = {}
    for decision in selected_decisions:
        source_id = _text(decision.get("source_flow_id"), "source Flow ID")
        slug = _text(TARGET_SPECS[source_id].get("slug"), "target Flow slug")
        flow_plans[slug] = _flow_plan(
            decision,
            assertion_by_source[source_id],
            resolution,
            integration,
            runtime,
            planning_anchor,
            authored_at,
        )
    classifications = _classification_decisions(
        selected_decisions, resolution, integration
    )
    patches = _downstream_patches(
        selected_decisions, gates, identity_actions, build_templates
    )
    dag = _execution_dag(plan_version, selected_decisions, runtime)

    plan_paths = {
        "high-voltage-electricity": (
            "outputs/flows/high-voltage-electricity-flow-build-plan.json"
        ),
        "power-plant-construction": (
            "outputs/flows/power-plant-construction-flow-build-plan.json"
        ),
    }
    flow_plan_bytes = {
        slug: _json_bytes(flow_plans[slug]) for slug in sorted(flow_plans)
    }
    index = {
        "schema_version": 1,
        "contract": f"{CONTRACT}/index",
        "plan_version": plan_version,
        "ordered_flow_plans": [
            {
                "position": index + 1,
                "slug": slug,
                "target_flow_id": flow_plans[slug]["target"]["uuid"],
                "path": plan_paths[slug],
                "sha256": _sha256(flow_plan_bytes[slug]),
                "bytes": len(flow_plan_bytes[slug]),
            }
            for index, slug in enumerate(
                [
                    _text(
                        TARGET_SPECS[
                            _text(row.get("source_flow_id"), "source Flow ID")
                        ].get("slug"),
                        "Flow slug",
                    )
                    for row in selected_decisions
                ]
            )
        ],
        "apply_requires_exact_plan_bytes": True,
        "runtime_tree_sha256": runtime["runtime_tree_sha256"],
    }
    paths = {
        "plan_index": "outputs/uslci-reference-flow-authoring-plan-index.json",
        "classifications": "outputs/uslci-reference-flow-classification-decisions.jsonl",
        "support_assertions": "outputs/uslci-reference-flow-support-reference-assertions.jsonl",
        "downstream_patches": "outputs/uslci-reference-flow-downstream-action-patches.jsonl",
        "execution_dag": "outputs/uslci-reference-flow-authoring-execution-dag.json",
    }
    product_contents = {
        paths["plan_index"]: _json_bytes(index),
        plan_paths["high-voltage-electricity"]: flow_plan_bytes[
            "high-voltage-electricity"
        ],
        plan_paths["power-plant-construction"]: flow_plan_bytes[
            "power-plant-construction"
        ],
        paths["classifications"]: _jsonl_bytes(classifications),
        paths["support_assertions"]: _jsonl_bytes(assertions),
        paths["downstream_patches"]: _jsonl_bytes(patches),
        paths["execution_dag"]: _json_bytes(dag),
    }
    input_bindings = _input_binding_list(verified_named, exchange_bindings)
    report: JsonObject = {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "status": "uslci_reference_flow_authoring_plan_compiled",
        "plan_version": plan_version,
        "resolution_version": resolution["resolution_version"],
        "resolution_policy_version": resolution["policy_version"],
        "native_preflight_plan_version": resolution["plan_version"],
        "integration_plan_version": integration["plan_version"],
        "source_repository_version": integration["source_repository_version"],
        "planning_anchor_event_id": planning_anchor["event_id"],
        "planning_anchor_event_sequence": planning_anchor["event_sequence"],
        "planned_flow_count": 2,
        "technical_name_correction_count": 1,
        "classification_decision_count": len(classifications),
        "support_reference_assertion_count": len(assertions),
        "support_reference_local_entity_verified_count": 0,
        "support_reference_reconciliation_required_count": 2,
        "unit_group_identity_known_count": 0,
        "waiting_dependency_count": len(patches),
        "downstream_name_patch_count": sum(
            row["patch_required"] is True for row in patches
        ),
        "downstream_release_only_count": sum(
            row["patch_required"] is False for row in patches
        ),
        "execution_dag_node_count": dag["node_count"],
        "execution_dag_edge_count": dag["edge_count"],
        "runtime": runtime,
        "input_bindings": input_bindings,
        "artifact_bindings": {
            key: _artifact_binding(path, product_contents[path])
            for key, path in {
                "plan_index": paths["plan_index"],
                "high_voltage_flow_plan": plan_paths["high-voltage-electricity"],
                "power_plant_construction_flow_plan": plan_paths[
                    "power-plant-construction"
                ],
                "classifications": paths["classifications"],
                "support_assertions": paths["support_assertions"],
                "downstream_patches": paths["downstream_patches"],
                "execution_dag": paths["execution_dag"],
            }.items()
        },
        "scientific_boundaries": {
            "remote_duplicate_unknown_count": 2,
            "us_to_cn_representativeness_proven": False,
            "publish_ready": False,
            "scientific_comparison_ready": False,
            "comparative_lcia_allowed": False,
        },
        "routing": {
            "next_internal_route": (
                "execute-uslci-native-reference-flow-authoring-plan"
            ),
            "pause_internal_work": False,
        },
        "next_action": (
            "Execute the exact saved two-Flow plan in isolated local staging through "
            "native materialization, controlled classification, validation, QA and "
            "save-draft dry-run."
        ),
    }
    for key in ZERO_SIDE_EFFECTS:
        report[key] = 0
    report["scientific_comparison_ready"] = False
    report["comparative_lcia_allowed"] = False
    return {
        "report": report,
        "plan_index": index,
        "flow_plans": flow_plans,
        "classification_decisions": classifications,
        "support_reference_assertions": assertions,
        "downstream_patches": patches,
        "execution_dag": dag,
        "product_contents": product_contents,
    }


def materialize_uslci_reference_flow_authoring_plan(
    result: Mapping[str, object], out_dir: Path
) -> Dict[str, Path]:
    root = out_dir.resolve()
    products = _object(result.get("product_contents"), "authoring plan products")
    paths: Dict[str, Path] = {}
    for relative, content in products.items():
        if not isinstance(relative, str) or not isinstance(content, bytes):
            raise ValueError("authoring plan product is invalid")
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        paths[relative] = target
    report_path = root / "outputs/uslci-reference-flow-authoring-plan.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(_json_bytes(result["report"]))
    paths["outputs/uslci-reference-flow-authoring-plan.json"] = report_path
    return paths


def _read_binding(path: Path) -> JsonObject:
    return _parse_json(path.read_bytes(), f"binding {path}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    names = (
        "resolution-report",
        "decisions",
        "dependency-gates",
        "process-identity-actions",
        "process-build-plan-templates",
        "integration-report",
        "flow-mapping-requirements",
        "source-inventory",
        "runtime-report",
        "runtime-manifest",
        "planning-anchor",
    )
    for name in names:
        parser.add_argument(f"--{name}", type=Path, required=True)
        parser.add_argument(f"--{name}-binding", type=Path, required=True)
    parser.add_argument(
        "--exchange-observation", type=Path, action="append", required=True
    )
    parser.add_argument(
        "--exchange-observation-binding", type=Path, action="append", required=True
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if len(args.exchange_observation) != len(args.exchange_observation_binding):
        raise ValueError("exchange observation paths and bindings must be paired")
    names = (
        "resolution_report",
        "decisions",
        "dependency_gates",
        "process_identity_actions",
        "process_build_plan_templates",
        "integration_report",
        "flow_mapping_requirements",
        "source_inventory",
        "runtime_report",
        "runtime_manifest",
        "planning_anchor",
    )
    kwargs: JsonObject = {}
    for name in names:
        path = getattr(args, name)
        binding_path = getattr(args, f"{name}_binding")
        kwargs[f"{name}_bytes"] = path.read_bytes()
        kwargs[f"{name}_binding"] = _read_binding(binding_path)
    kwargs["exchange_observation_artifacts"] = [
        {"content": path.read_bytes(), "binding": _read_binding(binding_path)}
        for path, binding_path in zip(
            args.exchange_observation, args.exchange_observation_binding
        )
    ]
    result = compile_uslci_reference_flow_authoring_plan(**kwargs)
    materialize_uslci_reference_flow_authoring_plan(result, args.out_dir)
    print(json.dumps(result["report"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
