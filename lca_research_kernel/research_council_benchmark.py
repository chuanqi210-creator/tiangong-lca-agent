"""Pure, deterministic qualification and freeze compilers for Council benchmarks."""

import hashlib
import json
import math
import re
from pathlib import PurePosixPath
from typing import Dict, List, Mapping, Sequence, Tuple


_POLICY_CONTRACT = "lca_research_kernel.research_council_benchmark_policy/v1"
_OBSERVATION_CONTRACT = "lca_research_kernel.research_council_benchmark_observation/v2"
_QUALIFICATION_CONTRACT = (
    "lca_research_kernel.research_council_benchmark_qualification/v2"
)
_FREEZE_CONTRACT = "lca_research_kernel.research_council_benchmark_freeze/v1"
_POLICY_KEYS = {
    "contract",
    "cluster_order",
    "hard_gates",
    "admission_criteria_lowering_allowed",
    "external_authority_substitution_allowed",
}
_REQUIRED_GATES = (
    "scientific_dispute_can_change_conclusion",
    "exact_tiangong_authority",
    "functional_unit_comparable_or_resolvable",
    "native_result_comparable_or_resolvable",
    "blind_falsifiable_evaluation",
    "read_only_zero_paid_route",
)
_FROZEN_CLUSTER_ORDER = (
    "copper_routes",
    "wastewater_routes",
    "truck_fuel_routes",
)
_OFFICIAL_RESULT_ORIGINS = {
    "official_calculation_bundle",
    "official_api_result",
    "official_tidas_zip_result",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)+$")
_LCIA_RESULT_VERSION_RE = re.compile(
    r"^lcia-result-[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_SECRET_KEY_MARKERS = {
    "accesstoken",
    "apikey",
    "apitoken",
    "authorization",
    "clientsecret",
    "credential",
    "credentials",
    "password",
    "passwd",
    "privatekey",
    "refreshtoken",
    "secret",
    "token",
}
_SECRET_VALUE_PATTERNS = (
    re.compile(r"authorization\s*:", re.IGNORECASE),
    re.compile(r"bearer(?:\s+token|\s+[a-z0-9._~+/=-]+)", re.IGNORECASE),
    re.compile(r"(?:api[_ -]?key|password|secret|token)\s*[=:]", re.IGNORECASE),
)
_ALLOWED_FREEZE_EFFECTS = {"read_only", "isolated_local_write"}
_ALLOWED_FREEZE_TOOLS = {
    "official_result_api_read",
    "tiangong_cli_read@0.0.29",
}
_ALLOWED_AGENT_CAPABILITIES = {
    "exact_tiangong_read",
    "isolated_local_artifact_write",
}
_REQUIRED_USER_RESPONSIBILITIES = {
    "credentials",
    "payment",
    "publication",
    "external_write",
    "irreversible_change",
    "unsupported_strong_public_claim",
}
_AUTHORITY_CONTRACT = "lca_research_kernel.tiangong_authority_evidence/v1"
_CLUSTER_SPEC_KEYS = {
    "cluster_id",
    "product_system_labels",
    "member_process_refs",
    "scientific_disputes",
    "functional_unit_comparison",
    "native_result_comparison",
    "blind_protocol",
    "route_effect_evidence",
    "boundary_traps",
    "evidence_ref",
}


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _reject_secret_keys(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("mapping keys must be strings")
            comparable = re.sub(r"[^a-z0-9]", "", key.casefold())
            if any(marker in comparable for marker in _SECRET_KEY_MARKERS):
                raise ValueError("secret-like key is not allowed")
            _reject_secret_keys(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_secret_keys(child)


def _reject_secret_values(value: object) -> None:
    if isinstance(value, str):
        if any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS):
            raise ValueError("secret-like value is not allowed")
    elif isinstance(value, Mapping):
        for child in value.values():
            _reject_secret_values(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_secret_values(child)


def _uuid(value: object, field: str) -> str:
    normalized = _text(value, field, casefold=True)
    if not _UUID_RE.fullmatch(normalized):
        raise ValueError(f"{field} must be an exact UUID")
    return normalized


def _version(value: object, field: str) -> str:
    normalized = _text(value, field)
    if not _VERSION_RE.fullmatch(normalized):
        raise ValueError(f"{field} must be an exact numeric dotted version")
    return normalized


def _result_version(value: object, field: str) -> str:
    normalized = _text(value, field, casefold=True)
    if not (
        _VERSION_RE.fullmatch(normalized)
        or _LCIA_RESULT_VERSION_RE.fullmatch(normalized)
    ):
        raise ValueError(
            f"{field} must be an exact numeric or official LCIA result version"
        )
    return normalized


def _text(value: object, field: str, *, casefold: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    normalized = " ".join(value.split())
    return normalized.casefold() if casefold else normalized


def _string_list(value: object, field: str, *, casefold: bool = False) -> List[str]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list")
    normalized = [
        _text(item, f"{field}[{index}]", casefold=casefold)
        for index, item in enumerate(value)
    ]
    normalized.sort(key=lambda item: (item.casefold(), item))
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"duplicate {field} entries are not allowed")
    return normalized


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field} keys must be strings")
    return value


def _json_value(value: object, field: str) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value.strip() if isinstance(value, str) else value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field} must not contain non-finite numbers")
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError(f"{field} keys must be strings")
        return {
            key: _json_value(value[key], f"{field}.{key}") for key in sorted(value)
        }
    if isinstance(value, (list, tuple)):
        return [
            _json_value(item, f"{field}[{index}]")
            for index, item in enumerate(value)
        ]
    raise ValueError(f"{field} must contain only JSON-compatible values")


def _versioned_ref(
    value: object, field: str, *, id_field: str, version_field: str
) -> Dict[str, str]:
    record = _mapping(value, field)
    if set(record) != {id_field, version_field}:
        raise ValueError(f"{field} must contain exactly {id_field} and {version_field}")
    return {
        id_field: _uuid(record[id_field], f"{field}.{id_field}"),
        version_field: (
            _result_version(record[version_field], f"{field}.{version_field}")
            if version_field == "result_version"
            else _version(record[version_field], f"{field}.{version_field}")
        ),
    }


def _process_key(value: Mapping[str, object], field: str) -> Tuple[str, str]:
    return (
        _uuid(value.get("process_id"), f"{field}.process_id"),
        _version(value.get("process_version"), f"{field}.process_version"),
    )


