"""Stable, machine-validated research workflows selected by the Director."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Mapping, Optional, Sequence


REGISTRY_CONTRACT = "lca_research_kernel.research_workflow_registry/v1"
REGISTRY_PATH = Path("docs/research-workflow-registry.json")
CAPABILITY_FIELDS = {
    "capability_id",
    "version",
    "applicability",
    "authority_inputs",
    "effect_type",
    "execution_entry",
    "expected_artifacts",
    "acceptance_predicate",
    "idempotency",
    "recovery_route",
    "next_route",
}
LIFEPO4_CAPABILITY_IDS = {
    "lifepo4.compile-minimum-comparison-graph",
    "lifepo4.materialize-minimum-comparison-model",
    "lifepo4.accept-minimum-comparison-model",
    "lifepo4.execute-comparative-lcia",
    "lifepo4.accept-comparative-lcia",
    "lifepo4.assemble-screening-final-package",
    "lifepo4.accept-screening-final-package",
}
NATIVE_LCIA_CAPABILITY_IDS = {
    "native-lcia.compile-interpretation-profile",
    "native-lcia.assemble-interpretation-package",
    "native-lcia.accept-interpretation-package",
}
SUPPORTED_CAPABILITY_IDS = LIFEPO4_CAPABILITY_IDS | NATIVE_LCIA_CAPABILITY_IDS
EXECUTION_BINDINGS = {
    "ResearchProject.compile_minimum_lifepo4_comparison_graph": (
        "compile_minimum_lifepo4_comparison_graph",
        "project_state_only",
    ),
    "ResearchProject.materialize_minimum_lifepo4_comparison_model": (
        "materialize_minimum_lifepo4_comparison_model",
        "project_state_only",
    ),
    "ResearchProject.accept_minimum_lifepo4_comparison_model_read_only": (
        "accept_minimum_lifepo4_comparison_model_read_only",
        "project_state_only",
    ),
    "ResearchProject.execute_lifepo4_comparative_lcia_sensitivity_uncertainty": (
        "execute_lifepo4_comparative_lcia_sensitivity_uncertainty",
        "project_state_only",
    ),
    "ResearchProject.accept_lifepo4_comparative_lcia_read_only": (
        "accept_lifepo4_comparative_lcia_read_only",
        "project_state_only",
    ),
    "ResearchProject.assemble_lifepo4_screening_final_package": (
        "assemble_lifepo4_screening_final_package",
        "project_state_only",
    ),
    "ResearchProject.accept_lifepo4_screening_final_package_read_only": (
        "accept_lifepo4_screening_final_package_read_only",
        "project_state_only",
    ),
    "ResearchProject.compile_native_lcia_interpretation_profile": (
        "compile_native_lcia_interpretation_profile",
        "director_work_item_context",
    ),
    "ResearchProject.assemble_native_lcia_interpretation_package": (
        "assemble_native_lcia_interpretation_package",
        "director_work_item_context",
    ),
    "ResearchProject.accept_native_lcia_interpretation_package_read_only": (
        "accept_native_lcia_interpretation_package_read_only",
        "director_work_item_context",
    ),
}
EXECUTION_ENTRIES = set(EXECUTION_BINDINGS)
EXECUTION_CONTEXT_FIELDS = {
    "work_item_id",
    "work_item_content_sha256",
    "lifecycle_basis_ref",
    "authority_receipt_ref",
    "input_artifacts",
    "outcome_contract",
}
WORKFLOW_FIELDS = {
    "workflow_id",
    "version",
    "applicability",
    "capability_sequence",
    "final_artifacts",
    "completion_predicate",
    "recovery_route",
    "post_completion_next_route",
}
FORBIDDEN_LIVE_KEYS = {
    "current_goal",
    "current_date",
    "current_route",
    "goal_observation_id",
}
CALLER_ROUTING_KEYS = {
    "workflow_id",
    "capability_id",
    "execution_entry",
    "command",
    "route",
    "next_route",
}
WORKFLOW_RECEIPT_CONTRACT = "lca_research_kernel.research_workflow_receipt/v1"


def load_workflow_registry(root: Path) -> dict:
    """Load the repository's stable workflow registry."""

    return json.loads((root / REGISTRY_PATH).read_text(encoding="utf-8"))


def workflow_registry_identity(root: Path) -> str:
    """Return the exact stable-registry byte identity."""

    return hashlib.sha256((root / REGISTRY_PATH).read_bytes()).hexdigest()


