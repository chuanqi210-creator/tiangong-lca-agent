from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


CONTRACT_PATH = Path("docs/lca-agent-d18-core-contract.json")
P0_ACCEPTANCE_PATH = Path("docs/lca-agent-d18-p0-acceptance.json")
P1_CONTRACT_PATH = Path("docs/lca-agent-d18-p1-contract.json")
P1_ACCEPTANCE_PATH = Path("docs/lca-agent-d18-p1-acceptance.json")
P2_CONTRACT_PATH = Path("docs/lca-agent-d18-p2-evaluation-contract.json")
FIXTURE_REGISTRY_PATH = Path("docs/lca-agent-d18-evaluation-fixtures.json")
FINAL_INTEGRATION_PATH = Path("docs/lca-agent-d18-final-integration-contract.json")
FINAL_ACCEPTANCE_PATH = Path("docs/lca-agent-d18-final-acceptance.json")
TOP_LEVEL_PATH = Path("docs/lca-agent-top-level-architecture.json")
MAPPING_PATH = Path("docs/lca-agent-d18-existing-system-mapping.json")
MODULE_IDS = {
    "product_interface",
    "research_orchestrator",
    "lca_domain_kernel",
    "model_gateway",
    "capability_gateway",
    "project_record",
    "assurance_boundary",
}
FAILURE_LAYERS = {"prompt", "context", "harness", "loop", "model"}
FORBIDDEN_CASE_NAMES = ("council", "uslci", "lifepo4", "nev")
P2_GATES = {
    "fixture_isolation",
    "normal_product_path",
    "explicit_missing_data",
    "model_failure",
    "capability_failure",
    "unauthorized_effect",
    "interruption_recovery",
    "same_input_idempotency",
    "project_truth_writer_boundary",
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _matches_live_or_reachable_git_object(
    root: Path, relative_path: str, digest: object
) -> bool:
    path = root / relative_path
    if path.is_file() and digest == _sha256(path):
        return True
    try:
        revisions = subprocess.run(
            ["git", "rev-list", "--all", "--", relative_path],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        return False
    for revision in revisions:
        try:
            payload = subprocess.run(
                ["git", "show", f"{revision}:{relative_path}"],
                cwd=root,
                check=True,
                capture_output=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError):
            continue
        if hashlib.sha256(payload).hexdigest() == digest:
            return True
    return False


def validate_d18_core_contract(root: str | Path) -> list[str]:
    root = Path(root)
    errors: list[str] = []
    contract = _load_json(root / CONTRACT_PATH)
    top_level = _load_json(root / TOP_LEVEL_PATH)
    mapping = _load_json(root / MAPPING_PATH)

    for key, relative_path in (
        ("top_level_architecture", TOP_LEVEL_PATH),
        ("existing_system_mapping", MAPPING_PATH),
    ):
        binding = contract.get("basis", {}).get(key, {})
        if binding.get("path") != relative_path.as_posix() or binding.get("sha256") != _sha256(root / relative_path):
            errors.append(f"D-18 basis artifact drifted: {relative_path.as_posix()}")

    modules = contract.get("modules", [])
    modules_by_id = {row.get("id"): row for row in modules if isinstance(row, dict)}
    if set(modules_by_id) != MODULE_IDS or len(modules) != len(MODULE_IDS):
        errors.append("D-18 module set is incomplete")

    top_by_id = {row["id"]: row for row in top_level.get("modules", [])}
    interfaces = {
        row["target_module"]: row.get("required_interface", [])
        for row in mapping.get("target_module_mapping", [])
    }
    for module_id in MODULE_IDS & set(modules_by_id):
        row = modules_by_id[module_id]
        expected = top_by_id.get(module_id, {})
        if row.get("kind") != expected.get("kind"):
            errors.append(f"D-18 module kind drifted: {module_id}")
        if row.get("owns") != expected.get("owns"):
            errors.append(f"D-18 module ownership drifted: {module_id}")
        if row.get("must_not") != expected.get("must_not"):
            errors.append(f"D-18 module prohibition drifted: {module_id}")
        if row.get("interface") != interfaces.get(module_id):
            errors.append(f"D-18 module interface drifted: {module_id}")
        implementation = row.get("implementation", {})
        status = implementation.get("status")
        if status not in {"contract_only", "implemented_focused_verified"}:
            errors.append(f"D-18 implementation status is unsupported: {module_id}")
        module_path = str(implementation.get("module_path", "")).lower()
        if not module_path or any(name in module_path for name in FORBIDDEN_CASE_NAMES):
            errors.append(f"D-18 core module depends on a frozen case: {module_id}")
        if status == "implemented_focused_verified":
            test_path_value = implementation.get("verification_test")
            if (
                not _matches_live_or_reachable_git_object(
                    root, module_path, implementation.get("sha256")
                )
                or not _matches_live_or_reachable_git_object(
                    root,
                    str(test_path_value or ""),
                    implementation.get("verification_test_sha256"),
                )
            ):
                errors.append(f"D-18 implemented module drifted: {module_id}")

    if [row.get("id") for row in modules if row.get("kind") == "control_core"] != ["research_orchestrator"]:
        errors.append("D-18 control core boundary drifted")
    if [row.get("id") for row in modules if row.get("kind") == "truth_plane"] != ["project_record"]:
        errors.append("D-18 truth plane boundary drifted")

    rules = contract.get("dependency_rules", {})
    if rules.get("accepted_project_record_writers") != ["assurance_boundary"]:
        errors.append("Project Record accepted-writer boundary drifted")
    if rules.get("forbidden_direct_write_sources") != [
        "capability_gateway",
        "model_gateway",
        "optional_council_strategy",
    ]:
        errors.append("Project Record forbidden-writer boundary drifted")
    if rules.get("model_output_status") != "candidate_only":
        errors.append("Model output status drifted")
    if rules.get("tool_output_status") != "sourced_result_not_fact":
        errors.append("Tool output status drifted")
    edges = {
        (row.get("from"), row.get("to"), row.get("channel"))
        for row in rules.get("edges", [])
        if isinstance(row, dict)
    }
    if ("assurance_boundary", "project_record", "accepted_change") not in edges:
        errors.append("Assurance accepted-change edge is missing")
    for source in ("model_gateway", "capability_gateway", "optional_council_strategy"):
        if (source, "project_record", "accepted_change") in edges:
            errors.append(f"Forbidden direct Project Record edge: {source}")

    mapped_frozen = {
        row["cluster"]: (row.get("path_patterns"), row.get("disposition"))
        for row in mapping.get("asset_cluster_disposition", [])
    }
    bound_frozen = {
        row.get("cluster"): (row.get("path_patterns"), row.get("disposition"))
        for row in contract.get("frozen_assets", [])
        if isinstance(row, dict)
    }
    if bound_frozen != mapped_frozen:
        errors.append("D-18 frozen-asset disposition drifted")

    failures = contract.get("failure_semantics", [])
    if {row.get("layer") for row in failures if isinstance(row, dict)} != FAILURE_LAYERS:
        errors.append("D-18 failure layers are incomplete")
    for row in failures:
        if not all(row.get(key) for key in ("layer", "code", "result_status", "next_route")):
            errors.append("D-18 failure route is incomplete")
        if row.get("may_promote_candidate_to_fact") is not False:
            errors.append("D-18 failure route may promote a candidate to fact")

    if contract.get("next_route") != "implement-d18-lca-domain-kernel" or "next_routes" in contract:
        errors.append("D-18 core contract does not have exactly one next route")
    return errors


def validate_d18_p0_acceptance(root: str | Path) -> list[str]:
    root = Path(root)
    errors: list[str] = []
    acceptance = _load_json(root / P0_ACCEPTANCE_PATH)
    if acceptance.get("contract") != "lca_research_kernel.d18_p0_acceptance/v1" or acceptance.get("status") != "accepted":
        errors.append("D-18 P0 acceptance identity is unsupported")
    contract_ref = acceptance.get("core_contract_ref", {})
    contract_path = root / str(contract_ref.get("path", ""))
    if (
        contract_ref.get("path") != CONTRACT_PATH.as_posix()
        or not contract_path.is_file()
        or contract_ref.get("sha256") != _sha256(contract_path)
    ):
        errors.append("D-18 P0 core contract drifted")
    expected_modules = {"project_record", "model_gateway", "research_orchestrator"}
    modules = {
        row.get("id"): row
        for row in acceptance.get("verified_modules", [])
        if isinstance(row, dict)
    }
    if set(modules) != expected_modules:
        errors.append("D-18 P0 verified module set drifted")
    for module_id, row in modules.items():
        if (
            not _matches_live_or_reachable_git_object(
                root, str(row.get("path", "")), row.get("sha256")
            )
            or not _matches_live_or_reachable_git_object(
                root, str(row.get("test_path", "")), row.get("test_sha256")
            )
        ):
            errors.append(f"D-18 P0 module drifted: {module_id}")
    if set(acceptance.get("remaining_contract_only_modules") or []) != (
        MODULE_IDS - expected_modules
    ):
        errors.append("D-18 P0 remaining module boundary drifted")
    verification = acceptance.get("verification", {})
    full = verification.get("full_repository", {}) if isinstance(verification, dict) else {}
    convergence = verification.get("state_convergence", {}) if isinstance(verification, dict) else {}
    recovery = verification.get("project_recovery", {}) if isinstance(verification, dict) else {}
    if full != {"command": "make verify", "test_count": 1040, "status": "passed"}:
        errors.append("D-18 P0 full verification evidence drifted")
    if convergence.get("error_count") != 0 or convergence.get("status") != "passed":
        errors.append("D-18 P0 convergence evidence is absent")
    if recovery.get("status") != "passed":
        errors.append("D-18 P0 recovery evidence is absent")
    boundaries = acceptance.get("preserved_boundaries", {})
    if not isinstance(boundaries, dict) or any(
        boundaries.get(key) != expected
        for key, expected in {
            "project_record_only_accepted_truth": True,
            "model_output_candidate_only": True,
            "council_optional_not_imported": True,
            "case_specific_core_imports": 0,
            "remote_provider_calls": 0,
            "credential_reads": 0,
            "external_writes": 0,
            "forbidden_effects": 0,
        }.items()
    ):
        errors.append("D-18 P0 preserved boundary evidence drifted")
    if acceptance.get("next_route") != "implement-d18-lca-domain-kernel":
        errors.append("D-18 P0 next route drifted")
    return errors


def validate_d18_p1_contract(root: str | Path) -> list[str]:
    root = Path(root)
    errors: list[str] = []
    contract = _load_json(root / P1_CONTRACT_PATH)
    if contract.get("contract") != "lca_research_kernel.d18_p1_vertical_contract/v1":
        errors.append("D-18 P1 contract identity is unsupported")
    basis = contract.get("p0_acceptance_ref", {})
    basis_path = root / str(basis.get("path", ""))
    if (
        basis.get("path") != P0_ACCEPTANCE_PATH.as_posix()
        or not basis_path.is_file()
        or basis.get("sha256") != _sha256(basis_path)
    ):
        errors.append("D-18 P1 P0 basis drifted")
    expected = {
        "lca_domain_kernel",
        "assurance_boundary",
        "capability_gateway",
        "product_interface",
    }
    modules = {
        row.get("id"): row
        for row in contract.get("modules", [])
        if isinstance(row, dict)
    }
    if set(modules) != expected:
        errors.append("D-18 P1 module set drifted")
    for module_id, row in modules.items():
        status = row.get("status")
        if status not in {"contract_only", "implemented_focused_verified"}:
            errors.append(f"D-18 P1 module status drifted: {module_id}")
            continue
        if status == "implemented_focused_verified":
            if (
                not _matches_live_or_reachable_git_object(
                    root, str(row.get("path", "")), row.get("sha256")
                )
                or not _matches_live_or_reachable_git_object(
                    root, str(row.get("test_path", "")), row.get("test_sha256")
                )
            ):
                errors.append(f"D-18 P1 module drifted: {module_id}")
    if set(contract.get("fact_classes") or []) != {
        "official",
        "candidate",
        "assumption",
        "computed",
        "missing",
    }:
        errors.append("D-18 P1 fact classes drifted")
    invariants = contract.get("invariants", {})
    if invariants != {
        "candidate_silent_promotion": False,
        "missing_data_is_explicit": True,
        "cross_unit_aggregation": False,
        "case_specific_core_imports": 0,
        "tiangong_authority_override": False,
    }:
        errors.append("D-18 P1 invariants drifted")
    if contract.get("next_route") not in {
        "implement-d18-assurance-boundary",
        "implement-d18-capability-gateway",
        "implement-d18-product-interface",
        "implement-d18-generic-static-material-gap-closure",
        "integrate-and-verify-d18-p1",
    }:
        errors.append("D-18 P1 next route drifted")
    closure = contract.get("generic_static_material_gap_closure", {})
    if closure.get("status") not in {
        "contract_only",
        "implemented_focused_verified",
    } or closure.get("must_not_import_paused_static_material_builder") is not True:
        errors.append("D-18 P1 generic gap closure boundary drifted")
    if closure.get("status") == "implemented_focused_verified":
        for path_key, hash_key in (
            ("path", "sha256"),
            ("test_path", "test_sha256"),
        ):
            if not _matches_live_or_reachable_git_object(
                root,
                str(closure.get(path_key, "")),
                closure.get(hash_key),
            ):
                errors.append("D-18 P1 generic gap closure drifted")
                break
    adapted = contract.get("adapted_p0_modules") or []
    if len(adapted) != 1 or adapted[0].get("id") != "research_orchestrator":
        errors.append("D-18 P1 adapted P0 module ledger drifted")
    else:
        row = adapted[0]
        if (
            not _matches_live_or_reachable_git_object(
                root, str(row.get("path", "")), row.get("sha256")
            )
            or not _matches_live_or_reachable_git_object(
                root, str(row.get("test_path", "")), row.get("test_sha256")
            )
        ):
            errors.append("D-18 P1 adapted P0 module drifted: research_orchestrator")
    return errors


def validate_d18_p1_acceptance(root: str | Path) -> list[str]:
    root = Path(root)
    errors: list[str] = []
    acceptance = _load_json(root / P1_ACCEPTANCE_PATH)
    if acceptance.get("contract") != "lca_research_kernel.d18_p1_acceptance/v1" or acceptance.get("status") != "accepted":
        errors.append("D-18 P1 acceptance identity is unsupported")
    for key, expected_path, message in (
        ("p0_acceptance_ref", P0_ACCEPTANCE_PATH, "D-18 P1 acceptance P0 basis drifted"),
        ("p1_contract_ref", P1_CONTRACT_PATH, "D-18 P1 acceptance contract basis drifted"),
    ):
        reference = acceptance.get(key, {})
        path = root / str(reference.get("path", ""))
        if (
            reference.get("path") != expected_path.as_posix()
            or not path.is_file()
            or reference.get("sha256") != _sha256(path)
        ):
            errors.append(message)
    expected_ids = {
        "lca_domain_kernel",
        "assurance_boundary",
        "capability_gateway",
        "product_interface",
        "material_gap_closure",
        "research_orchestrator_p1_adapter",
    }
    implementations = {
        row.get("id"): row
        for row in acceptance.get("verified_implementations", [])
        if isinstance(row, dict)
    }
    if set(implementations) != expected_ids:
        errors.append("D-18 P1 accepted implementation set drifted")
    for implementation_id, row in implementations.items():
        if (
            not _matches_live_or_reachable_git_object(
                root, str(row.get("path", "")), row.get("sha256")
            )
            or not _matches_live_or_reachable_git_object(
                root, str(row.get("test_path", "")), row.get("test_sha256")
            )
        ):
            errors.append(
                f"D-18 P1 accepted implementation drifted: {implementation_id}"
            )
    full = acceptance.get("verification", {}).get("full_repository", {})
    if full != {"command": "make verify", "test_count": 1078, "status": "passed"}:
        errors.append("D-18 P1 full verification evidence drifted")
    effects = acceptance.get("effects", {})
    if not isinstance(effects, dict) or any(value != 0 for value in effects.values()):
        errors.append("D-18 P1 forbidden effect evidence drifted")
    if acceptance.get("next_route") != "build-d18-evaluation-fixture-harness":
        errors.append("D-18 P1 next route drifted")
    return errors


def validate_d18_p2_contract(root: str | Path) -> list[str]:
    root = Path(root)
    errors: list[str] = []
    try:
        contract = _load_json(root / P2_CONTRACT_PATH)
    except (OSError, json.JSONDecodeError):
        return ["D-18 P2 evaluation contract is unreadable"]
    if contract.get("contract") != "lca_research_kernel.d18_p2_evaluation_contract/v1":
        errors.append("D-18 P2 contract identity drifted")
    for key, expected_path, message in (
        ("p1_acceptance_ref", P1_ACCEPTANCE_PATH, "D-18 P2 P1 basis drifted"),
        ("fixture_registry_ref", FIXTURE_REGISTRY_PATH, "D-18 P2 fixture registry drifted"),
    ):
        reference = contract.get(key, {})
        path = root / str(reference.get("path", ""))
        if (
            reference.get("path") != expected_path.as_posix()
            or not path.is_file()
            or reference.get("sha256") != _sha256(path)
        ):
            errors.append(message)
    expected_ids = {
        "system_acceptance_harness",
        "bounded_model_failure_adapter",
        "bounded_product_failure_view",
    }
    implementations = {
        row.get("id"): row
        for row in contract.get("verified_implementations", [])
        if isinstance(row, dict)
    }
    if set(implementations) != expected_ids:
        errors.append("D-18 P2 implementation set drifted")
    for implementation_id, row in implementations.items():
        if (
            not _matches_live_or_reachable_git_object(
                root, str(row.get("path", "")), row.get("sha256")
            )
            or not _matches_live_or_reachable_git_object(
                root, str(row.get("test_path", "")), row.get("test_sha256")
            )
        ):
            errors.append(f"D-18 P2 implementation drifted: {implementation_id}")
    if set(contract.get("required_gates") or []) != P2_GATES:
        errors.append("D-18 P2 required gate set drifted")
    if contract.get("invariants") != {
        "case_architecture_influence_count": 0,
        "accepted_project_record_writers": ["assurance_boundary"],
        "credential_reads": 0,
        "external_writes": 0,
        "irreversible_actions": 0,
        "payments": 0,
        "publication_actions": 0,
    }:
        errors.append("D-18 P2 invariants drifted")
    if contract.get("next_route") != "integrate-and-finalize-d18-agent":
        errors.append("D-18 P2 next route drifted")
    return errors


def validate_d18_final_integration_contract(root: str | Path) -> list[str]:
    root = Path(root)
    errors: list[str] = []
    try:
        contract = _load_json(root / FINAL_INTEGRATION_PATH)
    except (OSError, json.JSONDecodeError):
        return ["D-18 final integration contract is unreadable"]
    if contract.get("contract") != "lca_research_kernel.d18_final_integration_contract/v1":
        errors.append("D-18 final integration contract identity drifted")
    basis = contract.get("p2_contract_ref", {})
    basis_path = root / str(basis.get("path", ""))
    if (
        basis.get("path") != P2_CONTRACT_PATH.as_posix()
        or not basis_path.is_file()
        or basis.get("sha256") != _sha256(basis_path)
    ):
        errors.append("D-18 final integration P2 basis drifted")
    if set(contract.get("integrated_connections") or []) != {"c07", "c08", "c09"}:
        errors.append("D-18 final integration connection set drifted")
    expected_ids = {
        "research_orchestrator",
        "product_interface",
        "capability_gateway",
        "system_acceptance_harness",
    }
    implementations = {
        row.get("id"): row
        for row in contract.get("verified_implementations", [])
        if isinstance(row, dict)
    }
    if set(implementations) != expected_ids:
        errors.append("D-18 final integration implementation set drifted")
    for implementation_id, row in implementations.items():
        if (
            not _matches_live_or_reachable_git_object(
                root, str(row.get("path", "")), row.get("sha256")
            )
            or not _matches_live_or_reachable_git_object(
                root, str(row.get("test_path", "")), row.get("test_sha256")
            )
        ):
            errors.append(
                f"D-18 final integration implementation drifted: {implementation_id}"
            )
    if contract.get("invariants") != {
        "caller_capability_selection_count": 0,
        "model_output_status": "candidate_only",
        "tool_output_status": "sourced_result_not_fact",
        "accepted_project_record_writers": ["assurance_boundary"],
        "unapproved_external_effects": 0,
    }:
        errors.append("D-18 final integration invariants drifted")
    if contract.get("next_route") != "run-d18-final-acceptance":
        errors.append("D-18 final integration next route drifted")
    return errors


def validate_d18_final_acceptance(root: str | Path) -> list[str]:
    root = Path(root)
    errors: list[str] = []
    try:
        acceptance = _load_json(root / FINAL_ACCEPTANCE_PATH)
    except (OSError, json.JSONDecodeError):
        return ["D-18 final acceptance is unreadable"]
    if (
        acceptance.get("contract") != "lca_research_kernel.d18_final_acceptance/v1"
        or acceptance.get("status") != "accepted"
        or acceptance.get("formal_status")
        != "d18_operational_foundation_accepted"
    ):
        errors.append("D-18 final acceptance identity drifted")
    expected_basis = {
        TOP_LEVEL_PATH.as_posix(),
        MAPPING_PATH.as_posix(),
        P0_ACCEPTANCE_PATH.as_posix(),
        P1_ACCEPTANCE_PATH.as_posix(),
        FIXTURE_REGISTRY_PATH.as_posix(),
        P2_CONTRACT_PATH.as_posix(),
        FINAL_INTEGRATION_PATH.as_posix(),
    }
    refs = acceptance.get("basis_refs")
    if not isinstance(refs, list) or {
        row.get("path") for row in refs if isinstance(row, dict)
    } != expected_basis:
        errors.append("D-18 final acceptance basis set drifted")
        refs = []
    for reference in refs:
        path = root / str(reference.get("path", ""))
        if not path.is_file() or reference.get("sha256") != _sha256(path):
            errors.append("D-18 final acceptance basis drifted")
            break
    summary = acceptance.get("human_summary_ref", {})
    summary_path = root / str(summary.get("path", ""))
    if (
        summary.get("path") != "docs/lca-agent-d18-acceptance.zh-CN.md"
        or not summary_path.is_file()
        or summary.get("sha256") != _sha256(summary_path)
    ):
        errors.append("D-18 final acceptance human summary drifted")
    verification = acceptance.get("verification", {})
    if verification.get("full_repository") != {
        "command": "make verify",
        "test_count": 1105,
        "status": "passed",
    }:
        errors.append("D-18 final full verification evidence drifted")
    clean = verification.get("clean_context", {})
    if clean != {
        "git_commit": "4d025639723476aaa44e79b753a775792cb57c7c",
        "test_count": 103,
        "network_denied": True,
        "state_convergence_error_count": 0,
        "p1_count": 0,
        "p2_count": 0,
        "status": "passed",
    }:
        errors.append("D-18 final clean-context evidence drifted")
    security = verification.get("security_scan", {})
    if (
        security.get("scanned_file_count") != 71
        or security.get("secret_value_match_count") != 0
        or security.get("credential_assignment_match_count") != 0
        or security.get("online_identity_match_count") != 0
        or security.get("status") != "passed"
    ):
        errors.append("D-18 final security scan evidence drifted")
    if acceptance.get("product_truth") != {
        "sole_truth": "Project Record",
        "accepted_writer": "assurance_boundary",
        "model_status": "candidate_only",
        "tool_status": "sourced_result_not_fact",
    }:
        errors.append("D-18 final Project Record boundary drifted")
    effects = acceptance.get("effects", {})
    if not isinstance(effects, dict) or any(value != 0 for value in effects.values()):
        errors.append("D-18 final forbidden effect evidence drifted")
    unsupported = set(acceptance.get("unsupported_claims") or [])
    if not {
        "D18_supports_every_LCA_workflow",
        "remote_model_execution_is_verified",
        "Council_is_superior",
        "legacy_case_success_proves_general_scientific_correctness",
    }.issubset(unsupported):
        errors.append("D-18 final claim ceiling drifted")
    if acceptance.get("no_remaining_required_work") is not True:
        errors.append("D-18 final no-remaining-work audit is absent")
    if acceptance.get("next_route") != "run-first-d18-native-lca-research-vertical":
        errors.append("D-18 final next route drifted")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the D-18 core architecture contract")
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    errors = validate_d18_core_contract(args.root)
    if errors:
        print(json.dumps({"error_count": len(errors), "errors": errors, "status": "failed"}, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps({"error_count": 0, "status": "passed"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
