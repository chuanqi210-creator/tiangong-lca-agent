"""Project-bound semantic task frames for accepted research turns."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Mapping

from .content_identity import canonical_sha256


FRAME_CONTRACT = "lca_research_kernel.research_turn_frame/v1"
FRAME_CONTRACT_V2 = "lca_research_kernel.research_turn_frame/v2"
COGNITIVE_OPERATIONS = frozenset(
    {
        "extract",
        "explain",
        "apply",
        "compare",
        "calculate",
        "interpret",
        "audit",
        "research",
    }
)
ANSWER_OBLIGATIONS = frozenset(
    {
        "direct_resolution",
        "referent_resolution",
        "concept_meaning",
        "lca_relationship",
        "source_value_explanation",
        "calculation_explanation",
        "illustrative_application",
        "comparison_basis",
        "uncertainty_and_limit",
        "source_boundary",
    }
)
FRAME_FIELDS = {
    "contract",
    "turn_sha256",
    "project_state_sha256",
    "interaction_kind",
    "cognitive_operation",
    "referent",
    "answer_obligations",
    "required_knowledge_roles",
    "allowed_revision_ids",
    "claim_ceiling",
    "ambiguities",
    "stop_condition",
    "frame_sha256",
}
FRAME_V2_FIELDS = FRAME_FIELDS | {"domain_knowledge_intent"}
DOMAIN_KNOWLEDGE_INTENT_FIELDS = {
    "contract",
    "registry_sha256",
    "selected_knowledge_ids",
    "required_roles",
    "target_object_kinds",
    "relation_kinds",
    "intent_sha256",
}
PROPOSAL_FIELDS = {
    "cognitive_operation",
    "answer_obligations",
    "referent_claim_id",
}
RESEARCH_DIALOGUE_MOVES = {
    "source_identity": (
        "extract",
        ("direct_resolution", "source_boundary"),
    ),
    "source_item_meaning": (
        "explain",
        (
            "referent_resolution",
            "concept_meaning",
            "source_value_explanation",
        ),
    ),
    "deepen_explanation": (
        "explain",
        (
            "concept_meaning",
            "lca_relationship",
            "uncertainty_and_limit",
        ),
    ),
    "nonexpert_restatement": (
        "explain",
        ("direct_resolution", "concept_meaning", "source_boundary"),
    ),
    "bounded_application": (
        "apply",
        (
            "referent_resolution",
            "lca_relationship",
            "illustrative_application",
            "source_boundary",
        ),
    ),
    "comparison": (
        "compare",
        (
            "direct_resolution",
            "comparison_basis",
            "uncertainty_and_limit",
            "source_boundary",
        ),
    ),
    "calculation": (
        "calculate",
        (
            "direct_resolution",
            "calculation_explanation",
            "source_boundary",
        ),
    ),
    "result_interpretation": (
        "interpret",
        (
            "direct_resolution",
            "concept_meaning",
            "lca_relationship",
            "uncertainty_and_limit",
        ),
    ),
    "evidence_audit": (
        "audit",
        ("direct_resolution", "uncertainty_and_limit", "source_boundary"),
    ),
    "open_research": (
        "research",
        ("direct_resolution", "uncertainty_and_limit", "source_boundary"),
    ),
}
HEX64 = re.compile(r"[0-9a-f]{64}")
CJK_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


_OBLIGATION_KNOWLEDGE_ROLES = {
    "direct_resolution": (),
    "referent_resolution": (),
    "concept_meaning": ("lca_concept",),
    "lca_relationship": ("exchange", "flow", "process"),
    "source_value_explanation": ("amount", "flow", "unit"),
    "calculation_explanation": ("amount", "transformation", "unit"),
    "illustrative_application": (
        "illustrative_application_boundary",
        "lca_relationship",
    ),
    "comparison_basis": ("functional_unit", "system_boundary"),
    "uncertainty_and_limit": ("evidence_limitation",),
    # Source separation is enforced by typed claim/citation boundaries; it
    # does not justify disclosing unrelated illustration knowledge.
    "source_boundary": (),
}


def knowledge_roles_for_obligations(obligations: object) -> list[str]:
    """Derive the closed knowledge-role set from answer obligations."""

    if (
        not isinstance(obligations, list)
        or not obligations
        or len(obligations) != len(set(obligations))
        or any(obligation not in ANSWER_OBLIGATIONS for obligation in obligations)
    ):
        raise ValueError("research turn answer obligations are unsupported")
    return _knowledge_roles(obligations)


def infer_response_language(text: object) -> str:
    """Choose the response language from the accepted user turn only."""

    if not isinstance(text, str) or not text.strip():
        raise ValueError("accepted research turn text is unavailable")
    normalized = text.casefold()
    if re.search(r"\b(?:answer|explain|respond|write)?\s*in english\b", normalized) or (
        "用英文" in text or "以英文" in text
    ):
        return "en"
    if re.search(r"\b(?:answer|explain|respond|write)?\s*in chinese\b", normalized) or (
        "用中文" in text or "以中文" in text
    ):
        return "zh-CN"
    cjk_count = len(CJK_CHARACTER.findall(text))
    latin_count = len(re.findall(r"[A-Za-z]", text))
    return "zh-CN" if cjk_count and cjk_count * 2 >= latin_count else "en"


def build_research_turn_semantic_protocol() -> dict:
    """Expose the closed interpretation contract used by the turn classifier."""

    return {
        "contract": "lca_research_kernel.research_turn_semantic_protocol/v1",
        "operation_definitions": {
            "extract": "identify source-bound facts or object identity",
            "explain": "clarify meaning, relationship, or limitations",
            "apply": "show how the accepted meaning works in a bounded scenario",
            "compare": "compare on an explicit equivalent basis and boundary",
            "calculate": "derive a result from explicit inputs and transformations",
            "interpret": "explain the significance of an accepted result",
            "audit": "check evidence, method, identity, units, or consistency",
            "research": "investigate an unresolved dependency before concluding",
        },
        "obligation_definitions": {
            "direct_resolution": "answer the active question in the first claim",
            "referent_resolution": "bind the object or prior claim being discussed",
            "concept_meaning": "explain what the referent represents",
            "lca_relationship": (
                "explain how the referent relates to LCA objects or method roles"
            ),
            "source_value_explanation": (
                "explain named source values and units without inventing semantics"
            ),
            "calculation_explanation": (
                "show deterministic inputs, transformation, unit, and result"
            ),
            "illustrative_application": (
                "provide a visibly non-project example when the user asks for one"
            ),
            "comparison_basis": (
                "state functional equivalence and comparison boundary"
            ),
            "uncertainty_and_limit": (
                "state material uncertainty, conflict, or missing dependency"
            ),
            "source_boundary": (
                "distinguish project evidence, governed knowledge, and illustration"
            ),
        },
        "dialogue_rules": [
            "current_user_intent_over_prior_answer_shape",
            "progressive_depth_not_restatement",
            "named_source_item_meaning_requires_referent_concept_and_source_value",
            "deeper_explanation_requires_relationship_and_material_limit",
            "nonexpert_restatement_requires_direct_resolution_and_concept_meaning",
            "concrete_scenario_requires_relationship_illustration_and_source_boundary",
            "select_only_obligations_needed_by_the_current_turn",
            "never_answer_or_add_project_facts_during_classification",
        ],
    }


def build_research_turn_intent_protocol() -> dict:
    """Expose semantic dialogue moves while machines own obligations."""

    return {
        "contract": "lca_research_kernel.research_turn_intent_protocol/v2",
        "dialogue_move_definitions": {
            "source_identity": "identify what the accepted source is",
            "source_item_meaning": (
                "explain a named row, field, flow, value, or source item"
            ),
            "deepen_explanation": (
                "deepen the accepted explanation with LCA relationship and limits"
            ),
            "nonexpert_restatement": (
                "restate the accepted meaning for a non-specialist"
            ),
            "bounded_application": (
                "show the accepted meaning in a visibly non-project scenario"
            ),
            "comparison": "compare alternatives on an explicit equivalent basis",
            "calculation": "explain or perform an explicit bounded calculation",
            "result_interpretation": "interpret the significance of an accepted result",
            "evidence_audit": "audit identity, unit, evidence, or consistency",
            "open_research": "investigate an unresolved dependency before concluding",
        },
        "classifier_rules": [
            "classify_semantic_intent_not_keywords",
            "current_user_intent_over_prior_answer_shape",
            "use_deepen_explanation_for_an_unqualified_request_for_more_depth",
            "use_source_item_meaning_for_a_named_source_row_field_value_or_flow",
            "use_nonexpert_restatement_when_the_audience_requests_plain_language",
            "use_bounded_application_when_a_concrete_example_or_scenario_is_requested",
            "do_not_select_answer_obligations_or_answer_the_question",
        ],
        "machine_compiled_obligations": {
            move: list(specification[1])
            for move, specification in RESEARCH_DIALOGUE_MOVES.items()
        },
    }


def build_research_turn_knowledge_intent_protocol() -> dict:
    """Extend v2 with bounded selection while machines retain authority."""

    protocol = build_research_turn_intent_protocol()
    protocol["contract"] = (
        "lca_research_kernel.research_turn_intent_protocol/v3"
    )
    protocol["knowledge_selection_rules"] = [
        "select_only_ids_from_the_current_governed_catalog",
        "select_the_smallest_relation_set_needed_by_the_current_question",
        "select_no_domain_knowledge_when_the_catalog_is_not_needed",
        "selection_does_not_create_a_project_fact_or_raise_claim_ceiling",
        "do_not_return_reasoning_or_copy_source_content",
    ]
    return protocol


def compile_research_turn_intent(value: Mapping[str, object]) -> dict:
    """Compile one open semantic move into closed answer obligations."""

    if not isinstance(value, Mapping) or set(value) != {
        "dialogue_move",
        "referent_claim_id",
    }:
        raise ValueError("research turn intent fields are unsupported")
    move = value.get("dialogue_move")
    referent_claim_id = value.get("referent_claim_id")
    if move not in RESEARCH_DIALOGUE_MOVES:
        raise ValueError("research turn dialogue move is unsupported")
    if not isinstance(referent_claim_id, str) or not referent_claim_id:
        raise ValueError("research turn intent referent is unavailable")
    operation, obligations = RESEARCH_DIALOGUE_MOVES[move]
    return {
        "cognitive_operation": operation,
        "answer_obligations": list(obligations),
        "referent_claim_id": referent_claim_id,
    }


def compile_initial_research_turn_intent(value: Mapping[str, object]) -> dict:
    """Compile an initial semantic move while retaining Project Goal authority."""

    if not isinstance(value, Mapping) or set(value) != {"dialogue_move"}:
        raise ValueError("initial research turn intent fields are unsupported")
    move = value.get("dialogue_move")
    if move not in RESEARCH_DIALOGUE_MOVES:
        raise ValueError("research turn dialogue move is unsupported")
    operation, obligations = RESEARCH_DIALOGUE_MOVES[move]
    return {
        "cognitive_operation": operation,
        "answer_obligations": list(obligations),
        "referent_claim_id": None,
    }


def _active_turn(project_view: Mapping[str, object]) -> dict:
    facts = project_view.get("facts")
    fact = facts.get("active-user-turn") if isinstance(facts, Mapping) else None
    value = fact.get("value") if isinstance(fact, Mapping) else None
    if (
        not isinstance(fact, Mapping)
        or fact.get("domain_kind") != "user_turn"
        or fact.get("classification") != "candidate"
        or not isinstance(value, Mapping)
        or value.get("contract") != "lca_research_kernel.research_user_turn/v1"
        or not isinstance(value.get("turn_sha256"), str)
        or HEX64.fullmatch(value["turn_sha256"]) is None
    ):
        raise ValueError("accepted active research turn is unavailable")
    return deepcopy(dict(value))


def _prior_claims(prior_result: Mapping[str, object] | None) -> dict[str, dict]:
    if prior_result is None:
        return {}
    claims = prior_result.get("claims")
    if not isinstance(claims, list):
        raise ValueError("accepted prior result claims are invalid")
    index: dict[str, dict] = {}
    for claim in claims:
        if (
            not isinstance(claim, Mapping)
            or not isinstance(claim.get("claim_id"), str)
            or not claim["claim_id"].strip()
            or claim["claim_id"] in index
        ):
            raise ValueError("accepted prior result claim identity is invalid")
        index[claim["claim_id"]] = deepcopy(dict(claim))
    return index


def _validate_prior_result(
    turn: Mapping[str, object], prior_result: Mapping[str, object] | None
) -> str | None:
    expected = turn.get("prior_result_sha256")
    if expected is None:
        if prior_result is not None:
            raise ValueError("accepted prior result was not bound to the turn")
        return None
    if (
        not isinstance(expected, str)
        or HEX64.fullmatch(expected) is None
        or not isinstance(prior_result, Mapping)
        or canonical_sha256(prior_result) != expected
    ):
        raise ValueError("accepted prior result binding drifted")
    return expected


def _validate_allowed_revisions(
    project_view: Mapping[str, object], allowed_revision_ids: object
) -> list[str]:
    if (
        not isinstance(allowed_revision_ids, list)
        or len(allowed_revision_ids) != len(set(allowed_revision_ids))
        or any(
            not isinstance(revision_id, str) or not revision_id
            for revision_id in allowed_revision_ids
        )
    ):
        raise ValueError("research turn allowed revisions are invalid")
    materials = project_view.get("materials") or {}
    revisions = project_view.get("material_revisions") or {}
    if not isinstance(materials, Mapping) or not isinstance(revisions, Mapping):
        raise ValueError("Project Record material state is invalid")
    for revision_id in allowed_revision_ids:
        revision = revisions.get(revision_id)
        material = (
            materials.get(revision.get("material_id"))
            if isinstance(revision, Mapping)
            else None
        )
        if (
            not isinstance(revision, Mapping)
            or not isinstance(material, Mapping)
            or material.get("current_revision_id") != revision_id
            or material.get("deleted_at") is not None
            or revision.get("processing_status")
            not in {"ready", "partial", "needs_user"}
        ):
            raise ValueError("research turn allowed revision is not current")
    return sorted(allowed_revision_ids)


def _knowledge_roles(obligations: list[str]) -> list[str]:
    return sorted(
        {
            role
            for obligation in obligations
            for role in _OBLIGATION_KNOWLEDGE_ROLES[obligation]
        }
    )


def _validate_domain_knowledge_intent(value: object) -> dict:
    """Validate the content envelope; the Domain Kernel replays identities."""

    if not isinstance(value, Mapping) or set(value) != (
        DOMAIN_KNOWLEDGE_INTENT_FIELDS
    ):
        raise ValueError("research turn domain knowledge intent is invalid")
    normalized = deepcopy(dict(value))
    observed_hash = normalized.pop("intent_sha256", None)
    if (
        normalized.get("contract")
        != "lca_research_kernel.domain_knowledge_intent/v1"
        or not isinstance(normalized.get("registry_sha256"), str)
        or HEX64.fullmatch(normalized["registry_sha256"]) is None
        or not isinstance(observed_hash, str)
        or HEX64.fullmatch(observed_hash) is None
        or canonical_sha256(normalized) != observed_hash
    ):
        raise ValueError("research turn domain knowledge intent content drifted")
    for field in (
        "selected_knowledge_ids",
        "required_roles",
        "target_object_kinds",
        "relation_kinds",
    ):
        values = normalized.get(field)
        if (
            not isinstance(values, list)
            or values != sorted(values)
            or len(values) != len(set(values))
            or any(not isinstance(item, str) or not item for item in values)
        ):
            raise ValueError("research turn domain knowledge intent is invalid")
    if len(normalized["selected_knowledge_ids"]) > 8:
        raise ValueError("research turn domain knowledge intent is too broad")
    normalized["intent_sha256"] = observed_hash
    return normalized


def compile_research_turn_frame(
    project_view: Mapping[str, object],
    *,
    prior_result: Mapping[str, object] | None,
    allowed_revision_ids: list[str],
    semantic_proposal: Mapping[str, object] | None = None,
    domain_knowledge_intent: Mapping[str, object] | None = None,
) -> dict:
    """Compile one untrusted semantic proposal against accepted project state."""

    if not isinstance(project_view, Mapping):
        raise ValueError("Project Record view is invalid")
    project_id = project_view.get("project_id")
    project_state_sha256 = project_view.get("last_event_sha256")
    if (
        not isinstance(project_id, str)
        or not project_id
        or not isinstance(project_state_sha256, str)
        or HEX64.fullmatch(project_state_sha256) is None
    ):
        raise ValueError("Project Record turn identity is invalid")
    turn = _active_turn(project_view)
    prior_sha256 = _validate_prior_result(turn, prior_result)
    claims = _prior_claims(prior_result)
    allowed = _validate_allowed_revisions(project_view, allowed_revision_ids)

    if semantic_proposal is None:
        operation = "extract"
        obligations = ["direct_resolution", "source_boundary"]
        referent_claim_id = None
    else:
        if not isinstance(semantic_proposal, Mapping) or set(
            semantic_proposal
        ) != PROPOSAL_FIELDS:
            raise ValueError("research turn semantic proposal fields are unsupported")
        operation = semantic_proposal.get("cognitive_operation")
        obligations = semantic_proposal.get("answer_obligations")
        referent_claim_id = semantic_proposal.get("referent_claim_id")
        if operation not in COGNITIVE_OPERATIONS:
            raise ValueError("research turn cognitive operation is unsupported")
        if (
            not isinstance(obligations, list)
            or not obligations
            or len(obligations) != len(set(obligations))
            or any(obligation not in ANSWER_OBLIGATIONS for obligation in obligations)
        ):
            raise ValueError("research turn answer obligations are unsupported")
        obligations = list(obligations)

    if referent_claim_id is not None:
        if (
            not isinstance(referent_claim_id, str)
            or referent_claim_id not in claims
            or prior_sha256 is None
        ):
            raise ValueError("research turn accepted claim referent is unavailable")
        claim = claims[referent_claim_id]
        referent = {
            "kind": "accepted_claim",
            "identity": referent_claim_id,
            "source_sha256": canonical_sha256(claim),
        }
    elif prior_sha256 is not None:
        referent = {
            "kind": "accepted_result",
            "identity": prior_sha256,
            "source_sha256": prior_sha256,
        }
    else:
        referent = {
            "kind": "project_goal",
            "identity": project_id,
            "source_sha256": None,
        }

    frame = {
        "contract": (
            FRAME_CONTRACT_V2
            if domain_knowledge_intent is not None
            else FRAME_CONTRACT
        ),
        "turn_sha256": turn["turn_sha256"],
        "project_state_sha256": project_state_sha256,
        "interaction_kind": "followup" if prior_sha256 is not None else "initial",
        "cognitive_operation": operation,
        "referent": referent,
        "answer_obligations": obligations,
        "required_knowledge_roles": _knowledge_roles(obligations),
        "allowed_revision_ids": allowed,
        "claim_ceiling": "source_bound_bounded",
        "ambiguities": [],
        "stop_condition": "all_obligations_satisfied_or_explicitly_bounded",
    }
    if domain_knowledge_intent is not None:
        frame["domain_knowledge_intent"] = _validate_domain_knowledge_intent(
            domain_knowledge_intent
        )
    frame["frame_sha256"] = canonical_sha256(frame)
    return validate_research_turn_frame(
        frame,
        project_view=project_view,
        prior_result=prior_result,
    )


def require_research_turn_obligations(
    frame: Mapping[str, object],
    *,
    required_obligations: list[str],
    project_view: Mapping[str, object],
    prior_result: Mapping[str, object] | None,
) -> dict:
    """Add machine-owned delivery obligations without changing turn identity."""

    normalized = validate_research_turn_frame(
        frame,
        project_view=project_view,
        prior_result=prior_result,
    )
    if (
        not isinstance(required_obligations, list)
        or any(value not in ANSWER_OBLIGATIONS for value in required_obligations)
    ):
        raise ValueError("required research turn obligations are unsupported")
    obligations = list(normalized["answer_obligations"])
    obligations.extend(
        value for value in required_obligations if value not in obligations
    )
    normalized["answer_obligations"] = obligations
    normalized["required_knowledge_roles"] = _knowledge_roles(obligations)
    normalized.pop("frame_sha256")
    normalized["frame_sha256"] = canonical_sha256(normalized)
    return validate_research_turn_frame(
        normalized,
        project_view=project_view,
        prior_result=prior_result,
    )


def validate_research_turn_frame(
    value: Mapping[str, object],
    *,
    project_view: Mapping[str, object],
    prior_result: Mapping[str, object] | None,
) -> dict:
    """Validate a frame against the exact accepted state that authored it."""

    if not isinstance(value, Mapping) or frozenset(value) not in {
        frozenset(FRAME_FIELDS),
        frozenset(FRAME_V2_FIELDS),
    }:
        raise ValueError("research turn frame fields are unsupported")
    normalized = deepcopy(dict(value))
    observed_hash = normalized.pop("frame_sha256", None)
    if (
        not isinstance(observed_hash, str)
        or HEX64.fullmatch(observed_hash) is None
        or canonical_sha256(normalized) != observed_hash
    ):
        raise ValueError("research turn frame content hash drifted")
    turn = _active_turn(project_view)
    prior_sha256 = _validate_prior_result(turn, prior_result)
    if (
        normalized.get("contract")
        not in {FRAME_CONTRACT, FRAME_CONTRACT_V2}
        or normalized.get("turn_sha256") != turn["turn_sha256"]
        or normalized.get("project_state_sha256")
        != project_view.get("last_event_sha256")
        or normalized.get("interaction_kind")
        != ("followup" if prior_sha256 is not None else "initial")
        or normalized.get("cognitive_operation") not in COGNITIVE_OPERATIONS
        or normalized.get("claim_ceiling") != "source_bound_bounded"
        or normalized.get("ambiguities") != []
        or normalized.get("stop_condition")
        != "all_obligations_satisfied_or_explicitly_bounded"
    ):
        raise ValueError("research turn frame state binding drifted")
    if normalized["contract"] == FRAME_CONTRACT:
        if "domain_knowledge_intent" in normalized:
            raise ValueError("research turn frame contract drifted")
    else:
        if "domain_knowledge_intent" not in normalized:
            raise ValueError("research turn frame contract drifted")
        normalized["domain_knowledge_intent"] = (
            _validate_domain_knowledge_intent(
                normalized["domain_knowledge_intent"]
            )
        )
    obligations = normalized.get("answer_obligations")
    if (
        not isinstance(obligations, list)
        or not obligations
        or len(obligations) != len(set(obligations))
        or any(obligation not in ANSWER_OBLIGATIONS for obligation in obligations)
        or normalized.get("required_knowledge_roles") != _knowledge_roles(obligations)
    ):
        raise ValueError("research turn frame obligations drifted")
    if normalized.get("allowed_revision_ids") != _validate_allowed_revisions(
        project_view, normalized.get("allowed_revision_ids")
    ):
        raise ValueError("research turn frame allowed revisions drifted")
    referent = normalized.get("referent")
    if not isinstance(referent, Mapping) or set(referent) != {
        "kind",
        "identity",
        "source_sha256",
    }:
        raise ValueError("research turn frame referent is invalid")
    if referent.get("kind") == "accepted_claim":
        claim = _prior_claims(prior_result).get(referent.get("identity"))
        if (
            claim is None
            or prior_sha256 is None
            or referent.get("source_sha256") != canonical_sha256(claim)
        ):
            raise ValueError("research turn accepted claim referent drifted")
    elif referent.get("kind") == "accepted_result":
        if (
            prior_sha256 is None
            or referent.get("identity") != prior_sha256
            or referent.get("source_sha256") != prior_sha256
        ):
            raise ValueError("research turn accepted result referent drifted")
    elif referent.get("kind") == "project_goal":
        if (
            prior_sha256 is not None
            or referent.get("identity") != project_view.get("project_id")
            or referent.get("source_sha256") is not None
        ):
            raise ValueError("research turn project goal referent drifted")
    else:
        raise ValueError("research turn referent kind is unsupported")
    normalized["frame_sha256"] = observed_hash
    return normalized