def _all_keys(value: object):
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield key
            yield from _all_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _all_keys(child)


def _value_at(value: Mapping[str, object], path: str) -> object:
    current: object = value
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise KeyError(path)
        current = current[part]
    return current


def _predicates_hold(
    value: Mapping[str, object], predicates: Sequence[Mapping[str, object]]
) -> bool:
    for predicate in predicates:
        if set(predicate) != {"path", "equals"}:
            raise ValueError("workflow state predicate fields are invalid")
        path = predicate.get("path")
        if not isinstance(path, str) or not path:
            raise ValueError("workflow state predicate path is invalid")
        try:
            actual = _value_at(value, path)
        except KeyError:
            return False
        if actual != predicate.get("equals"):
            return False
    return True


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _artifact_ref(
    project: object,
    *,
    path_value: object,
    sha256_value: object,
    role: str,
) -> dict:
    if not isinstance(path_value, str) or not path_value:
        raise RuntimeError(f"workflow {role} path is missing")
    if (
        not isinstance(sha256_value, str)
        or len(sha256_value) != 64
        or any(character not in "0123456789abcdef" for character in sha256_value)
    ):
        raise RuntimeError(f"workflow {role} SHA-256 is invalid")
    registration = None
    for event in project.events():
        if event.get("event_type") != "artifact.registered":
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if payload.get("path") == path_value or payload.get("workspace_path") == path_value:
            registration = payload
    if not isinstance(registration, Mapping):
        raise RuntimeError(f"workflow {role} is not registered")
    workspace_path = registration.get("workspace_path")
    if not isinstance(workspace_path, str):
        raise RuntimeError(f"workflow {role} registration lacks workspace path")
    workspace = project.workspace.resolve()
    path = (workspace / workspace_path).resolve()
    try:
        path.relative_to(workspace)
    except ValueError as error:
        raise RuntimeError(f"workflow {role} artifact escapes Project workspace") from error
    if not path.is_file():
        raise RuntimeError(f"workflow {role} artifact is missing")
    content = path.read_bytes()
    actual = hashlib.sha256(content).hexdigest()
    if (
        actual != sha256_value
        or registration.get("sha256") != sha256_value
        or registration.get("bytes") != len(content)
    ):
        raise RuntimeError(f"workflow {role} artifact drifted")
    return {
        "role": role,
        "path": path_value,
        "sha256": actual,
        "bytes": len(content),
    }


def _accepted_values(
    result: Mapping[str, object], predicate: Mapping[str, object]
) -> dict:
    values = {}
    for path, expected in predicate.items():
        try:
            actual = _value_at(result, str(path))
        except KeyError as error:
            raise RuntimeError(f"workflow acceptance path is missing: {path}") from error
        if actual != expected:
            raise RuntimeError(f"workflow acceptance predicate failed: {path}")
        values[str(path)] = actual
    return values


def _capability_artifacts(
    project: object,
    capability: Mapping[str, object],
    result: Mapping[str, object],
) -> list[dict]:
    refs = []
    for declaration in capability["expected_artifacts"]:
        refs.append(
            _artifact_ref(
                project,
                path_value=result.get(declaration["path_field"]),
                sha256_value=result.get(declaration["sha256_field"]),
                role=str(declaration["role"]),
            )
        )
    return refs


def _receipt(body: Mapping[str, object]) -> dict:
    digest = hashlib.sha256(_canonical_bytes(body)).hexdigest()
    return {**body, "receipt_content_sha256": digest}


