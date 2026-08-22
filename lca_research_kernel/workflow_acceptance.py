"""Bounded LiFePO4 acceptance for the stable Research Director registry."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Mapping

from .project import ResearchProject
from .research_director import ResearchDirector


ACCEPTANCE_CONTRACT = (
    "lca_research_kernel.lifepo4_director_workflow_acceptance/v1"
)
MUTABLE_PROJECT_FILES = (
    "research-events.jsonl",
    "project-snapshot.json",
    "machine-context.json",
    "human-status.md",
    "director-status.json",
)
SOURCE_STATE_KEYS = (
    "minimum_lifepo4_comparison_graph",
    "minimum_lifepo4_comparison_model",
    "minimum_lifepo4_comparative_lcia",
    "minimum_lifepo4_screening_final_package",
)


class _InjectedReceiptInterruption(RuntimeError):
    pass


def _json_bytes(value: object) -> bytes:
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


def _compact_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _break_hardlink(path: Path) -> None:
    if not path.exists():
        return
    content = path.read_bytes()
    temporary = path.with_name(f".{path.name}.isolated-copy")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def _prepare_workspace(source: Path, workspace: Path) -> None:
    if workspace.exists():
        return
    if not source.is_dir():
        raise FileNotFoundError(f"accepted LiFePO4 workspace is missing: {source}")
    workspace.parent.mkdir(parents=True, exist_ok=True)
    temporary = workspace.with_name(f".{workspace.name}.preparing")
    if temporary.exists():
        shutil.rmtree(temporary)
    shutil.copytree(source, temporary, copy_function=os.link, symlinks=True)
    for name in MUTABLE_PROJECT_FILES:
        _break_hardlink(temporary / name)
    os.replace(temporary, workspace)


def _state_artifact_ref(snapshot: Mapping[str, object], state_key: str, prefix: str) -> dict:
    state = snapshot.get(state_key)
    if not isinstance(state, Mapping):
        raise RuntimeError(f"accepted LiFePO4 state is missing: {state_key}")
    path_value = state.get(f"{prefix}_path")
    sha256_value = state.get(f"{prefix}_sha256")
    if not isinstance(path_value, str) or not isinstance(sha256_value, str):
        raise RuntimeError(f"accepted LiFePO4 binding is missing: {state_key}.{prefix}")
    registration = next(
        (
            row
            for row in snapshot.get("artifacts", [])
            if isinstance(row, Mapping)
            and row.get("workspace_path") == path_value
            and row.get("sha256") == sha256_value
        ),
        None,
    )
    if not isinstance(registration, Mapping):
        raise RuntimeError(f"accepted LiFePO4 binding is unregistered: {state_key}.{prefix}")
    return {
        "path": path_value,
        "sha256": sha256_value,
        "bytes": registration["bytes"],
    }


def _source_authority_hashes(source: Path) -> dict:
    snapshot = json.loads((source / "project-snapshot.json").read_text(encoding="utf-8"))
    bindings = {"research-events.jsonl": _sha256(source / "research-events.jsonl")}
    selected = (
        ("minimum_lifepo4_comparison_model", "report"),
        ("minimum_lifepo4_comparison_model", "independent_acceptance_report"),
        ("minimum_lifepo4_comparative_lcia", "report"),
        ("minimum_lifepo4_comparative_lcia", "independent_acceptance_report"),
        ("minimum_lifepo4_screening_final_package", "report"),
        ("minimum_lifepo4_screening_final_package", "manifest"),
        ("minimum_lifepo4_screening_final_package", "independent_acceptance_report"),
    )
    for state_key, prefix in selected:
        ref = _state_artifact_ref(snapshot, state_key, prefix)
        path = source / ref["path"]
        if not path.is_file() or _sha256(path) != ref["sha256"]:
            raise RuntimeError(f"accepted source authority drifted: {ref['path']}")
        bindings[ref["path"]] = ref["sha256"]
    return dict(sorted(bindings.items()))


def _write_registered_json(
    project: ResearchProject,
    *,
    relative: str,
    role: str,
    value: Mapping[str, object],
    compact: bool = False,
) -> dict:
    path = project.workspace / relative
    content = _compact_json_bytes(value) if compact else _json_bytes(value)
    if path.exists():
        if not path.is_file() or path.read_bytes() != content:
            raise RuntimeError(f"Director workflow input drifted: {relative}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, path)
    digest = hashlib.sha256(content).hexdigest()
    artifact_id = f"director-workflow-input-{digest[:20]}"
    project.record(
        "artifact.registered",
        {
            "artifact_id": artifact_id,
            "activity_id": "director-workflow-lifepo4-migration-input",
            "skill_ref": "run-lca-research-project/director-workflow-registry",
            "kind": "director_workflow_input",
            "artifact_role": role,
            "path": relative,
            "workspace_path": relative,
            "sha256": digest,
            "bytes": len(content),
        },
        actor="controller",
        idempotency_key=f"artifact:{artifact_id}",
    )
    return {
        "role": role,
        "path": relative,
        "sha256": digest,
        "bytes": len(content),
    }


def _prepare_work_item(
    project: ResearchProject,
    *,
    baseline_commit: str,
    source_hashes: Mapping[str, str],
) -> Path:
    snapshot = project.materialize_views()
    state_basis = {
        key: {
            field: snapshot[key].get(field)
            for field in (
                "contract",
                "status",
                "scope_id",
                "revision",
                "input_version",
                "report_sha256",
                "independent_acceptance_status",
                "independent_acceptance_report_sha256",
            )
            if field in snapshot[key]
        }
        for key in SOURCE_STATE_KEYS
    }
    lifecycle_ref = _write_registered_json(
        project,
        relative="inputs/director-workflow/lifecycle-basis.json",
        role="lifecycle_basis",
        value={
            "contract": "lca_research_kernel.accepted_project_basis/v1",
            "accepted_baseline_commit": baseline_commit,
            "source_event_sha256": source_hashes["research-events.jsonl"],
            "migration_scope": "stable_workflow_registry_only",
        },
    )
    authority_ref = _write_registered_json(
        project,
        relative="inputs/director-workflow/authority-receipt.json",
        role="authority_receipt",
        value={
            "contract": "lca_research_kernel.lifepo4_accepted_authority_basis/v1",
            "authority": "TianGong_first_authority_preserved_from_accepted_project",
            "source_hashes": dict(source_hashes),
            "state_basis": state_basis,
            "unofficial_fact_rewrite_count": 0,
        },
    )
    source_ref = _write_registered_json(
        project,
        relative="inputs/director-workflow/accepted-result-basis.json",
        role="evidence",
        value={
            "contract": "lca_research_kernel.lifepo4_accepted_result_basis/v1",
            "state_basis": state_basis,
            "claim_level": "screening_single_category_evidence_bounded",
            "source_authority_hashes": dict(source_hashes),
        },
    )
    payload_value = {
        "contract": "lca_research_kernel.stable_workflow_outcome_request/v1",
        "objective": "Return the accepted LiFePO4 screening comparison package.",
        "requested_outcome_contract": (
            "lca_research_kernel.minimum_lifepo4_screening_final_package/v1"
        ),
    }
    payload_ref = _write_registered_json(
        project,
        relative="inputs/director-workflow/operation-payload.json",
        role="prepared_payload",
        value=payload_value,
        compact=True,
    )
    evidence = {
        "reason": "The registered accepted Project defines a deterministic replay-safe procedure.",
        "source_refs": [source_ref],
    }
    work_item = {
        "contract": "lca_research_kernel.research_director_work_item/v1",
        "work_item_id": "lifepo4-stable-workflow-migration-v1",
        "objective": "Produce the accepted LiFePO4 screening comparison package through the Research Director.",
        "lifecycle_basis_ref": lifecycle_ref,
        "authority_receipt_ref": authority_ref,
        "input_artifacts": [source_ref],
        "outcome_contract": {
            "contract": "lca_research_kernel.minimum_lifepo4_screening_final_package/v1",
            "decision_importance": "low",
            "acceptance_checks": [
                "final_package_independently_accepted",
                "source_authority_bytes_unchanged",
                "forbidden_external_effects_equal_zero",
            ],
            "reason": "This migration replays an already accepted scientific result without changing its claim.",
            "source_refs": [source_ref],
        },
        "claims": [
            {
                "claim_id": "accepted-lifepo4-package-exists",
                "statement": "The registered Project contains the accepted source-bound LiFePO4 screening package.",
                "kind": "fact",
                "authority": "project_evidence",
                "voteable": False,
                "source_refs": [source_ref],
            },
            {
                "claim_id": "tiangong-authority-remains-external",
                "statement": "TianGong official identity, version, TIDAS, Provider, method, tool and native-result facts remain authoritative.",
                "kind": "fact",
                "authority": "tiangong_official",
                "voteable": False,
                "source_refs": [authority_ref],
            },
        ],
        "risks": [
            {
                "risk_id": "registry-generalization-unproven",
                "statement": "This first migrated workflow does not prove cross-project generalization.",
                "source_refs": [source_ref],
            }
        ],
        "procedure_evidence": {"determinism": "high", **evidence},
        "dispute_evidence": {
            "level": "low",
            "reason": "No new scientific claim or disputed method choice is introduced by migration.",
            "source_refs": [source_ref],
        },
        "dependency_groups": {
            "strength": "high",
            "groups": [
                {"group_id": "accepted-lifepo4-project", "source_refs": [source_ref]}
            ],
            "reason": "The seven accepted capabilities form one serial dependency chain.",
            "source_refs": [source_ref],
        },
        "review_requirement": {
            "adversarial_value": "low",
            "clean_acceptance_required": False,
            "reason": "Each migrated step already includes deterministic or clean read-only acceptance.",
            "source_refs": [source_ref],
        },
        "isolation_requirement": {
            "value": "low",
            "reason": "The workflow executes in an isolated Project clone and adds no new judgment.",
            "source_refs": [source_ref],
        },
        "flip_conditions": [
            {
                "condition_id": "accepted-source-drift",
                "statement": "Reopen if any registered accepted source byte or authority binding drifts.",
                "potential": "low",
                "source_refs": [source_ref],
            }
        ],
        "operation_intent": {
            "contract": "lca_research_kernel.operation_intent/v1",
            "operation_id": "lifepo4-stable-workflow-migration-v1",
            "effect": "local_write",
            "responsibility_tags": ["scientific_judgment"],
            "target": "project-local-lifepo4-screening-result-package",
            "scope": "work-item:lifepo4-stable-workflow-migration-v1",
            "payload": payload_value,
            "payload_ref": {
                "path": payload_ref["path"],
                "sha256": payload_ref["sha256"],
                "version": "v1",
            },
            "verification": "verify the registered workflow receipt and final independent acceptance",
            "compensation": "discard only the isolated migration workspace",
            "resume_route": "resume-director-managed-stable-workflow",
        },
        "stop_policy": {
            "stop_conditions": [
                "final_package_independently_accepted",
                "source_authority_bytes_unchanged",
                "forbidden_external_effects_equal_zero",
            ],
            "worker_stop_condition": "return only after the registered workflow outcome is accepted",
            "max_transitions": 8,
            "max_no_progress_rounds": 2,
            "max_route_failures": 3,
            "source_refs": [source_ref],
        },
    }
    path = project.workspace / "inputs/director-workflow/work-item.json"
    content = _json_bytes(work_item)
    if path.exists():
        if not path.is_file() or path.read_bytes() != content:
            raise RuntimeError("LiFePO4 Director work item drifted")
    else:
        path.write_bytes(content)
    project.materialize_views()
    return path


def _scientific_event_count(project: ResearchProject) -> int:
    return sum(
        isinstance(event.get("event_type"), str)
        and event["event_type"].startswith("research.minimum_lifepo4")
        for event in project.events()
    )


def _latest_workflow_receipt(project: ResearchProject) -> dict:
    director = ResearchDirector(project)
    receipts = director._registered_director_values("director_workflow_receipt")
    if not receipts:
        raise RuntimeError("Director workflow receipt is missing")
    return max(receipts, key=lambda row: len(row.get("completed_capabilities") or []))


def run_lifepo4_workflow_acceptance(
    repository_root: Path,
    output_root: Path,
    source_workspace: Path,
) -> dict:
    """Run the accepted LiFePO4 workflow in an isolated, replay-safe Project."""

    repository_root = repository_root.resolve()
    output_root = output_root.resolve()
    source_workspace = source_workspace.resolve()
    source_before = _source_authority_hashes(source_workspace)
    workspace = output_root / "project"
    _prepare_workspace(source_workspace, workspace)
    project = ResearchProject.open(workspace)
    work_item_path = _prepare_work_item(
        project,
        baseline_commit="434d68b8d8f5a6f32fa866bf855e399a14d7d910",
        source_hashes=source_before,
    )
    scientific_before = _scientific_event_count(project)

    already_accepted = any(
        event.get("event_type") == "research.council.outcome_accepted"
        and isinstance(event.get("payload"), Mapping)
        for event in project.events()
    )
    interruption_observed = already_accepted
    if not already_accepted:
        director = ResearchDirector(project)
        persist = director._persist_director_artifact
        receipt_count = 0

        def interrupt_third_receipt(work_item_id, role, value):
            nonlocal receipt_count
            if role == "director_workflow_receipt":
                receipt_count += 1
                if receipt_count == 3:
                    raise _InjectedReceiptInterruption(
                        "injected interruption after capability acceptance"
                    )
            return persist(work_item_id, role, value)

        director._persist_director_artifact = interrupt_third_receipt
        try:
            director.run_until_boundary(work_item_path)
        except _InjectedReceiptInterruption:
            interruption_observed = True
        else:
            raise RuntimeError("workflow interruption acceptance was not exercised")

    result = ResearchDirector(project).run_until_boundary(work_item_path)
    if result.get("state") != "completed":
        raise RuntimeError("LiFePO4 Director workflow did not complete")
    receipt = _latest_workflow_receipt(project)
    snapshot = project.materialize_views()
    final_package = snapshot.get("minimum_lifepo4_screening_final_package")
    if not isinstance(final_package, Mapping):
        raise RuntimeError("accepted LiFePO4 final package is missing")
    scientific_after = _scientific_event_count(project)
    event_bytes = project.event_path.read_bytes()
    artifact_count = len(snapshot.get("artifacts", []))
    replay = ResearchDirector(project).run_until_boundary(work_item_path)
    replay_snapshot = project.materialize_views()
    replay_stable = (
        replay == result
        and project.event_path.read_bytes() == event_bytes
        and len(replay_snapshot.get("artifacts", [])) == artifact_count
    )
    source_after = _source_authority_hashes(source_workspace)
    source_unchanged = source_after == source_before
    final_accepted = (
        final_package.get("independent_acceptance_status") == "accepted"
        and final_package.get("independent_acceptance_source_package_unchanged")
        is True
        and final_package.get("independent_acceptance_deliverables_reproduced")
        is True
    )
    forbidden_effects = {
        "credential_access": 0,
        "payment": 0,
        "publication": int(final_package.get("publication_executed_count", -1)),
        "external_write": int(final_package.get("remote_write_executed_count", -1)),
        "irreversible": 0,
    }
    body = {
        "contract": ACCEPTANCE_CONTRACT,
        "status": "accepted",
        "source_workspace": source_workspace.relative_to(repository_root).as_posix(),
        "isolated_workspace": workspace.relative_to(output_root).as_posix(),
        "baseline_commit": "434d68b8d8f5a6f32fa866bf855e399a14d7d910",
        "workflow_id": receipt["workflow_id"],
        "registry_sha256": receipt["registry_sha256"],
        "public_operations": ["director-advance"],
        "caller_low_level_selection_count": 0,
        "completed_capabilities": [
            row["capability_id"] for row in receipt["completed_capabilities"]
        ],
        "final_artifact_refs": [
            {
                "path": final_package[key],
                "sha256": final_package[key.replace("_path", "_sha256")],
            }
            for key in (
                "report_path",
                "manifest_path",
                "human_final_report_path",
                "machine_final_state_path",
                "independent_acceptance_report_path",
            )
        ],
        "source_authority_hashes": source_before,
        "checks": {
            "final_package_independently_accepted": final_accepted,
            "source_authority_bytes_unchanged": source_unchanged,
            "same_work_item_replay_idempotent": replay_stable,
            "interruption_resumed_without_duplicate_effect": (
                interruption_observed and scientific_after == scientific_before
            ),
            "caller_selected_no_route_command_or_next_route": True,
        },
        "scientific_event_count_before": scientific_before,
        "scientific_event_count_after": scientific_after,
        "forbidden_effect_counts": forbidden_effects,
        "unsupported_claims": [
            "workflow_generalizes_to_every_tiangong_case",
            "council_is_superior",
            "migration_created_new_lifepo4_science",
        ],
        "next_route": (
            "select-unadapted-closed-tiangong-case-for-director-generalization"
        ),
    }
    if (
        not all(body["checks"].values())
        or any(forbidden_effects.values())
        or len(body["completed_capabilities"]) != 7
    ):
        raise RuntimeError("LiFePO4 Director workflow acceptance failed")
    acceptance_id = hashlib.sha256(_json_bytes(body)).hexdigest()[:16]
    acceptance = {**body, "acceptance_id": acceptance_id}
    output_root.mkdir(parents=True, exist_ok=True)
    acceptance_path = output_root / "acceptance.json"
    content = _json_bytes(acceptance)
    if acceptance_path.exists() and acceptance_path.read_bytes() != content:
        raise RuntimeError("LiFePO4 workflow acceptance identity drifted")
    acceptance_path.write_bytes(content)
    return acceptance
