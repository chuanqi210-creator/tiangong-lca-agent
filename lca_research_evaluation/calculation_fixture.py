"""Freeze and verify the first real TianGong-native calculation fixture.

The compiler derives the inventory and LCIA oracle from exact, source-bound
TIDAS unit processes.  It does not invoke openLCA; execution belongs to the
next accepted beat.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping


CONTRACT_PATH = Path("lca_research_kernel/contracts/controlled-lca-calculation-v1.json")


def _bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_bytes(value)).hexdigest()


def _file_binding(root: Path, path: Path) -> dict[str, object]:
    data = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_bytes(value) + b"\n")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _as_list(value: object) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list) and all(isinstance(row, dict) for row in value):
        return value
    raise ValueError("expected an object or list of objects")


def _process_dataset(path: Path) -> dict[str, Any]:
    return _load(path)["processDataSet"]


def _flow_dataset(path: Path) -> dict[str, Any]:
    return _load(path)["flowDataSet"]


def _process_exchanges(dataset: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _as_list(dataset.get("exchanges", {}).get("exchange"))


def _process_id(dataset: Mapping[str, Any]) -> str:
    return str(
        dataset["processInformation"]["dataSetInformation"]["common:UUID"]
    )


def _reference_exchange(dataset: Mapping[str, Any]) -> dict[str, Any]:
    reference = str(
        dataset["processInformation"]["quantitativeReference"][
            "referenceToReferenceFlow"
        ]
    )
    for exchange in _process_exchanges(dataset):
        if str(exchange.get("@dataSetInternalID")) == reference:
            return exchange
    raise ValueError("process reference exchange is missing")


def _flow_type(dataset: Mapping[str, Any]) -> str:
    return str(dataset["modellingAndValidation"]["LCIMethod"]["typeOfDataSet"])


def _scientific_inputs(
    *,
    process_rows: list[dict[str, object]],
    provider_connections: list[dict[str, object]],
    functional_unit: dict[str, object] | None,
    impact_method: dict[str, object],
) -> dict[str, object]:
    value: dict[str, object] = {
        "processes": process_rows,
        "provider_connections": provider_connections,
        "impact_method": {
            key: impact_method[key]
            for key in ("id", "version", "reference_unit")
        },
    }
    if functional_unit is not None:
        value["functional_unit"] = functional_unit
    return value


def validate_calculation_fixture(
    material: Mapping[str, Any],
    oracle: Mapping[str, Any],
    selection_receipt: Mapping[str, Any],
) -> None:
    if oracle.get("native_model_inputs", {}).get("execution_mode") != "openlca_ipc":
        raise ValueError("fixture must execute through openLCA IPC")
    if not oracle.get("provider_connections"):
        raise ValueError("provider graph must be materially present")
    if "expected_results" in material or "calculation-oracle" in json.dumps(
        material, sort_keys=True
    ):
        raise ValueError("oracle leaked into model-visible material")
    if selection_receipt.get("selection_strategy") != (
        "sha256_ascending_over_exact_process_and_method_refs"
    ):
        raise ValueError("selection order is not deterministic")
    if oracle.get("numeric_tolerance", {}).get("basis") != (
        "preexecution_ieee754_solver_tolerance"
    ):
        raise ValueError("numeric tolerance was not frozen before execution")
    if material.get("external_writes") != 0 or oracle.get("external_writes") != 0:
        raise ValueError("calculation fixture permits an external write")
    required = {
        "process_identity",
        "process_version",
        "reference_flow",
        "functional_unit",
        "method_identity",
        "method_version",
        "native_model_inputs",
        "expected_results",
        "source_bindings",
    }
    missing = sorted(required - set(oracle))
    if missing:
        raise ValueError(f"oracle is incomplete: {missing}")
    if not oracle["expected_results"].get("impacts"):
        raise ValueError("oracle has no deterministic impact")


def compile_calculation_fixture(
    root: Path,
    output_root: Path,
    *,
    selection_seed: str,
) -> dict[str, Any]:
    root = root.resolve()
    output_root = output_root.resolve()
    contract = _load(root / CONTRACT_PATH)
    source_root = root / contract["source_root"]

    candidates = []
    for row in contract["selection"]["eligible_candidates"]:
        candidate = dict(row)
        candidate["rank"] = hashlib.sha256(
            (
                f"{selection_seed}:{row['process_id']}@{row['version']}:"
                f"{row['method_id']}@{row['method_version']}"
            ).encode("utf-8")
        ).hexdigest()
        candidates.append(candidate)
    if not candidates:
        raise ValueError("no eligible calculation fixture candidate")
    candidates.sort(key=lambda row: row["rank"])
    selected = candidates[0]

    process_paths: dict[str, Path] = {}
    processes: dict[str, dict[str, Any]] = {}
    process_rows = []
    for row in contract["native_model"]["unit_processes"]:
        pattern = f"{row['id']}_{row['version']}.json"
        path = source_root / "processes" / pattern
        dataset = _process_dataset(path)
        if _process_id(dataset) != row["id"]:
            raise ValueError(f"process identity drifted: {path}")
        process_paths[row["id"]] = path
        processes[row["id"]] = dataset
        ref = _reference_exchange(dataset)
        process_rows.append(
            {
                "process_id": row["id"],
                "version": row["version"],
                "reference_flow_id": ref["referenceToFlowDataSet"]["@refObjectId"],
                "reference_amount": str(ref["resultingAmount"]),
            }
        )

    connections = [dict(row) for row in contract["native_model"]["provider_connections"]]
    root_spec = contract["native_model"]["root_process"]
    scales: dict[str, Decimal] = {
        root_spec["id"]: Decimal(str(root_spec["reference_amount"]))
        / Decimal(str(_reference_exchange(processes[root_spec["id"]])["resultingAmount"]))
    }
    unresolved = list(reversed(connections))
    while unresolved:
        changed = False
        for edge in list(unresolved):
            consumer = edge["consumer_process_id"]
            if consumer not in scales:
                continue
            consumer_exchange = next(
                row
                for row in _process_exchanges(processes[consumer])
                if row["referenceToFlowDataSet"]["@refObjectId"] == edge["flow_id"]
                and row["exchangeDirection"] == "Input"
            )
            provider = edge["provider_process_id"]
            provider_ref = _reference_exchange(processes[provider])
            scales[provider] = (
                scales[consumer]
                * Decimal(str(consumer_exchange["resultingAmount"]))
                / Decimal(str(provider_ref["resultingAmount"]))
            )
            unresolved.remove(edge)
            changed = True
        if not changed:
            raise ValueError("provider graph is not connected to the root process")

    flow_paths: dict[tuple[str, str], Path] = {}
    for path in (source_root / "flows").glob("*.json"):
        identity, version = path.stem.rsplit("_", 1)
        flow_paths[(identity, version)] = path
    inventory: dict[tuple[str, str, str], Decimal] = defaultdict(Decimal)
    for process_id, dataset in processes.items():
        for exchange in _process_exchanges(dataset):
            ref = exchange["referenceToFlowDataSet"]
            flow_id = ref["@refObjectId"]
            flow_key = (flow_id, str(ref["@version"]))
            flow = _flow_dataset(flow_paths[flow_key])
            if _flow_type(flow) != "Elementary flow":
                continue
            direction = str(exchange["exchangeDirection"]).upper()
            signed = Decimal(str(exchange["resultingAmount"])) * scales[process_id]
            if direction == "INPUT":
                signed = -signed
            inventory[(flow_id, str(ref["@version"]), direction)] += signed
    inventory = {
        key: amount for key, amount in inventory.items() if amount != Decimal("0")
    }

    official_spec = contract["native_model"]["official_result_process"]
    official_path = (
        source_root
        / "processes"
        / f"{official_spec['id']}_{official_spec['version']}.json"
    )
    official = _process_dataset(official_path)
    official_inventory: dict[tuple[str, str, str], Decimal] = {}
    for exchange in _process_exchanges(official):
        ref = exchange["referenceToFlowDataSet"]
        flow_id = ref["@refObjectId"]
        flow_key = (flow_id, str(ref["@version"]))
        if _flow_type(_flow_dataset(flow_paths[flow_key])) != "Elementary flow":
            continue
        direction = str(exchange["exchangeDirection"]).upper()
        signed = Decimal(str(exchange["resultingAmount"]))
        if direction == "INPUT":
            signed = -signed
        official_inventory[(flow_id, str(ref["@version"]), direction)] = signed
    if inventory != official_inventory:
        raise ValueError("independently aggregated inventory does not match official root")

    factor_map = {
        (row["flow_id"], row["flow_version"], row["direction"]): Decimal(
            str(row["value"])
        )
        for row in contract["impact_method"]["factors"]
    }
    impact = sum(
        amount * factor_map.get(key, Decimal("0"))
        for key, amount in inventory.items()
    )

    functional_unit = {
        "amount": str(root_spec["reference_amount"]),
        "unit": root_spec["reference_unit"],
        "reference_flow_id": root_spec["reference_flow_id"],
    }
    scientific = _scientific_inputs(
        process_rows=process_rows,
        provider_connections=connections,
        functional_unit=functional_unit,
        impact_method=contract["impact_method"],
    )
    scientific_without_fu = _scientific_inputs(
        process_rows=process_rows,
        provider_connections=connections,
        functional_unit=None,
        impact_method=contract["impact_method"],
    )
    source_paths = [
        source_root / relative for relative in contract["source_paths"]
    ] + [root / relative for relative in contract["method_source_paths"]]
    used_processes = [*processes.values(), official]
    source_paths.extend(
        flow_paths[
            (
                str(exchange["referenceToFlowDataSet"]["@refObjectId"]),
                str(exchange["referenceToFlowDataSet"]["@version"]),
            )
        ]
        for dataset in used_processes
        for exchange in _process_exchanges(dataset)
    )
    source_paths = sorted(set(source_paths))
    bindings = [_file_binding(root, path) for path in source_paths]
    bindings.append(_file_binding(root, root / CONTRACT_PATH))
    bindings.sort(key=lambda row: row["path"])

    material = {
        "contract": "lca_research_evaluation.calculation_material/v1",
        "fixture_scope": "controlled_native_lca_calculation",
        "scientific_inputs": scientific,
        "scientific_inputs_sha256": _sha(scientific),
        "functional_unit": functional_unit,
        "source_refs": [
            {"path": row["path"], "sha256": row["sha256"]} for row in bindings
        ],
        "external_writes": 0,
        "claim_ceiling": "candidate_only_until_assurance_acceptance",
    }
    missing = {
        key: value for key, value in material.items() if key != "functional_unit"
    }
    missing["scientific_inputs"] = scientific_without_fu
    missing["scientific_inputs_sha256"] = _sha(scientific_without_fu)
    missing["hidden_field"] = "functional_unit"

    inventory_rows = [
        {
            "flow_id": key[0],
            "flow_version": key[1],
            "direction": key[2],
            "amount": str(amount),
        }
        for key, amount in sorted(inventory.items())
    ]
    oracle = {
        "contract": "lca_research_evaluation.calculation_oracle/v1",
        "process_identity": selected["process_id"],
        "process_version": selected["version"],
        "reference_flow": functional_unit["reference_flow_id"],
        "functional_unit": functional_unit,
        "method_identity": selected["method_id"],
        "method_version": selected["method_version"],
        "native_model_inputs": {
            "execution_mode": contract["native_model"]["execution_mode"],
            "calculation_type": contract["native_model"]["calculation_type"],
            "processes": process_rows,
            "provider_scales": {
                key: str(value) for key, value in sorted(scales.items())
            },
        },
        "provider_connections": connections,
        "expected_results": {
            "derivation": "independent_tidas_inventory_oracle",
            "inventory": inventory_rows,
            "impacts": [
                {
                    "method_id": selected["method_id"],
                    "method_version": selected["method_version"],
                    "value": str(impact),
                    "unit": contract["impact_method"]["reference_unit"],
                }
            ],
        },
        "scientific_inputs_sha256": material["scientific_inputs_sha256"],
        "scientific_inputs_sha256_without_functional_unit": missing[
            "scientific_inputs_sha256"
        ],
        "source_bindings": bindings,
        "numeric_tolerance": contract["numeric_tolerance"],
        "external_writes": 0,
    }
    receipt = {
        "contract": "lca_research_evaluation.calculation_selection_receipt/v1",
        "selection_seed": selection_seed,
        "selection_strategy": contract["selection"]["strategy"],
        "eligible_candidate_count": len(candidates),
        "ranked_candidates": candidates,
        "selected": selected,
        "selected_before_execution": True,
    }
    failure = {
        "contract": "lca_research_evaluation.calculation_tool_failure_plan/v1",
        "failure_class": "retryable_transport_failure",
        "injected_attempts": [1],
        "max_attempts": 2,
        "scientific_inputs_sha256": material["scientific_inputs_sha256"],
        "external_writes": 0,
        "recovery": "retry_same_content_addressed_calculation_request",
    }
    validate_calculation_fixture(material, oracle, receipt)
    fixture_id = _sha(
        {
            "selected": selected,
            "scientific_inputs_sha256": material["scientific_inputs_sha256"],
            "oracle_sha256": _sha(oracle),
        }
    )[:16]
    manifest = {
        "contract": "lca_research_evaluation.calculation_fixture_manifest/v1",
        "status": "executable_fixture_frozen",
        "fixture_id": fixture_id,
        "eligible_candidate_count": len(candidates),
        "selected_exact_ref": (
            f"{selected['process_id']}@{selected['version']}:"
            f"{selected['method_id']}@{selected['method_version']}"
        ),
        "files": {},
    }
    artifacts = {
        "control/selection-receipt.json": receipt,
        "fixture/material-package.json": material,
        "fixture/missing-critical-field-package.json": missing,
        "fixture/tool-failure-plan.json": failure,
        "oracle/calculation-oracle.json": oracle,
    }
    for relative, value in artifacts.items():
        _write(output_root / relative, value)
        manifest["files"][relative] = _sha(value)
    _write(output_root / "fixture-manifest.json", manifest)
    return manifest


def verify_calculation_fixture(output_root: Path) -> dict[str, Any]:
    output_root = output_root.resolve()
    errors: list[str] = []
    try:
        manifest = _load(output_root / "fixture-manifest.json")
        material = _load(output_root / "fixture/material-package.json")
        oracle = _load(output_root / "oracle/calculation-oracle.json")
        receipt = _load(output_root / "control/selection-receipt.json")
        validate_calculation_fixture(material, oracle, receipt)
        for relative, expected in manifest.get("files", {}).items():
            actual = _sha(_load(output_root / relative))
            if actual != expected:
                errors.append(f"{relative}: content hash drifted")
        if manifest.get("status") != "executable_fixture_frozen":
            errors.append("fixture status is not frozen")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        errors.append(str(error))
    return {
        "contract": "lca_research_evaluation.calculation_fixture_verification/v1",
        "status": "passed" if not errors else "failed",
        "error_count": len(errors),
        "errors": errors,
    }
