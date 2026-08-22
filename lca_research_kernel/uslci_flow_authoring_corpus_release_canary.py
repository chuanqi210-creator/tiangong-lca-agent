"""Execute the three authorized local Flow release canaries and freeze breadth."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from lca_research_kernel.uslci_flow_authoring_canary_native_gates import (  # noqa: E402
        verify_classified_flow,
    )
else:
    from .uslci_flow_authoring_canary_native_gates import verify_classified_flow


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_flow_authoring_corpus_release_canary/v1"
PLAN_CONTRACT = "lca_research_kernel.uslci_flow_authoring_corpus_release_plan/v1"
PLAN_SET_VERSION = "80c2f62b344154d7"
LOGIC_VERSION = "2026-07-19.1"
NEXT_ROUTE = "compile-minimum-lifepo4-comparison-graph"
EXPECTED_CANARIES = ((1, "Mass|kg"), (3, "Energy|MJ"), (2, "Area*Time|m2*a"))
EXPECTED_TOTAL = 1456
EXPECTED_UNMATERIALIZED = 1453
EXPECTED_STAGES = (
    "flow-build-plan-validate",
    "flow-build-plan-materialize",
    "dataset-classification-apply",
    "dataset-validate-flow",
    "qa-flow",
    "dataset-save-draft-dry-run",
    "readback-compare",
)
OUTPUT_NAMES = {
    "report": "uslci-flow-authoring-corpus-release-canary.json",
    "execution_ledger": "uslci-flow-authoring-corpus-release-canary-executions.jsonl",
    "risk_ledger": "uslci-flow-authoring-corpus-release-canary-risks.jsonl",
    "artifact_manifest": "uslci-flow-authoring-corpus-release-canary-artifacts.jsonl",
    "freeze_decision": "uslci-flow-authoring-corpus-freeze-decision.json",
    "failures": "uslci-flow-authoring-corpus-release-canary-failures.jsonl",
}
PRIOR_EFFECT_FIELDS = (
    "flow_creation_executed_count",
    "remote_flow_materialization_executed_count",
    "save_draft_executed_count",
    "external_write_executed_count",
)
EXPECTED_QA_RULESET = {
    "ruleset_id": "flow-authoring/strict",
    "ruleset_version": "1",
    "ruleset_source_version": "2026.05.23",
}
EXPECTED_QA_RULE_IDS = [
    "tidas.flow.name.base-name.technical",
    "tidas.flow.type.required",
    "tidas.flow.reference-property-unit.required",
    "tidas.flow.flow-property.mean-value.positive",
    "tidas.flow.classification.elementary.valid",
    "tidas.flow.evidence.field-bindings.required",
    "tidas.flow.identity.alias-equivalence.review",
]
ELEMENTARY_QA_MESSAGE = (
    "Flow type is Elementary flow; check whether this batch should exclude it."
)


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


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return dict(value)


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value.strip()


def _digest(value: object, label: str) -> str:
    token = _text(value, label)
    if len(token) != 64 or any(char not in "0123456789abcdef" for char in token):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return token


def _contained_path(root: Path, relative: object, label: str) -> Path:
    path = (root / _text(relative, f"{label} path")).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"{label} escaped artifact root") from error
    return path


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
            raise ValueError(f"{label} row {index} is invalid JSON") from error
    return rows


def _artifact_binding(workspace: Path, path: Path) -> JsonObject:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(workspace.resolve()).as_posix()
    except ValueError as error:
        raise ValueError("artifact escaped isolated workspace") from error
    content = resolved.read_bytes()
    digest = _sha256(content)
    return {
        "path": relative,
        "media_type": mimetypes.guess_type(relative)[0] or "application/octet-stream",
        "sha256": digest,
        "digest": f"sha256:{digest}",
        "bytes": len(content),
        "size": len(content),
    }


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _read_json(path: Path, label: str) -> JsonObject:
    if not path.is_file():
        raise ValueError(f"{label} is missing")
    return _parse_json(path.read_bytes(), label)


def _read_single_jsonl(path: Path, label: str) -> JsonObject:
    if not path.is_file():
        raise ValueError(f"{label} is missing")
    rows = _parse_jsonl(path.read_bytes(), label)
    if len(rows) != 1:
        raise ValueError(f"{label} must contain exactly one row")
    return rows[0]


def validate_local_only_command(command: Sequence[str]) -> None:
    """Reject any command outside the six fixed, local-only TianGong stages."""

    tokens = list(command)
    if len(tokens) < 3:
        raise ValueError("local-only command boundary rejected command")
    operation = tuple(tokens[1:4])
    short_operation = tuple(tokens[1:3])
    allowed = {
        ("flow", "build-plan", "validate"),
        ("flow", "build-plan", "materialize"),
        ("dataset", "classification", "apply"),
    }
    allowed_short = {
        ("dataset", "validate"),
        ("qa", "flow"),
        ("dataset", "save-draft"),
    }
    if operation not in allowed and short_operation not in allowed_short:
        raise ValueError("local-only command boundary rejected command")
    forbidden_flags = {"--endpoint", "--url", "--commit", "--remote", "--publish"}
    if forbidden_flags.intersection(tokens[1:]):
        raise ValueError("local-only command boundary rejected command")


def _run_stage(
    *,
    stage: str,
    command: Sequence[str],
    workspace: Path,
    stdout_path: Path,
    runner: Callable[..., subprocess.CompletedProcess],
) -> JsonObject:
    validate_local_only_command(command)
    completed = runner(
        list(command), cwd=workspace, capture_output=True, check=False
    )
    stdout = completed.stdout or b""
    stderr = completed.stderr or b""
    if isinstance(stdout, str):
        stdout = stdout.encode("utf-8")
    if isinstance(stderr, str):
        stderr = stderr.encode("utf-8")
    _write(stdout_path, stdout)
    if stderr:
        _write(stdout_path.with_suffix(".stderr.txt"), stderr)
    if completed.returncode != 0:
        raise ValueError(
            f"{stage} failed: {stderr.decode('utf-8', errors='replace').strip()}"
        )
    return {
        "stage": stage,
        "command": list(command[1:]),
        "returncode": completed.returncode,
        "stdout_sha256": _sha256(stdout),
        "stderr_sha256": _sha256(stderr),
    }


def _verify_validation_report(report: Mapping[str, object], target_flow_id: str) -> None:
    counts = _object(report.get("counts"), "Flow validation counts")
    rows = report.get("rows")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("native Flow validation row drifted")
    row = rows[0]
    if (
        report.get("status") != "completed"
        or report.get("requested_type") != "flow"
        or counts.get("total") != 1
        or counts.get("valid") != 1
        or counts.get("invalid") != 0
        or _object(counts.get("by_type"), "Flow validation type counts").get("flow") != 1
        or row.get("id") != target_flow_id
        or row.get("version") != "00.00.001"
        or row.get("type") != "flow"
        or row.get("status") != "valid"
        or row.get("issue_count") != 0
    ):
        raise ValueError("native Flow validation failed")


def _verify_qa_report(
    report: Mapping[str, object], *, target_flow_id: str, flow_type: str
) -> JsonObject:
    gate = _object(report.get("ruleset_gate"), "Flow QA ruleset gate")
    if flow_type == "Product flow":
        if (
            report.get("status") != "completed_local_flow_qa"
            or report.get("flow_count") != 1
            or report.get("blocker_count") != 0
            or gate.get("status") != "passed"
        ):
            raise ValueError("native Product Flow QA failed")
        return {
            "outcome": "strict_native_pass",
            "exception_applied": False,
            "native_finding_preserved": True,
        }
    if flow_type != "Elementary flow":
        raise ValueError("native Flow QA target type is unsupported")
    if any(report.get(key) != value for key, value in EXPECTED_QA_RULESET.items()) or any(
        gate.get(key) != value for key, value in EXPECTED_QA_RULESET.items()
    ):
        raise ValueError("typed QA ruleset identity drifted")
    if (
        report.get("status") != "completed_local_flow_qa"
        or report.get("logic_version") != "flow-v1.0-cli"
        or report.get("ruleset_rule_ids") != EXPECTED_QA_RULE_IDS
        or report.get("flow_count") != 1
        or report.get("rule_finding_count") != 1
        or report.get("llm_finding_count") != 0
        or report.get("finding_count") != 1
        or report.get("blocker_count") != 1
        or report.get("severity_counts") != {"warning": 1}
        or report.get("rule_counts") != {"elementary_flow_in_flow_qa": 1}
        or report.get("methodology_rule_counts")
        != {"tidas.flow.type.required": 1}
        or gate.get("status") != "blocked"
        or gate.get("ruleset_rule_ids") != EXPECTED_QA_RULE_IDS
        or gate.get("counts") != {"findings": 1, "blockers": 1}
    ):
        raise ValueError("typed QA aggregate fingerprint drifted")
    llm = _object(report.get("llm"), "Flow QA LLM status")
    blockers = gate.get("blockers")
    if (
        llm.get("enabled") is not False
        or llm.get("reviewed_flow_count") != 0
        or not isinstance(blockers, list)
        or len(blockers) != 1
        or not isinstance(blockers[0], dict)
    ):
        raise ValueError("typed QA blocker cardinality drifted")
    expected_blocker = {
        "flow_uuid": target_flow_id,
        "base_version": "00.00.001",
        "severity": "warning",
        "rule_id": "elementary_flow_in_flow_qa",
        "ruleset_id": "flow-authoring/strict",
        "methodology_rule_id": "tidas.flow.type.required",
        "message": ELEMENTARY_QA_MESSAGE,
        "fixability": "review-needed",
        "source": "rule",
        "evidence": {"typeOfDataSet": "Elementary flow"},
    }
    if blockers[0] != expected_blocker:
        raise ValueError("typed QA Elementary finding fingerprint drifted")
    return {
        "outcome": "not_applicable_to_intentional_elementary_authoring_release_canary",
        "exception_applied": True,
        "native_rule_id": "elementary_flow_in_flow_qa",
        "methodology_rule_id": "tidas.flow.type.required",
        "native_finding_preserved": True,
        "scientific_mapping_invalidated": False,
    }


def _verify_save_report(report: Mapping[str, object]) -> None:
    counts = _object(report.get("counts"), "Flow save-draft counts")
    if (
        report.get("status") != "completed"
        or report.get("requested_type") != "flow"
        or report.get("commit") is not False
        or report.get("mode") != "dry_run"
        or counts.get("selected") != 1
        or counts.get("prepared") != 1
        or counts.get("executed") != 0
        or counts.get("failed") != 0
        or _object(counts.get("by_table"), "Flow save-draft table counts").get("flows")
        != 1
    ):
        raise ValueError("native Flow save-draft dry-run failed or executed a write")


def _strip_applied_classification(
    flow: Mapping[str, object], *, category_type: str
) -> JsonObject:
    copied = _parse_json(_json_bytes(flow), "Flow copy")
    data_info = _object(
        _object(
            _object(copied.get("flowDataSet"), "Flow data set").get("flowInformation"),
            "Flow information",
        ).get("dataSetInformation"),
        "Flow data information",
    )
    classification = data_info.get("classificationInformation")
    if not isinstance(classification, dict):
        raise ValueError("classified Flow has no classification information")
    if category_type == "flow-elementary":
        classification.pop("common:elementaryFlowCategorization", None)
    else:
        raise ValueError("readback delta normalization supports only selected Elementary canaries")
    if classification:
        data_info["classificationInformation"] = classification
    else:
        data_info.pop("classificationInformation", None)
    copied["flowDataSet"]["flowInformation"]["dataSetInformation"] = data_info
    return copied


def _input_identity(
    *,
    release_report: Mapping[str, object],
    selected: Sequence[Mapping[str, object]],
    retained_risks: Sequence[Mapping[str, object]],
    held_conflict: Mapping[str, object],
) -> Tuple[str, str]:
    declared_report_sha256 = release_report.get("report_sha256")
    release_report_sha256 = (
        _digest(declared_report_sha256, "release-plan report SHA-256")
        if declared_report_sha256 is not None
        else _sha256(_json_bytes(release_report, pretty=True))
    )
    value = {
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "plan_set_version": PLAN_SET_VERSION,
        "release_report_sha256": release_report_sha256,
        "selected_plan_sha256": [row["release_plan_sha256"] for row in selected],
        "retained_risk_sha256": [_sha256(_json_bytes(row)) for row in retained_risks],
        "held_conflict_sha256": _sha256(_json_bytes(held_conflict)),
    }
    return _sha256(_json_bytes(value))[:16], release_report_sha256


def _verify_replay_report(workspace: Path, report_path: Path, input_version: str) -> JsonObject:
    report = _read_json(report_path, "existing release-canary report")
    if (
        report.get("contract") != CONTRACT
        or report.get("status")
        != "uslci_flow_authoring_corpus_release_canary_completed"
        or report.get("activity_input_version") != input_version
        or report.get("corpus_release_frozen") is not True
    ):
        raise ValueError("existing release-canary report is not replayable")
    bindings = _object(report.get("artifact_bindings"), "release-canary bindings")
    for name, value in bindings.items():
        binding = _object(value, f"release-canary {name} binding")
        path = _contained_path(workspace, binding.get("path"), f"release-canary {name}")
        if not path.is_file():
            raise ValueError(f"release-canary replay artifact is missing: {name}")
        content = path.read_bytes()
        if len(content) != binding.get("bytes") or _sha256(content) != binding.get(
            "sha256"
        ):
            raise ValueError(f"release-canary replay artifact drifted: {name}")
    return report


def _by_position(rows: Sequence[Mapping[str, object]]) -> Dict[int, JsonObject]:
    result = {}
    for value in rows:
        row = _object(value, "release plan")
        position = _integer(row.get("corpus_position"), "release-plan position")
        if position in result:
            raise ValueError("release-plan positions are not unique")
        result[position] = row
    if sorted(result) != list(range(1, EXPECTED_TOTAL + 1)):
        raise ValueError("release-plan positions are not contiguous")
    return result


def select_release_canaries(
    *,
    release_report: Mapping[str, object],
    release_plans: Sequence[Mapping[str, object]],
    release_canaries: Sequence[Mapping[str, object]],
    artifact_root: Path,
) -> List[JsonObject]:
    """Return only the exact content-addressed canaries after fail-closed checks."""

    report = _object(release_report, "release-plan report")
    if report.get("contract") != PLAN_CONTRACT:
        raise ValueError("release-plan contract drifted")
    if report.get("status") != "uslci_flow_authoring_corpus_release_plan_compiled":
        raise ValueError("release-plan status drifted")
    if report.get("release_plan_set_version") != PLAN_SET_VERSION:
        raise ValueError("release-plan set version drifted")
    if report.get("release_plan_count") != EXPECTED_TOTAL:
        raise ValueError("release-plan count drifted")
    if report.get("release_canary_count") != len(EXPECTED_CANARIES):
        raise ValueError("release canary count drifted")
    if report.get("execution_authorized_count") != 0:
        raise ValueError("prior release execution effect detected")
    if any(report.get(field, 0) != 0 for field in PRIOR_EFFECT_FIELDS):
        raise ValueError("prior entity/save effect detected")
    if len(release_plans) != EXPECTED_TOTAL or len(release_canaries) != len(
        EXPECTED_CANARIES
    ):
        raise ValueError("release-plan or canary cardinality drifted")
    if any(
        row.get("execution_authorized") is not False
        or row.get("flow_entity_release_authorized") is not False
        or row.get("save_draft_authorized") is not False
        for row in release_plans
    ):
        raise ValueError("pre-authorized release record detected")

    by_position = _by_position(release_plans)
    selected = []
    for selection_order, (declaration_value, expected) in enumerate(
        zip(release_canaries, EXPECTED_CANARIES), start=1
    ):
        declaration = _object(declaration_value, "release canary")
        position, property_name = expected
        plan = by_position[position]
        if plan.get("release_lane") == "held_classification_namespace_conflict":
            raise ValueError("held release record cannot execute")
        if (
            declaration.get("selection_order") != selection_order
            or declaration.get("corpus_position") != position
            or declaration.get("flow_property") != property_name
            or plan.get("flow_property") != property_name
            or declaration.get("target_flow_id") != plan.get("target_flow_id")
            or declaration.get("release_lane") != plan.get("release_lane")
            or declaration.get("execution_authorized") is not False
        ):
            raise ValueError("release canary identity drifted")
        plan_digest = _digest(plan.get("plan_sha256"), "release plan SHA-256")
        if declaration.get("release_plan_sha256") != plan_digest:
            raise ValueError("release canary plan hash drifted")
        saved = _object(plan.get("saved_plan"), "saved plan")
        saved_path = _contained_path(artifact_root, saved.get("path"), "saved plan")
        if not saved_path.is_file():
            raise ValueError("saved plan is missing")
        content = saved_path.read_bytes()
        if (
            len(content) != _integer(saved.get("bytes"), "saved plan bytes")
            or _sha256(content) != _digest(saved.get("sha256"), "saved plan SHA-256")
        ):
            raise ValueError("saved plan hash or byte count drifted")
        selected.append(
            {
                **plan,
                "release_plan_sha256": plan_digest,
                "saved_plan_bytes": content,
            }
        )
    return selected


def execute_release_canaries(
    *,
    release_report: Mapping[str, object],
    release_plans: Sequence[Mapping[str, object]],
    release_canaries: Sequence[Mapping[str, object]],
    risk_ledger: Sequence[Mapping[str, object]],
    held_conflicts: Sequence[Mapping[str, object]],
    artifact_root: Path,
    workspace: Path,
    tiangong_cli: Path,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> JsonObject:
    """Run exactly three local native stage graphs and accept a corpus freeze."""

    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    selected = select_release_canaries(
        release_report=release_report,
        release_plans=release_plans,
        release_canaries=release_canaries,
        artifact_root=artifact_root,
    )
    risk_by_id = {}
    for value in risk_ledger:
        risk = _object(value, "release risk")
        risk_id = _text(risk.get("risk_id"), "release risk ID")
        if risk_id in risk_by_id:
            raise ValueError("release risk ID is duplicated")
        risk_by_id[risk_id] = risk
    retained_risks = []
    for plan in selected:
        if plan.get("release_lane") == "planned_with_retained_review":
            risk = risk_by_id.get(plan.get("risk_id"))
            if not isinstance(risk, dict):
                raise ValueError("selected retained-review risk is missing")
            retained_risks.append(risk)
    if len(retained_risks) != 2 or len({row["risk_id"] for row in retained_risks}) != 2:
        raise ValueError("selected retained-review risks drifted")
    if len(held_conflicts) != 1:
        raise ValueError("held CPC conflict cardinality drifted")
    held_conflict = _object(held_conflicts[0], "held CPC conflict")
    if (
        held_conflict.get("classification_code") != "33370"
        or held_conflict.get("release_lane")
        != "held_classification_namespace_conflict"
    ):
        raise ValueError("held CPC conflict drifted")

    input_version, release_report_sha256 = _input_identity(
        release_report=release_report,
        selected=selected,
        retained_risks=retained_risks,
        held_conflict=held_conflict,
    )
    activity_id = f"uslci-flow-release-canary:{input_version}"
    outputs = workspace / "outputs"
    report_path = outputs / OUTPUT_NAMES["report"]
    if report_path.is_file():
        return _verify_replay_report(workspace, report_path, input_version)

    execution_rows = []
    artifact_paths = []
    failure_rows = []
    try:
        for selection_order, plan_value in enumerate(selected, start=1):
            plan = dict(plan_value)
            plan_bytes = plan.pop("saved_plan_bytes")
            if not isinstance(plan_bytes, bytes):
                raise ValueError("selected saved plan bytes are unavailable")
            target_flow_id = _text(plan.get("target_flow_id"), "target Flow ID")
            run_root = workspace / f"canary-{selection_order}-{target_flow_id}"
            plan_path = run_root / "inputs/flow-build-plan.json"
            decision_path = run_root / "inputs/classification-decision.json"
            validate_dir = run_root / "native/plan-validate"
            materialize_dir = run_root / "native/materialize"
            classification_dir = run_root / "native/classification"
            validation_dir = run_root / "native/validation"
            qa_dir = run_root / "native/qa"
            save_dir = run_root / "native/save-draft"
            stdout_dir = run_root / "native/command-stdout"
            classified_path = classification_dir / "classified-flow.jsonl"
            _write(plan_path, plan_bytes)
            plan_document = _parse_json(plan_bytes, f"canary {selection_order} saved plan")
            target = _object(plan_document.get("target"), "saved plan target")
            classification = _object(
                plan_document.get("native_classification_plan"),
                "saved plan classification",
            )
            if target.get("uuid") != target_flow_id:
                raise ValueError("saved plan target identity drifted")
            decision = {
                "row_index": 0,
                "dataset_id": target_flow_id,
                "dataset_version": target.get("version"),
                "category_type": classification.get("category_type"),
                "code": classification.get("leaf_code"),
                "path": classification.get("path"),
                "basis": "exact classification preserved from accepted saved plan",
                "evidence": {
                    "release_plan_sha256": plan["release_plan_sha256"],
                    "saved_plan_sha256": _sha256(plan_bytes),
                },
            }
            _write(decision_path, _json_bytes(decision, pretty=True))
            commands = (
                (
                    EXPECTED_STAGES[0],
                    [
                        str(tiangong_cli),
                        "flow",
                        "build-plan",
                        "validate",
                        "--input",
                        str(plan_path),
                        "--out-dir",
                        str(validate_dir),
                        "--json",
                    ],
                ),
                (
                    EXPECTED_STAGES[1],
                    [
                        str(tiangong_cli),
                        "flow",
                        "build-plan",
                        "materialize",
                        "--input",
                        str(plan_path),
                        "--out-dir",
                        str(materialize_dir),
                        "--json",
                    ],
                ),
                (
                    EXPECTED_STAGES[2],
                    [
                        str(tiangong_cli),
                        "dataset",
                        "classification",
                        "apply",
                        "--input",
                        str(materialize_dir / "outputs/materialized-flow.json"),
                        "--decisions",
                        str(decision_path),
                        "--out",
                        str(classified_path),
                        "--type",
                        str(classification.get("category_type")),
                        "--out-dir",
                        str(classification_dir),
                        "--json",
                    ],
                ),
                (
                    EXPECTED_STAGES[3],
                    [
                        str(tiangong_cli),
                        "dataset",
                        "validate",
                        "--input",
                        str(classified_path),
                        "--type",
                        "flow",
                        "--out-dir",
                        str(validation_dir),
                        "--json",
                    ],
                ),
                (
                    EXPECTED_STAGES[4],
                    [
                        str(tiangong_cli),
                        "qa",
                        "flow",
                        "--rows-file",
                        str(classified_path),
                        "--out-dir",
                        str(qa_dir),
                        "--json",
                    ],
                ),
                (
                    EXPECTED_STAGES[5],
                    [
                        str(tiangong_cli),
                        "dataset",
                        "save-draft",
                        "--input",
                        str(classified_path),
                        "--type",
                        "flow",
                        "--out-dir",
                        str(save_dir),
                        "--dry-run",
                        "--json",
                    ],
                ),
            )
            stage_evidence = []
            typed_qa = None
            for stage_index, (stage, command) in enumerate(commands, start=1):
                stage_evidence.append(
                    _run_stage(
                        stage=stage,
                        command=command,
                        workspace=workspace,
                        stdout_path=stdout_dir / f"{stage_index:02d}-{stage}.json",
                        runner=runner,
                    )
                )
                if stage == EXPECTED_STAGES[0]:
                    report = _read_json(
                        validate_dir / "outputs/build-plan-gate-report.json",
                        "native plan validation report",
                    )
                    if (
                        report.get("status") != "passed"
                        or report.get("action") != "validate"
                        or report.get("blockers") != []
                        or _object(
                            report.get("required_fields"), "native required fields"
                        ).get("missing")
                        != []
                    ):
                        raise ValueError("native plan validation failed")
                elif stage == EXPECTED_STAGES[1]:
                    report = _read_json(
                        materialize_dir / "outputs/build-plan-gate-report.json",
                        "native materialization report",
                    )
                    if (
                        report.get("status") != "passed"
                        or report.get("action") != "materialize"
                        or report.get("blockers") != []
                        or _object(
                            report.get("schema_validation"),
                            "native materialization schema validation",
                        ).get("status")
                        != "passed"
                        or report.get("schema_validation", {}).get("issue_count") != 0
                    ):
                        raise ValueError("native Flow materialization failed")
                elif stage == EXPECTED_STAGES[2]:
                    report = _read_json(
                        classification_dir / "outputs/classification-apply-report.json",
                        "native classification report",
                    )
                    if (
                        report.get("status") != "completed"
                        or report.get("counts")
                        != {"rows": 1, "decisions": 1, "applied": 1, "blockers": 0}
                        or report.get("blockers") != []
                    ):
                        raise ValueError("native classification apply failed")
                elif stage == EXPECTED_STAGES[3]:
                    _verify_validation_report(
                        _read_json(
                            validation_dir / "outputs/validation-report.json",
                            "native Flow validation report",
                        ),
                        target_flow_id,
                    )
                elif stage == EXPECTED_STAGES[4]:
                    typed_qa = _verify_qa_report(
                        _read_json(
                            qa_dir / "flow_qa_report.json", "native Flow QA report"
                        ),
                        target_flow_id=target_flow_id,
                        flow_type=_text(target.get("flow_type"), "target Flow type"),
                    )
                elif stage == EXPECTED_STAGES[5]:
                    _verify_save_report(
                        _read_json(
                            save_dir / "outputs/dataset-save-draft/summary.json",
                            "native save-draft report",
                        )
                    )

            materialized_path = materialize_dir / "outputs/materialized-flow.json"
            classified = _read_single_jsonl(classified_path, "classified Flow")
            materialized = _read_json(materialized_path, "materialized Flow")
            semantic = verify_classified_flow(plan_document, classified)
            if _strip_applied_classification(
                classified,
                category_type=_text(
                    classification.get("category_type"), "classification category type"
                ),
            ) != materialized:
                raise ValueError("readback changed fields outside exact classification")
            validation_report = _read_json(
                validation_dir / "outputs/validation-report.json",
                "native Flow validation report",
            )
            if validation_report["rows"][0].get("version") != target.get("version"):
                raise ValueError("readback target version drifted")
            readback = {
                "schema_version": 1,
                "contract": "lca_research_kernel.flow_readback_comparison/v1",
                "status": "passed",
                "selection_order": selection_order,
                "target_flow_id": target_flow_id,
                "target_version": target.get("version"),
                "release_plan_sha256": plan["release_plan_sha256"],
                "saved_plan_sha256": _sha256(plan_bytes),
                "materialized_flow_sha256": _sha256(materialized_path.read_bytes()),
                "classified_flow_sha256": _sha256(classified_path.read_bytes()),
                "canonical_dataset_sha256": _sha256(_json_bytes(classified)),
                "target_identity_exact": semantic["target_identity_preserved"],
                "flow_property_exact": semantic["support_reference_preserved"],
                "classification_exact": semantic["exact_classification_applied"],
                "classification_only_delta": True,
            }
            readback_path = run_root / "verification/readback-report.json"
            _write(readback_path, _json_bytes(readback, pretty=True))
            save_report_path = save_dir / "outputs/dataset-save-draft/summary.json"
            execution_rows.append(
                {
                    "schema_version": 1,
                    "activity_id": activity_id,
                    "selection_order": selection_order,
                    "corpus_position": plan["corpus_position"],
                    "target_flow_id": target_flow_id,
                    "flow_property": plan["flow_property"],
                    "release_lane": plan["release_lane"],
                    "risk_id": plan.get("risk_id"),
                    "release_plan_sha256": plan["release_plan_sha256"],
                    "classified_dataset_sha256": _sha256(classified_path.read_bytes()),
                    "save_report_sha256": _sha256(save_report_path.read_bytes()),
                    "readback_report_sha256": _sha256(readback_path.read_bytes()),
                    "stage_order": list(EXPECTED_STAGES),
                    "native_command_evidence": stage_evidence,
                    "typed_qa": typed_qa,
                    "local_materialized": True,
                    "save_draft_dry_run_prepared": True,
                    "readback_exact_match": True,
                    "remote_write_executed": False,
                }
            )
            artifact_paths.extend(path for path in run_root.rglob("*") if path.is_file())
    except Exception as error:
        failure_rows.append(
            {
                "schema_version": 1,
                "activity_id": activity_id,
                "status": "failed",
                "error_type": type(error).__name__,
                "error": str(error),
                "corpus_release_frozen": False,
            }
        )
        _write(
            outputs / OUTPUT_NAMES["failures"], _jsonl_bytes(failure_rows)
        )
        raise

    artifact_manifest_rows = [
        _artifact_binding(workspace, path)
        for path in sorted(set(artifact_paths), key=lambda value: value.as_posix())
    ]
    execution_content = _jsonl_bytes(execution_rows)
    risk_content = _jsonl_bytes(retained_risks)
    manifest_content = _jsonl_bytes(artifact_manifest_rows)
    failures_content = b""
    _write(outputs / OUTPUT_NAMES["execution_ledger"], execution_content)
    _write(outputs / OUTPUT_NAMES["risk_ledger"], risk_content)
    _write(outputs / OUTPUT_NAMES["artifact_manifest"], manifest_content)
    _write(outputs / OUTPUT_NAMES["failures"], failures_content)

    classified_hashes = [row["classified_dataset_sha256"] for row in execution_rows]
    save_hashes = [row["save_report_sha256"] for row in execution_rows]
    readback_hashes = [row["readback_report_sha256"] for row in execution_rows]
    freeze = {
        "schema_version": 1,
        "contract": "lca_research_kernel.uslci_flow_authoring_corpus_freeze/v1",
        "status": "accepted",
        "activity_id": activity_id,
        "activity_input_version": input_version,
        "release_plan_report_sha256": release_report_sha256,
        "release_plan_set_version": PLAN_SET_VERSION,
        "release_plan_sha256": [row["release_plan_sha256"] for row in selected],
        "classified_dataset_sha256": classified_hashes,
        "save_report_sha256": save_hashes,
        "readback_report_sha256": readback_hashes,
        "retained_risk_ids": [row["risk_id"] for row in retained_risks],
        "retained_risk_sha256": [_sha256(_json_bytes(row)) for row in retained_risks],
        "held_conflict_sha256": _sha256(_json_bytes(held_conflict)),
        "executed_release_record_count": len(execution_rows),
        "unmaterialized_release_record_count": EXPECTED_UNMATERIALIZED,
        "remaining_release_records_authorized": False,
        "general_corpus_release_frozen": True,
        "corpus_release_frozen": True,
        "next_internal_route": NEXT_ROUTE,
    }
    if (
        len(execution_rows) != 3
        or len(set(classified_hashes)) != 3
        or len(save_hashes) != 3
        or len(readback_hashes) != 3
        or any(not row["readback_exact_match"] for row in execution_rows)
    ):
        raise ValueError("freeze evidence cardinality drifted")
    for row in artifact_manifest_rows:
        path = _contained_path(workspace, row["path"], "freeze artifact")
        content = path.read_bytes()
        if len(content) != row["bytes"] or _sha256(content) != row["sha256"]:
            raise ValueError("freeze artifact binding drifted")
    freeze_path = outputs / OUTPUT_NAMES["freeze_decision"]
    _write(freeze_path, _json_bytes(freeze, pretty=True))
    output_bindings = {
        name: _artifact_binding(workspace, outputs / filename)
        for name, filename in OUTPUT_NAMES.items()
        if name != "report"
    }
    report = {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "status": "uslci_flow_authoring_corpus_release_canary_completed",
        "activity_id": activity_id,
        "activity_input_version": input_version,
        "release_plan_set_version": PLAN_SET_VERSION,
        "release_plan_report_sha256": release_report_sha256,
        "artifact_bindings": output_bindings,
        "executed_canary_count": 3,
        "local_materialized_flow_count": 3,
        "native_invocation_executed_count": 18,
        "kernel_readback_comparison_count": 3,
        "typed_qa_pass_count": sum(
            isinstance(row.get("typed_qa"), dict) for row in execution_rows
        ),
        "save_draft_dry_run_evidence_count": 3,
        "readback_exact_match_count": 3,
        "retained_review_count": 2,
        "held_conflict_count": 1,
        "unmaterialized_release_record_count": EXPECTED_UNMATERIALIZED,
        "flow_creation_executed_count": 3,
        "save_draft_executed_count": 0,
        "remote_write_executed_count": 0,
        "model_connection_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "corpus_release_frozen": True,
        "next_internal_route": NEXT_ROUTE,
        "next_action": (
            "Compile the minimum LiFePO4 comparison graph; keep general corpus release, "
            "background expansion, remote writes, publication and LCIA closed."
        ),
    }
    _write(report_path, _json_bytes(report, pretty=True))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Execute three isolated TianGong Flow release canaries."
    )
    parser.add_argument("--release-report", type=Path, required=True)
    parser.add_argument("--release-plans", type=Path, required=True)
    parser.add_argument("--release-canaries", type=Path, required=True)
    parser.add_argument("--risk-ledger", type=Path, required=True)
    parser.add_argument("--held-conflicts", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--tiangong-cli", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    canaries_value = json.loads(args.release_canaries.read_bytes())
    if not isinstance(canaries_value, list):
        raise ValueError("release canaries must be an array")
    report = execute_release_canaries(
        release_report=_parse_json(args.release_report.read_bytes(), "release-plan report"),
        release_plans=_parse_jsonl(args.release_plans.read_bytes(), "release plans"),
        release_canaries=canaries_value,
        risk_ledger=_parse_jsonl(args.risk_ledger.read_bytes(), "release risks"),
        held_conflicts=_parse_jsonl(
            args.held_conflicts.read_bytes(), "held release conflicts"
        ),
        artifact_root=args.artifact_root,
        workspace=args.workspace,
        tiangong_cli=args.tiangong_cli,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
