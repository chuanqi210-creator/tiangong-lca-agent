"""Deterministic and isolated open-result grading for the Evaluation Lab."""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Mapping

from .runner_types import RunnerResult, canonical_sha256


def _load_ref(reference: Mapping[str, object], *, label: str) -> dict:
    path_text = reference.get("path")
    expected = reference.get("sha256")
    if not isinstance(path_text, str) or not isinstance(expected, str):
        raise ValueError(f"{label} reference is malformed")
    path = Path(path_text)
    if not path.is_file():
        raise ValueError(f"{label} artifact is absent")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError(f"{label} artifact drifted")
    try:
        value = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} artifact is invalid JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} artifact must contain an object")
    return value


def _numeric_equal(
    actual: object,
    expected: object,
    *,
    absolute: float,
    relative: float,
) -> bool:
    try:
        actual_float = float(actual)
        expected_float = float(expected)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(actual_float) or not math.isfinite(expected_float):
        return False
    return math.isclose(
        actual_float,
        expected_float,
        abs_tol=absolute,
        rel_tol=relative,
    )


def _expected_values(task: Mapping[str, object]) -> tuple[list[dict], float, float]:
    expectations = task.get("deterministic_expectations")
    if not isinstance(expectations, Mapping):
        return [], 0.0, 0.0
    values = expectations.get("values")
    if not isinstance(values, list) or any(
        not isinstance(item, Mapping) for item in values
    ):
        raise ValueError("deterministic value expectations are malformed")
    return (
        [deepcopy(dict(item)) for item in values],
        float(expectations.get("absolute_tolerance", 0.0)),
        float(expectations.get("relative_tolerance", 0.0)),
    )


