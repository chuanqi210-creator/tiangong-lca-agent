"""Compile a comparative research scope into native TianGong process build plans."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Dict, Optional

JsonObject = Dict[str, Any]
REQUIRED_EVIDENCE_PATHS = (
    "target",
    "identity_decision.decision",
    "unit_of_analysis",
    "name_plan.base_name",
    "target.geography",
    "target.technology_route",
    "quantitative_reference_plan.reference_flow_id",
)

MATERIALIZATION_COPY = {
    "solid-state": {
        "base_name": (
            "Lithium iron phosphate cathode active material production, "
            "solid-state synthesis"
        ),
        "technology_description": (
            "Candidate route producing lithium iron phosphate by high-temperature "
            "solid-state reaction after precursor mixing. The local prototype "
            "models the laboratory carbon-coated candidate variant reported by "
            "the source."
        ),
    },
    "hydrothermal": {
        "base_name": (
            "Lithium iron phosphate cathode active material production, "
            "continuous supercritical hydrothermal synthesis"
        ),
        "technology_description": (
            "Candidate route producing lithium iron phosphate by continuous "
            "supercritical hydrothermal synthesis. The local prototype models "
            "the laboratory carbon-coated candidate variant reported by the "
            "source."
        ),
    },
}
MATERIALIZATION_GEOGRAPHY = {"CN": "China"}
MATERIALIZATION_REFERENCE_FLOW = (
    "Laboratory carbon-coated lithium iron phosphate cathode active material candidate"
)
MATERIALIZATION_FUNCTIONAL_UNIT = f"1 kg {MATERIALIZATION_REFERENCE_FLOW.lower()}"


def _stable_uuid(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"tiangong-lca-research:{seed}"))


def _scope_hash(scope: JsonObject) -> str:
    return hashlib.sha256(
        json.dumps(scope, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _amendment_core(amendment: JsonObject) -> JsonObject:
    return {
        key: value
        for key, value in amendment.items()
        if key not in {"amendment_id", "amendment_sha256"}
    }


def build_process_plan(
    scope: JsonObject,
    route: JsonObject,
    provenance: JsonObject,
    resolution: Optional[JsonObject] = None,
    identity_scope_sha256: Optional[str] = None,
) -> JsonObject:
    """Return one strict-gate process plan without reimplementing TianGong validation."""
    scope_key = f"{scope['scope_id']}:{scope['revision']}"
    effective_scope_sha256 = _scope_hash(scope)
    amendment = resolution.get("scope_amendment") if resolution else None
    if resolution:
        if not isinstance(amendment, dict):
            raise ValueError("resolved process plan requires a scope amendment")
        if amendment.get("effective_scope_sha256") != effective_scope_sha256:
            raise ValueError("scope amendment does not match the effective scope")
        actual_amendment_sha256 = hashlib.sha256(
            json.dumps(
                _amendment_core(amendment), ensure_ascii=False, sort_keys=True
            ).encode("utf-8")
        ).hexdigest()
        if amendment.get("amendment_sha256") != actual_amendment_sha256:
            raise ValueError("scope amendment hash is invalid")
    if identity_scope_sha256 and identity_scope_sha256 != effective_scope_sha256:
        raise ValueError("identity scope hash does not match the supplied scope")
    effective_identity = bool(resolution or identity_scope_sha256)
    identity_scope_key = (
        f"{scope_key}:effective:{effective_scope_sha256[:16]}"
        if effective_identity
        else scope_key
    )
    route_key = f"{identity_scope_key}:{route['route_id']}"
    source_id = _stable_uuid(f"scope-source:{scope_key}")
    assumption_source_id = _stable_uuid(f"controller-assumptions:{route_key}")
    performance_source_id = _stable_uuid(
        f"performance-evidence:{identity_scope_key}"
    )
    amendment_source_id = _stable_uuid(f"scope-amendment:{identity_scope_key}")
    identity_source_id = _stable_uuid(f"identity-preflight:{route_key}")
    translation_source_id = _stable_uuid(
        f"controlled-authoring-translation:{route_key}:v1"
    )
    reference_flow_id = _stable_uuid(f"reference-flow:{identity_scope_key}")
    process_id = _stable_uuid(f"process:{route_key}")
    functional_unit = scope["functional_unit"]
    geography = scope["geography"]
    temporal = scope["temporal_scope"]
    boundary = scope["system_boundary"]
    try:
        materialization_copy = MATERIALIZATION_COPY[route["route_id"]]
        materialization_geography = MATERIALIZATION_GEOGRAPHY[geography["code"]]
    except KeyError as error:
        raise ValueError(
            "No controlled English materialization copy exists for the resolved "
            f"route/geography token: {error.args[0]}"
        ) from error
    translation_manifest = {
        "schema_version": 1,
        "source_language": "zh-CN",
        "authoring_language": "en",
        "route_id": route["route_id"],
        "fields": {
            "name_plan.base_name": materialization_copy["base_name"],
            "name_plan.mix_and_location_types": materialization_geography,
            "name_plan.functional_unit_flow_properties": (
                MATERIALIZATION_FUNCTIONAL_UNIT
            ),
            "target.geography_description": materialization_geography,
            "technology.description": materialization_copy[
                "technology_description"
            ],
            "quantitative_reference_plan.reference_flow_name": (
                MATERIALIZATION_REFERENCE_FLOW
            ),
        },
        "policy": (
            "controlled vertical-slice translation required because the TianGong "
            "materializer emits @xml:lang=en for authoring fields"
        ),
    }
    translation_sha256 = hashlib.sha256(
        json.dumps(
            translation_manifest, ensure_ascii=False, sort_keys=True
        ).encode("utf-8")
    ).hexdigest()
    evidence_bindings = []
    for path in REQUIRED_EVIDENCE_PATHS:
        source_ids = [source_id]
        evidence_type = "research_scope_decision"
        if path == "identity_decision.decision":
            source_ids = (
                [identity_source_id] if resolution else [assumption_source_id]
            )
            evidence_type = (
                "native_identity_preflight"
                if resolution
                else "explicit_pending_decision"
            )
        elif path == "unit_of_analysis":
            source_ids = (
                [source_id, amendment_source_id, performance_source_id]
                if resolution
                else [source_id, assumption_source_id]
            )
            evidence_type = (
                "scope_plus_performance_measurement"
                if resolution
                else "scope_plus_deterministic_generation"
            )
        elif path == "target":
            source_ids = (
                [
                    source_id,
                    amendment_source_id,
                    performance_source_id,
                    assumption_source_id,
                ]
                if resolution
                else [source_id, assumption_source_id]
            )
            evidence_type = (
                "scope_plus_release_gate_amendment"
                if resolution
                else "scope_plus_deterministic_generation"
            )
        elif path == "target.technology_route" and resolution:
            source_ids = [source_id, amendment_source_id, performance_source_id]
            evidence_type = "scope_plus_evidence_bound_route_amendment"
        elif path == "name_plan.base_name":
            source_ids = [source_id, translation_source_id]
            if resolution:
                source_ids.insert(1, amendment_source_id)
            evidence_type = "scope_plus_controlled_authoring_translation"
        elif path == "quantitative_reference_plan.reference_flow_id":
            source_ids = (
                [
                    source_id,
                    amendment_source_id,
                    performance_source_id,
                    assumption_source_id,
                ]
                if resolution
                else [source_id, assumption_source_id]
            )
            evidence_type = "deterministic_identity_from_scope"
        evidence_bindings.append(
            {
                "field_path": path,
                "source_ids": source_ids,
                "evidence_type": evidence_type,
            }
        )
    evidence_bindings.extend(
        [
            {
                "field_path": "target.geography_description",
                "source_ids": [source_id, translation_source_id],
                "evidence_type": "scope_plus_controlled_authoring_translation",
            },
            {
                "field_path": "technology.description",
                "source_ids": (
                    [
                        source_id,
                        amendment_source_id,
                        performance_source_id,
                        translation_source_id,
                    ]
                    if resolution
                    else [source_id, translation_source_id]
                ),
                "evidence_type": "route_evidence_plus_controlled_authoring_translation",
            },
            {
                "field_path": "quantitative_reference_plan.reference_flow_name",
                "source_ids": (
                    [source_id, amendment_source_id, translation_source_id]
                    if resolution
                    else [source_id, translation_source_id]
                ),
                "evidence_type": "functional_unit_plus_controlled_authoring_translation",
            },
            {
                "field_path": "name_plan.mix_and_location_types",
                "source_ids": [source_id, translation_source_id],
                "evidence_type": "scope_plus_controlled_authoring_translation",
            },
            {
                "field_path": "name_plan.functional_unit_flow_properties",
                "source_ids": (
                    [source_id, amendment_source_id, translation_source_id]
                    if resolution
                    else [source_id, translation_source_id]
                ),
                "evidence_type": "functional_unit_plus_controlled_authoring_translation",
            },
            {
                "field_path": "quantitative_reference_plan.data_derivation_type_status",
                "source_ids": [assumption_source_id],
                "evidence_type": "conservative_missing_inventory_default",
            },
        ]
    )
    identity_decision = {
        "decision": "pending_identity_preflight",
        "status": "blocked",
        "reason": "尚未运行 TianGong identity-preflight，不能声称 reuse、update 或 create_new。",
    }
    unit_decision = "blocked_until_scaling_evidence"
    scaling_evidence_status = scope["performance_equivalence"]["status"]
    performance_equivalence: JsonObject = scope["performance_equivalence"]
    identity_generation = {
        "path": "identity_decision.decision",
        "method": "explicit_pending_state",
        "basis": "identity-preflight not yet run",
        "status": "blocked",
    }
    if resolution:
        native_identity = resolution["identity_decision"]
        performance_decision = resolution["performance_decision"]
        if native_identity["decision"] not in {
            "reuse",
            "update_same_row",
            "version_bump",
            "create_new",
        }:
            raise ValueError("resolution identity decision is not automatic")
        if not performance_decision["equivalence_result"]:
            raise ValueError(
                "resolved mass functional unit requires an equivalent performance decision"
            )
        identity_decision = {
            "decision": native_identity["decision"],
            "status": "native_preflight_passed",
            "confidence": native_identity["confidence"],
            "reason": "采用已登记 TianGong identity-preflight 决定。",
        }
        unit_decision = "ready_for_materialization"
        scaling_evidence_status = "source_bound_route_measurements"
        performance_equivalence = {
            "status": "verified_for_provisional_lab_scope",
            "decision": performance_decision["decision"],
            "relative_difference_percent": performance_decision[
                "relative_difference_percent"
            ],
            "threshold_percent": performance_decision["threshold_percent"],
            "functional_unit_decision": performance_decision[
                "functional_unit_decision"
            ],
            "protocol": performance_decision["protocol"],
            "measurements": performance_decision["measurements"],
            "limitations": performance_decision["limitations"],
        }
        identity_generation = {
            "path": "identity_decision.decision",
            "method": "native_tiangong_identity_preflight",
            "basis": native_identity["report_sha256"],
            "status": native_identity["decision"],
        }
    sources = [
        {
            "id": source_id,
            "version": "00.00.001",
            "title": f"Research scope {scope_key}",
            "source_type": "research_scope_decision",
            "uri": provenance["source"].get("path")
            or f"project://research-scope/{scope['scope_id']}/{scope['revision']}",
            "sha256": provenance["source"]["sha256"],
            "validation_status": provenance["validation"]["status"],
        },
        {
            "id": assumption_source_id,
            "version": "00.00.001",
            "title": f"Controller-generated assumptions for {route_key}",
            "source_type": "controller_assumption_manifest",
            "uri": f"project://controller-assumptions/{scope['scope_id']}/{scope['revision']}/{route['route_id']}",
            "validation_status": "explicit_unverified_assumption",
        },
        {
            "id": translation_source_id,
            "version": "00.00.001",
            "title": f"Controlled English authoring translation for {route_key}",
            "source_type": "controlled_authoring_translation",
            "uri": (
                f"project://controlled-authoring-translation/{scope['scope_id']}/"
                f"{scope['revision']}/{route['route_id']}/{translation_sha256}"
            ),
            "sha256": translation_sha256,
            "manifest": translation_manifest,
            "validation_status": "deterministic_controller_transformation",
        },
    ]
    if resolution:
        performance_decision = resolution["performance_decision"]
        native_identity = resolution["identity_decision"]
        performance_source = {
            "id": performance_source_id,
            "version": "00.00.001",
            "title": performance_decision["source"]["title"],
            "source_type": "performance_measurement_evidence",
            "uri": performance_decision["source"]["url"],
            "doi": performance_decision["source"]["doi"],
            "sha256": performance_decision["evidence_sha256"],
            "validation_status": "source_bound_and_calculated",
        }
        sources = [
            performance_source,
            *sources,
            {
                "id": amendment_source_id,
                "version": "00.00.001",
                "title": amendment["amendment_id"],
                "source_type": "release_gate_scope_amendment",
                "uri": (
                    f"project://release-gate-scope-amendment/{scope['scope_id']}/"
                    f"{scope['revision']}/{amendment['amendment_sha256']}"
                ),
                "sha256": amendment["amendment_sha256"],
                "effective_scope_sha256": effective_scope_sha256,
                "validation_status": "source_bound_provisional_local_only",
            },
            {
                "id": identity_source_id,
                "version": "00.00.001",
                "title": f"Native identity preflight for {route_key}",
                "source_type": "native_identity_preflight",
                "uri": native_identity["report_path"],
                "sha256": native_identity["report_sha256"],
                "validation_status": native_identity["status"],
            },
        ]
    return {
        "schema_version": 1,
        "kind": "process",
        "scope_id": scope["scope_id"],
        "scope_revision": scope["revision"],
        "scope_sha256": effective_scope_sha256,
        "identity_scope_key": identity_scope_key,
        "route_id": route["route_id"],
        "target": {
            "id": process_id,
            "geography": geography["code"],
            "geography_description": materialization_geography,
            "technology_route": route["technology_route"],
            "classification_path": [
                "Technosphere",
                "Materials production",
                "Battery materials",
                "Lithium iron phosphate active material",
            ],
            "intended_reference_flow": reference_flow_id,
        },
        "identity_decision": identity_decision,
        "unit_of_analysis": {
            "target_kind": "material_product",
            "decision": unit_decision,
            "functional_unit": {
                "what": functional_unit["what"],
                "how_much": f"{functional_unit['amount']} {functional_unit['unit']}",
                "how_well": functional_unit["performance"],
                "how_long": functional_unit["duration"],
            },
            "reference_flow": {
                "flow_identity": functional_unit["what"],
                "reference_unit": functional_unit["unit"],
                "reference_amount": functional_unit["amount"],
                "flow_property": "Mass",
            },
            "scaling_evidence_status": scaling_evidence_status,
        },
        "name_plan": {
            "base_name": materialization_copy["base_name"],
            "treatment_standards_routes": route["technology_route"],
            "mix_and_location_types": materialization_geography,
            "functional_unit_flow_properties": MATERIALIZATION_FUNCTIONAL_UNIT,
        },
        "quantitative_reference_plan": {
            "reference_flow_id": reference_flow_id,
            "reference_flow_name": MATERIALIZATION_REFERENCE_FLOW,
            "reference_unit": functional_unit["unit"],
            "mean_amount": functional_unit["amount"],
            "data_derivation_type_status": "Unknown derivation",
        },
        "flow_property_plan": {
            "reference_property": "Mass",
            "reference_unit": functional_unit["unit"],
        },
        "technology": {
            "description": materialization_copy["technology_description"]
        },
        "time": {
            "reference_year": temporal["reference_year"],
            "valid_until": temporal["valid_until"],
        },
        "system_boundary": {
            "type": boundary["type"],
            "includes": boundary["includes"],
            "excludes": boundary["excludes"],
            "allocation_rule": boundary["allocation_rule"],
        },
        "performance_equivalence": performance_equivalence,
        "generation_manifest": {
            "compiler": "lca_research_kernel.process_plan/v4",
            "source_language": "zh-CN",
            "authoring_language": "en",
            "validation_status": "pending_native_gate",
            "generated_values": [
                {
                    "path": "target.id",
                    "method": "uuid5",
                    "basis": f"identity_scope_key + route_id: {identity_scope_key}",
                    "status": "generated_identifier",
                },
                {
                    "path": "quantitative_reference_plan.reference_flow_id",
                    "method": "uuid5",
                    "basis": f"shared identity_scope_key: {identity_scope_key}",
                    "status": "generated_identifier",
                },
                {
                    "path": "target.classification_path",
                    "method": "controller_policy_default",
                    "basis": "LiFePO4 active-material research scope",
                    "status": (
                        "controller_classification_after_identity_preflight"
                        if resolution
                        else "assumption_pending_identity_preflight"
                    ),
                },
                identity_generation,
                {
                    "path": "unit_of_analysis.target_kind",
                    "method": "controller_semantic_classification",
                    "basis": "functional_unit.what identifies an active material product",
                    "status": (
                        "source_bound_controller_classification"
                        if resolution
                        else "assumption_pending_evidence"
                    ),
                },
                {
                    "path": "unit_of_analysis.reference_flow.flow_property",
                    "method": "unit_to_flow_property_mapping",
                    "basis": f"{functional_unit['unit']} maps to Mass",
                    "status": "controller_mapping",
                },
                {
                    "path": "flow_property_plan.reference_property",
                    "method": "unit_to_flow_property_mapping",
                    "basis": f"{functional_unit['unit']} maps to Mass",
                    "status": "controller_mapping",
                },
                {
                    "path": "quantitative_reference_plan.data_derivation_type_status",
                    "method": "conservative_missing_evidence_default",
                    "basis": "no source-bound inventory measurement exists",
                    "status": "native_enum_unknown_derivation",
                },
                {
                    "path": (
                        "name_plan + technology.description + "
                        "target.geography_description + "
                        "quantitative_reference_plan.reference_flow_name"
                    ),
                    "method": "controlled_english_authoring_copy",
                    "basis": (
                        "TianGong process materializer emits @xml:lang=en for these "
                        "fields; original Chinese scope and amendment remain unchanged "
                        "in their registered artifacts"
                    ),
                    "status": "deterministic_translation_for_schema_alignment",
                },
            ],
        },
        "evidence_manifest": {
            "sources": sources,
            "field_bindings": evidence_bindings,
        },
    }
