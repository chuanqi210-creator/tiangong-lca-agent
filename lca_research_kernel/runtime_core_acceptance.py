"""Mechanical hard-gate compiler for D-18 production runtime completion."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Mapping

CORE_GATES = (
    "architecture_semantics",
    "run_trace",
    "context_assembler",
    "run_control",
    "execution_environment",
    "native_workflow",
    "architecture_evaluation",
)


def _sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _provider_receipt(value: object, provider: str) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("contract")
        == "lca_research_kernel.model_provider_probe_receipt/v1"
        and value.get("status") == "passed"
        and value.get("provider") == provider
        and value.get("real_call") is True
        and isinstance(value.get("actual_model"), str)
        and bool(value["actual_model"])
        and isinstance(value.get("provider_response_id"), str)
        and bool(value["provider_response_id"])
        and value.get("external_writes") == 0
        and value.get("scientific_result_created") is False
    )


def compile_runtime_core_acceptance(evidence: Mapping[str, object]) -> dict:
    if not isinstance(evidence, Mapping):
        raise ValueError("runtime-core acceptance evidence must be an object")
    errors = [
        f"{gate} failed" for gate in CORE_GATES if evidence.get(gate) is not True
    ]
    if evidence.get("forbidden_effects") != 0:
        errors.append("forbidden effects are nonzero")
    review = evidence.get("clean_review")
    if (
        not isinstance(review, Mapping)
        or review.get("p1") != 0
        or review.get("p2") != 0
    ):
        errors.append("clean review has unresolved P1/P2 findings")
    catalog = evidence.get("provider_catalog")
    receipts = evidence.get("provider_receipts")
    if (
        not isinstance(catalog, Mapping)
        or catalog.get("contract")
        != "lca_research_kernel.model_provider_catalog/v1"
        or not isinstance(catalog.get("current_validation_gate"), list)
        or not isinstance(catalog.get("providers"), list)
        or not isinstance(receipts, Mapping)
    ):
        errors.append("provider catalog or receipt map is invalid")
        validation_gate = []
        provider_gates = {}
        nonblocking_states = {}
    else:
        validation_gate = catalog["current_validation_gate"]
        provider_gates = {
            provider: _provider_receipt(receipts.get(provider), provider)
            for provider in validation_gate
            if isinstance(provider, str)
        }
        nonblocking_states = {
            row["provider_id"]: row["admission_state"]
            for row in catalog["providers"]
            if isinstance(row, Mapping)
            and isinstance(row.get("provider_id"), str)
            and row["provider_id"] not in validation_gate
        }
    verify = evidence.get("make_verify")
    verify_ready = (
        isinstance(verify, Mapping)
        and verify.get("status") == "passed"
        and verify.get("invocations") == 1
    )
    if errors:
        status = "failed"
        next_route = "repair-d18-production-runtime-hard-gates"
        completion_ready = False
    elif not provider_gates or not all(provider_gates.values()):
        status = "waiting_for_user"
        next_route = "verify-real-model-probes-after-user-configuration"
        completion_ready = False
    elif not verify_ready:
        status = "pending_final_verification"
        next_route = "run-single-final-lifecycle-verification"
        completion_ready = False
    else:
        status = "accepted"
        next_route = "run-first-controlled-native-lca-research-evaluation"
        completion_ready = True
    result = {
        "contract": "lca_research_kernel.runtime_core_acceptance/v1",
        "status": status,
        "completion_ready": completion_ready,
        "core_gates": {gate: evidence.get(gate) is True for gate in CORE_GATES},
        "provider_gates": {
            f"{provider}_real_structured_call": ready
            for provider, ready in provider_gates.items()
        },
        "nonblocking_provider_states": nonblocking_states,
        "clean_review": deepcopy(review),
        "forbidden_effects": evidence.get("forbidden_effects"),
        "final_verify": deepcopy(verify),
        "errors": errors,
        "next_route": next_route,
    }
    result["acceptance_sha256"] = _sha256(result)
    return result
