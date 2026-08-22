"""Deterministic acceptance for the three-task minimum real research path."""

from __future__ import annotations

import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Mapping

from lca_research_kernel.project_record import ProjectRecord
from lca_research_kernel.run_trace import RunTrace

from .minimum_real_path import validate_minimum_real_path_contract
from .minimum_real_path_runner import MinimumRealPathRunner


ACCEPTANCE_CONTRACT = "d18_minimum_real_path_acceptance/v1"
REPLAY_CONTRACT = "d18_minimum_real_path_replay_receipt/v1"
LEGACY_CONTRACT = "d18_minimum_real_path_legacy_disposition/v1"
FINALIZE_ROUTE = "finalize-minimum-real-research-path"
TASK_IDS = ("Q1", "Q9", "Q5")
FORMAL_STATUSES = {
    "formal_result_accepted",
    "formal_limited_result_accepted",
}
HIDDEN_TERMS = (
    "sealed-reference",
    "sealed_reference",
    "reference-answer",
    "reference_answer",
    "grader_answer",
    "oracle_answer",
)
GATE_NAMES = (
    "three_task_roles_exact",
    "q1_formal_or_limited",
    "q9_current_official_read",
    "q5_missing_data_bounded",
    "timing_contract",
    "project_record_only_formal_truth",
    "run_trace_registered",
    "replay_zero_growth",
    "forbidden_effects_zero",
    "sealed_answer_isolation",
    "v3_v7_bytes_preserved",
    "claim_ceiling_development_only",
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
        raise ValueError(f"JSON object required: {path}")
    return value


def _tree_rows(path: Path) -> list[dict[str, str]]:
    return [
        {
            "path": item.relative_to(path).as_posix(),
            "sha256": _file_sha256(item),
        }
        for item in sorted(path.rglob("*"))
        if item.is_file()
    ]


def _tree_sha256(path: Path) -> str:
    return _canonical_sha256(_tree_rows(path))


def _unsigned_hash(value: Mapping[str, object], field: str) -> str:
    return _canonical_sha256(
        {key: deepcopy(item) for key, item in value.items() if key != field}
    )


def _answer_paths(execution_root: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for question_id in TASK_IDS:
        matches = sorted(
            execution_root.glob(
                f"minimum-real-path*/{question_id}/accepted-answer.json"
            )
        )
        if len(matches) == 1:
            found[question_id] = matches[0]
    return found


def _run_root(answer_path: Path) -> Path:
    return answer_path.parents[1]


def _runtime_root(answer_path: Path) -> Path:
    return _run_root(answer_path) / "runtime-data"


def _validate_answer(
    answer: Mapping[str, object],
    *,
    question_id: str,
    contract_sha256: str,
) -> bool:
    result = answer.get("result")
    return bool(
        answer.get("contract") == "minimum_path_accepted_answer/v1"
        and answer.get("question_id") == question_id
        and answer.get("contract_sha256") == contract_sha256
        and answer.get("status") in FORMAL_STATUSES
        and answer.get("executed_external_effects") == 0
        and isinstance(result, Mapping)
        and answer.get("result_sha256") == _canonical_sha256(result)
        and answer.get("accepted_answer_sha256")
        == _unsigned_hash(answer, "accepted_answer_sha256")
        and answer.get("next_route") == "record-minimum-path-result"
    )


def _validate_trace(
    answer: Mapping[str, object],
    *,
    runtime_root: Path,
) -> tuple[bool, list[dict]]:
    ref = answer.get("run_trace_ref")
    if not isinstance(ref, Mapping):
        return False, []
    path_value = ref.get("path")
    run_id = ref.get("run_id")
    event_count = ref.get("event_count")
    if (
        not isinstance(path_value, str)
        or not isinstance(run_id, str)
        or isinstance(event_count, bool)
        or not isinstance(event_count, int)
        or event_count <= 0
    ):
        return False, []
    path = runtime_root / path_value
    try:
        path.resolve().relative_to(runtime_root.resolve())
    except ValueError:
        return False, []
    if not path.is_file():
        return False, []
    try:
        events = RunTrace(path, run_id=run_id).events()
    except (OSError, ValueError, json.JSONDecodeError):
        return False, []
    if len(events) < event_count:
        return False, events
    bounded = events[:event_count]
    trace_bytes = b"".join(_canonical_bytes(event) + b"\n" for event in bounded)
    valid = (
        ref.get("last_event_sha256") == bounded[-1].get("event_sha256")
        and ref.get("trace_sha256") == hashlib.sha256(trace_bytes).hexdigest()
        and any(
            event.get("event_type") == "research.run.answer.formal_accepted"
            for event in bounded
        )
    )
    return bool(valid), events


def _validate_project_record(
    answer: Mapping[str, object],
    *,
    runtime_root: Path,
) -> tuple[bool, dict, list[dict]]:
    ref = answer.get("project_record_ref")
    if not isinstance(ref, Mapping) or not isinstance(ref.get("path"), str):
        return False, {}, []
    path = runtime_root / str(ref["path"])
    try:
        path.resolve().relative_to(runtime_root.resolve())
    except ValueError:
        return False, {}, []
    if (
        not path.is_file()
        or ref.get("role") != "project_record_projection"
        or ref.get("sha256") != _file_sha256(path)
    ):
        return False, {}, []
    try:
        stored = _load_json(path)
        record = ProjectRecord(path.parent)
        replayed = record.read_current_project_view()
        events = record.read_audit_trail()
    except (OSError, ValueError, json.JSONDecodeError):
        return False, {}, []
    result = answer.get("result")
    valid = (
        stored == replayed
        and stored.get("result") == result
        and any(
            event.get("event_type") == "research.project.accepted_change"
            and event.get("actor") == "assurance_boundary"
            and isinstance(event.get("payload"), Mapping)
            and isinstance(event["payload"].get("candidate"), Mapping)
            and event["payload"]["candidate"].get("change_type")
            == "result.accepted"
            for event in events
        )
    )
    return bool(valid), stored, events


def _nested_contains(value: object, needle: str) -> bool:
    if isinstance(value, Mapping):
        return any(_nested_contains(item, needle) for item in value.values())
    if isinstance(value, list):
        return any(_nested_contains(item, needle) for item in value)
    return value == needle


def _runtime_official_source_valid(
    answer: Mapping[str, object],
    *,
    runtime_root: Path,
    project_view: Mapping[str, object],
) -> bool:
    result = answer.get("result")
    if not isinstance(result, Mapping):
        return False
    evidence = result.get("official_source_evidence")
    refs = result.get("official_source_refs")
    if not isinstance(evidence, list) or not evidence or not isinstance(refs, list):
        return False
    project_path = None
    project_ref = answer.get("project_record_ref")
    if isinstance(project_ref, Mapping) and isinstance(project_ref.get("path"), str):
        project_path = (runtime_root / str(project_ref["path"])).parent
    if project_path is None:
        return False

    valid_receipts = 0
    for row in evidence:
        if not isinstance(row, Mapping):
            return False
        receipt = row.get("source_receipt")
        cache_ref = row.get("cache_ref")
        if not isinstance(receipt, Mapping) or not isinstance(cache_ref, Mapping):
            return False
        receipt_sha = receipt.get("receipt_sha256")
        if (
            receipt.get("contract") != "official_public_source_receipt/v1"
            or receipt.get("status") != 200
            or not isinstance(receipt_sha, str)
            or receipt_sha != _unsigned_hash(receipt, "receipt_sha256")
            or not isinstance(receipt.get("run_id"), str)
            or not str(receipt["run_id"]).strip()
            or cache_ref.get("sha256") != receipt.get("body_sha256")
            or cache_ref.get("bytes") != receipt.get("body_bytes")
            or not isinstance(cache_ref.get("path"), str)
        ):
            return False
        cache_path = (
            runtime_root
            / "control/official-public-source-cache"
            / str(cache_ref["path"])
        )
        try:
            cache_path.resolve().relative_to(runtime_root.resolve())
        except ValueError:
            return False
        if (
            not cache_path.is_file()
            or cache_path.stat().st_size != cache_ref.get("bytes")
            or _file_sha256(cache_path) != cache_ref.get("sha256")
        ):
            return False
        matching_ref = any(
            isinstance(ref, Mapping)
            and ref.get("receipt_sha256") == receipt_sha
            and ref.get("body_sha256") == receipt.get("body_sha256")
            for ref in refs
        )
        if not matching_ref:
            return False
        run_id = str(receipt["run_id"])
        trace_path = project_path / "run-traces" / f"{run_id}.jsonl"
        if not trace_path.is_file():
            return False
        try:
            runtime_events = RunTrace(trace_path, run_id=run_id).events()
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        direct_trace_binding = any(
            event.get("event_type") == "research.run.capability.completed"
            and _nested_contains(event.get("payload"), receipt_sha)
            for event in runtime_events
        )
        facts = project_view.get("facts")
        fact_trace_binding = False
        if isinstance(facts, Mapping):
            for fact in facts.values():
                if not isinstance(fact, Mapping):
                    continue
                source = fact.get("source")
                value = fact.get("value")
                if not isinstance(source, Mapping) or not isinstance(value, Mapping):
                    continue
                content = value.get("content")
                if (
                    source.get("capability_id") != "official-public-source-read"
                    or source.get("operation_run_id") != run_id
                    or source.get("operation_id") != receipt.get("operation_id")
                    or value.get("status") != "sourced_result_not_fact"
                    or not isinstance(content, Mapping)
                    or content.get("source_receipt") != receipt
                    or value.get("content_sha256") != _canonical_sha256(content)
                    or source.get("content_sha256") != value.get("content_sha256")
                ):
                    continue
                value_sha256 = _canonical_sha256(value)
                fact_trace_binding = any(
                    event.get("event_type")
                    == "research.run.capability.completed"
                    and isinstance(event.get("payload"), Mapping)
                    and event["payload"].get("capability_id")
                    == "official-public-source-read"
                    and event["payload"].get("effect") == "external_read"
                    and event["payload"].get("request_id")
                    == source.get("operation_id")
                    and event["payload"].get("result_status")
                    == "sourced_result_not_fact"
                    and event["payload"].get("result_sha256") == value_sha256
                    for event in runtime_events
                )
                if fact_trace_binding:
                    break
        if not (direct_trace_binding or fact_trace_binding):
            return False
        valid_receipts += 1
    return valid_receipts > 0


def _timing_valid(answer: Mapping[str, object], timing: Mapping[str, object]) -> bool:
    receipt = answer.get("answer_timing_receipt")
    if not isinstance(receipt, Mapping):
        return False
    elapsed = receipt.get("elapsed_ms")
    if not isinstance(elapsed, Mapping):
        return False
    formal = elapsed.get("request_to_formal_acceptance")
    preview = elapsed.get("request_to_preview")
    preview_limit = timing.get("candidate_preview")
    if preview is None:
        if (
            isinstance(formal, bool)
            or not isinstance(formal, (int, float))
            or isinstance(preview_limit, bool)
            or not isinstance(preview_limit, (int, float))
            or formal > preview_limit
        ):
            return False
    checks = (
        ("request_to_formal_acceptance", timing.get("formal_result")),
        ("request_to_user_delivery", timing.get("hard_boundary")),
    )
    if preview is not None:
        checks = (
            ("request_to_preview", preview_limit),
            *checks,
        )
    for key, limit in checks:
        value = elapsed.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or isinstance(limit, bool)
            or not isinstance(limit, (int, float))
            or value < 0
            or value > limit
        ):
            return False
    return True


def _zero_effects(answer: Mapping[str, object], trace_events: list[dict]) -> bool:
    if answer.get("executed_external_effects") != 0:
        return False
    result = answer.get("result")
    if not isinstance(result, Mapping):
        return False
    receipt = result.get("permission_effect_receipt")
    if not isinstance(receipt, Mapping) or any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value != 0
        for key, value in receipt.items()
        if key != "contract"
    ):
        return False
    return not any(
        event.get("event_type") == "research.run.capability.completed"
        and isinstance(event.get("payload"), Mapping)
        and (
            event["payload"].get("effect") == "external_write"
            or (
                isinstance(event["payload"].get("receipt"), Mapping)
                and event["payload"]["receipt"].get("executed_external_effects")
                not in {None, 0}
            )
        )
        for event in trace_events
    )


