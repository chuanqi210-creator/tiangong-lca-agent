"""D-18 Product Interface arm for the product-external Evaluation Lab."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Callable

from lca_research_kernel.product_interface import ProductInterface

from .runner_types import RunnerRequest, RunnerResult, persist_runtime_response


class D18ProductRuntime:
    """Exercise only the public Product Interface and read its accepted view."""

    def __init__(
        self,
        *,
        interface_factory: Callable[[Path, str], ProductInterface],
        clock: Callable[[], str],
    ):
        self.interface_factory = interface_factory
        self.clock = clock

    def run_in_workspace(self, request: RunnerRequest, workspace: Path) -> dict:
        interface = self.interface_factory(workspace, request.run_id)
        research_id = f"evaluation-{request.run_id}"
        status = interface.start_research(
            {
                "contract": "lca_research_kernel.research_start/v1",
                "research_id": research_id,
                "title": "Source-bound evaluation task",
                "objective": (
                    "Produce a bounded source-backed LCA research candidate "
                    "without changing TianGong authority."
                ),
                "source_refs": deepcopy(
                    [dict(item) for item in request.material_refs]
                ),
                "received_at": self.clock(),
            }
        )
        result = interface.read_result(research_id)
        accepted = result.get("result") if result.get("status") == "accepted" else None
        missing_fields = []
        if accepted is None:
            missing_fields.append(str(status.get("boundary", "result_not_accepted")))
        candidate = (
            {
                key: deepcopy(accepted[key])
                for key in (
                    "status",
                    "bounded_conclusion",
                    "missing_fields",
                    "limitations",
                    "scientific_source_sha256s",
                    "runtime_provenance_sha256s",
                    "official_object_ids",
                    "values",
                )
            }
            if isinstance(accepted, dict)
            else {
                "status": "candidate_only",
                "bounded_conclusion": (
                    "D-18 reached a typed boundary before a result was accepted."
                ),
                "missing_fields": missing_fields,
                "limitations": ["No candidate passed the D-18 acceptance boundary."],
                "scientific_source_sha256s": [],
                "runtime_provenance_sha256s": [],
                "official_object_ids": [],
                "values": [],
            }
        )
        return {
            "candidate": candidate,
            "usage": {},
            "timing": {},
            "tool_calls": [],
            "actual_model": (
                accepted.get("model_identity", {}).get("model", request.model_id)
                if isinstance(accepted, dict)
                else request.model_id
            ),
            "project_record_write_count": 1,
            "project_record_result_status": result.get("status"),
            "boundary": status.get("boundary"),
            "failure_code": status.get("failure_code"),
            "recovery_route": status.get("recovery_route"),
            "effect_counts": {"external_write": 0},
        }


class D18ProductRunner:
    arm = "d18_product_agent"

    def __init__(self, *, runtime: object):
        self.runtime = runtime

    def run(self, request: RunnerRequest, workspace: Path) -> RunnerResult:
        if hasattr(self.runtime, "run_in_workspace"):
            response = self.runtime.run_in_workspace(request, workspace)  # type: ignore[attr-defined]
        else:
            response = self.runtime.run(request)  # type: ignore[attr-defined]
        return persist_runtime_response(
            arm=self.arm,
            request=request,
            workspace=workspace,
            response=response,
        )


__all__ = ["D18ProductRunner", "D18ProductRuntime"]
