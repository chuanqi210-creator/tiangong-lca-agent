"""Authority-narrow coordinator for artifact-only research workers."""

from __future__ import annotations

from copy import deepcopy
from typing import Callable, Mapping, Optional

from .semantic_graph import canonical_sha256
from .work_topology import TOPOLOGY_CONTRACT


EXECUTION_CONTRACT = "lca_research_kernel.worker_execution_result/v1"
WORKER_CONTRACT = "lca_research_kernel.research_worker_contract/v1"
OUTPUT_CONTRACT = "lca_research_kernel.research_worker_output/v1"
ARTIFACT_CONTRACT = "lca_research_kernel.research_worker_artifact/v1"


class ArtifactWorkerCoordinator:
    """Dispatch fixed slices and return candidate artifacts to the lead only."""

    def __init__(
        self,
        *,
        runner: object = None,
        clock: Optional[Callable[[], str]] = None,
        max_workers: int = 2,
    ):
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers < 1:
            raise ValueError("research worker concurrency budget is invalid")
        self.runner = runner
        self.clock = clock or (lambda: "time-unavailable")
        self.max_workers = max_workers if runner is not None else 1

    def execute(
        self,
        topology_plan: Mapping[str, object],
        *,
        context_by_slice: Mapping[str, object],
    ) -> dict:
        plan = self._plan(topology_plan)
        topology = plan["topology"]
        if topology == "single":
            return self._result(
                plan,
                status="not_dispatched",
                effective_topology="single",
                reason="single_agent_selected",
                artifacts=[],
                failures=[],
            )
        if self.runner is None:
            return self._result(
                plan,
                status="not_dispatched",
                effective_topology="single",
                reason="worker_runner_unavailable",
                artifacts=[],
                failures=[],
            )
        run = getattr(self.runner, "run", None)
        if not callable(run):
            raise ValueError("research worker runner contract is invalid")
        if not isinstance(context_by_slice, Mapping):
            raise ValueError("research worker context slices are invalid")

        artifacts = []
        failures = []
        completed_slice_ids = set()
        seen_digests = set()
        for work_slice in plan["work_slices"]:
            slice_id = work_slice["slice_id"]
            if not set(work_slice["depends_on"]).issubset(completed_slice_ids):
                failures.append(
                    {"slice_id": slice_id, "code": "worker_dependency_unavailable"}
                )
                continue
            context = context_by_slice.get(slice_id)
            if not isinstance(context, (list, Mapping)):
                failures.append(
                    {"slice_id": slice_id, "code": "worker_context_unavailable"}
                )
                continue
            contract = self._worker_contract(plan, work_slice, context)
            try:
                output = run(deepcopy(contract))
                artifact = self._artifact(contract, output)
            except Exception:
                failures.append(
                    {"slice_id": slice_id, "code": "worker_execution_failed"}
                )
                continue
            if artifact["artifact_sha256"] in seen_digests:
                failures.append(
                    {"slice_id": slice_id, "code": "duplicate_worker_artifact"}
                )
                continue
            artifacts.append(artifact)
            seen_digests.add(artifact["artifact_sha256"])
            completed_slice_ids.add(slice_id)

        artifacts.sort(key=lambda row: row["slice_id"])
        failures.sort(key=lambda row: row["slice_id"])
        status = "completed" if not failures else ("partial" if artifacts else "failed")
        next_route = {
            "completed": "aggregate-worker-artifacts-through-assurance",
            "partial": "reintegrate-completed-worker-artifacts-and-bound-gap",
            "failed": "degrade-to-bounded-single-agent-route",
        }[status]
        return self._result(
            plan,
            status=status,
            effective_topology=topology,
            reason="worker_execution_finished",
            artifacts=artifacts,
            failures=failures,
            next_route=next_route,
        )

    @staticmethod
    def _plan(value: Mapping[str, object]) -> dict:
        if (
            not isinstance(value, Mapping)
            or value.get("contract") != TOPOLOGY_CONTRACT
        ):
            raise ValueError("research worker topology plan is unsupported")
        normalized = deepcopy(dict(value))
        stored = normalized.pop("topology_sha256", None)
        if not isinstance(stored, str) or canonical_sha256(normalized) != stored:
            raise ValueError("research worker topology plan drifted")
        normalized["topology_sha256"] = stored
        if normalized.get("topology") not in {
            "single",
            "parallel_research",
            "generator_reviewer",
        } or not isinstance(normalized.get("work_slices"), list):
            raise ValueError("research worker topology is invalid")
        return normalized

    def _worker_contract(
        self,
        plan: Mapping[str, object],
        work_slice: Mapping[str, object],
        context: object,
    ) -> dict:
        contract = {
            "contract": WORKER_CONTRACT,
            "topology_ref": plan["topology_sha256"],
            "project_scope": {
                "project_id": plan["project_id"],
                "generation": plan["generation"],
            },
            "slice_id": work_slice["slice_id"],
            "worker_role": work_slice["worker_role"],
            "evidence_dimension": work_slice["evidence_dimension"],
            "objective": work_slice["objective"],
            "graph_slice": deepcopy(work_slice["graph_slice"]),
            "selected_skill_ids": deepcopy(work_slice["selected_skill_ids"]),
            "allowed_capability_ids": deepcopy(
                work_slice["allowed_capability_ids"]
            ),
            "excluded_scope": deepcopy(work_slice["excluded_scope"]),
            "expected_artifact": work_slice["expected_artifact"],
            "output_contract": work_slice["output_contract"],
            "stop_condition": work_slice["stop_condition"],
            "source_policy": work_slice["source_policy"],
            "budget": deepcopy(work_slice["budget"]),
            "can_delegate": False,
            "can_accept_project_truth": False,
            "can_expand_permissions": False,
        }
        if isinstance(context, Mapping):
            from .typed_context import validate_context_package

            package = validate_context_package(context)
            if package["project_state_ref"]["project_id"] != plan["project_id"]:
                raise ValueError("research worker context project drifted")
            contract["context_package"] = package
        else:
            contract["context_items"] = deepcopy(context)
        contract["contract_sha256"] = canonical_sha256(contract)
        return contract

    def _artifact(
        self, contract: Mapping[str, object], output: Mapping[str, object]
    ) -> dict:
        if (
            not isinstance(output, Mapping)
            or output.get("contract") != OUTPUT_CONTRACT
            or output.get("slice_id") != contract["slice_id"]
            or output.get("worker_role") != contract["worker_role"]
            or not isinstance(output.get("content"), Mapping)
            or not isinstance(output.get("source_refs"), list)
        ):
            raise ValueError("research worker output contract is invalid")
        artifact = {
            "contract": ARTIFACT_CONTRACT,
            "slice_id": contract["slice_id"],
            "worker_role": contract["worker_role"],
            "expected_artifact": contract["expected_artifact"],
            "classification": "candidate",
            "source_refs": deepcopy(output["source_refs"]),
            "content": deepcopy(dict(output["content"])),
            "worker_contract_sha256": contract["contract_sha256"],
            "acceptance_effect": "none",
        }
        artifact["artifact_sha256"] = canonical_sha256(artifact)
        return artifact

    @staticmethod
    def _result(
        plan: Mapping[str, object],
        *,
        status: str,
        effective_topology: str,
        reason: str,
        artifacts: list,
        failures: list,
        next_route: str = "continue-single-agent-control",
    ) -> dict:
        result = {
            "contract": EXECUTION_CONTRACT,
            "topology_ref": plan["topology_sha256"],
            "requested_topology": plan["topology"],
            "effective_topology": effective_topology,
            "status": status,
            "reason": reason,
            "artifacts": deepcopy(artifacts),
            "failures": deepcopy(failures),
            "next_route": next_route,
            "project_truth_effect": "none",
        }
        result["execution_sha256"] = canonical_sha256(result)
        return result


