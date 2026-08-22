"""Deterministically select a fully admitted TianGong Open Data second case."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Dict, List, Mapping, Optional, Sequence, Tuple


JsonObject = Dict[str, object]

OPEN_DATA_SELECTION_CONTRACT = (
    "lca_research_kernel.tiangong_open_data_case_selection/v1"
)
CATALOG_OBSERVATION_CONTRACT = (
    "lca_research_kernel.tiangong_open_data_catalog_observation/v1"
)
SELECTION_POLICY_CONTRACT = (
    "lca_research_kernel.tiangong_open_data_selection_policy/v1"
)
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_GATE_ORDER = [
    "published_open_data",
    "exact_versions",
    "material_difference",
    "functional_unit_reference_flow",
    "inspectable_executable_closure",
    "provider_snapshot",
    "native_method_result",
    "zero_paid_external_dependency",
    "read_only_access",
]
_EXPECTED_RANK_ORDER = [
    "real_version_pair_available_desc",
    "official_calculation_result_directly_accessible_desc",
    "differing_fingerprint_dimensions_desc",
    "dataset_id_asc",
    "dataset_version_asc",
]
_ALLOWED_RESULT_MODES = {
    "official_calculation_bundle",
    "official_api_result",
    "official_tidas_zip_export",
}
_ALLOWED_READ_ROUTES = {"official_api_read", "official_tidas_zip_export"}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return copy.deepcopy(value)


def _text(value: object) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _exact_version(value: object) -> bool:
    token = _text(value)
    return token is not None and "latest" not in token.casefold()


def _binding(value: object, label: str) -> JsonObject:
    row = _object(value, label)
    role = _text(row.get("role"))
    path = _text(row.get("path"))
    digest = _text(row.get("sha256"))
    if role is None or path is None or digest is None:
        raise ValueError(f"{label} is incomplete")
    pure = PurePosixPath(path.replace("\\", "/"))
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"{label}.path must be workspace-relative")
    if not _HEX_SHA256.fullmatch(digest):
        raise ValueError(f"{label}.sha256 must be a lowercase SHA-256")
    return {"role": role, "path": str(pure), "sha256": digest}


def _policy(value: object) -> JsonObject:
    row = _object(value, "selection_policy")
    if row.get("contract") != SELECTION_POLICY_CONTRACT:
        raise ValueError("selection policy contract is unsupported")
    if row.get("hard_gate_order") != _EXPECTED_GATE_ORDER:
        raise ValueError("selection policy hard-gate order is unsupported")
    if row.get("rank_order") != _EXPECTED_RANK_ORDER:
        raise ValueError("selection policy rank order is unsupported")
    minimum = row.get("material_difference_min_dimensions")
    maximum_paid = row.get("maximum_paid_external_dependency_count")
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
        raise ValueError("material difference threshold must be positive")
    if maximum_paid != 0:
        raise ValueError("paid external dependency threshold must remain zero")
    result_modes = row.get("allowed_native_result_modes")
    read_routes = row.get("allowed_read_routes")
    if not isinstance(result_modes, list) or not result_modes:
        raise ValueError("allowed native result modes are missing")
    if not isinstance(read_routes, list) or not read_routes:
        raise ValueError("allowed read routes are missing")
    if set(result_modes) != _ALLOWED_RESULT_MODES:
        raise ValueError("allowed native result modes must remain TianGong-only")
    if set(read_routes) != _ALLOWED_READ_ROUTES:
        raise ValueError("allowed read routes must remain official and read-only")
    return {
        "contract": SELECTION_POLICY_CONTRACT,
        "hard_gate_order": list(_EXPECTED_GATE_ORDER),
        "rank_order": list(_EXPECTED_RANK_ORDER),
        "material_difference_min_dimensions": minimum,
        "allowed_native_result_modes": sorted(set(result_modes)),
        "allowed_read_routes": sorted(set(read_routes)),
        "maximum_paid_external_dependency_count": 0,
        "no_eligible_next_route": _text(row.get("no_eligible_next_route")),
        "selected_next_route": _text(row.get("selected_next_route")),
    }


def _difference_count(candidate: Mapping[str, object], previous: Mapping[str, object]) -> int:
    return sum(
        1
        for key, old_value in previous.items()
        if key in candidate and candidate.get(key) != old_value
    )


def _candidate_gate_result(
    candidate: JsonObject,
    policy: JsonObject,
    previous: JsonObject,
) -> Tuple[JsonObject, Tuple[object, ...]]:
    candidate_id = _text(candidate.get("candidate_id"))
    if candidate_id is None:
        raise ValueError("candidate_id is required")
    dataset = candidate.get("dataset") if isinstance(candidate.get("dataset"), dict) else {}
    model = candidate.get("model") if isinstance(candidate.get("model"), dict) else {}
    functional = (
        candidate.get("functional_unit")
        if isinstance(candidate.get("functional_unit"), dict)
        else {}
    )
    reference = (
        candidate.get("reference_flow")
        if isinstance(candidate.get("reference_flow"), dict)
        else {}
    )
    fingerprint = (
        candidate.get("domain_fingerprint")
        if isinstance(candidate.get("domain_fingerprint"), dict)
        else {}
    )
    closure = (
        candidate.get("model_closure")
        if isinstance(candidate.get("model_closure"), dict)
        else {}
    )
    provider = (
        candidate.get("provider_snapshot")
        if isinstance(candidate.get("provider_snapshot"), dict)
        else {}
    )
    method = (
        candidate.get("lcia_method")
        if isinstance(candidate.get("lcia_method"), dict)
        else {}
    )
    result = (
        candidate.get("native_result")
        if isinstance(candidate.get("native_result"), dict)
        else {}
    )
    access = (
        candidate.get("read_access")
        if isinstance(candidate.get("read_access"), dict)
        else {}
    )
    history = candidate.get("official_history_versions")
    history_versions = history if isinstance(history, list) else []
    difference_count = _difference_count(fingerprint, previous)
    versions = [
        dataset.get("version"),
        model.get("version"),
        reference.get("version"),
    ]
    checks = {
        "published_open_data": candidate.get("publication_state") == "published_reviewed"
        and candidate.get("data_space") == "Open Data",
        "exact_versions": _text(dataset.get("id")) is not None
        and _text(model.get("id")) is not None
        and all(_exact_version(version) for version in versions),
        "material_difference": difference_count
        >= policy["material_difference_min_dimensions"],
        "functional_unit_reference_flow": _text(functional.get("amount")) is not None
        and _text(functional.get("unit")) is not None
        and _text(reference.get("id")) is not None,
        "inspectable_executable_closure": closure.get("inspectable") is True
        and closure.get("executable") is True
        and closure.get("complete") is True,
        "provider_snapshot": _text(provider.get("id")) is not None
        and _exact_version(provider.get("version"))
        and provider.get("inspectable") is True,
        "native_method_result": _text(method.get("id")) is not None
        and _exact_version(method.get("version"))
        and result.get("available") is True
        and result.get("mode") in policy["allowed_native_result_modes"]
        and _text(result.get("id")) is not None
        and _exact_version(result.get("version")),
        "zero_paid_external_dependency": candidate.get("paid_external_dependency_count")
        == policy["maximum_paid_external_dependency_count"],
        "read_only_access": access.get("read_only") is True
        and access.get("route") in policy["allowed_read_routes"],
    }
    gate_rows = [
        {"gate": gate, "passed": bool(checks[gate])}
        for gate in policy["hard_gate_order"]
    ]
    first_failed = next(
        (row["gate"] for row in gate_rows if not row["passed"]), None
    )
    source_values = candidate.get("source_bindings")
    if not isinstance(source_values, list) or not source_values:
        raise ValueError(f"{candidate_id} source_bindings are required")
    source_bindings = sorted(
        [
            _binding(value, f"{candidate_id}.source_bindings[{index}]")
            for index, value in enumerate(source_values)
        ],
        key=lambda row: (row["role"], row["path"], row["sha256"]),
    )
    candidate["source_bindings"] = copy.deepcopy(source_bindings)
    candidate["official_history_versions"] = sorted(set(history_versions))
    real_pair = len(set(history_versions)) >= 2 and all(
        _exact_version(version) for version in history_versions
    )
    direct_result = result.get("directly_accessible") is True
    ledger = {
        "candidate_id": candidate_id,
        "dataset_id": dataset.get("id"),
        "dataset_version": dataset.get("version"),
        "gates": gate_rows,
        "first_failed_gate": first_failed,
        "eligible": first_failed is None,
        "real_version_pair_available": real_pair,
        "official_calculation_result_directly_accessible": direct_result,
        "differing_fingerprint_dimensions": difference_count,
        "source_bindings": source_bindings,
    }
    rank = (
        -int(real_pair),
        -int(direct_result),
        -difference_count,
        str(dataset.get("id", "")),
        str(dataset.get("version", "")),
        candidate_id,
    )
    return ledger, rank


def compile_open_data_case_selection(
    *,
    catalog_observation: Mapping[str, object],
    selection_policy: Mapping[str, object],
    previous_case_fingerprint: Mapping[str, object],
) -> JsonObject:
    """Apply every hard gate and deterministically choose one eligible case."""

    catalog = _object(catalog_observation, "catalog_observation")
    if catalog.get("contract") != CATALOG_OBSERVATION_CONTRACT:
        raise ValueError("catalog observation contract is unsupported")
    if catalog.get("data_space") != "Open Data":
        raise ValueError("catalog observation must be Open Data")
    catalog_id = _text(catalog.get("catalog_observation_id"))
    if catalog_id is None:
        raise ValueError("catalog observation ID is required")
    candidates = catalog.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("catalog observation candidates are required")
    policy = _policy(selection_policy)
    previous_row = _object(previous_case_fingerprint, "previous_case_fingerprint")
    previous = previous_row.get("domain_fingerprint")
    if not isinstance(previous, dict) or not previous:
        raise ValueError("previous case domain fingerprint is required")

    evaluated = []
    seen = set()
    for index, raw_candidate in enumerate(candidates):
        candidate = _object(raw_candidate, f"candidates[{index}]")
        candidate_id = _text(candidate.get("candidate_id"))
        if candidate_id is None or candidate_id in seen:
            raise ValueError("candidate IDs must be non-empty and unique")
        seen.add(candidate_id)
        ledger, rank = _candidate_gate_result(candidate, policy, previous)
        evaluated.append((candidate_id, candidate, ledger, rank))
    evaluated.sort(key=lambda item: item[0])
    normalized_catalog = copy.deepcopy(catalog)
    normalized_catalog["candidates"] = [copy.deepcopy(item[1]) for item in evaluated]
    eligible = sorted(
        [item for item in evaluated if item[2]["eligible"]], key=lambda item: item[3]
    )
    selected_candidate = copy.deepcopy(eligible[0][1]) if eligible else None
    selected_rank = list(eligible[0][3]) if eligible else None
    status = "selected" if eligible else "no_eligible_case"
    next_route = (
        policy["selected_next_route"]
        if eligible
        else policy["no_eligible_next_route"]
    )
    if next_route is None:
        raise ValueError("selection policy next route is missing")
    body: JsonObject = {
        "contract": OPEN_DATA_SELECTION_CONTRACT,
        "status": status,
        "catalog_observation_id": catalog_id,
        "catalog_observation_sha256": _digest(normalized_catalog),
        "selection_policy_sha256": _digest(policy),
        "previous_case_fingerprint_sha256": _digest(previous_row),
        "hard_gate_order": list(policy["hard_gate_order"]),
        "rank_order": list(policy["rank_order"]),
        "candidate_count": len(evaluated),
        "eligible_candidate_count": len(eligible),
        "candidate_gate_ledger": [item[2] for item in evaluated],
        "selected_candidate": selected_candidate,
        "selected_rank": selected_rank,
        "admission_criteria_lowered": False,
        "external_authority_substituted": False,
        "next_route": next_route,
    }
    body["selection_id"] = _digest(body)[:16]
    return body
