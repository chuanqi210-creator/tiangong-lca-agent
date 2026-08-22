"""Compile the complete USLCI native Flow identity-resolution work plan."""

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

from lca_research_kernel.uslci_native_slice_preflight_batch import (
    ACTION_CONTRACT,
    PLAN_CONTRACT,
    _catalog_identities,
)


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_flow_identity_resolution_plan/v1"
LOGIC_VERSION = "2026-07-17.1"
POLICY_VERSION = "2026-07-17.1"
EXPECTED_ROUTE = "compile-uslci-native-slice-flow-identity-resolution-plan"
NEXT_ROUTE = "execute-uslci-flow-identity-resolution-batch-1"
FIRST_BATCH_CONTRACT = "lca_research_kernel.uslci_native_slice_preflight_batch/v1"
CONTINUATION_CONTRACT = (
    "lca_research_kernel.uslci_native_slice_preflight_continuation/v1"
)
REFERENCE_RESOLUTION_CONTRACT = (
    "lca_research_kernel.uslci_reference_flow_resolution/v1"
)
AUTHORING_EXECUTION_CONTRACT = (
    "lca_research_kernel.uslci_reference_flow_authoring_execution/v1"
)
EXPECTED_INPUT_NAMES = (
    "canonical_batch_index",
    "plan_report",
    "flow_actions",
    "flow_batches",
    "candidate_catalog",
    "reference_resolution_report",
    "reference_decisions",
    "authoring_execution_report",
    "authoring_branches",
    "staged_high_voltage_flow",
    "staged_construction_flow",
)
MAX_RESOLUTION_BATCH_SIZE = 100
EXPECTED_FLOW_COUNT = 1459
EXPECTED_CANDIDATE_COUNT = 13
EXPECTED_AUTOMATIC_COUNT = 8
EXPECTED_PENDING_COUNT = 1451


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


def _parse_jsonl(
    content: bytes, label: str, *, allow_empty: bool = False
) -> List[JsonObject]:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not UTF-8") from error
    rows: List[JsonObject] = []
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


def _binding(
    value: Mapping[str, object], content: bytes, label: str
) -> JsonObject:
    row = _object(value, label)
    path = _text(row.get("path"), f"{label} path")
    digest = _text(row.get("sha256"), f"{label} SHA-256")
    size = _integer(row.get("bytes"), f"{label} byte count")
    if digest != _sha256(content) or size != len(content):
        raise ValueError(f"{label} hash or byte count drifted")
    return {"path": path, "sha256": digest, "bytes": size}


def _artifact_binding(path: str, content: bytes) -> JsonObject:
    return {"path": path, "sha256": _sha256(content), "bytes": len(content)}


def input_version_for_bindings(bindings: Sequence[JsonObject]) -> str:
    return _sha256(
        _json_bytes(
            {
                "contract": CONTRACT,
                "logic_version": LOGIC_VERSION,
                "policy_version": POLICY_VERSION,
                "maximum_resolution_batch_size": MAX_RESOLUTION_BATCH_SIZE,
                "inputs": list(bindings),
            }
        )
    )[:16]


def _accepted_batch_status(index: int, status: object) -> bool:
    if index == 1:
        return status in {
            "uslci_native_slice_preflight_batch_reference_resolution_required",
            "uslci_native_slice_preflight_batch_completed",
        }
    return status == "uslci_native_slice_preflight_continuation_completed"


def select_latest_accepted_batch_payloads(
    events: Sequence[Mapping[str, object]],
) -> List[JsonObject]:
    """Select the last successful immutable observation for every batch index."""

    selected: Dict[int, JsonObject] = {}
    for event in events:
        if event.get("event_type") != (
            "research.uslci_native_slice_preflight_batch.completed"
        ):
            continue
        payload_value = event.get("payload")
        if not isinstance(payload_value, dict):
            continue
        payload = dict(payload_value)
        index = payload.get("batch_index")
        if isinstance(index, bool) or not isinstance(index, int) or not 1 <= index <= 15:
            continue
        start = (index - 1) * 100 + 1
        end = min(index * 100, EXPECTED_FLOW_COUNT)
        count = end - start + 1
        if (
            payload.get("action_failure_count") != 0
            or payload.get("action_semantic_accepted_count") != count
            or payload.get("start_position", start) != start
            or payload.get("end_position", end) != end
            or not _accepted_batch_status(index, payload.get("status"))
        ):
            continue
        payload["source_event_sequence"] = _integer(
            event.get("sequence"), "source event sequence"
        )
        payload["source_event_id"] = _text(
            event.get("event_id"), "source event ID"
        )
        selected[index] = payload
    if set(selected) != set(range(1, 16)):
        missing = sorted(set(range(1, 16)) - set(selected))
        raise ValueError(f"accepted native Flow batch observations are incomplete: {missing}")
    return [selected[index] for index in range(1, 16)]


def _normalized_name(value: str) -> str:
    return " ".join(
        "".join(character.casefold() if character.isalnum() else " " for character in value).split()
    )


