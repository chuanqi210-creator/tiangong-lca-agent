"""Execute one bounded USLCI Flow identity-preflight batch."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_native_slice_preflight_batch/v1"
LOGIC_VERSION = "2026-07-17.2"
PLAN_ACTION_LOGIC_VERSION = "2026-07-17.1"
PLAN_CONTRACT = "lca_research_kernel.uslci_native_slice_preflight_plan/v1"
ACTION_CONTRACT = "lca_research_kernel.uslci_flow_identity_preflight_action/v1"
RUNTIME_CONTRACT = "lca_research_kernel.portable_native_tool_runtime_closure/v1"
REMOTE_CREDENTIAL_NAMES = (
    "TIANGONG_LCA_API_BASE_URL",
    "TIANGONG_LCA_API_KEY_OR_SUPABASE_PUBLISHABLE_KEY",
)
AUTOMATIC_DECISIONS = {"reuse", "update_same_row", "version_bump", "create_new"}
SUPPORTED_DECISIONS = AUTOMATIC_DECISIONS | {"manual_review", "block_duplicate"}


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


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


def _parse_jsonl(content: bytes, label: str, *, allow_empty: bool = False) -> List[JsonObject]:
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
    if not rows and not allow_empty:
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


def _first_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        text = value.get("#text")
        return text.strip() if isinstance(text, str) else ""
    if isinstance(value, list):
        for row in value:
            text = _first_text(row)
            if text:
                return text
    return ""


def _catalog_identities(content: bytes) -> Tuple[List[JsonObject], set]:
    rows = _parse_jsonl(content, "local Flow candidate catalog")
    identities = set()
    for index, row in enumerate(rows, start=1):
        root = _object(row.get("flowDataSet"), f"candidate Flow {index}")
        information = _object(root.get("flowInformation"), "candidate Flow information")
        dataset = _object(
            information.get("dataSetInformation"), "candidate Flow dataset information"
        )
        flow_id = _text(dataset.get("common:UUID"), "candidate Flow UUID")
        administration = _object(
            root.get("administrativeInformation"),
            "candidate Flow administrative information",
        )
        publication = _object(
            administration.get("publicationAndOwnership"),
            "candidate Flow publication and ownership",
        )
        version = _text(publication.get("common:dataSetVersion"), "candidate Flow version")
        identity = (flow_id, version)
        if identity in identities:
            raise ValueError("local Flow candidate identity is duplicated")
        identities.add(identity)
    return rows, identities


def _candidate_identity(row: Mapping[str, object]) -> Tuple[str, str]:
    if isinstance(row.get("id"), str) and isinstance(row.get("version"), str):
        return _text(row.get("id"), "native candidate UUID"), _text(
            row.get("version"), "native candidate version"
        )
    root = _object(row.get("flowDataSet"), "native canonical candidate Flow")
    information = _object(root.get("flowInformation"), "native candidate Flow information")
    dataset = _object(
        information.get("dataSetInformation"), "native candidate Flow dataset information"
    )
    administration = _object(
        root.get("administrativeInformation"),
        "native candidate Flow administrative information",
    )
    publication = _object(
        administration.get("publicationAndOwnership"),
        "native candidate Flow publication and ownership",
    )
    return _text(dataset.get("common:UUID"), "native candidate Flow UUID"), _text(
        publication.get("common:dataSetVersion"), "native candidate Flow version"
    )


def _action_key(request: Mapping[str, object], catalog_sha256: str, runtime_sha256: str) -> str:
    return _sha256(
        _json_bytes(
            {
                "logic": PLAN_ACTION_LOGIC_VERSION,
                "kind": "flow-identity-preflight",
                "request": dict(request),
                "candidate_catalog_sha256": catalog_sha256,
                "runtime_tree_sha256": runtime_sha256,
            }
        )
    )[:16]


def _validate_inputs(
    *,
    plan_report_bytes: bytes,
    plan_report_binding: Mapping[str, object],
    batch_bytes: bytes,
    batch_binding: Mapping[str, object],
    candidate_catalog_bytes: bytes,
    candidate_catalog_binding: Mapping[str, object],
    runtime_report_bytes: bytes,
    runtime_report_binding: Mapping[str, object],
    runtime_manifest_bytes: bytes,
    runtime_manifest_binding: Mapping[str, object],
) -> Tuple[JsonObject, List[JsonObject], List[JsonObject], set, JsonObject]:
    bindings = (
        _binding(plan_report_binding, plan_report_bytes, "preflight plan binding"),
        _binding(batch_binding, batch_bytes, "first Flow batch binding"),
        _binding(candidate_catalog_binding, candidate_catalog_bytes, "Flow catalog binding"),
        _binding(runtime_report_binding, runtime_report_bytes, "runtime report binding"),
        _binding(runtime_manifest_binding, runtime_manifest_bytes, "runtime manifest binding"),
    )
    plan = _parse_json(plan_report_bytes, "preflight plan")
    actions = _parse_jsonl(batch_bytes, "first Flow batch")
    catalog_rows, catalog_identities = _catalog_identities(candidate_catalog_bytes)
    runtime = _parse_json(runtime_report_bytes, "runtime report")
    runtime_manifest = _parse_json(runtime_manifest_bytes, "runtime manifest")
    if (
        plan.get("contract") != PLAN_CONTRACT
        or plan.get("status") != "uslci_native_slice_preflight_plan_compiled"
        or plan.get("flow_action_count") != 1459
        or plan.get("flow_batch_count") != 15
        or plan.get("maximum_flow_batch_size") != 100
        or plan.get("maximum_native_concurrency") != 4
        or plan.get("first_flow_batch_action_count") != 100
        or plan.get("quantitative_reference_flow_count") != 3
        or plan.get("local_flow_candidate_count") != 13
        or plan.get("routing", {}).get("next_internal_route")
        != "execute-uslci-native-slice-preflight-batch-1"
        or len(actions) != 100
        or len(catalog_rows) != 13
    ):
        raise ValueError("preflight plan does not authorize the exact first batch")
    first_binding = _object(
        _object(plan.get("artifact_bindings"), "plan artifact bindings").get(
            "first_flow_batch"
        ),
        "plan first Flow batch binding",
    )
    if (
        first_binding.get("sha256") != bindings[1]["sha256"]
        or first_binding.get("bytes") != bindings[1]["bytes"]
    ):
        raise ValueError("plan and first Flow batch binding drifted")
    if (
        runtime.get("contract") != RUNTIME_CONTRACT
        or runtime.get("status") != "portable_native_tool_runtime_closure_verified"
        or runtime.get("runtime_content_closed") is not True
        or runtime.get("same_platform_relocation_proven") is not True
        or runtime_manifest.get("contract") != RUNTIME_CONTRACT
        or runtime_manifest.get("status") != "portable_native_tool_runtime_closure_compiled"
        or runtime_manifest.get("runtime_content_closed") is not True
        or runtime.get("tree_sha256") != runtime_manifest.get("tree_sha256")
        or runtime.get("tree_sha256") != plan.get("runtime_tree_sha256")
    ):
        raise ValueError("portable TianGong runtime binding drifted")
    runtime_sha = _text(runtime.get("tree_sha256"), "runtime tree SHA-256")
    catalog_sha = bindings[2]["sha256"]
    seen_keys = set()
    seen_sources = set()
    for expected_position, action in enumerate(actions, start=1):
        request = _object(action.get("request"), "Flow action request")
        target = _object(request.get("target"), "Flow action target")
        request_remote = _object(
            request.get("remote_candidate_search"), "remote candidate policy"
        )
        action_key = _text(action.get("action_key"), "Flow action key")
        source_id = _text(action.get("source_flow_id"), "source Flow UUID")
        if (
            action.get("contract") != ACTION_CONTRACT
            or action.get("position") != expected_position
            or action.get("candidate_catalog_sha256") != catalog_sha
            or action.get("runtime_tree_sha256") != runtime_sha
            or action.get("remote_candidate_search_enabled") is not False
            or request_remote.get("enabled") is not False
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
            or action_key != _action_key(request, catalog_sha, runtime_sha)
            or action_key in seen_keys
            or source_id in seen_sources
        ):
            raise ValueError(f"Flow action {expected_position} contract drifted")
        seen_keys.add(action_key)
        seen_sources.add(source_id)
    binding_rows = [dict(row) for row in bindings]
    return plan, actions, catalog_rows, catalog_identities, {
        "bindings": binding_rows,
        "runtime": runtime,
    }


def _validate_native_result(
    *,
    action: Mapping[str, object],
    request_path: Path,
    candidate_catalog_path: Path,
    candidate_count: int,
    candidate_identities: set,
    attempt_root: Path,
) -> JsonObject:
    decision_path = attempt_root / "outputs/identity-decision.json"
    candidates_path = attempt_root / "outputs/identity-candidates.jsonl"
    sources_path = attempt_root / "outputs/identity-candidate-sources.json"
    if not all(path.is_file() for path in (decision_path, candidates_path, sources_path)):
        raise FileNotFoundError("native identity preflight omitted required outputs")
    decision_bytes = decision_path.read_bytes()
    candidate_bytes = candidates_path.read_bytes()
    source_bytes = sources_path.read_bytes()
    report = _parse_json(decision_bytes, "native Flow identity decision")
    candidates = _parse_jsonl(candidate_bytes, "native Flow candidates", allow_empty=True)
    try:
        sources = json.loads(source_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("native Flow candidate sources are invalid JSON") from error
    if not isinstance(sources, list) or not all(isinstance(row, dict) for row in sources):
        raise ValueError("native Flow candidate sources must be an object array")
    request = _object(action.get("request"), "Flow action request")
    expected = _object(request.get("target"), "Flow request target")
    target = _object(report.get("target"), "native Flow report target")
    fields = _object(target.get("fields"), "native Flow report fields")
    files = _object(report.get("files"), "native Flow report files")
    blockers = report.get("blockers")
    findings = report.get("findings")
    decision = report.get("decision")
    status = report.get("status")
    confidence = report.get("confidence")
    status_consistent = (
        status == "passed" and decision in AUTOMATIC_DECISIONS and blockers == []
    ) or (
        status == "blocked"
        and decision == "block_duplicate"
        and isinstance(blockers, list)
        and len(blockers) > 0
    ) or (
        status == "needs_review" and decision == "manual_review" and blockers == []
    )
    if (
        report.get("schema_version") != 1
        or report.get("kind") != "flow"
        or decision not in SUPPORTED_DECISIONS
        or confidence not in {"low", "medium", "high"}
        or not isinstance(blockers, list)
        or not isinstance(findings, list)
        or not status_consistent
        or target.get("id") != expected.get("id")
        or target.get("names") != [expected.get("name")]
        or fields.get("type_of_dataset") != expected.get("type_of_dataset")
        or fields.get("flow_property") != expected.get("flow_property")
        or fields.get("reference_unit") != expected.get("reference_unit")
        or Path(_text(report.get("input_path"), "native report input path")).resolve()
        != request_path.resolve()
        or report.get("candidates") != candidates
        or report.get("candidate_sources") != sources
        or Path(_text(files.get("identity_decision"), "decision output path")).resolve()
        != decision_path.resolve()
        or Path(_text(files.get("candidates"), "candidate output path")).resolve()
        != candidates_path.resolve()
        or Path(_text(files.get("candidate_sources"), "source output path")).resolve()
        != sources_path.resolve()
    ):
        raise ValueError("native Flow identity report is malformed or target-misbound")
    if len(sources) != 1:
        raise ValueError("native Flow identity report must contain one local candidate source")
    source = _object(sources[0], "native local Flow candidate source")
    if (
        source.get("kind") != "file"
        or source.get("row_count") != candidate_count
        or Path(_text(source.get("path"), "candidate source path")).resolve()
        != candidate_catalog_path.resolve()
    ):
        raise ValueError("native Flow candidate source is not the exact bound catalog")
    candidate_keys = [_candidate_identity(row) for row in candidates]
    if len(candidate_keys) != len(set(candidate_keys)) or not set(candidate_keys).issubset(
        candidate_identities
    ):
        raise ValueError("native Flow candidates escaped or duplicated the bound catalog")
    return {
        "status": status,
        "decision": decision,
        "confidence": confidence,
        "candidate_count": len(candidates),
        "candidate_source_count": len(sources),
        "blocker_count": len(blockers),
        "finding_count": len(findings),
        "decision_sha256": _sha256(decision_bytes),
        "candidates_sha256": _sha256(candidate_bytes),
        "candidate_sources_sha256": _sha256(source_bytes),
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def _execute_action(
    action: JsonObject,
    *,
    candidate_catalog_path: Path,
    candidate_count: int,
    candidate_identities: set,
    node_path: Path,
    cli_entry_path: Path,
    raw_root: Path,
) -> JsonObject:
    position = _integer(action.get("position"), "Flow action position")
    action_key = _text(action.get("action_key"), "Flow action key")
    action_root = raw_root / f"{position:03d}-{action_key}"
    intervals = []
    last_failure_type = None
    last_failure = None
    invocation_count = 0
    semantic = None
    accepted_attempt = None
    for attempt in (1, 2):
        invocation_count += 1
        attempt_root = action_root / f"attempt-{attempt}"
        input_path = attempt_root / "inputs/flow-identity-preflight.json"
        _write_json(input_path, action["request"])
        command = [
            str(node_path.resolve()),
            str(cli_entry_path.resolve()),
            "flow",
            "identity-preflight",
            "--input",
            str(input_path.resolve()),
            "--candidate-input",
            str(candidate_catalog_path.resolve()),
            "--out-dir",
            str(attempt_root.resolve()),
            "--json",
        ]
        environment = dict(os.environ)
        for name in REMOTE_CREDENTIAL_NAMES:
            environment.pop(name, None)
        started = time.monotonic_ns()
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                check=False,
                env=environment,
            )
            exit_code = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
        except OSError as error:
            exit_code = 127
            stdout = b""
            stderr = str(error).encode("utf-8", errors="replace")
        finished = time.monotonic_ns()
        intervals.append((started, finished))
        required = [
            attempt_root / "outputs/identity-decision.json",
            attempt_root / "outputs/identity-candidates.jsonl",
            attempt_root / "outputs/identity-candidate-sources.json",
        ]
        missing = [str(path.relative_to(action_root)) for path in required if not path.is_file()]
        execution = {
            "schema_version": 1,
            "action_key": action_key,
            "position": position,
            "attempt": attempt,
            "command": command,
            "exit_code": exit_code,
            "duration_ms": (finished - started) // 1_000_000,
            "stdout_sha256": _sha256(stdout),
            "stdout_bytes": len(stdout),
            "stderr_sha256": _sha256(stderr),
            "stderr_bytes": len(stderr),
            "missing_outputs": missing,
            "remote_candidates_requested": False,
            "remote_credentials_forwarded": False,
        }
        _write_json(attempt_root / "execution.json", execution)
        if exit_code not in {0, 1} or missing:
            last_failure_type = (
                "command_failure" if exit_code not in {0, 1} else "missing_outputs"
            )
            last_failure = f"exit={exit_code}; missing={len(missing)}"
            if attempt == 1:
                continue
            break
        try:
            semantic = _validate_native_result(
                action=action,
                request_path=input_path,
                candidate_catalog_path=candidate_catalog_path,
                candidate_count=candidate_count,
                candidate_identities=candidate_identities,
                attempt_root=attempt_root,
            )
        except (OSError, ValueError) as error:
            last_failure_type = "semantic_rejection"
            last_failure = str(error)
            _write_json(
                attempt_root / "semantic-validation.json",
                {
                    "schema_version": 1,
                    "status": "rejected",
                    "reason": str(error),
                },
            )
            break
        _write_json(
            attempt_root / "semantic-validation.json",
            {"schema_version": 1, "status": "accepted", **semantic},
        )
        accepted_attempt = attempt
        last_failure_type = None
        last_failure = None
        break
    result = {
        "schema_version": 1,
        "action_key": action_key,
        "position": position,
        "source_flow_id": action["source_flow_id"],
        "source_flow_name": action["source_flow_name"],
        "target_flow_id": _object(action["target"], "action target")["id"],
        "target_flow_name": _object(action["target"], "action target")["name"],
        "invocation_count": invocation_count,
        "retried": invocation_count > 1,
        "semantic_status": "accepted" if semantic is not None else "rejected",
        "accepted_attempt": accepted_attempt,
        "failure_type": last_failure_type,
        "failure_reason": last_failure,
        "action_output_root": str(action_root.relative_to(raw_root.parent)),
    }
    if semantic is not None:
        result.update(semantic)
    result["_intervals"] = intervals
    return result


def _peak_concurrency(results: Sequence[JsonObject]) -> int:
    events = []
    for row in results:
        for started, finished in row.get("_intervals", []):
            events.append((started, 0, 1))
            events.append((finished, 1, -1))
    current = 0
    peak = 0
    for _, _, delta in sorted(events):
        current += delta
        peak = max(peak, current)
    return peak


def _manifest_rows(root: Path) -> List[JsonObject]:
    rows = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        content = path.read_bytes()
        rows.append(
            {
                "path": str(path.relative_to(root.parent)),
                "sha256": _sha256(content),
                "bytes": len(content),
            }
        )
    return rows


def create_deterministic_tar(source_root: Path, target: Path) -> None:
    """Archive the exact raw tree with normalized metadata."""

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(candidate for candidate in source_root.rglob("*") if candidate.is_file()):
            content = path.read_bytes()
            info = tarfile.TarInfo(str(path.relative_to(source_root.parent)))
            info.size = len(content)
            info.mode = 0o644
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            archive.addfile(info, io.BytesIO(content))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(buffer.getvalue())


def _artifact_binding(path: Path, content: bytes) -> JsonObject:
    return {"path": path.name, "sha256": _sha256(content), "bytes": len(content)}


def run_uslci_native_slice_preflight_batch(
    *,
    plan_report_bytes: bytes,
    plan_report_binding: Mapping[str, object],
    batch_bytes: bytes,
    batch_binding: Mapping[str, object],
    candidate_catalog_bytes: bytes,
    candidate_catalog_binding: Mapping[str, object],
    runtime_report_bytes: bytes,
    runtime_report_binding: Mapping[str, object],
    runtime_manifest_bytes: bytes,
    runtime_manifest_binding: Mapping[str, object],
    candidate_catalog_path: Path,
    node_path: Path,
    cli_entry_path: Path,
    out_dir: Path,
) -> JsonObject:
    """Run the exact first batch and materialize compact, replayable evidence."""

    plan, actions, catalog_rows, catalog_identities, context = _validate_inputs(
        plan_report_bytes=plan_report_bytes,
        plan_report_binding=plan_report_binding,
        batch_bytes=batch_bytes,
        batch_binding=batch_binding,
        candidate_catalog_bytes=candidate_catalog_bytes,
        candidate_catalog_binding=candidate_catalog_binding,
        runtime_report_bytes=runtime_report_bytes,
        runtime_report_binding=runtime_report_binding,
        runtime_manifest_bytes=runtime_manifest_bytes,
        runtime_manifest_binding=runtime_manifest_binding,
    )
    catalog_path = candidate_catalog_path.resolve()
    if not catalog_path.is_file() or catalog_path.read_bytes() != candidate_catalog_bytes:
        raise ValueError("candidate catalog execution path drifted from its binding")
    node = node_path.resolve()
    cli = cli_entry_path.resolve()
    if not node.is_file() or not cli.is_file():
        raise ValueError("copied Node and TianGong CLI entry must be files")
    root = out_dir.resolve()
    raw_root = root / "raw-actions"
    raw_root.mkdir(parents=True, exist_ok=True)
    results = []
    with ThreadPoolExecutor(max_workers=4) as executor:
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
            for action in actions
        }
        for future in as_completed(futures):
            results.append(future.result())
    peak = _peak_concurrency(results)
    results.sort(key=lambda row: int(row["position"]))
    for row in results:
        row.pop("_intervals", None)
    reference_count = int(plan["quantitative_reference_flow_count"])
    reference_results = [dict(row) for row in results[:reference_count]]
    failures = [dict(row) for row in results if row["semantic_status"] != "accepted"]
    semantic_accepted = len(results) - len(failures)
    decision_counts: Dict[str, int] = {}
    for row in results:
        decision = row.get("decision")
        if isinstance(decision, str):
            decision_counts[decision] = decision_counts.get(decision, 0) + 1
    reference_automatic = sum(
        row.get("semantic_status") == "accepted"
        and row.get("status") == "passed"
        and row.get("decision") in AUTOMATIC_DECISIONS
        for row in reference_results
    )
    blocker_count = sum(
        row.get("semantic_status") == "accepted"
        and (
            row.get("decision") not in AUTOMATIC_DECISIONS
            or row.get("status") != "passed"
            or int(row.get("blocker_count", 0)) > 0
        )
        for row in results
    )
    if failures:
        next_route = "retry-uslci-native-slice-preflight-batch-1-failures"
        status = "uslci_native_slice_preflight_batch_completed_with_failures"
        next_action = "Retry only failed or semantically rejected batch-1 action keys."
    elif reference_automatic != reference_count:
        next_route = "resolve-uslci-native-reference-flow-preflight-blockers"
        status = "uslci_native_slice_preflight_batch_reference_resolution_required"
        next_action = "Resolve native decisions for the quantitative-reference Flows."
    else:
        next_route = "execute-uslci-native-slice-preflight-batch-2"
        status = "uslci_native_slice_preflight_batch_completed"
        next_action = "Execute the exact second 100-Flow native identity-preflight batch."
    outputs = root / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    names = {
        "result_ledger": "uslci-native-slice-preflight-batch-1-results.jsonl",
        "action_output_manifest": "uslci-native-slice-preflight-batch-1-action-output-manifest.jsonl",
        "reference_results": "uslci-native-slice-preflight-batch-1-reference-flow-results.jsonl",
        "failures": "uslci-native-slice-preflight-batch-1-failures.jsonl",
        "raw_archive": "uslci-native-slice-preflight-batch-1-raw-results.tar",
    }
    result_bytes = b"".join(_json_bytes(row) for row in results)
    reference_bytes = b"".join(_json_bytes(row) for row in reference_results)
    failure_bytes = b"".join(_json_bytes(row) for row in failures)
    manifest_rows = _manifest_rows(raw_root)
    manifest_bytes = b"".join(_json_bytes(row) for row in manifest_rows)
    contents = {
        "result_ledger": result_bytes,
        "action_output_manifest": manifest_bytes,
        "reference_results": reference_bytes,
        "failures": failure_bytes,
    }
    for key, content in contents.items():
        (outputs / names[key]).write_bytes(content)
    archive_path = outputs / names["raw_archive"]
    create_deterministic_tar(raw_root, archive_path)
    archive_bytes = archive_path.read_bytes()
    artifact_bindings = {
        key: _artifact_binding(outputs / names[key], content)
        for key, content in {**contents, "raw_archive": archive_bytes}.items()
    }
    report = {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "status": status,
        "scope_id": plan["scope_id"],
        "revision": plan["revision"],
        "plan_input_version": plan["input_version"],
        "plan_version": plan["plan_version"],
        "batch_index": 1,
        "requested_action_count": len(actions),
        "action_semantic_accepted_count": semantic_accepted,
        "action_failure_count": len(failures),
        "native_invocation_executed_count": sum(int(row["invocation_count"]) for row in results),
        "retried_action_count": sum(bool(row["retried"]) for row in results),
        "maximum_native_concurrency": 4,
        "observed_peak_native_concurrency": peak,
        "local_flow_candidate_count": len(catalog_rows),
        "candidate_source_observation_count": sum(
            int(row.get("candidate_source_count", 0)) for row in results
        ),
        "candidate_observation_count": sum(
            int(row.get("candidate_count", 0)) for row in results
        ),
        "decision_counts": decision_counts,
        "decision_blocker_action_count": blocker_count,
        "quantitative_reference_flow_count": reference_count,
        "reference_flow_automatic_passed_count": reference_automatic,
        "remote_candidate_search_executed_count": 0,
        "remote_candidate_source_observation_count": 0,
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
        "os_network_sandbox": context["runtime"].get("os_network_sandbox", False),
        "runtime_tree_sha256": context["runtime"]["tree_sha256"],
        "tidas_cli_version": context["runtime"].get("tiangong_cli", {}).get("version"),
        "input_bindings": context["bindings"],
        "artifact_bindings": artifact_bindings,
        "raw_action_file_count": len(manifest_rows),
        "routing": {"next_internal_route": next_route, "pause_internal_work": False},
        "next_action": next_action,
    }
    report_path = outputs / "uslci-native-slice-preflight-batch-1.json"
    report_path.write_bytes(_json_bytes(report))
    return {
        **report,
        "results": results,
        "reference_results": reference_results,
        "failures": failures,
        "report_path": str(report_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in (
        "plan-report",
        "batch",
        "candidate-catalog",
        "runtime-report",
        "runtime-manifest",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
        parser.add_argument(f"--{name}-binding", type=Path, required=True)
    parser.add_argument("--native-node", type=Path, required=True)
    parser.add_argument("--native-cli-entry", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    values = {}
    for name in (
        "plan_report",
        "batch",
        "candidate_catalog",
        "runtime_report",
        "runtime_manifest",
    ):
        path = getattr(args, name)
        binding_path = getattr(args, f"{name}_binding")
        values[f"{name}_bytes"] = path.read_bytes()
        values[f"{name}_binding"] = json.loads(binding_path.read_bytes())
    result = run_uslci_native_slice_preflight_batch(
        **values,
        candidate_catalog_path=args.candidate_catalog,
        node_path=args.native_node,
        cli_entry_path=args.native_cli_entry,
        out_dir=args.out_dir,
    )
    print(json.dumps({key: value for key, value in result.items() if key not in {"results", "reference_results", "failures"}}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
