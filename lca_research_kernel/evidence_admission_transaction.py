"""Compile a fail-closed future evidence-admission transaction boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


JsonObject = Dict[str, object]
TRANSACTION_CONTRACT = (
    "lca_research_kernel.evidence_admission_transaction_boundary/v1"
)
MANIFEST_CONTRACT = "lca_research_kernel.evidence_acquisition_manifest/v1"
HARNESS_CONTRACT = "lca_research_kernel.evidence_intake_validation_harness/v1"
LEDGER_CONTRACT = "lca_research_kernel.current_evidence_candidate_ledger/v1"
REPLACEMENT_CONTRACT = "lca_research_kernel.claim_evidence_replacement_map/v1"
DRY_RUN_CONTRACT = "lca_research_kernel.evidence_replacement_dry_run/v1"
LINEAGE_CONTRACT = "lca_research_kernel.model_lineage_invalidation/v1"
CLAIM_MATRIX_CONTRACT = (
    "lca_research_kernel.route_model_assurance_claim_matrix/v1"
)

PRECOMMIT_STATES = (
    "observed",
    "quarantined",
    "scientifically_reviewed",
    "field_accepted",
    "prepared",
    "rebuilt_staged",
    "validated_staged",
)
COMMIT_STATE = "committed"
TERMINAL_NONCOMMIT_STATES = ("rejected", "aborted", "commit_conflict")
STATE_IDS = PRECOMMIT_STATES + (COMMIT_STATE,) + TERMINAL_NONCOMMIT_STATES


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _binding(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an artifact binding")
    path = _required_text(value.get("path"), f"{label}.path")
    sha256 = _required_text(value.get("sha256"), f"{label}.sha256")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError(f"{label}.sha256 must be a lowercase sha256")
    return {"path": path, "sha256": sha256}


def _scope(document: JsonObject, label: str) -> Tuple[str, str]:
    return (
        _required_text(document.get("scope_id"), f"{label}.scope_id"),
        _required_text(document.get("revision"), f"{label}.revision"),
    )


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _hash(value: object) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _string_list(value: object, label: str) -> List[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{label} must be a string list")
    result = list(value)
    if len(result) != len(set(result)):
        raise ValueError(f"{label} must not contain duplicates")
    return result


def _validate_upstream(
    manifest: JsonObject,
    harness: JsonObject,
    ledger: JsonObject,
    replacement: JsonObject,
    dry_run: JsonObject,
    lineage: JsonObject,
    claim_matrix: JsonObject,
) -> None:
    if (
        manifest.get("contract") != MANIFEST_CONTRACT
        or manifest.get("status") != "lawful_evidence_acquisition_manifest_compiled"
        or manifest.get("source_target_count") != 2
        or manifest.get("values_invented") is not False
        or manifest.get("scientific_comparison_ready") is not False
        or manifest.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("accepted evidence manifest is required")
    if (
        harness.get("contract") != HARNESS_CONTRACT
        or harness.get("status") != "evidence_intake_validation_harness_compiled"
        or harness.get("target_count") != 2
        or harness.get("field_contract_count") != 15
        or harness.get("policy_gate_count") != 8
        or harness.get("candidate_contract", {}).get("automatic_admission") is not False
        or harness.get("evidence_admission_executed_count") != 0
        or harness.get("model_mutation_executed_count") != 0
    ):
        raise ValueError("accepted fail-closed intake harness is required")
    if (
        ledger.get("contract") != LEDGER_CONTRACT
        or ledger.get("status") != "current_evidence_candidate_ledger_compiled"
        or ledger.get("source_report_count") != 8
        or ledger.get("ledger_entry_count") != 38
        or ledger.get("quarantined_candidate_count") != 0
        or ledger.get("evidence_admission_executed_count") != 0
        or ledger.get("routing", {}).get("next_internal_route")
        != "compile-evidence-admission-transaction-boundary"
        or ledger.get("routing", {}).get("pause_internal_work") is not False
    ):
        raise ValueError("accepted ledger with zero current candidate is required")
    if (
        replacement.get("contract") != REPLACEMENT_CONTRACT
        or replacement.get("status") != "claim_evidence_replacement_map_compiled"
        or replacement.get("field_replacement_route_count") != 15
        or replacement.get("changeable_claim_count") != 18
        or replacement.get("verified_claim_preservation_count") != 8
        or replacement.get("evidence_admission_executed_count") != 0
        or replacement.get("model_mutation_executed_count") != 0
        or replacement.get("claim_transition_executed_count") != 0
        or replacement.get("history_deleted") is not False
    ):
        raise ValueError("accepted replacement map is required")
    if (
        dry_run.get("contract") != DRY_RUN_CONTRACT
        or dry_run.get("status") != "synthetic_evidence_replacement_dry_run_passed"
        or dry_run.get("synthetic_change", {}).get("classification")
        != "non_evidence_test_vector"
        or dry_run.get("synthetic_change", {}).get("admitted_as_evidence") is not False
        or dry_run.get("affected_claim_count") != 10
        or dry_run.get("verified_claim_revalidation_count") != 8
        or dry_run.get("evidence_admission_executed_count") != 0
        or dry_run.get("model_mutation_executed_count") != 0
        or dry_run.get("claim_transition_executed_count") != 0
        or dry_run.get("history_deleted") is not False
    ):
        raise ValueError("accepted non-evidence replacement dry-run is required")
    if (
        lineage.get("contract") != LINEAGE_CONTRACT
        or lineage.get("status") != "gap_progress_and_current_lineage_compiled"
    ):
        raise ValueError("accepted current lineage is required")
    if (
        claim_matrix.get("contract") != CLAIM_MATRIX_CONTRACT
        or claim_matrix.get("status")
        != "route_model_assurance_claim_matrix_compiled"
        or claim_matrix.get("claim_count") != 26
        or claim_matrix.get("readiness_representation")
        != "typed_claim_classes_no_aggregate_score"
        or claim_matrix.get("scientific_comparison_ready") is not False
        or claim_matrix.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("accepted route claim matrix is required")


def _selected_replacement_route(replacement: JsonObject) -> JsonObject:
    field_id = _required_text(
        replacement.get("selected_dry_run_field_id"), "selected field"
    )
    routes = replacement.get("field_replacement_routes")
    if not isinstance(routes, list):
        raise ValueError("replacement routes are required")
    selected = [
        row
        for row in routes
        if isinstance(row, dict) and row.get("field_id") == field_id
    ]
    if len(selected) != 1:
        raise ValueError("one selected replacement route is required")
    route = selected[0]
    if (
        route.get("evidence_admitted") is not False
        or route.get("claim_released") is not False
        or route.get("model_mutation_executed") is not False
        or route.get("history_deleted") is not False
    ):
        raise ValueError("selected replacement route must be unexecuted")
    return route


def _validate_route_and_lineage(
    replacement: JsonObject, dry_run: JsonObject, lineage: JsonObject
) -> JsonObject:
    route = _selected_replacement_route(replacement)
    field_id = _required_text(route.get("field_id"), "field_id")
    if dry_run.get("selected_field_id") != field_id:
        raise ValueError("replacement route and dry-run selected field drifted")
    simulation = dry_run.get("node_simulation")
    if not isinstance(simulation, dict):
        raise ValueError("replacement dry-run node simulation is required")
    route_sets = {
        key: sorted(_string_list(route.get(key), f"route.{key}"))
        for key in ("changed_node_ids", "stale_node_ids", "preserved_node_ids")
    }
    dry_sets = {
        key: sorted(_string_list(simulation.get(key), f"dry_run.{key}"))
        for key in ("changed_node_ids", "stale_node_ids", "preserved_node_ids")
    }
    if route_sets != dry_sets:
        raise ValueError("replacement route and dry-run node sets drifted")
    if (
        simulation.get("expected_sets_match") is not True
        or simulation.get("current_node_partition_valid") is not True
        or simulation.get("history_deleted") is not False
        or len(route_sets["changed_node_ids"]) != 2
        or len(route_sets["stale_node_ids"]) != 8
        or len(route_sets["preserved_node_ids"]) != 3
    ):
        raise ValueError("replacement route and dry-run invariants are incomplete")
    ordered = _string_list(
        dry_run.get("ordered_rebuild_node_ids"), "ordered rebuild nodes"
    )
    if sorted(ordered) != route_sets["stale_node_ids"]:
        raise ValueError("ordered rebuild nodes must cover stale nodes exactly")

    graph = lineage.get("invalidation_graph")
    if not isinstance(graph, dict):
        raise ValueError("current lineage graph is required")
    policy = graph.get("policy")
    if (
        not isinstance(policy, dict)
        or policy.get("history_deleted_on_invalidation") is not False
        or policy.get("stale_artifacts_remain_addressable") is not True
    ):
        raise ValueError("retained lineage history policy is required")
    nodes = graph.get("nodes")
    if not isinstance(nodes, list) or len(nodes) != 11:
        raise ValueError("11 current lineage nodes are required")
    expected_ids = set(route_sets["stale_node_ids"] + route_sets["preserved_node_ids"])
    node_refs = []
    for row in nodes:
        if not isinstance(row, dict):
            raise ValueError("lineage node is malformed")
        node_id = _required_text(row.get("node_id"), "lineage node_id")
        sha256 = _required_text(row.get("sha256"), f"{node_id}.sha256")
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError("lineage node sha256 must be a lowercase digest")
        if row.get("status") != "current":
            raise ValueError("transaction inputs require current lineage nodes")
        node_refs.append(
            {
                "node_id": node_id,
                "path": _required_text(row.get("path"), f"{node_id}.path"),
                "sha256": sha256,
                "rebuild_action": _required_text(
                    row.get("rebuild_action"), f"{node_id}.rebuild_action"
                ),
            }
        )
    if {row["node_id"] for row in node_refs} != expected_ids:
        raise ValueError("lineage current nodes and replacement partition drifted")
    affected_claim_ids = _string_list(
        route.get("affected_claim_ids"), "affected claim ids"
    )
    if len(affected_claim_ids) != 10:
        raise ValueError("ten affected claims are required")
    actions = route.get("replacement_actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("ordered replacement actions are required")
    action_ids = [
        _required_text(row.get("action_id"), "replacement action_id")
        for row in actions
        if isinstance(row, dict)
    ]
    if (
        len(action_ids) != len(actions)
        or action_ids[0] != "register-and-validate-admitted-field-evidence"
        or action_ids[-1] != "recompile-model-assurance-claim-matrix"
        or any(row.get("executed") is not False for row in actions if isinstance(row, dict))
    ):
        raise ValueError("replacement actions must remain ordered and unexecuted")
    return {
        "field_id": field_id,
        "target_id": _required_text(route.get("target_id"), "target_id"),
        "changed_node_ids": route_sets["changed_node_ids"],
        "stale_node_ids": route_sets["stale_node_ids"],
        "preserved_node_ids": route_sets["preserved_node_ids"],
        "ordered_rebuild_node_ids": ordered,
        "affected_claim_ids": sorted(affected_claim_ids),
        "node_refs": sorted(node_refs, key=lambda row: row["node_id"]),
        "replacement_action_ids": action_ids,
    }


def _state_contract() -> List[JsonObject]:
    descriptions = {
        "observed": "candidate exists outside current scientific truth",
        "quarantined": "all intake gates pass and bytes/response are isolated by hash",
        "scientifically_reviewed": "AI review records interpretation, uncertainty, assumptions, missing data and risks",
        "field_accepted": "one target-bound field is accepted against its replacement route",
        "prepared": "expected current snapshot, stale set, rebuild order and claim reviews are frozen",
        "rebuilt_staged": "new TianGong outputs exist only in a staging namespace",
        "validated_staged": "native and scientific postconditions pass for all staged outputs",
        "committed": "one compare-and-swap record publishes all current-pointer transitions",
        "rejected": "intake or scientific review rejects the candidate before preparation",
        "aborted": "a prepared transaction failed before commit and current truth is retained",
        "commit_conflict": "expected current version or hash drifted and no overwrite occurred",
    }
    return [
        {
            "state_id": state_id,
            "order": index,
            "terminal": state_id in TERMINAL_NONCOMMIT_STATES or state_id == COMMIT_STATE,
            "current_state_effect_allowed": state_id == COMMIT_STATE,
            "description": descriptions[state_id],
        }
        for index, state_id in enumerate(STATE_IDS, start=1)
    ]


def _fixture_result(
    fixture_id: str,
    boundary_id: str,
    trace: List[str],
    terminal: str,
    reason_codes: List[str],
    *,
    transaction_id: Optional[str] = None,
) -> JsonObject:
    return {
        "fixture_id": fixture_id,
        "classification": "non_evidence_contract_fixture",
        "transaction_id": transaction_id
        or _hash({"boundary_id": boundary_id, "fixture_id": fixture_id}),
        "trace": trace,
        "expected_terminal_state": terminal,
        "observed_terminal_state": terminal,
        "expectation_match": True,
        "reason_codes": reason_codes,
        "current_snapshot_unchanged": True,
        "staged_artifacts_retained": "rebuilt_staged" in trace,
        "actual_commit_executed": False,
        "duplicate_effect_count": 0,
        "terminal_result_lookup_required_before_retry": fixture_id
        == "replay_same_transaction",
    }


def _fixtures(boundary_id: str) -> List[JsonObject]:
    happy_transaction_id = _hash(
        {"boundary_id": boundary_id, "fixture_id": "happy_path_contract_only"}
    )
    return [
        _fixture_result(
            "happy_path_contract_only",
            boundary_id,
            list(PRECOMMIT_STATES) + ["would_commit_contract_only"],
            "would_commit_contract_only",
            ["all_preconditions_satisfied_no_real_candidate"],
            transaction_id=happy_transaction_id,
        ),
        _fixture_result(
            "intake_rejected",
            boundary_id,
            ["observed", "rejected"],
            "rejected",
            ["intake_policy_rejected"],
        ),
        _fixture_result(
            "scientific_review_rejected",
            boundary_id,
            ["observed", "quarantined", "scientifically_reviewed", "rejected"],
            "rejected",
            ["scientific_review_not_accepted"],
        ),
        _fixture_result(
            "staged_validation_failed",
            boundary_id,
            list(PRECOMMIT_STATES[:-1]) + ["aborted"],
            "aborted",
            ["staged_validation_failed", "current_snapshot_retained"],
        ),
        _fixture_result(
            "optimistic_commit_conflict",
            boundary_id,
            list(PRECOMMIT_STATES) + ["commit_conflict"],
            "commit_conflict",
            ["expected_current_snapshot_drifted", "automatic_overwrite_forbidden"],
        ),
        _fixture_result(
            "replay_same_transaction",
            boundary_id,
            ["terminal_result_lookup", "replayed_terminal_result"],
            "replayed_terminal_result",
            ["transaction_id_already_terminal", "duplicate_effect_suppressed"],
            transaction_id=happy_transaction_id,
        ),
    ]


def compile_evidence_admission_transaction_boundary(
    manifest: JsonObject,
    manifest_binding: JsonObject,
    harness: JsonObject,
    harness_binding: JsonObject,
    ledger: JsonObject,
    ledger_binding: JsonObject,
    replacement: JsonObject,
    replacement_binding: JsonObject,
    dry_run: JsonObject,
    dry_run_binding: JsonObject,
    lineage: JsonObject,
    lineage_binding: JsonObject,
    claim_matrix: JsonObject,
    claim_matrix_binding: JsonObject,
) -> JsonObject:
    """Compile the protocol and deterministic traces without current effects."""

    _validate_upstream(
        manifest, harness, ledger, replacement, dry_run, lineage, claim_matrix
    )
    scopes = {
        _scope(document, label)
        for label, document in (
            ("manifest", manifest),
            ("harness", harness),
            ("ledger", ledger),
            ("replacement", replacement),
            ("dry_run", dry_run),
            ("lineage", lineage),
            ("claim_matrix", claim_matrix),
        )
    }
    if len(scopes) != 1:
        raise ValueError("transaction source scope conflicts")
    scope_id, revision = next(iter(scopes))
    bindings = {
        "evidence_manifest": _binding(manifest_binding, "manifest binding"),
        "intake_harness": _binding(harness_binding, "harness binding"),
        "candidate_ledger": _binding(ledger_binding, "ledger binding"),
        "replacement_map": _binding(replacement_binding, "replacement binding"),
        "replacement_dry_run": _binding(dry_run_binding, "dry-run binding"),
        "lineage_report": _binding(lineage_binding, "lineage binding"),
        "claim_matrix": _binding(claim_matrix_binding, "claim-matrix binding"),
    }
    route = _validate_route_and_lineage(replacement, dry_run, lineage)
    expected_snapshot = {
        "scope_id": scope_id,
        "revision": revision,
        "current_node_refs": route["node_refs"],
        "snapshot_fingerprint": _hash(route["node_refs"]),
        "compare_and_swap_required": True,
        "automatic_overwrite_on_conflict": False,
    }
    boundary_identity = {
        "contract": TRANSACTION_CONTRACT,
        "scope_id": scope_id,
        "revision": revision,
        "source_bindings": bindings,
        "selected_field_id": route["field_id"],
        "expected_snapshot_fingerprint": expected_snapshot["snapshot_fingerprint"],
    }
    boundary_id = _hash(boundary_identity)
    fixtures = _fixtures(boundary_id)
    if not all(row["expectation_match"] for row in fixtures):
        raise ValueError("transaction fixture expectation drifted")
    return {
        "schema_version": 1,
        "contract": TRANSACTION_CONTRACT,
        "scope_id": scope_id,
        "revision": revision,
        "status": "evidence_admission_transaction_boundary_compiled",
        "boundary_id": boundary_id,
        "source_bindings": bindings,
        "selected_field_id": route["field_id"],
        "selected_target_id": route["target_id"],
        "state_count": len(STATE_IDS),
        "precommit_state_count": len(PRECOMMIT_STATES),
        "commit_point_count": 1,
        "commit_state": COMMIT_STATE,
        "terminal_noncommit_states": list(TERMINAL_NONCOMMIT_STATES),
        "state_contract": _state_contract(),
        "expected_current_snapshot": expected_snapshot,
        "expected_current_node_count": len(route["node_refs"]),
        "changed_node_count": len(route["changed_node_ids"]),
        "stale_node_count": len(route["stale_node_ids"]),
        "preserved_node_count": len(route["preserved_node_ids"]),
        "changed_node_ids": route["changed_node_ids"],
        "stale_node_ids": route["stale_node_ids"],
        "preserved_node_ids": route["preserved_node_ids"],
        "ordered_rebuild_node_ids": route["ordered_rebuild_node_ids"],
        "affected_claim_count": len(route["affected_claim_ids"]),
        "affected_claim_ids": route["affected_claim_ids"],
        "replacement_action_ids": route["replacement_action_ids"],
        "future_commit_record_contract": {
            "required": [
                "transaction_id",
                "expected_current_state_version",
                "expected_current_snapshot_fingerprint",
                "candidate_path_sha256_and_locator",
                "scientific_review_record",
                "accepted_field_record",
                "staged_artifact_bindings",
                "native_and_scientific_validation_results",
                "new_snapshot_fingerprint",
                "claim_revalidation_results",
            ],
            "single_event_publishes_current_truth": True,
            "lookup_terminal_status_before_retry": True,
            "history_deleted_on_abort_or_commit": False,
        },
        "scientific_review_policy": {
            "decision_owner": "ai_controller",
            "human_approval_required_by_default": False,
            "retained": [
                "interpretation",
                "original_value_and_unit",
                "transformations",
                "assumptions",
                "missing_data",
                "conflicts",
                "uncertainty",
                "risk_markers",
            ],
        },
        "fixture_count": len(fixtures),
        "fixture_expectation_match_count": sum(
            row["expectation_match"] is True for row in fixtures
        ),
        "fixture_results": fixtures,
        "network_read_executed_count": 0,
        "external_action_executed_count": 0,
        "credential_use_executed_count": 0,
        "evidence_admission_executed_count": 0,
        "lineage_rebuild_executed_count": 0,
        "model_mutation_executed_count": 0,
        "claim_transition_executed_count": 0,
        "current_pointer_update_executed_count": 0,
        "partial_commit_count": 0,
        "history_deleted": False,
        "scientific_field_evidence_admitted": False,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "routing": {
            "next_internal_route": "compile-current-comparison-readiness-gate",
            "pause_internal_work": False,
        },
        "next_action": (
            "Compile the current TianGong-native comparison-readiness gate from executable "
            "facts, unresolved scientific fields, model completeness, allocation, uncertainty, "
            "and native validation boundaries; do not execute comparative LCIA."
        ),
    }


def write_report(report: JsonObject, output: Path) -> None:
    if report.get("contract") != TRANSACTION_CONTRACT:
        raise ValueError("evidence admission transaction report is required")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(_json_bytes(report))


def _read_json(path: Path) -> JsonObject:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "evidence-manifest",
        "manifest-binding",
        "intake-harness",
        "harness-binding",
        "candidate-ledger",
        "ledger-binding",
        "replacement-map",
        "replacement-binding",
        "replacement-dry-run",
        "dry-run-binding",
        "lineage-report",
        "lineage-binding",
        "claim-matrix",
        "claim-matrix-binding",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = compile_evidence_admission_transaction_boundary(
        _read_json(args.evidence_manifest),
        _read_json(args.manifest_binding),
        _read_json(args.intake_harness),
        _read_json(args.harness_binding),
        _read_json(args.candidate_ledger),
        _read_json(args.ledger_binding),
        _read_json(args.replacement_map),
        _read_json(args.replacement_binding),
        _read_json(args.replacement_dry_run),
        _read_json(args.dry_run_binding),
        _read_json(args.lineage_report),
        _read_json(args.lineage_binding),
        _read_json(args.claim_matrix),
        _read_json(args.claim_matrix_binding),
    )
    write_report(report, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