def _functional_unit(value: object, field: str) -> Dict[str, object]:
    unit = _mapping(value, field)
    if set(unit) != {"amount", "unit"}:
        raise ValueError(f"{field} must contain exactly amount and unit")
    amount = unit["amount"]
    if (
        isinstance(amount, bool)
        or not isinstance(amount, (int, float))
        or not math.isfinite(float(amount))
        or float(amount) <= 0
    ):
        raise ValueError(f"{field}.amount must be a positive finite number")
    return {
        "amount": amount,
        "unit": _text(unit["unit"], f"{field}.unit"),
    }


def _native_result(value: object, field: str) -> Dict[str, str]:
    result = _mapping(value, field)
    required = {
        "result_id",
        "result_version",
        "method_id",
        "method_version",
        "category_id",
        "category_version",
        "result_unit",
        "origin",
    }
    if not required.issubset(result):
        raise ValueError(f"{field} must bind result_id, result_version, and origin")
    origin = _text(result["origin"], f"{field}.origin", casefold=True)
    if origin not in _OFFICIAL_RESULT_ORIGINS:
        raise ValueError(f"{field}.origin must be an official TianGong result origin")
    return {
        "result_id": _uuid(result["result_id"], f"{field}.result_id"),
        "result_version": _result_version(
            result["result_version"], f"{field}.result_version"
        ),
        "method_id": _uuid(result["method_id"], f"{field}.method_id"),
        "method_version": _version(result["method_version"], f"{field}.method_version"),
        "category_id": _uuid(result["category_id"], f"{field}.category_id"),
        "category_version": _version(
            result["category_version"], f"{field}.category_version"
        ),
        "result_unit": _text(result["result_unit"], f"{field}.result_unit"),
        "origin": origin,
    }


def _candidate(value: object, field: str) -> Dict[str, object]:
    candidate = _mapping(value, field)
    process_id, process_version = _process_key(candidate, field)
    raw_screening_status = candidate.get("screening_status")
    normalized_screening_status = (
        _text(raw_screening_status, f"{field}.screening_status", casefold=True)
        if raw_screening_status is not None
        else None
    )
    raw_native_result = candidate.get("native_result")
    if raw_native_result is None and normalized_screening_status == "rejected":
        native_result = None
    else:
        native_result = _native_result(raw_native_result, f"{field}.native_result")
    reference_flow_id = _uuid(
        candidate.get("reference_flow_id"),
        f"{field}.reference_flow_id",
    )
    reference_flow_version = _version(
        candidate.get("reference_flow_version"), f"{field}.reference_flow_version"
    )
    normalized = {
        "process_id": process_id,
        "process_version": process_version,
        "product_system_label": _text(
            candidate.get("product_system_label"),
            f"{field}.product_system_label",
            casefold=True,
        ),
        "reference_flow_id": reference_flow_id,
        "reference_flow_version": reference_flow_version,
        "functional_unit": _functional_unit(
            candidate.get("functional_unit"), f"{field}.functional_unit"
        ),
        "native_result": native_result,
        "boundary": _text(
            candidate.get("boundary"), f"{field}.boundary", casefold=True
        ),
    }
    cluster_id = candidate.get("cluster_id")
    if cluster_id is not None:
        normalized["cluster_id"] = _text(
            cluster_id, f"{field}.cluster_id", casefold=True
        )
    if normalized_screening_status is not None:
        status = normalized_screening_status
        if status not in {"comparison_pair", "rejected"}:
            raise ValueError(
                f"{field}.screening_status must be comparison_pair or rejected"
            )
        normalized["screening_status"] = status
    screening_reasons = candidate.get("screening_reasons")
    if screening_reasons is not None:
        normalized["screening_reasons"] = _string_list(
            screening_reasons, f"{field}.screening_reasons", casefold=True
        )
    current_read_status = candidate.get("current_read_status")
    if current_read_status is not None:
        read_status = _text(
            current_read_status, f"{field}.current_read_status", casefold=True
        )
        if read_status not in {"needed", "exhausted"}:
            raise ValueError(f"{field}.current_read_status must be needed or exhausted")
        normalized["current_read_status"] = read_status
    terminal_blocker = candidate.get("terminal_blocker")
    if terminal_blocker is not None:
        normalized["terminal_blocker"] = _text(
            terminal_blocker, f"{field}.terminal_blocker", casefold=True
        )
    return normalized


def _artifact(value: object, field: str) -> Dict[str, object]:
    artifact = _mapping(value, field)
    required = {"role", "path", "sha256", "bytes"}
    if set(artifact) != required:
        raise ValueError(f"{field} must contain exactly role, path, sha256, and bytes")
    path = _text(artifact["path"], f"{field}.path")
    pure_path = PurePosixPath(path)
    if (
        "\\" in path
        or "\x00" in path
        or path.startswith("/")
        or re.match(r"^[a-zA-Z]:", path)
        or path == "."
        or not pure_path.parts
        or any(part in ("", ".") for part in pure_path.parts)
        or any(part == ".." for part in pure_path.parts)
    ):
        raise ValueError(f"{field}.path must be a safe workspace-relative path")
    digest = artifact["sha256"]
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ValueError(f"{field}.sha256 must be a lowercase hexadecimal SHA-256")
    size = artifact["bytes"]
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValueError(f"{field}.bytes must be a positive integer")
    return {
        "role": _text(artifact["role"], f"{field}.role", casefold=True),
        "path": str(pure_path),
        "sha256": digest,
        "bytes": size,
    }


def _catalog_ref(value: object) -> Dict[str, object]:
    catalog = _mapping(value, "candidate_catalog_ref")
    if set(catalog) != {"role", "path", "sha256", "bytes", "candidate_count"}:
        raise ValueError(
            "candidate_catalog_ref must bind role, path, sha256, bytes, and candidate_count"
        )
    artifact = _artifact(
        {key: catalog[key] for key in ("role", "path", "sha256", "bytes")},
        "candidate_catalog_ref",
    )
    if artifact["role"] != "candidate_catalog":
        raise ValueError("candidate_catalog_ref.role must be candidate_catalog")
    count = catalog["candidate_count"]
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ValueError("candidate_catalog_ref.candidate_count must be a positive integer")
    artifact["candidate_count"] = count
    return artifact


