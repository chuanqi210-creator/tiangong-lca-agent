"""Freeze and execute one answer-quality held-out fixture without oracle leakage."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Callable, Mapping

from lca_research_kernel.adaptive_answer import AdaptiveAnswerPolicy


CANDIDATE_CONTRACT = "adaptive_answer_heldout_candidates/v1"
ORACLE_CONTRACT = "adaptive_answer_heldout_oracles/v1"
TASK_CONTRACT = "adaptive_answer_heldout_task/v1"
FROZEN_ORACLE_CONTRACT = "adaptive_answer_heldout_oracle/v1"
RECEIPT_CONTRACT = "adaptive_answer_heldout_freeze_receipt/v1"
RUN_CONTRACT = "adaptive_answer_heldout_run/v1"
REPLAY_CONTRACT = "adaptive_answer_heldout_replay_receipt/v1"
TASK_EVALUATION_CONTRACT = "adaptive_answer_heldout_evaluation_contract/v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_RUNTIME_EFFECTS = frozenset(
    {
        "credential_request",
        "external_write",
        "publication",
        "payment",
        "irreversible_action",
    }
)


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _read_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unreadable") from error
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return deepcopy(dict(value))


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    content = canonical_bytes(value) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.is_symlink() or path.read_bytes() != content:
            raise ValueError("frozen held-out artifact is immutable")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _validated_candidates(path: Path) -> tuple[list[str], list[dict]]:
    value = _read_object(path, "held-out candidate pool")
    excluded = value.get("excluded_identities")
    candidates = value.get("candidates")
    if (
        value.get("contract") != CANDIDATE_CONTRACT
        or not isinstance(excluded, list)
        or any(not isinstance(item, str) or not item for item in excluded)
        or not isinstance(candidates, list)
        or not candidates
    ):
        raise ValueError("held-out candidate pool contract is invalid")
    excluded_set = set(excluded)
    seen: set[str] = set()
    validated: list[dict] = []
    for row in candidates:
        source = row.get("source") if isinstance(row, Mapping) else None
        candidate_id = row.get("candidate_id") if isinstance(row, Mapping) else None
        if (
            not isinstance(candidate_id, str)
            or not candidate_id
            or candidate_id in seen
            or candidate_id in excluded_set
        ):
            if candidate_id in excluded_set:
                raise ValueError("historical candidate entered held-out pool")
            raise ValueError("held-out candidate identity is invalid")
        if (
            not isinstance(row.get("question"), str)
            or not row["question"].strip()
            or not isinstance(source, Mapping)
            or not isinstance(source.get("url"), str)
            or not source["url"].startswith("https://")
            or not isinstance(source.get("sha256"), str)
            or _SHA256.fullmatch(source["sha256"]) is None
            or not isinstance(source.get("material"), str)
            or not source["material"].strip()
            or not isinstance(source.get("version"), str)
            or not source["version"].strip()
            or not isinstance(source.get("identity"), str)
            or not source["identity"].strip()
            or source.get("parse_location") is not None
            and (
                not isinstance(source.get("parse_location"), str)
                or not source["parse_location"].strip()
            )
            or source.get("scenario") is not None
            and not isinstance(source.get("scenario"), Mapping)
        ):
            raise ValueError("held-out candidate source is invalid")
        seen.add(candidate_id)
        validated.append(deepcopy(dict(row)))
    return sorted(excluded_set), validated


def select_candidate(pool_path: Path, seed_sha256: str) -> dict:
    """Select one unseen candidate deterministically from frozen identities."""

    if not isinstance(seed_sha256, str) or _SHA256.fullmatch(seed_sha256) is None:
        raise ValueError("held-out selection seed is invalid")
    _, candidates = _validated_candidates(Path(pool_path))
    ranked = sorted(
        candidates,
        key=lambda row: hashlib.sha256(
            (
                seed_sha256
                + ":"
                + str(row["candidate_id"])
                + ":"
                + str(row["source"]["sha256"])
            ).encode("utf-8")
        ).hexdigest(),
    )
    return deepcopy(ranked[0])


def _isolated_roots(execution_root: Path, grader_root: Path) -> tuple[Path, Path]:
    execution = Path(execution_root).resolve()
    grader = Path(grader_root).resolve()
    if execution == grader:
        raise ValueError("held-out execution and grader roots must be isolated")
    for left, right in ((execution, grader), (grader, execution)):
        try:
            left.relative_to(right)
        except ValueError:
            continue
        raise ValueError("held-out execution and grader roots must be isolated")
    return execution, grader


def freeze_fixture(
    pool_path: Path,
    oracle_catalog_path: Path,
    *,
    seed_sha256: str,
    execution_root: Path,
    grader_root: Path,
) -> dict[str, object]:
    """Write immutable execution and grader packages into disjoint roots."""

    execution, grader = _isolated_roots(execution_root, grader_root)
    selected = select_candidate(pool_path, seed_sha256)
    quality = AdaptiveAnswerPolicy.load(_ROOT)
    catalog = _read_object(oracle_catalog_path, "held-out oracle catalog")
    oracles = catalog.get("oracles")
    if catalog.get("contract") != ORACLE_CONTRACT or not isinstance(oracles, Mapping):
        raise ValueError("held-out oracle catalog contract is invalid")
    oracle = oracles.get(selected["candidate_id"])
    if not isinstance(oracle, Mapping):
        raise ValueError("selected held-out oracle is absent")
    task = {
        "contract": TASK_CONTRACT,
        "candidate_id": selected["candidate_id"],
        "question": selected["question"],
        "source": deepcopy(selected["source"]),
        "evaluation_contract": {
            "contract": TASK_EVALUATION_CONTRACT,
            "adaptive_answer_contract_sha256": quality.contract_sha256,
            "expected_research_depth": "targeted_source",
            "hard_gates": deepcopy(quality.contract["hard_gates"]),
            "weights": deepcopy(
                quality.contract["rubric_profiles"]["targeted_source"]
            ),
            "score_order": deepcopy(quality.contract["score_order"]),
            "pass_threshold": 85,
            "runtime_limits": {
                "model_completed_max": 3,
                "capability_completed_max": 2,
                "official_external_read_max": 2,
            },
        },
        "selection_seed_sha256": seed_sha256,
        "candidate_pool_sha256": hashlib.sha256(Path(pool_path).read_bytes()).hexdigest(),
        "allowed_effects": ["local_read", "local_write", "external_read"],
        "forbidden_effects": [
            "credential_request",
            "external_write",
            "publication",
            "payment",
            "irreversible_action",
        ],
    }
    task["evaluation_contract"]["evaluation_sha256"] = canonical_sha256(
        task["evaluation_contract"]
    )
    frozen_oracle = {
        "contract": FROZEN_ORACLE_CONTRACT,
        "candidate_id": selected["candidate_id"],
        "oracle": deepcopy(dict(oracle)),
        "task_sha256": canonical_sha256(task),
    }
    task_path = execution / "task.json"
    oracle_path = grader / "oracle.json"
    _atomic_json(task_path, task)
    _atomic_json(oracle_path, frozen_oracle)
    receipt = {
        "contract": RECEIPT_CONTRACT,
        "candidate_id": selected["candidate_id"],
        "selection_seed_sha256": seed_sha256,
        "task_sha256": canonical_sha256(task),
        "execution_effects": 0,
        "external_writes": 0,
        "next_route": "run-independent-adaptive-answer-heldout",
    }
    _atomic_json(execution / "freeze-receipt.json", receipt)
    _atomic_json(
        grader / "seal-receipt.json",
        {
            "contract": "adaptive_answer_heldout_oracle_seal/v1",
            "candidate_id": selected["candidate_id"],
            "task_sha256": canonical_sha256(task),
            "oracle_sha256": canonical_sha256(frozen_oracle),
        },
    )
    return receipt


def build_replay_receipt(
    before: Mapping[str, object], after: Mapping[str, object]
) -> dict[str, object]:
    """Fail closed unless replay reused Project truth and runtime evidence."""

    count_fields = (
        "project_event_count",
        "run_trace_event_count",
        "model_completed_count",
        "tool_completed_count",
        "capability_completed_count",
        "forbidden_effect_count",
    )
    for field in count_fields:
        if (
            isinstance(before.get(field), bool)
            or not isinstance(before.get(field), int)
            or isinstance(after.get(field), bool)
            or not isinstance(after.get(field), int)
        ):
            raise ValueError("replay snapshot count is invalid")
    growth = {
        field.removesuffix("_count") + "_growth": int(after[field]) - int(before[field])
        for field in count_fields
    }
    projection_same = (
        isinstance(before.get("project_record_sha256"), str)
        and before.get("project_record_sha256")
        == after.get("project_record_sha256")
    )
    raw_evidence_same = all(
        isinstance(before.get(field), str)
        and before.get(field) == after.get(field)
        for field in (
            "project_event_bytes_sha256",
            "run_trace_bytes_sha256",
        )
    )
    if (
        any(value != 0 for value in growth.values())
        or not projection_same
        or not raw_evidence_same
    ):
        raise ValueError("replay caused evidence growth or Project Record drift")
    receipt = {
        "contract": REPLAY_CONTRACT,
        **growth,
        "project_record_same": projection_same,
        "raw_evidence_bytes_same": raw_evidence_same,
        "before_snapshot_sha256": canonical_sha256(before),
        "after_snapshot_sha256": canonical_sha256(after),
        "status": "passed",
        "next_route": "grade-independent-adaptive-answer-heldout",
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def runtime_evidence_snapshot(workspace: Path, run_id: str) -> dict[str, object]:
    """Measure immutable Project Record and full Run Trace bytes for replay."""

    workspace = Path(workspace)
    project_events = workspace / "research-project-events.jsonl"
    run_trace = workspace / "run-traces" / f"{run_id}.jsonl"
    project_record = workspace / "project-record.json"
    project_bytes = project_events.read_bytes() if project_events.is_file() else b""
    trace_bytes = run_trace.read_bytes() if run_trace.is_file() else b""
    project_rows = [
        json.loads(line)
        for line in project_bytes.decode("utf-8").splitlines()
        if line.strip()
    ]
    trace_rows = [
        json.loads(line)
        for line in trace_bytes.decode("utf-8").splitlines()
        if line.strip()
    ]
    capability_completed = [
        row
        for row in trace_rows
        if row.get("event_type") == "research.run.capability.completed"
    ]
    forbidden = sum(
        isinstance(row.get("payload"), Mapping)
        and row["payload"].get("effect") in FORBIDDEN_RUNTIME_EFFECTS
        for row in trace_rows
        if row.get("event_type")
        in {
            "research.run.capability.requested",
            "research.run.capability.completed",
        }
    )
    snapshot = {
        "project_event_count": len(project_rows),
        "run_trace_event_count": len(trace_rows),
        "model_completed_count": sum(
            row.get("event_type") == "research.run.model.completed"
            for row in trace_rows
        ),
        "tool_completed_count": sum(
            row.get("event_type") == "research.run.tool.completed"
            for row in trace_rows
        ),
        "capability_completed_count": len(capability_completed),
        "forbidden_effect_count": forbidden,
        "project_event_bytes_sha256": hashlib.sha256(project_bytes).hexdigest(),
        "run_trace_bytes_sha256": hashlib.sha256(trace_bytes).hexdigest(),
        "project_record_sha256": (
            hashlib.sha256(project_record.read_bytes()).hexdigest()
            if project_record.is_file()
            else None
        ),
    }
    snapshot["snapshot_sha256"] = canonical_sha256(snapshot)
    return snapshot


class AdaptiveAnswerHeldoutRunner:
    """Thin runner that exposes no route choice beyond ProductInterface actions."""

    def __init__(
        self,
        *,
        product_interface: object,
        task_path: Path,
        output_root: Path,
        clock: Callable[[], str],
        evidence_snapshot: Callable[[], Mapping[str, object]] | None = None,
    ) -> None:
        self.product_interface = product_interface
        self.task_path = Path(task_path)
        self.output_root = Path(output_root)
        self.clock = clock
        self.evidence_snapshot = evidence_snapshot
        self.task = _read_object(self.task_path, "held-out execution task")
        if self.task.get("contract") != TASK_CONTRACT:
            raise ValueError("held-out execution task contract is invalid")
        self.task_sha256 = canonical_sha256(self.task)
        expected = _read_object(
            self.task_path.parent / "freeze-receipt.json",
            "held-out freeze receipt",
        )
        if expected.get("task_sha256") != self.task_sha256:
            raise ValueError("held-out execution task drifted")

    @property
    def research_id(self) -> str:
        return f"adaptive-heldout-{self.task_sha256[:24]}"

    def run_or_resume(self) -> dict[str, object]:
        path = self.output_root / "run.json"
        if path.is_file():
            value = _read_object(path, "held-out run")
            if (
                value.get("contract") != RUN_CONTRACT
                or value.get("task_sha256") != self.task_sha256
            ):
                raise ValueError("held-out run identity drifted")
            replay = self.product_interface.read_result(self.research_id)
            if canonical_sha256(replay) != value.get("result_view_sha256"):
                raise ValueError("held-out replay result drifted")
            return deepcopy(value)

        source = self.task.get("source")
        assert isinstance(source, Mapping)
        scenario = source.get("scenario")
        scenario = deepcopy(dict(scenario)) if isinstance(scenario, Mapping) else {}
        source_content = {
            "contract": "real_question_material/v1",
            "question_body": self.task["question"],
            "official_source_url": source["url"],
            "source_version": source["version"],
            "requested_official_parse_location": source.get(
                "parse_location", "Article 2 scope"
            ),
            **scenario,
        }
        scenario_sha256 = canonical_sha256(source_content)
        request = {
            "contract": "lca_research_kernel.research_start/v1",
            "research_id": self.research_id,
            "title": "Independent source-bound adaptive answer evaluation",
            "objective": self.task["question"],
            "source_refs": [
                {
                    "path": "urn:research-input:" + self.task_sha256,
                    "sha256": scenario_sha256,
                    "authority": "user_supplied_project_input",
                    "object_type": "research_question_with_official_locator",
                    "content": source_content,
                }
            ],
            "received_at": self.clock(),
        }
        try:
            status = self.product_interface.read_status(self.research_id)
        except ValueError:
            self.product_interface.start_research(request)
            status = self.product_interface.read_status(self.research_id)
        result = self.product_interface.read_result(self.research_id)
        evaluation_outcome = (
            "formal_result_accepted"
            if result.get("status") == "accepted"
            else "formal_result_not_accepted"
        )
        run = {
            "contract": RUN_CONTRACT,
            "candidate_id": self.task["candidate_id"],
            "research_id": self.research_id,
            "task_sha256": self.task_sha256,
            "status_view": deepcopy(status),
            "result_view": deepcopy(result),
            "result_view_sha256": canonical_sha256(result),
            "evaluation_outcome": evaluation_outcome,
            "executed_external_effects": 0,
            "runtime_evidence": (
                deepcopy(dict(self.evidence_snapshot()))
                if self.evidence_snapshot is not None
                else None
            ),
            "completed_at": self.clock(),
            "next_route": "grade-independent-adaptive-answer-heldout",
        }
        _atomic_json(path, run)
        return run