class ModelArtifactWorkerRunner:
    """Narrow adapter that turns one worker contract into one model candidate."""

    def __init__(self, model_gateway: object, *, trace: object = None):
        reason = getattr(model_gateway, "reason", None)
        if not callable(reason):
            raise ValueError("research worker model gateway is invalid")
        self.model_gateway = model_gateway
        self.trace = trace

    def run(self, contract: Mapping[str, object]) -> dict:
        if (
            not isinstance(contract, Mapping)
            or contract.get("contract") != WORKER_CONTRACT
            or contract.get("can_accept_project_truth") is not False
            or contract.get("can_delegate") is not False
        ):
            raise ValueError("research worker contract boundary is invalid")
        request = {
            "contract": "lca_research_kernel.model_request/v2",
            "request_id": "research-worker-" + str(contract["contract_sha256"])[:24],
            "task": (
                "Produce one bounded research artifact for the delegated evidence "
                "dimension. Use only the supplied context and source identities. "
                "Return candidate content, limitations, and no project-state or "
                "permission changes."
            ),
            "context": deepcopy(contract.get("context_package")),
            "output_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["finding", "limitations"],
                "properties": {
                    "finding": {"type": "string"},
                    "limitations": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
            "tools": [],
            "required_capabilities": ["structured_output"],
        }
        if request["context"] is None:
            raise ValueError("research worker typed context is unavailable")
        candidate = self.model_gateway.reason(request, trace=self.trace)
        validator = getattr(
            self.model_gateway, "validate_candidate_request_binding", None
        )
        if callable(validator):
            validator(candidate, request)
        else:
            from .model_gateway import validate_model_candidate_request_binding

            validate_model_candidate_request_binding(candidate, request)
        output = candidate.get("output") if isinstance(candidate, Mapping) else None
        if not isinstance(output, Mapping):
            raise ValueError("research worker model candidate is invalid")
        return {
            "contract": OUTPUT_CONTRACT,
            "slice_id": contract["slice_id"],
            "worker_role": contract["worker_role"],
            "content": deepcopy(dict(output)),
            "source_refs": deepcopy(contract["graph_slice"]["node_ids"]),
        }
