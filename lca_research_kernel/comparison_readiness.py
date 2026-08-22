"""Compile the current TianGong-native comparison-readiness gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


JsonObject = Dict[str, object]
READINESS_CONTRACT = "lca_research_kernel.current_comparison_readiness_gate/v1"
NATIVE_REGISTER_CONTRACT = "lca_research_kernel.native_current_model_register/v1"
LINEAGE_CONTRACT = "lca_research_kernel.model_lineage_invalidation/v1"
MANIFEST_CONTRACT = "lca_research_kernel.evidence_acquisition_manifest/v1"
STOICH_CONTRACT = "lca_research_kernel.elemental_stoichiometric_consistency/v1"
CLAIM_MATRIX_CONTRACT = "lca_research_kernel.route_model_assurance_claim_matrix/v1"
TRANSACTION_CONTRACT = "lca_research_kernel.evidence_admission_transaction_boundary/v1"

EXPECTED_GAPS = {
    "carbon-coating-inventory",
    "elementary-emissions-characterization",
    "route-aligned-primary-inventories",
    "source-bound-uncertainty-model",
    "performance-protocol-full-text",
}


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


def _gate(
    check_id: str,
    category: str,
    status: str,
    observed_facts: List[str],
    source_binding_keys: List[str],
    *,
    release_conditions: Optional[List[str]] = None,
    responsible_activity: Optional[str] = None,
) -> JsonObject:
    if status not in {"satisfied", "ready_internal_activity", "blocked", "not_applicable"}:
        raise ValueError("unknown readiness check status")
    result: JsonObject = {
        "check_id": check_id,
        "category": category,
        "status": status,
        "observed_facts": observed_facts,
        "source_binding_keys": source_binding_keys,
    }
    if release_conditions:
        result["release_conditions"] = release_conditions
    if responsible_activity:
        result["responsible_activity"] = responsible_activity
    if status == "blocked" and (not release_conditions or not responsible_activity):
        raise ValueError("blocked checks require release conditions and an activity")
    return result


def _validate_native(native: JsonObject) -> JsonObject:
    if (
        native.get("contract") != NATIVE_REGISTER_CONTRACT
        or native.get("status") != "native_current_model_register_compiled"
        or native.get("tool_identity") != "@tiangong-lca/cli@0.0.25"
        or native.get("scenario_route_count") != 2
        or native.get("scientific_comparison_ready") is not False
        or native.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("accepted native current-model register is required")
    validation = native.get("process_validation")
    if (
        not isinstance(validation, dict)
        or validation.get("total") != 2
        or validation.get("valid") != 2
        or validation.get("invalid") != 0
    ):
        raise ValueError("2/2 native Process validation is required")
    _binding(validation.get("binding"), "process validation binding")
    _binding(native.get("scenario_binding"), "scenario binding")
    route_qa = native.get("route_qa")
    if (
        not isinstance(route_qa, list)
        or len(route_qa) != 2
        or {row.get("route_id") for row in route_qa if isinstance(row, dict)}
        != {"hydrothermal", "solid-state"}
        or any(
            not isinstance(row, dict)
            or row.get("gate_status") != "passed"
            or row.get("finding_count") != 0
            or row.get("blocker_count") != 0
            for row in route_qa
        )
    ):
        raise ValueError("two blocker-free native Process QA reports are required")
    for row in route_qa:
        _binding(row.get("binding"), f"{row.get('route_id')} QA binding")
    entities = native.get("local_entities")
    if (
        not isinstance(entities, dict)
        or entities.get("flow_count") != 11
        or entities.get("source_count") != 4
        or entities.get("remote_duplicate_coverage") != "missing"
        or entities.get("support_reference_status") != "pending_remote_resolution"
    ):
        raise ValueError("current local entity boundary is required")
    _binding(entities.get("flow_validation_binding"), "flow validation binding")
    _binding(entities.get("source_validation_binding"), "source validation binding")
    capabilities = native.get("native_capabilities")
    required_commands = {
        "auto-build",
        "validate-build",
        "graph",
        "qa",
        "build-resulting-process",
        "orchestrate",
        "save-draft",
    }
    if (
        not isinstance(capabilities, dict)
        or set(capabilities.get("lifecyclemodel_commands", [])) != required_commands
        or capabilities.get("lifecyclemodel_contract_available") is not True
        or capabilities.get("lciamethod_contract_available") is not True
        or capabilities.get("lcia_calculation_command_available") is not False
        or capabilities.get("multi_variant_comparison_command_available") is not False
    ):
        raise ValueError("pinned TianGong lifecyclemodel and LCIA capability boundary is required")
    return {"validation": validation, "route_qa": route_qa, "entities": entities, "capabilities": capabilities}


def _validate_science(
    inventory: JsonObject,
    lineage: JsonObject,
    manifest: JsonObject,
    stoich: JsonObject,
    claims: JsonObject,
    transaction: JsonObject,
) -> JsonObject:
    if (
        inventory.get("status") != "screening_proxy_plan_ready"
        or inventory.get("readiness", {}).get("performance_aligned_inventory")
        != "not_ready_source_gaps"
        or inventory.get("readiness", {}).get("comparative_lcia") != "prohibited"
        or inventory.get("scientific_comparison_ready") is not False
    ):
        raise ValueError("accepted source-bound screening inventory plan is required")
    routes = inventory.get("routes")
    if (
        not isinstance(routes, list)
        or len(routes) != 2
        or {row.get("route_id") for row in routes if isinstance(row, dict)}
        != {"hydrothermal", "solid-state"}
    ):
        raise ValueError("two source-bound route inventory plans are required")
    hydro = next(row for row in routes if row.get("route_id") == "hydrothermal")
    solid = next(row for row in routes if row.get("route_id") == "solid-state")
    if (
        hydro.get("allocation", {}).get("status")
        != "unallocated_joint_process_with_mass_allocation_metadata"
        or solid.get("allocation") is not None
        or not str(hydro.get("evidence_alignment", "")).startswith("screening_proxy_")
        or not str(solid.get("evidence_alignment", "")).startswith("screening_proxy_")
    ):
        raise ValueError("current route proxy and allocation boundaries are required")

    if (
        lineage.get("contract") != LINEAGE_CONTRACT
        or lineage.get("status") != "gap_progress_and_current_lineage_compiled"
    ):
        raise ValueError("accepted current lineage report is required")
    progress = lineage.get("gap_progress")
    blocked = progress.get("blocked_high_impact") if isinstance(progress, dict) else None
    if (
        not isinstance(blocked, list)
        or {row.get("gap_id") for row in blocked if isinstance(row, dict)} != EXPECTED_GAPS
        or progress.get("ready_scientific_gap_count") != 0
        or progress.get("scientific_comparison_ready") is not False
    ):
        raise ValueError("five current blocked scientific gaps are required")

    if (
        manifest.get("contract") != MANIFEST_CONTRACT
        or manifest.get("status") != "lawful_evidence_acquisition_manifest_compiled"
        or manifest.get("source_target_count") != 2
        or manifest.get("values_invented") is not False
    ):
        raise ValueError("accepted evidence manifest is required")
    target_rows = manifest.get("source_targets")
    fields = [
        field
        for target in target_rows if isinstance(target_rows, list) and isinstance(target, dict)
        for field in target.get("required_fields", [])
        if isinstance(field, dict)
        and field.get("status") == "unresolved"
        and field.get("value_invented") is False
    ] if isinstance(target_rows, list) else []
    if len(fields) != 15:
        raise ValueError("15 unresolved fields are required")

    if (
        stoich.get("contract") != STOICH_CONTRACT
        or stoich.get("status") != "elemental_stoichiometric_consistency_compiled"
        or stoich.get("route_count") != 2
        or stoich.get("conditionally_consistent_route_count") != 2
        or stoich.get("quantitative_bracket_status")
        != "unbounded_pending_formula_basis_evidence"
        or stoich.get("residual_species_reclassification_allowed") is not False
        or stoich.get("model_mutation_allowed") is not False
    ):
        raise ValueError("accepted conditional stoichiometric boundary is required")
    if (
        claims.get("contract") != CLAIM_MATRIX_CONTRACT
        or claims.get("status") != "route_model_assurance_claim_matrix_compiled"
        or claims.get("claim_count") != 26
        or claims.get("verified_executable_fact_count") != 8
        or claims.get("conditional_screening_fact_count") != 4
        or claims.get("unresolved_evidence_dependency_count") != 8
        or claims.get("prohibited_claim_count") != 6
        or claims.get("readiness_representation")
        != "typed_claim_classes_no_aggregate_score"
    ):
        raise ValueError("accepted typed claim matrix is required")
    if (
        transaction.get("contract") != TRANSACTION_CONTRACT
        or transaction.get("status")
        != "evidence_admission_transaction_boundary_compiled"
        or transaction.get("state_count") != 11
        or transaction.get("fixture_count") != 6
        or transaction.get("fixture_expectation_match_count") != 6
        or transaction.get("commit_point_count") != 1
        or transaction.get("evidence_admission_executed_count") != 0
        or transaction.get("model_mutation_executed_count") != 0
        or transaction.get("partial_commit_count") != 0
        or transaction.get("routing", {}).get("next_internal_route")
        != "compile-current-comparison-readiness-gate"
    ):
        raise ValueError("accepted zero-effect admission transaction is required")
    return {"routes": routes, "blocked": blocked, "fields": fields}


def compile_current_comparison_readiness_gate(
    native: JsonObject,
    native_binding: JsonObject,
    inventory: JsonObject,
    inventory_binding: JsonObject,
    lineage: JsonObject,
    lineage_binding: JsonObject,
    manifest: JsonObject,
    manifest_binding: JsonObject,
    stoich: JsonObject,
    stoich_binding: JsonObject,
    claims: JsonObject,
    claims_binding: JsonObject,
    transaction: JsonObject,
    transaction_binding: JsonObject,
) -> JsonObject:
    """Compile current structural/scientific/native readiness without calculation."""

    native_state = _validate_native(native)
    science = _validate_science(inventory, lineage, manifest, stoich, claims, transaction)
    scopes = {
        _scope(document, label)
        for label, document in (
            ("native", native),
            ("inventory", inventory),
            ("lineage", lineage),
            ("manifest", manifest),
            ("stoich", stoich),
            ("claims", claims),
            ("transaction", transaction),
        )
    }
    if len(scopes) != 1:
        raise ValueError("comparison readiness source scope conflicts")
    scope_id, revision = next(iter(scopes))
    bindings = {
        "native_model_register": _binding(native_binding, "native binding"),
        "inventory_plan": _binding(inventory_binding, "inventory binding"),
        "lineage_report": _binding(lineage_binding, "lineage binding"),
        "evidence_manifest": _binding(manifest_binding, "manifest binding"),
        "stoichiometric_report": _binding(stoich_binding, "stoich binding"),
        "claim_matrix": _binding(claims_binding, "claims binding"),
        "admission_transaction": _binding(transaction_binding, "transaction binding"),
    }
    checks = [
        _gate("native-process-schema-validation", "native_structure", "satisfied", ["current residual scenario rows pass 2/2 TIDAS Process validation"], ["native_model_register"]),
        _gate("native-process-qa", "native_structure", "satisfied", ["both current route Process QA reports pass with zero finding and blocker"], ["native_model_register"]),
        _gate("native-lifecyclemodel-assembly", "native_structure", "ready_internal_activity", ["pinned CLI exposes lifecyclemodel planning, validation, graph, QA and resulting-process commands", "two current process rows are content-addressed and native-valid"], ["native_model_register"], responsible_activity="plan-native-route-lifecyclemodel-assembly"),
        _gate("functional-unit-and-effective-scope-alignment", "scientific_scope", "blocked", ["both routes use a 1 kg uncoated route-core screening basis", "exact performance protocol and coated-product scope remain unresolved"], ["inventory_plan", "evidence_manifest", "claim_matrix"], release_conditions=["accept target-bound protocol and coating-scope evidence", "re-run scope/performance gate and affected model rebuild"], responsible_activity="admit-performance-protocol-and-scope-evidence"),
        _gate("route-aligned-primary-inventories", "inventory_evidence", "blocked", ["both route inventories retain explicit screening-proxy mismatch", "five scientific gaps remain evidence-blocked"], ["inventory_plan", "lineage_report", "evidence_manifest"], release_conditions=["admit complete route- and sample-bound primary inventory evidence for both targets", "rebuild and revalidate both route scenarios"], responsible_activity="admit-and-rebuild-route-primary-inventories"),
        _gate("carbon-coating-stage-completeness", "inventory_evidence", "blocked", ["coating yield, purge gas, energy, emissions and exact sample conditions remain unresolved"], ["lineage_report", "evidence_manifest", "claim_matrix"], release_conditions=["admit coating-basis, yield, gas, energy and emission evidence", "materialize and validate route-specific coating stages"], responsible_activity="admit-and-build-carbon-coating-stages"),
        _gate("elementary-flow-species-compartment-characterization", "inventory_and_lcia", "blocked", ["mass-conserving residuals have unknown composition", "species reclassification and characterization are prohibited"], ["native_model_register", "stoichiometric_report", "lineage_report"], release_conditions=["admit species-, phase-, disposition- and compartment-resolved evidence", "resolve native elementary-flow identities and characterization references"], responsible_activity="replace-residuals-with-characterized-elementary-flows"),
        _gate("allocation-consistency", "model_method", "blocked", ["hydrothermal is an unallocated joint process with mass-allocation metadata", "solid-state is modeled as a single-product route"], ["inventory_plan", "claim_matrix"], release_conditions=["select and justify a common comparison allocation/system-expansion policy", "rebuild both route models under the accepted policy"], responsible_activity="compile-and-apply-comparative-allocation-policy"),
        _gate("remote-identity-and-support-reference-coverage", "integration", "blocked", ["11 local Flow and four Source rows validate", "remote duplicate coverage and support-reference resolution remain missing"], ["native_model_register"], release_conditions=["complete authorized remote identity checks", "resolve and validate all support references without local placeholders"], responsible_activity="resolve-remote-identity-and-support-references"),
        _gate("source-bound-uncertainty-model", "uncertainty", "blocked", ["conditional formula basis is unbounded", "no defensible ranges, distributions, priors, likelihoods or response model exist"], ["stoichiometric_report", "lineage_report", "claim_matrix"], release_conditions=["register source-bound parameter bounds/distributions", "define and validate a comparison-ready response model before analysis"], responsible_activity="compile-source-bound-uncertainty-model"),
        _gate("native-lcia-method-and-calculation-surface", "native_lcia", "blocked", ["TIDAS lifecyclemodel and lciamethod contracts are available", "pinned CLI exposes no LCIA calculation or multi-variant comparison command", "no source-bound LCIA method/characterization set is selected"], ["native_model_register", "claim_matrix"], release_conditions=["select and content-bind an applicable LCIA method and characterization factors", "implement or bind a verified TianGong-native calculation surface"], responsible_activity="resolve-native-lcia-method-and-calculation-capability"),
        _gate("comparative-claim-release", "assurance", "blocked", ["eight unresolved evidence dependencies and six prohibited claims remain", "atomic future admission is available but no evidence has been admitted"], ["claim_matrix", "admission_transaction"], release_conditions=["satisfy every relevant evidence and model release condition", "recompile claim matrix after governed rebuild and validation"], responsible_activity="revalidate-and-release-comparative-claims"),
    ]
    counts = Counter(row["status"] for row in checks)
    expected_counts = {"blocked": 9, "ready_internal_activity": 1, "satisfied": 2}
    if dict(sorted(counts.items())) != expected_counts:
        raise ValueError("comparison readiness check counts drifted")
    result_identity = {
        "contract": READINESS_CONTRACT,
        "scope_id": scope_id,
        "revision": revision,
        "source_bindings": bindings,
        "checks": checks,
    }
    return {
        "schema_version": 1,
        "contract": READINESS_CONTRACT,
        "scope_id": scope_id,
        "revision": revision,
        "status": "current_comparison_readiness_gate_compiled",
        "gate_id": _hash(result_identity),
        "source_bindings": bindings,
        "overall_status": "local_native_lifecyclemodel_planning_ready_comparative_science_closed",
        "readiness_representation": "typed_checks_no_aggregate_score",
        "check_count": len(checks),
        "satisfied_check_count": counts["satisfied"],
        "ready_internal_activity_count": counts["ready_internal_activity"],
        "blocked_check_count": counts["blocked"],
        "status_counts": expected_counts,
        "checks": checks,
        "blocked_scientific_gap_count": len(science["blocked"]),
        "unresolved_field_count": len(science["fields"]),
        "verified_executable_claim_count": claims["verified_executable_fact_count"],
        "conditional_claim_count": claims["conditional_screening_fact_count"],
        "unresolved_claim_count": claims["unresolved_evidence_dependency_count"],
        "prohibited_claim_count": claims["prohibited_claim_count"],
        "native_tool_identity": native["tool_identity"],
        "native_lifecyclemodel_planning_available": True,
        "native_lcia_calculation_command_available": native_state["capabilities"]["lcia_calculation_command_available"],
        "native_multi_variant_comparison_command_available": native_state["capabilities"]["multi_variant_comparison_command_available"],
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "network_read_executed_count": 0,
        "external_action_executed_count": 0,
        "remote_write_executed_count": 0,
        "model_mutation_executed_count": 0,
        "claim_transition_executed_count": 0,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "routing": {
            "next_internal_route": "plan-native-route-lifecyclemodel-assembly",
            "pause_internal_work": False,
        },
        "next_action": (
            "Compile two route-local TianGong lifecyclemodel orchestration plans from the "
            "current scenario rows and validate the bridge contract; do not execute comparison or LCIA."
        ),
    }


def write_report(report: JsonObject, output: Path) -> None:
    if report.get("contract") != READINESS_CONTRACT:
        raise ValueError("comparison readiness report is required")
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
        "native-register", "native-binding", "inventory-plan", "inventory-binding",
        "lineage-report", "lineage-binding", "evidence-manifest", "manifest-binding",
        "stoichiometric-report", "stoich-binding", "claim-matrix", "claims-binding",
        "admission-transaction", "transaction-binding",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = compile_current_comparison_readiness_gate(
        _read_json(args.native_register), _read_json(args.native_binding),
        _read_json(args.inventory_plan), _read_json(args.inventory_binding),
        _read_json(args.lineage_report), _read_json(args.lineage_binding),
        _read_json(args.evidence_manifest), _read_json(args.manifest_binding),
        _read_json(args.stoichiometric_report), _read_json(args.stoich_binding),
        _read_json(args.claim_matrix), _read_json(args.claims_binding),
        _read_json(args.admission_transaction), _read_json(args.transaction_binding),
    )
    write_report(report, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
