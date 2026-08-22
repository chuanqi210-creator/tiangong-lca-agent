"""Validate source-bound route performance evidence and derive a comparison decision."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


JsonObject = Dict[str, Any]


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")
    return value.strip()


def _positive_number(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{path} must be a positive number")
    return float(value)


def _positive_integer(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{path} must be a positive integer")
    return value


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _source_provenance(
    evidence: JsonObject, source_ref: Optional[JsonObject]
) -> JsonObject:
    canonical_bytes = _json_bytes(evidence)
    if source_ref is None:
        source = {
            "source_type": "in_memory",
            "path": None,
            "sha256": hashlib.sha256(canonical_bytes).hexdigest(),
            "bytes": len(canonical_bytes),
        }
    else:
        source = copy.deepcopy(source_ref)
        if source.get("source_type") != "local_file":
            raise ValueError("performance_source_ref.source_type must be local_file")
        source_path = Path(_text(source.get("path"), "performance_source_ref.path")).resolve()
        raw_bytes = source_path.read_bytes()
        if json.loads(raw_bytes) != evidence:
            raise ValueError(
                "performance_source_ref.path content does not match performance_evidence"
            )
        if source.get("sha256") != hashlib.sha256(raw_bytes).hexdigest():
            raise ValueError("performance_source_ref.sha256 does not match its file")
        if source.get("bytes") != len(raw_bytes):
            raise ValueError("performance_source_ref.bytes does not match its file")
        source["path"] = str(source_path)
    return {
        "source": source,
        "original_input": copy.deepcopy(evidence),
        "canonical_sha256": hashlib.sha256(canonical_bytes).hexdigest(),
        "validation": {
            "status": "passed",
            "validator": "lca_research_kernel.route_performance_evidence/v1",
        },
    }


def evaluate_route_performance(
    evidence: JsonObject,
    scope: JsonObject,
    source_ref: Optional[JsonObject] = None,
) -> JsonObject:
    """Validate two route measurements and derive the recorded equivalence decision."""
    if evidence.get("schema_version") != 1:
        raise ValueError("performance_evidence.schema_version must be 1")
    if evidence.get("scope_id") != scope.get("scope_id"):
        raise ValueError("performance_evidence.scope_id must match the research scope")
    evidence_id = _text(evidence.get("evidence_id"), "performance_evidence.evidence_id")
    source = evidence.get("source")
    comparison = evidence.get("comparison")
    policy = evidence.get("decision_policy")
    if not isinstance(source, dict):
        raise ValueError("performance_evidence.source must be an object")
    if not isinstance(comparison, dict):
        raise ValueError("performance_evidence.comparison must be an object")
    if not isinstance(policy, dict):
        raise ValueError("performance_evidence.decision_policy must be an object")

    normalized_source = {
        "title": _text(source.get("title"), "performance_evidence.source.title"),
        "doi": _text(source.get("doi"), "performance_evidence.source.doi"),
        "url": _text(source.get("url"), "performance_evidence.source.url"),
        "record_url": _text(
            source.get("record_url"), "performance_evidence.source.record_url"
        ),
        "source_type": _text(
            source.get("source_type"), "performance_evidence.source.source_type"
        ),
        "publication_year": _positive_integer(
            source.get("publication_year"),
            "performance_evidence.source.publication_year",
        ),
        "accessed_at": _text(
            source.get("accessed_at"), "performance_evidence.source.accessed_at"
        ),
        "content_scope": _text(
            source.get("content_scope"), "performance_evidence.source.content_scope"
        ),
    }
    protocol = comparison.get("protocol")
    measurements = comparison.get("measurements")
    if not isinstance(protocol, dict):
        raise ValueError("performance_evidence.comparison.protocol must be an object")
    if not isinstance(measurements, list) or len(measurements) != 2:
        raise ValueError(
            "performance_evidence.comparison.measurements must contain two routes"
        )
    current_density = protocol.get("current_density")
    temperature = protocol.get("temperature")
    if not isinstance(current_density, dict) or not isinstance(temperature, dict):
        raise ValueError("performance protocol density and temperature must be objects")
    normalized_protocol = {
        "c_rate": _positive_number(protocol.get("c_rate"), "protocol.c_rate"),
        "current_density": {
            "value": _positive_number(
                current_density.get("value"), "protocol.current_density.value"
            ),
            "unit": _text(
                current_density.get("unit"), "protocol.current_density.unit"
            ),
        },
        "cycle": _positive_integer(protocol.get("cycle"), "protocol.cycle"),
        "temperature": {
            "value": temperature.get("value"),
            "unit": _text(temperature.get("unit"), "protocol.temperature.unit"),
            "status": _text(
                temperature.get("status"), "protocol.temperature.status"
            ),
        },
        "product_modification": _text(
            protocol.get("product_modification"), "protocol.product_modification"
        ),
        "carbon_precursor": _text(
            protocol.get("carbon_precursor"), "protocol.carbon_precursor"
        ),
    }
    if normalized_protocol["temperature"]["value"] is not None and not isinstance(
        normalized_protocol["temperature"]["value"], (int, float)
    ):
        raise ValueError("protocol.temperature.value must be numeric or null")

    property_name = _text(comparison.get("property"), "comparison.property")
    value_unit = _text(comparison.get("value_unit"), "comparison.value_unit")
    machine_policy = scope["performance_equivalence"].get("machine_policy")
    if not isinstance(machine_policy, dict):
        raise ValueError("research scope is missing performance_equivalence.machine_policy")
    supplied_policy = {
        "property": property_name,
        "value_unit": value_unit,
        "current_density_unit": normalized_protocol["current_density"]["unit"],
        "c_rate": normalized_protocol["c_rate"],
        "cycle": normalized_protocol["cycle"],
        "method": policy.get("method"),
        "threshold_percent": policy.get("threshold_percent"),
        "at_or_below_threshold": policy.get("at_or_below_threshold"),
        "above_threshold": policy.get("above_threshold"),
    }
    expected_policy = {
        key: machine_policy[key]
        for key in supplied_policy
    }
    if supplied_policy != expected_policy:
        mismatches = [
            key
            for key in supplied_policy
            if supplied_policy[key] != expected_policy[key]
        ]
        raise ValueError(
            "performance evidence does not match research scope machine policy: "
            + ", ".join(mismatches)
        )
    expected_temperature = machine_policy["test_temperature_deg_c"]
    temperature_value = normalized_protocol["temperature"]["value"]
    temperature_unit = normalized_protocol["temperature"]["unit"]
    temperature_status = normalized_protocol["temperature"]["status"]
    if temperature_unit != machine_policy["test_temperature_unit"]:
        raise ValueError(
            "performance evidence does not match research scope test temperature unit"
        )
    if temperature_value is None:
        if temperature_status != "not_reported_in_public_abstract":
            raise ValueError(
                "performance evidence does not match research scope temperature status"
            )
    elif (
        float(temperature_value) != float(expected_temperature)
        or temperature_status
        not in {"reported", "reported_in_primary_source"}
    ):
        raise ValueError(
            "performance evidence does not match research scope test temperature"
        )
    scope_route_ids = {route["route_id"] for route in scope["routes"]}
    normalized_measurements: List[JsonObject] = []
    for index, row in enumerate(measurements):
        if not isinstance(row, dict):
            raise ValueError(f"performance measurement {index} must be an object")
        normalized_measurements.append(
            {
                "route_id": _text(row.get("route_id"), f"measurements[{index}].route_id"),
                "route_variant": _text(
                    row.get("route_variant"), f"measurements[{index}].route_variant"
                ),
                "value": _positive_number(
                    row.get("value"), f"measurements[{index}].value"
                ),
            }
        )
    measurement_ids = {row["route_id"] for row in normalized_measurements}
    if measurement_ids != scope_route_ids:
        raise ValueError("performance measurements must cover each research route exactly once")
    if len(measurement_ids) != len(normalized_measurements):
        raise ValueError("performance measurements must use distinct route IDs")

    threshold = _positive_number(
        policy.get("threshold_percent"), "decision_policy.threshold_percent"
    )
    values = [row["value"] for row in normalized_measurements]
    relative_difference = abs(values[0] - values[1]) / ((values[0] + values[1]) / 2) * 100
    equivalent = relative_difference <= threshold
    limitations = evidence.get("limitations")
    if not isinstance(limitations, list) or not limitations:
        raise ValueError("performance_evidence.limitations must be a non-empty list")
    normalized_limitations = [
        _text(value, f"performance_evidence.limitations[{index}]")
        for index, value in enumerate(limitations)
    ]
    protocol_gaps = []
    if normalized_protocol["temperature"]["value"] is None:
        protocol_gaps.append("test_temperature_not_reported_in_public_abstract")
    protocol_gaps.extend(
        [
            "xrd_phase_identity_not_extracted_from_public_abstract",
            "exact_carbon_coating_condition_not_extracted_from_public_abstract",
        ]
    )
    decision = (
        "equivalent_with_protocol_deviation"
        if equivalent and protocol_gaps
        else "equivalent"
        if equivalent
        else "not_equivalent"
    )

    return {
        "schema_version": 1,
        "evidence_id": evidence_id,
        "scope_id": scope["scope_id"],
        "decision": decision,
        "equivalence_result": equivalent,
        "relative_difference_percent": relative_difference,
        "threshold_percent": threshold,
        "functional_unit_decision": (
            _text(
                policy.get("at_or_below_threshold"),
                "decision_policy.at_or_below_threshold",
            )
            if equivalent
            else _text(
                policy.get("above_threshold"), "decision_policy.above_threshold"
            )
        ),
        "property": property_name,
        "value_unit": value_unit,
        "protocol": normalized_protocol,
        "measurements": normalized_measurements,
        "source": normalized_source,
        "limitations": normalized_limitations,
        "protocol_gaps": protocol_gaps,
        "scope_protocol_alignment": {
            "status": "partial" if protocol_gaps else "aligned",
            "accepted_for": "local_prototype_only" if protocol_gaps else "research_scope",
            "gaps": protocol_gaps,
        },
        "provenance": _source_provenance(evidence, source_ref),
    }


def effective_scope_for_local_prototype(
    scope: JsonObject, decision: JsonObject
) -> JsonObject:
    """Narrow an over-broad product scope to exactly what the evidence supports."""
    effective = copy.deepcopy(scope)
    original_what = effective["functional_unit"]["what"]
    effective_what = "实验室碳包覆磷酸铁锂正极活性材料候选"
    effective["functional_unit"]["what"] = effective_what
    effective["functional_unit"]["performance"] = (
        "来源内同条件 0.1 C、第 30 圈比放电容量；测试温度未在公开摘要报告，"
        "因此只用于本地原型"
    )
    effective["performance_equivalence"] = {
        "status": "provisionally_resolved_with_protocol_deviation",
        "common_test_protocol": copy.deepcopy(decision["protocol"]),
        "decision_rule": {
            "method": "symmetric_relative_difference_percent",
            "threshold_percent": decision["threshold_percent"],
        },
        "decision": decision["decision"],
        "relative_difference_percent": decision["relative_difference_percent"],
        "measurements": copy.deepcopy(decision["measurements"]),
        "evidence_basis": [copy.deepcopy(decision["source"])],
        "protocol_gaps": copy.deepcopy(decision["protocol_gaps"]),
    }
    variants = {
        row["route_id"]: row["route_variant"] for row in decision["measurements"]
    }
    route_changes = []
    for route in effective["routes"]:
        original_variant = route["technology_route"]
        route["technology_route"] = variants[route["route_id"]]
        route["description"] = (
            route["description"]
            + " 本地原型按来源中的实验室碳包覆候选变体建模。"
        )
        route_changes.append(
            {
                "route_id": route["route_id"],
                "original": original_variant,
                "effective": route["technology_route"],
            }
        )
    effective["assumptions"] = [
        *effective["assumptions"],
        "本地物化使用证据支持的实验室碳包覆候选窄化范围，不代表工业电池级产品。",
    ]
    effective_scope_sha256 = hashlib.sha256(_json_bytes(effective)).hexdigest()
    amendment_core = {
        "status": "provisional_local_only",
        "reason": "公开证据支持实验室碳包覆候选，但不足以证明原始电池级与 25°C 产品规格。",
        "functional_unit": {
            "original_what": original_what,
            "effective_what": effective_what,
            "amount": effective["functional_unit"]["amount"],
            "unit": effective["functional_unit"]["unit"],
        },
        "route_variant_changes": route_changes,
        "effective_scope_sha256": effective_scope_sha256,
        "publication_effect": "blocked_until_full_text_and_remote_identity_review",
    }
    amendment_sha256 = hashlib.sha256(_json_bytes(amendment_core)).hexdigest()
    return {
        "effective_scope": effective,
        "scope_amendment": {
            **amendment_core,
            "amendment_id": f"scope-amendment:{scope['scope_id']}:{scope['revision']}:{amendment_sha256[:16]}",
            "amendment_sha256": amendment_sha256,
        },
    }


def service_unit_scaling_decision(decision: JsonObject) -> JsonObject:
    """Derive route-specific mass needed to provide 1 kAh at the measured cycle."""
    routes = []
    for row in decision["measurements"]:
        capacity_ah_per_kg = row["value"]
        routes.append(
            {
                "route_id": row["route_id"],
                "capacity_ah_per_kg": capacity_ah_per_kg,
                "required_mass_kg": 1000.0 / capacity_ah_per_kg,
            }
        )
    return {
        "functional_unit": (
            f"1 kAh single discharge at cycle {decision['protocol']['cycle']}"
        ),
        "unit": "kg_per_1_kAh",
        "formula": "required_mass_kg = 1000 Ah / measured_capacity_Ah_per_kg",
        "routes": routes,
        "evidence_id": decision["evidence_id"],
        "evidence_sha256": decision["evidence_sha256"],
    }


def evidence_search_request(decision: JsonObject) -> JsonObject:
    """Build a bounded native evidence-search request for the cited primary study."""
    source = decision["source"]
    protocol = decision["protocol"]
    return {
        "question": (
            "Find the primary route-comparison evidence for carbon-coated LiFePO4 "
            f"specific discharge capacity at {protocol['c_rate']} C after "
            f"{protocol['cycle']} cycles"
        ),
        "field": {
            "dataset_type": "process",
            "field_path": "unit_of_analysis.scaling_evidence",
        },
        "profile": "shallow",
        "budget": {"max_queries": 4, "max_results_per_query": 5},
        "preferred_domains": ["sciencedirect.com"],
        "required_terms": [
            "LiFePO4",
            "solid-state",
            "supercritical",
            str(int(decision["measurements"][0]["value"])),
            str(int(decision["measurements"][1]["value"])),
            source["doi"],
        ],
        "required_evidence": {
            "source_type": "primary_research_article",
            "doi": source["doi"],
        },
    }
