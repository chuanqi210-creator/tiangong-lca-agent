"""Immutable contract loaders for the product-external Evaluation Lab."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Mapping


PROGRAM_CONTRACT = "lca_research_kernel.performance_parity_program/v1"
PROGRAM_PATH = Path(
    "lca_research_kernel/contracts/performance-parity-program-v1.json"
)
RUNNER_ARMS = [
    "raw_model_tool_loop",
    "openai_agents_reference",
    "d18_product_agent",
]
SUCCESS_ROUTE = "integrate-qingxiaoda-frontend-and-run-assisted-pilot"
EXACT_CODE_BASIS = "99c39c084efc18d4ef7930bf696be1dbcb4670a1"
EXACT_PLANNING_COMMIT = "ffce32249759302327e79c83f3a0653502461671"
EXACT_FROZEN_EXECUTION_COMMIT = "a0d6065df50a43e883190f2e0c9b0c8cdac9a3ca"
EXACT_COMPLEX_REVISION = "f055155c782ae315bd2c68e7fd332b10f4b68596"
EXACT_SPLITS = {"development": 18, "validation": 12, "final": 30}
EXACT_THRESHOLDS = {
    "tiangong_mechanical_correctness": 1.0,
    "deterministic_numerical_correctness": 1.0,
    "d18_final_completion_rate": 0.90,
    "per_family_completion_rate": 0.80,
    "mean_lca_score": 95,
    "completion_noninferiority_pp": -3,
    "median_latency_ratio_max": 1.20,
    "p90_latency_ratio_max": 1.30,
    "unnecessary_tool_ratio_max": 0.10,
    "recovery_success_rate": 1.0,
    "duplicate_effect_count": 0,
    "grader_disagreement_points_max": 10,
    "service_project_concurrency_min": 8,
    "cancel_ack_seconds_max": 2.0,
    "service_p95_overhead_seconds_max": 0.250,
}


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_object_sha256(root: Path, commit: str, relative: str) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(root), "show", f"{commit}:{relative}"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        return None
    return hashlib.sha256(completed.stdout).hexdigest()


def _validate_ref(
    root: Path,
    ref: object,
    *,
    immutable_commits: tuple[str, ...] = (),
) -> None:
    if not isinstance(ref, Mapping) or set(ref) != {"path", "sha256"}:
        raise ValueError("performance program basis reference is malformed")
    relative = ref.get("path")
    digest = ref.get("sha256")
    if (
        not isinstance(relative, str)
        or not isinstance(digest, str)
        or Path(relative).is_absolute()
        or ".." in Path(relative).parts
    ):
        raise ValueError("performance program basis reference is unsafe")
    path = root / relative
    if path.is_file() and _sha256(path) == digest:
        return
    if any(
        _git_object_sha256(root, commit, relative) == digest
        for commit in immutable_commits
    ):
        return
    raise ValueError(f"performance program basis reference drifted: {relative}")


def validate_performance_program_contract(
    root: Path,
    contract: Mapping[str, object],
) -> dict:
    """Fail closed on any scale, threshold, authority, or topology downgrade."""

    root = Path(root).resolve()
    if contract.get("contract") != PROGRAM_CONTRACT:
        raise ValueError("performance program contract identity is unsupported")
    if contract.get("accepted_code_basis") != EXACT_CODE_BASIS:
        raise ValueError("accepted code basis drifted")
    if contract.get("planning_artifact_commit") != EXACT_PLANNING_COMMIT:
        raise ValueError("planning artifact commit drifted")
    if contract.get("complex_revision") != EXACT_COMPLEX_REVISION:
        raise ValueError("Complex revision drifted")
    if contract.get("task_count") != 60:
        raise ValueError("performance program task count drifted")
    if contract.get("split_counts") != EXACT_SPLITS:
        raise ValueError("performance program split counts drifted")
    if contract.get("final_repetitions_per_arm") != 3:
        raise ValueError("final repetitions drifted")
    if contract.get("runner_arms") != RUNNER_ARMS:
        raise ValueError("runner arms drifted")
    if contract.get("final_run_count") != 270:
        raise ValueError("final run count drifted")
    if contract.get("success_next_route") != SUCCESS_ROUTE:
        raise ValueError("success route drifted")
    if contract.get("quality_thresholds_mutable") is not False:
        raise ValueError("quality thresholds must be immutable")
    for field in (
        "human_expert_review_required",
        "frontend_in_scope",
        "cloud_deployment_in_scope",
        "default_council",
        "product_route_may_use_task_identity",
    ):
        if contract.get(field) is not False:
            raise ValueError(f"performance program boundary drifted: {field}")
    if contract.get("research_truth_owner_count") != 1:
        raise ValueError("performance program introduced a second research truth")
    if contract.get("research_truth_owner") != "project_record":
        raise ValueError("Project Record is no longer the research truth owner")
    if contract.get("d18_module_count") != 7:
        raise ValueError("D-18 module count drifted")
    if contract.get("outer_packages") != {
        "evaluation_lab": "calls_product_ports_only",
        "service_control_plane": "operational_metadata_only",
    }:
        raise ValueError("outer package boundary drifted")
    families = contract.get("task_families")
    if (
        not isinstance(families, list)
        or len(families) != 6
        or len({row.get("family_id") for row in families if isinstance(row, Mapping)})
        != 6
        or any(
            not isinstance(row, Mapping)
            or row.get("task_count") != 10
            or row.get("development") != 3
            or row.get("validation") != 2
            or row.get("final") != 5
            for row in families
        )
    ):
        raise ValueError("performance program task families drifted")
    thresholds = contract.get("thresholds")
    if not isinstance(thresholds, Mapping) or dict(thresholds) != EXACT_THRESHOLDS:
        raise ValueError("frozen acceptance thresholds drifted")
    equality = contract.get("runner_equality_fields")
    if not isinstance(equality, list) or set(equality) != {
        "model_id",
        "model_settings",
        "materials",
        "tool_descriptors",
        "permission_policy",
        "budgets",
        "timing_boundary",
        "oracle_isolation",
    }:
        raise ValueError("runner equality contract drifted")
    if contract.get("veto_tolerance") != 0:
        raise ValueError("hard veto tolerance drifted")
    vetoes = contract.get("hard_vetoes")
    if not isinstance(vetoes, list) or len(vetoes) < 12:
        raise ValueError("hard veto surface was reduced")
    if contract.get("final_invalidation_policy") != {
        "product_or_contract_drift": "invalidate_entire_final_tranche",
        "failed_valid_run": "must_be_retained",
        "threshold_reduction": "forbidden",
        "fixture_replacement": "invalidate_and_reselect",
        "cross_version_row_splicing": "forbidden",
    }:
        raise ValueError("final invalidation policy drifted")
    dependencies = contract.get("product_runtime_dependencies")
    if not isinstance(dependencies, list) or "openai_agents_reference" in dependencies:
        raise ValueError("reference SDK entered the product runtime")
    refs = contract.get("basis_refs")
    if not isinstance(refs, list) or len(refs) < 10:
        raise ValueError("performance program basis is incomplete")
    for ref in refs:
        _validate_ref(
            root,
            ref,
            immutable_commits=(
                EXACT_CODE_BASIS,
                EXACT_PLANNING_COMMIT,
                EXACT_FROZEN_EXECUTION_COMMIT,
            ),
        )
    stored_sha = contract.get("program_sha256")
    if not isinstance(stored_sha, str):
        raise ValueError("performance program self hash is absent")
    body = dict(contract)
    body.pop("program_sha256", None)
    if _canonical_sha256(body) != stored_sha:
        raise ValueError("performance program self hash drifted")
    return dict(contract)


def load_performance_program_contract(
    root: Path,
    path: Path | None = None,
) -> dict:
    root = Path(root).resolve()
    contract_path = Path(path) if path is not None else root / PROGRAM_PATH
    if not contract_path.is_absolute():
        contract_path = root / contract_path
    value = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("performance program contract must be an object")
    return validate_performance_program_contract(root, value)


__all__ = [
    "PROGRAM_CONTRACT",
    "PROGRAM_PATH",
    "RUNNER_ARMS",
    "SUCCESS_ROUTE",
    "load_performance_program_contract",
    "validate_performance_program_contract",
]
