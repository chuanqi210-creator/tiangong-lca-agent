"""Resolve USLCI quantitative-reference Flow identities without authoring them."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import tarfile
from pathlib import Path, PurePosixPath
from typing import Dict, List, Mapping, Sequence, Tuple


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_reference_flow_resolution/v1"
POLICY_VERSION = "2026-07-17.1"
BATCH_CONTRACT = "lca_research_kernel.uslci_native_slice_preflight_batch/v1"
ACTION_CONTRACT = "lca_research_kernel.uslci_flow_identity_preflight_action/v1"


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _jsonl_bytes(rows: Sequence[JsonObject]) -> bytes:
    return b"".join(_json_bytes(row) for row in rows)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _array(value: object, label: str) -> List[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
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


def _parse_jsonl(content: bytes, label: str) -> List[JsonObject]:
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
            raise ValueError(f"{label} row {index} is not valid JSON") from error
    if not rows:
        raise ValueError(f"{label} is empty")
    return rows


def _binding(value: object, content: bytes, label: str) -> JsonObject:
    row = _object(value, label)
    path = _text(row.get("path"), f"{label} path")
    digest = _text(row.get("sha256"), f"{label} sha256")
    size = _integer(row.get("bytes"), f"{label} bytes")
    if digest != _sha256(content) or size != len(content):
        raise ValueError(f"{label} hash or byte count drifted")
    return {"path": path, "sha256": digest, "bytes": size}


def _safe_member_name(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("raw archive contains an unsafe member path")
    normalized = str(path)
    if normalized != name:
        raise ValueError("raw archive member path is not canonical")
    return normalized


def _verified_archive(
    archive_bytes: bytes, manifest_rows: Sequence[JsonObject]
) -> Dict[str, bytes]:
    expected: Dict[str, JsonObject] = {}
    for index, row in enumerate(manifest_rows, start=1):
        name = _safe_member_name(_text(row.get("path"), f"manifest row {index} path"))
        if name in expected:
            raise ValueError("raw manifest contains a duplicate path")
        _text(row.get("sha256"), f"manifest row {index} sha256")
        _integer(row.get("bytes"), f"manifest row {index} bytes")
        expected[name] = row
    observed: Dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as archive:
            for member in archive.getmembers():
                name = _safe_member_name(member.name)
                if not member.isfile() or member.issym() or member.islnk():
                    raise ValueError("raw archive contains a non-regular member")
                if name in observed:
                    raise ValueError("raw archive contains a duplicate member")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError("raw archive member cannot be read")
                observed[name] = stream.read()
    except (tarfile.TarError, OSError) as error:
        raise ValueError("raw archive is not a valid tar") from error
    if set(observed) != set(expected):
        raise ValueError("raw archive and manifest paths disagree")
    for name, content in observed.items():
        row = expected[name]
        if row.get("sha256") != _sha256(content) or row.get("bytes") != len(content):
            raise ValueError(f"raw archive member disagrees with manifest: {name}")
    return observed


def _normalized_name(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _candidate_assertion(
    *, action: JsonObject, candidate: JsonObject
) -> JsonObject:
    target = _object(action.get("target"), "Flow action target")
    names = [
        value.strip()
        for value in _array(candidate.get("names"), "candidate names")
        if isinstance(value, str) and value.strip()
    ]
    if not names:
        raise ValueError("candidate has no name")
    fields = _object(candidate.get("fields"), "candidate fields")
    expected_name = _normalized_name(_text(target.get("name"), "target name"))
    observed_names = [_normalized_name(name) for name in names]
    expected_type = _text(target.get("type_of_dataset"), "target Flow type")
    expected_property = _text(target.get("flow_property"), "target Flow property")
    expected_unit = _text(target.get("reference_unit"), "target reference unit")
    observed_type = fields.get("type_of_dataset")
    observed_property = fields.get("flow_property")
    observed_unit = fields.get("reference_unit")
    checks = {
        "distinguishing_name_semantics": expected_name in observed_names,
        "flow_type": observed_type == expected_type,
        "flow_property": observed_property == expected_property,
        "reference_unit": observed_unit == expected_unit,
    }
    reasons = [f"{name}_mismatch" for name, passed in checks.items() if not passed]
    return {
        "schema_version": 1,
        "assertion_id": _sha256(
            _json_bytes(
                {
                    "policy_version": POLICY_VERSION,
                    "action_key": action["action_key"],
                    "candidate_id": candidate.get("id"),
                }
            )
        )[:16],
        "policy_version": POLICY_VERSION,
        "position": action["position"],
        "action_key": action["action_key"],
        "source_flow_id": action["source_flow_id"],
        "target_flow_id": target["id"],
        "candidate_id": _text(candidate.get("id"), "candidate ID"),
        "candidate_names": names,
        "native_match_score": _integer(candidate.get("match_score"), "candidate match score"),
        "expected": {
            "name": target["name"],
            "type_of_dataset": expected_type,
            "flow_property": expected_property,
            "reference_unit": expected_unit,
        },
        "observed": {
            "names": names,
            "type_of_dataset": observed_type,
            "flow_property": observed_property,
            "reference_unit": observed_unit,
        },
        "checks": checks,
        "passed": not reasons,
        "failure_reasons": reasons,
    }


def _artifact_binding(name: str, content: bytes) -> JsonObject:
    return {"path": name, "sha256": _sha256(content), "bytes": len(content)}


def compile_uslci_reference_flow_resolution(
    *,
    batch_report_bytes: bytes,
    batch_report_binding: Mapping[str, object],
    reference_results_bytes: bytes,
    reference_results_binding: Mapping[str, object],
    flow_actions_bytes: bytes,
    flow_actions_binding: Mapping[str, object],
    action_graph_bytes: bytes,
    action_graph_binding: Mapping[str, object],
    action_output_manifest_bytes: bytes,
    action_output_manifest_binding: Mapping[str, object],
    raw_archive_bytes: bytes,
    raw_archive_binding: Mapping[str, object],
) -> JsonObject:
    contents = {
        "batch_report": batch_report_bytes,
        "reference_results": reference_results_bytes,
        "flow_actions": flow_actions_bytes,
        "action_graph": action_graph_bytes,
        "action_output_manifest": action_output_manifest_bytes,
        "raw_archive": raw_archive_bytes,
    }
    supplied_bindings = {
        "batch_report": batch_report_binding,
        "reference_results": reference_results_binding,
        "flow_actions": flow_actions_binding,
        "action_graph": action_graph_binding,
        "action_output_manifest": action_output_manifest_binding,
        "raw_archive": raw_archive_binding,
    }
    input_bindings = [
        _binding(supplied_bindings[name], contents[name], f"{name} binding")
        for name in contents
    ]
    batch = _parse_json(batch_report_bytes, "native preflight batch report")
    if (
        batch.get("contract") != BATCH_CONTRACT
        or batch.get("status")
        != "uslci_native_slice_preflight_batch_reference_resolution_required"
        or batch.get("batch_index") != 1
        or batch.get("requested_action_count") != 100
        or batch.get("action_semantic_accepted_count") != 100
        or batch.get("action_failure_count") != 0
        or batch.get("quantitative_reference_flow_count") != 3
        or batch.get("routing", {}).get("next_internal_route")
        != "resolve-uslci-native-reference-flow-preflight-blockers"
    ):
        raise ValueError("native preflight batch is not eligible for reference resolution")
    references = _parse_jsonl(reference_results_bytes, "reference Flow results")
    if len(references) != 3 or [row.get("position") for row in references] != [1, 2, 3]:
        raise ValueError("reference Flow results must cover positions 1 through 3")
    actions = _parse_jsonl(flow_actions_bytes, "Flow actions")
    action_by_key: Dict[str, JsonObject] = {}
    for row in actions:
        key = _text(row.get("action_key"), "Flow action key")
        if key in action_by_key:
            raise ValueError("Flow action key is duplicated")
        action_by_key[key] = row
    graph = _parse_json(action_graph_bytes, "preflight action graph")
    nodes = {
        _text(_object(row, "action graph node").get("action_key"), "graph node key"):
        _object(row, "action graph node")
        for row in _array(graph.get("nodes"), "action graph nodes")
    }
    edges = [_object(row, "action graph edge") for row in _array(graph.get("edges"), "action graph edges")]
    if graph.get("edge_count") != len(edges) or graph.get("node_count") != len(nodes):
        raise ValueError("action graph counts drifted")
    manifest = _parse_jsonl(action_output_manifest_bytes, "raw action output manifest")
    archive = _verified_archive(raw_archive_bytes, manifest)

    decisions = []
    assertions = []
    action_decision: Dict[str, str] = {}
    for reference in references:
        action_key = _text(reference.get("action_key"), "reference action key")
        if action_key not in action_by_key:
            raise ValueError("reference result has no bound Flow action")
        action = action_by_key[action_key]
        if action.get("contract") != ACTION_CONTRACT:
            raise ValueError("reference Flow action contract drifted")
        target = _object(action.get("target"), "Flow action target")
        request = _object(action.get("request"), "Flow action request")
        if (
            action.get("position") != reference.get("position")
            or action.get("source_flow_id") != reference.get("source_flow_id")
            or action.get("source_flow_name") != reference.get("source_flow_name")
            or target.get("id") != reference.get("target_flow_id")
            or target.get("name") != reference.get("target_flow_name")
            or request.get("target") != {
                **target,
                "source_flow_id": action.get("source_flow_id"),
                "source_repository_version": _object(request.get("target"), "request target").get("source_repository_version"),
            }
        ):
            raise ValueError("reference result, action and request target drifted")
        attempt = reference.get("accepted_attempt", 1)
        if not isinstance(attempt, int) or attempt < 1:
            raise ValueError("reference accepted attempt is invalid")
        root = f"{_text(reference.get('action_output_root'), 'action output root')}/attempt-{attempt}"
        paths = {
            "request": f"{root}/inputs/flow-identity-preflight.json",
            "decision": f"{root}/outputs/identity-decision.json",
            "candidates": f"{root}/outputs/identity-candidates.jsonl",
            "sources": f"{root}/outputs/identity-candidate-sources.json",
            "semantic": f"{root}/semantic-validation.json",
        }
        if any(path not in archive for path in paths.values()):
            raise ValueError("raw archive lacks required reference evidence")
        raw_request = _parse_json(archive[paths["request"]], "raw Flow request")
        raw_decision = _parse_json(archive[paths["decision"]], "raw Flow decision")
        candidates = _parse_jsonl(archive[paths["candidates"]], "raw Flow candidates")
        try:
            candidate_sources = _array(json.loads(archive[paths["sources"]]), "candidate sources")
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("raw candidate sources are invalid") from error
        semantic = _parse_json(archive[paths["semantic"]], "semantic validation")
        raw_target = _object(raw_decision.get("target"), "raw decision target")
        raw_fields = _object(raw_target.get("fields"), "raw decision target fields")
        if (
            raw_request != request
            or raw_target.get("id") != target.get("id")
            or raw_target.get("names") != [target.get("name")]
            or any(raw_fields.get(key) != target.get(key) for key in ("type_of_dataset", "flow_property", "reference_unit"))
            or raw_decision.get("status") != reference.get("status")
            or raw_decision.get("decision") != reference.get("decision")
            or raw_decision.get("confidence") != reference.get("confidence")
            or raw_decision.get("candidates") != candidates
            or raw_decision.get("candidate_sources") != candidate_sources
            or semantic.get("status") != reference.get("status")
            or semantic.get("decision") != reference.get("decision")
            or reference.get("candidate_count") != len(candidates)
            or reference.get("candidate_source_count") != len(candidate_sources)
            or reference.get("candidates_sha256") != _sha256(archive[paths["candidates"]])
            or reference.get("candidate_sources_sha256") != _sha256(archive[paths["sources"]])
            or reference.get("decision_sha256") != _sha256(archive[paths["decision"]])
            or reference.get("semantic_status") != "accepted"
        ):
            raise ValueError("raw reference evidence disagrees with accepted result")
        source_id = _text(action.get("source_flow_id"), "source Flow ID")
        target_id = _text(target.get("id"), "target Flow ID")
        complete_target = all(
            isinstance(target.get(key), str) and bool(str(target.get(key)).strip())
            for key in ("id", "name", "type_of_dataset", "flow_property", "reference_unit")
        )
        native_reuse = reference.get("status") == "passed" and reference.get("decision") == "reuse"
        selected = [
            row
            for row in candidates
            if row.get("id") == target_id
            and (
                row.get("decision_hint") == "reuse"
                or "same_dataset_id" in row.get("match_reasons", [])
            )
        ]
        rejection_reasons: List[str] = []
        if native_reuse and len(selected) == 1:
            resolution_decision = "reuse"
            decision_status = "resolved_existing_identity"
            selected_candidate_id = target_id
        elif reference.get("decision") == "manual_review" and complete_target and source_id != target_id:
            candidate_assertions = [
                _candidate_assertion(action=action, candidate=candidate)
                for candidate in candidates
            ]
            candidate_assertions.sort(key=lambda row: str(row["candidate_id"]))
            assertions.extend(candidate_assertions)
            equivalent = [row for row in candidate_assertions if row["passed"]]
            if equivalent:
                resolution_decision = "blocked_insufficient_identity_evidence"
                decision_status = "blocked_equivalent_candidate_requires_native_confirmation"
                selected_candidate_id = None
            else:
                resolution_decision = "create_new_intent"
                decision_status = "resolved_new_local_identity_intent"
                selected_candidate_id = None
            rejection_reasons = sorted(
                {
                    reason
                    for row in candidate_assertions
                    for reason in row["failure_reasons"]
                }
            )
        else:
            resolution_decision = "blocked_insufficient_identity_evidence"
            decision_status = "blocked_incomplete_or_inconsistent_target"
            selected_candidate_id = None
        action_decision[action_key] = resolution_decision
        decisions.append(
            {
                "schema_version": 1,
                "decision_id": _sha256(
                    _json_bytes(
                        {
                            "policy_version": POLICY_VERSION,
                            "action_key": action_key,
                            "native_decision_sha256": reference.get("decision_sha256"),
                            "resolution_decision": resolution_decision,
                        }
                    )
                )[:16],
                "policy_version": POLICY_VERSION,
                "position": reference["position"],
                "action_key": action_key,
                "source_flow_id": source_id,
                "source_flow_name": action["source_flow_name"],
                "target_flow_id": target_id,
                "target_flow_name": target["name"],
                "type_of_dataset": target["type_of_dataset"],
                "flow_property": target["flow_property"],
                "reference_unit": target["reference_unit"],
                "native_status": reference["status"],
                "native_decision": reference["decision"],
                "native_confidence": reference["confidence"],
                "resolution_decision": resolution_decision,
                "resolution_status": decision_status,
                "selected_candidate_id": selected_candidate_id,
                "proposed_local_uuid_preserved": resolution_decision == "create_new_intent",
                "semantic_loss_markers": action.get("semantic_loss_markers", []),
                "candidate_rejection_reasons": rejection_reasons,
                "remote_duplicate_unknown": resolution_decision == "create_new_intent",
                "flow_materialized": False,
                "flow_creation_authorized": False,
                "evidence": {
                    "native_decision_sha256": reference["decision_sha256"],
                    "candidates_sha256": reference["candidates_sha256"],
                    "candidate_sources_sha256": reference["candidate_sources_sha256"],
                    "raw_archive_sha256": input_bindings[-1]["sha256"],
                },
            }
        )

    decisions.sort(key=lambda row: int(row["position"]))
    assertions.sort(key=lambda row: (int(row["position"]), str(row["candidate_id"])))
    dependency_gates = []
    reference_keys = set(action_decision)
    for edge in edges:
        source = _text(edge.get("from_action_key"), "dependency source action key")
        if source not in reference_keys:
            continue
        target_key = _text(edge.get("to_action_key"), "dependency target action key")
        if target_key not in nodes:
            raise ValueError("dependency target node is absent")
        decision = action_decision[source]
        identity_released = decision in {"reuse", "create_new_intent"}
        entity_gate = (
            "ready_existing_identity"
            if decision == "reuse"
            else "waiting_for_flow_authoring"
            if decision == "create_new_intent"
            else "blocked_identity_evidence"
        )
        dependency_gates.append(
            {
                "schema_version": 1,
                "from_action_key": source,
                "to_action_key": target_key,
                "downstream_action_kind": nodes[target_key].get("kind"),
                "dependency": edge.get("dependency"),
                "resolution_decision": decision,
                "identity_decision_gate": "released" if identity_released else "blocked",
                "entity_availability_gate": entity_gate,
                "downstream_execution_ready": entity_gate == "ready_existing_identity",
            }
        )
    dependency_gates.sort(key=lambda row: (str(row["from_action_key"]), str(row["to_action_key"])))
    if len(dependency_gates) != 10:
        raise ValueError("reference Flow dependency graph must contain exactly 10 edges")

    retained = sum(row["resolution_decision"] == "reuse" for row in decisions)
    create_new = sum(row["resolution_decision"] == "create_new_intent" for row in decisions)
    blocked = len(decisions) - retained - create_new
    ready = sum(row["entity_availability_gate"] == "ready_existing_identity" for row in dependency_gates)
    waiting = sum(row["entity_availability_gate"] == "waiting_for_flow_authoring" for row in dependency_gates)
    if blocked:
        next_route = "gather-uslci-reference-flow-identity-evidence"
        status = "uslci_reference_flow_resolution_blocked"
        next_action = "Gather narrower identity evidence for unresolved reference Flows."
    elif create_new:
        next_route = "compile-uslci-native-reference-flow-authoring-plan"
        status = "uslci_reference_flow_resolution_compiled"
        next_action = "Compile a native authoring plan for the two resolved new reference Flow identities."
    else:
        next_route = "execute-uslci-native-slice-process-identity-preflights"
        status = "uslci_reference_flow_resolution_compiled"
        next_action = "Execute eligible Process identity preflights."
    detail_bytes = {
        "decisions": _jsonl_bytes(decisions),
        "equivalence_assertions": _jsonl_bytes(assertions),
        "dependency_gates": _jsonl_bytes(dependency_gates),
    }
    artifact_names = {
        "decisions": "uslci-reference-flow-decisions.jsonl",
        "equivalence_assertions": "uslci-reference-flow-equivalence-assertions.jsonl",
        "dependency_gates": "uslci-reference-flow-dependency-gates.jsonl",
    }
    artifact_bindings = {
        key: _artifact_binding(artifact_names[key], content)
        for key, content in detail_bytes.items()
    }
    resolution_version = _sha256(
        _json_bytes(
            {
                "contract": CONTRACT,
                "policy_version": POLICY_VERSION,
                "input_bindings": input_bindings,
                "artifact_bindings": artifact_bindings,
            }
        )
    )[:16]
    report = {
        "schema_version": 1,
        "contract": CONTRACT,
        "policy_version": POLICY_VERSION,
        "status": status,
        "scope_id": batch.get("scope_id"),
        "revision": batch.get("revision"),
        "plan_version": batch.get("plan_version"),
        "resolution_version": resolution_version,
        "reference_flow_decision_count": len(decisions),
        "retained_native_reuse_count": retained,
        "create_new_intent_count": create_new,
        "blocked_reference_flow_count": blocked,
        "candidate_equivalence_assertion_count": len(assertions),
        "rejected_candidate_assertion_count": sum(not row["passed"] for row in assertions),
        "dependency_gate_count": len(dependency_gates),
        "identity_decision_released_dependency_count": sum(
            row["identity_decision_gate"] == "released" for row in dependency_gates
        ),
        "entity_ready_dependency_count": ready,
        "waiting_for_flow_authoring_dependency_count": waiting,
        "remote_candidate_search_executed_count": 0,
        "native_invocation_executed_count": 0,
        "flow_creation_executed_count": 0,
        "process_identity_preflight_executed_count": 0,
        "process_build_plan_validation_executed_count": 0,
        "process_creation_executed_count": 0,
        "model_connection_executed_count": 0,
        "model_mutation_executed_count": 0,
        "external_write_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "input_bindings": input_bindings,
        "artifact_bindings": artifact_bindings,
        "routing": {"next_internal_route": next_route, "pause_internal_work": False},
        "next_action": next_action,
    }
    return {
        **report,
        "decisions": decisions,
        "equivalence_assertions": assertions,
        "dependency_gates": dependency_gates,
    }


def materialize_uslci_reference_flow_resolution(
    result: Mapping[str, object], out_dir: Path
) -> Dict[str, Path]:
    root = out_dir.resolve() / "outputs"
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "report": root / "uslci-reference-flow-resolution.json",
        "decisions": root / "uslci-reference-flow-decisions.jsonl",
        "equivalence_assertions": root / "uslci-reference-flow-equivalence-assertions.jsonl",
        "dependency_gates": root / "uslci-reference-flow-dependency-gates.jsonl",
    }
    for key in ("decisions", "equivalence_assertions", "dependency_gates"):
        rows = result.get(key)
        if not isinstance(rows, list):
            raise ValueError(f"resolution {key} are missing")
        paths[key].write_bytes(_jsonl_bytes(rows))
    report = {
        key: value
        for key, value in result.items()
        if key not in {"decisions", "equivalence_assertions", "dependency_gates"}
    }
    paths["report"].write_bytes(_json_bytes(report))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in (
        "batch-report",
        "reference-results",
        "flow-actions",
        "action-graph",
        "action-output-manifest",
        "raw-archive",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
        parser.add_argument(f"--{name}-binding", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    values = {}
    for name in (
        "batch_report",
        "reference_results",
        "flow_actions",
        "action_graph",
        "action_output_manifest",
        "raw_archive",
    ):
        path = getattr(args, name)
        values[f"{name}_bytes"] = path.read_bytes()
        values[f"{name}_binding"] = json.loads(
            getattr(args, f"{name}_binding").read_bytes()
        )
    result = compile_uslci_reference_flow_resolution(**values)
    materialize_uslci_reference_flow_resolution(result, args.out_dir)
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key not in {"decisions", "equivalence_assertions", "dependency_gates"}
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
