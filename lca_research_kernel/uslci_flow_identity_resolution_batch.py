"""Execute one bounded USLCI Flow identity-resolution decision batch."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tarfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_flow_identity_resolution_batch/v1"
PLAN_CONTRACT = "lca_research_kernel.uslci_flow_identity_resolution_plan/v1"
LOGIC_VERSION = "2026-07-17.1"
POLICY_VERSION = "2026-07-17.1"
EXPECTED_CANDIDATE_COUNT = 13
EXPECTED_INPUT_NAMES = (
    "plan_report",
    "verified_batch_index",
    "native_evidence_profiles",
    "automatic_decisions",
    "resolution_actions",
    "policy_groups",
    "resolution_batches",
    "plan_failures",
    "source_evidence_index",
    "route_authority_report",
)
PETROLEUM_TARGET_ID = "dd8dba8c-7c19-56e6-b26a-a18c0db28539"
PETROLEUM_TARGET_NAME = "petroleum fuel, through transportation"


def _json_bytes(value: object, *, pretty: bool = False) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _jsonl_bytes(rows: Iterable[JsonObject]) -> bytes:
    return b"".join(_json_bytes(row) for row in rows)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return dict(value)


def _array(value: object, label: str) -> List[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return list(value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value.strip()


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _parse_json(content: bytes, label: str) -> JsonObject:
    try:
        return _object(json.loads(content), label)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error


def _parse_json_array(content: bytes, label: str) -> List[object]:
    try:
        return _array(json.loads(content), label)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error


def _parse_jsonl(
    content: bytes, label: str, *, allow_empty: bool = False
) -> List[JsonObject]:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not UTF-8") from error
    rows = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            rows.append(_object(json.loads(line), f"{label} row {index}"))
        except json.JSONDecodeError as error:
            raise ValueError(f"{label} row {index} is invalid JSON") from error
    if not rows and not allow_empty:
        raise ValueError(f"{label} is empty")
    return rows


def _binding(value: Mapping[str, object], content: bytes, label: str) -> JsonObject:
    row = _object(value, label)
    path = _text(row.get("path"), f"{label} path")
    digest = _text(row.get("sha256"), f"{label} SHA-256")
    size = _integer(row.get("bytes"), f"{label} byte count")
    if digest != _sha256(content) or size != len(content):
        raise ValueError(f"{label} hash or byte count drifted")
    return {"path": path, "sha256": digest, "bytes": size}


def _artifact_binding(path: str, content: bytes) -> JsonObject:
    return {"path": path, "sha256": _sha256(content), "bytes": len(content)}


def input_version_for_bindings(
    batch_index: int, bindings: Sequence[JsonObject]
) -> str:
    if isinstance(batch_index, bool) or not isinstance(batch_index, int):
        raise ValueError("batch index must be an integer")
    return _sha256(
        _json_bytes(
            {
                "contract": CONTRACT,
                "logic_version": LOGIC_VERSION,
                "policy_version": POLICY_VERSION,
                "batch_index": batch_index,
                "inputs": list(bindings),
            }
        )
    )[:16]


def next_route_after_batch(batch_index: int) -> str:
    if not 1 <= batch_index <= 15:
        raise ValueError("resolution batch index must be between 1 and 15")
    if batch_index < 15:
        return f"execute-uslci-flow-identity-resolution-batch-{batch_index + 1}"
    return "compile-uslci-native-slice-flow-authoring-plan"


def validate_route_authority(
    batch_index: int,
    plan_value: Mapping[str, object],
    authority_value: Mapping[str, object],
) -> None:
    """Require plan authority for batch 1 and immediate-prior authority thereafter."""

    plan = _object(plan_value, "Flow resolution plan")
    authority = _object(authority_value, "resolution route authority")
    expected_route = f"execute-uslci-flow-identity-resolution-batch-{batch_index}"
    authority_route = _object(
        authority.get("routing"), "resolution route authority routing"
    ).get("next_internal_route")
    if batch_index == 1:
        if authority != plan or authority_route != expected_route:
            raise ValueError("resolution plan does not authorize batch 1")
        return
    if (
        authority.get("contract") != CONTRACT
        or authority.get("status")
        != "uslci_flow_identity_resolution_batch_completed"
        or authority.get("batch_index") != batch_index - 1
        or authority.get("source_resolution_plan_version")
        != plan.get("resolution_plan_version")
        or authority_route != expected_route
    ):
        raise ValueError("resolution route authority is not the immediately preceding batch")


def resolution_progress(
    descriptors: Sequence[Mapping[str, object]], batch_index: int
) -> Tuple[int, int]:
    if len(descriptors) != 15 or not 1 <= batch_index <= 15:
        raise ValueError("resolution descriptors or batch index are invalid")
    action_counts = []
    for index, descriptor_value in enumerate(descriptors, start=1):
        descriptor = _object(descriptor_value, f"resolution descriptor {index}")
        count = _integer(descriptor.get("action_count"), "descriptor action count")
        if descriptor.get("batch_index") != index or count != (100 if index < 15 else 51):
            raise ValueError("resolution descriptor cardinality drifted")
        action_counts.append(count)
    batch_resolved = sum(action_counts[:batch_index])
    return 8 + batch_resolved, 1451 - batch_resolved


def _safe_relative_path(value: object, label: str) -> str:
    raw = _text(value, label)
    path = PurePosixPath(raw)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or str(path) != raw
    ):
        raise ValueError(f"{label} is unsafe")
    return raw


def _read_bound_file(
    artifact_root: Path, binding_value: object, label: str
) -> Tuple[bytes, JsonObject]:
    binding = _object(binding_value, f"{label} binding")
    relative = _safe_relative_path(binding.get("path"), f"{label} path")
    root = artifact_root.resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} escaped artifact root") from error
    if not path.is_file():
        raise ValueError(f"{label} is missing")
    content = path.read_bytes()
    return content, _binding(binding, content, f"{label} binding")


def verify_source_archive(
    artifact_root: Path, source_value: Mapping[str, object]
) -> Tuple[Dict[str, bytes], JsonObject]:
    """Re-hash one manifest/archive pair and require exact canonical coverage."""

    source = _object(source_value, "source evidence row")
    native_batch_index = _integer(
        source.get("native_batch_index"), "source native batch index"
    )
    manifest_bytes, manifest_binding = _read_bound_file(
        artifact_root, source.get("action_output_manifest"), "action-output manifest"
    )
    archive_bytes, archive_binding = _read_bound_file(
        artifact_root, source.get("raw_archive"), "raw archive"
    )
    manifest_rows = _parse_jsonl(manifest_bytes, "action-output manifest")
    expected = {}
    for index, row in enumerate(manifest_rows, start=1):
        path = _safe_relative_path(row.get("path"), f"manifest row {index} path")
        if path in expected:
            raise ValueError("raw manifest path is duplicated")
        expected[path] = row
    observed = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
            for member in archive.getmembers():
                name = _safe_relative_path(member.name, "raw archive member")
                if not member.isfile() or name not in expected or name in observed:
                    raise ValueError("raw archive contains an invalid or duplicate member")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ValueError("raw archive member cannot be read")
                content = extracted.read()
                _binding(expected[name], content, f"raw archive member {name}")
                observed[name] = content
    except tarfile.TarError as error:
        raise ValueError("raw archive is invalid") from error
    if set(observed) != set(expected):
        raise ValueError("raw archive and manifest do not have exact coverage")
    verification = {
        "schema_version": 1,
        "native_batch_index": native_batch_index,
        "manifest_binding": manifest_binding,
        "archive_binding": archive_binding,
        "verified_member_count": len(observed),
        "verification_status": "manifest_archive_exact_coverage_verified",
    }
    return observed, verification


def _normalized_name(value: str) -> str:
    return " ".join(
        "".join(
            character.casefold() if character.isalnum() else " "
            for character in value
        ).split()
    )


def _candidate_assertion(
    action: JsonObject, candidate: JsonObject, ordinal: int
) -> JsonObject:
    target = _object(action.get("target"), "resolution target")
    fields = _object(candidate.get("fields"), "candidate fields")
    names = [
        _text(value, "candidate name")
        for value in _array(candidate.get("names"), "candidate names")
    ]
    target_name = _text(target.get("name"), "target name")
    normalized_target = _normalized_name(target_name)
    normalized_names = [_normalized_name(name) for name in names]
    target_type = _text(target.get("type_of_dataset"), "target Flow type")
    candidate_type = _text(fields.get("type_of_dataset"), "candidate Flow type")
    target_property = _text(target.get("flow_property"), "target Flow property")
    candidate_property_value = fields.get("flow_property")
    candidate_property = (
        candidate_property_value.strip()
        if isinstance(candidate_property_value, str)
        and candidate_property_value.strip()
        else None
    )
    target_unit = _text(target.get("reference_unit"), "target reference unit")
    candidate_unit_value = fields.get("reference_unit")
    candidate_unit = (
        candidate_unit_value.strip()
        if isinstance(candidate_unit_value, str) and candidate_unit_value.strip()
        else None
    )
    exact_name = normalized_target in set(normalized_names)
    all_name_text = " ".join(normalized_names)
    carrier_semantics = (
        "petroleum" in all_name_text and "fuel" in all_name_text
        if action.get("policy_group") == "product_identity_semantic_review"
        else None
    )
    context_semantics = (
        "transportation" in all_name_text or "transport" in all_name_text
        if action.get("policy_group") == "product_identity_semantic_review"
        else None
    )
    flow_type_equal = target_type == candidate_type
    property_equal = target_property == candidate_property
    unit_equal = candidate_unit is not None and target_unit == candidate_unit
    if candidate_unit is None:
        unit_status = "candidate_reference_unit_missing"
    elif unit_equal:
        unit_status = "exact"
    else:
        unit_status = "different"
    if action.get("policy_group") == "bounded_catalog_entity_type_mismatch":
        equivalent = False
        reason = "wrong_entity_type"
    else:
        equivalent = bool(
            flow_type_equal
            and property_equal
            and unit_equal
            and exact_name
            and carrier_semantics
        )
        if equivalent:
            reason = "all_distinguishing_identity_fields_match"
        elif not flow_type_equal:
            reason = "wrong_entity_type"
        elif not property_equal:
            reason = "flow_property_mismatch"
        elif not carrier_semantics:
            reason = "petroleum_carrier_semantics_not_preserved"
        elif not exact_name:
            reason = "distinguishing_name_not_preserved"
        else:
            reason = "reference_unit_not_verified"
    candidate_id = _text(candidate.get("id"), "candidate ID")
    assertion_id = _sha256(
        _json_bytes(
            {
                "contract": CONTRACT,
                "policy_version": POLICY_VERSION,
                "resolution_action_key": action.get("resolution_action_key"),
                "candidate_id": candidate_id,
                "candidate_version": candidate.get("version"),
                "ordinal": ordinal,
                "equivalence_supported": equivalent,
                "reason": reason,
            }
        )
    )[:16]
    return {
        "schema_version": 1,
        "assertion_id": assertion_id,
        "position": action.get("position"),
        "resolution_action_key": action.get("resolution_action_key"),
        "policy_group": action.get("policy_group"),
        "candidate_ordinal": ordinal,
        "candidate_id": candidate_id,
        "candidate_version": candidate.get("version"),
        "candidate_names": names,
        "native_match_score": candidate.get("match_score"),
        "native_match_reasons": candidate.get("match_reasons"),
        "target_flow_type": target_type,
        "candidate_flow_type": candidate_type,
        "flow_type_equivalent": flow_type_equal,
        "target_flow_property": target_property,
        "candidate_flow_property": candidate_property,
        "flow_property_equivalent": property_equal,
        "target_reference_unit": target_unit,
        "candidate_reference_unit": candidate_unit,
        "reference_unit_evidence_status": unit_status,
        "reference_unit_supports_equivalence": unit_equal,
        "exact_normalized_name": exact_name,
        "carrier_semantics_equivalent": carrier_semantics,
        "distinguishing_context_observed": context_semantics,
        "equivalence_supported": equivalent,
        "non_equivalence_reason": None if equivalent else reason,
        "native_score_is_decision_gate": False,
        "bounded_catalog_only": True,
    }


def resolve_flow_identity_action(
    action_value: Mapping[str, object],
    candidate_values: Sequence[Mapping[str, object]],
) -> Tuple[JsonObject, List[JsonObject]]:
    """Resolve one action from explicit candidate assertions."""

    action = _object(action_value, "resolution action")
    candidates = [
        _object(value, f"candidate {index}")
        for index, value in enumerate(candidate_values, start=1)
    ]
    if len(candidates) != EXPECTED_CANDIDATE_COUNT:
        raise ValueError("resolution action must bind exactly thirteen candidates")
    candidate_identities = [
        (_text(row.get("id"), "candidate ID"), row.get("version"))
        for row in candidates
    ]
    if len(set(candidate_identities)) != len(candidate_identities):
        raise ValueError("candidate identity is duplicated")
    policy_group = _text(action.get("policy_group"), "policy group")
    target = _object(action.get("target"), "resolution target")
    if action.get("remote_duplicate_unknown") is not True:
        raise ValueError("pending resolution action lost remote duplicate boundary")
    if action.get("flow_creation_authorized") is not False:
        raise ValueError("pending resolution action unexpectedly authorizes creation")
    assertions = [
        _candidate_assertion(action, candidate, index)
        for index, candidate in enumerate(candidates, start=1)
    ]
    equivalents = [row for row in assertions if row["equivalence_supported"]]
    if policy_group == "bounded_catalog_entity_type_mismatch":
        if (
            target.get("type_of_dataset") != "Elementary flow"
            or any(
                row.get("candidate_flow_type") != "Product flow"
                or row.get("flow_type_equivalent") is not False
                for row in assertions
            )
            or equivalents
            or action.get("allowed_resolution_decisions") != ["create_new_intent"]
        ):
            raise ValueError("bounded entity-type mismatch policy preconditions failed")
        resolution = "create_new_intent"
        basis = "all_candidates_wrong_entity_type"
        selected = None
    elif policy_group == "product_identity_semantic_review":
        if (
            target.get("id") != PETROLEUM_TARGET_ID
            or target.get("name") != PETROLEUM_TARGET_NAME
            or target.get("type_of_dataset") != "Product flow"
            or target.get("flow_property") != "Energy"
            or target.get("reference_unit") != "MJ"
        ):
            raise ValueError("Product semantic policy target is not approved")
        if len(equivalents) == 1:
            resolution = "reuse"
            basis = "one_candidate_preserves_complete_identity"
            selected = equivalents[0]["candidate_id"]
        elif len(equivalents) > 1:
            resolution = "blocked_insufficient_evidence"
            basis = "multiple_equivalent_candidates"
            selected = None
        else:
            resolution = "create_new_intent"
            basis = "no_candidate_preserves_petroleum_carrier_identity"
            selected = None
    else:
        raise ValueError("resolution action policy group is unsupported")
    allowed = _array(
        action.get("allowed_resolution_decisions"), "allowed resolution decisions"
    )
    if resolution not in allowed:
        raise ValueError("resolution decision is outside the compiled policy")
    assertion_digest = _sha256(_jsonl_bytes(assertions))
    decision_id = _sha256(
        _json_bytes(
            {
                "contract": CONTRACT,
                "policy_version": POLICY_VERSION,
                "resolution_action_key": action.get("resolution_action_key"),
                "assertion_sha256": assertion_digest,
                "resolution_decision": resolution,
                "selected_candidate_id": selected,
            }
        )
    )[:16]
    decision = {
        "schema_version": 1,
        "decision_id": decision_id,
        "position": action.get("position"),
        "resolution_action_key": action.get("resolution_action_key"),
        "source_action_key": action.get("source_action_key"),
        "target": target,
        "policy_group": policy_group,
        "policy_version": POLICY_VERSION,
        "resolution_decision": resolution,
        "selected_candidate_id": selected,
        "decision_basis": basis,
        "candidate_assertion_count": len(assertions),
        "candidate_assertions_sha256": assertion_digest,
        "remote_duplicate_unknown": resolution != "reuse",
        "flow_creation_authorized": False,
        "catalog_entity_created": False,
        "native_score_is_decision_gate": False,
        "review_priority": "high" if policy_group == "product_identity_semantic_review" else "low",
        "review_marker_retained": policy_group == "product_identity_semantic_review",
    }
    return decision, assertions


def _validate_action_raw_evidence(
    action: JsonObject,
    profile: JsonObject,
    members: Mapping[str, bytes],
) -> Tuple[List[JsonObject], JsonObject]:
    evidence = _object(action.get("evidence"), "resolution action evidence")
    if evidence != profile.get("evidence"):
        raise ValueError("resolution action/profile evidence drifted")
    root = _safe_relative_path(
        evidence.get("raw_action_output_root"), "raw action output root"
    )
    attempt = _integer(evidence.get("accepted_attempt"), "accepted attempt")
    prefix = f"{root}/attempt-{attempt}"
    relative_paths = {
        "execution": f"{prefix}/execution.json",
        "request": f"{prefix}/inputs/flow-identity-preflight.json",
        "candidate_sources": f"{prefix}/outputs/identity-candidate-sources.json",
        "candidates": f"{prefix}/outputs/identity-candidates.jsonl",
        "native_decision": f"{prefix}/outputs/identity-decision.json",
        "semantic_validation": f"{prefix}/semantic-validation.json",
    }
    if any(path not in members for path in relative_paths.values()):
        raise ValueError("selected action raw evidence is incomplete")
    request = _parse_json(members[relative_paths["request"]], "raw Flow request")
    native_decision = _parse_json(
        members[relative_paths["native_decision"]], "raw native decision"
    )
    candidates = _parse_jsonl(
        members[relative_paths["candidates"]], "raw native candidates"
    )
    candidate_sources = _parse_json_array(
        members[relative_paths["candidate_sources"]], "raw candidate sources"
    )
    execution = _parse_json(members[relative_paths["execution"]], "raw execution")
    semantic = _parse_json(
        members[relative_paths["semantic_validation"]], "raw semantic validation"
    )
    if (
        _sha256(members[relative_paths["native_decision"]])
        != evidence.get("native_decision_sha256")
        or _sha256(members[relative_paths["candidates"]])
        != evidence.get("candidates_sha256")
        or _sha256(members[relative_paths["candidate_sources"]])
        != evidence.get("candidate_sources_sha256")
    ):
        raise ValueError("selected action evidence hash drifted")
    target = _object(action.get("target"), "resolution action target")
    request_target = _object(request.get("target"), "raw request target")
    native_target = _object(native_decision.get("target"), "native decision target")
    native_fields = _object(native_target.get("fields"), "native target fields")
    if (
        request_target.get("id") != target.get("id")
        or request_target.get("name") != target.get("name")
        or request_target.get("source_flow_id") != action.get("source_flow_id")
        or request_target.get("type_of_dataset") != target.get("type_of_dataset")
        or request_target.get("flow_property") != target.get("flow_property")
        or request_target.get("reference_unit") != target.get("reference_unit")
        or native_target.get("id") != target.get("id")
        or native_target.get("names") != [target.get("name")]
        or native_fields.get("type_of_dataset") != target.get("type_of_dataset")
        or native_fields.get("flow_property") != target.get("flow_property")
        or native_fields.get("reference_unit") != target.get("reference_unit")
        or native_decision.get("status") != "needs_review"
        or native_decision.get("decision") != "manual_review"
        or native_decision.get("confidence") != "low"
        or native_decision.get("candidates") != candidates
        or len(candidate_sources) != 1
        or candidate_sources[0].get("kind") != "file"
        or candidate_sources[0].get("row_count") != EXPECTED_CANDIDATE_COUNT
        or execution.get("exit_code") not in {0, 1}
        or execution.get("action_key") != action.get("source_action_key")
        or execution.get("position") != action.get("position")
        or semantic.get("status") != "needs_review"
        or semantic.get("decision") != "manual_review"
        or semantic.get("confidence") != "low"
        or semantic.get("candidate_count") != EXPECTED_CANDIDATE_COUNT
        or semantic.get("candidate_source_count") != 1
        or semantic.get("blocker_count") != 0
        or semantic.get("decision_sha256")
        != evidence.get("native_decision_sha256")
        or semantic.get("candidates_sha256") != evidence.get("candidates_sha256")
        or semantic.get("candidate_sources_sha256")
        != evidence.get("candidate_sources_sha256")
    ):
        raise ValueError("selected action raw evidence does not bind compiled action")
    ledger = {
        "schema_version": 1,
        "position": action.get("position"),
        "resolution_action_key": action.get("resolution_action_key"),
        "source_action_key": action.get("source_action_key"),
        "source_native_batch_index": profile.get("batch_index"),
        "accepted_attempt": attempt,
        "raw_action_output_root": root,
        "verified_raw_file_count": len(relative_paths),
        "request_sha256": _sha256(members[relative_paths["request"]]),
        "native_decision_sha256": evidence.get("native_decision_sha256"),
        "candidates_sha256": evidence.get("candidates_sha256"),
        "candidate_sources_sha256": evidence.get("candidate_sources_sha256"),
        "semantic_validation_sha256": _sha256(
            members[relative_paths["semantic_validation"]]
        ),
        "verification_status": "raw_action_evidence_verified",
    }
    return candidates, ledger


def execute_uslci_flow_identity_resolution_batch(
    *,
    batch_index: int,
    plan_report_bytes: bytes,
    plan_report_binding: Mapping[str, object],
    verified_batch_index_bytes: bytes,
    verified_batch_index_binding: Mapping[str, object],
    native_evidence_profiles_bytes: bytes,
    native_evidence_profiles_binding: Mapping[str, object],
    automatic_decisions_bytes: bytes,
    automatic_decisions_binding: Mapping[str, object],
    resolution_actions_bytes: bytes,
    resolution_actions_binding: Mapping[str, object],
    policy_groups_bytes: bytes,
    policy_groups_binding: Mapping[str, object],
    resolution_batches_bytes: bytes,
    resolution_batches_binding: Mapping[str, object],
    plan_failures_bytes: bytes,
    plan_failures_binding: Mapping[str, object],
    source_evidence_index_bytes: bytes,
    source_evidence_index_binding: Mapping[str, object],
    route_authority_report_bytes: bytes,
    route_authority_report_binding: Mapping[str, object],
    artifact_root: Path,
) -> JsonObject:
    contents = {
        "plan_report": plan_report_bytes,
        "verified_batch_index": verified_batch_index_bytes,
        "native_evidence_profiles": native_evidence_profiles_bytes,
        "automatic_decisions": automatic_decisions_bytes,
        "resolution_actions": resolution_actions_bytes,
        "policy_groups": policy_groups_bytes,
        "resolution_batches": resolution_batches_bytes,
        "plan_failures": plan_failures_bytes,
        "source_evidence_index": source_evidence_index_bytes,
        "route_authority_report": route_authority_report_bytes,
    }
    supplied = {
        "plan_report": plan_report_binding,
        "verified_batch_index": verified_batch_index_binding,
        "native_evidence_profiles": native_evidence_profiles_binding,
        "automatic_decisions": automatic_decisions_binding,
        "resolution_actions": resolution_actions_binding,
        "policy_groups": policy_groups_binding,
        "resolution_batches": resolution_batches_binding,
        "plan_failures": plan_failures_binding,
        "source_evidence_index": source_evidence_index_binding,
        "route_authority_report": route_authority_report_binding,
    }
    input_bindings = [
        _binding(supplied[name], contents[name], f"{name} binding")
        for name in EXPECTED_INPUT_NAMES
    ]
    input_version = input_version_for_bindings(batch_index, input_bindings)
    plan = _parse_json(plan_report_bytes, "Flow resolution plan report")
    route_authority = _parse_json(
        route_authority_report_bytes, "route authority report"
    )
    verified_native_batches = _parse_jsonl(
        verified_batch_index_bytes, "verified native batch index"
    )
    profiles = _parse_jsonl(
        native_evidence_profiles_bytes, "native evidence profiles"
    )
    automatic = _parse_jsonl(automatic_decisions_bytes, "automatic decisions")
    actions = _parse_jsonl(resolution_actions_bytes, "resolution actions")
    policy_groups = _parse_jsonl(policy_groups_bytes, "policy groups")
    descriptors = _parse_jsonl(resolution_batches_bytes, "resolution batches")
    plan_failures = _parse_jsonl(
        plan_failures_bytes, "resolution plan failures", allow_empty=True
    )
    source_evidence = _parse_jsonl(
        source_evidence_index_bytes, "source evidence index"
    )
    if (
        plan.get("contract") != PLAN_CONTRACT
        or plan.get("status") != "uslci_flow_identity_resolution_plan_compiled"
        or plan.get("policy_version") != POLICY_VERSION
        or plan.get("canonical_flow_action_count") != 1459
        or plan.get("automatic_decision_count") != 8
        or plan.get("pending_resolution_action_count") != 1451
        or plan.get("resolution_batch_count") != 15
        or plan.get("compilation_failure_count") != 0
        or plan_failures
        or len(verified_native_batches) != 15
        or len(profiles) != 1459
        or len(automatic) != 8
        or len(actions) != 1451
        or len(policy_groups) != 2
        or len(descriptors) != 15
    ):
        raise ValueError("accepted Flow resolution plan cardinality drifted")
    if not 1 <= batch_index <= 15:
        raise ValueError("resolution batch index must be between 1 and 15")
    validate_route_authority(batch_index, plan, route_authority)
    descriptor = descriptors[batch_index - 1]
    keys = _array(
        descriptor.get("resolution_action_keys"), "descriptor resolution action keys"
    )
    expected_action_count = 100 if batch_index < 15 else 51
    expected_status = "selected_next" if batch_index == 1 else "pending_prior_batch"
    expected_group_counts = (
        {
            "bounded_catalog_entity_type_mismatch": 99,
            "product_identity_semantic_review": 1,
        }
        if batch_index == 1
        else {"bounded_catalog_entity_type_mismatch": expected_action_count}
    )
    if (
        descriptor.get("batch_index") != batch_index
        or descriptor.get("status") != expected_status
        or descriptor.get("action_count") != expected_action_count
        or len(keys) != expected_action_count
        or descriptor.get("policy_group_counts") != expected_group_counts
    ):
        raise ValueError("resolution batch descriptor drifted")
    action_by_key = {
        _text(row.get("resolution_action_key"), "resolution action key"): row
        for row in actions
    }
    profile_by_position = {
        _integer(row.get("position"), "evidence profile position"): row
        for row in profiles
    }
    selected_actions = []
    for key_value in keys:
        key = _text(key_value, "selected resolution action key")
        action = action_by_key.get(key)
        if not isinstance(action, dict):
            raise ValueError("descriptor references an unknown resolution action")
        selected_actions.append(action)
    if [row.get("position") for row in selected_actions] != descriptor.get(
        "source_positions"
    ):
        raise ValueError("descriptor action order drifted")
    required_native_batches = sorted(
        {
            _integer(
                profile_by_position[_integer(row.get("position"), "action position")].get(
                    "batch_index"
                ),
                "profile native batch index",
            )
            for row in selected_actions
        }
    )
    source_by_batch = {
        _integer(row.get("native_batch_index"), "source native batch index"): row
        for row in source_evidence
    }
    if sorted(source_by_batch) != required_native_batches:
        raise ValueError("source evidence index does not exactly cover selected actions")
    archive_members = {}
    source_verifications = []
    for native_batch_index in required_native_batches:
        members, verification = verify_source_archive(
            artifact_root, source_by_batch[native_batch_index]
        )
        archive_members[native_batch_index] = members
        source_verifications.append(verification)
    decisions = []
    assertions = []
    action_evidence = []
    for action in selected_actions:
        position = _integer(action.get("position"), "resolution action position")
        profile = profile_by_position[position]
        if (
            profile.get("action_key") != action.get("source_action_key")
            or profile.get("target") != action.get("target")
            or profile.get("policy_group") != action.get("policy_group")
            or profile.get("native_decision") != "manual_review"
        ):
            raise ValueError("resolution action no longer binds native profile")
        native_batch_index = _integer(
            profile.get("batch_index"), "profile native batch index"
        )
        candidates, evidence_row = _validate_action_raw_evidence(
            action, profile, archive_members[native_batch_index]
        )
        decision, candidate_assertions = resolve_flow_identity_action(
            action, candidates
        )
        decision["source_native_batch_index"] = native_batch_index
        decision["native_decision_sha256"] = action["evidence"][
            "native_decision_sha256"
        ]
        decisions.append(decision)
        assertions.extend(candidate_assertions)
        action_evidence.append(evidence_row)
    decision_counts = Counter(str(row["resolution_decision"]) for row in decisions)
    policy_counts = Counter(str(row["policy_group"]) for row in decisions)
    expected_assertion_count = expected_action_count * EXPECTED_CANDIDATE_COUNT
    if (
        len(decisions) != expected_action_count
        or len(assertions) != expected_assertion_count
        or len(action_evidence) != expected_action_count
        or decision_counts
        != Counter({"create_new_intent": expected_action_count})
        or policy_counts != Counter(expected_group_counts)
        or any(row.get("equivalence_supported") is not False for row in assertions)
        or sum(row.get("policy_group") == "product_identity_semantic_review" for row in decisions)
        != (1 if batch_index == 1 else 0)
    ):
        raise ValueError("resolution batch decision aggregate drifted")
    failures: List[JsonObject] = []
    detail_rows = {
        "decisions": decisions,
        "candidate_assertions": assertions,
        "action_evidence": action_evidence,
        "source_archive_verifications": source_verifications,
        "failures": failures,
    }
    artifact_prefix = f"uslci-flow-identity-resolution-batch-{batch_index}"
    artifact_names = {
        "decisions": f"{artifact_prefix}-decisions.jsonl",
        "candidate_assertions": f"{artifact_prefix}-candidate-assertions.jsonl",
        "action_evidence": f"{artifact_prefix}-action-evidence.jsonl",
        "source_archive_verifications": f"{artifact_prefix}-source-archive-verifications.jsonl",
        "failures": f"{artifact_prefix}-failures.jsonl",
    }
    detail_bytes = {key: _jsonl_bytes(rows) for key, rows in detail_rows.items()}
    artifact_bindings = {
        key: _artifact_binding(artifact_names[key], content)
        for key, content in detail_bytes.items()
    }
    batch_version = _sha256(
        _json_bytes(
            {
                "contract": CONTRACT,
                "logic_version": LOGIC_VERSION,
                "policy_version": POLICY_VERSION,
                "batch_index": batch_index,
                "batch_key": descriptor.get("batch_key"),
                "input_bindings": input_bindings,
                "artifact_bindings": artifact_bindings,
            }
        )
    )[:16]
    zero_counters = {
        "native_invocation_executed_count": 0,
        "network_read_executed_count": 0,
        "remote_candidate_search_executed_count": 0,
        "flow_creation_executed_count": 0,
        "flow_materialization_executed_count": 0,
        "save_draft_executed_count": 0,
        "process_creation_executed_count": 0,
        "model_connection_executed_count": 0,
        "model_mutation_executed_count": 0,
        "external_write_executed_count": 0,
        "publication_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
    }
    cumulative_resolved, remaining = resolution_progress(descriptors, batch_index)
    next_route = next_route_after_batch(batch_index)
    report = {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "policy_version": POLICY_VERSION,
        "status": "uslci_flow_identity_resolution_batch_completed",
        "scope_id": plan.get("scope_id"),
        "revision": plan.get("revision"),
        "input_version": input_version,
        "source_resolution_plan_version": plan.get("resolution_plan_version"),
        "batch_version": batch_version,
        "batch_index": batch_index,
        "batch_key": descriptor.get("batch_key"),
        "batch_action_count": len(decisions),
        "policy_group_counts": dict(sorted(policy_counts.items())),
        "resolution_decision_counts": dict(sorted(decision_counts.items())),
        "semantic_decision_action_count": 1 if batch_index == 1 else 0,
        "deterministic_policy_action_count": expected_action_count - (1 if batch_index == 1 else 0),
        "candidate_assertion_count": len(assertions),
        "candidate_equivalence_supported_count": 0,
        "candidate_non_equivalence_count": len(assertions),
        "source_native_batch_count": len(source_verifications),
        "verified_source_archive_count": len(source_verifications),
        "verified_source_archive_member_count": sum(
            int(row["verified_member_count"]) for row in source_verifications
        ),
        "selected_action_raw_file_count": sum(
            int(row["verified_raw_file_count"]) for row in action_evidence
        ),
        "remote_duplicate_unknown_count": sum(
            bool(row["remote_duplicate_unknown"]) for row in decisions
        ),
        "retained_review_marker_count": sum(
            bool(row["review_marker_retained"]) for row in decisions
        ),
        "inherited_automatic_decision_count": len(automatic),
        "cumulative_resolved_decision_count": cumulative_resolved,
        "remaining_resolution_action_count": remaining,
        "batch_failure_count": 0,
        "project_event_granularity": "aggregate_batch_only",
        "input_bindings": input_bindings,
        "artifact_bindings": artifact_bindings,
        "external_patterns": [
            {
                "project": "open-policy-agent/opa",
                "installed": False,
                "local_landing": "candidate_assertions_and_versioned_batch_decision_log",
            },
            {
                "project": "bazelbuild/bazel",
                "installed": False,
                "local_landing": "content_addressed_batch_and_decision_identity",
            },
            {
                "project": "argoproj/argo-workflows",
                "installed": False,
                "local_landing": "explicit_source_archives_and_terminal_batch_collection",
            },
        ],
        **zero_counters,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "routing": {
            "next_internal_route": next_route,
            "pause_internal_work": False,
        },
        "next_action": (
            f"Execute exact USLCI Flow identity-resolution batch {batch_index + 1}."
            if batch_index < 15
            else "Compile the native-slice Flow authoring plan."
        ),
    }
    return {**report, **detail_rows}


def materialize_uslci_flow_identity_resolution_batch(
    result: Mapping[str, object], out_dir: Path
) -> Dict[str, Path]:
    output = out_dir.resolve() / "outputs"
    output.mkdir(parents=True, exist_ok=True)
    batch_index = _integer(result.get("batch_index"), "result batch index")
    prefix = f"uslci-flow-identity-resolution-batch-{batch_index}"
    names = {
        "report": f"{prefix}.json",
        "decisions": f"{prefix}-decisions.jsonl",
        "candidate_assertions": f"{prefix}-candidate-assertions.jsonl",
        "action_evidence": f"{prefix}-action-evidence.jsonl",
        "source_archive_verifications": f"{prefix}-source-archive-verifications.jsonl",
        "failures": f"{prefix}-failures.jsonl",
    }
    paths = {key: output / name for key, name in names.items()}
    detail = set(names) - {"report"}
    for key in detail:
        rows = result.get(key)
        if not isinstance(rows, list):
            raise ValueError(f"resolution batch {key} is missing")
        paths[key].write_bytes(_jsonl_bytes(rows))
    report = {key: value for key, value in result.items() if key not in detail}
    paths["report"].write_bytes(_json_bytes(report, pretty=True))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-index", type=int, required=True)
    for name in EXPECTED_INPUT_NAMES:
        option = name.replace("_", "-")
        parser.add_argument(f"--{option}", type=Path, required=True)
        parser.add_argument(f"--{option}-binding", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    values = {"batch_index": args.batch_index}
    for name in EXPECTED_INPUT_NAMES:
        path = getattr(args, name)
        values[f"{name}_bytes"] = path.read_bytes()
        values[f"{name}_binding"] = json.loads(
            getattr(args, f"{name}_binding").read_bytes()
        )
    result = execute_uslci_flow_identity_resolution_batch(
        **values, artifact_root=args.artifact_root
    )
    materialize_uslci_flow_identity_resolution_batch(result, args.out_dir)
    detail = {
        "decisions",
        "candidate_assertions",
        "action_evidence",
        "source_archive_verifications",
        "failures",
    }
    print(
        json.dumps(
            {key: value for key, value in result.items() if key not in detail},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
