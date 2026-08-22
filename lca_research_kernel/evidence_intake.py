"""Compile and self-test a fail-closed evidence-intake validation boundary."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Sequence, Set, Tuple


JsonObject = Dict[str, object]
INTAKE_HARNESS_CONTRACT = (
    "lca_research_kernel.evidence_intake_validation_harness/v1"
)
CANDIDATE_CONTRACT = "lca_research_kernel.evidence_intake_candidate/v1"
MANIFEST_CONTRACT = "lca_research_kernel.evidence_acquisition_manifest/v1"
OPTIONS_CONTRACT = "lca_research_kernel.responsibility_aware_acquisition_options/v1"
REPLACEMENT_MAP_CONTRACT = (
    "lca_research_kernel.claim_evidence_replacement_map/v1"
)
DRY_RUN_CONTRACT = "lca_research_kernel.evidence_replacement_dry_run/v1"

FORBIDDEN_SECRET_PROPERTIES = {
    "access_token",
    "api_key",
    "apikey",
    "cookie",
    "credential_value",
    "password",
    "secret",
    "secret_value",
    "session_cookie",
    "token",
}
POLICY_GATES = [
    ("candidate-contract-and-secret-hygiene", "Contract shape and secret hygiene"),
    ("artifact-integrity", "Byte count, SHA-256, safe path, and detected media"),
    ("candidate-kind-and-document-role", "Admissible candidate kind and document role"),
    ("lawful-access-and-attribution", "Lawful access basis or attributable response"),
    ("target-continuity", "Manifest target ID, DOI, and title continuity"),
    ("field-scope", "Target-bound unresolved field and replacement-route coverage"),
    ("source-locator", "Page/span or message/span source locator"),
    ("lossless-field-payload", "Original value/unit and explicit transformations/risks"),
]


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _artifact_binding(value: object, label: str) -> JsonObject:
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


def _detect_media_type(content: bytes) -> str:
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    try:
        json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            return "application/octet-stream"
        return "text/plain"
    return "application/json"


def _secret_keys(value: object) -> Set[str]:
    found: Set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_SECRET_PROPERTIES:
                found.add(normalized)
            found.update(_secret_keys(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_secret_keys(child))
    return found


def _safe_relative_path(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    path = PurePosixPath(value.strip())
    return not path.is_absolute() and ".." not in path.parts and "." != str(path)


def _target_index(manifest: JsonObject) -> Dict[str, JsonObject]:
    targets = manifest.get("source_targets")
    if not isinstance(targets, list):
        return {}
    result: Dict[str, JsonObject] = {}
    for target in targets:
        if not isinstance(target, dict):
            continue
        target_id = target.get("target_id")
        if isinstance(target_id, str) and target_id not in result:
            result[target_id] = target
    return result


def _field_routes(replacement_map: JsonObject) -> Dict[str, JsonObject]:
    routes = replacement_map.get("field_replacement_routes")
    if not isinstance(routes, list):
        return {}
    result: Dict[str, JsonObject] = {}
    for route in routes:
        if not isinstance(route, dict):
            continue
        field_id = route.get("field_id")
        if isinstance(field_id, str) and field_id not in result:
            result[field_id] = route
    return result


def _gate(gate_id: str, reasons: List[str]) -> JsonObject:
    return {
        "gate_id": gate_id,
        "status": "passed" if not reasons else "denied",
        "reason_codes": list(dict.fromkeys(reasons)),
    }


def evaluate_evidence_candidate(
    candidate: JsonObject,
    artifact_bytes: bytes,
    manifest: JsonObject,
    replacement_map: JsonObject,
    synthetic_dry_run_sha256: str,
) -> JsonObject:
    """Evaluate one candidate without retaining input content or admitting evidence."""

    targets = _target_index(manifest)
    routes = _field_routes(replacement_map)
    gate_results: List[JsonObject] = []

    contract_reasons: List[str] = []
    if not isinstance(candidate, dict) or candidate.get("contract") != CANDIDATE_CONTRACT:
        contract_reasons.append("candidate_contract_invalid")
        candidate = candidate if isinstance(candidate, dict) else {}
    candidate_id = candidate.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        contract_reasons.append("candidate_contract_invalid")
        candidate_id = "invalid-candidate"
    classification = candidate.get("classification")
    if classification not in {"real_candidate", "non_evidence_test_fixture"}:
        contract_reasons.append("candidate_classification_invalid")
    secret_keys = _secret_keys(candidate)
    if secret_keys:
        contract_reasons.append("forbidden_secret_property")
    gate_results.append(_gate(POLICY_GATES[0][0], contract_reasons))

    artifact_reasons: List[str] = []
    artifact = candidate.get("artifact")
    artifact = artifact if isinstance(artifact, dict) else {}
    expected_sha = artifact.get("sha256")
    actual_sha = hashlib.sha256(artifact_bytes).hexdigest()
    if not re.fullmatch(r"[0-9a-f]{64}", str(expected_sha or "")):
        artifact_reasons.append("artifact_binding_invalid")
    elif expected_sha != actual_sha:
        artifact_reasons.append("artifact_digest_mismatch")
    expected_bytes = artifact.get("bytes")
    if (
        not isinstance(expected_bytes, int)
        or isinstance(expected_bytes, bool)
        or expected_bytes < 0
        or expected_bytes != len(artifact_bytes)
    ):
        artifact_reasons.append("artifact_byte_count_mismatch")
    detected_media = _detect_media_type(artifact_bytes)
    if artifact.get("media_type") != detected_media:
        artifact_reasons.append("artifact_media_mismatch")
    if not _safe_relative_path(artifact.get("path")):
        artifact_reasons.append("artifact_path_unsafe")
    if (
        expected_sha == synthetic_dry_run_sha256
        or classification == "non_evidence_test_vector"
    ):
        artifact_reasons.append("synthetic_or_fixture_hash_prohibited")
    gate_results.append(_gate(POLICY_GATES[1][0], artifact_reasons))

    kind_reasons: List[str] = []
    kind = candidate.get("candidate_kind")
    role = candidate.get("document_role")
    allowed_roles = {
        "target_paper_attachment": {
            "target_paper_full_text",
            "target_paper_supplementary_material",
        },
        "attributable_primary_response": {"attributable_primary_response"},
    }
    if kind not in allowed_roles:
        kind_reasons.append("candidate_kind_not_admissible")
    elif role not in allowed_roles[kind]:
        kind_reasons.append("document_role_not_admissible")
    if kind == "target_paper_attachment" and detected_media not in {
        "application/pdf",
        "application/json",
        "text/plain",
    }:
        kind_reasons.append("artifact_media_not_supported_for_candidate")
    if kind == "attributable_primary_response" and detected_media not in {
        "application/json",
        "text/plain",
    }:
        kind_reasons.append("artifact_media_not_supported_for_candidate")
    gate_results.append(_gate(POLICY_GATES[2][0], kind_reasons))

    access_reasons: List[str] = []
    allowed_access = {
        "target_paper_attachment": {
            "public_lawful_research_use",
            "human_provided_licensed_research_use",
            "human_provided_publisher_copy",
        },
        "attributable_primary_response": {"human_provided_author_response"},
    }
    if candidate.get("access_basis") not in allowed_access.get(str(kind), set()):
        access_reasons.append("lawful_access_basis_missing")
    if kind == "attributable_primary_response":
        attribution = candidate.get("attribution")
        attribution = attribution if isinstance(attribution, dict) else {}
        required = ("sender_identity", "identity_status", "permission_status", "message_id")
        if any(not isinstance(attribution.get(key), str) or not attribution[key].strip() for key in required):
            access_reasons.append("response_attribution_incomplete")
    gate_results.append(_gate(POLICY_GATES[3][0], access_reasons))

    target_reasons: List[str] = []
    target = candidate.get("target")
    target = target if isinstance(target, dict) else {}
    target_id = target.get("target_id")
    expected_target = targets.get(str(target_id))
    if not isinstance(expected_target, dict):
        target_reasons.append("target_identity_mismatch")
    else:
        identifiers = expected_target.get("identifiers")
        identifiers = identifiers if isinstance(identifiers, dict) else {}
        expected_doi = str(identifiers.get("doi") or "").strip().lower()
        observed_doi = str(target.get("doi") or "").strip().lower()
        expected_title = str(expected_target.get("title") or "").strip()
        observed_title = str(target.get("title") or "").strip()
        if not expected_doi or observed_doi != expected_doi or observed_title != expected_title:
            target_reasons.append("target_identity_mismatch")
    gate_results.append(_gate(POLICY_GATES[4][0], target_reasons))

    field_reasons: List[str] = []
    evidence_rows = candidate.get("field_evidence")
    evidence_rows = evidence_rows if isinstance(evidence_rows, list) else []
    expected_fields: Set[str] = set()
    if isinstance(expected_target, dict):
        rows = expected_target.get("required_fields")
        if isinstance(rows, list):
            expected_fields = {
                str(row.get("field_id"))
                for row in rows
                if isinstance(row, dict) and row.get("status") == "unresolved"
            }
    if not evidence_rows:
        field_reasons.append("field_evidence_missing")
    seen_fields: Set[str] = set()
    for row in evidence_rows:
        if not isinstance(row, dict):
            field_reasons.append("field_not_bound_to_target")
            continue
        field_id = row.get("field_id")
        route = routes.get(str(field_id))
        if (
            field_id not in expected_fields
            or not isinstance(route, dict)
            or route.get("target_id") != target_id
            or field_id in seen_fields
        ):
            field_reasons.append("field_not_bound_to_target")
        if isinstance(field_id, str):
            seen_fields.add(field_id)
    gate_results.append(_gate(POLICY_GATES[5][0], field_reasons))

    locator_reasons: List[str] = []
    for row in evidence_rows:
        if not isinstance(row, dict):
            locator_reasons.append("source_locator_incomplete")
            continue
        locator = row.get("source_locator")
        locator = locator if isinstance(locator, dict) else {}
        if kind == "target_paper_attachment":
            valid = (
                locator.get("locator_type") == "page_span"
                and isinstance(locator.get("page"), int)
                and not isinstance(locator.get("page"), bool)
                and locator["page"] >= 1
                and isinstance(locator.get("span"), str)
                and bool(locator["span"].strip())
            )
        elif kind == "attributable_primary_response":
            valid = (
                locator.get("locator_type") == "message_span"
                and isinstance(locator.get("message_id"), str)
                and bool(locator["message_id"].strip())
                and isinstance(locator.get("span"), str)
                and bool(locator["span"].strip())
            )
        else:
            valid = False
        if not valid:
            locator_reasons.append("source_locator_incomplete")
    gate_results.append(_gate(POLICY_GATES[6][0], locator_reasons))

    payload_reasons: List[str] = []
    for row in evidence_rows:
        if not isinstance(row, dict):
            payload_reasons.append("field_payload_not_lossless")
            continue
        valid = (
            "original_value" in row
            and row.get("original_value") is not None
            and isinstance(row.get("original_unit"), str)
            and bool(row["original_unit"].strip())
            and isinstance(row.get("transformations"), list)
            and isinstance(row.get("assumptions"), list)
            and isinstance(row.get("missing_data"), list)
        )
        if not valid:
            payload_reasons.append("field_payload_not_lossless")
    gate_results.append(_gate(POLICY_GATES[7][0], payload_reasons))

    reason_codes = list(
        dict.fromkeys(
            reason
            for gate_result in gate_results
            for reason in gate_result["reason_codes"]
        )
    )
    all_passed = not reason_codes
    if not all_passed:
        status = "rejected_not_admitted"
        policy_decision = "deny"
    elif classification == "non_evidence_test_fixture":
        status = "structurally_valid_test_fixture_not_admitted"
        policy_decision = "allow_for_contract_test_only"
    else:
        status = "quarantined_candidate_ready_for_scientific_review"
        policy_decision = "quarantine_for_scientific_review"
    return {
        "candidate_id": str(candidate_id),
        "status": status,
        "policy_decision": policy_decision,
        "all_required_gates_passed": all_passed,
        "reason_codes": reason_codes,
        "gate_results": gate_results,
        "artifact_observation": {
            "actual_sha256": actual_sha,
            "actual_bytes": len(artifact_bytes),
            "detected_media_type": detected_media,
        },
        "admission_eligible": False,
        "scientific_review_ready": all_passed and classification == "real_candidate",
        "evidence_admission_executed": False,
    }


def _bind_artifact(candidate: JsonObject, content: bytes) -> None:
    artifact = candidate["artifact"]
    assert isinstance(artifact, dict)
    artifact["sha256"] = hashlib.sha256(content).hexdigest()
    artifact["bytes"] = len(content)
    artifact["media_type"] = _detect_media_type(content)


def _build_fixture_suite(
    manifest: JsonObject, synthetic_sha256: str
) -> List[Tuple[str, JsonObject, bytes, str, str]]:
    targets = _target_index(manifest)
    target = next(
        row
        for row in targets.values()
        if any(
            isinstance(field, dict)
            and field.get("field_id") == "headline-capacity-to-sample-row"
            for field in row.get("required_fields", [])
        )
    )
    target_id = str(target["target_id"])
    identifiers = target.get("identifiers")
    assert isinstance(identifiers, dict)
    content = _json_bytes(
        {
            "message_id": "fixture-message-1",
            "sender": "test-only-author@example.invalid",
            "statement": "TEST ONLY: sample row reports 147 mAh/g.",
        }
    )
    valid: JsonObject = {
        "contract": CANDIDATE_CONTRACT,
        "candidate_id": "valid-attributable-response-test-fixture",
        "candidate_kind": "attributable_primary_response",
        "classification": "non_evidence_test_fixture",
        "artifact": {"path": "fixtures/valid-response.json"},
        "access_basis": "human_provided_author_response",
        "target": {
            "target_id": target_id,
            "doi": identifiers.get("doi"),
            "title": target.get("title"),
        },
        "document_role": "attributable_primary_response",
        "attribution": {
            "sender_identity": "test-only-author@example.invalid",
            "identity_status": "attributed_test_fixture",
            "permission_status": "test_fixture_research_use_only",
            "message_id": "fixture-message-1",
        },
        "field_evidence": [
            {
                "field_id": "headline-capacity-to-sample-row",
                "source_locator": {
                    "locator_type": "message_span",
                    "message_id": "fixture-message-1",
                    "span": "statement",
                },
                "original_value": "147",
                "original_unit": "mAh/g",
                "transformations": [],
                "assumptions": ["test fixture only"],
                "missing_data": [],
            }
        ],
    }
    _bind_artifact(valid, content)
    fixtures: List[Tuple[str, JsonObject, bytes, str, str]] = [
        (
            "valid-attributable-response",
            valid,
            content,
            "allow_for_contract_test_only",
            "",
        )
    ]

    def rejected(
        fixture_id: str,
        mutate,
        expected_reason: str,
        rebind: bool = True,
    ) -> None:
        candidate = copy.deepcopy(valid)
        candidate["candidate_id"] = fixture_id
        rejected_content = _json_bytes({"fixture_id": fixture_id, "test_only": True})
        mutate(candidate)
        if rebind:
            _bind_artifact(candidate, rejected_content)
        fixtures.append((fixture_id, candidate, rejected_content, "deny", expected_reason))

    rejected(
        "bibliographic-metadata",
        lambda row: row.update(
            {"candidate_kind": "target_paper_attachment", "document_role": "bibliographic_metadata"}
        ),
        "document_role_not_admissible",
    )
    rejected(
        "author-cv-citation-only",
        lambda row: row.update(
            {"candidate_kind": "target_paper_attachment", "document_role": "author_cv"}
        ),
        "document_role_not_admissible",
    )
    rejected(
        "credential-reference",
        lambda row: row.update({"candidate_kind": "credential_reference"}),
        "candidate_kind_not_admissible",
    )
    rejected(
        "artifact-hash-mismatch",
        lambda row: row["artifact"].update({"sha256": "a" * 64}),
        "artifact_digest_mismatch",
        rebind=False,
    )
    rejected(
        "wrong-target-title",
        lambda row: row["target"].update({"title": "Wrong target title"}),
        "target_identity_mismatch",
    )
    rejected(
        "missing-source-locator",
        lambda row: row["field_evidence"][0].update({"source_locator": {}}),
        "source_locator_incomplete",
    )
    rejected(
        "synthetic-dry-run-hash",
        lambda row: row["artifact"].update({"sha256": synthetic_sha256}),
        "synthetic_or_fixture_hash_prohibited",
        rebind=False,
    )
    return fixtures


def compile_evidence_intake_harness(
    manifest: JsonObject,
    manifest_binding: JsonObject,
    acquisition_options: JsonObject,
    acquisition_binding: JsonObject,
    replacement_map: JsonObject,
    replacement_binding: JsonObject,
    replacement_dry_run: JsonObject,
    dry_run_binding: JsonObject,
) -> JsonObject:
    """Compile the candidate policy and prove it with deterministic fixtures."""

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
        acquisition_options.get("contract") != OPTIONS_CONTRACT
        or acquisition_options.get("status")
        != "responsibility_aware_acquisition_options_compiled"
        or acquisition_options.get("target_count") != 2
        or acquisition_options.get("unresolved_field_count") != 15
        or acquisition_options.get("external_action_executed_count") != 0
        or acquisition_options.get("scientific_comparison_ready") is not False
        or acquisition_options.get("comparative_lcia_allowed") is not False
    ):
        raise ValueError("accepted acquisition options are required")
    if (
        replacement_map.get("contract") != REPLACEMENT_MAP_CONTRACT
        or replacement_map.get("status")
        != "claim_evidence_replacement_map_compiled"
        or replacement_map.get("field_replacement_route_count") != 15
        or replacement_map.get("external_action_executed_count") != 0
        or replacement_map.get("evidence_admission_executed_count") != 0
        or replacement_map.get("model_mutation_executed_count") != 0
        or replacement_map.get("claim_transition_executed_count") != 0
    ):
        raise ValueError("accepted replacement map is required")
    if (
        replacement_dry_run.get("contract") != DRY_RUN_CONTRACT
        or replacement_dry_run.get("status")
        != "synthetic_evidence_replacement_dry_run_passed"
        or replacement_dry_run.get("evidence_admission_executed_count") != 0
        or replacement_dry_run.get("model_mutation_executed_count") != 0
        or replacement_dry_run.get("claim_transition_executed_count") != 0
        or replacement_dry_run.get("routing", {}).get("next_internal_route")
        != "compile-evidence-intake-validation-harness"
        or replacement_dry_run.get("routing", {}).get("pause_internal_work") is not False
    ):
        raise ValueError("accepted replacement dry-run is required")
    scopes = {
        _scope(manifest, "evidence manifest"),
        _scope(acquisition_options, "acquisition options"),
        _scope(replacement_map, "replacement map"),
        _scope(replacement_dry_run, "replacement dry-run"),
    }
    if len(scopes) != 1:
        raise ValueError("evidence intake source scope conflicts")
    scope_id, revision = next(iter(scopes))
    bindings = {
        "evidence_manifest": _artifact_binding(manifest_binding, "manifest binding"),
        "acquisition_options": _artifact_binding(acquisition_binding, "acquisition binding"),
        "replacement_map": _artifact_binding(replacement_binding, "replacement binding"),
        "replacement_dry_run": _artifact_binding(dry_run_binding, "dry-run binding"),
    }

    targets = _target_index(manifest)
    if len(targets) != 2:
        raise ValueError("exactly two evidence targets are required")
    target_fields: Dict[str, Set[str]] = {}
    for target_id, target in targets.items():
        rows = target.get("required_fields")
        if not isinstance(rows, list):
            raise ValueError("15 unresolved field contracts are required")
        target_fields[target_id] = {
            _required_text(row.get("field_id"), "field_id")
            for row in rows
            if isinstance(row, dict)
            and row.get("status") == "unresolved"
            and row.get("value_invented") is False
        }
    field_ids = set().union(*target_fields.values())
    if len(field_ids) != 15 or sum(map(len, target_fields.values())) != 15:
        raise ValueError("15 unresolved field contracts are required")
    routes = _field_routes(replacement_map)
    if len(routes) != 15:
        raise ValueError("15 replacement routes are required")
    for target_id, fields in target_fields.items():
        for field_id in fields:
            if field_id not in routes or routes[field_id].get("target_id") != target_id:
                raise ValueError("15 replacement routes must preserve target-field binding")

    synthetic = replacement_dry_run.get("synthetic_change")
    if not isinstance(synthetic, dict):
        raise ValueError("accepted replacement dry-run synthetic marker is required")
    synthetic_sha256 = _required_text(synthetic.get("sha256"), "synthetic sha256")
    if (
        not re.fullmatch(r"[0-9a-f]{64}", synthetic_sha256)
        or synthetic.get("classification") != "non_evidence_test_vector"
        or synthetic.get("admitted_as_evidence") is not False
    ):
        raise ValueError("accepted replacement dry-run synthetic marker is required")

    fixtures = _build_fixture_suite(manifest, synthetic_sha256)
    results = []
    for fixture_id, candidate, content, expected_decision, expected_reason in fixtures:
        evaluation = evaluate_evidence_candidate(
            candidate, content, manifest, replacement_map, synthetic_sha256
        )
        expectation_match = evaluation["policy_decision"] == expected_decision
        if expected_reason:
            expectation_match = expectation_match and expected_reason in evaluation[
                "reason_codes"
            ]
        results.append(
            {
                "fixture_id": fixture_id,
                "expected_policy_decision": expected_decision,
                "expected_reason_code": expected_reason or None,
                "observed_status": evaluation["status"],
                "observed_policy_decision": evaluation["policy_decision"],
                "reason_codes": evaluation["reason_codes"],
                "gate_results": evaluation["gate_results"],
                "expectation_match": expectation_match,
                "evidence_admission_executed": False,
            }
        )
    expectation_matches = sum(row["expectation_match"] for row in results)
    structurally_valid = sum(
        row["observed_status"] == "structurally_valid_test_fixture_not_admitted"
        for row in results
    )
    rejected = sum(row["observed_status"] == "rejected_not_admitted" for row in results)
    if (
        len(results) != 8
        or expectation_matches != 8
        or structurally_valid != 1
        or rejected != 7
    ):
        raise ValueError("evidence intake fixture contract did not pass 1/7 expectations")

    return {
        "schema_version": 1,
        "contract": INTAKE_HARNESS_CONTRACT,
        "scope_id": scope_id,
        "revision": revision,
        "status": "evidence_intake_validation_harness_compiled",
        "source_bindings": bindings,
        "candidate_contract": {
            "contract": CANDIDATE_CONTRACT,
            "candidate_kinds": [
                "attributable_primary_response",
                "target_paper_attachment",
            ],
            "real_candidate_success_status": (
                "quarantined_candidate_ready_for_scientific_review"
            ),
            "test_fixture_success_status": (
                "structurally_valid_test_fixture_not_admitted"
            ),
            "automatic_admission": False,
            "required_field_payload_keys": [
                "field_id",
                "source_locator",
                "original_value",
                "original_unit",
                "transformations",
                "assumptions",
                "missing_data",
            ],
            "forbidden_secret_properties": sorted(FORBIDDEN_SECRET_PROPERTIES),
        },
        "policy_gates": [
            {"gate_id": gate_id, "meaning": meaning, "order": index}
            for index, (gate_id, meaning) in enumerate(POLICY_GATES, start=1)
        ],
        "target_count": len(targets),
        "field_contract_count": len(field_ids),
        "replacement_route_coverage_count": len(routes),
        "policy_gate_count": len(POLICY_GATES),
        "fixture_count": len(results),
        "fixture_results": results,
        "structurally_valid_fixture_count": structurally_valid,
        "rejected_fixture_count": rejected,
        "fixture_expectation_match_count": expectation_matches,
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
            "next_internal_route": "evaluate-current-evidence-candidate-ledger",
            "pause_internal_work": False,
        },
        "next_action": (
            "Apply the accepted intake policy to current content-addressed metadata, "
            "repository exports, CRIS records, CVs, and citation-only observations "
            "without new network access or evidence admission."
        ),
    }


def write_report(report: JsonObject, output: Path) -> None:
    if report.get("contract") != INTAKE_HARNESS_CONTRACT:
        raise ValueError("evidence intake validation harness report is required")
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
        "evidence-binding",
        "acquisition-options",
        "acquisition-binding",
        "replacement-map",
        "replacement-binding",
        "replacement-dry-run",
        "dry-run-binding",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = compile_evidence_intake_harness(
        _read_json(args.evidence_manifest),
        _read_json(args.evidence_binding),
        _read_json(args.acquisition_options),
        _read_json(args.acquisition_binding),
        _read_json(args.replacement_map),
        _read_json(args.replacement_binding),
        _read_json(args.replacement_dry_run),
        _read_json(args.dry_run_binding),
    )
    write_report(report, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
