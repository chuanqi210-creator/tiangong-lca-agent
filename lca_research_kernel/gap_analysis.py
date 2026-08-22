"""Rank source-bound scientific gaps without fabricating uncertainty evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Iterable, List


JsonObject = Dict[str, object]

SCIENTIFIC_GAP_CONTRACT = "lca_research_kernel.scientific_gap_impact/v1"
SCIENTIFIC_WEIGHTS = {
    "conclusion_impact": 45,
    "model_reach": 30,
    "invalidation_value": 25,
}
ACTIONABILITY_FACTORS = {
    "ready": 1.0,
    "partial": 0.65,
    "blocked": 0.25,
}


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _required_score(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
        raise ValueError(f"{label} must be an integer from 1 to 5")
    return value


def _required_text_list(value: object, label: str, *, allow_empty: bool = False) -> List[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"{label} must be a {'possibly empty ' if allow_empty else 'non-empty '}list")
    rows = [_required_text(row, f"{label} item") for row in value]
    if len(rows) != len(set(rows)):
        raise ValueError(f"{label} must not contain duplicates")
    return rows


def _score_candidate(candidate: JsonObject) -> JsonObject:
    dimensions = candidate.get("dimensions")
    if not isinstance(dimensions, dict):
        raise ValueError("candidate dimensions must be an object")
    scored_dimensions = {
        key: _required_score(dimensions.get(key), f"dimensions.{key}")
        for key in SCIENTIFIC_WEIGHTS
    }
    rationale = candidate.get("dimension_rationale")
    if not isinstance(rationale, dict):
        raise ValueError("candidate dimension_rationale must be an object")
    dimension_rationale = {
        key: _required_text(rationale.get(key), f"dimension_rationale.{key}")
        for key in SCIENTIFIC_WEIGHTS
    }
    availability = _required_score(
        candidate.get("evidence_availability"), "candidate evidence_availability"
    )
    actionability = _required_text(candidate.get("actionability"), "candidate actionability")
    if actionability not in ACTIONABILITY_FACTORS:
        raise ValueError(
            f"candidate actionability must be one of {sorted(ACTIONABILITY_FACTORS)}"
        )
    blockers = _required_text_list(
        candidate.get("blockers"), "candidate blockers", allow_empty=True
    )
    if actionability == "ready" and blockers:
        raise ValueError("ready candidates cannot retain blockers")
    if actionability != "ready" and not blockers:
        raise ValueError("non-ready candidates must name at least one blocker")

    scientific_score = round(
        sum(scored_dimensions[key] * weight for key, weight in SCIENTIFIC_WEIGHTS.items())
        / 5,
        2,
    )
    execution_score = round(
        0.8 * scientific_score * ACTIONABILITY_FACTORS[actionability]
        + 0.2 * availability * 20,
        2,
    )
    return {
        "gap_id": _required_text(candidate.get("gap_id"), "candidate gap_id"),
        "question": _required_text(candidate.get("question"), "candidate question"),
        "gap_type": _required_text(candidate.get("gap_type"), "candidate gap_type"),
        "linked_risk_ids": _required_text_list(
            candidate.get("linked_risk_ids"), "candidate linked_risk_ids"
        ),
        "evidence_refs": _required_text_list(
            candidate.get("evidence_refs"), "candidate evidence_refs"
        ),
        "external_pattern_refs": _required_text_list(
            candidate.get("external_pattern_refs"),
            "candidate external_pattern_refs",
            allow_empty=True,
        ),
        "dimensions": scored_dimensions,
        "dimension_rationale": dimension_rationale,
        "evidence_availability": availability,
        "evidence_availability_rationale": _required_text(
            candidate.get("evidence_availability_rationale"),
            "candidate evidence_availability_rationale",
        ),
        "actionability": actionability,
        "actionability_factor": ACTIONABILITY_FACTORS[actionability],
        "blockers": blockers,
        "next_operation": _required_text(
            candidate.get("next_operation"), "candidate next_operation"
        ),
        "scientific_impact_score": scientific_score,
        "execution_priority_score": execution_score,
    }


def compile_scientific_gap_register(
    assessment: JsonObject, bindings: JsonObject
) -> JsonObject:
    """Validate an assessment and choose one executable next scientific slice."""

    if assessment.get("schema_version") != 1:
        raise ValueError("assessment schema_version must be 1")
    if bindings.get("schema_version") != 1:
        raise ValueError("bindings schema_version must be 1")
    scope_id = _required_text(assessment.get("scope_id"), "assessment scope_id")
    revision = _required_text(assessment.get("revision"), "assessment revision")
    if bindings.get("scope_id") != scope_id or bindings.get("revision") != revision:
        raise ValueError("assessment and source bindings target different scope revisions")

    evidence_bindings = bindings.get("evidence_bindings")
    if not isinstance(evidence_bindings, list) or not evidence_bindings:
        raise ValueError("source bindings must contain evidence_bindings")
    evidence_ids = {
        _required_text(row.get("binding_id"), "evidence binding_id")
        for row in evidence_bindings
        if isinstance(row, dict)
    }
    if len(evidence_ids) != len(evidence_bindings):
        raise ValueError("evidence bindings must be unique objects")

    risks = bindings.get("unresolved_risks")
    if not isinstance(risks, list) or not risks:
        raise ValueError("source bindings must contain unresolved_risks")
    risk_ids = {
        _required_text(row.get("risk_id"), "risk_id")
        for row in risks
        if isinstance(row, dict)
    }
    if len(risk_ids) != len(risks):
        raise ValueError("unresolved risks must be unique objects")

    patterns = assessment.get("external_patterns")
    if not isinstance(patterns, list) or not patterns:
        raise ValueError("assessment must contain external_patterns")
    pattern_ids = set()
    normalized_patterns: List[JsonObject] = []
    for pattern in patterns:
        if not isinstance(pattern, dict):
            raise ValueError("external patterns must be objects")
        pattern_id = _required_text(pattern.get("pattern_id"), "external pattern_id")
        if pattern_id in pattern_ids:
            raise ValueError(f"duplicate external pattern_id: {pattern_id}")
        pattern_ids.add(pattern_id)
        stars = pattern.get("stars_observed")
        if isinstance(stars, bool) or not isinstance(stars, int) or stars < 0:
            raise ValueError("external stars_observed must be a non-negative integer")
        normalized_patterns.append(
            {
                "pattern_id": pattern_id,
                "repository": _required_text(pattern.get("repository"), "repository"),
                "repository_url": _required_text(
                    pattern.get("repository_url"), "repository_url"
                ),
                "metadata_source_url": _required_text(
                    pattern.get("metadata_source_url"), "metadata_source_url"
                ),
                "observed_at": _required_text(pattern.get("observed_at"), "observed_at"),
                "stars_observed": stars,
                "engineering_mechanism": _required_text(
                    pattern.get("engineering_mechanism"), "engineering_mechanism"
                ),
                "local_landing": _required_text(
                    pattern.get("local_landing"), "local_landing"
                ),
                "migration_status": _required_text(
                    pattern.get("migration_status"), "migration_status"
                ),
                "non_migration_reason": _required_text(
                    pattern.get("non_migration_reason"), "non_migration_reason"
                ),
            }
        )

    candidates = assessment.get("candidates")
    if not isinstance(candidates, list) or len(candidates) < 2:
        raise ValueError("assessment must contain at least two candidate gaps")
    scored = [_score_candidate(row) for row in candidates if isinstance(row, dict)]
    if len(scored) != len(candidates):
        raise ValueError("candidate gaps must be objects")
    gap_ids = [str(row["gap_id"]) for row in scored]
    if len(gap_ids) != len(set(gap_ids)):
        raise ValueError("candidate gap_ids must be unique")
    for row in scored:
        unknown_risks = sorted(set(row["linked_risk_ids"]) - risk_ids)
        if unknown_risks:
            raise ValueError(f"candidate {row['gap_id']} references unknown risks: {unknown_risks}")
        unknown_evidence = sorted(set(row["evidence_refs"]) - evidence_ids)
        if unknown_evidence:
            raise ValueError(
                f"candidate {row['gap_id']} references unknown evidence: {unknown_evidence}"
            )
        unknown_patterns = sorted(set(row["external_pattern_refs"]) - pattern_ids)
        if unknown_patterns:
            raise ValueError(
                f"candidate {row['gap_id']} references unknown patterns: {unknown_patterns}"
            )

    execution_ranking = sorted(
        scored,
        key=lambda row: (
            -float(row["execution_priority_score"]),
            -float(row["scientific_impact_score"]),
            str(row["gap_id"]),
        ),
    )
    selected = execution_ranking[0]
    if selected["actionability"] != "ready":
        raise ValueError("highest execution-priority gap is not executable")
    scientific_ranking = sorted(
        scored,
        key=lambda row: (-float(row["scientific_impact_score"]), str(row["gap_id"])),
    )
    blocked_high_impact = [
        {
            "gap_id": row["gap_id"],
            "scientific_impact_score": row["scientific_impact_score"],
            "execution_priority_score": row["execution_priority_score"],
            "blockers": row["blockers"],
        }
        for row in scientific_ranking
        if row["gap_id"] != selected["gap_id"]
        and float(row["scientific_impact_score"]) >= 85
        and row["actionability"] != "ready"
    ]
    return {
        "schema_version": 1,
        "contract": SCIENTIFIC_GAP_CONTRACT,
        "scope_id": scope_id,
        "revision": revision,
        "status": "ranked_one_executable_slice_selected",
        "scoring_policy": {
            "purpose": "deterministic routing only; not a causal, statistical, or LCIA result",
            "scientific_weights_percent": SCIENTIFIC_WEIGHTS,
            "actionability_factors": ACTIONABILITY_FACTORS,
            "execution_formula": (
                "0.8 * scientific_impact_score * actionability_factor + "
                "0.2 * evidence_availability * 20"
            ),
            "selection_rule": (
                "highest execution_priority_score; ties use scientific impact then gap_id"
            ),
        },
        "source_bindings": bindings,
        "external_patterns": normalized_patterns,
        "execution_ranking": execution_ranking,
        "scientific_ranking": scientific_ranking,
        "selected_gap": selected,
        "blocked_high_impact": blocked_high_impact,
        "sensitivity_analysis_ready": False,
        "uncertainty_model_ready": False,
        "scientific_comparison_ready": False,
        "next_action": selected["next_operation"],
    }


def render_decision_map(register: JsonObject) -> str:
    """Render the compact human-facing decision map from the canonical register."""

    selected = register["selected_gap"]
    lines = [
        "# Scientific Gap Decision Map",
        "",
        f"Scope: `{register['scope_id']}:{register['revision']}`",
        "",
        "This map routes the next research beat. Scores are ordinal controller inputs, "
        "not causal estimates, uncertainty distributions, or LCIA results.",
        "",
        "## Selected ticket",
        "",
        f"- Gap: `{selected['gap_id']}` — {selected['question']}",
        f"- Scientific impact: {selected['scientific_impact_score']}/100",
        f"- Execution priority: {selected['execution_priority_score']}/100",
        f"- Route: {selected['next_operation']}",
        "",
        "## Queue",
        "",
        "| Rank | Gap | Scientific impact | Execution priority | State |",
        "| ---: | --- | ---: | ---: | --- |",
    ]
    for index, row in enumerate(register["execution_ranking"], start=1):
        lines.append(
            f"| {index} | `{row['gap_id']}` | {row['scientific_impact_score']} | "
            f"{row['execution_priority_score']} | {row['actionability']} |"
        )
    lines.extend(
        [
            "",
            "## Decision tickets",
            "",
        ]
    )
    for row in register["execution_ranking"]:
        blockers = "; ".join(row["blockers"]) if row["blockers"] else "none"
        lines.extend(
            [
                f"### `{row['gap_id']}`",
                "",
                f"- Question: {row['question']}",
                f"- Evidence: {', '.join(row['evidence_refs'])}",
                f"- Blockers: {blockers}",
                f"- Next operation: {row['next_operation']}",
                "",
            ]
        )
    lines.extend(
        [
            "## External engineering calibration",
            "",
        ]
    )
    for pattern in register["external_patterns"]:
        lines.extend(
            [
                f"### {pattern['repository']} ({pattern['stars_observed']} stars observed)",
                "",
                f"- Mechanism: {pattern['engineering_mechanism']}",
                f"- Local landing: {pattern['local_landing']}",
                f"- Status: {pattern['migration_status']}",
                f"- Guardrail: {pattern['non_migration_reason']}",
                f"- Source: {pattern['repository_url']}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _load_object(path: Path) -> JsonObject:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _write_json(path: Path, value: JsonObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile a scientific-gap impact register.")
    parser.add_argument("--assessment", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--register-out", type=Path, required=True)
    parser.add_argument("--map-out", type=Path, required=True)
    args = parser.parse_args(argv)
    register = compile_scientific_gap_register(
        _load_object(args.assessment), _load_object(args.bindings)
    )
    _write_json(args.register_out, register)
    args.map_out.parent.mkdir(parents=True, exist_ok=True)
    args.map_out.write_text(render_decision_map(register), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
