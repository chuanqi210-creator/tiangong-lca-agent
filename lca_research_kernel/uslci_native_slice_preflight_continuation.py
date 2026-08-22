"""Execute an exact planned USLCI Flow identity-preflight continuation batch."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lca_research_kernel.uslci_native_slice_preflight_batch import (
    ACTION_CONTRACT,
    AUTOMATIC_DECISIONS,
    PLAN_CONTRACT,
    RUNTIME_CONTRACT,
    _action_key,
    _artifact_binding,
    _binding,
    _catalog_identities,
    _execute_action,
    _integer,
    _json_bytes,
    _manifest_rows,
    _object,
    _parse_json,
    _parse_jsonl,
    _peak_concurrency,
    _sha256,
    _text,
    create_deterministic_tar,
)


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_native_slice_preflight_continuation/v1"
LOGIC_VERSION = "2026-07-17.1"
PROCESS_AUTHORITY_CONTRACT = (
    "lca_research_kernel.uslci_native_slice_process_action_execution/v1"
)
FIRST_BATCH_CONTRACT = "lca_research_kernel.uslci_native_slice_preflight_batch/v1"
EXPECTED_INPUT_NAMES = (
    "plan_report",
    "flow_actions",
    "flow_batches",
    "previous_batch_report",
    "route_authority_report",
    "candidate_catalog",
    "runtime_report",
    "runtime_manifest",
)
MAX_CONCURRENCY = 4


def input_version_for_bindings(
    bindings: Sequence[JsonObject], batch_index: int
) -> str:
    return _sha256(
        _json_bytes(
            {
                "contract": CONTRACT,
                "logic_version": LOGIC_VERSION,
                "batch_index": batch_index,
                "maximum_native_concurrency": MAX_CONCURRENCY,
                "inputs": list(bindings),
            }
        )
    )[:16]


def _nested_binding(
    report: Mapping[str, object], key: str, binding: Mapping[str, object]
) -> None:
    row = _object(
        _object(report.get("artifact_bindings"), "plan artifact bindings").get(key),
        f"plan {key} binding",
    )
    if row.get("sha256") != binding["sha256"] or row.get("bytes") != binding["bytes"]:
        raise ValueError(f"plan {key} binding drifted")


def _contains_binding(
    report: Mapping[str, object], binding: Mapping[str, object]
) -> bool:
    return any(
        isinstance(row, dict)
        and row.get("sha256") == binding["sha256"]
        and row.get("bytes") == binding["bytes"]
        for row in report.get("input_bindings", [])
    )


def _validate_action(
    action: JsonObject,
    *,
    expected_position: int,
    catalog_sha256: str,
    runtime_sha256: str,
) -> None:
    request = _object(action.get("request"), "Flow action request")
    target = _object(request.get("target"), "Flow action target")
    remote = _object(request.get("remote_candidate_search"), "remote candidate policy")
    source_id = _text(action.get("source_flow_id"), "source Flow UUID")
    if (
        action.get("contract") != ACTION_CONTRACT
        or action.get("position") != expected_position
        or action.get("candidate_catalog_sha256") != catalog_sha256
        or action.get("runtime_tree_sha256") != runtime_sha256
        or action.get("remote_candidate_search_enabled") is not False
        or remote.get("enabled") is not False
        or action.get("native_command") != "flow identity-preflight"
        or target.get("source_flow_id") != source_id
        or action.get("target")
        != {
            key: target.get(key)
            for key in (
                "id",
                "name",
                "type_of_dataset",
                "flow_property",
                "reference_unit",
            )
        }
        or action.get("action_key")
        != _action_key(request, catalog_sha256, runtime_sha256)
    ):
        raise ValueError(f"Flow action {expected_position} contract drifted")


def _validate_inputs(
    *,
    batch_index: int,
    contents: Mapping[str, bytes],
    bindings: Mapping[str, JsonObject],
) -> Tuple[JsonObject, List[JsonObject], JsonObject, List[JsonObject], set, JsonObject]:
    if batch_index < 2 or batch_index > 15:
        raise ValueError("continuation batch index must be between 2 and 15")
    plan = _parse_json(contents["plan_report"], "native slice preflight plan")
    actions = _parse_jsonl(contents["flow_actions"], "Flow action ledger")
    descriptors = _parse_jsonl(contents["flow_batches"], "Flow batch descriptors")
    previous = _parse_json(contents["previous_batch_report"], "previous Flow batch report")
    authority = _parse_json(contents["route_authority_report"], "route authority report")
    catalog_rows, catalog_identities = _catalog_identities(contents["candidate_catalog"])
    runtime = _parse_json(contents["runtime_report"], "runtime verification report")
    manifest = _parse_json(contents["runtime_manifest"], "runtime manifest")
    if (
        plan.get("contract") != PLAN_CONTRACT
        or plan.get("status") != "uslci_native_slice_preflight_plan_compiled"
        or plan.get("flow_action_count") != 1459
        or plan.get("flow_batch_count") != 15
        or plan.get("maximum_flow_batch_size") != 100
        or plan.get("maximum_native_concurrency") != MAX_CONCURRENCY
        or plan.get("local_flow_candidate_count") != 13
        or len(actions) != 1459
        or len(descriptors) != 15
        or len(catalog_rows) != 13
    ):
        raise ValueError("native preflight plan/ledger cardinality drifted")
    _nested_binding(plan, "flow_actions", bindings["flow_actions"])
    _nested_binding(plan, "flow_batches", bindings["flow_batches"])
    if not _contains_binding(plan, bindings["candidate_catalog"]):
        raise ValueError("plan omitted the bound Flow candidate catalog")
    if (
        runtime.get("contract") != RUNTIME_CONTRACT
        or runtime.get("status") != "portable_native_tool_runtime_closure_verified"
        or runtime.get("runtime_content_closed") is not True
        or runtime.get("same_platform_relocation_proven") is not True
        or manifest.get("contract") != RUNTIME_CONTRACT
        or manifest.get("status") != "portable_native_tool_runtime_closure_compiled"
        or manifest.get("runtime_content_closed") is not True
        or runtime.get("tree_sha256") != manifest.get("tree_sha256")
        or runtime.get("tree_sha256") != plan.get("runtime_tree_sha256")
    ):
        raise ValueError("portable TianGong runtime binding drifted")

    descriptor_by_index = {
        _integer(row.get("batch_index"), "batch descriptor index"): row
        for row in descriptors
    }
    if set(descriptor_by_index) != set(range(1, 16)):
        raise ValueError("Flow batch descriptor indices drifted")
    descriptor = descriptor_by_index[batch_index]
    start = (batch_index - 1) * 100 + 1
    expected_count = min(100, 1459 - start + 1)
    end = start + expected_count - 1
    selected = actions[start - 1 : end]
    if (
        descriptor.get("start_position") != start
        or descriptor.get("end_position") != end
        or descriptor.get("action_count") != expected_count
        or descriptor.get("maximum_native_concurrency") != MAX_CONCURRENCY
        or descriptor.get("command_failure_retry_limit") != 1
        or descriptor.get("remote_candidate_search_enabled") is not False
        or descriptor.get("action_keys")
        != [row.get("action_key") for row in selected]
        or descriptor.get("source_flow_ids")
        != [row.get("source_flow_id") for row in selected]
    ):
        raise ValueError(f"Flow batch {batch_index} descriptor drifted")
    if len({row.get("action_key") for row in selected}) != expected_count or len(
        {row.get("source_flow_id") for row in selected}
    ) != expected_count:
        raise ValueError("selected Flow batch contains duplicate identities")
    catalog_sha = bindings["candidate_catalog"]["sha256"]
    runtime_sha = _text(runtime.get("tree_sha256"), "runtime tree SHA-256")
    for position, action in enumerate(selected, start=start):
        _validate_action(
            action,
            expected_position=position,
            catalog_sha256=catalog_sha,
            runtime_sha256=runtime_sha,
        )

    if (
        previous.get("contract")
        != (FIRST_BATCH_CONTRACT if batch_index == 2 else CONTRACT)
        or previous.get("batch_index") != batch_index - 1
        or previous.get("plan_version") != plan.get("plan_version")
        or previous.get("requested_action_count") != 100
        or previous.get("action_semantic_accepted_count") != 100
        or previous.get("action_failure_count") != 0
    ):
        raise ValueError("previous accepted Flow batch drifted")
    expected_route = f"execute-uslci-native-slice-preflight-batch-{batch_index}"
    if authority.get("routing", {}).get("next_internal_route") != expected_route:
        raise ValueError("continuation route authority drifted")
    if batch_index == 2:
        if (
            authority.get("contract") != PROCESS_AUTHORITY_CONTRACT
            or authority.get("status")
            != "uslci_native_slice_process_action_execution_completed"
            or authority.get("completed_process_count") != 5
            or authority.get("failed_process_count") != 0
            or authority.get("minimum_build_plan_passed_count") != 5
        ):
            raise ValueError("batch-2 Process route authority drifted")
    elif (
        authority.get("contract") != CONTRACT
        or authority.get("status") != "uslci_native_slice_preflight_continuation_completed"
        or authority.get("batch_index") != batch_index - 1
        or authority.get("continuation_version") != previous.get("continuation_version")
    ):
        raise ValueError("prior continuation route authority drifted")
    return plan, selected, descriptor, catalog_rows, catalog_identities, runtime


def run_uslci_native_slice_preflight_continuation(
    *,
    plan_report_bytes: bytes,
    plan_report_binding: Mapping[str, object],
    flow_actions_bytes: bytes,
    flow_actions_binding: Mapping[str, object],
    flow_batches_bytes: bytes,
    flow_batches_binding: Mapping[str, object],
    previous_batch_report_bytes: bytes,
    previous_batch_report_binding: Mapping[str, object],
    route_authority_report_bytes: bytes,
    route_authority_report_binding: Mapping[str, object],
    candidate_catalog_bytes: bytes,
    candidate_catalog_binding: Mapping[str, object],
    runtime_report_bytes: bytes,
    runtime_report_binding: Mapping[str, object],
    runtime_manifest_bytes: bytes,
    runtime_manifest_binding: Mapping[str, object],
    batch_index: int,
    candidate_catalog_path: Path,
    node_path: Path,
    cli_entry_path: Path,
    out_dir: Path,
) -> JsonObject:
    arguments = locals().copy()
    contents = {
        name: arguments[f"{name}_bytes"] for name in EXPECTED_INPUT_NAMES
    }
    bindings = {
        name: _binding(arguments[f"{name}_binding"], contents[name], f"{name} binding")
        for name in EXPECTED_INPUT_NAMES
    }
    plan, selected, descriptor, catalog_rows, catalog_identities, runtime = (
        _validate_inputs(
            batch_index=batch_index,
            contents=contents,
            bindings=bindings,
        )
    )
    catalog_path = Path(candidate_catalog_path).resolve()
    node = Path(node_path).resolve()
    cli = Path(cli_entry_path).resolve()
    if not catalog_path.is_file() or catalog_path.read_bytes() != candidate_catalog_bytes:
        raise ValueError("candidate catalog execution path drifted from its binding")
    if not node.is_file() or not cli.is_file():
        raise ValueError("copied Node and TianGong CLI entry must be files")
    root = Path(out_dir).resolve()
    raw_root = root / "raw-actions"
    raw_root.mkdir(parents=True, exist_ok=True)
    results = []
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as executor:
        futures = {
            executor.submit(
                _execute_action,
                action,
                candidate_catalog_path=catalog_path,
                candidate_count=len(catalog_rows),
                candidate_identities=catalog_identities,
                node_path=node,
                cli_entry_path=cli,
                raw_root=raw_root,
            ): action
            for action in selected
        }
        for future in as_completed(futures):
            results.append(future.result())
    peak = _peak_concurrency(results)
    results.sort(key=lambda row: int(row["position"]))
    for row in results:
        row.pop("_intervals", None)
    failures = [dict(row) for row in results if row["semantic_status"] != "accepted"]
    accepted = len(results) - len(failures)
    decision_counts: Dict[str, int] = {}
    for row in results:
        decision = row.get("decision")
        if isinstance(decision, str):
            decision_counts[decision] = decision_counts.get(decision, 0) + 1
    review_markers = sum(
        row.get("semantic_status") == "accepted"
        and (
            row.get("decision") not in AUTOMATIC_DECISIONS
            or row.get("status") != "passed"
            or int(row.get("blocker_count", 0)) > 0
        )
        for row in results
    )
    if failures:
        status = "uslci_native_slice_preflight_continuation_completed_with_failures"
        next_route = f"retry-uslci-native-slice-preflight-batch-{batch_index}-failures"
        next_action = f"Retry only failed batch-{batch_index} action keys."
    else:
        status = "uslci_native_slice_preflight_continuation_completed"
        next_index = batch_index + 1
        next_route = (
            f"execute-uslci-native-slice-preflight-batch-{next_index}"
            if batch_index < 15
            else "compile-uslci-native-slice-flow-identity-resolution-plan"
        )
        next_action = (
            f"Execute exact USLCI native Flow identity-preflight batch {next_index}."
            if batch_index < 15
            else "Compile the bounded Flow identity-resolution work plan."
        )
    outputs = root / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    prefix = f"uslci-native-slice-preflight-batch-{batch_index}"
    names = {
        "selected_batch": f"{prefix}-selected-actions.jsonl",
        "result_ledger": f"{prefix}-results.jsonl",
        "action_output_manifest": f"{prefix}-action-output-manifest.jsonl",
        "failures": f"{prefix}-failures.jsonl",
        "raw_archive": f"{prefix}-raw-results.tar",
    }
    selected_bytes = b"".join(_json_bytes(row) for row in selected)
    result_bytes = b"".join(_json_bytes(row) for row in results)
    failure_bytes = b"".join(_json_bytes(row) for row in failures)
    manifest_rows = _manifest_rows(raw_root)
    manifest_bytes = b"".join(_json_bytes(row) for row in manifest_rows)
    contents_out = {
        "selected_batch": selected_bytes,
        "result_ledger": result_bytes,
        "action_output_manifest": manifest_bytes,
        "failures": failure_bytes,
    }
    for key, content in contents_out.items():
        (outputs / names[key]).write_bytes(content)
    archive_path = outputs / names["raw_archive"]
    create_deterministic_tar(raw_root, archive_path)
    archive_bytes = archive_path.read_bytes()
    artifact_bindings = {
        key: _artifact_binding(outputs / names[key], content)
        for key, content in {**contents_out, "raw_archive": archive_bytes}.items()
    }
    input_version = input_version_for_bindings(
        [bindings[name] for name in EXPECTED_INPUT_NAMES], batch_index
    )
    continuation_version = _sha256(
        _json_bytes(
            {
                "logic_version": LOGIC_VERSION,
                "batch_index": batch_index,
                "input_version": input_version,
                "artifact_bindings": artifact_bindings,
            }
        )
    )[:16]
    report = {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "status": status,
        "scope_id": plan["scope_id"],
        "revision": plan["revision"],
        "plan_version": plan["plan_version"],
        "input_version": input_version,
        "continuation_version": continuation_version,
        "batch_index": batch_index,
        "start_position": descriptor["start_position"],
        "end_position": descriptor["end_position"],
        "requested_action_count": len(selected),
        "cumulative_completed_action_count": descriptor["end_position"],
        "remaining_flow_action_count": 1459 - int(descriptor["end_position"]),
        "action_semantic_accepted_count": accepted,
        "action_failure_count": len(failures),
        "native_invocation_executed_count": sum(
            int(row["invocation_count"]) for row in results
        ),
        "retried_action_count": sum(bool(row["retried"]) for row in results),
        "maximum_native_concurrency": MAX_CONCURRENCY,
        "observed_peak_native_concurrency": peak,
        "local_flow_candidate_count": len(catalog_rows),
        "candidate_source_observation_count": sum(
            int(row.get("candidate_source_count", 0)) for row in results
        ),
        "candidate_observation_count": sum(
            int(row.get("candidate_count", 0)) for row in results
        ),
        "decision_counts": decision_counts,
        "decision_blocker_action_count": review_markers,
        "remote_candidate_search_executed_count": 0,
        "remote_candidate_source_observation_count": 0,
        "network_read_executed_count": 0,
        "flow_creation_executed_count": 0,
        "process_identity_preflight_executed_count": 0,
        "process_build_plan_validation_executed_count": 0,
        "process_creation_executed_count": 0,
        "model_connection_executed_count": 0,
        "model_mutation_executed_count": 0,
        "remote_write_executed_count": 0,
        "external_write_executed_count": 0,
        "publication_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "runtime_tree_sha256": runtime["tree_sha256"],
        "tidas_cli_version": runtime.get("tiangong_cli", {}).get("version"),
        "input_bindings": [bindings[name] for name in EXPECTED_INPUT_NAMES],
        "artifact_bindings": artifact_bindings,
        "raw_action_file_count": len(manifest_rows),
        "routing": {"next_internal_route": next_route, "pause_internal_work": False},
        "next_action": next_action,
    }
    report_path = outputs / f"{prefix}.json"
    report_path.write_bytes(_json_bytes(report))
    return {
        **report,
        "selected_batch": selected,
        "results": results,
        "failures": failures,
        "report_path": str(report_path),
    }


def main(argv: Sequence[str] = None) -> int:
    parser = argparse.ArgumentParser()
    for name in EXPECTED_INPUT_NAMES:
        option = name.replace("_", "-")
        parser.add_argument(f"--{option}", type=Path, required=True)
        parser.add_argument(f"--{option}-binding", type=Path, required=True)
    parser.add_argument("--batch-index", type=int, required=True)
    parser.add_argument("--native-node", type=Path, required=True)
    parser.add_argument("--native-cli-entry", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    values = {}
    for name in EXPECTED_INPUT_NAMES:
        values[f"{name}_bytes"] = getattr(args, name).read_bytes()
        values[f"{name}_binding"] = json.loads(
            getattr(args, f"{name}_binding").read_bytes()
        )
    result = run_uslci_native_slice_preflight_continuation(
        **values,
        batch_index=args.batch_index,
        candidate_catalog_path=args.candidate_catalog,
        node_path=args.native_node,
        cli_entry_path=args.native_cli_entry,
        out_dir=args.out_dir,
    )
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key not in {"selected_batch", "results", "failures"}
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
