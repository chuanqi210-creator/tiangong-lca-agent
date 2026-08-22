#!/usr/bin/env python3
"""Run the one case-neutral D-18 calculation-to-interpretation thin slice.

The harness owns no Project Record.  It normalizes two independently replayed
records into content-addressed evaluation evidence and never promotes a model
or capability result to an official scientific fact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_evaluation.core_lca_quality_efficiency import (
    CoreLCAQualityEvaluator,
    _atomic_write,
    build_core_lca_evidence_locks,
    build_interpretation_fixture,
    calculation_execution_sha256,
    canonical_sha256,
    compare_core_lca_evidence_locks,
    derive_effect_summary,
    derive_efficiency_metrics,
    load_core_lca_quality_contract,
    scan_secret_material,
)
from lca_research_kernel.assurance import AssuranceBoundary
from lca_research_kernel.capability_gateway import (
    CapabilityGateway,
    LocalDeterministicCapability,
)
from lca_research_kernel.controlled_research_evaluation import (
    ControlledResearchEvaluation,
)
from lca_research_kernel.model_gateway import ModelGateway
from lca_research_kernel.lca_domain_kernel import LCADomainKernel
from lca_research_kernel.model_runtime import (
    build_model_gateway,
    load_model_runtime_contract,
)
from lca_research_kernel.project_record import ProjectRecord
from lca_research_kernel.run_trace import RunTrace
from scripts.probe_model_providers import load_credential_environment
from scripts.run_controlled_lca_calculation import execute_acceptance_tranche


CONTRACT_PATH = ROOT / "lca_research_kernel/contracts/core-lca-quality-efficiency-v1.json"
CALCULATION_FIXTURE = ROOT / "outputs/d18-controlled-calculation-v2"
CALCULATION_RUNTIME = ROOT / ".runtime/d18-tiangong-capabilities"
CAPACITY_QUALIFICATION = ROOT / (
    "outputs/real-question-optimization/73101eec6999b27f/staged-regression-v1/"
    "provider-preflight/gemini-capacity-qualification.json"
)
DEFAULT_CREDENTIAL_ENV = Path.home() / ".config/d18-lca-agent/model-runtime.env"
FLOW_VERSION_RE = re.compile(r"/flows/(?P<id>[^/]+)_(?P<version>\d{2}\.\d{2}\.\d{3})\.json$")


def _load_json(path: Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _git_object_ref(commit: str, path: str) -> dict:
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError("immutable execution commit is invalid")
    completed = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return {
        "commit": commit,
        "path": path,
        "sha256": hashlib.sha256(completed.stdout).hexdigest(),
        "bytes": len(completed.stdout),
    }


def _relative_ref(path: Path, repository_root: Path) -> dict:
    path = Path(path).resolve(strict=True)
    relative = path.relative_to(Path(repository_root).resolve()).as_posix()
    return {"path": relative, "sha256": _file_sha256(path)}


def _write_secret_scan(output_root: Path) -> dict:
    """Scan immutable raw/evidence artifacts, excluding derived acceptance files."""

    output_root = Path(output_root).resolve()
    targets = [
        path
        for path in output_root.glob("**/projects/**/*")
        if path.is_file()
        and "__pycache__" not in path.parts
        # Persistent Project Record synchronization metadata is outside the
        # scientific/effect evidence set and contains no research payload.
        and path.name != ".project-record-write.lock"
    ]
    for relative in (
        "complete/calculation-evidence.json",
        "complete/interpretation-evidence.json",
        "complete/interpretation-result.json",
        "missing/checkpoint.json",
        "missing/resumed.json",
        "missing/replayed.json",
        "missing/missing-evidence.json",
        "missing/missing-evaluation.json",
        "recovery/recovery-evidence.json",
        "recovery/recovery-evaluation.json",
        "recovery/failure-diagnostics.json",
    ):
        target = output_root / relative
        if target.is_file():
            targets.append(target)
    receipt = scan_secret_material(ROOT, targets)
    _atomic_write(output_root / "evidence/secret-scan.json", receipt)
    return receipt


def provider_policy_for_schema_profile(
    runtime_contract: Mapping[str, object], profile_id: str
) -> dict:
    """Select only admitted catalog routes that prove the required capability."""

    catalog = runtime_contract.get("provider_catalog")
    rows = catalog.get("providers") if isinstance(catalog, Mapping) else None
    eligible = sorted(
        (
            row
            for row in rows or []
            if isinstance(row, Mapping)
            and row.get("admission_state") == "active_verified"
            and isinstance(row.get("capabilities"), Mapping)
            and row["capabilities"].get(profile_id) is True
        ),
        key=lambda row: (int(row.get("priority", 10**9)), str(row.get("route_id"))),
    )
    if not eligible:
        raise ValueError(f"no active verified provider proves {profile_id}")
    catalog_sha = runtime_contract.get("provider_catalog_sha256")
    routes = [
        {
            "route_id": str(row["route_id"]),
            "adapter_id": str(row["route_id"]),
            "admission_state": str(row["admission_state"]),
        }
        for row in eligible
    ]
    return {
        "policy_id": "core-lca-source-interpretation-provider-policy-v1",
        "required_schema_profile": profile_id,
        "primary_route": {**routes[0], "provider_catalog_sha256": catalog_sha},
        "failover_routes": routes[1:],
    }


def _restrict_gateway(gateway: ModelGateway, provider_policy: Mapping[str, object]) -> ModelGateway:
    route_rows = [
        provider_policy["primary_route"],
        *(provider_policy.get("failover_routes") or []),
    ]
    route_ids = [str(row["route_id"]) for row in route_rows]
    missing = set(route_ids) - set(gateway.adapters)
    if missing:
        raise ValueError("selected provider adapter is unavailable")
    source_policy = deepcopy(dict(gateway.policy or {}))
    source_routes = {
        str(row["route_id"]): row for row in source_policy.get("routes") or []
    }
    source_policy["routes"] = [
        deepcopy(dict(source_routes[route_id])) for route_id in route_ids
    ]
    return ModelGateway(
        {route_id: gateway.adapters[route_id] for route_id in route_ids},
        policy=source_policy,
        clock=gateway.clock,
        monotonic=gateway.monotonic,
        sleep=gateway.sleep,
        remaining_budget_seconds=gateway.remaining_budget_seconds,
        execution_environment=gateway.execution_environment,
        answer_timing=gateway.answer_timing,
    )


def _reference_flow_version(source_refs: list[object], flow_id: str) -> str:
    matches = []
    for row in source_refs:
        if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
            continue
        matched = FLOW_VERSION_RE.search(row["path"])
        if matched and matched.group("id") == flow_id:
            matches.append(matched.group("version"))
    if len(set(matches)) != 1:
        raise ValueError("reference flow exact version is not uniquely source-bound")
    return matches[0]


def normalize_calculation_evidence(
    *,
    material: Mapping[str, object],
    calculation_content: Mapping[str, object],
    tidas_content: Mapping[str, object],
    oracle: Mapping[str, object],
    assurance_decision_ref: Mapping[str, object],
    project_record_ref: Mapping[str, object],
    project_replay: Mapping[str, object],
    accepted_event_count: int,
    run_trace_ref: Mapping[str, object],
    material_ref: Optional[Mapping[str, object]] = None,
    oracle_ref: Optional[Mapping[str, object]] = None,
    accepted_fact_binding: Optional[Mapping[str, object]] = None,
    derived_effects: Optional[Mapping[str, object]] = None,
) -> dict:
    """Normalize accepted calculation evidence without accepting new truth."""

    inputs = material.get("scientific_inputs")
    functional_unit = material.get("functional_unit")
    source_refs = material.get("source_refs")
    if not isinstance(inputs, Mapping) or not isinstance(functional_unit, Mapping):
        raise ValueError("calculation material goal/scope is incomplete")
    if not isinstance(source_refs, list) or not source_refs:
        raise ValueError("calculation source refs are absent")
    processes = inputs.get("processes")
    providers = inputs.get("provider_connections")
    method = inputs.get("impact_method")
    result = calculation_content.get("result")
    impacts = calculation_content.get("impacts")
    if (
        not isinstance(processes, list)
        or not processes
        or not isinstance(providers, list)
        or not providers
        or not isinstance(method, Mapping)
        or not isinstance(result, Mapping)
        or not isinstance(impacts, list)
        or len(impacts) != 1
        or not isinstance(impacts[0], Mapping)
    ):
        raise ValueError("calculation evidence is structurally incomplete")
    flow_id = str(functional_unit.get("reference_flow_id"))
    value = impacts[0].get("amount")
    unit = impacts[0].get("unit")
    expected_rows = oracle.get("expected_results", {}).get("impacts")  # type: ignore[union-attr]
    expected = expected_rows[0] if isinstance(expected_rows, list) and len(expected_rows) == 1 else None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not isinstance(expected, Mapping):
        raise ValueError("calculation numeric evidence is absent")
    try:
        numeric_match = Decimal(str(value)) == Decimal(str(expected.get("value")))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("calculation numeric evidence is invalid") from error
    input_sha = material.get("scientific_inputs_sha256")
    validated_sha = tidas_content.get("scientific_inputs_sha256")
    evidence = {
        "contract": "d18_core_lca_calculation_evidence/v1",
        "authoritative_inputs": {
            "material": deepcopy(dict(material_ref or {})),
            "oracle": deepcopy(dict(oracle_ref or {})),
        },
        "goal_scope": {
            "research_goal": "Produce one source-bound deterministic LCA result.",
            "functional_unit": deepcopy(dict(functional_unit)),
            "system_boundary": ["foreground", "provider-linked background"],
            "lifecycle_stage": "upstream_analysis",
            "expected_use": "controlled_scientific_interpretation",
        },
        "source_binding": {
            "processes": deepcopy(list(processes)),
            "reference_flow": {
                "id": flow_id,
                "version": _reference_flow_version(source_refs, flow_id),
                "unit": functional_unit.get("unit"),
            },
            "provider_connections": deepcopy(list(providers)),
            "source_refs": deepcopy(list(source_refs)),
        },
        "tidas_validation": {
            "status": tidas_content.get("validation_status"),
            "scientific_inputs_sha256": input_sha,
            "validated_scientific_inputs_sha256": validated_sha,
            "silent_corrections": 0,
        },
        "calculation": {
            "method_id": method.get("id"),
            "method_version": method.get("version"),
            "value": value,
            "unit": unit,
            "oracle_value": float(Decimal(str(expected.get("value")))),
            "oracle_unit": expected.get("unit"),
            "numeric_tolerance_passed": numeric_match,
            "input_sha256": input_sha,
            "output_sha256": canonical_sha256(calculation_content),
        },
        "candidate_boundary": {
            "classification": "candidate",
            "tool_result_status": "sourced_result_not_fact",
            "assurance_decision_ref": deepcopy(dict(assurance_decision_ref)),
        },
        "project_record": {
            "ref": deepcopy(dict(project_record_ref)),
            "replay_verified": project_replay.get("status") == "passed",
            "accepted_event_count": accepted_event_count,
            "accepted_fact": deepcopy(dict(accepted_fact_binding or {})),
        },
        "run_trace": {"ref": deepcopy(dict(run_trace_ref))},
        "effects": deepcopy(
            dict(
                derived_effects
                or {"external_writes": 0, "forbidden_effects": 0}
            )
        ),
    }
    return evidence


def build_interpretation_evidence(
    *,
    calculation: Mapping[str, object],
    model_candidate: Mapping[str, object],
    domain_acceptance: Mapping[str, object],
    project_record_ref: Mapping[str, object],
    project_replay: Mapping[str, object],
    accepted_event_count: int,
    run_trace_ref: Mapping[str, object],
    accepted_fact_binding: Optional[Mapping[str, object]] = None,
    derived_effects: Optional[Mapping[str, object]] = None,
) -> dict:
    output = model_candidate.get("output")
    if not isinstance(output, Mapping):
        raise ValueError("interpretation model output is absent")
    candidate = {
        "status": model_candidate.get("status"),
        "sufficiency": output.get("sufficiency"),
        "result_statements": deepcopy(output.get("result_statements")),
        "limitations": deepcopy(output.get("limitations")),
        "cross_unit_numeric_ranking_performed": output.get(
            "cross_unit_numeric_ranking_performed"
        ),
        "zero_value_claim": output.get("zero_value_claim"),
        "tool_requests": deepcopy(model_candidate.get("tool_requests")),
    }
    return {
        "contract": "d18_core_lca_interpretation_evidence/v1",
        "source_calculation_ref": {"sha256": calculation_execution_sha256(calculation)},
        "candidate_source_sha256": canonical_sha256(output),
        "candidate": candidate,
        "domain_acceptance": deepcopy(dict(domain_acceptance)),
        "assurance": {"accepted_after_candidate": accepted_event_count == 1},
        "project_record": {
            "ref": deepcopy(dict(project_record_ref)),
            "replay_verified": project_replay.get("status") == "passed",
            "accepted_event_count": accepted_event_count,
            "accepted_fact": deepcopy(dict(accepted_fact_binding or {})),
        },
        "run_trace": {"ref": deepcopy(dict(run_trace_ref))},
        "effects": deepcopy(
            dict(
                derived_effects
                or {"external_writes": 0, "forbidden_effects": 0}
            )
        ),
        "routing": {"case_neutral": True},
        "quality_sequence": {"efficiency_compiled_before_quality": False},
        "cross_branch_gates": {"missing_data_boundary_verified": False},
        "acceptance_gates": {
            "clean_review_p1_zero_p2_zero": False,
            "full_repository_verification_passed": False,
        },
    }


def build_missing_evidence(
    *,
    model_candidate: Mapping[str, object],
    domain_acceptance: Mapping[str, object],
    project_replay: Mapping[str, object],
    accepted_event_count: int,
    project_record_ref: Optional[Mapping[str, object]] = None,
    run_trace_ref: Optional[Mapping[str, object]] = None,
    accepted_fact_binding: Optional[Mapping[str, object]] = None,
    derived_effects: Optional[Mapping[str, object]] = None,
) -> dict:
    """Normalize a model gap into the fixed scientific recovery contract."""

    output = model_candidate.get("output")
    if not isinstance(output, Mapping):
        raise ValueError("missing-data model output is absent")
    missing_fields = output.get("missing_fields")
    statements = output.get("result_statements")
    consequence = output.get("bounded_interpretation")
    return {
        "contract": "d18_core_lca_missing_evidence/v1",
        "candidate_source_sha256": canonical_sha256(output),
        "candidate": {
            "status": model_candidate.get("status"),
            "sufficiency": output.get("sufficiency"),
            "missing_fields": deepcopy(missing_fields),
            "scientific_consequence": consequence,
            "resume_condition": {
                "field": "functional_unit",
                "required_contract": "amount_unit_and_reference_flow",
                "source_binding_required": True,
            },
            "result_statements": deepcopy(statements),
            "numeric_conclusion": None if statements == [] else statements,
            "tool_requests": deepcopy(model_candidate.get("tool_requests")),
        },
        "domain_acceptance": deepcopy(dict(domain_acceptance)),
        "project_record": {
            "ref": deepcopy(dict(project_record_ref or {})),
            "replay_verified": project_replay.get("status") == "passed",
            "accepted_event_count": accepted_event_count,
            "accepted_fact": deepcopy(dict(accepted_fact_binding or {})),
        },
        "run_trace": {"ref": deepcopy(dict(run_trace_ref or {}))},
        "failure_policy": {
            "classification": "missing_critical_data",
            "retryable": False,
            "attempt_count": 1,
        },
        "effects": deepcopy(
            dict(
                derived_effects
                or {"external_writes": 0, "forbidden_effects": 0}
            )
        ),
    }
def _accepted_candidate_event_count(record_path: Path, fact_id: str) -> int:
    count = 0
    for line in record_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        candidate = event.get("payload", {}).get("candidate", {})
        if (
            event.get("event_type") == "research.project.accepted_change"
            and candidate.get("fact", {}).get("fact_id") == fact_id
        ):
            count += 1
    return count


def _accepted_fact_binding(
    record_path: Path, fact_id: str, *, accepted_content_key: str
) -> dict:
    matches = []
    for line in record_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        fact = event.get("payload", {}).get("candidate", {}).get("fact", {})
        if (
            event.get("event_type") == "research.project.accepted_change"
            and isinstance(fact, Mapping)
            and fact.get("fact_id") == fact_id
        ):
            matches.append((event, fact))
    if len(matches) != 1:
        raise ValueError(f"exactly one accepted fact is required: {fact_id}")
    event, fact = matches[0]
    value = fact.get("value")
    if not isinstance(value, Mapping) or accepted_content_key not in value:
        raise ValueError(f"accepted fact content is absent: {fact_id}")
    return {
        "fact_id": fact_id,
        "event_sha256": event.get("event_sha256"),
        "value_sha256": canonical_sha256(value),
        "accepted_content_key": accepted_content_key,
        "accepted_content_sha256": canonical_sha256(value[accepted_content_key]),
    }


def _trace_rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _runtime_canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _authorization_context(project: Path) -> dict:
    identity_path = project / "execution/execution-identity.json"
    policy_path = ROOT / "lca_research_kernel/contracts/project-execution-policy.json"
    record_path = project / ProjectRecord.EVENT_FILE
    identity = _load_json(identity_path)
    policy = _load_json(policy_path)
    if (
        identity.get("contract")
        != "lca_research_kernel.project_execution_identity/v1"
        or policy.get("contract")
        != "lca_research_kernel.project_execution_policy/v1"
        or identity.get("policy_id") != policy.get("policy_id")
        or identity.get("policy_sha256") != _runtime_canonical_sha256(policy)
    ):
        raise ValueError("registered execution identity/policy binding drifted")
    view = ProjectRecord.open_or_create_project(project).read_current_project_view()
    operations = []
    for fact in view.get("facts", {}).values():
        if not isinstance(fact, Mapping) or not isinstance(fact.get("value"), Mapping):
            continue
        value = fact["value"]
        authority = value.get("authority")
        source = fact.get("source")
        if (
            not isinstance(authority, Mapping)
            or not isinstance(source, Mapping)
            or value.get("status") != "sourced_result_not_fact"
        ):
            continue
        operation_id = value.get("request_id")
        if not isinstance(operation_id, str):
            raise ValueError("accepted capability result has no operation identity")
        operations.append(
            {
                "operation_id": operation_id,
                "capability_id": value.get("capability_id"),
                "effect": value.get("effect"),
                "source_lock_sha256": _runtime_canonical_sha256(
                    authority.get("source_lock")
                ),
                "arguments_sha256": source.get("operation_payload_sha256"),
                "entrypoint": authority.get("entrypoint"),
            }
        )
    return {
        "contract": "d18_core_lca_authorization_context/v1",
        "operation_receipt_contract": (
            "lca_research_kernel.project_operation_authorization/v1"
        ),
        "execution_id": identity.get("execution_id"),
        "policy_sha256": identity.get("policy_sha256"),
        "operations": sorted(operations, key=lambda row: str(row["operation_id"])),
        "source_refs": {
            "execution_identity": _relative_ref(identity_path, ROOT),
            "execution_policy": _relative_ref(policy_path, ROOT),
            "project_record": _relative_ref(record_path, ROOT),
        },
    }


def _derived_effects(
    *,
    trace_path: Path,
    authorization_receipt_paths: list[Path],
    project: Path,
) -> dict:
    refs = [_relative_ref(path, ROOT) for path in authorization_receipt_paths]
    context = _authorization_context(project)
    summary = derive_effect_summary(
        _trace_rows(trace_path),
        [_load_json(path) for path in authorization_receipt_paths],
        {key: value for key, value in context.items() if key != "source_refs"},
    )
    return {
        **summary,
        "trace_ref": _relative_ref(trace_path, ROOT),
        "authorization_receipt_refs": refs,
        "authorization_context": context,
    }


def _calculation_evidence(output_root: Path) -> dict:
    tranche = output_root / "complete/calculation"
    document = _load_json(tranche / "complete/resumed.json")
    result = document["result"]
    project = tranche / "complete/workspace/projects" / result["research_id"]
    record = ProjectRecord.open_or_create_project(project)
    replay = record.rebuild_and_verify()
    view = record.read_current_project_view()
    calculation_content = view["facts"]["controlled-calculation-candidate"]["value"]["calculation"]
    tidas_rows = [
        fact["value"]["content"]
        for fact in view["facts"].values()
        if isinstance(fact, Mapping)
        and isinstance(fact.get("value"), Mapping)
        and fact["value"].get("capability_id") == "tidas.validate"
    ]
    if len(tidas_rows) != 1:
        raise ValueError("one accepted TIDAS validation result is required")
    record_path = project / ProjectRecord.EVENT_FILE
    trace_path = project / "run-traces" / result["run_trace_ref"]["path"]
    trace = RunTrace(trace_path, run_id=result["run_trace_ref"]["run_id"])
    trace.verify()
    authorization_receipts = sorted(
        (project / "execution/authorization-receipts").glob("*.json")
    )
    return normalize_calculation_evidence(
        material=_load_json(CALCULATION_FIXTURE / "fixture/material-package.json"),
        calculation_content=calculation_content,
        tidas_content=tidas_rows[0],
        oracle=_load_json(CALCULATION_FIXTURE / "oracle/calculation-oracle.json"),
        assurance_decision_ref=result["assurance_decision_ref"],
        project_record_ref=_relative_ref(record_path, ROOT),
        project_replay=replay,
        accepted_event_count=_accepted_candidate_event_count(
            record_path, "controlled-calculation-candidate"
        ),
        run_trace_ref=_relative_ref(trace_path, ROOT),
        material_ref=_relative_ref(
            CALCULATION_FIXTURE / "fixture/material-package.json", ROOT
        ),
        oracle_ref=_relative_ref(
            CALCULATION_FIXTURE / "oracle/calculation-oracle.json", ROOT
        ),
        accepted_fact_binding=_accepted_fact_binding(
            record_path,
            "controlled-calculation-candidate",
            accepted_content_key="calculation",
        ),
        derived_effects=_derived_effects(
            trace_path=trace_path,
            authorization_receipt_paths=authorization_receipts,
            project=project,
        ),
    )


def _permission_policy() -> dict:
    source = _load_json(
        ROOT / "outputs/d18-controlled-research-evaluation/control/evaluation-contract.json"
    )
    return {
        "policy_id": "core-lca-source-interpretation-permission-policy-v1",
        "allowed_effects": deepcopy(source["allowed_effects"]),
        "forbidden_effects": deepcopy(source["forbidden_effects"]),
    }


def _interpretation_runner(
    *,
    workspace: Path,
    fixture_root: Path,
    credential_env_file: Path,
    recorded_at: str,
    run_id: str,
) -> tuple[ControlledResearchEvaluation, dict, dict]:
    runtime_contract = load_model_runtime_contract(ROOT)
    capacity = _load_json(CAPACITY_QUALIFICATION)
    environment = dict(os.environ)
    environment.update(
        load_credential_environment(
            credential_env_file, runtime_contract["provider_catalog"]
        )
    )
    provider_policy = provider_policy_for_schema_profile(
        runtime_contract, "generic_source_candidate_v1"
    )
    gateway = build_model_gateway(
        ROOT,
        environment,
        execution_mode="production",
        capacity_qualification=capacity,
        clock=lambda: recorded_at,
    )
    gateway = _restrict_gateway(gateway, provider_policy)
    runner = ControlledResearchEvaluation(
        repository_root=ROOT,
        workspace=workspace,
        fixture_root=fixture_root,
        model_gateway=gateway,
        capability_gateway=CapabilityGateway(
            {
                "local.deterministic.content-hash": LocalDeterministicCapability(
                    "local.deterministic.content-hash",
                    lambda value: {"content_sha256": canonical_sha256(value)},
                )
            }
        ),
        assurance=AssuranceBoundary(),
        provider_policy=provider_policy,
        permission_policy=_permission_policy(),
        run_id=run_id,
        clock=lambda: recorded_at,
    )
    return runner, provider_policy, runtime_contract


def _project_counts(project: Path, run_id: str) -> dict:
    event_path = project / ProjectRecord.EVENT_FILE
    trace_path = project / "run-traces" / f"{run_id}.jsonl"
    events = [
        json.loads(line)
        for line in event_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    trace = RunTrace(trace_path, run_id=run_id)
    rows = trace.events()
    return {
        "model_calls": sum(
            row.get("event_type") == "research.run.model.completed" for row in rows
        ),
        "tool_calls": sum(
            row.get("event_type") == "research.run.capability.requested"
            for row in rows
        ),
        "accepted_events": sum(
            row.get("event_type") == "research.project.accepted_change"
            and row.get("payload", {}).get("candidate", {}).get("fact", {}).get(
                "fact_id"
            )
            == "controlled-evaluation-candidate"
            for row in events
        ),
    }


def _recovery_snapshot_row(
    *,
    counts: Mapping[str, object],
    result: Mapping[str, object],
    snapshot_path: Path,
) -> dict:
    trace = result.get("run_trace")
    if not isinstance(trace, Mapping) or trace.get("status") != "passed":
        raise ValueError("recovery snapshot has no verified Run Trace")
    manifest = result.get("run_manifest")
    if not isinstance(manifest, Mapping):
        raise ValueError("recovery snapshot has no immutable run manifest")
    material_ref = manifest.get("material_ref")
    provider_route = manifest.get("provider_route")
    manifest_body = deepcopy(dict(manifest))
    manifest_sha256 = manifest_body.pop("manifest_sha256", None)
    if (
        not isinstance(material_ref, Mapping)
        or not isinstance(material_ref.get("sha256"), str)
        or not isinstance(provider_route, Mapping)
        or _runtime_canonical_sha256(manifest_body) != manifest_sha256
    ):
        raise ValueError("recovery run manifest binding drifted")
    return {
        **deepcopy(dict(counts)),
        "snapshot_ref": _relative_ref(snapshot_path, ROOT),
        "input_sha256": material_ref.get("sha256"),
        "provider_route_sha256": manifest.get("provider_policy_sha256"),
        "permission_policy_sha256": manifest.get("permission_policy_sha256"),
        "run_manifest_sha256": manifest_sha256,
        "provider_route": deepcopy(dict(provider_route)),
        "fixture_id": manifest.get("fixture_id"),
        "project_record_sha256": result.get("project_record_sha256"),
        "run_trace_sha256": trace.get("trace_sha256"),
        "run_trace_event_count": trace.get("event_count"),
        "run_trace_last_event_sha256": trace.get("last_event_sha256"),
        "run_id": trace.get("run_id"),
    }


def _missing_failure_diagnostics(
    *, output_root: Path, fixture_root: Path, successful_generation: str
) -> dict:
    material = _load_json(fixture_root / "fixture/missing-material-package.json")
    failures = []
    workspace_root = output_root / "missing/workspaces"
    for workspace in sorted(path for path in workspace_root.iterdir() if path.is_dir()):
        if workspace.name == successful_generation:
            continue
        for project in sorted((workspace / "projects").glob("*")):
            event_path = project / ProjectRecord.EVENT_FILE
            traces = sorted((project / "run-traces").glob("*.jsonl"))
            if not event_path.is_file() or len(traces) != 1:
                continue
            record = ProjectRecord.open_or_create_project(project)
            view = record.read_current_project_view()
            fact = view.get("facts", {}).get("model-analysis")
            if not isinstance(fact, Mapping) or not isinstance(
                fact.get("value"), Mapping
            ):
                continue
            domain = LCADomainKernel().evaluate_source_bound_interpretation(
                material["source_material"], fact["value"]
            )
            failures.append(
                {
                    "execution_generation": workspace.name,
                    "failure_layer": "context_failure",
                    "failure_code": "functional_unit_structural_signal_absent",
                    "domain_errors": deepcopy(domain.get("errors")),
                    "model_sufficiency": fact["value"].get("sufficiency"),
                    "model_statement_count": len(
                        fact["value"].get("result_statements") or []
                    ),
                    "project_record_ref": _relative_ref(event_path, ROOT),
                    "run_trace_ref": _relative_ref(traces[0], ROOT),
                    "preserved": True,
                }
            )
    diagnostic = {
        "contract": "d18_core_lca_failure_diagnostics/v1",
        "status": "resolved_by_one_generic_context_fix",
        "same_failure_category_fix_count": 1,
        "same_failure_category_fix_limit": 2,
        "failures": failures,
        "case_specific_route_added": False,
        "external_writes": 0,
    }
    diagnostic["diagnostic_sha256"] = canonical_sha256(diagnostic)
    return diagnostic


def execute_complete_branch(
    *,
    output_root: Path,
    credential_env_file: Path,
    recorded_at: str,
    runtime_root: Path = CALCULATION_RUNTIME,
) -> dict:
    started = time.monotonic()
    output_root = Path(output_root).resolve()
    contract = load_core_lca_quality_contract(CONTRACT_PATH)
    calculation_tranche = execute_acceptance_tranche(
        acceptance_root=output_root / "complete/calculation",
        fixture=CALCULATION_FIXTURE,
        runtime=Path(runtime_root).resolve(),
    )
    calculation = _calculation_evidence(output_root)
    _atomic_write(output_root / "complete/calculation-evidence.json", calculation)
    fixture_root = build_interpretation_fixture(
        calculation, contract, output_root / "complete/interpretation-fixture"
    )
    fixture_manifest = _load_json(fixture_root / "fixture-manifest.json")
    execution_generation = canonical_sha256(
        {
            "fixture_manifest_sha256": fixture_manifest["manifest_sha256"],
            "orchestrator_sha256": _file_sha256(
                ROOT / "lca_research_kernel/research_orchestrator.py"
            ),
            "controlled_interpretation_sha256": _file_sha256(
                ROOT / "lca_research_kernel/controlled_research_evaluation.py"
            ),
        }
    )[:16]
    runtime_contract = load_model_runtime_contract(ROOT)
    capacity = _load_json(CAPACITY_QUALIFICATION)
    environment = dict(os.environ)
    environment.update(
        load_credential_environment(
            credential_env_file, runtime_contract["provider_catalog"]
        )
    )
    provider_policy = provider_policy_for_schema_profile(
        runtime_contract, "generic_source_candidate_v1"
    )
    gateway = build_model_gateway(
        ROOT,
        environment,
        execution_mode="production",
        capacity_qualification=capacity,
        clock=lambda: recorded_at,
    )
    gateway = _restrict_gateway(gateway, provider_policy)
    workspace = (
        output_root / "complete/interpretation-workspaces" / execution_generation
    )
    runner = ControlledResearchEvaluation(
        repository_root=ROOT,
        workspace=workspace,
        fixture_root=fixture_root,
        model_gateway=gateway,
        capability_gateway=CapabilityGateway(
            {
                "local.deterministic.content-hash": LocalDeterministicCapability(
                    "local.deterministic.content-hash",
                    lambda value: {"content_sha256": canonical_sha256(value)},
                )
            }
        ),
        assurance=AssuranceBoundary(),
        provider_policy=provider_policy,
        permission_policy=_permission_policy(),
        run_id="d18-core-lca-interpretation-" + execution_generation,
        clock=lambda: recorded_at,
    )
    result = runner.run("complete_material")
    _atomic_write(output_root / "complete/interpretation-result.json", result)
    project = workspace / "projects" / result["research_id"]
    record = ProjectRecord.open_or_create_project(project)
    replay = record.rebuild_and_verify()
    record_path = project / ProjectRecord.EVENT_FILE
    package = _load_json(
        project / "artifacts/controlled-evaluation-machine-package.json"
    )
    trace_ref = package["run_trace_ref"]
    trace_path = project / "run-traces" / trace_ref["path"]
    interpretation = build_interpretation_evidence(
        calculation=calculation,
        model_candidate=package["model_candidate"],
        domain_acceptance=package["domain_acceptance"],
        project_record_ref=_relative_ref(record_path, ROOT),
        project_replay=replay,
        accepted_event_count=_accepted_candidate_event_count(
            record_path, "controlled-evaluation-candidate"
        ),
        run_trace_ref=_relative_ref(trace_path, ROOT),
        accepted_fact_binding=_accepted_fact_binding(
            record_path,
            "controlled-evaluation-candidate",
            accepted_content_key="model_output",
        ),
        derived_effects=_derived_effects(
            trace_path=trace_path,
            authorization_receipt_paths=[],
            project=project,
        ),
    )
    _atomic_write(output_root / "complete/interpretation-evidence.json", interpretation)
    secret_scan = _write_secret_scan(output_root)
    quality = CoreLCAQualityEvaluator(contract).evaluate_complete(
        calculation, interpretation, secret_scan
    )
    _atomic_write(output_root / "complete/quality-report.json", quality)
    fixture_lock = {
        "contract": "d18_core_lca_fixture_lock/v1",
        "status": "frozen",
        "execution_generation": execution_generation,
        "calculation_fixture_manifest": _relative_ref(
            CALCULATION_FIXTURE / "fixture-manifest.json", ROOT
        ),
        "calculation_oracle": {
            **_relative_ref(
                CALCULATION_FIXTURE / "oracle/calculation-oracle.json", ROOT
            ),
            "access": "evaluator_only_not_model_visible",
        },
        "calculation_evidence_sha256": canonical_sha256(calculation),
        "interpretation_fixture_manifest": _relative_ref(
            fixture_root / "fixture-manifest.json", ROOT
        ),
        "interpretation_evidence_sha256": canonical_sha256(interpretation),
        "execution_inputs": {
            "orchestrator_sha256": _file_sha256(
                ROOT / "lca_research_kernel/research_orchestrator.py"
            ),
            "controlled_interpretation_sha256": _file_sha256(
                ROOT / "lca_research_kernel/controlled_research_evaluation.py"
            ),
            "quality_contract_sha256": _file_sha256(CONTRACT_PATH),
            "provider_catalog_sha256": runtime_contract["provider_catalog_sha256"],
        },
        "project_record_links": {
            "calculation": deepcopy(calculation["project_record"]["ref"]),
            "interpretation": deepcopy(interpretation["project_record"]["ref"]),
        },
        "case_neutral_routing": True,
        "executed_external_writes": 0,
    }
    fixture_lock["lock_sha256"] = canonical_sha256(fixture_lock)
    _atomic_write(output_root / "fixture/fixture-lock.json", fixture_lock)
    model_candidate = package["model_candidate"]
    return {
        "contract": "d18_core_lca_complete_execution_receipt/v1",
        "status": "passed" if quality["edge_status"] == "passed" else "failed",
        "calculation_tranche": calculation_tranche,
        "calculation_sha256": canonical_sha256(calculation),
        "interpretation_sha256": canonical_sha256(interpretation),
        "quality_report_sha256": canonical_sha256(quality),
        "model_route": provider_policy["primary_route"]["route_id"],
        "model_observation": {
            "provider": model_candidate.get("provider"),
            "adapter_id": model_candidate.get("adapter_id"),
            "model": model_candidate.get("model"),
            "usage": deepcopy(model_candidate.get("usage")),
            "tool_request_count": len(model_candidate.get("tool_requests") or []),
        },
        "fixture_lock_sha256": fixture_lock["lock_sha256"],
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "executed_external_writes": 0,
    }


def _model_duration_from_trace(trace_path: Path, run_id: str) -> int:
    rows = RunTrace(trace_path, run_id=run_id).events()
    durations = [
        row.get("payload", {}).get("duration_ms")
        for row in rows
        if row.get("event_type") == "research.run.model.completed"
    ]
    if len(durations) != 1 or not isinstance(durations[0], int):
        raise ValueError("one measured model duration is required")
    return durations[0]


def reconcile_complete_registered_evidence(output_root: Path) -> dict:
    """Rebuild acceptance evidence from immutable raw records without execution."""

    output_root = Path(output_root).resolve()
    contract = load_core_lca_quality_contract(CONTRACT_PATH)
    calculation = _calculation_evidence(output_root)
    fixture_root = output_root / "complete/interpretation-fixture"
    fixture_manifest = _load_json(fixture_root / "fixture-manifest.json")
    if fixture_manifest.get("source_calculation_sha256") != calculation_execution_sha256(
        calculation
    ):
        raise ValueError("registered interpretation fixture no longer binds execution")
    _atomic_write(output_root / "complete/calculation-evidence.json", calculation)

    result = _load_json(output_root / "complete/interpretation-result.json")
    prior_interpretation = _load_json(
        output_root / "complete/interpretation-evidence.json"
    )
    prior_record_ref = prior_interpretation.get("project_record", {}).get("ref")
    if not isinstance(prior_record_ref, Mapping) or not isinstance(
        prior_record_ref.get("path"), str
    ):
        raise ValueError("registered complete interpretation record is absent")
    project = (ROOT / prior_record_ref["path"]).resolve(strict=True).parent
    if project.name != result["research_id"]:
        raise ValueError("registered complete interpretation identity drifted")
    record = ProjectRecord.open_or_create_project(project)
    replay = record.rebuild_and_verify()
    record_path = project / ProjectRecord.EVENT_FILE
    package = _load_json(
        project / "artifacts/controlled-evaluation-machine-package.json"
    )
    trace_path = project / "run-traces" / package["run_trace_ref"]["path"]
    interpretation = build_interpretation_evidence(
        calculation=calculation,
        model_candidate=package["model_candidate"],
        domain_acceptance=package["domain_acceptance"],
        project_record_ref=_relative_ref(record_path, ROOT),
        project_replay=replay,
        accepted_event_count=_accepted_candidate_event_count(
            record_path, "controlled-evaluation-candidate"
        ),
        run_trace_ref=_relative_ref(trace_path, ROOT),
        accepted_fact_binding=_accepted_fact_binding(
            record_path,
            "controlled-evaluation-candidate",
            accepted_content_key="model_output",
        ),
        derived_effects=_derived_effects(
            trace_path=trace_path,
            authorization_receipt_paths=[],
            project=project,
        ),
    )
    _atomic_write(output_root / "complete/interpretation-evidence.json", interpretation)
    secret_scan = _write_secret_scan(output_root)
    quality = CoreLCAQualityEvaluator(contract).evaluate_complete(
        calculation, interpretation, secret_scan
    )
    _atomic_write(output_root / "complete/quality-report.json", quality)

    fixture_lock_path = output_root / "fixture/fixture-lock.json"
    fixture_lock = _load_json(fixture_lock_path)
    fixture_lock.pop("lock_sha256", None)
    fixture_lock["calculation_evidence_sha256"] = canonical_sha256(calculation)
    fixture_lock["interpretation_evidence_sha256"] = canonical_sha256(
        interpretation
    )
    fixture_lock["project_record_links"] = {
        "calculation": deepcopy(calculation["project_record"]["ref"]),
        "interpretation": deepcopy(interpretation["project_record"]["ref"]),
    }
    fixture_lock["lock_sha256"] = canonical_sha256(fixture_lock)
    _atomic_write(fixture_lock_path, fixture_lock)

    receipt_path = output_root / "complete/execution-receipt.json"
    receipt = _load_json(receipt_path)
    receipt["status"] = "passed" if quality["edge_status"] == "passed" else "failed"
    receipt["calculation_sha256"] = canonical_sha256(calculation)
    receipt["interpretation_sha256"] = canonical_sha256(interpretation)
    receipt["quality_report_sha256"] = canonical_sha256(quality)
    receipt["fixture_lock_sha256"] = fixture_lock["lock_sha256"]
    receipt["evidence_reconciliation"] = {
        "contract": "d18_core_lca_registered_evidence_reconciliation/v1",
        "raw_model_calls_added": 0,
        "raw_tool_calls_added": 0,
        "raw_external_writes_added": 0,
    }
    _atomic_write(receipt_path, receipt)
    return receipt


def execute_missing_and_recovery_branch(
    *,
    output_root: Path,
    credential_env_file: Path,
    recorded_at: str,
) -> dict:
    """Run one real missing-data candidate, then resume and replay it."""

    output_root = Path(output_root).resolve()
    contract = load_core_lca_quality_contract(CONTRACT_PATH)
    fixture_root = output_root / "complete/interpretation-fixture"
    fixture_manifest = _load_json(fixture_root / "fixture-manifest.json")
    generation = canonical_sha256(
        {
            "fixture_manifest_sha256": fixture_manifest["manifest_sha256"],
            "branch": "missing_critical_field",
            "orchestrator_sha256": _file_sha256(
                ROOT / "lca_research_kernel/research_orchestrator.py"
            ),
            "controlled_interpretation_sha256": _file_sha256(
                ROOT / "lca_research_kernel/controlled_research_evaluation.py"
            ),
        }
    )[:16]
    run_id = "d18-core-lca-missing-" + generation
    workspace = output_root / "missing/workspaces" / generation
    runner, provider_policy, runtime_contract = _interpretation_runner(
        workspace=workspace,
        fixture_root=fixture_root,
        credential_env_file=credential_env_file,
        recorded_at=recorded_at,
        run_id=run_id,
    )
    prior_recovery_path = output_root / "recovery/recovery-evidence.json"
    prior_missing_path = output_root / "missing/missing-evaluation.json"
    if prior_recovery_path.is_file() and prior_missing_path.is_file():
        prior_recovery = _load_json(prior_recovery_path)
        prior_missing = _load_json(prior_missing_path)
    else:
        prior_recovery = prior_missing = None
    if (
        isinstance(prior_recovery, Mapping)
        and prior_recovery.get("contract") == "d18_core_lca_recovery_evidence/v1"
        and isinstance(prior_missing, Mapping)
        and prior_missing.get("status") == "formal_limited_result_accepted"
    ):
        checkpoint = _load_json(output_root / "missing/checkpoint.json")
        resumed = _load_json(output_root / "missing/resumed.json")
        project = workspace / "projects" / checkpoint["research_id"]
        checkpoint_counts = deepcopy(prior_recovery["checkpoint"])
        resumed_counts = deepcopy(prior_recovery["resumed"])
        replayed = runner.run("missing_critical_field")
        replayed_counts = _project_counts(project, run_id)
        prior_counts = {
            key: prior_recovery["replayed"].get(key)
            for key in ("model_calls", "tool_calls", "accepted_events")
        }
        if replayed_counts != prior_counts:
            raise ValueError("boundary replay added a model, tool, or accepted event")
    else:
        checkpoint = runner.run(
            "missing_critical_field", stop_after_model_candidate=True
        )
        project = workspace / "projects" / checkpoint["research_id"]
        checkpoint_counts = _project_counts(project, run_id)
        resumed = runner.run("missing_critical_field")
        resumed_counts = _project_counts(project, run_id)
        replayed = runner.run("missing_critical_field")
        replayed_counts = _project_counts(project, run_id)
    _atomic_write(output_root / "missing/checkpoint.json", checkpoint)
    _atomic_write(output_root / "missing/resumed.json", resumed)
    _atomic_write(output_root / "missing/replayed.json", replayed)

    checkpoint_row = _recovery_snapshot_row(
        counts=checkpoint_counts,
        result=checkpoint,
        snapshot_path=output_root / "missing/checkpoint.json",
    )
    resumed_row = _recovery_snapshot_row(
        counts=resumed_counts,
        result=resumed,
        snapshot_path=output_root / "missing/resumed.json",
    )
    replayed_row = _recovery_snapshot_row(
        counts=replayed_counts,
        result=replayed,
        snapshot_path=output_root / "missing/replayed.json",
    )
    recovery_input_sha = str(checkpoint_row["input_sha256"])
    recovery_provider_sha = str(checkpoint_row["provider_route_sha256"])
    recovery_permission_sha = str(checkpoint_row["permission_policy_sha256"])

    record = ProjectRecord.open_or_create_project(project)
    replay = record.rebuild_and_verify()
    package = _load_json(
        project / "artifacts/controlled-evaluation-machine-package.json"
    )
    missing_record_path = project / ProjectRecord.EVENT_FILE
    missing_trace_path = project / "run-traces" / f"{run_id}.jsonl"
    missing_evidence = build_missing_evidence(
        model_candidate=package["model_candidate"],
        domain_acceptance=package["domain_acceptance"],
        project_replay=replay,
        accepted_event_count=_accepted_candidate_event_count(
            missing_record_path, "controlled-evaluation-candidate"
        ),
        project_record_ref=_relative_ref(missing_record_path, ROOT),
        run_trace_ref=_relative_ref(missing_trace_path, ROOT),
        accepted_fact_binding=_accepted_fact_binding(
            missing_record_path,
            "controlled-evaluation-candidate",
            accepted_content_key="model_output",
        ),
        derived_effects=_derived_effects(
            trace_path=missing_trace_path,
            authorization_receipt_paths=[],
            project=project,
        ),
    )
    missing_evidence["provenance"] = {
        "provider": package["model_candidate"].get("provider"),
        "adapter_id": package["model_candidate"].get("adapter_id"),
        "model": package["model_candidate"].get("model"),
        "duration_ms": package["model_candidate"].get("duration_ms"),
        "usage": deepcopy(package["model_candidate"].get("usage")),
        "project_record_ref": _relative_ref(
            missing_record_path, ROOT
        ),
        "run_trace_ref": _relative_ref(
            missing_trace_path, ROOT
        ),
    }
    missing_report = CoreLCAQualityEvaluator(contract).evaluate_missing(
        missing_evidence
    )
    _atomic_write(output_root / "missing/missing-evidence.json", missing_evidence)
    _atomic_write(output_root / "missing/missing-evaluation.json", missing_report)

    recovery_evidence = {
        "contract": "d18_core_lca_recovery_evidence/v1",
        "input_sha256": recovery_input_sha,
        "provider_route_sha256": recovery_provider_sha,
        "permission_policy_sha256": recovery_permission_sha,
        "checkpoint": checkpoint_row,
        "resumed": resumed_row,
        "replayed": replayed_row,
        "repeated_effects": 0,
        "external_writes": 0,
    }
    recovery_report = CoreLCAQualityEvaluator(contract).evaluate_recovery(
        recovery_evidence
    )
    _atomic_write(output_root / "recovery/recovery-evidence.json", recovery_evidence)
    _atomic_write(output_root / "recovery/recovery-evaluation.json", recovery_report)
    _atomic_write(
        output_root / "recovery/failure-diagnostics.json",
        _missing_failure_diagnostics(
            output_root=output_root,
            fixture_root=fixture_root,
            successful_generation=generation,
        ),
    )

    complete_calculation = _load_json(
        output_root / "complete/calculation-evidence.json"
    )
    complete_interpretation = _load_json(
        output_root / "complete/interpretation-evidence.json"
    )
    combined_interpretation = deepcopy(complete_interpretation)
    combined_interpretation["cross_branch_gates"] = {
        "missing_data_boundary_verified": (
            missing_report["status"] == "formal_limited_result_accepted"
            and recovery_report["status"] == "passed"
        )
    }
    secret_scan = _write_secret_scan(output_root)
    combined_quality = CoreLCAQualityEvaluator(contract).evaluate_complete(
        complete_calculation, combined_interpretation, secret_scan
    )
    _atomic_write(output_root / "quality-report.json", combined_quality)

    execution_paths = [
        "lca_research_kernel/contracts/model-provider-catalog.json",
        "lca_research_kernel/contracts/model-runtime-policy.json",
        "lca_research_kernel/contracts/project-execution-policy.json",
        "runtime/model-python/source-lock.json",
        "lca_research_kernel/research_orchestrator.py",
        "lca_research_kernel/controlled_research_evaluation.py",
        "lca_research_kernel/context_assembler.py",
        "lca_research_kernel/model_gateway.py",
        "lca_research_kernel/model_provider_adapters.py",
        "lca_research_kernel/model_execution_package.py",
        "lca_research_kernel/model_runtime.py",
        "lca_research_kernel/provider_capacity.py",
        "lca_research_kernel/capability_gateway.py",
        "lca_research_kernel/capability_adapters.py",
        "lca_research_kernel/capability_runtime.py",
        "lca_research_kernel/lca_domain_kernel.py",
        "lca_research_kernel/assurance.py",
        "lca_research_kernel/project_record.py",
        "lca_research_kernel/run_trace.py",
        "lca_research_kernel/run_control.py",
        "lca_research_kernel/product_interface.py",
        "lca_research_kernel/execution_environment.py",
        "lca_research_evaluation/controlled_calculation.py",
        "scripts/probe_model_providers.py",
        "scripts/run_controlled_lca_calculation.py",
        "outputs/real-question-optimization/73101eec6999b27f/staged-regression-v1/provider-preflight/gemini-capacity-qualification.json",
        "outputs/d18-controlled-calculation-v2/fixture/material-package.json",
        "outputs/d18-controlled-calculation-v2/oracle/calculation-oracle.json",
        "outputs/d18-core-lca-quality-efficiency/complete/interpretation-fixture/fixture/material-package.json",
        "outputs/d18-core-lca-quality-efficiency/complete/interpretation-fixture/fixture/missing-material-package.json",
    ]
    scoring_paths = [
        "lca_research_kernel/contracts/core-lca-quality-efficiency-v1.json",
        "lca_research_evaluation/core_lca_quality_efficiency.py",
        "scripts/check_core_lca_quality_efficiency_evaluation.py",
    ]
    acceptance_paths = [
        "lca_research_kernel/runtime_environment_clean_review.py",
        "scripts/accept_runtime_environment.py",
        "scripts/requalify_runtime_environment_snapshot.py",
        "tests/test_core_lca_quality_efficiency.py",
        "tests/test_accept_runtime_environment.py",
        "tests/test_runtime_environment_clean_review.py",
        "tests/test_real_question_research_path.py",
        "tools/verify_project_state.py",
        "outputs/runtime-environment/runtime-readiness-matrix-source-binding-v8.json",
        "outputs/runtime-environment/runtime-health-summary-source-binding-v8.json",
        "outputs/runtime-environment/receipts/runtime-source-index-requalification-v8-20260803.json",
        "outputs/runtime-environment/project-record-v10/research-project-events.jsonl",
        "outputs/runtime-environment/project-record-v10/project-record.json",
    ]
    raw_execution_refs = {
        "calculation_project_record": complete_calculation["project_record"]["ref"],
        "calculation_run_trace": complete_calculation["run_trace"]["ref"],
        "interpretation_project_record": complete_interpretation["project_record"]["ref"],
        "interpretation_run_trace": complete_interpretation["run_trace"]["ref"],
        "missing_project_record": missing_evidence["project_record"]["ref"],
        "missing_run_trace": missing_evidence["run_trace"]["ref"],
        "calculation_authorization_context": complete_calculation["effects"][
            "authorization_context"
        ],
        "calculation_authorization_receipts": complete_calculation["effects"][
            "authorization_receipt_refs"
        ],
    }
    raw_execution_source_paths = [
        "lca_research_evaluation/core_lca_quality_efficiency.py",
        "scripts/run_core_lca_quality_efficiency_evaluation.py",
    ]
    immutable_execution_git_objects = [
        _git_object_ref(
            "5c7e6252f37e28b11a75e642bf0d03f4525b1f00", path
        )
        for path in sorted(
            {
                *execution_paths,
                *raw_execution_source_paths,
            }
        )
    ]
    locks = build_core_lca_evidence_locks(
        ROOT,
        execution_paths=execution_paths,
        scoring_paths=scoring_paths,
        acceptance_paths=acceptance_paths,
        basis={
            "accepted_execution_code_basis": "5c7e6252f37e28b11a75e642bf0d03f4525b1f00",
            "provider_catalog_sha256": runtime_contract["provider_catalog_sha256"],
            "source_lock_sha256": runtime_contract["source_lock_sha256"],
            "raw_execution_refs_sha256": canonical_sha256(raw_execution_refs),
            "raw_execution_refs": raw_execution_refs,
            "raw_execution_source_paths": raw_execution_source_paths,
            "immutable_execution_git_objects": immutable_execution_git_objects,
        },
    )
    lock_paths = {
        "execution": "evidence/execution-lock.json",
        "scoring": "evidence/scoring-lock.json",
        "acceptance": "evidence/quality-acceptance-lock.json",
    }
    prior_locks = None
    if all((output_root / path).is_file() for path in lock_paths.values()):
        prior_locks = {
            layer: _load_json(output_root / path)
            for layer, path in lock_paths.items()
        }
    comparison = None
    if prior_locks is not None:
        comparison = compare_core_lca_evidence_locks(prior_locks, locks)
        if comparison["execution_reusable"] is not True:
            for layer, lock in locks.items():
                _atomic_write(
                    output_root / f"evidence/candidate-{layer}-lock.json", lock
                )
            diagnostic = {
                "contract": "d18_core_lca_execution_lock_change_diagnostic/v1",
                "status": "fail_closed_requires_explicit_basis_adoption",
                "comparison": comparison,
                "raw_execution_refs_sha256": canonical_sha256(raw_execution_refs),
                "immutable_execution_git_objects": immutable_execution_git_objects,
                "product_or_model_visible_execution_rerun": False,
            }
            diagnostic["diagnostic_sha256"] = canonical_sha256(diagnostic)
            _atomic_write(
                output_root / "evidence/execution-lock-change-diagnostic.json",
                diagnostic,
            )
            raise ValueError("execution evidence changed; rerun affected execution")
    for layer, lock in locks.items():
        _atomic_write(output_root / lock_paths[layer], lock)
    if comparison is None:
        comparison = {
            "changed_paths": [],
            "rerun_route": "no_evidence_invalidation",
            "execution_reusable": True,
        }
    reuse = {
        "contract": "d18_core_lca_evidence_reuse_receipt/v1",
        "status": "accepted",
        "execution_lock_sha256": locks["execution"]["lock_sha256"],
        "complete_evidence_sha256": _file_sha256(
            output_root / "complete/acceptance.json"
        ),
        "missing_evidence_sha256": canonical_sha256(missing_evidence),
        "recovery_evidence_sha256": canonical_sha256(recovery_evidence),
        "failed_rows_preserved": True,
        "cross_version_splicing": False,
        "changed_paths": deepcopy(comparison["changed_paths"]),
        "rerun_route": comparison["rerun_route"],
        "execution_reusable": comparison["execution_reusable"],
        "prior_execution_lock_sha256": comparison.get(
            "adopted_execution_lock_sha256"
        ),
    }
    migration_path = output_root / "evidence/reviewed-execution-basis-migration.json"
    if migration_path.is_file():
        reuse["reviewed_execution_basis_migration_ref"] = _relative_ref(
            migration_path, ROOT
        )
    reuse["receipt_sha256"] = canonical_sha256(reuse)
    _atomic_write(output_root / "evidence/evidence-reuse-receipt.json", reuse)

    calculation_trace_path = ROOT / complete_calculation["run_trace"]["ref"]["path"]
    complete_trace_path = ROOT / complete_interpretation["run_trace"]["ref"]["path"]
    diagnostics = _load_json(output_root / "recovery/failure-diagnostics.json")
    derived_metrics = derive_efficiency_metrics(
        calculation_trace=_trace_rows(calculation_trace_path),
        interpretation_traces=[
            _trace_rows(complete_trace_path),
            _trace_rows(missing_trace_path),
        ],
        failure_diagnostics=diagnostics,
    )
    measurement = {
        "contract": "core_lca_quality_efficiency_measurement/v1",
        "status": "accepted_measurement_with_known_critical_path_gap",
        "quality_first": True,
        "metrics": derived_metrics,
        "measurement_basis": {
            "contract": "d18_core_lca_trace_derived_measurement_basis/v1",
            "calculation_trace_ref": deepcopy(
                complete_calculation["run_trace"]["ref"]
            ),
            "complete_interpretation_trace_ref": deepcopy(
                complete_interpretation["run_trace"]["ref"]
            ),
            "missing_interpretation_trace_ref": deepcopy(
                missing_evidence["run_trace"]["ref"]
            ),
            "failure_diagnostics_ref": _relative_ref(
                output_root / "recovery/failure-diagnostics.json", ROOT
            ),
            "accepted_result_timing_status": "not_measured",
            "delivered_result_timing_status": "not_measured",
            "primary_provider_duration_ms": derived_metrics[
                "primary_provider_duration_ms"
            ],
            "provider_model_duration_ms_total": derived_metrics[
                "provider_model_duration_ms_total"
            ],
            "missing_branch_wall_status": "not_measured",
            "missing_branch_wall_ms": None,
            "complete_wall_clock_status": "not_retrospectively_invented",
            "known_excluded_time": ["initial_openlca_wall_duration"],
        },
        "quality_gate_failures_before_clean_acceptance": combined_quality[
            "quality_gate_failures"
        ],
    }
    _atomic_write(
        output_root / "core-lca-quality-efficiency-receipt.json", measurement
    )
    status = (
        "passed"
        if missing_report["status"] == "formal_limited_result_accepted"
        and recovery_report["status"] == "passed"
        else "failed"
    )
    receipt = {
        "contract": "d18_core_lca_boundary_execution_receipt/v1",
        "status": status,
        "model_route": provider_policy["primary_route"]["route_id"],
        "missing_evaluation_sha256": canonical_sha256(missing_report),
        "recovery_evaluation_sha256": canonical_sha256(recovery_report),
        "quality_report_sha256": canonical_sha256(combined_quality),
        "evidence_lock_sha256s": {
            layer: lock["lock_sha256"] for layer, lock in locks.items()
        },
        "model_observation": {
            "provider": package["model_candidate"].get("provider"),
            "adapter_id": package["model_candidate"].get("adapter_id"),
            "model": package["model_candidate"].get("model"),
            "duration_ms": package["model_candidate"].get("duration_ms"),
            "tool_request_count": len(
                package["model_candidate"].get("tool_requests") or []
            ),
        },
        "executed_external_writes": 0,
    }
    _atomic_write(output_root / "missing/execution-receipt.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "outputs/d18-core-lca-quality-efficiency",
    )
    parser.add_argument(
        "--credential-env-file", type=Path, default=DEFAULT_CREDENTIAL_ENV
    )
    parser.add_argument("--runtime", type=Path, default=CALCULATION_RUNTIME)
    parser.add_argument("--recorded-at", required=True)
    parser.add_argument(
        "--mode", choices=("complete", "boundaries", "reconcile"), default="complete"
    )
    args = parser.parse_args()
    if args.mode == "complete":
        receipt = execute_complete_branch(
            output_root=args.output_root,
            credential_env_file=args.credential_env_file,
            recorded_at=args.recorded_at,
            runtime_root=args.runtime,
        )
        _atomic_write(args.output_root / "complete/execution-receipt.json", receipt)
    elif args.mode == "boundaries":
        receipt = execute_missing_and_recovery_branch(
            output_root=args.output_root,
            credential_env_file=args.credential_env_file,
            recorded_at=args.recorded_at,
        )
    else:
        reconcile_complete_registered_evidence(args.output_root)
        receipt = execute_missing_and_recovery_branch(
            output_root=args.output_root,
            credential_env_file=args.credential_env_file,
            recorded_at=args.recorded_at,
        )
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "model_route": receipt["model_route"],
                "executed_external_writes": 0,
            },
            sort_keys=True,
        )
    )
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
