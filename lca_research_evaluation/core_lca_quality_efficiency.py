"""Deterministic quality-first evaluation for the D-18 core LCA path.

This module evaluates registered evidence.  It does not own a Project Record,
invoke a model or capability, or accept a scientific fact.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Iterable, Mapping, Optional


CONTRACT_ID = "d18_core_lca_quality_efficiency/v1"
CODE_BASIS_RE = re.compile(r"^[0-9a-f]{40}$")
EDGE_IDS = {
    "goal_scope_compilation",
    "tiangong_data_binding",
    "tidas_validation",
    "lci_lcia_calculation",
    "scientific_interpretation",
    "assurance_acceptance",
    "quality_first_efficiency",
}
LOCK_IDS = {"execution_lock", "scoring_lock", "acceptance_lock"}
EVIDENCE_ROUTES = {
    "rerun_affected_execution",
    "regrade_existing_traces",
    "rerun_clean_acceptance",
    "rebuild_report_only",
    "no_evidence_invalidation",
    "unknown_change_fail_closed",
}
BRANCH_ROLES = {
    "complete": "primary_path",
    "missing": "bounded_scenario",
    "failure_recovery": "robustness",
}
FAILURE_LAYERS = {
    "prompt_failure",
    "context_failure",
    "harness_failure",
    "loop_failure",
    "model_limitation",
}
QUALITY_GATES = {
    "goal_scope_fu_boundary_stage_use_complete",
    "tiangong_identity_version_source_provider_exact",
    "units_methods_and_numbers_exact",
    "missing_data_prevents_complete_conclusion",
    "candidate_outputs_cannot_bypass_assurance",
    "project_record_replay_and_exactly_once_acceptance",
    "zero_forbidden_effects_and_secrets",
    "case_neutral_product_routing",
    "clean_review_p1_zero_p2_zero",
    "full_repository_verification_passed",
}
TERMINAL_ROUTE = "run-first-user-lca-commission-through-core-quality-contract"
FORBIDDEN_ROUTING_TOKENS = {"cbam", "lifepo4", "freshwater", "nev", "uslci"}
ALLOWED_LOCAL_EFFECTS = {"local_read", "local_write"}
RELEVANT_CORE_CAPABILITIES = {"tidas.validate", "openlca.calculate"}
OPERATION_RECEIPT_CONTRACT = (
    "lca_research_kernel.project_operation_authorization/v1"
)
AUTHORIZATION_CONTEXT_CONTRACT = "d18_core_lca_authorization_context/v1"
SECRET_PATTERNS = (
    ("authorization_header", re.compile(rb"authorization\s*:", re.IGNORECASE)),
    ("bearer_token", re.compile(rb"\bbearer\s+[A-Za-z0-9._~+/=-]{4,}", re.IGNORECASE)),
    (
        "credential_assignment",
        re.compile(
            rb"\b(?:api[_-]?key|password|secret|access[_-]?token)[\"']?\s*[=:]",
            re.IGNORECASE,
        ),
    ),
    ("openai_key", re.compile(rb"\bsk-[A-Za-z0-9_-]{8,}")),
    ("gemini_key", re.compile(rb"\bAIza[0-9A-Za-z_-]{20,}")),
    (
        "compound_api_key",
        re.compile(rb"\b[A-Za-z0-9]{32}\.[A-Za-z0-9_-]{32,}\b"),
    ),
    ("private_key", re.compile(rb"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----")),
)


def _fail(field: str, reason: str) -> None:
    raise ValueError(f"{field}: {reason}")


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def calculation_execution_sha256(calculation: Mapping[str, object]) -> str:
    """Hash only the scientific execution payload, excluding review metadata."""

    keys = (
        "contract",
        "goal_scope",
        "source_binding",
        "tidas_validation",
        "calculation",
        "candidate_boundary",
        "project_record",
        "run_trace",
        "effects",
    )
    payload = {key: deepcopy(calculation.get(key)) for key in keys}
    # These fields were added by acceptance hardening after the raw execution.
    # Remove them when reconstructing the immutable model-visible execution key.
    project_record = payload.get("project_record")
    if isinstance(project_record, dict):
        project_record.pop("accepted_fact", None)
    effects = payload.get("effects")
    if isinstance(effects, dict):
        payload["effects"] = {
            "external_writes": effects.get("external_writes"),
            "forbidden_effects": effects.get("forbidden_effects"),
        }
    return canonical_sha256(payload)


def _receipt_sha256(value: Mapping[str, object]) -> str:
    """Return the authorization-receipt digest used by the execution kernel."""

    body = deepcopy(dict(value))
    body.pop("receipt_sha256", None)
    return hashlib.sha256(
        json.dumps(
            body,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def derive_effect_summary(
    trace_rows: Iterable[Mapping[str, object]],
    authorization_receipts: Iterable[Mapping[str, object]],
    authorization_context: Mapping[str, object],
) -> dict:
    """Derive effect counts from trace events and authorization receipts.

    This deliberately does not accept a caller-supplied ``external_writes=0``.
    Every capability request must have one matching completion and one exact,
    receipt bound to a separately reconstructed execution identity, policy,
    source lock, entrypoint and argument digest.
    """

    if (
        authorization_context.get("contract") != AUTHORIZATION_CONTEXT_CONTRACT
        or authorization_context.get("operation_receipt_contract")
        != OPERATION_RECEIPT_CONTRACT
        or not isinstance(authorization_context.get("execution_id"), str)
        or re.fullmatch(
            r"[0-9a-f]{64}", str(authorization_context.get("policy_sha256"))
        )
        is None
        or not isinstance(authorization_context.get("operations"), list)
    ):
        raise ValueError("authorization context is invalid")
    expected_operations = {}
    operation_fields = {
        "operation_id",
        "capability_id",
        "effect",
        "source_lock_sha256",
        "arguments_sha256",
        "entrypoint",
    }
    for operation in authorization_context["operations"]:
        if not isinstance(operation, Mapping) or set(operation) != operation_fields:
            raise ValueError("authorization context operation is invalid")
        operation_id = operation.get("operation_id")
        if not isinstance(operation_id, str) or operation_id in expected_operations:
            raise ValueError("authorization context operation identity is invalid")
        if any(
            re.fullmatch(r"[0-9a-f]{64}", str(operation.get(field))) is None
            for field in ("source_lock_sha256", "arguments_sha256")
        ):
            raise ValueError("authorization context operation digest is invalid")
        expected_operations[operation_id] = operation

    receipts: dict[str, Mapping[str, object]] = {}
    for receipt in authorization_receipts:
        operation_id = receipt.get("operation_id")
        if not isinstance(operation_id, str) or operation_id in receipts:
            raise ValueError("authorization receipt identity is invalid")
        if (
            receipt.get("contract") != OPERATION_RECEIPT_CONTRACT
            or receipt.get("status") != "authorized"
        ):
            raise ValueError("capability was not authorized")
        if receipt.get("receipt_sha256") != _receipt_sha256(receipt):
            raise ValueError("authorization receipt digest drifted")
        expected = expected_operations.get(operation_id)
        if not isinstance(expected, Mapping) or any(
            receipt.get(field) != expected.get(field)
            for field in operation_fields
            if field != "operation_id"
        ):
            raise ValueError("authorization context binding drifted")
        if (
            receipt.get("execution_id") != authorization_context["execution_id"]
            or receipt.get("policy_sha256")
            != authorization_context["policy_sha256"]
        ):
            raise ValueError("authorization context binding drifted")
        receipts[operation_id] = receipt

    if set(receipts) != set(expected_operations):
        raise ValueError("authorization context is incomplete")

    requested: dict[str, Mapping[str, object]] = {}
    completed: dict[str, Mapping[str, object]] = {}
    model_tool_requests = 0
    for row in trace_rows:
        event_type = row.get("event_type")
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if event_type == "research.run.capability.requested":
            request_id = payload.get("request_id")
            if not isinstance(request_id, str) or request_id in requested:
                raise ValueError("capability request identity is invalid")
            requested[request_id] = payload
        elif event_type == "research.run.capability.completed":
            request_id = payload.get("request_id")
            if not isinstance(request_id, str) or request_id in completed:
                raise ValueError("capability completion identity is invalid")
            completed[request_id] = payload
        elif event_type == "research.run.model.completed":
            candidate = payload.get("candidate")
            if isinstance(candidate, Mapping):
                tool_requests = candidate.get("tool_requests")
                if isinstance(tool_requests, list):
                    model_tool_requests += len(tool_requests)

    if set(requested) != set(completed) or set(requested) != set(receipts):
        raise ValueError("capability trace and authorization receipts are incomplete")

    effects = []
    capability_ids = []
    receipt_refs = []
    for request_id in sorted(requested):
        request = requested[request_id]
        completion = completed[request_id]
        receipt = receipts[request_id]
        identity = (
            request.get("capability_id"),
            request.get("effect"),
            request.get("operation_receipt_sha256"),
        )
        if identity != (
            completion.get("capability_id"),
            completion.get("effect"),
            completion.get("operation_receipt_sha256"),
        ) or identity != (
            receipt.get("capability_id"),
            receipt.get("effect"),
            receipt.get("receipt_sha256"),
        ):
            raise ValueError("capability trace/receipt binding drifted")
        if completion.get("result_status") != "sourced_result_not_fact":
            raise ValueError("capability output bypassed the candidate boundary")
        effects.append(str(request.get("effect")))
        capability_ids.append(str(request.get("capability_id")))
        receipt_refs.append(str(receipt.get("receipt_sha256")))

    forbidden = [effect for effect in effects if effect not in ALLOWED_LOCAL_EFFECTS]
    return {
        "contract": "d18_core_lca_derived_effect_summary/v1",
        "tool_calls": len(requested),
        "model_tool_requests": model_tool_requests,
        "capability_ids": capability_ids,
        "authorization_receipt_sha256s": receipt_refs,
        "authorization_context_sha256": canonical_sha256(authorization_context),
        "allowed_local_effects": sorted(effects),
        "external_writes": sum(
            effect in {"external_write", "publish", "payment", "irreversible"}
            for effect in effects
        ),
        "forbidden_effects": len(forbidden),
    }


def derive_efficiency_metrics(
    *,
    calculation_trace: Iterable[Mapping[str, object]],
    interpretation_traces: Iterable[Iterable[Mapping[str, object]]],
    failure_diagnostics: Mapping[str, object],
) -> dict:
    """Compile metrics exclusively from immutable trace/diagnostic rows."""

    calculation_rows = list(calculation_trace)
    interpretation_rows = [list(rows) for rows in interpretation_traces]
    all_rows = [*calculation_rows, *(row for rows in interpretation_rows for row in rows)]
    model_requested = [
        row for row in all_rows if row.get("event_type") == "research.run.model.requested"
    ]
    model_completed = [
        row for row in all_rows if row.get("event_type") == "research.run.model.completed"
    ]
    tool_requested = [
        row
        for row in calculation_rows
        if row.get("event_type") == "research.run.capability.requested"
    ]
    step_elapsed = [
        row.get("payload", {}).get("elapsed_ms")
        for row in calculation_rows
        if row.get("event_type") == "research.run.step.started"
        and isinstance(row.get("payload"), Mapping)
        and isinstance(row.get("payload", {}).get("elapsed_ms"), int)
    ]
    entered = [
        row.get("payload", {}).get("entered_at")
        for row in calculation_rows
        if row.get("event_type") == "research.run.answer.requested"
        and isinstance(row.get("payload"), Mapping)
        and isinstance(row.get("payload", {}).get("entered_at"), str)
    ]
    model_durations = [
        row.get("payload", {}).get("duration_ms")
        for row in model_completed
        if isinstance(row.get("payload"), Mapping)
        and isinstance(row.get("payload", {}).get("duration_ms"), int)
    ]
    requested_ids = [
        row.get("payload", {}).get("request_id")
        for row in tool_requested
        if isinstance(row.get("payload"), Mapping)
    ]
    retry_count = sum(
        max(int(row.get("payload", {}).get("attempt", 1)) - 1, 0)
        for row in model_requested
        if isinstance(row.get("payload"), Mapping)
    ) + (len(requested_ids) - len(set(requested_ids)))
    capability_ids = [
        row.get("payload", {}).get("capability_id")
        for row in tool_requested
        if isinstance(row.get("payload"), Mapping)
    ]
    failures = failure_diagnostics.get("failures")
    if not isinstance(failures, list):
        raise ValueError("failure diagnostics are not measurable")
    primary_duration = model_durations[0] if model_durations else None
    return {
        "request_started_at": entered[0] if len(entered) == 1 else None,
        "first_state_change_ms": min(step_elapsed) if step_elapsed else None,
        # The historical traces instrument provider calls but not the complete
        # request -> Assurance -> user-delivery critical path.  Do not relabel
        # a provider duration as an accepted or delivered result time.
        "accepted_result_ms": None,
        "delivered_result_ms": None,
        "primary_provider_duration_ms": primary_duration,
        "provider_model_duration_ms_total": sum(model_durations),
        "model_calls": len(model_completed),
        "tool_calls": len(tool_requested),
        "irrelevant_tool_calls": sum(
            capability_id not in RELEVANT_CORE_CAPABILITIES
            for capability_id in capability_ids
        ),
        "retry_count": retry_count,
        "replan_count": sum("replan" in str(row.get("event_type")) for row in all_rows),
        "rework_count": len(failures),
    }


def scan_secret_material(root: Path, paths: Iterable[Path]) -> dict:
    """Scan an exact file set without retaining or reporting secret values."""

    root = Path(root).resolve()
    rows = []
    findings = []
    for target in sorted({Path(path).resolve() for path in paths}):
        try:
            relative = target.relative_to(root)
        except ValueError as error:
            raise ValueError("secret scan target escapes the registered root") from error
        if not target.is_file():
            raise ValueError(f"secret scan target is absent: {relative}")
        content = target.read_bytes()
        rows.append(
            {
                "path": relative.as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
        )
        for marker_id, pattern in SECRET_PATTERNS:
            if pattern.search(content):
                findings.append(
                    {"path": relative.as_posix(), "marker_id": marker_id}
                )
    receipt = {
        "contract": "d18_core_lca_secret_scan/v1",
        "status": "passed" if not findings else "failed",
        "targets": rows,
        "findings": findings,
        "finding_count": len(findings),
        "secret_values_retained": False,
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def _atomic_write(path: Path, value: object) -> None:
    content = _canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == content:
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _require_exact_keys(field: str, value: object, expected: set[str]) -> None:
    if not isinstance(value, Mapping) or set(value) != expected:
        _fail(field, f"must contain exactly {sorted(expected)}")


def validate_core_lca_quality_contract(contract: Mapping[str, object]) -> dict:
    """Fail closed unless the stable evaluation contract is internally exact."""

    if contract.get("contract") != CONTRACT_ID:
        _fail("contract", "unsupported")
    identity = contract.get("identity")
    if not isinstance(identity, Mapping):
        _fail("identity", "must be an object")
    code_basis = identity.get("code_basis")
    if not isinstance(code_basis, str) or CODE_BASIS_RE.fullmatch(code_basis) is None:
        _fail("identity.code_basis", "must be an immutable Git commit")
    if contract.get("wip_limit") != 1:
        _fail("wip_limit", "must be one")

    edges = contract.get("executable_edges")
    if not isinstance(edges, list) or len(edges) != len(EDGE_IDS):
        _fail("executable_edges", "must contain the seven registered edges")
    edge_ids = {
        row.get("edge_id") for row in edges if isinstance(row, Mapping)
    }
    if edge_ids != EDGE_IDS or any(
        not isinstance(row, Mapping)
        or set(row)
        != {"edge_id", "input_contract", "output_contract", "acceptance_predicate"}
        for row in edges
    ):
        _fail("executable_edges", "identity or typed edge fields drifted")

    dag = contract.get("evidence_dag")
    if not isinstance(dag, Mapping):
        _fail("evidence_dag", "must be an object")
    _require_exact_keys("evidence_dag.locks", dag.get("locks"), LOCK_IDS)
    routes = dag.get("rerun_routes")
    if not isinstance(routes, list) or set(routes) != EVIDENCE_ROUTES or len(routes) != 6:
        _fail("evidence_dag.rerun_routes", "must contain the six exact routes")

    roles = contract.get("branch_roles")
    if roles != BRANCH_ROLES:
        if isinstance(roles, Mapping):
            for key, expected in BRANCH_ROLES.items():
                if roles.get(key) != expected:
                    _fail(f"branch_roles.{key}", "role drifted")
        _fail("branch_roles", "must contain the exact branch roles")

    gates = contract.get("quality_gates")
    if not isinstance(gates, list) or set(gates) != QUALITY_GATES or len(gates) != 10:
        _fail("quality_gates", "must contain the ten non-compensable gates")
    layers = contract.get("failure_layers")
    if not isinstance(layers, Mapping) or set(layers) != FAILURE_LAYERS:
        _fail("failure_layers", "must contain the five diagnostic layers")
    terminal = contract.get("terminal_next_routes")
    if terminal != [TERMINAL_ROUTE]:
        _fail("terminal_next_routes", "must contain exactly one frozen route")

    routing_text = json.dumps(
        {
            "primary_closure_path": contract.get("primary_closure_path"),
            "beat_routes": contract.get("beat_routes"),
            "terminal_next_routes": terminal,
        },
        ensure_ascii=False,
        sort_keys=True,
    ).lower()
    for token in FORBIDDEN_ROUTING_TOKENS:
        if token in routing_text:
            _fail("routing", f"case token {token} is forbidden")
    return deepcopy(dict(contract))


def load_core_lca_quality_contract(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"contract: unreadable: {path}") from error
    if not isinstance(value, dict):
        raise ValueError("contract: root must be an object")
    return validate_core_lca_quality_contract(value)


class CoreLCAQualityEvaluator:
    """Pure evidence evaluator; full edge checks are added by the next beat."""

    def __init__(self, contract: Mapping[str, object]):
        self.contract = validate_core_lca_quality_contract(contract)

    def empty_quality_report_for_test(self) -> dict:
        return {
            "contract": "d18_core_lca_quality_report/v1",
            "status": "passed",
            "quality_gates": {gate: True for gate in sorted(QUALITY_GATES)},
        }

    def evaluate_complete(
        self,
        calculation: Mapping[str, object],
        interpretation: Mapping[str, object],
        secret_scan: Optional[Mapping[str, object]] = None,
    ) -> dict:
        """Evaluate the linked calculation and interpretation records.

        The inputs are normalized, content-addressed evidence packages produced
        by the thin runner.  This method is read-only and never accepts facts.
        """

        edge_gates = {
            "goal_scope_compilation": self._goal_scope_valid(calculation),
            "tiangong_data_binding": self._source_binding_valid(calculation),
            "tidas_validation": self._tidas_validation_valid(calculation),
            "lci_lcia_calculation": self._calculation_valid(calculation),
            "scientific_interpretation": self._interpretation_valid(
                calculation, interpretation
            ),
            "assurance_acceptance": self._assurance_valid(
                calculation, interpretation
            ),
            "quality_first_efficiency": (
                isinstance(interpretation.get("quality_sequence"), Mapping)
                and interpretation["quality_sequence"].get(
                    "efficiency_compiled_before_quality"
                )
                is False
            ),
        }
        edge_failures = sorted(
            edge_id for edge_id, passed in edge_gates.items() if not passed
        )
        calculation_record = calculation.get("project_record")
        interpretation_record = interpretation.get("project_record")
        replay_verified = bool(
            isinstance(calculation_record, Mapping)
            and calculation_record.get("replay_verified") is True
            and isinstance(interpretation_record, Mapping)
            and interpretation_record.get("replay_verified") is True
        )
        effects = [calculation.get("effects"), interpretation.get("effects")]
        zero_effects = all(
            isinstance(row, Mapping)
            and row.get("external_writes") == 0
            and row.get("forbidden_effects") == 0
            for row in effects
        )
        secret_scan_passed = bool(
            isinstance(secret_scan, Mapping)
            and secret_scan.get("contract") == "d18_core_lca_secret_scan/v1"
            and secret_scan.get("status") == "passed"
            and secret_scan.get("finding_count") == 0
            and secret_scan.get("secret_values_retained") is False
            and secret_scan.get("receipt_sha256")
            == canonical_sha256(
                {
                    key: value
                    for key, value in secret_scan.items()
                    if key != "receipt_sha256"
                }
            )
        )
        routing = interpretation.get("routing")
        cross_branch = interpretation.get("cross_branch_gates")
        acceptance = interpretation.get("acceptance_gates")
        quality_gates = {
            "goal_scope_fu_boundary_stage_use_complete": edge_gates[
                "goal_scope_compilation"
            ],
            "tiangong_identity_version_source_provider_exact": edge_gates[
                "tiangong_data_binding"
            ],
            "units_methods_and_numbers_exact": (
                edge_gates["tidas_validation"]
                and edge_gates["lci_lcia_calculation"]
            ),
            "missing_data_prevents_complete_conclusion": bool(
                isinstance(cross_branch, Mapping)
                and cross_branch.get("missing_data_boundary_verified") is True
            ),
            "candidate_outputs_cannot_bypass_assurance": (
                edge_gates["scientific_interpretation"]
                and edge_gates["assurance_acceptance"]
            ),
            "project_record_replay_and_exactly_once_acceptance": (
                replay_verified and edge_gates["assurance_acceptance"]
            ),
            "zero_forbidden_effects_and_secrets": (
                zero_effects and secret_scan_passed
            ),
            "case_neutral_product_routing": bool(
                isinstance(routing, Mapping) and routing.get("case_neutral") is True
            ),
            "clean_review_p1_zero_p2_zero": bool(
                isinstance(acceptance, Mapping)
                and acceptance.get("clean_review_p1_zero_p2_zero") is True
            ),
            "full_repository_verification_passed": bool(
                isinstance(acceptance, Mapping)
                and acceptance.get("full_repository_verification_passed") is True
            ),
        }
        quality_failures = sorted(
            key for key, passed in quality_gates.items() if not passed
        )
        edge_status = "passed" if not edge_failures else "failed"
        status = (
            "failed"
            if edge_failures
            else "passed"
            if not quality_failures
            else "pending_acceptance"
        )
        return {
            "contract": "d18_core_lca_quality_report/v1",
            "status": status,
            "edge_status": edge_status,
            "edge_gates": edge_gates,
            "edge_failures": edge_failures,
            "quality_gates": quality_gates,
            "quality_gate_failures": quality_failures,
            "secret_scan_sha256": (
                canonical_sha256(secret_scan)
                if isinstance(secret_scan, Mapping)
                else None
            ),
            "project_record_replay": {
                "verified": replay_verified,
                "calculation_ref": deepcopy(
                    calculation_record.get("ref")
                    if isinstance(calculation_record, Mapping)
                    else None
                ),
                "interpretation_ref": deepcopy(
                    interpretation_record.get("ref")
                    if isinstance(interpretation_record, Mapping)
                    else None
                ),
            },
            "executed_external_writes": 0 if zero_effects else None,
            "calculation_sha256": canonical_sha256(calculation),
            "interpretation_sha256": canonical_sha256(interpretation),
        }

    def evaluate_missing(self, evidence: Mapping[str, object]) -> dict:
        """Accept a missing-data result only when its recovery boundary is exact."""

        candidate = evidence.get("candidate")
        domain = evidence.get("domain_acceptance")
        project_record = evidence.get("project_record")
        failure = evidence.get("failure_policy")
        effects = evidence.get("effects")
        resume = candidate.get("resume_condition") if isinstance(candidate, Mapping) else None
        gates = {
            "structured_gap": bool(
                evidence.get("contract") == "d18_core_lca_missing_evidence/v1"
                and isinstance(candidate, Mapping)
                and candidate.get("status") == "candidate_only"
                and candidate.get("sufficiency") == "insufficient_critical_data"
                and candidate.get("missing_fields") == ["functional_unit"]
                and isinstance(candidate.get("scientific_consequence"), str)
                and candidate["scientific_consequence"].strip()
            ),
            "resume_condition": bool(
                isinstance(resume, Mapping)
                and resume.get("field") == "functional_unit"
                and resume.get("required_contract")
                == "amount_unit_and_reference_flow"
                and resume.get("source_binding_required") is True
            ),
            "no_complete_numeric_conclusion": bool(
                isinstance(candidate, Mapping)
                and candidate.get("result_statements") == []
                and candidate.get("numeric_conclusion") is None
                and candidate.get("tool_requests") == []
            ),
            "domain_boundary": bool(
                isinstance(domain, Mapping)
                and domain.get("status") == "passed"
                and domain.get("claim_ceiling")
                == "bounded_missing_data_conclusion"
            ),
            "non_retryable_gap_not_retried": bool(
                isinstance(failure, Mapping)
                and failure.get("classification") == "missing_critical_data"
                and failure.get("retryable") is False
                and failure.get("attempt_count") == 1
            ),
            "project_record_replay": bool(
                isinstance(project_record, Mapping)
                and project_record.get("replay_verified") is True
                and project_record.get("accepted_event_count") == 1
            ),
            "zero_forbidden_effects": bool(
                isinstance(effects, Mapping)
                and effects.get("external_writes") == 0
                and effects.get("forbidden_effects") == 0
            ),
        }
        failures = sorted(key for key, value in gates.items() if not value)
        return {
            "contract": "d18_core_lca_missing_evaluation/v1",
            "status": (
                "formal_limited_result_accepted" if not failures else "failed"
            ),
            "quality_gates": gates,
            "quality_gate_failures": failures,
            "evidence_sha256": canonical_sha256(evidence),
        }

    def evaluate_recovery(self, evidence: Mapping[str, object]) -> dict:
        """Verify interruption/resume/replay without repeated expensive effects."""

        checkpoint = evidence.get("checkpoint")
        resumed = evidence.get("resumed")
        replayed = evidence.get("replayed")
        hashes_valid = all(
            isinstance(evidence.get(field), str)
            and re.fullmatch(r"[0-9a-f]{64}", str(evidence[field])) is not None
            for field in (
                "input_sha256",
                "provider_route_sha256",
                "permission_policy_sha256",
            )
        )
        identity_fields = (
            "input_sha256",
            "provider_route_sha256",
            "permission_policy_sha256",
        )
        rows_valid = all(
            isinstance(row, Mapping)
            and all(
                isinstance(row.get(field), int) and not isinstance(row.get(field), bool)
                for field in ("model_calls", "tool_calls", "accepted_events")
            )
            and all(row.get(field) == evidence.get(field) for field in identity_fields)
            and isinstance(row.get("snapshot_ref"), Mapping)
            and isinstance(row["snapshot_ref"].get("path"), str)
            and re.fullmatch(
                r"[0-9a-f]{64}", str(row["snapshot_ref"].get("sha256"))
            )
            is not None
            and re.fullmatch(r"[0-9a-f]{64}", str(row.get("project_record_sha256")))
            is not None
            and re.fullmatch(r"[0-9a-f]{64}", str(row.get("run_trace_sha256")))
            is not None
            and isinstance(row.get("run_trace_event_count"), int)
            and not isinstance(row.get("run_trace_event_count"), bool)
            and re.fullmatch(r"[0-9a-f]{64}", str(row.get("run_manifest_sha256")))
            is not None
            and isinstance(row.get("provider_route"), Mapping)
            and isinstance(row.get("fixture_id"), str)
            and bool(row.get("fixture_id"))
            for row in (checkpoint, resumed, replayed)
        )
        if rows_valid:
            model_resume = resumed["model_calls"] - checkpoint["model_calls"]  # type: ignore[index]
            model_replay = replayed["model_calls"] - resumed["model_calls"]  # type: ignore[index]
            tool_resume = resumed["tool_calls"] - checkpoint["tool_calls"]  # type: ignore[index]
            tool_replay = replayed["tool_calls"] - resumed["tool_calls"]  # type: ignore[index]
            accepted_resume = resumed["accepted_events"] - checkpoint["accepted_events"]  # type: ignore[index]
            accepted_replay = replayed["accepted_events"] - resumed["accepted_events"]  # type: ignore[index]
        else:
            model_resume = model_replay = tool_resume = tool_replay = -1
            accepted_resume = accepted_replay = -1
        gates = {
            "contract_and_identity": bool(
                evidence.get("contract") == "d18_core_lca_recovery_evidence/v1"
                and hashes_valid
            ),
            "resume_reuses_calls": bool(
                rows_valid and model_resume == 0 and tool_resume == 0
            ),
            "replay_reuses_calls": bool(
                rows_valid and model_replay == 0 and tool_replay == 0
            ),
            "exactly_once_acceptance": bool(
                rows_valid and accepted_resume == 1 and accepted_replay == 0
            ),
            "zero_repeated_or_external_effects": bool(
                evidence.get("repeated_effects") == 0
                and evidence.get("external_writes") == 0
            ),
        }
        failures = sorted(key for key, value in gates.items() if not value)
        return {
            "contract": "d18_core_lca_recovery_evaluation/v1",
            "status": "passed" if not failures else "failed",
            "gates": gates,
            "gate_failures": failures,
            "model_call_growth_after_resume": model_resume,
            "model_call_growth_after_replay": model_replay,
            "tool_call_growth_after_resume": tool_resume,
            "tool_call_growth_after_replay": tool_replay,
            "accepted_event_growth_after_resume": accepted_resume,
            "accepted_event_growth_after_replay": accepted_replay,
            "evidence_sha256": canonical_sha256(evidence),
        }

    @staticmethod
    def _goal_scope_valid(calculation: Mapping[str, object]) -> bool:
        scope = calculation.get("goal_scope")
        if not isinstance(scope, Mapping):
            return False
        functional_unit = scope.get("functional_unit")
        return bool(
            isinstance(scope.get("research_goal"), str)
            and scope["research_goal"].strip()
            and isinstance(functional_unit, Mapping)
            and all(functional_unit.get(key) not in (None, "") for key in ("amount", "unit", "reference_flow_id"))
            and isinstance(scope.get("system_boundary"), list)
            and scope["system_boundary"]
            and isinstance(scope.get("lifecycle_stage"), str)
            and scope["lifecycle_stage"].strip()
            and isinstance(scope.get("expected_use"), str)
            and scope["expected_use"].strip()
        )

    @staticmethod
    def _source_binding_valid(calculation: Mapping[str, object]) -> bool:
        source = calculation.get("source_binding")
        if not isinstance(source, Mapping):
            return False
        processes = source.get("processes")
        reference_flow = source.get("reference_flow")
        providers = source.get("provider_connections")
        refs = source.get("source_refs")
        return bool(
            isinstance(processes, list)
            and processes
            and all(
                isinstance(row, Mapping)
                and isinstance(row.get("process_id"), str)
                and isinstance(row.get("version"), str)
                for row in processes
            )
            and isinstance(reference_flow, Mapping)
            and all(reference_flow.get(key) not in (None, "") for key in ("id", "version", "unit"))
            and isinstance(providers, list)
            and providers
            and all(
                isinstance(row, Mapping)
                and all(row.get(key) for key in ("consumer_process_id", "provider_process_id", "flow_id"))
                for row in providers
            )
            and isinstance(refs, list)
            and refs
            and all(
                isinstance(row, Mapping)
                and isinstance(row.get("path"), str)
                and isinstance(row.get("sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is not None
                for row in refs
            )
        )

    @staticmethod
    def _tidas_validation_valid(calculation: Mapping[str, object]) -> bool:
        validation = calculation.get("tidas_validation")
        return bool(
            isinstance(validation, Mapping)
            and validation.get("status") == "passed"
            and validation.get("scientific_inputs_sha256")
            == validation.get("validated_scientific_inputs_sha256")
            and validation.get("silent_corrections") == 0
        )

    @staticmethod
    def _calculation_valid(calculation: Mapping[str, object]) -> bool:
        result = calculation.get("calculation")
        validation = calculation.get("tidas_validation")
        return bool(
            isinstance(result, Mapping)
            and all(result.get(key) not in (None, "") for key in ("method_id", "method_version", "value", "unit", "oracle_value", "oracle_unit", "input_sha256", "output_sha256"))
            and result.get("value") == result.get("oracle_value")
            and result.get("unit") == result.get("oracle_unit")
            and result.get("numeric_tolerance_passed") is True
            and isinstance(validation, Mapping)
            and result.get("input_sha256") == validation.get("scientific_inputs_sha256")
        )

    @staticmethod
    def _interpretation_valid(
        calculation: Mapping[str, object], interpretation: Mapping[str, object]
    ) -> bool:
        source_ref = interpretation.get("source_calculation_ref")
        candidate = interpretation.get("candidate")
        domain = interpretation.get("domain_acceptance")
        result = calculation.get("calculation")
        if not all(
            (
                isinstance(source_ref, Mapping),
                isinstance(candidate, Mapping),
                isinstance(domain, Mapping),
                isinstance(result, Mapping),
            )
        ):
            return False
        statements = candidate.get("result_statements")
        exact_ref = f"{result.get('method_id')}@{result.get('method_version')}"
        return bool(
            source_ref.get("sha256") == calculation_execution_sha256(calculation)
            and candidate.get("status") == "candidate_only"
            and candidate.get("sufficiency") == "sufficient_for_bounded_interpretation"
            and isinstance(statements, list)
            and len(statements) == 1
            and isinstance(statements[0], Mapping)
            and statements[0].get("method_exact_ref") == exact_ref
            and statements[0].get("value") == result.get("value")
            and statements[0].get("unit") == result.get("unit")
            and isinstance(candidate.get("limitations"), list)
            and candidate["limitations"]
            and candidate.get("cross_unit_numeric_ranking_performed") is False
            and isinstance(candidate.get("zero_value_claim"), str)
            and candidate.get("tool_requests") == []
            and domain.get("status") == "passed"
            and domain.get("claim_ceiling") == "bounded_candidate_interpretation"
        )

    @staticmethod
    def _assurance_valid(
        calculation: Mapping[str, object], interpretation: Mapping[str, object]
    ) -> bool:
        calculation_boundary = calculation.get("candidate_boundary")
        calculation_record = calculation.get("project_record")
        interpretation_record = interpretation.get("project_record")
        assurance = interpretation.get("assurance")
        return bool(
            isinstance(calculation_boundary, Mapping)
            and calculation_boundary.get("classification") == "candidate"
            and calculation_boundary.get("tool_result_status")
            == "sourced_result_not_fact"
            and isinstance(calculation_boundary.get("assurance_decision_ref"), Mapping)
            and isinstance(assurance, Mapping)
            and assurance.get("accepted_after_candidate") is True
            and isinstance(calculation_record, Mapping)
            and calculation_record.get("replay_verified") is True
            and calculation_record.get("accepted_event_count") == 1
            and isinstance(interpretation_record, Mapping)
            and interpretation_record.get("replay_verified") is True
            and interpretation_record.get("accepted_event_count") == 1
        )


def build_interpretation_fixture(
    calculation_package: Mapping[str, object],
    contract: Mapping[str, object],
    output_root: Path,
) -> Path:
    """Build the pure, content-addressed calculation-to-interpretation bridge."""

    validate_core_lca_quality_contract(contract)
    evaluator = CoreLCAQualityEvaluator(contract)
    if not (
        evaluator._goal_scope_valid(calculation_package)
        and evaluator._source_binding_valid(calculation_package)
        and evaluator._tidas_validation_valid(calculation_package)
        and evaluator._calculation_valid(calculation_package)
        and evaluator._assurance_valid(
            calculation_package,
            {
                "assurance": {"accepted_after_candidate": True},
                "project_record": calculation_package.get("project_record"),
            },
        )
    ):
        raise ValueError("calculation package is not admissible for interpretation")
    root = Path(output_root).resolve()
    source = calculation_package["source_binding"]
    scope = calculation_package["goal_scope"]
    calculation = calculation_package["calculation"]
    calculation_sha = calculation_execution_sha256(calculation_package)
    processes = deepcopy(list(source["processes"]))
    root_process = processes[-1]
    reference_flow = source["reference_flow"]
    source_material = {
        "process_identity": deepcopy(dict(root_process)),
        "process_exact_ref": (
            f"{root_process['process_id']}@{root_process['version']}"
        ),
        "reference_flow": {
            **deepcopy(dict(reference_flow)),
            "exact_ref": f"{reference_flow['id']}@{reference_flow['version']}",
        },
        "functional_unit": {
            **deepcopy(dict(scope["functional_unit"])),
            "label": (
                f"{scope['functional_unit']['amount']} "
                f"{scope['functional_unit']['unit']}"
            ),
        },
        "method_results": [
            {
                "method_exact_ref": (
                    f"{calculation['method_id']}@{calculation['method_version']}"
                ),
                "method_id": calculation["method_id"],
                "method_version": calculation["method_version"],
                "value": calculation["value"],
                "unit": calculation["unit"],
                "source_calculation_sha256": calculation_sha,
            }
        ],
        "provider_connections": deepcopy(list(source["provider_connections"])),
        "source_refs": deepcopy(list(source["source_refs"])),
        "source_calculation_ref": {
            "sha256": calculation_sha,
            "project_record_ref": deepcopy(calculation_package["project_record"]["ref"]),
            "run_trace_ref": deepcopy(calculation_package["run_trace"]["ref"]),
        },
        "scientific_inputs_sha256": calculation["input_sha256"],
    }
    fixture_id = "core-lca-" + calculation_sha[:16]
    commission = (
        "基于所提供的来源绑定 LCA 材料，形成限定于功能单位、系统边界、"
        "方法版本和证据状态的候选科学解读；不得修改或补造权威数据。"
    )
    complete = {
        "contract": "lca_research_kernel.controlled_evaluation_material/v1",
        "fixture_id": fixture_id,
        "commission": commission,
        "claim_ceiling": "candidate_only",
        "source_material": source_material,
        "untrusted_content_instruction_effect": "none",
    }
    missing_source = deepcopy(source_material)
    del missing_source["functional_unit"]
    missing = {
        "contract": "lca_research_kernel.controlled_evaluation_missing_material/v1",
        "fixture_id": fixture_id,
        "commission": commission,
        "expected_claim_ceiling": "bounded_missing_data_conclusion",
        "hidden_field": "functional_unit",
        "source_material": missing_source,
    }
    complete_path = root / "fixture/material-package.json"
    missing_path = root / "fixture/missing-material-package.json"
    _atomic_write(complete_path, complete)
    _atomic_write(missing_path, missing)
    rows = []
    for relative in (
        "fixture/material-package.json",
        "fixture/missing-material-package.json",
    ):
        path = root / relative
        rows.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
            }
        )
    manifest = {
        "contract": "d18_core_lca_interpretation_fixture_manifest/v1",
        "fixture_id": fixture_id,
        "source_calculation_sha256": calculation_sha,
        "registered_artifacts": rows,
        "oracle_access": "grader_only_not_loaded",
        "case_neutral": True,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    _atomic_write(root / "fixture-manifest.json", manifest)
    return root


def compile_efficiency_receipt(
    quality_report: Mapping[str, object], metrics: Mapping[str, object]
) -> dict:
    gates = quality_report.get("quality_gates")
    if (
        quality_report.get("status") != "passed"
        or not isinstance(gates, Mapping)
        or set(gates) != QUALITY_GATES
        or not all(value is True for value in gates.values())
    ):
        raise ValueError("quality gate must pass before efficiency is compiled")
    required = {
        "request_started_at",
        "first_state_change_ms",
        "accepted_result_ms",
        "delivered_result_ms",
        "model_calls",
        "tool_calls",
        "irrelevant_tool_calls",
        "retry_count",
        "replan_count",
        "rework_count",
    }
    if set(metrics) != required:
        raise ValueError("efficiency metrics must match the frozen contract")
    if (
        metrics.get("accepted_result_ms") is None
        or metrics.get("delivered_result_ms") is None
    ):
        raise ValueError("critical-path timing is not measured")
    return {
        "contract": "core_lca_quality_efficiency_receipt/v1",
        **deepcopy(dict(metrics)),
        "external_writes": 0,
    }


def build_core_lca_evidence_locks(
    root: Path,
    *,
    execution_paths: Iterable[str],
    scoring_paths: Iterable[str],
    acceptance_paths: Iterable[str],
    basis: Mapping[str, object],
) -> dict:
    """Build three linked, exact-input locks for the local evaluation tranche."""

    root = Path(root).resolve()
    locks: dict[str, dict] = {}
    previous_sha = None
    for layer, paths in (
        ("execution", execution_paths),
        ("scoring", scoring_paths),
        ("acceptance", acceptance_paths),
    ):
        rows = []
        for relative in sorted(set(paths)):
            target = (root / relative).resolve()
            if root not in target.parents or not target.is_file():
                raise ValueError(f"{layer} evidence input is absent: {relative}")
            rows.append(
                {
                    "path": relative,
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    "bytes": target.stat().st_size,
                }
            )
        if not rows:
            raise ValueError(f"{layer} evidence lock has no inputs")
        lock = {
            "contract": "d18_core_lca_evidence_lock/v1",
            "layer": layer,
            "basis": deepcopy(dict(basis)),
            "upstream_lock_sha256": previous_sha,
            "inputs": rows,
        }
        lock["lock_sha256"] = canonical_sha256(lock)
        locks[layer] = lock
        previous_sha = lock["lock_sha256"]
    return locks


def classify_core_lca_evidence_changes(
    changed_paths: Iterable[str], locks: Mapping[str, object]
) -> str:
    """Return the unique smallest rerun route, failing closed on unknown paths."""

    expected_layers = ("execution", "scoring", "acceptance")
    path_layers: dict[str, str] = {}
    previous_sha = None
    for layer in expected_layers:
        lock = locks.get(layer)
        if not isinstance(lock, Mapping) or lock.get("layer") != layer:
            raise ValueError("evidence lock registry is incomplete")
        body = deepcopy(dict(lock))
        stored = body.pop("lock_sha256", None)
        if canonical_sha256(body) != stored:
            raise ValueError(f"{layer} evidence lock drifted")
        if lock.get("upstream_lock_sha256") != previous_sha:
            raise ValueError(f"{layer} evidence lock linkage drifted")
        for row in lock.get("inputs", []):
            if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
                raise ValueError(f"{layer} evidence input is invalid")
            path_layers[row["path"]] = layer
        previous_sha = str(stored)
    normalized = sorted({str(path).replace("\\", "/") for path in changed_paths})
    if not normalized:
        return "no_evidence_invalidation"
    if any(path not in path_layers for path in normalized):
        return "unknown_change_fail_closed"
    layers = {path_layers[path] for path in normalized}
    if "execution" in layers:
        return "rerun_affected_execution"
    if "scoring" in layers:
        return "regrade_existing_traces"
    return "rerun_clean_acceptance"


def compare_core_lca_evidence_locks(
    adopted: Mapping[str, object], candidate: Mapping[str, object]
) -> dict:
    """Compare two complete lock DAGs and return a fail-closed reuse decision."""

    for registry in (adopted, candidate):
        # An empty change also validates integrity and upstream linkage.
        classify_core_lca_evidence_changes([], registry)
    changed = []
    for layer in ("execution", "scoring", "acceptance"):
        old = adopted[layer]  # type: ignore[index]
        new = candidate[layer]  # type: ignore[index]
        old_rows = {row["path"]: row for row in old["inputs"]}  # type: ignore[index]
        new_rows = {row["path"]: row for row in new["inputs"]}  # type: ignore[index]
        for path in sorted(set(old_rows) | set(new_rows)):
            if old_rows.get(path) != new_rows.get(path):
                changed.append(path)
        if old.get("basis") != new.get("basis"):
            changed.append(f"{layer}:basis")
    execution_changed = any(
        path == "execution:basis"
        or path
        in {
            row["path"]
            for row in adopted["execution"]["inputs"]  # type: ignore[index]
        }
        or path
        in {
            row["path"]
            for row in candidate["execution"]["inputs"]  # type: ignore[index]
        }
        for path in changed
    )
    scoring_changed = any(
        path == "scoring:basis"
        or path
        in {
            row["path"]
            for row in adopted["scoring"]["inputs"]  # type: ignore[index]
        }
        or path
        in {
            row["path"]
            for row in candidate["scoring"]["inputs"]  # type: ignore[index]
        }
        for path in changed
    )
    route = (
        "rerun_affected_execution"
        if execution_changed
        else "regrade_existing_traces"
        if scoring_changed
        else "rerun_clean_acceptance"
        if changed
        else "no_evidence_invalidation"
    )
    return {
        "contract": "d18_core_lca_evidence_lock_comparison/v1",
        "changed_paths": sorted(set(changed)),
        "rerun_route": route,
        "execution_reusable": not execution_changed,
        "adopted_execution_lock_sha256": adopted["execution"]["lock_sha256"],  # type: ignore[index]
        "candidate_execution_lock_sha256": candidate["execution"]["lock_sha256"],  # type: ignore[index]
    }
