"""Formal, benchmark-independent Council-default runtime contracts."""

from __future__ import annotations

import hashlib
import re
from typing import Dict, List, Mapping, Optional, Sequence

from .research_council_contracts import (
    _artifact,
    _artifacts,
    _canonical_json,
    _finish,
    _reject_secret_keys,
    _strings,
    _text,
    compile_decision_cell,
    compile_incumbent as compile_core_incumbent,
    compile_research_agenda,
)


JsonObject = Dict[str, object]

ROUTE_SIGNAL_KEYS = (
    "scientific_importance",
    "dispute_level",
    "procedure_determinism",
    "dependency_strength",
    "independent_evidence_value",
    "adversarial_review_value",
    "context_isolation_value",
    "conclusion_flip_potential",
)
INDEPENDENCE_SIGNAL_KEYS = (
    "independent_evidence_value",
    "adversarial_review_value",
    "context_isolation_value",
)
SIGNAL_LEVELS = {"low", "medium", "high"}
INTERNAL_EFFECTS = {"local_read", "local_write", "external_read"}
DECISION_CELL_RESPONSIBILITIES = {
    "scientific_planning",
    "evidence_investigation",
    "model_and_method_judgment",
    "adversarial_challenge",
    "independent_acceptance",
}
RESULT_KINDS = {
    "candidate",
    "independent_evidence",
    "challenge",
    "dissent",
    "independent_acceptance",
    "consensus",
}
RESULT_RESPONSIBILITIES = {
    "candidate": {"scientific_planning", "model_and_method_judgment"},
    "independent_evidence": {"evidence_investigation"},
    "challenge": {"adversarial_challenge"},
    "dissent": {"adversarial_challenge"},
    "independent_acceptance": {"independent_acceptance"},
    "consensus": DECISION_CELL_RESPONSIBILITIES,
}
FORWARD_RESULT_KINDS = RESULT_KINDS - {"consensus"}
INDEPENDENCE_RECEIPT_FIELDS = {
    "resource_kind",
    "opaque_resource_id_sha256",
    "context_manifest_sha256",
    "source_manifest_sha256",
    "output_sha256",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

FORMAL_EVENT_TYPES = {
    "research.council.agenda_compiled",
    "research.council.route_selected",
    "research.council.cell_opened",
    "research.council.worker_result_registered",
    "research.council.cell_advanced",
    "research.council.fast_path_started",
    "research.council.fast_path_completed",
    "research.council.cell_closed",
    "research.council.outcome_accepted",
    "research.council.rollback_verified",
    "research.council.cutover_accepted",
    "research.council.rollback_activated",
}
ROLLBACK_COMMIT = "22d0576ad1a3f25ceef05ec5940fb02a848a27e9"
FORMAL_EVENT_PAYLOAD_KEYS = {
    "research.council.agenda_compiled": {"agenda_revision", "agenda_ref"},
    "research.council.route_selected": {
        "agenda_revision",
        "route",
        "route_decision_id",
        "route_decision_ref",
    },
    "research.council.cell_opened": {
        "agenda_revision",
        "cell_revision",
        "route_decision_id",
        "cell_ref",
    },
    "research.council.worker_result_registered": {
        "agenda_revision",
        "cell_revision",
        "worker_result_ref",
    },
    "research.council.cell_advanced": {
        "agenda_revision",
        "cell_revision",
        "to_state",
        "cell_update_ref",
    },
    "research.council.fast_path_started": {
        "agenda_revision",
        "route_decision_id",
    },
    "research.council.fast_path_completed": {
        "agenda_revision",
        "route_decision_id",
        "result_ref",
    },
    "research.council.cell_closed": {
        "agenda_revision",
        "cell_revision",
        "cell_close_ref",
    },
    "research.council.outcome_accepted": {
        "agenda_revision",
        "route_decision_id",
        "route",
        "cell_revision",
        "outcome_ref",
    },
    "research.council.rollback_verified": {"rollback_commit", "receipt_ref"},
    "research.council.cutover_accepted": {
        "all_required_gates_passed",
        "gate_manifest_sha256",
        "gate_manifest_artifact_id",
    },
    "research.council.rollback_activated": {
        "rollback_commit",
        "rollback_receipt_ref",
    },
}
FORMAL_EVENT_ARTIFACT_ROLES = {
    "agenda_ref": "council_agenda",
    "route_decision_ref": "council_route_decision",
    "cell_ref": "council_cell",
    "worker_result_ref": "council_worker_result",
    "cell_update_ref": "council_cell_update",
    "result_ref": "fast_path_result",
    "cell_close_ref": "council_cell_close",
    "outcome_ref": "council_outcome",
    "receipt_ref": "rollback_receipt",
    "rollback_receipt_ref": "rollback_receipt",
}


def _require_mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _require_exact_keys(
    value: Mapping[str, object], expected: set, field: str
) -> None:
    if set(value) != expected:
        raise ValueError(f"{field} fields are incomplete or unsupported")


def _sha256_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase hexadecimal SHA-256")
    return value


def _content_addressed(body: JsonObject, noun: str, *, revision: bool = False) -> JsonObject:
    digest = hashlib.sha256(_canonical_json(body)).hexdigest()
    result = dict(body)
    result[f"{noun}_content_sha256"] = digest
    result[f"{noun}_id"] = digest[:16]
    if revision:
        result[f"{noun}_revision"] = digest
    return result


def _verify_content_addressed(
    value: Mapping[str, object], noun: str, *, revision: bool = False
) -> None:
    generated = {f"{noun}_content_sha256", f"{noun}_id"}
    if revision:
        generated.add(f"{noun}_revision")
    body = {key: value[key] for key in value if key not in generated}
    digest = hashlib.sha256(_canonical_json(body)).hexdigest()
    if value.get(f"{noun}_content_sha256") != digest:
        raise ValueError(f"{noun} content address drifted")
    if value.get(f"{noun}_id") != digest[:16]:
        raise ValueError(f"{noun} content-addressed ID drifted")
    if revision and value.get(f"{noun}_revision") != digest:
        raise ValueError(f"{noun} revision drifted from its content address")


def compile_agenda(request: Mapping[str, object]) -> JsonObject:
    """Compile the formal product agenda around the accepted pure agenda compiler."""

    request = _require_mapping(request, "agenda request")
    _require_exact_keys(
        request,
        {
            "goal",
            "authority_receipt_ref",
            "facts",
            "hypotheses",
            "risks",
            "plan",
        },
        "agenda request",
    )
    _reject_secret_keys(request)
    core = compile_research_agenda(
        goal=request["goal"],
        authority_receipt_ref=request["authority_receipt_ref"],
        facts=request["facts"],
        hypotheses=request["hypotheses"],
        risks=request["risks"],
        plan=request["plan"],
    )
    body: JsonObject = {
        "contract": "lca_research_kernel.council_agenda/v1",
        "research_agenda": core,
        "goal": core["goal"],
        "authority_receipt_ref": core["authority_receipt_ref"],
        "next_route": "select-council-route",
    }
    return _content_addressed(body, "agenda", revision=True)


def _normalize_route_signals(signals: Mapping[str, object]) -> JsonObject:
    signals = _require_mapping(signals, "route signals")
    extra = set(signals) - set(ROUTE_SIGNAL_KEYS)
    if extra:
        raise ValueError("route signals contain unsupported fields")
    normalized: JsonObject = {}
    for key in ROUTE_SIGNAL_KEYS:
        value = signals.get(key)
        if isinstance(value, str) and value.strip().casefold() in SIGNAL_LEVELS:
            normalized[key] = value.strip().casefold()
        else:
            normalized[key] = "unknown"
    return normalized


def select_route(
    agenda: Mapping[str, object], signals: Mapping[str, object]
) -> JsonObject:
    """Choose the fast path only when every declared fast-path predicate passes."""

    agenda = _require_mapping(agenda, "agenda")
    if agenda.get("contract") != "lca_research_kernel.council_agenda/v1":
        raise ValueError("agenda contract is unsupported")
    _verify_content_addressed(agenda, "agenda", revision=True)
    revision = _sha256_text(agenda.get("agenda_revision"), "agenda_revision")
    normalized = _normalize_route_signals(signals)
    ambiguous = sorted(
        key for key, value in normalized.items() if value == "unknown"
    )
    fast = (
        not ambiguous
        and normalized["procedure_determinism"] == "high"
        and normalized["dispute_level"] == "low"
        and normalized["dependency_strength"] == "high"
        and normalized["conclusion_flip_potential"] == "low"
        and all(normalized[key] == "low" for key in INDEPENDENCE_SIGNAL_KEYS)
    )
    route = "single_controller_fast_path" if fast else "bounded_decision_cell"
    body: JsonObject = {
        "contract": "lca_research_kernel.council_route_decision/v1",
        "agenda_id": agenda.get("agenda_id"),
        "agenda_revision": revision,
        "signals": normalized,
        "ambiguous_signals": ambiguous,
        "route": route,
        "next_route": (
            "run-single-controller-fast-path"
            if fast
            else "open-bounded-decision-cell"
        ),
    }
    return _finish(body, "route_decision")


def open_cell(
    agenda_ref: Mapping[str, object],
    authority_ref: Mapping[str, object],
    route: Mapping[str, object],
    request: Mapping[str, object],
) -> JsonObject:
    """Compile a bounded formal Cell and its source-bound worker contract."""

    route = _require_mapping(route, "route decision")
    if route.get("contract") != "lca_research_kernel.council_route_decision/v1":
        raise ValueError("route decision contract is unsupported")
    _verify_content_addressed(route, "route_decision")
    if route.get("route") != "bounded_decision_cell":
        raise ValueError("a Decision Cell requires the bounded_decision_cell route")
    request = _require_mapping(request, "cell request")
    _require_exact_keys(
        request,
        {
            "agenda_revision",
            "decision_id",
            "objective",
            "input_artifacts",
            "claims",
            "roles",
            "stop_conditions",
            "flip_conditions",
            "validation_conditions",
            "allowed_effects",
            "output_contract",
            "worker_stop_condition",
        },
        "cell request",
    )
    _reject_secret_keys(request)
    agenda_revision = _sha256_text(
        request.get("agenda_revision"), "agenda_revision"
    )
    if route.get("agenda_revision") != agenda_revision:
        raise ValueError("stale agenda revision in route decision")
    core = compile_decision_cell(
        agenda_ref=agenda_ref,
        authority_receipt_ref=authority_ref,
        decision_id=request["decision_id"],
        objective=request["objective"],
        input_artifacts=request["input_artifacts"],
        claims=request["claims"],
        roles=request["roles"],
        stop_conditions=request["stop_conditions"],
        flip_conditions=request["flip_conditions"],
    )
    if not set(core["roles"]).issubset(DECISION_CELL_RESPONSIBILITIES):
        raise ValueError("Decision Cell responsibility is unsupported")
    validation_rows = request.get("validation_conditions")
    if not isinstance(validation_rows, (list, tuple)) or not validation_rows:
        raise ValueError("validation_conditions must not be empty")
    validation_conditions: List[JsonObject] = []
    condition_ids = set()
    cell_claim_ids = {
        str(row.get("claim_id"))
        for row in core["claims"]
        if isinstance(row, Mapping)
    }
    for index, row in enumerate(validation_rows):
        row = _require_mapping(row, f"validation_conditions[{index}]")
        _require_exact_keys(
            row,
            {"condition_id", "statement", "claim_ids"},
            f"validation_conditions[{index}]",
        )
        condition_id = _text(
            row.get("condition_id"),
            f"validation_conditions[{index}].condition_id",
            casefold=True,
        )
        if condition_id in condition_ids:
            raise ValueError("duplicate validation condition is not allowed")
        condition_ids.add(condition_id)
        claim_ids = _strings(
            row.get("claim_ids"),
            f"validation_conditions[{index}].claim_ids",
            required=True,
        )
        if any(claim_id not in cell_claim_ids for claim_id in claim_ids):
            raise ValueError("validation condition references a claim outside the Cell")
        validation_conditions.append(
            {
                "condition_id": condition_id,
                "statement": _text(
                    row.get("statement"),
                    f"validation_conditions[{index}].statement",
                ),
                "claim_ids": claim_ids,
            }
        )
    validation_conditions.sort(key=lambda row: str(row["condition_id"]))
    validation_manifest_sha256 = hashlib.sha256(
        _canonical_json(validation_conditions)
    ).hexdigest()
    effects = _strings(request["allowed_effects"], "allowed_effects", required=True)
    if not set(effects).issubset(INTERNAL_EFFECTS):
        raise ValueError("Decision Cell contains a human-owned or unsupported effect")
    worker_contract: JsonObject = {
        "source_bindings": core["input_artifacts"],
        "allowed_effects": effects,
        "output_contract": _text(request["output_contract"], "output_contract"),
        "stop_condition": _text(
            request["worker_stop_condition"], "worker_stop_condition"
        ),
        "validation_manifest_sha256": validation_manifest_sha256,
        "independence_policy": "manager_observed_receipt_required",
    }
    body: JsonObject = {
        "contract": "lca_research_kernel.council_cell/v1",
        "decision_cell": core,
        "agenda_revision": agenda_revision,
        "route_decision_id": route.get("route_decision_id"),
        "route": "bounded_decision_cell",
        "authority_receipt_ref": core["authority_receipt_ref"],
        "input_artifacts": core["input_artifacts"],
        "claims": core["claims"],
        "flip_conditions": core["flip_conditions"],
        "validation_conditions": validation_conditions,
        "worker_contract": worker_contract,
        "next_route": "dispatch-bounded-workers",
    }
    return _content_addressed(body, "cell", revision=True)


def _validate_compiled_cell(cell: Mapping[str, object]) -> None:
    """Recompile the Cell core and revalidate every product-level safety binding."""

    cell = _require_mapping(cell, "cell")
    _require_exact_keys(
        cell,
        {
            "contract",
            "decision_cell",
            "agenda_revision",
            "route_decision_id",
            "route",
            "authority_receipt_ref",
            "input_artifacts",
            "claims",
            "flip_conditions",
            "validation_conditions",
            "worker_contract",
            "next_route",
            "cell_content_sha256",
            "cell_id",
            "cell_revision",
        },
        "cell",
    )
    if cell.get("contract") != "lca_research_kernel.council_cell/v1":
        raise ValueError("cell contract is unsupported")
    _reject_secret_keys(cell)
    _verify_content_addressed(cell, "cell", revision=True)
    _sha256_text(cell.get("agenda_revision"), "agenda_revision")
    _text(cell.get("route_decision_id"), "route_decision_id")
    if cell.get("route") != "bounded_decision_cell":
        raise ValueError("formal Cell route is unsupported")
    if cell.get("next_route") != "dispatch-bounded-workers":
        raise ValueError("Cell next route drifted")

    core = _require_mapping(cell.get("decision_cell"), "decision_cell")
    _require_exact_keys(
        core,
        {
            "contract",
            "agenda_ref",
            "authority_receipt_ref",
            "decision_id",
            "objective",
            "input_artifacts",
            "claims",
            "roles",
            "stop_conditions",
            "flip_conditions",
            "next_route",
            "cell_content_sha256",
            "cell_id",
        },
        "decision_cell",
    )
    rebuilt_core = compile_decision_cell(
        agenda_ref=core.get("agenda_ref"),
        authority_receipt_ref=core.get("authority_receipt_ref"),
        decision_id=core.get("decision_id"),
        objective=core.get("objective"),
        input_artifacts=core.get("input_artifacts"),
        claims=core.get("claims"),
        roles=core.get("roles"),
        stop_conditions=core.get("stop_conditions"),
        flip_conditions=core.get("flip_conditions"),
    )
    if rebuilt_core != dict(core):
        raise ValueError("decision_cell failed complete semantic validation")
    roles = _strings(core.get("roles"), "roles", required=True)
    if roles != core.get("roles") or not set(roles).issubset(
        DECISION_CELL_RESPONSIBILITIES
    ):
        raise ValueError("Decision Cell responsibility is unsupported")

    authority = _artifact(
        cell.get("authority_receipt_ref"),
        "authority_receipt_ref",
        expected_role="authority_receipt",
    )
    if authority != core.get("authority_receipt_ref"):
        raise ValueError("Cell authority binding drifted from its decision core")
    input_artifacts = _artifacts(
        cell.get("input_artifacts"), "input_artifacts", required=True
    )
    if input_artifacts != core.get("input_artifacts"):
        raise ValueError("Cell source bindings drifted from its decision core")
    if cell.get("claims") != core.get("claims"):
        raise ValueError("Cell claims drifted from its decision core")
    flip_conditions = _strings(
        cell.get("flip_conditions"), "flip_conditions", required=True
    )
    if flip_conditions != core.get("flip_conditions"):
        raise ValueError("Cell flip conditions drifted from its decision core")

    raw_conditions = cell.get("validation_conditions")
    if not isinstance(raw_conditions, list) or not raw_conditions:
        raise ValueError("validation_conditions must not be empty")
    conditions: List[JsonObject] = []
    seen_condition_ids = set()
    cell_claim_ids = {
        str(row.get("claim_id"))
        for row in core.get("claims", [])
        if isinstance(row, Mapping)
    }
    for index, row in enumerate(raw_conditions):
        row = _require_mapping(row, f"validation_conditions[{index}]")
        _require_exact_keys(
            row,
            {"condition_id", "statement", "claim_ids"},
            f"validation_conditions[{index}]",
        )
        condition_id = _text(
            row.get("condition_id"),
            f"validation_conditions[{index}].condition_id",
            casefold=True,
        )
        if condition_id in seen_condition_ids:
            raise ValueError("duplicate validation condition is not allowed")
        seen_condition_ids.add(condition_id)
        claim_ids = _strings(
            row.get("claim_ids"),
            f"validation_conditions[{index}].claim_ids",
            required=True,
        )
        if any(claim_id not in cell_claim_ids for claim_id in claim_ids):
            raise ValueError("validation condition references a claim outside the Cell")
        conditions.append(
            {
                "condition_id": condition_id,
                "statement": _text(
                    row.get("statement"),
                    f"validation_conditions[{index}].statement",
                ),
                "claim_ids": claim_ids,
            }
        )
    conditions.sort(key=lambda row: str(row["condition_id"]))
    if conditions != raw_conditions:
        raise ValueError("Cell validation conditions are not canonical")

    worker_contract = _require_mapping(
        cell.get("worker_contract"), "worker contract"
    )
    _require_exact_keys(
        worker_contract,
        {
            "source_bindings",
            "allowed_effects",
            "output_contract",
            "stop_condition",
            "validation_manifest_sha256",
            "independence_policy",
        },
        "worker contract",
    )
    worker_sources = _artifacts(
        worker_contract.get("source_bindings"),
        "worker_contract.source_bindings",
        required=True,
    )
    if worker_sources != input_artifacts:
        raise ValueError("worker contract source bindings drifted")
    effects = _strings(
        worker_contract.get("allowed_effects"),
        "worker_contract.allowed_effects",
        required=True,
    )
    if effects != worker_contract.get("allowed_effects") or not set(effects).issubset(
        INTERNAL_EFFECTS
    ):
        raise ValueError("Decision Cell contains a human-owned or unsupported effect")
    _text(worker_contract.get("output_contract"), "worker_contract.output_contract")
    _text(worker_contract.get("stop_condition"), "worker_contract.stop_condition")
    expected_manifest_sha256 = hashlib.sha256(_canonical_json(conditions)).hexdigest()
    if worker_contract.get("validation_manifest_sha256") != expected_manifest_sha256:
        raise ValueError("worker contract validation manifest drifted")
    if worker_contract.get("independence_policy") != "manager_observed_receipt_required":
        raise ValueError("worker contract independence policy drifted")


def _independence_receipt(value: object) -> JsonObject:
    value = _require_mapping(value, "independence receipt")
    if set(value) != INDEPENDENCE_RECEIPT_FIELDS:
        raise ValueError("independence receipt fields are incomplete or unsupported")
    resource_kind = _text(value.get("resource_kind"), "resource_kind", casefold=True)
    if resource_kind not in {"isolated_subagent_context", "isolated_worker_context"}:
        raise ValueError("independence receipt resource kind is unsupported")
    return {
        "resource_kind": resource_kind,
        "opaque_resource_id_sha256": _sha256_text(
            value.get("opaque_resource_id_sha256"), "opaque_resource_id_sha256"
        ),
        "context_manifest_sha256": _sha256_text(
            value.get("context_manifest_sha256"), "context_manifest_sha256"
        ),
        "source_manifest_sha256": _sha256_text(
            value.get("source_manifest_sha256"), "source_manifest_sha256"
        ),
        "output_sha256": _sha256_text(
            value.get("output_sha256"), "output_sha256"
        ),
    }


def compile_worker_result(
    cell: Mapping[str, object], result: Mapping[str, object]
) -> JsonObject:
    """Validate and compile a manager-observed, source-bound worker result."""

    cell = _require_mapping(cell, "cell")
    _validate_compiled_cell(cell)
    result = _require_mapping(result, "worker result")
    required = {
        "agenda_revision",
        "cell_revision",
        "result_kind",
        "responsibility",
        "source_bindings",
        "allowed_effects",
        "output_contract",
        "stop_condition_met",
        "artifact_ref",
        "statement",
        "validation_scope",
        "independence_receipt",
    }
    optional = {"flip_condition", "reactivation_condition"}
    if not required.issubset(result) or set(result) - required - optional:
        raise ValueError("worker result fields are incomplete or unsupported")
    _reject_secret_keys(result)
    if result.get("agenda_revision") != cell.get("agenda_revision"):
        raise ValueError("stale agenda revision in worker result")
    if result.get("cell_revision") != cell.get("cell_revision"):
        raise ValueError("stale cell revision in worker result")
    sources = _artifacts(result.get("source_bindings"), "source_bindings", required=True)
    bound = {_canonical_json(value) for value in cell.get("input_artifacts", [])}
    if any(_canonical_json(source) not in bound for source in sources):
        raise ValueError("worker result contains an unregistered source binding")
    effects = _strings(result.get("allowed_effects"), "allowed_effects", required=True)
    worker_contract = _require_mapping(cell.get("worker_contract"), "worker contract")
    if effects != worker_contract.get("allowed_effects"):
        raise ValueError("worker result effects differ from the Cell contract")
    output_contract = _text(result.get("output_contract"), "output_contract")
    if output_contract != worker_contract.get("output_contract"):
        raise ValueError("worker result output contract differs from the Cell contract")
    if result.get("stop_condition_met") is not True:
        raise ValueError("worker result did not meet its stop condition")
    result_kind = _text(result.get("result_kind"), "result_kind", casefold=True)
    if result_kind not in RESULT_KINDS:
        raise ValueError("worker result kind is unsupported")
    responsibility = _text(
        result.get("responsibility"), "responsibility", casefold=True
    )
    declared_roles = set(
        _strings(
            _require_mapping(cell.get("decision_cell"), "decision_cell").get(
                "roles"
            ),
            "Decision Cell roles",
            required=True,
        )
    )
    if responsibility not in declared_roles:
        raise ValueError("worker responsibility is not declared by the Decision Cell")
    if responsibility not in RESULT_RESPONSIBILITIES[result_kind]:
        raise ValueError("worker responsibility is incompatible with its result kind")
    output_artifact = _artifact(result.get("artifact_ref"), "artifact_ref")
    statement = _text(result.get("statement"), "statement")
    raw_validation_scope = result.get("validation_scope")
    validation_scope: Optional[JsonObject]
    if result_kind == "independent_acceptance":
        scope = _require_mapping(raw_validation_scope, "validation_scope")
        _require_exact_keys(
            scope,
            {
                "claim_ids",
                "candidate_conclusion_sha256",
                "validation_condition_ids",
            },
            "validation_scope",
        )
        claim_ids = _strings(scope.get("claim_ids"), "validation_scope.claim_ids", required=True)
        cell_claim_ids = {
            str(row.get("claim_id"))
            for row in cell.get("claims", [])
            if isinstance(row, Mapping)
        }
        if any(claim_id not in cell_claim_ids for claim_id in claim_ids):
            raise ValueError("validation scope references a claim outside the Cell")
        condition_ids = _strings(
            scope.get("validation_condition_ids"),
            "validation_scope.validation_condition_ids",
            required=True,
        )
        cell_condition_ids = {
            str(row.get("condition_id"))
            for row in cell.get("validation_conditions", [])
            if isinstance(row, Mapping)
        }
        if set(condition_ids) != cell_condition_ids:
            raise ValueError("validation scope does not cover the Cell conditions")
        validation_scope = {
            "claim_ids": claim_ids,
            "candidate_conclusion_sha256": _sha256_text(
                scope.get("candidate_conclusion_sha256"),
                "validation_scope.candidate_conclusion_sha256",
            ),
            "validation_condition_ids": condition_ids,
        }
    else:
        if raw_validation_scope is not None:
            raise ValueError("validation_scope is reserved for independent acceptance")
        validation_scope = None
    receipt = _independence_receipt(result.get("independence_receipt"))
    if receipt["output_sha256"] != output_artifact["sha256"]:
        raise ValueError("independence receipt output hash does not bind the artifact")
    expected_context_sha256 = hashlib.sha256(
        _canonical_json(
            {
                "agenda_revision": cell.get("agenda_revision"),
                "cell_revision": cell.get("cell_revision"),
                "allowed_effects": effects,
                "output_contract": output_contract,
                "stop_condition": worker_contract.get("stop_condition"),
                "result_kind": result_kind,
                "responsibility": responsibility,
                "statement": statement,
                "validation_scope": validation_scope,
            }
        )
    ).hexdigest()
    if receipt["context_manifest_sha256"] != expected_context_sha256:
        raise ValueError("independence receipt context manifest is unbound")
    expected_source_sha256 = hashlib.sha256(_canonical_json(sources)).hexdigest()
    if receipt["source_manifest_sha256"] != expected_source_sha256:
        raise ValueError("independence receipt source manifest is unbound")
    body: JsonObject = {
        "contract": "lca_research_kernel.council_worker_result/v1",
        "agenda_revision": cell.get("agenda_revision"),
        "cell_revision": cell.get("cell_revision"),
        "cell_id": cell.get("cell_id"),
        "result_kind": result_kind,
        "responsibility": responsibility,
        "source_bindings": sources,
        "allowed_effects": effects,
        "output_contract": output_contract,
        "stop_condition_met": True,
        "artifact_ref": output_artifact,
        "statement": statement,
        "validation_scope": validation_scope,
        "independence_receipt": receipt,
        "next_route": "integrate-worker-result",
    }
    if result_kind == "dissent":
        body["flip_condition"] = _text(
            result.get("flip_condition"), "dissent flip_condition"
        )
        body["reactivation_condition"] = _text(
            result.get("reactivation_condition"),
            "dissent reactivation_condition",
        )
    return _finish(body, "worker_result")


def _validate_compiled_worker_result(
    cell: Mapping[str, object], value: Mapping[str, object]
) -> None:
    """Recompile a registered worker result instead of trusting its digest alone."""

    value = _require_mapping(value, "worker result")
    base_fields = {
        "contract",
        "agenda_revision",
        "cell_revision",
        "cell_id",
        "result_kind",
        "responsibility",
        "source_bindings",
        "allowed_effects",
        "output_contract",
        "stop_condition_met",
        "artifact_ref",
        "statement",
        "validation_scope",
        "independence_receipt",
        "next_route",
        "worker_result_content_sha256",
        "worker_result_id",
    }
    result_kind = value.get("result_kind")
    expected_fields = set(base_fields)
    if result_kind == "dissent":
        expected_fields.update({"flip_condition", "reactivation_condition"})
    _require_exact_keys(value, expected_fields, "worker result")
    if value.get("contract") != "lca_research_kernel.council_worker_result/v1":
        raise ValueError("worker result contract is unsupported")
    _verify_content_addressed(value, "worker_result")
    if value.get("cell_id") != cell.get("cell_id"):
        raise ValueError("worker result belongs to a different Cell")
    if value.get("next_route") != "integrate-worker-result":
        raise ValueError("worker result next route drifted")
    raw: JsonObject = {
        "agenda_revision": value.get("agenda_revision"),
        "cell_revision": value.get("cell_revision"),
        "result_kind": result_kind,
        "responsibility": value.get("responsibility"),
        "source_bindings": value.get("source_bindings"),
        "allowed_effects": value.get("allowed_effects"),
        "output_contract": value.get("output_contract"),
        "stop_condition_met": value.get("stop_condition_met"),
        "artifact_ref": value.get("artifact_ref"),
        "statement": value.get("statement"),
        "validation_scope": value.get("validation_scope"),
        "independence_receipt": value.get("independence_receipt"),
    }
    if result_kind == "dissent":
        raw["flip_condition"] = value.get("flip_condition")
        raw["reactivation_condition"] = value.get("reactivation_condition")
    rebuilt = compile_worker_result(cell, raw)
    if rebuilt != dict(value):
        raise ValueError("worker result failed complete semantic validation")


def compile_incumbent(
    cell: Mapping[str, object],
    candidate: Mapping[str, object],
    worker_results: Sequence[Mapping[str, object]],
) -> JsonObject:
    """Compile an incumbent only from declared checks and registered results."""

    cell = _require_mapping(cell, "cell")
    _validate_compiled_cell(cell)
    if not isinstance(worker_results, (list, tuple)) or not worker_results:
        raise ValueError("registered worker results are required for an incumbent")
    result_by_id: Dict[str, Mapping[str, object]] = {}
    for index, result in enumerate(worker_results):
        result = _require_mapping(result, f"worker_results[{index}]")
        _validate_compiled_worker_result(cell, result)
        result_id = _text(result.get("worker_result_id"), "worker_result_id")
        if result_id in result_by_id:
            raise ValueError("duplicate worker result is not allowed")
        result_by_id[result_id] = result

    candidate = _require_mapping(candidate, "incumbent candidate")
    _require_exact_keys(
        candidate,
        {
            "candidate_id",
            "conclusion",
            "claim_ids",
            "evidence_worker_result_ids",
            "dissent_worker_result_ids",
            "validation",
        },
        "incumbent candidate",
    )
    _reject_secret_keys(candidate)
    claim_ids = _strings(candidate.get("claim_ids"), "claim_ids", required=True)
    cell_claims = {
        str(row.get("claim_id")): row
        for row in cell.get("claims", [])
        if isinstance(row, Mapping)
    }
    if any(claim_id not in cell_claims for claim_id in claim_ids):
        raise ValueError("incumbent references a claim outside the Cell")
    evidence_ids = _strings(
        candidate.get("evidence_worker_result_ids"),
        "evidence_worker_result_ids",
        required=True,
    )
    if any(result_id not in result_by_id for result_id in evidence_ids):
        raise ValueError("incumbent evidence is not a registered worker result")
    dissent_ids = _strings(
        candidate.get("dissent_worker_result_ids"),
        "dissent_worker_result_ids",
    )
    if any(result_id not in result_by_id for result_id in dissent_ids):
        raise ValueError("incumbent dissent is not a registered worker result")
    if any(result_by_id[result_id].get("result_kind") != "dissent" for result_id in dissent_ids):
        raise ValueError("incumbent dissent must reference dissent worker results")
    registered_dissent_ids = sorted(
        result_id
        for result_id, result in result_by_id.items()
        if result.get("result_kind") == "dissent"
    )
    if dissent_ids != registered_dissent_ids:
        raise ValueError("incumbent must retain every registered dissent")

    validation = _require_mapping(candidate.get("validation"), "validation")
    _require_exact_keys(
        validation,
        {
            "declared_conditions",
            "results",
            "independent_acceptance_worker_result_id",
        },
        "validation",
    )
    declared_rows = validation.get("declared_conditions")
    if not isinstance(declared_rows, (list, tuple)) or not declared_rows:
        raise ValueError("declared validation conditions are required")
    declared: List[JsonObject] = []
    declared_ids = set()
    for index, row in enumerate(declared_rows):
        row = _require_mapping(row, f"declared_conditions[{index}]")
        _require_exact_keys(
            row, {"condition_id", "statement"}, f"declared_conditions[{index}]"
        )
        condition_id = _text(
            row.get("condition_id"),
            f"declared_conditions[{index}].condition_id",
            casefold=True,
        )
        if condition_id in declared_ids:
            raise ValueError("duplicate validation condition is not allowed")
        declared_ids.add(condition_id)
        declared.append(
            {
                "condition_id": condition_id,
                "statement": _text(
                    row.get("statement"), f"declared_conditions[{index}].statement"
                ),
            }
        )
    declared.sort(key=lambda row: str(row["condition_id"]))
    cell_declared = [
        {
            "condition_id": row.get("condition_id"),
            "statement": row.get("statement"),
        }
        for row in cell.get("validation_conditions", [])
        if isinstance(row, Mapping)
    ]
    cell_declared.sort(key=lambda row: str(row["condition_id"]))
    if declared != cell_declared:
        raise ValueError("declared validation conditions drifted from the Cell")
    result_rows = validation.get("results")
    if not isinstance(result_rows, (list, tuple)) or not result_rows:
        raise ValueError("validation results are required")
    validation_results: List[JsonObject] = []
    observed_condition_ids = set()
    for index, row in enumerate(result_rows):
        row = _require_mapping(row, f"validation.results[{index}]")
        _require_exact_keys(
            row,
            {"condition_id", "status", "evidence_worker_result_ids"},
            f"validation.results[{index}]",
        )
        condition_id = _text(
            row.get("condition_id"),
            f"validation.results[{index}].condition_id",
            casefold=True,
        )
        if condition_id in observed_condition_ids:
            raise ValueError("duplicate validation result is not allowed")
        observed_condition_ids.add(condition_id)
        if _text(row.get("status"), "validation status", casefold=True) != "passed":
            raise ValueError("every declared validation condition must pass")
        support_ids = _strings(
            row.get("evidence_worker_result_ids"),
            f"validation.results[{index}].evidence_worker_result_ids",
            required=True,
        )
        if any(result_id not in result_by_id for result_id in support_ids):
            raise ValueError("validation evidence is not a registered worker result")
        if any(result_id not in evidence_ids for result_id in support_ids):
            raise ValueError("validation evidence is absent from incumbent evidence")
        validation_results.append(
            {
                "condition_id": condition_id,
                "status": "passed",
                "evidence_worker_result_ids": support_ids,
            }
        )
    if observed_condition_ids != declared_ids:
        raise ValueError("validation results must cover every declared condition exactly")
    validation_results.sort(key=lambda row: str(row["condition_id"]))
    acceptance_id = _text(
        validation.get("independent_acceptance_worker_result_id"),
        "independent_acceptance_worker_result_id",
    )
    if acceptance_id not in result_by_id or result_by_id[acceptance_id].get(
        "result_kind"
    ) != "independent_acceptance":
        raise ValueError("independent acceptance must reference a registered acceptance result")
    if acceptance_id not in evidence_ids or not any(
        acceptance_id in row["evidence_worker_result_ids"]
        for row in validation_results
    ):
        raise ValueError("independent acceptance must support a declared validation result")
    acceptance_resource_id = _require_mapping(
        result_by_id[acceptance_id].get("independence_receipt"),
        "independent acceptance receipt",
    ).get("opaque_resource_id_sha256")
    for result_id, registered_result in result_by_id.items():
        if result_id == acceptance_id:
            continue
        other_resource_id = _require_mapping(
            registered_result.get("independence_receipt"),
            "registered worker independence receipt",
        ).get("opaque_resource_id_sha256")
        if other_resource_id == acceptance_resource_id:
            raise ValueError(
                "independent acceptance resource must differ from every other registered worker resource"
            )
    acceptance_scope = result_by_id[acceptance_id].get("validation_scope")
    if not isinstance(acceptance_scope, Mapping):
        raise ValueError("independent acceptance validation scope is absent")
    if set(acceptance_scope.get("claim_ids") or []) != set(claim_ids):
        raise ValueError("independent acceptance claim scope drifted")
    if set(acceptance_scope.get("validation_condition_ids") or []) != declared_ids:
        raise ValueError("independent acceptance condition scope drifted")
    conclusion = _text(candidate.get("conclusion"), "conclusion")
    conclusion_sha256 = hashlib.sha256(conclusion.encode("utf-8")).hexdigest()
    if acceptance_scope.get("candidate_conclusion_sha256") != conclusion_sha256:
        raise ValueError("independent acceptance conclusion scope drifted")

    claim_refs: List[JsonObject] = []
    for claim_id in claim_ids:
        claim_bytes = _canonical_json(cell_claims[claim_id])
        claim_refs.append(
            {
                "role": "claim",
                "path": f"artifacts/claims/{claim_id}.json",
                "sha256": hashlib.sha256(claim_bytes).hexdigest(),
                "bytes": len(claim_bytes),
            }
        )
    evidence_artifacts = [
        dict(result_by_id[result_id]["artifact_ref"]) for result_id in evidence_ids
    ]
    normalized_dissent: List[JsonObject] = []
    for result_id in dissent_ids:
        result = result_by_id[result_id]
        normalized_dissent.append(
            {
                "dissent_id": result_id,
                "statement": result["statement"],
                "source_bindings": result["source_bindings"],
                "artifact_ref": result["artifact_ref"],
                "flip_condition": result["flip_condition"],
                "reactivation_condition": result["reactivation_condition"],
            }
        )
    cell_bytes = _canonical_json(cell)
    cell_ref = {
        "role": "decision_cell",
        "path": f"artifacts/cells/{cell.get('cell_id')}.json",
        "sha256": hashlib.sha256(cell_bytes).hexdigest(),
        "bytes": len(cell_bytes),
    }
    core = compile_core_incumbent(
        cell_ref=cell_ref,
        candidate_id=candidate["candidate_id"],
        conclusion=conclusion,
        claim_refs=claim_refs,
        evidence_artifacts=evidence_artifacts,
        dissent=normalized_dissent,
        validation_status="passed",
    )
    validation_receipt: JsonObject = {
        "declared_conditions": declared,
        "results": validation_results,
        "independent_acceptance_worker_result_id": acceptance_id,
        "status": "passed",
    }
    body: JsonObject = {
        "contract": "lca_research_kernel.council_incumbent/v1",
        "agenda_revision": cell.get("agenda_revision"),
        "cell_revision": cell.get("cell_revision"),
        "cell_id": cell.get("cell_id"),
        "candidate_id": core["candidate_id"],
        "conclusion": core["conclusion"],
        "claim_ids": claim_ids,
        "claim_refs": core["claim_refs"],
        "evidence_worker_result_ids": evidence_ids,
        "evidence_artifacts": core["evidence_artifacts"],
        "supporting_worker_result_ids": sorted(result_by_id),
        "dissent": core["dissent"],
        "validation_receipt": validation_receipt,
        "validation_status": "passed",
        "next_route": "close-decision-cell",
    }
    return _content_addressed(body, "incumbent", revision=True)


def _validate_compiled_incumbent(
    cell: Mapping[str, object],
    incumbent: Mapping[str, object],
    worker_results: Sequence[Mapping[str, object]],
) -> None:
    """Recompile an incumbent from registered results and require byte-equivalence."""

    incumbent = _require_mapping(incumbent, "incumbent")
    expected_fields = {
        "contract",
        "agenda_revision",
        "cell_revision",
        "cell_id",
        "candidate_id",
        "conclusion",
        "claim_ids",
        "claim_refs",
        "evidence_worker_result_ids",
        "evidence_artifacts",
        "supporting_worker_result_ids",
        "dissent",
        "validation_receipt",
        "validation_status",
        "next_route",
        "incumbent_content_sha256",
        "incumbent_id",
        "incumbent_revision",
    }
    _require_exact_keys(incumbent, expected_fields, "incumbent")
    if incumbent.get("contract") != "lca_research_kernel.council_incumbent/v1":
        raise ValueError("incumbent contract is unsupported")
    _verify_content_addressed(incumbent, "incumbent", revision=True)
    if incumbent.get("cell_id") != cell.get("cell_id"):
        raise ValueError("incumbent belongs to a different Cell")
    if incumbent.get("next_route") != "close-decision-cell":
        raise ValueError("incumbent next route drifted")
    if incumbent.get("validation_status") != "passed":
        raise ValueError("incumbent validation did not pass")
    if not isinstance(worker_results, (list, tuple)) or not worker_results:
        raise ValueError("incumbent requires its registered worker results")
    for index, result in enumerate(worker_results):
        _validate_compiled_worker_result(
            cell, _require_mapping(result, f"worker_results[{index}]")
        )
    receipt = _require_mapping(
        incumbent.get("validation_receipt"), "incumbent validation receipt"
    )
    _require_exact_keys(
        receipt,
        {
            "declared_conditions",
            "results",
            "independent_acceptance_worker_result_id",
            "status",
        },
        "incumbent validation receipt",
    )
    dissent_rows = incumbent.get("dissent")
    if not isinstance(dissent_rows, list):
        raise ValueError("incumbent dissent must be a list")
    dissent_ids = []
    for index, row in enumerate(dissent_rows):
        row = _require_mapping(row, f"incumbent.dissent[{index}]")
        dissent_ids.append(_text(row.get("dissent_id"), "dissent_id"))
    candidate: JsonObject = {
        "candidate_id": incumbent.get("candidate_id"),
        "conclusion": incumbent.get("conclusion"),
        "claim_ids": incumbent.get("claim_ids"),
        "evidence_worker_result_ids": incumbent.get(
            "evidence_worker_result_ids"
        ),
        "dissent_worker_result_ids": dissent_ids,
        "validation": {
            "declared_conditions": receipt.get("declared_conditions"),
            "results": receipt.get("results"),
            "independent_acceptance_worker_result_id": receipt.get(
                "independent_acceptance_worker_result_id"
            ),
        },
    }
    try:
        rebuilt = compile_incumbent(cell, candidate, worker_results)
    except ValueError as exc:
        raise ValueError(f"incumbent failed complete validation: {exc}") from exc
    if rebuilt != dict(incumbent):
        raise ValueError("incumbent failed complete semantic validation")


def advance_cell(
    cell: Mapping[str, object],
    worker_results: Sequence[Mapping[str, object]],
    incumbent: Optional[Mapping[str, object]],
) -> JsonObject:
    """Classify actual forward change without treating consensus as progress."""

    cell = _require_mapping(cell, "cell")
    _validate_compiled_cell(cell)
    if not isinstance(worker_results, (list, tuple)):
        raise ValueError("worker_results must be a list")
    normalized: List[JsonObject] = []
    seen = set()
    dissent: List[JsonObject] = []
    for index, value in enumerate(worker_results):
        value = _require_mapping(value, f"worker_results[{index}]")
        _validate_compiled_worker_result(cell, value)
        result_id = _text(value.get("worker_result_id"), "worker_result_id")
        if result_id in seen:
            raise ValueError("duplicate worker result is not allowed")
        seen.add(result_id)
        record = dict(value)
        normalized.append(record)
        if record.get("result_kind") == "dissent":
            dissent.append(
                {
                    "worker_result_id": result_id,
                    "statement": record.get("statement"),
                    "source_bindings": record.get("source_bindings"),
                    "flip_condition": record.get("flip_condition"),
                    "reactivation_condition": record.get("reactivation_condition"),
                    "artifact_ref": record.get("artifact_ref"),
                }
            )
    normalized.sort(key=lambda value: str(value.get("worker_result_id")))
    forward = any(
        value.get("result_kind") in FORWARD_RESULT_KINDS for value in normalized
    )
    if incumbent is not None:
        incumbent = _require_mapping(incumbent, "incumbent")
        _validate_compiled_incumbent(cell, incumbent, normalized)
        supporting = incumbent.get("supporting_worker_result_ids")
        if not isinstance(supporting, list) or not set(supporting).issubset(seen):
            raise ValueError("incumbent support is not registered in this Cell update")
        to_state = "incumbent_validated"
    elif forward:
        to_state = "worker_results_pending"
    else:
        to_state = "replan_required"
    body: JsonObject = {
        "contract": "lca_research_kernel.council_cell_update/v1",
        "agenda_revision": cell.get("agenda_revision"),
        "cell_revision": cell.get("cell_revision"),
        "cell_id": cell.get("cell_id"),
        "worker_results": normalized,
        "incumbent": dict(incumbent) if incumbent is not None else None,
        "dissent": dissent,
        "progress_classification": (
            "forward_change" if forward or incumbent is not None else "no_forward_change"
        ),
        "to_state": to_state,
        "next_route": {
            "incumbent_validated": "close-decision-cell",
            "worker_results_pending": "continue-bounded-cell",
            "replan_required": "replan-or-close-decision-cell",
        }[to_state],
    }
    return _finish(body, "cell_update")


def close_cell(cell: Mapping[str, object], outcome: Mapping[str, object]) -> JsonObject:
    """Close from a validated incumbent or a bounded no-progress stop."""

    cell = _require_mapping(cell, "cell")
    _validate_compiled_cell(cell)
    outcome = _require_mapping(outcome, "cell outcome")
    _require_exact_keys(
        outcome, {"incumbent", "worker_results", "close_reason"}, "cell outcome"
    )
    worker_results = outcome.get("worker_results")
    if not isinstance(worker_results, (list, tuple)):
        raise ValueError("cell outcome worker_results must be a list")
    normalized_workers = []
    for index, worker in enumerate(worker_results):
        worker = _require_mapping(worker, f"cell outcome worker_results[{index}]")
        _validate_compiled_worker_result(cell, worker)
        normalized_workers.append(dict(worker))
    incumbent_value = outcome.get("incumbent")
    if incumbent_value is None:
        if any(
            worker.get("result_kind") in FORWARD_RESULT_KINDS
            for worker in normalized_workers
        ):
            raise ValueError(
                "a Cell with forward evidence cannot close without a validated incumbent"
            )
        incumbent = None
        disposition = "closed_without_incumbent"
        validation_status = "insufficient_evidence_no_acceptance"
        incumbent_id = None
        incumbent_revision = None
        conclusion = None
        dissent = []
    else:
        incumbent = _require_mapping(incumbent_value, "incumbent")
        _validate_compiled_incumbent(cell, incumbent, normalized_workers)
        disposition = "incumbent_accepted"
        validation_status = "passed"
        incumbent_id = incumbent.get("incumbent_id")
        incumbent_revision = incumbent.get("incumbent_revision")
        conclusion = incumbent.get("conclusion")
        dissent = incumbent.get("dissent")
    body: JsonObject = {
        "contract": "lca_research_kernel.council_cell_close/v1",
        "agenda_revision": cell.get("agenda_revision"),
        "cell_revision": cell.get("cell_revision"),
        "cell_id": cell.get("cell_id"),
        "closure_disposition": disposition,
        "incumbent_id": incumbent_id,
        "incumbent_revision": incumbent_revision,
        "conclusion": conclusion,
        "validation_status": validation_status,
        "dissent": dissent,
        "close_reason": _text(outcome.get("close_reason"), "close_reason"),
        "next_route": "accept-council-outcome",
    }
    return _finish(body, "cell_close")


def _validate_formal_event_payload(
    event_type: str, payload: Mapping[str, object]
) -> None:
    expected = FORMAL_EVENT_PAYLOAD_KEYS[event_type]
    if set(payload) != expected:
        raise ValueError(f"{event_type} payload fields are incomplete or unsupported")
    _reject_secret_keys(payload)
    for field, role in FORMAL_EVENT_ARTIFACT_ROLES.items():
        if field in payload:
            _artifact(payload[field], field, expected_role=role)
    for field in ("agenda_revision", "cell_revision", "gate_manifest_sha256"):
        if field in payload and payload[field] is not None:
            _sha256_text(payload[field], field)
    if "route_decision_id" in payload:
        _text(payload["route_decision_id"], "route_decision_id")
    if "gate_manifest_artifact_id" in payload:
        _text(payload["gate_manifest_artifact_id"], "gate_manifest_artifact_id")
    if "rollback_commit" in payload and payload["rollback_commit"] != ROLLBACK_COMMIT:
        raise ValueError("formal Council rollback commit drifted")


def validate_formal_council_events(
    events: Sequence[Mapping[str, object]],
) -> List[str]:
    """Fail closed on invalid formal Council event types or state transitions."""

    state = "not_started"
    selected_route: Optional[str] = None
    active_agenda_revision: Optional[str] = None
    active_route_decision_id: Optional[str] = None
    active_cell_revision: Optional[str] = None
    cutover_accepted = False
    rollback_activated = False
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            raise ValueError(f"formal Council event {index} must be an object")
        event_type = event.get("event_type")
        if not isinstance(event_type, str) or not event_type.startswith(
            "research.council."
        ):
            continue
        if event_type not in FORMAL_EVENT_TYPES:
            raise ValueError(f"unsupported formal Council event type: {event_type}")
        if event.get("actor") != "controller":
            raise ValueError("formal Council Project truth may only be written by the controller")
        idempotency_key = event.get("idempotency_key")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError("formal Council events require a non-empty idempotency key")
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            raise ValueError(f"{event_type} payload must be an object")
        _validate_formal_event_payload(event_type, payload)
        if event_type == "research.council.rollback_verified":
            continue
        if event_type == "research.council.cutover_accepted":
            if cutover_accepted or rollback_activated:
                raise ValueError("formal Council cutover may be accepted exactly once")
            if payload.get("all_required_gates_passed") is not True:
                raise ValueError("cutover transition requires every declared gate")
            _sha256_text(payload.get("gate_manifest_sha256"), "gate_manifest_sha256")
            cutover_accepted = True
            continue
        if event_type == "research.council.rollback_activated":
            if not cutover_accepted:
                raise ValueError("rollback transition requires an accepted cutover")
            if rollback_activated:
                raise ValueError("formal Council rollback may be activated exactly once")
            rollback_activated = True
            state = "rolled_back"
            continue
        if event_type == "research.council.agenda_compiled":
            expected = {"not_started", "accepted"}
            new_state = "agenda_compiled"
            active_agenda_revision = str(payload["agenda_revision"])
            selected_route = None
            active_route_decision_id = None
            active_cell_revision = None
        elif event_type == "research.council.route_selected":
            expected = {"agenda_compiled"}
            if payload.get("agenda_revision") != active_agenda_revision:
                raise ValueError("stale agenda revision in formal Council event history")
            selected_route = payload.get("route")
            if selected_route not in {
                "single_controller_fast_path",
                "bounded_decision_cell",
            }:
                raise ValueError("route_selected contains an unsupported route")
            active_route_decision_id = str(payload["route_decision_id"])
            new_state = "route_selected"
        elif event_type == "research.council.fast_path_started":
            expected = {"route_selected"}
            if payload.get("agenda_revision") != active_agenda_revision:
                raise ValueError("stale agenda revision in formal Council event history")
            if payload.get("route_decision_id") != active_route_decision_id:
                raise ValueError("stale route decision in formal Council event history")
            if selected_route != "single_controller_fast_path":
                raise ValueError("fast-path transition contradicts the selected route")
            new_state = "fast_path_running"
        elif event_type == "research.council.fast_path_completed":
            expected = {"fast_path_running"}
            if payload.get("agenda_revision") != active_agenda_revision:
                raise ValueError("stale agenda revision in formal Council event history")
            if payload.get("route_decision_id") != active_route_decision_id:
                raise ValueError("stale route decision in formal Council event history")
            new_state = "closed"
        elif event_type == "research.council.cell_opened":
            expected = {"route_selected", "replan_required"}
            if payload.get("agenda_revision") != active_agenda_revision:
                raise ValueError("stale agenda revision in formal Council event history")
            if payload.get("route_decision_id") != active_route_decision_id:
                raise ValueError("stale route decision in formal Council event history")
            if state == "route_selected" and selected_route != "bounded_decision_cell":
                raise ValueError("Cell transition contradicts the selected route")
            active_cell_revision = str(payload["cell_revision"])
            new_state = "cell_open"
        elif event_type == "research.council.worker_result_registered":
            expected = {"cell_open", "worker_results_pending"}
            if payload.get("agenda_revision") != active_agenda_revision:
                raise ValueError("stale agenda revision in formal Council event history")
            if payload.get("cell_revision") != active_cell_revision:
                raise ValueError("stale cell revision in formal Council event history")
            new_state = "worker_results_pending"
        elif event_type == "research.council.cell_advanced":
            expected = {"worker_results_pending"}
            if payload.get("agenda_revision") != active_agenda_revision:
                raise ValueError("stale agenda revision in formal Council event history")
            if payload.get("cell_revision") != active_cell_revision:
                raise ValueError("stale cell revision in formal Council event history")
            new_state = payload.get("to_state")
            if new_state not in {
                "worker_results_pending",
                "incumbent_validated",
                "replan_required",
            }:
                raise ValueError("cell_advanced contains an unsupported target state")
        elif event_type == "research.council.cell_closed":
            expected = {"incumbent_validated", "replan_required"}
            if payload.get("agenda_revision") != active_agenda_revision:
                raise ValueError("stale agenda revision in formal Council event history")
            if payload.get("cell_revision") != active_cell_revision:
                raise ValueError("stale cell revision in formal Council event history")
            new_state = "closed"
        elif event_type == "research.council.outcome_accepted":
            expected = {"closed"}
            if payload.get("agenda_revision") != active_agenda_revision:
                raise ValueError("stale agenda revision in formal Council event history")
            if payload.get("route_decision_id") != active_route_decision_id:
                raise ValueError("stale route decision in formal Council event history")
            if payload.get("route") != selected_route:
                raise ValueError("accepted outcome contradicts the selected route")
            expected_cell_revision = (
                active_cell_revision if selected_route == "bounded_decision_cell" else None
            )
            if payload.get("cell_revision") != expected_cell_revision:
                raise ValueError("stale cell revision in accepted Council outcome")
            new_state = "accepted"
        else:  # pragma: no cover - exhaustive with FORMAL_EVENT_TYPES
            raise ValueError(f"unhandled formal Council event type: {event_type}")
        if state not in expected:
            raise ValueError(
                f"invalid formal Council transition: {state} --{event_type}--> {new_state}"
            )
        state = str(new_state)
    return []
