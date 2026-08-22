"""Parameterized, Director-only acceptance runner for exact native-LCIA work."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Mapping
from unittest import mock

from .cli import main as cli_main
from .project import ResearchProject
from .research_director import ResearchDirector
from .workflow_registry import load_workflow_registry


ACCEPTANCE_CONTRACT = (
    "lca_research_kernel.native_lcia_generalization_case_acceptance/v1"
)
FUNCTIONAL_UNIT_CONTRACT = (
    "lca_research_kernel.native_lcia_functional_unit_binding/v1"
)
FORBIDDEN_EFFECT_COUNTS = {
    "credential_access": 0,
    "payment": 0,
    "contract_acceptance": 0,
    "publication": 0,
    "external_write": 0,
    "external_message": 0,
    "irreversible": 0,
}
FROZEN_EXECUTION_PATHS = (
    "docs/research-workflow-registry.json",
    "docs/research-director-contract.json",
    "docs/tiangong-native-lcia-interpretation-contract.json",
    "lca_research_kernel/workflow_registry.py",
    "lca_research_kernel/research_director.py",
    "lca_research_kernel/project.py",
)


class _InjectedWorkflowReceiptInterruption(RuntimeError):
    pass


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


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != content:
            raise RuntimeError(f"native LCIA case artifact drifted: {path}")
        return
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def _write_json(path: Path, value: object) -> Path:
    _atomic_write(path, _pretty_bytes(value))
    return path


def _verify_repository_ref(root: Path, value: object, label: str) -> tuple[dict, bytes]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is missing")
    path_value = value.get("path")
    digest = value.get("sha256")
    if not isinstance(path_value, str) or not isinstance(digest, str):
        raise ValueError(f"{label} identity is incomplete")
    path = (root / path_value).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"{label} escapes the repository") from error
    if not path.is_file():
        raise FileNotFoundError(f"{label} is absent: {path_value}")
    content = path.read_bytes()
    if _sha256(content) != digest:
        raise RuntimeError(f"{label} source hash drifted")
    return dict(value), content


def _register_bytes(
    project: ResearchProject,
    *,
    relative: str,
    role: str,
    content: bytes,
) -> dict:
    path = project.workspace / relative
    _atomic_write(path, content)
    reopened = path.read_bytes()
    digest = _sha256(reopened)
    payload = {
        "artifact_id": f"native-lcia-case-input-{role}-{digest[:16]}",
        "activity_id": "native-lcia-generalization-case-input",
        "skill_ref": "run-lca-research-project/native-lcia-interpretation-v1",
        "kind": "director_workflow_input",
        "artifact_role": role,
        "path": relative,
        "workspace_path": relative,
        "sha256": digest,
        "bytes": len(reopened),
    }
    project.record(
        "artifact.registered",
        payload,
        actor="controller",
        idempotency_key=f"native-lcia-case-input:{role}:{digest}",
    )
    return {
        "role": role,
        "path": relative,
        "sha256": digest,
        "bytes": len(reopened),
    }


def _register_json(
    project: ResearchProject, *, relative: str, role: str, value: object
) -> dict:
    return _register_bytes(
        project, relative=relative, role=role, content=_pretty_bytes(value)
    )


def _find_authority_ref(case_spec: Mapping[str, object], role: str) -> dict:
    rows = case_spec.get("authority_refs")
    if not isinstance(rows, list):
        raise ValueError("native LCIA case authority refs are missing")
    matching = [row for row in rows if isinstance(row, Mapping) and row.get("role") == role]
    if len(matching) != 1:
        raise ValueError(f"native LCIA case authority role is not exact: {role}")
    return dict(matching[0])


def _flow_property_binding(
    repository_root: Path, case_spec: Mapping[str, object]
) -> dict:
    source_ref = _find_authority_ref(case_spec, "exact_flow_metadata_catalog")
    _, content = _verify_repository_ref(
        repository_root, source_ref, "exact flow metadata catalog"
    )
    document = json.loads(content)
    rows = document.get("rows") if isinstance(document, Mapping) else None
    exact_ref = case_spec.get("reference_flow_exact_ref")
    if not isinstance(rows, list) or not isinstance(exact_ref, str) or "@" not in exact_ref:
        raise ValueError("native LCIA reference-flow binding is incomplete")
    flow_id, version = exact_ref.split("@", 1)
    matches = [
        row
        for row in rows
        if isinstance(row, Mapping)
        and row.get("id") == flow_id
        and row.get("version") == version
    ]
    if len(matches) != 1:
        raise ValueError("native LCIA exact reference Flow is unavailable")
    flow = matches[0].get("flow", {}).get("flowDataSet", {})
    information = flow.get("flowInformation", {})
    reference_id = information.get("quantitativeReference", {}).get(
        "referenceToReferenceFlowProperty"
    )
    properties = flow.get("flowProperties", {}).get("flowProperty")
    if isinstance(properties, Mapping):
        properties = [properties]
    matching_properties = [
        row
        for row in (properties or [])
        if isinstance(row, Mapping) and row.get("@dataSetInternalID") == reference_id
    ]
    if len(matching_properties) != 1:
        raise ValueError("native LCIA reference Flow property is unavailable")
    property_row = matching_properties[0]
    property_ref = property_row.get("referenceToFlowPropertyDataSet", {})
    return {
        "reference_flow_exact_ref": exact_ref,
        "reference_flow_property_internal_id": reference_id,
        "mean_value": property_row.get("meanValue"),
        "flow_property_exact_ref": (
            f"{property_ref.get('@refObjectId')}@{property_ref.get('@version')}"
        ),
        "source_catalog_ref": source_ref,
    }


def _functional_unit_binding(
    repository_root: Path, case_spec: Mapping[str, object]
) -> dict:
    if isinstance(case_spec.get("functional_unit"), Mapping):
        value = case_spec["functional_unit"]
        return {
            "contract": FUNCTIONAL_UNIT_CONTRACT,
            "status": "exact_human_readable_functional_unit",
            "process_exact_ref": case_spec.get("process_exact_ref"),
            "amount": value.get("amount"),
            "unit": value.get("unit"),
            "reference_flow_exact_ref": None,
            "reference_flow_property_binding": None,
        }
    if case_spec.get("human_functional_unit_label") != "unresolved":
        raise ValueError("native LCIA functional-unit evidence is unsupported")
    return {
        "contract": FUNCTIONAL_UNIT_CONTRACT,
        "status": "explicit_human_readable_unit_gap",
        "process_exact_ref": case_spec.get("process_exact_ref"),
        "amount": case_spec.get("reference_amount"),
        "unit": "unresolved",
        "reference_flow_exact_ref": case_spec.get("reference_flow_exact_ref"),
        "reference_flow_property_binding": _flow_property_binding(
            repository_root, case_spec
        ),
    }


def _work_item(
    *,
    case_id: str,
    lifecycle_ref: Mapping[str, object],
    authority_ref: Mapping[str, object],
    input_refs: list[dict],
    payload_ref: Mapping[str, object],
) -> dict:
    source = input_refs[0]
    evidence = {
        "reason": "The exact registered TianGong result and interpretation rules are deterministic.",
        "source_refs": input_refs,
    }
    return {
        "contract": "lca_research_kernel.research_director_work_item/v1",
        "work_item_id": f"native-lcia-{case_id}",
        "objective": "Interpret and independently accept one exact TianGong native LCIA result.",
        "lifecycle_basis_ref": dict(lifecycle_ref),
        "authority_receipt_ref": dict(authority_ref),
        "input_artifacts": input_refs,
        "outcome_contract": {
            "contract": "lca_research_kernel.native_lcia_interpretation_package/v1",
            "decision_importance": "low",
            "acceptance_checks": [
                "preserve all exact official LCIA method identities, values, versions, and units",
                "pass the four frozen within-unit relation checks",
                "keep every claim within the registered evidence ceiling",
                "independently re-open and accept the Project-local package",
            ],
            "reason": "The bounded interpretation is scientifically relevant but mechanically deterministic.",
            "source_refs": input_refs,
        },
        "claims": [
            {
                "claim_id": "exact-native-lcia-source",
                "statement": "The registered native LCIA rows are TianGong official result facts.",
                "kind": "fact",
                "authority": "tiangong_official",
                "voteable": False,
                "source_refs": [source],
            }
        ],
        "risks": [
            {
                "risk_id": "source-or-claim-drift",
                "statement": "Source, unit, report, or claim drift invalidates the package.",
                "source_refs": input_refs,
            }
        ],
        "procedure_evidence": {"determinism": "high", **evidence},
        "dispute_evidence": {
            "level": "low",
            "reason": "The procedure interprets exact rows under a frozen claim contract.",
            "source_refs": input_refs,
        },
        "dependency_groups": {
            "strength": "high",
            "groups": [{"group_id": "exact-source-bundle", "source_refs": input_refs}],
            "reason": "The profile, report, and acceptance form one serial source-bound workflow.",
            "source_refs": input_refs,
        },
        "review_requirement": {
            "adversarial_value": "low",
            "clean_acceptance_required": False,
            "reason": "Deterministic read-only acceptance is part of the registered workflow.",
            "source_refs": input_refs,
        },
        "isolation_requirement": {
            "value": "low",
            "reason": "All inputs are exact registered local artifacts.",
            "source_refs": input_refs,
        },
        "flip_conditions": [
            {
                "condition_id": "registered-source-drift",
                "statement": "Reopen if any registered source identity or byte changes.",
                "potential": "low",
                "source_refs": input_refs,
            }
        ],
        "operation_intent": {
            "contract": "lca_research_kernel.operation_intent/v1",
            "operation_id": f"native-lcia-{case_id}-local-package",
            "effect": "local_write",
            "responsibility_tags": ["scientific_judgment"],
            "target": "project-local-native-lcia-interpretation-package",
            "scope": f"work-item:native-lcia-{case_id}",
            "payload": {"outcome_contract": "native-lcia-interpretation-package/v1"},
            "payload_ref": {
                "path": payload_ref["path"],
                "sha256": payload_ref["sha256"],
                "version": "v1",
            },
            "verification": "run exact outcome, trajectory, and read-only acceptance checks",
            "compensation": "remove only unaccepted generated Project-local artifacts",
            "resume_route": "resume-source-bound-native-lcia-interpretation",
        },
        "stop_policy": {
            "stop_conditions": ["independently accepted bounded package exists"],
            "worker_stop_condition": "return after the registered package is independently accepted",
            "max_transitions": 8,
            "max_no_progress_rounds": 2,
            "max_route_failures": 3,
            "source_refs": input_refs,
        },
    }


def _invoke_director(workspace: Path, work_item_path: Path) -> dict:
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        return_code = cli_main(
            [
                "director-advance",
                "--workspace",
                str(workspace),
                "--input",
                str(work_item_path),
            ]
        )
    if return_code != 0:
        raise RuntimeError(f"director-advance failed with code {return_code}")
    result = json.loads(stream.getvalue())
    if not isinstance(result, dict):
        raise RuntimeError("director-advance returned a non-object")
    return result


def _execution_hashes(repository_root: Path) -> dict:
    paths = list(FROZEN_EXECUTION_PATHS)
    paths.extend(
        path.relative_to(repository_root).as_posix()
        for path in sorted((repository_root / "lca_research_kernel").glob("native_lcia_*.py"))
    )
    return {
        relative: _sha256((repository_root / relative).read_bytes())
        for relative in sorted(set(paths))
    }


def _artifact_by_role(snapshot: Mapping[str, object], role: str) -> dict:
    rows = [
        row
        for row in snapshot.get("artifacts", [])
        if isinstance(row, Mapping) and row.get("artifact_role") == role
    ]
    if len(rows) != 1:
        raise RuntimeError(f"native LCIA case artifact role is not unique: {role}")
    return dict(rows[0])


def _drift_checks(project_root: Path, work_item_relative: str) -> tuple[bool, bool]:
    parent = project_root.parent
    with tempfile.TemporaryDirectory(dir=parent) as temporary:
        source_copy = Path(temporary) / "source-drift-project"
        shutil.copytree(project_root, source_copy)
        snapshot = json.loads((source_copy / "project-snapshot.json").read_text())
        source = _artifact_by_role(snapshot, "native_lcia_bound_result")
        source_path = source_copy / source["workspace_path"]
        source_path.write_bytes(source_path.read_bytes() + b"\n")
        try:
            _invoke_director(source_copy, source_copy / work_item_relative)
        except (ValueError, RuntimeError):
            source_failed_closed = True
        else:
            source_failed_closed = False

    with tempfile.TemporaryDirectory(dir=parent) as temporary:
        registry_copy = Path(temporary) / "registry-drift-project"
        shutil.copytree(project_root, registry_copy)
        registry = load_workflow_registry(Path(__file__).resolve().parents[1])
        workflow = next(
            row
            for row in registry["workflows"]
            if row["workflow_id"] == "native-lcia-interpretation-v1"
        )
        registry["workflows"].append(dict(workflow))
        try:
            with mock.patch(
                "lca_research_kernel.research_director.load_workflow_registry",
                return_value=registry,
            ):
                _invoke_director(registry_copy, registry_copy / work_item_relative)
        except ValueError:
            registry_failed_closed = True
        else:
            registry_failed_closed = False
    return source_failed_closed, registry_failed_closed


def run_native_lcia_case(
    repository_root: Path, output_root: Path, case_spec: Mapping[str, object]
) -> dict:
    """Run one content-bound native-LCIA case through ``director-advance`` only."""

    repository_root = repository_root.resolve()
    output_root = output_root.resolve()
    accepted_path = output_root / "acceptance.json"
    if accepted_path.is_file():
        value = json.loads(accepted_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("native LCIA case acceptance is invalid")
        return value
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError("native LCIA case output is partial or unaccepted")
    output_root.mkdir(parents=True, exist_ok=True)
    project_root = output_root / "project"
    project = ResearchProject.create(
        project_root,
        project_id=f"native-lcia-{case_spec.get('case_id')}",
        title="Exact TianGong native LCIA interpretation",
        objective="Produce one source-bound scientific interpretation package.",
    )
    case_id = case_spec.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        raise ValueError("native LCIA case_id is missing")
    bound_ref, bound_content = _verify_repository_ref(
        repository_root, case_spec.get("bound_result_ref"), "bound native LCIA result"
    )
    bound_value = json.loads(bound_content)
    if bound_value.get("process_exact_ref") != case_spec.get("process_exact_ref"):
        raise RuntimeError("native LCIA case process identity drifted")
    contract_path = repository_root / "docs/tiangong-native-lcia-interpretation-contract.json"
    contract_content = contract_path.read_bytes()
    source_ref = _register_bytes(
        project,
        relative="inputs/native-lcia/bound-result.json",
        role="native_lcia_bound_result",
        content=bound_content,
    )
    contract_ref = _register_bytes(
        project,
        relative="inputs/native-lcia/interpretation-contract.json",
        role="native_lcia_interpretation_contract",
        content=contract_content,
    )
    functional_unit = _functional_unit_binding(repository_root, case_spec)
    functional_ref = _register_json(
        project,
        relative="inputs/native-lcia/functional-unit.json",
        role="native_lcia_functional_unit_binding",
        value=functional_unit,
    )
    lifecycle_ref = _register_json(
        project,
        relative="inputs/native-lcia/lifecycle-basis.json",
        role="lifecycle_basis",
        value={
            "contract": "lca_research_kernel.native_lcia_case_lifecycle_basis/v1",
            "phase": "native-lcia-real-task-generalization",
            "case_spec_sha256": _sha256(_pretty_bytes(case_spec)),
        },
    )
    authority_bindings = []
    for index, authority in enumerate(case_spec.get("authority_refs") or []):
        verified, _ = _verify_repository_ref(
            repository_root, authority, f"case authority ref {index}"
        )
        authority_bindings.append(verified)
    authority_ref = _register_json(
        project,
        relative="inputs/native-lcia/authority-receipt.json",
        role="authority_receipt",
        value={
            "contract": "lca_research_kernel.native_lcia_case_authority_receipt/v1",
            "authority": "TianGong official native LCIA first authority",
            "process_exact_ref": case_spec.get("process_exact_ref"),
            "bound_result_repository_ref": bound_ref,
            "authority_bindings": authority_bindings,
            "unofficial_fact_rewrite_count": 0,
        },
    )
    operation_payload = {"outcome_contract": "native-lcia-interpretation-package/v1"}
    payload_ref = _register_bytes(
        project,
        relative="inputs/native-lcia/operation-payload.json",
        role="prepared_payload",
        content=_canonical_bytes(operation_payload),
    )
    input_refs = [source_ref, functional_ref, contract_ref]
    work_item = _work_item(
        case_id=case_id,
        lifecycle_ref=lifecycle_ref,
        authority_ref=authority_ref,
        input_refs=input_refs,
        payload_ref=payload_ref,
    )
    work_item_path = _write_json(
        project_root / "inputs/native-lcia/director-work-item.json", work_item
    )
    work_item_relative = work_item_path.relative_to(project_root).as_posix()
    before_hashes = _execution_hashes(repository_root)

    original_persist = ResearchDirector._persist_director_artifact
    interrupted = {"done": False}

    def interrupt_first_receipt(self, work_item_id, role, value):
        if role == "director_workflow_receipt" and not interrupted["done"]:
            interrupted["done"] = True
            raise _InjectedWorkflowReceiptInterruption(
                "injected after capability effect before workflow receipt"
            )
        return original_persist(self, work_item_id, role, value)

    try:
        with mock.patch.object(
            ResearchDirector,
            "_persist_director_artifact",
            interrupt_first_receipt,
        ):
            _invoke_director(project_root, work_item_path)
    except _InjectedWorkflowReceiptInterruption:
        interruption_observed = True
    else:
        interruption_observed = False
    interrupted_events = ResearchProject.open(project_root).events()
    effect_count_before_resume = sum(
        row.get("event_type")
        == "research.native_lcia_interpretation.profile.compiled"
        for row in interrupted_events
    )
    result = _invoke_director(project_root, work_item_path)
    project = ResearchProject.open(project_root)
    first_events = project.events()
    first_snapshot = project.materialize_views()
    effect_count_after_resume = sum(
        row.get("event_type")
        == "research.native_lcia_interpretation.profile.compiled"
        for row in first_events
    )
    replay = _invoke_director(project_root, work_item_path)
    replay_events = ResearchProject.open(project_root).events()
    replay_snapshot = ResearchProject.open(project_root).materialize_views()
    source_failed, registry_failed = _drift_checks(project_root, work_item_relative)
    after_hashes = _execution_hashes(repository_root)
    if before_hashes != after_hashes:
        raise RuntimeError("native LCIA runtime changed during the case run")

    package_registration = _artifact_by_role(
        first_snapshot, "native_lcia_interpretation_package"
    )
    report_registration = _artifact_by_role(
        first_snapshot, "native_lcia_interpretation_report"
    )
    acceptance_registration = _artifact_by_role(
        first_snapshot, "native_lcia_interpretation_acceptance"
    )
    package = json.loads(
        (project_root / package_registration["workspace_path"]).read_text()
    )
    receipt_rows = [
        row
        for row in first_snapshot["artifacts"]
        if isinstance(row, Mapping)
        and row.get("artifact_role") == "director_workflow_receipt"
    ]
    if not receipt_rows:
        raise RuntimeError("native LCIA workflow receipt is absent")
    receipt = json.loads(
        (project_root / receipt_rows[-1]["workspace_path"]).read_text()
    )
    trajectory = {
        "contract": "lca_research_kernel.native_lcia_trajectory_evidence/v1",
        "public_operations": ["director-advance"],
        "public_operation_attempt_count": 5,
        "caller_low_level_selection_count": 0,
        "interruption_observed": interruption_observed,
        "effect_count_before_resume": effect_count_before_resume,
        "effect_count_after_resume": effect_count_after_resume,
        "interruption_recovery_no_duplicate_effect": (
            interruption_observed
            and effect_count_before_resume == effect_count_after_resume == 1
        ),
        "same_work_item_replay_no_growth": (
            replay == result
            and len(replay_events) == len(first_events)
            and len(replay_snapshot["artifacts"])
            == len(first_snapshot["artifacts"])
        ),
        "source_drift_failed_closed": source_failed,
        "registry_drift_failed_closed": registry_failed,
        "forbidden_effect_counts": dict(FORBIDDEN_EFFECT_COUNTS),
    }
    _write_json(output_root / "trajectory-evidence.json", trajectory)
    _write_json(
        output_root / "execution-hashes.json",
        {
            "contract": "lca_research_kernel.native_lcia_execution_hashes/v1",
            "status": "frozen",
            "hashes": before_hashes,
        },
    )
    status = (
        "accepted_with_explicit_functional_unit_gap"
        if functional_unit["status"] == "explicit_human_readable_unit_gap"
        else "accepted"
    )
    acceptance = {
        "contract": ACCEPTANCE_CONTRACT,
        "status": status,
        "case_id": case_id,
        "process_exact_ref": case_spec.get("process_exact_ref"),
        "bound_result_ref": bound_ref,
        "functional_unit": functional_unit,
        "workflow_id": receipt["workflow_id"],
        "capability_ids": [
            row["capability_id"] for row in receipt["completed_capabilities"]
        ],
        "director_result": result,
        "work_item_path": f"project/{work_item_relative}",
        "package_ref": {
            "path": f"project/{package_registration['workspace_path']}",
            "sha256": package_registration["sha256"],
            "bytes": package_registration["bytes"],
        },
        "report_ref": {
            "path": f"project/{report_registration['workspace_path']}",
            "sha256": report_registration["sha256"],
            "bytes": report_registration["bytes"],
        },
        "independent_acceptance_ref": {
            "path": f"project/{acceptance_registration['workspace_path']}",
            "sha256": acceptance_registration["sha256"],
            "bytes": acceptance_registration["bytes"],
        },
        "indicator_count": len(package["interpretation_profile"]["indicators"]),
        "relation_check_count": len(package["interpretation_profile"]["relation_checks"]),
        "public_operations": ["director-advance"],
        "caller_low_level_selection_count": 0,
        "forbidden_effect_counts": dict(FORBIDDEN_EFFECT_COUNTS),
        "execution_hashes": before_hashes,
    }
    _write_json(accepted_path, acceptance)
    from .native_lcia_interpretation_evaluation import (
        grade_native_lcia_outcome,
        grade_native_lcia_trajectory,
        review_native_lcia_case,
    )

    task_freeze = json.loads(
        (repository_root / "outputs/native-lcia-director-generalization/task-freeze.json").read_text()
    )
    _write_json(
        output_root / "outcome-grade.json",
        grade_native_lcia_outcome(repository_root, output_root, task_freeze),
    )
    _write_json(
        output_root / "trajectory-grade.json",
        grade_native_lcia_trajectory(repository_root, output_root),
    )
    _write_json(
        output_root / "clean-review.json",
        review_native_lcia_case(repository_root, output_root),
    )
    return acceptance
