"""Fair, isolated compilation and execution of three evaluation arms."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .d18_runner import D18ProductRunner
from .openai_agents_runner import OpenAIAgentsReferenceRunner
from .raw_runner import RawModelToolLoopRunner
from .runner_types import RUNNER_ARMS, RunnerRequest, RunnerResult


class Experiment:
    def __init__(self, root: Path, *, runtimes: Mapping[str, object]):
        self.root = Path(root).resolve()
        if set(runtimes) != set(RUNNER_ARMS):
            raise ValueError("experiment runtimes must match the frozen runner arms")
        self.runners = {
            "raw_model_tool_loop": RawModelToolLoopRunner(
                runtime=runtimes["raw_model_tool_loop"]
            ),
            "openai_agents_reference": OpenAIAgentsReferenceRunner(
                runtime=runtimes["openai_agents_reference"]
            ),
            "d18_product_agent": D18ProductRunner(
                runtime=runtimes["d18_product_agent"]
            ),
        }

    def compile(self, request: RunnerRequest) -> dict:
        arms = []
        for arm in RUNNER_ARMS:
            workspace = self.root / request.run_id / arm
            arms.append(
                {
                    "arm": arm,
                    "request_sha256": request.request_sha256,
                    "workspace": workspace.as_posix(),
                    "trace_path": (workspace / "trajectory.json").as_posix(),
                    "credential_view": f"isolated:{request.run_id}:{arm}",
                }
            )
        return {
            "contract": "lca_research_evaluation.experiment_manifest/v1",
            "task_id": request.task_id,
            "run_id": request.run_id,
            "request_sha256": request.request_sha256,
            "arms": arms,
            "oracle_isolated": True,
            "credential_views_isolated": True,
            "cross_arm_intermediate_access": False,
        }

    def run(self, request: RunnerRequest) -> tuple[RunnerResult, ...]:
        manifest = self.compile(request)
        return tuple(
            self.runners[row["arm"]].run(request, Path(row["workspace"]))
            for row in manifest["arms"]
        )


__all__ = ["Experiment"]
