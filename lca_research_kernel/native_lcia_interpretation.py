"""Pure, deterministic interpretation of exact TianGong native LCIA rows."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Mapping


INTERPRETATION_CONTRACT = (
    "lca_research_kernel.native_lcia_interpretation_contract/v1"
)
PROFILE_CONTRACT = "lca_research_kernel.native_lcia_profile/v1"
SOURCE_CONTRACT = "lca_research_kernel.tiangong_bound_public_lcia_result/v1"


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError("LCIA value is not a finite decimal")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("LCIA value is not a finite decimal") from error
    if not parsed.is_finite():
        raise ValueError("LCIA value is not a finite decimal")
    return parsed


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return str(value.normalize())


def _within_tolerance(
    total: Decimal,
    component_sum: Decimal,
    *,
    relative: Decimal,
    absolute: Decimal,
) -> tuple[bool, Decimal, Decimal]:
    scale = max(abs(total), abs(component_sum))
    limit = max(absolute, relative * scale)
    error = abs(total - component_sum)
    return error <= limit, error, limit


def _unit_index(contract: Mapping[str, object]) -> dict[str, str]:
    groups = contract.get("unit_groups")
    if not isinstance(groups, Mapping) or not groups:
        raise ValueError("interpretation contract unit groups are missing")
    index: dict[str, str] = {}
    for group_id, declaration in groups.items():
        if not isinstance(group_id, str) or not isinstance(declaration, Mapping):
            raise ValueError("interpretation contract unit group is invalid")
        units = declaration.get("official_unit_strings")
        if not isinstance(units, list) or not units:
            raise ValueError("interpretation contract unit strings are missing")
        for unit in units:
            if not isinstance(unit, str) or not unit or unit in index:
                raise ValueError("interpretation contract unit string is ambiguous")
            index[unit] = group_id
    return index


def _validate_contract(contract: Mapping[str, object]) -> None:
    if contract.get("contract") != INTERPRETATION_CONTRACT:
        raise ValueError("native LCIA interpretation contract drifted")
    if contract.get("expected_indicator_count") != 25:
        raise ValueError("native LCIA indicator count contract drifted")
    _decimal(contract.get("relative_tolerance"))
    _decimal(contract.get("absolute_tolerance"))
    _unit_index(contract)
    relations = contract.get("relations")
    if not isinstance(relations, list) or len(relations) != 4:
        raise ValueError("native LCIA relation contract drifted")
    claim_policy = contract.get("claim_policy")
    if not isinstance(claim_policy, Mapping):
        raise ValueError("native LCIA claim policy is missing")
    for key in ("allowed", "forbidden", "required_caveats"):
        values = claim_policy.get(key)
        if not isinstance(values, list) or not values or len(values) != len(set(values)):
            raise ValueError(f"native LCIA claim policy {key} drifted")


def _validate_source(
    bound_result: Mapping[str, object], contract: Mapping[str, object]
) -> list[Mapping[str, object]]:
    policy = contract.get("source_policy")
    if not isinstance(policy, Mapping):
        raise ValueError("native LCIA source policy is missing")
    expected = {
        "contract": policy.get("expected_source_contract"),
        "status": policy.get("expected_source_status"),
        "cross_version_fallback_used": policy.get("cross_version_fallback_used"),
        "guessed_unit_used": policy.get("guessed_unit_used"),
        "remote_write_executed_count": policy.get("remote_write_executed_count"),
    }
    for key, value in expected.items():
        if bound_result.get(key) != value:
            raise ValueError(f"native LCIA source identity drifted: {key}")
    count = contract["expected_indicator_count"]
    rows = bound_result.get("method_results")
    if bound_result.get("method_result_count") != count or not isinstance(rows, list):
        raise ValueError("native LCIA source method count drifted")
    if len(rows) != count:
        raise ValueError("native LCIA source method row count drifted")
    return rows


def compile_native_lcia_profile(
    bound_result: Mapping[str, object],
    interpretation_contract: Mapping[str, object],
) -> dict:
    """Compile an exact, bounded interpretation profile without side effects."""

    _validate_contract(interpretation_contract)
    rows = _validate_source(bound_result, interpretation_contract)
    unit_index = _unit_index(interpretation_contract)
    required_binding = interpretation_contract["source_policy"][
        "required_unit_binding_source"
    ]
    indicators = []
    seen = set()
    values: dict[str, Decimal] = {}
    units: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("native LCIA source method row is invalid")
        method_id = row.get("method_id")
        version = row.get("method_version")
        exact_ref = row.get("method_exact_ref")
        public_impact_id = row.get("public_impact_id")
        if (
            not isinstance(method_id, str)
            or not isinstance(version, str)
            or exact_ref != f"{method_id}@{version}"
            or not isinstance(public_impact_id, str)
            or exact_ref in seen
        ):
            raise ValueError("native LCIA method identity drifted or duplicated")
        alias_used = row.get("identity_alias_used")
        alias_evidence = row.get("identity_alias_evidence")
        if public_impact_id == method_id:
            if alias_used is not False or alias_evidence is not None:
                raise ValueError("native LCIA identity alias evidence drifted")
        elif alias_used is not True or not isinstance(alias_evidence, Mapping):
            raise ValueError("native LCIA identity alias evidence is missing")
        seen.add(exact_ref)
        value = _decimal(row.get("value"))
        unit = row.get("resolved_unit")
        if not isinstance(unit, str) or unit not in unit_index:
            raise ValueError("native LCIA resolved unit is undeclared")
        if row.get("unit_binding_source") != required_binding:
            raise ValueError("native LCIA unit binding source drifted")
        if row.get("validation_status") != "exactly_bound":
            raise ValueError("native LCIA method validation status drifted")
        indicator = {
            "impact_index": row.get("impact_index"),
            "public_impact_id": public_impact_id,
            "method_id": method_id,
            "method_version": version,
            "method_exact_ref": exact_ref,
            "name": row.get("name"),
            "value": _decimal_text(value),
            "original_unit": row.get("original_unit"),
            "resolved_unit": unit,
            "unit_group": unit_index[unit],
            "unit_binding_source": row.get("unit_binding_source"),
            "identity_alias_used": alias_used,
            "identity_alias_evidence": deepcopy(alias_evidence),
            "validation_status": row.get("validation_status"),
        }
        if not isinstance(indicator["name"], str) or not indicator["name"]:
            raise ValueError("native LCIA method name is missing")
        indicators.append(indicator)
        values[exact_ref] = value
        units[exact_ref] = unit
    indicators.sort(key=lambda row: row["method_exact_ref"])

    relative = _decimal(interpretation_contract["relative_tolerance"])
    absolute = _decimal(interpretation_contract["absolute_tolerance"])
    relation_checks = []
    for relation in interpretation_contract["relations"]:
        if not isinstance(relation, Mapping):
            raise ValueError("native LCIA relation declaration is invalid")
        total_ref = relation.get("total_method_ref")
        component_refs = relation.get("component_method_refs")
        group_id = relation.get("unit_group")
        if (
            total_ref not in values
            or not isinstance(component_refs, list)
            or not component_refs
            or any(reference not in values for reference in component_refs)
            or group_id not in interpretation_contract["unit_groups"]
        ):
            raise ValueError("native LCIA relation references unknown methods")
        relation_refs = [total_ref, *component_refs]
        allowed_units = set(
            interpretation_contract["unit_groups"][group_id][
                "official_unit_strings"
            ]
        )
        if any(units[reference] not in allowed_units for reference in relation_refs):
            raise ValueError("native LCIA relation uses an undeclared unit alias")
        total = values[total_ref]
        component_sum = sum((values[reference] for reference in component_refs), Decimal(0))
        passed, error, limit = _within_tolerance(
            total,
            component_sum,
            relative=relative,
            absolute=absolute,
        )
        if not passed:
            raise ValueError("native LCIA declared relation is outside tolerance")
        relation_checks.append(
            {
                "relation_id": relation.get("relation_id"),
                "total_method_ref": total_ref,
                "component_method_refs": list(component_refs),
                "unit_group": group_id,
                "official_unit_strings": sorted(
                    {units[reference] for reference in relation_refs}
                ),
                "total_value": _decimal_text(total),
                "component_sum": _decimal_text(component_sum),
                "absolute_error": _decimal_text(error),
                "tolerance_limit": _decimal_text(limit),
                "status": "passed",
            }
        )
    relation_checks.sort(key=lambda row: row["relation_id"])

    grouped = []
    for group_id, declaration in sorted(
        interpretation_contract["unit_groups"].items()
    ):
        method_refs = [
            row["method_exact_ref"]
            for row in indicators
            if row["unit_group"] == group_id
        ]
        if method_refs:
            grouped.append(
                {
                    "unit_group": group_id,
                    "official_unit_strings": list(
                        declaration["official_unit_strings"]
                    ),
                    "method_refs": method_refs,
                    "cross_unit_numeric_ranking_allowed": False,
                }
            )
    zero_signals = [
        {
            "method_exact_ref": row["method_exact_ref"],
            "name": row["name"],
            "value": "0",
            "resolved_unit": row["resolved_unit"],
            "interpretation": "exact_zero_signal_only",
        }
        for row in indicators
        if _decimal(row["value"]) == 0
    ]
    profile = {
        "contract": PROFILE_CONTRACT,
        "source_identity": {
            "source_contract": bound_result.get("contract"),
            "source_status": bound_result.get("status"),
            "process_exact_ref": bound_result.get("process_exact_ref"),
            "binding_id": bound_result.get("binding_id"),
            "snapshot_id": bound_result.get("snapshot_id"),
            "method_index_id": bound_result.get("method_index_id"),
            "method_result_count": bound_result.get("method_result_count"),
            "result_artifact": deepcopy(bound_result.get("result_artifact")),
        },
        "indicators": indicators,
        "unit_groups": grouped,
        "relation_checks": relation_checks,
        "zero_signals": zero_signals,
        "claim_policy": deepcopy(interpretation_contract["claim_policy"]),
    }
    errors = validate_native_lcia_profile(profile)
    if errors:
        raise ValueError("native LCIA profile validation failed: " + "; ".join(errors))
    return profile


def validate_native_lcia_profile(profile: Mapping[str, object]) -> list[str]:
    """Return deterministic structural errors for one compiled profile."""

    errors = []
    if profile.get("contract") != PROFILE_CONTRACT:
        errors.append("native LCIA profile contract drifted")
    indicators = profile.get("indicators")
    if not isinstance(indicators, list) or len(indicators) != 25:
        errors.append("native LCIA profile indicator count drifted")
        indicators = []
    refs = [
        row.get("method_exact_ref") if isinstance(row, Mapping) else None
        for row in indicators
    ]
    if refs != sorted(refs) or len(set(refs)) != len(refs):
        errors.append("native LCIA profile indicator order or identity drifted")
    for row in indicators:
        if not isinstance(row, Mapping):
            errors.append("native LCIA profile indicator is invalid")
            continue
        try:
            _decimal(row.get("value"))
        except ValueError:
            errors.append("native LCIA profile indicator value is invalid")
        if not isinstance(row.get("resolved_unit"), str) or not isinstance(
            row.get("unit_group"), str
        ):
            errors.append("native LCIA profile indicator unit binding is invalid")
    relation_checks = profile.get("relation_checks")
    if not isinstance(relation_checks, list) or len(relation_checks) != 4 or any(
        not isinstance(row, Mapping) or row.get("status") != "passed"
        for row in (relation_checks or [])
    ):
        errors.append("native LCIA profile relation checks drifted")
    groups = profile.get("unit_groups")
    covered = []
    if not isinstance(groups, list) or not groups:
        errors.append("native LCIA profile unit groups are missing")
    else:
        for group in groups:
            if isinstance(group, Mapping) and isinstance(group.get("method_refs"), list):
                covered.extend(group["method_refs"])
            else:
                errors.append("native LCIA profile unit group is invalid")
    if sorted(covered) != sorted(refs) or len(covered) != len(set(covered)):
        errors.append("native LCIA profile unit group coverage drifted")
    claim_policy = profile.get("claim_policy")
    if not isinstance(claim_policy, Mapping) or not all(
        isinstance(claim_policy.get(key), list) and claim_policy[key]
        for key in ("allowed", "forbidden", "required_caveats")
    ):
        errors.append("native LCIA profile claim policy drifted")
    return errors
