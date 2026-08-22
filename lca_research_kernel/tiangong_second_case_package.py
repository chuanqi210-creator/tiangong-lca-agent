"""Build deterministic machine and human deliverables for the TianGong second case."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Dict, List, Mapping, Sequence

from .tiangong_authority import verify_authority_receipt
from .tiangong_authority_transition import transition_content_sha256
from .tiangong_capability_adoption import verify_tiangong_capability_adoption
from .tiangong_native_case import verify_native_case_binding


JsonObject = Dict[str, object]

SECOND_CASE_DELIVERABLES_CONTRACT = (
    "lca_research_kernel.tiangong_second_case_deliverables/v1"
)
_SELECTION_CONTRACT = "lca_research_kernel.tiangong_open_data_case_selection/v1"
_TRANSITION_EVIDENCE_CONTRACT = (
    "lca_research_kernel.tiangong_authority_transition_evidence/v1"
)
_REPLAY_CONTRACT = "lca_research_kernel.tiangong_same_version_replay/v1"
_EXPECTED_PROJECTIONS = {
    "research-events.jsonl",
    "project-snapshot.json",
    "machine-context.json",
    "human-status.md",
}
_PROHIBITED_EFFECTS = (
    "remote_write_executed_count",
    "calculation_trigger_executed_count",
    "publication_executed_count",
)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return copy.deepcopy(value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _verify_selection(value: Mapping[str, object]) -> JsonObject:
    selection = _object(value, "selection")
    if selection.get("contract") != _SELECTION_CONTRACT:
        raise ValueError("selection contract is unsupported")
    if selection.get("status") != "selected" or not isinstance(
        selection.get("selected_candidate"), dict
    ):
        raise ValueError("selection does not contain one admitted case")
    selection_id = _text(selection.get("selection_id"), "selection_id")
    body = copy.deepcopy(selection)
    body.pop("selection_id", None)
    if selection_id != _canonical_digest(body)[:16]:
        raise ValueError("selection identity does not match its content")
    if selection.get("admission_criteria_lowered") is not False:
        raise ValueError("selection admission criteria were lowered")
    if selection.get("external_authority_substituted") is not False:
        raise ValueError("selection substituted an external authority")
    return selection


def _authority_history(values: Sequence[Mapping[str, object]]) -> List[JsonObject]:
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError("authority history must contain at least one receipt")
    receipts = [verify_authority_receipt(value) for value in values]
    by_id = {str(row["receipt_id"]): row for row in receipts}
    if len(by_id) != len(receipts):
        raise ValueError("authority history contains duplicate receipts")
    ordered = sorted(
        receipts,
        key=lambda row: (str(row["observed_at_utc"]), str(row["receipt_id"])),
    )
    seen = set()
    for row in ordered:
        predecessor = row["authority"].get("predecessor")
        if predecessor is not None:
            predecessor_id = predecessor.get("receipt_id")
            if predecessor_id not in by_id or predecessor_id not in seen:
                raise ValueError("authority history predecessor is missing or out of order")
        seen.add(str(row["receipt_id"]))
    return ordered


def _verify_transition_evidence(
    value: Mapping[str, object], current_receipt: Mapping[str, object]
) -> JsonObject:
    evidence = _object(value, "transition evidence")
    if evidence.get("contract") != _TRANSITION_EVIDENCE_CONTRACT:
        raise ValueError("transition evidence contract is unsupported")
    transition = _object(evidence.get("transition"), "transition")
    execution = _object(evidence.get("execution"), "transition execution")
    recorded_digest = _text(
        transition.get("transition_content_sha256"),
        "transition_content_sha256",
    )
    expected_digest = transition_content_sha256(transition)
    if recorded_digest != expected_digest or transition.get(
        "transition_id"
    ) != expected_digest[:16]:
        raise ValueError("transition content identity is invalid")
    if transition.get("previous_receipt_id") != current_receipt.get("receipt_id"):
        raise ValueError("transition previous receipt does not join current authority")
    if execution.get("transition_id") != transition.get("transition_id"):
        raise ValueError("transition execution does not join transition")
    if execution.get("status") != "passed" or execution.get(
        "all_rebuild_actions_passed"
    ) is not True:
        raise ValueError("transition rebuild execution has not passed")
    if execution.get("unplanned_action_count") != 0:
        raise ValueError("transition execution contains an unplanned action")
    planned = transition.get("rebuild_actions")
    executed = execution.get("executed_actions")
    if not isinstance(planned, list) or not isinstance(executed, list):
        raise ValueError("transition rebuild action lists are malformed")
    if execution.get("executed_action_count") != len(executed) or len(planned) != len(
        executed
    ):
        raise ValueError("transition rebuild action count does not match")
    for planned_row, executed_row in zip(planned, executed):
        if not isinstance(planned_row, dict) or not isinstance(executed_row, dict):
            raise ValueError("transition rebuild row is malformed")
        for field in ("order", "node_id", "rebuild_action"):
            if planned_row.get(field) != executed_row.get(field):
                raise ValueError(f"transition rebuild {field} does not match")
        if executed_row.get("status") not in {
            "synthetic_canary_recomputed",
            "official_output_rebuilt_and_verified",
        }:
            raise ValueError("transition rebuild action status is unsupported")
    for field in _PROHIBITED_EFFECTS:
        if transition.get(field) != 0 or execution.get(field) != 0:
            raise ValueError(f"transition prohibited effect {field} is nonzero")
    kind = transition.get("transition_evidence_kind")
    if kind == "synthetic_binding_canary":
        if (
            transition.get("current_receipt_origin") != "synthetic_test_receipt"
            or transition.get("official_update_claim") is not False
            or execution.get("official_update_claim") is not False
            or execution.get("current_authority_advanced") is not False
            or transition.get("canary_isolated") is not True
        ):
            raise ValueError("official canary truthfulness is violated")
    elif kind == "official_version_pair":
        if transition.get("official_update_claim") is not True:
            raise ValueError("official version-pair transition is mislabeled")
        if planned and execution.get("current_authority_advanced") is not True:
            raise ValueError("official transition did not advance after rebuild")
    else:
        raise ValueError("transition evidence kind is unsupported")
    return {"transition": transition, "execution": execution}


def _verify_replay(value: Mapping[str, object]) -> JsonObject:
    replay = _object(value, "replay evidence")
    if replay.get("contract") != _REPLAY_CONTRACT or replay.get("status") != "passed":
        raise ValueError("replay evidence contract or status is unsupported")
    if replay.get("same_version_replay") is not True:
        raise ValueError("same-version replay did not pass")
    if replay.get("event_count_before") != replay.get("event_count_after"):
        raise ValueError("event count changed during same-version replay")
    if replay.get("identities_unchanged") is not True:
        raise ValueError("replay identities changed")
    if replay.get("registered_artifact_tree_sha256_before") != replay.get(
        "registered_artifact_tree_sha256_after"
    ):
        raise ValueError("replay artifact tree changed")
    before = replay.get("project_projection_hashes_before")
    after = replay.get("project_projection_hashes_after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("replay projection hashes are missing")
    if set(before) != _EXPECTED_PROJECTIONS or before != after:
        raise ValueError("replay Project projection hashes changed")
    if replay.get("package_compilation_replayed") is not True:
        raise ValueError("package compilation replay did not pass")
    for field in _PROHIBITED_EFFECTS:
        if replay.get(field) != 0:
            raise ValueError(f"replay prohibited effect {field} is nonzero")
    return replay


def _normalize_risks(values: Sequence[Mapping[str, object]]) -> List[JsonObject]:
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError("risk register must retain at least one bounded risk")
    rows = []
    seen = set()
    for index, value in enumerate(values):
        row = _object(value, f"risks[{index}]")
        risk_id = _text(row.get("risk_id"), f"risks[{index}].risk_id")
        if risk_id in seen:
            raise ValueError("risk register contains duplicate risk IDs")
        seen.add(risk_id)
        evidence = row.get("evidence")
        if not isinstance(evidence, list) or not evidence or not all(
            isinstance(item, str) and item for item in evidence
        ):
            raise ValueError(f"risk {risk_id} has no evidence pointers")
        rows.append(
            {
                "risk_id": risk_id,
                "severity": _text(row.get("severity"), f"risk {risk_id}.severity"),
                "summary": _text(row.get("summary"), f"risk {risk_id}.summary"),
                "evidence": sorted(set(evidence)),
            }
        )
    return sorted(rows, key=lambda row: str(row["risk_id"]))


def _descriptor(filename: str, content: object) -> JsonObject:
    encoded = content.encode("utf-8") if isinstance(content, str) else _json_bytes(content)
    return {
        "filename": filename,
        "media_type": "text/markdown" if filename.endswith(".md") else "application/json",
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "bytes": len(encoded),
    }


def _human_report(
    machine: Mapping[str, object], risks: Sequence[Mapping[str, object]]
) -> str:
    authority = machine["authority"]
    case = machine["case"]
    transition = machine["transition"]
    result = case["native_result"]
    primary = next(
        (
            row
            for row in result["categories"]
            if row["method_id"] == case["lcia_method"]["id"]
            and row["method_version"] == case["lcia_method"]["version"]
        ),
        result["categories"][0],
    )
    lines = [
        "# TianGong 第一权威跟随式第二案例",
        "",
        "## 案例与权威",
        "",
        f"- 当前 Authority Receipt：{authority['current_receipt_id']}。",
        f"- TianGong 运行时：{authority['current_package_spec']}；历史 {authority['historical_package_spec']} 未改写。",
        f"- 案例：{case['candidate_id']}，Dataset {case['dataset']['id']}@{case['dataset']['version']}。",
        "- TianGong 维护官方数据、版本、Schema、Provider、方法和原生结果；Agent 只负责只读绑定、验证、编排、失效、重放和本地交付。",
        "",
        "## 功能单位、边界、方法与结果",
        "",
        f"- 功能单位：{case['functional_unit']['amount']} {case['functional_unit']['unit']}。",
        f"- 系统边界：{case['system_boundary']}。",
        f"- LCIA 方法：{case['lcia_method']['id']}@{case['lcia_method']['version']}。",
        f"- 原生结果：{primary['name']} = {primary['original_value']} {primary['original_unit']}。数值原样保存在 machine-final-state.json。",
        "",
        "## 上游变化与精确失效",
        "",
        f"- 迁移证据：{transition['transition_id']}，类型 {transition['transition_evidence_kind']}。",
        f"- 精确失效节点：{', '.join(transition['invalidated_nodes']) or '无'}。",
        f"- 有序重建动作：{transition['executed_action_count']} 个，全部通过={transition['all_rebuild_actions_passed']}。",
        "- 这是显式合成 canary，不等于 TianGong 发布了新版本；候选 Receipt 没有成为当前官方权威。"
        if transition["transition_evidence_kind"] == "synthetic_binding_canary"
        else "- 这是经 Receipt 前驱关系和重建结果验证的真实官方版本对。",
        "",
        "## 风险与恢复",
        "",
    ]
    lines.extend(
        f"- [{row['severity']}] {row['summary']}（证据：{', '.join(row['evidence'])}）"
        for row in risks
    )
    lines.extend(
        [
            "- 恢复入口：按 authority-history.json、replay-evidence.json 与 package-manifest.json 的哈希重新打开。",
            "",
            "## 成熟度边界",
            "",
            "本包证明了第二个 TianGong 原生案例的显式权威绑定、原生结果保留、精确失效、同版本重放和本地交付能力。它仍是证据有界的筛选级能力，不等于覆盖全部 LCA 领域，不等于 Agent 可以维护或修补 TianGong 官方数据，也不构成发布、审核或工业优越性声明。",
            "",
        ]
    )
    return "\n".join(lines)


def build_tiangong_second_case_deliverables(
    *,
    capability_adoption: Mapping[str, object],
    selection: Mapping[str, object],
    authority_history: Sequence[Mapping[str, object]],
    native_case_binding: Mapping[str, object],
    transition: Mapping[str, object],
    replay_evidence: Mapping[str, object],
    risks: Sequence[Mapping[str, object]],
) -> JsonObject:
    """Build eight deterministic, evidence-joined final deliverables."""

    adoption = verify_tiangong_capability_adoption(capability_adoption)
    selected = _verify_selection(selection)
    history = _authority_history(authority_history)
    current_receipt = history[-1]
    if current_receipt["capability_adoption"]["adoption_id"] != adoption["adoption_id"]:
        raise ValueError("capability adoption does not join current receipt")
    try:
        binding = verify_native_case_binding(native_case_binding, current_receipt)
    except ValueError as error:
        raise ValueError(f"receipt/native-case join failed: {error}") from error
    candidate = selected["selected_candidate"]
    if (
        selected["selection_id"] != binding["selection_id"]
        or candidate["candidate_id"] != binding["candidate_id"]
        or candidate["dataset"] != binding["model"]["dataset"]
    ):
        raise ValueError("selection does not join native-case binding")
    transition_evidence = _verify_transition_evidence(transition, current_receipt)
    replay = _verify_replay(replay_evidence)
    risk_rows = _normalize_risks(risks)
    transition_row = transition_evidence["transition"]
    execution = transition_evidence["execution"]

    authority_document: JsonObject = {
        "contract": "lca_research_kernel.tiangong_authority_history/v1",
        "status": "current_authority_recoverable",
        "current_receipt_id": current_receipt["receipt_id"],
        "receipt_count": len(history),
        "receipts": history,
        "synthetic_candidate_receipt_id": (
            transition_row["current_receipt_id"]
            if transition_row["transition_evidence_kind"] == "synthetic_binding_canary"
            else None
        ),
        "synthetic_candidate_promoted": False,
    }
    machine: JsonObject = {
        "contract": "lca_research_kernel.tiangong_second_case_machine_state/v1",
        "status": "validated_screening_level_authority_following_case",
        "authority": {
            "current_receipt_id": current_receipt["receipt_id"],
            "current_receipt_sha256": current_receipt["receipt_content_sha256"],
            "current_package_spec": adoption["current_package_spec"],
            "historical_package_spec": adoption["historical_package_spec"],
            "data_space": current_receipt["authority"]["data_space"],
            "publication_state": current_receipt["authority"]["publication_state"],
        },
        "case": {
            "selection_id": selected["selection_id"],
            "binding_id": binding["binding_id"],
            "candidate_id": binding["candidate_id"],
            "dataset": binding["model"]["dataset"],
            "model": binding["model"]["identity"],
            "functional_unit": binding["scope"]["functional_unit"],
            "reference_flow": binding["scope"]["reference_flow"],
            "system_boundary": binding["scope"]["system_boundary"],
            "provider_snapshot": binding["provider_snapshot"],
            "lcia_method": binding["lcia_method"],
            "native_result": binding["native_result"],
        },
        "transition": {
            "transition_id": transition_row["transition_id"],
            "transition_evidence_kind": transition_row["transition_evidence_kind"],
            "official_update_claim": transition_row["official_update_claim"],
            "previous_receipt_id": transition_row["previous_receipt_id"],
            "candidate_receipt_id": transition_row["current_receipt_id"],
            "current_authority_advanced": execution["current_authority_advanced"],
            "change_classes": transition_row["change_classes"],
            "invalidated_nodes": transition_row["invalidated_nodes"],
            "unaffected_nodes": transition_row["unaffected_nodes"],
            "executed_action_count": execution["executed_action_count"],
            "all_rebuild_actions_passed": execution[
                "all_rebuild_actions_passed"
            ],
        },
        "risk_count": len(risk_rows),
        "claim_boundary": "screening_level_evidence_bounded_not_publication_approval",
        "case_specific_kernel_branch": False,
        "effects": {field: 0 for field in _PROHIBITED_EFFECTS},
    }
    human = _human_report(machine, risk_rows)
    risk_document: JsonObject = {
        "contract": "lca_research_kernel.tiangong_risk_feedback_register/v1",
        "status": "open_risks_retained",
        "risk_count": len(risk_rows),
        "risks": risk_rows,
        "unsupported_public_claims_allowed": False,
    }
    validation_gates = [
        {"gate": "exact_selection_receipt_case_join", "passed": True},
        {"gate": "accepted_official_native_result_origin", "passed": True},
        {"gate": "machine_human_identity_and_result_agreement", "passed": True},
        {"gate": "transition_pair_or_canary_truthfulness", "passed": True},
        {"gate": "old_and_candidate_receipts_recoverable", "passed": True},
        {"gate": "no_case_specific_kernel_branch", "passed": True},
        {"gate": "same_version_replay_stable", "passed": True},
        {"gate": "zero_prohibited_effects", "passed": True},
        {"gate": "single_acceptance_route", "passed": True},
    ]
    validation: JsonObject = {
        "contract": "lca_research_kernel.tiangong_second_case_validation/v1",
        "status": "passed",
        "gates": validation_gates,
        "passed_gate_count": len(validation_gates),
        "failed_gate_count": 0,
        "next_route": "accept-tiangong-second-case-package-read-only",
    }
    package_report: JsonObject = {
        "contract": "lca_research_kernel.tiangong_second_case_package_report/v1",
        "status": "assembled_and_validated",
        "deliverable_count": 8,
        "current_receipt_id": current_receipt["receipt_id"],
        "binding_id": binding["binding_id"],
        "transition_id": transition_row["transition_id"],
        "validation_status": "passed",
        "bounded_maturity_claim": (
            "Explicit TianGong authority binding, native result preservation, precise "
            "transition canary, replay, and local delivery are demonstrated for one "
            "materially different second case; general LCA maturity is not claimed."
        ),
        "remote_write_executed_count": 0,
        "calculation_trigger_executed_count": 0,
        "publication_executed_count": 0,
        "next_route": "accept-tiangong-second-case-package-read-only",
    }
    deliverables: Dict[str, object] = {
        "authority-history.json": authority_document,
        "machine-final-state.json": machine,
        "human-final-report.md": human,
        "replay-evidence.json": replay,
        "risk-feedback-register.json": risk_document,
        "validation.json": validation,
        "package-report.json": package_report,
    }
    descriptors = [
        _descriptor(filename, content)
        for filename, content in sorted(deliverables.items())
    ]
    manifest: JsonObject = {
        "contract": "lca_research_kernel.tiangong_second_case_package_manifest/v1",
        "status": "complete",
        "deliverable_count": 8,
        "manifest_filename": "package-manifest.json",
        "manifest_self_identity": "bound by package_id and outer artifact registration",
        "files": descriptors,
        "input_identity": {
            "adoption_id": adoption["adoption_id"],
            "selection_id": selected["selection_id"],
            "current_receipt_id": current_receipt["receipt_id"],
            "binding_id": binding["binding_id"],
            "transition_id": transition_row["transition_id"],
        },
    }
    deliverables["package-manifest.json"] = manifest
    package_digest = _canonical_digest(deliverables)
    return {
        "contract": SECOND_CASE_DELIVERABLES_CONTRACT,
        "status": "assembled_and_validated",
        "package_id": package_digest[:16],
        "package_content_sha256": package_digest,
        "deliverable_count": 8,
        "deliverables": deliverables,
        "next_route": "accept-tiangong-second-case-package-read-only",
    }
