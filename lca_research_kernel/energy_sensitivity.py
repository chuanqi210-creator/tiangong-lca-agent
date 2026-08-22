"""Compile source-bound deterministic ton-unit energy scenarios."""

from __future__ import annotations

import copy
import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Dict, List


JsonObject = Dict[str, object]

TON_UNIT_SCENARIO_CONTRACT = "lca_research_kernel.ton_unit_energy_scenarios/v1"
TON_UNIT_GAP_ID = "ton-unit-sensitivity"


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _decimal(value: object, label: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{label} must be numeric") from error
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{label} must be finite and positive")
    return result


def _amount_text(value: Decimal) -> str:
    rendered = format(value.normalize(), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _process_id(row: JsonObject) -> str:
    try:
        value = row["processDataSet"]["processInformation"]["dataSetInformation"][
            "common:UUID"
        ]
    except (KeyError, TypeError) as error:
        raise ValueError("scenario process row is malformed") from error
    return _required_text(value, "scenario process UUID")


def _process_version(row: JsonObject) -> str:
    try:
        value = row["processDataSet"]["administrativeInformation"][
            "publicationAndOwnership"
        ]["common:dataSetVersion"]
    except (KeyError, TypeError) as error:
        raise ValueError("scenario process version is missing") from error
    return _required_text(value, "scenario process version")


def _comment(exchange: JsonObject) -> JsonObject:
    value = exchange.get("generalComment")
    rows = [value] if isinstance(value, dict) else value
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("exchange must contain exactly one generalComment")
    try:
        result = json.loads(_required_text(rows[0].get("#text"), "generalComment"))
    except json.JSONDecodeError as error:
        raise ValueError("exchange generalComment is not canonical JSON") from error
    if not isinstance(result, dict):
        raise ValueError("exchange generalComment must decode to an object")
    return result


def _energy_exchange(row: JsonObject) -> tuple[int, JsonObject]:
    try:
        exchanges = row["processDataSet"]["exchanges"]["exchange"]
    except (KeyError, TypeError) as error:
        raise ValueError("scenario exchanges are missing") from error
    if not isinstance(exchanges, list):
        raise ValueError("scenario exchanges must be a list")
    matches = []
    for index, exchange in enumerate(exchanges):
        if not isinstance(exchange, dict):
            raise ValueError("scenario exchange must be an object")
        qa = str(_comment(exchange).get("qa", ""))
        if (
            exchange.get("exchangeDirection") == "Input"
            and "[tg_io_kind_tag=energy]" in qa
            and "[tg_io_uom_tag=MJ]" in qa
        ):
            matches.append((index, exchange))
    if len(matches) != 1:
        raise ValueError("each scenario must contain exactly one MJ energy input")
    return matches[0]


def _operation(
    *,
    op: str,
    path: str,
    value: object,
    evidence: JsonObject | None = None,
) -> JsonObject:
    result: JsonObject = {"op": op, "path": path, "value": value}
    if op == "replace":
        result.update(
            {
                "basis": (
                    "Apply the source-bound metric-tonne interpretation only to "
                    "the current route energy amount."
                ),
                "evidence": copy.deepcopy(evidence or {}),
                "resolution": {
                    "mode": "deterministic_reported_ton_interpretation",
                    "changed_field_class": "energy_amount_only",
                    "comparative_lcia_allowed": False,
                    "global_sensitivity_analysis": False,
                },
            }
        )
    return result


def compile_ton_unit_scenarios(
    scenario_rows: List[JsonObject],
    inventory_plan: JsonObject,
    gap_result: JsonObject,
    *,
    scope_id: str,
    revision: str,
) -> JsonObject:
    """Compile paired short-ton/metric-tonne patches and deterministic deltas."""

    scope = _required_text(scope_id, "scope_id")
    rev = _required_text(revision, "revision")
    ranking = gap_result.get("execution_ranking")
    if not isinstance(ranking, list) or not any(
        isinstance(row, dict)
        and row.get("gap_id") == TON_UNIT_GAP_ID
        and row.get("actionability") == "ready"
        for row in ranking
    ):
        raise ValueError("ton-unit sensitivity is not a ready scientific ticket")
    routes = inventory_plan.get("routes")
    if not isinstance(routes, list) or len(routes) != 2 or len(scenario_rows) != 2:
        raise ValueError("ton-unit scenarios require exactly two routes")
    rows_by_id = {_process_id(row): row for row in scenario_rows}
    if len(rows_by_id) != 2:
        raise ValueError("scenario process identities must be unique")

    short_patch_sets = []
    metric_patch_sets = []
    route_results = []
    accepted_conversion: tuple[Decimal, Decimal, Decimal] | None = None
    for route in sorted(routes, key=lambda item: str(item.get("route_id"))):
        if not isinstance(route, dict):
            raise ValueError("inventory route must be an object")
        route_id = _required_text(route.get("route_id"), "route_id")
        if route_id not in {"solid-state", "hydrothermal"}:
            raise ValueError(f"unsupported ton-unit route: {route_id}")
        process_id = _required_text(
            route.get("scenario_process_id"), "scenario_process_id"
        )
        if process_id not in rows_by_id:
            raise ValueError("route process binding did not resolve")
        row = rows_by_id[process_id]
        version = _process_version(row)
        exchange_index, exchange = _energy_exchange(row)
        energy = route.get("energy")
        if not isinstance(energy, dict):
            raise ValueError("route energy contract is missing")
        short_amount = _decimal(energy.get("amount"), "energy.amount")
        metric_amount = _decimal(
            energy.get("metric_tonne_sensitivity_model_amount_mj"),
            "energy.metric_tonne_sensitivity_model_amount_mj",
        )
        if (
            _decimal(exchange.get("meanAmount"), "energy meanAmount") != short_amount
            or _decimal(exchange.get("resultingAmount"), "energy resultingAmount")
            != short_amount
        ):
            raise ValueError("current energy exchange drifted from the source-bound plan")
        assumptions = energy.get("conversion_assumptions")
        if not isinstance(assumptions, dict) or assumptions.get(
            "reported_ton_interpretation"
        ) != "assumed_us_short_ton_unconfirmed":
            raise ValueError("reported ton interpretation is not the accepted ambiguity")
        short_kg = _decimal(assumptions.get("short_ton_to_kg"), "short_ton_to_kg")
        metric_kg = _decimal(
            assumptions.get("metric_tonne_to_kg"), "metric_tonne_to_kg"
        )
        conversion = (
            short_kg,
            metric_kg,
            _decimal(assumptions.get("mmBtu_to_MJ"), "mmBtu_to_MJ"),
        )
        if accepted_conversion is None:
            accepted_conversion = conversion
        elif conversion != accepted_conversion:
            raise ValueError("ton conversion assumptions differ across routes")
        ratio = short_kg / metric_kg
        expected_metric = short_amount * ratio
        if abs(expected_metric - metric_amount) > Decimal("0.000000005"):
            raise ValueError("metric-tonne amount is inconsistent with the ton conversion")
        short_text = _amount_text(short_amount)
        metric_text = _amount_text(metric_amount)
        base_path = f"/processDataSet/exchanges/exchange/{exchange_index}"
        tests = [
            _operation(
                op="test",
                path=(
                    "/processDataSet/processInformation/dataSetInformation/common:UUID"
                ),
                value=process_id,
            ),
            _operation(
                op="test",
                path=(
                    "/processDataSet/administrativeInformation/"
                    "publicationAndOwnership/common:dataSetVersion"
                ),
                value=version,
            ),
            _operation(
                op="test",
                path=f"{base_path}/meanAmount",
                value=short_text,
            ),
            _operation(
                op="test",
                path=f"{base_path}/resultingAmount",
                value=short_text,
            ),
        ]
        row_index = scenario_rows.index(row)
        short_patch_sets.append(
            {
                "row_index": row_index,
                "dataset_id": process_id,
                "dataset_version": version,
                "operations": copy.deepcopy(tests),
            }
        )
        evidence = {
            "contract": TON_UNIT_SCENARIO_CONTRACT,
            "gap_id": TON_UNIT_GAP_ID,
            "scope_id": scope,
            "revision": rev,
            "route_id": route_id,
            "source_ids": sorted(energy.get("source_ids", [])),
            "reported_value": energy.get("reported_value"),
            "reported_unit": energy.get("reported_unit"),
            "model_allocation_basis": energy.get("model_allocation_basis"),
            "short_ton_to_kg": _amount_text(short_kg),
            "metric_tonne_to_kg": _amount_text(metric_kg),
        }
        metric_patch_sets.append(
            {
                "row_index": row_index,
                "dataset_id": process_id,
                "dataset_version": version,
                "operations": copy.deepcopy(tests)
                + [
                    _operation(
                        op="replace",
                        path=f"{base_path}/meanAmount",
                        value=metric_text,
                        evidence=evidence,
                    ),
                    _operation(
                        op="replace",
                        path=f"{base_path}/resultingAmount",
                        value=metric_text,
                        evidence=evidence,
                    ),
                ],
            }
        )
        route_results.append(
            {
                "route_id": route_id,
                "scenario_process_id": process_id,
                "energy_exchange_index": exchange_index,
                "carrier": _required_text(energy.get("carrier"), "energy.carrier"),
                "unit": "MJ",
                "short_ton_amount_mj": short_text,
                "metric_tonne_amount_mj": metric_text,
                "absolute_delta_mj": _amount_text(metric_amount - short_amount),
                "relative_delta_percent": _amount_text((ratio - 1) * 100),
                "metric_to_short_ratio": _amount_text(ratio),
                "model_allocation_basis": _required_text(
                    energy.get("model_allocation_basis"),
                    "energy.model_allocation_basis",
                ),
            }
        )

    short_order = [
        row["route_id"]
        for row in sorted(
            route_results,
            key=lambda item: Decimal(str(item["short_ton_amount_mj"])),
            reverse=True,
        )
    ]
    metric_order = [
        row["route_id"]
        for row in sorted(
            route_results,
            key=lambda item: Decimal(str(item["metric_tonne_amount_mj"])),
            reverse=True,
        )
    ]
    return {
        "schema_version": 1,
        "contract": TON_UNIT_SCENARIO_CONTRACT,
        "scope_id": scope,
        "revision": rev,
        "status": "paired_deterministic_ton_unit_energy_scenarios_compiled",
        "scenarios": [
            {
                "scenario_id": "reported-us-short-ton",
                "interpretation": "assumed_us_short_ton_unconfirmed",
                "patch_status": "completed",
                "patch_sets": short_patch_sets,
                "expected_changed_field_count": 0,
            },
            {
                "scenario_id": "metric-tonne",
                "interpretation": "metric_tonne",
                "patch_status": "completed",
                "patch_sets": metric_patch_sets,
                "expected_changed_field_count": 4,
            },
        ],
        "route_results": route_results,
        "ranking_stability": {
            "short_ton_descending_energy_order": short_order,
            "metric_tonne_descending_energy_order": metric_order,
            "route_order_unchanged": short_order == metric_order,
        },
        "scientific_boundary": {
            "analysis_type": "deterministic_one_factor_interpretation_bracket",
            "global_sensitivity_analysis": False,
            "probabilistic_uncertainty_analysis": False,
            "comparative_lcia_allowed": False,
            "only_energy_amounts_may_change": True,
        },
    }


def apply_ton_unit_patch(
    scenario_rows: List[JsonObject], scenario: JsonObject
) -> List[JsonObject]:
    """Apply the deliberately narrow ton-unit patch contract in memory."""

    result = copy.deepcopy(scenario_rows)
    patch_sets = scenario.get("patch_sets")
    if not isinstance(patch_sets, list) or len(patch_sets) != len(result):
        raise ValueError("ton-unit scenario patch set does not cover every route")
    for patch_set in patch_sets:
        if not isinstance(patch_set, dict):
            raise ValueError("ton-unit patch set must be an object")
        row_index = patch_set.get("row_index")
        if not isinstance(row_index, int) or not 0 <= row_index < len(result):
            raise ValueError("ton-unit patch row_index is invalid")
        row = result[row_index]
        if _process_id(row) != patch_set.get("dataset_id"):
            raise ValueError("ton-unit patch dataset identity drifted")
        for operation in patch_set.get("operations", []):
            path = operation.get("path")
            op = operation.get("op")
            value = operation.get("value")
            if path.endswith("common:UUID"):
                current = _process_id(row)
            elif path.endswith("common:dataSetVersion"):
                current = _process_version(row)
            else:
                parts = str(path).split("/")
                if len(parts) != 6 or parts[3] != "exchange":
                    raise ValueError("ton-unit patch may address only energy amounts")
                try:
                    exchange = row["processDataSet"]["exchanges"]["exchange"][
                        int(parts[4])
                    ]
                except (KeyError, IndexError, TypeError, ValueError) as error:
                    raise ValueError("ton-unit patch exchange path is invalid") from error
                field = parts[5]
                if field not in {"meanAmount", "resultingAmount"}:
                    raise ValueError("ton-unit patch may change only energy amounts")
                current = exchange.get(field)
            if op == "test":
                if current != value:
                    raise ValueError("ton-unit patch test operation failed")
            elif op == "replace":
                if not str(path).endswith(("/meanAmount", "/resultingAmount")):
                    raise ValueError("ton-unit replacement escaped the energy amount")
                exchange[field] = value
            else:
                raise ValueError("unsupported ton-unit patch operation")
    return result


def protected_ton_unit_sha256(scenario_rows: List[JsonObject]) -> str:
    """Hash every scenario field except the two allowed energy amount values."""

    protected = copy.deepcopy(scenario_rows)
    for row in protected:
        _, exchange = _energy_exchange(row)
        exchange["meanAmount"] = "__TON_UNIT_ENERGY_AMOUNT__"
        exchange["resultingAmount"] = "__TON_UNIT_ENERGY_AMOUNT__"
    payload = (
        json.dumps(
            protected,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
