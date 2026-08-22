"""Deterministic outcome, trajectory, and claim review for native-LCIA cases."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Mapping

from .native_lcia_interpretation import validate_native_lcia_profile
from .native_lcia_interpretation_workflow import _render_report


OUTCOME_GRADE_CONTRACT = "lca_research_kernel.native_lcia_outcome_grade/v1"
TRAJECTORY_GRADE_CONTRACT = "lca_research_kernel.native_lcia_trajectory_grade/v1"
REVIEW_CONTRACT = "lca_research_kernel.native_lcia_clean_review/v1"
FORBIDDEN_PHRASES = {
    "largest impact across all units": "cross_unit_ranking",
    "provider hotspot": "provider_attribution",
    "zero means no emission": "zero_means_absence",
    "zero means no impact": "zero_means_absence",
}


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _acceptance(case_root: Path) -> dict:
    return _read_json(case_root / "acceptance.json")


def _ref_bytes(case_root: Path, ref: Mapping[str, object]) -> bytes:
    path_value = ref.get("path")
    if not isinstance(path_value, str):
        raise ValueError("case artifact reference path is missing")
    path = (case_root / path_value).resolve()
    try:
        path.relative_to(case_root.resolve())
    except ValueError as error:
        raise ValueError("case artifact reference escapes the case") from error
    if not path.is_file():
        raise FileNotFoundError(f"case artifact is absent: {path_value}")
    return path.read_bytes()


def _matching_case(task_freeze: Mapping[str, object], process_exact_ref: object) -> dict:
    matches = [
        value
        for key in ("anchor", "canary")
        for value in [task_freeze.get(key)]
        if isinstance(value, Mapping)
        and value.get("process_exact_ref") == process_exact_ref
    ]
    if len(matches) != 1:
        raise ValueError("case process is outside the frozen task")
    return dict(matches[0])


def grade_native_lcia_outcome(
    repository_root: Path, case_root: Path, task_freeze: Mapping[str, object]
) -> dict:
    """Grade exact values, units, source binding, claims, and package agreement."""

    acceptance = _acceptance(case_root)
    expected_case = _matching_case(task_freeze, acceptance.get("process_exact_ref"))
    findings: list[str] = []
    package_ref = acceptance.get("package_ref")
    report_ref = acceptance.get("report_ref")
    independent_ref = acceptance.get("independent_acceptance_ref")
    if not all(isinstance(ref, Mapping) for ref in (package_ref, report_ref, independent_ref)):
        findings.append("case artifact refs are incomplete")
        package = {}
        report_content = b""
    else:
        package_content = _ref_bytes(case_root, package_ref)
        report_content = _ref_bytes(case_root, report_ref)
        _ref_bytes(case_root, independent_ref)
        if _sha256(package_content) != package_ref.get("sha256"):
            findings.append("registered package hash drifted")
        if _sha256(report_content) != report_ref.get("sha256"):
            findings.append("registered report hash drifted")
        package = json.loads(package_content)
    source_ref = expected_case.get("bound_result_ref")
    source_path = repository_root / str(source_ref.get("path"))
    source_content = source_path.read_bytes() if source_path.is_file() else b""
    source_hashes_match = (
        bool(source_content)
        and _sha256(source_content) == source_ref.get("sha256")
        and package.get("authority", {}).get("source_ref", {}).get("sha256")
        == source_ref.get("sha256")
    )
    if not source_hashes_match:
        findings.append("exact TianGong source identity drifted")
    source = json.loads(source_content) if source_content else {}
    profile = package.get("interpretation_profile")
    profile_errors = (
        validate_native_lcia_profile(profile) if isinstance(profile, Mapping) else ["profile missing"]
    )
    findings.extend(profile_errors)
    expected_rows = {
        row["method_exact_ref"]: row for row in source.get("method_results", [])
    }
    actual_rows = {
        row["method_exact_ref"]: row
        for row in (profile.get("indicators", []) if isinstance(profile, Mapping) else [])
        if isinstance(row, Mapping) and isinstance(row.get("method_exact_ref"), str)
    }
    exact_rows_match = set(expected_rows) == set(actual_rows) and all(
        actual_rows[ref].get("method_id") == row.get("method_id")
        and actual_rows[ref].get("method_version") == row.get("method_version")
        and actual_rows[ref].get("resolved_unit") == row.get("resolved_unit")
        and Decimal(str(actual_rows[ref].get("value"))) == Decimal(str(row.get("value")))
        for ref, row in expected_rows.items()
    )
    if not exact_rows_match:
        findings.append("one or more official LCIA rows drifted")
    relation_checks = profile.get("relation_checks", []) if isinstance(profile, Mapping) else []
    relations_pass = len(relation_checks) == 4 and all(
        isinstance(row, Mapping) and row.get("status") == "passed"
        for row in relation_checks
    )
    if not relations_pass:
        findings.append("frozen relation checks did not all pass")
    report_package_agreement = bool(package) and report_content == _render_report(package)
    if not report_package_agreement:
        findings.append("plain-language report and machine package disagree")
    forbidden_effects = package.get("forbidden_effect_counts")
    claim_ceiling_respected = (
        isinstance(forbidden_effects, Mapping)
        and not any(forbidden_effects.values())
        and isinstance(profile, Mapping)
        and isinstance(profile.get("claim_policy"), Mapping)
    )
    if not claim_ceiling_respected:
        findings.append("claim ceiling or forbidden-effect gate failed")
    functional = package.get("functional_unit", {})
    if isinstance(expected_case.get("functional_unit"), Mapping):
        expected_functional = dict(expected_case["functional_unit"])
    else:
        expected_functional = {
            "amount": expected_case.get("reference_amount"),
            "unit": "unresolved",
        }
    actual_functional = {
        "amount": functional.get("amount"),
        "unit": functional.get("unit"),
    }
    if actual_functional != expected_functional:
        findings.append("functional-unit binding drifted")
    accepted = _read_json(
        case_root / str(acceptance["independent_acceptance_ref"]["path"])
    )
    independently_accepted = accepted.get("status") == "accepted"
    if not independently_accepted:
        findings.append("independent package acceptance is absent")
    return {
        "contract": OUTCOME_GRADE_CONTRACT,
        "status": "passed" if not findings else "failed",
        "process_exact_ref": acceptance.get("process_exact_ref"),
        "indicator_count": len(actual_rows),
        "relation_check_count": len(relation_checks),
        "functional_unit": actual_functional,
        "source_hashes_match": source_hashes_match,
        "exact_rows_match": exact_rows_match,
        "relations_pass": relations_pass,
        "report_package_agreement": report_package_agreement,
        "claim_ceiling_respected": claim_ceiling_respected,
        "independently_accepted": independently_accepted,
        "findings": findings,
    }


def grade_native_lcia_trajectory(
    repository_root: Path, case_root: Path
) -> dict:
    """Grade the Director-only path, replay, recovery, drift, and effects."""

    del repository_root
    evidence = _read_json(case_root / "trajectory-evidence.json")
    checks = {
        "director_only": evidence.get("public_operations") == ["director-advance"],
        "caller_selection_zero": evidence.get("caller_low_level_selection_count") == 0,
        "same_work_item_replay_no_growth": evidence.get("same_work_item_replay_no_growth") is True,
        "interruption_recovery_no_duplicate_effect": evidence.get(
            "interruption_recovery_no_duplicate_effect"
        )
        is True,
        "registry_drift_failed_closed": evidence.get("registry_drift_failed_closed") is True,
        "source_drift_failed_closed": evidence.get("source_drift_failed_closed") is True,
        "forbidden_effects_zero": isinstance(evidence.get("forbidden_effect_counts"), Mapping)
        and not any(evidence["forbidden_effect_counts"].values()),
    }
    return {
        "contract": TRAJECTORY_GRADE_CONTRACT,
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks,
        "public_operations": evidence.get("public_operations"),
        "caller_low_level_selection_count": evidence.get(
            "caller_low_level_selection_count"
        ),
        "same_work_item_replay_no_growth": evidence.get(
            "same_work_item_replay_no_growth"
        ),
        "interruption_recovery_no_duplicate_effect": evidence.get(
            "interruption_recovery_no_duplicate_effect"
        ),
        "registry_drift_failed_closed": evidence.get("registry_drift_failed_closed"),
        "source_drift_failed_closed": evidence.get("source_drift_failed_closed"),
        "forbidden_effect_counts": evidence.get("forbidden_effect_counts"),
    }


def review_native_lcia_case(repository_root: Path, case_root: Path) -> dict:
    """Review scientific integrity and claim wording from a clean file context."""

    task_freeze = _read_json(
        repository_root / "outputs/native-lcia-director-generalization/task-freeze.json"
    )
    p1: list[str] = []
    p2: list[str] = []
    try:
        outcome = grade_native_lcia_outcome(repository_root, case_root, task_freeze)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        outcome = {"status": "failed", "findings": [str(error)]}
    if outcome.get("status") != "passed":
        p1.extend(str(row) for row in outcome.get("findings", ["outcome grade failed"]))
    try:
        acceptance = _acceptance(case_root)
        report = _ref_bytes(case_root, acceptance["report_ref"]).decode("utf-8")
    except (OSError, ValueError, RuntimeError, KeyError, UnicodeError) as error:
        p1.append(str(error))
        report = ""
    lowered = report.casefold()
    for phrase, finding in FORBIDDEN_PHRASES.items():
        if phrase in lowered:
            p2.append(finding)
    return {
        "contract": REVIEW_CONTRACT,
        "status": "accepted" if not p1 and not p2 else "rejected",
        "p1_count": len(set(p1)),
        "p2_count": len(set(p2)),
        "p1_findings": sorted(set(p1)),
        "p2_findings": sorted(set(p2)),
        "review_scope": "exact_source_package_report_and_claim_boundary",
    }
