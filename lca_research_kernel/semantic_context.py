"""Deep facade for graph-guided context, Skill, and work planning."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Mapping, Sequence

from .answer_obligations import GovernedLCAKnowledge
from .research_skills import ResearchSkillRegistry
from .semantic_graph import canonical_sha256
from .semantic_graph_projection import SemanticGraphProjector
from .semantic_retrieval import ProblemSignatureCompiler, SemanticRetriever
from .work_topology import WorkTopologyPlanner
from .material_sources import build_material_use_decision
from .material_sources import material_revision_context_eligibility
from .domain_rules import DomainRuleResolver


PLAN_CONTRACT = "lca_research_kernel.semantic_context_plan/v2"
PROGRAM_CLOSURE_CONTRACT = (
    "lca_research_kernel.research_program_semantic_closure/v2"
)

_PROGRAM_ROLE_TARGETS = (
    (
        frozenset({"provider_relation", "technosphere_exchange"}),
        "provider_completeness_report",
        ["Exchange", "Process", "Provider"],
    ),
    (
        frozenset({"functional_unit", "reference_flow", "system_boundary"}),
        "research_scope_contract",
        ["FunctionalUnit", "ProductSystem", "SystemBoundary"],
    ),
    (
        frozenset({"dataset_identity", "dataset_version", "source_identity"}),
        "dataset_identity_confirmation",
        ["Dataset", "DatasetVersion"],
    ),
    (
        frozenset({"unit_equivalence", "flow_identity", "flow_property"}),
        "unit_flow_validation",
        ["Flow", "FlowProperty", "Unit"],
    ),
    (
        frozenset({"characterization_factor", "lcia_method"}),
        "lcia_calculation_result",
        ["ImpactCategory", "LCIAMethod"],
    ),
    (
        frozenset({"contribution_result", "hotspot", "sensitivity_result"}),
        "result_interpretation",
        ["Calculation", "Result"],
    ),
    (
        frozenset({"equivalent_functional_unit", "comparative_result"}),
        "comparative_claim_assessment",
        ["FunctionalUnit", "Result"],
    ),
    (
        frozenset({"claim_evidence_binding", "source_material"}),
        "evidence_conclusion_audit",
        ["Claim", "EvidenceSpan", "Source"],
    ),
)
_PROGRAM_OPERATION_TARGETS = {
    "extract": ("evidence_conclusion_audit", ["Claim", "EvidenceSpan", "Source"]),
    "explain": ("evidence_conclusion_audit", ["Claim", "EvidenceSpan", "Source"]),
    "apply": ("research_scope_contract", ["FunctionalUnit", "SystemBoundary"]),
    "compare": ("comparative_claim_assessment", ["FunctionalUnit", "Result"]),
    "calculate": ("lcia_calculation_result", ["Calculation", "LCIAMethod"]),
    "interpret": ("result_interpretation", ["Calculation", "Result"]),
    "audit": ("evidence_conclusion_audit", ["Claim", "EvidenceSpan"]),
    "research": ("data_gap_resolution", ["Assumption", "MissingDatum"]),
}

_KNOWLEDGE_ROLE_OBLIGATIONS = (
    (frozenset({"lca_concept"}), "concept_meaning"),
    (frozenset({"exchange", "flow", "process"}), "lca_relationship"),
    (frozenset({"amount", "flow", "unit"}), "source_value_explanation"),
    (frozenset({"transformation"}), "calculation_explanation"),
    (
        frozenset({"illustrative_application_boundary", "lca_relationship"}),
        "illustrative_application",
    ),
    (frozenset({"functional_unit", "system_boundary"}), "comparison_basis"),
    (frozenset({"evidence_limitation"}), "uncertainty_and_limit"),
)


def _read_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unavailable or invalid") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ResearchSemanticContext:
    """Plan attributable context without executing or accepting research."""

    def __init__(
        self,
        *,
        root: Path,
        source_lock: Mapping[str, object],
        capability_catalog: Mapping[str, object],
        skill_registry: ResearchSkillRegistry,
        governed_knowledge: GovernedLCAKnowledge,
        domain_rules: Sequence[Mapping[str, object]] = (),
        domain_rule_resolver: DomainRuleResolver | None = None,
        source_lock_sha256: str,
        capability_catalog_sha256: str,
        skill_registry_sha256: str,
        topology_planner: WorkTopologyPlanner = None,
    ):
        self.root = Path(root).resolve()
        self.source_lock = deepcopy(dict(source_lock))
        self.capability_catalog = deepcopy(dict(capability_catalog))
        self.skill_registry = skill_registry
        self.governed_knowledge = governed_knowledge
        self.governed_knowledge_registry_sha256 = (
            governed_knowledge.registry_sha256
        )
        self.domain_rule_resolver = domain_rule_resolver
        self.domain_rules = tuple(
            deepcopy(
                list(
                    domain_rule_resolver.rules
                    if domain_rule_resolver is not None
                    else domain_rules
                )
            )
        )
        self.domain_rule_registry_sha256 = (
            domain_rule_resolver.registry_sha256
            if domain_rule_resolver is not None
            else None
        )
        self.source_lock_sha256 = source_lock_sha256
        self.capability_catalog_sha256 = capability_catalog_sha256
        self.skill_registry_sha256 = skill_registry_sha256
        self.signature_compiler = ProblemSignatureCompiler()
        self.retriever = SemanticRetriever()
        self.topology_planner = topology_planner or WorkTopologyPlanner()

    @classmethod
    def from_files(
        cls,
        root: Path,
        *,
        source_lock_path: Path,
        capability_catalog_path: Path,
        skill_registry_path: Path,
        domain_rules: Sequence[Mapping[str, object]] = (),
        domain_rule_registry_path: Path | None = None,
    ) -> "ResearchSemanticContext":
        runtime_root = Path(root).resolve()
        source_path = Path(source_lock_path).resolve()
        catalog_path = Path(capability_catalog_path).resolve()
        registry_path = Path(skill_registry_path).resolve()
        for path in (source_path, catalog_path, registry_path):
            try:
                path.relative_to(runtime_root)
            except ValueError as error:
                raise ValueError("semantic context source escaped repository") from error
        registry = ResearchSkillRegistry.load(
            runtime_root,
            registry_path,
            capability_catalog_path=catalog_path,
            source_lock_path=source_path,
        )
        governed_knowledge = GovernedLCAKnowledge.load(runtime_root)
        rule_resolver = (
            DomainRuleResolver.load(
                runtime_root,
                registry_path=domain_rule_registry_path,
                source_lock_path=source_path,
            )
            if domain_rule_registry_path is not None
            else None
        )
        if rule_resolver is not None:
            rule_resolver.require(registry.required_domain_rule_ids())
        return cls(
            root=runtime_root,
            source_lock=_read_json(source_path, "semantic context source lock"),
            capability_catalog=_read_json(catalog_path, "semantic context capability catalog"),
            skill_registry=registry,
            governed_knowledge=governed_knowledge,
            domain_rules=domain_rules,
            domain_rule_resolver=rule_resolver,
            source_lock_sha256=_file_sha256(source_path),
            capability_catalog_sha256=_file_sha256(catalog_path),
            skill_registry_sha256=_file_sha256(registry_path),
        )

    def plan(
        self,
        *,
        project_view: Mapping[str, object],
        expected_state_change: Mapping[str, object],
        available_capabilities: Sequence[Mapping[str, object]],
        context_budget: int,
        work_budget: Mapping[str, object],
        generation: int,
        material_evidence_units: Sequence[Mapping[str, object]] = (),
        allowed_revision_ids: Sequence[str] | None = None,
    ) -> dict:
        if not isinstance(context_budget, int) or context_budget < 1:
            raise ValueError("semantic context token budget is invalid")
        normalized_work_budget = self._work_budget(work_budget)
        signature = self.signature_compiler.compile(
            project_view=project_view,
            expected_state_change=expected_state_change,
            generation=generation,
        )
        projector = SemanticGraphProjector(
            project_id=str(project_view.get("project_id")), generation=generation
        )
        graph = projector.project(
            project_view=project_view,
            source_lock=self.source_lock,
            capability_catalog=self.capability_catalog,
            domain_rules=self.domain_rules,
            skill_descriptors=self.skill_registry.projection_descriptors(),
            governed_knowledge_entries=(
                self.governed_knowledge.projection_entries()
            ),
            material_evidence_units=material_evidence_units,
        )
        retrieval = self.retriever.retrieve(graph, signature)
        required_knowledge_roles = expected_state_change.get(
            "required_knowledge_roles", []
        )
        if (
            not isinstance(required_knowledge_roles, list)
            or any(
                not isinstance(role, str) or not role
                for role in required_knowledge_roles
            )
            or len(required_knowledge_roles) != len(set(required_knowledge_roles))
        ):
            raise ValueError("semantic context required knowledge roles are invalid")
        required_knowledge_roles = sorted(required_knowledge_roles)
        knowledge_query = self.governed_knowledge.compile_query(
            cognitive_operation=self._knowledge_operation(signature),
            answer_obligations=self._knowledge_obligations(
                required_knowledge_roles
            ),
            required_roles=required_knowledge_roles,
            referent_refs=signature["exact_node_ids"],
            target_object_kinds=signature["target_object_kinds"],
            relation_kinds=[],
            query_mode=signature["query_mode"],
            claim_ceiling=signature["claim_ceiling"],
            applicability={
                "geographies": [],
                "times": [],
                "technologies": [],
                "methods": ["generic_lca"],
            },
        )
        knowledge_slice = self.governed_knowledge.resolve_slice(
            knowledge_query,
            response_language="en",
        )
        selected_knowledge_ids = list(
            knowledge_slice["selected_knowledge_ids"]
        )
        selected_knowledge = deepcopy(knowledge_slice["entries"])
        uncovered_knowledge_roles = list(
            knowledge_slice["uncovered_requirements"]["roles"]
        )
        skill_selection = self.skill_registry.select(signature)
        selected_skills = list(skill_selection["selected_skill_ids"])
        disclosures = []
        selected_rule_ids = set()
        required_capability_ids = expected_state_change.get(
            "required_capability_ids", []
        )
        if (
            not isinstance(required_capability_ids, list)
            or any(
                not isinstance(capability_id, str) or not capability_id
                for capability_id in required_capability_ids
            )
            or len(set(required_capability_ids)) != len(required_capability_ids)
        ):
            raise ValueError("semantic context required capability ids are invalid")
        requested_capability_ids = set(required_capability_ids)
        requested_resources = expected_state_change.get("required_skill_resources")
        if requested_resources is None:
            requested_resources = {}
        if not isinstance(requested_resources, Mapping) or any(
            not isinstance(skill_id, str)
            or not isinstance(resource_ids, list)
            or any(not isinstance(resource_id, str) or not resource_id for resource_id in resource_ids)
            for skill_id, resource_ids in requested_resources.items()
        ):
            raise ValueError("semantic context Skill resource request is invalid")
        unselected_resource_requirements = set(requested_resources) - set(
            selected_skills
        )
        if unselected_resource_requirements:
            raise ValueError(
                "semantic context Skill resource requirement is not causally selected"
            )
        for skill_id in selected_skills:
            contract = self.skill_registry.contract(skill_id)
            if skill_id in requested_resources:
                disclosure_level = "L3"
                resource_ids = list(requested_resources[skill_id])
            elif signature["missing_roles"] or signature["conflict_types"]:
                disclosure_level = "L2"
                resource_ids = None
            else:
                disclosure_level = "L1"
                resource_ids = None
            disclosure = self.skill_registry.disclose(
                skill_id,
                level=disclosure_level,
                resource_ids=resource_ids,
            )
            disclosure.pop("disclosure_sha256", None)
            descriptor = next(
                row
                for row in self.skill_registry.projection_descriptors()
                if row["skill_id"] == skill_id
            )
            disclosure["source_ref"] = descriptor["source_ref"]
            disclosure["source_sha256"] = descriptor["source_sha256"]
            disclosure["disclosure_sha256"] = canonical_sha256(disclosure)
            disclosures.append(disclosure)
            selected_rule_ids.update(contract["domain_rule_ids"])
            requested_capability_ids.update(contract["capability_ids"])
        available_by_id = {}
        for row in available_capabilities:
            if not isinstance(row, Mapping) or not isinstance(row.get("capability_id"), str):
                raise ValueError("semantic context capability descriptor is invalid")
            available_by_id[str(row["capability_id"])] = row
        domain_by_id = {}
        for row in self.domain_rules:
            if not isinstance(row, Mapping) or not isinstance(row.get("rule_id"), str):
                raise ValueError("semantic context domain rule is invalid")
            domain_by_id[str(row["rule_id"])] = row
        selected_fact_ids = self._selected_fact_ids(project_view, signature, retrieval)
        required_fact_ids = expected_state_change.get("required_fact_ids", [])
        if (
            not isinstance(required_fact_ids, list)
            or any(
                not isinstance(fact_id, str) or not fact_id
                for fact_id in required_fact_ids
            )
            or len(set(required_fact_ids)) != len(required_fact_ids)
        ):
            raise ValueError("semantic context required fact ids are invalid")
        facts = project_view.get("facts")
        if not isinstance(facts, Mapping):
            raise ValueError("semantic context Project Record facts are absent")
        absent_required_facts = set(required_fact_ids) - set(facts)
        if absent_required_facts:
            raise ValueError("semantic context required Project Record fact is absent")
        selected_fact_ids = sorted({*selected_fact_ids, *required_fact_ids})
        material_selection = self._material_selection(
            project_view,
            retrieval,
            graph=graph,
            execution_intent=self._execution_intent(
                project_view, expected_state_change
            ),
            allowed_revision_ids=allowed_revision_ids,
            material_evidence_units=material_evidence_units,
        )
        graph_paths = [self._enrich_path(graph, row) for row in retrieval["paths"]]
        selected_rules = sorted(selected_rule_ids.intersection(domain_by_id))
        selected_capabilities = sorted(requested_capability_ids.intersection(available_by_id))
        topology = self.topology_planner.plan(
            problem_signature=signature,
            selected_node_ids=retrieval["selected_node_ids"],
            selected_capability_ids=selected_capabilities,
            selected_skill_ids=selected_skills,
            work_budget=normalized_work_budget,
        )
        exclusions = self._fact_exclusions(project_view, selected_fact_ids)
        exclusions.extend(deepcopy(retrieval["exclusions"]))
        missing_dependencies = [
            *(f"evidence_role:{role}" for role in retrieval["uncovered_roles"]),
            *(
                f"knowledge_role:{role}"
                for role in uncovered_knowledge_roles
            ),
            *(
                f"domain_rule:{rule_id}"
                for rule_id in sorted(selected_rule_ids - set(domain_by_id))
            ),
            *(
                f"capability:{capability_id}"
                for capability_id in sorted(
                    requested_capability_ids - set(available_by_id)
                )
            ),
        ]
        accepted_required_roles = bool(
            signature["required_evidence_roles"]
        ) and set(signature["required_evidence_roles"]).issubset(
            set(signature["accepted_roles"])
        )
        next_route = (
            "advance-after-required-evidence-accepted"
            if accepted_required_roles
            else "execute-selected-skill-to-resolve-missing-dependencies"
            if selected_skills
            else "preserve-unresolved-method-boundary"
        )
        stop_condition = (
            "expected_state_change_completed"
            if accepted_required_roles
            else "required_roles_are_accepted_or_bounded"
        )
        plan = {
            "contract": PLAN_CONTRACT,
            "project_id": project_view.get("project_id"),
            "generation": generation,
            "project_state_ref": {
                "last_event_id": project_view.get("last_event_id"),
                "last_event_sha256": project_view.get("last_event_sha256"),
            },
            "source_bindings": {
                "tiangong_source_lock_sha256": self.source_lock_sha256,
                "capability_catalog_sha256": self.capability_catalog_sha256,
                "skill_registry_sha256": self.skill_registry_sha256,
                "governed_knowledge_registry_sha256": (
                    self.governed_knowledge_registry_sha256
                ),
                **(
                    {
                        "domain_rule_registry_sha256": (
                            self.domain_rule_registry_sha256
                        )
                    }
                    if self.domain_rule_registry_sha256 is not None
                    else {}
                ),
            },
            "graph_sha256": graph.graph_sha256,
            "problem_signature": signature,
            "retrieval_sha256": retrieval["retrieval_sha256"],
            "query_mode": retrieval["query_mode"],
            "graph_paths": graph_paths,
            "selected_node_ids": deepcopy(retrieval["selected_node_ids"]),
            "selected_fact_ids": selected_fact_ids,
            "selected_evidence_locator_ids": material_selection[
                "selected_evidence_locator_ids"
            ],
            "material_use_decision": material_selection["material_use_decision"],
            "selected_domain_rule_ids": selected_rules,
            "selected_capability_ids": selected_capabilities,
            "selected_skill_ids": selected_skills,
            "required_knowledge_roles": required_knowledge_roles,
            "domain_knowledge_query": knowledge_query,
            "domain_knowledge_slice": knowledge_slice,
            "selected_governed_knowledge_ids": selected_knowledge_ids,
            "selected_governed_knowledge": selected_knowledge,
            "skill_disclosures": disclosures,
            "uncovered_roles": deepcopy(retrieval["uncovered_roles"]),
            "missing_dependencies": sorted(missing_dependencies),
            "exclusions": self._deduplicate_exclusions(exclusions),
            "expected_state_change": {
                "desired_artifact": signature["desired_artifact"],
                "completion_predicates": deepcopy(signature["completion_predicates"]),
                "claim_ceiling": signature["claim_ceiling"],
            },
            "budgets": {
                "context_tokens": context_budget,
                **normalized_work_budget,
            },
            "work_topology": topology,
            "next_route": next_route,
            "stop_condition": stop_condition,
            "authority_boundary": {
                "project_truth": "project_record_only",
                "graph_is_projection": True,
                "model_tool_skill_outputs": "candidate_only",
            },
        }
        plan["plan_sha256"] = canonical_sha256(plan)
        return plan

    @staticmethod
    def _knowledge_operation(signature: Mapping[str, object]) -> str:
        requirements = signature.get("requirements")
        requirements = requirements if isinstance(requirements, Mapping) else {}
        artifact = str(signature.get("desired_artifact") or "")
        if requirements.get("calculation") is True:
            return "calculate"
        if requirements.get("comparison") is True:
            return "compare"
        if "interpretation" in artifact:
            return "interpret"
        if "audit" in artifact or "evidence_conclusion" in artifact:
            return "audit"
        if "gap" in artifact or requirements.get("unknown_target") is True:
            return "research"
        return "explain"

    @staticmethod
    def _knowledge_obligations(required_roles: Sequence[str]) -> list[str]:
        role_set = set(required_roles)
        obligations = {
            obligation
            for trigger_roles, obligation in _KNOWLEDGE_ROLE_OBLIGATIONS
            if role_set.intersection(trigger_roles)
        }
        return sorted(obligations)

    def compile_program_semantic_closure(
        self,
        *,
        project_view: Mapping[str, object],
        turn_frame: Mapping[str, object],
        material_use_decision: Mapping[str, object],
        available_capabilities: Sequence[Mapping[str, object]],
        selected_governed_knowledge_ids: Sequence[str],
        domain_knowledge_query: Mapping[str, object] | None = None,
        domain_knowledge_slice: Mapping[str, object] | None = None,
    ) -> dict:
        """Compile the method/rule closure for one accepted material program."""

        if self.domain_rule_resolver is None:
            raise ValueError("versioned domain rule resolver is unavailable")
        operation = turn_frame.get("cognitive_operation")
        if operation not in _PROGRAM_OPERATION_TARGETS:
            raise ValueError("research program cognitive operation is unsupported")
        expected = material_use_decision.get("expected_state_change")
        required_roles = (
            expected.get("required_evidence_roles")
            if isinstance(expected, Mapping)
            else None
        )
        if (
            not isinstance(required_roles, list)
            or any(not isinstance(role, str) or not role for role in required_roles)
            or len(required_roles) != len(set(required_roles))
        ):
            raise ValueError("research program evidence roles are invalid")
        role_set = set(required_roles)
        desired_artifact, target_kinds = _PROGRAM_OPERATION_TARGETS[operation]
        for candidate_roles, artifact, kinds in _PROGRAM_ROLE_TARGETS:
            if role_set.intersection(candidate_roles):
                desired_artifact, target_kinds = artifact, kinds
                break
        generation_source = project_view.get("last_event_sha256")
        if not isinstance(generation_source, str) or len(generation_source) < 8:
            raise ValueError("research program Project Record generation is absent")
        expected_change = {
            "contract": "lca_research_kernel.expected_state_change/v1",
            "desired_artifact": desired_artifact,
            "target_object_kinds": sorted(set(target_kinds)),
            "exact_node_ids": ["project:goal"],
            "required_evidence_roles": sorted(role_set),
            "calculation_required": operation == "calculate",
            "source_confirmation_required": bool(role_set),
            "comparison_required": operation == "compare",
            "global_structure_required": False,
            "unknown_target": False,
            "conflict_types": [],
            "claim_ceiling": str(
                turn_frame.get("claim_ceiling") or "source_bound_bounded"
            ),
            "independent_evidence_dimensions": [],
            "completion_predicates": [
                "answer_obligations_satisfied_or_explicitly_bounded"
            ],
        }
        signature = self.signature_compiler.compile(
            project_view=project_view,
            expected_state_change=expected_change,
            generation=int(generation_source[:8], 16),
        )
        selection = self.skill_registry.select(signature)
        selected_skill_ids = list(selection["selected_skill_ids"])
        required_rule_ids = sorted(
            {
                rule_id
                for skill_id in selected_skill_ids
                for rule_id in self.skill_registry.contract(skill_id)[
                    "domain_rule_ids"
                ]
            }
        )
        rule_closure = self.domain_rule_resolver.require(required_rule_ids)
        required_capability_ids = sorted(
            {
                capability_id
                for skill_id in selected_skill_ids
                for capability_id in self.skill_registry.contract(skill_id)[
                    "capability_ids"
                ]
            }
        )
        available_ids = {
            str(row["capability_id"])
            for row in available_capabilities
            if isinstance(row, Mapping)
            and isinstance(row.get("capability_id"), str)
            and row["capability_id"]
        }
        selected_capability_ids = sorted(
            set(required_capability_ids).intersection(available_ids)
        )
        if (domain_knowledge_query is None) != (domain_knowledge_slice is None):
            raise ValueError("research program domain knowledge binding is incomplete")
        if domain_knowledge_query is None:
            domain_knowledge_slice = self.resolve_program_knowledge_slice(
                turn_frame,
                response_language="en",
            )
            domain_knowledge_query = domain_knowledge_slice["query"]
            domain_knowledge_slice = domain_knowledge_slice["slice"]
        else:
            domain_knowledge_query = self.governed_knowledge.validate_query(
                domain_knowledge_query
            )
            replayed_slice = self.governed_knowledge.resolve_slice(
                domain_knowledge_query,
                response_language="en",
            )
            if replayed_slice != domain_knowledge_slice:
                raise ValueError("research program domain knowledge slice drifted")
            domain_knowledge_slice = replayed_slice
        knowledge_ids = sorted(
            set(
                self._string_sequence(
                    selected_governed_knowledge_ids,
                    "governed knowledge identities",
                )
            )
        )
        if knowledge_ids != domain_knowledge_slice["selected_knowledge_ids"]:
            raise ValueError("research program governed knowledge selection drifted")
        knowledge_binding = {
            "contract": (
                "lca_research_kernel.research_program_knowledge_binding/v1"
            ),
            "query_sha256": domain_knowledge_query["query_sha256"],
            "slice_sha256": domain_knowledge_slice["slice_sha256"],
            "selected_knowledge_ids": knowledge_ids,
            "covered_requirements": deepcopy(
                domain_knowledge_slice["covered_requirements"]
            ),
            "uncovered_requirements": deepcopy(
                domain_knowledge_slice["uncovered_requirements"]
            ),
            "claim_ceiling_effect": domain_knowledge_slice[
                "claim_ceiling_effect"
            ],
        }
        closure = {
            "contract": PROGRAM_CLOSURE_CONTRACT,
            "source_bindings": {
                "capability_catalog_sha256": self.capability_catalog_sha256,
                "skill_registry_sha256": self.skill_registry_sha256,
                "governed_knowledge_registry_sha256": (
                    self.governed_knowledge_registry_sha256
                ),
                "domain_rule_registry_sha256": (
                    self.domain_rule_registry_sha256
                ),
            },
            "selected_skill_ids": selected_skill_ids,
            "required_domain_rule_ids": required_rule_ids,
            "selected_domain_rule_ids": deepcopy(
                rule_closure["selected_domain_rule_ids"]
            ),
            "selected_domain_rules": deepcopy(
                rule_closure["selected_domain_rules"]
            ),
            "required_capability_ids": required_capability_ids,
            "selected_capability_ids": selected_capability_ids,
            "selected_governed_knowledge_ids": knowledge_ids,
            "domain_knowledge": knowledge_binding,
            "missing_dependencies": sorted(
                [
                    *(
                        "capability:" + capability_id
                        for capability_id in (
                            set(required_capability_ids)
                            - set(selected_capability_ids)
                        )
                    ),
                    *(
                        "knowledge_role:" + role
                        for role in domain_knowledge_slice[
                            "uncovered_requirements"
                        ]["roles"]
                    ),
                ]
            ),
        }
        closure["closure_sha256"] = canonical_sha256(closure)
        return closure

    @staticmethod
    def _string_sequence(value: object, label: str) -> list[str]:
        if (
            not isinstance(value, Sequence)
            or isinstance(value, (str, bytes))
            or any(not isinstance(item, str) or not item for item in value)
            or len(value) != len(set(value))
        ):
            raise ValueError(f"research program {label} are invalid")
        return list(value)

    def select_governed_knowledge(
        self,
        turn_frame: Mapping[str, object],
        *,
        response_language: str = "en",
    ) -> list[dict]:
        """Select the same source-bound knowledge used by graph planning."""

        if not isinstance(turn_frame.get("cognitive_operation"), str) or not isinstance(
            turn_frame.get("answer_obligations"), list
        ):
            return self.governed_knowledge.select(
                turn_frame,
                response_language=response_language,
            )
        return self.resolve_program_knowledge_slice(
            turn_frame,
            response_language=response_language,
        )["slice"]["entries"]

    def resolve_program_knowledge_slice(
        self,
        turn_frame: Mapping[str, object],
        *,
        response_language: str,
    ) -> dict:
        """Resolve one user-turn query without exposing index implementation."""

        return self.governed_knowledge.resolve_turn_slice(
            turn_frame,
            response_language=response_language,
        )

    @staticmethod
    def _material_selection(
        project_view: Mapping[str, object],
        retrieval: Mapping[str, object],
        *,
        graph: object,
        execution_intent: Mapping[str, object],
        allowed_revision_ids: Sequence[str],
        material_evidence_units: Sequence[Mapping[str, object]],
    ) -> dict:
        materials = project_view.get("materials") or {}
        revisions = project_view.get("material_revisions") or {}
        locators = project_view.get("evidence_locators") or {}
        if not all(isinstance(value, Mapping) for value in (materials, revisions, locators)):
            raise ValueError("semantic context material state is invalid")

        if allowed_revision_ids is None:
            requested_allowed = set()
        elif any(not isinstance(value, str) or not value for value in allowed_revision_ids):
            raise ValueError("semantic context allowed material revisions are invalid")
        else:
            requested_allowed = set(allowed_revision_ids)
        if allowed_revision_ids is not None and len(requested_allowed) != len(allowed_revision_ids):
            raise ValueError("semantic context allowed material revisions are duplicated")
        if material_evidence_units and allowed_revision_ids is None:
            raise ValueError("semantic context allowed material revisions are absent")
        effective_allowed_revision_ids = []
        revision_exclusions = []
        for revision_id, revision in sorted(revisions.items()):
            material = (
                materials.get(revision.get("material_id"))
                if isinstance(revision, Mapping)
                else None
            )
            reason = None
            if allowed_revision_ids is None:
                reason = "evidence_binding_not_resolved"
            elif revision_id not in requested_allowed:
                reason = "revision_not_allowed_by_request"
            elif not isinstance(material, Mapping):
                reason = "material_not_accepted"
            elif material.get("current_revision_id") != revision_id:
                reason = "revision_superseded"
            elif material.get("deleted_at") is not None:
                reason = "material_withdrawn"
            elif not material_revision_context_eligibility(
                project_view, str(revision_id)
            )[0]:
                reason = material_revision_context_eligibility(
                    project_view, str(revision_id)
                )[1]
            elif revision.get("processing_status") not in {"ready", "partial", "needs_user"}:
                reason = "material_not_ready_for_selection"
            if reason is None:
                effective_allowed_revision_ids.append(str(revision_id))
            else:
                revision_exclusions.append(
                    {"identity": f"material_revision:{revision_id}", "reason": reason}
                )

        selected_locator_ids = sorted(
            {
                str((node.get("attributes") or {}).get("locator_id"))
                for node_id in retrieval.get("selected_node_ids") or []
                if isinstance(node_id, str)
                and node_id.startswith("evidence:locator:")
                and isinstance((node := graph.node(node_id)), Mapping)
                and isinstance((node.get("attributes") or {}).get("locator_id"), str)
                and isinstance(
                    locators.get((node.get("attributes") or {}).get("locator_id")),
                    Mapping,
                )
                and locators[(node.get("attributes") or {}).get("locator_id")].get(
                    "revision_id"
                ) in set(effective_allowed_revision_ids)
            }
        )
        conflict_locator_ids = set(selected_locator_ids)
        for edge in graph.edges:
            if (
                edge.get("edge_kind") == "conflicts_with"
                and (
                    edge.get("source_id") in set(retrieval.get("selected_node_ids") or [])
                    or edge.get("target_id") in set(retrieval.get("selected_node_ids") or [])
                )
            ):
                for node_id in (edge.get("source_id"), edge.get("target_id")):
                    node = graph.node(str(node_id))
                    locator_id = (
                        (node.get("attributes") or {}).get("locator_id")
                        if isinstance(node, Mapping)
                        else None
                    )
                    locator = locators.get(locator_id) if isinstance(locator_id, str) else None
                    if (
                        isinstance(locator, Mapping)
                        and locator.get("revision_id")
                        in set(effective_allowed_revision_ids)
                    ):
                        conflict_locator_ids.add(locator_id)
        selected_locator_ids = sorted(conflict_locator_ids)
        path_reason = {}
        for path in retrieval.get("paths") or []:
            if not isinstance(path, Mapping):
                continue
            required_role = path.get("required_role")
            for node_id in path.get("node_ids") or []:
                if isinstance(node_id, str) and node_id.startswith(
                    "evidence:locator:"
                ):
                    node = graph.node(node_id)
                    exact_locator_id = (
                        (node.get("attributes") or {}).get("locator_id")
                        if isinstance(node, Mapping)
                        else None
                    )
                    if not isinstance(exact_locator_id, str):
                        continue
                    path_reason[exact_locator_id] = (
                        f"covers_required_role:{required_role}"
                        if isinstance(required_role, str) and required_role
                        else str(path.get("reason") or "selected_by_semantic_plan")
                    )
        locator_exclusions = []
        for locator_id, locator in sorted(locators.items()):
            if not isinstance(locator, Mapping):
                raise ValueError("semantic context evidence locator is invalid")
            if locator_id in selected_locator_ids:
                continue
            reason = (
                "revision_not_allowed"
                if locator.get("revision_id") not in set(effective_allowed_revision_ids)
                else "not_required_by_expected_state_change"
            )
            locator_exclusions.append(
                {"identity": f"evidence_locator:{locator_id}", "reason": reason}
            )

        selected_units_by_id = {
            str(binding.get("locator_id")): binding.get("evidence_unit")
            for binding in material_evidence_units
            if isinstance(binding, Mapping)
            and isinstance(binding.get("locator_id"), str)
        }
        graph_conflict_visible = any(
            edge.get("edge_kind") == "conflicts_with"
            and edge.get("source_id")
            in {
                node["node_id"]
                for node in graph.nodes
                if (node.get("attributes") or {}).get("locator_id")
                in set(selected_locator_ids)
            }
            and edge.get("target_id")
            in {
                node["node_id"]
                for node in graph.nodes
                if (node.get("attributes") or {}).get("locator_id")
                in set(selected_locator_ids)
            }
            for edge in graph.edges
        )
        conflict_visible = graph_conflict_visible or any(
            isinstance(selected_units_by_id.get(locator_id), Mapping)
            and (
                selected_units_by_id[locator_id].get("conflict_status")
                in {"conflicting", "unresolved_conflict"}
                or bool(selected_units_by_id[locator_id].get("conflicts_with_locator_ids"))
            )
            for locator_id in selected_locator_ids
        )
        bounded = conflict_visible or any(
            isinstance(revisions.get(revision_id), Mapping)
            and revisions[revision_id].get("processing_status") in {"partial", "needs_user"}
            for revision_id in effective_allowed_revision_ids
        )
        decision = build_material_use_decision(
            project_view=project_view,
            task_kind="semantic_material_selection",
            question={
                "objective": project_view.get("objective"),
                "execution_intent": deepcopy(dict(execution_intent)),
            },
            allowed_revision_ids=effective_allowed_revision_ids,
            selected_evidence_locator_ids=selected_locator_ids,
            selection_reasons={
                locator_id: path_reason.get(
                    locator_id, "selected_by_expected_state_change"
                )
                for locator_id in selected_locator_ids
            },
            exclusions=[*revision_exclusions, *locator_exclusions],
            context_mode="focused_retrieval",
            expected_state_change={
                "desired_artifact": retrieval.get("query_mode"),
                "required_evidence_roles": sorted(
                    {
                        str(role)
                        for role in [
                            *(retrieval.get("covered_roles") or []),
                            *(retrieval.get("uncovered_roles") or []),
                        ]
                        if isinstance(role, str) and role
                    }
                ),
            },
            claim_ceiling=(
                "conflicting_evidence_bounded"
                if conflict_visible
                else "calculation_structure_only"
                if bounded
                else "source_bound_bounded"
            ),
            locator_index=locators,
        )
        return {
            "selected_evidence_locator_ids": selected_locator_ids,
            "material_use_decision": decision,
        }

    @staticmethod
    def _execution_intent(
        project_view: Mapping[str, object],
        expected_state_change: Mapping[str, object],
    ) -> dict:
        """Stable replay identity for one consuming action, excluding generation."""

        facts = project_view.get("facts") or {}
        turn = facts.get("active-user-turn") if isinstance(facts, Mapping) else None
        turn_value = turn.get("value") if isinstance(turn, Mapping) else None
        turn_sha256 = (
            turn_value.get("turn_sha256")
            if isinstance(turn_value, Mapping)
            else None
        )
        action_type = expected_state_change.get("execution_action")
        if not isinstance(action_type, str) or not action_type:
            action_type = str(expected_state_change.get("desired_artifact") or "unknown")
        stable = {
            "contract": "lca_research_kernel.material_execution_intent/v1",
            "action_type": action_type,
            "turn_or_task_sha256": (
                turn_sha256
                if isinstance(turn_sha256, str) and len(turn_sha256) == 64
                else canonical_sha256(
                    {
                        "objective": project_view.get("objective"),
                        "research_goal": (
                            facts.get("research-goal")
                            if isinstance(facts, Mapping)
                            else None
                        ),
                    }
                )
            ),
            "action_sha256": canonical_sha256(
                {
                    "action_type": action_type,
                    "expected_state_change": {
                        key: deepcopy(value)
                        for key, value in expected_state_change.items()
                        if key not in {"selection_project_record_sha256"}
                    },
                }
            ),
        }
        return stable

    @staticmethod
    def _work_budget(value: Mapping[str, object]) -> dict:
        if not isinstance(value, Mapping):
            raise ValueError("semantic context work budget is invalid")
        result = {}
        for key in ("max_workers", "max_model_calls", "max_tool_calls"):
            item = value.get(key)
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                raise ValueError(f"semantic context work budget {key} is invalid")
            result[key] = item
        return result

    @staticmethod
    def _selected_fact_ids(
        project_view: Mapping[str, object],
        signature: Mapping[str, object],
        retrieval: Mapping[str, object],
    ) -> list[str]:
        roles = set(signature["required_evidence_roles"]) | set(signature["accepted_roles"])
        selected = set()
        facts = project_view.get("facts") or {}
        if not isinstance(facts, Mapping):
            raise ValueError("semantic context Project Record facts are invalid")
        for fact_id, fact in facts.items():
            if not isinstance(fact, Mapping):
                raise ValueError("semantic context Project Record fact is invalid")
            role = ProblemSignatureCompiler._fact_role(fact)
            classification = fact.get("classification")
            domain_kind = fact.get("domain_kind")
            if (
                fact_id == "research-goal"
                or (classification == "missing" and role in roles)
                or (
                    domain_kind
                    in {
                        "source_bound_research_material",
                        "user_turn",
                    }
                    and role in roles
                )
            ):
                selected.add(str(fact_id))
        for node_id in retrieval["selected_node_ids"]:
            prefix = "project:fact:"
            if isinstance(node_id, str) and node_id.startswith(prefix):
                selected.add(node_id[len(prefix):])
            missing_prefix = "evidence:missing:"
            if isinstance(node_id, str) and node_id.startswith(missing_prefix):
                selected.add(node_id[len(missing_prefix):])
        return sorted(selected)

    @staticmethod
    def _enrich_path(graph, path: Mapping[str, object]) -> dict:
        sources = []
        for node_id in path["node_ids"]:
            node = graph.node(node_id)
            if node is None:
                raise ValueError("semantic context retrieval path node is absent")
            sources.append(
                {
                    "node_id": node_id,
                    "node_kind": node["node_kind"],
                    "source_ref": node["source_ref"],
                    "source_sha256": node["source_sha256"],
                    "source_version": node["source_version"],
                    "authority_class": node["authority_class"],
                    "classification": node["classification"],
                }
            )
        return {**deepcopy(dict(path)), "sources": sources}

    @staticmethod
    def _fact_exclusions(
        project_view: Mapping[str, object], selected_fact_ids: list[str]
    ) -> list[dict]:
        facts = project_view.get("facts") or {}
        return [
            {
                "identity": f"project_fact:{fact_id}",
                "reason": "not_on_required_semantic_path",
            }
            for fact_id in sorted(facts)
            if fact_id not in set(selected_fact_ids)
        ]

    @staticmethod
    def _deduplicate_exclusions(rows: list[dict]) -> list[dict]:
        unique = {
            (str(row.get("identity")), str(row.get("reason"))): deepcopy(row)
            for row in rows
        }
        return [unique[key] for key in sorted(unique)]
