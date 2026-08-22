"""Freeze and execute one deterministic real-question regression Epoch."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Mapping

from .evidence_dag import build_layer_lock, canonical_sha256, verify_layer_lock
from .real_question_pack import (
    build_blind_run_manifest,
    validate_question_package_tree,
)
from .staged_real_question_regression import (
    PDF_SHA256,
    build_regression_epoch,
    decide_regression_continuation,
    select_regression_tranches,
)


QUESTION_PACK_SHA256 = "84d6a671c89e34a807818a3662bfec1643e41ff153689ea466dd29a0fb81a3e1"
QUESTION_SOURCE = Path(
    "outputs/real-question-optimization/73101eec6999b27f/question-pack"
)
PACKAGE_ROOT = Path(
    "outputs/real-question-optimization/73101eec6999b27f/"
    "blind-execution-v4/package"
)
PROVIDER_ROOT = Path(
    "outputs/real-question-optimization/73101eec6999b27f/"
    "staged-regression-v1/provider-preflight"
)
QUALIFICATION_PATH = PROVIDER_ROOT / "gemini-capacity-qualification.json"
PROBE_RECEIPT_PATH = PROVIDER_ROOT / "gemini-structured-probe-receipt.json"
PROBE_TRACE_PATH = PROVIDER_ROOT / "gemini-probe-run-trace.jsonl"
EXECUTION_CLOSURE_PATHS = [
    "agent/lca-project-controller.md",
    "lca_research_evaluation/real_question_blind_runner.py",
    "lca_research_evaluation/real_question_pack.py",
    "lca_research_evaluation/staged_real_question_execution.py",
    "lca_research_evaluation/staged_real_question_regression.py",
    "lca_research_kernel/agent_bootstrap.py",
    "lca_research_kernel/answer_timing.py",
    "lca_research_kernel/assurance.py",
    "lca_research_kernel/capability_gateway.py",
    "lca_research_kernel/claim_semantics.py",
    "lca_research_kernel/context_assembler.py",
    "lca_research_kernel/execution_environment.py",
    "lca_research_kernel/lca_domain_kernel.py",
    "lca_research_kernel/model_credentials.py",
    "lca_research_kernel/model_gateway.py",
    "lca_research_kernel/model_provider_adapters.py",
    "lca_research_kernel/model_runtime.py",
    "lca_research_kernel/official_public_source.py",
    "lca_research_kernel/product_interface.py",
    "lca_research_kernel/project.py",
    "lca_research_kernel/research_orchestrator.py",
    "lca_research_kernel/run_control.py",
    "lca_research_kernel/run_trace.py",
    "lca_research_kernel/contracts/agent-startup-manifest-v2.json",
    "lca_research_kernel/contracts/model-provider-catalog.json",
    "lca_research_kernel/contracts/model-runtime-policy.json",
    "lca_research_kernel/contracts/official-public-source-index.json",
    "lca_research_kernel/contracts/project-execution-policy.json",
    "lca_research_kernel/contracts/tiangong-capability-catalog.json",
    "lca_research_kernel/contracts/tiangong-source-lock.json",
    "runtime/model-python/requirements.lock",
    "runtime/model-python/source-lock.json",
    "lca_research_service/runtime.py",
    "lca_research_service/runtime_factory.py",
    "scripts/run_real_question_blind.py",
    "scripts/run_staged_real_question_regression.py",
    "skills/run-lca-research-project/SKILL.md",
]
STOP_GATE_PATH = Path("regression-stop.json")


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unreadable") from error
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return deepcopy(dict(value))


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if path.is_file():
        if path.read_bytes() != content:
            raise ValueError(f"frozen Epoch artifact drifted: {path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise ValueError("staged regression artifact escaped the repository") from error


def _ref(root: Path, path: Path) -> dict:
    return {"path": _relative(root, path), "sha256": _file_sha256(path)}


def _head(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def build_staged_regression_stop_gate(
    *,
    epoch: Mapping[str, object],
    failure_observations: list[Mapping[str, object]],
    diagnosis_path: Path,
    epoch_root: Path,
    next_route: str,
) -> dict:
    """Bind a systemic stop to its exact Epoch and diagnosis evidence."""

    epoch_root = Path(epoch_root).resolve()
    diagnosis_path = Path(diagnosis_path)
    try:
        relative = diagnosis_path.resolve().relative_to(epoch_root).as_posix()
    except ValueError as error:
        raise ValueError("stop diagnosis escaped its Epoch") from error
    if (
        diagnosis_path.is_symlink()
        or not diagnosis_path.is_file()
        or not isinstance(next_route, str)
        or not next_route.strip()
    ):
        raise ValueError("stop diagnosis or recovery route is invalid")
    decision = decide_regression_continuation(
        epoch=epoch,
        completed_rows=[],
        failure_observations=failure_observations,
    )
    if decision["decision"] not in {
        "stop_hard_veto",
        "stop_systemic_failure",
        "stop_unknown_failure",
    } or decision["provider_calls_authorized"] != 0:
        raise ValueError("failure history does not authorize a stop gate")
    gate = {
        "contract": "staged_regression_stop_gate/v1",
        "epoch_sha256": epoch.get("epoch_sha256"),
        "decision": decision["decision"],
        "reason": decision["reason"],
        "repeated_failure_component": decision["repeated_failure_component"],
        "provider_calls_authorized": 0,
        "failure_observations": [dict(row) for row in failure_observations],
        "diagnosis_ref": {
            "path": relative,
            "sha256": _file_sha256(diagnosis_path),
        },
        "next_route": next_route.strip(),
    }
    gate["stop_gate_sha256"] = canonical_sha256(gate)
    return gate


def _validate_staged_regression_stop_gate(
    *, epoch_root: Path, epoch: Mapping[str, object], gate: Mapping[str, object]
) -> dict:
    expected = {
        "contract",
        "epoch_sha256",
        "decision",
        "reason",
        "repeated_failure_component",
        "provider_calls_authorized",
        "failure_observations",
        "diagnosis_ref",
        "next_route",
        "stop_gate_sha256",
    }
    normalized = deepcopy(dict(gate))
    stored = normalized.pop("stop_gate_sha256", None)
    diagnosis_ref = gate.get("diagnosis_ref")
    if (
        set(gate) != expected
        or gate.get("contract") != "staged_regression_stop_gate/v1"
        or gate.get("epoch_sha256") != epoch.get("epoch_sha256")
        or gate.get("provider_calls_authorized") != 0
        or stored != canonical_sha256(normalized)
        or not isinstance(gate.get("next_route"), str)
        or not str(gate.get("next_route")).strip()
        or not isinstance(diagnosis_ref, Mapping)
        or set(diagnosis_ref) != {"path", "sha256"}
        or not isinstance(diagnosis_ref.get("sha256"), str)
        or len(str(diagnosis_ref.get("sha256"))) != 64
        or any(
            character not in "0123456789abcdef"
            for character in str(diagnosis_ref.get("sha256"))
        )
    ):
        raise ValueError("staged regression stop gate drifted")
    relative = diagnosis_ref.get("path")
    if (
        not isinstance(relative, str)
        or not relative
        or "\\" in relative
        or Path(relative).is_absolute()
        or ".." in Path(relative).parts
    ):
        raise ValueError("staged regression stop diagnosis path drifted")
    diagnosis_path = Path(epoch_root).resolve() / relative
    if (
        diagnosis_path.is_symlink()
        or not diagnosis_path.is_file()
        or _file_sha256(diagnosis_path) != diagnosis_ref.get("sha256")
    ):
        raise ValueError("staged regression stop diagnosis drifted")
    observations = gate.get("failure_observations")
    if not isinstance(observations, list):
        raise ValueError("staged regression stop history drifted")
    decision = decide_regression_continuation(
        epoch=epoch,
        completed_rows=[],
        failure_observations=observations,
    )
    for field in (
        "decision",
        "reason",
        "repeated_failure_component",
        "provider_calls_authorized",
    ):
        if gate.get(field) != decision.get(field):
            raise ValueError("staged regression stop decision drifted")
    normalized["stop_gate_sha256"] = stored
    return normalized


def _enforce_staged_regression_stop_gate(
    *, epoch_root: Path, epoch: Mapping[str, object]
) -> None:
    path = Path(epoch_root) / STOP_GATE_PATH
    if not path.exists():
        return
    if path.is_symlink() or not path.is_file():
        raise ValueError("staged regression stop gate drifted")
    gate = _validate_staged_regression_stop_gate(
        epoch_root=epoch_root,
        epoch=epoch,
        gate=_load_object(path, "staged regression stop gate"),
    )
    raise ValueError(
        "staged regression is mechanically stopped; resume only through "
        + str(gate["next_route"])
    )


def build_staged_product_run_budget(
    answer_budget: Mapping[str, object],
) -> dict:
    """Bind the frozen answer budget to Product Interface run control."""

    expected = {
        "contract",
        "candidate_preview_seconds",
        "formal_answer_target_seconds",
        "hard_stop_seconds",
        "max_external_reads_per_question",
        "max_model_calls_per_question",
        "max_context_tokens_per_model_call",
        "max_steps_per_question",
    }
    if (
        not isinstance(answer_budget, Mapping)
        or set(answer_budget) != expected
        or answer_budget.get("contract")
        != "staged_regression_answer_budget/v1"
    ):
        raise ValueError("staged answer budget is invalid")
    for field in expected - {"contract"}:
        if (
            not isinstance(answer_budget[field], int)
            or isinstance(answer_budget[field], bool)
            or answer_budget[field] < 1
        ):
            raise ValueError(f"staged answer budget {field} is invalid")
    return {
        "contract": "lca_research_kernel.run_budget/v1",
        "max_elapsed_ms": int(answer_budget["hard_stop_seconds"]) * 1000,
        "max_steps": int(answer_budget["max_steps_per_question"]),
        "max_model_calls": int(answer_budget["max_model_calls_per_question"]),
        "max_tool_calls": int(answer_budget["max_external_reads_per_question"]),
        "max_context_tokens": int(
            answer_budget["max_context_tokens_per_model_call"]
        ),
        "max_repeated_fingerprint": 2,
    }


def freeze_staged_regression_epoch(
    *, root: Path, output_root: Path, accepted_commit: str, run_started_at: str
) -> dict:
    """Freeze execution, environment, provider and source inputs before Q1."""

    root = Path(root).resolve()
    output_root = Path(output_root).resolve()
    _relative(root, output_root)
    if _head(root) != accepted_commit:
        raise ValueError("accepted code identity is not current HEAD")
    for required in (QUALIFICATION_PATH, PROBE_RECEIPT_PATH, PROBE_TRACE_PATH):
        if not (root / required).is_file():
            raise ValueError(f"provider preflight artifact is absent: {required}")
    probe = _load_object(root / PROBE_RECEIPT_PATH, "provider probe")
    qualification = _load_object(
        root / QUALIFICATION_PATH, "provider capacity qualification"
    )
    if (
        probe.get("status") != "passed"
        or probe.get("provider") != "gemini"
        or probe.get("actual_model") != "gemini-3.6-flash"
        or probe.get("tool_requests") != 0
        or probe.get("external_writes") != 0
        or probe.get("scientific_result_created") is not False
        or qualification.get("status") != "qualified"
        or qualification.get("provider_id") != "gemini"
    ):
        raise ValueError("Gemini provider preflight is not qualified")
    output_root.mkdir(parents=True, exist_ok=True)
    budget = {
        "contract": "staged_regression_answer_budget/v1",
        "candidate_preview_seconds": 90,
        "formal_answer_target_seconds": 480,
        "hard_stop_seconds": 720,
        "max_external_reads_per_question": 6,
        "max_model_calls_per_question": 4,
        "max_context_tokens_per_model_call": 65536,
        "max_steps_per_question": 24,
    }
    runtime_settings = {
        "contract": "staged_regression_runtime_settings/v1",
        "provider_id": "gemini",
        "actual_model": probe["actual_model"],
        "provider_catalog_sha256": probe["provider_catalog_sha256"],
        "source_lock_sha256": probe["source_lock_sha256"],
        "capacity_qualification_receipt_sha256": qualification[
            "receipt_sha256"
        ],
        "run_id": "d18-staged-real-question-regression-v1",
        "run_started_at": run_started_at,
        "provider_fallback_role": "failure_only_not_comparison",
    }
    official_index = root / "lca_research_kernel/contracts/official-public-source-index.json"
    repair_acceptance = root / "outputs/real-question-optimization/adaptive-answer-repair/acceptance-v3.json"
    official_snapshot = {
        "contract": "staged_regression_official_source_policy_snapshot/v1",
        "official_source_index": _ref(root, official_index),
        "accepted_source_binding_repair": _ref(root, repair_acceptance),
        "correctness_authority": "official_evidence",
        "external_read_limit_per_question": 6,
    }
    budget_path = output_root / "answer-budget.json"
    settings_path = output_root / "runtime-settings.json"
    source_path = output_root / "official-source-policy-snapshot.json"
    _atomic_json(budget_path, budget)
    _atomic_json(settings_path, runtime_settings)
    _atomic_json(source_path, official_snapshot)
    dynamic_paths = [
        _relative(root, budget_path),
        _relative(root, settings_path),
        _relative(root, source_path),
        QUALIFICATION_PATH.as_posix(),
        PROBE_RECEIPT_PATH.as_posix(),
        PROBE_TRACE_PATH.as_posix(),
    ]
    execution_lock = build_layer_lock(
        root,
        layer="execution",
        paths=[*EXECUTION_CLOSURE_PATHS, *dynamic_paths],
        basis={
            "accepted_commit": accepted_commit,
            "provider_id": "gemini",
            "actual_model": probe["actual_model"],
            "pdf_sha256": PDF_SHA256,
            "question_pack_sha256": QUESTION_PACK_SHA256,
        },
        evidence={
            "provider_probe_receipt_sha256": probe["receipt_sha256"],
            "capacity_qualification_sha256": qualification["receipt_sha256"],
        },
    )
    execution_lock_path = output_root / "execution-lock.json"
    _atomic_json(execution_lock_path, execution_lock)
    epoch = build_regression_epoch(
        accepted_commit=accepted_commit,
        pdf_sha256=PDF_SHA256,
        question_pack_sha256=QUESTION_PACK_SHA256,
        execution_lock_refs=[_ref(root, execution_lock_path)],
    )
    plan = select_regression_tranches(epoch)
    _atomic_json(output_root / "epoch.json", epoch)
    _atomic_json(output_root / "tranche-plan.json", plan)
    verification = verify_layer_lock(root, execution_lock)
    if verification.get("status") != "valid":
        raise ValueError("staged regression execution lock failed verification")
    return {
        "contract": "staged_regression_freeze_result/v1",
        "epoch": epoch,
        "plan": plan,
        "execution_lock": execution_lock,
        "execution_verification": verification,
    }


def build_staged_blind_manifest(
    *,
    root: Path,
    frozen: Mapping[str, object],
    execution_environment: object,
    question_root: Path,
    question_authorization: Mapping[str, object],
) -> dict:
    """Compile only question-visible identities; sealed answers never enter."""

    root = Path(root).resolve()
    _relative(root, Path(question_root))
    epoch = frozen.get("epoch")
    lock = frozen.get("execution_lock")
    if not isinstance(epoch, Mapping) or not isinstance(lock, Mapping):
        raise ValueError("frozen staged regression inputs are invalid")
    verification = verify_layer_lock(root, lock)
    if verification.get("status") != "valid":
        raise ValueError("execution lock drifted before manifest construction")
    question_tree = validate_question_package_tree(Path(question_root))
    if question_tree["question_pack_sha256"] != QUESTION_PACK_SHA256:
        raise ValueError("question package drifted before manifest construction")
    settings_row = next(
        row for row in lock["inputs"] if row["path"].endswith("runtime-settings.json")
    )
    budget_row = next(
        row for row in lock["inputs"] if row["path"].endswith("answer-budget.json")
    )
    source_row = next(
        row
        for row in lock["inputs"]
        if row["path"].endswith("official-source-policy-snapshot.json")
    )
    settings = _load_object(root / settings_row["path"], "runtime settings")
    return build_blind_run_manifest(
        question_pack_sha256=question_tree["question_pack_sha256"],
        question_tree_sha256=question_tree["tree_sha256"],
        code_sha256=lock["lock_sha256"],
        model_id=settings["actual_model"],
        model_settings_sha256=settings_row["sha256"],
        budget_sha256=budget_row["sha256"],
        official_source_snapshot_sha256=source_row["sha256"],
        execution_identity=execution_environment.identity,
        question_read_authorization_receipt=question_authorization,
    )


def prepare_staged_execution(
    *, root: Path, epoch_root: Path
) -> dict:
    """Materialize a least-authority question-only execution environment."""

    from lca_research_kernel.execution_environment import (
        ProjectExecutionEnvironment,
    )

    root = Path(root).resolve()
    epoch_root = Path(epoch_root).resolve()
    _relative(root, epoch_root)
    epoch = _load_object(epoch_root / "epoch.json", "staged regression Epoch")
    plan = _load_object(epoch_root / "tranche-plan.json", "staged tranche plan")
    execution_lock = _load_object(
        epoch_root / "execution-lock.json", "staged execution lock"
    )
    verification = verify_layer_lock(root, execution_lock)
    if verification.get("status") != "valid":
        raise ValueError("staged execution lock drifted")
    execution_ref = {
        "path": _relative(root, epoch_root / "execution-lock.json"),
        "sha256": _file_sha256(epoch_root / "execution-lock.json"),
    }
    if (
        epoch.get("execution_lock_refs") != [execution_ref]
        or plan.get("epoch_sha256") != epoch.get("epoch_sha256")
    ):
        raise ValueError("staged Epoch and execution lock binding drifted")
    policy = _load_object(
        root / "lca_research_kernel/contracts/project-execution-policy.json",
        "project execution policy",
    )
    environment = ProjectExecutionEnvironment.create(
        epoch_root / "projects",
        "staged-real-question-regression-v1",
        policy,
    )
    question_root = environment.project_root / "inputs" / "question-pack"
    if not question_root.exists():
        shutil.copytree(root / QUESTION_SOURCE, question_root)
    question_tree = validate_question_package_tree(question_root)
    if question_tree["question_pack_sha256"] != QUESTION_PACK_SHA256:
        raise ValueError("staged question package copy drifted")
    authorization = environment.authorize_file(
        question_root / "real-question-pack.json", "read"
    )
    frozen = {
        "epoch": epoch,
        "plan": plan,
        "execution_lock": execution_lock,
        "execution_verification": verification,
    }
    manifest = build_staged_blind_manifest(
        root=root,
        frozen=frozen,
        execution_environment=environment,
        question_root=question_root,
        question_authorization=authorization,
    )
    manifest_path = environment.project_root / "inputs" / "blind-run-manifest.json"
    _atomic_json(manifest_path, manifest)
    return {
        "contract": "staged_regression_execution_preparation/v1",
        "epoch": epoch,
        "plan": plan,
        "execution_lock": execution_lock,
        "execution_verification": verification,
        "environment": environment,
        "question_root": question_root,
        "question_authorization": authorization,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "output_root": environment.project_root / "artifacts" / "blind-runs",
    }


def execute_frozen_tranche(
    *,
    root: Path,
    epoch_root: Path,
    tranche_index: int,
    credential_env_file: Path,
    product_interface: object = None,
) -> dict:
    """Execute one controller-selected tranche through the production interface."""

    from lca_research_kernel.provider_capacity import (
        validate_provider_capacity_qualification,
    )
    from lca_research_service.runtime_factory import RuntimeFactory
    from scripts.probe_model_providers import load_credential_environment
    from scripts.run_real_question_blind import run_with_product_interface
    from scripts.run_staged_real_question_regression import execute_tranche

    root = Path(root).resolve()
    epoch_root = Path(epoch_root).resolve()
    frozen_epoch = _load_object(epoch_root / "epoch.json", "staged Epoch")
    _enforce_staged_regression_stop_gate(
        epoch_root=epoch_root,
        epoch=frozen_epoch,
    )
    prepared = prepare_staged_execution(root=root, epoch_root=epoch_root)
    settings = _load_object(epoch_root / "runtime-settings.json", "runtime settings")
    answer_budget = _load_object(epoch_root / "answer-budget.json", "answer budget")
    qualification = validate_provider_capacity_qualification(
        _load_object(root / QUALIFICATION_PATH, "provider capacity qualification")
    )
    fixed_clock = lambda: settings["run_started_at"]
    if product_interface is None:
        environment_values = load_credential_environment(
            Path(credential_env_file)
        )
        binding = RuntimeFactory(
            repository_root=root,
            data_root=epoch_root / "runtime-data",
            environ=environment_values,
            capacity_qualification=qualification,
            run_id=settings["run_id"],
            run_budget=build_staged_product_run_budget(answer_budget),
            clock=fixed_clock,
        ).build()
        if not binding.ready or binding.runtime is None:
            raise ValueError("staged Product Interface runtime is unavailable")
        product_interface = binding.runtime.product_interface

    package_root = root / PACKAGE_ROOT
    sealed_root = package_root / "sealed-reference"
    package_receipt = package_root / "package-build-receipt.json"
    extraction_ledger = package_root / "package-extraction-ledger.json"

    def run_questions(*, question_ids):
        return run_with_product_interface(
            product_interface,
            question_root=prepared["question_root"],
            blind_manifest=prepared["manifest"],
            execution_environment=prepared["environment"],
            question_read_authorization_receipt=prepared[
                "question_authorization"
            ],
            output_root=prepared["output_root"],
            sealed_root=sealed_root,
            sealed_identifiers=set(),
            official_source_cache_root=(
                epoch_root / "runtime-data/control/official-public-source-cache"
            ),
            sealed_package_receipt_path=package_receipt,
            accepted_package_receipt_sha256=_file_sha256(package_receipt),
            extraction_ledger_path=extraction_ledger,
            question_ids=question_ids,
            clock=fixed_clock,
        )

    return execute_tranche(
        epoch=prepared["epoch"],
        tranche_plan=prepared["plan"],
        tranche_index=tranche_index,
        output_root=prepared["output_root"],
        run_questions=run_questions,
    )


__all__ = [
    "build_staged_blind_manifest",
    "build_staged_product_run_budget",
    "build_staged_regression_stop_gate",
    "execute_frozen_tranche",
    "freeze_staged_regression_epoch",
    "prepare_staged_execution",
]