def _validate_existing_receipt(
    project: object,
    receipt: Mapping[str, object],
    *,
    registry_identity: str,
    workflow: Mapping[str, object],
    work_item_id: str,
    work_item_content_sha256: str,
) -> list[dict]:
    if receipt.get("contract") != WORKFLOW_RECEIPT_CONTRACT:
        raise RuntimeError("workflow receipt contract drifted")
    if (
        receipt.get("registry_sha256") != registry_identity
        or receipt.get("workflow_id") != workflow.get("workflow_id")
        or receipt.get("workflow_version") != workflow.get("version")
        or receipt.get("work_item_id") != work_item_id
        or receipt.get("work_item_content_sha256") != work_item_content_sha256
    ):
        raise RuntimeError("workflow receipt identity drifted")
    body = {
        key: value
        for key, value in receipt.items()
        if key != "receipt_content_sha256"
    }
    if hashlib.sha256(_canonical_bytes(body)).hexdigest() != receipt.get(
        "receipt_content_sha256"
    ):
        raise RuntimeError("workflow receipt content identity drifted")
    completed = receipt.get("completed_capabilities")
    if not isinstance(completed, list):
        raise RuntimeError("workflow receipt completed capabilities are invalid")
    sequence = workflow.get("capability_sequence")
    if not isinstance(sequence, list) or [
        row.get("capability_id") if isinstance(row, Mapping) else None
        for row in completed
    ] != sequence[: len(completed)]:
        raise RuntimeError("workflow receipt capability sequence drifted")
    for row in completed:
        if not isinstance(row, Mapping):
            raise RuntimeError("workflow receipt capability result is invalid")
        refs = row.get("artifact_refs")
        if not isinstance(refs, list) or not refs:
            raise RuntimeError("workflow receipt artifact refs are missing")
        for ref in refs:
            if not isinstance(ref, Mapping):
                raise RuntimeError("workflow receipt artifact ref is invalid")
            _artifact_ref(
                project,
                path_value=ref.get("path"),
                sha256_value=ref.get("sha256"),
                role=str(ref.get("role") or "registered_artifact"),
            )
    return [dict(row) for row in completed]


def _final_artifacts(
    project: object, workflow: Mapping[str, object], snapshot: Mapping[str, object]
) -> list[dict]:
    refs = []
    for dotted_path in workflow.get("final_artifacts", []):
        if not isinstance(dotted_path, str) or not dotted_path.endswith("_path"):
            raise RuntimeError("workflow final artifact declaration is invalid")
        try:
            path_value = _value_at(snapshot, dotted_path)
            sha256_value = _value_at(snapshot, dotted_path[:-5] + "_sha256")
        except KeyError as error:
            raise RuntimeError(
                f"workflow final artifact binding is missing: {dotted_path}"
            ) from error
        refs.append(
            _artifact_ref(
                project,
                path_value=path_value,
                sha256_value=sha256_value,
                role=dotted_path.rsplit(".", 1)[-1][:-5],
            )
        )
    return refs


