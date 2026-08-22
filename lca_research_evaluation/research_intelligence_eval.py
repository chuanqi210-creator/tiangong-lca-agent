"""Outcome evaluation for source-bound multi-turn research conversations."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Mapping

from lca_research_kernel.answer_quality import (
    evaluate_answer_quality,
    validate_answer_quality_basis,
)
from lca_research_kernel.project_record import canonical_sha256
from lca_research_kernel.run_trace import RunTrace


FIXTURE_CONTRACT = "lca_research_evaluation.research_dialogue_fixture/v1"
RUN_CONTRACT = "lca_research_evaluation.research_dialogue_run/v1"
GRADE_CONTRACT = "lca_research_evaluation.research_dialogue_grade/v2"
ACCEPTANCE_CONTRACT = (
    "lca_research_evaluation.research_intelligence_acceptance/v2"
)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CJK_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
INTERNAL_IDENTIFIER = re.compile(
    r"(?<![\w])(?:claim|locator|revision|material|artifact|run|task)-"
    r"[A-Za-z0-9](?:[A-Za-z0-9._:-]*[A-Za-z0-9])?(?![\w])"
)


def _validate_fixture(value: Mapping[str, object]) -> dict:
    if (
        not isinstance(value, Mapping)
        or value.get("contract") != FIXTURE_CONTRACT
        or not isinstance(value.get("fixture_id"), str)
        or not value["fixture_id"]
        or not isinstance(value.get("project"), Mapping)
        or not isinstance(value.get("turns"), list)
        or not value["turns"]
        or not isinstance(value.get("prohibited"), list)
    ):
        raise ValueError("research dialogue fixture is invalid")
    project = value["project"]
    if set(project) != {"research_id", "title", "source_ref"}:
        raise ValueError("research dialogue project fixture is invalid")
    source_ref = project.get("source_ref")
    if (
        not isinstance(source_ref, Mapping)
        or set(source_ref) != {"path", "sha256"}
        or not isinstance(source_ref.get("path"), str)
        or not isinstance(source_ref.get("sha256"), str)
        or len(source_ref["sha256"]) != 64
    ):
        raise ValueError("research dialogue source reference is invalid")
    seen: set[str] = set()
    for turn in value["turns"]:
        if (
            not isinstance(turn, Mapping)
            or set(turn) != {"turn_id", "text", "operation", "obligations"}
            or not isinstance(turn.get("turn_id"), str)
            or not turn["turn_id"]
            or turn["turn_id"] in seen
            or not isinstance(turn.get("text"), str)
            or not turn["text"].strip()
            or not isinstance(turn.get("operation"), str)
            or not isinstance(turn.get("obligations"), list)
            or not turn["obligations"]
            or any(
                not isinstance(obligation, str) or not obligation
                for obligation in turn["obligations"]
            )
        ):
            raise ValueError("research dialogue turn fixture is invalid")
        seen.add(turn["turn_id"])
    return deepcopy(dict(value))


def run_research_dialogue(
    product_interface: object,
    fixture: Mapping[str, object],
    material_bundle: Mapping[str, object],
) -> dict:
    """Run one frozen dialogue through only the public Product Interface."""

    frozen = _validate_fixture(fixture)
    project = frozen["project"]
    turns = frozen["turns"]
    observations = []
    try:
        first = turns[0]
        product_interface.start_research_with_materials(
            {
                "contract": "lca_research_kernel.research_start/v1",
                "research_id": project["research_id"],
                "title": project["title"],
                "objective": first["text"],
                "source_refs": [deepcopy(project["source_ref"])],
                "received_at": "2026-08-15T08:00:00Z",
            },
            [deepcopy(material_bundle)],
        )
        observations.append(
            {
                "turn": deepcopy(first),
                "result_view": product_interface.read_result(
                    project["research_id"]
                ),
            }
        )
        for index, turn in enumerate(turns[1:], start=2):
            product_interface.continue_research(
                project["research_id"],
                {
                    "contract": "lca_research_kernel.research_user_turn/v1",
                    "input_id": f"{frozen['fixture_id']}-{turn['turn_id']}",
                    "text": turn["text"],
                    "source_refs": [deepcopy(project["source_ref"])],
                    "received_at": f"2026-08-15T08:0{index}:00Z",
                },
                [deepcopy(material_bundle)],
            )
            observations.append(
                {
                    "turn": deepcopy(turn),
                    "result_view": product_interface.read_result(
                        project["research_id"]
                    ),
                }
            )
    except Exception as error:  # trial failures are not grader failures
        return {
            "contract": RUN_CONTRACT,
            "fixture_id": frozen["fixture_id"],
            "fixture_sha256": canonical_sha256(frozen),
            "trial_status": "failed",
            "trial_error": type(error).__name__,
            "turns": observations,
        }
    evidence = _execution_evidence(product_interface, project["research_id"])
    return {
        "contract": RUN_CONTRACT,
        "fixture_id": frozen["fixture_id"],
        "fixture_sha256": canonical_sha256(frozen),
        "trial_status": "completed",
        "trial_error": None,
        "turns": observations,
        **evidence,
    }


def _accepted_dialogue_observations(
    project_record: object,
    fixture: Mapping[str, object],
) -> list[dict]:
    """Recover sequential accepted results from the authoritative event chain."""

    read_audit_trail = getattr(project_record, "read_audit_trail", None)
    read_current_project_view = getattr(
        project_record, "read_current_project_view", None
    )
    resolve_accepted_result = getattr(
        project_record, "resolve_accepted_result", None
    )
    if not all(
        callable(port)
        for port in (
            read_audit_trail,
            read_current_project_view,
            resolve_accepted_result,
        )
    ):
        raise ValueError("Project Record dialogue recovery is unavailable")
    audit = read_audit_trail()
    view = read_current_project_view()
    project = fixture["project"]
    turns = fixture["turns"]
    if (
        not isinstance(audit, list)
        or not isinstance(view, Mapping)
        or view.get("project_id") != project["research_id"]
    ):
        raise ValueError("Project Record dialogue recovery identity drifted")

    active_turn: Mapping[str, object] | None = None
    observations: list[dict] = []
    prior_result_sha256: str | None = None
    for event in audit:
        payload = event.get("payload") if isinstance(event, Mapping) else None
        candidate = (
            payload.get("candidate") if isinstance(payload, Mapping) else None
        )
        if not isinstance(candidate, Mapping):
            continue
        if candidate.get("change_type") == "user_turn.activated":
            fact = candidate.get("fact")
            value = fact.get("value") if isinstance(fact, Mapping) else None
            if (
                not isinstance(value, Mapping)
                or fact.get("domain_kind") != "user_turn"
                or not isinstance(value.get("text"), str)
                or not isinstance(value.get("turn_sha256"), str)
            ):
                raise ValueError("accepted dialogue turn is invalid")
            active_turn = value
            continue
        if candidate.get("change_type") != "result.accepted":
            continue
        result = candidate.get("result")
        if (
            not isinstance(active_turn, Mapping)
            or not isinstance(result, Mapping)
            or len(observations) >= len(turns)
        ):
            raise ValueError("accepted dialogue result sequence is invalid")
        expected_turn = turns[len(observations)]
        result_sha256 = canonical_sha256(result)
        if (
            active_turn.get("text") != expected_turn["text"]
            or active_turn.get("prior_result_sha256") != prior_result_sha256
            or result.get("interaction_turn_sha256")
            != active_turn.get("turn_sha256")
            or resolve_accepted_result(result_sha256) != result
        ):
            raise ValueError("accepted dialogue result membership drifted")
        observations.append(
            {
                "turn": deepcopy(expected_turn),
                "result_view": {"result": deepcopy(dict(result))},
            }
        )
        prior_result_sha256 = result_sha256
    return observations


def resume_research_dialogue(
    product_interface: object,
    fixture: Mapping[str, object],
    material_bundle: Mapping[str, object],
) -> dict:
    """Resume one interrupted frozen dialogue from accepted Project Record results."""

    frozen = _validate_fixture(fixture)
    project = frozen["project"]
    turns = frozen["turns"]
    open_record = getattr(product_interface, "_open_record", None)
    if not callable(open_record):
        raise ValueError("Product Interface Project Record port is unavailable")
    observations = _accepted_dialogue_observations(
        open_record(project["research_id"]), frozen
    )
    if not observations:
        raise ValueError("interrupted dialogue has no accepted recovery point")
    accepted_turns_before_resume = len(observations)
    try:
        for index, turn in enumerate(
            turns[len(observations) :], start=len(observations) + 1
        ):
            product_interface.continue_research(
                project["research_id"],
                {
                    "contract": "lca_research_kernel.research_user_turn/v1",
                    "input_id": f"{frozen['fixture_id']}-{turn['turn_id']}",
                    "text": turn["text"],
                    "source_refs": [deepcopy(project["source_ref"])],
                    "received_at": f"2026-08-15T08:0{index}:00Z",
                },
                [deepcopy(material_bundle)],
            )
            observations.append(
                {
                    "turn": deepcopy(turn),
                    "result_view": product_interface.read_result(
                        project["research_id"]
                    ),
                }
            )
    except Exception as error:  # trial failures are not grader failures
        return {
            "contract": RUN_CONTRACT,
            "fixture_id": frozen["fixture_id"],
            "fixture_sha256": canonical_sha256(frozen),
            "trial_status": "failed",
            "trial_error": type(error).__name__,
            "turns": observations,
            "recovery": {
                "mode": "project_record_resume",
                "accepted_turns_before_resume": accepted_turns_before_resume,
            },
        }
    evidence = _execution_evidence(product_interface, project["research_id"])
    return {
        "contract": RUN_CONTRACT,
        "fixture_id": frozen["fixture_id"],
        "fixture_sha256": canonical_sha256(frozen),
        "trial_status": "completed",
        "trial_error": None,
        "turns": observations,
        "recovery": {
            "mode": "project_record_resume",
            "accepted_turns_before_resume": accepted_turns_before_resume,
        },
        **evidence,
    }


def _execution_evidence(product_interface: object, research_id: str) -> dict:
    """Project hash-only evaluation evidence from the production truth seams."""

    open_record = getattr(product_interface, "_open_record", None)
    if not callable(open_record):
        raise ValueError("Product Interface Project Record port is unavailable")
    record = open_record(research_id)
    audit = record.read_audit_trail()
    view = record.read_current_project_view()
    accepted_by_result: dict[str, str] = {}
    for event in audit:
        payload = event.get("payload")
        candidate = payload.get("candidate") if isinstance(payload, Mapping) else None
        result = candidate.get("result") if isinstance(candidate, Mapping) else None
        if (
            isinstance(result, Mapping)
            and candidate.get("change_type") == "result.accepted"
            and isinstance(event.get("event_sha256"), str)
        ):
            accepted_by_result[canonical_sha256(result)] = event["event_sha256"]
    trace_events = []
    trace_refs = []
    trace_directory = record.workspace / "run-traces"
    for path in sorted(trace_directory.glob("*.jsonl")):
        first_line = next(
            (
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ),
            None,
        )
        if first_line is None:
            continue
        try:
            first = json.loads(first_line)
        except (OSError, ValueError) as error:
            raise ValueError("research dialogue Run Trace is invalid") from error
        run_id = first.get("run_id") if isinstance(first, Mapping) else None
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("research dialogue Run Trace identity is absent")
        events = RunTrace(path, run_id=run_id).events()
        if not events:
            continue
        trace_events.extend(events)
        trace_refs.append(
            {
                "run_id": run_id,
                "event_count": len(events),
                "last_event_sha256": events[-1]["event_sha256"],
                "trace_sha256": canonical_sha256(events),
            }
        )
    external_effect_count = sum(
        event.get("event_type") == "research.run.capability.completed"
        and isinstance(event.get("payload"), Mapping)
        and event["payload"].get("effect") == "external_write"
        for event in trace_events
    )
    tool_effect_count = sum(
        event.get("event_type") == "research.run.tool.completed"
        and isinstance(event.get("payload"), Mapping)
        and event["payload"].get("project_truth_effect") not in {None, "none"}
        for event in trace_events
    )
    return {
        "project_record_project_id": view["project_id"],
        "project_record_event_sha256s": [
            event["event_sha256"] for event in audit
        ],
        "project_record_replay_sha256": canonical_sha256(view),
        "accepted_result_event_sha256s": accepted_by_result,
        "run_trace_refs": trace_refs,
        "runtime_counts": {
            "model_completed": sum(
                event.get("event_type") == "research.run.model.completed"
                for event in trace_events
            ),
            "repair_requested": sum(
                event.get("event_type")
                == "research.run.material.answer_repair_requested"
                for event in trace_events
            ),
            "tool_completed": sum(
                event.get("event_type") == "research.run.tool.completed"
                for event in trace_events
            ),
            "tool_effect": tool_effect_count,
            "external_effect": external_effect_count,
        },
    }


def _grade_surface_integrity(
    turn: Mapping[str, object],
    result: Mapping[str, object],
) -> dict:
    """Independently grade visible language, identifiers, punctuation, citations."""

    text = turn.get("text")
    answer = result.get("answer")
    citation_ids = result.get("citation_ids")
    citations = result.get("citations")
    errors: list[str] = []
    if not isinstance(text, str) or not isinstance(answer, str) or not answer.strip():
        errors.append("answer_surface_absent")
        answer = ""
    response_language = "zh-CN" if CJK_CHARACTER.search(str(text)) else "en"
    visible_prose = re.sub(r"\[[^\[\]\s]+\]", "", answer)
    if INTERNAL_IDENTIFIER.search(visible_prose):
        errors.append("internal_identifier_exposed")
    if response_language == "zh-CN" and CJK_CHARACTER.search(answer) is None:
        errors.append("response_language_mismatch")
    if any(token in answer for token in ("..", ".。", "。.")):
        errors.append("mixed_terminal_punctuation")
    terminal = "。" if response_language == "zh-CN" else "."
    bracket_ids = re.findall(r"\[([^\[\]\s]+)\]", answer)
    citation_rows = (
        {
            row.get("locator_id"): row
            for row in citations
            if isinstance(row, Mapping)
            and isinstance(row.get("locator_id"), str)
        }
        if isinstance(citations, list)
        else {}
    )
    accepted_evidence_hashes = {}
    try:
        accepted_basis = validate_answer_quality_basis(
            result.get("answer_quality_basis")
        )
        accepted_evidence_hashes = {
            row["locator_id"]: row["content_hash"]
            for row in accepted_basis["selected_evidence_roles"]
        }
    except (KeyError, TypeError, ValueError):
        accepted_evidence_hashes = {}
    citations_valid = (
        isinstance(citation_ids, list)
        and bool(citation_ids)
        and isinstance(citations, list)
        and len(citation_ids) == len(set(citation_ids))
        and len(bracket_ids) == len(citation_ids)
        and set(bracket_ids) == set(citation_ids)
        and len(citations) == len(citation_ids)
        and len(citation_rows) == len(citation_ids)
        and set(citation_rows) == set(citation_ids)
        and set(accepted_evidence_hashes) == set(citation_ids)
        and all(
            isinstance(locator_id, str)
            and answer.count(f"[{locator_id}]") == 1
            and f"[{locator_id}]{terminal}" in answer
            and citation_rows[locator_id].get("citation_id") == locator_id
            and isinstance(citation_rows[locator_id].get("revision_id"), str)
            and bool(citation_rows[locator_id]["revision_id"])
            and isinstance(citation_rows[locator_id].get("content_hash"), str)
            and re.fullmatch(
                r"[0-9a-f]{64}", citation_rows[locator_id]["content_hash"]
            )
            is not None
            and citation_rows[locator_id]["content_hash"]
            == accepted_evidence_hashes[locator_id]
            for locator_id in citation_ids
        )
    )
    if not citations_valid:
        errors.append("citation_rendering_invalid")
    return {
        "contract": "lca_research_evaluation.answer_surface_integrity/v1",
        "status": "passed" if not errors else "failed",
        "response_language": response_language,
        "errors": sorted(set(errors)),
    }


def _grade_turn(value: Mapping[str, object]) -> dict:
    from lca_research_kernel.research_turn import infer_response_language

    turn = value.get("turn")
    result_view = value.get("result_view")
    if not isinstance(turn, Mapping) or not isinstance(result_view, Mapping):
        raise ValueError("research dialogue turn observation is invalid")
    expected = turn.get("obligations")
    result = result_view.get("result")
    if not isinstance(expected, list) or not isinstance(result, Mapping):
        raise ValueError("research dialogue accepted result is absent")
    claims = result.get("claims")
    turn_frame = result.get("turn_frame")
    citation_ids = result.get("citation_ids")
    quality_basis = result.get("answer_quality_basis")
    unsupported = [
        str(claim.get("claim_id") or "<invalid-claim>")
        for claim in claims
        if isinstance(claim, Mapping)
    ] if isinstance(claims, list) else ["<claims-absent>"]
    covered: list[str] = []
    recomputed_quality = None
    if (
        isinstance(claims, list)
        and isinstance(turn_frame, Mapping)
        and isinstance(citation_ids, list)
        and all(isinstance(locator_id, str) for locator_id in citation_ids)
    ):
        try:
            accepted_basis = validate_answer_quality_basis(quality_basis)
            response_language = accepted_basis["response_language"]
            if response_language != infer_response_language(turn.get("text")):
                raise ValueError("accepted answer language drifted from turn")
            selected_evidence_units = [
                {
                    "locator_id": row["locator_id"],
                    "content_hash": row["content_hash"],
                    "evidence_unit": {
                        "semantic_roles": deepcopy(row["semantic_roles"]),
                        "source_value_anchors": deepcopy(
                            row["source_value_anchors"]
                        ),
                        "source_identity_anchors": deepcopy(
                            row["source_identity_anchors"]
                        ),
                    },
                }
                for row in accepted_basis["selected_evidence_roles"]
                if isinstance(row, Mapping)
            ]
            governed = deepcopy(accepted_basis["governed_knowledge"])
            recomputed_quality = evaluate_answer_quality(
                claims,
                turn_frame=turn_frame,
                selected_locator_ids=citation_ids,
                selected_evidence_units=selected_evidence_units,
                governed_knowledge=governed,
                response_language=response_language,
            )
        except (OSError, TypeError, ValueError):
            recomputed_quality = None
    if isinstance(recomputed_quality, Mapping):
        covered = (
            list(recomputed_quality.get("covered_obligations") or [])
            if isinstance(recomputed_quality.get("covered_obligations"), list)
            else []
        )
        unsupported = (
            list(recomputed_quality.get("unsupported_claim_ids") or [])
            if isinstance(recomputed_quality.get("unsupported_claim_ids"), list)
            else ["<quality-invalid>"]
        )
    missing = [obligation for obligation in expected if obligation not in covered]
    surface_integrity = _grade_surface_integrity(turn, result)
    return {
        "turn_id": turn["turn_id"],
        "passed": (
            isinstance(recomputed_quality, Mapping)
            and recomputed_quality.get("status") == "passed"
            and not missing
            and not unsupported
            and surface_integrity["status"] == "passed"
        ),
        "expected_obligations": deepcopy(expected),
        "covered_obligations": deepcopy(covered),
        "missing_obligations": missing,
        "unsupported_claim_ids": deepcopy(unsupported),
        "surface_integrity": surface_integrity,
        "result_sha256": canonical_sha256(result),
    }


def _verify_project_record_membership(
    run: Mapping[str, object], project_record: object
) -> None:
    """Bind every graded result to one replay-verified Project Record event."""

    read_audit_trail = getattr(project_record, "read_audit_trail", None)
    read_current_project_view = getattr(
        project_record, "read_current_project_view", None
    )
    resolve_accepted_result = getattr(
        project_record, "resolve_accepted_result", None
    )
    if not all(
        callable(port)
        for port in (
            read_audit_trail,
            read_current_project_view,
            resolve_accepted_result,
        )
    ):
        raise ValueError("Project Record grading authority is unavailable")
    audit = read_audit_trail()
    view = read_current_project_view()
    if (
        not isinstance(audit, list)
        or not isinstance(view, Mapping)
        or run.get("project_record_project_id") != view.get("project_id")
        or run.get("project_record_event_sha256s")
        != [event.get("event_sha256") for event in audit]
        or run.get("project_record_replay_sha256") != canonical_sha256(view)
    ):
        raise ValueError("research dialogue Project Record evidence drifted")
    accepted_by_result: dict[str, str] = {}
    for event in audit:
        payload = event.get("payload") if isinstance(event, Mapping) else None
        candidate = payload.get("candidate") if isinstance(payload, Mapping) else None
        result = candidate.get("result") if isinstance(candidate, Mapping) else None
        if (
            isinstance(result, Mapping)
            and candidate.get("change_type") == "result.accepted"
            and event.get("event_type") == "research.project.accepted_change"
            and event.get("actor") == "assurance_boundary"
            and isinstance(event.get("event_sha256"), str)
        ):
            result_sha256 = canonical_sha256(result)
            if result_sha256 in accepted_by_result:
                raise ValueError("accepted result membership is ambiguous")
            accepted_by_result[result_sha256] = event["event_sha256"]
    if run.get("accepted_result_event_sha256s") != accepted_by_result:
        raise ValueError("accepted result event evidence drifted")
    turns = run.get("turns")
    if not isinstance(turns, list):
        raise ValueError("research dialogue turns are invalid")
    for observation in turns:
        result_view = (
            observation.get("result_view")
            if isinstance(observation, Mapping)
            else None
        )
        result = (
            result_view.get("result")
            if isinstance(result_view, Mapping)
            else None
        )
        if not isinstance(result, Mapping):
            raise ValueError("research dialogue accepted result is absent")
        result_sha256 = canonical_sha256(result)
        resolved = resolve_accepted_result(result_sha256)
        if resolved != result or result_sha256 not in accepted_by_result:
            raise ValueError("graded result is not a Project Record member")


def grade_research_dialogue(
    run: Mapping[str, object], *, project_record: object | None = None
) -> dict:
    """Grade typed outcomes only after Project Record membership is proven."""

    if not isinstance(run, Mapping) or run.get("contract") != RUN_CONTRACT:
        return {
            "contract": GRADE_CONTRACT,
            "task_status": "not_evaluated",
            "trial_status": "not_started",
            "grader_status": "failed",
            "instrument_status": "failed",
            "grader_error": "research dialogue run is invalid",
            "turns": [],
        }
    if run.get("trial_status") != "completed":
        return {
            "contract": GRADE_CONTRACT,
            "task_status": "not_evaluated",
            "trial_status": "failed",
            "grader_status": "not_started",
            "instrument_status": "completed",
            "grader_error": None,
            "turns": [],
        }
    try:
        if project_record is None:
            raise ValueError("Project Record grading authority is required")
        _verify_project_record_membership(run, project_record)
        rows = [_grade_turn(turn) for turn in run.get("turns", [])]
        if not rows:
            raise ValueError("research dialogue has no turn observations")
    except (KeyError, TypeError, ValueError) as error:
        return {
            "contract": GRADE_CONTRACT,
            "task_status": "not_evaluated",
            "trial_status": "completed",
            "grader_status": "failed",
            "instrument_status": "completed",
            "grader_error": str(error),
            "turns": [],
        }
    return {
        "contract": GRADE_CONTRACT,
        "task_status": "passed" if all(row["passed"] for row in rows) else "failed",
        "trial_status": "completed",
        "grader_status": "completed",
        "instrument_status": "completed",
        "grader_error": None,
        "turns": rows,
    }


def build_acceptance_receipt(
    run: Mapping[str, object],
    grade: Mapping[str, object],
    *,
    baseline_commit: str,
    candidate_commit: str,
    counterfactuals: Mapping[str, str],
    next_route: str,
) -> dict:
    """Build a redacted, content-addressed outcome receipt."""

    if (
        run.get("contract") != RUN_CONTRACT
        or grade.get("contract") != GRADE_CONTRACT
        or run.get("trial_status") != "completed"
        or grade.get("task_status") != "passed"
        or grade.get("trial_status") != "completed"
        or grade.get("grader_status") != "completed"
        or grade.get("instrument_status") != "completed"
    ):
        raise ValueError("research intelligence outcome is not accepted")
    if (
        not isinstance(baseline_commit, str)
        or len(baseline_commit) != 40
        or not isinstance(candidate_commit, str)
        or len(candidate_commit) != 40
        or not isinstance(counterfactuals, Mapping)
        or set(counterfactuals.values()) != {"passed"}
        or not isinstance(next_route, str)
        or not next_route
    ):
        raise ValueError("research intelligence receipt inputs are invalid")
    turns = run.get("turns")
    graded_turns = grade.get("turns")
    result_events = run.get("accepted_result_event_sha256s")
    if (
        not isinstance(turns, list)
        or not isinstance(graded_turns, list)
        or len(turns) != len(graded_turns)
        or not isinstance(result_events, Mapping)
    ):
        raise ValueError("research intelligence turn evidence is invalid")
    receipt_turns = []
    knowledge_refs: set[str] = set()
    evidence_refs: set[str] = set()
    limitations: list[str] = []
    for observation, graded in zip(turns, graded_turns, strict=True):
        result_view = observation.get("result_view")
        result = (
            result_view.get("result")
            if isinstance(result_view, Mapping)
            else None
        )
        turn = observation.get("turn")
        if not isinstance(result, Mapping) or not isinstance(turn, Mapping):
            raise ValueError("research intelligence accepted result is invalid")
        result_sha256 = canonical_sha256(result)
        event_sha256 = result_events.get(result_sha256)
        if not isinstance(event_sha256, str):
            raise ValueError("accepted result event membership is absent")
        knowledge_refs.update(
            value
            for value in (result.get("governed_knowledge_refs") or [])
            if isinstance(value, str)
        )
        evidence_refs.update(
            value
            for value in (result.get("citation_ids") or [])
            if isinstance(value, str)
        )
        limitations.extend(
            value
            for value in (result.get("limitations") or [])
            if isinstance(value, str) and value.strip()
        )
        receipt_turns.append(
            {
                "turn_id": turn["turn_id"],
                "expected_obligations": deepcopy(
                    graded["expected_obligations"]
                ),
                "covered_obligations": deepcopy(
                    graded["covered_obligations"]
                ),
                "surface_integrity": deepcopy(graded["surface_integrity"]),
                "accepted_result_sha256": result_sha256,
                "accepted_result_event_sha256": event_sha256,
            }
        )
    receipt = {
        "contract": ACCEPTANCE_CONTRACT,
        "baseline": {
            "commit": baseline_commit,
            "task_status": "failed",
            "evidence_mode": "frozen_behavior_reproduction",
            "diagnosis": "context_and_harness_failure",
        },
        "candidate_commit": candidate_commit,
        "fixture_id": run["fixture_id"],
        "fixture_sha256": run["fixture_sha256"],
        "outcome": {
            "task_status": grade["task_status"],
            "trial_status": grade["trial_status"],
            "grader_status": grade["grader_status"],
            "instrument_status": grade["instrument_status"],
        },
        "evaluation_scope": {
            "deterministic_provenance_and_obligation_support": "passed",
            "deterministic_user_visible_surface_integrity": "passed",
            "live_model_execution": "not_evaluated",
            "open_language_source_entailment": "not_evaluated",
            "independent_model_graded_quality": "not_evaluated",
            "human_lca_and_nonexpert_quality": "not_evaluated",
            "public_superiority_claim_allowed": False,
        },
        "turns": receipt_turns,
        "project_record": {
            "event_sha256s": deepcopy(
                run["project_record_event_sha256s"]
            ),
            "replay_sha256": run["project_record_replay_sha256"],
        },
        "run_trace_refs": deepcopy(run["run_trace_refs"]),
        "counts": deepcopy(run["runtime_counts"]),
        "evidence_locator_ids": sorted(evidence_refs),
        "governed_knowledge_refs": sorted(knowledge_refs),
        "counterfactuals": dict(sorted(counterfactuals.items())),
        "limitations": sorted(
            set(
                [
                    *limitations,
                    (
                        "Deterministic gates verify source identity, citation "
                        "binding, governed-knowledge statements, referents, and "
                        "declared obligation support; they do not establish "
                        "open-language source-claim entailment."
                    ),
                    (
                        "Independent live-model and human LCA/non-expert graders "
                        "have not yet been calibrated or run."
                    ),
                ]
            )
        ),
        "next_route": next_route,
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt
