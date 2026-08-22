"""Immutable negative benchmark assets and TianGong content-change monitoring."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
from pathlib import Path, PurePosixPath
from typing import Dict, List, Mapping, Sequence

from .tiangong_authority import verify_authority_receipt
from .tiangong_native_case import verify_native_case_binding
from .tiangong_second_case_package import _verify_selection


JsonObject = Dict[str, object]

_POLICY_CONTRACT = "lca_research_kernel.research_council_benchmark_portfolio_policy/v1"
_QUALIFICATION_CONTRACT = "lca_research_kernel.research_council_benchmark_qualification/v2"
_NEGATIVE_CONTRACT = "lca_research_kernel.negative_benchmark_assets/v1"
_MONITOR_CONTRACT = "lca_research_kernel.benchmark_update_monitor/v1"
_OBSERVATION_CONTRACT = "lca_research_kernel.benchmark_monitor_observation/v1"
_FRESHWATER_CANARY_CONTRACT = "lca_research_kernel.freshwater_harness_canary/v1"
_EXPECTED_POLICY = {
    "contract": _POLICY_CONTRACT,
    "asset_roles": [
        "negative_authority_stress_case",
        "upstream_change_monitor_subject",
        "harness_canary",
        "positive_benchmark_candidate",
        "positive_benchmark_cluster",
        "immutable_freeze_proposal",
    ],
    "discovery_model_cap": 10000,
    "positive_hard_gates": [
        "published_open_data_exact_version",
        "tidas_schema_valid",
        "provider_closure_complete",
        "functional_unit_explicit",
        "system_boundary_explicit",
        "native_result_exact",
        "read_only_replay_complete",
        "comparison_basis_shared",
        "scientific_dispute_real",
        "not_harness_canary",
    ],
    "terminal_outcomes": [
        "benchmark_portfolio_ready",
        "no_positive_benchmark_currently_available",
    ],
    "admission_criteria_lowering_allowed": False,
    "external_authority_substitution_allowed": False,
    "remote_write_allowed": False,
}
_EXPECTED_SOURCES = {
    "qualification": (
        "qualification_ledger",
        "outputs/research-council-benchmark/research-council-benchmark/"
        "qualification/8c842560d748ce92/ledger.json",
    ),
    "provider_audit_v1": (
        "provider_closure_audit_v1",
        "outputs/research-council-benchmark/provider-closure-audit/audit.json",
    ),
    "provider_audit_v2": (
        "provider_closure_audit_v2",
        "outputs/research-council-benchmark/provider-closure-audits/"
        "capture-20260721T012744Z/audit.json",
    ),
}
_EXPECTED_QUALIFICATION_ID = "8c842560d748ce92"
_EXPECTED_AUDITS = {
    "lca_research_kernel.tiangong_provider_closure_audit/v1": "1b93c55f0206b9ff",
    "lca_research_kernel.tiangong_provider_closure_audit/v2": "be7268aaf5dc0569",
}
_EXPECTED_CLUSTER_COUNTS = {
    "copper_routes": 6,
    "truck_fuel_routes": 45,
    "wastewater_routes": 2,
}
_EXPECTED_FRESHWATER_SOURCES = {
    "selection": (
        "freshwater_selection",
        "outputs/tiangong-authority-following-second-case/tiangong-authority-following/"
        "catalog/selection-f42cfa88d09f7f5f.json",
    ),
    "receipt": (
        "freshwater_authority_receipt",
        "outputs/tiangong-authority-following-second-case/authority/"
        "receipt-50d206a2899a86b1.json",
    ),
    "native_binding": (
        "freshwater_native_binding",
        "outputs/tiangong-authority-following-second-case/native-case/"
        "binding-aed71a598fbbf7ba.json",
    ),
    "acceptance": (
        "freshwater_clean_acceptance",
        "outputs/tiangong-authority-following-second-case/tiangong-authority-following/"
        "acceptance/acceptance-5e426c8a392a576a.json",
    ),
}
_EXPECTED_FRESHWATER_IDENTITIES = {
    "selection_id": "f42cfa88d09f7f5f",
    "receipt_id": "50d206a2899a86b1",
    "receipt_content_sha256": "50d206a2899a86b12aef3ac0a66a162d08dba74acb15527ab1f2b8567c7f6399",
    "binding_id": "aed71a598fbbf7ba",
    "binding_content_sha256": "aed71a598fbbf7bad08e50a9c11962e02b7ed9de816d39b1055654bd65ce9d91",
    "acceptance_id": "5e426c8a392a576a",
    "acceptance_content_sha256": "5e426c8a392a576a52320058e89f0f3179cbe3c85417f15f5f4f70cd04c16e4d",
}
_EXPECTED_FRESHWATER_SOURCE_BYTES = {
    "selection": {
        "sha256": "8c31a8b30e28b1ed39816ac2852b2b5e1f293f24fb1182518a8db2482e36db28",
        "bytes": 207957,
    },
    "receipt": {
        "sha256": "7ff94d0b3c04687884b18e80d06e43e067cf0516515d38730e4879bc6118ebe9",
        "bytes": 4274,
    },
    "native_binding": {
        "sha256": "a49aa794d39548691aa3cebe4a377b5775a258aed31cddb59190ec2f649b009b",
        "bytes": 47119,
    },
    "acceptance": {
        "sha256": "b855f8e4218ceff136f1a5e59ff35d581d893c1b15f92e99563d79e0e812a427",
        "bytes": 1495,
    },
}
_EXPECTED_FRESHWATER_CANARY_CONTENT_SHA256 = (
    "284d5292284da5bf59629b2f8db2d85bb50dd2056e12e3e8ad1f3c5f0920aab8"
)
_EXPECTED_FRESHWATER_CANARY_ID = "284d5292284da5bf"
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)+$")
_SECRET_KEY_MARKERS = {
    "accesstoken", "apikey", "apitoken", "authorization", "clientsecret",
    "credential", "credentials", "password", "passwd", "privatekey",
    "refreshtoken", "secret", "token", "cookie", "setcookie", "sessionid",
}
_SECRET_VALUE_PATTERNS = (
    re.compile(r"authorization\s*:", re.IGNORECASE),
    re.compile(r"bearer(?:\s+token|\s+[a-z0-9._~+/=-]+)", re.IGNORECASE),
    re.compile(r"(?:api[_ -]?key|password|secret|token)\s*[=:]", re.IGNORECASE),
    re.compile(r"(?:set-)?cookie\s*:\s*[^\s]+", re.IGNORECASE),
    re.compile(r"session[_ -]?id\s*=\s*[^\s;]+", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_+/=-]{20,}(?:\.[A-Za-z0-9_+/=-]{8,})*\b"),
    re.compile(r"\bsb_(?:publishable|secret)_[A-Za-z0-9_-]{12,}\b"),
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be an object with string keys")
    return value


def _sequence(value: object, field: str) -> Sequence[object]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return " ".join(value.split())


def _sha256(value: object, field: str) -> str:
    text = _text(value, field).lower()
    if not _SHA256_RE.fullmatch(text):
        raise ValueError(f"{field} must be a lowercase SHA-256")
    return text


def _uuid(value: object, field: str) -> str:
    text = _text(value, field).lower()
    if not _UUID_RE.fullmatch(text):
        raise ValueError(f"{field} must be an exact UUID")
    return text


def _version(value: object, field: str) -> str:
    text = _text(value, field)
    if not _VERSION_RE.fullmatch(text):
        raise ValueError(f"{field} must be an exact dotted version")
    return text


def _reject_secrets(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("mapping keys must be strings")
            normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
            if normalized != "secretpersistencecount" and any(
                marker in normalized for marker in _SECRET_KEY_MARKERS
            ):
                raise ValueError("secret-like key is not allowed")
            _reject_secrets(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_secrets(child)
    elif isinstance(value, str) and any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS):
        raise ValueError("secret-like value is not allowed")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite values are not allowed")


def _validate_policy(value: object) -> JsonObject:
    _reject_secrets(value)
    policy = dict(_mapping(value, "policy"))
    if policy != _EXPECTED_POLICY:
        raise ValueError("policy must exactly match the approved portfolio policy")
    return policy


def _safe_source_path(value: object, field: str) -> str:
    path = _text(value, field)
    pure = PurePosixPath(path)
    if ("\\" in path or "\x00" in path or path.startswith("/") or
            not pure.parts or any(part in {"", ".", ".."} for part in pure.parts)):
        raise ValueError(f"{field} must be a safe workspace-relative path")
    return str(pure)


def _validate_sources(value: object, *, inputs: Mapping[str, object]) -> JsonObject:
    refs = _mapping(value, "source_refs")
    if set(refs) != set(_EXPECTED_SOURCES):
        raise ValueError("source_refs must bind exactly the three immutable sources")
    normalized: JsonObject = {}
    root = _REPO_ROOT
    for key, (expected_role, expected_path) in _EXPECTED_SOURCES.items():
        ref = _mapping(refs[key], f"source_refs.{key}")
        if set(ref) != {"role", "path", "sha256", "bytes"}:
            raise ValueError(f"source_refs.{key} has an invalid shape")
        path = _safe_source_path(ref["path"], f"source_refs.{key}.path")
        if ref["role"] != expected_role or path != expected_path:
            raise ValueError(f"source_refs.{key} is not the required immutable source")
        size = ref["bytes"]
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise ValueError(f"source_refs.{key}.bytes must be a positive integer")
        expected_sha = _sha256(ref["sha256"], f"source_refs.{key}.sha256")
        resolved = (root / path).resolve()
        if root not in resolved.parents:
            raise ValueError(f"source_refs.{key}.path escapes the workspace")
        try:
            payload = resolved.read_bytes()
            reopened = json.loads(payload.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"source_refs.{key} must reopen a UTF-8 JSON source") from error
        if len(payload) != size or hashlib.sha256(payload).hexdigest() != expected_sha:
            raise ValueError(f"source_refs.{key} source bytes do not match the pinned SHA")
        if reopened != inputs[key]:
            raise ValueError(f"source_refs.{key} does not reopen the supplied input")
        normalized[key] = {"role": expected_role, "path": path, "sha256": expected_sha, "bytes": size}
    return normalized


def _validate_qualification(value: object) -> List[JsonObject]:
    qualification = _mapping(value, "qualification")
    if qualification.get("contract") != _QUALIFICATION_CONTRACT:
        raise ValueError("qualification contract is not accepted v2")
    body = {key: child for key, child in qualification.items()
            if key not in {"qualification_content_sha256", "qualification_id"}}
    digest = _sha256(qualification.get("qualification_content_sha256"), "qualification.qualification_content_sha256")
    if digest != _digest(body) or qualification.get("qualification_id") != digest[:16]:
        raise ValueError("qualification identity does not match its content")
    if qualification["qualification_id"] != _EXPECTED_QUALIFICATION_ID:
        raise ValueError("qualification is not the immutable 53-candidate ledger")
    rows: List[JsonObject] = []
    seen = set()
    for index, raw in enumerate(_sequence(qualification.get("candidate_ledger"), "qualification.candidate_ledger")):
        candidate = _mapping(raw, f"qualification.candidate_ledger[{index}]")
        row = {
            "cluster_id": _text(candidate.get("cluster_id"), f"qualification.candidate_ledger[{index}].cluster_id"),
            "process_id": _uuid(candidate.get("process_id"), f"qualification.candidate_ledger[{index}].process_id"),
            "process_version": _version(candidate.get("process_version"), f"qualification.candidate_ledger[{index}].process_version"),
            "terminal_blocker": _text(candidate.get("terminal_blocker"), f"qualification.candidate_ledger[{index}].terminal_blocker"),
        }
        identity = (row["process_id"], row["process_version"])
        if identity in seen:
            raise ValueError("qualification contains duplicate candidate identities")
        seen.add(identity)
        if row["terminal_blocker"] != "tiangong_provider_closure_unpublished":
            raise ValueError("negative candidates must retain their authority blocker")
        rows.append(row)
    counts = {cluster: sum(row["cluster_id"] == cluster for row in rows)
              for cluster in _EXPECTED_CLUSTER_COUNTS}
    if len(rows) != 53 or counts != _EXPECTED_CLUSTER_COUNTS:
        raise ValueError("qualification must contain the exact 6/2/45 candidate distribution")
    return sorted(rows, key=lambda row: (str(row["cluster_id"]), str(row["process_id"]), str(row["process_version"])))


def _validate_audits(value: object, qualification: Mapping[str, object]) -> List[JsonObject]:
    audits = _sequence(value, "provider_audits")
    if len(audits) != 2:
        raise ValueError("provider_audits must contain the historical v1 and hardened v2")
    normalized = []
    for index, raw in enumerate(audits):
        audit = _mapping(raw, f"provider_audits[{index}]")
        contract = audit.get("contract")
        expected_id = _EXPECTED_AUDITS.get(contract)
        if expected_id is None:
            raise ValueError("provider audit contract is not an accepted historical version")
        body = {key: child for key, child in audit.items() if key not in {"audit_content_sha256", "audit_id"}}
        digest = _sha256(audit.get("audit_content_sha256"), f"provider_audits[{index}].audit_content_sha256")
        if digest != _digest(body) or audit.get("audit_id") != digest[:16] or audit["audit_id"] != expected_id:
            raise ValueError("provider audit identity does not match accepted content")
        if (audit.get("qualification_id") != qualification["qualification_id"] or
                audit.get("qualification_content_sha256") != qualification["qualification_content_sha256"]):
            raise ValueError("provider audit is not bound to the immutable qualification")
        for field, expected in (("candidate_count", 53), ("successful_candidate_count", 53),
                                ("failed_candidate_count", 0), ("mention_candidate_count", 0),
                                ("mention_row_count", 0)):
            if audit.get(field) != expected:
                raise ValueError("provider audit does not preserve the 53/53 zero-hit observation")
        if audit.get("closure_status") != "published_lifecyclemodel_reference_absent":
            raise ValueError("provider audit must remain a bounded zero-hit observation")
        results = _sequence(_mapping(audit.get("capture"), f"provider_audits[{index}].capture").get("candidate_results"), f"provider_audits[{index}].capture.candidate_results")
        if len(results) != 53:
            raise ValueError("provider audit capture must contain all 53 candidates")
        for result_index, raw_result in enumerate(results):
            result = _mapping(raw_result, f"provider_audits[{index}].capture.candidate_results[{result_index}]")
            if (result.get("http_status") != 200 or result.get("success") is not True or
                    result.get("error_code") is not None or result.get("error_message") is not None or
                    result.get("mentions") != []):
                raise ValueError("provider audit capture must retain an exact successful zero-hit result")
        if contract.endswith("/v2"):
            runtime = _mapping(_mapping(audit.get("capture"), "audit.capture").get("runtime"), "audit.capture.runtime")
            required_runtime = {
                "cli_package": "@tiangong-lca/cli", "cli_version": "0.0.29",
                "adoption_id": "4069fd724039d86c",
                "capture_tool_sha256": "958e2aaf62be80fccd1aad1e5b6c671443927ea2008c6440ec76aa67d61f084f",
            }
            if any(runtime.get(key) != expected for key, expected in required_runtime.items()):
                raise ValueError("hardened audit v2 runtime binding has changed")
        normalized.append({"contract": contract, "audit_id": expected_id, "audit_content_sha256": digest})
    if {row["contract"] for row in normalized} != set(_EXPECTED_AUDITS):
        raise ValueError("provider audit versions must be unique and complete")
    return sorted(normalized, key=lambda row: str(row["contract"]))


def _identity(row: Mapping[str, object]) -> str:
    return f"{row['process_id']}@{row['process_version']}"


def _monitor_observation(candidates: Sequence[Mapping[str, object]], audits: Sequence[Mapping[str, object]], qualification: Mapping[str, object]) -> JsonObject:
    audit_ids = {row["contract"]: row["audit_content_sha256"] for row in audits}
    identities = [{"process_id": row["process_id"], "process_version": row["process_version"]} for row in candidates]
    return {
        "contract": _OBSERVATION_CONTRACT,
        "qualification_id": qualification["qualification_id"],
        "qualification_content_sha256": qualification["qualification_content_sha256"],
        "audit_content_sha256": audit_ids,
        "candidate_identities": identities,
        "reference_sets": [{**row, "references": []} for row in identities],
        "model_states": [{**row, "state": "published_lifecyclemodel_reference_absent"} for row in identities],
        "provider_graph_digests": [{**row, "digest": _digest({"candidate": row, "provider_graph": "not_published"})} for row in identities],
        "surface_contract_sha256": _digest({"audit_content_sha256": audit_ids, "qualification_content_sha256": qualification["qualification_content_sha256"]}),
    }


def _validate_observation(value: object, *, require_historical_binding: bool = False) -> JsonObject:
    observation = _mapping(value, "observation")
    required = {"contract", "qualification_id", "qualification_content_sha256", "audit_content_sha256", "candidate_identities", "reference_sets", "model_states", "provider_graph_digests", "surface_contract_sha256"}
    if set(observation) != required or observation.get("contract") != _OBSERVATION_CONTRACT:
        raise ValueError("monitor observation has an invalid contract shape")
    if require_historical_binding and observation.get("qualification_id") != _EXPECTED_QUALIFICATION_ID:
        raise ValueError("monitor observation is not bound to the immutable qualification")
    identities = []
    seen = set()
    for index, raw in enumerate(_sequence(observation["candidate_identities"], "observation.candidate_identities")):
        row = _mapping(raw, f"observation.candidate_identities[{index}]")
        if set(row) != {"process_id", "process_version"}:
            raise ValueError("candidate identity has an invalid shape")
        normalized = {"process_id": _uuid(row["process_id"], "candidate identity process_id"), "process_version": _version(row["process_version"], "candidate identity process_version")}
        if _identity(normalized) in seen:
            raise ValueError("monitor observation contains duplicate candidate identities")
        seen.add(_identity(normalized)); identities.append(normalized)
    if not identities:
        raise ValueError("monitor observation must contain candidates")
    identities.sort(key=_identity)

    def indexed(name: str, extra: str, validator) -> List[JsonObject]:
        rows = []
        seen_keys = set()
        for index, raw in enumerate(_sequence(observation[name], f"observation.{name}")):
            row = _mapping(raw, f"observation.{name}[{index}]")
            if set(row) != {"process_id", "process_version", extra}:
                raise ValueError(f"observation.{name} has an invalid row shape")
            item = {"process_id": _uuid(row["process_id"], f"observation.{name}.process_id"), "process_version": _version(row["process_version"], f"observation.{name}.process_version"), extra: validator(row[extra], f"observation.{name}.{extra}")}
            key = _identity(item)
            if key in seen_keys: raise ValueError(f"observation.{name} contains duplicates")
            seen_keys.add(key); rows.append(item)
        if seen_keys != {_identity(row) for row in identities}:
            raise ValueError(f"observation.{name} must cover exactly all candidate identities")
        return sorted(rows, key=_identity)

    def references(value: object, field: str) -> List[str]:
        refs = [_text(item, f"{field}[{index}]") for index, item in enumerate(_sequence(value, field))]
        if refs != sorted(set(refs)):
            raise ValueError(f"{field} must be sorted and unique")
        return refs

    normalized = {
        "contract": _OBSERVATION_CONTRACT,
        "qualification_id": _text(observation["qualification_id"], "observation.qualification_id"),
        "qualification_content_sha256": _sha256(observation["qualification_content_sha256"], "observation.qualification_content_sha256"),
        "audit_content_sha256": {key: _sha256(child, f"observation.audit_content_sha256.{key}") for key, child in sorted(_mapping(observation["audit_content_sha256"], "observation.audit_content_sha256").items())},
        "candidate_identities": identities,
        "reference_sets": indexed("reference_sets", "references", references),
        "model_states": indexed("model_states", "state", lambda child, field: _text(child, field)),
        "provider_graph_digests": indexed("provider_graph_digests", "digest", _sha256),
        "surface_contract_sha256": _sha256(observation["surface_contract_sha256"], "observation.surface_contract_sha256"),
    }
    return normalized


def compile_negative_assets(*, qualification: object, provider_audits: object,
                            source_refs: object, policy: object) -> JsonObject:
    """Reopen accepted history and compile non-promotable stress and monitor assets."""
    _reject_secrets(qualification); _reject_secrets(provider_audits); _reject_secrets(source_refs)
    normalized_policy = _validate_policy(policy)
    qualification_map = _mapping(qualification, "qualification")
    candidates = _validate_qualification(qualification_map)
    audits = _validate_audits(provider_audits, qualification_map)
    sources = _validate_sources(source_refs, inputs={
        "qualification": qualification, "provider_audit_v1": provider_audits[0], "provider_audit_v2": provider_audits[1],
    })
    if provider_audits[0].get("contract") != "lca_research_kernel.tiangong_provider_closure_audit/v1" or provider_audits[1].get("contract") != "lca_research_kernel.tiangong_provider_closure_audit/v2":
        raise ValueError("provider audits must be supplied in historical v1 then hardened v2 order")
    source_list = [sources[key] for key in ("qualification", "provider_audit_v1", "provider_audit_v2")]
    stress_cases = []
    monitor_subjects = []
    for candidate in candidates:
        candidate_ref = {key: candidate[key] for key in ("cluster_id", "process_id", "process_version")}
        stress_body = {"asset_role": "negative_authority_stress_case", "candidate_ref": candidate_ref,
                       "candidate_admission_status": "rejected", "formal_benchmark_eligible": False,
                       "expected_failed_gate": "provider_closure_complete",
                       "refusal_assertions": ["do_not_fabricate_provider", "do_not_lower_admission_criteria", "do_not_claim_zero_hit_is_global_absence"],
                       "source_refs": source_list}
        stress_cases.append({**stress_body, "asset_content_sha256": _digest(stress_body), "asset_id": _digest(stress_body)[:16]})
        monitor_body = {"asset_role": "upstream_change_monitor_subject", "candidate_ref": candidate_ref,
                        "candidate_admission_status": "rejected", "formal_benchmark_eligible": False,
                        "source_refs": source_list}
        monitor_subjects.append({**monitor_body, "asset_content_sha256": _digest(monitor_body), "asset_id": _digest(monitor_body)[:16]})
    observation = _validate_observation(
        _monitor_observation(candidates, audits, qualification_map),
        require_historical_binding=True,
    )
    monitor_fingerprint = _digest(observation)
    body: JsonObject = {
        "contract": _NEGATIVE_CONTRACT, "policy_contract": normalized_policy["contract"],
        "qualification_id": qualification_map["qualification_id"], "qualification_content_sha256": qualification_map["qualification_content_sha256"],
        "provider_audits": audits, "source_refs": source_list, "candidate_count": len(candidates),
        "cluster_counts": _EXPECTED_CLUSTER_COUNTS, "qualification_execution_status": "completed",
        "candidate_admission_status": "rejected", "positive_benchmark_member_count": 0,
        "formal_benchmark_eligible": False, "stress_cases": stress_cases, "monitor_subjects": monitor_subjects,
        "monitor_observation": observation, "monitor_fingerprint": monitor_fingerprint,
        "next_route": "validate-freshwater-benchmark-harness",
    }
    digest = _digest(body)
    return {**body, "negative_assets_content_sha256": digest, "negative_assets_id": digest[:16]}


def _compile_workspace_negative_assets(policy: Mapping[str, object]) -> JsonObject:
    """Recompile the only acceptable baseline from fixed workspace evidence."""
    inputs: JsonObject = {}
    source_refs: JsonObject = {}
    for key, (role, path) in _EXPECTED_SOURCES.items():
        resolved = (_REPO_ROOT / path).resolve()
        if _REPO_ROOT not in resolved.parents:
            raise ValueError("fixed immutable source escapes the repository")
        try:
            payload = resolved.read_bytes()
            inputs[key] = json.loads(payload.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("fixed immutable source must reopen as UTF-8 JSON") from error
        source_refs[key] = {
            "role": role,
            "path": path,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        }
    return compile_negative_assets(
        qualification=inputs["qualification"],
        provider_audits=[
            inputs["provider_audit_v1"],
            inputs["provider_audit_v2"],
        ],
        source_refs=source_refs,
        policy=policy,
    )


def _validate_negative_assets(value: object, policy: Mapping[str, object]) -> JsonObject:
    assets = _mapping(value, "negative_assets")
    required = {"contract", "policy_contract", "qualification_id", "qualification_content_sha256", "provider_audits", "source_refs", "candidate_count", "cluster_counts", "qualification_execution_status", "candidate_admission_status", "positive_benchmark_member_count", "formal_benchmark_eligible", "stress_cases", "monitor_subjects", "monitor_observation", "monitor_fingerprint", "next_route", "negative_assets_content_sha256", "negative_assets_id"}
    if set(assets) != required or assets.get("contract") != _NEGATIVE_CONTRACT or assets.get("policy_contract") != policy["contract"]:
        raise ValueError("negative assets have an invalid contract shape")
    canonical_assets = _compile_workspace_negative_assets(policy)
    if dict(assets) != canonical_assets:
        raise ValueError("negative assets do not exactly match the fixed workspace evidence")
    return canonical_assets

def _index(rows: Sequence[Mapping[str, object]], field: str) -> Dict[str, Mapping[str, object]]:
    return {_identity(row): row for row in rows}


def _classify_change_kinds(baseline_value: object, current_value: object) -> List[str]:
    """Classify content differences between two structurally valid observations."""
    baseline = _validate_observation(baseline_value)
    current = _validate_observation(current_value)
    change_kinds = []
    baseline_by_process = {
        row["process_id"]: row["process_version"]
        for row in baseline["candidate_identities"]
    }
    current_by_process = {
        row["process_id"]: row["process_version"]
        for row in current["candidate_identities"]
    }
    if set(current_by_process) - set(baseline_by_process):
        change_kinds.append("identity_added")
    if any(
        baseline_by_process[key] != current_by_process[key]
        for key in set(baseline_by_process) & set(current_by_process)
    ):
        change_kinds.append("version_changed")
    for field, added, removed in (
        ("reference_sets", "reference_added", "reference_removed"),
    ):
        base_rows = _index(baseline[field], field)
        current_rows = _index(current[field], field)
        for key in set(base_rows) & set(current_rows):
            base_refs = set(base_rows[key]["references"])
            current_refs = set(current_rows[key]["references"])
            if current_refs - base_refs:
                change_kinds.append(added)
            if base_refs - current_refs:
                change_kinds.append(removed)
    for field, kind, compared in (
        ("model_states", "state_changed", "state"),
        ("provider_graph_digests", "provider_graph_changed", "digest"),
    ):
        base_rows = _index(baseline[field], field)
        current_rows = _index(current[field], field)
        if any(
            base_rows[key][compared] != current_rows[key][compared]
            for key in set(base_rows) & set(current_rows)
        ):
            change_kinds.append(kind)
    if baseline["surface_contract_sha256"] != current["surface_contract_sha256"]:
        change_kinds.append("surface_contract_changed")
    return sorted(set(change_kinds))


def compile_update_monitor(*, negative_assets: object, current_observation: object,
                           policy: object) -> JsonObject:
    """Compare a canonical observation with the immutable monitor baseline."""
    _reject_secrets(negative_assets); _reject_secrets(current_observation)
    normalized_policy = _validate_policy(policy)
    assets = _validate_negative_assets(negative_assets, normalized_policy)
    baseline = _validate_observation(assets["monitor_observation"], require_historical_binding=True)
    current = _validate_observation(current_observation, require_historical_binding=True)
    if (current["qualification_id"] != baseline["qualification_id"] or
            current["qualification_content_sha256"] != baseline["qualification_content_sha256"] or
            current["audit_content_sha256"] != baseline["audit_content_sha256"]):
        raise ValueError("current observation must retain the immutable historical bindings")
    baseline_by_process = {row["process_id"]: row["process_version"] for row in baseline["candidate_identities"]}
    current_by_process = {row["process_id"]: row["process_version"] for row in current["candidate_identities"]}
    if set(baseline_by_process) - set(current_by_process):
        raise ValueError("current observation cannot remove immutable candidate identities")
    change_kinds = _classify_change_kinds(baseline, current)
    changed = bool(change_kinds)
    body: JsonObject = {
        "contract": _MONITOR_CONTRACT, "negative_assets_id": assets["negative_assets_id"],
        "baseline_monitor_fingerprint": assets["monitor_fingerprint"], "monitor_fingerprint": _digest(current),
        "current_observation": current, "change_status": "content_change_detected" if changed else "no_content_change",
        "change_kinds": change_kinds,
        "next_route": "run-bounded-benchmark-requalification" if changed else "skip-full-requalification-until-content-change",
    }
    digest = _digest(body)
    return {**body, "monitor_content_sha256": digest, "monitor_id": digest[:16]}


def _validate_freshwater_sources(value: object, *, inputs: Mapping[str, object]) -> JsonObject:
    refs = _mapping(value, "source_refs")
    if set(refs) != set(_EXPECTED_FRESHWATER_SOURCES):
        raise ValueError("source_refs must bind exactly the four accepted Freshwater sources")
    normalized: JsonObject = {}
    for key, (expected_role, expected_path) in _EXPECTED_FRESHWATER_SOURCES.items():
        ref = _mapping(refs[key], f"source_refs.{key}")
        if set(ref) != {"role", "path", "sha256", "bytes"}:
            raise ValueError(f"source_refs.{key} has an invalid shape")
        path = _safe_source_path(ref["path"], f"source_refs.{key}.path")
        if ref["role"] != expected_role or path != expected_path:
            raise ValueError(f"source_refs.{key} is not the required accepted Freshwater source")
        byte_count = ref["bytes"]
        if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count <= 0:
            raise ValueError(f"source_refs.{key}.bytes must be a positive integer")
        source_sha = _sha256(ref["sha256"], f"source_refs.{key}.sha256")
        expected_bytes = _EXPECTED_FRESHWATER_SOURCE_BYTES[key]
        if source_sha != expected_bytes["sha256"] or byte_count != expected_bytes["bytes"]:
            raise ValueError(f"source_refs.{key} must retain the accepted source byte identity")
        resolved = (_REPO_ROOT / path).resolve()
        if _REPO_ROOT not in resolved.parents:
            raise ValueError(f"source_refs.{key}.path escapes the workspace")
        try:
            payload = resolved.read_bytes()
            reopened = json.loads(payload.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"source_refs.{key} must reopen a UTF-8 JSON source") from error
        if (len(payload) != expected_bytes["bytes"] or
                hashlib.sha256(payload).hexdigest() != expected_bytes["sha256"]):
            raise ValueError(f"source_refs.{key} source bytes do not match the pinned SHA")
        if reopened != inputs[key]:
            raise ValueError(f"source_refs.{key} does not reopen the supplied accepted input")
        normalized[key] = {
            "role": expected_role, "path": path, "sha256": source_sha, "bytes": byte_count,
        }
    return normalized


def _exact_mapping(value: object, field: str) -> JsonObject:
    return dict(_mapping(value, field))


def _verify_freshwater_acceptance(value: object) -> JsonObject:
    acceptance = _exact_mapping(value, "acceptance")
    if (acceptance.get("contract") != "lca_research_kernel.tiangong_second_case_acceptance/v1" or
            acceptance.get("status") != "accepted"):
        raise ValueError("acceptance is not an accepted TianGong second-case acceptance")
    body = {key: child for key, child in acceptance.items()
            if key not in {"acceptance_content_sha256", "acceptance_id"}}
    digest = _digest(body)
    if (acceptance.get("acceptance_content_sha256") != digest or
            acceptance.get("acceptance_id") != digest[:16]):
        raise ValueError("acceptance identity does not match its governed content")
    return acceptance


def _validate_freshwater_harness_inputs(*, receipt: object, selection: object,
                                        native_binding: object, acceptance: object,
                                        source_refs: object, policy: object) -> JsonObject:
    for value in (receipt, selection, native_binding, acceptance, source_refs):
        _reject_secrets(value)
    normalized_policy = _validate_policy(policy)
    sources = _validate_freshwater_sources(source_refs, inputs={
        "receipt": receipt, "selection": selection, "native_binding": native_binding,
        "acceptance": acceptance,
    })
    receipt_map = verify_authority_receipt(_mapping(receipt, "receipt"))
    selection_map = _verify_selection(_mapping(selection, "selection"))
    binding = verify_native_case_binding(
        _mapping(native_binding, "native_binding"), receipt_map
    )
    acceptance_map = _verify_freshwater_acceptance(acceptance)
    expected = _EXPECTED_FRESHWATER_IDENTITIES
    if (receipt_map.get("contract") != "lca_research_kernel.tiangong_authority_receipt/v1" or
            receipt_map.get("status") != "accepted_immutable_authority_receipt" or
            receipt_map.get("receipt_id") != expected["receipt_id"] or
            receipt_map.get("receipt_content_sha256") != expected["receipt_content_sha256"]):
        raise ValueError("receipt is not the accepted Freshwater Authority Receipt")
    if (selection_map.get("contract") != "lca_research_kernel.tiangong_open_data_case_selection/v1" or
            selection_map.get("status") != "selected" or
            selection_map.get("selection_id") != expected["selection_id"]):
        raise ValueError("selection is not the accepted Freshwater selection")
    if (binding.get("contract") != "lca_research_kernel.tiangong_native_case_binding/v1" or
            binding.get("status") != "accepted_tiangong_native_case_binding" or
            binding.get("binding_id") != expected["binding_id"] or
            binding.get("binding_content_sha256") != expected["binding_content_sha256"] or
            binding.get("authority_receipt_id") != expected["receipt_id"] or
            binding.get("authority_receipt_sha256") != expected["receipt_content_sha256"] or
            binding.get("selection_id") != expected["selection_id"]):
        raise ValueError("native binding is not the accepted Freshwater binding")
    if (acceptance_map.get("contract") != "lca_research_kernel.tiangong_second_case_acceptance/v1" or
            acceptance_map.get("status") != "accepted" or
            acceptance_map.get("acceptance_id") != expected["acceptance_id"] or
            acceptance_map.get("acceptance_content_sha256") != expected["acceptance_content_sha256"] or
            acceptance_map.get("byte_for_byte_rebuild") is not True or
            acceptance_map.get("source_project_tree_unchanged") is not True):
        raise ValueError("acceptance is not the accepted Freshwater clean acceptance")
    for field in ("remote_write_executed_count", "project_write_executed_count",
                  "recalculation_executed_count", "publication_executed_count",
                  "review_submission_executed_count", "contact_executed_count",
                  "message_executed_count", "dialogue_or_warm_cold_context_copied_count"):
        if acceptance_map.get(field) != 0:
            raise ValueError("Freshwater acceptance must retain its zero-side-effect boundary")

    selected = _exact_mapping(selection_map.get("selected_candidate"), "selection.selected_candidate")
    authority = _exact_mapping(receipt_map.get("authority"), "receipt.authority")
    model = _exact_mapping(binding.get("model"), "native_binding.model")
    scope = _exact_mapping(binding.get("scope"), "native_binding.scope")
    if selected.get("candidate_id") != binding.get("candidate_id"):
        raise ValueError("selection and native binding candidate identities must join exactly")
    functional_unit = _exact_mapping(selected.get("functional_unit"), "selected functional_unit")
    reference_flow = _exact_mapping(selected.get("reference_flow"), "selected reference_flow")
    selected_provider = _exact_mapping(selected.get("provider_snapshot"), "selected provider snapshot")
    selected_method = _exact_mapping(selected.get("lcia_method"), "selected LCIA method")
    for name, expected_identity, observed in (
        ("dataset", selected.get("dataset"), authority.get("dataset")),
        ("dataset", selected.get("dataset"), model.get("dataset")),
        ("model", selected.get("model"), authority.get("model")),
        ("model", selected.get("model"), model.get("identity")),
        ("functional_unit", functional_unit, authority.get("functional_unit")),
        ("functional_unit", functional_unit, scope.get("functional_unit")),
        ("reference_flow", reference_flow, authority.get("reference_flow")),
        ("reference_flow", reference_flow, scope.get("reference_flow")),
        ("provider_snapshot", {key: selected_provider[key] for key in ("id", "version")}, binding.get("provider_snapshot")),
        ("lcia_method", selected_method, authority.get("lcia_method")),
        ("lcia_method", selected_method, binding.get("lcia_method")),
    ):
        observed_map = _exact_mapping(observed, f"Freshwater {name} observed identity")
        if any(observed_map.get(key) != expected_value for key, expected_value in _mapping(expected_identity, f"Freshwater {name} expected identity").items()):
            raise ValueError(f"Freshwater {name} identity does not join across accepted sources")
    if functional_unit != {"amount": "1.0", "unit": "m3"} or set(reference_flow) != {"id", "version"}:
        raise ValueError("Freshwater functional unit must be exact 1.0 m3 with an exact reference Flow")
    if authority["functional_unit"].get("reference_flow_id") != reference_flow["id"]:
        raise ValueError("receipt functional unit must bind the exact reference Flow")
    nodes = _sequence(model.get("nodes"), "native_binding.model.nodes")
    edges = _sequence(model.get("edges"), "native_binding.model.edges")
    if len(nodes) != 64 or len(set(nodes)) != 64 or binding["candidate_id"] not in nodes:
        raise ValueError("Freshwater native binding must contain exactly 64 unique nodes including the root")
    if len(edges) != 63:
        raise ValueError("Freshwater native binding must contain exactly 63 provider edges")
    node_set = set(nodes)
    for index, raw_edge in enumerate(edges):
        edge = _mapping(raw_edge, f"native_binding.model.edges[{index}]")
        if set(edge) != {"consumer", "provider", "flow", "connected"} or edge.get("connected") is not True:
            raise ValueError("Freshwater provider edge must remain exactly connected")
        if edge.get("consumer") not in node_set or edge.get("provider") not in node_set:
            raise ValueError("Freshwater provider edge must join exact model nodes")
    native_result = _exact_mapping(binding.get("native_result"), "native_binding.native_result")
    primary_method = _exact_mapping(native_result.get("primary_method"), "native_result.primary_method")
    if primary_method != selected["lcia_method"]:
        raise ValueError("native result primary method must bind the selected exact LCIA method")
    categories = _sequence(native_result.get("categories"), "native_result.categories")
    category_ids = []
    if len(categories) != 25:
        raise ValueError("Freshwater native result must bind exactly 25 LCIA categories")
    for index, raw_category in enumerate(categories):
        category = _mapping(raw_category, f"native_result.categories[{index}]")
        if (set(category) < {"method_id", "method_version", "original_value", "original_unit", "validation_status"} or
                category.get("validation_status") != "exactly_bound"):
            raise ValueError("Freshwater LCIA category must retain an exact native binding")
        category_ids.append((_uuid(category.get("method_id"), "native category method_id"),
                             _version(category.get("method_version"), "native category method_version")))
    if len(set(category_ids)) != 25 or tuple(primary_method.values()) not in set(category_ids):
        raise ValueError("Freshwater LCIA category identities must be unique and include the primary method")
    if binding.get("validation") != {"status": "passed", "native_lcia_calculated_by_kernel": False,
                                     "external_authority_substituted": False}:
        raise ValueError("Freshwater native validation must remain native and passed")
    return {
        "policy": normalized_policy, "sources": sources, "receipt": receipt_map,
        "selection": selection_map, "binding": binding, "acceptance": acceptance_map,
        "selected": selected,
    }


def compile_freshwater_harness_canary(*, receipt: object, selection: object,
                                      native_binding: object, acceptance: object,
                                      source_refs: object, policy: object) -> JsonObject:
    """Reopen the accepted Freshwater case as a non-promotable Harness canary."""
    validated = _validate_freshwater_harness_inputs(
        receipt=receipt, selection=selection, native_binding=native_binding,
        acceptance=acceptance, source_refs=source_refs, policy=policy,
    )
    selected = validated["selected"]
    binding = validated["binding"]
    source_list = [validated["sources"][key] for key in (
        "selection", "receipt", "native_binding", "acceptance",
    )]
    body: JsonObject = {
        "contract": _FRESHWATER_CANARY_CONTRACT,
        "policy_contract": validated["policy"]["contract"],
        "asset_role": "harness_canary",
        "candidate_admission_status": "not_applicable",
        "formal_benchmark_eligible": False,
        "formal_benchmark_ineligibility_reason": "harness_canary_not_competing_cluster",
        "harness_valid": True,
        "selection_id": validated["selection"]["selection_id"],
        "receipt_id": validated["receipt"]["receipt_id"],
        "native_binding_id": binding["binding_id"],
        "acceptance_id": validated["acceptance"]["acceptance_id"],
        "candidate_id": selected["candidate_id"],
        "functional_unit": copy.deepcopy(selected["functional_unit"]),
        "reference_flow": copy.deepcopy(selected["reference_flow"]),
        "provider_node_count": len(binding["model"]["nodes"]),
        "provider_edge_count": len(binding["model"]["edges"]),
        "native_lcia_category_count": len(binding["native_result"]["categories"]),
        "source_refs": source_list,
        "next_route": "capture-tiangong-lifecyclemodel-surface",
    }
    digest = _digest(body)
    if (digest != _EXPECTED_FRESHWATER_CANARY_CONTENT_SHA256 or
            digest[:16] != _EXPECTED_FRESHWATER_CANARY_ID):
        raise ValueError("Freshwater canary content has drifted from the accepted authority")
    return {**body, "canary_content_sha256": digest, "canary_id": digest[:16]}


def validate_freshwater_harness_canary(*, canary: object) -> JsonObject:
    """Reject any attempt to re-label or promote a compiled Harness canary."""
    _reject_secrets(canary)
    value = _exact_mapping(canary, "canary")
    required = {
        "contract", "policy_contract", "asset_role", "candidate_admission_status",
        "formal_benchmark_eligible", "formal_benchmark_ineligibility_reason", "harness_valid",
        "selection_id", "receipt_id", "native_binding_id", "acceptance_id", "candidate_id",
        "functional_unit", "reference_flow", "provider_node_count", "provider_edge_count",
        "native_lcia_category_count", "source_refs", "next_route", "canary_content_sha256", "canary_id",
    }
    if set(value) != required:
        raise ValueError("Freshwater canary has an invalid contract shape")
    body = {key: child for key, child in value.items() if key not in {"canary_content_sha256", "canary_id"}}
    digest = _digest(body)
    if value["canary_content_sha256"] != digest or value["canary_id"] != digest[:16]:
        raise ValueError("Freshwater canary identity does not match content")
    immutable = {
        "contract": _FRESHWATER_CANARY_CONTRACT,
        "asset_role": "harness_canary",
        "candidate_admission_status": "not_applicable",
        "formal_benchmark_eligible": False,
        "formal_benchmark_ineligibility_reason": "harness_canary_not_competing_cluster",
        "harness_valid": True,
        "selection_id": _EXPECTED_FRESHWATER_IDENTITIES["selection_id"],
        "receipt_id": _EXPECTED_FRESHWATER_IDENTITIES["receipt_id"],
        "native_binding_id": _EXPECTED_FRESHWATER_IDENTITIES["binding_id"],
        "acceptance_id": _EXPECTED_FRESHWATER_IDENTITIES["acceptance_id"],
        "provider_node_count": 64,
        "provider_edge_count": 63,
        "native_lcia_category_count": 25,
        "next_route": "capture-tiangong-lifecyclemodel-surface",
        "canary_content_sha256": _EXPECTED_FRESHWATER_CANARY_CONTENT_SHA256,
        "canary_id": _EXPECTED_FRESHWATER_CANARY_ID,
    }
    if any(value.get(key) != expected for key, expected in immutable.items()):
        raise ValueError("Freshwater canary cannot be promoted or structurally altered")
    inputs = _workspace_freshwater_inputs()
    canonical = compile_freshwater_harness_canary(
        receipt=inputs["receipt"], selection=inputs["selection"],
        native_binding=inputs["native_binding"], acceptance=inputs["acceptance"],
        source_refs=inputs["source_refs"],
        policy=json.loads(
            (_REPO_ROOT / "cases/research-council-benchmark-portfolio-policy.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    if value != canonical:
        raise ValueError("Freshwater canary does not exactly match reopened workspace authority")
    return canonical


def _workspace_freshwater_inputs() -> JsonObject:
    inputs: JsonObject = {}
    refs: JsonObject = {}
    for key, (role, path) in _EXPECTED_FRESHWATER_SOURCES.items():
        resolved = (_REPO_ROOT / path).resolve()
        try:
            payload = resolved.read_bytes()
            inputs[key] = json.loads(payload.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("accepted Freshwater source must reopen as UTF-8 JSON") from error
        expected_bytes = _EXPECTED_FRESHWATER_SOURCE_BYTES[key]
        if (len(payload) != expected_bytes["bytes"] or
                hashlib.sha256(payload).hexdigest() != expected_bytes["sha256"]):
            raise ValueError("accepted Freshwater source bytes have drifted")
        refs[key] = {
            "role": role, "path": path, "sha256": expected_bytes["sha256"],
            "bytes": expected_bytes["bytes"],
        }
    inputs["source_refs"] = refs
    return inputs


def materialize_freshwater_harness_canary(*, output_path: Path) -> JsonObject:
    """Write a canonical canary once, refusing every existing destination."""
    target = Path(output_path)
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"refusing to overwrite existing canary: {target}")
    inputs = _workspace_freshwater_inputs()
    policy_path = _REPO_ROOT / "cases/research-council-benchmark-portfolio-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    canary = compile_freshwater_harness_canary(
        receipt=inputs["receipt"], selection=inputs["selection"],
        native_binding=inputs["native_binding"], acceptance=inputs["acceptance"],
        source_refs=inputs["source_refs"], policy=policy,
    )
    payload = _canonical_json(canary)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
    except BaseException:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        raise
    return canary


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile the immutable Freshwater Harness canary")
    command = parser.add_subparsers(dest="command", required=True)
    materialize = command.add_parser("materialize-freshwater-canary")
    materialize.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        canary = materialize_freshwater_harness_canary(output_path=arguments.output)
    except (OSError, ValueError, FileExistsError) as error:
        parser.error(str(error))
    print(json.dumps({"canary_id": canary["canary_id"], "canary_content_sha256": canary["canary_content_sha256"]}, sort_keys=True))
    return 0


__all__ = [
    "compile_negative_assets", "compile_update_monitor",
    "compile_freshwater_harness_canary", "validate_freshwater_harness_canary",
    "materialize_freshwater_harness_canary",
]


if __name__ == "__main__":
    raise SystemExit(_main())