def execute_selected_workflow(
    *,
    project: object,
    registry: Mapping[str, object],
    registry_identity: str,
    workflow: Mapping[str, object],
    work_item_id: str,
    work_item_content_sha256: str,
    existing_receipts: Sequence[Mapping[str, object]],
    persist_receipt,
    execution_context: Optional[Mapping[str, object]] = None,
) -> dict:
    """Execute or resume one allowlisted workflow without parallel state truth."""

    capabilities = {
        row["capability_id"]: row for row in registry.get("capabilities", [])
    }
    sequence = workflow.get("capability_sequence")
    if not isinstance(sequence, list) or any(
        capability_id not in capabilities for capability_id in sequence
    ):
        raise ValueError("workflow capability sequence is unsupported")

    matching = []
    for receipt in existing_receipts:
        if not isinstance(receipt, Mapping):
            continue
        if (
            receipt.get("registry_sha256") == registry_identity
            and receipt.get("workflow_id") == workflow.get("workflow_id")
            and receipt.get("work_item_id") == work_item_id
            and receipt.get("work_item_content_sha256")
            == work_item_content_sha256
        ):
            matching.append(receipt)
    completed: list[dict] = []
    current_receipt = None
    if matching:
        current_receipt = max(
            matching,
            key=lambda row: len(row.get("completed_capabilities") or []),
        )
        completed = _validate_existing_receipt(
            project,
            current_receipt,
            registry_identity=registry_identity,
            workflow=workflow,
            work_item_id=work_item_id,
            work_item_content_sha256=work_item_content_sha256,
        )

    for capability_id in sequence[len(completed) :]:
        capability = capabilities[capability_id]
        entry = capability.get("execution_entry")
        if entry not in EXECUTION_BINDINGS:
            raise ValueError("workflow execution entry is not allowlisted")
        applicability = capability.get("applicability")
        predicates = (
            applicability.get("project_state")
            if isinstance(applicability, Mapping)
            else None
        )
        snapshot = project.materialize_views()
        if not isinstance(predicates, list) or not _predicates_hold(snapshot, predicates):
            raise ValueError(f"workflow capability authority is not accepted: {capability_id}")
        method_name, input_mode = EXECUTION_BINDINGS[entry]
        method = getattr(project, method_name, None)
        if not callable(method):
            raise RuntimeError(f"workflow execution entry is unavailable: {entry}")
        if input_mode == "project_state_only":
            result = method()
        elif input_mode == "director_work_item_context":
            if not isinstance(execution_context, Mapping) or set(
                execution_context
            ) != EXECUTION_CONTEXT_FIELDS:
                raise ValueError("workflow Director work-item context is incomplete")
            result = method(copy.deepcopy(dict(execution_context)))
        else:
            raise RuntimeError("workflow execution input mode is unsupported")
        if not isinstance(result, Mapping):
            raise RuntimeError("workflow capability returned a non-object")
        accepted_values = _accepted_values(
            result, capability["acceptance_predicate"]["all_equals"]
        )
        artifact_refs = _capability_artifacts(project, capability, result)
        completed.append(
            {
                "capability_id": capability_id,
                "capability_version": capability["version"],
                "execution_entry": entry,
                "accepted_values": accepted_values,
                "artifact_refs": artifact_refs,
                "recovery_route": capability["recovery_route"],
            }
        )
        next_index = len(completed)
        body = {
            "contract": WORKFLOW_RECEIPT_CONTRACT,
            "registry_sha256": registry_identity,
            "workflow_id": workflow["workflow_id"],
            "workflow_version": workflow["version"],
            "work_item_id": work_item_id,
            "work_item_content_sha256": work_item_content_sha256,
            "status": (
                "workflow_completed"
                if next_index == len(sequence)
                else "workflow_in_progress"
            ),
            "completed_capabilities": completed,
            "next_capability_id": (
                None if next_index == len(sequence) else sequence[next_index]
            ),
            "recovery_route": workflow["recovery_route"],
            "post_completion_next_route": workflow["post_completion_next_route"],
        }
        current_receipt = _receipt(body)
        persist_receipt(
            work_item_id, "director_workflow_receipt", current_receipt
        )

    if current_receipt is None or len(completed) != len(sequence):
        raise RuntimeError("workflow execution did not reach a receipt")
    snapshot = project.materialize_views()
    completion = workflow.get("completion_predicate")
    if not isinstance(completion, Mapping):
        raise RuntimeError("workflow completion predicate is invalid")
    _accepted_values(snapshot, completion.get("all_equals") or {})
    final_refs = _final_artifacts(project, workflow, snapshot)
    return {
        "contract": "lca_research_kernel.research_workflow_result/v1",
        "status": "workflow_completed",
        "workflow_id": workflow["workflow_id"],
        "workflow_version": workflow["version"],
        "registry_sha256": registry_identity,
        "work_item_id": work_item_id,
        "work_item_content_sha256": work_item_content_sha256,
        "completed_capabilities": [row["capability_id"] for row in completed],
        "workflow_receipt": dict(current_receipt),
        "final_artifact_refs": final_refs,
        "post_completion_next_route": workflow["post_completion_next_route"],
    }


