"""Typed applicability policy for TianGong Flow QA canaries."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import mimetypes
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_elementary_flow_native_qa_harness/v1"
LOGIC_VERSION = "2026-07-18.1"
POLICY_VERSION = "2026-07-18.1"
NEXT_ROUTE = "resolve-uslci-flow-authoring-bulk-batch-2-supports"
EXPECTED_RULESET = {
    "ruleset_id": "flow-authoring/strict",
    "ruleset_version": "1",
    "ruleset_source_version": "2026.05.23",
}
EXPECTED_INPUT_NAMES = (
    "native_gate_report",
    "gate_results",
    "classification_assertions",
    "support_assertions",
    "qa_outcomes",
    "qa_harness_gaps",
    "raw_artifact_manifest",
    "raw_artifact_archive",
    "runtime_report",
    "runtime_manifest",
)
KNOWN_MESSAGE = (
    "Flow type is Elementary flow; check whether this batch should exclude it."
)
CLI_PATH = "npm-runtime/node_modules/@tiangong-lca/cli/bin/tiangong-lca.js"
CLASSIFIED_MEMBER_RE = re.compile(
    r"^canary-(0[1-5])/classify/classified-flow\.jsonl$"
)


def _reject(reason: str) -> None:
    raise ValueError(f"typed QA policy rejected: {reason}")


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        _reject(f"{label} must be an object")
    return dict(value)


def _array(value: object, label: str) -> List[object]:
    if not isinstance(value, list):
        _reject(f"{label} must be an array")
    return list(value)


def _flow_identity(value: Mapping[str, object]) -> tuple[str, str]:
    root = _object(value.get("flowDataSet"), "Flow data set")
    information = _object(root.get("flowInformation"), "Flow information")
    data = _object(information.get("dataSetInformation"), "Flow data information")
    modelling = _object(root.get("modellingAndValidation"), "Flow modelling")
    method_value = modelling.get("LCIMethod")
    if not isinstance(method_value, dict):
        method_value = modelling.get("LCIMethodAndAllocation")
    method = _object(method_value, "Flow LCI method")
    target_id = data.get("common:UUID")
    flow_type = method.get("typeOfDataSet")
    if not isinstance(target_id, str) or not target_id:
        _reject("Flow UUID is missing")
    if flow_type not in {"Elementary flow", "Product flow"}:
        _reject("Flow type is outside the typed canary policy")
    return target_id, str(flow_type)


def _rows_by_id(
    values: Sequence[Mapping[str, object]], label: str, *, id_key: str
) -> Dict[str, JsonObject]:
    rows: Dict[str, JsonObject] = {}
    for value in values:
        row = _object(value, label)
        target_id = row.get(id_key)
        if not isinstance(target_id, str) or not target_id or target_id in rows:
            _reject(f"{label} identity is missing or duplicated")
        rows[target_id] = row
    return rows


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


def _jsonl_bytes(rows: Iterable[Mapping[str, object]]) -> bytes:
    return b"".join(_json_bytes(dict(row)) for row in rows)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _parse_json(content: bytes, label: str) -> JsonObject:
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return dict(value)


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
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{label} row {index} is invalid JSON") from error
        if not isinstance(value, dict):
            raise ValueError(f"{label} row {index} must be an object")
        rows.append(dict(value))
    if not rows and not allow_empty:
        raise ValueError(f"{label} is empty")
    return rows


def _artifact_binding(path: str, content: bytes) -> JsonObject:
    return {"path": path, "sha256": _sha256(content), "bytes": len(content)}


def _binding(value: Mapping[str, object], content: bytes, label: str) -> JsonObject:
    row = dict(value)
    path = row.get("path")
    digest = row.get("sha256")
    size = row.get("bytes")
    if (
        not isinstance(path, str)
        or not path
        or not isinstance(digest, str)
        or isinstance(size, bool)
        or not isinstance(size, int)
        or digest != _sha256(content)
        or size != len(content)
    ):
        raise ValueError(f"{label} hash or byte count drifted")
    return {"path": path, "sha256": digest, "bytes": size}


def input_version_for_bindings(bindings: Sequence[Mapping[str, object]]) -> str:
    return _sha256(
        _json_bytes(
            {
                "contract": CONTRACT,
                "logic_version": LOGIC_VERSION,
                "policy_version": POLICY_VERSION,
                "inputs": [dict(value) for value in bindings],
            }
        )
    )[:16]


def _safe_member_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("archive verification rejected: member path is missing")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or str(path) != value
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("archive verification rejected: unsafe member path")
    return value


def verify_and_reconstruct_classified_flows(
    *, manifest_content: bytes, archive_content: bytes
) -> Tuple[List[JsonObject], List[JsonObject]]:
    """Verify every raw member, then return the five exact classified rows."""

    try:
        manifest_rows = _parse_jsonl(manifest_content, "raw artifact manifest")
    except ValueError as error:
        raise ValueError(f"archive verification rejected: {error}") from error
    manifest_by_path: Dict[str, JsonObject] = {}
    for row in manifest_rows:
        path = _safe_member_path(row.get("path"))
        if path in manifest_by_path:
            raise ValueError("archive verification rejected: duplicate manifest member")
        digest = row.get("sha256")
        size = row.get("bytes")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
        ):
            raise ValueError("archive verification rejected: invalid member descriptor")
        manifest_by_path[path] = row
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_content), mode="r:*") as archive:
            file_members = [member for member in archive.getmembers() if member.isfile()]
            names = [member.name for member in file_members]
            if len(names) != len(set(names)):
                raise ValueError("duplicate archive member")
            if set(names) != set(manifest_by_path):
                raise ValueError("archive and manifest member sets differ")
            member_contents = {}
            for member in file_members:
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ValueError("archive member is unreadable")
                content = extracted.read()
                descriptor = manifest_by_path[member.name]
                if (
                    len(content) != descriptor.get("bytes")
                    or _sha256(content) != descriptor.get("sha256")
                ):
                    raise ValueError("archive member hash or byte count drifted")
                member_contents[member.name] = content
    except (tarfile.TarError, OSError, ValueError) as error:
        raise ValueError(f"archive verification rejected: {error}") from error

    selected = []
    for path, content in member_contents.items():
        match = CLASSIFIED_MEMBER_RE.fullmatch(path)
        if match is None:
            continue
        canary_index = int(match.group(1))
        rows = _parse_jsonl(content, f"classified Flow canary {canary_index}")
        if len(rows) != 1:
            raise ValueError(
                "archive verification rejected: classified member must contain one row"
            )
        target_id, flow_type = _flow_identity(rows[0])
        selected.append(
            (
                canary_index,
                rows[0],
                {
                    "schema_version": 1,
                    "canary_index": canary_index,
                    "path": path,
                    "target_flow_id": target_id,
                    "flow_type": flow_type,
                    "bytes": len(content),
                    "sha256": _sha256(content),
                    "archive_member_verified": True,
                },
            )
        )
    selected.sort(key=lambda value: value[0])
    if [value[0] for value in selected] != [1, 2, 3, 4, 5]:
        raise ValueError(
            "archive verification rejected: exact five classified canaries are required"
        )
    return [value[1] for value in selected], [value[2] for value in selected]


def evaluate_typed_flow_qa(
    *,
    flows: Sequence[Mapping[str, object]],
    validation_report: Mapping[str, object],
    native_qa_report: Mapping[str, object],
    classification_assertions: Sequence[Mapping[str, object]],
    support_assertions: Sequence[Mapping[str, object]],
) -> JsonObject:
    """Apply one exact applicability policy without rewriting native QA evidence."""

    if len(flows) != 5:
        _reject("exactly five classified canary Flows are required")
    flow_by_id: Dict[str, JsonObject] = {}
    flow_type_by_id: Dict[str, str] = {}
    for value in flows:
        flow = _object(value, "classified Flow")
        target_id, flow_type = _flow_identity(flow)
        if target_id in flow_by_id:
            _reject("classified Flow UUID is duplicated")
        flow_by_id[target_id] = flow
        flow_type_by_id[target_id] = flow_type
    elementary_ids = {
        target_id
        for target_id, flow_type in flow_type_by_id.items()
        if flow_type == "Elementary flow"
    }
    product_ids = {
        target_id
        for target_id, flow_type in flow_type_by_id.items()
        if flow_type == "Product flow"
    }
    if len(elementary_ids) != 4 or len(product_ids) != 1:
        _reject("typed canary set must contain four Elementary and one Product Flow")

    classifications = _rows_by_id(
        classification_assertions,
        "classification assertion",
        id_key="target_flow_id",
    )
    supports = _rows_by_id(
        support_assertions, "support assertion", id_key="target_flow_id"
    )
    if set(classifications) != set(flow_by_id) or set(supports) != set(flow_by_id):
        _reject("assertion UUID coverage drifted")
    for target_id, flow_type in flow_type_by_id.items():
        classification = classifications[target_id]
        support = supports[target_id]
        if (
            classification.get("flow_type") != flow_type
            or classification.get("target_identity_preserved") is not True
            or classification.get("exact_classification_applied") is not True
            or classification.get("support_reference_preserved") is not True
            or support.get("support_reference_preserved") is not True
            or support.get("flow_property_id") in {None, ""}
            or support.get("flow_property_version") in {None, ""}
        ):
            _reject(f"accepted identity/classification/support drifted for {target_id}")

    validation = _object(validation_report, "native validation report")
    counts = _object(validation.get("counts"), "native validation counts")
    validation_rows = [
        _object(value, "native validation row")
        for value in _array(validation.get("rows"), "native validation rows")
    ]
    validation_by_id = _rows_by_id(
        validation_rows, "native validation row", id_key="id"
    )
    if (
        validation.get("status") != "completed"
        or validation.get("requested_type") != "flow"
        or counts.get("total") != 5
        or counts.get("valid") != 5
        or counts.get("invalid") != 0
        or set(validation_by_id) != set(flow_by_id)
    ):
        _reject("native FlowSchema aggregate validation drifted")
    for target_id, row in validation_by_id.items():
        if (
            row.get("type") != "flow"
            or row.get("status") != "valid"
            or row.get("validator") != "@tiangong-lca/tidas-sdk/FlowSchema"
            or row.get("issue_count") != 0
            or row.get("issues") != []
        ):
            _reject(f"native FlowSchema row is not valid for {target_id}")

    report = _object(native_qa_report, "native Flow QA report")
    gate = _object(report.get("ruleset_gate"), "native Flow QA ruleset gate")
    for key, expected in EXPECTED_RULESET.items():
        if report.get(key) != expected or gate.get(key) != expected:
            _reject(f"native Flow QA {key} drifted")
    llm = _object(report.get("llm"), "native Flow QA LLM status")
    if (
        report.get("status") != "completed_local_flow_qa"
        or report.get("flow_count") != 5
        or report.get("rule_finding_count") != 4
        or report.get("llm_finding_count") != 0
        or report.get("finding_count") != 4
        or report.get("blocker_count") != 4
        or report.get("rule_counts") != {"elementary_flow_in_flow_qa": 4}
        or report.get("methodology_rule_counts")
        != {"tidas.flow.type.required": 4}
        or gate.get("status") != "blocked"
        or gate.get("counts") != {"findings": 4, "blockers": 4}
        or llm.get("enabled") is not False
        or llm.get("reviewed_flow_count") != 0
    ):
        _reject("native Flow QA aggregate result drifted")
    blockers = [
        _object(value, "native Flow QA blocker")
        for value in _array(gate.get("blockers"), "native Flow QA blockers")
    ]
    if len(blockers) != 4:
        _reject("native Flow QA blocker count drifted")
    blockers_by_id = _rows_by_id(
        blockers, "native Flow QA blocker", id_key="flow_uuid"
    )
    if set(blockers_by_id) != elementary_ids:
        _reject("native Flow QA exception candidates are not the exact Elementary set")
    for target_id, blocker in blockers_by_id.items():
        if (
            blocker.get("severity") != "warning"
            or blocker.get("rule_id") != "elementary_flow_in_flow_qa"
            or blocker.get("ruleset_id") != "flow-authoring/strict"
            or blocker.get("methodology_rule_id") != "tidas.flow.type.required"
            or blocker.get("message") != KNOWN_MESSAGE
            or blocker.get("fixability") != "review-needed"
            or blocker.get("source") != "rule"
            or blocker.get("evidence") != {"typeOfDataSet": "Elementary flow"}
        ):
            _reject(f"native Elementary applicability finding drifted for {target_id}")

    def canary_index(target_id: str) -> int:
        value = classifications[target_id].get("canary_index")
        if isinstance(value, bool) or not isinstance(value, int):
            _reject("classification assertion canary index is invalid")
        return value

    typed_decisions = []
    applicability_decisions = []
    for target_id in sorted(flow_by_id, key=canary_index):
        flow_type = flow_type_by_id[target_id]
        if flow_type == "Product flow":
            outcome = "strict_native_pass"
            exception_applied = False
        else:
            outcome = "not_applicable_to_intentional_elementary_authoring_batch"
            exception_applied = True
        decision = {
            "schema_version": 1,
            "canary_index": canary_index(target_id),
            "target_flow_id": target_id,
            "flow_type": flow_type,
            "native_schema_validation": "passed",
            "upstream_native_qa_status": "blocked",
            "outcome": outcome,
            "exception_applied": exception_applied,
            "classification_code": classifications[target_id].get(
                "classification_code"
            ),
            "flow_property_id": supports[target_id].get("flow_property_id"),
            "flow_property_version": supports[target_id].get(
                "flow_property_version"
            ),
        }
        typed_decisions.append(decision)
        if exception_applied:
            applicability_decisions.append(
                {
                    **decision,
                    "native_rule_id": "elementary_flow_in_flow_qa",
                    "methodology_rule_id": "tidas.flow.type.required",
                    "native_finding_preserved": True,
                    "scientific_mapping_invalidated": False,
                }
            )
    product_control = next(
        row for row in typed_decisions if row["flow_type"] == "Product flow"
    )
    return {
        "status": "typed_flow_qa_passed_with_scoped_elementary_not_applicable_findings",
        "policy_version": POLICY_VERSION,
        "upstream_native_qa_status": "blocked",
        "typed_decisions": typed_decisions,
        "applicability_decisions": applicability_decisions,
        "product_control": product_control,
        "unexpected_findings": [],
        "typed_qa_pass_count": 5,
        "strict_product_qa_pass_count": 1,
        "elementary_not_applicable_count": 4,
        "unexpected_finding_count": 0,
        "bulk_planning_release_authorized": True,
        "bulk_entity_execution_authorized": False,
    }


def _run_command(command: Sequence[str], *, stdout_path: Path) -> None:
    completed = subprocess.run(command, capture_output=True, check=False)
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_bytes(completed.stdout)
    if completed.stderr:
        stdout_path.with_suffix(".stderr.txt").write_bytes(completed.stderr)
    if completed.returncode != 0:
        raise ValueError(
            "copied TianGong native command failed: "
            + completed.stderr.decode("utf-8", errors="replace").strip()
        )


def _runtime_identity(
    report: Mapping[str, object], manifest: Mapping[str, object]
) -> JsonObject:
    if (
        report.get("status") != "portable_native_tool_runtime_closure_verified"
        or report.get("runtime_content_closed") is not True
    ):
        raise ValueError("copied native runtime is not verified")
    tree = report.get("tree_sha256")
    if not isinstance(tree, str) or not tree or manifest.get("tree_sha256") != tree:
        raise ValueError("copied runtime report and manifest disagree")
    cli = manifest.get("tiangong_cli")
    if not isinstance(cli, dict):
        raise ValueError("copied runtime TianGong CLI identity is missing")
    if cli.get("package") != "@tiangong-lca/cli" or cli.get("version") != "0.0.25":
        raise ValueError("unsupported TianGong CLI runtime")
    return {
        "runtime_tree_sha256": tree,
        "tiangong_cli_package": cli["package"],
        "tiangong_cli_version": cli["version"],
    }


def _archive_tree(source_root: Path, archive_path: Path) -> Tuple[bytes, List[JsonObject]]:
    files = sorted(path for path in source_root.rglob("*") if path.is_file())
    manifest = []
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in files:
            relative = path.relative_to(source_root).as_posix()
            content = path.read_bytes()
            info = tarfile.TarInfo(name=relative)
            info.size = len(content)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(content))
            manifest.append(
                {
                    "schema_version": 1,
                    "path": relative,
                    "media_type": mimetypes.guess_type(relative)[0]
                    or "application/octet-stream",
                    "bytes": len(content),
                    "sha256": _sha256(content),
                }
            )
    return archive_path.read_bytes(), manifest


def execute_and_materialize(
    *,
    contents: Mapping[str, bytes],
    bindings: Mapping[str, Mapping[str, object]],
    runtime_root: Path,
    out_dir: Path,
) -> JsonObject:
    verified_bindings = [
        _binding(bindings[name], contents[name], f"{name} binding")
        for name in EXPECTED_INPUT_NAMES
    ]
    input_version = input_version_for_bindings(verified_bindings)
    native_report = _parse_json(contents["native_gate_report"], "native-gate report")
    gate_results = _parse_jsonl(contents["gate_results"], "native gate results")
    classification_assertions = _parse_jsonl(
        contents["classification_assertions"], "classification assertions"
    )
    support_assertions = _parse_jsonl(
        contents["support_assertions"], "support assertions"
    )
    qa_outcomes = _parse_jsonl(contents["qa_outcomes"], "native QA outcomes")
    qa_harness_gaps = _parse_jsonl(
        contents["qa_harness_gaps"], "native QA harness gaps"
    )
    runtime_report = _parse_json(contents["runtime_report"], "runtime report")
    runtime_manifest = _parse_json(contents["runtime_manifest"], "runtime manifest")
    runtime = _runtime_identity(runtime_report, runtime_manifest)
    if (
        native_report.get("contract")
        != "lca_research_kernel.uslci_flow_authoring_canary_native_gates/v1"
        or native_report.get("status")
        != "uslci_flow_authoring_canary_native_gates_completed_with_elementary_qa_harness_gap"
        or native_report.get("native_plan_validation_pass_count") != 5
        or native_report.get("local_flow_materialization_pass_count") != 5
        or native_report.get("exact_classification_apply_pass_count") != 5
        or native_report.get("support_reference_preservation_pass_count") != 5
        or native_report.get("qa_pass_count") != 1
        or native_report.get("elementary_qa_harness_gap_count") != 4
        or native_report.get("unexpected_failure_count") != 0
        or native_report.get("routing", {}).get("next_internal_route")
        != "resolve-uslci-elementary-flow-native-qa-harness"
        or native_report.get("runtime_identity", {}).get("runtime_tree_sha256")
        != runtime["runtime_tree_sha256"]
        or len(gate_results) != 5
        or len(classification_assertions) != 5
        or len(support_assertions) != 5
        or len(qa_outcomes) != 5
        or len(qa_harness_gaps) != 4
    ):
        raise ValueError("accepted native-gate authority drifted")
    artifact_input_names = (
        "gate_results",
        "classification_assertions",
        "support_assertions",
        "qa_outcomes",
        "qa_harness_gaps",
        "raw_artifact_manifest",
        "raw_artifact_archive",
    )
    for name in artifact_input_names:
        expected = native_report.get("artifact_bindings", {}).get(name)
        if (
            not isinstance(expected, dict)
            or expected.get("sha256") != bindings[name].get("sha256")
            or expected.get("bytes") != bindings[name].get("bytes")
        ):
            raise ValueError(f"accepted native-gate artifact drifted: {name}")

    flows, replay_input_manifest = verify_and_reconstruct_classified_flows(
        manifest_content=contents["raw_artifact_manifest"],
        archive_content=contents["raw_artifact_archive"],
    )
    classifications_by_index = {
        row.get("canary_index"): row for row in classification_assertions
    }
    gate_by_index = {row.get("canary_index"): row for row in gate_results}
    outcome_by_index = {row.get("canary_index"): row for row in qa_outcomes}
    for descriptor in replay_input_manifest:
        index = descriptor["canary_index"]
        classification = classifications_by_index.get(index)
        gate = gate_by_index.get(index)
        outcome = outcome_by_index.get(index)
        if (
            not isinstance(classification, dict)
            or classification.get("classified_flow_sha256")
            != descriptor.get("sha256")
            or classification.get("target_flow_id")
            != descriptor.get("target_flow_id")
            or not isinstance(gate, dict)
            or gate.get("target_flow_id") != descriptor.get("target_flow_id")
            or gate.get("exact_classification_apply") != "passed"
            or gate.get("support_reference_preservation") != "passed"
            or not isinstance(outcome, dict)
            or outcome.get("target_flow_id") != descriptor.get("target_flow_id")
        ):
            raise ValueError("classified replay row drifted from Beat 69 assertions")
    gap_ids = {row.get("target_flow_id") for row in qa_harness_gaps}
    expected_gap_ids = {
        row.get("target_flow_id")
        for row in classification_assertions
        if row.get("flow_type") == "Elementary flow"
    }
    if gap_ids != expected_gap_ids:
        raise ValueError("Beat 69 Elementary QA gap identity coverage drifted")

    node = runtime_root / "bin/node"
    cli = runtime_root / CLI_PATH
    if not node.is_file() or not cli.is_file():
        raise ValueError("copied runtime executable is missing")
    outputs = out_dir / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="uslci-typed-flow-qa-") as temp:
        scratch = Path(temp)
        replay_input_path = scratch / "inputs/classified-canary-flows.json"
        replay_input_path.parent.mkdir(parents=True, exist_ok=True)
        replay_input_path.write_bytes(_json_bytes(flows, pretty=True))
        validate_dir = scratch / "native-dataset-validate"
        qa_dir = scratch / "native-flow-qa"
        stdout_dir = scratch / "command-stdout"
        _run_command(
            [
                str(node),
                str(cli),
                "dataset",
                "validate",
                "--input",
                str(replay_input_path),
                "--type",
                "flow",
                "--out-dir",
                str(validate_dir),
                "--json",
            ],
            stdout_path=stdout_dir / "01-dataset-validate.json",
        )
        _run_command(
            [
                str(node),
                str(cli),
                "qa",
                "flow",
                "--rows-file",
                str(replay_input_path),
                "--out-dir",
                str(qa_dir),
                "--json",
            ],
            stdout_path=stdout_dir / "02-flow-qa.json",
        )
        validation_report = _parse_json(
            (validate_dir / "outputs/validation-report.json").read_bytes(),
            "native replay validation report",
        )
        native_qa_report = _parse_json(
            (qa_dir / "flow_qa_report.json").read_bytes(),
            "native replay Flow QA report",
        )
        policy = evaluate_typed_flow_qa(
            flows=flows,
            validation_report=validation_report,
            native_qa_report=native_qa_report,
            classification_assertions=classification_assertions,
            support_assertions=support_assertions,
        )
        archive_path = outputs / "uslci-flow-qa-replay-raw-artifacts.tar"
        replay_archive_bytes, replay_raw_manifest = _archive_tree(
            scratch, archive_path
        )

    output_rows = {
        "typed_decisions": (
            "uslci-typed-flow-qa-decisions.jsonl",
            _jsonl_bytes(policy["typed_decisions"]),
        ),
        "applicability_decisions": (
            "uslci-elementary-flow-qa-applicability-decisions.jsonl",
            _jsonl_bytes(policy["applicability_decisions"]),
        ),
        "product_control": (
            "uslci-product-flow-strict-qa-control.json",
            _json_bytes(policy["product_control"], pretty=True),
        ),
        "replay_input_manifest": (
            "uslci-flow-qa-replay-input-manifest.jsonl",
            _jsonl_bytes(replay_input_manifest),
        ),
        "unexpected_findings": (
            "uslci-flow-qa-unexpected-findings.jsonl",
            _jsonl_bytes(policy["unexpected_findings"]),
        ),
        "raw_artifact_manifest": (
            "uslci-flow-qa-replay-raw-artifact-manifest.jsonl",
            _jsonl_bytes(replay_raw_manifest),
        ),
        "raw_artifact_archive": (
            "uslci-flow-qa-replay-raw-artifacts.tar",
            replay_archive_bytes,
        ),
    }
    artifact_bindings = {
        key: _artifact_binding(filename, content)
        for key, (filename, content) in output_rows.items()
    }
    execution_version = _sha256(
        _json_bytes(
            {
                "contract": CONTRACT,
                "input_version": input_version,
                "typed_decisions_sha256": artifact_bindings["typed_decisions"][
                    "sha256"
                ],
                "raw_artifact_archive_sha256": artifact_bindings[
                    "raw_artifact_archive"
                ]["sha256"],
            }
        )
    )[:16]
    result = {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "policy_version": POLICY_VERSION,
        "status": "uslci_elementary_flow_native_qa_harness_resolved",
        "typed_qa_status": policy["status"],
        "scope_id": native_report.get("scope_id"),
        "revision": native_report.get("revision"),
        "input_version": input_version,
        "qa_harness_execution_version": execution_version,
        "source_native_gate_execution_version": native_report.get(
            "native_gate_execution_version"
        ),
        "source_saved_plan_set_version": native_report.get(
            "source_saved_plan_set_version"
        ),
        "runtime_identity": runtime,
        "input_bindings": verified_bindings,
        "artifact_bindings": artifact_bindings,
        "replay_raw_artifact_member_count": len(replay_raw_manifest),
        "source_raw_artifact_member_verified_count": len(
            _parse_jsonl(contents["raw_artifact_manifest"], "source raw manifest")
        ),
        "native_schema_validation_pass_count": 5,
        "strict_product_qa_pass_count": policy["strict_product_qa_pass_count"],
        "elementary_not_applicable_count": policy[
            "elementary_not_applicable_count"
        ],
        "typed_qa_pass_count": policy["typed_qa_pass_count"],
        "unexpected_finding_count": policy["unexpected_finding_count"],
        "upstream_native_qa_status": policy["upstream_native_qa_status"],
        "scientific_review_marker_preserved_count": 4,
        "native_invocation_executed_count": 2,
        "native_dataset_validation_executed_count": 1,
        "native_flow_qa_executed_count": 1,
        "network_read_executed_count": 0,
        "remote_candidate_search_executed_count": 0,
        "flow_creation_executed_count": 0,
        "remote_flow_materialization_executed_count": 0,
        "save_draft_executed_count": 0,
        "process_creation_executed_count": 0,
        "model_connection_executed_count": 0,
        "model_mutation_executed_count": 0,
        "external_write_executed_count": 0,
        "publication_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "bulk_planning_release_authorized": True,
        "bulk_entity_execution_authorized": False,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "upstream_native_report_rewritten": False,
        "failure_diagnosis": {
            "prompt_failure_count": 0,
            "context_failure_count": 0,
            "harness_failure_count": 0,
            "loop_failure_count": 0,
            "model_limitation_count": 0,
            "resolved_harness_failure_count": 4,
        },
        "external_patterns": [
            {
                "project": "kubernetes/kubernetes",
                "local_landing": "typed_rule_applicability_match_conditions",
                "installed": False,
            },
            {
                "project": "open-policy-agent/opa",
                "local_landing": "immutable_input_policy_result_decision_record",
                "installed": False,
            },
            {
                "project": "kyverno/kyverno",
                "local_landing": "narrow_auditable_policy_exception_ledger",
                "installed": False,
            },
        ],
        "routing": {"next_internal_route": NEXT_ROUTE, "pause_internal_work": False},
        "next_action": (
            "Unlock only USLCI Flow authoring batch 2 and resolve exact per-Flow "
            "classification/support contracts before compiling saved plans; keep entity "
            "creation, save-draft and remote execution closed."
        ),
    }
    for filename, content in output_rows.values():
        path = outputs / filename
        if path.suffix == ".tar":
            continue
        path.write_bytes(content)
    (outputs / "uslci-elementary-flow-native-qa-harness.json").write_bytes(
        _json_bytes(result, pretty=True)
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve typed applicability for the exact TianGong Flow QA canaries."
    )
    for name in EXPECTED_INPUT_NAMES:
        option = name.replace("_", "-")
        parser.add_argument(f"--{option}", type=Path, required=True)
        parser.add_argument(f"--{option}-binding", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    contents = {}
    bindings = {}
    for name in EXPECTED_INPUT_NAMES:
        contents[name] = getattr(args, name).read_bytes()
        bindings[name] = _parse_json(
            getattr(args, f"{name}_binding").read_bytes(), f"{name} binding"
        )
    execute_and_materialize(
        contents=contents,
        bindings=bindings,
        runtime_root=args.runtime_root,
        out_dir=args.out_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
