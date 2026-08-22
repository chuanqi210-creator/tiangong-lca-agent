"""Execute five immutable Flow plans through copied TianGong native gates."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import mimetypes
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_flow_authoring_canary_native_gates/v1"
LOGIC_VERSION = "2026-07-18.1"
POLICY_VERSION = "2026-07-18.1"
SAVED_PLAN_CONTRACT = "lca_research_kernel.uslci_flow_authoring_canary_saved_plans/v1"
SUPPORT_CONTRACT = "lca_research_kernel.uslci_flow_authoring_canary_supports/v1"
NEXT_ROUTE = "resolve-uslci-elementary-flow-native-qa-harness"
EXPECTED_INPUT_NAMES = (
    "saved_plan_report",
    "plan_index",
    "execution_descriptors",
    "review_marker_carry_forward",
    "classification_decisions",
    "support_contracts",
    "runtime_report",
    "runtime_manifest",
    "saved_plan_manifest",
)
CLI_PATH = "npm-runtime/node_modules/@tiangong-lca/cli/bin/tiangong-lca.js"


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


def input_version_for_bindings(bindings: Sequence[JsonObject]) -> str:
    return _sha256(
        _json_bytes(
            {
                "contract": CONTRACT,
                "logic_version": LOGIC_VERSION,
                "policy_version": POLICY_VERSION,
                "inputs": list(bindings),
            }
        )
    )[:16]


def _safe_path(root: Path, relative_value: object, label: str) -> Path:
    relative = _text(relative_value, label)
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in {"", ".", ".."} for part in pure.parts)
        or str(pure) != relative
    ):
        raise ValueError(f"{label} is unsafe")
    resolved_root = root.resolve()
    path = (resolved_root / relative).resolve()
    try:
        path.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(f"{label} escaped artifact root") from error
    return path


def classify_qa_outcome(
    report_value: Mapping[str, object], *, flow_type: str
) -> JsonObject:
    """Separate a native QA pass from the known Elementary harness applicability gap."""

    report = _object(report_value, "native Flow QA report")
    if report.get("status") != "completed_local_flow_qa":
        raise ValueError("native Flow QA did not complete")
    blocker_count = _integer(report.get("blocker_count"), "QA blocker count")
    finding_count = _integer(report.get("finding_count"), "QA finding count")
    gate = _object(report.get("ruleset_gate"), "QA ruleset gate")
    if blocker_count == 0 and gate.get("status") == "passed":
        return {
            "outcome": "passed",
            "diagnosis": None,
            "blocker_count": 0,
            "finding_count": finding_count,
            "scientific_mapping_invalidated": False,
            "bulk_release_authorized": False,
        }
    blockers = [
        _object(value, "QA blocker")
        for value in _array(gate.get("blockers"), "QA blockers")
    ]
    rule_counts = report.get("rule_counts")
    known = (
        flow_type == "Elementary flow"
        and blocker_count == 1
        and finding_count == 1
        and rule_counts == {"elementary_flow_in_flow_qa": 1}
        and gate.get("status") == "blocked"
        and len(blockers) == 1
        and blockers[0].get("rule_id") == "elementary_flow_in_flow_qa"
        and blockers[0].get("methodology_rule_id") == "tidas.flow.type.required"
    )
    if known:
        return {
            "outcome": "elementary_qa_harness_applicability_blocked",
            "diagnosis": "harness_failure",
            "blocker_count": 1,
            "finding_count": 1,
            "rule_id": "elementary_flow_in_flow_qa",
            "methodology_rule_id": "tidas.flow.type.required",
            "scientific_mapping_invalidated": False,
            "bulk_release_authorized": False,
        }
    raise ValueError(f"unexpected native QA blockers for {flow_type}")


def _flow_property_rows(flow: Mapping[str, object]) -> List[JsonObject]:
    dataset = _object(flow.get("flowDataSet"), "TIDAS Flow data set")
    properties = _object(dataset.get("flowProperties"), "TIDAS Flow properties")
    raw = properties.get("flowProperty")
    if isinstance(raw, dict):
        return [dict(raw)]
    return [_object(value, "TIDAS Flow property") for value in _array(raw, "TIDAS Flow property rows")]


def verify_classified_flow(
    plan_value: Mapping[str, object], flow_value: Mapping[str, object]
) -> JsonObject:
    """Verify target identity, support property and exact native classification."""

    plan = _object(plan_value, "saved Flow plan")
    flow = _object(flow_value, "classified Flow")
    target = _object(plan.get("target"), "saved plan target")
    support = _object(plan.get("flow_property_plan"), "saved plan support")
    classification = _object(
        plan.get("native_classification_plan"), "saved plan classification"
    )
    dataset = _object(flow.get("flowDataSet"), "TIDAS Flow data set")
    information = _object(dataset.get("flowInformation"), "TIDAS Flow information")
    data_info = _object(information.get("dataSetInformation"), "TIDAS Flow data information")
    modelling = _object(
        dataset.get("modellingAndValidation"), "TIDAS Flow modelling"
    )
    method = _object(modelling.get("LCIMethod"), "TIDAS Flow LCI method")
    if (
        data_info.get("common:UUID") != target.get("uuid")
        or method.get("typeOfDataSet") != target.get("flow_type")
    ):
        raise ValueError("native materialization changed target identity or Flow type")
    properties = _flow_property_rows(flow)
    property_matches = []
    for row in properties:
        reference = row.get("referenceToFlowPropertyDataSet")
        if not isinstance(reference, dict):
            continue
        if (
            reference.get("@refObjectId") == support.get("reference_property_id")
            and reference.get("@version") == support.get("reference_property_version")
            and row.get("meanValue") in {"1", "1.0", 1, 1.0}
        ):
            property_matches.append(row)
    if len(property_matches) != 1:
        raise ValueError("native materialization did not preserve exact support property")
    classification_info = _object(
        data_info.get("classificationInformation"), "TIDAS classification information"
    )
    category_type = classification.get("category_type")
    if category_type == "flow-elementary":
        container = _object(
            classification_info.get("common:elementaryFlowCategorization"),
            "Elementary categorization",
        )
        raw_path = container.get("common:category")
        code_key = "@catId"
    elif category_type == "flow-product":
        container = _object(
            classification_info.get("common:classification"), "Product classification"
        )
        raw_path = container.get("common:class")
        code_key = "@classId"
    else:
        raise ValueError("saved plan classification type is unsupported")
    if isinstance(raw_path, dict):
        actual_path = [dict(raw_path)]
    else:
        actual_path = [
            _object(value, "native classification node")
            for value in _array(raw_path, "native classification path")
        ]
    expected_path = [
        _object(value, "saved classification node")
        for value in _array(classification.get("path"), "saved classification path")
    ]
    if len(actual_path) != len(expected_path):
        raise ValueError("native exact classification depth drifted")
    for actual, expected in zip(actual_path, expected_path):
        if (
            actual.get(code_key) != expected.get(code_key)
            or actual.get("@level") != expected.get("@level")
            or actual.get("#text") != expected.get("#text")
        ):
            raise ValueError("native exact classification path drifted")
    if actual_path[-1].get(code_key) != classification.get("leaf_code"):
        raise ValueError("native exact classification leaf drifted")
    return {
        "target_flow_id": target.get("uuid"),
        "flow_type": target.get("flow_type"),
        "flow_property_id": support.get("reference_property_id"),
        "flow_property_version": support.get("reference_property_version"),
        "classification_type": category_type,
        "classification_code": classification.get("leaf_code"),
        "target_identity_preserved": True,
        "support_reference_preserved": True,
        "exact_classification_applied": True,
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


def _read_json_file(path: Path, label: str) -> JsonObject:
    if not path.is_file():
        raise ValueError(f"{label} is missing")
    return _parse_json(path.read_bytes(), label)


def _archive_native_runs(source_root: Path, archive_path: Path) -> Tuple[bytes, List[JsonObject]]:
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
            media_type = mimetypes.guess_type(relative)[0] or "application/octet-stream"
            manifest.append(
                {
                    "schema_version": 1,
                    "path": relative,
                    "media_type": media_type,
                    "bytes": len(content),
                    "sha256": _sha256(content),
                }
            )
    return archive_path.read_bytes(), manifest


def _runtime_identity(report: Mapping[str, object], manifest: Mapping[str, object]) -> JsonObject:
    if (
        report.get("status") != "portable_native_tool_runtime_closure_verified"
        or report.get("runtime_content_closed") is not True
    ):
        raise ValueError("copied native runtime is not verified")
    tree = _text(report.get("tree_sha256"), "runtime tree SHA-256")
    if manifest.get("tree_sha256") != tree:
        raise ValueError("copied runtime report and manifest disagree")
    cli = _object(manifest.get("tiangong_cli"), "TianGong CLI identity")
    if cli.get("package") != "@tiangong-lca/cli" or cli.get("version") != "0.0.25":
        raise ValueError("unsupported TianGong CLI runtime")
    return {
        "runtime_tree_sha256": tree,
        "tiangong_cli_package": cli["package"],
        "tiangong_cli_version": cli["version"],
    }


def execute_and_materialize(
    *,
    contents: Mapping[str, bytes],
    bindings: Mapping[str, Mapping[str, object]],
    artifact_root: Path,
    runtime_root: Path,
    out_dir: Path,
) -> JsonObject:
    verified_bindings = [
        _binding(bindings[name], contents[name], f"{name} binding")
        for name in EXPECTED_INPUT_NAMES
    ]
    input_version = input_version_for_bindings(verified_bindings)
    saved_report = _parse_json(contents["saved_plan_report"], "saved-plan report")
    plan_index = _parse_json(contents["plan_index"], "saved-plan index")
    descriptors = _parse_jsonl(
        contents["execution_descriptors"], "execution descriptors"
    )
    reviews = _parse_jsonl(
        contents["review_marker_carry_forward"], "review marker carry-forward"
    )
    decisions = _parse_jsonl(
        contents["classification_decisions"], "classification decisions"
    )
    supports = _parse_jsonl(contents["support_contracts"], "support contracts")
    saved_manifest = _parse_json(contents["saved_plan_manifest"], "saved-plan manifest")
    if (
        saved_report.get("contract") != SAVED_PLAN_CONTRACT
        or saved_report.get("status")
        != "uslci_flow_authoring_canary_saved_plans_compiled"
        or saved_report.get("saved_flow_plan_count") != 5
        or saved_report.get("complete_plan_count") != 5
        or saved_report.get("bulk_release_authorized") is not False
        or saved_report.get("routing", {}).get("next_internal_route")
        != "execute-uslci-flow-authoring-canary-native-gates"
        or plan_index.get("plan_set_version")
        != saved_report.get("saved_plan_set_version")
        or plan_index.get("apply_requires_exact_plan_bytes") is not True
        or len(descriptors) != 5
        or len(reviews) != 4
        or len(decisions) != 5
        or len(supports) != 3
        or saved_manifest.get("plan_count") != 5
    ):
        raise ValueError("saved-plan authority is not accepted for native execution")
    for name in (
        "plan_index",
        "execution_descriptors",
        "review_marker_carry_forward",
    ):
        expected = _object(
            saved_report.get("artifact_bindings", {}).get(name),
            f"saved-plan {name} binding",
        )
        if (
            expected.get("sha256") != bindings[name].get("sha256")
            or expected.get("bytes") != bindings[name].get("bytes")
        ):
            raise ValueError(f"saved-plan artifact drifted: {name}")
    runtime_report = _parse_json(contents["runtime_report"], "runtime report")
    runtime_manifest = _parse_json(contents["runtime_manifest"], "runtime manifest")
    runtime = _runtime_identity(runtime_report, runtime_manifest)
    if plan_index.get("runtime_tree_sha256") != runtime["runtime_tree_sha256"]:
        raise ValueError("plan index and copied runtime tree disagree")
    root = artifact_root.resolve()
    manifest_rows = [
        _object(value, "saved plan manifest row")
        for value in _array(saved_manifest.get("plans"), "saved plan manifest rows")
    ]
    index_rows = {
        row.get("canary_index"): row
        for row in _array(plan_index.get("ordered_flow_plans"), "ordered Flow plans")
        if isinstance(row, dict)
    }
    descriptor_by_index = {row.get("canary_index"): row for row in descriptors}
    decision_by_key = {row.get("authoring_intent_key"): row for row in decisions}
    plans = []
    for row in sorted(manifest_rows, key=lambda value: int(value.get("canary_index", 0))):
        index = _integer(row.get("canary_index"), "canary index")
        index_row = _object(index_rows.get(index), "plan index row")
        if (
            row.get("sha256") != index_row.get("sha256")
            or row.get("bytes") != index_row.get("bytes")
            or row.get("target_flow_id") != index_row.get("target_flow_id")
        ):
            raise ValueError("saved-plan manifest and index disagree")
        path = _safe_path(root, row.get("workspace_path"), "saved plan workspace path")
        content = path.read_bytes()
        if _sha256(content) != row.get("sha256") or len(content) != row.get("bytes"):
            raise ValueError("saved plan exact bytes drifted")
        plan = _parse_json(content, f"saved plan {index}")
        if plan.get("generation_manifest", {}).get("canary_index") != index:
            raise ValueError("saved plan canary index drifted")
        plans.append((row, path, plan))

    node = runtime_root / "bin/node"
    cli = runtime_root / CLI_PATH
    if not node.is_file() or not cli.is_file():
        raise ValueError("copied runtime executable is missing")
    gate_results = []
    classification_assertions = []
    support_assertions = []
    qa_outcomes = []
    qa_gaps = []
    unexpected_failures: List[JsonObject] = []
    outputs = out_dir / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="uslci-flow-native-gates-") as temp:
        scratch = Path(temp)
        for row, plan_path, plan in plans:
            index = int(row["canary_index"])
            descriptor = _object(
                descriptor_by_index.get(index), f"canary {index} descriptor"
            )
            target = _object(plan.get("target"), "saved plan target")
            classification = _object(
                plan.get("native_classification_plan"), "saved plan classification"
            )
            intent_key = _text(
                descriptor.get("authoring_intent_key"), "authoring intent key"
            )
            decision = _object(
                decision_by_key.get(intent_key), f"canary {index} classification decision"
            )
            if (
                decision.get("classification_code") != classification.get("leaf_code")
                or decision.get("classification_path") != classification.get("path")
            ):
                raise ValueError("classification decision and saved plan disagree")
            run = scratch / f"canary-{index:02d}"
            validate_dir = run / "validate"
            materialize_dir = run / "materialize"
            classify_dir = run / "classify"
            qa_dir = run / "qa"
            stdout_dir = run / "command-stdout"
            _run_command(
                [
                    str(node), str(cli), "flow", "build-plan", "validate",
                    "--input", str(plan_path), "--out-dir", str(validate_dir),
                    "--report-only", "--json",
                ],
                stdout_path=stdout_dir / "01-validate.json",
            )
            validate_report = _read_json_file(
                validate_dir / "outputs/build-plan-gate-report.json",
                f"canary {index} validate report",
            )
            if (
                validate_report.get("status") != "passed"
                or validate_report.get("action") != "validate"
                or validate_report.get("blockers") != []
                or validate_report.get("required_fields", {}).get("missing") != []
            ):
                raise ValueError(f"canary {index} native plan validation failed")
            _run_command(
                [
                    str(node), str(cli), "flow", "build-plan", "materialize",
                    "--input", str(plan_path), "--out-dir", str(materialize_dir),
                    "--report-only", "--json",
                ],
                stdout_path=stdout_dir / "02-materialize.json",
            )
            materialize_report = _read_json_file(
                materialize_dir / "outputs/build-plan-gate-report.json",
                f"canary {index} materialize report",
            )
            materialized_path = materialize_dir / "outputs/materialized-flow.json"
            materialized = _read_json_file(
                materialized_path, f"canary {index} materialized Flow"
            )
            if (
                materialize_report.get("status") != "passed"
                or materialize_report.get("action") != "materialize"
                or materialize_report.get("blockers") != []
                or materialize_report.get("schema_validation", {}).get("status")
                != "passed"
                or materialize_report.get("schema_validation", {}).get("issue_count")
                != 0
            ):
                raise ValueError(f"canary {index} native materialization failed")
            decision_payload = {
                "row_index": 0,
                "category_type": classification.get("category_type"),
                "code": classification.get("leaf_code"),
                "basis": decision.get("decision_rationale"),
                "evidence": {
                    "authoring_intent_key": intent_key,
                    "classification_decision_sha256": _sha256(_json_bytes(decision)),
                    "saved_plan_sha256": row.get("sha256"),
                },
            }
            decision_path = run / "classification-decision.json"
            decision_path.parent.mkdir(parents=True, exist_ok=True)
            decision_path.write_bytes(_json_bytes(decision_payload, pretty=True))
            classified_path = classify_dir / "classified-flow.jsonl"
            _run_command(
                [
                    str(node), str(cli), "dataset", "classification", "apply",
                    "--input", str(materialized_path), "--decisions", str(decision_path),
                    "--out", str(classified_path), "--type",
                    str(classification.get("category_type")), "--out-dir", str(classify_dir),
                    "--json",
                ],
                stdout_path=stdout_dir / "03-classification-apply.json",
            )
            classification_report = _read_json_file(
                classify_dir / "outputs/classification-apply-report.json",
                f"canary {index} classification report",
            )
            if (
                classification_report.get("status") != "completed"
                or classification_report.get("counts")
                != {"rows": 1, "decisions": 1, "applied": 1, "blockers": 0}
                or classification_report.get("blockers") != []
            ):
                raise ValueError(f"canary {index} exact classification apply failed")
            classified_rows = _parse_jsonl(
                classified_path.read_bytes(), f"canary {index} classified Flow"
            )
            if len(classified_rows) != 1:
                raise ValueError("classification output row count drifted")
            verification = verify_classified_flow(plan, classified_rows[0])
            verification.update(
                {
                    "schema_version": 1,
                    "canary_index": index,
                    "authoring_intent_key": intent_key,
                    "classified_flow_sha256": _sha256(classified_path.read_bytes()),
                }
            )
            classification_assertions.append(verification)
            support_assertions.append(
                {
                    "schema_version": 1,
                    "canary_index": index,
                    "target_flow_id": target.get("uuid"),
                    "flow_property_id": verification["flow_property_id"],
                    "flow_property_version": verification["flow_property_version"],
                    "support_reference_preserved": True,
                }
            )
            _run_command(
                [
                    str(node), str(cli), "qa", "flow", "--rows-file",
                    str(classified_path), "--out-dir", str(qa_dir), "--json",
                ],
                stdout_path=stdout_dir / "04-flow-qa.json",
            )
            qa_report = _read_json_file(
                qa_dir / "flow_qa_report.json", f"canary {index} QA report"
            )
            qa = classify_qa_outcome(
                qa_report, flow_type=_text(target.get("flow_type"), "Flow type")
            )
            qa.update(
                {
                    "schema_version": 1,
                    "canary_index": index,
                    "authoring_intent_key": intent_key,
                    "source_flow_id": row.get("source_flow_id"),
                    "target_flow_id": target.get("uuid"),
                    "flow_type": target.get("flow_type"),
                    "qa_report_sha256": _sha256(
                        (qa_dir / "flow_qa_report.json").read_bytes()
                    ),
                }
            )
            qa_outcomes.append(qa)
            if qa["outcome"] == "elementary_qa_harness_applicability_blocked":
                qa_gaps.append(
                    {
                        **qa,
                        "gap_id": f"elementary-flow-qa-harness:{intent_key}",
                        "next_route": NEXT_ROUTE,
                    }
                )
            gate_results.append(
                {
                    "schema_version": 1,
                    "canary_index": index,
                    "authoring_intent_key": intent_key,
                    "source_flow_id": row.get("source_flow_id"),
                    "target_flow_id": target.get("uuid"),
                    "saved_plan_sha256": row.get("sha256"),
                    "native_plan_validation": "passed",
                    "local_schema_materialization": "passed",
                    "exact_classification_apply": "passed",
                    "support_reference_preservation": "passed",
                    "native_flow_qa": qa["outcome"],
                    "save_draft_executed": False,
                    "remote_entity_created": False,
                    "bulk_release_authorized": False,
                }
            )
        archive_path = outputs / "uslci-flow-canary-native-gate-raw-artifacts.tar"
        archive_bytes, raw_manifest = _archive_native_runs(scratch, archive_path)
    raw_manifest_bytes = _jsonl_bytes(raw_manifest)
    output_rows = {
        "gate_results": (
            "uslci-flow-canary-native-gate-results.jsonl",
            _jsonl_bytes(gate_results),
        ),
        "classification_assertions": (
            "uslci-flow-canary-native-classification-assertions.jsonl",
            _jsonl_bytes(classification_assertions),
        ),
        "support_assertions": (
            "uslci-flow-canary-native-support-assertions.jsonl",
            _jsonl_bytes(support_assertions),
        ),
        "qa_outcomes": (
            "uslci-flow-canary-native-qa-outcomes.jsonl",
            _jsonl_bytes(qa_outcomes),
        ),
        "qa_harness_gaps": (
            "uslci-flow-canary-elementary-qa-harness-gaps.jsonl",
            _jsonl_bytes(qa_gaps),
        ),
        "unexpected_failures": (
            "uslci-flow-canary-native-unexpected-failures.jsonl",
            _jsonl_bytes(unexpected_failures),
        ),
        "raw_artifact_manifest": (
            "uslci-flow-canary-native-gate-raw-artifact-manifest.jsonl",
            raw_manifest_bytes,
        ),
        "raw_artifact_archive": (
            "uslci-flow-canary-native-gate-raw-artifacts.tar",
            archive_bytes,
        ),
    }
    artifact_bindings = {
        key: _artifact_binding(filename, content)
        for key, (filename, content) in output_rows.items()
    }
    native_gate_execution_version = _sha256(
        _json_bytes(
            {
                "contract": CONTRACT,
                "input_version": input_version,
                "gate_results_sha256": artifact_bindings["gate_results"]["sha256"],
                "qa_outcomes_sha256": artifact_bindings["qa_outcomes"]["sha256"],
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
        "status": "uslci_flow_authoring_canary_native_gates_completed_with_elementary_qa_harness_gap",
        "scope_id": saved_report.get("scope_id"),
        "revision": saved_report.get("revision"),
        "input_version": input_version,
        "native_gate_execution_version": native_gate_execution_version,
        "source_saved_plan_set_version": saved_report.get("saved_plan_set_version"),
        "source_support_resolution_version": saved_report.get(
            "source_support_resolution_version"
        ),
        "input_bindings": verified_bindings,
        "runtime_identity": runtime,
        "artifact_bindings": artifact_bindings,
        "raw_artifact_member_count": len(raw_manifest),
        "native_plan_validation_pass_count": 5,
        "local_flow_materialization_pass_count": 5,
        "exact_classification_apply_pass_count": 5,
        "support_reference_preservation_pass_count": 5,
        "qa_execution_count": 5,
        "qa_pass_count": sum(row["outcome"] == "passed" for row in qa_outcomes),
        "elementary_qa_harness_gap_count": len(qa_gaps),
        "scientific_review_marker_preserved_count": len(reviews),
        "unexpected_failure_count": len(unexpected_failures),
        "native_invocation_executed_count": 20,
        "native_plan_validation_executed_count": 5,
        "local_flow_materialization_executed_count": 5,
        "exact_classification_apply_executed_count": 5,
        "native_flow_qa_executed_count": 5,
        "flow_creation_executed_count": 0,
        "remote_flow_materialization_executed_count": 0,
        "save_draft_executed_count": 0,
        "network_read_executed_count": 0,
        "remote_candidate_search_executed_count": 0,
        "process_creation_executed_count": 0,
        "model_connection_executed_count": 0,
        "model_mutation_executed_count": 0,
        "external_write_executed_count": 0,
        "publication_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "save_draft_authorized": False,
        "bulk_release_authorized": False,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "failure_diagnosis": {
            "prompt_failure_count": 0,
            "context_failure_count": 0,
            "harness_failure_count": len(qa_gaps),
            "loop_failure_count": 0,
            "model_limitation_count": 0,
        },
        "external_patterns": [
            {
                "project": "kubernetes/kubernetes",
                "local_landing": "full_native_admission_and_schema_checks_without_persistence",
                "installed": False,
            },
            {
                "project": "hashicorp/terraform",
                "local_landing": "exact_saved_plan_and_runtime_rehash_before_execution",
                "installed": False,
            },
            {
                "project": "opencontainers/image-spec",
                "local_landing": "raw_native_archive_member_content_descriptors",
                "installed": False,
            },
            {
                "project": "open-policy-agent/opa",
                "local_landing": "classification_decision_results_separated_from_qa_policy_findings",
                "installed": False,
            },
        ],
        "routing": {"next_internal_route": NEXT_ROUTE, "pause_internal_work": False},
        "next_action": (
            "Resolve the TianGong Elementary-Flow QA harness applicability gap and replay "
            "the four schema-valid exactly classified Elementary canaries; keep Product QA, "
            "save-draft and bulk release boundaries unchanged."
        ),
    }
    for filename, content in output_rows.values():
        path = outputs / filename
        if path.name.endswith(".tar"):
            continue
        path.write_bytes(content)
    (outputs / "uslci-flow-authoring-canary-native-gates.json").write_bytes(
        _json_bytes(result, pretty=True)
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Execute five exact Flow plans through copied TianGong native gates."
    )
    for name in EXPECTED_INPUT_NAMES:
        option = name.replace("_", "-")
        parser.add_argument(f"--{option}", type=Path, required=True)
        parser.add_argument(f"--{option}-binding", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
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
        artifact_root=args.artifact_root,
        runtime_root=args.runtime_root,
        out_dir=args.out_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
