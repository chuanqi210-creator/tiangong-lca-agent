"""Thin checkpoint/answer boundary for the three-task minimum real path."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Callable, Mapping

from lca_research_kernel.provider_capacity import (
    validate_provider_capacity_qualification,
)

from .minimum_real_path import (
    REPOSITORY_ROOT,
    validate_minimum_real_path_contract,
)


CHECKPOINT_CONTRACT = "minimum_path_checkpoint/v1"
ANSWER_CONTRACT = "minimum_path_accepted_answer/v1"
FORMAL_STATUSES = {
    "formal_result_accepted",
    "formal_limited_result_accepted",
}


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


def _load_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unreadable") from error
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return deepcopy(dict(value))


def _atomic_json(path: Path, value: Mapping[str, object], *, immutable: bool) -> None:
    content = _canonical_bytes(value) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if immutable and path.is_file():
        if path.read_bytes() != content:
            raise ValueError("accepted answer is immutable")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        if _canonical_bytes(_load_json(temporary, "staged research artifact")) != _canonical_bytes(value):
            raise ValueError("staged research artifact validation failed")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class MinimumRealPathRunner:
    """Run or resume one contract-bound task through the Product Interface."""

    def __init__(
        self,
        *,
        product_interface: object,
        contract: Mapping[str, object],
        output_root: Path,
        clock: Callable[[], str],
    ) -> None:
        pack = contract.get("question_pack")
        if not isinstance(pack, Mapping) or not isinstance(pack.get("path"), str):
            raise ValueError("minimum real path question-pack reference is absent")
        self.contract = validate_minimum_real_path_contract(
            contract,
            question_pack_path=REPOSITORY_ROOT / pack["path"],
        )
        self.product_interface = product_interface
        self.output_root = Path(output_root)
        self.clock = clock
        self.contract_sha256 = _canonical_sha256(self.contract)
        tasks = self.contract["tasks"]
        assert isinstance(tasks, list)
        self.tasks = {
            str(task["question_id"]): deepcopy(dict(task))
            for task in tasks
            if isinstance(task, Mapping)
        }

    def run_one(
        self,
        question_id: str,
        *,
        qualification: Mapping[str, object],
    ) -> dict[str, object]:
        validated = validate_provider_capacity_qualification(qualification)
        task = self._task(question_id)
        input_sha256 = self._input_sha256(task)
        qualification_sha256 = validated["receipt_sha256"]
        assert isinstance(qualification_sha256, str)
        checkpoint_path = self._checkpoint_path(question_id)
        answer_path = self._answer_path(question_id)

        if answer_path.is_file():
            return self._accepted_report(
                self._validate_answer(_load_json(answer_path, "accepted answer"))
            )
        if checkpoint_path.is_file():
            stored_checkpoint = _load_json(
                checkpoint_path,
                "minimum path checkpoint",
            )
            stored_qualification_sha256 = str(
                stored_checkpoint.get("qualification_sha256", "")
            )
            checkpoint = self._validate_checkpoint(
                stored_checkpoint,
                question_id=question_id,
                input_sha256=input_sha256,
                qualification_sha256=stored_qualification_sha256,
            )
            if checkpoint["status"] != "provider_capacity_deferred":
                if stored_qualification_sha256 != qualification_sha256:
                    raise ValueError(
                        "active research qualification identity drifted"
                    )
                return self._resume_checkpoint(checkpoint)
            if validated["status"] != "qualified":
                refreshed = self._checkpoint(
                    question_id=question_id,
                    research_id=str(checkpoint["research_id"]),
                    input_sha256=input_sha256,
                    qualification_sha256=qualification_sha256,
                    status="provider_capacity_deferred",
                    project_record_ref=None,
                    run_trace_ref=None,
                    answer_timing_receipt=None,
                    next_route=str(validated["next_route"]),
                    observed_boundary="provider_capacity_deferred",
                )
                _atomic_json(checkpoint_path, refreshed, immutable=False)
                return self._checkpoint_report(refreshed)

        if validated["status"] != "qualified":
            checkpoint = self._checkpoint(
                question_id=question_id,
                research_id=self._research_id(question_id, input_sha256),
                input_sha256=input_sha256,
                qualification_sha256=qualification_sha256,
                status="provider_capacity_deferred",
                project_record_ref=None,
                run_trace_ref=None,
                answer_timing_receipt=None,
                next_route=str(validated["next_route"]),
                observed_boundary="provider_capacity_deferred",
            )
            _atomic_json(checkpoint_path, checkpoint, immutable=False)
            return self._checkpoint_report(checkpoint)

        self._ensure_qualification_capacity(
            qualification_sha256,
            int(validated["qualified_task_count"]),
        )
        research_id = self._research_id(question_id, input_sha256)
        pack = self.contract["question_pack"]
        assert isinstance(pack, Mapping)
        self.product_interface.start_research(
            {
                "contract": "lca_research_kernel.research_start/v1",
                "research_id": research_id,
                "title": "Source-bound minimum-path research",
                "objective": task["question_body"],
                "source_refs": [
                    {
                        "path": pack["path"],
                        "sha256": pack["sha256"],
                        "authority": "user_supplied_project_input",
                        "object_type": "real_question",
                        "official_object_ids": [],
                        "content": {
                            "contract": "real_question_material/v1",
                            "question_body": task["question_body"],
                            "page_number": task["page_number"],
                            "image_refs": deepcopy(task["image_refs"]),
                            "trajectory_role": task["trajectory_role"],
                        },
                    }
                ],
                "received_at": self.clock(),
            }
        )
        status_view = self.product_interface.read_status(research_id)
        result_view = self.product_interface.read_result(research_id)
        return self._record_observation(
            question_id=question_id,
            research_id=research_id,
            input_sha256=input_sha256,
            qualification_sha256=qualification_sha256,
            status_view=status_view,
            result_view=result_view,
        )

    def resume_one(self, question_id: str) -> dict[str, object]:
        task = self._task(question_id)
        answer_path = self._answer_path(question_id)
        if answer_path.is_file():
            return self._accepted_report(
                self._validate_answer(_load_json(answer_path, "accepted answer"))
            )
        checkpoint = _load_json(
            self._checkpoint_path(question_id),
            "minimum path checkpoint",
        )
        self._validate_checkpoint(
            checkpoint,
            question_id=question_id,
            input_sha256=self._input_sha256(task),
            qualification_sha256=str(
                checkpoint.get("qualification_sha256", "")
            ),
        )
        return self._resume_checkpoint(checkpoint)

    def _resume_checkpoint(self, checkpoint: Mapping[str, object]) -> dict:
        if checkpoint["status"] == "provider_capacity_deferred":
            return self._checkpoint_report(checkpoint)
        question_id = str(checkpoint["question_id"])
        research_id = str(checkpoint["research_id"])
        resume = getattr(self.product_interface, "resume_research", None)
        status_view = (
            resume(research_id)
            if callable(resume)
            else self.product_interface.read_status(research_id)
        )
        return self._record_observation(
            question_id=question_id,
            research_id=research_id,
            input_sha256=str(checkpoint["input_sha256"]),
            qualification_sha256=str(checkpoint["qualification_sha256"]),
            status_view=status_view,
            result_view=self.product_interface.read_result(research_id),
        )

    def _record_observation(
        self,
        *,
        question_id: str,
        research_id: str,
        input_sha256: str,
        qualification_sha256: str,
        status_view: Mapping[str, object],
        result_view: Mapping[str, object],
    ) -> dict:
        project_record_ref = result_view.get(
            "project_record_ref", status_view.get("project_record_ref")
        )
        run_trace_ref = result_view.get(
            "run_trace_ref", status_view.get("run_trace_ref")
        )
        timing = result_view.get(
            "answer_timing_receipt",
            status_view.get("answer_timing_receipt"),
        )
        if result_view.get("status") != "accepted":
            checkpoint = self._checkpoint(
                question_id=question_id,
                research_id=research_id,
                input_sha256=input_sha256,
                qualification_sha256=qualification_sha256,
                status="research_active",
                project_record_ref=project_record_ref,
                run_trace_ref=run_trace_ref,
                answer_timing_receipt=timing,
                next_route="resume-minimum-path-research",
                observed_boundary=str(
                    status_view.get("boundary", "research_active")
                ),
            )
            _atomic_json(
                self._checkpoint_path(question_id),
                checkpoint,
                immutable=False,
            )
            return self._checkpoint_report(checkpoint)

        result = result_view.get("result")
        if not isinstance(result, Mapping) or result.get("status") not in FORMAL_STATUSES:
            raise ValueError("accepted Project Record result status is invalid")
        result_status = str(result["status"])
        self._validate_formal_result(result)
        self._validate_zero_effects(result_view)
        answer = {
            "contract": ANSWER_CONTRACT,
            "question_id": question_id,
            "research_id": research_id,
            "input_sha256": input_sha256,
            "contract_sha256": self.contract_sha256,
            "qualification_sha256": qualification_sha256,
            "status": result_status,
            "result": deepcopy(dict(result)),
            "result_sha256": _canonical_sha256(result),
            "project_record_ref": deepcopy(project_record_ref),
            "run_trace_ref": deepcopy(run_trace_ref),
            "answer_timing_receipt": deepcopy(timing),
            "executed_external_effects": 0,
            "accepted_at": self.clock(),
            "next_route": "record-minimum-path-result",
        }
        answer["accepted_answer_sha256"] = _canonical_sha256(answer)
        self._validate_answer(answer)
        _atomic_json(self._answer_path(question_id), answer, immutable=True)
        checkpoint = self._checkpoint(
            question_id=question_id,
            research_id=research_id,
            input_sha256=input_sha256,
            qualification_sha256=qualification_sha256,
            status=result_status,
            project_record_ref=project_record_ref,
            run_trace_ref=run_trace_ref,
            answer_timing_receipt=timing,
            next_route="record-minimum-path-result",
            observed_boundary=str(
                status_view.get("boundary", "research_result_accepted")
            ),
        )
        _atomic_json(
            self._checkpoint_path(question_id),
            checkpoint,
            immutable=False,
        )
        return self._accepted_report(answer)

    def _validate_formal_result(self, result: Mapping[str, object]) -> None:
        if not isinstance(result.get("source_basis"), list):
            raise ValueError("formal result source basis is invalid")
        if result["status"] == "formal_limited_result_accepted" and (
            not isinstance(result.get("missing_facts"), list)
            or not result["missing_facts"]
            or not isinstance(result.get("claim_ceiling"), str)
            or not result["claim_ceiling"]
            or not isinstance(result.get("resume_conditions"), list)
            or not result["resume_conditions"]
            or (
                "computed_values" in result
                and result.get("computed_values") != []
            )
        ):
            raise ValueError("limited result scientific gap fields are invalid")
        if not isinstance(result.get("claim_ceiling"), str) or not result[
            "claim_ceiling"
        ]:
            raise ValueError("formal result claim ceiling is invalid")
        if result["status"] != "formal_limited_result_accepted":
            return

    @staticmethod
    def _validate_zero_effects(result_view: Mapping[str, object]) -> None:
        effect = result_view.get("effect_receipt")
        counts = effect.get("effect_counts") if isinstance(effect, Mapping) else None
        if (
            not isinstance(counts, Mapping)
            or counts.get("external_write") != 0
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value != 0
                for value in counts.values()
            )
        ):
            raise ValueError("accepted result effect receipt is unsafe")

    def _task(self, question_id: str) -> dict:
        if not isinstance(question_id, str) or question_id not in self.tasks:
            raise ValueError("minimum real path question is not allowed")
        return deepcopy(self.tasks[question_id])

    def _input_sha256(self, task: Mapping[str, object]) -> str:
        return _canonical_sha256(
            {
                "task": deepcopy(dict(task)),
                "question_pack": deepcopy(self.contract["question_pack"]),
            }
        )

    @staticmethod
    def _research_id(question_id: str, input_sha256: str) -> str:
        return f"minimum-real-path-{question_id.lower()}-{input_sha256[:16]}"

    def _ensure_qualification_capacity(
        self, qualification_sha256: str, qualified_task_count: int
    ) -> None:
        used = 0
        if self.output_root.is_dir():
            for path in self.output_root.glob("*/checkpoint.json"):
                checkpoint = _load_json(path, "minimum path checkpoint")
                if (
                    checkpoint.get("qualification_sha256")
                    == qualification_sha256
                    and checkpoint.get("status")
                    != "provider_capacity_deferred"
                ):
                    used += 1
        if used >= qualified_task_count:
            raise ValueError("provider qualification capacity is exhausted")

    def _checkpoint(
        self,
        *,
        question_id: str,
        research_id: str,
        input_sha256: str,
        qualification_sha256: str,
        status: str,
        project_record_ref: object,
        run_trace_ref: object,
        answer_timing_receipt: object,
        next_route: str,
        observed_boundary: str,
    ) -> dict:
        checkpoint = {
            "contract": CHECKPOINT_CONTRACT,
            "question_id": question_id,
            "research_id": research_id,
            "input_sha256": input_sha256,
            "contract_sha256": self.contract_sha256,
            "qualification_sha256": qualification_sha256,
            "status": status,
            "project_record_ref": deepcopy(project_record_ref),
            "run_trace_ref": deepcopy(run_trace_ref),
            "answer_timing_receipt": deepcopy(answer_timing_receipt),
            "executed_external_effects": 0,
            "next_route": next_route,
            "observed_boundary": observed_boundary,
        }
        checkpoint["checkpoint_sha256"] = _canonical_sha256(checkpoint)
        return checkpoint

    def _validate_checkpoint(
        self,
        checkpoint: Mapping[str, object],
        *,
        question_id: str,
        input_sha256: str,
        qualification_sha256: str,
    ) -> dict:
        expected_hash = _canonical_sha256(
            {
                key: deepcopy(value)
                for key, value in checkpoint.items()
                if key != "checkpoint_sha256"
            }
        )
        if (
            checkpoint.get("contract") != CHECKPOINT_CONTRACT
            or checkpoint.get("question_id") != question_id
            or checkpoint.get("input_sha256") != input_sha256
            or checkpoint.get("contract_sha256") != self.contract_sha256
            or checkpoint.get("qualification_sha256")
            != qualification_sha256
            or checkpoint.get("status")
            not in {
                "provider_capacity_deferred",
                "research_active",
                *FORMAL_STATUSES,
            }
            or checkpoint.get("executed_external_effects") != 0
            or not isinstance(checkpoint.get("next_route"), str)
            or not isinstance(checkpoint.get("observed_boundary"), str)
            or checkpoint.get("checkpoint_sha256") != expected_hash
            or not self._is_sha256(checkpoint.get("qualification_sha256"))
        ):
            raise ValueError("minimum path checkpoint identity drifted")
        return deepcopy(dict(checkpoint))

    def _validate_answer(self, answer: Mapping[str, object]) -> dict:
        expected = _canonical_sha256(
            {
                key: deepcopy(value)
                for key, value in answer.items()
                if key != "accepted_answer_sha256"
            }
        )
        if (
            answer.get("contract") != ANSWER_CONTRACT
            or answer.get("question_id") not in self.tasks
            or answer.get("input_sha256")
            != self._input_sha256(
                self.tasks[str(answer.get("question_id"))]
            )
            or answer.get("contract_sha256") != self.contract_sha256
            or answer.get("status") not in FORMAL_STATUSES
            or answer.get("executed_external_effects") != 0
            or answer.get("accepted_answer_sha256") != expected
            or not isinstance(answer.get("result"), Mapping)
            or answer.get("result_sha256")
            != _canonical_sha256(answer["result"])
        ):
            raise ValueError("accepted answer identity drifted")
        self._validate_formal_result(answer["result"])
        return deepcopy(dict(answer))

    @staticmethod
    def _is_sha256(value: object) -> bool:
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
        )

    def _checkpoint_report(self, checkpoint: Mapping[str, object]) -> dict:
        path = self._checkpoint_path(str(checkpoint["question_id"]))
        return {
            "question_id": checkpoint["question_id"],
            "research_id": checkpoint["research_id"],
            "status": checkpoint["status"],
            "checkpoint_ref": {
                "path": path.as_posix(),
                "sha256": _file_sha256(path),
            },
            "project_record_ref": deepcopy(checkpoint["project_record_ref"]),
            "run_trace_ref": deepcopy(checkpoint["run_trace_ref"]),
            "answer_timing_receipt": deepcopy(
                checkpoint["answer_timing_receipt"]
            ),
            "executed_external_effects": 0,
            "next_route": checkpoint["next_route"],
            "observed_boundary": checkpoint["observed_boundary"],
        }

    def _accepted_report(self, answer: Mapping[str, object]) -> dict:
        return {
            "question_id": answer["question_id"],
            "research_id": answer["research_id"],
            "status": answer["status"],
            "accepted_answer_ref": {
                "path": self._answer_path(str(answer["question_id"])).as_posix(),
                "sha256": _file_sha256(
                    self._answer_path(str(answer["question_id"]))
                ),
            },
            "accepted_answer_sha256": answer["accepted_answer_sha256"],
            "project_record_ref": deepcopy(answer["project_record_ref"]),
            "run_trace_ref": deepcopy(answer["run_trace_ref"]),
            "answer_timing_receipt": deepcopy(
                answer["answer_timing_receipt"]
            ),
            "executed_external_effects": 0,
            "next_route": answer["next_route"],
        }

    def _checkpoint_path(self, question_id: str) -> Path:
        return self.output_root / question_id / "checkpoint.json"

    def _answer_path(self, question_id: str) -> Path:
        return self.output_root / question_id / "accepted-answer.json"
