"""Deterministic, source-constrained context assembly for one D-18 decision."""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from typing import Mapping, Sequence

from .material_sources import validate_material_use_decision

CONTEXT_ASSEMBLY_CONTRACT = "lca_research_kernel.context_assembly/v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UNTRUSTED_AUTHORITIES = {
    "user_supplied_project_input",
    "web_content",
    "mcp_resource",
    "tool_description",
    "model_candidate_only",
}
INJECTION_MARKERS = (
    "ignore the goal",
    "ignore previous",
    "grant external_write",
    "replace accepted facts",
    "change the goal",
)
MODEL_EXCERPT_CHARACTER_LIMIT = 1024
MODEL_COMPLETE_SECTION_CHARACTER_LIMIT = 16 * 1024
PRIOR_RESULT_ANSWER_CHARACTER_LIMIT = 8 * 1024


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _evidence_unit_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value) + b"\n").hexdigest()


def _selectors(frame: Mapping[str, object]) -> dict[str, list[str]]:
    problem = frame.get("current_problem")
    if not isinstance(problem, Mapping):
        raise ValueError("context decision problem is absent")
    value = problem.get("context_selectors")
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValueError("context selectors are invalid")
    result = {}
    for key in (
        "fact_ids",
        "domain_rule_ids",
        "capability_ids",
        "evidence_locator_ids",
    ):
        rows = value.get(key) or []
        if not isinstance(rows, list) or any(
            not isinstance(item, str) or not item for item in rows
        ):
            raise ValueError(f"context selector {key} is invalid")
        if len(rows) != len(set(rows)):
            raise ValueError(f"context selector {key} is duplicated")
        result[key] = list(rows)
    return result


def _source_binding(source: object) -> dict:
    if not isinstance(source, Mapping):
        raise ValueError("context item source binding is absent")
    authority = source.get("authority")
    if not isinstance(authority, str) or not authority:
        raise ValueError("context item source authority is absent")
    version = source.get("version")
    content_sha256 = source.get("content_sha256")
    source_refs = source.get("source_refs")
    if isinstance(version, str) and version:
        identity = {"authority": authority, "version": version}
        if isinstance(content_sha256, str):
            if SHA256_RE.fullmatch(content_sha256) is None:
                raise ValueError("context item source hash is invalid")
            identity["content_sha256"] = content_sha256
        return identity
    if isinstance(source_refs, list) and source_refs:
        normalized = []
        for row in source_refs:
            if (
                not isinstance(row, Mapping)
                or not isinstance(row.get("path"), str)
                or not isinstance(row.get("sha256"), str)
                or SHA256_RE.fullmatch(row["sha256"]) is None
            ):
                raise ValueError("context item source reference is invalid")
            normalized.append(
                {"path": row["path"], "sha256": row["sha256"]}
            )
        return {
            "authority": authority,
            "version": "content_addressed",
            "source_refs": normalized,
        }
    raise ValueError("context item source version or content reference is absent")


def _trust(classification: object, authority: str, content: object) -> tuple[str, list[str]]:
    warnings = []
    text = json.dumps(content, ensure_ascii=False).lower()
    has_injection_marker = any(marker in text for marker in INJECTION_MARKERS)
    if authority in UNTRUSTED_AUTHORITIES and (
        classification != "official" or has_injection_marker
    ):
        trust = "untrusted_content"
        if has_injection_marker:
            warnings.append("embedded_control_instruction_ignored")
    elif classification == "official":
        trust = "accepted_official"
    elif classification == "missing":
        trust = "missing"
    elif classification in {"candidate", "assumption", "computed"}:
        trust = str(classification)
    else:
        trust = "source_bound"
    return trust, warnings


def _model_content_projection(
    content: object,
    *,
    excerpt_character_limit: int = MODEL_EXCERPT_CHARACTER_LIMIT,
) -> object:
    """Deterministically bound official-source excerpts for model context only."""

    if (
        not isinstance(excerpt_character_limit, int)
        or excerpt_character_limit < MODEL_EXCERPT_CHARACTER_LIMIT
        or excerpt_character_limit > MODEL_COMPLETE_SECTION_CHARACTER_LIMIT
    ):
        raise ValueError("model excerpt character limit is invalid")

    projected = deepcopy(content)
    if not isinstance(projected, dict):
        return projected
    if (
        projected.get("contract")
        == "lca_research_kernel.selected_material_evidence/v1"
        and isinstance(projected.get("evidence_unit"), Mapping)
        and isinstance(projected["evidence_unit"].get("content"), str)
    ):
        text = projected["evidence_unit"]["content"]
        material_limit = min(excerpt_character_limit, 4095)
        if len(text) > material_limit:
            projected["evidence_unit"]["content"] = text[:material_limit]
            projected["evidence_unit"]["model_projection"] = {
                "strategy": "source_bound_prefix",
                "original_characters": len(text),
                "selected_characters": material_limit,
                "source_content_sha256": projected.get("content_hash"),
            }
    authority = projected.get("authority")
    if (
        isinstance(authority, Mapping)
        and authority.get("authority") == "official_public_source"
    ):
        projected["authority"] = {
            key: deepcopy(authority[key])
            for key in (
                "authority",
                "authority_tier",
                "access",
                "effect",
                "public_credential_free",
                "capability_id",
                "catalog_snapshot_sha256",
                "source_lock",
            )
            if key in authority
        }
    sourced_content = projected.get("content")
    if not isinstance(sourced_content, dict):
        return projected
    excerpt = sourced_content.get("content_excerpt")
    if not isinstance(excerpt, dict):
        return projected
    text = excerpt.get("text")
    body_sha256 = excerpt.get("source_body_sha256")
    receipt = sourced_content.get("source_receipt")
    if (
        not isinstance(text, str)
        or not isinstance(body_sha256, str)
        or SHA256_RE.fullmatch(body_sha256) is None
        or not isinstance(receipt, Mapping)
        or receipt.get("body_sha256") != body_sha256
    ):
        return projected
    if (
        excerpt.get("section_complete") is True
        and excerpt.get("truncated") is False
        and len(text) <= excerpt_character_limit
    ):
        return projected
    if len(text) <= excerpt_character_limit:
        return projected
    excerpt["text"] = text[:excerpt_character_limit]
    excerpt["truncated"] = True
    excerpt["model_projection"] = {
        "strategy": "source_bound_prefix",
        "original_characters": len(text),
            "selected_characters": excerpt_character_limit,
        "source_body_sha256": body_sha256,
    }
    return projected


