#!/usr/bin/env python3
"""Run exactly one real, zero-effect Gemini five-turn quality acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_evaluation.research_intelligence_eval import (
    build_acceptance_receipt,
    grade_research_dialogue,
    resume_research_dialogue,
    run_research_dialogue,
)
from lca_research_kernel.assurance import AssuranceBoundary
from lca_research_kernel.model_runtime import build_production_model_gateway
from lca_research_kernel.product_interface import ProductInterface
from lca_research_kernel.project_record import canonical_sha256
from lca_research_kernel.provider_capacity import (
    validate_provider_capacity_qualification,
)
from lca_research_kernel.run_trace import RunTrace
from lca_research_service.runtime_factory import required_runtime_environment_names
from scripts.generate_research_intelligence_acceptance import (
    BASELINE_COMMIT,
    NEXT_ROUTE,
    _run_counterfactuals,
)
from scripts.run_frontend_real_pilot_stack import load_credential_environment
from tests.test_product_material_acceptance import (
    ProductMaterialAcceptanceTests,
    material_bundle,
)
from tests.test_research_intelligence_eval import (
    FIXTURE_PATH,
    NOW,
    _replace_bundle_evidence,
)


DEFAULT_QUALIFICATION = Path(
    "outputs/real-question-optimization/73101eec6999b27f/minimum-real-path/"
    "gemini-provider-capacity-q5-v2.json"
)
DEFAULT_RUN_ROOT = Path(
    "outputs/d18-research-intelligence/live-five-turn-quality-v2"
)


def _under_root(path: Path) -> Path:
    resolved = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError as error:
        raise ValueError("live acceptance path escaped repository root") from error
    return resolved


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("live acceptance input is invalid")
    return deepcopy(dict(value))


def _atomic_private_write(path: Path, value: Mapping[str, object]) -> None:
    content = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / ("." + path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _observed_models(record: object, run: Mapping[str, object]) -> tuple[list[str], list[str]]:
    workspace = getattr(record, "workspace", None)
    refs = run.get("run_trace_refs")
    if not isinstance(workspace, Path) or not isinstance(refs, list):
        raise ValueError("live model trace authority is unavailable")
    providers: set[str] = set()
    models: set[str] = set()
    for ref in refs:
        run_id = ref.get("run_id") if isinstance(ref, Mapping) else None
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("live model trace reference is invalid")
        events = RunTrace(
            workspace / "run-traces" / f"{run_id}.jsonl",
            run_id=run_id,
        ).events()
        for event in events:
            if event.get("event_type") != "research.run.model.completed":
                continue
            payload = event.get("payload")
            provider = payload.get("provider") if isinstance(payload, Mapping) else None
            model = payload.get("model") if isinstance(payload, Mapping) else None
            if not isinstance(provider, str) or not isinstance(model, str):
                raise ValueError("live model identity is absent")
            providers.add(provider)
            models.add(model)
    if providers != {"gemini"} or not models:
        raise ValueError("live acceptance used an unexpected model route")
    return sorted(providers), sorted(models)


def _existing_trace_prefixes(workspace: Path) -> dict[str, bytes]:
    prefixes: dict[str, bytes] = {}
    trace_directory = workspace / "run-traces"
    if not trace_directory.exists():
        return prefixes
    for path in sorted(trace_directory.glob("*.jsonl")):
        first_line = next(
            (line for line in path.read_text(encoding="utf-8").splitlines() if line),
            None,
        )
        if first_line is None:
            continue
        first = json.loads(first_line)
        run_id = first.get("run_id") if isinstance(first, Mapping) else None
        if not isinstance(run_id, str) or not run_id or run_id in prefixes:
            raise ValueError("existing live Run Trace identity is invalid")
        prefixes[run_id] = path.read_bytes()
    return prefixes


def _validate_commit(value: str | None, *, field: str) -> str:
    if not isinstance(value, str) or len(value) != 40:
        raise ValueError(f"{field} is invalid")
    checked = subprocess.run(
        ["git", "cat-file", "-e", f"{value}^{{commit}}"],
        cwd=ROOT,
        capture_output=True,
    )
    if checked.returncode != 0:
        raise ValueError(f"{field} is unavailable")
    return value


def _superseded_receipt_ref(path: Path) -> dict:
    value = _read_json(path)
    observed = value.pop("receipt_sha256", None)
    if not isinstance(observed, str) or canonical_sha256(value) != observed:
        raise ValueError("superseded receipt is not content addressed")
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "receipt_sha256": observed,
        "status": "rejected_before_lifecycle_acceptance",
    }


def run(args: argparse.Namespace) -> dict:
    run_root = _under_root(args.run_root)
    qualification_path = _under_root(args.capacity_qualification)
    if args.preflight_failures_before_network < 0:
        raise ValueError("preflight failure count is invalid")
    if args.process_attempts < 1 or (args.resume and args.process_attempts < 2):
        raise ValueError("live acceptance process attempt count is invalid")
    if not args.resume and args.process_attempts != 1:
        raise ValueError("new live acceptance must start with one process attempt")
    acceptance_path = run_root / "acceptance.json"
    superseded_receipts = [
        _superseded_receipt_ref(_under_root(path))
        for path in args.superseded_receipt
    ]
    if args.resume:
        if not run_root.is_dir() or acceptance_path.exists():
            raise ValueError("resumable live acceptance root is invalid")
        initial_execution_commit = _validate_commit(
            args.initial_execution_commit,
            field="initial execution commit",
        )
        prior_resume_commits = [
            _validate_commit(value, field="prior resume commit")
            for value in args.prior_resume_commit
        ]
    else:
        if run_root.exists():
            raise ValueError("live acceptance run root already exists")
        if args.initial_execution_commit is not None:
            raise ValueError("initial execution commit is only valid for resume")
        initial_execution_commit = None
        prior_resume_commits = []
    if subprocess.run(["git", "diff", "--quiet"], cwd=ROOT).returncode != 0 or subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=ROOT
    ).returncode != 0:
        raise ValueError("live acceptance requires a committed candidate")
    candidate_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    qualification = validate_provider_capacity_qualification(
        _read_json(qualification_path)
    )
    credentials = load_credential_environment(
        args.credential_env_file,
        required_runtime_environment_names(ROOT),
    )
    gateway = build_production_model_gateway(
        ROOT,
        credentials,
        execution_mode="verification_probe",
        provider_ids={"gemini"},
        clock=lambda: NOW,
    )
    fixture = _read_json(FIXTURE_PATH)
    bundle = material_bundle()
    bundle["research_material"]["scope"] = "project"
    bundle = _replace_bundle_evidence(
        bundle,
        content="flow=water; amount=2; unit=kg; check==1+1",
        semantic_roles=["flow", "amount", "unit", "validation_expression"],
        artifact_sha="a" * 64,
    )
    workspace = run_root / "execution/projects" / fixture["project"]["research_id"]
    existing_event_prefix = (
        (workspace / "research-project-events.jsonl").read_bytes()
        if args.resume
        else b""
    )
    existing_trace_prefixes = (
        _existing_trace_prefixes(workspace) if args.resume else {}
    )
    product = ProductInterface(
        run_root / "execution",
        model_gateway=gateway,
        assurance=AssuranceBoundary(),
        clock=lambda: NOW,
        run_id="live-five-turn-quality-v2",
        verified_content_resolver=(
            ProductMaterialAcceptanceTests._material_resolver([bundle])
        ),
    )
    run = (
        resume_research_dialogue(product, fixture, bundle)
        if args.resume
        else run_research_dialogue(product, fixture, bundle)
    )
    record = product._open_record(fixture["project"]["research_id"])
    if args.resume:
        if not record.event_path.read_bytes().startswith(existing_event_prefix):
            raise ValueError("resumed Project Record did not preserve its event prefix")
        current_trace_prefixes = _existing_trace_prefixes(record.workspace)
        if any(
            run_id not in current_trace_prefixes
            or not current_trace_prefixes[run_id].startswith(prefix)
            for run_id, prefix in existing_trace_prefixes.items()
        ):
            raise ValueError("resumed Run Trace did not preserve its event prefix")
    grade = grade_research_dialogue(run, project_record=record)
    receipt = build_acceptance_receipt(
        run,
        grade,
        baseline_commit=BASELINE_COMMIT,
        candidate_commit=candidate_commit,
        counterfactuals=_run_counterfactuals(),
        next_route=NEXT_ROUTE,
    )
    providers, models = _observed_models(record, run)
    receipt["evaluation_scope"]["live_model_execution"] = "passed"
    receipt["live_model_evidence"] = {
        "provider_ids": providers,
        "actual_models": models,
        "real_dialogue_runs": 1,
        "prior_capacity_qualification": {
            "path": str(qualification_path.relative_to(ROOT)),
            "sha256": hashlib.sha256(qualification_path.read_bytes()).hexdigest(),
        },
        "tool_effects": receipt["counts"]["tool_effect"],
        "external_effects": receipt["counts"]["external_effect"],
        "process_attempts": args.process_attempts,
        "preflight_failures_before_network": args.preflight_failures_before_network,
    }
    if args.resume:
        recovery = run.get("recovery")
        if not isinstance(recovery, Mapping):
            raise ValueError("resumed dialogue recovery evidence is absent")
        receipt["live_model_evidence"]["recovery"] = {
            "mode": recovery.get("mode"),
            "accepted_turns_before_resume": recovery.get(
                "accepted_turns_before_resume"
            ),
            "initial_execution_commit": initial_execution_commit,
            "intermediate_execution_commits": prior_resume_commits,
            "resumed_execution_commit": candidate_commit,
            "same_project_record_event_prefix": True,
            "preserved_run_trace_ids": sorted(existing_trace_prefixes),
            "same_run_trace_event_prefixes": True,
            "superseded_receipts": superseded_receipts,
        }
    receipt["limitations"] = [
        value
        for value in receipt["limitations"]
        if not value.startswith("Independent live-model")
    ] + [
        "One real Gemini five-turn path passed the Project Record-bound deterministic grader; independent model-graded quality and human LCA/non-expert review remain not evaluated."
    ]
    receipt["limitations"] = sorted(set(receipt["limitations"]))
    receipt.pop("receipt_sha256", None)
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    _atomic_private_write(acceptance_path, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credential-env-file", type=Path, required=True)
    parser.add_argument(
        "--capacity-qualification", type=Path, default=DEFAULT_QUALIFICATION
    )
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--initial-execution-commit")
    parser.add_argument("--prior-resume-commit", action="append", default=[])
    parser.add_argument("--process-attempts", type=int, default=1)
    parser.add_argument(
        "--superseded-receipt", type=Path, action="append", default=[]
    )
    parser.add_argument(
        "--preflight-failures-before-network", type=int, default=0
    )
    receipt = run(parser.parse_args())
    print(receipt["receipt_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
