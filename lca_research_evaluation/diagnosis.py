"""Evidence-bound failure ownership for the performance optimization loop."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Mapping

from .runner_types import RunnerResult, canonical_sha256


FAILURE_RULES = {
    "official_identity_or_version_drift": (
        "context_failure",
        "context_assembler",
        "source-selection-and-version-binding",
        "rebuild-source-bound-context",
    ),
    "oracle_content_visible": (
        "harness_failure",
        "evaluation_harness",
        "oracle-isolation",
        "repair-evaluation-isolation",
    ),
    "direct_formal_write": (
        "harness_failure",
        "assurance_project_record",
        "accepted-write-boundary",
        "repair-assurance-write-boundary",
    ),
    "forbidden_effect": (
        "harness_failure",
        "execution_environment",
        "effect-authorization",
        "repair-project-execution-policy",
    ),
    "critical_missing_data_overclaim": (
        "prompt_failure",
        "lca_domain_kernel",
        "missing-data-claim-ceiling",
        "repair-missing-data-claim-boundary",
    ),
    "official_value_or_unit_drift": (
        "context_failure",
        "lca_domain_kernel",
        "exact-value-and-unit-preservation",
        "repair-lca-value-unit-binding",
    ),
    "candidate_absent": (
        "loop_failure",
        "research_orchestrator",
        "candidate-integration-transition",
        "repair-candidate-integration-loop",
    ),
}


def diagnose_failure(
    task: Mapping[str, object],
    result: RunnerResult,
    grade: Mapping[str, object],
) -> dict:
    failures = grade.get("veto_failures")
    if not isinstance(failures, list) or not failures:
        failure = "candidate_absent" if result.final_candidate_ref is None else None
        failures = [failure] if failure else []
    rules = [FAILURE_RULES.get(str(failure)) for failure in failures]
    rules = [rule for rule in rules if rule is not None]
    primary = {(rule[0], rule[1], rule[2], rule[3]) for rule in rules}
    if len(primary) != 1:
        raise ValueError("diagnosis requires one primary measured failure class")
    failure_class, owner, repair_variable, next_route = next(iter(primary))
    refs = grade.get("evidence_refs") or [result.trajectory_ref]
    if not isinstance(refs, list) or not refs:
        raise ValueError("diagnosis evidence references are absent")
    diagnosis = {
        "contract": "lca_research_evaluation.performance_diagnosis/v1",
        "failure_class": failure_class,
        "owner": owner,
        "evidence_refs": deepcopy(refs),
        "falsification": (
            f"Rerun the preserved regression through {owner}; reject the repair "
            f"unless {repair_variable} changes the failed gate without moving it "
            "to another veto or reducing aggregate quality."
        ),
        "repair_variable": repair_variable,
        "regression_task_ids": [str(task["task_id"])],
        "next_route": next_route,
    }
    diagnosis["diagnosis_sha256"] = canonical_sha256(diagnosis)
    return diagnosis


def validate_repair_contract(value: Mapping[str, object]) -> dict:
    if value.get("contract") != "lca_research_evaluation.repair_contract/v1":
        raise ValueError("repair contract identity is unsupported")
    variables = value.get("repair_variables")
    if not isinstance(variables, list) or len(variables) != 1:
        raise ValueError("repair contract requires one repair variable")
    paths = value.get("product_paths")
    if not isinstance(paths, list) or not paths:
        raise ValueError("repair contract product paths are absent")
    task_ids = {
        str(item).casefold()
        for item in value.get("regression_task_ids", [])
    }
    for path in paths:
        normalized = Path(str(path)).as_posix().casefold()
        if any(task_id in normalized for task_id in task_ids):
            raise ValueError("repair path contains a case or task identity")
    return deepcopy(dict(value))


__all__ = ["diagnose_failure", "validate_repair_contract"]