def _authority_binding(value: object, field: str) -> Dict[str, str]:
    binding = _mapping(value, field)
    required = {
        "process_id",
        "process_version",
        "reference_flow_id",
        "reference_flow_version",
        "result_id",
        "result_version",
        "method_id",
        "method_version",
        "category_id",
        "category_version",
        "result_unit",
        "origin",
    }
    if set(binding) != required:
        raise ValueError(f"{field} must contain every exact TianGong identity/version")
    return {
        "process_id": _uuid(binding["process_id"], f"{field}.process_id"),
        "process_version": _version(
            binding["process_version"], f"{field}.process_version"
        ),
        "reference_flow_id": _uuid(
            binding["reference_flow_id"], f"{field}.reference_flow_id"
        ),
        "reference_flow_version": _version(
            binding["reference_flow_version"], f"{field}.reference_flow_version"
        ),
        "result_id": _uuid(binding["result_id"], f"{field}.result_id"),
        "result_version": _result_version(
            binding["result_version"], f"{field}.result_version"
        ),
        "method_id": _uuid(binding["method_id"], f"{field}.method_id"),
        "method_version": _version(
            binding["method_version"], f"{field}.method_version"
        ),
        "category_id": _uuid(binding["category_id"], f"{field}.category_id"),
        "category_version": _version(
            binding["category_version"], f"{field}.category_version"
        ),
        "result_unit": _text(binding["result_unit"], f"{field}.result_unit"),
        "origin": _text(binding["origin"], f"{field}.origin", casefold=True),
    }


def _authority_evidence(value: object) -> Dict[str, object]:
    evidence = _mapping(value, "authority_evidence")
    if set(evidence) != {"contract", "artifact_ref", "bindings"}:
        raise ValueError("authority_evidence must have the exact evidence shape")
    if evidence.get("contract") != _AUTHORITY_CONTRACT:
        raise ValueError("unsupported TianGong authority evidence contract")
    raw_bindings = evidence.get("bindings")
    if not isinstance(raw_bindings, (list, tuple)) or not raw_bindings:
        raise ValueError("authority_evidence.bindings must be a non-empty list")
    bindings = [
        _authority_binding(value, f"authority_evidence.bindings[{index}]")
        for index, value in enumerate(raw_bindings)
    ]
    bindings.sort(key=_canonical_json)
    if len({_canonical_json(value) for value in bindings}) != len(bindings):
        raise ValueError("duplicate authority evidence bindings are not allowed")
    artifact_ref = _artifact(evidence.get("artifact_ref"), "authority_evidence.artifact_ref")
    if artifact_ref["role"] != "tiangong_authority_evidence":
        raise ValueError(
            "authority_evidence.artifact_ref.role must be tiangong_authority_evidence"
        )
    return {
        "contract": _AUTHORITY_CONTRACT,
        "artifact_ref": artifact_ref,
        "bindings": bindings,
    }


def _process_refs(value: object, field: str, *, minimum: int = 1) -> List[Dict[str, str]]:
    if not isinstance(value, (list, tuple)) or len(value) < minimum:
        raise ValueError(f"{field} must contain at least {minimum} exact Process refs")
    refs = [
        _versioned_ref(
            item,
            f"{field}[{index}]",
            id_field="process_id",
            version_field="process_version",
        )
        for index, item in enumerate(value)
    ]
    refs.sort(key=_canonical_json)
    if len({_canonical_json(item) for item in refs}) != len(refs):
        raise ValueError(f"duplicate {field} entries are not allowed")
    return refs


def _result_refs(value: object, field: str) -> List[Dict[str, str]]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list")
    refs = []
    required = {
        "process_id",
        "process_version",
        "result_id",
        "result_version",
        "method_id",
        "method_version",
        "category_id",
        "category_version",
        "result_unit",
        "origin",
    }
    for index, item in enumerate(value):
        record = _mapping(item, f"{field}[{index}]")
        if set(record) != required:
            raise ValueError(
                f"{field}[{index}] must bind one exact Process and native result identity"
            )
        refs.append(
            {
                "process_id": _uuid(record["process_id"], f"{field}[{index}].process_id"),
                "process_version": _version(
                    record["process_version"], f"{field}[{index}].process_version"
                ),
                **_native_result(record, f"{field}[{index}]"),
            }
        )
    refs.sort(key=_canonical_json)
    process_keys = {(row["process_id"], row["process_version"]) for row in refs}
    if len(process_keys) != len(refs):
        raise ValueError(f"duplicate {field} Process bindings are not allowed")
    return refs


def _functional_comparison(value: object, field: str) -> Dict[str, object]:
    comparison = _mapping(value, field)
    if set(comparison) != {"member_process_refs", "treatment", "boundaries_comparable"}:
        raise ValueError(f"{field} must have the exact functional-unit evidence shape")
    if not isinstance(comparison["boundaries_comparable"], bool):
        raise ValueError(f"{field}.boundaries_comparable must be boolean")
    return {
        "member_process_refs": _process_refs(
            comparison["member_process_refs"], f"{field}.member_process_refs"
        ),
        "treatment": _functional_unit(comparison["treatment"], f"{field}.treatment"),
        "boundaries_comparable": comparison["boundaries_comparable"],
    }


def _result_comparison(value: object, field: str) -> Dict[str, object]:
    comparison = _mapping(value, field)
    required = {
        "method_id",
        "method_version",
        "category_id",
        "category_version",
        "result_unit",
        "member_result_refs",
    }
    if set(comparison) != required:
        raise ValueError(f"{field} must have the exact native-result evidence shape")
    return {
        "method_id": _uuid(comparison["method_id"], f"{field}.method_id"),
        "method_version": _version(
            comparison["method_version"], f"{field}.method_version"
        ),
        "category_id": _uuid(comparison["category_id"], f"{field}.category_id"),
        "category_version": _version(
            comparison["category_version"], f"{field}.category_version"
        ),
        "result_unit": _text(comparison["result_unit"], f"{field}.result_unit"),
        "member_result_refs": _result_refs(
            comparison["member_result_refs"], f"{field}.member_result_refs"
        ),
    }


def _blind_protocol(value: object, field: str) -> Dict[str, bool]:
    protocol = _mapping(value, field)
    required = {"candidate_labels_hidden", "predeclared_decision_rule"}
    if set(protocol) != required or any(not isinstance(protocol[key], bool) for key in required):
        raise ValueError(f"{field} must have the exact blind-protocol evidence shape")
    return {key: protocol[key] for key in sorted(required)}