def _load_signed(path: Path, *, contract: str, signature: str) -> dict | None:
    try:
        value = _load_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if (
        value.get("contract") != contract
        or value.get(signature) != _unsigned_hash(value, signature)
    ):
        return None
    return value


def _replay_valid(execution_root: Path) -> tuple[bool, dict | None]:
    receipt = _load_signed(
        execution_root / "minimum-real-path/replay-receipt.json",
        contract=REPLAY_CONTRACT,
        signature="receipt_sha256",
    )
    if receipt is None or receipt.get("status") != "passed":
        return False, receipt
    tasks = receipt.get("tasks")
    if not isinstance(tasks, Mapping) or set(tasks) != set(TASK_IDS):
        return False, receipt
    paths = _answer_paths(execution_root)
    for question_id in TASK_IDS:
        row = tasks.get(question_id)
        path = paths.get(question_id)
        if not isinstance(row, Mapping) or path is None:
            return False, receipt
        current_tree = _tree_sha256(_run_root(path))
        if (
            row.get("before_tree_sha256") != current_tree
            or row.get("after_tree_sha256") != current_tree
            or any(
                row.get(key) != 0
                for key in (
                    "model_call_growth",
                    "tool_call_growth",
                    "external_effect_growth",
                    "project_record_event_growth",
                )
            )
        ):
            return False, receipt
    return True, receipt


