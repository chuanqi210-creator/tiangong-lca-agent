"""Deterministic held-out TianGong fixture compiler for controlled evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence


CONTRACT = "lca_research_kernel.controlled_evaluation_fixture_bundle/v1"
MATERIAL_CONTRACT = "lca_research_kernel.controlled_evaluation_material/v1"
ORACLE_CONTRACT = "lca_research_kernel.controlled_evaluation_oracle/v1"
MISSING_CONTRACT = (
    "lca_research_kernel.controlled_evaluation_missing_material/v1"
)
MANIFEST_CONTRACT = (
    "lca_research_kernel.controlled_evaluation_fixture_manifest/v1"
)


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(value: object) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _file_ref(root: Path, path: Path, *, role: str) -> dict:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise ValueError("fixture source is outside repository root") from error
    if not resolved.is_file():
        raise ValueError(f"fixture source is absent: {relative}")
    data = resolved.read_bytes()
    return {
        "role": role,
        "path": relative,
        "sha256": _sha256_bytes(data),
        "bytes": len(data),
    }


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"fixture JSON is unreadable: {path}") from error


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _as_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _localized_text(value: object, *, preferred: str = "en") -> str:
    rows = _as_list(value)
    normalized = [
        row
        for row in rows
        if isinstance(row, Mapping)
        and isinstance(row.get("#text"), str)
        and row["#text"].strip()
    ]
    for row in normalized:
        if row.get("@xml:lang") == preferred:
            return str(row["#text"]).strip()
    return str(normalized[0]["#text"]).strip() if normalized else ""


def _load_contract(path: Path, expected: str) -> dict:
    value = _require_mapping(_load_json(path), "fixture contract")
    if value.get("contract") != expected or value.get("schema_version") != 1:
        raise ValueError("fixture contract identity is unsupported")
    return deepcopy(dict(value))


def _negative_exact_refs(value: Mapping[str, object]) -> set[str]:
    result: set[str] = set()
    for key in ("stress_cases", "monitor_subjects"):
        for row in _as_list(value.get(key)):
            if not isinstance(row, Mapping):
                continue
            candidate = row.get("candidate_ref")
            if not isinstance(candidate, Mapping):
                continue
            process_id = candidate.get("process_id")
            version = candidate.get("process_version")
            if isinstance(process_id, str) and isinstance(version, str):
                result.add(f"{process_id}@{version}")
    return result


def _public_result_exact_ref(path: Path) -> Optional[str]:
    value = _load_json(path)
    if not isinstance(value, Mapping) or not isinstance(value.get("data"), Mapping):
        return None
    process = value["data"].get("process")
    if not isinstance(process, Mapping):
        return None
    process_id = process.get("processId")
    version = process.get("processVersion")
    if isinstance(process_id, str) and isinstance(version, str):
        return f"{process_id}@{version}"
    return None


def _compile_exclusions(
    root: Path,
    contract: Mapping[str, object],
    *,
    additional: Iterable[str],
) -> dict:
    excluded: set[str] = set(additional)
    source_refs: list[dict] = []
    reasons: dict[str, list[str]] = {}

    def add(exact_ref: str, reason: str) -> None:
        excluded.add(exact_ref)
        reasons.setdefault(exact_ref, []).append(reason)

    for row in _as_list(contract.get("explicit_exact_refs")):
        if not isinstance(row, Mapping):
            raise ValueError("explicit fixture exclusion is invalid")
        exact_ref = row.get("process_exact_ref")
        label = row.get("label")
        if not isinstance(exact_ref, str) or not isinstance(label, str):
            raise ValueError("explicit fixture exclusion identity is absent")
        add(exact_ref, label)
    for relative in _as_list(contract.get("negative_asset_sources")):
        if not isinstance(relative, str):
            raise ValueError("negative asset source path is invalid")
        path = root / relative
        source_refs.append(_file_ref(root, path, role="negative_asset_source"))
        value = _require_mapping(_load_json(path), "negative asset source")
        for exact_ref in _negative_exact_refs(value):
            add(exact_ref, "negative_authority_or_safety_evaluation_binding")
    for relative in _as_list(contract.get("historical_public_result_directories")):
        if not isinstance(relative, str):
            raise ValueError("historical result directory path is invalid")
        directory = root / relative
        if not directory.is_dir():
            raise ValueError(f"historical result directory is absent: {relative}")
        for path in sorted(directory.glob("*.json")):
            exact_ref = _public_result_exact_ref(path)
            if exact_ref is None:
                continue
            source_refs.append(
                _file_ref(root, path, role="historical_evaluation_result")
            )
            add(exact_ref, "historical_top_level_evaluation_binding")
    rows = [
        {"process_exact_ref": exact_ref, "reasons": sorted(set(reasons.get(exact_ref, ["additional_test_exclusion"])))}
        for exact_ref in sorted(excluded)
    ]
    return {
        "contract": "lca_research_kernel.controlled_evaluation_exclusion_receipt/v1",
        "policy": contract.get("policy"),
        "excluded_exact_refs": sorted(excluded),
        "exclusions": rows,
        "source_refs": source_refs,
        "exclusion_content_sha256": _sha256(rows),
    }


def _process_rows(value: object) -> dict[tuple[str, str], Mapping[str, object]]:
    container = _require_mapping(value, "exact process rows")
    rows = container.get("rows")
    if not isinstance(rows, list):
        raise ValueError("exact process rows are absent")
    result = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("exact process row is invalid")
        key = (row.get("id"), row.get("version"))
        if not all(isinstance(part, str) for part in key):
            raise ValueError("exact process row identity is absent")
        if key in result:
            raise ValueError("exact process row identity is duplicated")
        result[key] = row
    return result


def _flow_rows(value: object) -> dict[tuple[str, str], Mapping[str, object]]:
    container = _require_mapping(value, "exact flow rows")
    rows = container.get("rows")
    if not isinstance(rows, list):
        raise ValueError("exact flow rows are absent")
    result = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("exact flow row is invalid")
        key = (row.get("id"), row.get("version"))
        if not all(isinstance(part, str) for part in key):
            raise ValueError("exact flow row identity is absent")
        result[key] = row
    return result


def _extract_reference(
    process_row: Mapping[str, object],
    flows: Mapping[tuple[str, str], Mapping[str, object]],
) -> tuple[dict, dict]:
    process = _require_mapping(process_row.get("process"), "process payload")
    dataset = _require_mapping(process.get("processDataSet"), "process dataset")
    information = _require_mapping(
        dataset.get("processInformation"), "process information"
    )
    quantitative = _require_mapping(
        information.get("quantitativeReference"), "process quantitative reference"
    )
    internal_id = str(quantitative.get("referenceToReferenceFlow"))
    exchanges = _require_mapping(dataset.get("exchanges"), "process exchanges")
    matches = [
        row
        for row in _as_list(exchanges.get("exchange"))
        if isinstance(row, Mapping)
        and str(row.get("@dataSetInternalID")) == internal_id
    ]
    if len(matches) != 1:
        raise ValueError("process exact reference flow is not unique")
    exchange = matches[0]
    flow_ref = _require_mapping(
        exchange.get("referenceToFlowDataSet"), "reference flow binding"
    )
    flow_id = flow_ref.get("@refObjectId")
    flow_version = flow_ref.get("@version")
    if not isinstance(flow_id, str) or not isinstance(flow_version, str):
        raise ValueError("reference flow exact identity is absent")
    flow_row = flows.get((flow_id, flow_version))
    if flow_row is None or flow_row.get("state_code") != 100:
        raise ValueError("reference flow exact Open Data row is absent")
    flow = _require_mapping(flow_row.get("flow"), "reference flow payload")
    flow_dataset = _require_mapping(flow.get("flowDataSet"), "reference flow dataset")
    flow_information = _require_mapping(
        flow_dataset.get("flowInformation"), "reference flow information"
    )
    flow_data = _require_mapping(
        flow_information.get("dataSetInformation"),
        "reference flow data-set information",
    )
    flow_name = _require_mapping(flow_data.get("name"), "reference flow name")
    process_data = _require_mapping(
        information.get("dataSetInformation"), "process data-set information"
    )
    process_name = _require_mapping(process_data.get("name"), "process name")
    amount = exchange.get("meanAmount")
    if not isinstance(amount, (str, int, float)):
        raise ValueError("functional-unit reference amount is absent")
    functional_label = _localized_text(
        flow_name.get("functionalUnitFlowProperties")
    ) or _localized_text(process_name.get("functionalUnitFlowProperties"))
    if not functional_label:
        raise ValueError("functional-unit label is absent")
    reference_property = _require_mapping(
        flow_information.get("quantitativeReference"),
        "reference flow quantitative reference",
    ).get("referenceToReferenceFlowProperty")
    if not isinstance(reference_property, (str, int)):
        raise ValueError("reference flow property binding is absent")
    functional_unit = {
        "amount": str(amount),
        "label": functional_label,
        "reference_flow_property_internal_id": str(reference_property),
        "basis": "exact_tidas_reference_exchange_and_flow_metadata",
    }
    reference_flow = {
        "id": flow_id,
        "version": flow_version,
        "exact_ref": f"{flow_id}@{flow_version}",
        "name": _localized_text(flow_name.get("baseName")),
        "functional_property_label": functional_label,
        "reference_flow_property_internal_id": str(reference_property),
    }
    return functional_unit, reference_flow


def _compile_candidate(
    root: Path,
    bound_path: Path,
    bound: Mapping[str, object],
    processes: Mapping[tuple[str, str], Mapping[str, object]],
    flows: Mapping[tuple[str, str], Mapping[str, object]],
    shared_refs: Sequence[Mapping[str, object]],
    *,
    ready_sha256: str,
) -> dict:
    if (
        bound.get("contract")
        != "lca_research_kernel.tiangong_bound_public_lcia_result/v1"
        or bound.get("status") != "exact_public_lcia_bound"
    ):
        raise ValueError("bound TianGong result contract is unsupported")
    exact_ref = bound.get("process_exact_ref")
    if not isinstance(exact_ref, str) or "@" not in exact_ref:
        raise ValueError("bound TianGong process exact reference is absent")
    process_id, version = exact_ref.rsplit("@", 1)
    process_row = processes.get((process_id, version))
    if process_row is None or process_row.get("state_code") != 100:
        raise ValueError("exact Open Data process row is absent")
    process = _require_mapping(process_row.get("process"), "process payload")
    dataset = _require_mapping(process.get("processDataSet"), "process dataset")
    information = _require_mapping(
        dataset.get("processInformation"), "process information"
    )
    data = _require_mapping(
        information.get("dataSetInformation"), "process data-set information"
    )
    if data.get("common:UUID") != process_id:
        raise ValueError("process payload identity drifted")
    publication = _require_mapping(
        _require_mapping(
            dataset.get("administrativeInformation"),
            "process administrative information",
        ).get("publicationAndOwnership"),
        "process publication identity",
    )
    if publication.get("common:dataSetVersion") != version:
        raise ValueError("process payload version drifted")
    functional_unit, reference_flow = _extract_reference(process_row, flows)
    method_results = []
    for row in _as_list(bound.get("method_results")):
        if not isinstance(row, Mapping):
            raise ValueError("bound TianGong method result is invalid")
        method_id = row.get("method_id")
        method_version = row.get("method_version")
        value = row.get("value")
        unit = row.get("resolved_unit")
        if (
            not isinstance(method_id, str)
            or not isinstance(method_version, str)
            or not isinstance(value, (int, float))
            or not isinstance(unit, str)
            or not unit
            or unit == "unknown"
            or row.get("validation_status") != "exactly_bound"
        ):
            raise ValueError("bound TianGong method identity, value or unit is invalid")
        method_results.append(
            {
                "impact_index": row.get("impact_index"),
                "method_id": method_id,
                "method_version": method_version,
                "method_exact_ref": f"{method_id}@{method_version}",
                "name": row.get("name"),
                "value": value,
                "unit": unit,
                "unit_binding_source": row.get("unit_binding_source"),
            }
        )
    method_results.sort(key=lambda row: int(row["impact_index"]))
    process_name = _require_mapping(data.get("name"), "process name")
    sources = [
        _file_ref(root, bound_path, role="exact_bound_official_result"),
        *deepcopy(list(shared_refs)),
    ]
    for reference in _as_list(bound.get("source_bindings")):
        if not isinstance(reference, Mapping):
            raise ValueError("bound result source reference is invalid")
        relative = reference.get("path")
        if not isinstance(relative, str):
            raise ValueError("bound result source path is invalid")
        observed = _file_ref(root, root / relative, role=str(reference.get("role")))
        if observed["sha256"] != reference.get("sha256"):
            raise ValueError("bound result source reference drifted")
        sources.append(observed)
    sources = sorted(
        {row["path"]: row for row in sources}.values(),
        key=lambda row: (row["role"], row["path"]),
    )
    selection_rank = hashlib.sha256(
        f"{ready_sha256}:{exact_ref}".encode("utf-8")
    ).hexdigest()
    return {
        "process_exact_ref": exact_ref,
        "process_identity": {
            "id": process_id,
            "version": version,
            "name": _localized_text(process_name.get("baseName")),
            "geography": _require_mapping(
                information.get("geography"), "process geography"
            ).get("locationOfOperationSupplyOrProduction", {}).get("@location")
            if isinstance(
                _require_mapping(
                    information.get("geography"), "process geography"
                ).get("locationOfOperationSupplyOrProduction"),
                Mapping,
            )
            else None,
            "official_state_code": process_row.get("state_code"),
        },
        "functional_unit": functional_unit,
        "reference_flow": reference_flow,
        "method_results": method_results,
        "source_refs": sources,
        "selection_rank_sha256": selection_rank,
        "official_result_status": bound.get("status"),
        "remote_write_executed_count": bound.get("remote_write_executed_count"),
    }


def compile_controlled_evaluation_fixture(
    root: Path,
    *,
    ready_receipt_path: Path,
    contract_path: Path,
    exclusions_path: Path,
    additional_exclusions: Iterable[str] = (),
) -> dict:
    """Compile the one deterministic held-out fixture without running a case."""

    root = Path(root).resolve()
    contract = _load_contract(
        contract_path,
        "lca_research_kernel.controlled_research_evaluation_contract/v1",
    )
    exclusions = _load_contract(
        exclusions_path,
        "lca_research_kernel.controlled_evaluation_exclusions/v1",
    )
    ready = _require_mapping(_load_json(ready_receipt_path), "ready receipt")
    ready_ref = _file_ref(root, ready_receipt_path, role="prior_ready_receipt")
    sources = _require_mapping(contract.get("fixture_sources"), "fixture sources")
    process_path = root / str(sources.get("process_rows"))
    flow_path = root / str(sources.get("flow_rows"))
    method_path = root / str(sources.get("method_unit_index"))
    bound_directory = root / str(sources.get("bound_result_directory"))
    shared_refs = [
        _file_ref(root, process_path, role="exact_open_data_process_rows"),
        _file_ref(root, flow_path, role="exact_open_data_reference_flow_rows"),
        _file_ref(root, method_path, role="official_lcia_method_unit_index"),
    ]
    processes = _process_rows(_load_json(process_path))
    flows = _flow_rows(_load_json(flow_path))
    exclusion_receipt = _compile_exclusions(
        root, exclusions, additional=additional_exclusions
    )
    excluded = set(exclusion_receipt["excluded_exact_refs"])
    discovered = []
    rejected = []
    required_results = int(contract["required_method_result_count"])
    for path in sorted(bound_directory.glob("*.bound.json")):
        try:
            bound = _require_mapping(_load_json(path), "bound result")
            candidate = _compile_candidate(
                root,
                path,
                bound,
                processes,
                flows,
                shared_refs,
                ready_sha256=ready_ref["sha256"],
            )
        except ValueError as error:
            rejected.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "reason": str(error),
                }
            )
            continue
        if len(candidate["method_results"]) != required_results:
            rejected.append(
                {
                    "process_exact_ref": candidate["process_exact_ref"],
                    "reason": "official method result count is incomplete",
                }
            )
            continue
        discovered.append(candidate)
    eligible = [
        candidate
        for candidate in discovered
        if candidate["process_exact_ref"] not in excluded
    ]
    eligible.sort(key=lambda row: row["selection_rank_sha256"])
    ledger = {
        "contract": "lca_research_kernel.controlled_evaluation_candidate_ledger/v1",
        "discovered_count": len(discovered),
        "excluded_count": len(discovered) - len(eligible),
        "eligible_count": len(eligible),
        "eligible": eligible,
        "rejected": rejected,
        "source_refs": [ready_ref, *shared_refs],
    }
    selected = deepcopy(eligible[0]) if eligible else None
    selection = {
        "contract": "lca_research_kernel.controlled_evaluation_selection_receipt/v1",
        "algorithm": contract["selection_algorithm"],
        "ready_receipt_sha256": ready_ref["sha256"],
        "previous_finalizer_id": ready.get("finalizer_id"),
        "candidate_ledger_sha256": _sha256(ledger),
        "exclusion_receipt_sha256": _sha256(exclusion_receipt),
        "selected": selected,
        "selection_rank_sha256": (
            selected["selection_rank_sha256"] if selected else None
        ),
    }
    if selected is None:
        return {
            "contract": CONTRACT,
            "status": "no_eligible_held_out_fixture",
            "evaluation_contract": contract,
            "candidate_ledger": ledger,
            "exclusion_receipt": exclusion_receipt,
            "selection_receipt": selection,
            "material_package": None,
            "missing_material_package": None,
            "official_oracle": None,
            "next_route": "establish-held-out-tiangong-evaluation-fixture",
        }
    official_material = {
        key: deepcopy(selected[key])
        for key in (
            "process_exact_ref",
            "process_identity",
            "functional_unit",
            "reference_flow",
            "method_results",
            "source_refs",
        )
    }
    material = {
        "contract": MATERIAL_CONTRACT,
        "fixture_id": "held-out-" + _sha256(official_material)[:16],
        "commission": contract["commission"],
        "claim_ceiling": "candidate_only",
        "source_material": official_material,
        "untrusted_content_instruction_effect": "none",
    }
    hidden_field = "functional_unit"
    counterfactual_material = deepcopy(official_material)
    counterfactual_material.pop(hidden_field)
    missing = {
        "contract": MISSING_CONTRACT,
        "fixture_id": material["fixture_id"],
        "commission": contract["commission"],
        "hidden_field": hidden_field,
        "source_material": counterfactual_material,
        "expected_claim_ceiling": "bounded_missing_data_conclusion",
    }
    oracle_basis = {
        "contract": ORACLE_CONTRACT,
        "fixture_id": material["fixture_id"],
        "official_material": deepcopy(official_material),
        "allowed_conclusions": [
            "bounded_interpretation_only",
            "preserve_exact_tiangong_values_units_identity_and_version",
            "do_not_compare_numeric_magnitude_across_units",
            "zero_does_not_prove_no_emission_or_no_impact",
            "no_hotspot_or_contribution_ranking_without_contribution_evidence",
        ],
        "counterfactual_expected_boundary": "functional_unit_missing",
        "numeric_tolerance": {"absolute": 0.0, "relative": 0.0},
    }
    oracle_id = "oracle-" + _sha256(oracle_basis)[:16]
    oracle = {**oracle_basis, "oracle_id": oracle_id}
    oracle["oracle_sha256"] = _sha256(oracle)
    return {
        "contract": CONTRACT,
        "status": "held_out_fixture_selected",
        "evaluation_contract": contract,
        "candidate_ledger": ledger,
        "exclusion_receipt": exclusion_receipt,
        "selection_receipt": selection,
        "material_package": material,
        "missing_material_package": missing,
        "official_oracle": oracle,
        "next_route": "connect-thin-controlled-evaluation-runner",
    }


def write_controlled_evaluation_fixture(bundle: Mapping[str, object], output: Path) -> dict:
    output = Path(output)
    paths = {
        "control/evaluation-contract.json": bundle["evaluation_contract"],
        "fixture/candidate-ledger.json": bundle["candidate_ledger"],
        "fixture/exclusion-receipt.json": bundle["exclusion_receipt"],
        "fixture/selection-receipt.json": bundle["selection_receipt"],
    }
    if bundle.get("status") == "held_out_fixture_selected":
        paths.update(
            {
                "fixture/material-package.json": bundle["material_package"],
                "fixture/missing-material-package.json": bundle[
                    "missing_material_package"
                ],
                "oracle/official-oracle.json": bundle["official_oracle"],
            }
        )
    registered = []
    for relative, value in sorted(paths.items()):
        data = _canonical_bytes(value)
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        registered.append(
            {
                "path": relative,
                "sha256": _sha256_bytes(data),
                "bytes": len(data),
            }
        )
    manifest = {
        "contract": MANIFEST_CONTRACT,
        "status": bundle["status"],
        "registered_artifacts": registered,
        "next_route": bundle["next_route"],
    }
    manifest["manifest_sha256"] = _sha256(manifest)
    (output / "fixture-manifest.json").write_bytes(_canonical_bytes(manifest))
    return manifest


def verify_controlled_evaluation_fixture(output: Path) -> list[str]:
    output = Path(output)
    errors: list[str] = []
    manifest_path = output / "fixture-manifest.json"
    try:
        manifest = _require_mapping(_load_json(manifest_path), "fixture manifest")
    except ValueError as error:
        return [str(error)]
    if manifest.get("contract") != MANIFEST_CONTRACT:
        errors.append("fixture manifest contract is unsupported")
    basis = dict(manifest)
    observed_manifest_sha = basis.pop("manifest_sha256", None)
    if observed_manifest_sha != _sha256(basis):
        errors.append("fixture manifest identity drifted")
    for row in _as_list(manifest.get("registered_artifacts")):
        if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
            errors.append("registered fixture artifact is invalid")
            continue
        path = output / row["path"]
        if (
            not path.is_file()
            or _sha256_bytes(path.read_bytes()) != row.get("sha256")
            or path.stat().st_size != row.get("bytes")
        ):
            errors.append(f"registered fixture artifact drifted: {row['path']}")
    if manifest.get("status") == "held_out_fixture_selected":
        try:
            material = _load_json(output / "fixture/material-package.json")
            oracle = _require_mapping(
                _load_json(output / "oracle/official-oracle.json"), "fixture oracle"
            )
        except ValueError as error:
            errors.append(str(error))
        else:
            material_text = json.dumps(material, ensure_ascii=False, sort_keys=True)
            for forbidden in (
                str(oracle.get("oracle_id")),
                str(oracle.get("oracle_sha256")),
                "allowed_conclusions",
            ):
                if forbidden in material_text:
                    errors.append("grader-only oracle leaked into model-visible material")
    return errors


def _main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", type=Path)
    args = parser.parse_args(argv)
    if args.check is None:
        parser.error("--check is required")
    errors = verify_controlled_evaluation_fixture(args.check)
    if errors:
        raise ValueError("; ".join(errors))
    print(json.dumps({"status": "passed", "error_count": 0}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