def _prior_result_answer(result: Mapping[str, object]) -> str:
    delivery = result.get("human_delivery")
    candidates = [
        delivery.get("answer") if isinstance(delivery, Mapping) else None,
        result.get("answer"),
        result.get("bounded_conclusion"),
        result.get("summary"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    raise ValueError("accepted prior result has no user-visible answer")


def _prior_accepted_result_item(
    project_view: Mapping[str, object],
    *,
    selected_fact_ids: set[str],
) -> dict | None:
    """Project the immediately preceding result as context, never as evidence."""

    if "active-user-turn" not in selected_fact_ids:
        return None
    facts = project_view.get("facts")
    active = facts.get("active-user-turn") if isinstance(facts, Mapping) else None
    turn = active.get("value") if isinstance(active, Mapping) else None
    prior_sha256 = (
        turn.get("prior_result_sha256") if isinstance(turn, Mapping) else None
    )
    if prior_sha256 is None:
        return None
    result = project_view.get("result")
    if (
        not isinstance(prior_sha256, str)
        or SHA256_RE.fullmatch(prior_sha256) is None
        or not isinstance(result, Mapping)
        or _sha256(result) != prior_sha256
    ):
        raise ValueError("accepted prior result binding drifted")
    answer = _prior_result_answer(result)
    projected_answer = answer[:PRIOR_RESULT_ANSWER_CHARACTER_LIMIT]
    content = {
        "contract": (
            "lca_research_kernel.research_dialogue_predecessor/v1"
        ),
        "relation": "immediately_precedes_active_user_turn",
        "prior_result_sha256": prior_sha256,
        "status": result.get("status"),
        "claim_ceiling": result.get("claim_ceiling"),
        "answer": projected_answer,
        "authority_boundary": {
            "may_resolve_conversational_referents": True,
            "may_establish_new_project_facts": False,
            "may_serve_as_source_evidence": False,
        },
    }
    if len(answer) > len(projected_answer):
        content["answer_projection"] = {
            "strategy": "bounded_prefix",
            "original_characters": len(answer),
            "selected_characters": len(projected_answer),
        }
    return {
        "kind": "prior_accepted_result",
        "identity": "project_result:" + prior_sha256,
        "source": {
            "authority": "project_record_accepted_result",
            "version": "content_addressed",
            "content_sha256": prior_sha256,
        },
        "selection_reason": (
            "The active user turn is content-bound to this immediately prior "
            "accepted answer, which is needed to resolve conversational "
            "ellipsis and references."
        ),
        "trust_level": "accepted_conversation_context_only",
        "instruction_effect": "none",
        "warnings": [],
        "content": content,
    }


class ContextAssembler:
    """Select only explicitly required, attributable context items."""

    def typed_package(
        self,
        *,
        research_program: Mapping[str, object],
        project_id: str,
        model_context: Mapping[str, object],
        material_authorization_ref: Mapping[str, object] | None = None,
    ) -> dict:
        """Expose one deep, content-bound context interface to consumers."""

        from .typed_context import build_context_package

        return build_context_package(
            research_program=research_program,
            project_id=project_id,
            model_context=model_context,
            material_authorization_ref=material_authorization_ref,
        )

    def assemble_material_answer(
        self,
        *,
        research_program: Mapping[str, object],
        turn_frame: Mapping[str, object],
        prior_accepted_claims: Sequence[Mapping[str, object]],
        governed_knowledge: Sequence[Mapping[str, object]],
        response_language: str,
        required_limitations: Sequence[str],
        material_use_decision: Mapping[str, object],
        material_use_decision_event_sha256: str,
        material_bundles: Sequence[Mapping[str, object]],
    ) -> dict:
        """Build the only model-visible context for a material answer.

        The caller supplies an already accepted use decision and compiled
        research program.  This boundary verifies that both name the same
        evidence closure, then projects only those locators and evidence
        units.  Unselected material never crosses the model boundary.
        """

        from .answer_quality import build_answer_claim_protocol

        if not all(
            isinstance(value, Mapping)
            for value in (research_program, turn_frame, material_use_decision)
        ):
            raise ValueError("material answer context inputs are invalid")
        if response_language not in {"en", "zh-CN"}:
            raise ValueError("material answer context language is unsupported")
        if (
            isinstance(prior_accepted_claims, (str, bytes))
            or not isinstance(prior_accepted_claims, Sequence)
            or any(not isinstance(row, Mapping) for row in prior_accepted_claims)
            or isinstance(governed_knowledge, (str, bytes))
            or not isinstance(governed_knowledge, Sequence)
            or any(not isinstance(row, Mapping) for row in governed_knowledge)
            or isinstance(required_limitations, (str, bytes))
            or not isinstance(required_limitations, Sequence)
            or any(not isinstance(row, str) or not row for row in required_limitations)
            or isinstance(material_bundles, (str, bytes))
            or not isinstance(material_bundles, Sequence)
            or any(not isinstance(row, Mapping) for row in material_bundles)
        ):
            raise ValueError("material answer context collections are invalid")
        if SHA256_RE.fullmatch(material_use_decision_event_sha256) is None:
            raise ValueError("material answer decision event hash is invalid")

        selected = material_use_decision.get("selected_evidence_locator_ids")
        evidence_plan = research_program.get("evidence_plan")
        program_selected = (
            evidence_plan.get("selected_evidence_locator_ids")
            if isinstance(evidence_plan, Mapping)
            else None
        )
        if (
            not isinstance(selected, list)
            or not selected
            or len(selected) != len(set(selected))
            or any(not isinstance(locator_id, str) or not locator_id for locator_id in selected)
            or program_selected != selected
        ):
            raise ValueError("material answer evidence selection drifted")
        selected_set = set(selected)
        selected_evidence_units = []
        revisions = []
        seen_locators: set[str] = set()
        for bundle in material_bundles:
            material = bundle.get("research_material")
            revision = bundle.get("material_revision")
            locators = bundle.get("locators")
            bindings = bundle.get("evidence_unit_bindings")
            if (
                not isinstance(material, Mapping)
                or not isinstance(revision, Mapping)
                or not isinstance(locators, list)
                or not isinstance(bindings, list)
            ):
                raise ValueError("material answer bundle is invalid")
            locator_by_id = {
                row.get("locator_id"): row
                for row in locators
                if isinstance(row, Mapping)
                and isinstance(row.get("locator_id"), str)
            }
            binding_by_id = {
                row.get("locator_id"): row
                for row in bindings
                if isinstance(row, Mapping)
                and isinstance(row.get("locator_id"), str)
            }
            bundle_selected = sorted(selected_set.intersection(locator_by_id))
            if not bundle_selected:
                continue
            revision_locators = []
            for locator_id in bundle_selected:
                locator = locator_by_id[locator_id]
                binding = binding_by_id.get(locator_id)
                content_hash = locator.get("content_hash")
                if (
                    locator_id in seen_locators
                    or not isinstance(binding, Mapping)
                    or binding.get("content_hash") != content_hash
                    or not isinstance(content_hash, str)
                    or SHA256_RE.fullmatch(content_hash) is None
                    or not isinstance(locator.get("locator_kind"), str)
                    or not isinstance(locator.get("locator"), Mapping)
                    or not isinstance(binding.get("source_span"), Mapping)
                    or not isinstance(binding.get("evidence_unit"), Mapping)
                ):
                    raise ValueError("material answer selected evidence is invalid")
                seen_locators.add(locator_id)
                revision_locators.append(
                    {
                        "locator_id": locator_id,
                        "locator_kind": locator["locator_kind"],
                        "locator": deepcopy(locator["locator"]),
                        "content_hash": content_hash,
                    }
                )
                selected_evidence_units.append(
                    {
                        "material_id": material.get("material_id"),
                        "revision_id": revision.get("revision_id"),
                        "locator_id": locator_id,
                        "content_hash": content_hash,
                        "source_span": deepcopy(binding["source_span"]),
                        "evidence_unit": deepcopy(binding["evidence_unit"]),
                        "trust": "untrusted_content",
                        "instruction_effect": "none",
                    }
                )
            artifact_ref = revision.get("artifact_ref")
            if (
                not isinstance(material.get("material_id"), str)
                or not isinstance(revision.get("revision_id"), str)
                or not isinstance(revision.get("processing_status"), str)
                or not isinstance(artifact_ref, Mapping)
                or not isinstance(artifact_ref.get("sha256"), str)
                or SHA256_RE.fullmatch(artifact_ref["sha256"]) is None
            ):
                raise ValueError("material answer revision identity is invalid")
            revisions.append(
                {
                    "material_id": material["material_id"],
                    "revision_id": revision["revision_id"],
                    "processing_status": revision["processing_status"],
                    "input_artifact_sha256": artifact_ref["sha256"],
                    "evidence_locators": revision_locators,
                }
            )
        if seen_locators != selected_set:
            raise ValueError("material answer selected evidence is unavailable")

        outcome = research_program.get("requested_outcome")
        if (
            not isinstance(outcome, Mapping)
            or outcome.get("response_language") != response_language
            or not isinstance(outcome.get("objective"), str)
            or not outcome["objective"]
        ):
            raise ValueError("material answer requested outcome drifted")
        context = {
            "contract": "lca_research_kernel.material_answer_context/v3",
            "research_program": deepcopy(dict(research_program)),
            "objective": outcome["objective"],
            "turn_frame": deepcopy(dict(turn_frame)),
            "prior_accepted_claims": deepcopy(list(prior_accepted_claims)),
            "governed_knowledge": deepcopy(list(governed_knowledge)),
            "response_language": response_language,
            "required_limitations": list(required_limitations),
            "answer_claim_protocol": build_answer_claim_protocol(
                turn_frame=turn_frame,
                selected_locator_ids=selected,
                response_language=response_language,
            ),
            "material_use_decision_checkpoint": {
                "contract": "lca_research_kernel.material_use_decision_checkpoint/v1",
                "decision_id": material_use_decision.get("task_id"),
                "decision_sha256": _sha256(material_use_decision),
                "project_record_event_sha256": material_use_decision_event_sha256,
            },
            "material_revisions": revisions,
            "selected_evidence_units": selected_evidence_units,
            "material_use_decision": {
                "contract": material_use_decision.get("contract"),
                "task_id": material_use_decision.get("task_id"),
                "allowed_revision_ids": deepcopy(
                    material_use_decision.get("allowed_revision_ids")
                ),
                "selected_evidence_locator_ids": list(selected),
                "selection_reasons": deepcopy(
                    material_use_decision.get("selection_reasons")
                ),
                "context_mode": material_use_decision.get("context_mode"),
                "expected_state_change": deepcopy(
                    material_use_decision.get("expected_state_change")
                ),
                "claim_ceiling": material_use_decision.get("claim_ceiling"),
            },
        }
        assembly = {
            "contract": "lca_research_kernel.material_answer_context_assembly/v1",
            "context": context,
            "selected_evidence_units": deepcopy(selected_evidence_units),
        }
        assembly["assembly_sha256"] = _sha256(assembly)
        return assembly

    def material_model_view(self, assembly: Mapping[str, object]) -> dict:
        """Project material content without exposing selection harness state."""

        if (
            not isinstance(assembly, Mapping)
            or assembly.get("contract")
            != "lca_research_kernel.material_answer_context_assembly/v1"
        ):
            raise ValueError("material model context requires an assembly")
        expected = assembly.get("assembly_sha256")
        unhashed = deepcopy(dict(assembly))
        unhashed.pop("assembly_sha256", None)
        if not isinstance(expected, str) or _sha256(unhashed) != expected:
            raise ValueError("material model context assembly drifted")
        context = assembly.get("context")
        if (
            not isinstance(context, Mapping)
            or context.get("contract")
            != "lca_research_kernel.material_answer_context/v3"
        ):
            raise ValueError("material model context payload is invalid")
        required = (
            "objective",
            "turn_frame",
            "prior_accepted_claims",
            "governed_knowledge",
            "response_language",
            "required_limitations",
            "answer_claim_protocol",
            "selected_evidence_units",
        )
        if any(field not in context for field in required):
            raise ValueError("material model context payload is incomplete")
        turn_frame = context["turn_frame"]
        if not isinstance(turn_frame, Mapping):
            raise ValueError("material model turn semantics are invalid")
        projected = {
            "contract": "lca_research_kernel.material_model_context/v1",
            **{
                field: deepcopy(context[field])
                for field in required
                if field != "turn_frame"
            },
            "turn_semantics": {
                field: deepcopy(turn_frame.get(field))
                for field in (
                    "frame_sha256",
                    "interaction_kind",
                    "cognitive_operation",
                    "referent",
                    "answer_obligations",
                    "required_knowledge_roles",
                    "claim_ceiling",
                    "stop_condition",
                )
                if field in turn_frame
            },
            "processing_boundary": {
                "requires_limitation": any(
                    isinstance(row, Mapping)
                    and row.get("processing_status") in {"partial", "needs_user"}
                    for row in context.get("material_revisions", [])
                ),
                "statuses": sorted(
                    {
                        str(row["processing_status"])
                        for row in context.get("material_revisions", [])
                        if isinstance(row, Mapping)
                        and row.get("processing_status")
                        in {"ready", "partial", "needs_user"}
                    }
                ),
            },
            "assembly_ref": {"sha256": expected},
        }
        projected["model_context_sha256"] = _sha256(projected)
        return projected

    def model_view(
        self,
        assembly: Mapping[str, object],
        *,
        identity_allowlist: Sequence[str] | None = None,
    ) -> dict:
        """Compile the minimal model-facing view without losing source identity."""

        if (
            not isinstance(assembly, Mapping)
            or assembly.get("contract") != CONTEXT_ASSEMBLY_CONTRACT
        ):
            raise ValueError("model context requires a context assembly")
        expected = assembly.get("assembly_sha256")
        unhashed = deepcopy(dict(assembly))
        unhashed.pop("assembly_sha256", None)
        if not isinstance(expected, str) or _sha256(unhashed) != expected:
            raise ValueError("model context assembly drifted")
        items = assembly.get("items")
        boundary = assembly.get("control_boundary")
        token_budget = assembly.get("token_budget")
        if (
            not isinstance(items, list)
            or not isinstance(boundary, Mapping)
            or not isinstance(token_budget, int)
        ):
            raise ValueError("model context assembly is incomplete")
        projected_items = list(items)
        projection = {"mode": "all_assembled_items"}
        if identity_allowlist is not None:
            if (
                isinstance(identity_allowlist, (str, bytes))
                or not isinstance(identity_allowlist, Sequence)
                or not identity_allowlist
                or any(
                    not isinstance(identity, str) or not identity
                    for identity in identity_allowlist
                )
                or len(set(identity_allowlist)) != len(identity_allowlist)
            ):
                raise ValueError("model context identity allowlist is invalid")
            by_identity = {
                item.get("identity"): item
                for item in items
                if isinstance(item, Mapping)
            }
            if any(identity not in by_identity for identity in identity_allowlist):
                raise ValueError("model context identity allowlist is absent")
            projected_items = [
                by_identity[identity] for identity in identity_allowlist
            ]
            projection = {
                "mode": "explicit_identity_allowlist",
                "identities": list(identity_allowlist),
            }
        compact_items = []
        official_excerpt_count = 0
        for item in projected_items:
            if not isinstance(item, Mapping):
                raise ValueError("model context item is invalid")
            value = item.get("content")
            sourced_content = (
                value.get("content") if isinstance(value, Mapping) else None
            )
            excerpt = (
                sourced_content.get("content_excerpt")
                if isinstance(sourced_content, Mapping)
                else None
            )
            if isinstance(excerpt, Mapping) and isinstance(
                excerpt.get("text"), str
            ):
                official_excerpt_count += 1
        excerpt_character_limit = min(
            MODEL_COMPLETE_SECTION_CHARACTER_LIMIT,
            max(
                MODEL_EXCERPT_CHARACTER_LIMIT,
                (token_budget * 2) // max(official_excerpt_count, 1),
            ),
        )
        for item in projected_items:
            if not isinstance(item, Mapping):
                raise ValueError("model context item is invalid")
            source = item.get("source")
            if (
                not isinstance(source, Mapping)
                or not isinstance(source.get("authority"), str)
                or not isinstance(source.get("version"), str)
                or item.get("instruction_effect") != "none"
            ):
                raise ValueError("model context item boundary is invalid")
            compact = {
                "identity": deepcopy(item.get("identity")),
                "source": deepcopy(dict(source)),
                "trust_level": deepcopy(item.get("trust_level")),
                "content": _model_content_projection(
                    item.get("content"),
                    excerpt_character_limit=excerpt_character_limit,
                ),
            }
            warnings = item.get("warnings")
            if isinstance(warnings, list) and warnings:
                compact["warnings"] = deepcopy(warnings)
            compact_items.append(compact)
        basis = {
            "contract": "lca_research_kernel.model_context/v1",
            "assembly_ref": {"sha256": expected},
            "items": compact_items,
            "projection": projection,
            "control_boundary": deepcopy(dict(boundary)),
        }
        estimated = math.ceil(len(_canonical_bytes(basis)) / 4)
        if estimated > token_budget:
            raise ValueError(
                f"model context budget exceeded: {estimated} > {token_budget}"
            )
        result = {
            **basis,
            "estimated_tokens": estimated,
            "token_budget": token_budget,
        }
        result["model_context_sha256"] = _sha256(result)
        result["runtime_provenance_sha256s"] = [
            result["model_context_sha256"]
        ]
        return result

    def assemble(
        self,
        *,
        decision_frame: Mapping[str, object],
        project_view: Mapping[str, object],
        domain_rules: Sequence[Mapping[str, object]],
        capabilities: Sequence[Mapping[str, object]],
        token_budget: int,
        semantic_plan: Mapping[str, object] = None,
        material_evidence_units: Sequence[Mapping[str, object]] = (),
    ) -> dict:
        if (
            not isinstance(decision_frame, Mapping)
            or decision_frame.get("contract")
            != "lca_research_kernel.architecture_decision_frame/v1"
        ):
            raise ValueError("context decision frame contract is unsupported")
        if not isinstance(token_budget, int) or token_budget < 1:
            raise ValueError("context token budget is invalid")
        validated_plan = (
            self._validated_semantic_plan(
                semantic_plan,
                project_view=project_view,
                token_budget=token_budget,
            )
            if semantic_plan is not None
            else None
        )
        selectors = (
            {
                "fact_ids": list(validated_plan["selected_fact_ids"]),
                "domain_rule_ids": list(validated_plan["selected_domain_rule_ids"]),
                "capability_ids": list(validated_plan["selected_capability_ids"]),
                "evidence_locator_ids": list(
                    validated_plan["selected_evidence_locator_ids"]
                ),
            }
            if validated_plan is not None
            else _selectors(decision_frame)
        )
        facts = project_view.get("facts")
        if not isinstance(facts, Mapping):
            raise ValueError("context Project Record facts are absent")
        items = (
            self._semantic_plan_items(validated_plan)
            if validated_plan is not None
            else []
        )
        excluded = (
            deepcopy(list(validated_plan["exclusions"]))
            if validated_plan is not None
            else []
        )
        selected_facts = set(selectors["fact_ids"])
        for fact_id in sorted(facts):
            fact = facts[fact_id]
            identity = f"project_fact:{fact_id}"
            if fact_id not in selected_facts:
                excluded.append(
                    {
                        "identity": identity,
                        "reason": "not_required_by_current_decision",
                    }
                )
                continue
            if not isinstance(fact, Mapping):
                raise ValueError("context project fact is invalid")
            source = _source_binding(fact.get("source"))
            content = deepcopy(fact.get("value"))
            trust, warnings = _trust(
                fact.get("classification"), source["authority"], content
            )
            items.append(
                {
                    "kind": "project_fact",
                    "identity": identity,
                    "source": source,
                    "selection_reason": (
                        "The current decision explicitly requires this accepted "
                        "Project Record fact."
                    ),
                    "trust_level": trust,
                    "instruction_effect": "none",
                    "warnings": warnings,
                    "content": content,
                }
            )
        missing_facts = selected_facts - set(facts)
        if missing_facts:
            raise ValueError(
                "context selected Project Record facts are absent: "
                + ", ".join(sorted(missing_facts))
            )
        prior_result_item = _prior_accepted_result_item(
            project_view,
            selected_fact_ids=selected_facts,
        )
        if prior_result_item is not None:
            items.append(prior_result_item)
        items.extend(
            self._selected_domain_rules(
                domain_rules, set(selectors["domain_rule_ids"]), excluded
            )
        )
        items.extend(
            self._selected_capabilities(
                capabilities, set(selectors["capability_ids"]), excluded
            )
        )
        items.extend(
            self._selected_material_evidence(
                project_view,
                material_evidence_units,
                set(selectors["evidence_locator_ids"]),
                (
                    validated_plan["material_use_decision"]
                    if validated_plan is not None
                    else None
                ),
                excluded,
            )
        )
        if validated_plan is not None and validated_plan["missing_dependencies"]:
            items.append(
                {
                    "kind": "semantic_missing_dependencies",
                    "identity": "semantic_missing:" + validated_plan["plan_sha256"][:24],
                    "source": {
                        "authority": "research_semantic_context_projection",
                        "version": validated_plan["graph_sha256"],
                        "content_sha256": validated_plan["plan_sha256"],
                    },
                    "selection_reason": (
                        "The validated semantic plan requires these unresolved "
                        "dependencies to remain explicit."
                    ),
                    "trust_level": "source_bound_projection",
                    "instruction_effect": "none",
                    "warnings": [],
                    "content": {
                        "missing_dependencies": deepcopy(
                            validated_plan["missing_dependencies"]
                        ),
                        "claim_ceiling": validated_plan["expected_state_change"][
                            "claim_ceiling"
                        ],
                    },
                }
            )
        excluded = self._deduplicate_excluded(excluded)
        basis = {
            "contract": CONTEXT_ASSEMBLY_CONTRACT,
            "decision_ref": {
                "decision_id": decision_frame.get("decision_id"),
                "frame_sha256": decision_frame.get("frame_sha256"),
            },
            "project_state_ref": {
                "project_id": project_view.get("project_id"),
                "last_event_id": project_view.get("last_event_id"),
                "last_event_sha256": project_view.get("last_event_sha256"),
            },
            "items": items,
            "excluded": excluded,
            "control_boundary": {
                "untrusted_content_can_change_goal": False,
                "untrusted_content_can_change_permissions": False,
                "untrusted_content_can_write_project_truth": False,
            },
        }
        if validated_plan is not None:
            basis["semantic_plan_ref"] = {
                "sha256": validated_plan["plan_sha256"],
                "graph_sha256": validated_plan["graph_sha256"],
                "retrieval_sha256": validated_plan["retrieval_sha256"],
            }
        estimate_basis = deepcopy(basis)
        estimate_basis["items"] = [
            {
                **deepcopy(dict(item)),
                "content": _model_content_projection(item.get("content")),
            }
            for item in items
        ]
        estimated = math.ceil(len(_canonical_bytes(estimate_basis)) / 4)
        if estimated > token_budget:
            raise ValueError(
                f"context budget exceeded: {estimated} > {token_budget}"
            )
        assembly = {
            **basis,
            "token_budget": token_budget,
            "estimated_tokens": estimated,
        }
        assembly["assembly_sha256"] = _sha256(assembly)
        return assembly

    def _validated_semantic_plan(
        self,
        value: Mapping[str, object],
        *,
        project_view: Mapping[str, object],
        token_budget: int,
    ) -> dict:
        if (
            not isinstance(value, Mapping)
            or value.get("contract")
            != "lca_research_kernel.semantic_context_plan/v1"
        ):
            raise ValueError("semantic context plan contract is unsupported")
        normalized = deepcopy(dict(value))
        stored = normalized.pop("plan_sha256", None)
        if not isinstance(stored, str) or _sha256(normalized) != stored:
            raise ValueError("semantic context plan drifted")
        normalized["plan_sha256"] = stored
        if normalized.get("project_id") != project_view.get("project_id"):
            raise ValueError("semantic context plan project scope drifted")
        state_ref = normalized.get("project_state_ref")
        if (
            not isinstance(state_ref, Mapping)
            or state_ref.get("last_event_sha256")
            != project_view.get("last_event_sha256")
        ):
            raise ValueError("semantic context plan Project Record state drifted")
        budgets = normalized.get("budgets")
        if (
            not isinstance(budgets, Mapping)
            or not isinstance(budgets.get("context_tokens"), int)
            or token_budget > budgets["context_tokens"]
        ):
            raise ValueError("semantic context plan budget drifted")
        for key in (
            "selected_fact_ids",
            "selected_domain_rule_ids",
            "selected_capability_ids",
            "selected_skill_ids",
            "selected_governed_knowledge_ids",
            "selected_governed_knowledge",
            "selected_evidence_locator_ids",
            "graph_paths",
            "skill_disclosures",
            "missing_dependencies",
            "exclusions",
        ):
            if not isinstance(normalized.get(key), list):
                raise ValueError(f"semantic context plan {key} is invalid")
        knowledge_ids = normalized["selected_governed_knowledge_ids"]
        knowledge_rows = normalized["selected_governed_knowledge"]
        if (
            any(not isinstance(item, str) or not item for item in knowledge_ids)
            or len(knowledge_ids) != len(set(knowledge_ids))
            or any(
                not isinstance(row, Mapping)
                or row.get("knowledge_id") not in knowledge_ids
                for row in knowledge_rows
            )
            or sorted(row["knowledge_id"] for row in knowledge_rows)
            != sorted(knowledge_ids)
        ):
            raise ValueError("semantic context governed knowledge selection drifted")
        decision = normalized.get("material_use_decision")
        locators = project_view.get("evidence_locators") or {}
        if not isinstance(locators, Mapping):
            raise ValueError("semantic context material locator state is invalid")
        if not isinstance(decision, Mapping):
            raise ValueError("semantic context material use decision is absent")
        normalized["material_use_decision"] = validate_material_use_decision(
            decision,
            locator_index=locators,
        )
        if set(normalized["selected_evidence_locator_ids"]) != set(
            normalized["material_use_decision"]["selected_evidence_locator_ids"]
        ):
            raise ValueError("semantic context material selection drifted")
        return normalized

    @staticmethod
    def _selected_material_evidence(
        project_view: Mapping[str, object],
        bindings: Sequence[Mapping[str, object]],
        selected: set[str],
        use_decision: Mapping[str, object] | None,
        excluded: list[dict],
    ) -> list[dict]:
        if not selected and not bindings:
            return []
        locators = project_view.get("evidence_locators") or {}
        revisions = project_view.get("material_revisions") or {}
        materials = project_view.get("materials") or {}
        if not all(isinstance(value, Mapping) for value in (locators, revisions, materials)):
            raise ValueError("context material state is invalid")
        by_locator: dict[str, Mapping[str, object]] = {}
        for binding in bindings:
            if not isinstance(binding, Mapping) or not isinstance(
                binding.get("locator_id"), str
            ):
                raise ValueError("context material evidence binding is invalid")
            locator_id = str(binding["locator_id"])
            if locator_id in by_locator:
                raise ValueError("context material evidence locator is duplicated")
            by_locator[locator_id] = binding
        missing = selected - set(by_locator)
        if missing:
            raise ValueError(
                "context selected evidence locators are absent: "
                + ", ".join(sorted(missing))
            )
        selection_reasons = (
            use_decision.get("selection_reasons")
            if isinstance(use_decision, Mapping)
            else {}
        )
        result = []
        for locator_id in sorted(by_locator):
            identity = f"evidence_locator:{locator_id}"
            if locator_id not in selected:
                if not any(row.get("identity") == identity for row in excluded):
                    excluded.append(
                        {
                            "identity": identity,
                            "reason": "not_required_by_expected_state_change",
                        }
                    )
                continue
            locator = locators.get(locator_id)
            binding = by_locator[locator_id]
            revision = (
                revisions.get(locator.get("revision_id"))
                if isinstance(locator, Mapping)
                else None
            )
            material = (
                materials.get(revision.get("material_id"))
                if isinstance(revision, Mapping)
                else None
            )
            if (
                not isinstance(locator, Mapping)
                or locator.get("scientific_status") != "accepted_evidence"
                or not isinstance(locator.get("assurance_acceptance_ref"), Mapping)
                or not isinstance(revision, Mapping)
                or not isinstance(material, Mapping)
                or material.get("current_revision_id") != locator.get("revision_id")
                or material.get("deleted_at") is not None
                or material.get("scope") not in {"turn", "project"}
                or binding.get("content_hash") != locator.get("content_hash")
                or not isinstance(binding.get("evidence_unit"), Mapping)
                or _evidence_unit_sha256(binding.get("evidence_unit"))
                != locator.get("content_hash")
                or binding.get("source_span")
                != {
                    "kind": locator.get("locator_kind"),
                    "locator": locator.get("locator"),
                }
                or binding["evidence_unit"].get("source_span")
                != binding.get("source_span")
            ):
                raise ValueError("context selected evidence locator is not accepted and current")
            unit = deepcopy(dict(binding["evidence_unit"]))
            warnings = []
            serialized = json.dumps(unit, ensure_ascii=False).lower()
            if any(marker in serialized for marker in INJECTION_MARKERS):
                warnings.append("embedded_control_instruction_ignored")
            result.append(
                {
                    "kind": "material_evidence_unit",
                    "identity": identity,
                    "source": {
                        "authority": "user_supplied_project_input",
                        "version": str(locator["revision_id"]),
                        "content_sha256": str(locator["content_hash"]),
                    },
                    "selection_reason": str(
                        selection_reasons.get(
                            locator_id, "selected_by_expected_state_change"
                        )
                    ),
                    "trust_level": "untrusted_content",
                    "estimated_tokens": math.ceil(
                        len(_canonical_bytes(unit)) / 4
                    ),
                    "instruction_effect": "none",
                    "warnings": warnings,
                    "content": {
                        "contract": (
                            "lca_research_kernel.selected_material_evidence/v1"
                        ),
                        "material_id": material.get("material_id"),
                        "revision_id": locator.get("revision_id"),
                        "locator_id": locator_id,
                        "locator_kind": locator.get("locator_kind"),
                        "locator": deepcopy(locator.get("locator")),
                        "content_hash": locator.get("content_hash"),
                        "evidence_unit": unit,
                    },
                }
            )
        return result

    def _semantic_plan_items(self, plan: Mapping[str, object]) -> list[dict]:
        source = {
            "authority": "research_semantic_context_projection",
            "version": plan["graph_sha256"],
            "content_sha256": plan["plan_sha256"],
        }
        items = [
            {
                "kind": "semantic_state_transition",
                "identity": "semantic_state:" + str(plan["plan_sha256"])[:24],
                "source": deepcopy(source),
                "selection_reason": (
                    "The current accepted state and expected state change define "
                    "the context objective."
                ),
                "trust_level": "source_bound_projection",
                "instruction_effect": "none",
                "warnings": [],
                "content": {
                    "expected_state_change": deepcopy(
                        plan["expected_state_change"]
                    ),
                    "query_mode": plan["query_mode"],
                    "selected_skill_ids": deepcopy(plan["selected_skill_ids"]),
                    "next_route": plan["next_route"],
                },
            }
        ]
        for path in plan["graph_paths"]:
            if not isinstance(path, Mapping) or not isinstance(
                path.get("path_sha256"), str
            ):
                raise ValueError("semantic context graph path is invalid")
            items.append(
                {
                    "kind": "semantic_relationship_path",
                    "identity": "semantic_path:" + path["path_sha256"],
                    "source": deepcopy(source),
                    "selection_reason": str(path.get("reason")),
                    "trust_level": "source_bound_projection",
                    "instruction_effect": "none",
                    "warnings": [],
                    "content": {
                        "node_ids": deepcopy(path.get("node_ids")),
                        "edge_ids": deepcopy(path.get("edge_ids")),
                        "required_role": path.get("required_role"),
                        "sources": deepcopy(path.get("sources")),
                    },
                }
            )
        for knowledge in plan["selected_governed_knowledge"]:
            if not isinstance(knowledge, Mapping):
                raise ValueError("semantic context governed knowledge is invalid")
            source_ref = knowledge.get("source_ref")
            if (
                not isinstance(source_ref, Mapping)
                or not isinstance(source_ref.get("sha256"), str)
                or SHA256_RE.fullmatch(str(source_ref["sha256"])) is None
                or not isinstance(knowledge.get("registry_id"), str)
            ):
                raise ValueError("semantic context governed knowledge source is invalid")
            items.append(
                {
                    "kind": "governed_lca_knowledge",
                    "identity": "governed_knowledge:"
                    + str(knowledge["knowledge_id"]),
                    "source": {
                        "authority": "governed_lca_domain_knowledge",
                        "version": str(knowledge["registry_id"]),
                        "content_sha256": str(source_ref["sha256"]),
                    },
                    "selection_reason": (
                        "The accepted semantic path requires this exact "
                        "source-bound method explanation."
                    ),
                    "trust_level": "source_bound_method",
                    "instruction_effect": "none",
                    "warnings": [],
                    "content": {
                        "knowledge_id": knowledge["knowledge_id"],
                        "source_kind": knowledge["source_kind"],
                        "relation": knowledge["relation"],
                        "target_kind": knowledge["target_kind"],
                        "statement": knowledge["statement"],
                        "knowledge_roles": deepcopy(
                            knowledge["knowledge_roles"]
                        ),
                        "claim_ceiling": knowledge["claim_ceiling"],
                        "source_ref": deepcopy(source_ref),
                        "not_project_fact": True,
                    },
                }
            )
        for disclosure in plan["skill_disclosures"]:
            if not isinstance(disclosure, Mapping):
                raise ValueError("semantic context Skill disclosure is invalid")
            stored = disclosure.get("disclosure_sha256")
            unhashed = deepcopy(dict(disclosure))
            unhashed.pop("disclosure_sha256", None)
            if not isinstance(stored, str) or _sha256(unhashed) != stored:
                raise ValueError("semantic context Skill disclosure drifted")
            level = disclosure.get("level")
            if level not in {"L1", "L2", "L3"}:
                raise ValueError("semantic context Skill disclosure level is invalid")
            metadata = disclosure.get("metadata")
            method = disclosure.get("method_contract")
            if not isinstance(metadata, Mapping):
                raise ValueError("semantic context Skill disclosure is incomplete")
            skill_content = {
                "skill_id": metadata.get("skill_id"),
            }
            if level == "L1":
                if any(
                    key in disclosure
                    for key in ("instructions", "method_contract", "resource_contents")
                ):
                    raise ValueError("L1 Skill disclosure exceeded metadata scope")
                skill_content.update(
                    {
                        "disclosure_level": level,
                        "routing_summary": metadata.get("routing_summary"),
                    }
                )
            else:
                instructions = disclosure.get("instructions")
                if (
                    not isinstance(method, Mapping)
                    or not isinstance(instructions, str)
                    or not instructions.strip()
                ):
                    raise ValueError("semantic context Skill disclosure is incomplete")
                skill_content.update(
                    {
                        "instructions": instructions,
                        "expected_artifact": method.get("expected_artifact"),
                        "accepted_state_delta": method.get("accepted_state_delta"),
                        "acceptance_checks": deepcopy(
                            method.get("acceptance_checks")
                        ),
                        "failure_boundaries": deepcopy(
                            method.get("failure_boundaries")
                        ),
                    }
                )
                resources = disclosure.get("resource_contents")
                if level == "L2" and resources is not None:
                    raise ValueError("L2 Skill disclosure included resource contents")
                if level == "L3":
                    if not isinstance(resources, list):
                        raise ValueError("L3 Skill resource disclosure is incomplete")
                    skill_content["resource_contents"] = deepcopy(resources)
            items.append(
                {
                    "kind": "research_skill",
                    "identity": "research_skill:" + str(metadata.get("skill_id")),
                    "source": {
                        "authority": "source_locked_research_skill",
                        "version": str(metadata.get("version")),
                        "content_sha256": str(disclosure.get("source_sha256")),
                    },
                    "selection_reason": (
                        "The semantic state transition selected this validated "
                        "research method."
                    ),
                    "trust_level": "governed_method",
                    "instruction_effect": "none",
                    "warnings": [],
                    "content": skill_content,
                }
            )
        worker_execution = plan.get("worker_execution")
        if worker_execution is not None:
            if not isinstance(worker_execution, Mapping):
                raise ValueError("semantic context worker execution is invalid")
            stored_execution = worker_execution.get("execution_sha256")
            unhashed_execution = deepcopy(dict(worker_execution))
            unhashed_execution.pop("execution_sha256", None)
            if (
                not isinstance(stored_execution, str)
                or _sha256(unhashed_execution) != stored_execution
                or worker_execution.get("project_truth_effect") != "none"
            ):
                raise ValueError("semantic context worker execution drifted")
            for artifact in worker_execution.get("artifacts") or []:
                if not isinstance(artifact, Mapping):
                    raise ValueError("semantic context worker artifact is invalid")
                stored_artifact = artifact.get("artifact_sha256")
                unhashed_artifact = deepcopy(dict(artifact))
                unhashed_artifact.pop("artifact_sha256", None)
                if (
                    not isinstance(stored_artifact, str)
                    or _sha256(unhashed_artifact) != stored_artifact
                    or artifact.get("classification") != "candidate"
                    or artifact.get("acceptance_effect") != "none"
                ):
                    raise ValueError("semantic context worker artifact drifted")
                items.append(
                    {
                        "kind": "research_worker_artifact",
                        "identity": "research_worker_artifact:" + stored_artifact,
                        "source": {
                            "authority": "bounded_artifact_worker",
                            "version": str(worker_execution["topology_ref"]),
                            "content_sha256": stored_artifact,
                        },
                        "selection_reason": (
                            "The lead selected this bounded worker artifact for "
                            "candidate reintegration; it is not accepted truth."
                        ),
                        "trust_level": "candidate",
                        "instruction_effect": "none",
                        "warnings": [],
                        "content": {
                            "slice_id": artifact.get("slice_id"),
                            "worker_role": artifact.get("worker_role"),
                            "expected_artifact": artifact.get("expected_artifact"),
                            "source_refs": deepcopy(artifact.get("source_refs")),
                            "candidate": deepcopy(artifact.get("content")),
                        },
                    }
                )
        return items

    @staticmethod
    def _deduplicate_excluded(rows: list[dict]) -> list[dict]:
        unique = {
            (str(row.get("identity")), str(row.get("reason"))): deepcopy(row)
            for row in rows
        }
        return [unique[key] for key in sorted(unique)]

    def _selected_domain_rules(
        self,
        rules: Sequence[Mapping[str, object]],
        selected: set[str],
        excluded: list[dict],
    ) -> list[dict]:
        by_id = {}
        for row in rules:
            if not isinstance(row, Mapping) or not isinstance(row.get("rule_id"), str):
                raise ValueError("context domain rule is invalid")
            by_id[row["rule_id"]] = row
        result = []
        for rule_id in sorted(by_id):
            identity = f"domain_rule:{rule_id}"
            row = by_id[rule_id]
            if rule_id not in selected:
                excluded.append(
                    {"identity": identity, "reason": "not_required_by_current_decision"}
                )
                continue
            version = row.get("version")
            if row.get("contract") == "lca_research_kernel.domain_rule/v1":
                source_lock_ids = row.get("source_lock_ids")
                if (
                    not isinstance(source_lock_ids, list)
                    or not source_lock_ids
                    or any(
                        not isinstance(source_id, str) or not source_id
                        for source_id in source_lock_ids
                    )
                ):
                    raise ValueError(
                        "context domain rule source binding is invalid"
                    )
                source_ref = {
                    "path": f"domain-rule-registry:{rule_id}@{version}",
                    "sha256": _sha256(row),
                }
                content = {
                    "title": row.get("title"),
                    "statement": row.get("statement"),
                    "source_lock_ids": deepcopy(source_lock_ids),
                    "enforcement": row.get("enforcement"),
                }
            else:
                source_ref = row.get("source_ref")
                content = deepcopy(row.get("content"))
            if (
                not isinstance(version, str)
                or not version
                or not isinstance(source_ref, Mapping)
                or not isinstance(source_ref.get("path"), str)
                or not isinstance(source_ref.get("sha256"), str)
                or SHA256_RE.fullmatch(source_ref["sha256"]) is None
            ):
                raise ValueError("context domain rule source binding is invalid")
            result.append(
                {
                    "kind": "domain_rule",
                    "identity": identity,
                    "source": {
                        "authority": "lca_domain_kernel",
                        "version": version,
                        "source_ref": deepcopy(dict(source_ref)),
                    },
                    "selection_reason": (
                        "The current decision explicitly requires this stable "
                        "LCA Domain Kernel rule."
                    ),
                    "trust_level": "adopted_domain_rule",
                    "instruction_effect": "none",
                    "warnings": [],
                    "content": content,
                }
            )
        if selected - set(by_id):
            raise ValueError("context selected domain rule is absent")
        return result

    def _selected_capabilities(
        self,
        capabilities: Sequence[Mapping[str, object]],
        selected: set[str],
        excluded: list[dict],
    ) -> list[dict]:
        by_id = {}
        for row in capabilities:
            if not isinstance(row, Mapping) or not isinstance(
                row.get("capability_id"), str
            ):
                raise ValueError("context capability descriptor is invalid")
            by_id[row["capability_id"]] = row
        result = []
        for capability_id in sorted(by_id):
            identity = f"capability:{capability_id}"
            row = by_id[capability_id]
            if capability_id not in selected:
                excluded.append(
                    {"identity": identity, "reason": "not_required_by_current_decision"}
                )
                continue
            if any(
                not isinstance(row.get(key), str) or not row[key]
                for key in (
                    "source_identity",
                    "source_version",
                    "source_sha256",
                    "effect",
                )
            ) or SHA256_RE.fullmatch(str(row["source_sha256"])) is None:
                raise ValueError("context capability source binding is invalid")
            result.append(
                {
                    "kind": "capability_descriptor",
                    "identity": identity,
                    "source": {
                        "authority": row["source_identity"],
                        "version": row["source_version"],
                        "content_sha256": row["source_sha256"],
                    },
                    "selection_reason": (
                        "The current decision selected this capability catalog "
                        "descriptor; runtime identity still requires execution."
                    ),
                    "trust_level": "capability_catalog_metadata",
                    "instruction_effect": "none",
                    "warnings": [],
                    "content": {
                        "capability_id": capability_id,
                        "effect": row["effect"],
                        **(
                            {"authority": row["authority"]}
                            if isinstance(row.get("authority"), str)
                            else {}
                        ),
                        **(
                            {"access": row["access"]}
                            if isinstance(row.get("access"), str)
                            else {}
                        ),
                        **(
                            {
                                "public_credential_free": row[
                                    "public_credential_free"
                                ]
                            }
                            if isinstance(
                                row.get("public_credential_free"), bool
                            )
                            else {}
                        ),
                        **(
                            {
                                "runtime_context": deepcopy(
                                    dict(row["runtime_context"])
                                )
                            }
                            if isinstance(row.get("runtime_context"), Mapping)
                            else {}
                        ),
                    },
                }
            )
        if selected - set(by_id):
            raise ValueError("context selected capability is absent")
        return result
