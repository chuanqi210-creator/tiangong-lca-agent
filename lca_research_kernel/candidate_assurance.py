"""Scientific candidate evaluation shared by Assurance and accepted-state storage."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Mapping

from .change_contracts import (
    ACCEPTANCE_CONTRACT,
    CANDIDATE_CONTRACT,
    MATERIAL_LOCATOR_AUTHORITY_BINDING,
    material_locator_authority_view,
    validate_project_candidate,
)
from .content_identity import canonical_json_bytes, canonical_sha256
from .lca_domain_kernel import CLAIM_CLASSES, MODALITY_TO_CLAIM_CLASS, LCADomainKernel
from .material_gap_closure import MaterialGapClosure


CANDIDATE_ONLY_AUTHORITIES = {
    "model_candidate_only",
    "capability_sourced_result",
    "conversation_memory",
    "optional_council_strategy",
}


def evaluate_candidate_change(
    candidate: Mapping[str, object],
    project_view: Mapping[str, object],
    *,
    domain_kernel: object | None = None,
    verified_content: Mapping[str, object] = None,
) -> dict:
    domain_kernel = domain_kernel or LCADomainKernel()
    errors: list[str] = []
    verified_manifest = None
    if not isinstance(candidate, Mapping) or candidate.get(
        "contract"
    ) != CANDIDATE_CONTRACT:
        errors.append("unsupported_candidate_contract")
    change_type = candidate.get("change_type")
    if change_type not in {
        "fact.upserted",
        "user_turn.activated",
        "decision.recorded",
        "risk.upserted",
        "result.accepted",
        "route.updated",
        "material.registered",
        "material.revision_accepted",
        "material.scope_updated",
        "material.withdrawn",
        "material.restored",
        "evidence.locator_accepted",
        "material.use_decision_recorded",
    }:
        errors.append("unsupported_candidate_change_type")
    fact = candidate.get("fact")
    if change_type == "fact.upserted":
        if not isinstance(fact, Mapping):
            errors.append("candidate_fact_absent")
        else:
            if fact.get("fact_id") == "active-user-turn":
                errors.append("active_user_turn_requires_control_change")
            classification = fact.get("classification")
            source = fact.get("source")
            authority = source.get("authority") if isinstance(source, Mapping) else None
            if classification == "official" and authority in CANDIDATE_ONLY_AUTHORITIES:
                errors.append("candidate_source_cannot_create_official_fact")
            if fact.get("domain_kind") == "lca_material":
                material = {
                    **deepcopy(dict(fact)),
                    "material_id": fact.get("fact_id"),
                    "declared_classification": classification,
                }
                classification_result = domain_kernel.classify_material(
                    material
                )
                if classification_result["status"] != "valid":
                    errors.append("invalid_lca_material")
            elif fact.get("domain_kind") == "material_gap":
                try:
                    MaterialGapClosure().resolve(fact.get("value"))
                except (TypeError, ValueError):
                    errors.append("material_gap_invalid")
                if classification != "missing":
                    errors.append("material_gap_classification_invalid")
    elif change_type == "user_turn.activated":
        try:
            normalized = validate_project_candidate(candidate)
            turn = normalized["fact"]["value"]
            prior = (project_view.get("facts") or {}).get("active-user-turn")
            prior_value = prior.get("value") if isinstance(prior, Mapping) else None
            if (
                isinstance(prior_value, Mapping)
                and prior_value.get("input_id") == turn.get("input_id")
                and prior_value.get("turn_sha256") != turn.get("turn_sha256")
            ):
                errors.append("active_user_turn_input_identity_drifted")
        except (KeyError, TypeError, ValueError):
            errors.append("active_user_turn_control_invalid")
    elif change_type == "decision.recorded":
        decision = candidate.get("decision")
        if not isinstance(decision, Mapping):
            errors.append("candidate_decision_absent")
        elif decision.get("decision_kind") == "factual_data_availability":
            source = decision.get("source")
            gap_ids = decision.get("gap_ids")
            facts = project_view.get("facts")
            if (
                decision.get("decision") != "unavailable"
                or not isinstance(source, Mapping)
                or source.get("authority") != "user_explicit_decision"
                or not isinstance(gap_ids, list)
                or not gap_ids
                or len(gap_ids) != len(set(gap_ids))
                or any(not isinstance(gap_id, str) for gap_id in gap_ids)
                or not isinstance(facts, Mapping)
            ):
                errors.append("factual_data_availability_decision_invalid")
            else:
                for gap_id in gap_ids:
                    gap_fact = facts.get("material-gap:" + gap_id)
                    gap = (
                        gap_fact.get("value")
                        if isinstance(gap_fact, Mapping)
                        else None
                    )
                    existing_resolution = facts.get(
                        "material-gap-resolution:" + gap_id
                    )
                    if (
                        not isinstance(gap, Mapping)
                        or gap.get("gap_id") != gap_id
                        or gap.get("user_data_status")
                        not in {"unknown", "available"}
                        or isinstance(existing_resolution, Mapping)
                    ):
                        errors.append(
                            "factual_data_availability_gap_binding_invalid"
                        )
                        break
    elif change_type == "material.registered":
        try:
            material = candidate.get("material")
            from .material_sources import validate_research_material

            material = validate_research_material(material)  # type: ignore[arg-type]
            if material["authority"] != "user_supplied_project_input":
                errors.append("user_material_authority_invalid")
            if candidate.get("scientific_status") != "accepted_project_input":
                errors.append("user_material_scientific_status_invalid")
        except (KeyError, ValueError, TypeError):
            errors.append("material_candidate_invalid")
    elif change_type == "material.revision_accepted":
        try:
            from .material_sources import validate_material_revision

            revision = validate_material_revision(  # type: ignore[arg-type]
                candidate.get("material_revision")
            )
            turn_scope_sha256 = candidate.get("turn_scope_sha256")
            if turn_scope_sha256 is not None and (
                not isinstance(turn_scope_sha256, str)
                or len(turn_scope_sha256) != 64
                or any(character not in "0123456789abcdef" for character in turn_scope_sha256)
            ):
                errors.append("material_turn_scope_binding_invalid")
            receipt_ref = candidate.get("processing_receipt_ref")
            if not isinstance(receipt_ref, Mapping):
                errors.append("material_processing_receipt_ref_invalid")
            receipt_sha256 = candidate.get("processing_receipt_sha256")
            if (
                not isinstance(receipt_sha256, str)
                or len(receipt_sha256) != 64
                or receipt_ref.get("sha256") != receipt_sha256
            ):
                errors.append("material_processing_receipt_binding_invalid")
            materials = project_view.get("materials")
            material = (
                materials.get(revision["material_id"])
                if isinstance(materials, Mapping)
                else None
            )
            if not isinstance(material, Mapping):
                errors.append("material_revision_material_absent")
            elif material.get("current_revision_id") not in {
                revision["revision_id"],
                revision.get("parent_revision_id"),
            }:
                errors.append("material_revision_not_current")
            if isinstance(material, Mapping):
                from .material_sources import active_user_turn_sha256

                if material.get("scope") == "turn":
                    active_turn = active_user_turn_sha256(project_view)
                    if (
                        active_turn is None
                        or turn_scope_sha256 != active_turn
                    ):
                        errors.append("material_turn_scope_binding_absent_or_stale")
                elif turn_scope_sha256 is not None:
                    errors.append("project_material_has_turn_scope_binding")
            if not isinstance(verified_content, Mapping):
                errors.append("material_verified_content_absent")
            else:
                from .material_sources import validate_processing_receipt

                receipt = validate_processing_receipt(
                    verified_content.get("content")  # type: ignore[arg-type]
                )
                verified_ref = verified_content.get("content_ref")
                verified_sha256 = hashlib.sha256(
                    canonical_json_bytes(receipt) + b"\n"
                ).hexdigest()
                if (
                    verified_content.get("content_kind")
                    != "material_processing_receipt"
                    or verified_ref != receipt_ref
                    or verified_sha256 != receipt_sha256
                    or receipt.get("revision_id") != revision["revision_id"]
                    or receipt.get("input_sha256")
                    != revision["artifact_ref"]["sha256"]
                    or receipt.get("status") != revision["processing_status"]
                    or receipt.get("output_refs")
                    != revision["extraction_refs"]
                ):
                    errors.append("material_verified_content_drifted")
                else:
                    verified_manifest = {
                        "contract": (
                            "lca_research_kernel.verified_content_manifest/v3"
                        ),
                        "content_kind": "material_processing_receipt",
                        "subject_id": revision["revision_id"],
                        "content_ref": deepcopy(dict(verified_ref)),
                        "content_sha256": verified_sha256,
                    }
        except (KeyError, ValueError, TypeError):
            errors.append("material_revision_candidate_invalid")
    elif change_type == "evidence.locator_accepted":
        locator = candidate.get("evidence_locator")
        binding = candidate.get("evidence_binding")
        if (
            not isinstance(locator, Mapping)
            or locator.get("scientific_status") != "accepted_evidence"
            or not isinstance(locator.get("assurance_acceptance_ref"), Mapping)
        ):
            errors.append("accepted_evidence_locator_invalid")
        else:
            acceptance_basis = {
                "locator_id": locator.get("locator_id"),
                "revision_id": locator.get("revision_id"),
                "locator_kind": locator.get("locator_kind"),
                "locator": deepcopy(locator.get("locator")),
                "content_hash": locator.get("content_hash"),
                "scientific_status": "accepted_project_input",
            }
            expected_acceptance_sha256 = canonical_sha256(acceptance_basis)
            acceptance_ref = locator.get("assurance_acceptance_ref")
            if (
                not isinstance(binding, Mapping)
                or binding.get("locator_id") != locator.get("locator_id")
                or binding.get("content_hash") != locator.get("content_hash")
                or binding.get("source_span")
                != {
                    "kind": locator.get("locator_kind"),
                    "locator": locator.get("locator"),
                }
                or not isinstance(acceptance_ref, Mapping)
                or acceptance_ref.get("artifact_id")
                != expected_acceptance_sha256
                or acceptance_ref.get("sha256")
                != expected_acceptance_sha256
            ):
                errors.append("accepted_evidence_binding_invalid")
            revisions = project_view.get("material_revisions")
            if (
                not isinstance(revisions, Mapping)
                or locator.get("revision_id") not in revisions
            ):
                errors.append("accepted_evidence_revision_absent")
            if not isinstance(verified_content, Mapping):
                errors.append("evidence_verified_content_absent")
            else:
                evidence_unit = verified_content.get("content")
                verified_ref = verified_content.get("content_ref")
                verified_sha256 = (
                    hashlib.sha256(
                        canonical_json_bytes(evidence_unit) + b"\n"
                    ).hexdigest()
                    if isinstance(evidence_unit, Mapping)
                    else None
                )
                if (
                    verified_content.get("content_kind") != "evidence_unit"
                    or not isinstance(verified_ref, Mapping)
                    or set(verified_ref) != {"artifact_id", "sha256"}
                    or verified_ref.get("artifact_id")
                    != verified_ref.get("sha256")
                    or not isinstance(verified_ref.get("sha256"), str)
                    or len(verified_ref["sha256"]) != 64
                    or any(
                        character not in "0123456789abcdef"
                        for character in verified_ref["sha256"]
                    )
                    or verified_sha256 != locator.get("content_hash")
                    or evidence_unit.get("source_span")
                    != binding.get("source_span")
                ):
                    errors.append("evidence_verified_content_drifted")
                else:
                    revision = revisions[locator["revision_id"]]
                    if verified_ref not in revision.get("extraction_refs", []):
                        errors.append("evidence_verified_content_drifted")
                    if not errors:
                        verified_manifest = {
                            "contract": (
                                "lca_research_kernel.verified_content_manifest/v3"
                            ),
                            "content_kind": "evidence_unit",
                            "subject_id": locator.get("locator_id"),
                            "content_ref": deepcopy(dict(verified_ref)),
                            "content_sha256": verified_sha256,
                        }
    elif change_type == "material.use_decision_recorded":
        decision = candidate.get("material_use_decision")
        if not isinstance(decision, Mapping):
            errors.append("material_use_decision_invalid")
        else:
            from .material_sources import validate_material_use_decision

            locators = project_view.get("evidence_locators")
            try:
                normalized_decision = validate_material_use_decision(
                    decision,
                    locator_index=(
                        locators if isinstance(locators, Mapping) else {}
                    ),
                )
                from .material_sources import material_revision_context_eligibility

                revisions = project_view.get("material_revisions")
                materials = project_view.get("materials")
                for revision_id in normalized_decision[
                    "allowed_revision_ids"
                ]:
                    revision = (
                        revisions.get(revision_id)
                        if isinstance(revisions, Mapping)
                        else None
                    )
                    material = (
                        materials.get(revision.get("material_id"))
                        if isinstance(revision, Mapping)
                        and isinstance(materials, Mapping)
                        else None
                    )
                    if (
                        not isinstance(material, Mapping)
                        or material.get("current_revision_id") != revision_id
                        or material.get("deleted_at") is not None
                        or not material_revision_context_eligibility(
                            project_view, revision_id
                        )[0]
                    ):
                        errors.append("material_use_scope_invalid")
            except (KeyError, ValueError, TypeError):
                errors.append("material_use_decision_invalid")
            if candidate.get("decision_sha256") != canonical_sha256(decision):
                errors.append("material_use_decision_binding_invalid")
    elif change_type == "material.scope_updated":
        if candidate.get("scope") not in {"turn", "project"}:
            errors.append("material_scope_invalid")
    elif change_type == "material.withdrawn":
        if not isinstance(candidate.get("deleted_at"), str):
            errors.append("material_withdrawal_time_invalid")
    elif change_type == "material.restored":
        if candidate.get("deleted_at") is not None:
            errors.append("material_restore_status_invalid")
    if change_type == "result.accepted":
        result = candidate.get("result")
        assessment = (
            result.get("domain_acceptance")
            if isinstance(result, Mapping)
            else None
        )
        direct_explanation = (
            isinstance(assessment, Mapping)
            and assessment.get("contract")
            == "lca_research_kernel.direct_answer_assessment/v1"
        )
        tiangong_native_result = (
            isinstance(assessment, Mapping)
            and assessment.get("contract")
            == "lca_research_kernel.tiangong_native_contract_assessment/v1"
        )
        if tiangong_native_result:
            facts = project_view.get("facts")
            matching_facts = [
                fact
                for fact in (facts.values() if isinstance(facts, Mapping) else [])
                if isinstance(fact, Mapping)
                and fact.get("domain_kind") == "capability_result"
                and isinstance(fact.get("value"), Mapping)
                and fact["value"].get("capability_id")
                == "tiangong.native.tidas-sdk"
                and fact["value"].get("content_sha256")
                == assessment.get("content_sha256")
            ]
            recomputed = (
                domain_kernel.evaluate_tiangong_native_contract_result(
                    matching_facts[0]["value"]
                )
                if len(matching_facts) == 1
                else None
            )
            expected_missing = [
                "exchange_provider",
                "lca_method",
                "quantity_unit",
            ]
            native_authority = (
                result.get("native_authority")
                if isinstance(result, Mapping)
                else None
            )
            source_basis = (
                result.get("source_basis")
                if isinstance(result, Mapping)
                else None
            )
            if (
                not isinstance(result, Mapping)
                or recomputed != assessment
                or assessment.get("status") != "passed"
                or assessment.get("errors") != []
                or assessment.get("may_promote_to_official") is not False
                or result.get("status")
                != "formal_limited_result_accepted"
                or result.get("claim_ceiling")
                != "capability_contract_only"
                or result.get("answer") != assessment.get("bounded_answer")
                or result.get("bounded_conclusion")
                != assessment.get("bounded_answer")
                or result.get("values") != []
                or result.get("computed_values") != []
                or result.get("scientific_source_sha256s") != []
                or result.get("missing_facts") != expected_missing
                or result.get("missing_fields") != expected_missing
                or result.get("scientific_boundary")
                != assessment.get("scientific_boundary")
                or not isinstance(native_authority, Mapping)
                or native_authority.get("provider") != "TianGong"
                or native_authority.get("capability_id")
                != assessment.get("capability_id")
                or native_authority.get("source_identity")
                != assessment.get("source_identity")
                or native_authority.get("source_version")
                != assessment.get("source_version")
                or native_authority.get("source_integrity")
                != assessment.get("source_integrity")
                or native_authority.get("content_sha256")
                != assessment.get("content_sha256")
                or not isinstance(source_basis, list)
                or len(source_basis) != 1
                or not isinstance(source_basis[0], Mapping)
                or source_basis[0].get("identity")
                != assessment.get("source_identity")
                or source_basis[0].get("version")
                != assessment.get("source_version")
                or source_basis[0].get("integrity")
                != assessment.get("source_integrity")
                or source_basis[0].get("authority") != "TianGong"
                or source_basis[0].get("capability_id")
                != assessment.get("capability_id")
                or source_basis[0].get("content_sha256")
                != assessment.get("content_sha256")
            ):
                errors.append("accepted_tiangong_native_result_invalid")
        elif direct_explanation:
            direct_status = (
                result.get("status") if isinstance(result, Mapping) else None
            )
            direct_ceiling = (
                result.get("claim_ceiling")
                if isinstance(result, Mapping)
                else None
            )
            if (
                not isinstance(result, Mapping)
                or (direct_status, direct_ceiling)
                not in {
                    ("candidate_only", "candidate_only"),
                    (
                        "formal_limited_result_accepted",
                        "calculation_structure_only",
                    ),
                }
                or not isinstance(result.get("answer"), str)
                or not result["answer"].strip()
                or result.get("values") != []
                or result.get("official_object_ids") != []
                or result.get("scientific_source_sha256s") != []
            ):
                errors.append("accepted_direct_explanation_invalid")
            if direct_status == "formal_limited_result_accepted" and (
                not isinstance(result.get("missing_facts"), list)
                or not result["missing_facts"]
                or result.get("missing_facts")
                != assessment.get("missing_fields")
                or not isinstance(result.get("resume_conditions"), list)
                or not result["resume_conditions"]
                or result.get("computed_values") != []
            ):
                errors.append("accepted_direct_limited_result_invalid")
            if direct_status == "formal_limited_result_accepted":
                facts = project_view.get("facts")
                accepted_resolutions = []
                expected_missing = assessment.get("missing_fields")
                direct_model_analysis_sha256 = result.get(
                    "direct_model_analysis_sha256"
                )
                accepted_model_analysis = (
                    facts.get("model-analysis")
                    if isinstance(facts, Mapping)
                    else None
                )
                if (
                    not isinstance(direct_model_analysis_sha256, str)
                    or direct_model_analysis_sha256
                    != canonical_sha256(accepted_model_analysis)
                ):
                    errors.append("accepted_direct_model_analysis_drifted")
                if isinstance(facts, Mapping) and isinstance(
                    expected_missing, list
                ):
                    for fact in facts.values():
                        gap = (
                            fact.get("value")
                            if isinstance(fact, Mapping)
                            and fact.get("domain_kind") == "material_gap"
                            else None
                        )
                        role = (
                            gap.get("required_role")
                            if isinstance(gap, Mapping)
                            else None
                        )
                        gap_id = gap.get("gap_id") if isinstance(gap, Mapping) else None
                        gap_source = fact.get("source")
                        if (
                            role not in expected_missing
                            or not isinstance(gap_id, str)
                            or not isinstance(gap_source, Mapping)
                            or gap_source.get("model_analysis_sha256")
                            != direct_model_analysis_sha256
                        ):
                            continue
                        resolution_fact = facts.get(
                            "material-gap-resolution:" + gap_id
                        )
                        resolution = (
                            resolution_fact.get("value")
                            if isinstance(resolution_fact, Mapping)
                            else None
                        )
                        if (
                            isinstance(resolution, Mapping)
                            and resolution.get("required_role") == role
                            and resolution.get("classification") == "missing"
                            and resolution_fact.get("source", {}).get(
                                "gap_sha256"
                            )
                            == canonical_sha256(gap)
                        ):
                            accepted_resolutions.append(deepcopy(dict(resolution)))
                projected_resolutions = result.get("gap_resolutions")
                if (
                    not isinstance(projected_resolutions, list)
                    or any(
                        not isinstance(value, Mapping)
                        for value in (projected_resolutions or [])
                    )
                    or sorted(
                        canonical_sha256(value)
                        for value in projected_resolutions
                        if isinstance(value, Mapping)
                    )
                    != sorted(
                        canonical_sha256(value)
                        for value in accepted_resolutions
                    )
                    or len(accepted_resolutions)
                    != len(expected_missing or [])
                ):
                    errors.append("accepted_direct_gap_resolution_drifted")
            if (
                assessment.get("status") != "passed"
                or assessment.get("may_promote_to_official") is not False
                or assessment.get("errors") != []
            ):
                errors.append("accepted_direct_explanation_assessment_invalid")
            direct_identities = [
                result.get(key)
                for key in (
                    "interaction_turn_sha256",
                    "initial_request_sha256",
                )
                if isinstance(result, Mapping) and result.get(key) is not None
            ]
            if (
                len(direct_identities) != 1
                or not isinstance(direct_identities[0], str)
                or len(direct_identities[0]) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in direct_identities[0]
                )
            ):
                errors.append("accepted_direct_explanation_identity_invalid")
        elif (
            not isinstance(result, Mapping)
            or result.get("status")
            not in {
                "formal_result_accepted",
                "formal_limited_result_accepted",
            }
            or result.get("claim_ceiling")
            not in {
                "source_bounded_interpretation",
                "calculation_structure_only",
            }
        ):
            errors.append("accepted_result_exceeded_candidate_boundary")
        if not direct_explanation and not tiangong_native_result and (
            not isinstance(assessment, Mapping)
            or assessment.get("contract")
            not in {
                "lca_research_kernel.generic_source_candidate_evaluation/v1",
                "lca_research_kernel.material_answer_assessment/v1",
                "lca_research_kernel.material_answer_assessment/v2",
            }
            or assessment.get("status") != "passed"
            or assessment.get("may_promote_to_official") is not False
        ):
            errors.append("accepted_result_domain_assessment_invalid")
        if (
            not direct_explanation
            and not tiangong_native_result
            and isinstance(result, Mapping)
            and isinstance(assessment, Mapping)
        ):
            material_answer = assessment.get("contract") in {
                "lca_research_kernel.material_answer_assessment/v1",
                "lca_research_kernel.material_answer_assessment/v2",
            }
            formal_fields = {
                "status": "formal_result_status",
                "claim_ceiling": "formal_claim_ceiling",
                "source_basis": "source_basis",
                "claim_classifications": "claim_classifications",
                "claim_modality_resolutions": "claim_modality_resolutions",
                "missing_facts": "missing_facts",
                "resume_conditions": "resume_conditions",
                "computed_values": "computed_values",
            }
            if not material_answer and any(
                result.get(result_field)
                != assessment.get(assessment_field)
                for result_field, assessment_field in formal_fields.items()
            ):
                errors.append("accepted_result_domain_projection_drifted")
            source_basis = result.get("source_basis")
            if not isinstance(source_basis, list) or not source_basis:
                errors.append("accepted_result_source_basis_invalid")
            claim_classes = result.get("claim_classifications")
            if not material_answer and (
                not isinstance(claim_classes, list)
                or any(
                    not isinstance(row, Mapping)
                    or row.get("classification") not in CLAIM_CLASSES
                    or not isinstance(row.get("statement"), str)
                    or not row["statement"].strip()
                    for row in (claim_classes or [])
                )
            ):
                errors.append("accepted_result_claim_classification_invalid")
            claim_statements = result.get("claim_statements")
            if not material_answer and isinstance(claim_statements, list):
                classified = {
                    (
                        row.get("statement"),
                        row.get("classification"),
                    )
                    for row in (claim_classes or [])
                    if isinstance(row, Mapping)
                }
                for claim in claim_statements:
                    expected_class = (
                        MODALITY_TO_CLAIM_CLASS.get(
                            str(claim.get("modality"))
                        )
                        if isinstance(claim, Mapping)
                        else None
                    )
                    if (
                        not isinstance(claim, Mapping)
                        or expected_class is None
                        or (
                            claim.get("statement"),
                            expected_class,
                        )
                        not in classified
                    ):
                        errors.append(
                            "accepted_result_claim_classification_missing"
                        )
                        break
            if result.get("status") == "formal_limited_result_accepted":
                if (
                    not isinstance(result.get("missing_facts"), list)
                    or not result["missing_facts"]
                    or not isinstance(result.get("resume_conditions"), list)
                    or not result["resume_conditions"]
                    or result.get("claim_ceiling")
                    != "calculation_structure_only"
                    or result.get("computed_values") != []
                ):
                    errors.append("accepted_limited_result_invalid")
        if isinstance(result, Mapping):
            effect_receipt = result.get("permission_effect_receipt")
            if (
                not isinstance(effect_receipt, Mapping)
                or effect_receipt.get("contract")
                != "lca_research_kernel.formal_result_effect_receipt/v1"
                or effect_receipt.get("external_writes") != 0
                or effect_receipt.get("forbidden_effects") != 0
            ):
                errors.append("accepted_result_effect_receipt_invalid")
    proposed_at = candidate.get("proposed_at")
    if not isinstance(proposed_at, str) or not proposed_at.strip():
        errors.append("candidate_proposed_at_absent")
        proposed_at = "unknown"
    acceptance = {
        "contract": ACCEPTANCE_CONTRACT,
        "decision": "accepted" if not errors else "rejected",
        "candidate_sha256": canonical_sha256(candidate),
        "evaluator": "assurance_boundary",
        "evaluated_at": proposed_at,
        "errors": errors,
        "may_promote_candidate_to_official": False,
    }
    if change_type in {
        "material.registered",
        "material.revision_accepted",
        "material.scope_updated",
        "material.withdrawn",
        "material.restored",
        "evidence.locator_accepted",
        "material.use_decision_recorded",
    }:
        if change_type == "evidence.locator_accepted" and isinstance(
            candidate.get("evidence_locator"), Mapping
        ):
            locator_authority = material_locator_authority_view(
                project_view,
                str(candidate["evidence_locator"].get("revision_id")),
            )
            acceptance["project_view_binding"] = (
                MATERIAL_LOCATOR_AUTHORITY_BINDING
            )
            acceptance["project_view_sha256"] = canonical_sha256(
                locator_authority
            )
        else:
            acceptance["project_view_sha256"] = canonical_sha256(project_view)
    if verified_manifest is not None:
        acceptance["verified_content_manifest"] = verified_manifest
        acceptance["content_acceptance_sha256"] = canonical_sha256(
            {
                "candidate_sha256": acceptance["candidate_sha256"],
                "project_view_sha256": acceptance["project_view_sha256"],
                "verified_content_manifest": verified_manifest,
            }
        )
    return acceptance
