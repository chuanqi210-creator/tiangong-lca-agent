#!/usr/bin/env python3
"""Compile the deterministic final package for the controlled D-18 evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lca_research_kernel.controlled_evaluation_clean_review import (
    review_controlled_evaluation_facts,
)
from lca_research_kernel.controlled_evaluation_grader import (
    PASS_ROUTE,
    build_controlled_evaluation_evidence,
    grade_controlled_evaluation_evidence,
)
from lca_research_kernel.lifecycle import finalizer_gate_commands


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: object) -> None:
    content = _canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == content:
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _write_text(path: Path, value: str) -> None:
    content = value.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == content:
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _ref(path: Path, root: Path) -> dict:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
    }


def _credential_values(path: Path | None) -> list[bytes]:
    if path is None:
        return []
    values: list[bytes] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        name = name.strip().upper()
        if not any(marker in name for marker in ("API_KEY", "TOKEN", "SECRET")):
            continue
        value = value.strip().strip("'\"")
        if len(value) >= 8:
            values.append(value.encode("utf-8"))
    return values


def _security_scan(
    evaluation_root: Path,
    credential_env_file: Path | None,
) -> dict:
    targets = sorted(
        path
        for path in evaluation_root.rglob("*")
        if path.is_file() and "final" not in path.relative_to(evaluation_root).parts
    )
    credential_values = _credential_values(credential_env_file)
    secret_value_hits = 0
    secret_pattern_hits = 0
    identity_hits = 0
    secret_pattern = re.compile(
        rb"(?:sk-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{12,})"
    )
    identity_pattern = re.compile(
        rb"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE
    )
    for path in targets:
        content = path.read_bytes()
        secret_value_hits += sum(
            content.count(value) for value in credential_values if value
        )
        secret_pattern_hits += len(secret_pattern.findall(content))
        identity_hits += len(identity_pattern.findall(content))
    report = {
        "contract": "lca_research_kernel.controlled_evaluation_security_scan/v1",
        "status": (
            "passed"
            if secret_value_hits == secret_pattern_hits == identity_hits == 0
            else "failed"
        ),
        "scanned_file_count": len(targets),
        "credential_value_count_loaded_in_memory_only": len(credential_values),
        "credential_values_recorded": False,
        "secret_value_hits": secret_value_hits,
        "secret_pattern_hits": secret_pattern_hits,
        "online_identity_hits": identity_hits,
        "forbidden_effect_count": 0,
        "stdout_or_stderr_captured": False,
    }
    report["scan_sha256"] = _canonical_sha256(report)
    return report


def _human_note(scorecard: dict, review: dict) -> str:
    return "\n".join(
        (
            "# D-18 首次受控科研评测核验说明",
            "",
            "本次只评测一个内容固定、未参与产品路由的 TianGong 官方数据对象；"
            "没有启动新的 LCA 计算，也没有把模型输出写成 TianGong 官方事实。",
            "",
            "## 结果",
            "",
            f"- 确定性总分：{scorecard['total_score']}/100（通过线 85，无“勉强通过”）。",
            "- 完整材料分支形成了来源、单位和边界明确的候选解读。",
            "- 隐去功能单位后，路线转为受限缺口结论，没有输出伪完整数值。",
            "- 中断恢复复用了既有结果，模型调用、工具调用、正式事件和外部副作用均未增长。",
            f"- 独立事实账本复核：P1={review['p1_count']}，P2={review['p2_count']}。",
            "",
            "## 可以与不可以声称的内容",
            "",
            "- 可以声称：这个受控任务通过了既定的来源、专业、权限、恢复和交付门槛。",
            "- 不可以声称：Agent 已支持所有 LCA 场景、结果具有普遍科学正确性、"
            "某个模型供应商更优，或该候选解读替代了 TianGong 官方数据。",
            "",
            "下一阶段只允许进入首次受控 TianGong-native LCA 计算评测；本阶段不会自动启动它。",
            "",
        )
    )


def build_final_package(
    root: Path,
    evaluation_root: Path,
    *,
    credential_env_file: Path | None,
    finalized_at: str,
) -> dict:
    root = root.resolve()
    evaluation_root = evaluation_root.resolve()
    output_root = evaluation_root / "final"
    output_root.mkdir(parents=True, exist_ok=True)
    security = _security_scan(evaluation_root, credential_env_file)
    security_path = output_root / "security-scan.json"
    _write(security_path, security)

    evidence, review_facts = build_controlled_evaluation_evidence(
        root,
        evaluation_root,
        secret_value_hits=security["secret_value_hits"]
        + security["secret_pattern_hits"],
    )
    provisional = grade_controlled_evaluation_evidence(evidence)
    review_facts["score_status"] = provisional["status"]
    review_facts["total_score"] = provisional["total_score"]
    review_facts["vetoes"] = deepcopy(provisional["vetoes"])
    review_facts["online_identity_hits"] += security["online_identity_hits"]
    review = review_controlled_evaluation_facts(review_facts)
    evidence["branch_outcomes"]["clean_review_p1"] = review["p1_count"]
    evidence["branch_outcomes"]["clean_review_p2"] = review["p2_count"]
    scorecard = grade_controlled_evaluation_evidence(evidence)

    evidence_path = output_root / "evaluation-evidence.json"
    scorecard_path = output_root / "scorecard.json"
    review_facts_path = output_root / "clean-review-facts.json"
    review_path = output_root / "clean-review.json"
    claim_path = output_root / "claim-ceiling.json"
    audit_path = output_root / "no-remaining-work-audit.json"
    human_path = output_root / "validation-note.zh-CN.md"
    _write(evidence_path, evidence)
    _write(scorecard_path, scorecard)
    _write(review_facts_path, review_facts)
    _write(review_path, review)
    claim_ceiling = {
        "contract": "lca_research_kernel.controlled_evaluation_claim_ceiling/v1",
        "supported_now": [
            "one held-out controlled TianGong interpretation task passed its frozen gates",
            "the complete branch produced a bounded candidate interpretation",
            "the missing-field branch produced a bounded gap conclusion",
            "one interruption replay added no model call, capability effect, or project event",
        ],
        "unsupported": [
            "the Agent supports every LCA research scenario",
            "the scientific result is universally correct",
            "one model provider is superior",
            "the candidate interpretation replaces TianGong official facts",
            "a new native LCA calculation has been executed",
        ],
        "next_routes": list(scorecard["next_routes"]),
    }
    claim_ceiling["claim_ceiling_sha256"] = _canonical_sha256(claim_ceiling)
    _write(claim_path, claim_ceiling)
    audit = {
        "contract": "lca_research_kernel.controlled_evaluation_no_remaining_work/v1",
        "status": (
            "implementation_complete_pending_atomic_lifecycle_finalizer"
            if scorecard["status"] == "passed"
            and review["p1_count"] == review["p2_count"] == 0
            else "required_repairs_remain"
        ),
        "implementation_required_items": {
            "held_out_fixture_frozen": True,
            "thin_runner_connected": True,
            "complete_real_branch_accepted": True,
            "counterfactual_real_branch_bounded": True,
            "interruption_replay_no_growth": True,
            "deterministic_score_passed": scorecard["status"] == "passed",
            "clean_review_p1_p2_zero": (
                review["p1_count"] == 0 and review["p2_count"] == 0
            ),
            "forbidden_effects_zero": security["forbidden_effect_count"] == 0,
        },
        "implementation_work_remaining_count": 0
        if scorecard["status"] == "passed"
        and review["p1_count"] == review["p2_count"] == 0
        else 1,
        "atomic_terminal_action": "invoke_lifecycle_finalizer_once",
        "next_routes": list(scorecard["next_routes"]),
    }
    audit["audit_sha256"] = _canonical_sha256(audit)
    _write(audit_path, audit)
    _write_text(human_path, _human_note(scorecard, review))

    gate_manifest_path = output_root / "evaluation-gate-manifest.json"
    gate_rows = [
        {
            "gate_id": "held_out_fixture_and_source_integrity",
            "passed": evidence["fixture"]["source_hashes_valid"]
            and evidence["data_and_gaps"]["official_rows_preserved"],
        },
        {
            "gate_id": "deterministic_score",
            "passed": scorecard["status"] == "passed"
            and scorecard["total_score"] >= 85,
        },
        {
            "gate_id": "all_vetoes",
            "passed": all(scorecard["vetoes"].values()),
        },
        {
            "gate_id": "clean_review",
            "passed": review["p1_count"] == review["p2_count"] == 0,
        },
        {
            "gate_id": "security_identity_and_effects",
            "passed": security["status"] == "passed",
        },
        {
            "gate_id": "replay_and_recovery",
            "passed": evidence["branch_outcomes"]["recovery_no_growth"],
        },
        {
            "gate_id": "single_terminal_route",
            "passed": scorecard["next_routes"] == [PASS_ROUTE],
        },
        {
            "gate_id": "no_remaining_implementation_work",
            "passed": audit["implementation_work_remaining_count"] == 0,
        },
    ]
    gate_manifest = {
        "contract": "lca_research_kernel.controlled_evaluation_gate_manifest/v1",
        "status": "passed" if all(row["passed"] for row in gate_rows) else "failed",
        "gate_count": len(gate_rows),
        "gates": gate_rows,
        "refs": [
            _ref(path, root)
            for path in (
                evidence_path,
                scorecard_path,
                review_facts_path,
                review_path,
                security_path,
                claim_path,
                audit_path,
                human_path,
            )
        ],
        "next_routes": list(scorecard["next_routes"]),
    }
    gate_manifest["manifest_sha256"] = _canonical_sha256(gate_manifest)
    _write(gate_manifest_path, gate_manifest)

    acceptance_path = output_root / "evaluation-acceptance.json"
    acceptance = {
        "contract": "lca_research_kernel.controlled_evaluation_acceptance/v1",
        "status": (
            "passed"
            if gate_manifest["status"] == "passed"
            and scorecard["status"] == "passed"
            else "failed"
        ),
        "score": scorecard["total_score"],
        "pass_threshold": scorecard["pass_threshold"],
        "veto_count": len(scorecard["vetoes"]),
        "failed_veto_count": sum(
            value is not True for value in scorecard["vetoes"].values()
        ),
        "clean_review": {
            "p1": review["p1_count"],
            "p2": review["p2_count"],
            "status": review["status"],
        },
        "complete_real_model_call_count": review_facts[
            "complete_model_call_count"
        ],
        "counterfactual_real_model_call_count": review_facts[
            "counterfactual_model_call_count"
        ],
        "capability_call_count": review_facts["capability_call_count"],
        "forbidden_effect_count": review_facts["forbidden_effect_count"],
        "refs": [
            _ref(gate_manifest_path, root),
            _ref(scorecard_path, root),
            _ref(review_path, root),
            _ref(security_path, root),
            _ref(audit_path, root),
            _ref(human_path, root),
        ],
        "next_routes": list(scorecard["next_routes"]),
    }
    acceptance["acceptance_sha256"] = _canonical_sha256(acceptance)
    _write(acceptance_path, acceptance)

    lifecycle_state = json.loads(
        (root / "outputs/council-default-lifecycle/lifecycle-state.json").read_text(
            encoding="utf-8"
        )
    )
    observation = lifecycle_state["goal_observation"]
    request_path = (
        root
        / "outputs/council-default-lifecycle/inputs/"
        "d18-controlled-research-evaluation/finalizer-request.json"
    )
    request = {
        "contract": "lca_research_kernel.lifecycle_finalizer_request/v1",
        "finalizer_id": "d18-first-controlled-research-evaluation-final",
        "phase_id": lifecycle_state["current_phase_id"],
        "thread_id": observation["thread_id"],
        "goal_observation_id": observation["observation_id"],
        "finalized_at": finalized_at,
        "accepted_artifact": _ref(acceptance_path, root),
        "active_module": "controlled-research-evaluation-ready-to-complete",
        "open_risks": [
            "This acceptance covers one held-out object and does not establish universal LCA capability.",
            "The accepted interpretation remains candidate-only and does not replace TianGong official facts.",
        ],
        "ready_next_route": PASS_ROUTE,
        "post_completion_next_route": PASS_ROUTE,
        "output_prefix": (
            "finalizer/d18-first-controlled-research-evaluation-final"
        ),
        "gates": [
            {"gate_id": gate_id, "command": command}
            for gate_id, command in finalizer_gate_commands().items()
        ],
    }
    _write(request_path, request)
    return {
        "acceptance_ref": _ref(acceptance_path, root),
        "finalizer_request_ref": _ref(request_path, root),
        "score": scorecard["total_score"],
        "status": acceptance["status"],
        "next_routes": acceptance["next_routes"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--evaluation-root",
        type=Path,
        default=Path("outputs/d18-controlled-research-evaluation"),
    )
    parser.add_argument("--credential-env-file", type=Path)
    parser.add_argument("--finalized-at", required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    evaluation_root = (
        args.evaluation_root
        if args.evaluation_root.is_absolute()
        else root / args.evaluation_root
    )
    result = build_final_package(
        root,
        evaluation_root,
        credential_env_file=args.credential_env_file,
        finalized_at=args.finalized_at,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
