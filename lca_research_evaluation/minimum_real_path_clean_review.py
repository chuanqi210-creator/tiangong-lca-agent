"""Clean-context fact-ledger review for minimum-path acceptance evidence."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Mapping


CONTRACT = "d18_minimum_real_path_clean_review/v1"
CASE_ROUTE_PATTERNS = (
    re.compile(
        r"^\+.*\b(?:if|elif|match|case)\b.*\bQ(?:1|5|9)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\+.*\bquestion_id\b.*(?:==|in)\s*.*\bQ(?:1|5|9)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\+.*\bminimum-real-path\b.*(?:route|capability|module)",
        re.IGNORECASE,
    ),
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("clean review input must be a JSON object")
    return value


def _case_specific_routing_findings(production_patch: str) -> list[dict]:
    findings = []
    for line_number, line in enumerate(production_patch.splitlines(), start=1):
        if line.startswith("+++") or not line.startswith("+"):
            continue
        if any(pattern.search(line) for pattern in CASE_ROUTE_PATTERNS):
            findings.append(
                {
                    "priority": "P1",
                    "code": "case_specific_production_routing",
                    "line": line_number,
                    "evidence_sha256": hashlib.sha256(
                        line.encode("utf-8")
                    ).hexdigest(),
                }
            )
    return findings


def review_minimum_real_path_acceptance(
    *,
    acceptance_path: Path,
    production_patch: str,
) -> dict[str, object]:
    """Review only a bounded evidence ledger and production-code patch."""

    path = Path(acceptance_path)
    acceptance = _load_json(path)
    execution_root = path.parents[1]
    findings = _case_specific_routing_findings(production_patch)
    checks = {
        "acceptance_contract_valid": (
            acceptance.get("contract") == "d18_minimum_real_path_acceptance/v1"
            and acceptance.get("acceptance_sha256")
            == _canonical_sha256(
                {
                    key: value
                    for key, value in acceptance.items()
                    if key != "acceptance_sha256"
                }
            )
        ),
        "all_acceptance_gates_true": (
            acceptance.get("status") == "accepted"
            and isinstance(acceptance.get("gates"), Mapping)
            and bool(acceptance["gates"])
            and all(value is True for value in acceptance["gates"].values())
        ),
        "single_finalize_route": (
            acceptance.get("next_routes")
            == ["finalize-minimum-real-research-path"]
        ),
        "development_claim_ceiling": (
            acceptance.get("claim_ceiling")
            == "development_path_evidence_only"
        ),
        "accepted_answer_refs_content_bound": True,
        "case_specific_production_routing_absent": not findings,
        "false_accepted_result_absent": all(
            acceptance.get("gates", {}).get(name) is True
            for name in (
                "q1_formal_or_limited",
                "q9_current_official_read",
                "q5_missing_data_bounded",
                "project_record_only_formal_truth",
            )
        ),
        "source_or_answer_leakage_absent": (
            acceptance.get("gates", {}).get("sealed_answer_isolation") is True
        ),
        "project_record_bypass_absent": (
            acceptance.get("gates", {}).get(
                "project_record_only_formal_truth"
            )
            is True
        ),
        "runtime_failure_not_scientific_gap": True,
        "replay_or_effect_duplication_absent": all(
            acceptance.get("gates", {}).get(name) is True
            for name in ("replay_zero_growth", "forbidden_effects_zero")
        ),
        "unsupported_generalization_claim_absent": (
            acceptance.get("gates", {}).get(
                "claim_ceiling_development_only"
            )
            is True
        ),
    }
    evidence = acceptance.get("task_evidence")
    if not isinstance(evidence, Mapping):
        checks["accepted_answer_refs_content_bound"] = False
        checks["runtime_failure_not_scientific_gap"] = False
    else:
        for question_id in ("Q1", "Q9", "Q5"):
            row = evidence.get(question_id)
            ref = row.get("answer_ref") if isinstance(row, Mapping) else None
            if (
                not isinstance(ref, Mapping)
                or not isinstance(ref.get("path"), str)
                or not isinstance(ref.get("sha256"), str)
            ):
                checks["accepted_answer_refs_content_bound"] = False
                continue
            answer_path = execution_root / str(ref["path"])
            try:
                answer_path.resolve().relative_to(execution_root.resolve())
            except ValueError:
                checks["accepted_answer_refs_content_bound"] = False
                continue
            if (
                not answer_path.is_file()
                or _file_sha256(answer_path) != ref["sha256"]
            ):
                checks["accepted_answer_refs_content_bound"] = False
                continue
            answer = _load_json(answer_path)
            result = answer.get("result")
            if not isinstance(result, Mapping):
                checks["runtime_failure_not_scientific_gap"] = False
                continue
            missing = json.dumps(
                result.get("missing_facts", []),
                ensure_ascii=False,
                sort_keys=True,
            ).casefold()
            if any(
                term in missing
                for term in (
                    "provider",
                    "quota",
                    "rate_limit",
                    "billing",
                    "runtime unavailable",
                )
            ):
                checks["runtime_failure_not_scientific_gap"] = False

    for name, passed in checks.items():
        represented = (
            name == "case_specific_production_routing_absent"
            and any(
                finding.get("code") == "case_specific_production_routing"
                for finding in findings
            )
        )
        if not passed and not represented and not any(
            finding.get("code") == name for finding in findings
        ):
            findings.append(
                {
                    "priority": "P1",
                    "code": name,
                    "evidence_sha256": _canonical_sha256(
                        {"check": name, "passed": passed}
                    ),
                }
            )
    p1_count = sum(row["priority"] == "P1" for row in findings)
    p2_count = sum(row["priority"] == "P2" for row in findings)
    report = {
        "contract": CONTRACT,
        "status": "passed" if p1_count == 0 and p2_count == 0 else "failed",
        "review_method": "isolated_deterministic_fact_ledger",
        "input_manifest": {
            "acceptance_path": path.relative_to(execution_root).as_posix(),
            "acceptance_file_sha256": _file_sha256(path),
            "acceptance_sha256": acceptance.get("acceptance_sha256"),
            "production_patch_sha256": hashlib.sha256(
                production_patch.encode("utf-8")
            ).hexdigest(),
        },
        "checks": checks,
        "findings": findings,
        "p1_count": p1_count,
        "p2_count": p2_count,
        "claim_ceiling": (
            "mechanical_clean_review_of_development_path_evidence"
        ),
    }
    report["review_sha256"] = _canonical_sha256(report)
    return report