def validate_workflow_registry(root: Path) -> list[str]:
    """Fail closed when stable workflow or capability declarations drift."""

    try:
        registry = load_workflow_registry(root)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return [f"workflow registry is unreadable: {error}"]
    errors: list[str] = []
    if registry.get("contract") != REGISTRY_CONTRACT:
        errors.append("workflow registry contract drifted")
    if registry.get("schema_version") != 1:
        errors.append("workflow registry schema version drifted")
    if registry.get("product_entry") != "director-advance":
        errors.append("workflow registry product entry drifted")
    if FORBIDDEN_LIVE_KEYS.intersection(_all_keys(registry)):
        errors.append("workflow registry contains live lifecycle state")

    capabilities = registry.get("capabilities")
    if not isinstance(capabilities, list):
        errors.append("workflow registry capabilities are missing")
        capabilities = []
    ids = []
    entries = []
    for index, capability in enumerate(capabilities):
        if not isinstance(capability, Mapping) or set(capability) != CAPABILITY_FIELDS:
            errors.append(f"workflow capability {index} fields drifted")
            continue
        capability_id = capability.get("capability_id")
        execution_entry = capability.get("execution_entry")
        ids.append(capability_id)
        entries.append(execution_entry)
        if capability.get("version") != "v1":
            errors.append(f"workflow capability {capability_id} version drifted")
        if capability.get("effect_type") != "local_write":
            errors.append(f"workflow capability {capability_id} effect drifted")
        if execution_entry not in EXECUTION_ENTRIES:
            errors.append(f"workflow capability {capability_id} execution entry is not allowlisted")
        if not isinstance(capability.get("authority_inputs"), list) or not capability["authority_inputs"]:
            errors.append(f"workflow capability {capability_id} authority inputs are missing")
        applicability = capability.get("applicability")
        if not isinstance(applicability, Mapping) or set(applicability) != {"project_state"}:
            errors.append(f"workflow capability {capability_id} applicability drifted")
        elif not isinstance(applicability.get("project_state"), list):
            errors.append(f"workflow capability {capability_id} applicability is invalid")
        expected = capability.get("expected_artifacts")
        if not isinstance(expected, list) or not expected:
            errors.append(f"workflow capability {capability_id} expected artifacts are missing")
        elif any(
            not isinstance(row, Mapping)
            or set(row) != {"role", "path_field", "sha256_field"}
            for row in expected
        ):
            errors.append(f"workflow capability {capability_id} artifact declaration drifted")
        acceptance = capability.get("acceptance_predicate")
        if (
            not isinstance(acceptance, Mapping)
            or set(acceptance) != {"all_equals"}
            or not isinstance(acceptance.get("all_equals"), Mapping)
            or not acceptance["all_equals"]
        ):
            errors.append(f"workflow capability {capability_id} acceptance predicate drifted")
        idempotency = capability.get("idempotency")
        if (
            not isinstance(idempotency, Mapping)
            or set(idempotency) != {"key_source", "replay_behavior"}
            or idempotency.get("replay_behavior")
            != "return_registered_accepted_state_without_event_or_artifact_growth"
        ):
            errors.append(f"workflow capability {capability_id} idempotency drifted")
        for key in ("recovery_route", "next_route"):
            if not isinstance(capability.get(key), str) or not capability[key]:
                errors.append(f"workflow capability {capability_id} {key} is missing")
    if set(ids) != SUPPORTED_CAPABILITY_IDS or len(ids) != len(SUPPORTED_CAPABILITY_IDS):
        errors.append("workflow registry capability set drifted")
    if set(entries) != EXECUTION_ENTRIES or len(entries) != len(EXECUTION_ENTRIES):
        errors.append("workflow registry execution allowlist coverage drifted")

    workflows = registry.get("workflows")
    if not isinstance(workflows, list) or len(workflows) != 2:
        errors.append("workflow registry must contain exactly two migrated workflows")
        workflows = []
    for workflow in workflows:
        if not isinstance(workflow, Mapping) or set(workflow) != WORKFLOW_FIELDS:
            errors.append("workflow declaration fields drifted")
            continue
        workflow_id = workflow.get("workflow_id")
        if workflow_id not in {
            "lifepo4-screening-comparison-v1",
            "native-lcia-interpretation-v1",
        }:
            errors.append("workflow identity drifted")
            continue
        if workflow.get("version") != "v1":
            errors.append(f"workflow {workflow_id} version drifted")
        expected_sequence = (
            [row for row in ids if row in LIFEPO4_CAPABILITY_IDS]
            if workflow_id == "lifepo4-screening-comparison-v1"
            else [row for row in ids if row in NATIVE_LCIA_CAPABILITY_IDS]
        )
        if workflow.get("capability_sequence") != expected_sequence:
            errors.append(f"workflow {workflow_id} capability order drifted")
        applicability = workflow.get("applicability")
        expected_outcome = (
            "lca_research_kernel.minimum_lifepo4_screening_final_package/v1"
            if workflow_id == "lifepo4-screening-comparison-v1"
            else "lca_research_kernel.native_lcia_interpretation_package/v1"
        )
        expected_roles = (
            []
            if workflow_id == "lifepo4-screening-comparison-v1"
            else [
                "native_lcia_bound_result",
                "native_lcia_functional_unit_binding",
                "native_lcia_interpretation_contract",
            ]
        )
        expected_applicability_keys = {
            "outcome_contract",
            "effect_type",
            "required_project_state",
        } | ({"required_input_roles"} if expected_roles else set())
        if not isinstance(applicability, Mapping) or set(applicability) != expected_applicability_keys:
            errors.append(f"workflow {workflow_id} applicability drifted")
        elif (
            applicability.get("outcome_contract") != expected_outcome
            or applicability.get("effect_type") != "local_write"
            or not isinstance(applicability.get("required_project_state"), list)
            or (
                workflow_id == "lifepo4-screening-comparison-v1"
                and not applicability["required_project_state"]
            )
            or applicability.get("required_input_roles", []) != expected_roles
        ):
            errors.append(f"workflow {workflow_id} applicability values drifted")
        completion = workflow.get("completion_predicate")
        if (
            not isinstance(completion, Mapping)
            or set(completion) != {"all_equals"}
            or not isinstance(completion.get("all_equals"), Mapping)
            or not completion["all_equals"]
        ):
            errors.append("LiFePO4 workflow completion predicate drifted")
        expected_route = (
            "select-unadapted-closed-tiangong-case-for-director-generalization"
            if workflow_id == "lifepo4-screening-comparison-v1"
            else "pilot-generalized-director-on-evolving-nev-project-with-explicit-data-gaps"
        )
        if workflow.get("post_completion_next_route") != expected_route:
            errors.append(f"workflow {workflow_id} post-completion route drifted")

    truth = registry.get("truth_policy")
    if not isinstance(truth, Mapping) or truth.get("project_truth") != (
        "existing_research_kernel_events_and_registered_artifacts"
    ):
        errors.append("workflow registry truth policy drifted")
    effects = registry.get("effect_policy")
    if (
        not isinstance(effects, Mapping)
        or set(effects.get("allowed") or [])
        != {"local_read", "local_write", "external_read"}
        or set(effects.get("forbidden_direct") or [])
        != {"external_write", "irreversible"}
        or effects.get("responsibility_boundary_precedes_execution") is not True
    ):
        errors.append("workflow registry effect policy drifted")
    return errors


