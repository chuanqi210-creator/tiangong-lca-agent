"""Analyze exact canary and bulk Flow-authoring evidence as one finite corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_flow_authoring_corpus_closure/v1"
MANIFEST_CONTRACT = (
    "lca_research_kernel.uslci_flow_authoring_corpus_closure_manifest/v1"
)
LOGIC_VERSION = "2026-07-18.1"
POLICY_VERSION = "2026-07-18.1"
NEXT_ROUTE = "compile-uslci-flow-authoring-corpus-release-plan"
EXPECTED_BATCHES = tuple(range(2, 17))
EXPECTED_CORPUS_COUNT = 1456
EXPECTED_BULK_COUNT = 1451
EXPECTED_RISK_COUNT = 196
EXPECTED_CLASSIFICATION_COUNTS = {
    "1.1.3": 501,
    "1.2.3": 140,
    "1.3.4": 755,
    "2.1.1": 24,
    "2.1.2": 19,
    "2.1.3": 2,
    "2.1.6": 1,
    "2.2.3": 1,
    "2.2.5": 1,
    "2.2.6": 2,
    "2.3.3": 1,
    "2.3.5": 3,
    "3.1": 5,
    "33370": 1,
}
EXPECTED_PROPERTY_COUNTS = {
    "Area*Time|m2*a": 5,
    "Energy|MJ": 9,
    "Mass|kg": 1442,
}
EXPECTED_NAMESPACE_COUNTS = {"flow-elementary": 1455, "flow-product": 1}
EXPECTED_RISK_SEVERITY_COUNTS = {"high": 1, "low": 140, "medium": 55}
PROPERTY_LABELS = {
    ("93a60a56-a3c8-11da-a746-0800200b9a66", "03.00.003"): "Mass|kg",
    ("93a60a56-a3c8-21da-a746-0800200c9a66", "03.00.003"): "Area*Time|m2*a",
    ("f6811440-ee37-11de-8a39-0800200c9a66", "00.00.000"): "Energy|MJ",
}
ZERO_SIDE_EFFECTS = (
    "network_read_executed_count",
    "remote_candidate_search_executed_count",
    "flow_creation_executed_count",
    "remote_flow_materialization_executed_count",
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


def _jsonl_bytes(rows: Iterable[Mapping[str, object]]) -> bytes:
    return b"".join(_json_bytes(dict(row)) for row in rows)


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


def _digest(value: object, label: str) -> str:
    token = _text(value, label)
    if len(token) != 64 or any(char not in "0123456789abcdef" for char in token):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return token


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


def _artifact_binding(path: str, content: bytes) -> JsonObject:
    digest = _sha256(content)
    return {
        "path": path,
        "media_type": mimetypes.guess_type(path)[0] or "application/octet-stream",
        "sha256": digest,
        "digest": f"sha256:{digest}",
        "bytes": len(content),
        "size": len(content),
    }


def _read_binding(
    root: Path, value: object, label: str, *, jsonl: bool = False, allow_empty: bool = False
) -> Tuple[object, JsonObject]:
    binding = _object(value, f"{label} binding")
    relative = _text(binding.get("path"), f"{label} path")
    digest = _digest(binding.get("sha256"), f"{label} SHA-256")
    size = _integer(binding.get("bytes"), f"{label} bytes")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"{label} escaped artifact root") from error
    if not candidate.is_file():
        raise ValueError(f"{label} is missing")
    content = candidate.read_bytes()
    if len(content) != size or _sha256(content) != digest:
        raise ValueError(f"{label} hash or byte count drifted")
    parsed = (
        _parse_jsonl(content, label, allow_empty=allow_empty)
        if jsonl
        else _parse_json(content, label)
    )
    return parsed, {"path": relative, "sha256": digest, "bytes": size}


def input_version_for_manifest_binding(binding_value: Mapping[str, object]) -> str:
    binding = _object(binding_value, "corpus manifest binding")
    normalized = {
        "path": _text(binding.get("path"), "corpus manifest path"),
        "sha256": _digest(binding.get("sha256"), "corpus manifest SHA-256"),
        "bytes": _integer(binding.get("bytes"), "corpus manifest bytes"),
    }
    return _sha256(
        _json_bytes(
            {
                "contract": CONTRACT,
                "logic_version": LOGIC_VERSION,
                "policy_version": POLICY_VERSION,
                "manifest": normalized,
            }
        )
    )[:16]


def _rows_by_position(
    rows: Sequence[Mapping[str, object]], *, position_key: str, count: int, label: str
) -> Dict[int, JsonObject]:
    result = {}
    for value in rows:
        row = _object(value, label)
        position = _integer(row.get(position_key), f"{label} position")
        if position in result:
            raise ValueError(f"{label} position is duplicated")
        result[position] = row
    if sorted(result) != list(range(1, count + 1)):
        raise ValueError(f"{label} positions are not contiguous 1..{count}")
    return result


def _property_label(row: Mapping[str, object], label: str) -> str:
    key = (
        _text(row.get("flow_property_id"), f"{label} property ID"),
        _text(row.get("flow_property_version"), f"{label} property version"),
    )
    if key not in PROPERTY_LABELS:
        raise ValueError(f"{label} uses an unaccepted FlowProperty contract")
    return PROPERTY_LABELS[key]


def _validate_canary(
    canary_value: Mapping[str, object], *, root: Path
) -> Tuple[List[JsonObject], List[JsonObject], JsonObject]:
    canary = _object(canary_value, "canary closure inputs")
    support_report, support_ref = _read_binding(root, canary.get("support_report"), "canary support report")
    saved_report, saved_ref = _read_binding(root, canary.get("saved_plan_report"), "canary saved-plan report")
    native_report, native_ref = _read_binding(root, canary.get("native_gate_report"), "canary native-gate report")
    typed_report, typed_ref = _read_binding(root, canary.get("typed_qa_report"), "canary typed-QA report")
    identities, identity_ref = _read_binding(root, canary.get("identity_ledger"), "canary identity ledger", jsonl=True)
    classifications, classification_ref = _read_binding(root, canary.get("classification_ledger"), "canary classification ledger", jsonl=True)
    supports, support_ledger_ref = _read_binding(root, canary.get("support_ledger"), "canary property-support ledger", jsonl=True)
    risks, risk_ref = _read_binding(root, canary.get("risk_ledger"), "canary risk ledger", jsonl=True)
    if (
        support_report.get("status") != "uslci_flow_authoring_canary_supports_resolved"
        or support_report.get("ready_canary_count") != 5
        or support_report.get("review_marker_count") != 4
        or support_report.get("resolution_failure_count") != 0
        or saved_report.get("status") != "uslci_flow_authoring_canary_saved_plans_compiled"
        or saved_report.get("source_support_resolution_version")
        != support_report.get("support_resolution_version")
        or saved_report.get("saved_flow_plan_count") != 5
        or saved_report.get("complete_plan_count") != 5
        or saved_report.get("review_marker_carry_forward_count") != 4
        or saved_report.get("planning_failure_count") != 0
        or native_report.get("status")
        != "uslci_flow_authoring_canary_native_gates_completed_with_elementary_qa_harness_gap"
        or native_report.get("source_saved_plan_set_version")
        != saved_report.get("saved_plan_set_version")
        or any(native_report.get(key) != 5 for key in (
            "native_plan_validation_pass_count",
            "local_flow_materialization_pass_count",
            "exact_classification_apply_pass_count",
            "support_reference_preservation_pass_count",
            "qa_execution_count",
        ))
        or native_report.get("qa_pass_count") != 1
        or native_report.get("elementary_qa_harness_gap_count") != 4
        or native_report.get("scientific_review_marker_preserved_count") != 4
        or native_report.get("unexpected_failure_count") != 0
        or typed_report.get("status") != "uslci_elementary_flow_native_qa_harness_resolved"
        or typed_report.get("source_native_gate_execution_version")
        != native_report.get("native_gate_execution_version")
        or typed_report.get("source_saved_plan_set_version")
        != saved_report.get("saved_plan_set_version")
        or typed_report.get("typed_qa_pass_count") != 5
        or typed_report.get("strict_product_qa_pass_count") != 1
        or typed_report.get("elementary_not_applicable_count") != 4
        or typed_report.get("scientific_review_marker_preserved_count") != 4
        or typed_report.get("unexpected_finding_count") != 0
        or len(identities) != 5
        or len(classifications) != 5
        or len(supports) != 5
        or len(risks) != 4
    ):
        raise ValueError("accepted canary corpus authority drifted")
    identity_by = _rows_by_position(identities, position_key="canary_index", count=5, label="canary identity")
    classification_by = _rows_by_position(classifications, position_key="canary_index", count=5, label="canary classification")
    support_by = _rows_by_position(supports, position_key="canary_index", count=5, label="canary support")
    risk_by_intent = {}
    for value in risks:
        risk = _object(value, "canary risk")
        intent = _text(risk.get("authoring_intent_key"), "canary risk intent")
        if intent in risk_by_intent or risk.get("native_gate_review_retained") is not True:
            raise ValueError("canary risk conservation drifted")
        risk_by_intent[intent] = risk
    rows = []
    normalized_risks = []
    for position in range(1, 6):
        identity = identity_by[position]
        classification = classification_by[position]
        support = support_by[position]
        intent = _text(identity.get("authoring_intent_key"), "canary intent")
        source = _text(identity.get("source_flow_id"), "canary source Flow")
        target = _text(identity.get("target_flow_id"), "canary target Flow")
        if (
            classification.get("authoring_intent_key") != intent
            or classification.get("target_flow_id") != target
            or support.get("target_flow_id") != target
            or classification.get("flow_property_id") != support.get("flow_property_id")
            or classification.get("flow_property_version") != support.get("flow_property_version")
            or classification.get("exact_classification_applied") is not True
            or classification.get("support_reference_preserved") is not True
            or support.get("support_reference_preserved") is not True
            or any(identity.get(key) != "passed" for key in (
                "native_plan_validation",
                "local_schema_materialization",
                "exact_classification_apply",
                "support_reference_preservation",
            ))
            or identity.get("save_draft_executed") is not False
            or identity.get("remote_entity_created") is not False
        ):
            raise ValueError(f"canary corpus joins drifted at position {position}")
        property_label = _property_label(support, f"canary support {position}")
        namespace = _text(classification.get("classification_type"), "canary classification namespace")
        code = _text(classification.get("classification_code"), "canary classification code")
        risk = risk_by_intent.get(intent)
        lane = (
            "held_classification_namespace_conflict"
            if risk and risk.get("severity") == "high"
            else "planned_with_retained_review"
            if risk
            else "planned_clean"
        )
        row = {
            "schema_version": 1,
            "corpus_position": position,
            "slice_id": "canary",
            "slice_position": position,
            "authoring_intent_key": intent,
            "source_flow_id": source,
            "target_flow_id": target,
            "classification_code": code,
            "classification_type": namespace,
            "flow_property": property_label,
            "native_gate_status": "passed_with_typed_qa_resolution",
            "risk_id": risk.get("risk_id") if risk else None,
            "risk_severity": risk.get("severity") if risk else None,
            "release_lane": lane,
            "flow_entity_release_authorized": False,
        }
        rows.append(row)
        if risk:
            normalized_risks.append({**risk, "slice_id": "canary", "slice_position": position, "target_flow_id": target, "classification_code": code, "classification_type": namespace, "release_lane": lane})
    summary = {
        "slice_id": "canary",
        "intent_count": 5,
        "risk_marker_count": 4,
        "support_report_sha256": support_ref["sha256"],
        "saved_plan_report_sha256": saved_ref["sha256"],
        "native_gate_report_sha256": native_ref["sha256"],
        "typed_qa_report_sha256": typed_ref["sha256"],
        "identity_ledger_sha256": identity_ref["sha256"],
        "classification_ledger_sha256": classification_ref["sha256"],
        "support_ledger_sha256": support_ledger_ref["sha256"],
        "risk_ledger_sha256": risk_ref["sha256"],
        "terminal_status": "completed_with_typed_qa_resolution",
    }
    return rows, normalized_risks, summary


def _validate_bulk_batch(
    batch_value: Mapping[str, object], *, root: Path, corpus_offset: int
) -> Tuple[List[JsonObject], List[JsonObject], JsonObject]:
    batch = _object(batch_value, "bulk batch closure inputs")
    batch_index = _integer(batch.get("batch_index"), "bulk batch index")
    if batch_index not in EXPECTED_BATCHES:
        raise ValueError("bulk closure batch index is outside 2..16")
    expected_count = 100 if batch_index < 16 else 51
    support_report, support_ref = _read_binding(root, batch.get("support_report"), f"batch-{batch_index} support report")
    saved_report, saved_ref = _read_binding(root, batch.get("saved_plan_report"), f"batch-{batch_index} saved-plan report")
    native_report, native_ref = _read_binding(root, batch.get("native_gate_report"), f"batch-{batch_index} native-gate report")
    identities, identity_ref = _read_binding(root, batch.get("identity_ledger"), f"batch-{batch_index} identity ledger", jsonl=True)
    classifications, classification_ref = _read_binding(root, batch.get("classification_ledger"), f"batch-{batch_index} classification ledger", jsonl=True)
    supports, support_ledger_ref = _read_binding(root, batch.get("support_ledger"), f"batch-{batch_index} property-support ledger", jsonl=True)
    risks, risk_ref = _read_binding(root, batch.get("risk_ledger"), f"batch-{batch_index} risk ledger", jsonl=True, allow_empty=True)
    review_count = _integer(support_report.get("review_marker_count"), f"batch-{batch_index} support risk count")
    if (
        support_report.get("status") != f"uslci_flow_authoring_bulk_batch_{batch_index}_supports_resolved"
        or support_report.get("batch_index") != batch_index
        or support_report.get("ready_intent_count") != expected_count
        or support_report.get("resolution_failure_count") != 0
        or saved_report.get("status") != f"uslci_flow_authoring_bulk_batch_{batch_index}_saved_plans_compiled"
        or saved_report.get("batch_index") != batch_index
        or saved_report.get("source_bulk_support_report_sha256") != support_ref["sha256"]
        or saved_report.get("saved_flow_plan_count") != expected_count
        or saved_report.get("complete_plan_count") != expected_count
        or saved_report.get("review_marker_carry_forward_count") != review_count
        or saved_report.get("planning_failure_count") != 0
        or native_report.get("status") != "uslci_flow_authoring_bulk_batch_native_gates_completed"
        or native_report.get("batch_index") != batch_index
        or native_report.get("source_saved_plan_set_version") != saved_report.get("saved_plan_set_version")
        or native_report.get("successful_branch_count") != expected_count
        or native_report.get("failed_branch_count") != 0
        or native_report.get("typed_qa_pass_count") != expected_count
        or native_report.get("native_qa_finding_preserved_count") != expected_count
        or native_report.get("review_marker_preserved_count") != review_count
        or native_report.get("unexpected_failure_count") != 0
        or native_report.get("batch_entity_release_authorized") is not False
        or len(identities) != expected_count
        or len(classifications) != expected_count
        or len(supports) != expected_count
        or len(risks) != review_count
    ):
        raise ValueError(f"accepted batch-{batch_index} corpus authority drifted")
    identity_by = _rows_by_position(identities, position_key="batch_position", count=expected_count, label=f"batch-{batch_index} identity")
    classification_by = _rows_by_position(classifications, position_key="batch_position", count=expected_count, label=f"batch-{batch_index} classification")
    support_by = _rows_by_position(supports, position_key="batch_position", count=expected_count, label=f"batch-{batch_index} support")
    risk_by_intent = {}
    for value in risks:
        risk = _object(value, f"batch-{batch_index} risk")
        intent = _text(risk.get("authoring_intent_key"), f"batch-{batch_index} risk intent")
        if intent in risk_by_intent or risk.get("native_gate_review_retained") is not True:
            raise ValueError(f"batch-{batch_index} risk conservation drifted")
        risk_by_intent[intent] = risk
    rows = []
    normalized_risks = []
    for position in range(1, expected_count + 1):
        identity = identity_by[position]
        classification = classification_by[position]
        support = support_by[position]
        intent = _text(identity.get("authoring_intent_key"), f"batch-{batch_index} intent")
        source = _text(identity.get("source_flow_id"), f"batch-{batch_index} source Flow")
        target = _text(identity.get("target_flow_id"), f"batch-{batch_index} target Flow")
        if (
            classification.get("authoring_intent_key") != intent
            or classification.get("target_flow_id") != target
            or support.get("authoring_intent_key") != intent
            or support.get("target_flow_id") != target
            or classification.get("flow_property_id") != support.get("flow_property_id")
            or classification.get("flow_property_version") != support.get("flow_property_version")
            or classification.get("exact_classification_applied") is not True
            or classification.get("support_reference_preserved") is not True
            or support.get("support_reference_preserved") is not True
            or identity.get("terminal_status") != "succeeded"
            or identity.get("native_invocation_count") != 5
            or identity.get("save_draft_executed") is not False
            or identity.get("remote_entity_created") is not False
        ):
            raise ValueError(f"batch-{batch_index} corpus joins drifted at position {position}")
        property_label = _property_label(support, f"batch-{batch_index} support {position}")
        namespace = _text(classification.get("classification_type"), f"batch-{batch_index} classification namespace")
        code = _text(classification.get("classification_code"), f"batch-{batch_index} classification code")
        risk = risk_by_intent.get(intent)
        lane = (
            "held_classification_namespace_conflict"
            if risk and risk.get("severity") == "high"
            else "planned_with_retained_review"
            if risk
            else "planned_clean"
        )
        row = {
            "schema_version": 1,
            "corpus_position": corpus_offset + position,
            "slice_id": f"batch-{batch_index}",
            "slice_position": position,
            "authoring_intent_key": intent,
            "source_flow_id": source,
            "target_flow_id": target,
            "classification_code": code,
            "classification_type": namespace,
            "flow_property": property_label,
            "native_gate_status": "passed_with_scoped_typed_qa",
            "risk_id": risk.get("risk_id") if risk else None,
            "risk_severity": risk.get("severity") if risk else None,
            "release_lane": lane,
            "flow_entity_release_authorized": False,
        }
        rows.append(row)
        if risk:
            normalized_risks.append({**risk, "slice_id": f"batch-{batch_index}", "slice_position": position, "target_flow_id": target, "classification_code": code, "classification_type": namespace, "release_lane": lane})
    summary = {
        "slice_id": f"batch-{batch_index}",
        "batch_index": batch_index,
        "intent_count": expected_count,
        "risk_marker_count": review_count,
        "support_report_sha256": support_ref["sha256"],
        "saved_plan_report_sha256": saved_ref["sha256"],
        "native_gate_report_sha256": native_ref["sha256"],
        "identity_ledger_sha256": identity_ref["sha256"],
        "classification_ledger_sha256": classification_ref["sha256"],
        "support_ledger_sha256": support_ledger_ref["sha256"],
        "risk_ledger_sha256": risk_ref["sha256"],
        "terminal_status": "completed",
    }
    return rows, normalized_risks, summary


def _build_dag(summaries: Sequence[Mapping[str, object]]) -> JsonObject:
    nodes = []
    edges = []
    closure_id = "flow-authoring-corpus-closure"
    for summary in summaries:
        slice_id = _text(summary.get("slice_id"), "closure slice ID")
        support = f"{slice_id}-support"
        plan = f"{slice_id}-saved-plans"
        native = f"{slice_id}-native-gates"
        nodes.extend(
            [
                {"node_id": support, "node_type": "support", "status": "completed"},
                {"node_id": plan, "node_type": "saved_plans", "status": "completed"},
                {"node_id": native, "node_type": "native_gates", "status": "completed"},
            ]
        )
        edges.extend(
            [
                {"from": support, "to": plan},
                {"from": plan, "to": native},
                {"from": native, "to": closure_id},
            ]
        )
    nodes.append({"node_id": closure_id, "node_type": "corpus_closure", "status": "completed"})
    return {"schema_version": 1, "node_count": len(nodes), "edge_count": len(edges), "nodes": nodes, "edges": edges}


def analyze_manifest(
    manifest_value: Mapping[str, object], *, artifact_root: Path
) -> JsonObject:
    """Verify all exact lineage and compile the finite corpus decision."""

    manifest = _object(manifest_value, "corpus closure manifest")
    if manifest.get("contract") != MANIFEST_CONTRACT or manifest.get("schema_version") != 1:
        raise ValueError("corpus closure manifest contract drifted")
    scope_id = _text(manifest.get("scope_id"), "corpus scope ID")
    revision = _text(manifest.get("revision"), "corpus revision")
    batches = [_object(value, "bulk batch manifest") for value in _array(manifest.get("bulk_batches"), "bulk batch manifests")]
    if [value.get("batch_index") for value in batches] != list(EXPECTED_BATCHES):
        raise ValueError("corpus closure requires exact ordered batches 2..16")
    identities, risks, canary_summary = _validate_canary(
        _object(manifest.get("canary"), "canary manifest"), root=artifact_root
    )
    summaries = [canary_summary]
    offset = 5
    for batch in batches:
        batch_rows, batch_risks, summary = _validate_bulk_batch(
            batch, root=artifact_root, corpus_offset=offset
        )
        identities.extend(batch_rows)
        risks.extend(batch_risks)
        summaries.append(summary)
        offset += len(batch_rows)
    if len(identities) != EXPECTED_CORPUS_COUNT or offset != EXPECTED_CORPUS_COUNT:
        raise ValueError("corpus closure coverage count drifted")
    intent_count = len({row["authoring_intent_key"] for row in identities})
    source_count = len({row["source_flow_id"] for row in identities})
    target_count = len({row["target_flow_id"] for row in identities})
    if (intent_count, source_count, target_count) != (
        EXPECTED_CORPUS_COUNT,
        EXPECTED_CORPUS_COUNT,
        EXPECTED_CORPUS_COUNT,
    ):
        raise ValueError("corpus closure requires unique corpus identities")
    risk_ids = [_text(row.get("risk_id"), "corpus risk ID") for row in risks]
    risk_intents = [_text(row.get("authoring_intent_key"), "corpus risk intent") for row in risks]
    if (
        len(risks) != EXPECTED_RISK_COUNT
        or len(set(risk_ids)) != EXPECTED_RISK_COUNT
        or len(set(risk_intents)) != EXPECTED_RISK_COUNT
    ):
        raise ValueError("corpus risk conservation drifted")
    classification_counts = dict(sorted(Counter(str(row["classification_code"]) for row in identities).items()))
    namespace_counts = dict(sorted(Counter(str(row["classification_type"]) for row in identities).items()))
    property_counts = dict(sorted(Counter(str(row["flow_property"]) for row in identities).items()))
    risk_severity_counts = dict(sorted(Counter(_text(row.get("severity"), "corpus risk severity") for row in risks).items()))
    lane_counts = dict(sorted(Counter(str(row["release_lane"]) for row in identities).items()))
    high_risk_intents = {str(row["authoring_intent_key"]) for row in risks if row.get("severity") == "high"}
    product_namespace_intents = {str(row["authoring_intent_key"]) for row in identities if row.get("classification_type") == "flow-product"}
    if (
        classification_counts != EXPECTED_CLASSIFICATION_COUNTS
        or namespace_counts != EXPECTED_NAMESPACE_COUNTS
        or property_counts != EXPECTED_PROPERTY_COUNTS
        or risk_severity_counts != EXPECTED_RISK_SEVERITY_COUNTS
        or high_risk_intents != product_namespace_intents
        or lane_counts
        != {
            "held_classification_namespace_conflict": 1,
            "planned_clean": 1260,
            "planned_with_retained_review": 195,
        }
    ):
        raise ValueError("exact Flow corpus distribution or release partition drifted")
    dag = _build_dag(summaries)
    distributions = {
        "schema_version": 1,
        "classification_code_counts": classification_counts,
        "classification_namespace_counts": namespace_counts,
        "flow_property_counts": property_counts,
        "risk_severity_counts": risk_severity_counts,
        "release_lane_counts": lane_counts,
    }
    release_partition = [
        {
            "corpus_position": row["corpus_position"],
            "authoring_intent_key": row["authoring_intent_key"],
            "target_flow_id": row["target_flow_id"],
            "classification_type": row["classification_type"],
            "risk_id": row["risk_id"],
            "risk_severity": row["risk_severity"],
            "release_lane": row["release_lane"],
            "flow_entity_release_authorized": False,
        }
        for row in identities
    ]
    report = {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "policy_version": POLICY_VERSION,
        "status": "uslci_flow_authoring_corpus_closure_analyzed",
        "scope_id": scope_id,
        "revision": revision,
        "slice_count": len(summaries),
        "bulk_batch_count": len(batches),
        "canary_intent_count": 5,
        "bulk_intent_count": EXPECTED_BULK_COUNT,
        "corpus_intent_count": len(identities),
        "native_evidence_complete_count": len(identities),
        "unique_intent_count": intent_count,
        "unique_source_flow_count": source_count,
        "unique_target_flow_count": target_count,
        "identity_collision_count": 0,
        "classification_code_counts": classification_counts,
        "classification_namespace_counts": namespace_counts,
        "flow_property_counts": property_counts,
        "risk_marker_count": len(risks),
        "unique_risk_marker_count": len(set(risk_ids)),
        "risk_severity_counts": risk_severity_counts,
        "release_lane_counts": lane_counts,
        "classification_namespace_conflict_count": len(product_namespace_intents),
        "lineage_dag_node_count": dag["node_count"],
        "lineage_dag_edge_count": dag["edge_count"],
        "corpus_coverage_status": "complete_with_retained_risks",
        "risk_conservation_status": "complete",
        "release_plan_compilation_authorized": True,
        "flow_entity_release_authorized": False,
        "save_draft_authorized": False,
        **{key: 0 for key in ZERO_SIDE_EFFECTS},
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "external_patterns": [
            {
                "project": "kubernetes/kubernetes",
                "local_landing": "finite_slice_terminal_reconciliation_and_explicit_closure_condition",
                "module": "build_corpus_closure_dag",
                "installed": False,
            },
            {
                "project": "bazelbuild/bazel",
                "local_landing": "content_addressed_authority_set_and_reproducible_release_input",
                "module": "verify_manifest_bindings",
                "installed": False,
            },
            {
                "project": "open-policy-agent/opa",
                "local_landing": "risk_preserving_three_lane_release_partition",
                "module": "compile_release_partition",
                "installed": False,
            },
            {
                "project": "OpenLineage/OpenLineage",
                "local_landing": "slice_report_and_ledger_lineage_facets",
                "module": "compile_slice_summaries",
                "installed": False,
            },
        ],
        "routing": {"next_internal_route": NEXT_ROUTE, "pause_internal_work": False},
        "next_internal_route": NEXT_ROUTE,
        "next_action": (
            "Compile an immutable three-lane Flow corpus release plan: 1,260 clean, "
            "195 retained-review and one held classification-namespace conflict; "
            "keep entity creation/save, model mutation, comparison and LCIA closed."
        ),
    }
    return {
        "report": report,
        "identity_ledger": identities,
        "risk_ledger": risks,
        "slice_summaries": summaries,
        "distributions": distributions,
        "release_partition": release_partition,
        "dag": dag,
        "unexpected_failures": [],
    }


def execute_and_materialize(
    *,
    manifest_content: bytes,
    manifest_binding_value: Mapping[str, object],
    artifact_root: Path,
    out_dir: Path,
) -> JsonObject:
    binding = _object(manifest_binding_value, "corpus manifest binding")
    if (
        _digest(binding.get("sha256"), "corpus manifest SHA-256")
        != _sha256(manifest_content)
        or _integer(binding.get("bytes"), "corpus manifest bytes")
        != len(manifest_content)
    ):
        raise ValueError("corpus manifest binding hash or byte count drifted")
    manifest = _parse_json(manifest_content, "corpus closure manifest")
    result = analyze_manifest(manifest, artifact_root=artifact_root)
    output_rows = {
        "identity_ledger": ("uslci-flow-authoring-corpus-identities.jsonl", _jsonl_bytes(result["identity_ledger"])),
        "risk_ledger": ("uslci-flow-authoring-corpus-risks.jsonl", _jsonl_bytes(result["risk_ledger"])),
        "slice_summaries": ("uslci-flow-authoring-corpus-slices.jsonl", _jsonl_bytes(result["slice_summaries"])),
        "distributions": ("uslci-flow-authoring-corpus-distributions.json", _json_bytes(result["distributions"], pretty=True)),
        "release_partition": ("uslci-flow-authoring-corpus-release-partition.jsonl", _jsonl_bytes(result["release_partition"])),
        "lineage_dag": ("uslci-flow-authoring-corpus-lineage-dag.json", _json_bytes(result["dag"], pretty=True)),
        "unexpected_failures": ("uslci-flow-authoring-corpus-closure-failures.jsonl", b""),
    }
    artifact_bindings = {
        key: _artifact_binding(filename, content)
        for key, (filename, content) in output_rows.items()
    }
    closure_version = _sha256(
        _json_bytes(
            {
                "contract": CONTRACT,
                "logic_version": LOGIC_VERSION,
                "manifest_sha256": _sha256(manifest_content),
                "identity_ledger_sha256": artifact_bindings["identity_ledger"]["sha256"],
                "risk_ledger_sha256": artifact_bindings["risk_ledger"]["sha256"],
                "release_partition_sha256": artifact_bindings["release_partition"]["sha256"],
            }
        )
    )[:16]
    report = {
        **result["report"],
        "input_version": input_version_for_manifest_binding(binding),
        "corpus_closure_version": closure_version,
        "input_binding": {
            "path": _text(binding.get("path"), "corpus manifest path"),
            "sha256": _sha256(manifest_content),
            "bytes": len(manifest_content),
        },
        "artifact_bindings": artifact_bindings,
        "unexpected_failure_count": 0,
    }
    outputs = out_dir / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    for filename, content in output_rows.values():
        (outputs / filename).write_bytes(content)
    report_path = outputs / "uslci-flow-authoring-corpus-closure.json"
    report_path.write_bytes(_json_bytes(report, pretty=True))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze the complete exact Flow-authoring corpus without entity effects."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-binding", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = execute_and_materialize(
        manifest_content=args.manifest.read_bytes(),
        manifest_binding_value=_parse_json(args.manifest_binding.read_bytes(), "corpus manifest binding"),
        artifact_root=args.artifact_root,
        out_dir=args.out_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
