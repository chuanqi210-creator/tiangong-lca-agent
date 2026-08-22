"""Local four-case acceptance harness for the production Research Director."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Dict, Mapping, Sequence

from .cli import main as cli_main
from .project import ResearchProject


JsonObject = Dict[str, object]
ACCEPTANCE_CONTRACT = "lca_research_kernel.research_director_acceptance/v1"
FORBIDDEN_EFFECT_COUNTS = {
    "credential_access": 0,
    "payment": 0,
    "publication": 0,
    "external_write": 0,
    "irreversible": 0,
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _pretty_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_json(path: Path, value: object, *, compact: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _canonical_bytes(value) if compact else _pretty_bytes(value)
    path.write_bytes(content)
    return path


def _cli_advance(workspace: Path, input_path: Path) -> JsonObject:
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        return_code = cli_main(
            [
                "director-advance",
                "--workspace",
                str(workspace),
                "--input",
                str(input_path),
            ]
        )
    if return_code != 0:
        raise RuntimeError(f"director-advance failed with code {return_code}")
    value = json.loads(stream.getvalue())
    if not isinstance(value, dict):
        raise RuntimeError("director-advance returned a non-object")
    return value


class _Fixture:
    def __init__(self, workspace: Path, project_id: str) -> None:
        self.workspace = workspace
        self.project = ResearchProject.create(
            workspace,
            project_id=project_id,
            title="Research Director acceptance fixture",
            objective="Prove the source-bound production Director control path.",
        )
        self.authority = self.register(
            "inputs/authority.json",
            {"authority": "TianGong", "role": "first_authority"},
            "authority_receipt",
        )
        self.source = self.register(
            "inputs/source.json",
            {"finding": "source-bound acceptance fixture"},
            "evidence",
        )
        self.second_source = self.register(
            "inputs/source-2.json",
            {"finding": "independent source-bound challenge"},
            "evidence",
        )
        self.lifecycle = self.register(
            "inputs/lifecycle.json",
            {"goal": "research-director-continuous-autonomy", "status": "active"},
            "lifecycle_basis",
        )
        self.payload_value = {"operation": "write one verified project-local result"}
        self.payload = self.register(
            "inputs/operation-payload.json",
            self.payload_value,
            "prepared_payload",
            compact=True,
        )

    def register(
        self, relative: str, value: object, role: str, *, compact: bool = False
    ) -> JsonObject:
        path = _write_json(self.workspace / relative, value, compact=compact)
        content = path.read_bytes()
        ref: JsonObject = {
            "role": role,
            "path": relative,
            "sha256": _sha256(content),
            "bytes": len(content),
        }
        self.project.record(
            "artifact.registered",
            {
                "artifact_id": f"director-acceptance-{role}-{ref['sha256'][:12]}",
                "activity_id": "research-director-acceptance-fixture",
                "skill_ref": "research-director-acceptance/v1",
                "artifact_role": role,
                "path": relative,
                "workspace_path": relative,
                "sha256": ref["sha256"],
                "bytes": ref["bytes"],
            },
            actor="controller",
            idempotency_key=f"director-acceptance-fixture:{relative}",
        )
        return ref

    def work_item(self, *, disputed: bool = False, external: bool = False) -> JsonObject:
        evidence = {
            "reason": "The registered acceptance procedure is deterministic.",
            "source_refs": [self.source],
        }
        value: JsonObject = {
            "contract": "lca_research_kernel.research_director_work_item/v1",
            "work_item_id": "work-item-001",
            "objective": "Produce and validate one source-bound local result.",
            "lifecycle_basis_ref": self.lifecycle,
            "authority_receipt_ref": self.authority,
            "input_artifacts": [self.source],
            "outcome_contract": {
                "contract": "research_director.acceptance_result/v1",
                "decision_importance": "low",
                "acceptance_checks": ["result_is_source_bound"],
                "reason": "The fixture has bounded decision consequence.",
                "source_refs": [self.source],
            },
            "claims": [
                {
                    "claim_id": "claim-1",
                    "statement": "The fixture procedure is registered.",
                    "kind": "fact",
                    "authority": "project_evidence",
                    "voteable": False,
                    "source_refs": [self.source],
                }
            ],
            "risks": [
                {
                    "risk_id": "risk-1",
                    "statement": "Artifact drift invalidates the result.",
                    "source_refs": [self.source],
                }
            ],
            "procedure_evidence": {"determinism": "high", **evidence},
            "dispute_evidence": {
                "level": "low",
                "reason": "No material competing interpretation is registered.",
                "source_refs": [self.source],
            },
            "dependency_groups": {
                "strength": "high",
                "groups": [
                    {"group_id": "serial-source", "source_refs": [self.source]}
                ],
                "reason": "The activity is serial and strongly ordered.",
                "source_refs": [self.source],
            },
            "review_requirement": {
                "adversarial_value": "low",
                "clean_acceptance_required": False,
                "reason": "Deterministic checks are sufficient.",
                "source_refs": [self.source],
            },
            "isolation_requirement": {
                "value": "low",
                "reason": "No context isolation is needed.",
                "source_refs": [self.source],
            },
            "flip_conditions": [
                {
                    "condition_id": "flip-1",
                    "statement": "The registered artifact drifts.",
                    "potential": "low",
                    "source_refs": [self.source],
                }
            ],
            "operation_intent": {
                "contract": "lca_research_kernel.operation_intent/v1",
                "operation_id": "operation-001",
                "effect": "external_write" if external else "local_write",
                "responsibility_tags": ["scientific_judgment"],
                "target": "project-local-result",
                "scope": "work-item:work-item-001",
                "payload": self.payload_value,
                "payload_ref": {
                    "path": self.payload["path"],
                    "sha256": self.payload["sha256"],
                    "version": "v1",
                },
                "verification": "run the declared acceptance check",
                "compensation": "remove only the generated local result",
                "resume_route": "resume-director-after-local-result",
            },
            "stop_policy": {
                "stop_conditions": ["result_is_source_bound"],
                "worker_stop_condition": "return after the contracted artifact",
                "max_transitions": 8,
                "max_no_progress_rounds": 2,
                "max_route_failures": 3,
                "source_refs": [self.source],
            },
        }
        if disputed:
            value["input_artifacts"].append(self.second_source)
            value["dependency_groups"]["groups"].append(
                {
                    "group_id": "independent-source",
                    "source_refs": [self.second_source],
                }
            )
            value["dependency_groups"]["source_refs"].append(self.second_source)
            value["procedure_evidence"]["determinism"] = "unknown"
            value["dispute_evidence"]["level"] = "high"
            value["review_requirement"]["adversarial_value"] = "high"
            value["review_requirement"]["clean_acceptance_required"] = True
            value["isolation_requirement"]["value"] = "high"
        return value

    def write_work_item(self, *, disputed: bool = False, external: bool = False) -> Path:
        return _write_json(
            self.workspace / "inputs/director-work-item.json",
            self.work_item(disputed=disputed, external=external),
        )

    def write_internal_result(self, started: Mapping[str, object], result: object) -> Path:
        directive = started["directive"]
        return _write_json(
            self.workspace / "inputs/director-internal-result.json",
            {
                "contract": "lca_research_kernel.research_director_internal_result/v1",
                "work_item_ref": directive["work_item_ref"],
                "packet_id": directive["packet_id"],
                "packet_sha256": directive["packet_sha256"],
                "status": "completed",
                "statement": "Internal work completed and passed its stop condition.",
                "result": result,
            },
        )

    def worker_output(self, name: str, value: object) -> JsonObject:
        path = _write_json(self.workspace / "outputs" / f"{name}.json", value, compact=True)
        content = path.read_bytes()
        return {
            "role": "worker_output",
            "path": path.relative_to(self.workspace).as_posix(),
            "sha256": _sha256(content),
            "bytes": len(content),
        }

    def worker_result(
        self,
        started: Mapping[str, object],
        *,
        name: str,
        result_kind: str,
        responsibility: str,
        resource: str,
        statement: str,
        validation_scope: object = None,
    ) -> JsonObject:
        packet = started["directive"]
        artifact = self.worker_output(name, {"statement": statement})
        context = {
            "agenda_revision": packet["agenda_revision"],
            "cell_revision": packet["cell_revision"],
            "allowed_effects": packet["allowed_effects"],
            "output_contract": packet["output_contract"],
            "stop_condition": packet["stop_condition"],
            "result_kind": result_kind,
            "responsibility": responsibility,
            "statement": statement,
            "validation_scope": validation_scope,
        }
        row: JsonObject = {
            "agenda_revision": packet["agenda_revision"],
            "cell_revision": packet["cell_revision"],
            "result_kind": result_kind,
            "responsibility": responsibility,
            "source_bindings": packet["input_refs"],
            "allowed_effects": packet["allowed_effects"],
            "output_contract": packet["output_contract"],
            "stop_condition_met": True,
            "artifact_ref": artifact,
            "statement": statement,
            "validation_scope": validation_scope,
            "independence_receipt": {
                "resource_kind": "isolated_worker_context",
                "opaque_resource_id_sha256": _sha256(resource.encode("utf-8")),
                "context_manifest_sha256": _sha256(_canonical_bytes(context)),
                "source_manifest_sha256": _sha256(_canonical_bytes(packet["input_refs"])),
                "output_sha256": artifact["sha256"],
            },
        }
        if result_kind == "dissent":
            row["flip_condition"] = "Reopen if exact contradictory evidence appears."
            row["reactivation_condition"] = (
                "Reopen on an authority-bound contradiction."
            )
        return row

    def cell_result(self, started: Mapping[str, object]) -> JsonObject:
        conclusion = "The source-bound disputed claim is accepted with dissent retained."
        dissent = self.worker_result(
            started,
            name="dissent",
            result_kind="dissent",
            responsibility="adversarial_challenge",
            resource="dissent-worker",
            statement="A bounded dissent remains.",
        )
        acceptance = self.worker_result(
            started,
            name="acceptance",
            result_kind="independent_acceptance",
            responsibility="independent_acceptance",
            resource="acceptance-worker",
            statement="Independent acceptance passed.",
            validation_scope={
                "claim_ids": ["claim-1"],
                "candidate_conclusion_sha256": _sha256(conclusion.encode("utf-8")),
                "validation_condition_ids": ["acceptance-1"],
            },
        )
        return {
            "kind": "cell_batch_result",
            "worker_results": [dissent, acceptance],
            "incumbent": {
                "candidate_id": "candidate-1",
                "conclusion": conclusion,
                "claim_ids": ["claim-1"],
                "evidence_result_indexes": [0, 1],
                "dissent_result_indexes": [0],
                "validation": {
                    "declared_conditions": [
                        {
                            "condition_id": "acceptance-1",
                            "statement": "result_is_source_bound",
                        }
                    ],
                    "results": [
                        {
                            "condition_id": "acceptance-1",
                            "status": "passed",
                            "evidence_result_indexes": [1],
                        }
                    ],
                    "independent_acceptance_result_index": 1,
                },
            },
            "close_reason": "Declared checks passed with bounded dissent retained.",
        }


def _events(workspace: Path) -> Sequence[JsonObject]:
    return ResearchProject.open(workspace).events()


def _registered_inventory(root: Path) -> JsonObject:
    inventory: JsonObject = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path != root / "acceptance.json":
            content = path.read_bytes()
            inventory[path.relative_to(root).as_posix()] = {
                "sha256": _sha256(content),
                "bytes": len(content),
            }
    return inventory


def _checkpoint(
    observations: list,
    *,
    case: str,
    started_ns: int,
    result: Mapping[str, object],
) -> None:
    forward_ref = None
    for key in ("accepted_result_ref", "handoff_ref"):
        candidate = result.get(key)
        if isinstance(candidate, Mapping):
            forward_ref = dict(candidate)
            break
    directive = result.get("directive") or result.get("pending_directive")
    if forward_ref is None and isinstance(directive, Mapping):
        candidate = directive.get("registered_packet_ref")
        if isinstance(candidate, Mapping):
            forward_ref = dict(candidate)
    diagnostic = None if forward_ref else {
        "state": result.get("state"),
        "route": result.get("route"),
        "decision": "bounded Director state transition",
    }
    observations.append(
        {
            "case": case,
            "elapsed_ns": time.monotonic_ns() - started_ns,
            "forward_artifact_ref": forward_ref,
            "decision_grade_diagnostic": diagnostic,
        }
    )


def _case_summary(workspace: Path, replay_input: Path) -> JsonObject:
    project = ResearchProject.open(workspace)
    views = project.materialize_views()
    return {
        "workspace": workspace.name,
        "event_count": len(project.events()),
        "event_sha256": _sha256(project.event_path.read_bytes()),
        "council_sha256": _sha256(_canonical_bytes(views.get("council"))),
        "director_sha256": _sha256(_canonical_bytes(views.get("director"))),
        "replay_input": replay_input.relative_to(workspace).as_posix(),
    }


def _assert_zero_forbidden_events(root: Path) -> None:
    for case_dir in (root / "cases").iterdir():
        for event in _events(case_dir):
            event_type = str(event.get("event_type", ""))
            if event_type in {
                "authorization.granted",
                "external.write.completed",
                "publication.completed",
                "payment.completed",
                "irreversible.completed",
            }:
                raise RuntimeError(f"forbidden effect event observed: {event_type}")


def _build_acceptance(root: Path) -> JsonObject:
    observations = []
    started_ns = time.monotonic_ns()
    cases: JsonObject = {}

    fast = _Fixture(root / "cases/deterministic-fast-path", "director-fast-path")
    fast_work = fast.write_work_item()
    fast_started = _cli_advance(fast.workspace, fast_work)
    _checkpoint(
        observations,
        case="deterministic_fast_path",
        started_ns=started_ns,
        result=fast_started,
    )
    fast_result = fast.write_internal_result(
        fast_started,
        {
            "kind": "fast_path_result",
            "value": {"status": "passed", "artifact": "project-local-result"},
        },
    )
    fast_completed = _cli_advance(fast.workspace, fast_result)
    _checkpoint(
        observations,
        case="deterministic_fast_path",
        started_ns=started_ns,
        result=fast_completed,
    )
    if fast_started.get("requires_human") or fast_completed.get("state") != "completed":
        raise RuntimeError("fast path did not complete autonomously")
    cases["deterministic_fast_path"] = _case_summary(fast.workspace, fast_result)

    cell = _Fixture(root / "cases/disputed-decision-cell", "director-cell")
    cell_work = cell.write_work_item(disputed=True)
    cell_started = _cli_advance(cell.workspace, cell_work)
    _checkpoint(
        observations,
        case="disputed_decision_cell",
        started_ns=started_ns,
        result=cell_started,
    )
    cell_result = cell.write_internal_result(cell_started, cell.cell_result(cell_started))
    cell_completed = _cli_advance(cell.workspace, cell_result)
    _checkpoint(
        observations,
        case="disputed_decision_cell",
        started_ns=started_ns,
        result=cell_completed,
    )
    cell_views = cell.project.materialize_views()
    dissent = cell_views["council"].get("dissent") or []
    if cell_completed.get("state") != "completed" or not dissent:
        raise RuntimeError("Decision Cell did not retain dissent and complete")
    if not all(row.get("flip_condition") for row in dissent):
        raise RuntimeError("Decision Cell dissent lacks a flip condition")
    cases["disputed_decision_cell"] = _case_summary(cell.workspace, cell_result)

    external = _Fixture(
        root / "cases/external-write-zero-effect-handoff", "director-handoff"
    )
    external_work = external.write_work_item(external=True)
    external_result = _cli_advance(external.workspace, external_work)
    _checkpoint(
        observations,
        case="external_write_zero_effect_handoff",
        started_ns=started_ns,
        result=external_result,
    )
    if external_result.get("state") != "human_action_required":
        raise RuntimeError("external write did not stop at the human boundary")
    if not isinstance(external_result.get("handoff_ref"), Mapping):
        raise RuntimeError("external write did not register a responsibility handoff")
    cases["external_write_zero_effect_handoff"] = _case_summary(
        external.workspace, external_work
    )

    replay = _Fixture(root / "cases/interruption-and-replay", "director-replay")
    replay_work = replay.write_work_item()
    replay_first = _cli_advance(replay.workspace, replay_work)
    _checkpoint(
        observations,
        case="interruption_and_replay",
        started_ns=started_ns,
        result=replay_first,
    )
    before = _registered_inventory(replay.workspace)
    before_events = replay.project.event_path.read_bytes()
    before_views = replay.project.materialize_views()
    replay_second = _cli_advance(replay.workspace, replay_work)
    after = _registered_inventory(replay.workspace)
    after_events = replay.project.event_path.read_bytes()
    after_views = replay.project.materialize_views()
    _checkpoint(
        observations,
        case="interruption_and_replay",
        started_ns=started_ns,
        result=replay_second,
    )
    replay_identical = (
        replay_first == replay_second
        and before == after
        and before_events == after_events
        and before_views.get("council") == after_views.get("council")
        and before_views.get("director") == after_views.get("director")
    )
    if not replay_identical:
        raise RuntimeError("interruption replay changed accepted project state")
    cases["interruption_and_replay"] = _case_summary(replay.workspace, replay_work)

    all_checkpoints = all(
        bool(row["forward_artifact_ref"] or row["decision_grade_diagnostic"])
        for row in observations
    )
    _assert_zero_forbidden_events(root)
    checks = {
        "fast_path_completed_without_human_step": True,
        "cell_retained_dissent_and_flip_condition": True,
        "external_write_handoff_zero_effect": True,
        "interruption_replay_idempotent": replay_identical,
        "all_checkpoints_forward_or_diagnostic": all_checkpoints,
    }
    basis = {
        "contract": ACCEPTANCE_CONTRACT,
        "cases": cases,
        "checks": checks,
        "forbidden_effect_counts": FORBIDDEN_EFFECT_COUNTS,
        "public_operations": ["director-advance"],
    }
    return {
        **basis,
        "acceptance_id": _sha256(_canonical_bytes(basis))[:16],
        "status": "accepted",
        "checkpoint_observations": observations,
    }


def _verify_existing(root: Path, value: Mapping[str, object]) -> JsonObject:
    if value.get("contract") != ACCEPTANCE_CONTRACT or value.get("status") != "accepted":
        raise RuntimeError("existing Research Director acceptance is not accepted")
    identity_basis = {
        key: value.get(key)
        for key in (
            "contract",
            "cases",
            "checks",
            "forbidden_effect_counts",
            "public_operations",
        )
    }
    if value.get("acceptance_id") != _sha256(_canonical_bytes(identity_basis))[:16]:
        raise RuntimeError("existing Research Director acceptance identity drifted")
    before = _registered_inventory(root)
    if value.get("package_inventory") != before:
        raise RuntimeError("existing Research Director acceptance inventory drifted")
    cases = value.get("cases")
    if not isinstance(cases, Mapping):
        raise RuntimeError("existing Research Director acceptance cases are invalid")
    for case_name, case in cases.items():
        if not isinstance(case, Mapping):
            raise RuntimeError("existing Research Director acceptance case is invalid")
        workspace = root / "cases" / str(case["workspace"])
        replay_input = workspace / str(case["replay_input"])
        event_before = ResearchProject.open(workspace).event_path.read_bytes()
        _cli_advance(workspace, replay_input)
        event_after = ResearchProject.open(workspace).event_path.read_bytes()
        if event_before != event_after:
            raise RuntimeError(f"{case_name} replay grew event history")
    after = _registered_inventory(root)
    if before != after:
        raise RuntimeError("Research Director acceptance replay drifted registered bytes")
    return dict(value)


def run_acceptance(output_dir: Path) -> JsonObject:
    """Build or replay the four-case local Director acceptance package."""

    output_dir = output_dir.resolve()
    acceptance_path = output_dir / "acceptance.json"
    if acceptance_path.is_file():
        value = json.loads(acceptance_path.read_text(encoding="utf-8"))
        return _verify_existing(output_dir, value)
    if output_dir.exists():
        raise RuntimeError("acceptance output exists without a complete receipt")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=output_dir.parent, prefix=".research-director-acceptance-"
    ) as temporary:
        candidate = Path(temporary) / "package"
        candidate.mkdir()
        value = _build_acceptance(candidate)
        inventory = _registered_inventory(candidate)
        value["package_inventory"] = inventory
        _write_json(candidate / "acceptance.json", value)
        os.replace(candidate, output_dir)
    return value
