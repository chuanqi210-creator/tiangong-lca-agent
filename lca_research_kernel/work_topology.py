"""Deterministic planning for bounded, artifact-only research workers."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Mapping, Optional, Sequence

from .semantic_graph import canonical_sha256


TOPOLOGY_CONTRACT = "lca_research_kernel.work_topology_plan/v1"
SIGNATURE_CONTRACT = "lca_research_kernel.research_problem_signature/v1"
SAFE_ID_RE = re.compile(r"[^a-z0-9]+")


def _strings(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"work topology {label} is invalid")
    return sorted(set(value))


def _safe_id(value: str) -> str:
    normalized = SAFE_ID_RE.sub("-", value.lower()).strip("-")
    if not normalized:
        raise ValueError("work topology dimension identity is invalid")
    return normalized


class WorkTopologyPlanner:
    """Choose collaboration only when the state proves bounded independence."""

    def plan(
        self,
        *,
        problem_signature: Mapping[str, object],
        selected_node_ids: Sequence[str],
        selected_capability_ids: Sequence[str],
        selected_skill_ids: Sequence[str],
        work_budget: Mapping[str, object],
        dependencies: Sequence[Mapping[str, object]] = (),
        review_requirement: Optional[Mapping[str, object]] = None,
    ) -> dict:
        signature = self._signature(problem_signature)
        node_ids = _strings(list(selected_node_ids), "selected_node_ids")
        capability_ids = _strings(
            list(selected_capability_ids), "selected_capability_ids"
        )
        skill_ids = _strings(list(selected_skill_ids), "selected_skill_ids")
        budget = self._budget(work_budget)
        dimensions = _strings(
            signature["independent_evidence_dimensions"],
            "independent_evidence_dimensions",
        )
        normalized_dependencies = self._dependencies(dependencies, dimensions)

        topology = "single"
        reason_codes = ["single_agent_default"]
        slices = []
        if review_requirement is not None:
            review = self._review_requirement(review_requirement)
            if budget["max_workers"] >= 2 and budget["max_model_calls"] >= 2:
                topology = "generator_reviewer"
                reason_codes = ["explicit_candidate_and_review_rubric"]
                slices = self._review_slices(
                    signature,
                    review,
                    node_ids,
                    capability_ids,
                    skill_ids,
                    budget,
                )
            else:
                reason_codes = ["insufficient_worker_or_model_budget"]
        elif (
            2 <= len(dimensions) <= 4
            and not normalized_dependencies
            and budget["max_workers"] >= len(dimensions)
            and budget["max_model_calls"] >= len(dimensions)
        ):
            topology = "parallel_research"
            reason_codes = ["independent_graph_separated_evidence_dimensions"]
            slices = self._parallel_slices(
                signature,
                dimensions,
                node_ids,
                capability_ids,
                skill_ids,
                budget,
            )
        elif normalized_dependencies:
            reason_codes = ["sequential_dependency_requires_single_control"]
        elif len(dimensions) >= 2:
            reason_codes = ["insufficient_worker_or_model_budget"]

        plan = {
            "contract": TOPOLOGY_CONTRACT,
            "project_id": signature["project_id"],
            "generation": signature["generation"],
            "topology": topology,
            "reason_codes": reason_codes,
            "work_slices": slices,
            "dependencies": normalized_dependencies,
            "acceptance_owner": "research_orchestrator",
            "aggregation_route": "assurance_boundary",
            "project_truth_boundary": "workers_candidate_artifacts_only",
            "budgets": budget,
        }
        plan["topology_sha256"] = canonical_sha256(plan)
        return plan

    @staticmethod
    def _signature(value: Mapping[str, object]) -> dict:
        if (
            not isinstance(value, Mapping)
            or value.get("contract") != SIGNATURE_CONTRACT
            or not isinstance(value.get("project_id"), str)
            or isinstance(value.get("generation"), bool)
            or not isinstance(value.get("generation"), int)
            or not isinstance(value.get("desired_artifact"), str)
            or not isinstance(value.get("claim_ceiling"), str)
        ):
            raise ValueError("work topology problem signature is invalid")
        _strings(
            value.get("independent_evidence_dimensions"),
            "independent_evidence_dimensions",
        )
        _strings(value.get("completion_predicates"), "completion_predicates")
        return deepcopy(dict(value))

    @staticmethod
    def _budget(value: Mapping[str, object]) -> dict:
        if not isinstance(value, Mapping):
            raise ValueError("work topology budget is invalid")
        result = {}
        for key in ("max_workers", "max_model_calls", "max_tool_calls"):
            item = value.get(key)
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                raise ValueError(f"work topology budget {key} is invalid")
            result[key] = item
        return result

    @staticmethod
    def _dependencies(
        rows: Sequence[Mapping[str, object]], dimensions: list[str]
    ) -> list[dict]:
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise ValueError("work topology dependencies are invalid")
        result = []
        allowed = set(dimensions)
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError("work topology dependency is invalid")
            before = row.get("before")
            after = row.get("after")
            if (
                not isinstance(before, str)
                or not isinstance(after, str)
                or before == after
                or before not in allowed
                or after not in allowed
            ):
                raise ValueError("work topology dependency endpoints are invalid")
            result.append({"before": before, "after": after})
        return sorted(result, key=lambda row: (row["before"], row["after"]))

    @staticmethod
    def _review_requirement(value: Mapping[str, object]) -> dict:
        if not isinstance(value, Mapping):
            raise ValueError("work topology review requirement is invalid")
        candidate = value.get("candidate_artifact_ref")
        rubric = value.get("review_rubric_id")
        if (
            not isinstance(candidate, str)
            or not candidate.strip()
            or not isinstance(rubric, str)
            or not rubric.strip()
        ):
            raise ValueError("work topology review contract is incomplete")
        return {"candidate_artifact_ref": candidate, "review_rubric_id": rubric}

    def _parallel_slices(
        self,
        signature: Mapping[str, object],
        dimensions: list[str],
        node_ids: list[str],
        capability_ids: list[str],
        skill_ids: list[str],
        budget: Mapping[str, int],
    ) -> list[dict]:
        return [
            self._slice(
                slice_id="slice-" + _safe_id(dimension),
                worker_role="evidence_researcher",
                evidence_dimension=dimension,
                expected_artifact=(
                    str(signature["desired_artifact"]) + "#" + _safe_id(dimension)
                ),
                node_ids=node_ids,
                capability_ids=capability_ids,
                skill_ids=skill_ids,
                depends_on=[],
                budget=budget,
                lane_count=len(dimensions),
            )
            for dimension in dimensions
        ]

    def _review_slices(
        self,
        signature: Mapping[str, object],
        review: Mapping[str, str],
        node_ids: list[str],
        capability_ids: list[str],
        skill_ids: list[str],
        budget: Mapping[str, int],
    ) -> list[dict]:
        generator = self._slice(
            slice_id="slice-generator",
            worker_role="generator",
            evidence_dimension="candidate_generation",
            expected_artifact=str(signature["desired_artifact"]) + "#candidate",
            node_ids=node_ids,
            capability_ids=capability_ids,
            skill_ids=skill_ids,
            depends_on=[],
            budget=budget,
            lane_count=2,
        )
        reviewer = self._slice(
            slice_id="slice-reviewer",
            worker_role="reviewer",
            evidence_dimension="independent_review",
            expected_artifact=str(signature["desired_artifact"]) + "#review",
            node_ids=node_ids,
            capability_ids=capability_ids,
            skill_ids=skill_ids,
            depends_on=[generator["slice_id"]],
            budget=budget,
            lane_count=2,
        )
        reviewer["candidate_artifact_ref"] = review["candidate_artifact_ref"]
        reviewer["review_rubric_id"] = review["review_rubric_id"]
        return [generator, reviewer]

    @staticmethod
    def _slice(
        *,
        slice_id: str,
        worker_role: str,
        evidence_dimension: str,
        expected_artifact: str,
        node_ids: list[str],
        capability_ids: list[str],
        skill_ids: list[str],
        depends_on: list[str],
        budget: Mapping[str, int],
        lane_count: int,
    ) -> dict:
        return {
            "slice_id": slice_id,
            "worker_role": worker_role,
            "evidence_dimension": evidence_dimension,
            "objective": "Produce bounded evidence for " + evidence_dimension,
            "graph_slice": {"node_ids": deepcopy(node_ids)},
            "selected_skill_ids": deepcopy(skill_ids),
            "allowed_capability_ids": deepcopy(capability_ids),
            "excluded_scope": [
                "project_record_mutation",
                "worker_delegation",
                "self_acceptance",
                "permission_expansion",
            ],
            "expected_artifact": expected_artifact,
            "output_contract": "lca_research_kernel.research_worker_output/v1",
            "stop_condition": "return_one_candidate_artifact_or_typed_failure",
            "source_policy": "use_only_content_bound_graph_slice",
            "depends_on": deepcopy(depends_on),
            "budget": {
                "max_model_calls": max(1, budget["max_model_calls"] // lane_count),
                "max_tool_calls": budget["max_tool_calls"] // lane_count,
            },
        }