def _legacy_valid(execution_root: Path) -> tuple[bool, bool, dict | None]:
    disposition = _load_signed(
        execution_root / "minimum-real-path/legacy-disposition.json",
        contract=LEGACY_CONTRACT,
        signature="disposition_sha256",
    )
    if disposition is None:
        return False, False, disposition
    trees = disposition.get("trees")
    valid = isinstance(trees, Mapping) and set(trees) == {
        "v3",
        "v4",
        "v5",
        "v6",
        "v7",
    }
    if valid:
        for version, row in trees.items():
            if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
                valid = False
                break
            path = execution_root / str(row["path"])
            try:
                path.resolve().relative_to(execution_root.resolve())
            except ValueError:
                valid = False
                break
            if (
                not path.is_dir()
                or row.get("tree_sha256") != _tree_sha256(path)
                or (
                    version == "v7"
                    and row.get("status") != "parked_unexecuted"
                )
            ):
                valid = False
                break
    valid = bool(valid and disposition.get("v7_model_completed_count") == 0)
    ceiling = disposition.get("generalization_claim_permitted") is False
    return valid, ceiling, disposition


def _failure_route(gates: Mapping[str, bool]) -> tuple[str, str]:
    routes = {
        "three_task_roles_exact": (
            "context",
            "restore-exact-minimum-real-path-task-set",
        ),
        "q1_formal_or_limited": (
            "harness",
            "repair-q1-accepted-result-evidence",
        ),
        "q9_current_official_read": (
            "context",
            "repair-q9-runtime-official-source-binding",
        ),
        "q5_missing_data_bounded": (
            "model",
            "repair-q5-missing-data-claim-boundary",
        ),
        "timing_contract": ("loop", "repair-answer-timing-route"),
        "project_record_only_formal_truth": (
            "harness",
            "repair-project-record-acceptance-chain",
        ),
        "run_trace_registered": (
            "harness",
            "repair-run-trace-registration",
        ),
        "replay_zero_growth": ("loop", "repair-idempotent-recovery"),
        "forbidden_effects_zero": (
            "harness",
            "repair-effect-boundary",
        ),
        "sealed_answer_isolation": (
            "context",
            "repair-sealed-answer-isolation",
        ),
        "v3_v7_bytes_preserved": (
            "harness",
            "restore-immutable-development-evidence",
        ),
        "claim_ceiling_development_only": (
            "prompt",
            "restore-development-evidence-claim-ceiling",
        ),
    }
    for name in GATE_NAMES:
        if not gates[name]:
            return routes[name]
    return "loop", FINALIZE_ROUTE


