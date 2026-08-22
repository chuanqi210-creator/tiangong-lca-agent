"""Comparative LCA research-scope normalization and projections."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List


JsonObject = Dict[str, Any]
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _number_from_text(pattern: str, value: str, path: str) -> float:
    match = re.search(pattern, value, flags=re.IGNORECASE)
    if not match:
        raise ValueError(f"{path} must state a machine-readable numeric policy")
    return float(match.group(1))


def _performance_machine_policy(
    protocol: JsonObject, decision_rule: str, functional_unit: JsonObject
) -> JsonObject:
    """Compile the approved prose comparison rule into an executable contract."""
    capacity_text = protocol["specific_discharge_capacity"]
    c_rate = _number_from_text(
        r"(?<!°)(\d+(?:\.\d+)?)\s*C\b",
        capacity_text,
        "performance_equivalence.common_test_protocol.specific_discharge_capacity",
    )
    cycle = _number_from_text(
        r"第\s*(\d+)\s*圈",
        capacity_text,
        "performance_equivalence.common_test_protocol.specific_discharge_capacity",
    )
    temperature = _number_from_text(
        r"(\d+(?:\.\d+)?)\s*°\s*C",
        capacity_text,
        "performance_equivalence.common_test_protocol.specific_discharge_capacity",
    )
    unit_match = re.search(r"[（(]\s*([A-Za-z]+\s*/\s*[A-Za-z]+)\s*[）)]", capacity_text)
    if not unit_match:
        raise ValueError(
            "performance_equivalence.common_test_protocol.specific_discharge_capacity "
            "must state a machine-readable value unit"
        )
    value_unit = unit_match.group(1).replace(" ", "")
    threshold = _number_from_text(
        r"(\d+(?:\.\d+)?)\s*%",
        decision_rule,
        "performance_equivalence.decision_rule",
    )
    amount = functional_unit["amount"]
    unit = functional_unit["unit"]
    if unit != "kg" or float(amount) != 1.0 or "保留" not in decision_rule:
        raise ValueError(
            "performance_equivalence.decision_rule must explicitly retain the approved 1 kg mass unit"
        )
    if "1 kAh" not in decision_rule or "改为" not in decision_rule:
        raise ValueError(
            "performance_equivalence.decision_rule must explicitly state the 1 kAh fallback"
        )
    return {
        "property": "specific_discharge_capacity",
        "value_unit": value_unit,
        "current_density_unit": "mA/g",
        "c_rate": c_rate,
        "cycle": int(cycle),
        "test_temperature_deg_c": temperature,
        "test_temperature_unit": "degC",
        "method": "symmetric_relative_difference_percent",
        "threshold_percent": threshold,
        "at_or_below_threshold": "retain_1kg_mass_functional_unit",
        "above_threshold": f"switch_to_1kAh_at_cycle_{int(cycle)}_service_unit",
    }


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} must be a non-empty string")
    return value.strip()


def _slug(value: object, path: str) -> str:
    token = _text(value, path)
    if not SLUG_PATTERN.fullmatch(token):
        raise ValueError(f"{path} must be a lowercase kebab-case identifier")
    return token


def _text_list(value: object, path: str) -> List[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{path} must be a non-empty list")
    return [_text(item, f"{path}[{index}]") for index, item in enumerate(value)]


def canonicalize_comparative_scope(scope: JsonObject, revision: str) -> JsonObject:
    """Validate and copy the two-route scope accepted by the first vertical slice."""
    if not isinstance(scope, dict):
        raise ValueError("scope must be a JSON object")
    normalized_revision = _slug(revision, "revision")
    functional_unit = scope.get("functional_unit")
    boundary = scope.get("system_boundary")
    geography = scope.get("geography")
    temporal_scope = scope.get("temporal_scope")
    performance_equivalence = scope.get("performance_equivalence")
    routes = scope.get("routes")
    if not isinstance(functional_unit, dict):
        raise ValueError("functional_unit must be a JSON object")
    if not isinstance(boundary, dict):
        raise ValueError("system_boundary must be a JSON object")
    if not isinstance(geography, dict):
        raise ValueError("geography must be a JSON object")
    if not isinstance(temporal_scope, dict):
        raise ValueError("temporal_scope must be a JSON object")
    if not isinstance(performance_equivalence, dict):
        raise ValueError("performance_equivalence must be a JSON object")
    if not isinstance(routes, list) or len(routes) != 2:
        raise ValueError("routes must contain exactly two route hypotheses")

    amount = functional_unit.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount <= 0:
        raise ValueError("functional_unit.amount must be a positive number")

    normalized_routes: List[JsonObject] = []
    route_ids = set()
    for index, route in enumerate(routes):
        if not isinstance(route, dict):
            raise ValueError(f"routes[{index}] must be a JSON object")
        route_id = _slug(route.get("route_id"), f"routes[{index}].route_id")
        if route_id in route_ids:
            raise ValueError("routes must use distinct route_id values")
        route_ids.add(route_id)
        normalized_routes.append(
            {
                "route_id": route_id,
                "name": _text(route.get("name"), f"routes[{index}].name"),
                "technology_route": _text(
                    route.get("technology_route"),
                    f"routes[{index}].technology_route",
                ),
                "description": _text(
                    route.get("description"), f"routes[{index}].description"
                ),
            }
        )

    normalized: JsonObject = {
        "scope_id": _slug(scope.get("scope_id"), "scope_id"),
        "revision": normalized_revision,
        "question": _text(scope.get("question"), "question"),
        "functional_unit": {
            "what": _text(functional_unit.get("what"), "functional_unit.what"),
            "amount": amount,
            "unit": _text(functional_unit.get("unit"), "functional_unit.unit"),
            "performance": _text(
                functional_unit.get("performance"), "functional_unit.performance"
            ),
            "duration": _text(
                functional_unit.get("duration"), "functional_unit.duration"
            ),
        },
        "performance_equivalence": {
            "status": _text(
                performance_equivalence.get("status"),
                "performance_equivalence.status",
            ),
            "common_test_protocol": {},
            "decision_rule": _text(
                performance_equivalence.get("decision_rule"),
                "performance_equivalence.decision_rule",
            ),
            "evidence_basis": [],
        },
        "system_boundary": {
            "type": _slug(
                _text(boundary.get("type"), "system_boundary.type").replace("_", "-"),
                "system_boundary.type",
            ),
            "includes": _text_list(boundary.get("includes"), "system_boundary.includes"),
            "excludes": _text_list(boundary.get("excludes"), "system_boundary.excludes"),
            "allocation_rule": _text(
                boundary.get("allocation_rule"), "system_boundary.allocation_rule"
            ),
        },
        "geography": {
            "code": _text(geography.get("code"), "geography.code"),
            "description": _text(
                geography.get("description"), "geography.description"
            ),
        },
        "temporal_scope": {
            "reference_year": temporal_scope.get("reference_year"),
            "valid_from": temporal_scope.get("valid_from"),
            "valid_until": temporal_scope.get("valid_until"),
        },
        "routes": normalized_routes,
        "assumptions": _text_list(scope.get("assumptions"), "assumptions"),
    }
    protocol = performance_equivalence.get("common_test_protocol")
    if not isinstance(protocol, dict):
        raise ValueError("performance_equivalence.common_test_protocol must be an object")
    for key in ("phase_identity", "specific_discharge_capacity", "material_modification"):
        normalized["performance_equivalence"]["common_test_protocol"][key] = _text(
            protocol.get(key), f"performance_equivalence.common_test_protocol.{key}"
        )
    normalized["performance_equivalence"]["machine_policy"] = (
        _performance_machine_policy(
            normalized["performance_equivalence"]["common_test_protocol"],
            normalized["performance_equivalence"]["decision_rule"],
            normalized["functional_unit"],
        )
    )
    evidence_basis = performance_equivalence.get("evidence_basis")
    if not isinstance(evidence_basis, list) or not evidence_basis:
        raise ValueError("performance_equivalence.evidence_basis must be a non-empty list")
    for index, source in enumerate(evidence_basis):
        if not isinstance(source, dict):
            raise ValueError(
                f"performance_equivalence.evidence_basis[{index}] must be an object"
            )
        normalized["performance_equivalence"]["evidence_basis"].append(
            {
                "title": _text(
                    source.get("title"),
                    f"performance_equivalence.evidence_basis[{index}].title",
                ),
                "doi": _text(
                    source.get("doi"),
                    f"performance_equivalence.evidence_basis[{index}].doi",
                ),
                "url": _text(
                    source.get("url"),
                    f"performance_equivalence.evidence_basis[{index}].url",
                ),
                "supports": _text(
                    source.get("supports"),
                    f"performance_equivalence.evidence_basis[{index}].supports",
                ),
            }
        )
    for field in ("reference_year", "valid_from", "valid_until"):
        value = normalized["temporal_scope"][field]
        if isinstance(value, bool) or not isinstance(value, int) or not 1900 <= value <= 2200:
            raise ValueError(f"temporal_scope.{field} must be a four-digit year")
    if normalized["temporal_scope"]["valid_from"] > normalized["temporal_scope"]["valid_until"]:
        raise ValueError("temporal_scope.valid_from must not exceed valid_until")
    if (
        normalized["performance_equivalence"]["status"]
        != "blocked_until_route_evidence"
    ):
        raise ValueError(
            "performance_equivalence.status must remain blocked_until_route_evidence "
            "until the kernel supports source-bound route measurements"
        )
    return copy.deepcopy(normalized)


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _normalization_changes(
    original: object, normalized: object, path: str = ""
) -> List[JsonObject]:
    changes: List[JsonObject] = []
    if isinstance(original, dict) and isinstance(normalized, dict):
        for key in sorted(set(original) | set(normalized)):
            child_path = f"{path}.{key}" if path else key
            if key not in original:
                changes.append(
                    {
                        "path": child_path,
                        "operation": "added",
                        "original": None,
                        "normalized": normalized[key],
                    }
                )
            elif key not in normalized:
                changes.append(
                    {
                        "path": child_path,
                        "operation": "omitted",
                        "original": original[key],
                        "normalized": None,
                    }
                )
            else:
                changes.extend(
                    _normalization_changes(original[key], normalized[key], child_path)
                )
        return changes
    if original != normalized:
        changes.append(
            {
                "path": path or "<root>",
                "operation": "normalized",
                "original": original,
                "normalized": normalized,
            }
        )
    return changes


def build_scope_provenance(
    original: JsonObject,
    canonical: JsonObject,
    source_ref: JsonObject | None,
) -> JsonObject:
    """Bind canonical scope state to its original source and transformations."""
    original_bytes = _json_bytes(original)
    if source_ref is None:
        source: JsonObject = {
            "source_type": "in_memory",
            "path": None,
            "sha256": hashlib.sha256(original_bytes).hexdigest(),
            "bytes": len(original_bytes),
        }
    else:
        source = copy.deepcopy(source_ref)
        source_type = _text(source.get("source_type"), "source_ref.source_type")
        source["source_type"] = source_type
        if source_type == "local_file":
            source_path = Path(_text(source.get("path"), "source_ref.path")).resolve()
            file_bytes = source_path.read_bytes()
            file_payload = json.loads(file_bytes)
            if file_payload != original:
                raise ValueError("source_ref.path content does not match the supplied scope")
            actual_hash = hashlib.sha256(file_bytes).hexdigest()
            if source.get("sha256") != actual_hash:
                raise ValueError("source_ref.sha256 does not match source_ref.path")
            if source.get("bytes") != len(file_bytes):
                raise ValueError("source_ref.bytes does not match source_ref.path")
            source["path"] = str(source_path)
        elif source_type != "in_memory":
            raise ValueError("source_ref.source_type must be local_file or in_memory")
    return {
        "source": source,
        "original_input": copy.deepcopy(original),
        "original_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "canonical_sha256": hashlib.sha256(_json_bytes(canonical)).hexdigest(),
        "transformations": _normalization_changes(original, canonical),
        "validation": {
            "status": "passed",
            "validator": "lca_research_kernel.comparative_scope/v1",
        },
        "assumption_paths": [
            "assumptions",
            "performance_equivalence.decision_rule",
        ],
        "missing_data_paths": [
            "performance_equivalence.route_measurements",
            "routes[*].inventory_evidence",
        ],
    }


def functional_unit_text(scope: JsonObject) -> str:
    unit = scope["functional_unit"]
    return f"{unit['amount']} {unit['unit']} {unit['what']}"


def machine_scope_view(
    scope: JsonObject, provenance: JsonObject | None = None
) -> JsonObject:
    view = {
        "scope_id": scope["scope_id"],
        "revision": scope["revision"],
        "question": scope["question"],
        "functional_unit": functional_unit_text(scope),
        "system_boundary": scope["system_boundary"]["type"],
        "geography": scope["geography"]["code"],
        "temporal_scope": scope["temporal_scope"],
        "performance_equivalence_status": scope["performance_equivalence"]["status"],
        "performance_equivalence_rule": scope["performance_equivalence"][
            "decision_rule"
        ],
        "routes": [
            {
                "route_id": route["route_id"],
                "name": route["name"],
                "technology_route": route["technology_route"],
            }
            for route in scope["routes"]
        ],
    }
    if provenance:
        view["source"] = provenance["source"]
        view["canonical_sha256"] = provenance["canonical_sha256"]
        view["validation_status"] = provenance["validation"]["status"]
    return view


def human_scope_lines(
    scope: JsonObject,
    provenance: JsonObject | None = None,
    title: str = "研究范围",
) -> List[str]:
    boundary_labels = {
        "cradle-to-gate": "从摇篮到工厂大门",
        "gate-to-gate": "从工厂大门到工厂大门",
        "cradle-to-grave": "从摇篮到坟墓",
    }
    boundary = scope["system_boundary"]
    temporal = scope["temporal_scope"]
    lines = [
        f"## {title}",
        "",
        f"- 研究问题：{scope['question']}",
        f"- 功能单位：{functional_unit_text(scope)}",
        "- 等效性能要求：" + scope["functional_unit"]["performance"],
        "- 系统边界：" + boundary_labels.get(boundary["type"], boundary["type"]),
        f"- 地理范围：{scope['geography']['description']}（{scope['geography']['code']}）",
        f"- 时间范围：{temporal['valid_from']}–{temporal['valid_until']}，参考年 {temporal['reference_year']}",
        f"- 分配规则：{boundary['allocation_rule']}",
        "- 比较路线：",
    ]
    lines.extend(
        f"  - {route['name']}（{route['technology_route']}）"
        for route in scope["routes"]
    )
    equivalence = scope["performance_equivalence"]
    protocol = equivalence["common_test_protocol"]
    lines.extend(
        [
            "- 性能等效状态：" + equivalence["status"],
            "- 共同测试条件：" + protocol["specific_discharge_capacity"],
            "- 等效决策规则：" + equivalence["decision_rule"],
            "- 性能依据：" + "; ".join(
                f"{source['title']}（DOI: {source['doi']}）"
                for source in equivalence["evidence_basis"]
            ),
        ]
    )
    if provenance:
        source = provenance["source"]
        lines.extend(
            [
                f"- 范围来源：{source.get('path') or source['source_type']}",
                f"- 规范化校验：{provenance['validation']['status']}，变换 {len(provenance['transformations'])} 项",
            ]
        )
    return lines