def select_workflow(
    registry: Mapping[str, object],
    compiled_work_item: Mapping[str, object],
    project_snapshot: Mapping[str, object],
) -> Optional[dict]:
    """Select one stable workflow from source-bound outcome and Project truth."""

    injected = CALLER_ROUTING_KEYS.intersection(compiled_work_item)
    if injected:
        raise ValueError("caller-controlled workflow routing is forbidden")
    outcome = compiled_work_item.get("outcome_contract")
    operation = compiled_work_item.get("operation_intent")
    if not isinstance(outcome, Mapping) or not isinstance(operation, Mapping):
        raise ValueError("compiled work item lacks workflow selection evidence")
    candidates = []
    authority_failed = False
    for workflow in registry.get("workflows", []):
        if not isinstance(workflow, Mapping):
            continue
        applicability = workflow.get("applicability")
        if not isinstance(applicability, Mapping):
            continue
        if (
            outcome.get("contract") != applicability.get("outcome_contract")
            or operation.get("effect") != applicability.get("effect_type")
        ):
            continue
        predicates = applicability.get("required_project_state")
        if not isinstance(predicates, list):
            raise ValueError("workflow authority predicates are invalid")
        required_roles = applicability.get("required_input_roles", [])
        if not isinstance(required_roles, list) or any(
            not isinstance(role, str) or not role for role in required_roles
        ):
            raise ValueError("workflow input-role predicates are invalid")
        input_refs = compiled_work_item.get("input_artifacts")
        registrations = project_snapshot.get("artifacts")
        roles_hold = True
        if required_roles:
            roles_hold = (
                isinstance(input_refs, list)
                and isinstance(registrations, list)
                and {ref.get("role") for ref in input_refs if isinstance(ref, Mapping)}
                == set(required_roles)
            )
            for ref in input_refs if isinstance(input_refs, list) else []:
                if not isinstance(ref, Mapping):
                    roles_hold = False
                    continue
                if not any(
                    isinstance(row, Mapping)
                    and row.get("artifact_role") == ref.get("role")
                    and row.get("path") == ref.get("path")
                    and row.get("sha256") == ref.get("sha256")
                    and row.get("bytes") == ref.get("bytes")
                    for row in (
                        registrations if isinstance(registrations, list) else []
                    )
                ):
                    roles_hold = False
        if not _predicates_hold(project_snapshot, predicates) or not roles_hold:
            authority_failed = True
            continue
        candidates.append(dict(workflow))
    if len(candidates) > 1:
        raise ValueError("multiple stable workflows match one work item")
    if candidates:
        return candidates[0]
    if authority_failed:
        raise ValueError("stable workflow authority inputs are not accepted")
    return None
