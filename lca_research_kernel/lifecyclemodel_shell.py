"""Materialize and verify route-local TIDAS lifecyclemodel shells."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

try:
    from .lifecyclemodel_assembly_plan import (
        ASSEMBLY_PLAN_CONTRACT,
        TIANGONG_PACKAGE,
        extract_current_process_facts,
    )
except ImportError:  # Direct activity-script execution from the project workspace.
    from lifecyclemodel_assembly_plan import (  # type: ignore
        ASSEMBLY_PLAN_CONTRACT,
        TIANGONG_PACKAGE,
        extract_current_process_facts,
    )


JsonObject = Dict[str, object]
SHELL_CONTRACT = "lca_research_kernel.native_route_lifecyclemodel_shell_materialization/v1"
SHELL_DRAFT_STATUS = "native_route_lifecyclemodel_shells_compiled_pre_native_validation"
LIFECYCLEMODEL_SCHEMA_SHA256 = (
    "a7fc9f9bdbb12f01e1fa52c387c3862a88cf44b3edc35a1cb8313c8a72d67cbc"
)
EXPECTED_ROUTES = {"hydrothermal", "solid-state"}
MODEL_VERSION = "00.00.001"


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash(value: object) -> str:
    return _hash_bytes(_json_bytes(value))


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _required_object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _required_list(value: object, label: str) -> List[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    return value


def _binding(value: object, label: str) -> JsonObject:
    row = _required_object(value, label)
    path = _required_text(row.get("path"), f"{label}.path")
    digest = _required_text(row.get("sha256"), f"{label}.sha256")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError(f"{label}.sha256 must be a lowercase sha256")
    return {"path": path, "sha256": digest}


def _validate_plan(plan: JsonObject) -> Dict[str, JsonObject]:
    if (
        plan.get("contract") != ASSEMBLY_PLAN_CONTRACT
        or plan.get("status")
        != "native_route_lifecyclemodel_assembly_plans_compiled"
        or plan.get("tool_identity") != TIANGONG_PACKAGE
        or plan.get("route_count") != 2
        or plan.get("native_node_count") != 2
        or plan.get("native_edge_count") != 0
        or plan.get("native_boundary_count") != 2
        or plan.get("native_invocation_count") != 0
        or plan.get("native_unresolved_count") != 0
        or plan.get("preserved_blocked_check_count") != 9
        or plan.get("preserved_scientific_gap_count") != 5
        or plan.get("scientific_comparison_ready") is not False
        or plan.get("comparative_lcia_allowed") is not False
        or plan.get("routing", {}).get("next_internal_route")
        != "materialize-native-route-lifecyclemodel-shells"
        or plan.get("routing", {}).get("pause_internal_work") is not False
    ):
        raise ValueError("accepted Beat 30 native assembly plan is required")
    routes = _required_list(plan.get("routes"), "Beat 30 routes")
    by_route = {
        str(row.get("route_id")): row for row in routes if isinstance(row, dict)
    }
    if set(by_route) != EXPECTED_ROUTES or len(routes) != 2:
        raise ValueError("accepted Beat 30 plan must contain both exact routes")
    for route_id, row in by_route.items():
        summary = _required_object(row.get("native_summary"), f"{route_id} summary")
        if (
            row.get("native_resolution") != "reused_existing_process"
            or row.get("boundary_reason") != "collapsed_at_existing_process"
            or summary
            != {
                "node_count": 1,
                "edge_count": 0,
                "invocation_count": 0,
                "unresolved_count": 0,
            }
        ):
            raise ValueError(f"{route_id} Beat 30 route boundary drifted")
    return by_route


def _global_reference(
    identifier: str,
    version: str,
    description: object,
    dataset_type: str,
    uri: str,
) -> JsonObject:
    return {
        "@refObjectId": identifier,
        "@type": dataset_type,
        "@uri": uri,
        "@version": version,
        "common:shortDescription": copy.deepcopy(description),
    }


def _model_uuid(
    scope_id: str,
    revision: str,
    route_id: str,
    process_id: str,
    process_version: str,
    row_sha256: str,
    plan_sha256: str,
) -> str:
    identity = "|".join(
        (
            SHELL_CONTRACT,
            scope_id,
            revision,
            route_id,
            process_id,
            process_version,
            row_sha256,
            plan_sha256,
        )
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, identity))


def _shell_for_process(
    row: JsonObject,
    facts: JsonObject,
    plan_route: JsonObject,
    plan_binding: JsonObject,
    scope_id: str,
    revision: str,
    row_sha256: str,
) -> JsonObject:
    process = _required_object(row.get("processDataSet"), "processDataSet")
    process_information = _required_object(
        process.get("processInformation"), "process information"
    )
    dataset_information = _required_object(
        process_information.get("dataSetInformation"), "process dataset information"
    )
    name = _required_object(dataset_information.get("name"), "process name")
    classification = _required_object(
        dataset_information.get("classificationInformation"),
        "process classification",
    )
    modelling = _required_object(
        process.get("modellingAndValidation"), "process modelling and validation"
    )
    compliance = _required_object(
        modelling.get("complianceDeclarations"), "process compliance declarations"
    )
    administrative = _required_object(
        process.get("administrativeInformation"), "process administrative information"
    )
    commissioner = _required_object(
        administrative.get("common:commissionerAndGoal"), "process commissioner"
    )
    data_entry = _required_object(
        administrative.get("dataEntryBy"), "process data entry"
    )
    publication = copy.deepcopy(
        _required_object(
            administrative.get("publicationAndOwnership"), "process publication"
        )
    )
    entering_ref = copy.deepcopy(
        _required_object(
            data_entry.get("common:referenceToPersonOrEntityEnteringTheData"),
            "process data-entry reference",
        )
    )
    route_id = str(facts["route_id"])
    process_id = str(facts["process_id"])
    process_version = str(facts["process_version"])
    model_id = _model_uuid(
        scope_id,
        revision,
        route_id,
        process_id,
        process_version,
        row_sha256,
        str(plan_binding["sha256"]),
    )
    base_name = copy.deepcopy(name.get("baseName"))
    provenance = {
        "contract": SHELL_CONTRACT,
        "scope_id": scope_id,
        "revision": revision,
        "route_id": route_id,
        "source_process_id": process_id,
        "source_process_version": process_version,
        "source_row_sha256": row_sha256,
        "source_plan_sha256": plan_binding["sha256"],
        "source_request_sha256": plan_route["request_sha256"],
        "reference_flow_id": facts["reference"]["flow_id"],
        "reference_flow_version": facts["reference"]["flow_version"],
        "reference_amount": facts["reference"]["mean_amount"],
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "resulting_process_materialized": False,
    }
    publication["common:dataSetVersion"] = MODEL_VERSION
    publication["common:permanentDataSetURI"] = f"urn:uuid:{model_id}"
    payload = {
        "lifeCycleModelDataSet": {
            "@xmlns": "http://eplca.jrc.ec.europa.eu/ILCD/LifeCycleModel/2017",
            "@xmlns:acme": "http://acme.com/custom",
            "@xmlns:common": "http://lca.jrc.it/ILCD/Common",
            "@xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "@locations": "../ILCDLocations.xml",
            "@version": "1.1",
            "@xsi:schemaLocation": (
                "http://eplca.jrc.ec.europa.eu/ILCD/LifeCycleModel/2017 "
                "../../schemas/ILCD_LifeCycleModelDataSet.xsd"
            ),
            "lifeCycleModelInformation": {
                "dataSetInformation": {
                    "common:UUID": model_id,
                    "name": copy.deepcopy(name),
                    "classificationInformation": copy.deepcopy(classification),
                    "referenceToResultingProcess": _global_reference(
                        model_id,
                        MODEL_VERSION,
                        base_name,
                        "process data set",
                        f"../processes/{model_id}_{MODEL_VERSION}.xml",
                    ),
                    "common:generalComment": [
                        {
                            "@xml:lang": "en",
                            "#text": json.dumps(
                                provenance,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        }
                    ],
                },
                "quantitativeReference": {"referenceToReferenceProcess": 1},
                "technology": {
                    "processes": {
                        "processInstance": {
                            "@dataSetInternalID": "1",
                            "@multiplicationFactor": "1",
                            "referenceToProcess": _global_reference(
                                process_id,
                                process_version,
                                base_name,
                                "process data set",
                                f"../processes/{process_id}_{process_version}.xml",
                            ),
                        }
                    }
                },
            },
            "modellingAndValidation": {
                "validation": {
                    "review": {
                        "common:referenceToNameOfReviewerAndInstitution": entering_ref,
                        "common:otherReviewDetails": [
                            {
                                "@xml:lang": "en",
                                "#text": (
                                    "Local deterministic lifecyclemodel shell; native schema, "
                                    "graph, QA, and dry-run validation required."
                                ),
                            }
                        ],
                    }
                },
                "complianceDeclarations": copy.deepcopy(compliance),
            },
            "administrativeInformation": {
                "common:commissionerAndGoal": copy.deepcopy(commissioner),
                "dataEntryBy": copy.deepcopy(data_entry),
                "publicationAndOwnership": publication,
            },
        }
    }
    return {
        "route_id": route_id,
        "model_id": model_id,
        "model_version": MODEL_VERSION,
        "process_id": process_id,
        "process_version": process_version,
        "route_row_sha256": row_sha256,
        "source_request_sha256": plan_route["request_sha256"],
        "reference_flow_id": facts["reference"]["flow_id"],
        "reference_flow_version": facts["reference"]["flow_version"],
        "reference_amount": facts["reference"]["mean_amount"],
        "reference_direction": facts["reference"]["direction"],
        "resulting_process_id": model_id,
        "resulting_process_materialized": False,
        "provenance": provenance,
        "payload": payload,
        "payload_sha256": _hash(payload),
    }


def compile_lifecyclemodel_shells(
    scenario_rows: bytes,
    scenario_binding: JsonObject,
    plan: JsonObject,
    plan_binding: JsonObject,
) -> JsonObject:
    """Render two deterministic one-Process lifecyclemodel shell payloads."""

    scenario_ref = _binding(scenario_binding, "scenario binding")
    if _hash_bytes(scenario_rows) != scenario_ref["sha256"]:
        raise ValueError("scenario rows do not match their binding")
    plan_ref = _binding(plan_binding, "Beat 30 plan binding")
    plan_routes = _validate_plan(plan)
    plan_sources = _required_object(plan.get("source_bindings"), "plan source bindings")
    planned_scenario = _binding(
        plan_sources.get("current_residual_scenarios"),
        "plan current residual scenarios",
    )
    if planned_scenario["sha256"] != scenario_ref["sha256"]:
        raise ValueError(
            "Beat 30 plan and current scenario/route-row hash conflict"
        )
    scope_id = _required_text(plan.get("scope_id"), "plan scope_id")
    revision = _required_text(plan.get("revision"), "plan revision")
    lines = [line for line in scenario_rows.splitlines(keepends=True) if line.strip()]
    if len(lines) != 2:
        raise ValueError("exactly two current Process rows are required")
    routes = []
    for line in lines:
        try:
            row = json.loads(line)
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("current Process rows must be valid JSONL") from error
        if not isinstance(row, dict):
            raise ValueError("current Process row must be an object")
        facts = extract_current_process_facts(row)
        route_id = str(facts["route_id"])
        plan_route = plan_routes[route_id]
        row_sha256 = _hash_bytes(line)
        if row_sha256 != plan_route.get("route_row_sha256"):
            raise ValueError(f"{route_id} route-row hash no longer matches Beat 30")
        reference = facts["reference"]
        expected = {
            "process_id": facts["process_id"],
            "process_version": facts["process_version"],
            "reference_flow_id": reference["flow_id"],
            "reference_flow_version": reference["flow_version"],
            "reference_amount": reference["mean_amount"],
            "reference_resulting_amount": reference["resulting_amount"],
            "reference_direction": reference["direction"],
            "scientific_use": reference["scientific_use"],
        }
        if any(plan_route.get(key) != value for key, value in expected.items()):
            raise ValueError(f"{route_id} Beat 30 Process/reference facts drifted")
        routes.append(
            _shell_for_process(
                row,
                facts,
                plan_route,
                plan_ref,
                scope_id,
                revision,
                row_sha256,
            )
        )
    routes.sort(key=lambda row: str(row["route_id"]))
    if {str(row["route_id"]) for row in routes} != EXPECTED_ROUTES:
        raise ValueError("both current routes are required")
    identity = {
        "contract": SHELL_CONTRACT,
        "scope_id": scope_id,
        "revision": revision,
        "scenario_binding": scenario_ref,
        "plan_binding": plan_ref,
        "models": [
            {
                "route_id": row["route_id"],
                "model_id": row["model_id"],
                "process_id": row["process_id"],
                "payload_sha256": row["payload_sha256"],
            }
            for row in routes
        ],
    }
    return {
        "schema_version": 1,
        "contract": SHELL_CONTRACT,
        "scope_id": scope_id,
        "revision": revision,
        "status": SHELL_DRAFT_STATUS,
        "shell_version": _hash(identity)[:16],
        "tool_identity": TIANGONG_PACKAGE,
        "source_bindings": {
            "current_residual_scenarios": scenario_ref,
            "native_route_assembly_plan": plan_ref,
        },
        "route_count": 2,
        "routes": routes,
        "preserved_blocked_check_count": 9,
        "preserved_scientific_gap_count": 5,
        "preserved_unresolved_field_count": plan.get(
            "preserved_unresolved_field_count"
        ),
        "preserved_unresolved_claim_count": plan.get(
            "preserved_unresolved_claim_count"
        ),
        "preserved_prohibited_claim_count": plan.get(
            "preserved_prohibited_claim_count"
        ),
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
    }


def _ids(rows: object, key: str) -> List[str]:
    return sorted(
        str(row.get(key))
        for row in rows
        if isinstance(rows, list) and isinstance(row, dict) and row.get(key)
    )


def verify_native_shell_gates(
    compiled: JsonObject,
    native: Mapping[str, object],
) -> JsonObject:
    """Fail closed unless native contract/schema/graph/QA/dry-run gates agree."""

    if (
        compiled.get("contract") != SHELL_CONTRACT
        or compiled.get("status") != SHELL_DRAFT_STATUS
        or compiled.get("route_count") != 2
        or compiled.get("tool_identity") != TIANGONG_PACKAGE
        or compiled.get("preserved_blocked_check_count") != 9
        or compiled.get("preserved_scientific_gap_count") != 5
    ):
        raise ValueError("compiled lifecyclemodel shells are required")
    routes = [
        row for row in _required_list(compiled.get("routes"), "compiled routes")
        if isinstance(row, dict)
    ]
    if len(routes) != 2:
        raise ValueError("two compiled lifecyclemodel routes are required")
    model_ids = sorted(str(row["model_id"]) for row in routes)
    contract = _required_object(native.get("contract_manifest"), "contract manifest")
    schema = _required_object(contract.get("schema"), "contract schema")
    if (
        contract.get("kind") != "lifecyclemodel"
        or contract.get("profile") != "default"
        or contract.get("sdk_package") != "@tiangong-lca/tidas-sdk"
        or schema.get("sha256") != LIFECYCLEMODEL_SCHEMA_SHA256
        or contract.get("methodology") is not None
        or contract.get("ruleset") is not None
    ):
        raise ValueError("pinned native lifecyclemodel contract drifted")
    validation = _required_object(
        native.get("validation_report"), "native validation report"
    )
    counts = _required_object(validation.get("counts"), "validation counts")
    validation_rows = _required_list(validation.get("rows"), "validation rows")
    if (
        validation.get("status") != "completed"
        or validation.get("requested_type") != "lifecyclemodel"
        or counts.get("total") != 2
        or counts.get("valid") != 2
        or counts.get("invalid") != 0
        or _ids(validation_rows, "id") != model_ids
        or any(
            not isinstance(row, dict)
            or row.get("type") != "lifecyclemodel"
            or row.get("status") != "valid"
            or row.get("issue_count") != 0
            for row in validation_rows
        )
    ):
        raise ValueError("2/2 native lifecyclemodel validation is required")
    graph = _required_object(native.get("graph_report"), "native graph report")
    graph_counts = _required_object(graph.get("counts"), "graph counts")
    graph_models = _required_list(graph.get("models"), "graph models")
    if (
        graph.get("status") != "completed"
        or graph.get("check_connections") is not True
        or graph_counts
        != {"models": 2, "nodes": 2, "edges": 0, "findings": 0}
        or _ids(graph_models, "id") != model_ids
        or any(
            not isinstance(row, dict)
            or row.get("node_count") != 1
            or row.get("edge_count") != 0
            or row.get("finding_count") != 0
            for row in graph_models
        )
    ):
        raise ValueError("two isolated one-node graphs with zero finding are required")
    qa = _required_object(native.get("qa_report"), "native QA report")
    severity = _required_object(qa.get("severity_counts"), "QA severity counts")
    summaries = _required_list(qa.get("model_summaries"), "QA model summaries")
    if (
        qa.get("status") != "completed_local_lifecyclemodel_qa"
        or qa.get("input_mode") != "rows_file"
        or qa.get("model_count") != 2
        or severity.get("error") != 0
        or len(summaries) != 2
        or sorted(
            str(model_id)
            for row in summaries
            if isinstance(row, dict)
            for model_id in row.get("model_uuids", [])
        )
        != model_ids
        or any(
            not isinstance(row, dict)
            or row.get("process_instance_count") != 1
            or row.get("zero_multiplication_factor_count") != 0
            or row.get("resulting_process_uuids") != row.get("model_uuids")
            for row in summaries
        )
    ):
        raise ValueError("native lifecyclemodel QA must retain two one-Process shells and zero error")
    save = _required_object(
        native.get("save_draft_summary"), "native save-draft summary"
    )
    save_counts = _required_object(save.get("counts"), "save-draft counts")
    save_rows = _required_list(save.get("lifecyclemodels"), "save-draft rows")
    if (
        save.get("commit") is not False
        or save.get("mode") != "dry_run"
        or save.get("status") != "completed"
        or save_counts
        != {"selected": 2, "prepared": 2, "executed": 0, "failed": 0}
        or _ids(save_rows, "id") != model_ids
        or any(
            not isinstance(row, dict)
            or row.get("status") != "prepared"
            or row.get("validation", {}).get("ok") is not True
            or row.get("validation", {}).get("issue_count") != 0
            for row in save_rows
        )
    ):
        raise ValueError("native lifecyclemodel save-draft dry-run must prepare 2/2")
    qa_findings = native.get("qa_findings")
    finding_rows = qa_findings if isinstance(qa_findings, list) else []
    warning_ids = sorted(
        str(row.get("rule_id"))
        for row in finding_rows
        if isinstance(row, dict) and row.get("severity") == "warning"
    )
    bindings = native.get("bindings")
    native_bindings = bindings if isinstance(bindings, dict) else {}
    compact_routes = [
        {
            "route_id": row["route_id"],
            "model_id": row["model_id"],
            "model_version": row["model_version"],
            "model_path": f"routes/{row['route_id']}/lifecyclemodel.json",
            "model_sha256": row["payload_sha256"],
            "process_id": row["process_id"],
            "process_version": row["process_version"],
            "route_row_sha256": row["route_row_sha256"],
            "reference_flow_id": row["reference_flow_id"],
            "reference_flow_version": row["reference_flow_version"],
            "reference_amount": row["reference_amount"],
            "resulting_process_id": row["resulting_process_id"],
            "resulting_process_materialized": False,
        }
        for row in routes
    ]
    return {
        "schema_version": 1,
        "contract": SHELL_CONTRACT,
        "scope_id": compiled["scope_id"],
        "revision": compiled["revision"],
        "status": "native_route_lifecyclemodel_shells_materialized",
        "shell_version": compiled["shell_version"],
        "tool_identity": TIANGONG_PACKAGE,
        "native_schema_sha256": LIFECYCLEMODEL_SCHEMA_SHA256,
        "source_bindings": compiled["source_bindings"],
        "native_artifacts": native_bindings,
        "route_count": 2,
        "routes": compact_routes,
        "native_valid_count": 2,
        "native_invalid_count": 0,
        "native_graph_model_count": 2,
        "native_graph_node_count": 2,
        "native_graph_edge_count": 0,
        "native_graph_finding_count": 0,
        "native_qa_model_count": 2,
        "native_qa_error_count": 0,
        "native_qa_warning_count": int(severity.get("warning", 0)),
        "native_qa_warning_ids": warning_ids,
        "native_dry_run_selected_count": 2,
        "native_dry_run_prepared_count": 2,
        "native_dry_run_executed_count": 0,
        "native_dry_run_failed_count": 0,
        "resulting_process_materialized_count": 0,
        "preserved_blocked_check_count": 9,
        "preserved_scientific_gap_count": 5,
        "preserved_unresolved_field_count": compiled.get(
            "preserved_unresolved_field_count"
        ),
        "preserved_unresolved_claim_count": compiled.get(
            "preserved_unresolved_claim_count"
        ),
        "preserved_prohibited_claim_count": compiled.get(
            "preserved_prohibited_claim_count"
        ),
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "network_read_executed_count": 0,
        "external_action_executed_count": 0,
        "remote_write_executed_count": 0,
        "model_mutation_executed_count": 0,
        "claim_transition_executed_count": 0,
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "routing": {
            "next_internal_route": "build-native-route-resulting-process-dry-run",
            "pause_internal_work": False,
            "reason": (
                "Both exact Process roots now have schema-valid local lifecyclemodel shells; "
                "exercise native resulting-Process projection without publish or comparison."
            ),
        },
        "next_action": (
            "Build and verify native resulting-Process dry-runs from both validated local "
            "lifecyclemodel shells; do not publish, compare, or calculate LCIA."
        ),
    }


def native_shell_commands(
    rows_path: Path,
    out_dir: Path,
    tiangong_package: str = TIANGONG_PACKAGE,
) -> List[List[str]]:
    """Return the five pinned, offline TianGong commands used by this beat."""

    prefix = [
        "npm",
        "exec",
        "--yes",
        "--offline",
        f"--package={tiangong_package}",
        "--",
        "tiangong-lca",
    ]
    return [
        [
            *prefix,
            "dataset",
            "contract",
            "get",
            "--type",
            "lifecyclemodel",
            "--profile",
            "default",
            "--out-dir",
            str(out_dir / "native-contract"),
            "--json",
        ],
        [
            *prefix,
            "dataset",
            "validate",
            "--input",
            str(rows_path),
            "--type",
            "lifecyclemodel",
            "--out-dir",
            str(out_dir / "native-validation"),
            "--json",
        ],
        [
            *prefix,
            "lifecyclemodel",
            "graph",
            "--input",
            str(rows_path),
            "--out-dir",
            str(out_dir / "native-graph"),
            "--format",
            "all",
            "--check-connections",
            "--json",
        ],
        [
            *prefix,
            "qa",
            "lifecyclemodel",
            "--rows-file",
            str(rows_path),
            "--out-dir",
            str(out_dir / "native-qa"),
            "--json",
        ],
        [
            *prefix,
            "lifecyclemodel",
            "save-draft",
            "--input",
            str(rows_path),
            "--out-dir",
            str(out_dir / "native-save-draft"),
            "--dry-run",
            "--json",
        ],
    ]


def _read_json(path: Path, label: str) -> JsonObject:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} is not valid JSON: {path}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object: {path}")
    return value


def _read_jsonl(path: Path, label: str) -> List[JsonObject]:
    rows: List[JsonObject] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise RuntimeError(f"{label} is not readable: {path}") from error
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"{label} contains invalid JSONL: {path}") from error
        if not isinstance(row, dict):
            raise RuntimeError(f"{label} row must be an object: {path}")
        rows.append(row)
    return rows


def _artifact_binding(path: Path, root: Path) -> JsonObject:
    content = path.read_bytes()
    return {
        "path": str(path.relative_to(root)),
        "sha256": _hash_bytes(content),
        "bytes": len(content),
    }


def _run_native(command: Sequence[str], cwd: Path) -> None:
    environment = dict(os.environ)
    environment["npm_config_offline"] = "true"
    result = subprocess.run(
        list(command),
        cwd=str(cwd),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Pinned TianGong command failed: "
            + " ".join(command)
            + f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def execute_native_shell_materialization(
    scenario_rows_path: Path,
    scenario_binding_path: Path,
    plan_path: Path,
    plan_binding_path: Path,
    out_dir: Path,
    tiangong_package: str = TIANGONG_PACKAGE,
) -> JsonObject:
    """Render shells, run all native local gates, and write the accepted report."""

    if tiangong_package != TIANGONG_PACKAGE:
        raise ValueError(f"tiangong package must remain pinned to {TIANGONG_PACKAGE}")
    scenario_bytes = scenario_rows_path.read_bytes()
    scenario_binding = _read_json(scenario_binding_path, "scenario binding")
    plan_bytes = plan_path.read_bytes()
    plan_binding = _read_json(plan_binding_path, "assembly-plan binding")
    if _hash_bytes(plan_bytes) != plan_binding.get("sha256"):
        raise ValueError("Beat 30 assembly plan does not match its binding")
    plan = _read_json(plan_path, "Beat 30 assembly plan")
    compiled = compile_lifecyclemodel_shells(
        scenario_bytes,
        scenario_binding,
        plan,
        plan_binding,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    output_dir = out_dir / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    payloads: List[JsonObject] = []
    manifest_routes: List[JsonObject] = []
    for route in compiled["routes"]:
        route_id = str(route["route_id"])
        model_path = out_dir / "routes" / route_id / "lifecyclemodel.json"
        model_path.parent.mkdir(parents=True, exist_ok=True)
        payload = _required_object(route.get("payload"), f"{route_id} payload")
        model_path.write_bytes(_json_bytes(payload))
        payloads.append(payload)
        manifest_routes.append(
            {
                key: copy.deepcopy(value)
                for key, value in route.items()
                if key != "payload"
            }
        )
    rows_path = output_dir / "lifecyclemodels.jsonl"
    rows_path.write_bytes(b"".join(_json_bytes(row) for row in payloads))
    shell_manifest = {
        key: copy.deepcopy(value)
        for key, value in compiled.items()
        if key != "routes"
    }
    shell_manifest["routes"] = manifest_routes
    shell_manifest_path = output_dir / "shell-manifest.json"
    shell_manifest_path.write_bytes(_json_bytes(shell_manifest))

    for command in native_shell_commands(rows_path, out_dir, tiangong_package):
        _run_native(command, out_dir)

    paths = {
        "contract_manifest": out_dir
        / "native-contract"
        / "outputs"
        / "contract-manifest.json",
        "contract_schema": out_dir / "native-contract" / "outputs" / "schema.json",
        "validation_report": out_dir
        / "native-validation"
        / "outputs"
        / "validation-report.json",
        "graph_report": out_dir / "native-graph" / "outputs" / "graph-report.json",
        "graph_findings": out_dir / "native-graph" / "outputs" / "findings.jsonl",
        "qa_report": out_dir / "native-qa" / "lifecyclemodel_qa_report.json",
        "qa_findings": out_dir / "native-qa" / "findings.jsonl",
        "save_draft_summary": out_dir
        / "native-save-draft"
        / "outputs"
        / "save-draft-bundle"
        / "summary.json",
        "save_draft_failures": out_dir
        / "native-save-draft"
        / "outputs"
        / "save-draft-bundle"
        / "failures.jsonl",
    }
    native: JsonObject = {
        "contract_manifest": _read_json(paths["contract_manifest"], "contract manifest"),
        "validation_report": _read_json(paths["validation_report"], "validation report"),
        "graph_report": _read_json(paths["graph_report"], "graph report"),
        "qa_report": _read_json(paths["qa_report"], "QA report"),
        "qa_findings": _read_jsonl(paths["qa_findings"], "QA findings"),
        "save_draft_summary": _read_json(
            paths["save_draft_summary"], "save-draft summary"
        ),
        "bindings": {
            key: _artifact_binding(path, out_dir) for key, path in paths.items()
        },
    }
    report = verify_native_shell_gates(compiled, native)
    report["shell_manifest"] = _artifact_binding(shell_manifest_path, out_dir)
    report["lifecyclemodel_rows"] = _artifact_binding(rows_path, out_dir)
    report_path = output_dir / "native-route-lifecyclemodel-shell-materialization.json"
    report_path.write_bytes(_json_bytes(report))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario-rows", type=Path, required=True)
    parser.add_argument("--scenario-binding", type=Path, required=True)
    parser.add_argument("--assembly-plan", type=Path, required=True)
    parser.add_argument("--assembly-plan-binding", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--tiangong-package", default=TIANGONG_PACKAGE)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = execute_native_shell_materialization(
        args.scenario_rows,
        args.scenario_binding,
        args.assembly_plan,
        args.assembly_plan_binding,
        args.out_dir,
        args.tiangong_package,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
