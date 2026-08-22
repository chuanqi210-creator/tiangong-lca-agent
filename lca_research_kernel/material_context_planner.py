"""State-driven, bounded material context planning for Product Interface."""

from __future__ import annotations

from copy import deepcopy
from typing import Mapping, Sequence

from .content_identity import canonical_sha256
from .material_sources import (
    material_revision_context_eligibility,
    validate_material_use_decision,
)


class MaterialContextPlanner:
    """Hide evidence sampling and scope rules behind one planning interface."""

    def plan(
        self,
        *,
        request: Mapping[str, object],
        bundles: Sequence[Mapping[str, object]],
        locator_acceptance: Mapping[str, Mapping[str, object]],
        project_view: Mapping[str, object],
    ) -> dict:
        locator_index = dict(locator_acceptance)
        binding_by_locator = {
            str(binding["locator_id"]): binding
            for bundle in bundles
            for binding in bundle["evidence_unit_bindings"]
        }
        required_roles = self._required_roles(project_view)
        selected = sorted(
            locator_id
            for locator_id, binding in binding_by_locator.items()
            if required_roles.intersection(
                self._evidence_roles(binding["evidence_unit"])
            )
        )
        representative_plan: dict[str, object] | None = None
        if required_roles and not selected:
            selected = []
        elif not required_roles:
            representative_plan = self._representative_selection(
                binding_by_locator
            )
            selected = list(representative_plan["selected_locator_ids"])

        requested_revision_ids = {
            str(bundle["material_revision"]["revision_id"])
            for bundle in bundles
        }
        revisions = project_view.get("material_revisions") or {}
        materials = project_view.get("materials") or {}
        if not isinstance(revisions, Mapping) or not isinstance(materials, Mapping):
            raise ValueError("material selection Project Record state is invalid")
        allowed_revision_ids = []
        for revision_id in sorted(requested_revision_ids):
            revision = revisions.get(revision_id)
            material = (
                materials.get(revision.get("material_id"))
                if isinstance(revision, Mapping)
                else None
            )
            if (
                isinstance(material, Mapping)
                and material.get("current_revision_id") == revision_id
                and material.get("deleted_at") is None
                and material_revision_context_eligibility(
                    project_view, revision_id
                )[0]
            ):
                allowed_revision_ids.append(revision_id)
        allowed_set = set(allowed_revision_ids)
        locator_index = {
            locator_id: locator
            for locator_id, locator in locator_index.items()
            if locator.get("revision_id") in allowed_set
        }
        binding_by_locator = {
            locator_id: binding
            for locator_id, binding in binding_by_locator.items()
            if locator_id in locator_index
        }
        bounded = any(
            bundle["material_revision"]["processing_status"]
            in {"partial", "needs_user"}
            for bundle in bundles
        )
        return validate_material_use_decision(
            {
                "contract": "lca_research_kernel.material_use_decision/v1",
                "task_id": "material-answer-"
                + canonical_sha256(
                    {
                        "research_id": request["research_id"],
                        "objective": request["objective"],
                        "revision_ids": sorted(requested_revision_ids),
                    }
                )[:24],
                "allowed_revision_ids": allowed_revision_ids,
                "selected_evidence_locator_ids": selected,
                "selection_reasons": {
                    locator_id: (
                        "covers_required_role:"
                        + ",".join(
                            sorted(
                                required_roles.intersection(
                                    self._evidence_roles(
                                        binding_by_locator[locator_id][
                                            "evidence_unit"
                                        ]
                                    )
                                )
                            )
                        )
                        if required_roles
                        else "representative_structural_coverage"
                    )
                    for locator_id in selected
                },
                "exclusions": [
                    {
                        "identity": "evidence_locator:" + locator_id,
                        "reason": (
                            "deferred_by_representative_sampling_budget"
                            if representative_plan is not None
                            else "not_required_by_expected_state_change"
                        ),
                    }
                    for locator_id in sorted(set(locator_index) - set(selected))
                ],
                "context_mode": "focused_retrieval",
                "expected_state_change": {
                    "desired_artifact": "source_bound_material_answer",
                    "required_evidence_roles": sorted(required_roles),
                    "selection_project_record_sha256": project_view[
                        "last_event_sha256"
                    ],
                    "representative_sampling": deepcopy(representative_plan),
                },
                "claim_ceiling": (
                    "calculation_structure_only"
                    if bounded
                    else "source_bounded_interpretation"
                ),
            },
            locator_index=locator_index,
        )

    @staticmethod
    def _required_roles(project_view: Mapping[str, object]) -> set[str]:
        facts = project_view.get("facts")
        if not isinstance(facts, Mapping):
            raise ValueError("material selection Project Record facts are invalid")
        required_roles = set()
        for fact in facts.values():
            if not isinstance(fact, Mapping):
                continue
            role = fact.get("semantic_role")
            if not isinstance(role, str) or not role:
                continue
            if (
                fact.get("domain_kind") == "research_requirement"
                or fact.get("classification") == "missing"
            ):
                required_roles.add(role)
        return required_roles

    @classmethod
    def _representative_selection(
        cls,
        binding_by_locator: Mapping[str, Mapping[str, object]],
        *,
        max_selected: int = 12,
        representatives_per_shape: int = 3,
        max_must_include: int = 128,
    ) -> dict[str, object]:
        if (
            max_selected < 1
            or representatives_per_shape < 1
            or max_must_include < max_selected
        ):
            raise ValueError("representative material selection budget is invalid")
        groups: dict[
            tuple[str, tuple[str, ...], str, str],
            list[tuple[str, str]],
        ] = {}
        must_include: set[str] = set()
        for locator_id, binding in binding_by_locator.items():
            evidence_unit = binding.get("evidence_unit")
            if not isinstance(evidence_unit, Mapping):
                raise ValueError("material evidence unit is invalid")
            source_span = evidence_unit.get("source_span")
            roles = cls._evidence_roles(evidence_unit)
            signature = (
                str(evidence_unit.get("kind") or "unknown"),
                tuple(sorted(roles)),
                str(
                    source_span.get("kind")
                    if isinstance(source_span, Mapping)
                    else "unknown"
                ),
                cls._source_family(source_span),
            )
            content_hash = str(binding.get("content_hash") or "")
            groups.setdefault(signature, []).append((content_hash, locator_id))
            if cls._requires_inclusion(evidence_unit):
                must_include.add(locator_id)

        if len(must_include) > max_must_include:
            raise ValueError("material risk evidence exceeds bounded context")

        representatives: list[
            tuple[tuple[str, tuple[str, ...], str, str], str]
        ] = []
        for signature, hashed_locator_ids in sorted(groups.items()):
            locator_ids = [
                locator_id for _, locator_id in sorted(hashed_locator_ids)
            ]
            if len(locator_ids) <= representatives_per_shape:
                group_representatives = locator_ids
            else:
                group_representatives = [
                    locator_ids[index]
                    for index in cls._evenly_spaced_indices(
                        len(locator_ids), representatives_per_shape
                    )
                ]
            representatives.extend(
                (signature, locator_id)
                for locator_id in group_representatives
            )

        ordered_candidates = sorted(must_include) + [
            locator_id
            for _, locator_id in representatives
            if locator_id not in must_include
        ]
        ordered_candidates = list(dict.fromkeys(ordered_candidates))
        if len(ordered_candidates) > max_selected:
            mandatory = [
                locator_id
                for locator_id in ordered_candidates
                if locator_id in must_include
            ]
            remaining_budget = max(0, max_selected - len(mandatory))
            optional = [
                locator_id
                for locator_id in ordered_candidates
                if locator_id not in must_include
            ]
            optional = (
                [
                    optional[index]
                    for index in cls._evenly_spaced_indices(
                        len(optional), remaining_budget
                    )
                ]
                if remaining_budget
                else []
            )
            selected = mandatory + optional
        else:
            selected = ordered_candidates
        return {
            "strategy": "content_stable_structural_representatives/v1",
            "selected_locator_ids": sorted(selected),
            "eligible_locator_count": len(binding_by_locator),
            "selected_locator_count": len(selected),
            "structural_group_count": len(groups),
            "must_include_count": len(must_include),
            "ordinary_selection_budget": max_selected,
            "ordinary_budget_overridden_by_risk": len(must_include) > max_selected,
            "sampling_applied": len(selected) < len(binding_by_locator),
            "limitation": (
                "representative_sample_not_exhaustive"
                if len(selected) < len(binding_by_locator)
                else "none"
            ),
        }

    @staticmethod
    def _evenly_spaced_indices(size: int, count: int) -> list[int]:
        if size < 1 or count < 1:
            return []
        if count >= size:
            return list(range(size))
        if count == 1:
            return [0]
        return [
            round(index * (size - 1) / (count - 1))
            for index in range(count)
        ]

    @staticmethod
    def _source_family(source_span: object) -> str:
        if not isinstance(source_span, Mapping):
            return "unknown"
        locator = source_span.get("locator")
        kind = source_span.get("kind")
        if kind == "json_pointer" and isinstance(locator, Mapping):
            pointer = locator.get("pointer")
            if isinstance(pointer, str):
                tokens = [
                    "*" if token.isdigit() else token
                    for token in pointer.split("/")
                    if token
                ]
                return "/".join(tokens[:4]) or "/"
        if kind in {"table_range", "spreadsheet_range"} and isinstance(
            locator, Mapping
        ):
            return str(locator.get("sheet") or locator.get("worksheet") or "table")
        if isinstance(locator, Mapping):
            return ",".join(sorted(str(key) for key in locator))
        return str(kind or "unknown")

    @staticmethod
    def _requires_inclusion(evidence_unit: Mapping[str, object]) -> bool:
        risk_markers = {
            "conflict",
            "conflicts",
            "warning",
            "warnings",
            "error",
            "errors",
            "missing",
            "limitation",
            "limitations",
            "uncertainty",
            "quality_flags",
        }
        if any(
            key in risk_markers and value not in (None, False, "", [], {})
            for key, value in evidence_unit.items()
        ):
            return True
        source_span = evidence_unit.get("source_span")
        locator = (
            source_span.get("locator")
            if isinstance(source_span, Mapping)
            else None
        )
        pointer = locator.get("pointer") if isinstance(locator, Mapping) else None
        if not isinstance(pointer, str):
            return False
        pointer_tokens = {
            token.replace("~1", "/").replace("~0", "~").casefold()
            for token in pointer.split("/")
            if token
        }
        return bool(pointer_tokens.intersection(risk_markers))

    @staticmethod
    def _evidence_roles(evidence_unit: object) -> set[str]:
        if not isinstance(evidence_unit, Mapping):
            return set()
        roles = evidence_unit.get("semantic_roles")
        if isinstance(roles, list):
            return {value for value in roles if isinstance(value, str) and value}
        text = str(evidence_unit.get("content") or "").lower()
        inferred = set()
        if "functional unit" in text or "功能单位" in text:
            inferred.add("functional_unit")
        if "system boundary" in text or "系统边界" in text:
            inferred.add("system_boundary")
        return inferred
