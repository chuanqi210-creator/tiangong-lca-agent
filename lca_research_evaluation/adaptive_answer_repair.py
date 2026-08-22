"""Deterministically replay accepted negative evidence into repair receipts."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Mapping, Optional

from lca_research_kernel.claim_semantics import resolve_claim_modality
from lca_research_kernel.adaptive_answer_repair_contract import (
    validate_repair_contract,
)
from lca_research_kernel.official_source_binding import (
    build_official_source_observation,
    classify_frozen_source_relation,
    freeze_official_source_snapshot,
    validate_official_source_observation,
)


CONTRACT_PATH = Path("docs/adaptive-answer-evidence-repair-contract.json")
TRACE_PATH = Path(
    "outputs/real-question-optimization/adaptive-answer-held-out/"
    "runtime-data-v4/projects/adaptive-heldout-36ef455282b39a14e538cfbb/"
    "run-traces/adaptive-heldout-36ef455282b39a14e538cfbb.jsonl"
)
CACHE_ROOT = Path(
    "outputs/real-question-optimization/adaptive-answer-held-out/"
    "runtime-data-v4/control/official-public-source-cache"
)
OUTPUT_ROOT = Path(
    "outputs/real-question-optimization/adaptive-answer-repair"
)
CLAIM_OUTPUT = "claim-modality-repair-receipt-v3.json"
SOURCE_OUTPUT = "source-binding-repair-receipt-v3.json"
ACCEPTANCE_OUTPUT = "acceptance-v3.json"
HUMAN_OUTPUT = "acceptance-v3.zh-CN.md"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _pretty(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _self_hash(value: Mapping[str, object]) -> dict:
    result = deepcopy(dict(value))
    result["receipt_sha256"] = hashlib.sha256(_canonical(result)).hexdigest()
    return result


def validate_acceptance_basis(acceptance: Mapping[str, object]) -> None:
    if (
        acceptance.get("contract") != "adaptive_answer_heldout_acceptance/v1"
        or acceptance.get("acceptance_status") != "accepted_negative_evidence"
        or acceptance.get("product_outcome") != "failed"
    ):
        raise ValueError("historical negative acceptance status drifted")
    grade = acceptance.get("grade")
    if (
        not isinstance(grade, Mapping)
        or grade.get("decision") != "failed"
        or grade.get("pass_threshold") != 85
    ):
        raise ValueError("historical pass threshold or failed grade drifted")
    diagnosis = acceptance.get("failure_diagnosis")
    if not isinstance(diagnosis, Mapping):
        raise ValueError("historical failure diagnosis is absent")
    if diagnosis.get("failed_rows_deleted") is not False:
        raise ValueError("historical failed rows were deleted")
    if diagnosis.get("cross_version_evidence_spliced") is not False:
        raise ValueError("historical evidence was cross-version spliced")
    source = acceptance.get("source_lock_diagnosis")
    if (
        not isinstance(source, Mapping)
        or source.get("fixture_or_threshold_mutated") is not False
        or source.get("third_model_run_performed") is not False
    ):
        raise ValueError("historical source-lock diagnosis drifted")


def _load_contract_and_verify_immutability(root: Path) -> tuple[dict, dict]:
    contract = _read(root / CONTRACT_PATH)
    contract = validate_repair_contract(contract)
    evidence = contract.get("immutable_evidence")
    if not isinstance(evidence, Mapping):
        raise ValueError("immutable repair evidence is absent")
    loaded: dict[str, dict] = {}
    for role, reference in evidence.items():
        if not isinstance(reference, Mapping):
            raise ValueError(f"immutable reference is invalid: {role}")
        path = root / str(reference.get("path", ""))
        if not path.is_file() or _sha256(path) != reference.get("sha256"):
            raise ValueError(f"immutable evidence drifted: {role}")
        if role != "heldout_oracle_v3":
            loaded[str(role)] = _read(path)
    acceptance = loaded["negative_acceptance_v2"]
    validate_acceptance_basis(acceptance)
    return contract, loaded


def _model_candidates(root: Path) -> tuple[list[dict], str]:
    path = root / TRACE_PATH
    trace_sha256 = _sha256(path)
    candidates: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("event_type") == "research.run.model.completed":
            payload = event.get("payload")
            if not isinstance(payload, Mapping) or not isinstance(
                payload.get("candidate"), Mapping
            ):
                raise ValueError("model-completed trace event lacks a candidate")
            candidate = deepcopy(dict(payload["candidate"]))
            if hashlib.sha256(_canonical(candidate)).hexdigest() != payload.get(
                "candidate_sha256"
            ):
                raise ValueError("raw model candidate hash drifted")
            candidates.append(candidate)
    if len(candidates) != 3:
        raise ValueError("historical model-completed row count drifted")
    return candidates, trace_sha256


def _claim_receipt(root: Path, run: Mapping[str, object]) -> dict:
    candidates, trace_sha256 = _model_candidates(root)
    candidate = candidates[-1]
    raw_before = hashlib.sha256(_canonical(candidate)).hexdigest()
    output = candidate.get("output")
    claims = output.get("claim_statements") if isinstance(output, Mapping) else None
    if not isinstance(claims, list) or len(claims) != 3:
        raise ValueError("frozen negative candidate claim rows drifted")

    resolutions: list[dict] = []
    for claim in claims:
        if not isinstance(claim, Mapping):
            raise ValueError("frozen claim row is invalid")
        evidence = claim.get("evidence_basis")
        if not isinstance(evidence, Mapping):
            raise ValueError("frozen claim evidence is absent")
        resolution = resolve_claim_modality(
            declared_modality=str(claim.get("modality")),
            statement=str(claim.get("statement")),
            literal=str(evidence.get("literal")),
            source_sha256=str(evidence.get("source_sha256")),
        )
        resolutions.append(resolution)
    changed = [row for row in resolutions if row["changed"]]
    if (
        len(changed) != 1
        or changed[0]["declared_modality"] != "legal_must"
        or changed[0]["normalized_modality"] != "legal_exclusion"
    ):
        raise ValueError("real negative claim did not yield the one expected repair")

    raw_after = hashlib.sha256(_canonical(candidate)).hexdigest()
    if raw_before != raw_after:
        raise ValueError("repair mutated the raw model candidate")
    runtime = run.get("runtime_evidence")
    if not isinstance(runtime, Mapping) or runtime.get("model_completed_count") != 3:
        raise ValueError("historical runtime model count drifted")
    return _self_hash(
        {
            "contract": "adaptive_answer_claim_modality_repair_receipt/v1",
            "status": "passed",
            "raw_trace": {
                "path": TRACE_PATH.as_posix(),
                "sha256": trace_sha256,
            },
            "raw_candidate_sha256_before": raw_before,
            "raw_candidate_sha256_after": raw_after,
            "changed_claim_count": 1,
            "unchanged_claim_count": len(resolutions) - 1,
            "target_resolution": changed[0],
            "unchanged_resolution_sha256s": [
                hashlib.sha256(_canonical(row)).hexdigest()
                for row in resolutions
                if not row["changed"]
            ],
            "statement_mutations": 0,
            "literal_mutations": 0,
            "source_mutations": 0,
            "model_completed_count_before": 3,
            "model_completed_count_after": len(candidates),
            "model_calls": 0,
            "historical_outcome_promoted": False,
        }
    )


def _source_content(project_record: Mapping[str, object]) -> dict:
    facts = project_record.get("facts")
    if not isinstance(facts, Mapping):
        raise ValueError("runtime Project Record facts are absent")
    matches = []
    for key, fact in facts.items():
        if not str(key).startswith("capability-result:") or not isinstance(
            fact, Mapping
        ):
            continue
        value = fact.get("value")
        result = value.get("result") if isinstance(value, Mapping) else None
        content = (
            result.get("content")
            if isinstance(result, Mapping)
            else value.get("content")
            if isinstance(value, Mapping)
            else None
        )
        if isinstance(content, Mapping) and content.get("contract") == (
            "official_public_source_content/v1"
        ):
            matches.append(deepcopy(dict(content)))
    if len(matches) != 1:
        raise ValueError("official-source runtime evidence is not unique")
    return matches[0]


def _source_receipt(
    root: Path,
    task: Mapping[str, object],
    project_record: Mapping[str, object],
) -> dict:
    content = _source_content(project_record)
    source_receipt = content.get("source_receipt")
    cache_ref = content.get("cache_ref")
    if not isinstance(source_receipt, Mapping) or not isinstance(cache_ref, Mapping):
        raise ValueError("official-source receipt or cache ref is absent")
    cache_path = root / CACHE_ROOT / str(cache_ref.get("path", ""))
    observed_body = str(source_receipt.get("body_sha256"))
    if (
        not cache_path.is_file()
        or _sha256(cache_path) != observed_body
        or cache_path.stat().st_size != cache_ref.get("bytes")
    ):
        raise ValueError("frozen official-source object drifted")
    task_source = task.get("source")
    if not isinstance(task_source, Mapping):
        raise ValueError("held-out task source is absent")
    repository_cache_ref = {
        "path": (CACHE_ROOT / str(cache_ref["path"])).as_posix(),
        "sha256": observed_body,
        "bytes": int(source_receipt["body_bytes"]),
    }
    observed = build_official_source_observation(
        source_id="eu-reg-2023-956-consolidated",
        authority=str(task_source["identity"]),
        declared_version=str(task_source["version"]),
        requested_url=str(source_receipt["requested_url"]),
        final_url=str(source_receipt["final_url"]),
        parse_location=str(source_receipt["parse_location"]),
        observed_at=str(source_receipt["accessed_at"]),
        body_sha256=observed_body,
        body_bytes=int(source_receipt["body_bytes"]),
        media_type=str(source_receipt["media_type"]),
        http_status=int(source_receipt["status"]),
        etag=source_receipt.get("etag"),
        last_modified=source_receipt.get("last_modified"),
        cache_ref=repository_cache_ref,
    )
    if not validate_official_source_observation(observed):
        raise ValueError("typed official-source observation is invalid")
    historical_relation = classify_frozen_source_relation(
        task_source, observed, root=root
    )
    if historical_relation.get("relation") != "legacy_untyped_digest_recovered":
        raise ValueError("legacy task source was reinterpreted instead of recovered")

    body_ref = {
        "path": (CACHE_ROOT / str(cache_ref["path"])).as_posix(),
        "sha256": observed_body,
    }
    frozen = freeze_official_source_snapshot(observed, body_ref=body_ref)
    exact_relation = classify_frozen_source_relation(
        frozen, observed, root=root
    )
    if exact_relation.get("relation") != "exact_snapshot_observed":
        raise ValueError("current frozen object did not classify as exact")

    candidate_bytes = b"deterministic-same-locator-candidate-revision"
    candidate_sha256 = hashlib.sha256(candidate_bytes).hexdigest()
    candidate = build_official_source_observation(
        source_id="eu-reg-2023-956-consolidated",
        authority=str(task_source["identity"]),
        declared_version=str(task_source["version"]),
        requested_url=str(source_receipt["requested_url"]),
        final_url=str(source_receipt["final_url"]),
        parse_location=str(source_receipt["parse_location"]),
        observed_at="2026-08-01T01:10:00Z",
        body_sha256=candidate_sha256,
        body_bytes=len(candidate_bytes),
        media_type=str(source_receipt["media_type"]),
        http_status=200,
        etag=None,
        last_modified=None,
        cache_ref={
            "path": f"candidate-only/{candidate_sha256}.bin",
            "sha256": candidate_sha256,
            "bytes": len(candidate_bytes),
        },
    )
    candidate_relation = classify_frozen_source_relation(
        frozen, candidate, root=root
    )
    if candidate_relation.get("relation") != "same_locator_candidate_revision":
        raise ValueError("same-locator changed bytes did not fail closed")

    return _self_hash(
        {
            "contract": "adaptive_answer_source_binding_repair_receipt/v1",
            "status": "passed",
            "legacy_task_source_sha256": task_source.get("sha256"),
            "verified_observed_body_sha256": observed_body,
            "verified_object": {
                "path": body_ref["path"],
                "sha256": observed_body,
                "bytes": cache_path.stat().st_size,
            },
            "observation_binding": observed,
            "historical_relation": historical_relation,
            "exact_snapshot_probe": exact_relation,
            "candidate_revision_probe": candidate_relation,
            "accepted_snapshot_replacements": 0,
            "oracle_mutations": 0,
            "external_reads": 0,
            "external_writes": 0,
            "model_calls": 0,
        }
    )


def build_repair(root: Path) -> dict[str, object]:
    root = Path(root).resolve()
    contract, loaded = _load_contract_and_verify_immutability(root)
    before = {
        role: reference["sha256"]
        for role, reference in contract["immutable_evidence"].items()
    }
    run = loaded["heldout_run_v4"]
    claim_receipt = _claim_receipt(root, run)
    source_receipt = _source_receipt(
        root,
        loaded["heldout_task_v3"],
        loaded["runtime_project_record_v4"],
    )
    after = {
        role: _sha256(root / str(reference["path"]))
        for role, reference in contract["immutable_evidence"].items()
    }
    mismatches = sorted(role for role in before if before[role] != after[role])
    claim_bytes = _pretty(claim_receipt)
    source_bytes = _pretty(source_receipt)
    acceptance = _self_hash(
        {
            "contract": "adaptive_answer_evidence_repair_acceptance/v1",
            "status": "accepted_contract_repair",
            "historical_heldout_outcome": "failed_negative_evidence",
            "pass_threshold": 85,
            "repair_receipts": {
                "claim_semantics": {
                    "path": (OUTPUT_ROOT / CLAIM_OUTPUT).as_posix(),
                    "sha256": hashlib.sha256(claim_bytes).hexdigest(),
                },
                "official_source_binding": {
                    "path": (OUTPUT_ROOT / SOURCE_OUTPUT).as_posix(),
                    "sha256": hashlib.sha256(source_bytes).hexdigest(),
                },
            },
            "immutable_evidence": deepcopy(dict(contract["immutable_evidence"])),
            "immutable_evidence_mismatches": mismatches,
            "immutable_evidence_mismatch_count": len(mismatches),
            "failed_rows_deleted": False,
            "cross_version_evidence_spliced": False,
            "threshold_lowered": False,
            "old_result_promoted": False,
            "model_calls": 0,
            "external_reads": 0,
            "external_writes": 0,
            "oracle_mutations": 0,
            "project_record_events_added": 0,
            "evidence_dag": {
                "historical_run_role": "immutable_negative_evidence_only",
                "repair_validation": "deterministic_receipts_only",
                "future_execution_required": True,
                "rerun_19_question_regression": False,
            },
            "claim_ceiling": {
                "supported": [
                    "The two measured contract defects are repaired deterministically.",
                    "The accepted historical failed result and all frozen evidence remain unchanged.",
                ],
                "unsupported": [
                    "The previous held-out task passed.",
                    "Adaptive-answer generalization has been demonstrated.",
                    "The 19-question regression is authorized or required by this repair.",
                ],
            },
            "next_route": "run-one-new-sealed-adaptive-answer-preflight",
        }
    )
    if mismatches:
        raise ValueError(f"immutable repair evidence drifted: {mismatches}")
    human = (
        "# 自适应科研答案合同修复验收\n\n"
        "本轮只修复了两项已测得的通用合同缺陷，没有重新调用模型，也没有重跑 19 题。\n\n"
        "- 法规中的“不适用/不要求”现在被明确归为 `legal_exclusion`，原始模型候选保持不变。\n"
        "- 官方来源已拆分为定位、冻结字节快照和一次观察三层身份；同 URL 新字节只能进入候选版本复核。\n"
        "- 旧题目的来源 SHA 保留为含义未类型化的历史摘要，不再冒充原始网页字节哈希。\n"
        "- 冻结题目、oracle、Raw Trace、Project Record、失败验收和 85 分门槛均未改变。\n"
        "- 外部读取、外部写入和模型调用均为 0。\n\n"
        "这不表示上一轮盲测已经通过，也不证明泛化能力。下一步只能使用一份新的密封题目做单题预检。\n"
    )
    return {
        "claim_receipt": claim_receipt,
        "source_receipt": source_receipt,
        "acceptance": acceptance,
        "human_report": human,
    }


def render_repair(root: Path) -> tuple[dict, dict[str, bytes]]:
    """Render expected bytes without mutating accepted repair outputs."""

    root = Path(root).resolve()
    result = build_repair(root)
    payloads = {
        CLAIM_OUTPUT: _pretty(result["claim_receipt"]),
        SOURCE_OUTPUT: _pretty(result["source_receipt"]),
        ACCEPTANCE_OUTPUT: _pretty(result["acceptance"]),
        HUMAN_OUTPUT: str(result["human_report"]).encode("utf-8"),
    }
    return result, payloads


def write_repair(root: Path, *, output_root: Optional[Path] = None) -> dict:
    root = Path(root).resolve()
    destination = Path(output_root) if output_root is not None else root / OUTPUT_ROOT
    result, payloads = render_repair(root)
    destination.mkdir(parents=True, exist_ok=True)
    summary: dict[str, object] = {
        "status": result["acceptance"]["status"],
        "next_route": result["acceptance"]["next_route"],
        "artifacts": {},
    }
    for name, payload in payloads.items():
        path = destination / name
        path.write_bytes(payload)
        summary["artifacts"][name] = hashlib.sha256(payload).hexdigest()
    return summary
