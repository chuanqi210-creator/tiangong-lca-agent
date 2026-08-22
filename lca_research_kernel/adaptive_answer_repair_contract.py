"""Stable contract for the bounded adaptive-answer evidence repair phase."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Mapping


CONTRACT = "adaptive_answer_evidence_repair_contract/v1"
REPAIR_TARGETS = [
    "candidate_claim_modality_contract",
    "official_source_identity_binding",
]
IMMUTABLE_EVIDENCE = {
    "heldout_task_v3": {
        "path": "outputs/real-question-optimization/adaptive-answer-held-out/fixture-v3/execution/task.json",
        "sha256": "e82bf94537a205097b1123c11a6d7001318fde44448ffa8acc1f0623579956a5",
    },
    "heldout_oracle_v3": {
        "path": "outputs/real-question-optimization/adaptive-answer-held-out/fixture-v3/grader-only/oracle.json",
        "sha256": "9e9ae1984ce3683e155a9a95e51c74b037519219d46bd7309dc78b6014bec2d8",
    },
    "heldout_run_v4": {
        "path": "outputs/real-question-optimization/adaptive-answer-held-out/run-v4/run.json",
        "sha256": "f574d95ab01d2ded26190ca459454a598bc7098b43303a7f012fee7880c72b01",
    },
    "runtime_project_record_v4": {
        "path": "outputs/real-question-optimization/adaptive-answer-held-out/runtime-data-v4/projects/adaptive-heldout-36ef455282b39a14e538cfbb/project-record.json",
        "sha256": "9030d2e623927b8af5c0376202e8242b96eedbea86980e8953de02434d93b173",
    },
    "negative_acceptance_v2": {
        "path": "outputs/real-question-optimization/adaptive-answer-held-out/acceptance/acceptance-v2.json",
        "sha256": "77be32a218c98b01ea8491d544c3c6eb99aed1f0eaa8f7e2af04c9f4168185bb",
    },
}
ALLOWED_OPERATIONS = [
    "deterministic_claim_modality_normalization",
    "official_source_binding_validation",
    "replay_frozen_raw_evidence",
    "deterministic_synthetic_probe",
    "local_receipt_write",
    "local_test_execution",
]
FORBIDDEN_OPERATIONS = [
    "real_model_call",
    "external_read",
    "external_write",
    "new_research_case",
    "nineteen_question_regression",
    "historical_evidence_mutation",
    "oracle_mutation",
    "threshold_reduction",
]
SOURCE_IDENTITY_POLICY = {
    "identity_layers": [
        "locator_identity",
        "snapshot_identity",
        "observation_identity",
    ],
    "legacy_untyped_digest": "retain_as_untyped_and_never_compare_as_transport_body",
    "same_locator_new_bytes": "candidate_revision_requires_assurance",
    "accepted_snapshot_replacement": "forbidden",
    "oracle_live_observation_mutation": "forbidden",
}
EVIDENCE_INVALIDATION_POLICY = {
    "historical_run_role": "immutable_negative_evidence_only",
    "product_contract_change": "future_execution_required_for_new_generalization_claim",
    "current_phase_validation": "deterministic_repair_probe_only",
    "cross_version_splicing": "forbidden",
    "failed_row_deletion": "forbidden",
}
NEXT_ROUTES = ["reproduce-and-repair-claim-semantics"]
EXPECTED_FIELDS = {
    "allowed_operations",
    "contract",
    "contract_sha256",
    "evidence_invalidation_policy",
    "forbidden_operations",
    "immutable_evidence",
    "next_routes",
    "repair_targets",
    "source_identity_policy",
    "unknown_field_policy",
}


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def validate_repair_contract(contract: Mapping[str, object]) -> dict:
    """Reject scope expansion, evidence mutation, and ambiguous source identity."""

    if not isinstance(contract, Mapping) or set(contract) != EXPECTED_FIELDS:
        raise ValueError("repair contract fields are invalid")
    normalized = deepcopy(dict(contract))
    if normalized["contract"] != CONTRACT:
        raise ValueError("repair contract identity drifted")
    if normalized["repair_targets"] != REPAIR_TARGETS:
        raise ValueError("repair targets drifted")
    if normalized["immutable_evidence"] != IMMUTABLE_EVIDENCE:
        raise ValueError("immutable evidence drifted")
    if normalized["allowed_operations"] != ALLOWED_OPERATIONS:
        raise ValueError("allowed operations drifted")
    if normalized["forbidden_operations"] != FORBIDDEN_OPERATIONS:
        raise ValueError("forbidden operations drifted")
    if normalized["source_identity_policy"] != SOURCE_IDENTITY_POLICY:
        raise ValueError("source identity policy drifted")
    if normalized["evidence_invalidation_policy"] != EVIDENCE_INVALIDATION_POLICY:
        raise ValueError("evidence invalidation policy drifted")
    if normalized["next_routes"] != NEXT_ROUTES:
        raise ValueError("repair contract requires exactly one next route")
    if normalized["unknown_field_policy"] != "reject":
        raise ValueError("repair contract unknown fields must be rejected")
    unsigned = deepcopy(normalized)
    stored = unsigned.pop("contract_sha256")
    if not isinstance(stored, str) or stored != _canonical_sha256(unsigned):
        raise ValueError("repair contract self hash drifted")
    return normalized


__all__ = ["validate_repair_contract"]
