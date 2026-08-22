"""Deterministic factual data requests and bounded gap-resolution ladders."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Mapping, Sequence

from .static_material_intake import validate_static_material_classification


REQUEST_CONTRACT = "lca_research_kernel.static_material_data_availability_request/v1"
RESPONSE_CONTRACT = "lca_research_kernel.static_material_data_availability_response/v1"
RESOLUTION_CONTRACT = "lca_research_kernel.static_material_gap_resolution/v1"
FALLBACK_ORDER = (
    "authoritative_supplement",
    "transparent_proxy",
    "parameter_range",
    "bounded_exclusion",
    "claim_closed",
)
ANSWER_VALUES = ("provided", "partial", "not_available")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _contract(contract: Mapping[str, object]) -> None:
    if contract.get("contract") != "lca_research_kernel.static_research_material_intake_contract/v1":
        raise ValueError("static material intake contract drifted")
    if tuple(contract.get("fallback_order", ())) != FALLBACK_ORDER:
        raise ValueError("static material fallback order drifted")
    if contract.get("user_question_limit") != 1:
        raise ValueError("static material user question limit drifted")


def _all_rows(classification: Mapping[str, object]) -> list[Mapping[str, object]]:
    findings = validate_static_material_classification(classification)
    if findings:
        raise ValueError("classification is invalid: " + "; ".join(findings))
    ledgers = classification["ledgers"]
    rows = [row for label in sorted(ledgers) for row in ledgers[label]]
    rows.sort(key=lambda row: str(row["material_id"]))
    return rows


def _missing_pairs(classification: Mapping[str, object]) -> list[tuple[str, str]]:
    rows = _all_rows(classification)
    expected = sorted(
        (str(row["material_id"]), str(field))
        for row in rows
        for field in row.get("missing_required_fields", [])
    )
    declared_raw = classification.get("missing_required_fields")
    if not isinstance(declared_raw, list):
        raise ValueError("classification missing-field ledger is absent")
    declared: list[tuple[str, str]] = []
    for row in declared_raw:
        if not isinstance(row, Mapping) or not isinstance(row.get("fields"), list):
            raise ValueError("classification missing-field ledger is invalid")
        material_id = _text(row.get("material_id"), "missing material_id")
        for field in row["fields"]:
            declared.append((material_id, _text(field, "missing field")))
    if sorted(declared) != expected:
        raise ValueError("classification missing-field ledger drifted")
    return expected


def _field_instruction(field: str) -> tuple[str, str, str, str]:
    formats = {
        "quantity": ("数值或含明确列名的表格", "原始资料声明的单位"),
        "original_value": ("原始数值或原始表格单元格", "原始资料声明的单位"),
        "unit": ("原始单位字符串", "该字段本身即为单位"),
        "transformations": ("有顺序的换算步骤列表；无换算时写 none", "不适用"),
        "uncertainty": ("区间、分布或明确的 not_reported", "与数量相同或资料声明单位"),
        "functional_unit": ("数量、流/服务名称和单位", "功能单位中的声明单位"),
        "system_boundary": ("包含项、排除项和阶段的文字或表格", "不适用"),
        "source": ("可重开的文件/页码/表名或内容寻址引用", "不适用"),
        "material_flow": ("材料名称、规格和流向", "数量资料的对应单位"),
    }
    minimum_format, minimum_unit = formats.get(
        field, ("可重开的原始资料或结构化字段", "资料声明单位或不适用")
    )
    reason = f"缺少 {field} 会限制材料流输入的可追溯性或可计算范围。"
    affected = (
        "comparative_lca_result"
        if field in {"functional_unit", "system_boundary"}
        else "accepted_project_lca_input"
    )
    return minimum_format, minimum_unit, reason, affected


def compile_data_availability_request(
    classification: Mapping[str, object], contract: Mapping[str, object]
) -> dict:
    """Compile exactly one consolidated factual availability request."""

    _contract(contract)
    pairs = _missing_pairs(classification)
    items = []
    for material_id, field in pairs:
        minimum_format, minimum_unit, reason, affected = _field_instruction(field)
        item_basis = {
            "package_id": classification["package_id"],
            "revision": classification["revision"],
            "package_content_sha256": classification["package_content_sha256"],
            "material_id": material_id,
            "field_id": field,
        }
        item_id = "data-item-" + hashlib.sha256(_canonical_json(item_basis)).hexdigest()[:24]
        items.append(
            {
                "item_id": item_id,
                "material_id": material_id,
                "field_id": field,
                "minimum_format": minimum_format,
                "minimum_unit": minimum_unit,
                "scientific_reason": reason,
                "answer_options": list(ANSWER_VALUES),
                "default_if_unavailable": FALLBACK_ORDER[0],
                "affected_claim": affected,
                "question_zh": f"您是否拥有材料 {material_id} 的 {field} 数据？如有，请按最低格式提供；如无请选择 not_available。",
            }
        )
    items.sort(key=lambda row: (row["material_id"], row["field_id"]))
    request_basis = {
        "package_id": classification["package_id"],
        "revision": classification["revision"],
        "package_content_sha256": classification["package_content_sha256"],
        "items": items,
    }
    request_id = "static-data-request-" + hashlib.sha256(_canonical_json(request_basis)).hexdigest()[:24]
    return {
        "contract": REQUEST_CONTRACT,
        "request_id": request_id,
        "package_id": classification["package_id"],
        "revision": classification["revision"],
        "package_content_sha256": classification["package_content_sha256"],
        "question_count": 1,
        "user_question_limit": 1,
        "interaction_kind": "factual_data_availability",
        "authorization_required": False,
        "question_zh": "请一次性说明下列缺失数据您是否拥有：有则提供，只有部分则标记 partial，没有则标记 not_available；没有的数据将由 Agent 按固定证据梯级继续处理。",
        "items": items,
        "fallback_order": list(FALLBACK_ORDER),
        "resume_route": "register-static-material-data-response",
    }


def _source_ref(value: object, label: str) -> dict:
    if not isinstance(value, Mapping) or set(value) != {"path", "sha256"}:
        raise ValueError(f"{label} must be an exact path/hash reference")
    path = _text(value.get("path"), f"{label}.path")
    sha256 = _text(value.get("sha256"), f"{label}.sha256")
    if SHA256_RE.fullmatch(sha256) is None:
        raise ValueError(f"{label}.sha256 must be a lowercase SHA-256")
    if path.startswith("/") or ".." in path.split("/"):
        raise ValueError(f"{label}.path escapes the package")
    return {"path": path, "sha256": sha256}


def _provided_resolution(item: Mapping[str, object], answer: Mapping[str, object]) -> dict:
    source = _source_ref(answer.get("source_ref"), "provided source_ref")
    value = answer.get("value")
    if value is None or isinstance(value, (dict, list)):
        raise ValueError("provided answer value is missing or unsupported")
    unit = _text(answer.get("unit"), "provided answer unit")
    return {
        "item_id": item["item_id"],
        "material_id": item["material_id"],
        "field_id": item["field_id"],
        "response_status": "provided",
        "resolution_type": "user_supplied",
        "source_ref": source,
        "value": deepcopy(value),
        "unit": unit,
        "assumption": "user supplied; authority and applicability remain subject to package acceptance",
        "transformation": "none declared",
        "evidence_level": "user_supplied_content_bound_candidate",
        "claim_impact": "may support project input only after exact acceptance",
        "falsification_cue": "source hash, scope, or authority review fails",
        "result_status": "user_supplied_candidate_not_yet_project_result",
    }


def _discovered_resolution(
    item: Mapping[str, object], response_status: str, source: Mapping[str, object]
) -> dict:
    resolution_type = _text(source.get("resolution_type"), "resolution_type")
    if resolution_type not in FALLBACK_ORDER[:-1]:
        raise ValueError("discovered source resolution_type is unsupported")
    row = {
        "item_id": item["item_id"],
        "material_id": item["material_id"],
        "field_id": item["field_id"],
        "response_status": response_status,
        "resolution_type": resolution_type,
        "source_ref": _source_ref(source.get("source_ref"), "discovered source_ref"),
        "value": deepcopy(source.get("value")),
        "unit": _text(source.get("unit"), "discovered source unit"),
        "assumption": _text(source.get("assumption"), "discovered source assumption"),
        "transformation": _text(source.get("transformation"), "discovered source transformation"),
        "evidence_level": _text(source.get("evidence_level"), "discovered source evidence_level"),
        "claim_impact": _text(source.get("claim_impact"), "discovered source claim_impact"),
        "falsification_cue": _text(source.get("falsification_cue"), "discovered source falsification_cue"),
        "result_status": "bounded_non_observed_input",
    }
    if resolution_type in {"transparent_proxy", "parameter_range"}:
        row["result_status"] = "not_observed_project_result"
    elif resolution_type == "bounded_exclusion":
        row["result_status"] = "excluded_from_calculable_slice"
    elif resolution_type == "authoritative_supplement":
        row["result_status"] = "authoritative_supplement_pending_scope_acceptance"
    return row


def _closed_resolution(item: Mapping[str, object], response_status: str) -> dict:
    return {
        "item_id": item["item_id"],
        "material_id": item["material_id"],
        "field_id": item["field_id"],
        "response_status": response_status,
        "resolution_type": "claim_closed",
        "source_ref": None,
        "value": None,
        "unit": item["minimum_unit"],
        "assumption": "no accepted value is available",
        "transformation": "none",
        "evidence_level": "missing",
        "claim_impact": f"{item['affected_claim']} remains closed",
        "falsification_cue": "reopen only with a content-bound accepted source",
        "result_status": "claim_closed_missing_input",
    }


def resolve_data_gaps(
    classification: Mapping[str, object],
    response: Mapping[str, object],
    discovered_sources: Sequence[Mapping[str, object]],
    contract: Mapping[str, object],
) -> dict:
    """Resolve every requested item through a fixed, auditable evidence ladder."""

    _contract(contract)
    request = compile_data_availability_request(classification, contract)
    if not isinstance(response, Mapping) or response.get("contract") != RESPONSE_CONTRACT:
        raise ValueError("data availability response contract drifted")
    for key in ("request_id", "package_id", "revision"):
        if response.get(key) != request.get(key):
            raise ValueError("data availability response binding drifted")
    answers_raw = response.get("answers")
    if not isinstance(answers_raw, list):
        raise ValueError("data availability response answers are missing")
    answers: dict[str, Mapping[str, object]] = {}
    for raw in answers_raw:
        if not isinstance(raw, Mapping):
            raise ValueError("data availability answer is invalid")
        item_id = _text(raw.get("item_id"), "answer item_id")
        if item_id in answers:
            raise ValueError("duplicate data availability answer")
        answer = _text(raw.get("answer"), "answer")
        if answer not in ANSWER_VALUES:
            raise ValueError("data availability answer is unsupported")
        answers[item_id] = raw
    request_ids = {str(item["item_id"]) for item in request["items"]}
    if set(answers) != request_ids:
        raise ValueError("data availability response does not cover the exact request")

    sources_by_item: dict[str, list[Mapping[str, object]]] = {item_id: [] for item_id in request_ids}
    for raw in discovered_sources:
        if not isinstance(raw, Mapping):
            raise ValueError("discovered source is invalid")
        item_id = _text(raw.get("item_id"), "discovered source item_id")
        if item_id not in sources_by_item:
            raise ValueError("discovered source targets an unknown request item")
        kind = _text(raw.get("resolution_type"), "resolution_type")
        if kind not in FALLBACK_ORDER[:-1]:
            raise ValueError("discovered source resolution_type is unsupported")
        sources_by_item[item_id].append(raw)

    rank = {kind: index for index, kind in enumerate(FALLBACK_ORDER)}
    resolutions = []
    for item in request["items"]:
        item_id = str(item["item_id"])
        answer = answers[item_id]
        response_status = str(answer["answer"])
        if response_status == "provided":
            resolved = _provided_resolution(item, answer)
        else:
            candidates = sorted(
                sources_by_item[item_id],
                key=lambda row: (rank[str(row["resolution_type"])], _canonical_json(row)),
            )
            resolved = (
                _discovered_resolution(item, response_status, candidates[0])
                if candidates
                else _closed_resolution(item, response_status)
            )
        resolutions.append(resolved)
    resolutions.sort(key=lambda row: str(row["item_id"]))
    unresolved_critical = sorted(
        {
            str(row["field_id"])
            for row in resolutions
            if row["resolution_type"] == "claim_closed"
            and row["field_id"] in {"functional_unit", "system_boundary"}
        }
    )
    closed_claims = sorted(
        {
            "comparative_lca_result"
            for _field in unresolved_critical
        }
        | {
            str(item["affected_claim"])
            for item, resolution in zip(request["items"], sorted(resolutions, key=lambda row: (str(row["material_id"]), str(row["field_id"]))))
            if resolution["resolution_type"] == "claim_closed"
        }
    )
    return {
        "contract": RESOLUTION_CONTRACT,
        "request_id": request["request_id"],
        "package_id": request["package_id"],
        "revision": request["revision"],
        "package_content_sha256": request["package_content_sha256"],
        "fallback_order": list(FALLBACK_ORDER),
        "resolutions": resolutions,
        "unresolved_critical_fields": unresolved_critical,
        "safe_material_mapping_allowed": True,
        "allowed_claims": ["source_bound_material_mapping", "bounded_screening_input_where_explicit"],
        "closed_claims": closed_claims,
        "external_effect_count": 0,
    }