def evaluate_minimum_real_path(
    *,
    contract: Mapping[str, object],
    execution_root: Path,
) -> dict[str, object]:
    """Evaluate only immutable runtime evidence; never execute a model or tool."""

    root = Path(execution_root)
    gates = {name: False for name in GATE_NAMES}
    errors: list[str] = []
    try:
        pack = contract.get("question_pack")
        if not isinstance(pack, Mapping) or not isinstance(pack.get("path"), str):
            raise ValueError("minimum real path question pack reference is absent")
        validated = validate_minimum_real_path_contract(
            contract,
            question_pack_path=Path(__file__).resolve().parents[1] / str(pack["path"]),
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        errors.append(str(error))
        report = {
            "contract": ACCEPTANCE_CONTRACT,
            "status": "rejected",
            "gates": gates,
            "task_evidence": {},
            "errors": errors,
            "layer_diagnosis": "prompt",
            "next_routes": ["repair-minimum-real-path-contract"],
            "claim_ceiling": "development_path_evidence_only",
        }
        report["acceptance_sha256"] = _canonical_sha256(report)
        return report

    paths = _answer_paths(root)
    expected_roles = {
        str(task["question_id"]): str(task["trajectory_role"])
        for task in validated["tasks"]
        if isinstance(task, Mapping)
    }
    gates["three_task_roles_exact"] = (
        set(paths) == set(TASK_IDS)
        and expected_roles
        == {
            "Q1": "complete_user_material",
            "Q9": "current_official_source_required",
            "Q5": "critical_data_missing",
        }
    )
    contract_sha = _canonical_sha256(validated)
    task_evidence: dict[str, dict] = {}
    hidden_clean = True
    project_truth = True
    traces_registered = True
    effects_zero = True
    timing_valid = True

    for question_id in TASK_IDS:
        path = paths.get(question_id)
        if path is None:
            errors.append(f"{question_id}: accepted answer is absent")
            project_truth = False
            traces_registered = False
            effects_zero = False
            timing_valid = False
            continue
        try:
            answer = _load_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            errors.append(f"{question_id}: accepted answer is unreadable")
            continue
        answer_valid = _validate_answer(
            answer,
            question_id=question_id,
            contract_sha256=contract_sha,
        )
        pr_valid, project_view, _ = _validate_project_record(
            answer,
            runtime_root=_runtime_root(path),
        )
        trace_valid, trace_events = _validate_trace(
            answer,
            runtime_root=_runtime_root(path),
        )
        project_truth = project_truth and pr_valid
        traces_registered = traces_registered and trace_valid
        effects_zero = effects_zero and _zero_effects(answer, trace_events)
        timing_valid = timing_valid and _timing_valid(
            answer,
            validated["timing_ms"],
        )
        scanned = json.dumps(
            {
                "answer": answer,
                "project_record": project_view,
                "run_trace": trace_events,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).casefold()
        hidden_clean = hidden_clean and not any(
            term in scanned for term in HIDDEN_TERMS
        )
        result = answer.get("result")
        result_map = result if isinstance(result, Mapping) else {}
        task_evidence[question_id] = {
            "trajectory_role": expected_roles.get(question_id),
            "status": answer.get("status"),
            "answer_ref": {
                "path": path.relative_to(root).as_posix(),
                "sha256": _file_sha256(path),
            },
            "accepted_answer_sha256": answer.get("accepted_answer_sha256"),
            "project_record_ref": deepcopy(answer.get("project_record_ref")),
            "run_trace_ref": deepcopy(answer.get("run_trace_ref")),
            "timing": deepcopy(answer.get("answer_timing_receipt")),
            "source_count": len(result_map.get("official_source_refs", []))
            if isinstance(result_map.get("official_source_refs"), list)
            else 0,
            "claim_ceiling": result_map.get("claim_ceiling"),
            "answer_identity_valid": answer_valid,
            "project_record_valid": pr_valid,
            "run_trace_valid": trace_valid,
        }
        if question_id == "Q1":
            gates["q1_formal_or_limited"] = bool(
                answer_valid and answer.get("status") in FORMAL_STATUSES
            )
        elif question_id == "Q9":
            gates["q9_current_official_read"] = bool(
                answer_valid
                and answer.get("status") == "formal_result_accepted"
                and pr_valid
                and _runtime_official_source_valid(
                    answer,
                    runtime_root=_runtime_root(path),
                    project_view=project_view,
                )
            )
        elif question_id == "Q5":
            gates["q5_missing_data_bounded"] = bool(
                answer_valid
                and answer.get("status") == "formal_limited_result_accepted"
                and isinstance(result, Mapping)
                and isinstance(result.get("missing_facts"), list)
                and bool(result["missing_facts"])
                and isinstance(result.get("resume_conditions"), list)
                and bool(result["resume_conditions"])
                and result.get("computed_values") == []
                and result.get("values") == []
                and isinstance(result.get("claim_ceiling"), str)
                and bool(result["claim_ceiling"])
            )

    gates["timing_contract"] = timing_valid
    gates["project_record_only_formal_truth"] = project_truth
    gates["run_trace_registered"] = traces_registered
    gates["forbidden_effects_zero"] = effects_zero
    gates["sealed_answer_isolation"] = hidden_clean
    gates["replay_zero_growth"], replay = _replay_valid(root)
    (
        gates["v3_v7_bytes_preserved"],
        gates["claim_ceiling_development_only"],
        legacy,
    ) = _legacy_valid(root)
    gates["claim_ceiling_development_only"] = bool(
        gates["claim_ceiling_development_only"]
        and validated.get("claim_ceiling") == "development_path_evidence_only"
        and validated.get("question_pack", {}).get("evidence_role")
        == "development_regression_pack"
    )
    status = "accepted" if all(gates.values()) else "rejected"
    layer, route = _failure_route(gates)
    report = {
        "contract": ACCEPTANCE_CONTRACT,
        "status": status,
        "gates": gates,
        "task_evidence": task_evidence,
        "replay_receipt_sha256": (
            replay.get("receipt_sha256") if isinstance(replay, Mapping) else None
        ),
        "legacy_disposition_sha256": (
            legacy.get("disposition_sha256")
            if isinstance(legacy, Mapping)
            else None
        ),
        "errors": errors,
        "layer_diagnosis": None if status == "accepted" else layer,
        "next_routes": [route],
        "claim_ceiling": "development_path_evidence_only",
    }
    report["acceptance_sha256"] = _canonical_sha256(report)
    return report


class _FailIfCalledProduct:
    def __getattr__(self, name: str):
        raise AssertionError(f"accepted replay attempted Product Interface call: {name}")


def build_replay_receipt(
    *,
    contract: Mapping[str, object],
    execution_root: Path,
) -> dict[str, object]:
    """Prove accepted-answer replay returns without any runtime call or growth."""

    root = Path(execution_root)
    paths = _answer_paths(root)
    if set(paths) != set(TASK_IDS):
        raise ValueError("three accepted answers are required for replay")
    tasks: dict[str, dict] = {}
    for question_id in TASK_IDS:
        path = paths[question_id]
        run_root = _run_root(path)
        before = _tree_sha256(run_root)
        answer = _load_json(path)
        timing = answer.get("answer_timing_receipt")
        counts = timing.get("counts") if isinstance(timing, Mapping) else {}
        runner = MinimumRealPathRunner(
            product_interface=_FailIfCalledProduct(),
            contract=contract,
            output_root=path.parent.parent,
            clock=lambda: "2026-07-31T00:00:00Z",
        )
        replay = runner.resume_one(question_id)
        after = _tree_sha256(run_root)
        replay_timing = replay.get("answer_timing_receipt")
        replay_counts = (
            replay_timing.get("counts")
            if isinstance(replay_timing, Mapping)
            else {}
        )
        tasks[question_id] = {
            "before_tree_sha256": before,
            "after_tree_sha256": after,
            "model_call_growth": int(replay_counts.get("model", 0))
            - int(counts.get("model", 0)),
            "tool_call_growth": int(replay_counts.get("tool", 0))
            - int(counts.get("tool", 0)),
            "external_effect_growth": int(
                replay.get("executed_external_effects", 0)
            )
            - int(answer.get("executed_external_effects", 0)),
            "project_record_event_growth": 0,
        }
    receipt = {
        "contract": REPLAY_CONTRACT,
        "tasks": tasks,
        "status": (
            "passed"
            if all(
                row["before_tree_sha256"] == row["after_tree_sha256"]
                and all(
                    row[key] == 0
                    for key in (
                        "model_call_growth",
                        "tool_call_growth",
                        "external_effect_growth",
                        "project_record_event_growth",
                    )
                )
                for row in tasks.values()
            )
            else "failed"
        ),
    }
    receipt["receipt_sha256"] = _canonical_sha256(receipt)
    return receipt


def build_legacy_disposition(
    *,
    repository_root: Path,
    execution_root: Path,
) -> dict[str, object]:
    """Bind v3-v7 to tracked bytes and keep v7 explicitly unexecuted."""

    repo = Path(repository_root)
    root = Path(execution_root)
    trees = {}
    model_completed = 0
    for version in ("v3", "v4", "v5", "v6", "v7"):
        relative = Path(f"blind-execution-{version}")
        path = root / relative
        if not path.is_dir():
            raise ValueError(f"legacy development evidence is absent: {version}")
        tracked_path = path.relative_to(repo).as_posix()
        status = subprocess.run(
            [
                "git",
                "status",
                "--porcelain",
                "--untracked-files=all",
                "--",
                tracked_path,
            ],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        if status.stdout.strip():
            raise ValueError(f"legacy development evidence drifted: {version}")
        if version == "v7":
            for trace in path.rglob("*.jsonl"):
                for line in trace.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    value = json.loads(line)
                    model_completed += (
                        value.get("event_type") == "research.run.model.completed"
                    )
        trees[version] = {
            "path": relative.as_posix(),
            "tree_sha256": _tree_sha256(path),
            "status": (
                "parked_unexecuted"
                if version == "v7"
                else "immutable_development_evidence"
            ),
        }
    disposition = {
        "contract": LEGACY_CONTRACT,
        "trees": trees,
        "v7_model_completed_count": model_completed,
        "generalization_claim_permitted": False,
    }
    disposition["disposition_sha256"] = _canonical_sha256(disposition)
    return disposition
