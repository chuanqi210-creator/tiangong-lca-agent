"""Clustered quality, safety, and time metrics for the frozen three-arm study."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from copy import deepcopy
from typing import Mapping, Sequence

from .contracts import EXACT_THRESHOLDS, RUNNER_ARMS
from .runner_types import canonical_sha256


def _mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return math.inf
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return float(ordered[index])


def _task_metrics(rows: Sequence[Mapping[str, object]]) -> dict:
    success = _mean([1.0 if row.get("success") is True else 0.0 for row in rows])
    scores = [float(row.get("lca_score", 0)) for row in rows]
    valid_times = [
        float(row["valid_result_seconds"])
        for row in rows
        if row.get("success") is True
        and isinstance(row.get("valid_result_seconds"), (int, float))
    ]
    return {
        "task_id": rows[0]["task_id"],
        "family": rows[0]["family"],
        "completion_rate": success,
        "mean_lca_score": _mean(scores),
        "valid_result_seconds": (
            statistics.median(valid_times) if valid_times else math.inf
        ),
        "unnecessary_tool_ratio": _mean(
            [float(row.get("unnecessary_tool_ratio", 0)) for row in rows]
        ),
        "recovery_success_rate": _mean(
            [1.0 if row.get("recovery_success") is True else 0.0 for row in rows]
        ),
        "duplicate_effect_count": sum(
            int(row.get("duplicate_effect_count", 0)) for row in rows
        ),
        "veto_failures": sorted(
            {
                str(failure)
                for row in rows
                for failure in (row.get("veto_failures") or [])
            }
        ),
        "scientific_exactness": min(
            float(row.get("scientific_exactness", 1.0)) for row in rows
        ),
        "unit_exactness": min(
            float(row.get("unit_exactness", 1.0)) for row in rows
        ),
        "grader_disagreement_points": max(
            float(row.get("grader_disagreement_points", 0) or 0)
            for row in rows
        ),
    }


def aggregate_arm_metrics(rows: Sequence[Mapping[str, object]]) -> dict:
    if not rows:
        raise ValueError("performance rows are absent")
    by_arm_task: dict[tuple[str, str], list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        arm = row.get("arm")
        task_id = row.get("task_id")
        family = row.get("family")
        if (
            arm not in RUNNER_ARMS
            or not isinstance(task_id, str)
            or not isinstance(family, str)
        ):
            raise ValueError("performance row identity is invalid")
        by_arm_task[(str(arm), task_id)].append(row)
    result: dict[str, dict] = {}
    for arm in RUNNER_ARMS:
        arm_rows = [row for row in rows if row["arm"] == arm]
        if not arm_rows:
            continue
        tasks = [
            _task_metrics(task_rows)
            for (row_arm, _), task_rows in sorted(by_arm_task.items())
            if row_arm == arm
        ]
        families: dict[str, list[dict]] = defaultdict(list)
        for task in tasks:
            families[task["family"]].append(task)
        valid_times = [
            task["valid_result_seconds"]
            for task in tasks
            if math.isfinite(task["valid_result_seconds"])
        ]
        completion_rate = _mean(
            [task["completion_rate"] for task in tasks]
        )
        median_valid_result_seconds = (
            statistics.median(valid_times) if valid_times else math.inf
        )
        p90_valid_result_seconds = _percentile(valid_times, 0.90)
        result[arm] = {
            "arm": arm,
            "task_count": len(tasks),
            "run_count": len(arm_rows),
            "completion_rate": completion_rate,
            "per_family_completion_rate": {
                family: _mean([task["completion_rate"] for task in group])
                for family, group in sorted(families.items())
            },
            "mean_lca_score": _mean(
                [task["mean_lca_score"] for task in tasks]
            ),
            "median_valid_result_seconds": median_valid_result_seconds,
            "p90_valid_result_seconds": p90_valid_result_seconds,
            "expected_median_seconds_per_correct_result": (
                median_valid_result_seconds / completion_rate
                if completion_rate > 0
                else math.inf
            ),
            "expected_p90_seconds_per_correct_result": (
                p90_valid_result_seconds / completion_rate
                if completion_rate > 0
                else math.inf
            ),
            "unnecessary_tool_ratio": _mean(
                [task["unnecessary_tool_ratio"] for task in tasks]
            ),
            "recovery_success_rate": _mean(
                [task["recovery_success_rate"] for task in tasks]
            ),
            "duplicate_effect_count": sum(
                task["duplicate_effect_count"] for task in tasks
            ),
            "veto_failures": sorted(
                {
                    failure
                    for task in tasks
                    for failure in task["veto_failures"]
                }
            ),
            "scientific_exactness": min(
                task["scientific_exactness"] for task in tasks
            ),
            "unit_exactness": min(task["unit_exactness"] for task in tasks),
            "grader_disagreement_points": max(
                task["grader_disagreement_points"] for task in tasks
            ),
            "clustered_by_task": True,
            "task_metrics": tasks,
        }
    aggregate = {
        "contract": "lca_research_evaluation.clustered_arm_metrics/v1",
        "arms": result,
    }
    aggregate["metrics_sha256"] = canonical_sha256(aggregate)
    return aggregate


def _best_baseline(arms: Mapping[str, Mapping[str, object]]) -> Mapping[str, object]:
    baselines = [
        arms[arm]
        for arm in ("raw_model_tool_loop", "openai_agents_reference")
        if arm in arms
    ]
    if len(baselines) != 2:
        raise ValueError("both general baseline arms are required")
    return max(
        baselines,
        key=lambda row: (
            float(row["completion_rate"]),
            float(row["mean_lca_score"]),
            -float(row["median_valid_result_seconds"]),
        ),
    )


def compare_d18_to_best_baseline(metrics: Mapping[str, object]) -> dict:
    arms = metrics.get("arms")
    if not isinstance(arms, Mapping) or "d18_product_agent" not in arms:
        raise ValueError("D-18 and baseline arm metrics are required")
    d18 = arms["d18_product_agent"]
    if not isinstance(d18, Mapping):
        raise ValueError("D-18 metrics are malformed")
    best = _best_baseline(arms)  # type: ignore[arg-type]
    failures = []
    completion = float(d18["completion_rate"])
    best_completion = float(best["completion_rate"])
    if completion < EXACT_THRESHOLDS["d18_final_completion_rate"]:
        failures.append("d18_final_completion_rate")
    if completion - best_completion < (
        EXACT_THRESHOLDS["completion_noninferiority_pp"] / 100.0
    ):
        failures.append("completion_noninferiority_pp")
    per_family = d18["per_family_completion_rate"]
    if not isinstance(per_family, Mapping) or any(
        float(rate) < EXACT_THRESHOLDS["per_family_completion_rate"]
        for rate in per_family.values()
    ):
        failures.append("per_family_completion_rate")
    score = float(d18["mean_lca_score"])
    if score < EXACT_THRESHOLDS["mean_lca_score"]:
        failures.append("mean_lca_score")
    if score < float(best["mean_lca_score"]):
        failures.append("lca_score_not_below_best_baseline")
    accuracy_differs = not math.isclose(
        completion, best_completion, rel_tol=0.0, abs_tol=1e-12
    )
    latency_comparison_basis = (
        "expected_time_to_correct_result"
        if accuracy_differs
        else "valid_result_latency"
    )
    median_field = (
        "expected_median_seconds_per_correct_result"
        if accuracy_differs
        else "median_valid_result_seconds"
    )
    p90_field = (
        "expected_p90_seconds_per_correct_result"
        if accuracy_differs
        else "p90_valid_result_seconds"
    )
    median_ratio = float(d18[median_field]) / float(best[median_field])
    p90_ratio = float(d18[p90_field]) / float(best[p90_field])
    if median_ratio > EXACT_THRESHOLDS["median_latency_ratio_max"]:
        failures.append("median_latency_ratio_max")
    if p90_ratio > EXACT_THRESHOLDS["p90_latency_ratio_max"]:
        failures.append("p90_latency_ratio_max")
    if (
        float(d18["unnecessary_tool_ratio"])
        > EXACT_THRESHOLDS["unnecessary_tool_ratio_max"]
    ):
        failures.append("unnecessary_tool_ratio_max")
    if (
        float(d18["recovery_success_rate"])
        != EXACT_THRESHOLDS["recovery_success_rate"]
    ):
        failures.append("recovery_success_rate")
    if int(d18["duplicate_effect_count"]) != EXACT_THRESHOLDS[
        "duplicate_effect_count"
    ]:
        failures.append("duplicate_effect_count")
    if d18["veto_failures"]:
        failures.append("hard_veto")
    if float(d18["scientific_exactness"]) != 1.0:
        failures.append("tiangong_mechanical_correctness")
    if float(d18["unit_exactness"]) != 1.0:
        failures.append("deterministic_numerical_correctness")
    if float(d18["grader_disagreement_points"]) > EXACT_THRESHOLDS[
        "grader_disagreement_points_max"
    ]:
        failures.append("grader_disagreement_points_max")
    decision = {
        "contract": "lca_research_evaluation.performance_decision/v1",
        "status": "passed" if not failures else "failed",
        "d18_arm": "d18_product_agent",
        "best_baseline_arm": best["arm"],
        "failed_thresholds": sorted(set(failures)),
        "completion_difference_pp": (completion - best_completion) * 100,
        "latency_comparison_basis": latency_comparison_basis,
        "median_latency_ratio": median_ratio,
        "p90_latency_ratio": p90_ratio,
        "machine_reproducible_not_human_expert_blind_reviewed": True,
    }
    decision["decision_sha256"] = canonical_sha256(decision)
    return decision


def detect_no_quality_gain_loop(
    *,
    baseline_tokens: int,
    baseline_score: float,
    candidate_tokens: int,
    candidate_score: float,
) -> dict:
    detected = (
        baseline_tokens > 0
        and candidate_tokens > 4 * baseline_tokens
        and candidate_score <= baseline_score
    )
    return {
        "contract": "lca_research_evaluation.no_quality_gain_diagnosis/v1",
        "detected": detected,
        "failure_class": "loop_failure" if detected else None,
        "token_ratio": (
            candidate_tokens / baseline_tokens if baseline_tokens else math.inf
        ),
        "quality_gain": candidate_score - baseline_score,
    }


__all__ = [
    "aggregate_arm_metrics",
    "compare_d18_to_best_baseline",
    "detect_no_quality_gain_loop",
]
