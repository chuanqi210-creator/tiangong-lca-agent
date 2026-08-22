"""Pure validation and classification for immutable research-material packages."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Mapping


INTAKE_CONTRACT = "lca_research_kernel.static_research_material_intake_contract/v1"
PACKAGE_CONTRACT = "lca_research_kernel.static_material_package/v1"
CLASSIFICATION_CONTRACT = "lca_research_kernel.static_material_classification/v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _string_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{label} must be a string list")
    if len(value) != len(set(value)):
        raise ValueError(f"{label} must not contain duplicates")
    return list(value)


def _ensure_finite(value: object, label: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{label} must contain only finite numeric values")
    if isinstance(value, Mapping):
        for key, child in value.items():
            _ensure_finite(child, f"{label}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _ensure_finite(child, f"{label}[{index}]")


def _contract_sets(contract: Mapping[str, object]) -> dict[str, object]:
    if contract.get("contract") != INTAKE_CONTRACT:
        raise ValueError("static research material intake contract drifted")
    if contract.get("package_contract") != PACKAGE_CONTRACT:
        raise ValueError("static material package contract drifted")
    if contract.get("classification_contract") != CLASSIFICATION_CONTRACT:
        raise ValueError("static material classification contract drifted")
    classes = _string_list(contract.get("material_classes"), "material_classes")
    expected_classes = [
        "accepted_research_input",
        "candidate_requires_confirmation",
        "method_or_design_not_result",
        "unusable_or_missing",
    ]
    if classes != expected_classes:
        raise ValueError("static material classes drifted")
    required = _string_list(contract.get("required_lca_fields"), "required_lca_fields")
    if not required:
        raise ValueError("required_lca_fields must not be empty")
    fallback = _string_list(contract.get("fallback_order"), "fallback_order")
    if fallback != [
        "authoritative_supplement",
        "transparent_proxy",
        "parameter_range",
        "bounded_exclusion",
        "claim_closed",
    ]:
        raise ValueError("static material fallback order drifted")
    if contract.get("user_question_limit") != 1:
        raise ValueError("static material user question limit drifted")
    claim_rules = contract.get("claim_rules")
    if not isinstance(claim_rules, Mapping) or any(
        claim_rules.get(key) is not expected
        for key, expected in {
            "accepted_input_requires_exact_bytes_authority_and_scope": True,
            "filename_can_promote_result": False,
            "git_tracking_can_promote_result": False,
            "executable_code_can_promote_result": False,
            "proxy_can_be_observed_project_result": False,
            "parameter_can_be_observed_project_result": False,
            "missing_fields_must_narrow_claims": True,
        }.items()
    ):
        raise ValueError("static material claim rules drifted")
    return {
        "classes": classes,
        "required": required,
        "types": set(_string_list(contract.get("allowed_material_types"), "allowed_material_types")),
        "non_results": set(_string_list(contract.get("non_result_material_types"), "non_result_material_types")),
        "read_statuses": set(_string_list(contract.get("allowed_read_statuses"), "allowed_read_statuses")),
        "authority_statuses": set(_string_list(contract.get("allowed_authority_statuses"), "allowed_authority_statuses")),
        "scope_statuses": set(_string_list(contract.get("allowed_scope_statuses"), "allowed_scope_statuses")),
    }


def _relative_path(value: object, label: str) -> str:
    text = _text(value, label)
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} path escapes the static package")
    return path.as_posix()


def _enum(row: Mapping[str, object], key: str, allowed: set[str]) -> str:
    value = _text(row.get(key), key)
    if value not in allowed:
        raise ValueError(f"{key} is unsupported")
    return value


def _classify(
    *, read_status: str, material_type: str, authority_status: str, scope_status: str,
    non_results: set[str],
) -> str:
    if read_status != "exact_bytes_verified":
        return "unusable_or_missing"
    if material_type in non_results:
        return "method_or_design_not_result"
    if authority_status == "accepted" and scope_status == "applicable":
        return "accepted_research_input"
    return "candidate_requires_confirmation"


def compile_static_material_classification(
    package: Mapping[str, object],
    material_bytes: Mapping[str, bytes],
    contract: Mapping[str, object],
) -> dict:
    """Compile a deterministic four-ledger classification with no side effects."""

    rules = _contract_sets(contract)
    _ensure_finite(package, "package")
    if package.get("contract") != PACKAGE_CONTRACT:
        raise ValueError("static material package identity drifted")
    package_id = _text(package.get("package_id"), "package_id")
    revision = _text(package.get("revision"), "revision")
    source_commit = _text(package.get("source_git_commit"), "source_git_commit")
    if GIT_COMMIT_RE.fullmatch(source_commit) is None:
        raise ValueError("source_git_commit must be an exact Git commit")
    materials = package.get("materials")
    if not isinstance(materials, list) or not materials:
        raise ValueError("static material package materials are missing")
    if not isinstance(material_bytes, Mapping):
        raise ValueError("material_bytes must be a mapping")

    normalized = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(materials):
        if not isinstance(raw, Mapping):
            raise ValueError(f"materials[{index}] must be an object")
        material_id = _text(raw.get("material_id"), "material_id")
        if material_id in seen_ids:
            raise ValueError("duplicate material_id")
        seen_ids.add(material_id)
        path = _relative_path(raw.get("path"), f"materials[{index}]")
        sha256 = _text(raw.get("sha256"), "sha256")
        if SHA256_RE.fullmatch(sha256) is None:
            raise ValueError("sha256 must be a lowercase SHA-256")
        byte_count = raw.get("bytes")
        if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count < 0:
            raise ValueError("bytes must be a non-negative integer")
        row_commit = _text(raw.get("source_git_commit"), "material source_git_commit")
        if row_commit != source_commit:
            raise ValueError("mixed source commits are forbidden")
        read_status = _enum(raw, "read_status", rules["read_statuses"])
        material_type = _enum(raw, "material_type", rules["types"])
        authority_status = _enum(raw, "authority_status", rules["authority_statuses"])
        scope_status = _enum(raw, "scope_status", rules["scope_statuses"])
        lca_fields = raw.get("lca_fields")
        if not isinstance(lca_fields, Mapping):
            raise ValueError("lca_fields must be an object")
        _ensure_finite(lca_fields, f"materials[{index}].lca_fields")
        supported_uses = _string_list(raw.get("supported_uses"), "supported_uses")
        forbidden_wording = _string_list(raw.get("forbidden_wording"), "forbidden_wording")
        if read_status == "exact_bytes_verified":
            content = material_bytes.get(path)
            if not isinstance(content, bytes):
                raise ValueError(f"exact material bytes are missing: {path}")
            if len(content) != byte_count or hashlib.sha256(content).hexdigest() != sha256:
                raise ValueError(f"material content identity drifted under revision {revision}: {path}")
        classification = _classify(
            read_status=read_status,
            material_type=material_type,
            authority_status=authority_status,
            scope_status=scope_status,
            non_results=rules["non_results"],
        )
        missing_fields = sorted(set(rules["required"]) - set(lca_fields))
        normalized.append(
            {
                "material_id": material_id,
                "path": path,
                "sha256": sha256,
                "bytes": byte_count,
                "source_git_commit": source_commit,
                "read_status": read_status,
                "material_type": material_type,
                "authority_status": authority_status,
                "scope_status": scope_status,
                "classification": classification,
                "lca_fields": deepcopy(dict(lca_fields)),
                "missing_required_fields": missing_fields,
                "supported_uses": supported_uses,
                "forbidden_wording": forbidden_wording,
            }
        )
    normalized.sort(key=lambda row: row["material_id"])
    ledgers = {
        label: [deepcopy(row) for row in normalized if row["classification"] == label]
        for label in rules["classes"]
    }
    identity_basis = {
        "package_id": package_id,
        "revision": revision,
        "source_git_commit": source_commit,
        "materials": [
            {
                key: row[key]
                for key in ("material_id", "path", "sha256", "bytes", "source_git_commit")
            }
            for row in normalized
        ],
    }
    return {
        "contract": CLASSIFICATION_CONTRACT,
        "package_id": package_id,
        "revision": revision,
        "source_git_commit": source_commit,
        "package_content_sha256": hashlib.sha256(_canonical_json(identity_basis)).hexdigest(),
        "material_count": len(normalized),
        "ledgers": ledgers,
        "missing_required_fields": [
            {"material_id": row["material_id"], "fields": row["missing_required_fields"]}
            for row in normalized
            if row["missing_required_fields"]
        ],
        "supported_uses": sorted({item for row in normalized for item in row["supported_uses"]}),
        "forbidden_wording": sorted({item for row in normalized for item in row["forbidden_wording"]}),
        "fallback_order": list(contract["fallback_order"]),
        "user_question_limit": 1,
        "external_effect_count": 0,
    }


def validate_static_material_classification(
    classification: Mapping[str, object],
) -> list[str]:
    """Return deterministic fail-closed findings for a compiled classification."""

    findings: list[str] = []
    if classification.get("contract") != CLASSIFICATION_CONTRACT:
        findings.append("classification contract mismatch")
    ledgers = classification.get("ledgers")
    expected = {
        "accepted_research_input",
        "candidate_requires_confirmation",
        "method_or_design_not_result",
        "unusable_or_missing",
    }
    if not isinstance(ledgers, Mapping) or set(ledgers) != expected:
        findings.append("classification ledger set mismatch")
        return findings
    seen: set[str] = set()
    count = 0
    for label in sorted(ledgers):
        rows = ledgers[label]
        if not isinstance(rows, list):
            findings.append(f"classification ledger {label} is not a list")
            continue
        ids = []
        for row in rows:
            count += 1
            if not isinstance(row, Mapping):
                findings.append(f"classification ledger {label} contains a non-object")
                continue
            material_id = row.get("material_id")
            ids.append(material_id)
            if row.get("classification") != label:
                findings.append("ledger classification mismatch")
            if not isinstance(material_id, str) or material_id in seen:
                findings.append("classification material identity duplicated or missing")
            else:
                seen.add(material_id)
            if SHA256_RE.fullmatch(str(row.get("sha256", ""))) is None:
                findings.append("classification source hash invalid")
            path = Path(str(row.get("path", "")))
            if path.is_absolute() or ".." in path.parts:
                findings.append("classification source path escapes package")
        if ids != sorted(ids):
            findings.append(f"classification ledger {label} is not deterministic")
    if classification.get("material_count") != count:
        findings.append("classification material count mismatch")
    if classification.get("external_effect_count") != 0:
        findings.append("classification external effect count is nonzero")
    return findings
