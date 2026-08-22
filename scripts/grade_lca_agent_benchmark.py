#!/usr/bin/env python3
"""Grade preserved Evaluation Lab runs without writing product truth."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_evaluation.counterfactual import counterfactual_material
from lca_research_evaluation.grader import grade_open_result, grade_run
from lca_research_evaluation.model_grader import GeminiIsolatedGrader
from lca_research_evaluation.metrics import (
    aggregate_arm_metrics,
    compare_d18_to_best_baseline,
)
from lca_research_evaluation.runner_types import RunnerResult
from scripts.probe_model_providers import load_credential_environment


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"grader input must contain an object: {path}")
    return value


def _load_ref(reference: dict) -> dict:
    path = Path(str(reference["path"]))
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != reference.get("sha256"):
        raise ValueError("grader candidate or trajectory reference drifted")
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("grader referenced artifact must contain an object")
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


def _runner_result(value: dict) -> RunnerResult:
    fields = {
        key: value[key]
        for key in (
            "arm",
            "task_id",
            "run_id",
            "status",
            "final_candidate_ref",
            "trajectory_ref",
            "usage",
            "timing",
            "effect_counts",
        )
    }
    return RunnerResult(**fields)


def grade_workspace(
    workspace: Path,
    splits: set[str],
    *,
    model_grader: GeminiIsolatedGrader,
    corpus_path: Path | None = None,
) -> dict:
    workspace = workspace.resolve()
    corpus = (
        workspace / "corpus"
        if corpus_path is None
        else Path(corpus_path)
    ).resolve()
    tasks = {
        task["task_id"]: task
        for path in sorted((corpus / "tasks").glob("*.json"))
        if (task := _load(path)).get("split") in splits
    }
    rows = []
    grader_call_count = 0
    routes = ("gemini-isolated-grader-a", "gemini-isolated-grader-b")
    for path in sorted(workspace.glob("*/runs/*/*/result.json")):
        raw = _load(path)
        if raw.get("task_id") not in tasks:
            continue
        result = _runner_result(raw)
        task = dict(tasks[result.task_id])
        transformation = str(
            task["derived_counterfactuals"][0]["transformation"]
        )
        visible_materials = []
        for reference in task["material_refs"]:
            source_path = corpus / reference["path"]
            content = source_path.read_bytes()
            if hashlib.sha256(content).hexdigest() != reference["sha256"]:
                raise ValueError("benchmark grader source material drifted")
            source = json.loads(content)
            if not isinstance(source, dict):
                raise ValueError("benchmark grader source material is invalid")
            visible_materials.append(
                counterfactual_material(source, transformation)
            )
        task["grader_visible_materials"] = visible_materials
        grade = grade_run(task, result)
        open_grade = None
        lca_score = 0.0
        if grade["deterministic_passed"] and result.final_candidate_ref is not None:
            receipt_path = (
                workspace
                / "grader/runs"
                / str(raw["split"])
                / result.run_id
                / f"{result.arm}.json"
            )
            if receipt_path.is_file():
                receipt = _load(receipt_path)
                if (
                    receipt.get("candidate_sha256")
                    != result.final_candidate_ref["sha256"]
                    or tuple(receipt.get("grader_routes") or ()) != routes
                ):
                    raise ValueError("preserved grader receipt drifted")
                open_grade = receipt["open_grade"]
            else:
                candidate = _load_ref(dict(result.final_candidate_ref))
                oracle = _load(corpus / task["oracle_ref"]["path"])
                responses = {}
                with ThreadPoolExecutor(max_workers=len(routes)) as executor:
                    futures = {
                        route: executor.submit(
                            model_grader.grade,
                            candidate=candidate,
                            task=task,
                            oracle=oracle,
                            route_id=route,
                        )
                        for route in routes
                    }
                    graded = {
                        route: futures[route].result()
                        for route in routes
                    }
                for route, response in graded.items():
                    grader_call_count += 1
                    responses[route] = {
                        "score": response["score"],
                        "criteria": response["criteria"],
                        "response_sha256": response["response_sha256"],
                    }
                open_grade = grade_open_result(
                    {
                        "arm_identity_visible": False,
                        "responses": responses,
                    },
                    result.final_candidate_ref,
                    grader_routes=routes,
                )
                _atomic(
                    receipt_path,
                    {
                        "contract": (
                            "lca_research_evaluation.isolated_model_grade_receipt/v1"
                        ),
                        "task_id": result.task_id,
                        "run_id": result.run_id,
                        "arm": result.arm,
                        "candidate_sha256": result.final_candidate_ref["sha256"],
                        "grader_routes": list(routes),
                        "open_grade": open_grade,
                        "executor_feedback_permitted": False,
                        "hidden_reasoning_recorded": False,
                    },
                )
            lca_score = (
                float(open_grade["score"]) if open_grade.get("accepted") else 0.0
            )
        trajectory = _load_ref(dict(result.trajectory_ref))
        tool_calls = trajectory.get("tool_calls") or []
        unnecessary_tool_ratio = (
            0.0
            if not tool_calls or transformation == "fail_tool_once"
            else 1.0
        )
        rows.append(
            {
                "arm": result.arm,
                "task_id": result.task_id,
                "run_id": result.run_id,
                "family": task["family"],
                "success": (
                    grade["success"]
                    and (
                        open_grade is None
                        or open_grade.get("accepted") is True
                    )
                ),
                "lca_score": lca_score,
                "valid_result_seconds": float(
                    result.timing.get("final_result_ms", 0)
                )
                / 1000.0,
                "unnecessary_tool_ratio": unnecessary_tool_ratio,
                "recovery_success": grade["recovery_success"],
                "duplicate_effect_count": grade["duplicate_effect_count"],
                "veto_failures": grade["veto_failures"],
                "scientific_exactness": grade["scientific_exactness"],
                "unit_exactness": grade["unit_exactness"],
                "grader_disagreement_points": (
                    open_grade.get("disagreement_points")
                    if isinstance(open_grade, dict)
                    else None
                ),
            }
        )
    if not rows:
        raise ValueError("no matching benchmark runs were found")
    metrics = aggregate_arm_metrics(rows)
    decision = compare_d18_to_best_baseline(metrics)
    return {
        "contract": "lca_research_evaluation.benchmark_grade_package/v1",
        "splits": sorted(splits),
        "row_count": len(rows),
        "grader_call_count": grader_call_count,
        "grader_routes": list(routes),
        "metrics": metrics,
        "decision": decision,
    }


def main() -> int:
    root = ROOT
    evaluation_python = root / "runtime/evaluation-python/.venv/bin/python"
    evaluation_prefix = (root / "runtime/evaluation-python/.venv").resolve()
    if Path(sys.prefix).resolve() != evaluation_prefix:
        if not evaluation_python.is_file():
            raise ValueError("isolated evaluation runtime is unavailable")
        os.execv(
            str(evaluation_python),
            [str(evaluation_python), str(Path(__file__).resolve()), *sys.argv[1:]],
        )
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--splits", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument(
        "--credential-env-file",
        type=Path,
        default=Path("~/.config/d18-lca-agent/model-runtime.env"),
    )
    args = parser.parse_args()
    catalog = _load(
        root / "lca_research_kernel/contracts/model-provider-catalog.json"
    )
    environ = dict(os.environ)
    environ.update(
        load_credential_environment(
            args.credential_env_file.expanduser(),
            catalog,
        )
    )
    providers = [
        row
        for row in catalog["providers"]
        if row.get("provider_id") == "gemini"
        and row.get("admission_state") == "active_verified"
    ]
    if len(providers) != 1:
        raise ValueError("one active_verified Gemini grader route is required")
    provider = providers[0]
    credential = environ.get(str(provider["credential_environment"]), "")
    model_id = environ.get(str(provider["model_environment"]), "")
    model_grader = GeminiIsolatedGrader(
        model_id=model_id,
        api_key_provider=lambda: credential,
    )
    result = grade_workspace(
        args.workspace,
        {item.strip() for item in args.splits.split(",")},
        model_grader=model_grader,
        corpus_path=args.corpus,
    )
    encoded = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        _atomic(args.output, result)
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
