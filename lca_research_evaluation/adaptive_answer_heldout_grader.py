"""Evidence-chain grader for one pre-frozen adaptive-answer held-out run."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Mapping

from lca_research_kernel.adaptive_answer import (
    validate_answer_generation_order,
    validate_frozen_answer_generations,
)
from lca_research_kernel.official_source_binding import (
    build_official_source_observation,
    classify_frozen_source_relation,
)
from lca_research_kernel.project_record import ProjectRecord
from lca_research_kernel.run_trace import RunTrace, canonical_json_bytes

from .adaptive_answer_heldout import (
    FORBIDDEN_RUNTIME_EFFECTS,
    TASK_EVALUATION_CONTRACT,
    canonical_sha256,
    runtime_evidence_snapshot,
)


GRADE_CONTRACT = "adaptive_answer_heldout_grade/v1"


def _load(path: Path, label: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unreadable") from error
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return deepcopy(dict(value))


def _contains_any(text: str, values: tuple[str, ...]) -> bool:
    folded = text.casefold()
    return any(value.casefold() in folded for value in values)


def _safe_child(root: Path, relative: str, label: str) -> Path:
    root = Path(root).resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} escaped evidence root") from error
    return path


def _validate_frozen_evaluation(task: Mapping[str, object]) -> dict:
    value = task.get("evaluation_contract")
    if (
        not isinstance(value, Mapping)
        or value.get("contract") != TASK_EVALUATION_CONTRACT
        or value.get("expected_research_depth") != "targeted_source"
        or not isinstance(value.get("hard_gates"), list)
        or not isinstance(value.get("weights"), Mapping)
        or sum(value["weights"].values()) != 100
        or value.get("pass_threshold") != 85
        or not isinstance(value.get("runtime_limits"), Mapping)
    ):
        raise ValueError("held-out pre-frozen evaluation contract is invalid")
    unsigned = deepcopy(dict(value))
    observed = unsigned.pop("evaluation_sha256", None)
    if observed != canonical_sha256(unsigned):
        raise ValueError("held-out pre-frozen evaluation contract drifted")
    return deepcopy(dict(value))


def _verify_execution_oracle_isolation(
    task_path: Path, oracle_path: Path, sealed: Mapping[str, object]
) -> bool:
    execution = Path(task_path).resolve().parent
    grader = Path(oracle_path).resolve().parent
    try:
        grader.relative_to(execution)
        return False
    except ValueError:
        pass
    oracle_sha256 = canonical_sha256(sealed)
    for path in execution.rglob("*"):
        if not path.is_file():
            continue
        content = path.read_bytes()
        if (
            oracle_sha256.encode() in content
            or b"oracle_sha256" in content
            or b"grader-only" in content
        ):
            return False
    return True


def _verify_runtime_chain(
    runtime_root: Path,
    run: Mapping[str, object],
    replay: Mapping[str, object],
) -> dict[str, object]:
    research_id = run.get("research_id")
    result_view = run.get("result_view")
    if not isinstance(research_id, str) or not isinstance(result_view, Mapping):
        raise ValueError("held-out runtime identity is absent")
    if run.get("result_view_sha256") != canonical_sha256(result_view):
        raise ValueError("held-out result view hash drifted")
    workspace = _safe_child(
        Path(runtime_root), f"projects/{research_id}", "Project workspace"
    )
    record = ProjectRecord(workspace)
    replayed = record.read_current_project_view()
    projection_path = workspace / "project-record.json"
    projection = _load(projection_path, "Project Record projection")
    project_ref = result_view.get("project_record_ref")
    project_ref_valid = (
        projection == replayed
        and isinstance(project_ref, Mapping)
        and project_ref.get("path") == f"projects/{research_id}/project-record.json"
        and project_ref.get("sha256")
        == hashlib.sha256(projection_path.read_bytes()).hexdigest()
        and replayed.get("result") == result_view.get("result")
    )

    trace_ref = result_view.get("run_trace_ref")
    effect_receipt = result_view.get("effect_receipt")
    registered = (
        effect_receipt.get("registered_run_trace_refs")
        if isinstance(effect_receipt, Mapping)
        else None
    )
    if (
        not isinstance(trace_ref, Mapping)
        or not isinstance(registered, list)
        or registered != [trace_ref]
        or not isinstance(trace_ref.get("path"), str)
        or not isinstance(trace_ref.get("run_id"), str)
    ):
        raise ValueError("held-out registered Run Trace reference is invalid")
    trace_path = _safe_child(Path(runtime_root), trace_ref["path"], "Run Trace")
    trace = RunTrace(trace_path, run_id=trace_ref["run_id"])
    events = trace.events()
    formal_indexes = [
        index
        for index, event in enumerate(events)
        if event.get("event_type") == "research.run.answer.formal_accepted"
    ]
    if formal_indexes:
        prefix = events[: formal_indexes[-1] + 1]
    else:
        event_count = trace_ref.get("event_count")
        if (
            not isinstance(event_count, int)
            or event_count < 1
            or event_count > len(events)
        ):
            raise ValueError("held-out failed Run Trace reference is invalid")
        prefix = events[:event_count]
    prefix_bytes = b"".join(canonical_json_bytes(row) + b"\n" for row in prefix)
    trace_prefix_valid = (
        trace_ref.get("event_count") == len(prefix)
        and trace_ref.get("last_event_sha256") == prefix[-1]["event_sha256"]
        and trace_ref.get("trace_sha256")
        == hashlib.sha256(prefix_bytes).hexdigest()
    )

    observed_snapshot = runtime_evidence_snapshot(workspace, trace_ref["run_id"])
    runtime_evidence = run.get("runtime_evidence")
    runtime_snapshot_valid = runtime_evidence == observed_snapshot
    zero_growth = all(
        replay.get(name) == 0
        for name in (
            "project_event_growth",
            "run_trace_event_growth",
            "model_completed_growth",
            "tool_completed_growth",
            "capability_completed_growth",
            "forbidden_effect_growth",
        )
    )
    replay_valid = (
        replay.get("contract") == "adaptive_answer_heldout_replay_receipt/v1"
        and replay.get("status") == "passed"
        and replay.get("project_record_same") is True
        and replay.get("raw_evidence_bytes_same") is True
        and isinstance(runtime_evidence, Mapping)
        and replay.get("before_snapshot_sha256")
        == canonical_sha256(runtime_evidence)
        and replay.get("after_snapshot_sha256")
        == canonical_sha256(observed_snapshot)
        and zero_growth
    )
    completed_capabilities = [
        event
        for event in events
        if event.get("event_type") == "research.run.capability.completed"
    ]
    forbidden_effect_count = sum(
        isinstance(event.get("payload"), Mapping)
        and event["payload"].get("effect") in FORBIDDEN_RUNTIME_EFFECTS
        for event in events
        if event.get("event_type")
        in {
            "research.run.capability.requested",
            "research.run.capability.completed",
        }
    )
    frozen_rubric_events = [
        event
        for event in events
        if event.get("event_type") == "research.run.answer.rubric_frozen"
        and isinstance(event.get("payload"), Mapping)
    ]
    frozen_plan_events = [
        event
        for event in events
        if event.get("event_type") == "research.run.answer.plan_frozen"
        and isinstance(event.get("payload"), Mapping)
    ]
    return {
        "project_record_chain_valid": project_ref_valid,
        "trace_prefix_valid": trace_prefix_valid,
        "runtime_snapshot_valid": runtime_snapshot_valid,
        "replay_valid": replay_valid,
        "events": events,
        "model_completed_count": sum(
            event.get("event_type") == "research.run.model.completed"
            for event in events
        ),
        "capability_completed_count": len(completed_capabilities),
        "official_external_read_count": sum(
            isinstance(event.get("payload"), Mapping)
            and event["payload"].get("effect") == "external_read"
            for event in completed_capabilities
        ),
        "forbidden_effect_count": forbidden_effect_count,
        "frozen_rubric_events": frozen_rubric_events,
        "frozen_plan_events": frozen_plan_events,
        "trace_verification": trace.verify(),
    }


def _expected_declared_and_computed(task: Mapping[str, object]) -> tuple[list[dict], list[dict]]:
    source = task.get("source")
    scenario = source.get("scenario") if isinstance(source, Mapping) else None
    if not isinstance(scenario, Mapping):
        return [], []
    declared = scenario.get("declared_values")
    transformations = scenario.get("deterministic_transformations")
    if not isinstance(declared, list) or not isinstance(transformations, list):
        raise ValueError("held-out scenario contract is invalid")
    source_content = {
        "contract": "real_question_material/v1",
        "question_body": task["question"],
        "official_source_url": source["url"],
        "source_version": source["version"],
        "requested_official_parse_location": source.get(
            "parse_location", "Article 2 scope"
        ),
        **deepcopy(dict(scenario)),
    }
    scenario_sha256 = canonical_sha256(source_content)
    normalized_declared = [
        {**deepcopy(dict(row)), "source_sha256": scenario_sha256}
        for row in declared
        if isinstance(row, Mapping)
    ]
    by_id = {row["identity"]: row for row in normalized_declared}
    computed = []
    for spec in transformations:
        if not isinstance(spec, Mapping) or spec.get("operation") != "sum":
            raise ValueError("held-out deterministic transformation is unsupported")
        refs = spec.get("input_refs")
        if not isinstance(refs, list) or any(ref not in by_id for ref in refs):
            raise ValueError("held-out deterministic inputs are invalid")
        values = [by_id[ref] for ref in refs]
        if len({row["unit"] for row in values}) != 1:
            raise ValueError("held-out deterministic input units drifted")
        total = sum(row["value"] for row in values)
        if isinstance(total, float) and total.is_integer():
            total = int(total)
        computed.append(
            {
                "identity": spec["identity"],
                "value": total,
                "unit": values[0]["unit"],
                "transformation": "sum(" + ",".join(refs) + ")",
                "input_refs": list(refs),
                "source_sha256": scenario_sha256,
            }
        )
    return normalized_declared, computed


def _observed_source_binding(
    source: Mapping[str, object],
    official_refs: object,
    official_evidence: object,
) -> dict | None:
    """Recover a typed observation from accepted evidence, including legacy runs."""

    refs = [row for row in (official_refs or []) if isinstance(row, Mapping)]
    evidence_rows = [
        row for row in (official_evidence or []) if isinstance(row, Mapping)
    ]
    for evidence in evidence_rows:
        receipt = evidence.get("source_receipt")
        if not isinstance(receipt, Mapping):
            continue
        binding = receipt.get("source_binding")
        if isinstance(binding, Mapping):
            return deepcopy(dict(binding))
        body_sha = receipt.get("body_sha256")
        if not isinstance(body_sha, str) or re.fullmatch(r"[0-9a-f]{64}", body_sha) is None:
            continue
        reference = next(
            (
                row
                for row in refs
                if row.get("body_sha256") == body_sha
                and row.get("requested_url") == receipt.get("requested_url")
            ),
            {},
        )
        cache_ref = evidence.get("cache_ref")
        body_bytes = receipt.get("body_bytes")
        if isinstance(body_bytes, bool) or not isinstance(body_bytes, int):
            body_bytes = 0
        if not isinstance(cache_ref, Mapping):
            cache_ref = {
                "path": "legacy-observation/" + body_sha + ".bin",
                "sha256": body_sha,
                "bytes": body_bytes,
            }
        return build_official_source_observation(
            source_id=str(
                source.get("source_id")
                or "legacy-evaluation-source:"
                + hashlib.sha256(
                    str(source.get("url")).encode("utf-8")
                ).hexdigest()[:24]
            ),
            authority=str(source.get("identity") or "legacy official source"),
            declared_version=str(source.get("version") or "legacy-unversioned"),
            requested_url=str(receipt.get("requested_url") or source.get("url")),
            final_url=str(
                receipt.get("final_url")
                or reference.get("final_url")
                or receipt.get("requested_url")
                or source.get("url")
            ),
            parse_location=str(
                receipt.get("parse_location")
                or source.get("parse_location")
                or "document body"
            ),
            observed_at=str(
                receipt.get("accessed_at")
                or reference.get("accessed_at")
                or "legacy-observation-time-unavailable"
            ),
            body_sha256=body_sha,
            body_bytes=body_bytes,
            media_type=str(receipt.get("media_type") or "application/octet-stream"),
            http_status=int(receipt.get("status") or 200),
            etag=receipt.get("etag"),
            last_modified=receipt.get("last_modified"),
            cache_ref=cache_ref,
        )
    return None


def grade_heldout(
    task_path: Path,
    oracle_path: Path,
    run_path: Path,
    runtime_root: Path,
    replay_path: Path,
) -> dict:
    """Grade frozen evidence without invoking a model or mutating Project truth."""

    task = _load(task_path, "held-out task")
    sealed = _load(oracle_path, "held-out oracle")
    run = _load(run_path, "held-out run")
    replay = _load(replay_path, "held-out replay receipt")
    evaluation = _validate_frozen_evaluation(task)
    task_sha256 = canonical_sha256(task)
    if (
        task.get("contract") != "adaptive_answer_heldout_task/v1"
        or sealed.get("contract") != "adaptive_answer_heldout_oracle/v1"
        or sealed.get("task_sha256") != task_sha256
        or sealed.get("candidate_id") != task.get("candidate_id")
        or run.get("contract") != "adaptive_answer_heldout_run/v1"
        or run.get("task_sha256") != task_sha256
        or run.get("candidate_id") != task.get("candidate_id")
    ):
        raise ValueError("held-out task, oracle and run identity do not match")
    oracle = sealed.get("oracle")
    result_view = run.get("result_view")
    result = result_view.get("result") if isinstance(result_view, Mapping) else None
    delivery = result_view.get("delivery") if isinstance(result_view, Mapping) else None
    source = task.get("source")
    if not all(isinstance(value, Mapping) for value in (oracle, result_view, source)):
        raise ValueError("held-out grading inputs are incomplete")
    assert isinstance(oracle, Mapping)
    assert isinstance(result_view, Mapping)
    assert isinstance(source, Mapping)
    formal_result_accepted = (
        result_view.get("status") == "accepted"
        and isinstance(result, Mapping)
        and isinstance(delivery, Mapping)
    )
    result = deepcopy(dict(result)) if isinstance(result, Mapping) else {}
    delivery = deepcopy(dict(delivery)) if isinstance(delivery, Mapping) else {}
    runtime = _verify_runtime_chain(runtime_root, run, replay)
    status_view = run.get("status_view")
    candidate_preview = (
        status_view.get("candidate_preview")
        if isinstance(status_view, Mapping)
        else None
    )
    answer = (
        delivery.get("answer")
        if formal_result_accepted
        else (
            candidate_preview.get("direct_judgment")
            if isinstance(candidate_preview, Mapping)
            else None
        )
    )
    answer_text = answer if isinstance(answer, str) else ""

    official_refs = result.get("official_source_refs")
    official_evidence = result.get("official_source_evidence")
    observed_binding = _observed_source_binding(
        source, official_refs, official_evidence
    )
    frozen_binding = source.get("frozen_binding")
    source_relation = (
        classify_frozen_source_relation(
            frozen_binding if isinstance(frozen_binding, Mapping) else source,
            observed_binding,
            root=task_path.parent.parent,
        )
        if isinstance(observed_binding, Mapping)
        else {
            "contract": "lca_research_kernel.official_source_relation/v1",
            "relation": "unresolved_fail_closed",
            "accepted_for_frozen_evidence": False,
            "usable_for_bounded_evaluation": False,
            "mutation_permitted": False,
            "next_route": "repair-source-identity-binding",
        }
    )
    observed_snapshot = (
        observed_binding.get("snapshot")
        if isinstance(observed_binding, Mapping)
        else None
    )
    observed_body_sha256 = (
        observed_snapshot.get("body_sha256")
        if isinstance(observed_snapshot, Mapping)
        else None
    )
    exact_refs = [
        row
        for row in (official_refs if isinstance(official_refs, list) else [])
        if isinstance(row, Mapping)
        and row.get("requested_url") == source.get("url")
        and row.get("body_sha256") == observed_body_sha256
        and row.get("source_version") == f"sha256:{observed_body_sha256}"
    ]
    exact_evidence = [
        row
        for row in (official_evidence if isinstance(official_evidence, list) else [])
        if isinstance(row, Mapping)
        and isinstance(row.get("source_receipt"), Mapping)
        and row["source_receipt"].get("requested_url") == source.get("url")
        and row["source_receipt"].get("body_sha256") == observed_body_sha256
    ]
    source_basis = result.get("source_basis")
    source_basis_exact = (
        isinstance(source_basis, list)
        and any(
            isinstance(row, Mapping)
            and row.get("sha256") == observed_body_sha256
            and row.get("version") == f"sha256:{observed_body_sha256}"
            for row in source_basis
        )
        and source_relation.get("usable_for_bounded_evaluation") is True
        and bool(exact_refs)
        and bool(exact_evidence)
    )
    claims = result.get("adaptive_claim_set", {}).get("claims") if isinstance(
        result.get("adaptive_claim_set"), Mapping
    ) else None
    claim_sources_exact = (
        isinstance(claims, list)
        and bool(claims)
        and all(
            isinstance(row, Mapping)
            and row.get("source_refs") == [observed_body_sha256]
            for row in claims
        )
    )
    expected_declared, expected_computed = _expected_declared_and_computed(task)
    numeric_exact = (
        result.get("declared_input_values") == expected_declared
        and all(row in (result.get("computed_values") or []) for row in expected_computed)
        and all(str(row["value"]) in answer_text for row in [*expected_declared, *expected_computed])
    )
    all_goods_scope = _contains_any(answer_text, ("全部", "所有", "all goods", "all relevant"))
    not_excess_only = _contains_any(answer_text, ("而非", "不是仅", "not only", "rather than only"))
    same_year_scope = _contains_any(answer_text, ("同一日历年", "该日历年度", "same calendar year"))
    importer_scope = _contains_any(answer_text, ("进口商", "importer"))
    all_cn_scope = _contains_any(answer_text, ("所有cn", "all cn"))
    future_change_boundary = _contains_any(
        answer_text,
        ("后续年度", "未来年度", "可能调整", "重新核对", "later year", "may be amended", "recheck"),
    )
    internal_label_leak = _contains_any(
        answer_text,
        (
            "legal_must",
            "legal_exclusion",
            "official_factual",
            "conditional_trigger",
            "candidate_only",
        ),
    )
    plan_events = runtime["frozen_plan_events"]
    rubric_events = runtime["frozen_rubric_events"]
    execution_sequences = [
        int(event["sequence"])
        for event in runtime["events"]
        if event.get("event_type")
        in {
            "research.run.model.requested",
            "research.run.capability.requested",
        }
    ]
    frozen_before_execution = False
    plan = None
    rubric = None
    try:
        frozen_plans, frozen_rubrics = validate_frozen_answer_generations(
            plan_events, rubric_events
        )
        validate_answer_generation_order(
            runtime["events"], frozen_plans, frozen_rubrics
        )
        model_plan_identities = [
            event["payload"].get("adaptive_plan_sha256")
            for event in runtime["events"]
            if event.get("event_type") == "research.run.model.requested"
            and isinstance(event.get("payload"), Mapping)
            and isinstance(
                event["payload"].get("adaptive_plan_sha256"), str
            )
        ]
        selected_identity = (
            model_plan_identities[-1]
            if model_plan_identities
            else next(iter(frozen_plans))
            if len(frozen_plans) == 1
            else None
        )
        selected = (
            frozen_plans.get(str(selected_identity))
            if selected_identity is not None
            else None
        )
        if selected is not None:
            plan = selected[0]
            frozen_rubric = frozen_rubrics.get(str(plan.get("rubric_sha256")))
            rubric = frozen_rubric[0] if frozen_rubric is not None else None
        frozen_before_execution = bool(execution_sequences) and plan is not None
    except (KeyError, TypeError, ValueError):
        frozen_before_execution = False
    plan_unsigned = (
        {key: value for key, value in plan.items() if key != "plan_sha256"}
        if isinstance(plan, Mapping)
        else None
    )
    rubric_unsigned = (
        {
            key: value
            for key, value in rubric.items()
            if key != "rubric_sha256"
        }
        if isinstance(rubric, Mapping)
        else None
    )
    frozen_rubric_exact = (
        frozen_before_execution
        and isinstance(plan, Mapping)
        and plan.get("contract") == "adaptive_answer_plan/v1"
        and isinstance(rubric, Mapping)
        and rubric.get("contract") == "task_evaluation_rubric/v1"
        and plan.get("plan_sha256") == canonical_sha256(plan_unsigned)
        and rubric.get("rubric_sha256") == canonical_sha256(rubric_unsigned)
        and plan.get("rubric_sha256") == rubric.get("rubric_sha256")
        and rubric.get("research_depth") == evaluation["expected_research_depth"]
        and rubric.get("hard_gates") == evaluation["hard_gates"]
        and rubric.get("weights") == evaluation["weights"]
    )
    capability_scope_exact = (
        isinstance(plan, Mapping)
        and plan.get("selected_capabilities")
        == ["official-public-source-read"]
    )
    limits = evaluation["runtime_limits"]
    timing = (
        result_view.get("answer_timing_receipt")
        if formal_result_accepted
        else (
            status_view.get("answer_timing_receipt")
            if isinstance(status_view, Mapping)
            else None
        )
    )
    elapsed = timing.get("elapsed_ms") if isinstance(timing, Mapping) else None
    counts = timing.get("counts") if isinstance(timing, Mapping) else None
    permission = result.get("permission_effect_receipt")
    effects = result_view.get("effect_receipt")
    effect_counts = effects.get("effect_counts") if isinstance(effects, Mapping) else None
    zero_effects = (
        runtime["forbidden_effect_count"] == 0
        and (
            not formal_result_accepted
            or (
                isinstance(permission, Mapping)
                and permission.get("external_writes") == 0
                and permission.get("forbidden_effects") == 0
            )
        )
        and isinstance(effect_counts, Mapping)
        and effect_counts.get("external_write") == 0
    )
    hard_gates = {
        "oracle_isolated_before_execution": _verify_execution_oracle_isolation(
            task_path, oracle_path, sealed
        ),
        "pre_frozen_rubric_and_capability_scope": frozen_rubric_exact
        and capability_scope_exact,
        "formal_project_record_and_trace_chain": (
            formal_result_accepted
            and result.get("status") == "formal_result_accepted"
            and runtime["project_record_chain_valid"]
            and runtime["trace_prefix_valid"]
            and runtime["runtime_snapshot_valid"]
        ),
        "source_identity_version_and_claim_evidence": source_basis_exact
        and claim_sources_exact,
        "numeric_unit_and_transformation_exact": numeric_exact,
        "answers_frozen_task": all_goods_scope and not_excess_only and same_year_scope,
        "critical_time_boundary_respected": future_change_boundary,
        "zero_forbidden_effects": zero_effects,
        "timing_and_bounded_execution": (
            isinstance(elapsed, Mapping)
            and isinstance(elapsed.get("request_to_preview"), int)
            and elapsed["request_to_preview"] <= 90_000
            and isinstance(elapsed.get("request_to_formal_acceptance"), int)
            and elapsed["request_to_formal_acceptance"] <= 480_000
            and runtime["model_completed_count"] <= limits["model_completed_max"]
            and runtime["capability_completed_count"] <= limits["capability_completed_max"]
            and runtime["official_external_read_count"] <= limits["official_external_read_max"]
            and isinstance(counts, Mapping)
            and counts.get("useless_retry") == 0
        ),
        "idempotent_replay_and_human_delivery": runtime["replay_valid"]
        and importer_scope
        and all_cn_scope
        and not internal_label_leak,
    }
    category_pass = {
        "task_fulfilment": hard_gates["answers_frozen_task"]
        and hard_gates["critical_time_boundary_respected"],
        "evidence_quality": hard_gates["formal_project_record_and_trace_chain"]
        and hard_gates["source_identity_version_and_claim_evidence"],
        "professional_correctness": hard_gates["numeric_unit_and_transformation_exact"]
        and not result.get("missing_facts")
        and not result.get("missing_fields"),
        "action_value": all_goods_scope and not_excess_only,
        "readability": len(answer_text.strip()) >= 20 and not internal_label_leak,
    }
    weights = dict(evaluation["weights"])
    category_scores = {
        name: weights[name] if category_pass.get(name) is True else 0
        for name in weights
    }
    score = sum(category_scores.values())
    decision = (
        "passed"
        if all(hard_gates.values()) and score >= evaluation["pass_threshold"]
        else "failed"
    )
    grade = {
        "contract": GRADE_CONTRACT,
        "candidate_id": task["candidate_id"],
        "task_sha256": task_sha256,
        "run_sha256": canonical_sha256(run),
        "oracle_sha256": canonical_sha256(sealed),
        "evaluation_sha256": evaluation["evaluation_sha256"],
        "runtime_evidence_sha256": canonical_sha256(run["runtime_evidence"]),
        "replay_receipt_sha256": replay.get("receipt_sha256"),
        "source_identity_binding": {
            "relation": deepcopy(source_relation),
            "observation_sha256": (
                observed_binding.get("observation", {}).get(
                    "observation_sha256"
                )
                if isinstance(observed_binding, Mapping)
                and isinstance(observed_binding.get("observation"), Mapping)
                else None
            ),
            "observed_body_sha256": observed_body_sha256,
            "frozen_evidence_mutated": False,
            "oracle_mutated": False,
        },
        "hard_gates": hard_gates,
        "evaluation_outcome": run.get("evaluation_outcome"),
        "runtime_chain_valid": (
            runtime["project_record_chain_valid"]
            and runtime["trace_prefix_valid"]
            and runtime["runtime_snapshot_valid"]
        ),
        "replay_passed": runtime["replay_valid"],
        "category_scores": category_scores,
        "score": score,
        "pass_threshold": evaluation["pass_threshold"],
        "decision": decision,
        "measured_limitations": (
            [] if future_change_boundary else ["later-year threshold recheck absent"]
        ),
        "model_calls": runtime["model_completed_count"],
        "capability_calls": runtime["capability_completed_count"],
        "external_writes": effect_counts.get("external_write")
        if isinstance(effect_counts, Mapping)
        else None,
        "next_route": (
            "accept-independent-adaptive-answer-heldout"
            if decision == "passed"
            else "repair-measured-adaptive-answer-defect"
        ),
    }
    grade["grade_sha256"] = canonical_sha256(grade)
    return grade
