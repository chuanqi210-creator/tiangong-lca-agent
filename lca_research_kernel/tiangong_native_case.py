"""Bind one selected TianGong case to immutable native result evidence."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import PurePosixPath
from typing import Dict, List, Mapping, Optional, Sequence

from .tiangong_authority import verify_authority_receipt


JsonObject = Dict[str, object]

NATIVE_CASE_BINDING_CONTRACT = "lca_research_kernel.tiangong_native_case_binding/v1"
_SELECTION_CONTRACT = "lca_research_kernel.tiangong_open_data_case_selection/v1"
_CASE_OBSERVATION_CONTRACT = (
    "lca_research_kernel.tiangong_native_case_observation/v1"
)
_RESULT_OBSERVATION_CONTRACT = (
    "lca_research_kernel.tiangong_native_result_observation/v1"
)
_ALLOWED_ORIGINS = {
    "tiangong_official_calculation_bundle",
    "tiangong_official_api_result",
    "tiangong_official_tidas_zip_result",
}
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return copy.deepcopy(value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _identity(value: object, label: str) -> JsonObject:
    row = _object(value, label)
    return {
        "id": _text(row.get("id"), f"{label}.id"),
        "version": _text(row.get("version"), f"{label}.version"),
    }


def _binding(value: object, label: str) -> JsonObject:
    row = _object(value, label)
    role = _text(row.get("role"), f"{label}.role")
    path = _text(row.get("path"), f"{label}.path").replace("\\", "/")
    pure = PurePosixPath(path)
    sha256 = _text(row.get("sha256"), f"{label}.sha256")
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"{label}.path must be workspace-relative")
    if not _HEX_SHA256.fullmatch(sha256):
        raise ValueError(f"{label}.sha256 must be a lowercase SHA-256")
    return {"role": role, "path": str(pure), "sha256": sha256}


def _bindings(value: object, label: str) -> List[JsonObject]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty list")
    rows = sorted(
        [_binding(row, f"{label}[{index}]") for index, row in enumerate(value)],
        key=lambda row: (str(row["role"]), str(row["path"]), str(row["sha256"])),
    )
    identities = [(row["role"], row["path"]) for row in rows]
    if len(identities) != len(set(identities)):
        raise ValueError(f"{label} contains duplicate bindings")
    return rows


def _text_list(value: object, label: str, *, allow_empty: bool = True) -> List[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ValueError(f"{label} must be a list")
    rows = [_text(item, f"{label}[{index}]") for index, item in enumerate(value)]
    return sorted(set(rows))


def _same_identity(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    return left.get("id") == right.get("id") and left.get("version") == right.get(
        "version"
    )


def _source_pointer(value: object, label: str) -> str:
    token = _text(value, label).replace("\\", "/")
    path_token = token.split("#", 1)[0]
    path = PurePosixPath(path_token)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{label} must be workspace-relative")
    return token


def _normalize_selection(value: object) -> JsonObject:
    selection = _object(value, "selection")
    if selection.get("contract") != _SELECTION_CONTRACT or selection.get("status") != "selected":
        raise ValueError("selection must contain one accepted candidate")
    candidate = _object(selection.get("selected_candidate"), "selected_candidate")
    native = _object(candidate.get("native_result"), "selected native_result")
    if native.get("available") is not True:
        raise ValueError("selected native result is unavailable")
    return {
        "selection_id": _text(selection.get("selection_id"), "selection.selection_id"),
        "candidate_id": _text(candidate.get("candidate_id"), "candidate_id"),
        "dataset": _identity(candidate.get("dataset"), "selected dataset"),
        "model": _identity(candidate.get("model"), "selected model"),
        "functional_unit": {
            "amount": _text(
                _object(candidate.get("functional_unit"), "selected functional_unit").get(
                    "amount"
                ),
                "selected functional_unit.amount",
            ),
            "unit": _text(
                _object(candidate.get("functional_unit"), "selected functional_unit").get(
                    "unit"
                ),
                "selected functional_unit.unit",
            ),
        },
        "reference_flow": _identity(candidate.get("reference_flow"), "selected reference_flow"),
        "provider_snapshot": _identity(
            candidate.get("provider_snapshot"), "selected provider_snapshot"
        ),
        "lcia_method": _identity(candidate.get("lcia_method"), "selected lcia_method"),
        "native_result": {
            "id": _text(native.get("id"), "selected native_result.id"),
            "version": _text(native.get("version"), "selected native_result.version"),
            "mode": _text(native.get("mode"), "selected native_result.mode"),
        },
    }


def _normalize_case(value: object, expected: Mapping[str, object], authority: Mapping[str, object]) -> JsonObject:
    case = _object(value, "case_observation")
    if case.get("contract") != _CASE_OBSERVATION_CONTRACT:
        raise ValueError("case observation contract is unsupported")
    if _text(case.get("candidate_id"), "case candidate_id") != expected["candidate_id"]:
        raise ValueError("case candidate does not join selection")
    dataset = _identity(case.get("dataset"), "case dataset")
    model = _identity(case.get("model"), "case model")
    if not _same_identity(dataset, expected["dataset"]) or not _same_identity(
        dataset, authority["dataset"]
    ):
        raise ValueError("case dataset does not join selection and receipt")
    if not _same_identity(model, expected["model"]) or not _same_identity(
        model, authority["model"]
    ):
        raise ValueError("case model does not join selection and receipt")

    scope = _object(case.get("scope"), "case scope")
    functional = _object(scope.get("functional_unit"), "case functional_unit")
    normalized_functional = {
        "amount": _text(functional.get("amount"), "case functional_unit.amount"),
        "unit": _text(functional.get("unit"), "case functional_unit.unit"),
    }
    reference = _identity(scope.get("reference_flow"), "case reference_flow")
    if normalized_functional != expected["functional_unit"] or (
        normalized_functional["amount"] != authority["functional_unit"]["amount"]
        or normalized_functional["unit"] != authority["functional_unit"]["unit"]
    ):
        raise ValueError("case functional unit does not join selection and receipt")
    if not _same_identity(reference, expected["reference_flow"]) or not _same_identity(
        reference, authority["reference_flow"]
    ):
        raise ValueError("case reference Flow does not join selection and receipt")

    closure = _object(case.get("closure"), "case closure")
    if any(closure.get(key) is not True for key in ("inspectable", "executable", "complete")):
        raise ValueError("case closure must be inspectable, executable, and complete")
    nodes = _text_list(closure.get("nodes"), "case closure.nodes", allow_empty=False)
    edge_values = closure.get("edges")
    if not isinstance(edge_values, list) or not edge_values:
        raise ValueError("case closure.edges must be non-empty")
    edges = []
    for index, value_row in enumerate(edge_values):
        edge = _object(value_row, f"case closure.edges[{index}]")
        if edge.get("connected") is not True:
            raise ValueError("case closure contains a disconnected Provider edge")
        normalized_edge = {
            "consumer": _text(edge.get("consumer"), "edge.consumer"),
            "provider": _text(edge.get("provider"), "edge.provider"),
            "flow": _text(edge.get("flow"), "edge.flow"),
            "connected": True,
        }
        if normalized_edge["consumer"] not in nodes or normalized_edge["provider"] not in nodes:
            raise ValueError("case closure edge endpoint is absent from nodes")
        edges.append(normalized_edge)
    edges.sort(key=lambda row: (row["consumer"], row["provider"], row["flow"]))

    provider_raw = _object(case.get("provider_snapshot"), "case provider_snapshot")
    provider = _identity(provider_raw, "case provider_snapshot")
    if (
        not _same_identity(provider, expected["provider_snapshot"])
        or not _same_identity(provider, authority["provider_snapshot"])
        or provider_raw.get("inspectable") is not True
    ):
        raise ValueError("case Provider snapshot does not join selection and receipt")
    provider_refs = _text_list(
        provider_raw.get("provider_exact_refs"),
        "case provider_snapshot.provider_exact_refs",
        allow_empty=False,
    )
    if not set(provider_refs).issubset(set(nodes)):
        raise ValueError("Provider snapshot identities are absent from closure nodes")
    if any(edge["provider"] not in provider_refs for edge in edges):
        raise ValueError("closure edge Provider is absent from Provider snapshot")

    schema = _object(case.get("schema"), "case schema")
    schema_identity = _identity(schema, "case schema")
    if not _same_identity(schema_identity, authority["tidas_schema"]) or schema.get(
        "validation_status"
    ) != "valid":
        raise ValueError("case TIDAS Schema binding is invalid")
    method = _identity(case.get("lcia_method"), "case lcia_method")
    if not _same_identity(method, expected["lcia_method"]) or not _same_identity(
        method, authority["lcia_method"]
    ):
        raise ValueError("case LCIA method does not join selection and receipt")
    validation = _object(case.get("validation"), "case validation")
    if validation.get("status") != "passed":
        raise ValueError("case validation has not passed")
    if case.get("remote_write_executed_count") != 0:
        raise ValueError("case observation contains a prohibited remote write")
    remote_reads = case.get("remote_read_executed_count")
    if isinstance(remote_reads, bool) or not isinstance(remote_reads, int) or remote_reads < 0:
        raise ValueError("case remote read count is invalid")
    return {
        "observation_id": _text(case.get("observation_id"), "case observation_id"),
        "dataset": dataset,
        "model": model,
        "scope": {
            "functional_unit": normalized_functional,
            "reference_flow": reference,
            "system_boundary": _text(scope.get("system_boundary"), "system_boundary"),
        },
        "closure": {
            "inspectable": True,
            "executable": True,
            "complete": True,
            "nodes": nodes,
            "edges": edges,
        },
        "provider_snapshot": {
            **provider,
            "inspectable": True,
            "provider_rule": _text(provider_raw.get("provider_rule"), "provider_rule"),
            "provider_exact_refs": provider_refs,
            "weight_vector_exposed": provider_raw.get("weight_vector_exposed") is True,
        },
        "schema": {**schema_identity, "validation_status": "valid"},
        "lcia_method": method,
        "source_bindings": _bindings(case.get("source_bindings"), "case source_bindings"),
        "missing_data": _text_list(case.get("missing_data"), "case missing_data"),
        "assumptions": _text_list(case.get("assumptions"), "case assumptions"),
        "validation": {"status": "passed"},
        "remote_read_executed_count": remote_reads,
    }


def _normalize_result(value: object, expected: Mapping[str, object], authority: Mapping[str, object]) -> JsonObject:
    observation = _object(value, "native_result_observation")
    if observation.get("contract") != _RESULT_OBSERVATION_CONTRACT:
        raise ValueError("native result observation contract is unsupported")
    origin = _text(observation.get("origin"), "native result origin")
    if origin not in _ALLOWED_ORIGINS:
        raise ValueError("native result origin is not TianGong official")
    if _text(observation.get("candidate_id"), "result candidate_id") != expected[
        "candidate_id"
    ]:
        raise ValueError("native result candidate does not join selection")
    for label in ("dataset", "model", "provider_snapshot"):
        identity = _identity(observation.get(label), f"result {label}")
        if not _same_identity(identity, expected[label]) or not _same_identity(
            identity, authority[label]
        ):
            raise ValueError(f"native result {label} does not join authority")
    primary_method = _identity(observation.get("primary_method"), "primary_method")
    if not _same_identity(primary_method, expected["lcia_method"]) or not _same_identity(
        primary_method, authority["lcia_method"]
    ):
        raise ValueError("native result primary method does not join authority")

    result_raw = _object(observation.get("result"), "native result identity")
    result_identity = _identity(result_raw, "native result identity")
    if not _same_identity(result_identity, expected["native_result"]):
        raise ValueError("native result identity does not join selection")
    artifact_sha = _text(result_raw.get("artifact_sha256"), "result artifact_sha256")
    artifact_bytes = result_raw.get("artifact_bytes")
    if not _HEX_SHA256.fullmatch(artifact_sha):
        raise ValueError("native result artifact SHA-256 is invalid")
    if isinstance(artifact_bytes, bool) or not isinstance(artifact_bytes, int) or artifact_bytes < 1:
        raise ValueError("native result artifact byte size is invalid")
    if _text(result_raw.get("snapshot_id"), "result snapshot_id") != authority[
        "provider_snapshot"
    ]["id"]:
        raise ValueError("native result snapshot does not join Provider snapshot")

    category_values = observation.get("categories")
    if not isinstance(category_values, list) or not category_values:
        raise ValueError("native result categories are required")
    categories = []
    identities = set()
    for index, value_row in enumerate(category_values):
        row = _object(value_row, f"categories[{index}]")
        method_id = _text(row.get("method_id"), "category method_id")
        method_version = _text(row.get("method_version"), "category method_version")
        exact = f"{method_id}@{method_version}"
        if exact in identities:
            raise ValueError("native result categories contain duplicate method identities")
        identities.add(exact)
        number = row.get("original_value")
        if (
            isinstance(number, bool)
            or not isinstance(number, (int, float))
            or not math.isfinite(float(number))
        ):
            raise ValueError("native result original_value must remain finite numeric evidence")
        unit = _text(row.get("original_unit"), "category original_unit")
        if unit.casefold() in {"unknown", "guessed", "unbound"}:
            raise ValueError("native result unit must be exactly source-bound")
        if row.get("validation_status") != "exactly_bound":
            raise ValueError("native result category is guessed or unbound")
        transformations = _text_list(row.get("transformations"), "category transformations")
        assumptions = _text_list(row.get("assumptions"), "category assumptions")
        missing = _text_list(row.get("missing_data"), "category missing_data")
        categories.append(
            {
                "method_id": method_id,
                "method_version": method_version,
                "name": _text(row.get("name"), "category name"),
                "original_value": number,
                "original_unit": unit,
                "source_pointer": _source_pointer(
                    row.get("source_pointer"), "category source_pointer"
                ),
                "transformations": transformations,
                "assumptions": assumptions,
                "missing_data": missing,
                "validation_status": "exactly_bound",
            }
        )
    primary_exact = f"{primary_method['id']}@{primary_method['version']}"
    if primary_exact not in identities:
        raise ValueError("native result categories omit the receipt LCIA method")
    categories.sort(key=lambda row: (row["method_id"], row["method_version"]))
    if observation.get("remote_write_executed_count") != 0:
        raise ValueError("native result observation contains a prohibited remote write")
    remote_reads = observation.get("remote_read_executed_count")
    if isinstance(remote_reads, bool) or not isinstance(remote_reads, int) or remote_reads < 0:
        raise ValueError("native result remote read count is invalid")
    return {
        "observation_id": _text(observation.get("observation_id"), "result observation_id"),
        "origin": origin,
        "dataset": _identity(observation.get("dataset"), "result dataset"),
        "model": _identity(observation.get("model"), "result model"),
        "provider_snapshot": _identity(
            observation.get("provider_snapshot"), "result provider_snapshot"
        ),
        "primary_method": primary_method,
        "result": {
            **result_identity,
            "package_id": _text(result_raw.get("package_id"), "result package_id"),
            "snapshot_id": _text(result_raw.get("snapshot_id"), "result snapshot_id"),
            "artifact_format": _text(
                result_raw.get("artifact_format"), "result artifact_format"
            ),
            "artifact_sha256": artifact_sha,
            "artifact_bytes": artifact_bytes,
        },
        "categories": categories,
        "source_bindings": _bindings(
            observation.get("source_bindings"), "native result source_bindings"
        ),
        "remote_read_executed_count": remote_reads,
    }


def native_case_binding_content_sha256(binding: Mapping[str, object]) -> str:
    body = _object(binding, "binding")
    body.pop("binding_id", None)
    body.pop("binding_content_sha256", None)
    return _digest(body)


def compile_native_case_binding(
    *,
    selection: Mapping[str, object],
    authority_receipt: Mapping[str, object],
    case_observation: Mapping[str, object],
    native_result_observation: Mapping[str, object],
) -> JsonObject:
    """Normalize and bind official evidence without performing any LCIA calculation."""

    selected = _normalize_selection(selection)
    receipt = verify_authority_receipt(authority_receipt)
    authority = _object(receipt.get("authority"), "receipt authority")
    for label in ("dataset", "model", "reference_flow", "provider_snapshot"):
        if not _same_identity(selected[label], authority[label]):
            raise ValueError(f"selection {label} does not join Authority Receipt")
    if not _same_identity(selected["lcia_method"], authority["lcia_method"]):
        raise ValueError("selection LCIA method does not join Authority Receipt")
    if (
        selected["functional_unit"]["amount"] != authority["functional_unit"]["amount"]
        or selected["functional_unit"]["unit"] != authority["functional_unit"]["unit"]
    ):
        raise ValueError("selection functional unit does not join Authority Receipt")
    case = _normalize_case(case_observation, selected, authority)
    result = _normalize_result(native_result_observation, selected, authority)
    body: JsonObject = {
        "contract": NATIVE_CASE_BINDING_CONTRACT,
        "status": "accepted_tiangong_native_case_binding",
        "selection_id": selected["selection_id"],
        "candidate_id": selected["candidate_id"],
        "authority_receipt_id": receipt["receipt_id"],
        "authority_receipt_sha256": receipt["receipt_content_sha256"],
        "case_observation_id": case["observation_id"],
        "result_observation_id": result["observation_id"],
        "scope": case["scope"],
        "model": {
            "identity": case["model"],
            "dataset": case["dataset"],
            "nodes": case["closure"]["nodes"],
            "edges": case["closure"]["edges"],
            "closure": {
                "inspectable": True,
                "executable": True,
                "complete": True,
            },
        },
        "provider_snapshot": case["provider_snapshot"],
        "tidas_schema": case["schema"],
        "lcia_method": case["lcia_method"],
        "native_result": {
            "origin": result["origin"],
            "identity": result["result"],
            "primary_method": result["primary_method"],
            "categories": result["categories"],
        },
        "source_bindings": {
            "case": case["source_bindings"],
            "native_result": result["source_bindings"],
        },
        "missing_data": case["missing_data"],
        "assumptions": case["assumptions"],
        "validation": {
            "status": "passed",
            "native_lcia_calculated_by_kernel": False,
            "external_authority_substituted": False,
        },
        "remote_read_executed_count": case["remote_read_executed_count"]
        + result["remote_read_executed_count"],
        "remote_write_executed_count": 0,
        "next_route": "integrate-tiangong-authority-workflow",
    }
    digest = _digest(body)
    body["binding_id"] = digest[:16]
    body["binding_content_sha256"] = digest
    return body


def verify_native_case_binding(
    binding: Mapping[str, object], authority_receipt: Mapping[str, object]
) -> JsonObject:
    """Verify an immutable native-case binding against its Authority Receipt."""

    row = _object(binding, "binding")
    if row.get("contract") != NATIVE_CASE_BINDING_CONTRACT:
        raise ValueError("native case binding contract is unsupported")
    if row.get("status") != "accepted_tiangong_native_case_binding":
        raise ValueError("native case binding status is not accepted")
    receipt = verify_authority_receipt(authority_receipt)
    if (
        row.get("authority_receipt_id") != receipt.get("receipt_id")
        or row.get("authority_receipt_sha256")
        != receipt.get("receipt_content_sha256")
    ):
        raise ValueError("native case binding does not join Authority Receipt")
    recorded_digest = _text(
        row.get("binding_content_sha256"), "binding_content_sha256"
    )
    expected_digest = native_case_binding_content_sha256(row)
    if recorded_digest != expected_digest or row.get("binding_id") != expected_digest[:16]:
        raise ValueError("native case binding digest does not match content")
    if row.get("remote_write_executed_count") != 0:
        raise ValueError("native case binding records a prohibited remote write")
    model = _object(row.get("model"), "binding model")
    closure = _object(model.get("closure"), "binding closure")
    if any(closure.get(key) is not True for key in ("inspectable", "executable", "complete")):
        raise ValueError("native case binding closure is incomplete")
    result = _object(row.get("native_result"), "binding native_result")
    if result.get("origin") not in _ALLOWED_ORIGINS:
        raise ValueError("native case binding result origin is unsupported")
    categories = result.get("categories")
    if not isinstance(categories, list) or not categories:
        raise ValueError("native case binding result categories are missing")
    return copy.deepcopy(row)