def classify_candidate_evidence(
    action: Mapping[str, object], candidates: Sequence[Mapping[str, object]]
) -> JsonObject:
    """Classify by identity evidence; native score is retained but never a gate."""

    target = _object(action.get("target"), "Flow target")
    target_type = _text(target.get("type_of_dataset"), "target Flow type")
    target_name = _text(target.get("name"), "target Flow name")
    if len(candidates) != EXPECTED_CANDIDATE_COUNT:
        raise ValueError("candidate evidence must contain exactly thirteen rows")
    type_counts: Counter[str] = Counter()
    scores: List[int] = []
    exact_name_count = 0
    decision_hint_counts: Counter[str] = Counter()
    candidate_ids = set()
    for index, candidate_value in enumerate(candidates, start=1):
        candidate = _object(candidate_value, f"candidate {index}")
        candidate_id = _text(candidate.get("id"), f"candidate {index} ID")
        if candidate_id in candidate_ids:
            raise ValueError("candidate ID is duplicated")
        candidate_ids.add(candidate_id)
        fields = _object(candidate.get("fields"), f"candidate {index} fields")
        candidate_type = _text(
            fields.get("type_of_dataset"), f"candidate {index} Flow type"
        )
        type_counts[candidate_type] += 1
        score = _integer(candidate.get("match_score"), f"candidate {index} score")
        scores.append(score)
        names = [
            _text(name, f"candidate {index} name")
            for name in _array(candidate.get("names"), f"candidate {index} names")
        ]
        if _normalized_name(target_name) in {_normalized_name(name) for name in names}:
            exact_name_count += 1
        hint = candidate.get("decision_hint")
        decision_hint_counts["none" if hint is None else str(hint)] += 1
    all_hard_mismatch = all(candidate_type != target_type for candidate_type in type_counts)
    if target_type == "Elementary flow" and all_hard_mismatch:
        policy_group = "bounded_catalog_entity_type_mismatch"
        allowed = ["create_new_intent"]
        execution_mode = "deterministic_policy"
    elif target_type == "Product flow":
        policy_group = "product_identity_semantic_review"
        allowed = ["reuse", "create_new_intent", "blocked_insufficient_evidence"]
        execution_mode = "ai_semantic"
    else:
        raise ValueError("candidate evidence does not match an approved policy group")
    top_score = max(scores)
    return {
        "policy_group": policy_group,
        "decision_execution_mode": execution_mode,
        "candidate_type_counts": dict(sorted(type_counts.items())),
        "all_candidates_hard_type_mismatched": all_hard_mismatch,
        "top_native_match_score": top_score,
        "top_native_match_candidate_count": sum(score == top_score for score in scores),
        "exact_normalized_name_candidate_count": exact_name_count,
        "decision_hint_counts": dict(sorted(decision_hint_counts.items())),
        "allowed_resolution_decisions": allowed,
        "native_score_is_decision_gate": False,
    }


def build_resolution_batches(
    actions: Sequence[Mapping[str, object]],
) -> List[JsonObject]:
    keys = [_text(row.get("resolution_action_key"), "resolution action key") for row in actions]
    if len(set(keys)) != len(keys):
        raise ValueError("resolution action key is duplicated")
    priority = {
        "product_identity_semantic_review": 0,
        "bounded_catalog_entity_type_mismatch": 1,
    }
    ordered = sorted(
        [dict(row) for row in actions],
        key=lambda row: (
            priority.get(str(row.get("policy_group")), 99),
            _integer(row.get("position"), "resolution action position"),
            str(row.get("resolution_action_key")),
        ),
    )
    batches = []
    for offset in range(0, len(ordered), MAX_RESOLUTION_BATCH_SIZE):
        selected = ordered[offset : offset + MAX_RESOLUTION_BATCH_SIZE]
        index = len(batches) + 1
        action_keys = [str(row["resolution_action_key"]) for row in selected]
        positions = [int(row["position"]) for row in selected]
        group_counts = Counter(str(row["policy_group"]) for row in selected)
        batch_key = _sha256(
            _json_bytes(
                {
                    "contract": CONTRACT,
                    "policy_version": POLICY_VERSION,
                    "batch_index": index,
                    "resolution_action_keys": action_keys,
                }
            )
        )[:16]
        batches.append(
            {
                "schema_version": 1,
                "batch_index": index,
                "batch_key": batch_key,
                "action_count": len(selected),
                "source_positions": positions,
                "resolution_action_keys": action_keys,
                "policy_group_counts": dict(sorted(group_counts.items())),
                "maximum_batch_size": MAX_RESOLUTION_BATCH_SIZE,
                "project_event_granularity": "aggregate_batch_only",
                "status": "selected_next" if index == 1 else "pending_prior_batch",
            }
        )
    return batches


def _safe_relative_path(value: object, label: str) -> str:
    raw = _text(value, label)
    path = PurePosixPath(raw)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{label} is unsafe")
    if str(path) != raw:
        raise ValueError(f"{label} is not canonical")
    return raw


def _read_bound_artifact(
    artifact_root: Path, binding_value: object, label: str
) -> Tuple[bytes, JsonObject]:
    binding = _object(binding_value, f"{label} binding")
    relative = _safe_relative_path(binding.get("path"), f"{label} path")
    root = artifact_root.resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} escaped the artifact root") from error
    if not path.is_file():
        raise ValueError(f"{label} is missing")
    content = path.read_bytes()
    verified = _binding(binding, content, f"{label} binding")
    return content, verified