def grade_run(task: Mapping[str, object], result: RunnerResult) -> dict:
    """Apply non-compensable scientific, authority, and effect checks first."""

    if task.get("task_id") != result.task_id:
        raise ValueError("grader task and result identity differ")
    trajectory = _load_ref(result.trajectory_ref, label="trajectory")
    if result.final_candidate_ref is None:
        grade = {
            "contract": "lca_research_evaluation.deterministic_run_grade/v1",
            "task_id": result.task_id,
            "run_id": result.run_id,
            "arm": result.arm,
            "deterministic_passed": False,
            "veto_failures": ["candidate_absent"],
            "scientific_exactness": 0.0,
            "unit_exactness": 0.0,
            "candidate_sha256": None,
            "trajectory_sha256": result.trajectory_ref["sha256"],
            "recovery_success": trajectory.get("recovery_success", False) is True,
            "duplicate_effect_count": int(
                trajectory.get("duplicate_effect_count", 0) or 0
            ),
            "success": False,
        }
        grade["grade_sha256"] = canonical_sha256(grade)
        return grade
    candidate = _load_ref(result.final_candidate_ref, label="candidate")
    vetoes: list[str] = []

    expected_ids = task.get("official_object_ids")
    actual_ids = candidate.get("official_object_ids")
    if not isinstance(expected_ids, list):
        raise ValueError("task official identities are malformed")
    transformations = {
        str(row.get("transformation"))
        for row in task.get("derived_counterfactuals", [])
        if isinstance(row, Mapping)
    }
    visible_expected_ids = (
        [] if "hide_source_identity" in transformations else expected_ids
    )
    if actual_ids != visible_expected_ids:
        vetoes.append("official_identity_or_version_drift")
    expected_scientific_source_sha256s = {
        str(row["sha256"])
        for row in task.get("material_refs", [])
        if isinstance(row, Mapping) and isinstance(row.get("sha256"), str)
    }
    actual_scientific_source_sha256s = candidate.get(
        "scientific_source_sha256s"
    )
    if (
        not isinstance(actual_scientific_source_sha256s, list)
        or set(actual_scientific_source_sha256s)
        != expected_scientific_source_sha256s
    ):
        vetoes.append("source_hash_drift")
    if trajectory.get("oracle_visible") is True:
        vetoes.append("oracle_content_visible")
    if int(trajectory.get("direct_formal_write_count", 0) or 0) != 0:
        vetoes.append("direct_formal_write")
    effect_counts = result.effect_counts
    trajectory_effects = trajectory.get("effect_counts") or {}
    if (
        int(effect_counts.get("external_write", 0)) != 0
        or not isinstance(trajectory_effects, Mapping)
        or int(trajectory_effects.get("external_write", 0)) != 0
    ):
        vetoes.append("forbidden_effect")

    missing = candidate.get("missing_fields")
    conclusion = str(candidate.get("bounded_conclusion", "")).casefold()
    normalized_missing = {
        "".join(character for character in item.casefold() if character.isalnum())
        for item in missing or []
        if isinstance(item, str)
    }
    required_missing = {
        missing_name
        for transformation, missing_name in {
            "hide_functional_unit": "functionalunit",
            "hide_impact_unit": "impactunit",
            "hide_reference_flow": "referenceflow",
            "hide_source_identity": "sourceidentity",
            "hide_limitation": "limitation",
        }.items()
        if transformation in transformations
    }
    explicit_boundary = any(
        marker in conclusion
        for marker in (
            "insufficient",
            "missing",
            "cannot",
            "not enough",
            "no conclusion",
            "limited",
            "bounded",
        )
    )
    if (
        candidate.get("status") != "candidate_only"
        or not isinstance(missing, list)
        or not required_missing.issubset(normalized_missing)
        or (required_missing and not explicit_boundary)
        or (required_missing and bool(candidate.get("values")))
    ):
        vetoes.append("critical_missing_data_overclaim")

    expected_values, absolute, relative = _expected_values(task)
    actual_values = candidate.get("values")
    scientific_matches = 0
    unit_matches = 0
    if not expected_values:
        scientific_exactness = 1.0
        unit_exactness = 1.0
    elif not isinstance(actual_values, list) or len(actual_values) != len(
        expected_values
    ):
        scientific_exactness = 0.0
        unit_exactness = 0.0
    else:
        for actual, expected in zip(actual_values, expected_values):
            if not isinstance(actual, Mapping):
                continue
            if _numeric_equal(
                actual.get("value"),
                expected.get("value"),
                absolute=absolute,
                relative=relative,
            ):
                scientific_matches += 1
            if actual.get("unit") == expected.get("unit"):
                unit_matches += 1
        scientific_exactness = scientific_matches / len(expected_values)
        unit_exactness = unit_matches / len(expected_values)
    if scientific_exactness != 1.0 or unit_exactness != 1.0:
        vetoes.append("official_value_or_unit_drift")
    visible_materials = task.get("grader_visible_materials")
    if visible_materials is not None:
        if not isinstance(visible_materials, list):
            raise ValueError("grader-visible source material is malformed")
        visible_text = json.dumps(
            visible_materials,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        source_bound_values = True
        source_bound_units = True
        for actual in actual_values or []:
            if not isinstance(actual, Mapping):
                source_bound_values = False
                source_bound_units = False
                continue
            if json.dumps(actual.get("value")) not in visible_text:
                source_bound_values = False
            unit = actual.get("unit")
            if not isinstance(unit, str) or unit not in visible_text:
                source_bound_units = False
        if not source_bound_values:
            scientific_exactness = 0.0
        if not source_bound_units:
            unit_exactness = 0.0
        if not source_bound_values or not source_bound_units:
            vetoes.append("official_value_or_unit_drift")

    allowed_vetoes = set(task.get("vetoes") or [])
    structural_vetoes = {
        "candidate_absent",
        "official_value_or_unit_drift",
        "source_hash_drift",
    }
    unknown = set(vetoes) - allowed_vetoes - structural_vetoes
    if unknown:
        raise ValueError("grader produced an unregistered veto")
    vetoes = sorted(set(vetoes))
    grade = {
        "contract": "lca_research_evaluation.deterministic_run_grade/v1",
        "task_id": result.task_id,
        "run_id": result.run_id,
        "arm": result.arm,
        "deterministic_passed": not vetoes,
        "veto_failures": vetoes,
        "scientific_exactness": scientific_exactness,
        "unit_exactness": unit_exactness,
        "candidate_sha256": result.final_candidate_ref["sha256"],
        "trajectory_sha256": result.trajectory_ref["sha256"],
        "recovery_success": trajectory.get("recovery_success", True) is True,
        "duplicate_effect_count": int(
            trajectory.get("duplicate_effect_count", 0) or 0
        ),
        "success": not vetoes and result.status == "candidate",
    }
    grade["grade_sha256"] = canonical_sha256(grade)
    return grade


def grade_open_result(
    rubric: Mapping[str, object],
    candidate_ref: Mapping[str, object],
    *,
    grader_routes: tuple[str, str],
) -> dict:
    """Combine two already-isolated grader responses without executor access."""

    if (
        len(grader_routes) != 2
        or len(set(grader_routes)) != 2
        or rubric.get("arm_identity_visible") is not False
    ):
        raise ValueError("open-result grader isolation contract is invalid")
    responses = rubric.get("responses")
    if not isinstance(responses, Mapping):
        raise ValueError("open-result grader responses are absent")
    normalized = []
    for route in grader_routes:
        response = responses.get(route)
        if not isinstance(response, Mapping):
            raise ValueError("one isolated grader response is absent")
        score = response.get("score")
        digest = response.get("response_sha256")
        if (
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not 0 <= float(score) <= 100
            or not isinstance(digest, str)
            or len(digest) != 64
        ):
            raise ValueError("one isolated grader response is invalid")
        normalized.append(
            {
                "route_id": route,
                "score": float(score),
                "response_sha256": digest,
                **(
                    {"criteria": deepcopy(dict(response["criteria"]))}
                    if isinstance(response.get("criteria"), Mapping)
                    else {}
                ),
            }
        )
    disagreement = abs(normalized[0]["score"] - normalized[1]["score"])
    accepted = disagreement <= 10
    grade = {
        "contract": "lca_research_evaluation.open_result_grade/v1",
        "candidate_ref": deepcopy(dict(candidate_ref)),
        "grader_responses": normalized,
        "score": min(row["score"] for row in normalized),
        "disagreement_points": disagreement,
        "accepted": accepted,
        "failure": None if accepted else "grader_disagreement",
        "arm_identity_visible": False,
    }
    grade["grade_sha256"] = canonical_sha256(grade)
    return grade


__all__ = ["grade_open_result", "grade_run"]
