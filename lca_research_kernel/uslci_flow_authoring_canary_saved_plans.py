"""Compile exact saved TianGong Flow build plans for five authoring canaries."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_flow_authoring_canary_saved_plans/v1"
LOGIC_VERSION = "2026-07-18.1"
POLICY_VERSION = "2026-07-18.1"
SUPPORT_CONTRACT = "lca_research_kernel.uslci_flow_authoring_canary_supports/v1"
AUTHORING_CONTRACT = "lca_research_kernel.uslci_flow_authoring_plan/v1"
NEXT_ROUTE = "execute-uslci-flow-authoring-canary-native-gates"
EXPECTED_INPUT_NAMES = (
    "support_resolution_report",
    "source_observations",
    "support_contracts",
    "classification_decisions",
    "canary_readiness",
    "risk_markers",
    "authoring_plan_report",
    "authoring_intents",
    "canary_actions",
    "runtime_report",
    "runtime_manifest",
    "planning_anchor",
)

ZERO_SIDE_EFFECTS = (
    "native_invocation_executed_count",
    "network_read_executed_count",
    "remote_candidate_search_executed_count",
    "flow_creation_executed_count",
    "flow_materialization_executed_count",
    "save_draft_executed_count",
    "process_creation_executed_count",
    "model_connection_executed_count",
    "model_mutation_executed_count",
    "external_write_executed_count",
    "publication_executed_count",
    "comparison_executed_count",
    "lcia_executed_count",
)


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
                "inputs": list(bindings),
            }
        )
    )[:16]


def _iso_millis(value: object) -> str:
    token = _text(value, "planning anchor occurred_at")
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("planning anchor occurred_at is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError("planning anchor occurred_at must include timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    if not slug:
        raise ValueError("canary name cannot form a plan slug")
    return slug[:80]


def _runtime_identity(report: Mapping[str, object], manifest: Mapping[str, object]) -> JsonObject:
    if (
        report.get("status") != "portable_native_tool_runtime_closure_verified"
        or report.get("runtime_content_closed") is not True
    ):
        raise ValueError("copied runtime is not verified and content-closed")
    tree = _text(report.get("tree_sha256"), "runtime tree SHA-256")
    if manifest.get("tree_sha256") != tree:
        raise ValueError("runtime report and manifest tree disagree")
    cli = _object(manifest.get("tiangong_cli"), "TianGong CLI identity")
    if cli.get("package") != "@tiangong-lca/cli" or cli.get("version") != "0.0.25":
        raise ValueError("unsupported copied TianGong CLI")
    return {
        "runtime_tree_sha256": tree,
        "tiangong_cli_package": cli["package"],
        "tiangong_cli_version": cli["version"],
        "same_platform_relocation_proven": report.get(
            "same_platform_relocation_proven"
        )
        is True,
        "cross_platform_portability_proven": False,
    }


def compile_canary_flow_plan(
    intent_value: Mapping[str, object],
    canary_value: Mapping[str, object],
    observation_value: Mapping[str, object],
    support_value: Mapping[str, object],
    decision_value: Mapping[str, object],
    readiness_value: Mapping[str, object],
    risk_value: Mapping[str, object] | None,
    *,
    planning_time: str,
    runtime_tree_sha256: str,
    support_resolution_version: str,
) -> Tuple[JsonObject, JsonObject, JsonObject]:
    """Render one exact native Flow build plan from accepted evidence."""

    intent = _object(intent_value, "authoring intent")
    canary = _object(canary_value, "canary action")
    observation = _object(observation_value, "source observation")
    support = _object(support_value, "support contract")
    decision = _object(decision_value, "classification decision")
    readiness = _object(readiness_value, "canary readiness")
    intent_key = _text(intent.get("authoring_intent_key"), "authoring intent key")
    source_id = _text(intent.get("source_flow_id"), "source Flow ID")
    target = _object(intent.get("target"), "authoring target")
    target_id = _text(target.get("id"), "target Flow ID")
    target_name = _text(target.get("name"), "target Flow name")
    canary_target = _object(canary.get("target"), "canary target")
    if (
        canary.get("authoring_intent_key") != intent_key
        or decision.get("authoring_intent_key") != intent_key
        or readiness.get("authoring_intent_key") != intent_key
        or observation.get("source_flow_id") != source_id
        or decision.get("source_flow_id") != source_id
        or readiness.get("source_flow_id") != source_id
        or readiness.get("target_flow_id") != target_id
        or canary_target != target
    ):
        raise ValueError("canary identity chain drifted")
    if (
        observation.get("source_flow_name") != target_name
        or decision.get("source_flow_name") != target_name
        or intent.get("source_flow_name") != target_name
    ):
        raise ValueError("canary technical name drifted")
    property_id = _text(intent.get("flow_property_id"), "FlowProperty ID")
    property_name = _text(intent.get("flow_property_name"), "FlowProperty name")
    reference_unit = _text(intent.get("reference_unit"), "reference unit")
    if (
        support.get("flow_property_id") != property_id
        or support.get("flow_property_name") != property_name
        or support.get("reference_unit") != reference_unit
        or observation.get("flow_property_id") != property_id
        or observation.get("flow_property_name") != property_name
        or observation.get("reference_unit") != reference_unit
        or readiness.get("flow_property_id") != property_id
        or readiness.get("flow_property_version")
        != support.get("flow_property_version")
        or readiness.get("unit_group_id") != support.get("unit_group_id")
        or readiness.get("unit_group_version") != support.get("unit_group_version")
        or readiness.get("reference_unit") != reference_unit
        or target.get("flow_property") != property_name
        or target.get("reference_unit") != reference_unit
    ):
        raise ValueError("canary support tuple drifted")
    if (
        support.get("support_status") != "resolved_for_native_saved_plan"
        or support.get("saved_plan_reference_authorized") is not True
        or decision.get("saved_plan_compilation_authorized") is not True
        or readiness.get("saved_plan_compilation_authorized") is not True
    ):
        raise ValueError("canary is not authorized for saved-plan compilation")
    classification_path = [
        _object(value, "classification path node")
        for value in _array(
            decision.get("classification_path"), "classification path"
        )
    ]
    classification_code = _text(
        decision.get("classification_code"), "classification code"
    )
    if (
        not classification_path
        or readiness.get("classification_code") != classification_code
        or classification_path[-1].get("#text")
        != decision.get("classification_leaf_label")
    ):
        raise ValueError("canary classification path drifted")
    review_required = decision.get("review_required") is True
    if review_required:
        if not isinstance(risk_value, dict):
            raise ValueError("required review marker was silently dropped")
        risk = dict(risk_value)
        if (
            readiness.get("review_risk_id") != risk.get("risk_id")
            or risk.get("authoring_intent_key") != intent_key
            or risk.get("source_flow_id") != source_id
            or risk.get("blocks_saved_plan_compilation") is not False
        ):
            raise ValueError("canary review marker drifted")
        review_markers = [
            {
                "risk_id": risk.get("risk_id"),
                "priority": risk.get("severity"),
                "reason": risk.get("summary"),
                "blocks_saved_plan_compilation": False,
                "blocks_bulk_release": True,
                "human_review_retained": True,
            }
        ]
    else:
        if risk_value is not None or readiness.get("review_risk_id") is not None:
            raise ValueError("unexpected canary review marker")
        review_markers = []
    flow_type = _text(target.get("type_of_dataset"), "target Flow type")
    if flow_type not in {"Elementary flow", "Product flow"}:
        raise ValueError("canary target Flow type is unsupported")
    if (
        (flow_type == "Elementary flow" and decision.get("category_type") != "flow-elementary")
        or (flow_type == "Product flow" and decision.get("category_type") != "flow-product")
    ):
        raise ValueError("classification schema and target Flow type disagree")
    occurred_at = _iso_millis(planning_time)
    observation_hash = _text(
        observation.get("source_observation_sha256"), "source observation SHA-256"
    )
    decision_hash = _sha256(_json_bytes(decision))
    support_hash = _sha256(_json_bytes(support))
    field_paths = (
        "target",
        "identity_decision.decision",
        "unit_of_analysis",
        "name_plan.base_name",
        "target.flow_type",
        "flow_property_plan.reference_property",
        "flow_property_plan.reference_unit",
        "native_classification_plan",
    )
    evidence_bindings = [
        {
            "field_path": field_path,
            "source_flow_id": source_id,
            "source_observation_sha256": observation_hash,
            "classification_decision_sha256": decision_hash,
            "support_contract_sha256": support_hash,
            "basis": (
                "accepted authoring identity plus bound USLCI observation, native "
                "classification decision and TianGong support contract"
            ),
        }
        for field_path in field_paths
    ]
    slug = _slug(target_name)
    plan = {
        "schema_version": 1,
        "kind": "flow",
        "ruleset": {"id": "flow-authoring/strict", "version": "1"},
        "target": {
            "uuid": target_id,
            "version": "00.00.001",
            "flow_type": flow_type,
            "classification_path": [
                _text(node.get("#text"), "classification label")
                for node in classification_path
            ],
            "permanent_uri": f"https://data.tiangong.earth/flows/{target_id}.xml",
            "general_comment": (
                "Source-bound USLCI Flow canary. Source category and TianGong native "
                "classification/support evidence are retained separately. Remote duplicate, "
                "publication readiness and characterization remain unresolved."
            ),
        },
        "identity_decision": {
            "decision": "create_new",
            "translation_from": "create_new_intent",
            "source_action_key": intent.get("source_action_key"),
            "source_decision_id": intent.get("source_decision_id"),
            "target_id": target_id,
            "remote_duplicate_unknown": intent.get("remote_duplicate_unknown") is True,
        },
        "name_plan": {
            "base_name": target_name,
            "treatment_standards_routes": "No additional treatment or route asserted beyond accepted technical name.",
            "mix_and_location_types": "USLCI source geography and time remain provenance, not Flow identity.",
        },
        "native_classification_plan": {
            "category_type": decision.get("category_type"),
            "leaf_code": classification_code,
            "path": classification_path,
            "source_decision_status": decision.get("decision_status"),
            "source_confidence": decision.get("confidence"),
            "apply_required_after_materialization": True,
        },
        "flow_property_plan": {
            "mean_value": 1,
            "reference_property": property_name,
            "reference_property_id": property_id,
            "reference_property_version": support.get("flow_property_version"),
            "reference_property_uri": support.get("flow_property_uri"),
            "reference_unit_group": support.get("unit_group_name"),
            "reference_unit_group_id": support.get("unit_group_id"),
            "reference_unit_group_version": support.get("unit_group_version"),
            "reference_unit_group_uri": support.get("unit_group_uri"),
            "reference_unit": reference_unit,
            "support_reference_status": "resolved_for_native_saved_plan",
        },
        "unit_of_analysis": {
            "decision": "declared_unit_dataset",
            "target_kind": "flow",
            "declared_unit": {"amount": 1, "unit": reference_unit},
            "reference_flow": {
                "id": target_id,
                "name": target_name,
                "reference_amount": 1,
                "reference_unit": reference_unit,
                "flow_property": property_name,
            },
        },
        "administrative_information": {
            "time_stamp": occurred_at,
            "version": "00.00.001",
        },
        "evidence_manifest": {
            "sources": [
                {
                    "id": source_id,
                    "source_kind": "uslci_flow_observation",
                    "title": target_name,
                    "uri": observation.get("source_request_url"),
                    "sha256": observation_hash,
                },
                {
                    "id": property_id,
                    "source_kind": "tiangong_flow_property",
                    "title": property_name,
                    "uri": support.get("flow_property_uri"),
                    "version": support.get("flow_property_version"),
                },
                {
                    "id": support.get("unit_group_id"),
                    "source_kind": "tiangong_unit_group",
                    "title": support.get("unit_group_name"),
                    "uri": support.get("unit_group_uri"),
                    "version": support.get("unit_group_version"),
                },
            ],
            "field_bindings": evidence_bindings,
            "lineage": {
                "support_resolution_version": support_resolution_version,
                "runtime_tree_sha256": runtime_tree_sha256,
                "authoring_intent_key": intent_key,
            },
            "summary": (
                "Evidence-bound USLCI canary Flow plan; exact classification and support "
                "references are resolved, while remote uniqueness and publication remain open."
            ),
        },
        "review_markers": review_markers,
        "execution_policy": {
            "saved_plan_required": True,
            "apply_requires_exact_plan_bytes": True,
            "local_report_only_next": True,
            "flow_creation_authorized_in_this_activity": False,
            "flow_materialization_authorized_in_this_activity": False,
            "save_draft_allowed": False,
            "remote_write_allowed": False,
            "model_connection_allowed": False,
            "publication_allowed": False,
            "bulk_release_allowed": False,
        },
        "generation_manifest": {
            "contract": CONTRACT,
            "logic_version": LOGIC_VERSION,
            "policy_version": POLICY_VERSION,
            "canary_index": canary.get("canary_index"),
            "source_flow_id": source_id,
            "target_flow_id": target_id,
            "authoring_intent_key": intent_key,
            "support_resolution_version": support_resolution_version,
            "runtime_tree_sha256": runtime_tree_sha256,
            "remote_duplicate_unknown": True,
            "publish_ready": False,
        },
    }
    assertion = {
        "schema_version": 1,
        "canary_index": _integer(canary.get("canary_index"), "canary index"),
        "authoring_intent_key": intent_key,
        "source_flow_id": source_id,
        "target_flow_id": target_id,
        "required_field_count": len(field_paths),
        "bound_required_field_count": len(evidence_bindings),
        "all_required_fields_bound": True,
        "classification_path_bound": True,
        "support_contract_bound": True,
        "review_marker_required": review_required,
        "review_marker_preserved": len(review_markers) == (1 if review_required else 0),
        "missing_fields": [],
        "status": "saved_plan_complete",
    }
    descriptor = {
        "schema_version": 1,
        "canary_index": assertion["canary_index"],
        "authoring_intent_key": intent_key,
        "source_flow_id": source_id,
        "target_flow_id": target_id,
        "slug": slug,
        "classification_code": classification_code,
        "flow_property_id": property_id,
        "reference_unit": reference_unit,
        "review_marker_count": len(review_markers),
        "status": "saved_plan_compiled_pending_native_gates",
        "entity_creation_authorized": False,
        "bulk_release_authorized": False,
    }
    return plan, assertion, descriptor


def build_plan_index(
    plans: Sequence[Tuple[Mapping[str, object], bytes]],
    *,
    runtime_tree_sha256: str,
    support_resolution_version: str,
) -> JsonObject:
    """Bind exact plan bytes into an ordered immutable apply index."""

    if not plans:
        raise ValueError("saved Flow plan set is empty")
    target_ids = [row.get("target_flow_id") for row, _ in plans]
    source_ids = [row.get("source_flow_id") for row, _ in plans]
    if len(set(target_ids)) != len(target_ids):
        raise ValueError("saved Flow plans contain duplicate target IDs")
    if len(set(source_ids)) != len(source_ids):
        raise ValueError("saved Flow plans contain duplicate source IDs")
    ordered = []
    for descriptor_value, content in sorted(
        plans, key=lambda pair: int(pair[0].get("canary_index", 0))
    ):
        descriptor = _object(descriptor_value, "saved plan descriptor")
        canary_index = _integer(descriptor.get("canary_index"), "canary index")
        slug = _text(descriptor.get("slug"), "plan slug")
        ordered.append(
            {
                "canary_index": canary_index,
                "source_flow_id": _text(
                    descriptor.get("source_flow_id"), "source Flow ID"
                ),
                "target_flow_id": _text(
                    descriptor.get("target_flow_id"), "target Flow ID"
                ),
                "slug": slug,
                "path": f"outputs/plans/{canary_index:02d}-{slug}-flow-build-plan.json",
                "sha256": _sha256(content),
                "bytes": len(content),
            }
        )
    version = _sha256(
        _json_bytes(
            {
                "contract": f"{CONTRACT}/index",
                "runtime_tree_sha256": runtime_tree_sha256,
                "support_resolution_version": support_resolution_version,
                "plans": ordered,
            }
        )
    )[:16]
    return {
        "schema_version": 1,
        "contract": f"{CONTRACT}/index",
        "plan_set_version": version,
        "runtime_tree_sha256": runtime_tree_sha256,
        "support_resolution_version": support_resolution_version,
        "apply_requires_exact_plan_bytes": True,
        "ordered_flow_plans": ordered,
    }


def _execution_dag(descriptors: Sequence[Mapping[str, object]]) -> JsonObject:
    nodes = []
    edges = []
    for row in descriptors:
        index = int(row["canary_index"])
        compile_id = f"compile-canary-{index:02d}"
        gate_id = f"native-gates-canary-{index:02d}"
        nodes.extend(
            [
                {"node_id": compile_id, "phase": "saved_plan", "status": "completed"},
                {"node_id": gate_id, "phase": "native_report_only_gates", "status": "pending"},
            ]
        )
        edges.append({"from": compile_id, "to": gate_id})
        edges.append({"from": gate_id, "to": "canary-analysis-gate"})
    nodes.append(
        {
            "node_id": "canary-analysis-gate",
            "phase": "bulk_release_analysis",
            "status": "pending",
            "bulk_release_authorized": False,
        }
    )
    return {
        "schema_version": 1,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
    }


def compile_and_materialize(
    *,
    contents: Mapping[str, bytes],
    bindings: Mapping[str, Mapping[str, object]],
    out_dir: Path,
) -> JsonObject:
    verified_bindings = [
        _binding(bindings[name], contents[name], f"{name} binding")
        for name in EXPECTED_INPUT_NAMES
    ]
    input_version = input_version_for_bindings(verified_bindings)
    support_report = _parse_json(
        contents["support_resolution_report"], "support resolution report"
    )
    authoring_report = _parse_json(
        contents["authoring_plan_report"], "authoring plan report"
    )
    if (
        support_report.get("contract") != SUPPORT_CONTRACT
        or support_report.get("status")
        != "uslci_flow_authoring_canary_supports_resolved"
        or support_report.get("ready_canary_count") != 5
        or support_report.get("support_contract_count") != 3
        or support_report.get("review_marker_count") != 4
        or support_report.get("routing", {}).get("next_internal_route")
        != "compile-uslci-flow-authoring-canary-saved-plans"
        or authoring_report.get("contract") != AUTHORING_CONTRACT
        or authoring_report.get("status") != "uslci_flow_authoring_plan_compiled"
        or authoring_report.get("canary_action_count") != 5
    ):
        raise ValueError("upstream authoring/support route is not accepted")
    if support_report.get("source_authoring_plan_version") != authoring_report.get(
        "authoring_plan_version"
    ):
        raise ValueError("support resolution and authoring plan versions disagree")
    for name in (
        "source_observations",
        "support_contracts",
        "classification_decisions",
        "canary_readiness",
        "risk_markers",
    ):
        expected = _object(
            support_report.get("artifact_bindings", {}).get(name),
            f"support resolution {name} binding",
        )
        if (
            expected.get("sha256") != bindings[name].get("sha256")
            or expected.get("bytes") != bindings[name].get("bytes")
        ):
            raise ValueError(f"support resolution artifact drifted: {name}")
    for name in ("authoring_intents", "canary_actions"):
        expected = _object(
            authoring_report.get("artifact_bindings", {}).get(name),
            f"authoring {name} binding",
        )
        if (
            expected.get("sha256") != bindings[name].get("sha256")
            or expected.get("bytes") != bindings[name].get("bytes")
        ):
            raise ValueError(f"authoring artifact drifted: {name}")
    observations = _parse_jsonl(contents["source_observations"], "source observations")
    supports = _parse_jsonl(contents["support_contracts"], "support contracts")
    decisions = _parse_jsonl(
        contents["classification_decisions"], "classification decisions"
    )
    readiness_rows = _parse_jsonl(contents["canary_readiness"], "canary readiness")
    risks = _parse_jsonl(contents["risk_markers"], "risk markers")
    intents = _parse_jsonl(contents["authoring_intents"], "authoring intents")
    canaries = _parse_jsonl(contents["canary_actions"], "canary actions")
    if not (
        len(observations) == 5
        and len(supports) == 3
        and len(decisions) == 5
        and len(readiness_rows) == 5
        and len(risks) == 4
        and len(canaries) == 5
    ):
        raise ValueError("upstream canary evidence counts drifted")
    runtime_report = _parse_json(contents["runtime_report"], "runtime report")
    runtime_manifest = _parse_json(contents["runtime_manifest"], "runtime manifest")
    runtime = _runtime_identity(runtime_report, runtime_manifest)
    planning_anchor = _parse_json(contents["planning_anchor"], "planning anchor")
    if (
        planning_anchor.get("support_resolution_version")
        != support_report.get("support_resolution_version")
        or planning_anchor.get("support_resolution_report_sha256")
        != bindings["support_resolution_report"].get("sha256")
    ):
        raise ValueError("planning anchor does not bind the support resolution")
    planning_time = _iso_millis(planning_anchor.get("occurred_at"))
    canary_keys = {row.get("authoring_intent_key") for row in canaries}
    selected_intents = {
        row.get("authoring_intent_key"): row
        for row in intents
        if row.get("authoring_intent_key") in canary_keys
    }
    if set(selected_intents) != canary_keys or len(selected_intents) != 5:
        raise ValueError("five canary authoring intents were not recovered")
    observation_by_id = {row.get("source_flow_id"): row for row in observations}
    support_by_id = {row.get("flow_property_id"): row for row in supports}
    decision_by_key = {row.get("authoring_intent_key"): row for row in decisions}
    readiness_by_key = {
        row.get("authoring_intent_key"): row for row in readiness_rows
    }
    risk_by_key = {row.get("authoring_intent_key"): row for row in risks}
    plans = []
    assertions = []
    descriptors = []
    plan_contents = []
    for canary in sorted(canaries, key=lambda row: int(row.get("canary_index", 0))):
        intent_key = canary.get("authoring_intent_key")
        intent = selected_intents[intent_key]
        source_id = intent.get("source_flow_id")
        property_id = intent.get("flow_property_id")
        plan, assertion, descriptor = compile_canary_flow_plan(
            intent,
            canary,
            observation_by_id.get(source_id, {}),
            support_by_id.get(property_id, {}),
            decision_by_key.get(intent_key, {}),
            readiness_by_key.get(intent_key, {}),
            risk_by_key.get(intent_key),
            planning_time=planning_time,
            runtime_tree_sha256=str(runtime["runtime_tree_sha256"]),
            support_resolution_version=str(
                support_report["support_resolution_version"]
            ),
        )
        content = _json_bytes(plan, pretty=True)
        plans.append(plan)
        assertions.append(assertion)
        descriptors.append(descriptor)
        plan_contents.append((descriptor, content))
    index = build_plan_index(
        plan_contents,
        runtime_tree_sha256=str(runtime["runtime_tree_sha256"]),
        support_resolution_version=str(support_report["support_resolution_version"]),
    )
    dag = _execution_dag(descriptors)
    review_carry = [
        {
            "schema_version": 1,
            "risk_id": row.get("risk_id"),
            "authoring_intent_key": row.get("authoring_intent_key"),
            "source_flow_id": row.get("source_flow_id"),
            "severity": row.get("severity"),
            "summary": row.get("summary"),
            "saved_plan_compilation_completed": True,
            "native_gate_review_retained": True,
            "bulk_release_blocked": True,
        }
        for row in sorted(risks, key=lambda value: str(value.get("risk_id")))
    ]
    output_rows = {
        "plan_index": (
            "uslci-flow-canary-saved-plan-index.json",
            _json_bytes(index, pretty=True),
        ),
        "field_assertions": (
            "uslci-flow-canary-saved-plan-field-assertions.jsonl",
            _jsonl_bytes(assertions),
        ),
        "execution_descriptors": (
            "uslci-flow-canary-saved-plan-execution-descriptors.jsonl",
            _jsonl_bytes(descriptors),
        ),
        "review_marker_carry_forward": (
            "uslci-flow-canary-saved-plan-review-markers.jsonl",
            _jsonl_bytes(review_carry),
        ),
        "execution_dag": (
            "uslci-flow-canary-saved-plan-execution-dag.json",
            _json_bytes(dag, pretty=True),
        ),
        "failures": ("uslci-flow-canary-saved-plan-failures.jsonl", b""),
    }
    artifact_bindings = {
        key: _artifact_binding(filename, content)
        for key, (filename, content) in output_rows.items()
    }
    plan_bindings = []
    for descriptor, content in plan_contents:
        index_row = next(
            row
            for row in index["ordered_flow_plans"]
            if row["canary_index"] == descriptor["canary_index"]
        )
        plan_bindings.append(dict(index_row))
    result = {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "policy_version": POLICY_VERSION,
        "status": "uslci_flow_authoring_canary_saved_plans_compiled",
        "scope_id": support_report.get("scope_id"),
        "revision": support_report.get("revision"),
        "input_version": input_version,
        "source_authoring_plan_version": authoring_report.get("authoring_plan_version"),
        "source_support_resolution_version": support_report.get(
            "support_resolution_version"
        ),
        "saved_plan_set_version": index["plan_set_version"],
        "input_bindings": verified_bindings,
        "runtime_identity": runtime,
        "artifact_bindings": artifact_bindings,
        "saved_plan_bindings": plan_bindings,
        "saved_flow_plan_count": len(plans),
        "complete_plan_count": sum(
            row["all_required_fields_bound"] for row in assertions
        ),
        "unique_source_flow_count": len(
            {row["source_flow_id"] for row in descriptors}
        ),
        "unique_target_flow_count": len(
            {row["target_flow_id"] for row in descriptors}
        ),
        "classification_path_bound_count": sum(
            row["classification_path_bound"] for row in assertions
        ),
        "support_contract_bound_count": sum(
            row["support_contract_bound"] for row in assertions
        ),
        "represented_support_contract_count": len(
            {row["flow_property_id"] for row in descriptors}
        ),
        "review_marker_carry_forward_count": len(review_carry),
        "missing_required_field_count": sum(
            len(row["missing_fields"]) for row in assertions
        ),
        "execution_dag_node_count": dag["node_count"],
        "execution_dag_edge_count": dag["edge_count"],
        "planning_failure_count": 0,
        "entity_creation_authorized_count": 0,
        "bulk_release_authorized": False,
        **{key: 0 for key in ZERO_SIDE_EFFECTS},
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "external_patterns": [
            {
                "project": "hashicorp/terraform",
                "local_landing": "exact_saved_plan_bytes_runtime_and_upstream_version_binding",
                "installed": False,
            },
            {
                "project": "helm/helm",
                "local_landing": "rendered_plan_separated_from_native_validation_and_save",
                "installed": False,
            },
            {
                "project": "open-policy-agent/opa",
                "local_landing": "classification_decisions_and_review_markers_copied_not_recomputed",
                "installed": False,
            },
            {
                "project": "argoproj/argo-rollouts",
                "local_landing": "five_canary_only_plan_set_with_bulk_release_closed",
                "installed": False,
            },
        ],
        "routing": {"next_internal_route": NEXT_ROUTE, "pause_internal_work": False},
        "next_action": (
            "Execute copied-runtime native validate/materialize report-only and Flow QA "
            "gates for the exact five saved plans; keep save-draft and bulk release closed."
        ),
    }
    outputs = out_dir / "outputs"
    plans_dir = outputs / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    for index_row, (_, content) in zip(index["ordered_flow_plans"], plan_contents):
        filename = Path(str(index_row["path"])).name
        (plans_dir / filename).write_bytes(content)
    for filename, content in output_rows.values():
        (outputs / filename).write_bytes(content)
    (outputs / "uslci-flow-authoring-canary-saved-plans.json").write_bytes(
        _json_bytes(result, pretty=True)
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile five exact TianGong Flow canary saved plans."
    )
    for name in EXPECTED_INPUT_NAMES:
        option = name.replace("_", "-")
        parser.add_argument(f"--{option}", type=Path, required=True)
        parser.add_argument(f"--{option}-binding", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    contents = {}
    bindings = {}
    for name in EXPECTED_INPUT_NAMES:
        contents[name] = getattr(args, name).read_bytes()
        bindings[name] = _parse_json(
            getattr(args, f"{name}_binding").read_bytes(), f"{name} binding"
        )
    compile_and_materialize(contents=contents, bindings=bindings, out_dir=args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