def _verified_archive(
    archive_bytes: bytes, manifest_rows: Sequence[JsonObject]
) -> Dict[str, bytes]:
    expected = {}
    for index, row in enumerate(manifest_rows, start=1):
        path = _safe_relative_path(row.get("path"), f"manifest row {index} path")
        if path in expected:
            raise ValueError("raw manifest path is duplicated")
        expected[path] = row
    observed: Dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
            for member in archive.getmembers():
                name = _safe_relative_path(member.name, "raw archive member")
                if not member.isfile() or name not in expected:
                    raise ValueError("raw archive contains an undeclared member")
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
    return observed


def _flow_identity(flow: JsonObject) -> Tuple[str, str, str]:
    dataset = _object(flow.get("flowDataSet"), "staged Flow data set")
    information = _object(dataset.get("flowInformation"), "staged Flow information")
    data = _object(information.get("dataSetInformation"), "staged Flow data information")
    flow_id = _text(data.get("common:UUID"), "staged Flow UUID")
    name = _object(data.get("name"), "staged Flow name")
    base_names = _array(name.get("baseName"), "staged Flow base names")
    first_name = _object(base_names[0], "staged Flow base name")
    base_name = _text(first_name.get("#text"), "staged Flow base name text")
    validation = _object(dataset.get("modellingAndValidation"), "staged Flow validation")
    method = _object(validation.get("LCIMethod"), "staged Flow method")
    flow_type = _text(method.get("typeOfDataSet"), "staged Flow type")
    return flow_id, base_name, flow_type


def _validate_global_contracts(
    *,
    plan: JsonObject,
    actions: Sequence[JsonObject],
    batches: Sequence[JsonObject],
    catalog_identities: set,
) -> None:
    if (
        plan.get("contract") != PLAN_CONTRACT
        or plan.get("status") != "uslci_native_slice_preflight_plan_compiled"
        or plan.get("flow_action_count") != EXPECTED_FLOW_COUNT
        or plan.get("flow_batch_count") != 15
        or plan.get("maximum_flow_batch_size") != 100
        or plan.get("local_flow_candidate_count") != EXPECTED_CANDIDATE_COUNT
        or len(actions) != EXPECTED_FLOW_COUNT
        or len(batches) != 15
        or len(catalog_identities) != EXPECTED_CANDIDATE_COUNT
    ):
        raise ValueError("native preflight plan cardinality drifted")
    if [row.get("position") for row in actions] != list(range(1, EXPECTED_FLOW_COUNT + 1)):
        raise ValueError("Flow actions do not have exact positional coverage")
    if len({row.get("action_key") for row in actions}) != EXPECTED_FLOW_COUNT:
        raise ValueError("Flow action key is duplicated")
    for index, descriptor in enumerate(batches, start=1):
        start = (index - 1) * 100 + 1
        end = min(index * 100, EXPECTED_FLOW_COUNT)
        selected = actions[start - 1 : end]
        if (
            descriptor.get("batch_index") != index
            or descriptor.get("start_position") != start
            or descriptor.get("end_position") != end
            or descriptor.get("action_count") != len(selected)
            or descriptor.get("action_keys")
            != [row.get("action_key") for row in selected]
        ):
            raise ValueError(f"Flow batch descriptor {index} drifted")


