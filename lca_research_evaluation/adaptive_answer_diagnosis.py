"""Deterministic, official-source-first review of immutable blind answers."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Mapping


AUTHORITY_ORDER = ["official_source", "reference_answer", "frozen_answer"]
FINDING_CLASSES = {
    "scientific_or_fact_error",
    "evidence_coverage",
    "task_understanding",
    "lca_or_tiangong_rule",
    "expression_fit",
    "ineffective_investigation",
}
FAILURE_LAYERS = {
    "prompt_failure",
    "context_failure",
    "harness_failure",
    "loop_failure",
    "model_limitation",
}
MAPPED_COMPONENTS = {
    "product_interface",
    "research_orchestrator",
    "lca_domain_kernel",
    "model_gateway",
    "capability_gateway",
    "project_record",
    "assurance_boundary",
}
SEVERITIES = {"P0", "P1", "P2", "P3"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _checked_ref(value: object, *, label: str) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} ref is invalid")
    path = Path(str(value.get("path", "")))
    expected = value.get("sha256")
    if not path.is_file() or not isinstance(expected, str) or len(expected) != 64:
        raise ValueError(f"{label} ref is invalid")
    if _sha256(path) != expected:
        raise ValueError(f"{label} hash drifted")
    return {"path": str(path), "sha256": expected}


def _checked_official_source(value: object) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError("official source basis is invalid")
    source = deepcopy(dict(value))
    source_id = source.get("source_id")
    url = source.get("url")
    accessed_at = source.get("accessed_at")
    body_sha256 = source.get("sha256")
    if not isinstance(source_id, str) or not source_id:
        raise ValueError("official source identity is invalid")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ValueError("official source URL is invalid")
    if not isinstance(accessed_at, str) or not accessed_at:
        raise ValueError("official source access time is invalid")
    if not isinstance(body_sha256, str) or len(body_sha256) != 64:
        raise ValueError("official source hash is invalid")

    receipt_ref = _checked_ref(
        source.get("receipt_ref"), label="official source receipt"
    )
    body_ref = _checked_ref(source.get("body_ref"), label="official source body")
    if body_ref["sha256"] != body_sha256:
        raise ValueError("official source body hash drifted")
    try:
        receipt_entry = json.loads(
            Path(receipt_ref["path"]).read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as error:
        raise ValueError("official source receipt JSON is invalid") from error
    if receipt_entry.get("contract") != "official_public_source_cache_entry/v1":
        raise ValueError("official source receipt contract is invalid")
    receipt = receipt_entry.get("source_receipt")
    if not isinstance(receipt, Mapping) or receipt.get("contract") != (
        "official_public_source_receipt/v1"
    ):
        raise ValueError("official source receipt payload is invalid")
    if receipt.get("body_sha256") != body_sha256:
        raise ValueError("official source receipt body hash drifted")
    if receipt.get("accessed_at") != accessed_at:
        raise ValueError("official source receipt access time drifted")
    if url not in {receipt.get("requested_url"), receipt.get("final_url")}:
        raise ValueError("official source receipt URL drifted")
    cache_ref = receipt_entry.get("cache_ref")
    if isinstance(cache_ref, Mapping) and cache_ref.get("sha256") != body_sha256:
        raise ValueError("official source cache hash drifted")
    source["receipt_ref"] = receipt_ref
    source["body_ref"] = body_ref
    return source


def build_retrospective(ledger: Mapping[str, object]) -> dict:
    """Validate a human-authored fact ledger and compile a replayable comparison.

    This boundary deliberately performs no semantic inference and has no model or
    gateway dependency. Findings are accepted only when bound to frozen bytes and
    official evidence with the approved authority order.
    """

    if not isinstance(ledger, Mapping) or ledger.get("contract") != (
        "adaptive_answer_retrospective_input/v1"
    ):
        raise ValueError("retrospective input contract is invalid")
    if ledger.get("authority_order") != AUTHORITY_ORDER:
        raise ValueError("retrospective authority order is invalid")
    questions = ledger.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("retrospective questions are absent")
    compiled = []
    for question in questions:
        if not isinstance(question, Mapping):
            raise ValueError("retrospective question is invalid")
        question_id = question.get("question_id")
        if not isinstance(question_id, str) or not question_id:
            raise ValueError("retrospective question identity is invalid")
        answer_ref = _checked_ref(question.get("answer_ref"), label="answer")
        reference_ref = _checked_ref(question.get("reference_ref"), label="reference")
        try:
            answer = json.loads(Path(answer_ref["path"]).read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError("frozen answer JSON is invalid") from error
        if answer.get("accepted_at") != question.get("accepted_at"):
            raise ValueError("accepted answer time drifted")
        if question.get("post_acceptance_model_call_count") != 0:
            raise ValueError("post-acceptance model call is forbidden")
        sources = question.get("official_sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError("official source basis is absent")
        checked_sources = [_checked_official_source(source) for source in sources]
        source_ids = {source["source_id"] for source in checked_sources}
        findings = question.get("findings")
        if not isinstance(findings, list) or not findings:
            raise ValueError("retrospective findings are absent")
        for finding in findings:
            if not isinstance(finding, Mapping):
                raise ValueError("finding is invalid")
            if finding.get("classification") not in FINDING_CLASSES:
                raise ValueError("finding classification is invalid")
            if finding.get("failure_layer") not in FAILURE_LAYERS:
                raise ValueError("finding failure layer is invalid")
            if finding.get("mapped_component") not in MAPPED_COMPONENTS:
                raise ValueError("finding mapped component is invalid")
            if not isinstance(finding.get("implementation_component"), str) or not (
                finding["implementation_component"]
            ):
                raise ValueError("finding implementation component is invalid")
            if finding.get("severity") not in SEVERITIES:
                raise ValueError("finding severity is invalid")
            if "reference_answer_risk" not in finding:
                raise ValueError("finding reference_answer_risk is required")
            basis = finding.get("official_basis_ids")
            if not isinstance(basis, list) or not basis:
                raise ValueError("finding official basis is absent")
            if source_ids and not set(basis).issubset(source_ids):
                raise ValueError("finding official basis is unknown")
        scores = question.get("scores")
        if (
            not isinstance(scores, Mapping)
            or set(scores) != {"official_correctness", "reference_coverage", "usability"}
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                or value > 100
                for value in scores.values()
            )
        ):
            raise ValueError("retrospective scores are invalid")
        compiled.append(
            {
                "question_id": question_id,
                "answer_ref": answer_ref,
                "reference_ref": reference_ref,
                "accepted_at": question["accepted_at"],
                "post_acceptance_model_call_count": 0,
                "official_sources": checked_sources,
                "findings": deepcopy(findings),
                "scores": deepcopy(dict(scores)),
                "timing_receipt": deepcopy(answer.get("answer_timing_receipt")),
                "formal_status": answer.get("status"),
            }
        )
    result = {
        "contract": "adaptive_answer_retrospective/v1",
        "authority_order": AUTHORITY_ORDER,
        "model_call_count": 0,
        "score_provenance": (
            "human_assessment_against_content_bound_official_evidence_and_"
            "sealed_reference"
        ),
        "questions": compiled,
        "aggregate": {
            key: round(sum(row["scores"][key] for row in compiled) / len(compiled), 2)
            for key in ("official_correctness", "reference_coverage", "usability")
        },
        "measured_generic_repairs": [
            "separate_internal_claim_taxonomy_from_human_delivery",
            "bind_time_sensitive_facts_to_current_official_source",
            "require_period_specific_numeric_fact_coverage",
            "render_real_line_breaks_in_human_delivery",
        ],
        "frozen_bytes_mutated": False,
        "next_route": "implement-adaptive-answer-policy",
    }
    result["retrospective_sha256"] = _canonical_sha256(result)
    return result


def render_chinese_report(retrospective: Mapping[str, object]) -> str:
    lines = [
        "# Q1 / Q9 / Q5 冻结回答双轨诊断",
        "",
        "本报告仅复用已经冻结的回答；没有重新调用模型。每项官方依据均绑定既有 Gateway 收据与原文字节；官方正确性分数是针对这些冻结证据的人审评估，标准答案只用于覆盖度和实用性比较。",
        "",
    ]
    for row in retrospective["questions"]:
        lines.extend(
            [
                f"## {row['question_id']}",
                "",
                f"- 官方正确性：{row['scores']['official_correctness']}/100",
                f"- 标准答案覆盖：{row['scores']['reference_coverage']}/100",
                f"- 用户可用性：{row['scores']['usability']}/100",
                "",
            ]
        )
        for finding in row["findings"]:
            lines.append(
                f"- [{finding['severity']}/{finding['classification']}/{finding['failure_layer']}/{finding['mapped_component']}:{finding['implementation_component']}] "
                f"{finding['summary']}（参考答案风险：{finding['reference_answer_risk']}）"
            )
        lines.append("")
    aggregate = retrospective["aggregate"]
    lines.extend(
        [
            "## 结论",
            "",
            f"三题平均：官方正确性 {aggregate['official_correctness']}，标准答案覆盖 {aggregate['reference_coverage']}，用户可用性 {aggregate['usability']}。",
            "需要修的是通用的时点绑定、数值覆盖和表达编排，不是增加题号或 CBAM 专用路由。",
            "",
        ]
    )
    return "\n".join(lines)


def write_retrospective(ledger_path: Path, output_path: Path, report_path: Path) -> dict:
    ledger = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
    result = build_retrospective(ledger)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    Path(report_path).write_text(render_chinese_report(result), encoding="utf-8")
    return result


__all__ = ["build_retrospective", "render_chinese_report", "write_retrospective"]
