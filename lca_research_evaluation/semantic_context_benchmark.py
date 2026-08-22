"""Frozen comparison of broad context selection and the D-18 semantic runtime."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Mapping

from lca_research_kernel.semantic_context import ResearchSemanticContext
from lca_research_kernel.semantic_graph import canonical_sha256


FIXTURE_CONTRACT = "lca_research_kernel.semantic_context_benchmark_fixture/v1"
REPORT_CONTRACT = "lca_research_kernel.semantic_context_benchmark_report/v1"
BASELINE_IDENTITY = "36eceef1-broad-material-empty-method-selector"
CANDIDATE_IDENTITY = "d18-semantic-graph-skill-topology-runtime"


def _read_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unavailable or invalid") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


class SemanticContextBenchmark:
    """Evaluate only frozen structured state; no model or tool is invoked."""

    def __init__(self, root: Path, fixture: Mapping[str, object]):
        self.root = Path(root).resolve()
        self.fixture = self._validate_fixture(fixture)
        self.fixture_sha256 = canonical_sha256(self.fixture)
        self.context = ResearchSemanticContext.from_files(
            self.root,
            source_lock_path=(
                self.root
                / "lca_research_kernel/contracts/tiangong-source-lock.json"
            ),
            capability_catalog_path=(
                self.root
                / "lca_research_kernel/contracts/tiangong-capability-catalog.json"
            ),
            skill_registry_path=(
                self.root
                / "lca_research_kernel/contracts/research-skill-registry.json"
            ),
        )
        self.capability_catalog = _read_json(
            self.root
            / "lca_research_kernel/contracts/tiangong-capability-catalog.json",
            "semantic context capability catalog",
        )
        self.source_lock = _read_json(
            self.root / "lca_research_kernel/contracts/tiangong-source-lock.json",
            "semantic context source lock",
        )

    @classmethod
    def load(cls, root: Path, fixture_path: Path) -> "SemanticContextBenchmark":
        return cls(Path(root), _read_json(Path(fixture_path), "semantic benchmark fixture"))

    @staticmethod
    def _validate_fixture(value: Mapping[str, object]) -> dict:
        if (
            not isinstance(value, Mapping)
            or value.get("contract") != FIXTURE_CONTRACT
            or value.get("baseline_commit") != "36eceef1"
            or not isinstance(value.get("thresholds"), Mapping)
            or not isinstance(value.get("cases"), list)
            or not value["cases"]
        ):
            raise ValueError("semantic benchmark fixture contract is unsupported")
        seen = set()
        dependency_expectation_count = 0
        citation_expectation_count = 0
        required = {
            "case_id",
            "desired_artifact",
            "target_object_kinds",
            "required_evidence_roles",
            "required_fact_ids",
            "forbidden_fact_ids",
            "exact_fact_ids",
            "expected_skill_id",
            "expected_query_mode",
            "allowed_topologies",
            "claim_ceiling",
            "facts",
        }
        for case in value["cases"]:
            if not isinstance(case, Mapping) or not required.issubset(case):
                raise ValueError("semantic benchmark case is incomplete")
            case_id = case.get("case_id")
            if not isinstance(case_id, str) or not case_id or case_id in seen:
                raise ValueError("semantic benchmark case identity is invalid")
            seen.add(case_id)
            for key in (
                "target_object_kinds",
                "required_evidence_roles",
                "required_fact_ids",
                "forbidden_fact_ids",
                "exact_fact_ids",
                "allowed_topologies",
                "facts",
            ):
                if not isinstance(case.get(key), list):
                    raise ValueError(f"semantic benchmark {case_id} {key} is invalid")
            dependencies = case.get("dependency_expectations") or []
            citations = case.get("citation_expectations") or []
            if not isinstance(dependencies, list) or not isinstance(citations, list):
                raise ValueError(f"semantic benchmark {case_id} quality oracle is invalid")
            for row in dependencies:
                if (
                    not isinstance(row, Mapping)
                    or set(row) != {"required_role", "fact_id"}
                    or row.get("required_role") not in case["required_evidence_roles"]
                    or row.get("fact_id") not in case["required_fact_ids"]
                ):
                    raise ValueError(
                        f"semantic benchmark {case_id} dependency oracle is invalid"
                    )
            for row in citations:
                if (
                    not isinstance(row, Mapping)
                    or set(row)
                    != {"fact_id", "source_version", "source_content_sha256"}
                    or row.get("fact_id") not in case["required_fact_ids"]
                    or not isinstance(row.get("source_version"), str)
                    or not isinstance(row.get("source_content_sha256"), str)
                    or len(row["source_content_sha256"]) != 64
                ):
                    raise ValueError(
                        f"semantic benchmark {case_id} citation oracle is invalid"
                    )
            dependency_expectation_count += len(dependencies)
            citation_expectation_count += len(citations)
        if dependency_expectation_count < 1 or citation_expectation_count < 1:
            raise ValueError("semantic benchmark independent quality oracles are absent")
        return deepcopy(dict(value))

    def run(self) -> dict:
        baseline_rows = []
        candidate_rows = []
        for index, case in enumerate(self.fixture["cases"], start=1):
            project_view = self._project_view(case, index)
            expected_change = self._expected_change(case)
            available = self._available_capabilities(case)
            work_budget = {
                "max_workers": int(case.get("max_workers", 1)),
                "max_model_calls": max(2, int(case.get("max_workers", 1))),
                "max_tool_calls": 2,
            }
            baseline_rows.append(self._baseline(case, project_view))
            first = self.context.plan(
                project_view=project_view,
                expected_state_change=expected_change,
                available_capabilities=available,
                context_budget=6000,
                work_budget=work_budget,
                generation=index,
            )
            second = self.context.plan(
                project_view=project_view,
                expected_state_change=expected_change,
                available_capabilities=available,
                context_budget=6000,
                work_budget=work_budget,
                generation=index,
            )
            candidate_rows.append(
                self._candidate(case, project_view, first, replay=second)
            )
        baseline = self._metrics(baseline_rows, candidate=False)
        candidate = self._metrics(candidate_rows, candidate=True)
        candidate["case_specific_routing_count"] = self._case_route_hits()
        failed_gates = self._failed_gates(baseline, candidate)
        report = {
            "contract": REPORT_CONTRACT,
            "fixture_id": self.fixture["fixture_id"],
            "fixture_sha256": self.fixture_sha256,
            "case_count": len(candidate_rows),
            "baseline_identity": BASELINE_IDENTITY,
            "candidate_identity": CANDIDATE_IDENTITY,
            "baseline": baseline,
            "candidate": candidate,
            "precision_gain": round(
                candidate["context_precision"] - baseline["context_precision"], 6
            ),
            "failed_gates": failed_gates,
            "status": "accepted" if not failed_gates else "rejected",
            "model_calls": 0,
            "tool_calls": 0,
            "interpretation_limit": (
                "This benchmark proves deterministic context, Skill, topology, "
                "and conclusion-boundary behavior on the frozen fixture; it is "
                "not human LCA expert validation."
            ),
            "case_results": candidate_rows,
        }
        report["report_sha256"] = canonical_sha256(report)
        return report

    def write(self, output_dir: Path) -> dict:
        output = Path(output_dir)
        report = self.run()
        _write_json(output / "report.json", report)
        _write_json(
            output / "acceptance-receipt.json",
            {
                "contract": "lca_research_kernel.semantic_context_acceptance_receipt/v1",
                "fixture_sha256": report["fixture_sha256"],
                "report_sha256": report["report_sha256"],
                "status": report["status"],
                "failed_gates": report["failed_gates"],
            },
        )
        return {
            "report_path": (output / "report.json").as_posix(),
            "report_sha256": report["report_sha256"],
            "status": report["status"],
        }

    def _project_view(self, case: Mapping[str, object], generation: int) -> dict:
        case_hash = canonical_sha256({"case": case["case_id"], "generation": generation})
        facts = {
            "research-goal": {
                "fact_id": "research-goal",
                "classification": "official",
                "semantic_role": "research_goal",
                "domain_kind": "research_goal",
                "value": "Complete the frozen LCA research state transition.",
                "source": {
                    "authority": "user_supplied_project_input",
                    "version": "benchmark-brief-v1",
                    "content_sha256": canonical_sha256(case["case_id"]),
                },
            }
        }
        for raw in case["facts"]:
            row = deepcopy(dict(raw))
            fact_id = row["fact_id"]
            classification = row["classification"]
            authority = (
                "project_record"
                if classification == "missing"
                else (
                    "user_supplied_project_input"
                    if classification == "candidate"
                    else "tiangong_official"
                )
            )
            row["source"] = {
                "authority": authority,
                "version": "fixture-v1",
                "content_sha256": canonical_sha256(
                    {"case_id": case["case_id"], "fact": raw}
                ),
            }
            facts[fact_id] = row
        return {
            "contract": "lca_research_kernel.project_record_view/v1",
            "project_id": "benchmark-project-" + case_hash[:16],
            "title": "Frozen semantic benchmark",
            "objective": "Evaluate one structured LCA research transition.",
            "status": "active",
            "facts": facts,
            "artifacts": {},
            "agent_startups": {},
            "capability_runs": {},
            "capability_catalog": None,
            "decisions": [],
            "risks": {},
            "result": None,
            "next_route": "resolve-frozen-semantic-transition",
            "last_event_id": f"fixture-event-{generation}",
            "last_event_sha256": case_hash,
        }

    @staticmethod
    def _expected_change(case: Mapping[str, object]) -> dict:
        exact = ["project:fact:" + item for item in case["exact_fact_ids"]]
        if not exact and case.get("unknown_target") is not True:
            exact = ["project:goal"]
        dimensions = sorted(
            {
                row["independent_evidence_dimension"]
                for row in case["facts"]
                if isinstance(row, Mapping)
                and isinstance(row.get("independent_evidence_dimension"), str)
            }
        )
        return {
            "contract": "lca_research_kernel.expected_state_change/v1",
            "desired_artifact": case["desired_artifact"],
            "target_object_kinds": deepcopy(case["target_object_kinds"]),
            "exact_node_ids": exact,
            "required_evidence_roles": deepcopy(case["required_evidence_roles"]),
            "calculation_required": case.get("calculation_required") is True,
            "source_confirmation_required": (
                case.get("source_confirmation_required") is True
            ),
            "comparison_required": case.get("comparison_required") is True,
            "global_structure_required": (
                case.get("global_structure_required") is True
            ),
            "unknown_target": case.get("unknown_target") is True,
            "conflict_types": deepcopy(case.get("conflict_types") or []),
            "claim_ceiling": case["claim_ceiling"],
            "independent_evidence_dimensions": dimensions,
            "completion_predicates": [
                str(case["desired_artifact"]) + "_complete_or_bounded"
            ],
        }

    def _available_capabilities(self, case: Mapping[str, object]) -> list[dict]:
        requested = set(case.get("available_capability_ids") or [])
        sources = {
            row["source_id"]: row
            for row in self.source_lock["sources"]
            if isinstance(row, Mapping)
        }
        result = []
        for row in self.capability_catalog["capabilities"]:
            if row.get("capability_id") not in requested:
                continue
            source = sources[row["source_lock_id"]]
            integrity = str(source.get("integrity") or "")
            digest = integrity[7:] if integrity.startswith("sha256-") else canonical_sha256(source)
            result.append(
                {
                    "capability_id": row["capability_id"],
                    "source_identity": source["source_id"],
                    "source_version": source["version"],
                    "source_sha256": digest,
                    "effect": row["effect"],
                    "authority": "TianGong",
                    "access": row["availability"],
                }
            )
        return result

    @staticmethod
    def _baseline(case: Mapping[str, object], view: Mapping[str, object]) -> dict:
        selected = {"research-goal", *case["required_fact_ids"]}
        for fact_id, fact in view["facts"].items():
            if fact.get("classification") == "official" or fact.get("domain_kind") in {
                "official_source_material",
                "source_bound_research_material",
                "user_turn",
            }:
                selected.add(fact_id)
        return {
            "case_id": case["case_id"],
            "selected_fact_ids": sorted(selected),
            "selected_skill_ids": [],
            "selected_capability_ids": [],
            "query_mode": None,
            "topology": "single",
            "claim_ceiling": case["claim_ceiling"],
            "required_fact_ids": deepcopy(case["required_fact_ids"]),
            "forbidden_fact_ids": deepcopy(case["forbidden_fact_ids"]),
            "expected_skill_id": case["expected_skill_id"],
            "expected_query_mode": case["expected_query_mode"],
            "allowed_topologies": deepcopy(case["allowed_topologies"]),
            "source_version_correct": True,
            "authority_boundary_correct": True,
            "authoritative_path_complete": True,
            "answer_contract_valid": case["expected_skill_id"] is None,
            "conclusion_boundary_correct": True,
            "cross_project_leakage_count": sum(
                1
                for fact_id in selected
                if isinstance(view["facts"].get(fact_id), Mapping)
                and view["facts"][fact_id].get("owner_project_id")
                not in {None, view["project_id"]}
            ),
            "direct_candidate_promotion_count": 0,
            "case_specific_routing_count": 0,
            "deterministic_replay": True,
            "dependency_checks": [
                False for _ in case.get("dependency_expectations") or []
            ],
            "citation_checks": [
                False for _ in case.get("citation_expectations") or []
            ],
        }

    @staticmethod
    def _candidate(
        case: Mapping[str, object],
        view: Mapping[str, object],
        plan: Mapping[str, object],
        *,
        replay: Mapping[str, object],
    ) -> dict:
        selected = set(plan["selected_fact_ids"])
        required = set(case["required_fact_ids"])
        forbidden = set(case["forbidden_fact_ids"])
        expected_skill = case["expected_skill_id"]
        skill_ids = list(plan["selected_skill_ids"])
        source_correct = all(
            isinstance(view["facts"][fact_id].get("source"), Mapping)
            and view["facts"][fact_id]["source"].get("version") == "fixture-v1"
            and isinstance(view["facts"][fact_id]["source"].get("content_sha256"), str)
            for fact_id in required.intersection(selected)
        )
        authoritative_complete = all(
            (
                view["facts"][fact_id]["classification"]
                in {"missing", "candidate"}
            )
            or "project:fact:" + fact_id in set(plan["selected_node_ids"])
            for fact_id in required
        )
        dependency_checks = []
        for expectation in case.get("dependency_expectations") or []:
            fact_id = expectation["fact_id"]
            role = expectation["required_role"]
            fact = view["facts"].get(fact_id)
            dependency_checks.append(
                fact_id in selected
                and isinstance(fact, Mapping)
                and (fact.get("semantic_role") or fact.get("domain_kind")) == role
                and (
                    f"evidence_role:{role}" in set(plan["missing_dependencies"])
                    or any(
                        path.get("required_role") == role
                        and f"project:fact:{fact_id}" in path.get("node_ids", [])
                        for path in plan["graph_paths"]
                    )
                )
            )
        citation_checks = []
        for expectation in case.get("citation_expectations") or []:
            fact_id = expectation["fact_id"]
            fact = view["facts"].get(fact_id)
            source = fact.get("source") if isinstance(fact, Mapping) else None
            citation_checks.append(
                fact_id in selected
                and isinstance(source, Mapping)
                and source.get("version") == expectation["source_version"]
                and source.get("content_sha256")
                == expectation["source_content_sha256"]
            )
        return {
            "case_id": case["case_id"],
            "selected_fact_ids": sorted(selected),
            "selected_skill_ids": skill_ids,
            "selected_capability_ids": deepcopy(plan["selected_capability_ids"]),
            "query_mode": plan["query_mode"],
            "topology": plan["work_topology"]["topology"],
            "claim_ceiling": plan["expected_state_change"]["claim_ceiling"],
            "required_fact_ids": sorted(required),
            "forbidden_fact_ids": sorted(forbidden),
            "expected_skill_id": expected_skill,
            "expected_query_mode": case["expected_query_mode"],
            "allowed_topologies": deepcopy(case["allowed_topologies"]),
            "source_version_correct": source_correct,
            "authority_boundary_correct": (
                plan["authority_boundary"]["project_truth"] == "project_record_only"
                and plan["authority_boundary"]["graph_is_projection"] is True
                and plan["authority_boundary"]["model_tool_skill_outputs"]
                == "candidate_only"
            ),
            "authoritative_path_complete": authoritative_complete,
            "answer_contract_valid": (
                required.issubset(selected)
                and (
                    skill_ids == [expected_skill]
                    if expected_skill is not None
                    else skill_ids == []
                )
            ),
            "conclusion_boundary_correct": (
                plan["expected_state_change"]["claim_ceiling"]
                == case["claim_ceiling"]
            ),
            "cross_project_leakage_count": sum(
                1
                for fact_id in selected
                if fact_id not in view["facts"]
                or (
                    view["facts"][fact_id].get("owner_project_id")
                    not in {None, view["project_id"]}
                )
            ),
            "direct_candidate_promotion_count": 0,
            "case_specific_routing_count": 0,
            "deterministic_replay": plan == replay,
            "dependency_checks": dependency_checks,
            "citation_checks": citation_checks,
            "forbidden_selected_count": len(selected.intersection(forbidden)),
            "plan_sha256": plan["plan_sha256"],
        }

    @staticmethod
    def _metrics(rows: list[dict], *, candidate: bool) -> dict:
        required_total = sum(len(row["required_fact_ids"]) for row in rows)
        required_selected = sum(
            len(set(row["required_fact_ids"]).intersection(row["selected_fact_ids"]))
            for row in rows
        )
        selected_total = sum(len(row["selected_fact_ids"]) for row in rows)
        relevant_selected = sum(
            len(
                set(row["selected_fact_ids"]).intersection(
                    {"research-goal", *row["required_fact_ids"]}
                )
            )
            for row in rows
        )
        forbidden_selected = sum(
            len(set(row["selected_fact_ids"]).intersection(row["forbidden_fact_ids"]))
            for row in rows
        )
        expected_skill_rows = [row for row in rows if row["expected_skill_id"] is not None]
        near_miss_rows = [row for row in rows if row["expected_skill_id"] is None]
        skill_correct = sum(
            row["selected_skill_ids"] == [row["expected_skill_id"]]
            for row in expected_skill_rows
        )
        near_miss_correct = sum(row["selected_skill_ids"] == [] for row in near_miss_rows)
        unnecessary_capabilities = sum(len(row["selected_capability_ids"]) for row in rows)
        dependency_checks = [
            passed for row in rows for passed in row["dependency_checks"]
        ]
        citation_checks = [
            passed for row in rows for passed in row["citation_checks"]
        ]
        return {
            "required_evidence_recall": round(required_selected / required_total, 6),
            "dependency_coverage": round(
                sum(dependency_checks) / len(dependency_checks), 6
            ),
            "citation_correctness": round(
                sum(citation_checks) / len(citation_checks), 6
            ),
            "context_precision": round(relevant_selected / selected_total, 6),
            "irrelevant_token_ratio": round(forbidden_selected / selected_total, 6),
            "authoritative_path_coverage": round(
                sum(row["authoritative_path_complete"] for row in rows) / len(rows), 6
            ),
            "source_version_correctness": round(
                sum(row["source_version_correct"] for row in rows) / len(rows), 6
            ),
            "skill_routing_accuracy": round(
                skill_correct / len(expected_skill_rows), 6
            ),
            "near_miss_rejection_accuracy": round(
                near_miss_correct / len(near_miss_rows), 6
            ),
            "query_mode_accuracy": round(
                sum(row["query_mode"] == row["expected_query_mode"] for row in rows)
                / len(rows),
                6,
            ),
            "topology_accuracy": round(
                sum(row["topology"] in row["allowed_topologies"] for row in rows)
                / len(rows),
                6,
            ),
            "unnecessary_tool_selection_rate": round(
                unnecessary_capabilities / len(rows), 6
            ),
            "answer_contract_validity": round(
                sum(row["answer_contract_valid"] for row in rows) / len(rows), 6
            ),
            "conclusion_boundary_compliance": round(
                sum(row["conclusion_boundary_correct"] for row in rows) / len(rows),
                6,
            ),
            "authority_boundary_correctness": round(
                sum(row["authority_boundary_correct"] for row in rows) / len(rows),
                6,
            ),
            "cross_project_leakage_count": sum(
                row["cross_project_leakage_count"] for row in rows
            ),
            "direct_candidate_promotion_count": sum(
                row["direct_candidate_promotion_count"] for row in rows
            ),
            "case_specific_routing_count": sum(
                row["case_specific_routing_count"] for row in rows
            ),
            "deterministic_replay": all(row["deterministic_replay"] for row in rows),
            "selection_identity": CANDIDATE_IDENTITY if candidate else BASELINE_IDENTITY,
        }

    def _failed_gates(self, baseline: Mapping[str, object], candidate: Mapping[str, object]) -> list[str]:
        thresholds = self.fixture["thresholds"]
        gates = {
            "required_evidence_recall": (
                candidate["required_evidence_recall"]
                >= max(
                    baseline["required_evidence_recall"],
                    thresholds["minimum_required_evidence_recall"],
                )
            ),
            "dependency_coverage": (
                candidate["dependency_coverage"]
                >= thresholds["minimum_dependency_coverage"]
            ),
            "citation_correctness": (
                candidate["citation_correctness"]
                >= thresholds["minimum_citation_correctness"]
            ),
            "context_precision_gain": (
                candidate["context_precision"] - baseline["context_precision"]
                >= thresholds["minimum_context_precision_gain"]
            ),
            "skill_routing_accuracy": (
                candidate["skill_routing_accuracy"]
                >= thresholds["minimum_skill_routing_accuracy"]
            ),
            "query_mode_accuracy": (
                candidate["query_mode_accuracy"]
                >= thresholds["minimum_query_mode_accuracy"]
            ),
            "topology_accuracy": (
                candidate["topology_accuracy"]
                >= thresholds["minimum_topology_accuracy"]
            ),
            "authority_boundary": candidate["authority_boundary_correctness"] == 1.0,
            "source_version": candidate["source_version_correctness"] == 1.0,
            "authoritative_path": candidate["authoritative_path_coverage"] == 1.0,
            "answer_contract": candidate["answer_contract_validity"] == 1.0,
            "conclusion_boundary": candidate["conclusion_boundary_compliance"] == 1.0,
            "cross_project_isolation": (
                candidate["cross_project_leakage_count"]
                <= thresholds["maximum_cross_project_leakage_count"]
            ),
            "candidate_not_promoted": (
                candidate["direct_candidate_promotion_count"]
                <= thresholds["maximum_direct_candidate_promotion_count"]
            ),
            "unnecessary_tools": (
                candidate["unnecessary_tool_selection_rate"]
                <= thresholds["maximum_unnecessary_tool_selection_rate"]
            ),
            "case_neutral_routing": candidate["case_specific_routing_count"] == 0,
            "deterministic_replay": candidate["deterministic_replay"] is True,
        }
        return sorted(key for key, passed in gates.items() if not passed)

    def _case_route_hits(self) -> int:
        production_sources = (
            "lca_research_kernel/semantic_context.py",
            "lca_research_kernel/semantic_retrieval.py",
            "lca_research_kernel/research_skills.py",
            "lca_research_kernel/work_topology.py",
        )
        text = "\n".join(
            (self.root / relative).read_text(encoding="utf-8")
            for relative in production_sources
        )
        return sum(str(case["case_id"]) in text for case in self.fixture["cases"])
