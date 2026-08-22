"""Compile current content-addressed evidence observations into one typed ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


JsonObject = Dict[str, object]
LEDGER_CONTRACT = "lca_research_kernel.current_evidence_candidate_ledger/v1"
MANIFEST_CONTRACT = "lca_research_kernel.evidence_acquisition_manifest/v1"
HARNESS_CONTRACT = "lca_research_kernel.evidence_intake_validation_harness/v1"
SOURCE_CONTRACTS = {
    "public_discovery": (
        "lca_research_kernel.public_evidence_discovery/v1",
        "public_discovery_completed_degraded",
    ),
    "source_expansion": (
        "lca_research_kernel.public_source_expansion/v1",
        "public_source_expansion_completed_no_candidate",
    ),
    "author_institution": (
        "lca_research_kernel.author_institution_public_routes/v1",
        "author_institution_routes_completed_degraded",
    ),
    "record_export": (
        "lca_research_kernel.exact_repository_record_export_probe/v1",
        "record_export_probe_completed_metadata_only",
    ),
    "korean_sources": (
        "lca_research_kernel.korean_scholarly_source_routes/v1",
        "korean_source_routes_completed_metadata_only",
    ),
    "author_hosted": (
        "lca_research_kernel.author_hosted_public_file_routes/v1",
        "author_hosted_routes_completed_non_target_files",
    ),
}
POLICY_GATE_IDS = [
    "candidate-contract-and-secret-hygiene",
    "artifact-integrity",
    "candidate-kind-and-document-role",
    "lawful-access-and-attribution",
    "target-continuity",
    "field-scope",
    "source-locator",
    "lossless-field-payload",
]


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _binding(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an artifact binding")
    path = _required_text(value.get("path"), f"{label}.path")
    sha256 = _required_text(value.get("sha256"), f"{label}.sha256")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError(f"{label}.sha256 must be a lowercase sha256")
    return {"path": path, "sha256": sha256}


def _scope(document: JsonObject, label: str) -> Tuple[str, str]:
    return (
        _required_text(document.get("scope_id"), f"{label}.scope_id"),
        _required_text(document.get("revision"), f"{label}.revision"),
    )


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _hash(value: object) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _sha_values(value: object) -> List[str]:
    candidates: List[object]
    if isinstance(value, list):
        candidates = value
    else:
        candidates = [value]
    result = []
    for candidate in candidates:
        if isinstance(candidate, str) and re.fullmatch(r"[0-9a-f]{64}", candidate):
            result.append(candidate)
    return sorted(set(result))


def _entry(
    *,
    source_key: str,
    source_binding: JsonObject,
    pointer: str,
    target_ids: Iterable[str],
    observation_class: str,
    nested_sha256: Iterable[str] = (),
    nested_bytes: Optional[int] = None,
    exact_target_identity: bool = False,
    document_role: Optional[str] = None,
    target_occurrence_count: int = 0,
    occurrence_semantics: Optional[str] = None,
    route_id: Optional[str] = None,
    route_state: Optional[str] = None,
    disposition: str,
    reason_codes: Iterable[str],
) -> JsonObject:
    targets = sorted(set(_required_text(value, "target_id") for value in target_ids))
    nested = sorted(set(nested_sha256))
    source = {
        "report_key": source_key,
        "report_path": source_binding["path"],
        "report_sha256": source_binding["sha256"],
        "pointer": pointer,
    }
    occurrence_identity = {
        "source": source,
        "target_ids": targets,
        "observation_class": observation_class,
        "nested_sha256": nested,
        "document_role": document_role,
        "route_id": route_id,
        "route_state": route_state,
    }
    group_identity = {
        "target_ids": targets,
        "observation_class": observation_class,
        "nested_sha256": nested,
        "document_role": document_role,
        "route_id": route_id,
        "route_state": route_state,
    }
    return {
        "entry_id": _hash(occurrence_identity),
        "group_fingerprint": _hash(group_identity),
        "source": source,
        "observation_class": observation_class,
        "integrity": {
            "upstream_report_binding_verified": True,
            "nested_sha256": nested,
            "nested_bytes": nested_bytes,
        },
        "identity_and_role": {
            "target_ids": targets,
            "exact_target_identity": exact_target_identity,
            "document_role": document_role,
            "target_occurrence_count": target_occurrence_count,
            "occurrence_semantics": occurrence_semantics,
        },
        "route_state": {
            "route_id": route_id,
            "status": route_state,
        },
        "policy": {
            "disposition": disposition,
            "reason_codes": list(dict.fromkeys(reason_codes)),
            "quarantined_candidate": False,
            "evidence_admission_executed": False,
        },
        "scientific_absence_proven": False,
        "scientific_field_evidence_admitted": False,
    }


def _metadata_entry(
    source_key: str,
    binding: JsonObject,
    pointer: str,
    target_id: str,
    role: str,
    hashes: Iterable[str],
    nested_bytes: Optional[int] = None,
) -> JsonObject:
    return _entry(
        source_key=source_key,
        source_binding=binding,
        pointer=pointer,
        target_ids=[target_id],
        observation_class="exact_identity_metadata_observation",
        nested_sha256=hashes,
        nested_bytes=nested_bytes,
        exact_target_identity=True,
        document_role=role,
        disposition="rejected_non_candidate_document",
        reason_codes=[
            "document_role_not_admissible",
            "field_evidence_missing",
            "source_locator_incomplete",
        ],
    )


def _route_entry(
    source_key: str,
    binding: JsonObject,
    pointer: str,
    target_ids: Iterable[str],
    route_id: str,
    route_state: str,
    reason_code: str,
    hashes: Iterable[str] = (),
) -> JsonObject:
    return _entry(
        source_key=source_key,
        source_binding=binding,
        pointer=pointer,
        target_ids=target_ids,
        observation_class="route_state_observation",
        nested_sha256=hashes,
        route_id=route_id,
        route_state=route_state,
        disposition="not_candidate_route_state",
        reason_codes=[reason_code],
    )


def _validate_source(
    key: str, document: JsonObject, contract: str, status: str
) -> None:
    if (
        document.get("contract") != contract
        or document.get("status") != status
        or document.get("lawful_attachment_candidate_count") != 0
        or document.get("scientific_field_evidence_admitted") is not False
        or document.get("scientific_comparison_ready") is not False
        or document.get("comparative_lcia_allowed") is not False
        or document.get("scientific_absence_proven") is True
    ):
        raise ValueError(
            f"{key} must retain zero lawful attachment candidates and no scientific admission"
        )


def _adapt_public(
    report: JsonObject, binding: JsonObject, target_ids: set[str]
) -> Tuple[List[JsonObject], JsonObject]:
    entries = []
    metadata_count = no_hit_count = degraded_count = 0
    results = report.get("target_results")
    if not isinstance(results, list):
        raise ValueError("public discovery target results are required")
    for target_index, target in enumerate(results):
        if not isinstance(target, dict):
            raise ValueError("public discovery target result is malformed")
        target_id = _required_text(target.get("target_id"), "public target_id")
        if target_id not in target_ids:
            raise ValueError("public discovery references an unknown target")
        routes = target.get("route_results")
        if not isinstance(routes, list):
            raise ValueError("public discovery route results are required")
        for route_index, route in enumerate(routes):
            if not isinstance(route, dict):
                raise ValueError("public route result is malformed")
            pointer = f"/target_results/{target_index}/route_results/{route_index}"
            route_id = _required_text(route.get("route_id"), "public route_id")
            hashes = _sha_values(route.get("response_sha256"))
            if route.get("status") == "metadata_identity_verified":
                entries.append(
                    _metadata_entry(
                        "public_discovery",
                        binding,
                        pointer,
                        target_id,
                        "bibliographic_metadata",
                        hashes,
                    )
                )
                metadata_count += 1
            elif route.get("status") == "route_failed":
                entries.append(
                    _route_entry(
                        "public_discovery",
                        binding,
                        pointer,
                        [target_id],
                        route_id,
                        "degraded_route",
                        "route_degraded_not_artifact",
                        hashes,
                    )
                )
                degraded_count += 1
            elif route.get("no_hit") is True:
                entries.append(
                    _route_entry(
                        "public_discovery",
                        binding,
                        pointer,
                        [target_id],
                        route_id,
                        "dated_no_hit",
                        "route_no_hit_not_artifact",
                        hashes,
                    )
                )
                no_hit_count += 1
    if (metadata_count, no_hit_count, degraded_count) != (2, 4, 2):
        raise ValueError("public discovery ledger counts drifted")
    return entries, {
        "adapter_id": "public-discovery",
        "emitted_entry_count": len(entries),
        "metadata_count": metadata_count,
        "dated_no_hit_count": no_hit_count,
        "degraded_route_count": degraded_count,
    }


def _adapt_expansion(
    report: JsonObject, binding: JsonObject, target_ids: set[str]
) -> Tuple[List[JsonObject], JsonObject]:
    entries = []
    metadata_count = no_hit_count = 0
    results = report.get("target_results")
    if not isinstance(results, list):
        raise ValueError("source expansion target results are required")
    for target_index, target in enumerate(results):
        if not isinstance(target, dict):
            raise ValueError("source expansion target result is malformed")
        target_id = _required_text(target.get("target_id"), "expansion target_id")
        if target_id not in target_ids:
            raise ValueError("source expansion references an unknown target")
        routes = target.get("route_results")
        if not isinstance(routes, list):
            raise ValueError("source expansion route results are required")
        for route_index, route in enumerate(routes):
            if not isinstance(route, dict):
                raise ValueError("source expansion route result is malformed")
            pointer = f"/target_results/{target_index}/route_results/{route_index}"
            route_id = _required_text(route.get("route_id"), "expansion route_id")
            hashes = _sha_values(route.get("response_sha256"))
            exact = route.get("exact_identity_records")
            if isinstance(exact, list) and exact:
                entries.append(
                    _metadata_entry(
                        "source_expansion",
                        binding,
                        pointer,
                        target_id,
                        "research_graph_metadata",
                        hashes + _sha_values(route.get("metadata_abstract_sha256")),
                    )
                )
                metadata_count += 1
            if route.get("no_hit") is True:
                entries.append(
                    _route_entry(
                        "source_expansion",
                        binding,
                        pointer,
                        [target_id],
                        route_id,
                        "dated_no_hit",
                        "route_no_hit_not_artifact",
                        hashes,
                    )
                )
                no_hit_count += 1
    if (metadata_count, no_hit_count) != (2, 6):
        raise ValueError("source expansion ledger counts drifted")
    return entries, {
        "adapter_id": "public-source-expansion",
        "emitted_entry_count": len(entries),
        "metadata_count": metadata_count,
        "dated_no_hit_count": no_hit_count,
    }


def _adapt_author(
    report: JsonObject, binding: JsonObject, target_ids: set[str]
) -> Tuple[List[JsonObject], JsonObject]:
    rows = report.get("exact_repository_records")
    if not isinstance(rows, list):
        raise ValueError("exact repository records are required")
    entries = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("exact repository record is malformed")
        target_id = _required_text(row.get("target_id"), "repository target_id")
        if target_id not in target_ids or row.get("lawful_attachment_candidates") != []:
            raise ValueError("exact repository metadata record crossed candidate boundary")
        entries.append(
            _metadata_entry(
                "author_institution",
                binding,
                f"/exact_repository_records/{index}",
                target_id,
                "repository_metadata_record",
                _sha_values(row.get("metadata_abstract_sha256")),
            )
        )
    if len(entries) != 2:
        raise ValueError("two exact repository metadata records are required")
    return entries, {
        "adapter_id": "author-institution-routes",
        "emitted_entry_count": 2,
        "metadata_count": 2,
    }


def _adapt_record(
    report: JsonObject, binding: JsonObject, target_ids: set[str]
) -> Tuple[List[JsonObject], JsonObject]:
    rows = report.get("record_results")
    if not isinstance(rows, list):
        raise ValueError("record export results are required")
    entries = []
    metadata_count = oai_count = 0
    for record_index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("record export result is malformed")
        target_id = _required_text(row.get("target_id"), "record target_id")
        if target_id not in target_ids or row.get("public_bitstream_candidates") != []:
            raise ValueError("record export crossed public bitstream boundary")
        exports = row.get("metadata_exports")
        if not isinstance(exports, list):
            raise ValueError("metadata exports are required")
        for export_index, export in enumerate(exports):
            if not isinstance(export, dict) or export.get("status") != "metadata_export_exact":
                raise ValueError("metadata export is malformed")
            entries.append(
                _metadata_entry(
                    "record_export",
                    binding,
                    f"/record_results/{record_index}/metadata_exports/{export_index}",
                    target_id,
                    f"repository_metadata_export:{export.get('format')}",
                    _sha_values(export.get("response_sha256")),
                    export.get("response_bytes")
                    if isinstance(export.get("response_bytes"), int)
                    else None,
                )
            )
            metadata_count += 1
        oai = row.get("oai_results")
        if not isinstance(oai, list):
            raise ValueError("OAI results are required")
        for oai_index, result in enumerate(oai):
            if (
                not isinstance(result, dict)
                or result.get("status") != "oai_semantic_error"
                or result.get("record_available") is not False
            ):
                raise ValueError("OAI semantic result is malformed")
            entries.append(
                _route_entry(
                    "record_export",
                    binding,
                    f"/record_results/{record_index}/oai_results/{oai_index}",
                    [target_id],
                    _required_text(result.get("route_key"), "OAI route_key"),
                    "oai_semantic_error",
                    "protocol_error_not_artifact",
                )
            )
            oai_count += 1
    if (metadata_count, oai_count) != (6, 4):
        raise ValueError("record export ledger counts drifted")
    return entries, {
        "adapter_id": "exact-record-export-probe",
        "emitted_entry_count": len(entries),
        "metadata_count": metadata_count,
        "oai_semantic_error_count": oai_count,
    }


def _adapt_korean(
    report: JsonObject, binding: JsonObject, target_ids: set[str]
) -> Tuple[List[JsonObject], JsonObject]:
    entries = []
    metadata_count = credential_count = scope_count = 0
    records = report.get("record_results")
    if not isinstance(records, list):
        raise ValueError("Korean exact record results are required")
    for index, row in enumerate(records):
        if (
            not isinstance(row, dict)
            or row.get("status") != "exact_cris_record_metadata_only"
            or row.get("public_document_candidates") != []
        ):
            raise ValueError("Korean exact metadata record crossed candidate boundary")
        target_id = _required_text(row.get("target_id"), "CRIS target_id")
        if target_id not in target_ids:
            raise ValueError("CRIS record references an unknown target")
        entries.append(
            _metadata_entry(
                "korean_sources",
                binding,
                f"/record_results/{index}",
                target_id,
                "cris_metadata_record",
                _sha_values(row.get("metadata_abstract_sha256")),
            )
        )
        metadata_count += 1
    registry = report.get("source_registry")
    if not isinstance(registry, list):
        raise ValueError("Korean source registry is required")
    for index, route in enumerate(registry):
        if not isinstance(route, dict):
            raise ValueError("Korean source route is malformed")
        route_status = route.get("route_status")
        if route_status not in {"credential_required", "scope_inapplicable"}:
            continue
        route_id = _required_text(route.get("route_id"), "Korean route_id")
        targets = route.get("target_ids")
        if not isinstance(targets, list) or not set(targets).issubset(target_ids):
            raise ValueError("Korean source route target binding is invalid")
        reason = (
            "credential_reference_not_evidence"
            if route_status == "credential_required"
            else "scope_inapplicable_not_evidence"
        )
        entries.append(
            _route_entry(
                "korean_sources",
                binding,
                f"/source_registry/{index}",
                targets,
                route_id,
                str(route_status),
                reason,
            )
        )
        if route_status == "credential_required":
            credential_count += 1
        else:
            scope_count += 1
    if (metadata_count, credential_count, scope_count) != (2, 2, 2):
        raise ValueError("Korean source ledger counts drifted")
    return entries, {
        "adapter_id": "korean-source-routes",
        "emitted_entry_count": len(entries),
        "metadata_count": metadata_count,
        "credential_required_count": credential_count,
        "scope_inapplicable_count": scope_count,
    }


def _adapt_hosted(
    report: JsonObject, binding: JsonObject, target_ids: set[str]
) -> Tuple[List[JsonObject], JsonObject]:
    observations = report.get("observations")
    if not isinstance(observations, list):
        raise ValueError("author-hosted observations are required")
    entries = []
    binary_count = citation_count = 0
    for index, row in enumerate(observations):
        if (
            not isinstance(row, dict)
            or row.get("status") != "verified_non_target_document"
            or row.get("lawful_attachment_candidates") != []
            or row.get("scientific_field_evidence_admitted") is not False
            or row.get("scientific_absence_proven") is not False
        ):
            raise ValueError("author-hosted non-target boundary drifted")
        targets = row.get("target_ids")
        if not isinstance(targets, list) or not set(targets).issubset(target_ids):
            raise ValueError("author-hosted target binding is invalid")
        role = _required_text(row.get("document_role"), "document_role")
        binary = row.get("binary_file_ref")
        hashes = _sha_values(row.get("response_sha256"))
        nested_bytes = row.get("response_bytes") if isinstance(row.get("response_bytes"), int) else None
        if isinstance(binary, dict):
            hashes += _sha_values(binary.get("sha256"))
            if binary.get("materialized") is not True:
                raise ValueError("hosted binary must remain materialized")
            binary_count += 1
        occurrences = row.get("target_occurrences")
        occurrences = occurrences if isinstance(occurrences, list) else []
        count = row.get("target_occurrence_count")
        if not isinstance(count, int) or count != len(occurrences):
            raise ValueError("target occurrence count drifted")
        citation_count += count
        entries.append(
            _entry(
                source_key="author_hosted",
                source_binding=binding,
                pointer=f"/observations/{index}",
                target_ids=targets,
                observation_class="verified_non_target_document",
                nested_sha256=hashes,
                nested_bytes=nested_bytes,
                exact_target_identity=False,
                document_role=role,
                target_occurrence_count=count,
                occurrence_semantics=row.get("occurrence_semantics")
                if isinstance(row.get("occurrence_semantics"), str)
                else None,
                route_id=row.get("route_id") if isinstance(row.get("route_id"), str) else None,
                route_state="verified_non_target_document",
                disposition="rejected_non_candidate_document",
                reason_codes=[
                    "document_role_not_admissible",
                    "field_evidence_missing",
                    "source_locator_incomplete",
                    _required_text(row.get("rejection_reason"), "rejection_reason"),
                ],
            )
        )
    if (len(entries), binary_count, citation_count) != (4, 2, 2):
        raise ValueError("author-hosted ledger counts drifted")
    return entries, {
        "adapter_id": "author-hosted-files",
        "emitted_entry_count": len(entries),
        "verified_non_target_document_count": len(entries),
        "binary_file_observation_count": binary_count,
        "citation_only_occurrence_count": citation_count,
    }


def compile_current_evidence_candidate_ledger(
    manifest: JsonObject,
    manifest_binding: JsonObject,
    harness: JsonObject,
    harness_binding: JsonObject,
    public_discovery: JsonObject,
    public_binding: JsonObject,
    source_expansion: JsonObject,
    expansion_binding: JsonObject,
    author_institution: JsonObject,
    author_binding: JsonObject,
    record_export: JsonObject,
    record_binding: JsonObject,
    korean_sources: JsonObject,
    korean_binding: JsonObject,
    author_hosted: JsonObject,
    hosted_binding: JsonObject,
) -> JsonObject:
    """Normalize all current evidence observations without performing new effects."""

    if (
        manifest.get("contract") != MANIFEST_CONTRACT
        or manifest.get("status") != "lawful_evidence_acquisition_manifest_compiled"
        or manifest.get("source_target_count") != 2
        or manifest.get("values_invented") is not False
        or manifest.get("scientific_comparison_ready") is not False
        or manifest.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("accepted evidence manifest is required")
    if (
        harness.get("contract") != HARNESS_CONTRACT
        or harness.get("status") != "evidence_intake_validation_harness_compiled"
        or harness.get("target_count") != 2
        or harness.get("field_contract_count") != 15
        or harness.get("policy_gate_count") != 8
        or harness.get("candidate_contract", {}).get("automatic_admission") is not False
        or harness.get("evidence_admission_executed_count") != 0
        or harness.get("model_mutation_executed_count") != 0
        or harness.get("routing", {}).get("next_internal_route")
        != "evaluate-current-evidence-candidate-ledger"
        or harness.get("routing", {}).get("pause_internal_work") is not False
    ):
        raise ValueError("accepted evidence intake harness is required")
    gates = harness.get("policy_gates")
    if (
        not isinstance(gates, list)
        or [row.get("gate_id") for row in gates if isinstance(row, dict)]
        != POLICY_GATE_IDS
    ):
        raise ValueError("eight accepted intake policy gates are required")

    documents = {
        "public_discovery": public_discovery,
        "source_expansion": source_expansion,
        "author_institution": author_institution,
        "record_export": record_export,
        "korean_sources": korean_sources,
        "author_hosted": author_hosted,
    }
    for key, document in documents.items():
        contract, status = SOURCE_CONTRACTS[key]
        _validate_source(key, document, contract, status)
    scopes = {
        _scope(manifest, "manifest"),
        _scope(harness, "harness"),
        *(_scope(document, key) for key, document in documents.items()),
    }
    if len(scopes) != 1:
        raise ValueError("candidate ledger source scope conflicts")
    scope_id, revision = next(iter(scopes))
    bindings = {
        "evidence_manifest": _binding(manifest_binding, "manifest binding"),
        "intake_harness": _binding(harness_binding, "harness binding"),
        "public_discovery": _binding(public_binding, "public binding"),
        "source_expansion": _binding(expansion_binding, "expansion binding"),
        "author_institution": _binding(author_binding, "author binding"),
        "record_export": _binding(record_binding, "record binding"),
        "korean_sources": _binding(korean_binding, "Korean binding"),
        "author_hosted": _binding(hosted_binding, "hosted binding"),
    }

    target_rows = manifest.get("source_targets")
    if not isinstance(target_rows, list) or len(target_rows) != 2:
        raise ValueError("two target manifests are required")
    target_ids = {
        _required_text(row.get("target_id"), "target_id")
        for row in target_rows
        if isinstance(row, dict)
    }
    fields = {
        _required_text(field.get("field_id"), "field_id")
        for target in target_rows
        if isinstance(target, dict)
        for field in target.get("required_fields", [])
        if isinstance(field, dict)
        and field.get("status") == "unresolved"
        and field.get("value_invented") is False
    }
    if len(target_ids) != 2 or len(fields) != 15:
        raise ValueError("two targets and 15 unresolved fields are required")

    adapters = [
        _adapt_public(public_discovery, bindings["public_discovery"], target_ids),
        _adapt_expansion(source_expansion, bindings["source_expansion"], target_ids),
        _adapt_author(author_institution, bindings["author_institution"], target_ids),
        _adapt_record(record_export, bindings["record_export"], target_ids),
        _adapt_korean(korean_sources, bindings["korean_sources"], target_ids),
        _adapt_hosted(author_hosted, bindings["author_hosted"], target_ids),
    ]
    entries = sorted(
        [entry for adapter_entries, _ in adapters for entry in adapter_entries],
        key=lambda row: row["entry_id"],
    )
    if len(entries) != 38 or len({row["entry_id"] for row in entries}) != 38:
        raise ValueError("38 unique current candidate ledger occurrences are required")

    observation_counts = Counter(row["observation_class"] for row in entries)
    disposition_counts = Counter(row["policy"]["disposition"] for row in entries)
    route_counts = Counter(
        row["route_state"]["status"]
        for row in entries
        if row["observation_class"] == "route_state_observation"
    )
    if observation_counts != {
        "exact_identity_metadata_observation": 14,
        "verified_non_target_document": 4,
        "route_state_observation": 20,
    }:
        raise ValueError("current observation class counts drifted")
    if disposition_counts != {
        "rejected_non_candidate_document": 18,
        "not_candidate_route_state": 20,
    }:
        raise ValueError("current evidence policy dispositions drifted")
    expected_routes = {
        "dated_no_hit": 10,
        "degraded_route": 2,
        "oai_semantic_error": 4,
        "credential_required": 2,
        "scope_inapplicable": 2,
    }
    if route_counts != expected_routes:
        raise ValueError("current route-state counts drifted")
    retry_counts = [
        record_export.get("retained_retry_count"),
        korean_sources.get("retained_retry_count"),
        author_hosted.get("retained_retry_count"),
    ]
    if retry_counts != [1, 1, 1]:
        raise ValueError("one retained retry observation must remain current")

    binary_count = sum(
        row["integrity"]["nested_bytes"] is not None
        and row["observation_class"] == "verified_non_target_document"
        and row["identity_and_role"]["document_role"]
        in {"author_cv", "institutional_profile_publication"}
        for row in entries
    )
    citation_count = sum(
        row["identity_and_role"]["target_occurrence_count"] for row in entries
    )
    group_members: Dict[str, List[str]] = defaultdict(list)
    for row in entries:
        group_members[row["group_fingerprint"]].append(row["entry_id"])
    groups = [
        {
            "group_fingerprint": fingerprint,
            "occurrence_count": len(entry_ids),
            "entry_ids": sorted(entry_ids),
        }
        for fingerprint, entry_ids in sorted(group_members.items())
    ]

    return {
        "schema_version": 1,
        "contract": LEDGER_CONTRACT,
        "scope_id": scope_id,
        "revision": revision,
        "status": "current_evidence_candidate_ledger_compiled",
        "source_bindings": bindings,
        "source_report_count": len(bindings),
        "source_adapter_summary": [summary for _, summary in adapters],
        "ledger_entry_count": len(entries),
        "ledger_entries": entries,
        "group_count": len(groups),
        "groups": groups,
        "exact_identity_metadata_observation_count": observation_counts[
            "exact_identity_metadata_observation"
        ],
        "verified_non_target_document_count": observation_counts[
            "verified_non_target_document"
        ],
        "route_state_observation_count": observation_counts[
            "route_state_observation"
        ],
        "rejected_non_candidate_document_count": disposition_counts[
            "rejected_non_candidate_document"
        ],
        "not_candidate_route_state_count": disposition_counts[
            "not_candidate_route_state"
        ],
        "quarantined_candidate_count": 0,
        "binary_file_observation_count": binary_count,
        "citation_only_occurrence_count": citation_count,
        "route_state_summary": dict(sorted(expected_routes.items())),
        "retained_retry_observation_count": 1,
        "scientific_absence_proven_count": 0,
        "network_read_executed_count": 0,
        "external_action_executed_count": 0,
        "credential_use_executed_count": 0,
        "evidence_admission_executed_count": 0,
        "lineage_rebuild_executed_count": 0,
        "model_mutation_executed_count": 0,
        "claim_transition_executed_count": 0,
        "scientific_field_evidence_admitted": False,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "routing": {
            "next_internal_route": "compile-evidence-admission-transaction-boundary",
            "pause_internal_work": False,
        },
        "next_action": (
            "Compile an atomic future evidence-admission transaction from quarantine "
            "through scientific review, field acceptance, stale marking, and governed rebuild; "
            "do not execute it without a real accepted candidate."
        ),
    }


def write_report(report: JsonObject, output: Path) -> None:
    if report.get("contract") != LEDGER_CONTRACT:
        raise ValueError("current evidence candidate ledger report is required")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(_json_bytes(report))


def _read_json(path: Path) -> JsonObject:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "evidence-manifest",
        "manifest-binding",
        "intake-harness",
        "harness-binding",
        "public-discovery",
        "public-binding",
        "source-expansion",
        "expansion-binding",
        "author-institution",
        "author-binding",
        "record-export",
        "record-binding",
        "korean-sources",
        "korean-binding",
        "author-hosted",
        "hosted-binding",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = compile_current_evidence_candidate_ledger(
        _read_json(args.evidence_manifest),
        _read_json(args.manifest_binding),
        _read_json(args.intake_harness),
        _read_json(args.harness_binding),
        _read_json(args.public_discovery),
        _read_json(args.public_binding),
        _read_json(args.source_expansion),
        _read_json(args.expansion_binding),
        _read_json(args.author_institution),
        _read_json(args.author_binding),
        _read_json(args.record_export),
        _read_json(args.record_binding),
        _read_json(args.korean_sources),
        _read_json(args.korean_binding),
        _read_json(args.author_hosted),
        _read_json(args.hosted_binding),
    )
    write_report(report, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
