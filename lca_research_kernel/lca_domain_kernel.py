"""Case-independent LCA fact, scope, calculation and claim rules."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import re
from copy import deepcopy
from typing import Mapping, Sequence

from .claim_semantics import (
    CLAIM_CLASSES,
    CLAIM_MODALITIES,
    MODALITY_TO_CLAIM_CLASS,
    resolve_claim_modality,
    required_user_visible_legal_modalities,
    unbound_user_visible_legal_clauses,
)
from .official_operation_identity import (
    official_public_source_operation_id,
)
from .official_source_binding import validate_official_source_observation
from .material_sources import (
    validate_material_use_decision,
    validate_source_bound_material_revision,
)
from .answer_quality import (
    build_answer_quality_basis,
    evaluate_answer_quality,
    required_material_limitations,
)
from .research_program import validate_research_program
from .answer_rendering import (
    normalize_rendered_clause_text,
    render_typed_material_answer,
)


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DIRECT_LCA_MISSING_FIELDS = frozenset(
    {
        "allocation_rule",
        "functional_unit",
        "geographical_scope",
        "impact_assessment_method",
        "inventory_data",
        "reference_flow",
        "scenario_parameters",
        "system_boundary",
        "technology_scope",
        "temporal_scope",
    }
)
FACT_CLASSES = {"official", "candidate", "assumption", "computed", "missing"}
CLASS_REQUIREMENTS = {
    "official": (
        "source_ref",
        "authority",
        "identity",
        "version",
        "value",
        "unit",
    ),
    "candidate": ("source_ref", "value", "unit"),
    "assumption": ("statement", "basis", "value", "unit"),
    "computed": ("transformation", "input_refs", "value", "unit"),
    "missing": ("gap_reason", "decision_impact"),
}
def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _present(value: Mapping[str, object], key: str) -> bool:
    if key not in value or value[key] is None:
        return False
    item = value[key]
    if isinstance(item, str):
        return bool(item.strip())
    if isinstance(item, (list, dict)):
        return bool(item)
    return True


def _nested_present(value: Mapping[str, object], dotted: str) -> bool:
    current: object = value
    for part in dotted.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return False
        current = current[part]
    if isinstance(current, str):
        return bool(current.strip())
    if isinstance(current, (list, dict)):
        return bool(current)
    return current is not None


def _is_content_bound_primary_source(row: object) -> bool:
    """Recognize only native TianGong truth at intake.

    Public official sources become authoritative evidence only through the
    source-locked read capability and its accepted receipt.
    """

    if not isinstance(row, Mapping):
        return False
    return row.get("authority") == "TianGong"


def _declared_input_calculations(
    materials: Sequence[object],
) -> tuple[list[dict], list[dict], list[str]]:
    """Validate user-declared inputs and evaluate a tiny deterministic sum DSL."""

    declared: list[dict] = []
    computed: list[dict] = []
    errors: list[str] = []
    by_identity: dict[str, dict] = {}
    transformations: list[tuple[Mapping[str, object], str]] = []
    for material in materials:
        if (
            not isinstance(material, Mapping)
            or material.get("authority") != "user_supplied_project_input"
            or not isinstance(material.get("content"), Mapping)
        ):
            continue
        source_sha256 = material.get("sha256")
        if not isinstance(source_sha256, str) or SHA256_RE.fullmatch(source_sha256) is None:
            errors.append("declared_input_source_hash_invalid")
            continue
        content = material["content"]
        rows = content.get("declared_values", [])
        if not isinstance(rows, list):
            errors.append("declared_input_values_invalid")
            rows = []
        for row in rows:
            if (
                not isinstance(row, Mapping)
                or set(row) != {"identity", "value", "unit"}
                or not isinstance(row.get("identity"), str)
                or not row["identity"].strip()
                or isinstance(row.get("value"), bool)
                or not isinstance(row.get("value"), (int, float))
                or not isinstance(row.get("unit"), str)
                or not row["unit"].strip()
                or row["identity"] in by_identity
            ):
                errors.append("declared_input_value_invalid")
                continue
            normalized = {
                "identity": row["identity"],
                "value": row["value"],
                "unit": row["unit"],
                "source_sha256": source_sha256,
            }
            by_identity[row["identity"]] = normalized
            declared.append(normalized)
        specs = content.get("deterministic_transformations", [])
        if not isinstance(specs, list):
            errors.append("deterministic_transformations_invalid")
            specs = []
        transformations.extend(
            (spec, source_sha256) for spec in specs if isinstance(spec, Mapping)
        )
        if any(not isinstance(spec, Mapping) for spec in specs):
            errors.append("deterministic_transformation_invalid")
    for spec, source_sha256 in transformations:
        identity = spec.get("identity")
        operation = spec.get("operation")
        refs = spec.get("input_refs")
        if (
            set(spec) != {"identity", "operation", "input_refs"}
            or not isinstance(identity, str)
            or not identity.strip()
            or identity in by_identity
            or operation != "sum"
            or not isinstance(refs, list)
            or len(refs) < 2
            or any(not isinstance(ref, str) or ref not in by_identity for ref in refs)
        ):
            errors.append("deterministic_transformation_invalid")
            continue
        inputs = [by_identity[ref] for ref in refs]
        units = {row["unit"] for row in inputs}
        if len(units) != 1:
            errors.append("deterministic_transformation_unit_mismatch")
            continue
        total = sum(row["value"] for row in inputs)
        normalized_total = int(total) if isinstance(total, float) and total.is_integer() else total
        row = {
            "identity": identity,
            "value": normalized_total,
            "unit": inputs[0]["unit"],
            "transformation": "sum(" + ",".join(refs) + ")",
            "input_refs": list(refs),
            "source_sha256": source_sha256,
        }
        by_identity[identity] = row
        computed.append(row)
    return declared, computed, sorted(set(errors))


class LCADomainKernel:
    def evaluate_material_answer_candidate(
        self,
        model_candidate: Mapping[str, object],
        *,
        material_bundles: Sequence[Mapping[str, object]],
        use_decision: Mapping[str, object],
        turn_frame: Mapping[str, object] | None = None,
        governed_knowledge: Sequence[Mapping[str, object]] = (),
        response_language: str | None = None,
        answer_quality_repair_exhausted: bool = False,
        research_program: Mapping[str, object] | None = None,
    ) -> dict:
        """Bind a candidate answer to exact, accepted user-material evidence."""

        if research_program is not None:
            program = validate_research_program(research_program)
            if (
                not isinstance(turn_frame, Mapping)
                or program["turn_sha256"] != turn_frame.get("turn_sha256")
                or program["evidence_plan"]["use_decision_sha256"]
                != _canonical_sha256(use_decision)
                or program["requested_outcome"]["response_language"]
                != (response_language or "en")
            ):
                raise ValueError("research program domain binding drifted")
            semantic_closure = program.get("semantic_closure")
            if isinstance(semantic_closure, Mapping):
                knowledge_ids = sorted(
                    row["knowledge_id"]
                    for row in governed_knowledge
                    if isinstance(row, Mapping)
                    and isinstance(row.get("knowledge_id"), str)
                )
                if knowledge_ids != semantic_closure.get(
                    "selected_governed_knowledge_ids"
                ):
                    raise ValueError("research program domain knowledge drifted")

        if model_candidate.get("contract") == (
            "lca_research_kernel.material_answer_candidate/v2"
        ):
            if not isinstance(turn_frame, Mapping):
                return {
                    "contract": "lca_research_kernel.material_answer_assessment/v2",
                    "status": "rejected",
                    "errors": ["research_turn_frame_absent"],
                    "scientific_status": "accepted_project_input",
                    "claim_ceiling": "source_bounded_interpretation",
                    "may_satisfy_tiangong_official_requirement": False,
                    "may_promote_to_official": False,
                    "citations": [],
                    "admissible_candidate": None,
                    "answer_quality": None,
                }
            return self._evaluate_material_answer_candidate_v2(
                model_candidate,
                material_bundles=material_bundles,
                use_decision=use_decision,
                turn_frame=turn_frame,
                governed_knowledge=governed_knowledge,
                response_language=response_language,
                answer_quality_repair_exhausted=answer_quality_repair_exhausted,
                research_program=research_program,
            )

        errors: list[str] = []
        bundles: list[dict] = []
        for value in material_bundles:
            try:
                bundles.append(validate_source_bound_material_revision(value))
            except ValueError as error:
                message = str(error)
                if "locator content drifted" in message:
                    errors.append("locator_content_drifted")
                elif "locator revision" in message:
                    errors.append("locator_revision_drifted")
                elif "processing input SHA-256" in message:
                    errors.append("processing_input_sha256_drifted")
                else:
                    errors.append("material_bundle_invalid")

        locator_index = {
            locator["locator_id"]: locator
            for bundle in bundles
            for locator in bundle["locators"]
        }
        allowed_locator_ids = {
            locator["locator_id"]
            for bundle in bundles
            if bundle["material_revision"]["revision_id"]
            in set(use_decision.get("allowed_revision_ids") or [])
            for locator in bundle["locators"]
        }
        try:
            decision = validate_material_use_decision(
                use_decision, locator_index=locator_index
            )
        except ValueError as error:
            message = str(error)
            if "not allowed" in message:
                errors.append("selected_material_not_allowed")
            elif "unknown" in message:
                errors.append("selected_evidence_locator_unknown")
            else:
                errors.append("material_use_decision_invalid")
            decision = None

        if model_candidate.get("status") != "candidate_only":
            errors.append("candidate_status_exceeded_boundary")
        answer = model_candidate.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            errors.append("candidate_answer_absent")
            answer = ""
        selected_ids = (
            decision["selected_evidence_locator_ids"]
            if isinstance(decision, Mapping)
            else []
        )
        citation_ids = model_candidate.get("citation_ids")
        if (
            not isinstance(citation_ids, list)
            or not citation_ids
            or len(citation_ids) != len(set(citation_ids))
            or not set(citation_ids).issubset(set(selected_ids))
        ):
            errors.append("candidate_citation_set_drifted")
        cited_ids = citation_ids if isinstance(citation_ids, list) else []
        answer_locator_mention_list = re.findall(r"\[([^\[\]\s]+)\]", answer)
        answer_locator_mentions = set(answer_locator_mention_list)
        # A single source location may support multiple distinct claims.
        # Token uniqueness remains enforced within each claim binding below.
        if answer_locator_mentions != set(cited_ids):
            errors.append("candidate_citation_set_drifted")
        unselected_mentions = answer_locator_mentions.intersection(
            allowed_locator_ids - set(selected_ids)
        )
        if unselected_mentions:
            errors.append("unselected_allowed_citation_laundering")
        claims_value = model_candidate.get("claims")
        rendered_clauses: list[str] = []
        claim_bindings: list[dict[str, object]] = []
        if not isinstance(claims_value, list) or not claims_value:
            errors.append("scientific_claims_absent")
            claims_value = []
        for claim in claims_value:
            if (
                not isinstance(claim, Mapping)
                or set(claim)
                != {"claim_id", "text", "requires_evidence", "citation_tokens"}
                or not isinstance(claim.get("claim_id"), str)
                or not claim["claim_id"].strip()
                or not isinstance(claim.get("text"), str)
                or not claim["text"].strip()
                or claim.get("requires_evidence") is not True
                or not isinstance(claim.get("citation_tokens"), list)
                or not claim["citation_tokens"]
                or len(claim["citation_tokens"])
                != len(set(claim["citation_tokens"]))
            ):
                errors.append("scientific_claim_invalid")
                continue
            rendered_clauses.append(
                claim["text"].strip()
                + " "
                + " ".join(claim["citation_tokens"])
            )
            claim_bindings.append(
                {
                    "claim_text": claim["text"].strip(),
                    "citation_tokens": list(claim["citation_tokens"]),
                }
            )
        rendered_answer = ". ".join(rendered_clauses) + ("." if rendered_clauses else "")
        if answer != rendered_answer or model_candidate.get(
            "bounded_conclusion"
        ) != rendered_answer:
            errors.append("answer_claim_projection_drifted")
        bindings_value = model_candidate.get("claim_citation_bindings")
        bound_ids: list[str] = []
        if not isinstance(bindings_value, list) or not bindings_value:
            errors.append("claim_citation_binding_absent")
            bindings_value = []
        if bindings_value != claim_bindings:
            errors.append("claim_citation_binding_drifted")
        for binding in bindings_value:
            if (
                not isinstance(binding, Mapping)
                or set(binding) != {"claim_text", "citation_tokens"}
                or not isinstance(binding.get("claim_text"), str)
                or not binding["claim_text"].strip()
                or not isinstance(binding.get("citation_tokens"), list)
                or not binding["citation_tokens"]
                or len(binding["citation_tokens"])
                != len(set(binding["citation_tokens"]))
            ):
                errors.append("claim_citation_binding_invalid")
                continue
            claim_text = binding["claim_text"].strip()
            locator_ids = []
            for token in binding["citation_tokens"]:
                if (
                    not isinstance(token, str)
                    or len(token) < 3
                    or token[0] != "["
                    or token[-1] != "]"
                ):
                    errors.append("claim_citation_token_invalid")
                    continue
                locator_ids.append(token[1:-1])
            exact_clause = claim_text + " " + " ".join(
                binding["citation_tokens"]
            )
            if exact_clause not in answer:
                errors.append("claim_citation_binding_absent")
            bound_ids.extend(locator_ids)
        # Reuse across claims is valid when every rendered occurrence belongs to
        # one exact binding.  Multiplicity detects locator tokens smuggled into
        # claim prose without imposing global single-use on a source location.
        if (
            set(bound_ids) != set(cited_ids)
            or Counter(bound_ids) != Counter(answer_locator_mention_list)
        ):
            errors.append("candidate_citation_set_drifted")
        for locator_id in cited_ids:
            if f"[{locator_id}]" not in answer:
                errors.append("answer_citation_id_absent")

        observed_hashes = model_candidate.get("scientific_source_sha256s")
        expected_hashes = {
            locator_index[locator_id]["content_hash"]
            for locator_id in cited_ids
            if locator_id in locator_index
        }
        if (
            not isinstance(observed_hashes, list)
            or set(observed_hashes) != expected_hashes
            or len(observed_hashes) != len(set(observed_hashes))
        ):
            errors.append("candidate_source_hash_drifted")

        bounded = any(
            bundle["material_revision"]["processing_status"]
            in {"partial", "needs_user"}
            for bundle in bundles
        )
        limitations = model_candidate.get("limitations")
        limitations = (
            [item for item in limitations if isinstance(item, str) and item.strip()]
            if isinstance(limitations, list)
            else []
        )
        required_limitations = required_material_limitations(
            use_decision,
            response_language=response_language or "en",
        )
        if required_limitations and limitations != required_limitations:
            errors.append("representative_sampling_limitation_drifted")
        if bounded and not limitations:
            errors.append("processing_limitation_absent")

        missing_facts = []
        if bounded:
            for bundle in bundles:
                receipt = bundle["processing_receipt"]
                if receipt["status"] not in {"partial", "needs_user"}:
                    continue
                reasons = [
                    *receipt["completeness"]["unprocessed_regions"],
                    *receipt["warnings"],
                ]
                missing_facts.extend(
                    {
                        "revision_id": receipt["revision_id"],
                        "reason": reason,
                        "processing_status": receipt["status"],
                    }
                    for reason in reasons
                    if isinstance(reason, str) and reason.strip()
                )
            if not missing_facts and bundles:
                revision = bundles[0]["material_revision"]
                missing_facts.append(
                    {
                        "revision_id": revision["revision_id"],
                        "reason": "processing did not cover the complete material",
                        "processing_status": revision["processing_status"],
                    }
                )
        resume_conditions = [
            {
                "revision_id": missing["revision_id"],
                "condition": "provide or process the missing source region",
            }
            for missing in missing_facts
        ]

        citations = [
            {
                "citation_id": locator_id,
                "locator_id": locator_id,
                "revision_id": locator_index[locator_id]["revision_id"],
                "content_hash": locator_index[locator_id]["content_hash"],
            }
            for locator_id in selected_ids
            if locator_id in set(cited_ids)
            if locator_id in locator_index
        ]
        admissible = None
        if not errors:
            admissible = {
                "status": (
                    "formal_limited_result_accepted"
                    if bounded
                    else "formal_result_accepted"
                ),
                "answer": rendered_answer,
                "bounded_conclusion": rendered_answer,
                "claims": deepcopy(list(claims_value)),
                "claim_citation_bindings": deepcopy(claim_bindings),
                "citation_ids": list(citation_ids),
                "scientific_source_sha256s": list(observed_hashes),
                "runtime_provenance_sha256s": deepcopy(
                    model_candidate.get("runtime_provenance_sha256s", [])
                ),
                "official_object_ids": [],
                "values": [],
                "claim_ceiling": (
                    "calculation_structure_only"
                    if bounded
                    else "source_bounded_interpretation"
                ),
                "citations": citations,
                "source_basis": [
                    {
                        "authority": "user_supplied_project_input",
                        "scientific_status": "accepted_project_input",
                        "material_id": bundle["research_material"]["material_id"],
                        "revision_id": bundle["material_revision"]["revision_id"],
                        "input_artifact_sha256": bundle["material_revision"][
                            "artifact_ref"
                        ]["sha256"],
                    }
                    for bundle in bundles
                ],
                "limitations": list(
                    dict.fromkeys(
                        [
                            *required_limitations,
                            *[missing["reason"] for missing in missing_facts],
                        ]
                    )
                ),
                "missing_facts": missing_facts,
                "resume_conditions": resume_conditions,
                "computed_values": [],
            }
        return {
            "contract": "lca_research_kernel.material_answer_assessment/v1",
            "status": "passed" if not errors else "rejected",
            "errors": sorted(set(errors)),
            "scientific_status": "accepted_project_input",
            "claim_ceiling": (
                "calculation_structure_only"
                if bounded
                else "source_bounded_interpretation"
            ),
            "may_satisfy_tiangong_official_requirement": False,
            "may_promote_to_official": False,
            "citations": citations,
            "admissible_candidate": admissible,
        }

    def _evaluate_material_answer_candidate_v2(
        self,
        model_candidate: Mapping[str, object],
        *,
        material_bundles: Sequence[Mapping[str, object]],
        use_decision: Mapping[str, object],
        turn_frame: Mapping[str, object],
        governed_knowledge: Sequence[Mapping[str, object]],
        response_language: str | None,
        answer_quality_repair_exhausted: bool,
        research_program: Mapping[str, object] | None,
    ) -> dict:
        """Evaluate typed source facts, governed explanations, and illustrations."""

        expected_fields = {
            "contract",
            "status",
            "answer",
            "bounded_conclusion",
            "claims",
            "claim_citation_bindings",
            "claim_statements",
            "recommendations",
            "missing_fields",
            "limitations",
            "scientific_source_sha256s",
            "runtime_provenance_sha256s",
            "official_object_ids",
            "values",
            "capability_intent",
            "citation_ids",
        }
        errors: list[str] = []
        if set(model_candidate) != expected_fields:
            errors.append("typed_material_answer_fields_unsupported")
        if model_candidate.get("status") != "candidate_only":
            errors.append("candidate_status_exceeded_boundary")
        if (
            model_candidate.get("claim_statements") is not None
            or model_candidate.get("recommendations") is not None
            or model_candidate.get("official_object_ids") != []
            or model_candidate.get("values") != []
            or model_candidate.get("capability_intent") is not None
        ):
            errors.append("typed_material_answer_boundary_exceeded")
        for field in ("missing_fields", "limitations"):
            values = model_candidate.get(field)
            if not isinstance(values, list) or any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                errors.append(f"typed_material_answer_{field}_invalid")
        required_limitations = required_material_limitations(
            use_decision,
            response_language=response_language or "en",
        )
        if (
            required_limitations
            and model_candidate.get("limitations") != required_limitations
        ):
            errors.append("representative_sampling_limitation_drifted")
        provenance = model_candidate.get("runtime_provenance_sha256s")
        if (
            not isinstance(provenance, list)
            or len(provenance) != len(set(provenance))
            or any(
                not isinstance(value, str) or SHA256_RE.fullmatch(value) is None
                for value in provenance
            )
        ):
            errors.append("typed_material_answer_runtime_provenance_invalid")
        claims = model_candidate.get("claims")
        source_claims = [
            claim
            for claim in claims
            if isinstance(claim, Mapping)
            and claim.get("claim_kind") == "source_fact"
        ] if isinstance(claims, list) else []
        evidence_claims = [
            {
                "claim_id": claim.get("claim_id"),
                "text": (
                    normalize_rendered_clause_text(claim.get("text"))
                    if isinstance(claim.get("text"), str)
                    and claim.get("text").strip()
                    else claim.get("text")
                ),
                "requires_evidence": True,
                "citation_tokens": deepcopy(claim.get("citation_tokens")),
            }
            for claim in source_claims
        ]
        evidence_bindings = [
            {
                "claim_text": claim["text"],
                "citation_tokens": deepcopy(claim["citation_tokens"]),
            }
            for claim in evidence_claims
            if isinstance(claim.get("text"), str)
            and isinstance(claim.get("citation_tokens"), list)
        ]
        if model_candidate.get("claim_citation_bindings") != evidence_bindings:
            errors.append("typed_claim_citation_binding_drifted")
        evidence_answer = (
            ". ".join(
                str(claim["text"]).strip()
                + " "
                + " ".join(claim["citation_tokens"])
                for claim in evidence_claims
                if isinstance(claim.get("text"), str)
                and isinstance(claim.get("citation_tokens"), list)
            )
            + ("." if evidence_claims else "")
        )
        evidence_candidate = {
            "status": model_candidate.get("status"),
            "answer": evidence_answer,
            "bounded_conclusion": evidence_answer,
            "claims": evidence_claims,
            "claim_citation_bindings": evidence_bindings,
            "claim_statements": None,
            "recommendations": None,
            "missing_fields": deepcopy(model_candidate.get("missing_fields")),
            "limitations": deepcopy(model_candidate.get("limitations")),
            "scientific_source_sha256s": deepcopy(
                model_candidate.get("scientific_source_sha256s")
            ),
            "runtime_provenance_sha256s": deepcopy(
                model_candidate.get("runtime_provenance_sha256s")
            ),
            "official_object_ids": deepcopy(
                model_candidate.get("official_object_ids")
            ),
            "values": deepcopy(model_candidate.get("values")),
            "capability_intent": deepcopy(
                model_candidate.get("capability_intent")
            ),
            "citation_ids": deepcopy(model_candidate.get("citation_ids")),
        }
        evidence_assessment = self.evaluate_material_answer_candidate(
            evidence_candidate,
            material_bundles=material_bundles,
            use_decision=use_decision,
            turn_frame=turn_frame,
            governed_knowledge=governed_knowledge,
            response_language=response_language,
            research_program=research_program,
        )
        errors.extend(evidence_assessment["errors"])
        selected_locator_ids = use_decision.get(
            "selected_evidence_locator_ids", []
        )
        selected_locator_set = (
            set(selected_locator_ids)
            if isinstance(selected_locator_ids, list)
            else set()
        )
        selected_evidence_units = [
            {
                "locator_id": binding.get("locator_id"),
                "content_hash": binding.get("content_hash"),
                "evidence_unit": deepcopy(binding.get("evidence_unit")),
            }
            for bundle in material_bundles
            if isinstance(bundle, Mapping)
            for binding in (bundle.get("evidence_unit_bindings") or [])
            if isinstance(binding, Mapping)
            and binding.get("locator_id") in selected_locator_set
        ]
        try:
            quality = evaluate_answer_quality(
                claims,
                turn_frame=turn_frame,
                selected_locator_ids=selected_locator_ids,
                selected_evidence_units=selected_evidence_units,
                governed_knowledge=governed_knowledge,
                response_language=response_language,
            )
        except ValueError:
            quality = {
                "contract": "lca_research_kernel.answer_quality_assessment/v2",
                "status": "rejected",
                "disposition": "bounded_reject",
                "covered_obligations": [],
                "missing_obligations": deepcopy(
                    turn_frame.get("answer_obligations") or []
                ),
                "unsupported_claim_ids": [],
                "repairable_claim_issues": [],
                "presentation_issues": [],
                "errors": ["selected_evidence_role_projection_invalid"],
                "repair_instructions": [],
                "frame_sha256": turn_frame.get("frame_sha256"),
            }
        try:
            quality_basis = build_answer_quality_basis(
                selected_evidence_units=selected_evidence_units,
                governed_knowledge=governed_knowledge,
                response_language=response_language or "en",
            )
        except ValueError:
            quality_basis = None
            errors.append("answer_quality_basis_invalid")
        missing_anchor_errors = {
            "candidate_answer_absent",
            "candidate_citation_set_drifted",
            "claim_citation_binding_absent",
            "scientific_claims_absent",
        }
        if (
            not source_claims
            and set(errors) == missing_anchor_errors
            and quality.get("disposition") == "accept"
        ):
            quality = {
                **deepcopy(quality),
                "status": "repairable",
                "disposition": "repair_once",
                "repairable_claim_issues": [
                    {
                        "claim_id": "source-anchor",
                        "misassigned_obligations": [],
                    }
                ],
                "errors": ["source_anchor_absent"],
                "repair_instructions": [
                    "Add exactly one source_fact claim grounded only in the "
                    "selected evidence, citing at least one selected locator. "
                    "It may declare no obligation when the current turn only "
                    "asks for governed explanation or limitation."
                ],
            }
            errors = []
        if quality["status"] == "repairable":
            errors.append("answer_quality_repair_required")
        elif quality["status"] != "passed":
            errors.append("answer_quality_rejected")
        try:
            full_rendered = render_typed_material_answer(
                [claim for claim in claims if isinstance(claim, Mapping)],
                response_language=response_language or "en",
            )
        except (TypeError, ValueError):
            full_rendered = ""
            errors.append("typed_answer_projection_unrenderable")
        if (
            model_candidate.get("answer") != full_rendered
            or model_candidate.get("bounded_conclusion") != full_rendered
        ):
            errors.append("typed_answer_projection_drifted")
        repairable_only = errors == ["answer_quality_repair_required"]
        if answer_quality_repair_exhausted and repairable_only:
            bounded_quality = deepcopy(quality)
            bounded_quality.update(
                {
                    "status": "rejected",
                    "disposition": "bounded_reject",
                    "errors": ["answer_quality_repair_exhausted"],
                    "repair_instructions": [],
                }
            )
            missing = list(quality["missing_obligations"])
            limitation_text = (
                (
                    "受限结论：一次受控修复后，回答仍未满足以下可审核要求："
                    + "、".join(missing)
                    + "。已核验的来源事实仍予保留，但不得据此形成更强解释。"
                )
                if response_language == "zh-CN"
                else (
                    "Bounded result: one controlled repair did not satisfy: "
                    + ", ".join(missing)
                    + ". Verified source facts are retained, but they do not "
                    "support a stronger explanation."
                )
            )
            limitation_claim = {
                "claim_id": "system-answer-quality-limit-"
                + _canonical_sha256(
                    {
                        "frame_sha256": turn_frame["frame_sha256"],
                        "missing_obligations": missing,
                    }
                )[:24],
                "claim_kind": "limitation",
                "text": limitation_text,
                "citation_tokens": [],
                "knowledge_refs": [],
                "referent_ids": [turn_frame["referent"]["identity"]],
                "not_project_fact": False,
                "satisfies_obligations": ["source_boundary"],
            }
            bounded_claims = [*deepcopy(list(claims)), limitation_claim]
            bounded_answer = render_typed_material_answer(
                bounded_claims,
                response_language=response_language or "en",
            )
            admissible = deepcopy(evidence_assessment["admissible_candidate"])
            source_limitations = [
                value
                for value in (admissible.get("limitations") or [])
                if isinstance(value, str) and value.strip()
            ]
            source_missing_facts = [
                deepcopy(dict(value))
                for value in (admissible.get("missing_facts") or [])
                if isinstance(value, Mapping)
            ]
            source_resume_conditions = [
                deepcopy(dict(value))
                for value in (admissible.get("resume_conditions") or [])
                if isinstance(value, Mapping)
            ]
            quality_missing_facts = [
                {
                    "reason": "answer_obligation_unsatisfied:" + obligation,
                    "processing_status": "answer_quality_bounded",
                }
                for obligation in missing
            ]
            quality_resume_conditions = [
                {
                    "condition": (
                        "provide a qualified explanation satisfying: "
                        + obligation
                    )
                }
                for obligation in missing
            ]
            admissible.update(
                {
                    "status": "formal_limited_result_accepted",
                    "answer": bounded_answer,
                    "bounded_conclusion": bounded_answer,
                    "claims": bounded_claims,
                    "claim_citation_bindings": deepcopy(
                        model_candidate["claim_citation_bindings"]
                    ),
                    "answer_quality": bounded_quality,
                    "answer_quality_basis": deepcopy(quality_basis),
                    "turn_frame": deepcopy(turn_frame),
                    "governed_knowledge_refs": sorted(
                        {
                            ref
                            for claim in claims
                            if isinstance(claim, Mapping)
                            for ref in (claim.get("knowledge_refs") or [])
                            if isinstance(ref, str)
                        }
                    ),
                    "claim_ceiling": "calculation_structure_only",
                    "limitations": list(
                        dict.fromkeys([*source_limitations, limitation_text])
                    ),
                    "missing_facts": [
                        *source_missing_facts,
                        *quality_missing_facts,
                    ],
                    "resume_conditions": [
                        *source_resume_conditions,
                        *quality_resume_conditions,
                    ],
                    "computed_values": [],
                }
            )
            return {
                "contract": "lca_research_kernel.material_answer_assessment/v2",
                "status": "passed",
                "errors": [],
                "scientific_status": "accepted_project_input",
                "claim_ceiling": "calculation_structure_only",
                "may_satisfy_tiangong_official_requirement": False,
                "may_promote_to_official": False,
                "citations": deepcopy(evidence_assessment["citations"]),
                "admissible_candidate": admissible,
                "answer_quality": bounded_quality,
            }
        status = (
            "passed"
            if not errors
            else "repairable"
            if errors == ["answer_quality_repair_required"]
            else "rejected"
        )
        admissible = None
        if status == "passed":
            admissible = deepcopy(evidence_assessment["admissible_candidate"])
            admissible.update(
                {
                    "answer": full_rendered,
                    "bounded_conclusion": full_rendered,
                    "claims": deepcopy(claims),
                    "claim_citation_bindings": deepcopy(
                        model_candidate["claim_citation_bindings"]
                    ),
                    "answer_quality": deepcopy(quality),
                    "answer_quality_basis": deepcopy(quality_basis),
                    "turn_frame": deepcopy(turn_frame),
                    "governed_knowledge_refs": sorted(
                        {
                            ref
                            for claim in claims
                            if isinstance(claim, Mapping)
                            for ref in (claim.get("knowledge_refs") or [])
                            if isinstance(ref, str)
                        }
                    ),
                }
            )
        return {
            "contract": "lca_research_kernel.material_answer_assessment/v2",
            "status": status,
            "errors": sorted(set(errors)),
            "scientific_status": "accepted_project_input",
            "claim_ceiling": evidence_assessment["claim_ceiling"],
            "may_satisfy_tiangong_official_requirement": False,
            "may_promote_to_official": False,
            "citations": deepcopy(evidence_assessment["citations"]),
            "admissible_candidate": admissible,
            "answer_quality": quality,
        }

    def evaluate_direct_answer_candidate(
        self,
        model_candidate: Mapping[str, object],
        *,
        unexecuted_capability_claims: Sequence[Mapping[str, object]] = (),
    ) -> dict:
        """Admit only non-numeric, non-official explanatory candidates."""

        errors: list[str] = []
        answer = model_candidate.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            errors.append("direct_candidate_answer_absent")
        normalized_capability_claims: list[tuple[str, str, str]] = []
        if not isinstance(unexecuted_capability_claims, Sequence) or isinstance(
            unexecuted_capability_claims, (str, bytes)
        ):
            errors.append("direct_candidate_capability_claim_contract_invalid")
        else:
            for claim in unexecuted_capability_claims:
                if not isinstance(claim, Mapping):
                    errors.append(
                        "direct_candidate_capability_claim_contract_invalid"
                    )
                    continue
                capability_id = claim.get("capability_id")
                source_identity = claim.get("source_identity")
                source_version = claim.get("source_version")
                if not all(
                    isinstance(value, str) and value.strip()
                    for value in (
                        capability_id,
                        source_identity,
                        source_version,
                    )
                ):
                    errors.append(
                        "direct_candidate_capability_claim_contract_invalid"
                    )
                    continue
                normalized_capability_claims.append(
                    (
                        capability_id.strip(),
                        source_identity.strip(),
                        source_version.strip(),
                    )
                )
        if isinstance(answer, str):
            normalized_answer = answer.casefold()
            if any(
                capability_id.casefold() in normalized_answer
                or source_identity.casefold() in normalized_answer
                or source_version.casefold() in normalized_answer
                for capability_id, source_identity, source_version in (
                    normalized_capability_claims
                )
            ):
                errors.append("direct_candidate_unexecuted_capability_claim")
        for key in ("values", "official_object_ids", "scientific_source_sha256s"):
            value = model_candidate.get(key, [])
            if value not in (None, []):
                errors.append(f"direct_candidate_{key}_forbidden")
        if model_candidate.get("capability_intent") not in (None, {}):
            errors.append("direct_candidate_capability_intent_forbidden")
        raw_missing_fields = model_candidate.get("missing_fields", [])
        if (
            not isinstance(raw_missing_fields, list)
            or any(
                not isinstance(item, str)
                or item not in DIRECT_LCA_MISSING_FIELDS
                for item in raw_missing_fields
            )
            or len(raw_missing_fields) != len(set(raw_missing_fields))
        ):
            errors.append("direct_candidate_missing_fields_invalid")
            missing_fields: list[str] = []
        else:
            missing_fields = sorted(raw_missing_fields)
        admissible = None
        if not errors:
            limitations = model_candidate.get("limitations")
            normalized_limitations = (
                [
                    item.strip()
                    for item in limitations
                    if isinstance(item, str) and item.strip()
                ]
                if isinstance(limitations, list)
                else []
            )
            normalized_limitations.append(
                "This is a candidate explanation without an official data or calculation claim."
            )
            if missing_fields:
                normalized_limitations.append(
                    "The requested calculation or stronger conclusion is blocked by missing user-controlled LCA inputs."
                )
            admissible = {
                "status": "candidate_only",
                "claim_ceiling": "candidate_only",
                "answer": answer.strip(),
                "summary": answer.strip(),
                "values": [],
                "official_object_ids": [],
                "scientific_source_sha256s": [],
                "missing_fields": missing_fields,
                "limitations": sorted(set(normalized_limitations)),
            }
        return {
            "contract": "lca_research_kernel.direct_answer_assessment/v1",
            "status": "passed" if not errors else "rejected",
            "errors": sorted(set(errors)),
            "may_promote_to_official": False,
            "missing_fields": missing_fields,
            "admissible_candidate": admissible,
        }

    def evaluate_tiangong_native_contract_result(
        self, sourced_result: Mapping[str, object]
    ) -> dict:
        """Accept only an exact TianGong SDK contract observation, not science."""

        errors: list[str] = []
        authority = sourced_result.get("authority")
        source_lock = (
            authority.get("source_lock")
            if isinstance(authority, Mapping)
            else None
        )
        content = sourced_result.get("content")
        if (
            sourced_result.get("contract")
            != "lca_research_kernel.sourced_capability_result/v1"
            or sourced_result.get("status") != "sourced_result_not_fact"
            or sourced_result.get("capability_id")
            != "tiangong.native.tidas-sdk"
            or sourced_result.get("effect") != "local_read"
            or sourced_result.get("executed_external_writes") != 0
        ):
            errors.append("tiangong_native_result_contract_invalid")
        if (
            not isinstance(authority, Mapping)
            or authority.get("provider") != "TianGong"
            or authority.get("authority") != "official_native_tool"
            or authority.get("access") != "read_only"
            or not isinstance(source_lock, Mapping)
            or source_lock.get("source_id") != "tiangong.sdk.npm"
            or source_lock.get("identity") != "@tiangong-lca/tidas-sdk"
            or source_lock.get("version") != sourced_result.get("source_version")
            or source_lock.get("identity") != sourced_result.get("source_identity")
        ):
            errors.append("tiangong_native_source_identity_invalid")
        if (
            not isinstance(content, Mapping)
            or set(content)
            != {
                "contract",
                "provider",
                "package_identity",
                "package_version",
                "package_integrity",
                "package_json_sha256",
                "package_lock_sha256",
                "runtime_verified",
                "requested_state_change",
                "method_boundary",
                "provider_boundary",
                "unit_boundary",
                "scientific_result_created",
            }
            or content.get("contract")
            != "lca_research_kernel.tiangong_native_sdk_contract_observation/v1"
            or content.get("provider") != "TianGong"
            or not isinstance(source_lock, Mapping)
            or content.get("package_identity") != source_lock.get("identity")
            or content.get("package_version") != source_lock.get("version")
            or content.get("package_integrity") != source_lock.get("integrity")
            or not isinstance(content.get("package_json_sha256"), str)
            or SHA256_RE.fullmatch(content["package_json_sha256"]) is None
            or not isinstance(content.get("package_lock_sha256"), str)
            or SHA256_RE.fullmatch(content["package_lock_sha256"]) is None
            or content.get("runtime_verified") is not True
            or content.get("requested_state_change")
            != "inspect_tidas_sdk_contract"
            or content.get("method_boundary") != "not_selected"
            or content.get("provider_boundary") != "not_selected"
            or content.get("unit_boundary") != "not_evaluated"
            or content.get("scientific_result_created") is not False
            or sourced_result.get("content_sha256")
            != _canonical_sha256(content)
        ):
            errors.append("tiangong_native_boundary_observation_invalid")
        source_identity = sourced_result.get("source_identity")
        source_version = sourced_result.get("source_version")
        bounded_answer = (
            f"当前已通过 TianGong 原生只读能力确认 {source_identity}，锁定版本为 "
            f"{source_version}。本轮只验证了 TIDAS SDK 的身份、版本和接口合同；"
            "没有选择具体 LCA 方法、过程交换 Provider 或计量单位，也没有执行"
            "生命周期计算，因此不能据此给出清单或影响评价数值。"
        )
        return {
            "contract": (
                "lca_research_kernel.tiangong_native_contract_assessment/v1"
            ),
            "status": "passed" if not errors else "failed",
            "errors": sorted(set(errors)),
            "claim_ceiling": "capability_contract_only",
            "may_promote_to_official": False,
            "capability_id": sourced_result.get("capability_id"),
            "source_identity": sourced_result.get("source_identity"),
            "source_version": sourced_result.get("source_version"),
            "source_integrity": (
                source_lock.get("integrity")
                if isinstance(source_lock, Mapping)
                else None
            ),
            "content_sha256": sourced_result.get("content_sha256"),
            "bounded_answer": bounded_answer,
            "scientific_boundary": {
                "lca_method": "not_selected",
                "exchange_provider": "not_selected",
                "quantity_unit": "not_evaluated",
                "scientific_result_created": False,
            },
        }

    def evaluate_generic_source_candidate(
        self,
        source_material: Mapping[str, object],
        model_candidate: Mapping[str, object],
        capability_results: Sequence[Mapping[str, object]] = (),
    ) -> dict:
        """Validate a case-neutral candidate against its exact visible sources."""

        errors: list[str] = []
        official = self._official_source_evidence(capability_results)
        errors.extend(official["errors"])
        if source_material.get("contract") != (
            "lca_research_kernel.source_bound_research_material/v1"
        ):
            errors.append("source_material_contract_invalid")
        materials = source_material.get("materials")
        if not isinstance(materials, list) or not materials:
            errors.append("source_materials_absent")
            materials = []
        primary = [row for row in materials if _is_content_bound_primary_source(row)]
        all_source_hashes = {
            str(row["sha256"])
            for row in materials
            if isinstance(row, Mapping)
            if isinstance(row.get("sha256"), str)
            and SHA256_RE.fullmatch(str(row["sha256"])) is not None
        }
        expected_hashes = {
            str(row["sha256"])
            for row in primary
            if isinstance(row.get("sha256"), str)
            and SHA256_RE.fullmatch(str(row["sha256"])) is not None
        }
        content_addressed_project_input_hashes: set[str] = set()
        for row in materials:
            if (
                not isinstance(row, Mapping)
                or row.get("authority") != "user_supplied_project_input"
                or not isinstance(row.get("content"), Mapping)
            ):
                continue
            source_sha256 = row.get("sha256")
            source_text = row["content"].get("text")
            if not isinstance(source_text, str):
                continue
            if (
                not isinstance(source_sha256, str)
                or SHA256_RE.fullmatch(source_sha256) is None
                or _text_sha256(source_text) != source_sha256
            ):
                errors.append("content_addressed_project_input_hash_drifted")
                continue
            content_addressed_project_input_hashes.add(source_sha256)
        official_hashes = set(official["scientific_source_sha256s"])
        if official_hashes:
            expected_hashes = official_hashes
        all_source_hashes.update(official_hashes)
        expected_ids = {
            str(identity)
            for row in primary
            for identity in row.get("official_object_ids", [])
            if isinstance(row.get("official_object_ids"), list)
            and isinstance(identity, str)
        }
        transformations = {
            str(row.get("transformation"))
            for row in primary
            if isinstance(row.get("transformation"), str)
        }
        observed_hashes = model_candidate.get("scientific_source_sha256s")
        observed_hash_set = (
            set(observed_hashes)
            if isinstance(observed_hashes, list)
            and all(
                isinstance(value, str)
                and SHA256_RE.fullmatch(value) is not None
                for value in observed_hashes
            )
            else set()
        )
        source_hashes_valid = (
            isinstance(observed_hashes, list)
            and len(observed_hashes) == len(observed_hash_set)
            and (
                bool(observed_hash_set)
                and observed_hash_set.issubset(official_hashes)
                if official_hashes
                else observed_hash_set == expected_hashes
            )
        )
        if not source_hashes_valid:
            errors.append("candidate_source_hash_drifted")
        selected_source_hashes = (
            observed_hash_set if official_hashes else expected_hashes
        )
        runtime_provenance = model_candidate.get("runtime_provenance_sha256s")
        if not isinstance(runtime_provenance, list) or any(
            not isinstance(value, str) or SHA256_RE.fullmatch(value) is None
            for value in runtime_provenance
        ):
            errors.append("candidate_runtime_provenance_invalid")
            runtime_provenance = []
        observed_ids = model_candidate.get("official_object_ids")
        if not isinstance(observed_ids, list) or set(observed_ids) != expected_ids:
            errors.append("candidate_official_identity_drifted")
        if model_candidate.get("status") != "candidate_only":
            errors.append("candidate_status_exceeded_boundary")
        if model_candidate.get("capability_intent") is not None:
            errors.append("unnecessary_capability_intent")
        admissible_candidate = deepcopy(dict(model_candidate))
        claim_modality_resolutions: list[dict] = []
        if official_hashes:
            answer = model_candidate.get("answer")
            if not isinstance(answer, str) or not answer.strip():
                errors.append("candidate_answer_absent")
            recommendations = model_candidate.get("recommendations")
            if (
                recommendations is not None
                and (
                    not isinstance(recommendations, list)
                    or any(
                        not isinstance(value, str) or not value.strip()
                        for value in recommendations
                    )
                )
            ):
                errors.append("candidate_recommendations_invalid")
            claims = model_candidate.get("claim_statements")
            if not isinstance(claims, list) or not claims:
                errors.append("candidate_claim_statements_absent")
                claims = []
            for claim_index, claim in enumerate(claims):
                if (
                    not isinstance(claim, Mapping)
                    or set(claim)
                    != {
                        "modality",
                        "statement",
                        "scientific_source_sha256s",
                        "evidence_basis",
                    }
                ):
                    errors.append("candidate_claim_statement_invalid")
                    continue
                if claim.get("modality") not in CLAIM_MODALITIES:
                    errors.append("candidate_claim_modality_invalid")
                if (
                    not isinstance(claim.get("statement"), str)
                    or not claim["statement"].strip()
                ):
                    errors.append("candidate_claim_statement_absent")
                claim_hashes = claim.get("scientific_source_sha256s")
                if (
                    not isinstance(claim_hashes, list)
                    or not claim_hashes
                    or len(claim_hashes) != len(set(claim_hashes))
                    or any(
                        not isinstance(value, str)
                        or value not in selected_source_hashes
                        for value in claim_hashes
                    )
                ):
                    errors.append("candidate_claim_source_hash_drifted")
                evidence_basis = claim.get("evidence_basis")
                literal = (
                    evidence_basis.get("literal")
                    if isinstance(evidence_basis, Mapping)
                    else None
                )
                evidence_source_sha256 = (
                    evidence_basis.get("source_sha256")
                    if isinstance(evidence_basis, Mapping)
                    else None
                )
                if (
                    not isinstance(evidence_basis, Mapping)
                    or set(evidence_basis)
                    != {"literal", "source_sha256"}
                    or not isinstance(literal, str)
                    or not literal
                    or not isinstance(evidence_source_sha256, str)
                    or evidence_source_sha256 not in selected_source_hashes
                    or not isinstance(claim_hashes, list)
                    or evidence_source_sha256 not in claim_hashes
                    or literal
                    not in official["content_by_sha256"].get(
                        evidence_source_sha256, ""
                    )
                ):
                    errors.append("candidate_claim_evidence_basis_drifted")
                    continue
                modality = claim.get("modality")
                try:
                    resolution = resolve_claim_modality(
                        declared_modality=str(modality),
                        literal=literal,
                        statement=str(claim.get("statement", "")),
                        source_sha256=evidence_source_sha256,
                    )
                except ValueError:
                    errors.append("candidate_claim_modality_contradicted")
                    continue
                claim_modality_resolutions.append(resolution)
                if resolution["status"] != "resolved":
                    errors.append("candidate_claim_modality_contradicted")
                    continue
                admissible_claims = admissible_candidate.get(
                    "claim_statements"
                )
                if isinstance(admissible_claims, list):
                    admissible_claims[claim_index]["modality"] = resolution[
                        "normalized_modality"
                    ]
        conclusion = model_candidate.get("bounded_conclusion")
        if not isinstance(conclusion, str) or not conclusion.strip():
            errors.append("bounded_conclusion_absent")
        if official_hashes:
            answer_text = model_candidate.get("answer")
            visible_legal_modalities = required_user_visible_legal_modalities(
                "\n".join(
                    value
                    for value in (answer_text, conclusion)
                    if isinstance(value, str)
                )
            )
            bound_legal_modalities = {
                str(resolution.get("normalized_modality"))
                for resolution in claim_modality_resolutions
                if resolution.get("status") == "resolved"
            }
            if not visible_legal_modalities.issubset(bound_legal_modalities):
                errors.append("candidate_user_visible_legal_claim_unbound")
            unbound_legal_clauses = unbound_user_visible_legal_clauses(
                "\n".join(
                    value
                    for value in (answer_text, conclusion)
                    if isinstance(value, str)
                ),
                (
                    admissible_candidate.get("claim_statements")
                    if isinstance(
                        admissible_candidate.get("claim_statements"), list
                    )
                    else []
                ),
            )
            if unbound_legal_clauses:
                errors.append("candidate_user_visible_legal_clause_unbound")
        limitations = model_candidate.get("limitations")
        if not isinstance(limitations, list):
            errors.append("candidate_limitations_absent")
        missing_fields = model_candidate.get("missing_fields")
        if not isinstance(missing_fields, list):
            errors.append("candidate_missing_fields_invalid")
            missing_fields = []
        normalized_missing = {
            re.sub(r"[\s-]+", "_", value.strip().casefold())
            for value in missing_fields
            if isinstance(value, str)
        }
        if (
            self._source_material_requests_derived_quantity(materials)
            and not normalized_missing
        ):
            errors.append("candidate_requested_quantity_gap_not_declared")
        critical_gap = {
            "hide_functional_unit": "functional_unit",
            "hide_impact_unit": "impact_unit",
            "hide_reference_flow": "reference_flow",
            "hide_source_identity": "source_identity",
            "hide_limitation": "limitation",
        }
        required_gaps = {
            missing
            for transformation, missing in critical_gap.items()
            if transformation in transformations
        }
        if not required_gaps.issubset(normalized_missing):
            errors.append("critical_gap_not_declared")
        values = model_candidate.get("values")
        if not isinstance(values, list):
            errors.append("candidate_values_invalid")
            values = []
        declared_input_values, deterministic_values, calculation_errors = (
            _declared_input_calculations(materials)
        )
        errors.extend(calculation_errors)
        admissible_values: list[dict] = []
        dropped_numeric_claim_count = 0
        source_text = repr(
            [
                *[
                    row.get("content")
                    for row in materials
                    if isinstance(row, Mapping)
                    and (
                        _is_content_bound_primary_source(row)
                        or row.get("sha256")
                        in content_addressed_project_input_hashes
                    )
                ],
                *official["content_excerpts"],
            ]
        )
        for value in values:
            if not isinstance(value, Mapping):
                errors.append("candidate_value_invalid")
                continue
            identity_valid = (
                isinstance(value.get("identity"), str)
                and bool(value["identity"].strip())
            )
            number_valid = isinstance(value.get("value"), (int, float))
            unit_valid = (
                isinstance(value.get("unit"), str)
                and bool(value["unit"].strip())
            )
            source_sha256_valid = (
                isinstance(value.get("source_sha256"), str)
                and value["source_sha256"]
                in (
                    selected_source_hashes
                    | content_addressed_project_input_hashes
                )
            )
            time_or_version_valid = (
                isinstance(value.get("time_or_version"), str)
                and bool(value["time_or_version"].strip())
                and (
                    value.get("source_sha256")
                    not in content_addressed_project_input_hashes
                    or value["time_or_version"] == "content_addressed"
                )
            )
            if not identity_valid:
                errors.append("candidate_value_identity_absent")
            if not number_valid:
                errors.append("candidate_value_number_absent")
            if not unit_valid:
                errors.append("candidate_value_unit_absent")
            if not source_sha256_valid or not time_or_version_valid:
                errors.append(
                    "candidate_value_source_or_time_binding_absent"
                )
            if not (
                identity_valid
                and number_valid
                and unit_valid
                and source_sha256_valid
                and time_or_version_valid
            ):
                continue
            source_bound = (
                not required_gaps
                and str(value["value"]) in source_text
                and str(value["unit"]) in source_text
                and (
                    value["source_sha256"]
                    in content_addressed_project_input_hashes
                    or str(value["time_or_version"]) in source_text
                )
            )
            if source_bound:
                admissible_values.append(deepcopy(dict(value)))
            else:
                dropped_numeric_claim_count += 1
        admissible_candidate["values"] = admissible_values
        if dropped_numeric_claim_count:
            admissible_candidate["bounded_conclusion"] = (
                "The source remains usable only as bounded qualitative evidence; "
                "numeric claims without an exact source-bound value and unit were "
                "excluded."
            )
            normalized_limitations = [
                str(value)
                for value in admissible_candidate.get("limitations", [])
                if isinstance(value, str)
            ]
            normalized_limitations.append(
                "Unsupported numeric claims were excluded by the LCA Domain Kernel."
            )
            admissible_candidate["limitations"] = normalized_limitations
        source_basis = self._formal_source_basis(materials, official)
        claim_classifications = self._formal_claim_classifications(
            admissible_candidate.get("claim_statements")
        )
        formal_is_limited = bool(normalized_missing)
        formal_status = (
            "formal_limited_result_accepted"
            if formal_is_limited
            else "formal_result_accepted"
        )
        formal_claim_ceiling = (
            "calculation_structure_only"
            if formal_is_limited
            else "source_bounded_interpretation"
        )
        resume_conditions = [
            f"Provide a source-bound value for {field}."
            for field in sorted(normalized_missing)
        ]
        return {
            "contract": (
                "lca_research_kernel.generic_source_candidate_evaluation/v1"
            ),
            "status": "passed" if not errors else "failed",
            "errors": sorted(set(errors)),
            "scientific_source_sha256s": sorted(selected_source_hashes),
            "runtime_provenance_sha256s": sorted(set(runtime_provenance)),
            "official_object_ids": sorted(expected_ids),
            "required_missing_fields": sorted(required_gaps),
            "admissible_candidate": admissible_candidate,
            "dropped_numeric_claim_count": dropped_numeric_claim_count,
            "claim_ceiling": (
                "bounded_missing_data_conclusion"
                if required_gaps and not errors
                else "candidate_only"
                if not errors
                else "candidate_not_admissible"
            ),
            "may_promote_to_official": False,
            "official_source_refs": deepcopy(official["official_source_refs"]),
            "official_source_evidence": deepcopy(
                official["official_source_evidence"]
            ),
            "formal_result_status": formal_status,
            "formal_claim_ceiling": formal_claim_ceiling,
            "source_basis": source_basis,
            "claim_classifications": claim_classifications,
            "claim_modality_resolutions": claim_modality_resolutions,
            "missing_facts": sorted(normalized_missing),
            "resume_conditions": resume_conditions,
            "computed_values": (
                []
                if formal_is_limited
                else deepcopy([*admissible_values, *deterministic_values])
            ),
            "declared_input_values": (
                [] if formal_is_limited else deepcopy(declared_input_values)
            ),
        }

    @staticmethod
    def _formal_source_basis(
        materials: Sequence[object],
        official: Mapping[str, object],
    ) -> list[dict]:
        official_refs = official.get("official_source_refs")
        if isinstance(official_refs, list) and official_refs:
            return [
                {
                    "authority": "official_public_source",
                    "url": reference["final_url"],
                    "accessed_at": reference["accessed_at"],
                    "sha256": reference["body_sha256"],
                    "version": reference["source_version"],
                    "receipt_sha256": reference["receipt_sha256"],
                }
                for reference in official_refs
                if isinstance(reference, Mapping)
            ]
        basis = []
        observed = set()
        for material in materials:
            if (
                not isinstance(material, Mapping)
                or not isinstance(material.get("authority"), str)
                or not isinstance(material.get("sha256"), str)
                or SHA256_RE.fullmatch(material["sha256"]) is None
            ):
                continue
            row = {
                "authority": material["authority"],
                "sha256": material["sha256"],
            }
            if isinstance(material.get("version"), str):
                row["version"] = material["version"]
            identity = _canonical_sha256(row)
            if identity not in observed:
                basis.append(row)
                observed.add(identity)
        return basis

    @staticmethod
    def _formal_claim_classifications(claims: object) -> list[dict]:
        if claims is None:
            return []
        if not isinstance(claims, list):
            return []
        result = []
        for claim in claims:
            if not isinstance(claim, Mapping):
                continue
            modality = claim.get("modality")
            classification = MODALITY_TO_CLAIM_CLASS.get(str(modality))
            if classification is None:
                continue
            result.append(
                {
                    "classification": classification,
                    "statement": claim.get("statement"),
                    "scientific_source_sha256s": deepcopy(
                        claim.get("scientific_source_sha256s")
                    ),
                    "evidence_basis": deepcopy(claim.get("evidence_basis")),
                }
            )
        return result

    def _official_source_evidence(
        self, capability_results: object
    ) -> dict:
        errors: list[str] = []
        hashes: set[str] = set()
        references: list[dict] = []
        evidence: list[dict] = []
        excerpts: list[str] = []
        content_by_sha256: dict[str, str] = {}
        if capability_results is None:
            capability_results = ()
        if not isinstance(capability_results, (list, tuple)):
            return {
                "errors": ["capability_results_invalid"],
                "scientific_source_sha256s": [],
                "official_source_refs": [],
                "official_source_evidence": [],
                "content_excerpts": [],
                "content_by_sha256": {},
            }
        sortable = [
            row
            for row in capability_results
            if isinstance(row, Mapping)
        ]
        for fact in sorted(
            sortable, key=lambda row: str(row.get("fact_id", ""))
        ):
            sourced = fact.get("value")
            authority = (
                sourced.get("authority")
                if isinstance(sourced, Mapping)
                else None
            )
            if (
                not isinstance(authority, Mapping)
                or authority.get("authority") != "official_public_source"
            ):
                continue
            fact_id = fact.get("fact_id")
            source = fact.get("source")
            content = (
                sourced.get("content")
                if isinstance(sourced, Mapping)
                else None
            )
            receipt = (
                content.get("source_receipt")
                if isinstance(content, Mapping)
                else None
            )
            excerpt = (
                content.get("content_excerpt")
                if isinstance(content, Mapping)
                else None
            )
            cache_ref = (
                content.get("cache_ref")
                if isinstance(content, Mapping)
                else None
            )
            source_binding = (
                content.get("source_binding")
                if isinstance(content, Mapping)
                else None
            )
            receipt_source_binding = (
                receipt.get("source_binding")
                if isinstance(receipt, Mapping)
                else None
            )
            if (
                not isinstance(fact_id, str)
                or not fact_id
                or fact.get("classification") != "candidate"
                or fact.get("domain_kind") != "capability_result"
                or not isinstance(source, Mapping)
                or not isinstance(sourced, Mapping)
                or sourced.get("contract")
                != "lca_research_kernel.sourced_capability_result/v1"
                or sourced.get("status") != "sourced_result_not_fact"
                or sourced.get("effect") != "external_read"
                or authority.get("access") != "read_only"
                or authority.get("public_credential_free") is not True
                or not isinstance(content, Mapping)
                or content.get("contract")
                != "official_public_source_content/v1"
                or not isinstance(receipt, Mapping)
                or receipt.get("contract")
                != "official_public_source_receipt/v1"
                or not isinstance(cache_ref, Mapping)
                or cache_ref.get("contract")
                != "official_public_source_cache_ref/v1"
            ):
                errors.append("official_capability_result_invalid")
                continue
            body_sha256 = receipt.get("body_sha256")
            result_content_sha256 = sourced.get("content_sha256")
            source_version = sourced.get("source_version")
            receipt_unsigned = deepcopy(dict(receipt))
            receipt_sha256 = receipt_unsigned.pop("receipt_sha256", None)
            operation_payload = source.get("operation_payload")
            source_binding_valid = (
                isinstance(body_sha256, str)
                and SHA256_RE.fullmatch(body_sha256) is not None
                and isinstance(sourced.get("capability_id"), str)
                and bool(sourced["capability_id"])
                and source.get("capability_id")
                == sourced.get("capability_id")
                and source.get("authority")
                == "capability_sourced_result"
                and isinstance(receipt.get("question_id"), str)
                and bool(receipt["question_id"])
                and isinstance(receipt.get("run_id"), str)
                and bool(receipt["run_id"])
                and source.get("operation_question_id")
                == receipt.get("question_id")
                and source.get("operation_run_id") == receipt.get("run_id")
                and isinstance(source.get("operation_payload_sha256"), str)
                and SHA256_RE.fullmatch(
                    str(source.get("operation_payload_sha256"))
                )
                is not None
                and isinstance(operation_payload, Mapping)
                and source.get("operation_payload_sha256")
                == _canonical_sha256(operation_payload)
                and operation_payload.get("contract")
                == "official_public_source_request/v1"
                and operation_payload.get("question_id")
                == receipt.get("question_id")
                and operation_payload.get("run_id")
                == receipt.get("run_id")
                and operation_payload.get("url")
                == receipt.get("requested_url")
                and operation_payload.get("parse_location")
                == receipt.get("parse_location")
                and operation_payload.get("extraction")
                == receipt.get("extraction_request")
                and source.get("operation_id")
                == official_public_source_operation_id(
                    operation_payload,
                    capability_id=str(sourced.get("capability_id")),
                )
                and receipt.get("operation_id")
                == source.get("operation_id")
                and sourced.get("request_id") == source.get("operation_id")
                and fact_id
                == f"capability-result:{source.get('operation_id')}"
                and isinstance(receipt.get("requested_url"), str)
                and bool(receipt["requested_url"])
                and isinstance(receipt.get("final_url"), str)
                and bool(receipt["final_url"])
                and sourced.get("source_identity")
                == receipt.get("final_url")
                and source.get("source_identity")
                == receipt.get("final_url")
                and isinstance(receipt.get("accessed_at"), str)
                and bool(receipt["accessed_at"])
                and isinstance(receipt.get("parse_location"), str)
                and bool(receipt["parse_location"])
                and isinstance(receipt.get("status"), int)
                and 200 <= receipt["status"] < 300
                and isinstance(receipt.get("body_bytes"), int)
                and receipt["body_bytes"] >= 0
                and source_version == f"sha256:{body_sha256}"
                and source.get("version") == source_version
                and source.get("source_version") == source_version
                and isinstance(result_content_sha256, str)
                and SHA256_RE.fullmatch(result_content_sha256) is not None
                and result_content_sha256 == _canonical_sha256(content)
                and source.get("content_sha256") == result_content_sha256
                and cache_ref.get("sha256") == body_sha256
                and cache_ref.get("bytes") == receipt.get("body_bytes")
                and receipt_sha256 == _canonical_sha256(receipt_unsigned)
                and (
                    source_binding is None
                    and receipt_source_binding is None
                    or source_binding == receipt_source_binding
                    and validate_official_source_observation(source_binding)
                    and isinstance(source_binding.get("snapshot"), Mapping)
                    and source_binding["snapshot"].get("body_sha256")
                    == body_sha256
                )
            )
            if not source_binding_valid:
                errors.append("official_capability_source_binding_drifted")
                continue
            literal = ""
            if isinstance(excerpt, Mapping):
                if (
                    excerpt.get("contract")
                    != "official_public_source_excerpt/v1"
                    or excerpt.get("source_body_sha256") != body_sha256
                    or not isinstance(excerpt.get("text"), str)
                ):
                    errors.append("official_capability_excerpt_drifted")
                    continue
                literal = excerpt["text"]
            else:
                artifact = content.get("content_artifact_ref")
                if (
                    not isinstance(artifact, Mapping)
                    or artifact.get("source_body_sha256") != body_sha256
                ):
                    errors.append("official_capability_excerpt_absent")
                    continue
            hashes.add(body_sha256)
            excerpts.append(literal)
            self._merge_content_by_sha256(
                content_by_sha256,
                body_sha256,
                literal,
            )
            reference = {
                "contract": "lca_research_kernel.official_source_ref/v1",
                "capability_fact_id": fact_id,
                "capability_id": sourced.get("capability_id"),
                "requested_url": receipt.get("requested_url"),
                "final_url": receipt.get("final_url"),
                "accessed_at": receipt.get("accessed_at"),
                "body_sha256": body_sha256,
                "receipt_sha256": receipt_sha256,
                "parse_location": receipt.get("parse_location"),
                "source_version": source_version,
                "content_sha256": result_content_sha256,
            }
            if isinstance(source_binding, Mapping):
                locator_identity = source_binding.get("locator")
                if isinstance(locator_identity, Mapping):
                    reference.update(
                        {
                            "source_id": locator_identity.get("source_id"),
                            "source_authority": locator_identity.get("authority"),
                            "declared_version": locator_identity.get(
                                "declared_version"
                            ),
                        }
                    )
            references.append(reference)
            fact_excerpt = {
                "contract": "official_source_fact_excerpt/v1",
                "question_id": receipt.get("question_id"),
                "dimension": (
                    receipt.get("extraction_request", {}).get("kind")
                    if isinstance(
                        receipt.get("extraction_request"), Mapping
                    )
                    else "document"
                ),
                "literal": literal,
                "literal_sha256": _text_sha256(literal),
                "source_body_sha256": body_sha256,
                "receipt_sha256": receipt_sha256,
                "parse_location": receipt.get("parse_location"),
            }
            extraction_request = receipt.get("extraction_request")
            if isinstance(extraction_request, Mapping) and isinstance(
                extraction_request.get("composite_key"), Mapping
            ):
                fact_excerpt["composite_key"] = deepcopy(
                    dict(extraction_request["composite_key"])
                )
            fact_excerpt["excerpt_sha256"] = _canonical_sha256(
                fact_excerpt
            )
            evidence.append(
                {
                    "source_receipt": deepcopy(dict(receipt)),
                    "cache_ref": deepcopy(dict(cache_ref)),
                    "fact_excerpt": fact_excerpt,
                }
            )
        return {
            "errors": sorted(set(errors)),
            "scientific_source_sha256s": sorted(hashes),
            "official_source_refs": references,
            "official_source_evidence": evidence,
            "content_excerpts": excerpts,
            "content_by_sha256": content_by_sha256,
        }

    @staticmethod
    def _merge_content_by_sha256(
        content_by_sha256: dict[str, str],
        source_sha256: str,
        literal: str,
    ) -> None:
        existing = content_by_sha256.get(source_sha256)
        if existing is None:
            content_by_sha256[source_sha256] = literal
        elif literal and literal not in existing:
            content_by_sha256[source_sha256] = existing + "\n" + literal

    @staticmethod
    def _source_material_requests_derived_quantity(
        materials: Sequence[object],
    ) -> bool:
        text = " ".join(
            json.dumps(
                row.get("content"),
                ensure_ascii=False,
                sort_keys=True,
            )
            for row in materials
            if isinstance(row, Mapping)
            and row.get("authority") == "user_supplied_project_input"
            and row.get("content") is not None
        ).casefold()
        chinese = (
            re.search(
                r"(?:每|/)\s*(?:吨|千克|公斤|kg|件)"
                r".{0,24}(?:多少钱|成本|费用|应缴|支付|交多少)",
                text,
            )
            is not None
            or re.search(
                r"(?:多少钱|成本|费用|应缴|支付|交多少)"
                r".{0,24}(?:每|/)\s*(?:吨|千克|公斤|kg|件)",
                text,
            )
            is not None
        )
        english = (
            re.search(
                r"\b(?:how much|cost|amount payable|payment)"
                r".{0,40}\bper\s+(?:tonne|ton|kg|unit)\b",
                text,
            )
            is not None
        )
        return chinese or english

    @staticmethod
    def _claim_modality_supported(modality: str, literal: str) -> bool:
        try:
            resolution = resolve_claim_modality(
                declared_modality=modality,
                literal=literal,
                statement=literal,
                source_sha256="0" * 64,
            )
        except ValueError:
            return False
        return (
            resolution["status"] == "resolved"
            and resolution["normalized_modality"] == modality
        )

    def classify_material(self, material: Mapping[str, object]) -> dict:
        if not isinstance(material, Mapping):
            raise ValueError("LCA material must be an object")
        classification = material.get("declared_classification")
        if classification not in FACT_CLASSES:
            raise ValueError("LCA material declared_classification is unsupported")
        material_id = material.get("material_id")
        if not isinstance(material_id, str) or not material_id.strip():
            raise ValueError("LCA material_id is absent")
        missing = [
            key for key in CLASS_REQUIREMENTS[classification] if not _present(material, key)
        ]
        if classification in {"official", "candidate"} and _present(
            material, "source_ref"
        ):
            source_ref = material["source_ref"]
            if (
                not isinstance(source_ref, Mapping)
                or not isinstance(source_ref.get("path"), str)
                or not isinstance(source_ref.get("sha256"), str)
                or SHA256_RE.fullmatch(source_ref["sha256"]) is None
            ):
                missing.append("source_ref")
        return {
            "contract": "lca_research_kernel.material_classification/v1",
            "material_id": material_id,
            "classification": classification,
            "status": "valid" if not missing else "invalid",
            "missing_fields": sorted(set(missing)),
            "normalized_material": deepcopy(dict(material)),
            "promotion_permitted": False,
        }

    def validate_goal_and_scope(self, goal_scope: Mapping[str, object]) -> dict:
        if not isinstance(goal_scope, Mapping):
            raise ValueError("LCA goal and scope must be an object")
        required = (
            "research_goal",
            "functional_unit.amount",
            "functional_unit.unit",
            "functional_unit.reference_flow_id",
            "system_boundary",
            "method_basis.authority",
            "method_basis.version",
        )
        missing = [key for key in required if not _nested_present(goal_scope, key)]
        return {
            "contract": "lca_research_kernel.goal_scope_validation/v1",
            "status": "valid" if not missing else "invalid",
            "missing_fields": missing,
            "claim_ceiling": (
                "source_bound_lca_claims"
                if not missing
                else "no_lca_result_claim_until_goal_scope_complete"
            ),
        }

    def evaluate_data_admissibility(
        self, classification_result: Mapping[str, object]
    ) -> dict:
        if classification_result.get("contract") != (
            "lca_research_kernel.material_classification/v1"
        ):
            raise ValueError("material classification result is unsupported")
        classification = classification_result.get("classification")
        status = classification_result.get("status")
        if classification not in FACT_CLASSES:
            raise ValueError("material classification is unsupported")
        if status != "valid":
            disposition = "reject_invalid_material"
        else:
            disposition = {
                "official": "admit_as_official",
                "candidate": "retain_as_candidate",
                "assumption": "retain_as_explicit_assumption",
                "computed": "admit_as_replayable_computed",
                "missing": "retain_as_explicit_gap",
            }[classification]
        return {
            "contract": "lca_research_kernel.data_admissibility/v1",
            "material_id": classification_result.get("material_id"),
            "classification": classification,
            "disposition": disposition,
            "may_promote_to_official": False,
            "missing_fields": deepcopy(
                classification_result.get("missing_fields") or []
            ),
        }

    def validate_model_and_calculation(
        self, calculation: Mapping[str, object]
    ) -> dict:
        if not isinstance(calculation, Mapping):
            raise ValueError("LCA calculation must be an object")
        required = (
            "calculation_id",
            "method.identity",
            "method.version",
            "input_fact_ids",
            "result.value",
            "result.unit",
            "unit_groups",
            "replay_command",
        )
        missing = [key for key in required if not _nested_present(calculation, key)]
        errors: list[str] = []
        unit_groups = calculation.get("unit_groups")
        if calculation.get("aggregate_across_unit_groups") is True and isinstance(
            unit_groups, Mapping
        ) and len(unit_groups) > 1:
            errors.append("cross_unit_aggregation_forbidden")
        if calculation.get("result", {}).get("unit") == "mixed":  # type: ignore[union-attr]
            errors.append("mixed_result_unit_forbidden")
        return {
            "contract": "lca_research_kernel.calculation_validation/v1",
            "status": "valid" if not missing and not errors else "invalid",
            "missing_fields": missing,
            "errors": errors,
            "claim_ceiling": (
                "replayable_calculation"
                if not missing and not errors
                else "calculation_not_admissible"
            ),
        }

    def derive_claim_boundary(
        self,
        classifications: Sequence[str],
        *,
        decision_relevant_missing: Sequence[str],
    ) -> dict:
        classes = set(classifications)
        unknown = classes - FACT_CLASSES
        if unknown:
            raise ValueError(
                "claim boundary contains unsupported classes: "
                + ", ".join(sorted(unknown))
            )
        unresolved = sorted(set(decision_relevant_missing))
        limiting = sorted(classes & {"candidate", "assumption", "missing"})
        if unresolved or "missing" in classes:
            level = "bounded_no_conclusion"
        elif classes & {"candidate", "assumption"}:
            level = "screening_only"
        elif "computed" in classes:
            level = "evidence_bounded"
        else:
            level = "source_bound"
        return {
            "contract": "lca_research_kernel.claim_boundary/v1",
            "claim_level": level,
            "limiting_classes": limiting,
            "unresolved_gaps": unresolved,
            "may_override_official_authority": False,
            "may_infer_missing_provider_weights": False,
        }

    def evaluate_source_bound_interpretation(
        self,
        official_material: Mapping[str, object],
        model_candidate: Mapping[str, object],
    ) -> dict:
        """Check a bounded interpretation without recomputing official results."""

        if not isinstance(official_material, Mapping) or not isinstance(
            model_candidate, Mapping
        ):
            raise ValueError("source-bound interpretation inputs must be objects")
        errors: list[str] = []
        rows = official_material.get("method_results")
        if not isinstance(rows, list) or not rows:
            errors.append("official_method_results_absent")
            rows = []
        by_ref = {}
        for row in rows:
            if not isinstance(row, Mapping):
                errors.append("official_method_result_invalid")
                continue
            exact_ref = row.get("method_exact_ref")
            if not isinstance(exact_ref, str) or not exact_ref:
                errors.append("official_method_identity_absent")
                continue
            if exact_ref in by_ref:
                errors.append("official_method_identity_duplicated")
            by_ref[exact_ref] = row
        has_functional_unit = isinstance(
            official_material.get("functional_unit"), Mapping
        )
        expected_sufficiency = (
            "sufficient_for_bounded_interpretation"
            if has_functional_unit
            else "insufficient_critical_data"
        )
        if model_candidate.get("sufficiency") != expected_sufficiency:
            errors.append("model_sufficiency_conflicts_with_material_state")
        source_ids = model_candidate.get("source_fact_ids")
        normalized_source_ids = (
            [
                item.removeprefix("project_fact:")
                for item in source_ids
                if isinstance(item, str)
            ]
            if isinstance(source_ids, list)
            else []
        )
        if (
            not isinstance(source_ids, list)
            or len(normalized_source_ids) != len(source_ids)
            or "research-source-material" not in normalized_source_ids
            or set(normalized_source_ids)
            - {"research-goal", "research-source-material"}
        ):
            errors.append("model_source_binding_drifted")
        if model_candidate.get("capability_intent") is not None:
            errors.append("unnecessary_capability_intent")
        if model_candidate.get("official_data_preserved") is not True:
            errors.append("model_did_not_attest_official_data_preservation")
        if model_candidate.get("cross_unit_numeric_ranking_performed") is not False:
            errors.append("cross_unit_numeric_ranking_forbidden")
        zero_claim = model_candidate.get("zero_value_claim")
        if not isinstance(zero_claim, str) or not zero_claim.strip():
            errors.append("zero_value_claim_boundary_absent")
        else:
            lowered = zero_claim.casefold()
            prohibited = any(
                phrase in lowered
                for phrase in (
                    "no impact",
                    "no emission",
                    "no emissions",
                    "没有影响",
                    "没有排放",
                    "无影响",
                    "无排放",
                )
            )
            explicit_boundary = any(
                phrase in lowered
                for phrase in (
                    "does not prove",
                    "does not mean",
                    "cannot prove",
                    "不证明",
                    "不能说明",
                    "不代表",
                )
            )
            if prohibited and not explicit_boundary:
                errors.append("zero_value_overclaim")
        statements = model_candidate.get("result_statements")
        if not isinstance(statements, list):
            errors.append("model_result_statements_invalid")
            statements = []
        if not has_functional_unit and statements:
            errors.append("missing_functional_unit_has_numeric_interpretation")
        for statement in statements:
            if not isinstance(statement, Mapping):
                errors.append("model_result_statement_invalid")
                continue
            exact_ref = statement.get("method_exact_ref")
            official = by_ref.get(exact_ref)
            if official is None:
                errors.append("model_result_identity_drifted")
                continue
            if statement.get("value") != official.get("value"):
                errors.append("model_result_value_drifted")
            if statement.get("unit") != official.get("unit"):
                errors.append("model_result_unit_drifted")
        missing_fields = model_candidate.get("missing_fields")
        if not isinstance(missing_fields, list):
            errors.append("model_missing_fields_invalid")
        elif not has_functional_unit:
            normalized_missing = {
                re.sub(r"[\s-]+", "_", item.strip().casefold())
                for item in missing_fields
                if isinstance(item, str)
            }
            if "functional_unit" not in normalized_missing:
                errors.append("functional_unit_gap_not_declared")
        interpretation = model_candidate.get("bounded_interpretation")
        if not isinstance(interpretation, str) or not interpretation.strip():
            errors.append("bounded_interpretation_absent")
        limitations = model_candidate.get("limitations")
        if not isinstance(limitations, list) or not limitations:
            errors.append("interpretation_limitations_absent")
        return {
            "contract": (
                "lca_research_kernel.source_bound_interpretation_evaluation/v1"
            ),
            "status": "passed" if not errors else "failed",
            "errors": sorted(set(errors)),
            "official_result_count": len(rows),
            "model_statement_count": len(statements),
            "functional_unit_present": has_functional_unit,
            "claim_ceiling": (
                "bounded_candidate_interpretation"
                if has_functional_unit and not errors
                else "bounded_missing_data_conclusion"
                if not has_functional_unit and not errors
                else "interpretation_not_admissible"
            ),
            "official_values_recomputed": False,
            "may_promote_to_official": False,
        }