def compile_uslci_flow_identity_resolution_plan(
    *,
    canonical_batch_index_bytes: bytes,
    canonical_batch_index_binding: Mapping[str, object],
    plan_report_bytes: bytes,
    plan_report_binding: Mapping[str, object],
    flow_actions_bytes: bytes,
    flow_actions_binding: Mapping[str, object],
    flow_batches_bytes: bytes,
    flow_batches_binding: Mapping[str, object],
    candidate_catalog_bytes: bytes,
    candidate_catalog_binding: Mapping[str, object],
    reference_resolution_report_bytes: bytes,
    reference_resolution_report_binding: Mapping[str, object],
    reference_decisions_bytes: bytes,
    reference_decisions_binding: Mapping[str, object],
    authoring_execution_report_bytes: bytes,
    authoring_execution_report_binding: Mapping[str, object],
    authoring_branches_bytes: bytes,
    authoring_branches_binding: Mapping[str, object],
    staged_high_voltage_flow_bytes: bytes,
    staged_high_voltage_flow_binding: Mapping[str, object],
    staged_construction_flow_bytes: bytes,
    staged_construction_flow_binding: Mapping[str, object],
    artifact_root: Path,
) -> JsonObject:
    contents = {
        "canonical_batch_index": canonical_batch_index_bytes,
        "plan_report": plan_report_bytes,
        "flow_actions": flow_actions_bytes,
        "flow_batches": flow_batches_bytes,
        "candidate_catalog": candidate_catalog_bytes,
        "reference_resolution_report": reference_resolution_report_bytes,
        "reference_decisions": reference_decisions_bytes,
        "authoring_execution_report": authoring_execution_report_bytes,
        "authoring_branches": authoring_branches_bytes,
        "staged_high_voltage_flow": staged_high_voltage_flow_bytes,
        "staged_construction_flow": staged_construction_flow_bytes,
    }
    supplied = {
        "canonical_batch_index": canonical_batch_index_binding,
        "plan_report": plan_report_binding,
        "flow_actions": flow_actions_binding,
        "flow_batches": flow_batches_binding,
        "candidate_catalog": candidate_catalog_binding,
        "reference_resolution_report": reference_resolution_report_binding,
        "reference_decisions": reference_decisions_binding,
        "authoring_execution_report": authoring_execution_report_binding,
        "authoring_branches": authoring_branches_binding,
        "staged_high_voltage_flow": staged_high_voltage_flow_binding,
        "staged_construction_flow": staged_construction_flow_binding,
    }
    input_bindings = [
        _binding(supplied[name], contents[name], f"{name} binding")
        for name in EXPECTED_INPUT_NAMES
    ]
    input_version = input_version_for_bindings(input_bindings)
    canonical_batches = _parse_jsonl(
        canonical_batch_index_bytes, "canonical native batch index"
    )
    plan = _parse_json(plan_report_bytes, "native preflight plan")
    actions = _parse_jsonl(flow_actions_bytes, "Flow action ledger")
    batches = _parse_jsonl(flow_batches_bytes, "Flow batch descriptors")
    _, catalog_identities = _catalog_identities(candidate_catalog_bytes)
    _validate_global_contracts(
        plan=plan,
        actions=actions,
        batches=batches,
        catalog_identities=catalog_identities,
    )
    if [row.get("batch_index") for row in canonical_batches] != list(range(1, 16)):
        raise ValueError("canonical batch index does not cover indices 1 through 15")

    action_by_position = {int(row["position"]): row for row in actions}
    profiles: List[JsonObject] = []
    native_results: Dict[int, JsonObject] = {}
    verified_index: List[JsonObject] = []
    aggregate_decisions: Counter[str] = Counter()
    aggregate_candidates = 0
    aggregate_sources = 0
    aggregate_raw_files = 0
    aggregate_native_calls = 0
    plan_version = _text(plan.get("plan_version"), "preflight plan version")
    runtime_sha = _text(plan.get("runtime_tree_sha256"), "runtime tree SHA-256")
    catalog_sha = input_bindings[4]["sha256"]

    for index, row in enumerate(canonical_batches, start=1):
        artifacts = _object(row.get("artifacts"), f"batch {index} artifacts")
        required = (
            "report",
            "result_ledger",
            "action_output_manifest",
            "raw_archive",
            "failures",
        )
        if set(artifacts) != set(required):
            raise ValueError(f"batch {index} canonical artifact set drifted")
        bound_contents = {}
        verified_artifacts = {}
        for name in required:
            content, verified = _read_bound_artifact(
                artifact_root, artifacts[name], f"batch {index} {name}"
            )
            bound_contents[name] = content
            verified_artifacts[name] = verified
        report = _parse_json(bound_contents["report"], f"batch {index} report")
        results = _parse_jsonl(
            bound_contents["result_ledger"], f"batch {index} result ledger"
        )
        manifest = _parse_jsonl(
            bound_contents["action_output_manifest"],
            f"batch {index} raw action manifest",
        )
        failures = _parse_jsonl(
            bound_contents["failures"], f"batch {index} failures", allow_empty=True
        )
        archive = _verified_archive(bound_contents["raw_archive"], manifest)
        start = (index - 1) * 100 + 1
        end = min(index * 100, EXPECTED_FLOW_COUNT)
        expected_count = end - start + 1
        expected_contract = FIRST_BATCH_CONTRACT if index == 1 else CONTINUATION_CONTRACT
        if (
            report.get("contract") != expected_contract
            or not _accepted_batch_status(index, report.get("status"))
            or report.get("batch_index") != index
            or report.get("plan_version") != plan_version
            or report.get("runtime_tree_sha256") != runtime_sha
            or report.get("start_position", start) != start
            or report.get("end_position", end) != end
            or report.get("requested_action_count") != expected_count
            or report.get("action_semantic_accepted_count") != expected_count
            or report.get("action_failure_count") != 0
            or failures
            or len(results) != expected_count
            or len(manifest) != expected_count * 6
            or len(archive) != expected_count * 6
            or report.get("local_flow_candidate_count") != EXPECTED_CANDIDATE_COUNT
        ):
            raise ValueError(f"batch {index} accepted report contract drifted")
        if (
            row.get("source_event_sequence") is None
            or row.get("source_event_id") is None
            or row.get("batch_index") != index
            or row.get("start_position") != start
            or row.get("end_position") != end
            or row.get("action_count") != expected_count
            or row.get("plan_version") != plan_version
            or row.get("runtime_tree_sha256") != runtime_sha
            or row.get("decision_counts") != report.get("decision_counts")
        ):
            raise ValueError(f"batch {index} canonical index metadata drifted")

        for expected_position, result in zip(range(start, end + 1), results):
            action = action_by_position[expected_position]
            request = _object(action.get("request"), "Flow action request")
            target = _object(action.get("target"), "Flow action target")
            if (
                action.get("contract") != ACTION_CONTRACT
                or action.get("candidate_catalog_sha256") != catalog_sha
                or action.get("runtime_tree_sha256") != runtime_sha
                or result.get("position") != expected_position
                or result.get("action_key") != action.get("action_key")
                or result.get("source_flow_id") != action.get("source_flow_id")
                or result.get("source_flow_name") != action.get("source_flow_name")
                or result.get("target_flow_id") != target.get("id")
                or result.get("target_flow_name") != target.get("name")
                or result.get("semantic_status") != "accepted"
                or result.get("failure_type") is not None
                or result.get("failure_reason") is not None
                or result.get("candidate_count") != EXPECTED_CANDIDATE_COUNT
                or result.get("candidate_source_count") != 1
            ):
                raise ValueError(f"accepted Flow result {expected_position} drifted")
            attempt = _integer(result.get("accepted_attempt"), "accepted attempt")
            output_root = _safe_relative_path(
                result.get("action_output_root"), "raw action output root"
            )
            base = f"{output_root}/attempt-{attempt}"
            raw_paths = {
                "execution": f"{base}/execution.json",
                "request": f"{base}/inputs/flow-identity-preflight.json",
                "decision": f"{base}/outputs/identity-decision.json",
                "candidates": f"{base}/outputs/identity-candidates.jsonl",
                "sources": f"{base}/outputs/identity-candidate-sources.json",
                "semantic": f"{base}/semantic-validation.json",
            }
            if any(path not in archive for path in raw_paths.values()):
                raise ValueError(f"Flow result {expected_position} raw evidence is incomplete")
            raw_request = _parse_json(archive[raw_paths["request"]], "raw Flow request")
            raw_decision = _parse_json(
                archive[raw_paths["decision"]], "raw Flow decision"
            )
            candidates = _parse_jsonl(
                archive[raw_paths["candidates"]], "raw Flow candidates"
            )
            try:
                sources = _array(
                    json.loads(archive[raw_paths["sources"]]), "candidate sources"
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("raw candidate sources are invalid") from error
            semantic = _parse_json(
                archive[raw_paths["semantic"]], "raw semantic validation"
            )
            raw_target = _object(raw_decision.get("target"), "raw decision target")
            raw_fields = _object(raw_target.get("fields"), "raw decision target fields")
            candidate_ids = {
                (
                    _text(candidate.get("id"), "candidate ID"),
                    _text(candidate.get("version"), "candidate version"),
                )
                for candidate in candidates
            }
            if (
                raw_request != request
                or raw_target.get("id") != target.get("id")
                or raw_target.get("names") != [target.get("name")]
                or any(
                    raw_fields.get(key) != target.get(key)
                    for key in ("type_of_dataset", "flow_property", "reference_unit")
                )
                or raw_decision.get("status") != result.get("status")
                or raw_decision.get("decision") != result.get("decision")
                or raw_decision.get("confidence") != result.get("confidence")
                or raw_decision.get("candidates") != candidates
                or raw_decision.get("candidate_sources") != sources
                or semantic.get("status") != result.get("status")
                or semantic.get("decision") != result.get("decision")
                or result.get("decision_sha256") != _sha256(archive[raw_paths["decision"]])
                or result.get("candidates_sha256") != _sha256(archive[raw_paths["candidates"]])
                or result.get("candidate_sources_sha256") != _sha256(archive[raw_paths["sources"]])
                or len(candidates) != EXPECTED_CANDIDATE_COUNT
                or candidate_ids != catalog_identities
                or len(sources) != 1
                or not isinstance(sources[0], dict)
                or sources[0].get("kind") != "file"
                or sources[0].get("row_count") != EXPECTED_CANDIDATE_COUNT
            ):
                raise ValueError(f"Flow result {expected_position} raw evidence drifted")
            classification = classify_candidate_evidence(action, candidates)
            profile = {
                "schema_version": 1,
                "position": expected_position,
                "batch_index": index,
                "action_key": action["action_key"],
                "source_flow_id": action["source_flow_id"],
                "source_flow_name": action["source_flow_name"],
                "target": target,
                "native_status": result["status"],
                "native_decision": result["decision"],
                "native_confidence": result["confidence"],
                **classification,
                "evidence": {
                    "native_decision_sha256": result["decision_sha256"],
                    "candidates_sha256": result["candidates_sha256"],
                    "candidate_sources_sha256": result["candidate_sources_sha256"],
                    "raw_archive_path": verified_artifacts["raw_archive"]["path"],
                    "raw_archive_sha256": verified_artifacts["raw_archive"]["sha256"],
                    "raw_action_output_root": output_root,
                    "accepted_attempt": attempt,
                },
            }
            profiles.append(profile)
            native_results[expected_position] = result
            aggregate_decisions[str(result["decision"])] += 1
        aggregate_candidates += _integer(
            report.get("candidate_observation_count"), "candidate observation count"
        )
        aggregate_sources += _integer(
            report.get("candidate_source_observation_count"),
            "candidate source observation count",
        )
        aggregate_raw_files += _integer(
            report.get("raw_action_file_count"), "raw action file count"
        )
        aggregate_native_calls += _integer(
            report.get("native_invocation_executed_count"), "native invocation count"
        )
        verified_index.append(
            {
                **{key: value for key, value in row.items() if key != "artifacts"},
                "artifacts": verified_artifacts,
                "verification_status": "content_and_raw_manifest_verified",
            }
        )

    if (
        len(profiles) != EXPECTED_FLOW_COUNT
        or [row["position"] for row in profiles]
        != list(range(1, EXPECTED_FLOW_COUNT + 1))
        or aggregate_decisions != Counter(
            {"reuse": 1, "create_new": 5, "manual_review": 1453}
        )
        or aggregate_candidates != 18967
        or aggregate_sources != EXPECTED_FLOW_COUNT
        or aggregate_raw_files != 8754
        or aggregate_native_calls != EXPECTED_FLOW_COUNT
    ):
        raise ValueError("canonical native evidence aggregate drifted")

    reference_report = _parse_json(
        reference_resolution_report_bytes, "reference Flow resolution report"
    )
    reference_decisions = _parse_jsonl(
        reference_decisions_bytes, "reference Flow decisions"
    )
    authoring_report = _parse_json(
        authoring_execution_report_bytes, "reference Flow authoring report"
    )
    authoring_branches = _parse_jsonl(
        authoring_branches_bytes, "reference Flow authoring branches"
    )
    staged_flows = [
        (
            _parse_json(staged_high_voltage_flow_bytes, "staged high-voltage Flow"),
            input_bindings[9],
        ),
        (
            _parse_json(staged_construction_flow_bytes, "staged construction Flow"),
            input_bindings[10],
        ),
    ]
    if (
        reference_report.get("contract") != REFERENCE_RESOLUTION_CONTRACT
        or reference_report.get("status") != "uslci_reference_flow_resolution_compiled"
        or reference_report.get("plan_version") != plan_version
        or reference_report.get("reference_flow_decision_count") != 3
        or reference_report.get("retained_native_reuse_count") != 1
        or reference_report.get("create_new_intent_count") != 2
        or reference_report.get("blocked_reference_flow_count") != 0
        or [row.get("position") for row in reference_decisions] != [1, 2, 3]
        or authoring_report.get("contract") != AUTHORING_EXECUTION_CONTRACT
        or authoring_report.get("status")
        != "uslci_reference_flow_authoring_execution_completed"
        or authoring_report.get("completed_flow_count") != 2
        or authoring_report.get("failed_flow_count") != 0
        or authoring_report.get("staged_flow_registration_count") != 2
        or authoring_report.get("catalog_flow_creation_executed_count") != 0
        or len(authoring_branches) != 2
    ):
        raise ValueError("accepted reference Flow authority drifted")
    branch_by_target = {
        _text(row.get("target_flow_id"), "authoring branch target ID"): row
        for row in authoring_branches
    }
    staged_by_id = {}
    for flow, binding in staged_flows:
        flow_id, base_name, flow_type = _flow_identity(flow)
        if flow_type != "Product flow":
            raise ValueError("staged reference Flow type drifted")
        branch = branch_by_target.get(flow_id)
        if (
            not isinstance(branch, dict)
            or branch.get("status") != "staged_flow_native_gates_passed"
            or branch.get("staged_flow_sha256") != binding["sha256"]
            or branch.get("staged_flow_bytes") != binding["bytes"]
            or branch.get("catalog_entity_created") is not False
            or branch.get("remote_write_executed") is not False
            or branch.get("qa_blocker_count") != 0
        ):
            raise ValueError("staged reference Flow branch drifted")
        staged_by_id[flow_id] = {"base_name": base_name, "binding": binding}

    profile_by_position = {int(row["position"]): row for row in profiles}
    automatic: List[JsonObject] = []
    automatic_positions = set()
    for reference in reference_decisions:
        position = _integer(reference.get("position"), "reference decision position")
        profile = profile_by_position[position]
        target = _object(profile.get("target"), "profile target")
        if (
            reference.get("action_key") != profile.get("action_key")
            or reference.get("target_flow_id") != target.get("id")
            or reference.get("native_decision") != profile.get("native_decision")
            or reference.get("evidence", {}).get("native_decision_sha256")
            != profile.get("evidence", {}).get("native_decision_sha256")
        ):
            raise ValueError("reference decision no longer binds native evidence")
        decision = reference.get("resolution_decision")
        if position == 1:
            if decision != "reuse" or reference.get("remote_duplicate_unknown") is not False:
                raise ValueError("reference reuse authority drifted")
            status = "reuse_existing_local"
            staged = None
        else:
            if decision != "create_new_intent" or target.get("id") not in staged_by_id:
                raise ValueError("reference staged create-new authority drifted")
            status = "create_new_intent_staged"
            staged = staged_by_id[str(target["id"])]["binding"]
        automatic.append(
            {
                "schema_version": 1,
                "position": position,
                "action_key": profile["action_key"],
                "source_flow_id": profile["source_flow_id"],
                "target": target,
                "resolution_decision": decision,
                "resolution_status": status,
                "decision_authority": "accepted_reference_flow_resolution",
                "staged_flow_binding": staged,
                "remote_duplicate_unknown": bool(reference.get("remote_duplicate_unknown")),
                "flow_creation_authorized": False,
                "catalog_entity_created": False,
                "evidence": {
                    **_object(profile.get("evidence"), "profile evidence"),
                    "reference_decision_id": reference.get("decision_id"),
                },
            }
        )
        automatic_positions.add(position)
    for profile in profiles:
        position = int(profile["position"])
        if position in automatic_positions or profile.get("native_decision") == "manual_review":
            continue
        if profile.get("native_decision") != "create_new":
            raise ValueError("unexpected non-manual native Flow decision")
        automatic.append(
            {
                "schema_version": 1,
                "position": position,
                "action_key": profile["action_key"],
                "source_flow_id": profile["source_flow_id"],
                "target": profile["target"],
                "resolution_decision": "create_new_intent",
                "resolution_status": "bounded_catalog_create_new_intent",
                "decision_authority": "native_identity_preflight",
                "staged_flow_binding": None,
                "remote_duplicate_unknown": True,
                "flow_creation_authorized": False,
                "catalog_entity_created": False,
                "evidence": profile["evidence"],
            }
        )
        automatic_positions.add(position)
    automatic.sort(key=lambda row: int(row["position"]))
    if len(automatic) != EXPECTED_AUTOMATIC_COUNT or len(automatic_positions) != 8:
        raise ValueError("preserved automatic Flow decision count drifted")

    resolution_actions = []
    for profile in profiles:
        position = int(profile["position"])
        if position in automatic_positions:
            continue
        if profile.get("native_decision") != "manual_review":
            raise ValueError("pending resolution action is not native manual review")
        evidence = _object(profile.get("evidence"), "profile evidence")
        action_key = _sha256(
            _json_bytes(
                {
                    "contract": CONTRACT,
                    "policy_version": POLICY_VERSION,
                    "source_action_key": profile["action_key"],
                    "target": profile["target"],
                    "native_decision_sha256": evidence["native_decision_sha256"],
                    "candidates_sha256": evidence["candidates_sha256"],
                    "raw_archive_sha256": evidence["raw_archive_sha256"],
                }
            )
        )[:16]
        resolution_actions.append(
            {
                "schema_version": 1,
                "position": position,
                "resolution_action_key": action_key,
                "source_action_key": profile["action_key"],
                "source_flow_id": profile["source_flow_id"],
                "source_flow_name": profile["source_flow_name"],
                "target": profile["target"],
                "policy_version": POLICY_VERSION,
                "policy_group": profile["policy_group"],
                "decision_execution_mode": profile["decision_execution_mode"],
                "allowed_resolution_decisions": profile[
                    "allowed_resolution_decisions"
                ],
                "required_assertions": (
                    [
                        "all_candidates_verified",
                        "target_is_elementary_flow",
                        "every_candidate_is_product_flow",
                        "bounded_catalog_only",
                    ]
                    if profile["policy_group"]
                    == "bounded_catalog_entity_type_mismatch"
                    else [
                        "all_candidates_verified",
                        "distinguishing_name_or_carrier_semantics_evaluated",
                        "flow_property_and_unit_evaluated",
                        "native_score_not_used_as_equivalence",
                    ]
                ),
                "candidate_profile": {
                    key: profile[key]
                    for key in (
                        "candidate_type_counts",
                        "all_candidates_hard_type_mismatched",
                        "top_native_match_score",
                        "top_native_match_candidate_count",
                        "exact_normalized_name_candidate_count",
                        "decision_hint_counts",
                    )
                },
                "remote_duplicate_unknown": True,
                "flow_creation_authorized": False,
                "evidence": evidence,
            }
        )
    resolution_actions.sort(key=lambda row: int(row["position"]))
    policy_counts = Counter(str(row["policy_group"]) for row in resolution_actions)
    if (
        len(resolution_actions) != EXPECTED_PENDING_COUNT
        or policy_counts
        != Counter(
            {
                "bounded_catalog_entity_type_mismatch": 1450,
                "product_identity_semantic_review": 1,
            }
        )
    ):
        raise ValueError("Flow resolution policy group counts drifted")
    resolution_batches = build_resolution_batches(resolution_actions)
    if len(resolution_batches) != 15 or resolution_batches[-1]["action_count"] != 51:
        raise ValueError("Flow resolution batch partition drifted")
    policy_groups = [
        {
            "schema_version": 1,
            "policy_group": "product_identity_semantic_review",
            "policy_version": POLICY_VERSION,
            "action_count": 1,
            "decision_execution_mode": "ai_semantic",
            "native_score_is_decision_gate": False,
            "bounded_catalog_only": True,
            "allowed_resolution_decisions": [
                "reuse",
                "create_new_intent",
                "blocked_insufficient_evidence",
            ],
        },
        {
            "schema_version": 1,
            "policy_group": "bounded_catalog_entity_type_mismatch",
            "policy_version": POLICY_VERSION,
            "action_count": 1450,
            "decision_execution_mode": "deterministic_policy",
            "native_score_is_decision_gate": False,
            "bounded_catalog_only": True,
            "allowed_resolution_decisions": ["create_new_intent"],
        },
    ]
    failures: List[JsonObject] = []
    detail_rows = {
        "verified_batch_index": verified_index,
        "native_evidence_profiles": profiles,
        "automatic_decisions": automatic,
        "resolution_actions": resolution_actions,
        "policy_groups": policy_groups,
        "resolution_batches": resolution_batches,
        "failures": failures,
    }
    artifact_names = {
        "verified_batch_index": "verified-uslci-native-flow-batch-index.jsonl",
        "native_evidence_profiles": "uslci-native-flow-evidence-profiles.jsonl",
        "automatic_decisions": "uslci-flow-automatic-decisions.jsonl",
        "resolution_actions": "uslci-flow-identity-resolution-actions.jsonl",
        "policy_groups": "uslci-flow-identity-resolution-policy-groups.jsonl",
        "resolution_batches": "uslci-flow-identity-resolution-batches.jsonl",
        "failures": "uslci-flow-identity-resolution-plan-failures.jsonl",
    }
    detail_bytes = {
        key: _jsonl_bytes(rows) for key, rows in detail_rows.items()
    }
    artifact_bindings = {
        key: _artifact_binding(artifact_names[key], content)
        for key, content in detail_bytes.items()
    }
    plan_version_out = _sha256(
        _json_bytes(
            {
                "contract": CONTRACT,
                "logic_version": LOGIC_VERSION,
                "policy_version": POLICY_VERSION,
                "input_bindings": input_bindings,
                "artifact_bindings": artifact_bindings,
            }
        )
    )[:16]
    report = {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "policy_version": POLICY_VERSION,
        "status": "uslci_flow_identity_resolution_plan_compiled",
        "scope_id": plan.get("scope_id"),
        "revision": plan.get("revision"),
        "input_version": input_version,
        "source_preflight_plan_version": plan_version,
        "resolution_plan_version": plan_version_out,
        "canonical_batch_count": 15,
        "canonical_flow_action_count": EXPECTED_FLOW_COUNT,
        "canonical_position_start": 1,
        "canonical_position_end": EXPECTED_FLOW_COUNT,
        "native_invocation_observation_count": aggregate_native_calls,
        "native_candidate_observation_count": aggregate_candidates,
        "native_candidate_source_observation_count": aggregate_sources,
        "native_raw_action_file_count": aggregate_raw_files,
        "native_decision_counts": dict(sorted(aggregate_decisions.items())),
        "automatic_decision_count": len(automatic),
        "retained_reuse_decision_count": sum(
            row["resolution_decision"] == "reuse" for row in automatic
        ),
        "create_new_intent_decision_count": sum(
            str(row["resolution_decision"]).startswith("create_new")
            for row in automatic
        ),
        "staged_reference_flow_decision_count": sum(
            row["resolution_status"] == "create_new_intent_staged"
            for row in automatic
        ),
        "pending_resolution_action_count": len(resolution_actions),
        "policy_group_counts": dict(sorted(policy_counts.items())),
        "resolution_batch_count": len(resolution_batches),
        "maximum_resolution_batch_size": MAX_RESOLUTION_BATCH_SIZE,
        "selected_resolution_batch_index": 1,
        "compilation_failure_count": 0,
        "remote_duplicate_unknown_count": sum(
            bool(row["remote_duplicate_unknown"]) for row in automatic
        )
        + len(resolution_actions),
        "project_event_granularity": "aggregate_batch_only",
        "external_patterns": [
            {
                "project": "bazelbuild/bazel",
                "installed": False,
                "local_landing": "content_addressed_resolution_action_keys",
            },
            {
                "project": "open-policy-agent/opa",
                "installed": False,
                "local_landing": "versioned_policy_groups_and_aggregate_decision_logs",
            },
            {
                "project": "argoproj/argo-workflows",
                "installed": False,
                "local_landing": "bounded_resolution_batches_and_terminal_collection",
            },
        ],
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
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "input_bindings": input_bindings,
        "artifact_bindings": artifact_bindings,
        "routing": {"next_internal_route": NEXT_ROUTE, "pause_internal_work": False},
        "next_action": "Execute exact USLCI Flow identity-resolution batch 1.",
    }
    return {**report, **detail_rows}


def materialize_uslci_flow_identity_resolution_plan(
    result: Mapping[str, object], out_dir: Path
) -> Dict[str, Path]:
    output = out_dir.resolve() / "outputs"
    output.mkdir(parents=True, exist_ok=True)
    names = {
        "report": "uslci-flow-identity-resolution-plan.json",
        "verified_batch_index": "verified-uslci-native-flow-batch-index.jsonl",
        "native_evidence_profiles": "uslci-native-flow-evidence-profiles.jsonl",
        "automatic_decisions": "uslci-flow-automatic-decisions.jsonl",
        "resolution_actions": "uslci-flow-identity-resolution-actions.jsonl",
        "policy_groups": "uslci-flow-identity-resolution-policy-groups.jsonl",
        "resolution_batches": "uslci-flow-identity-resolution-batches.jsonl",
        "failures": "uslci-flow-identity-resolution-plan-failures.jsonl",
    }
    paths = {key: output / name for key, name in names.items()}
    detail = set(names) - {"report"}
    for key in detail:
        rows = result.get(key)
        if not isinstance(rows, list):
            raise ValueError(f"resolution plan {key} is missing")
        paths[key].write_bytes(_jsonl_bytes(rows))
    report = {key: value for key, value in result.items() if key not in detail}
    paths["report"].write_bytes(_json_bytes(report))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in EXPECTED_INPUT_NAMES:
        option = name.replace("_", "-")
        parser.add_argument(f"--{option}", type=Path, required=True)
        parser.add_argument(f"--{option}-binding", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    values = {}
    for name in EXPECTED_INPUT_NAMES:
        path = getattr(args, name)
        values[f"{name}_bytes"] = path.read_bytes()
        values[f"{name}_binding"] = json.loads(
            getattr(args, f"{name}_binding").read_bytes()
        )
    result = compile_uslci_flow_identity_resolution_plan(
        **values, artifact_root=args.artifact_root
    )
    materialize_uslci_flow_identity_resolution_plan(result, args.out_dir)
    detail = {
        "verified_batch_index",
        "native_evidence_profiles",
        "automatic_decisions",
        "resolution_actions",
        "policy_groups",
        "resolution_batches",
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
