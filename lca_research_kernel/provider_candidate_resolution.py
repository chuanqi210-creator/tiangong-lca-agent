"""Resolve Provider Process candidates without authoring or connecting models."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple


JsonObject = Dict[str, object]
PROVIDER_CANDIDATE_RESOLUTION_CONTRACT = (
    "lca_research_kernel.native_provider_candidate_resolution/v1"
)
PROVIDER_CANDIDATE_RESOLUTION_LOGIC_VERSION = "2026-07-17.1"
GAP_CONTRACT = "lca_research_kernel.native_route_provider_network_gap_map/v1"
BATCH_CONTRACT = "lca_research_kernel.native_provider_resolution_batch/v1"
PACKAGE_CONTRACT = "lca_research_kernel.native_route_model_execution_package/v1"
REMOTE_CREDENTIAL_NAMES = (
    "TIANGONG_LCA_API_BASE_URL",
    "TIANGONG_LCA_API_KEY_OR_SUPABASE_PUBLISHABLE_KEY",
)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _jsonl_bytes(rows: Sequence[JsonObject]) -> bytes:
    return b"".join(_json_bytes(row) for row in rows)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def provider_preflight_version(requirement_id: str, request: Mapping[str, object]) -> str:
    """Return the one canonical directory identity for a native preflight."""

    return _sha256(
        _json_bytes({"requirement_id": requirement_id, "request": dict(request)})
    )[:16]


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
    rows: List[JsonObject] = []
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
    binding = _object(value, label)
    path = _text(binding.get("path"), f"{label} path")
    expected_hash = _text(binding.get("sha256"), f"{label} sha256")
    if expected_hash != _sha256(content):
        raise ValueError(f"{label} hash does not match content")
    if "bytes" in binding and binding.get("bytes") != len(content):
        raise ValueError(f"{label} byte count does not match content")
    return {"path": path, "sha256": expected_hash, "bytes": len(content)}


def _rows(value: object) -> List[JsonObject]:
    if isinstance(value, list):
        return [_object(row, "row") for row in value]
    if value is None:
        return []
    return [_object(value, "row")]


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


def _process_catalog(content: bytes) -> List[JsonObject]:
    catalog: List[JsonObject] = []
    seen = set()
    for row in _parse_jsonl(content, "candidate Process catalog"):
        root = _object(row.get("processDataSet"), "candidate Process dataset")
        information = _object(root.get("processInformation"), "Process information")
        dataset = _object(
            information.get("dataSetInformation"), "Process dataset information"
        )
        process_id = _text(dataset.get("common:UUID"), "Process UUID")
        administrative = _object(
            root.get("administrativeInformation"), "Process administrative information"
        )
        ownership = _object(
            administrative.get("publicationAndOwnership"),
            "Process publication and ownership",
        )
        version = _text(ownership.get("common:dataSetVersion"), "Process version")
        if (process_id, version) in seen:
            raise ValueError("candidate Process catalog contains a duplicate identity")
        seen.add((process_id, version))
        name = _first_text(_object(dataset.get("name"), "Process name").get("baseName"))
        if not name:
            raise ValueError(f"candidate Process {process_id} has no base name")
        exchange_root = _object(root.get("exchanges"), f"Process {process_id} exchanges")
        exchanges = []
        for exchange in _rows(exchange_root.get("exchange")):
            internal_id = _text(
                exchange.get("@dataSetInternalID"), "exchange internal ID"
            )
            direction = _text(exchange.get("exchangeDirection"), "exchange direction")
            if direction not in ("Input", "Output"):
                raise ValueError("candidate Process exchange direction is invalid")
            reference = _object(
                exchange.get("referenceToFlowDataSet"), "exchange Flow reference"
            )
            exchanges.append(
                {
                    "internal_id": internal_id,
                    "direction": direction,
                    "flow_id": _text(reference.get("@refObjectId"), "exchange Flow UUID"),
                    "flow_version": _text(reference.get("@version"), "exchange Flow version"),
                }
            )
        catalog.append(
            {
                "process_id": process_id,
                "version": version,
                "name": name,
                "exchanges": exchanges,
            }
        )
    return sorted(catalog, key=lambda row: (str(row["process_id"]), str(row["version"])))


def _native_observation(
    raw: Mapping[str, object], requirement: JsonObject, catalog: List[JsonObject]
) -> Tuple[JsonObject, List[JsonObject]]:
    requirement_id = _text(raw.get("requirement_id"), "native requirement ID")
    if requirement_id != requirement["requirement_id"]:
        raise ValueError("native observation requirement ID drifted")
    decision_content = raw.get("decision_bytes")
    candidates_content = raw.get("candidates_bytes")
    sources_content = raw.get("sources_bytes")
    if not all(isinstance(value, bytes) for value in (decision_content, candidates_content, sources_content)):
        raise ValueError("native observation artifacts must be bytes")
    decision_bytes = decision_content  # type: ignore[assignment]
    candidate_bytes = candidates_content  # type: ignore[assignment]
    source_bytes = sources_content  # type: ignore[assignment]
    report = _parse_json(decision_bytes, "native identity decision")
    candidates = _parse_jsonl(candidate_bytes, "native identity candidates")
    try:
        sources = _array(json.loads(source_bytes), "native candidate sources")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("native candidate sources are not valid JSON") from error
    if report.get("schema_version") != 1 or report.get("kind") != "process":
        raise ValueError("native identity report contract drifted")
    if report.get("candidates") != candidates or report.get("candidate_sources") != sources:
        raise ValueError("native candidate artifacts disagree with the decision report")
    target = _object(report.get("target"), "native target")
    fields = _object(target.get("fields"), "native target fields")
    expected_flow_id = requirement["flow_id"]
    expected_flow_name = requirement["flow_name"]
    if (
        fields.get("reference_flow_ids") != [expected_flow_id]
        or fields.get("reference_flow_names") != [expected_flow_name]
        or fields.get("provider_role")
        != "background_provider_for_bound_input_exchange"
        or target.get("schema_validation", {}).get("status") != "not_applicable"
    ):
        raise ValueError("native target does not match the accepted Provider requirement")
    if len(sources) != 1:
        raise ValueError("native local candidate source count drifted")
    source = _object(sources[0], "native local candidate source")
    if source.get("kind") != "file" or source.get("row_count") != len(catalog):
        raise ValueError("native local candidate source did not scan the bound catalog")
    catalog_by_id = {
        (row["process_id"], row["version"]): row for row in catalog
    }
    if len(candidates) != len(catalog):
        raise ValueError("native candidate count does not match the bound catalog")
    candidate_keys = []
    observations: List[JsonObject] = []
    for candidate in candidates:
        candidate_id = _text(candidate.get("id"), "native candidate Process UUID")
        candidate_version = _text(
            candidate.get("version"), "native candidate Process version"
        )
        key = (candidate_id, candidate_version)
        if key not in catalog_by_id or key in candidate_keys:
            raise ValueError("native candidate identity is absent or duplicated in catalog")
        candidate_keys.append(key)
        process = catalog_by_id[key]
        matching_outputs = [
            exchange
            for exchange in process["exchanges"]  # type: ignore[index]
            if exchange["direction"] == "Output"
            and exchange["flow_id"] == expected_flow_id
            and exchange["flow_version"] == requirement["flow_version"]
        ]
        is_consumer = candidate_id == requirement["consumer_process_id"]
        eligible = bool(matching_outputs) and not is_consumer
        if eligible:
            eligibility_status = "eligible_exact_output"
            reason = "candidate_has_exact_required_output_flow"
        elif is_consumer and matching_outputs:
            eligibility_status = "ineligible_consumer_self_provider"
            reason = "candidate_is_the_consumer_process"
        else:
            eligibility_status = "ineligible_no_exact_output"
            reason = "candidate_does_not_output_required_flow"
        native_identity = {
            "match_score": candidate.get("match_score"),
            "match_reasons": candidate.get("match_reasons", []),
            "decision_hint": candidate.get("decision_hint"),
        }
        observation_core = {
            "requirement_id": requirement_id,
            "route_id": requirement["route_id"],
            "consumer_process_id": requirement["consumer_process_id"],
            "required_flow_id": expected_flow_id,
            "required_flow_version": requirement["flow_version"],
            "candidate_process_id": candidate_id,
            "candidate_process_version": candidate_version,
            "candidate_process_name": process["name"],
            "native_identity": native_identity,
            "provider_eligibility": {
                "status": eligibility_status,
                "reason": reason,
                "exact_required_output_found": bool(matching_outputs),
                "candidate_is_consumer": is_consumer,
                "matching_output_exchange_ids": [
                    exchange["internal_id"] for exchange in matching_outputs
                ],
            },
        }
        observations.append(
            {
                "observation_id": "provider-candidate-observation:"
                + _sha256(_json_bytes(observation_core))[:16],
                **observation_core,
            }
        )
    if set(candidate_keys) != set(catalog_by_id):
        raise ValueError("native candidates do not cover the bound Process catalog")
    if report.get("decision") not in {
        "create_new",
        "manual_review",
        "block_duplicate",
        "reuse",
        "update_same_row",
        "version_bump",
    }:
        raise ValueError("native identity decision is unsupported")
    native_summary = {
        "requirement_id": requirement_id,
        "decision": report["decision"],
        "status": report.get("status"),
        "confidence": report.get("confidence"),
        "candidate_count": len(candidates),
        "decision_sha256": _sha256(decision_bytes),
        "candidates_sha256": _sha256(candidate_bytes),
        "sources_sha256": _sha256(source_bytes),
    }
    return native_summary, observations


def compile_provider_candidate_resolution(
    *,
    gap_report_bytes: bytes,
    gap_report_binding: Mapping[str, object],
    requirements_bytes: bytes,
    requirements_binding: Mapping[str, object],
    resolution_batch_bytes: bytes,
    resolution_batch_binding: Mapping[str, object],
    package_manifest_bytes: bytes,
    package_manifest_binding: Mapping[str, object],
    candidate_catalog_bytes: bytes,
    candidate_catalog_binding: Mapping[str, object],
    native_observations: Sequence[Mapping[str, object]],
) -> JsonObject:
    """Compile native identity observations into LCA Provider eligibility facts."""

    gap_binding = _binding(gap_report_binding, gap_report_bytes, "gap report binding")
    requirement_binding = _binding(
        requirements_binding, requirements_bytes, "Provider requirements binding"
    )
    batch_binding = _binding(
        resolution_batch_binding, resolution_batch_bytes, "resolution batch binding"
    )
    manifest_binding = _binding(
        package_manifest_binding, package_manifest_bytes, "package manifest binding"
    )
    catalog_binding = _binding(
        candidate_catalog_binding, candidate_catalog_bytes, "candidate catalog binding"
    )
    gap = _parse_json(gap_report_bytes, "Provider gap report")
    requirements = _parse_jsonl(requirements_bytes, "Provider requirements")
    batch = _parse_json(resolution_batch_bytes, "Provider resolution batch")
    manifest = _parse_json(package_manifest_bytes, "model package manifest")
    if (
        gap.get("contract") != GAP_CONTRACT
        or gap.get("status") != "native_route_provider_network_gap_map_compiled"
        or gap.get("provider_requirement_count") != 9
        or gap.get("provider_network_complete") is not False
    ):
        raise ValueError("Provider gap report is not the accepted nine-gap boundary")
    if (
        batch.get("contract") != BATCH_CONTRACT
        or batch.get("status") != "provider_resolution_batch_compiled"
        or batch.get("requirement_count") != 9
        or batch.get("model_mutation_allowed") is not False
        or batch.get("remote_write_allowed") is not False
    ):
        raise ValueError("Provider resolution batch contract drifted")
    if (
        manifest.get("contract") != PACKAGE_CONTRACT
        or manifest.get("status") != "native_route_model_execution_package_verified"
    ):
        raise ValueError("model package manifest contract drifted")
    manifest_files = _object(manifest.get("file_bindings"), "package file bindings")
    current_process_binding = _object(
        manifest_files.get("datasets/current-processes.jsonl"),
        "package current Process binding",
    )
    if catalog_binding != {
        "path": current_process_binding.get("path"),
        "sha256": current_process_binding.get("sha256"),
        "bytes": current_process_binding.get("bytes"),
    }:
        raise ValueError("candidate catalog binding does not match the package manifest")
    catalog = _process_catalog(candidate_catalog_bytes)
    if len(catalog) != 2:
        raise ValueError("current candidate Process catalog must contain exactly two rows")
    requirements_by_id = {}
    for requirement in requirements:
        requirement_id = _text(requirement.get("requirement_id"), "requirement ID")
        if requirement_id in requirements_by_id:
            raise ValueError("Provider requirement ID is duplicated")
        requirements_by_id[requirement_id] = requirement
    if len(requirements_by_id) != 9:
        raise ValueError("Provider requirements must contain exactly nine rows")
    batch_by_id = {}
    for raw_request in _array(batch.get("requests"), "resolution batch requests"):
        request = _object(raw_request, "resolution batch request")
        requirement_id = _text(request.get("requirement_id"), "batch requirement ID")
        if requirement_id in batch_by_id:
            raise ValueError("resolution batch requirement ID is duplicated")
        batch_by_id[requirement_id] = request
    if set(batch_by_id) != set(requirements_by_id):
        raise ValueError("resolution batch and Provider requirements disagree")
    for requirement_id, requirement in requirements_by_id.items():
        batch_request = batch_by_id[requirement_id]
        target = _object(
            _object(batch_request.get("request"), "batch native request").get("target"),
            "batch target",
        )
        acceptance = _object(batch_request.get("acceptance"), "batch acceptance")
        if (
            batch_request.get("route_id") != requirement.get("route_id")
            or target.get("reference_flow_id") != requirement.get("flow_id")
            or target.get("reference_flow_name") != requirement.get("flow_name")
            or target.get("provider_role")
            != "background_provider_for_bound_input_exchange"
            or acceptance.get("candidate_must_output_flow_id")
            != requirement.get("flow_id")
            or acceptance.get("candidate_must_output_flow_version")
            != requirement.get("flow_version")
            or acceptance.get("identity_similarity_alone_is_sufficient") is not False
            or acceptance.get("model_connection_mutation_allowed") is not False
        ):
            raise ValueError("resolution batch Flow requirement drifted")
    observation_by_id = {}
    for observation in native_observations:
        requirement_id = _text(observation.get("requirement_id"), "native requirement ID")
        if requirement_id in observation_by_id:
            raise ValueError("native observation requirement ID is duplicated")
        observation_by_id[requirement_id] = observation
    if set(observation_by_id) != set(requirements_by_id):
        raise ValueError("native observations do not cover the Provider requirements")

    native_summaries = []
    candidate_observations = []
    requirement_results = []
    source_routes = []
    unresolved = []
    for requirement_id in sorted(requirements_by_id):
        requirement = requirements_by_id[requirement_id]
        native, observations = _native_observation(
            observation_by_id[requirement_id], requirement, catalog
        )
        native_summaries.append(native)
        candidate_observations.extend(observations)
        eligible = [
            row
            for row in observations
            if row["provider_eligibility"]["status"] == "eligible_exact_output"  # type: ignore[index]
        ]
        local_status = (
            "bounded_no_hit"
            if not eligible
            else "one_eligible_candidate"
            if len(eligible) == 1
            else "ambiguous_multiple_eligible_candidates"
        )
        result = {
            "requirement_id": requirement_id,
            "route_id": requirement["route_id"],
            "consumer_process_id": requirement["consumer_process_id"],
            "exchange_internal_id": requirement["exchange_internal_id"],
            "required_flow_id": requirement["flow_id"],
            "required_flow_version": requirement["flow_version"],
            "required_flow_name": requirement["flow_name"],
            "native_decision": native["decision"],
            "native_confidence": native["confidence"],
            "local_candidate_count": len(observations),
            "eligible_candidate_count": len(eligible),
            "eligible_candidate_process_ids": [
                row["candidate_process_id"] for row in eligible
            ],
            "local_route_status": local_status,
            "remote_route_status": "credential_required_not_executed",
            "accepted_candidate_process_ids": [],
            "build_authorized": False,
            "connection_authorized": False,
            "unresolved": True,
        }
        requirement_results.append(result)
        unresolved.append(
            {
                "requirement_id": requirement_id,
                "route_id": requirement["route_id"],
                "consumer_process_id": requirement["consumer_process_id"],
                "exchange_internal_id": requirement["exchange_internal_id"],
                "flow_id": requirement["flow_id"],
                "flow_version": requirement["flow_version"],
                "flow_name": requirement["flow_name"],
                "local_route_status": local_status,
                "remote_route_status": "credential_required_not_executed",
                "next_evidence_contract": "provider_process_inventory_and_identity",
            }
        )
        source_routes.extend(
            [
                {
                    "requirement_id": requirement_id,
                    "source_route": "local_bound_process_catalog",
                    "status": local_status,
                    "executed": True,
                    "candidate_count": len(observations),
                    "eligible_candidate_count": len(eligible),
                    "catalog_path": catalog_binding["path"],
                    "catalog_sha256": catalog_binding["sha256"],
                    "global_absence_proven": False,
                },
                {
                    "requirement_id": requirement_id,
                    "source_route": "tiangong_public_hybrid_search",
                    "status": "credential_required_not_executed",
                    "executed": False,
                    "candidate_count": 0,
                    "required_configuration_names": list(REMOTE_CREDENTIAL_NAMES),
                    "secret_values_recorded": False,
                    "global_absence_proven": False,
                },
            ]
        )

    candidate_observations.sort(
        key=lambda row: (str(row["requirement_id"]), str(row["candidate_process_id"]))
    )
    source_routes.sort(key=lambda row: (str(row["requirement_id"]), str(row["source_route"])))
    unresolved.sort(key=lambda row: str(row["requirement_id"]))
    details = {
        "requirement_results": requirement_results,
        "candidate_observations": candidate_observations,
        "source_route_ledger": source_routes,
        "unresolved_requirements": unresolved,
    }
    detail_bytes = {
        "requirement_results": _jsonl_bytes(requirement_results),
        "candidate_observations": _jsonl_bytes(candidate_observations),
        "source_route_ledger": _jsonl_bytes(source_routes),
        "unresolved_requirements": _json_bytes(
            {
                "schema_version": 1,
                "contract": "lca_research_kernel.unresolved_provider_requirements/v1",
                "requirement_count": len(unresolved),
                "requirements": unresolved,
            }
        ),
    }
    artifact_names = {
        "requirement_results": "provider-requirement-resolution-results.jsonl",
        "candidate_observations": "provider-candidate-observations.jsonl",
        "source_route_ledger": "provider-candidate-source-routes.jsonl",
        "unresolved_requirements": "unresolved-provider-requirements.json",
    }
    artifact_bindings = {
        key: {
            "path": artifact_names[key],
            "sha256": _sha256(content),
            "bytes": len(content),
        }
        for key, content in detail_bytes.items()
    }
    identity = {
        "contract": PROVIDER_CANDIDATE_RESOLUTION_CONTRACT,
        "logic_version": PROVIDER_CANDIDATE_RESOLUTION_LOGIC_VERSION,
        "gap_sha256": gap_binding["sha256"],
        "requirements_sha256": requirement_binding["sha256"],
        "batch_sha256": batch_binding["sha256"],
        "package_sha256": manifest_binding["sha256"],
        "catalog_sha256": catalog_binding["sha256"],
        "native_summaries": native_summaries,
        "artifact_bindings": artifact_bindings,
    }
    resolution_version = _sha256(_json_bytes(identity))[:16]
    eligible_count = sum(
        row["provider_eligibility"]["status"] == "eligible_exact_output"  # type: ignore[index]
        for row in candidate_observations
    )
    report = {
        "schema_version": 1,
        "contract": PROVIDER_CANDIDATE_RESOLUTION_CONTRACT,
        "logic_version": PROVIDER_CANDIDATE_RESOLUTION_LOGIC_VERSION,
        "status": "native_provider_candidate_resolution_compiled",
        "scope_id": gap.get("scope_id"),
        "revision": gap.get("revision"),
        "resolution_version": resolution_version,
        "requirement_count": len(requirements),
        "native_local_preflight_count": len(native_summaries),
        "local_catalog_process_count": len(catalog),
        "local_candidate_evaluation_count": len(candidate_observations),
        "native_create_new_decision_count": sum(
            row["decision"] == "create_new" for row in native_summaries
        ),
        "exact_required_output_match_count": eligible_count,
        "eligible_provider_candidate_count": eligible_count,
        "accepted_provider_candidate_count": 0,
        "local_bounded_no_hit_count": sum(
            row["local_route_status"] == "bounded_no_hit"
            for row in requirement_results
        ),
        "remote_route_credential_required_count": len(requirements),
        "remote_search_executed_count": 0,
        "remote_candidate_count": 0,
        "unresolved_requirement_count": len(unresolved),
        "global_provider_absence_proven": False,
        "native_create_new_authorizes_build": False,
        "model_connection_executed_count": 0,
        "model_mutation_executed_count": 0,
        "remote_write_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "artifact_bindings": artifact_bindings,
        "input_bindings": {
            "gap_report": gap_binding,
            "provider_requirements": requirement_binding,
            "resolution_batch": batch_binding,
            "package_manifest": manifest_binding,
            "local_candidate_catalog": catalog_binding,
        },
        "routing": {
            "next_internal_route": "compile-provider-process-evidence-requirements",
            "pause_internal_work": False,
            "reason": (
                "The bounded local foreground catalog supplies none of the nine input Flows, "
                "while the TianGong public route requires credentials and was not executed."
            ),
        },
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "next_action": (
            "Compile evidence and identity requirements for nine unresolved background "
            "Provider Processes without fabricating inventories or connecting models."
        ),
        **details,
    }
    return report


def materialize_provider_candidate_resolution(
    result: Mapping[str, object], out_dir: Path
) -> Dict[str, Path]:
    """Write the compact report and detailed deterministic resolution ledgers."""

    out_dir.mkdir(parents=True, exist_ok=True)
    detail_keys = (
        "requirement_results",
        "candidate_observations",
        "source_route_ledger",
        "unresolved_requirements",
    )
    requirement_results = [
        _object(row, "requirement result")
        for row in _array(result.get("requirement_results"), "requirement results")
    ]
    candidate_observations = [
        _object(row, "candidate observation")
        for row in _array(result.get("candidate_observations"), "candidate observations")
    ]
    source_routes = [
        _object(row, "source route")
        for row in _array(result.get("source_route_ledger"), "source route ledger")
    ]
    unresolved = [
        _object(row, "unresolved requirement")
        for row in _array(result.get("unresolved_requirements"), "unresolved requirements")
    ]
    contents = {
        "requirement_results": _jsonl_bytes(requirement_results),
        "candidate_observations": _jsonl_bytes(candidate_observations),
        "source_route_ledger": _jsonl_bytes(source_routes),
        "unresolved_requirements": _json_bytes(
            {
                "schema_version": 1,
                "contract": "lca_research_kernel.unresolved_provider_requirements/v1",
                "requirement_count": len(unresolved),
                "requirements": unresolved,
            }
        ),
    }
    bindings = _object(result.get("artifact_bindings"), "artifact bindings")
    paths = {}
    for key, content in contents.items():
        binding = _object(bindings.get(key), f"{key} binding")
        if binding.get("sha256") != _sha256(content) or binding.get("bytes") != len(content):
            raise ValueError(f"{key} artifact binding drifted")
        path = out_dir / _text(binding.get("path"), f"{key} path")
        path.write_bytes(content)
        paths[key] = path
    compact = {key: value for key, value in result.items() if key not in detail_keys}
    report_path = out_dir / "native-provider-candidate-resolution.json"
    report_path.write_bytes(_json_bytes(compact))
    return {"report": report_path, **paths}


def run_native_provider_preflights(
    node_path: Path,
    cli_entry_path: Path,
    resolution_batch_path: Path,
    candidate_catalog_path: Path,
    out_dir: Path,
) -> List[JsonObject]:
    """Run one local-only native Process identity preflight per requirement."""

    node = node_path.resolve()
    cli = cli_entry_path.resolve()
    batch_path = resolution_batch_path.resolve()
    catalog = candidate_catalog_path.resolve()
    if not node.is_file() or not cli.is_file():
        raise ValueError("copied Node and TianGong CLI entry must be files")
    if not batch_path.is_file() or not catalog.is_file():
        raise ValueError("resolution batch and local candidate catalog must be files")
    batch = _parse_json(batch_path.read_bytes(), "Provider resolution batch")
    requests = [
        _object(row, "Provider resolution request")
        for row in _array(batch.get("requests"), "Provider resolution requests")
    ]
    if batch.get("contract") != BATCH_CONTRACT or len(requests) != 9:
        raise ValueError("Provider resolution batch cannot drive nine native preflights")
    observations: List[JsonObject] = []
    for index, row in enumerate(requests, start=1):
        requirement_id = _text(row.get("requirement_id"), "Provider requirement ID")
        request = _object(row.get("request"), "native identity request")
        version = provider_preflight_version(requirement_id, request)
        root = out_dir.resolve() / f"{index:02d}-{version}"
        input_path = root / "inputs/process-identity-preflight.json"
        input_path.parent.mkdir(parents=True, exist_ok=True)
        input_path.write_bytes(_json_bytes(request))
        command = [
            str(node),
            str(cli),
            "process",
            "identity-preflight",
            "--input",
            str(input_path),
            "--candidate-input",
            str(catalog),
            "--out-dir",
            str(root),
            "--json",
        ]
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        decision_path = root / "outputs/identity-decision.json"
        candidates_path = root / "outputs/identity-candidates.jsonl"
        sources_path = root / "outputs/identity-candidate-sources.json"
        if not all(path.is_file() for path in (decision_path, candidates_path, sources_path)):
            raise RuntimeError(
                f"native identity preflight did not produce required artifacts: {requirement_id}"
            )
        observations.append(
            {
                "requirement_id": requirement_id,
                "request_path": str(input_path),
                "decision_path": str(decision_path),
                "candidates_path": str(candidates_path),
                "sources_path": str(sources_path),
                "decision_bytes": decision_path.read_bytes(),
                "candidates_bytes": candidates_path.read_bytes(),
                "sources_bytes": sources_path.read_bytes(),
                "stdout_sha256": _sha256(completed.stdout.encode("utf-8")),
                "stderr_sha256": _sha256(completed.stderr.encode("utf-8")),
            }
        )
    return observations


def run_provider_candidate_resolution(
    *,
    gap_report_path: Path,
    gap_report_binding_path: Path,
    requirements_path: Path,
    requirements_binding_path: Path,
    resolution_batch_path: Path,
    resolution_batch_binding_path: Path,
    package_manifest_path: Path,
    package_manifest_binding_path: Path,
    candidate_catalog_path: Path,
    candidate_catalog_binding_path: Path,
    node_path: Path,
    cli_entry_path: Path,
    out_dir: Path,
) -> JsonObject:
    """Run native local reconciliation and materialize the aggregate result."""

    observations = run_native_provider_preflights(
        node_path,
        cli_entry_path,
        resolution_batch_path,
        candidate_catalog_path,
        out_dir / "native-preflights",
    )
    result = compile_provider_candidate_resolution(
        gap_report_bytes=gap_report_path.read_bytes(),
        gap_report_binding=_parse_json(
            gap_report_binding_path.read_bytes(), "gap report binding"
        ),
        requirements_bytes=requirements_path.read_bytes(),
        requirements_binding=_parse_json(
            requirements_binding_path.read_bytes(), "Provider requirements binding"
        ),
        resolution_batch_bytes=resolution_batch_path.read_bytes(),
        resolution_batch_binding=_parse_json(
            resolution_batch_binding_path.read_bytes(), "resolution batch binding"
        ),
        package_manifest_bytes=package_manifest_path.read_bytes(),
        package_manifest_binding=_parse_json(
            package_manifest_binding_path.read_bytes(), "package manifest binding"
        ),
        candidate_catalog_bytes=candidate_catalog_path.read_bytes(),
        candidate_catalog_binding=_parse_json(
            candidate_catalog_binding_path.read_bytes(), "candidate catalog binding"
        ),
        native_observations=observations,
    )
    materialize_provider_candidate_resolution(result, out_dir / "outputs")
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Resolve bound local Provider candidates through native TianGong preflight."
    )
    parser.add_argument("--gap-report", type=Path, required=True)
    parser.add_argument("--gap-binding", type=Path, required=True)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--requirements-binding", type=Path, required=True)
    parser.add_argument("--resolution-batch", type=Path, required=True)
    parser.add_argument("--batch-binding", type=Path, required=True)
    parser.add_argument("--package-manifest", type=Path, required=True)
    parser.add_argument("--manifest-binding", type=Path, required=True)
    parser.add_argument("--candidate-catalog", type=Path, required=True)
    parser.add_argument("--candidate-catalog-binding", type=Path, required=True)
    parser.add_argument("--native-node", type=Path, required=True)
    parser.add_argument("--native-cli-entry", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    result = run_provider_candidate_resolution(
        gap_report_path=args.gap_report,
        gap_report_binding_path=args.gap_binding,
        requirements_path=args.requirements,
        requirements_binding_path=args.requirements_binding,
        resolution_batch_path=args.resolution_batch,
        resolution_batch_binding_path=args.batch_binding,
        package_manifest_path=args.package_manifest,
        package_manifest_binding_path=args.manifest_binding,
        candidate_catalog_path=args.candidate_catalog,
        candidate_catalog_binding_path=args.candidate_catalog_binding,
        node_path=args.native_node,
        cli_entry_path=args.native_cli_entry,
        out_dir=args.out_dir,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
