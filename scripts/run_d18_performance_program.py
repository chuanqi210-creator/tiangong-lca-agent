#!/usr/bin/env python3
"""Run preserved, equal-input Evaluation Lab arms on open benchmark splits."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_evaluation.d18_runner import D18ProductRuntime
from lca_research_evaluation.counterfactual import counterfactual_material
from lca_research_evaluation.experiment import Experiment
from lca_research_evaluation.openai_agents_runner import OpenAIAgentsRuntime
from lca_research_evaluation.raw_runner import GeminiRawRuntime, OpenAIRawRuntime
from lca_research_evaluation.runner_types import (
    RUNNER_ARMS,
    RunnerRequest,
    RunnerResult,
    canonical_sha256,
)
from lca_research_kernel.assurance import AssuranceBoundary
from lca_research_kernel.model_gateway import ModelGateway
from lca_research_kernel.model_provider_adapters import (
    GeminiSDKAdapter,
    OpenAISDKAdapter,
)
from lca_research_kernel.product_interface import ProductInterface
from scripts.probe_model_providers import load_credential_environment


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"performance input must contain an object: {path}")
    return value


def _atomic(path: Path, value: object) -> None:
    content = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def compile_runner_request(
    root: Path,
    task: Mapping[str, object],
    *,
    run_id: str,
    model_id: str,
    corpus: Path | None = None,
) -> RunnerRequest:
    corpus = (
        root / "outputs/d18-performance-benchmark/corpus"
        if corpus is None
        else Path(corpus)
    ).resolve()
    transformations = task.get("derived_counterfactuals")
    if not isinstance(transformations, list) or len(transformations) != 1:
        raise ValueError("benchmark task requires one typed counterfactual")
    transformation = transformations[0].get("transformation")
    if not isinstance(transformation, str):
        raise ValueError("benchmark counterfactual identity is absent")
    model_refs = []
    for reference in task.get("material_refs") or []:
        if not isinstance(reference, Mapping):
            raise ValueError("benchmark material reference is invalid")
        path = (corpus / str(reference["path"])).resolve()
        path.relative_to(corpus.resolve())
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != reference.get("sha256"):
            raise ValueError("benchmark material source drifted")
        raw = json.loads(content)
        if not isinstance(raw, dict):
            raise ValueError("benchmark material source must be an object")
        visible_ref = {
            "path": str(reference["path"]),
            "sha256": reference["sha256"],
            "authority": reference.get("authority"),
            "object_type": reference.get("object_type"),
            "content_state": "derived_counterfactual_from_bound_official_bytes",
            "transformation": transformation,
            "content": counterfactual_material(raw, transformation),
        }
        if transformation != "hide_source_identity":
            visible_ref["official_object_ids"] = deepcopy(
                task.get("official_object_ids")
            )
        model_refs.append(visible_ref)
    model_refs.append(
        {
            "path": "commission/benchmark-commission-v1",
            "sha256": canonical_sha256(task["commission_ref"]),
            "authority": "frozen_evaluation_contract",
            "object_type": "commission",
            "content": deepcopy(task["commission_ref"]),
        }
    )
    descriptors = []
    for capability_id in task.get("allowed_capabilities") or []:
        descriptors.append(
            {
                "name": re.sub(r"[^a-zA-Z0-9_-]", "_", str(capability_id)),
                "description": (
                    "Bounded source operation. Use only if it can change the "
                    "commission outcome; output remains sourced_result_not_fact."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "source_sha256": {"type": "string"},
                    },
                    "required": ["source_sha256"],
                    "additionalProperties": False,
                },
                "capability_id": capability_id,
            }
        )
    return RunnerRequest(
        task_id=str(task["task_id"]),
        run_id=run_id,
        model_id=model_id,
        model_settings={"reasoning_effort": "low"},
        material_refs=tuple(model_refs),
        tool_descriptors=tuple(descriptors),
        permission_policy={
            "allowed_effects": ["local_read", "local_write", "external_read"],
            "forbidden_effects": [
                "credentials",
                "payment",
                "publication",
                "external_write",
                "irreversible",
            ],
            "allowed_capabilities": deepcopy(task.get("allowed_capabilities") or []),
        },
        budgets=deepcopy(dict(task["budgets"])),
    )


class _BoundedToolExecutor:
    def __init__(self, request: RunnerRequest, *, fail_once: bool):
        self.request = request
        self.fail_once = fail_once
        self.failed = False

    def __call__(self, name: str, arguments: Mapping[str, object]) -> dict:
        if self.fail_once and not self.failed:
            self.failed = True
            raise RuntimeError("injected_retryable_tool_failure")
        allowed = {str(item["name"]) for item in self.request.tool_descriptors}
        if name not in allowed:
            raise ValueError("tool call is not in the equal-input allowlist")
        requested = arguments.get("source_sha256")
        matches = [
            row
            for row in self.request.material_refs
            if row.get("sha256") == requested and "content" in row
        ]
        if len(matches) != 1:
            raise ValueError("tool source binding is absent or ambiguous")
        return {
            "status": "sourced_result_not_fact",
            "source_sha256": requested,
            "content": deepcopy(matches[0]["content"]),
        }


def _active_route(
    root: Path,
    environ: Mapping[str, str],
    *,
    provider_id: str,
) -> tuple[str, str, str]:
    catalog = _load(root / "lca_research_kernel/contracts/model-provider-catalog.json")
    rows = [
        row
        for row in catalog["providers"]
        if row.get("provider_id") == provider_id
        and row.get("admission_state") == "active_verified"
    ]
    if len(rows) != 1:
        raise ValueError(f"one active_verified {provider_id} route is required")
    row = rows[0]
    key = environ.get(str(row["credential_environment"]), "")
    model = environ.get(str(row["model_environment"]), "")
    if not key or not model:
        raise ValueError("OpenAI evaluation configuration is incomplete")
    return str(row["route_id"]), model, key


def _product_run_budget(request: RunnerRequest) -> dict:
    """Translate the equal-input byte/time budget into the product contract."""

    required = {
        "max_context_bytes",
        "max_elapsed_seconds",
        "max_model_calls",
        "max_steps",
        "max_tool_calls",
    }
    budgets = dict(request.budgets)
    if set(budgets) != required or any(
        not isinstance(budgets[key], int) or budgets[key] < 1
        for key in required - {"max_tool_calls"}
    ):
        raise ValueError("equal-input product run budget is invalid")
    if not isinstance(budgets["max_tool_calls"], int) or budgets["max_tool_calls"] < 0:
        raise ValueError("equal-input product tool budget is invalid")
    return {
        "contract": "lca_research_kernel.run_budget/v1",
        "max_elapsed_ms": budgets["max_elapsed_seconds"] * 1000,
        "max_steps": budgets["max_steps"],
        "max_model_calls": budgets["max_model_calls"],
        "max_tool_calls": budgets["max_tool_calls"],
        "max_context_tokens": (budgets["max_context_bytes"] + 3) // 4,
        "max_repeated_fingerprint": 2,
    }


def _runtimes(
    *,
    root: Path,
    request: RunnerRequest,
    route_id: str,
    provider_id: str,
    api_key: str,
    recorded_at: str,
) -> dict[str, object]:
    transformation = str(request.material_refs[0].get("transformation"))
    raw_tool_executor = _BoundedToolExecutor(
        request, fail_once=transformation == "fail_tool_once"
    )
    agents_tool_executor = _BoundedToolExecutor(
        request, fail_once=transformation == "fail_tool_once"
    )
    if provider_id == "gemini":
        raw = GeminiRawRuntime(
            api_key_provider=lambda: api_key,
            tool_executor=raw_tool_executor,
        )
    else:
        from openai import OpenAI

        raw = OpenAIRawRuntime(
            client_factory=lambda: OpenAI(api_key=api_key, timeout=180),
            tool_executor=raw_tool_executor,
        )
    agents = OpenAIAgentsRuntime(
        api_key_provider=lambda: api_key,
        tool_executor=agents_tool_executor,
        provider_id=provider_id,
        base_url=(
            "https://generativelanguage.googleapis.com/v1beta/openai/"
            if provider_id == "gemini"
            else None
        ),
    )

    def interface_factory(workspace: Path, run_id: str) -> ProductInterface:
        adapter_class = (
            GeminiSDKAdapter if provider_id == "gemini" else OpenAISDKAdapter
        )
        adapter = adapter_class(
            adapter_id=route_id,
            model_id=request.model_id,
            api_key_provider=lambda: api_key,
            timeout=180,
            **(
                {
                    "thinking_level": str(
                        request.model_settings["reasoning_effort"]
                    ).upper()
                }
                if provider_id == "gemini"
                and request.model_settings.get("reasoning_effort")
                else {}
            ),
        )
        gateway = ModelGateway(
            {route_id: adapter},
            policy={
                "contract": "lca_research_kernel.model_runtime_policy/v1",
                "policy_id": "evaluation-single-provider-bounded-retry-v1",
                "routes": [
                    {
                        "route_id": route_id,
                        "adapter_id": route_id,
                        "priority": 1,
                    }
                ],
                "retry": {
                    "max_attempts_per_route": 2,
                    "base_delay_ms": 250,
                    "retryable_codes": [
                        "provider_timeout",
                        "rate_limited",
                        "provider_failure",
                        "provider_server_error",
                        "provider_transport_error",
                    ],
                },
                "circuit_breaker": {"failure_threshold": 2},
            },
            clock=lambda: recorded_at,
        )
        return ProductInterface(
            workspace,
            model_gateway=gateway,
            capability_gateway=None,
            assurance=AssuranceBoundary(),
            clock=lambda: recorded_at,
            run_id=run_id,
            run_budget=_product_run_budget(request),
        )

    d18 = D18ProductRuntime(
        interface_factory=interface_factory,
        clock=lambda: recorded_at,
    )
    return {
        "raw_model_tool_loop": raw,
        "openai_agents_reference": agents,
        "d18_product_agent": d18,
    }


def _failure_result(
    *,
    arm: str,
    request: RunnerRequest,
    workspace: Path,
    error: BaseException,
    elapsed_ms: int,
) -> RunnerResult:
    failure = {
        "contract": "lca_research_evaluation.runner_failure/v1",
        "arm": arm,
        "task_id": request.task_id,
        "run_id": request.run_id,
        "request_sha256": request.request_sha256,
        "error_type": type(error).__name__,
        "error_message_recorded": False,
        "retryable": False,
        "external_writes": 0,
    }
    _atomic(workspace / "failure.json", failure)
    trajectory = {
        "contract": "lca_research_evaluation.runner_trajectory/v1",
        "arm": arm,
        "task_id": request.task_id,
        "run_id": request.run_id,
        "request_sha256": request.request_sha256,
        "failure_type": type(error).__name__,
        "oracle_visible": False,
        "direct_formal_write_count": 0,
        "effect_counts": {"external_write": 0, "local_write": 2},
        "recovery_success": False,
        "duplicate_effect_count": 0,
        "tool_calls": [],
    }
    _atomic(workspace / "trajectory.json", trajectory)
    trajectory_ref = {
        "path": (workspace / "trajectory.json").as_posix(),
        "sha256": hashlib.sha256(
            (workspace / "trajectory.json").read_bytes()
        ).hexdigest(),
    }
    return RunnerResult(
        arm=arm,
        task_id=request.task_id,
        run_id=request.run_id,
        status="failed",
        final_candidate_ref=None,
        trajectory_ref=trajectory_ref,
        usage={},
        timing={"final_result_ms": elapsed_ms},
        effect_counts={"external_write": 0, "local_write": 2},
    )


def _with_measured_timing(result: RunnerResult, elapsed_ms: int) -> RunnerResult:
    """Use the same outer wall-clock boundary for every comparison arm."""

    timing = deepcopy(dict(result.timing))
    timing["final_result_ms"] = max(0, int(elapsed_ms))
    timing.setdefault("first_artifact_ms", timing["final_result_ms"])
    return RunnerResult(
        arm=result.arm,
        task_id=result.task_id,
        run_id=result.run_id,
        status=result.status,
        final_candidate_ref=result.final_candidate_ref,
        trajectory_ref=result.trajectory_ref,
        usage=result.usage,
        timing=timing,
        effect_counts=result.effect_counts,
    )


def materialize_run_manifest(output: Path) -> dict:
    output = Path(output).resolve()
    rows = []
    for path in sorted(output.glob("*/runs/*/*/result.json")):
        relative = path.relative_to(output).as_posix()
        rows.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    manifest = {
        "contract": "lca_research_evaluation.performance_run_manifest/v1",
        "run_count": len(rows),
        "result_refs": rows,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    _atomic(output / "run-manifest.json", manifest)
    return manifest


def run_program(
    *,
    root: Path,
    output: Path,
    splits: set[str],
    repetitions: int,
    credential_env_file: Path,
    arms: tuple[str, ...] = RUNNER_ARMS,
    limit: int | None = None,
    corpus_path: Path | None = None,
) -> dict:
    root = root.resolve()
    output = output.resolve()
    catalog = _load(root / "lca_research_kernel/contracts/model-provider-catalog.json")
    environ = dict(os.environ)
    environ.update(load_credential_environment(credential_env_file, catalog))
    provider_id = os.environ.get("D18_EVALUATION_PROVIDER", "gemini")
    if provider_id not in {"openai", "gemini"}:
        raise ValueError("D18 evaluation provider is unsupported")
    route_id, model_id, api_key = _active_route(
        root, environ, provider_id=provider_id
    )
    corpus = (
        root / "outputs/d18-performance-benchmark/corpus"
        if corpus_path is None
        else Path(corpus_path)
    ).resolve()
    task_paths = sorted((corpus / "tasks").glob("*.json"))
    tasks = [task for path in task_paths if (task := _load(path))["split"] in splits]
    if limit is not None:
        tasks = tasks[:limit]
    run_rows = []
    for task in tasks:
        for repetition in range(1, repetitions + 1):
            run_id = f"{task['task_id']}-r{repetition}"
            request = compile_runner_request(
                root,
                task,
                run_id=run_id,
                model_id=model_id,
                corpus=corpus,
            )
            experiment = Experiment(
                output / str(task["split"]) / "runs",
                runtimes=_runtimes(
                    root=root,
                    request=request,
                    route_id=route_id,
                    provider_id=provider_id,
                    api_key=api_key,
                    recorded_at="2026-07-24T08:30:00Z",
                ),
            )
            manifest = experiment.compile(request)
            for row in manifest["arms"]:
                arm = row["arm"]
                if arm not in arms:
                    continue
                workspace = Path(row["workspace"])
                result_path = workspace / "result.json"
                if result_path.is_file():
                    prior = _load(result_path)
                    if prior.get("request_sha256") != request.request_sha256:
                        raise ValueError("preserved run request drifted")
                    run_rows.append(prior)
                    continue
                started = time.monotonic()
                try:
                    result = experiment.runners[arm].run(request, workspace)
                    result = _with_measured_timing(
                        result,
                        max(0, int((time.monotonic() - started) * 1000)),
                    )
                except BaseException as error:
                    result = _failure_result(
                        arm=arm,
                        request=request,
                        workspace=workspace,
                        error=error,
                        elapsed_ms=max(
                            0, int((time.monotonic() - started) * 1000)
                        ),
                    )
                payload = {
                    **result.as_dict(),
                    "request_sha256": request.request_sha256,
                    "family": task["family"],
                    "split": task["split"],
                    "repetition": repetition,
                    "model_id": model_id,
                    "provider_route": route_id,
                    "external_writes": 0,
                }
                _atomic(result_path, payload)
                run_rows.append(payload)
    run_manifest = materialize_run_manifest(output)
    summary = {
        "contract": "lca_research_evaluation.performance_run_summary/v1",
        "splits": sorted(splits),
        "task_count": len(tasks),
        "repetitions": repetitions,
        "arms": list(arms),
        "run_count": len(run_rows),
        "candidate_count": sum(row["status"] == "candidate" for row in run_rows),
        "failure_count": sum(row["status"] == "failed" for row in run_rows),
        "model_id": model_id,
        "provider_route": route_id,
        "provider_id": provider_id,
        "credential_values_recorded": False,
        "external_writes": 0,
        "run_manifest_sha256": run_manifest["manifest_sha256"],
    }
    summary["summary_sha256"] = canonical_sha256(summary)
    summary_name = (
        "final-run-summary.json"
        if splits == {"final"}
        else "open-splits-run-summary.json"
    )
    _atomic(output / summary_name, summary)
    return summary


def main() -> int:
    evaluation_python = ROOT / "runtime/evaluation-python/.venv/bin/python"
    evaluation_prefix = (ROOT / "runtime/evaluation-python/.venv").resolve()
    if Path(sys.prefix).resolve() != evaluation_prefix:
        if not evaluation_python.is_file():
            raise ValueError("isolated evaluation runtime is unavailable")
        os.execv(
            str(evaluation_python),
            [str(evaluation_python), str(Path(__file__).resolve()), *sys.argv[1:]],
        )
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--splits", required=True)
    parser.add_argument("--repetitions", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--credential-env-file",
        type=Path,
        default=Path("~/.config/d18-lca-agent/model-runtime.env"),
    )
    parser.add_argument("--arms", default=",".join(RUNNER_ARMS))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--corpus", type=Path)
    args = parser.parse_args()
    summary = run_program(
        root=args.root,
        output=args.output,
        splits={item.strip() for item in args.splits.split(",") if item.strip()},
        repetitions=args.repetitions,
        credential_env_file=args.credential_env_file.expanduser(),
        arms=tuple(item.strip() for item in args.arms.split(",") if item.strip()),
        limit=args.limit,
        corpus_path=args.corpus,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