def _route_effect_evidence(value: object, field: str) -> Dict[str, object]:
    evidence = _mapping(value, field)
    if set(evidence) != {"route", "effects", "paid_dependency_count"}:
        raise ValueError(f"{field} must have the exact route/effect evidence shape")
    count = evidence["paid_dependency_count"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError(f"{field}.paid_dependency_count must be a non-negative integer")
    return {
        "route": _text(evidence["route"], f"{field}.route", casefold=True),
        "effects": _string_list(evidence["effects"], f"{field}.effects", casefold=True),
        "paid_dependency_count": count,
    }


def _cluster_spec(value: object, field: str) -> Dict[str, object]:
    spec = _mapping(value, field)
    if set(spec) != _CLUSTER_SPEC_KEYS:
        raise ValueError(f"{field} must have the exact content-bound cluster shape")
    body: Dict[str, object] = {
        "cluster_id": _text(spec["cluster_id"], f"{field}.cluster_id", casefold=True),
        "product_system_labels": _string_list(
            spec["product_system_labels"], f"{field}.product_system_labels", casefold=True
        ),
        "member_process_refs": _process_refs(
            spec["member_process_refs"], f"{field}.member_process_refs", minimum=2
        ),
        "scientific_disputes": _string_list(
            spec["scientific_disputes"], f"{field}.scientific_disputes"
        ),
        "functional_unit_comparison": _functional_comparison(
            spec["functional_unit_comparison"], f"{field}.functional_unit_comparison"
        ),
        "native_result_comparison": _result_comparison(
            spec["native_result_comparison"], f"{field}.native_result_comparison"
        ),
        "blind_protocol": _blind_protocol(
            spec["blind_protocol"], f"{field}.blind_protocol"
        ),
        "route_effect_evidence": _route_effect_evidence(
            spec["route_effect_evidence"], f"{field}.route_effect_evidence"
        ),
        "boundary_traps": _string_list(
            spec["boundary_traps"], f"{field}.boundary_traps"
        ),
    }
    evidence_ref = _artifact(spec["evidence_ref"], f"{field}.evidence_ref")
    if evidence_ref["role"] != "benchmark_cluster_evidence":
        raise ValueError(f"{field}.evidence_ref.role must be benchmark_cluster_evidence")
    body["evidence_ref"] = evidence_ref
    return body


def _candidate_authority_binding(candidate: Mapping[str, object]) -> Dict[str, str]:
    result = candidate["native_result"]
    return {
        "process_id": str(candidate["process_id"]),
        "process_version": str(candidate["process_version"]),
        "reference_flow_id": str(candidate["reference_flow_id"]),
        "reference_flow_version": str(candidate["reference_flow_version"]),
        "result_id": str(result["result_id"]),
        "result_version": str(result["result_version"]),
        "method_id": str(result["method_id"]),
        "method_version": str(result["method_version"]),
        "category_id": str(result["category_id"]),
        "category_version": str(result["category_version"]),
        "result_unit": str(result["result_unit"]),
        "origin": str(result["origin"]),
    }


def _derived_gate_values(
    exact_members: Sequence[Mapping[str, object]],
    spec: Mapping[str, object],
    authority_bindings: set,
) -> Dict[str, bool]:
    expected_process_refs = [
        {
            "process_id": str(member["process_id"]),
            "process_version": str(member["process_version"]),
        }
        for member in exact_members
    ]
    expected_result_refs = []
    for member in exact_members:
        expected_result_refs.append(
            {
                "process_id": str(member["process_id"]),
                "process_version": str(member["process_version"]),
                **{key: str(value) for key, value in member["native_result"].items()},
            }
        )
    expected_process_refs.sort(key=_canonical_json)
    expected_result_refs.sort(key=_canonical_json)
    functional = spec["functional_unit_comparison"]
    native = spec["native_result_comparison"]
    blind = spec["blind_protocol"]
    route = spec["route_effect_evidence"]
    return {
        "scientific_dispute_can_change_conclusion": bool(
            spec["scientific_disputes"]
        ),
        "exact_tiangong_authority": all(
            member.get("terminal_blocker") is None
            and _canonical_json(_candidate_authority_binding(member))
            in authority_bindings
            for member in exact_members
        ),
        "functional_unit_comparable_or_resolvable": (
            functional["member_process_refs"] == expected_process_refs
            and functional["boundaries_comparable"] is True
            and len({str(member["boundary"]) for member in exact_members}) == 1
            and all(
                member["functional_unit"] == functional["treatment"]
                for member in exact_members
            )
        ),
        "native_result_comparable_or_resolvable": (
            native["member_result_refs"] == expected_result_refs
            and all(
                member["native_result"]["method_id"] == native["method_id"]
                and member["native_result"]["method_version"] == native["method_version"]
                and member["native_result"]["category_id"] == native["category_id"]
                and member["native_result"]["category_version"]
                == native["category_version"]
                and member["native_result"]["result_unit"] == native["result_unit"]
                for member in exact_members
            )
        ),
        "blind_falsifiable_evaluation": (
            blind["candidate_labels_hidden"] is True
            and blind["predeclared_decision_rule"] is True
        ),
        "read_only_zero_paid_route": (
            route["route"] == "official_public_result_read"
            and route["effects"] == ["read_only"]
            and route["paid_dependency_count"] == 0
        ),
    }


def _finish(body: Dict[str, object], noun: str) -> Dict[str, object]:
    digest = hashlib.sha256(_canonical_json(body)).hexdigest()
    result = dict(body)
    result[f"{noun}_content_sha256"] = digest
    result[f"{noun}_id"] = digest[:16]
    return result


def _validate_policy(policy: object) -> Tuple[List[str], List[str]]:
    record = _mapping(policy, "policy")
    if set(record) != _POLICY_KEYS:
        raise ValueError("policy must have the exact benchmark policy top-level shape")
    if record.get("contract") != _POLICY_CONTRACT:
        raise ValueError("unsupported benchmark policy contract")
    _string_list(record.get("cluster_order"), "policy.cluster_order")
    declared_order = [
        _text(value, f"policy.cluster_order[{index}]", casefold=True)
        for index, value in enumerate(record["cluster_order"])
    ]
    if (
        tuple(declared_order) != _FROZEN_CLUSTER_ORDER
        or record.get("cluster_order") != list(_FROZEN_CLUSTER_ORDER)
    ):
        raise ValueError("policy.cluster_order must equal the frozen three-cluster order")
    raw_gates = record.get("hard_gates")
    if not isinstance(raw_gates, (list, tuple)):
        raise ValueError("policy.hard_gates must be a list")
    gates = [
        _text(value, f"policy.hard_gates[{index}]", casefold=True)
        for index, value in enumerate(raw_gates)
    ]
    if tuple(gates) != _REQUIRED_GATES:
        raise ValueError("policy.hard_gates must declare the frozen gates in order")
    if record.get("admission_criteria_lowering_allowed") is not False:
        raise ValueError("admission criteria lowering must be forbidden")
    if record.get("external_authority_substitution_allowed") is not False:
        raise ValueError("external authority substitution must be forbidden")
    return declared_order, gates


def compile_benchmark_qualification(
    *,
    candidate_observation: Mapping[str, object],
    policy: Mapping[str, object],
) -> Dict[str, object]:
    """Compile a canonical three-cluster, fail-closed qualification ledger."""
    _reject_secret_keys(candidate_observation)
    _reject_secret_keys(policy)
    _reject_secret_values(candidate_observation)
    _reject_secret_values(policy)
    cluster_order, hard_gates = _validate_policy(policy)
    observation = _mapping(candidate_observation, "candidate_observation")
    if observation.get("contract") != _OBSERVATION_CONTRACT:
        raise ValueError("unsupported benchmark observation contract")
    catalog_ref = _catalog_ref(observation.get("candidate_catalog_ref"))
    authority_evidence = _authority_evidence(observation.get("authority_evidence"))
    authority_bindings = {
        _canonical_json(value) for value in authority_evidence["bindings"]
    }

    raw_candidates = observation.get("candidates")
    if not isinstance(raw_candidates, (list, tuple)) or not raw_candidates:
        raise ValueError("candidate_observation.candidates must be a non-empty list")
    candidates = [
        _candidate(value, f"candidates[{index}]")
        for index, value in enumerate(raw_candidates)
    ]
    candidates_by_key: Dict[Tuple[str, str], Dict[str, object]] = {}
    for candidate in candidates:
        key = (str(candidate["process_id"]), str(candidate["process_version"]))
        if key in candidates_by_key:
            raise ValueError("duplicate exact Process identity/version is not allowed")
        candidates_by_key[key] = candidate

    raw_specs = observation.get("cluster_specs")
    if not isinstance(raw_specs, (list, tuple)) or not raw_specs:
        raise ValueError("candidate_observation.cluster_specs must be a non-empty list")
    order_index = {cluster_id: index for index, cluster_id in enumerate(cluster_order)}
    ledger: List[Dict[str, object]] = []
    seen_clusters = set()
    assigned_members = set()
    assigned_cluster_by_member: Dict[Tuple[str, str], str] = {}
    for spec_index, raw_spec in enumerate(raw_specs):
        spec = _cluster_spec(raw_spec, f"cluster_specs[{spec_index}]")
        cluster_id = str(spec["cluster_id"])
        if cluster_id in seen_clusters:
            raise ValueError("duplicate cluster_id is not allowed")
        if cluster_id not in order_index:
            raise ValueError("every observed cluster must appear in policy.cluster_order")
        seen_clusters.add(cluster_id)
        labels = spec["product_system_labels"]
        if not labels:
            raise ValueError("cluster product_system_labels must not be empty")
        member_refs = spec["member_process_refs"]
        member_keys = [
            (value["process_id"], value["process_version"]) for value in member_refs
        ]
        if len(member_keys) != len(set(member_keys)):
            raise ValueError("duplicate cluster member Process refs are not allowed")
        if any(key in assigned_members for key in member_keys):
            raise ValueError("an exact Process may belong to only one benchmark cluster")

        exact_members = []
        for member_key in member_keys:
            if member_key not in candidates_by_key:
                raise ValueError("cluster member exact Process ref was not observed")
            candidate = candidates_by_key[member_key]
            if candidate["product_system_label"] not in labels:
                raise ValueError(
                    "cluster membership requires an exact Process ref and normalized product-system label"
                )
            exact_members.append(candidate)
            assigned_members.add(member_key)
            assigned_cluster_by_member[member_key] = cluster_id

        disputes = spec["scientific_disputes"]
        traps = spec["boundary_traps"]
        flow_groups: Dict[Tuple[str, str], List[Dict[str, str]]] = {}
        for member in exact_members:
            flow_key = (
                str(member["reference_flow_id"]),
                str(member["reference_flow_version"]),
            )
            flow_groups.setdefault(flow_key, []).append(
                {
                    "process_id": str(member["process_id"]),
                    "process_version": str(member["process_version"]),
                }
            )
        reference_flow_groups = []
        for (flow_id, flow_version), refs in sorted(flow_groups.items()):
            refs.sort(key=lambda value: (value["process_id"], value["process_version"]))
            reference_flow_groups.append(
                {
                    "reference_flow_id": flow_id,
                    "reference_flow_version": flow_version,
                    "member_process_refs": refs,
                }
            )

        functional_comparison = spec["functional_unit_comparison"]
        native_comparison = spec["native_result_comparison"]
        blind_protocol = spec["blind_protocol"]
        route_evidence = spec["route_effect_evidence"]
        gate_values = _derived_gate_values(exact_members, spec, authority_bindings)
        gate_results = [
            {"gate": gate, "passed": gate_values[gate]} for gate in hard_gates
        ]
        failed_gate = next(
            (entry["gate"] for entry in gate_results if not entry["passed"]), None
        )
        ledger.append(
            {
                "cluster_id": cluster_id,
                "normalized_product_system_labels": labels,
                "scientific_disputes": disputes,
                "dispute_cardinality": len(disputes),
                "exact_members": exact_members,
                "reference_flow_groups": reference_flow_groups,
                "functional_unit_treatment": functional_comparison["treatment"],
                "functional_unit_comparison": functional_comparison,
                "native_result_comparison": native_comparison,
                "blind_protocol": blind_protocol,
                "route_effect_evidence": route_evidence,
                "evidence_ref": spec["evidence_ref"],
                "boundary_traps": traps,
                "gate_results": gate_results,
                "failed_gate": failed_gate,
                "qualified": failed_gate is None,
            }
        )

    if seen_clusters != set(cluster_order):
        raise ValueError("observation must provide exactly the policy-declared clusters")

    def sort_key(cluster: Mapping[str, object]) -> Tuple[object, ...]:
        member_refs = tuple(
            (member["process_id"], member["process_version"])
            for member in cluster["exact_members"]
        )
        return (
            order_index[str(cluster["cluster_id"])],
            -int(cluster["dispute_cardinality"]),
            member_refs,
            cluster["cluster_id"],
        )

    ledger.sort(key=sort_key)
    ledger_by_cluster = {str(row["cluster_id"]): row for row in ledger}
    candidate_ledger = []
    for key, candidate in candidates_by_key.items():
        assigned_cluster = assigned_cluster_by_member.get(key)
        declared_cluster = candidate.get("cluster_id")
        cluster_id = str(declared_cluster or assigned_cluster or "")
        if cluster_id not in ledger_by_cluster:
            raise ValueError(
                "every screened candidate must declare a policy cluster or be a pair member"
            )
        if assigned_cluster and declared_cluster and assigned_cluster != declared_cluster:
            raise ValueError("candidate cluster_id conflicts with its comparison pair")
        status = str(
            candidate.get(
                "screening_status",
                "comparison_pair" if assigned_cluster else "",
            )
        )
        reasons = list(candidate.get("screening_reasons", []))
        if assigned_cluster and status != "comparison_pair":
            raise ValueError("comparison pair members must have comparison_pair status")
        if not assigned_cluster and (status != "rejected" or not reasons):
            raise ValueError(
                "screened candidates outside the comparison pair require rejection reasons"
            )
        row = {
            **candidate,
            "cluster_id": cluster_id,
            "screening_status": status,
            "screening_reasons": reasons,
        }
        candidate_ledger.append(row)
    candidate_ledger.sort(
        key=lambda row: (
            order_index[str(row["cluster_id"])],
            str(row["process_id"]),
            str(row["process_version"]),
        )
    )
    for row in ledger:
        cluster_rows = [
            candidate
            for candidate in candidate_ledger
            if candidate["cluster_id"] == row["cluster_id"]
        ]
        row["screened_candidate_count"] = len(cluster_rows)
        row["candidate_rows"] = cluster_rows
    qualified = [cluster for cluster in ledger if cluster["qualified"]]
    qualified.sort(key=sort_key)
    body: Dict[str, object] = {
        "contract": _QUALIFICATION_CONTRACT,
        "candidate_catalog_ref": catalog_ref,
        "authority_evidence": authority_evidence,
        "policy_contract": _POLICY_CONTRACT,
        "cluster_order": cluster_order,
        "hard_gates": hard_gates,
        "cluster_ledger": ledger,
        "candidate_ledger": candidate_ledger,
    }
    body.update(
        {
            "qualified_cluster_count": len(qualified),
            "selected_cluster": qualified[0] if qualified else None,
            "admission_criteria_lowered": False,
            "external_authority_substituted": False,
            "next_route": (
                "freeze-qualified-research-council-benchmark"
                if qualified
                else (
                    "await-tiangong-provider-closure-publication"
                    if candidate_ledger
                    and all(
                        row.get("current_read_status") == "exhausted"
                        and row.get("terminal_blocker")
                        == "tiangong_provider_closure_unpublished"
                        for row in candidate_ledger
                    )
                    else "complete-next-bounded-tiangong-read"
                )
            ),
        }
    )
    return _finish(body, "qualification")


def _reference_flow_groups(
    exact_members: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    grouped: Dict[Tuple[str, str], List[Dict[str, str]]] = {}
    for member in exact_members:
        key = (
            str(member["reference_flow_id"]),
            str(member["reference_flow_version"]),
        )
        grouped.setdefault(key, []).append(
            {
                "process_id": str(member["process_id"]),
                "process_version": str(member["process_version"]),
            }
        )
    result = []
    for (flow_id, flow_version), refs in sorted(grouped.items()):
        refs.sort(key=_canonical_json)
        result.append(
            {
                "reference_flow_id": flow_id,
                "reference_flow_version": flow_version,
                "member_process_refs": refs,
            }
        )
    return result


def _validate_qualification(value: object) -> Dict[str, object]:
    record = _mapping(value, "qualification")
    required_keys = {
        "contract",
        "candidate_catalog_ref",
        "authority_evidence",
        "policy_contract",
        "cluster_order",
        "hard_gates",
        "cluster_ledger",
        "candidate_ledger",
        "qualified_cluster_count",
        "selected_cluster",
        "admission_criteria_lowered",
        "external_authority_substituted",
        "next_route",
        "qualification_content_sha256",
        "qualification_id",
    }
    if set(record) != required_keys:
        raise ValueError("qualification must have the exact authenticated top-level shape")
    if record.get("contract") != _QUALIFICATION_CONTRACT:
        raise ValueError("unsupported benchmark qualification contract")
    digest = record.get("qualification_content_sha256")
    identity = record.get("qualification_id")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ValueError("qualification_content_sha256 must be a SHA-256")
    body = {
        key: record[key]
        for key in record
        if key not in {"qualification_content_sha256", "qualification_id"}
    }
    expected_digest = hashlib.sha256(_canonical_json(body)).hexdigest()
    if digest != expected_digest or identity != expected_digest[:16]:
        raise ValueError("qualification content identity does not authenticate its body")

    catalog_ref = _catalog_ref(record["candidate_catalog_ref"])
    authority_evidence = _authority_evidence(record["authority_evidence"])
    if record.get("policy_contract") != _POLICY_CONTRACT:
        raise ValueError("qualification policy contract is invalid")
    raw_cluster_order = record.get("cluster_order")
    if not isinstance(raw_cluster_order, list):
        raise ValueError("qualification cluster_order must be a list")
    cluster_order = [
        _text(item, f"qualification.cluster_order[{index}]", casefold=True)
        for index, item in enumerate(raw_cluster_order)
    ]
    if (
        cluster_order != raw_cluster_order
        or tuple(cluster_order) != _FROZEN_CLUSTER_ORDER
    ):
        raise ValueError(
            "qualification cluster_order must equal the frozen three-cluster order"
        )
    hard_gates = record.get("hard_gates")
    if hard_gates != list(_REQUIRED_GATES):
        raise ValueError("qualification hard-gate order/content is invalid")
    ledger = record.get("cluster_ledger")
    if not isinstance(ledger, list) or len(ledger) != len(cluster_order):
        raise ValueError("qualification cluster ledger is inconsistent")
    order_index = {cluster_id: index for index, cluster_id in enumerate(cluster_order)}
    authority_bindings = {
        _canonical_json(binding) for binding in authority_evidence["bindings"]
    }
    ledger_keys = {
        "cluster_id",
        "normalized_product_system_labels",
        "scientific_disputes",
        "dispute_cardinality",
        "exact_members",
        "reference_flow_groups",
        "functional_unit_treatment",
        "functional_unit_comparison",
        "native_result_comparison",
        "blind_protocol",
        "route_effect_evidence",
        "evidence_ref",
        "boundary_traps",
        "gate_results",
        "failed_gate",
        "qualified",
        "screened_candidate_count",
        "candidate_rows",
    }
    normalized_ledger = []
    seen_clusters = set()
    for index, raw_cluster in enumerate(ledger):
        cluster = _mapping(raw_cluster, f"qualification.cluster_ledger[{index}]")
        if set(cluster) != ledger_keys:
            raise ValueError("qualification cluster ledger entry has an invalid shape")
        cluster_id = _text(
            cluster["cluster_id"],
            f"qualification.cluster_ledger[{index}].cluster_id",
            casefold=True,
        )
        if cluster_id in seen_clusters or cluster_id not in order_index:
            raise ValueError("qualification cluster IDs are inconsistent")
        seen_clusters.add(cluster_id)
        exact_members_raw = cluster["exact_members"]
        if not isinstance(exact_members_raw, list) or len(exact_members_raw) < 2:
            raise ValueError("qualification cluster must bind at least two exact members")
        exact_members = [
            _candidate(member, f"qualification.cluster_ledger[{index}].exact_members[{member_index}]")
            for member_index, member in enumerate(exact_members_raw)
        ]
        sorted_members = sorted(
            exact_members,
            key=lambda member: (member["process_id"], member["process_version"]),
        )
        member_keys = [
            (member["process_id"], member["process_version"])
            for member in sorted_members
        ]
        if exact_members != sorted_members or len(member_keys) != len(set(member_keys)):
            raise ValueError("qualification exact members must be canonical and unique")
        member_refs = [
            {"process_id": member["process_id"], "process_version": member["process_version"]}
            for member in exact_members
        ]
        spec = _cluster_spec(
            {
                "cluster_id": cluster_id,
                "product_system_labels": cluster["normalized_product_system_labels"],
                "member_process_refs": member_refs,
                "scientific_disputes": cluster["scientific_disputes"],
                "functional_unit_comparison": cluster["functional_unit_comparison"],
                "native_result_comparison": cluster["native_result_comparison"],
                "blind_protocol": cluster["blind_protocol"],
                "route_effect_evidence": cluster["route_effect_evidence"],
                "boundary_traps": cluster["boundary_traps"],
                "evidence_ref": cluster["evidence_ref"],
            },
            f"qualification.cluster_ledger[{index}]",
        )
        if any(
            member["product_system_label"] not in spec["product_system_labels"]
            for member in exact_members
        ):
            raise ValueError("qualification member labels are inconsistent")
        gate_values = _derived_gate_values(exact_members, spec, authority_bindings)
        expected_gate_results = [
            {"gate": gate, "passed": gate_values[gate]} for gate in _REQUIRED_GATES
        ]
        failed_gate = next(
            (item["gate"] for item in expected_gate_results if not item["passed"]),
            None,
        )
        expected_fields = {
            "normalized_product_system_labels": spec["product_system_labels"],
            "scientific_disputes": spec["scientific_disputes"],
            "dispute_cardinality": len(spec["scientific_disputes"]),
            "reference_flow_groups": _reference_flow_groups(exact_members),
            "functional_unit_treatment": spec["functional_unit_comparison"]["treatment"],
            "functional_unit_comparison": spec["functional_unit_comparison"],
            "native_result_comparison": spec["native_result_comparison"],
            "blind_protocol": spec["blind_protocol"],
            "route_effect_evidence": spec["route_effect_evidence"],
            "evidence_ref": spec["evidence_ref"],
            "boundary_traps": spec["boundary_traps"],
            "gate_results": expected_gate_results,
            "failed_gate": failed_gate,
            "qualified": failed_gate is None,
        }
        for key, expected in expected_fields.items():
            if cluster[key] != expected:
                raise ValueError(f"qualification cluster {key} is inconsistent")
        normalized_ledger.append(dict(cluster))

    raw_candidate_ledger = record.get("candidate_ledger")
    if not isinstance(raw_candidate_ledger, list) or not raw_candidate_ledger:
        raise ValueError("qualification candidate ledger must be non-empty")
    normalized_candidate_ledger = [
        _candidate(row, f"qualification.candidate_ledger[{index}]")
        for index, row in enumerate(raw_candidate_ledger)
    ]
    if normalized_candidate_ledger != raw_candidate_ledger:
        raise ValueError("qualification candidate ledger must be canonical")
    candidate_keys = [
        (row["process_id"], row["process_version"])
        for row in normalized_candidate_ledger
    ]
    if len(candidate_keys) != len(set(candidate_keys)):
        raise ValueError("qualification candidate ledger identities must be unique")
    if normalized_candidate_ledger != sorted(
        normalized_candidate_ledger,
        key=lambda row: (
            order_index[str(row.get("cluster_id"))],
            str(row["process_id"]),
            str(row["process_version"]),
        ),
    ):
        raise ValueError("qualification candidate ledger order is inconsistent")
    for cluster in normalized_ledger:
        cluster_rows = [
            row
            for row in normalized_candidate_ledger
            if row.get("cluster_id") == cluster["cluster_id"]
        ]
        if (
            cluster["screened_candidate_count"] != len(cluster_rows)
            or cluster["candidate_rows"] != cluster_rows
        ):
            raise ValueError("qualification cluster candidate rows are inconsistent")

    def sort_key(cluster: Mapping[str, object]) -> Tuple[object, ...]:
        member_refs = tuple(
            (member["process_id"], member["process_version"])
            for member in cluster["exact_members"]
        )
        return (
            order_index[str(cluster["cluster_id"])],
            -int(cluster["dispute_cardinality"]),
            member_refs,
            cluster["cluster_id"],
        )

    if normalized_ledger != sorted(normalized_ledger, key=sort_key):
        raise ValueError("qualification cluster ledger ranking is inconsistent")
    qualified = [cluster for cluster in normalized_ledger if cluster["qualified"]]
    qualified.sort(key=sort_key)
    selected = qualified[0] if qualified else None
    if record["qualified_cluster_count"] != len(qualified):
        raise ValueError("qualification qualified-cluster count is inconsistent")
    if record["selected_cluster"] != selected:
        raise ValueError("qualification selected cluster is inconsistent with ranking")
    if record["admission_criteria_lowered"] is not False:
        raise ValueError("qualification may not lower admission criteria")
    if record["external_authority_substituted"] is not False:
        raise ValueError("qualification may not substitute external authority")
    expected_route = "freeze-qualified-research-council-benchmark" if qualified else (
        "await-tiangong-provider-closure-publication"
        if all(
            row.get("current_read_status") == "exhausted"
            and row.get("terminal_blocker")
            == "tiangong_provider_closure_unpublished"
            for row in normalized_candidate_ledger
        )
        else "complete-next-bounded-tiangong-read"
    )
    if record["next_route"] != expected_route:
        raise ValueError("qualification next route is inconsistent")
    return {
        "record": dict(record),
        "catalog_ref": catalog_ref,
        "authority_evidence": authority_evidence,
        "selected": selected,
    }


def compile_benchmark_freeze(
    *,
    qualification: Mapping[str, object],
    task_prompt: str,
    tools: Sequence[str],
    allowed_effects: Sequence[str],
    initial_evidence_set: Sequence[Mapping[str, object]],
    controller_version: Mapping[str, object],
    responsibility_boundary: Mapping[str, object],
) -> Dict[str, object]:
    """Freeze one qualified cluster and its exact execution/authority boundary."""
    _reject_secret_keys(qualification)
    _reject_secret_keys(
        {
            "task_prompt": task_prompt,
            "tools": tools,
            "allowed_effects": allowed_effects,
            "initial_evidence_set": initial_evidence_set,
            "controller_version": controller_version,
            "responsibility_boundary": responsibility_boundary,
        }
    )
    _reject_secret_values(
        {
            "qualification": qualification,
            "task_prompt": task_prompt,
            "tools": tools,
            "allowed_effects": allowed_effects,
            "initial_evidence_set": initial_evidence_set,
            "controller_version": controller_version,
            "responsibility_boundary": responsibility_boundary,
        }
    )
    validated = _validate_qualification(qualification)
    record = validated["record"]
    selected = validated["selected"]
    if selected is None:
        raise ValueError("a qualified cluster is required for benchmark freeze")
    exact_members = selected.get("exact_members")
    functional_unit = selected.get("functional_unit_treatment")
    if any(member["functional_unit"] != functional_unit for member in exact_members):
        raise ValueError("qualification selected member functional units must be equal")
    frozen_members = [
        {
            key: member[key]
            for key in (
                "process_id",
                "process_version",
                "product_system_label",
                "reference_flow_id",
                "reference_flow_version",
                "functional_unit",
                "native_result",
                "boundary",
            )
        }
        for member in exact_members
    ]

    evidence = [
        _artifact(value, f"initial_evidence_set[{index}]")
        for index, value in enumerate(initial_evidence_set)
    ]
    if not evidence:
        raise ValueError("initial_evidence_set must not be empty")
    evidence.sort(key=_canonical_json)
    if len({_canonical_json(value) for value in evidence}) != len(evidence):
        raise ValueError("duplicate initial evidence is not allowed")
    normalized_tools = _string_list(tools, "tools")
    normalized_effects = _string_list(allowed_effects, "allowed_effects")
    if not normalized_tools or not normalized_effects:
        raise ValueError("tools and allowed_effects must not be empty")
    if set(normalized_tools) != _ALLOWED_FREEZE_TOOLS:
        raise ValueError("tools must equal the frozen safe read tool IDs")
    if set(normalized_effects) != _ALLOWED_FREEZE_EFFECTS:
        raise ValueError(
            "allowed_effects must contain only read_only and isolated_local_write"
        )

    controller = _mapping(controller_version, "controller_version")
    required_controller_keys = {
        "git_commit",
        "controller_sha256",
        "skill_sha256",
        "state_contract_sha256",
    }
    if set(controller) != required_controller_keys:
        raise ValueError("controller_version must bind the frozen Controller contract")
    normalized_controller = {
        key: _text(controller[key], f"controller_version.{key}", casefold=True)
        for key in sorted(controller)
    }
    if not re.fullmatch(r"[0-9a-f]{40}", normalized_controller["git_commit"]):
        raise ValueError("controller_version.git_commit must be a full Git commit")
    for key in ("controller_sha256", "skill_sha256", "state_contract_sha256"):
        if not _SHA256_RE.fullmatch(normalized_controller[key]):
            raise ValueError(f"controller_version.{key} must be a SHA-256")

    boundary = _mapping(responsibility_boundary, "responsibility_boundary")
    if set(boundary) != {"agent_may", "user_required_for"}:
        raise ValueError(
            "responsibility_boundary must bind agent_may and user_required_for"
        )
    normalized_boundary = {
        "agent_may": _string_list(boundary["agent_may"], "responsibility_boundary.agent_may"),
        "user_required_for": _string_list(
            boundary["user_required_for"],
            "responsibility_boundary.user_required_for",
        ),
    }
    if not normalized_boundary["agent_may"] or not normalized_boundary["user_required_for"]:
        raise ValueError("responsibility boundary lists must not be empty")
    if set(normalized_boundary["agent_may"]) != _ALLOWED_AGENT_CAPABILITIES:
        raise ValueError(
            "responsibility_boundary.agent_may must equal the frozen capability IDs"
        )
    if set(normalized_boundary["user_required_for"]) != _REQUIRED_USER_RESPONSIBILITIES:
        raise ValueError(
            "responsibility_boundary.user_required_for must equal the approved responsibility IDs"
        )

    body: Dict[str, object] = {
        "contract": _FREEZE_CONTRACT,
        "qualification_id": _text(
            record.get("qualification_id"), "qualification.qualification_id", casefold=True
        ),
        "cluster_id": _text(selected.get("cluster_id"), "selected_cluster.cluster_id", casefold=True),
        "exact_tiangong_members": frozen_members,
        "tiangong_authority_evidence_ref": validated["authority_evidence"]["artifact_ref"],
        "cluster_evidence_ref": selected["evidence_ref"],
        "functional_unit_treatment": functional_unit,
        "task_prompt": _text(task_prompt, "task_prompt"),
        "tools": normalized_tools,
        "allowed_effects": normalized_effects,
        "initial_evidence_set": evidence,
        "controller_version": normalized_controller,
        "responsibility_boundary": normalized_boundary,
        "next_route": "run-frozen-single-agent-baseline",
    }
    return _finish(body, "freeze")
