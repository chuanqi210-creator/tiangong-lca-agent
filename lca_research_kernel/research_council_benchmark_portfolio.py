"""Pure compiler for the terminal TianGong-first dual-track benchmark portfolio."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .research_council_positive_benchmark import (
    _walk_security,
    compile_positive_benchmark_qualification,
)
from .research_council_benchmark_assets import (
    _compile_workspace_negative_assets,
    compile_update_monitor,
)


JsonObject = Dict[str, object]
PORTFOLIO_CONTRACT = (
    "lca_research_kernel.research_council_benchmark_portfolio/v1"
)
FREEZE_PROPOSAL_CONTRACT = (
    "lca_research_kernel.research_council_benchmark_freeze_proposal/v1"
)
INPUT_CONTRACT = (
    "lca_research_kernel.research_council_benchmark_portfolio_input/v1"
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_POLICY = {
    "contract": "lca_research_kernel.research_council_benchmark_portfolio_policy/v1",
    "asset_roles": [
        "negative_authority_stress_case",
        "upstream_change_monitor_subject",
        "harness_canary",
        "positive_benchmark_candidate",
        "positive_benchmark_cluster",
        "immutable_freeze_proposal",
    ],
    "discovery_model_cap": 10000,
    "positive_hard_gates": [
        "published_open_data_exact_version",
        "tidas_schema_valid",
        "provider_closure_complete",
        "functional_unit_explicit",
        "system_boundary_explicit",
        "native_result_exact",
        "read_only_replay_complete",
        "comparison_basis_shared",
        "scientific_dispute_real",
        "not_harness_canary",
    ],
    "terminal_outcomes": [
        "benchmark_portfolio_ready",
        "no_positive_benchmark_currently_available",
    ],
    "admission_criteria_lowering_allowed": False,
    "external_authority_substitution_allowed": False,
    "remote_write_allowed": False,
}
_CONTROLLER_BASELINE = {
    "git_commit": "22d0576ad1a3f25ceef05ec5940fb02a848a27e9",
    "controller_sha256": "5e2530288e699283d9bff8d83988b975cb5d6c6b7090226c960725b279a8db43",
    "skill_sha256": "32e83edb63104079f87807fcce0ab6213c5f631c8204a1d1df387c8a784b2cca",
    "state_contract_sha256": "695e1b4b55fbcb5078989a7bb9bed4ca8403b07af8530e48942cdbd939059fc9",
}
_BASELINE_SOURCE_HASHES = {
    "controller_contract": _CONTROLLER_BASELINE["controller_sha256"],
    "controller_skill": _CONTROLLER_BASELINE["skill_sha256"],
    "controller_state_contract": _CONTROLLER_BASELINE["state_contract_sha256"],
}
_SOURCE_ROLES = {
    "negative_assets",
    "update_monitor",
    "freshwater_canary",
    "surface_capture",
    "positive_qualification",
    "policy",
    *_BASELINE_SOURCE_HASHES,
}
_ZERO_EFFECTS = {
    "remote_write_executed_count": 0,
    "shared_write_executed_count": 0,
    "calculation_trigger_executed_count": 0,
    "publication_executed_count": 0,
    "review_submission_executed_count": 0,
    "contact_executed_count": 0,
    "payment_executed_count": 0,
    "role_administration_executed_count": 0,
    "external_message_executed_count": 0,
    "unofficial_tiangong_correction_count": 0,
    "unsupported_strong_public_claim_count": 0,
    "secret_persistence_count": 0,
}
_EXPECTED_NEGATIVE_ID = "084faf1efb293292"
_EXPECTED_NEGATIVE_SHA = (
    "084faf1efb293292f6c03dbcf841c2260df94f73c905f777e5f7218325a3b549"
)
_EXPECTED_AUDITS = {
    "1b93c55f0206b9ff": "1b93c55f0206b9ff87d3757b49ddac7128e2d980dc601561fc35efffe3d8cdb3",
    "be7268aaf5dc0569": "be7268aaf5dc0569f6e100e0b5dc73fb124ce0895ee7987d42ec81b523faf582",
}
_EXPECTED_CANARY_ID = "284d5292284da5bf"
_EXPECTED_CANARY_SHA = (
    "284d5292284da5bf59629b2f8db2d85bb50dd2056e12e3e8ad1f3c5f0920aab8"
)
_REAL_SOURCE_PATHS = {
    "freshwater_canary": (
        "outputs/research-council-benchmark/portfolio/freshwater-canary.json"
    ),
    "surface_capture": (
        "outputs/research-council-benchmark/portfolio/discovery/captures/"
        "20260721T045131Z-state-100/surface-capture.json"
    ),
    "positive_qualification": (
        "outputs/research-council-benchmark/portfolio/positive-qualification/"
        "20260721T-task4-9423d274/qualification-v5/positive-qualification.json"
    ),
    "policy": "cases/research-council-benchmark-portfolio-policy.json",
}
_BASELINE_GIT_PATHS = {
    "controller_contract": "agent/lca-project-controller.md",
    "controller_skill": "skills/run-lca-research-project/SKILL.md",
    "controller_state_contract": (
        "skills/run-lca-research-project/references/state-contract.md"
    ),
}
RUNTIME_ROLE_PATHS = {
    "benchmark_portfolio_compiler": (
        "lca_research_kernel/research_council_benchmark_portfolio.py"
    ),
    "benchmark_portfolio_workflow": (
        "lca_research_kernel/research_council_benchmark_portfolio_workflow.py"
    ),
    "research_council_event_validator": (
        "lca_research_kernel/research_council_workflow.py"
    ),
    "project_reducer": "lca_research_kernel/project.py",
    "positive_benchmark_compiler": (
        "lca_research_kernel/research_council_positive_benchmark.py"
    ),
    "negative_monitor_canary_compiler": (
        "lca_research_kernel/research_council_benchmark_assets.py"
    ),
    "tiangong_authority_verifier": "lca_research_kernel/tiangong_authority.py",
    "tiangong_authority_transition_verifier": (
        "lca_research_kernel/tiangong_authority_transition.py"
    ),
    "tiangong_capability_adoption_verifier": (
        "lca_research_kernel/tiangong_capability_adoption.py"
    ),
    "tiangong_native_case_verifier": "lca_research_kernel/tiangong_native_case.py",
    "tiangong_second_case_verifier": (
        "lca_research_kernel/tiangong_second_case_package.py"
    ),
}


def canonical_json_bytes(value: object) -> bytes:
    """Canonical bytes used by every Task-5 content identity."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def pretty_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _mapping(value: object, label: str) -> JsonObject:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be a JSON object")
    return copy.deepcopy(dict(value))


def _finish(body: JsonObject, prefix: str) -> JsonObject:
    digest = _digest(body)
    return {
        **body,
        f"{prefix}_content_sha256": digest,
        f"{prefix}_id": digest[:16],
    }


def _validate_content_identity(value: Mapping[str, object], prefix: str) -> None:
    digest_key = f"{prefix}_content_sha256"
    id_key = f"{prefix}_id"
    if not {digest_key, id_key}.issubset(value):
        raise ValueError(f"{prefix} content identity is missing")
    body = {key: child for key, child in value.items() if key not in {digest_key, id_key}}
    digest = _digest(body)
    if value.get(digest_key) != digest or value.get(id_key) != digest[:16]:
        raise ValueError(f"{prefix} content identity does not match its content")


def _safe_relative(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError(f"{label} must be a safe relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or any(
        part in {"", "."} for part in relative.parts
    ):
        raise ValueError(f"{label} must be a safe relative path")
    return relative


def absolute_nofollow_path(
    value: Path, *, label: str, allow_missing_tail: bool
) -> Path:
    """Return a lexical absolute path after lstat-checking every existing component."""

    absolute = Path(os.path.abspath(os.fspath(value)))
    cursor = Path(absolute.anchor)
    missing = False
    for part in absolute.parts[1:]:
        cursor = cursor / part
        if missing:
            continue
        try:
            status = os.lstat(cursor)
        except FileNotFoundError:
            if not allow_missing_tail:
                raise ValueError(f"{label} is missing: {cursor}")
            missing = True
            continue
        if stat.S_ISLNK(status.st_mode):
            raise ValueError(f"{label} may not contain a symlink: {cursor}")
    return absolute


def _runtime_closure() -> List[JsonObject]:
    closure = []
    for role, relative_text in sorted(RUNTIME_ROLE_PATHS.items()):
        relative = _safe_relative(relative_text, f"runtime source {role}")
        path = absolute_nofollow_path(
            _REPO_ROOT / relative,
            label=f"runtime source {role}",
            allow_missing_tail=False,
        )
        status = os.lstat(path)
        if not stat.S_ISREG(status.st_mode):
            raise ValueError(f"runtime source {role} must be a regular file")
        content = path.read_bytes()
        closure.append(
            {
                "role": role,
                "path": relative.as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
        )
    return closure


def _read_descriptor(
    descriptor: object,
    *,
    root: Path,
    expected_role: Optional[str] = None,
    parse_json: bool = True,
) -> Tuple[object, JsonObject]:
    ref = _mapping(descriptor, "source descriptor")
    if set(ref) != {"role", "path", "sha256", "bytes"}:
        raise ValueError("source descriptor must contain role/path/sha256/bytes exactly")
    if expected_role is not None and ref.get("role") != expected_role:
        raise ValueError(f"source descriptor role must be {expected_role}")
    relative = _safe_relative(ref.get("path"), "source descriptor path")
    source_root = absolute_nofollow_path(
        Path(root), label="source descriptor root", allow_missing_tail=False
    )
    path = source_root / relative
    cursor = source_root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("source descriptor path may not contain a symlink")
    try:
        checked = absolute_nofollow_path(
            path, label="source descriptor path", allow_missing_tail=False
        )
        checked.relative_to(source_root)
        if not stat.S_ISREG(os.lstat(checked).st_mode):
            raise ValueError("source descriptor must reopen a regular file")
        content = checked.read_bytes()
    except (OSError, ValueError) as error:
        raise ValueError(f"source descriptor cannot reopen {relative.as_posix()}") from error
    byte_count = ref.get("bytes")
    source_sha = ref.get("sha256")
    if (
        isinstance(byte_count, bool)
        or not isinstance(byte_count, int)
        or byte_count < 1
        or not isinstance(source_sha, str)
        or _SHA256.fullmatch(source_sha) is None
        or byte_count != len(content)
        or source_sha != hashlib.sha256(content).hexdigest()
    ):
        raise ValueError(f"source descriptor bytes drifted: {relative.as_posix()}")
    normalized = {
        "role": ref["role"],
        "path": relative.as_posix(),
        "sha256": source_sha,
        "bytes": byte_count,
    }
    if not parse_json:
        return content, normalized
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"source descriptor is not UTF-8 JSON: {relative.as_posix()}") from error
    return value, normalized


def _open_authority_refs(
    refs: object, *, authority_root: Path, expected_count: int, label: str
) -> List[Tuple[JsonObject, object]]:
    if isinstance(refs, Mapping):
        raw_refs = list(refs.values())
    elif isinstance(refs, list):
        raw_refs = list(refs)
    else:
        raise ValueError(f"{label} source refs are malformed")
    if len(raw_refs) != expected_count:
        raise ValueError(f"{label} must bind exactly {expected_count} source refs")
    reopened: List[Tuple[JsonObject, object]] = []
    seen = set()
    for raw_ref in raw_refs:
        value, ref = _read_descriptor(raw_ref, root=authority_root)
        identity = (ref["role"], ref["path"], ref["sha256"], ref["bytes"])
        if identity in seen:
            raise ValueError(f"{label} contains a duplicate source ref")
        seen.add(identity)
        _walk_security(value)
        reopened.append((ref, value))
    return reopened


def _require_zero_effects(value: object, label: str) -> None:
    if value != _ZERO_EFFECTS:
        raise ValueError(f"{label} must bind all twelve prohibited effects to integer zero")


def _validate_negative(
    negative: Mapping[str, object], monitor: Mapping[str, object], authority_root: Path
) -> None:
    _validate_content_identity(negative, "negative_assets")
    if (
        negative.get("contract") != "lca_research_kernel.negative_benchmark_assets/v1"
        or negative.get("negative_assets_id") != _EXPECTED_NEGATIVE_ID
        or negative.get("negative_assets_content_sha256") != _EXPECTED_NEGATIVE_SHA
        or negative.get("qualification_id") != "8c842560d748ce92"
        or negative.get("candidate_count") != 53
        or negative.get("cluster_counts")
        != {"copper_routes": 6, "truck_fuel_routes": 45, "wastewater_routes": 2}
        or negative.get("qualification_execution_status") != "completed"
        or negative.get("candidate_admission_status") != "rejected"
        or negative.get("positive_benchmark_member_count") != 0
        or negative.get("formal_benchmark_eligible") is not False
    ):
        raise ValueError("negative benchmark assets are not the accepted immutable 53")
    audits = negative.get("provider_audits")
    if not isinstance(audits, list) or len(audits) != 2:
        raise ValueError("negative assets must retain both Provider audits")
    if {
        row.get("audit_id"): row.get("audit_content_sha256")
        for row in audits
        if isinstance(row, Mapping)
    } != _EXPECTED_AUDITS:
        raise ValueError("negative assets Provider audit identities drifted")
    for field, role in (
        ("stress_cases", "negative_authority_stress_case"),
        ("monitor_subjects", "upstream_change_monitor_subject"),
    ):
        rows = negative.get(field)
        if not isinstance(rows, list) or len(rows) != 53:
            raise ValueError(f"negative assets {field} must retain 53 rows")
        identities = set()
        for row in rows:
            if (
                not isinstance(row, Mapping)
                or row.get("asset_role") != role
                or row.get("candidate_admission_status") != "rejected"
                or row.get("formal_benchmark_eligible") is not False
            ):
                raise ValueError(f"negative assets {field} role/admission drifted")
            candidate = row.get("candidate_ref")
            if not isinstance(candidate, Mapping):
                raise ValueError(f"negative assets {field} candidate ref is malformed")
            identities.add((candidate.get("cluster_id"), candidate.get("process_id"), candidate.get("process_version")))
        if len(identities) != 53:
            raise ValueError(f"negative assets {field} identities are not unique")
    reopened = _open_authority_refs(
        negative.get("source_refs"),
        authority_root=authority_root,
        expected_count=3,
        label="negative assets",
    )
    by_role = {ref["role"]: value for ref, value in reopened}
    qualification = by_role.get("qualification_ledger")
    audit_v1 = by_role.get("provider_closure_audit_v1")
    audit_v2 = by_role.get("provider_closure_audit_v2")
    if (
        not isinstance(qualification, Mapping)
        or qualification.get("qualification_id") != "8c842560d748ce92"
        or not isinstance(audit_v1, Mapping)
        or audit_v1.get("audit_id") != "1b93c55f0206b9ff"
        or not isinstance(audit_v2, Mapping)
        or audit_v2.get("audit_id") != "be7268aaf5dc0569"
        or audit_v1.get("candidate_count") != 53
        or audit_v2.get("candidate_count") != 53
    ):
        raise ValueError("negative source refs do not reopen both exact audits and ledger")
    _validate_content_identity(monitor, "monitor")
    if (
        monitor.get("contract") != "lca_research_kernel.benchmark_update_monitor/v1"
        or monitor.get("negative_assets_id") != _EXPECTED_NEGATIVE_ID
        or monitor.get("baseline_monitor_fingerprint") != negative.get("monitor_fingerprint")
        or monitor.get("monitor_fingerprint") != negative.get("monitor_fingerprint")
        or monitor.get("change_status") != "no_content_change"
        or monitor.get("change_kinds") != []
        or monitor.get("next_route") != "skip-full-requalification-until-content-change"
        or monitor.get("current_observation") != negative.get("monitor_observation")
    ):
        raise ValueError("update monitor does not bind the immutable no-change baseline")


def _validate_canary(canary: Mapping[str, object], authority_root: Path) -> None:
    _validate_content_identity(canary, "canary")
    if (
        canary.get("contract") != "lca_research_kernel.freshwater_harness_canary/v1"
        or canary.get("canary_id") != _EXPECTED_CANARY_ID
        or canary.get("canary_content_sha256") != _EXPECTED_CANARY_SHA
        or canary.get("asset_role") != "harness_canary"
        or canary.get("candidate_admission_status") != "not_applicable"
        or canary.get("formal_benchmark_eligible") is not False
        or canary.get("harness_valid") is not True
        or canary.get("provider_node_count") != 64
        or canary.get("provider_edge_count") != 63
        or canary.get("native_lcia_category_count") != 25
        or canary.get("functional_unit") != {"amount": "1.0", "unit": "m3"}
    ):
        raise ValueError("Freshwater Harness canary failed or was promoted")
    reopened = _open_authority_refs(
        canary.get("source_refs"),
        authority_root=authority_root,
        expected_count=4,
        label="Freshwater canary",
    )
    identities = {
        "freshwater_selection": ("selection_id", "f42cfa88d09f7f5f"),
        "freshwater_authority_receipt": ("receipt_id", "50d206a2899a86b1"),
        "freshwater_native_binding": ("binding_id", "aed71a598fbbf7ba"),
        "freshwater_clean_acceptance": ("acceptance_id", "5e426c8a392a576a"),
    }
    for ref, value in reopened:
        expected = identities.get(ref["role"])
        if expected is None or not isinstance(value, Mapping) or value.get(expected[0]) != expected[1]:
            raise ValueError("Freshwater canary source identity drifted")


def _validate_surface(surface: Mapping[str, object]) -> None:
    _validate_content_identity(surface, "surface_capture")
    state = surface.get("surface")
    models = surface.get("models")
    if (
        surface.get("contract")
        != "lca_research_kernel.tiangong_lifecyclemodel_surface_capture/v1"
        or not isinstance(state, Mapping)
        or state.get("data_source") != "tg"
        or state.get("table") != "lifecyclemodels"
        or state.get("state_code") != 100
        or state.get("model_cap") != 10000
        or state.get("pagination_complete") is not True
        or state.get("server_total_count") != state.get("captured_exact_version_count")
        or not isinstance(models, list)
        or len(models) != state.get("captured_exact_version_count")
    ):
        raise ValueError("LifecycleModel surface is partial or its authority contract drifted")
    runtime = surface.get("runtime")
    frontend = surface.get("frontend")
    if (
        not isinstance(runtime, Mapping)
        or runtime.get("cli_package") != "@tiangong-lca/cli"
        or runtime.get("cli_version") != "0.0.29"
        or runtime.get("adoption_id") != "4069fd724039d86c"
        or runtime.get("session_cache_disabled") is not True
        or not isinstance(frontend, Mapping)
        or frontend.get("surface_semantics_verified") is not True
    ):
        raise ValueError("LifecycleModel frontend/runtime authority drifted")
    _require_zero_effects(surface.get("effects"), "surface effects")


def _validate_qualification(
    qualification: Mapping[str, object],
    surface: Mapping[str, object],
    policy: Mapping[str, object],
    authority_root: Path,
) -> Tuple[List[JsonObject], JsonObject]:
    _validate_content_identity(qualification, "qualification")
    if (
        qualification.get("contract")
        != "lca_research_kernel.positive_benchmark_qualification/v1"
        or qualification.get("qualification_execution_status") != "completed"
        or qualification.get("positive_discovery_status") != "exhausted"
        or qualification.get("next_route") != "compile-benchmark-portfolio"
    ):
        raise ValueError("positive qualification is incomplete or non-terminal")
    _require_zero_effects(qualification.get("effects"), "qualification effects")
    surface_summary = qualification.get("surface")
    if (
        not isinstance(surface_summary, Mapping)
        or surface_summary.get("surface_capture_id") != surface.get("surface_capture_id")
        or surface_summary.get("surface_capture_content_sha256")
        != surface.get("surface_capture_content_sha256")
        or surface_summary.get("pagination_complete") is not True
        or surface_summary.get("model_cap") != 10000
        or surface_summary.get("server_total_count")
        != surface_summary.get("captured_exact_version_count")
    ):
        raise ValueError("positive qualification does not bind the exhausted surface")
    candidates = qualification.get("candidate_ledger")
    if (
        not isinstance(candidates, list)
        or qualification.get("candidate_count") != len(candidates)
        or len(candidates) != surface_summary.get("candidate_count")
    ):
        raise ValueError("positive qualification candidate ledger is incomplete")
    gate_names = list(policy["positive_hard_gates"])
    candidate_by_ref = {}
    for row in candidates:
        if not isinstance(row, Mapping) or row.get("asset_role") != "positive_benchmark_candidate":
            raise ValueError("positive qualification candidate role is invalid")
        ref = row.get("model_exact_ref")
        gates = row.get("hard_gates")
        if not isinstance(ref, str) or ref in candidate_by_ref or not isinstance(gates, Mapping) or set(gates) != set(gate_names):
            raise ValueError("positive qualification candidate identity/gate ledger is invalid")
        admitted = row.get("candidate_admission_status") == "admitted"
        passed = all(
            isinstance(gates[name], Mapping)
            and gates[name].get("status") == "pass"
            and gates[name].get("passed") is True
            for name in gate_names
        )
        if admitted != passed:
            raise ValueError("positive admission must equal all ten noncompensable gates")
        if not admitted and not row.get("failed_gates"):
            raise ValueError("rejected positive candidate needs a traceable failed gate")
        candidate_by_ref[ref] = row
    clusters = qualification.get("admitted_clusters")
    if not isinstance(clusters, list) or qualification.get("admitted_cluster_count") != len(clusters):
        raise ValueError("positive admitted-cluster count is inconsistent")
    normalized_clusters = []
    for raw in clusters:
        cluster = _mapping(raw, "admitted cluster")
        members = cluster.get("model_exact_refs")
        if (
            cluster.get("asset_role") != "positive_benchmark_cluster"
            or cluster.get("candidate_admission_status") != "admitted"
            or not isinstance(members, list)
            or len(members) < 2
            or len(set(members)) != len(members)
            or any(ref not in candidate_by_ref or candidate_by_ref[ref].get("candidate_admission_status") != "admitted" for ref in members)
            or cluster.get("comparison_basis_status", "pass") != "pass"
            or cluster.get("scientific_dispute_status", "pass") != "pass"
        ):
            raise ValueError("positive cluster does not retain two all-gate-passing routes")
        normalized_clusters.append(cluster)
    reopened = _open_authority_refs(
        qualification.get("source_refs"),
        authority_root=authority_root,
        expected_count=7,
        label="positive qualification",
    )
    by_role = {ref["role"]: value for ref, value in reopened}
    if by_role.get("surface_capture") != surface or by_role.get("policy") != policy:
        raise ValueError("positive qualification source refs do not bind surface and policy")
    try:
        recompiled = compile_positive_benchmark_qualification(
            surface_capture=by_role["surface_capture"],
            native_validation=by_role["native_validation"],
            process_evidence=by_role["process_evidence"],
            flow_evidence=by_role["flow_evidence"],
            graph_evidence=by_role["graph_evidence"],
            result_evidence=by_role["result_evidence"],
            policy=by_role["policy"],
            source_refs=qualification["source_refs"],
        )
    except KeyError as error:
        raise ValueError("positive qualification source roles are incomplete") from error
    if recompiled != qualification:
        raise ValueError(
            "positive qualification differs from deterministic replay of its raw evidence"
        )
    graph_rows = {
        row.get("model_exact_ref"): row
        for row in by_role["graph_evidence"].get("records", [])
        if isinstance(row, Mapping)
    }
    enriched_clusters = []
    for cluster in normalized_clusters:
        provider_node_count = 0
        for member in cluster["model_exact_refs"]:
            graph = graph_rows.get(member)
            report = graph.get("native_graph_report") if isinstance(graph, Mapping) else None
            nodes = report.get("nodes") if isinstance(report, Mapping) else None
            if not isinstance(nodes, list) or not nodes:
                raise ValueError(
                    "admitted cluster lacks a complete source-bound Provider snapshot"
                )
            provider_node_count += len(nodes)
        enriched = copy.deepcopy(cluster)
        enriched["observable_competing_interpretation_count"] = int(
            cluster.get("distinct_route_count", 1)
        )
        enriched["provider_graph_node_count"] = provider_node_count
        enriched_clusters.append(enriched)
    return enriched_clusters, by_role


def _rank_cluster(cluster: Mapping[str, object]) -> Tuple[object, ...]:
    members = sorted(str(value) for value in cluster["model_exact_refs"])
    member_count = cluster.get("member_count", len(members))
    dispute_count = cluster.get(
        "observable_competing_interpretation_count",
        cluster.get("distinct_route_count", 1),
    )
    provider_size = cluster.get("provider_graph_node_count", 2**63 - 1)
    for value, label in (
        (member_count, "member_count"),
        (dispute_count, "observable competition count"),
        (provider_size, "Provider graph node count"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"freeze ranking {label} must be a positive integer")
    key = cluster.get("comparability_key")
    if not isinstance(key, str) or not key:
        raise ValueError("freeze ranking comparability_key is required")
    return (-member_count, -dispute_count, provider_size, key, tuple(members))


def _compile_freeze_proposal(
    cluster: Mapping[str, object],
    qualification: Mapping[str, object],
    surface: Mapping[str, object],
    controller: Mapping[str, object],
    evidence: Mapping[str, object],
) -> JsonObject:
    rank = _rank_cluster(cluster)
    candidates = {
        row.get("model_exact_ref"): row
        for row in qualification["candidate_ledger"]
        if isinstance(row, Mapping)
    }

    def records(name: str) -> Dict[str, Mapping[str, object]]:
        source = evidence.get(name)
        rows = source.get("records") if isinstance(source, Mapping) else None
        if not isinstance(rows, list):
            raise ValueError(f"freeze evidence {name} records are missing")
        return {
            row.get("model_exact_ref"): row
            for row in rows
            if isinstance(row, Mapping)
        }

    flow_rows = records("flow_evidence")
    graph_rows = records("graph_evidence")
    result_rows = records("result_evidence")
    exact_members = []
    for model_ref in sorted(str(value) for value in cluster["model_exact_refs"]):
        candidate = candidates.get(model_ref)
        flow = flow_rows.get(model_ref)
        graph = graph_rows.get(model_ref)
        result = result_rows.get(model_ref)
        if not all(isinstance(value, Mapping) for value in (candidate, flow, graph, result)):
            raise ValueError("freeze proposal cannot reopen every admitted member evidence")
        provider_snapshot = graph.get("native_graph_report")
        native_result = result.get("native_result_payload")
        replay = result.get("replay_report")
        if not all(
            isinstance(value, Mapping)
            for value in (provider_snapshot, native_result, replay)
        ):
            raise ValueError("freeze proposal member evidence is incomplete")
        exact_members.append(
            {
                "model_exact_ref": model_ref,
                "root_process_exact_ref": candidate.get("root_process_exact_ref"),
                "reference_flow": {
                    "exact_ref": flow.get("reference_flow_exact_ref"),
                    "state_code": flow.get("state_code"),
                    "unit_evidence": copy.deepcopy(flow.get("unit_evidence")),
                },
                "functional_unit": copy.deepcopy(candidate.get("functional_unit")),
                "system_boundary": copy.deepcopy(candidate.get("system_boundary")),
                "provider_snapshot": copy.deepcopy(dict(provider_snapshot)),
                "native_result": copy.deepcopy(dict(native_result)),
                "read_only_replay": copy.deepcopy(dict(replay)),
            }
        )
    body: JsonObject = {
        "contract": FREEZE_PROPOSAL_CONTRACT,
        "asset_role": "immutable_freeze_proposal",
        "selected_cluster": copy.deepcopy(dict(cluster)),
        "exact_members": exact_members,
        "selection_ranking": {
            "all_hard_gates_passed": True,
            "member_count": -rank[0],
            "observable_competing_interpretation_count": -rank[1],
            "provider_graph_node_count": rank[2],
            "comparability_key": rank[3],
            "model_exact_refs": list(rank[4]),
        },
        "positive_qualification": {
            "qualification_id": qualification["qualification_id"],
            "qualification_content_sha256": qualification["qualification_content_sha256"],
        },
        "surface_capture": {
            "surface_capture_id": surface["surface_capture_id"],
            "surface_capture_content_sha256": surface["surface_capture_content_sha256"],
        },
        "controller_baseline": copy.deepcopy(dict(controller)),
        "task_template": (
            "在冻结的 TianGong 精确模型、Process、Flow、Provider、方法、原生结果和"
            "共同功能单位边界内，对真实竞争路线执行独立、可复现的科研判断评价。"
        ),
        "allowed_tools": [
            "tiangong_cli_read@0.0.29",
            "official_result_api_read",
        ],
        "allowed_effects": ["read_only", "isolated_local_write"],
        "prohibited_effects": copy.deepcopy(_ZERO_EFFECTS),
        "council_ab_executed": False,
        "council_promoted": False,
    }
    return _finish(body, "freeze_proposal")


def compile_benchmark_portfolio(
    *, input_manifest_path: Path, authority_root: Optional[Path] = None
) -> JsonObject:
    """Reopen every declared source and derive exactly one Goal-A terminal candidate."""

    manifest_path = absolute_nofollow_path(
        Path(input_manifest_path),
        label="portfolio input manifest",
        allow_missing_tail=False,
    )
    if not stat.S_ISREG(os.lstat(manifest_path).st_mode):
        raise ValueError("portfolio input manifest must be a real local file")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("portfolio input manifest must be UTF-8 JSON") from error
    manifest = _mapping(manifest, "portfolio input manifest")
    if set(manifest) != {"contract", "sources", "controller_baseline", "effects"} or manifest.get("contract") != INPUT_CONTRACT:
        raise ValueError("portfolio input manifest has an invalid contract shape")
    _walk_security(manifest)
    _require_zero_effects(manifest.get("effects"), "portfolio input effects")
    controller = _mapping(manifest.get("controller_baseline"), "controller baseline")
    if controller != _CONTROLLER_BASELINE:
        raise ValueError("formal single Controller baseline drifted")
    sources = _mapping(manifest.get("sources"), "portfolio input sources")
    if set(sources) != _SOURCE_ROLES:
        raise ValueError("portfolio input sources are incomplete or contain extras")
    values: JsonObject = {}
    normalized_sources: JsonObject = {}
    for key in sorted(sources):
        parse_json = key not in _BASELINE_SOURCE_HASHES
        value, ref = _read_descriptor(
            sources[key],
            root=manifest_path.parent,
            expected_role=key,
            parse_json=parse_json,
        )
        if key in _BASELINE_SOURCE_HASHES:
            if ref["sha256"] != _BASELINE_SOURCE_HASHES[key]:
                raise ValueError("frozen Controller source bytes drifted")
        else:
            _walk_security(value)
            values[key] = _mapping(value, key)
        normalized_sources[key] = ref
    policy = values["policy"]
    if policy != _EXPECTED_POLICY:
        raise ValueError("benchmark portfolio policy was lowered or drifted")
    authority = _REPO_ROOT if authority_root is None else Path(authority_root).resolve()
    negative = values["negative_assets"]
    monitor = values["update_monitor"]
    canary = values["freshwater_canary"]
    surface = values["surface_capture"]
    qualification = values["positive_qualification"]
    _validate_negative(negative, monitor, authority)
    _validate_canary(canary, authority)
    _validate_surface(surface)
    clusters, qualification_evidence = _validate_qualification(
        qualification, surface, policy, authority
    )
    freeze = None
    if clusters:
        selected = min(clusters, key=_rank_cluster)
        freeze = _compile_freeze_proposal(
            selected,
            qualification,
            surface,
            controller,
            qualification_evidence,
        )
        status = "benchmark_portfolio_ready"
        disposition = "ready_for_separate_evaluation_goal"
        next_route = "create-separate-research-council-evaluation-goal"
    else:
        status = "no_positive_benchmark_currently_available"
        disposition = "insufficient_evidence_no_promotion"
        next_route = "requalify-benchmark-portfolio-on-tiangong-content-change"
    manifest_content = manifest_path.read_bytes()
    runtime_closure = _runtime_closure()
    compiler_runtime = next(
        row
        for row in runtime_closure
        if row["role"] == "benchmark_portfolio_compiler"
    )
    body: JsonObject = {
        "contract": PORTFOLIO_CONTRACT,
        "compiler_runtime": copy.deepcopy(compiler_runtime),
        "runtime_closure": runtime_closure,
        "portfolio_status": status,
        "council_disposition": disposition,
        "negative_track": {
            "negative_assets_id": negative["negative_assets_id"],
            "candidate_count": 53,
            "cluster_counts": copy.deepcopy(negative["cluster_counts"]),
            "provider_audit_ids": ["1b93c55f0206b9ff", "be7268aaf5dc0569"],
            "positive_benchmark_member_count": 0,
        },
        "update_monitor": {
            "monitor_id": monitor["monitor_id"],
            "monitor_fingerprint": monitor["monitor_fingerprint"],
            "change_status": monitor["change_status"],
        },
        "harness_canary": {
            "canary_id": canary["canary_id"],
            "harness_valid": True,
            "formal_benchmark_eligible": False,
        },
        "positive_discovery": {
            "surface_capture_id": surface["surface_capture_id"],
            "surface_capture_content_sha256": surface["surface_capture_content_sha256"],
            "positive_discovery_status": qualification["positive_discovery_status"],
            "qualification_execution_status": qualification["qualification_execution_status"],
            "qualification_id": qualification["qualification_id"],
            "candidate_count": qualification["candidate_count"],
            "admitted_cluster_count": len(clusters),
        },
        "admitted_clusters": copy.deepcopy(clusters),
        "freeze_proposal": freeze,
        "controller_baseline": copy.deepcopy(controller),
        "source_manifest": {
            "path": manifest_path.name,
            "sha256": hashlib.sha256(manifest_content).hexdigest(),
            "bytes": len(manifest_content),
        },
        "source_refs": normalized_sources,
        "effects": copy.deepcopy(_ZERO_EFFECTS),
        "council_ab_executed": False,
        "council_promoted": False,
        "next_route": next_route,
    }
    return _finish(body, "portfolio")


def _secure_mkdirs(path: Path) -> Path:
    absolute = absolute_nofollow_path(
        path, label="portfolio output parent", allow_missing_tail=True
    )
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor = cursor / part
        try:
            status = os.lstat(cursor)
        except FileNotFoundError:
            try:
                os.mkdir(cursor, 0o700)
            except FileExistsError:
                status = os.lstat(cursor)
            else:
                status = os.lstat(cursor)
                os.chmod(cursor, 0o700)
        if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
            raise ValueError(f"portfolio output parent is unsafe: {cursor}")
    return absolute


def materialize_benchmark_portfolio_input(*, output_dir: Path) -> Path:
    """Materialize accepted inputs through an exclusive no-follow directory claim."""

    target = absolute_nofollow_path(
        Path(output_dir),
        label="portfolio input output",
        allow_missing_tail=True,
    )
    try:
        policy = json.loads(
            (_REPO_ROOT / _REAL_SOURCE_PATHS["policy"]).read_text(encoding="utf-8")
        )
        canary = json.loads(
            (_REPO_ROOT / _REAL_SOURCE_PATHS["freshwater_canary"]).read_text(
                encoding="utf-8"
            )
        )
        surface = json.loads(
            (_REPO_ROOT / _REAL_SOURCE_PATHS["surface_capture"]).read_text(
                encoding="utf-8"
            )
        )
        qualification = json.loads(
            (_REPO_ROOT / _REAL_SOURCE_PATHS["positive_qualification"]).read_text(
                encoding="utf-8"
            )
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("accepted Task-5 input sources cannot be reopened") from error
    negative = _compile_workspace_negative_assets(policy)
    monitor = compile_update_monitor(
        negative_assets=negative,
        current_observation=negative["monitor_observation"],
        policy=policy,
    )
    payloads = {
        "negative_assets": negative,
        "update_monitor": monitor,
        "freshwater_canary": canary,
        "surface_capture": surface,
        "positive_qualification": qualification,
        "policy": policy,
    }
    sources: JsonObject = {}
    files: Dict[Path, bytes] = {}
    for key, value in sorted(payloads.items()):
        relative = Path("inputs") / f"{key}.json"
        content = pretty_json_bytes(value)
        files[relative] = content
        sources[key] = {
            "role": key,
            "path": relative.as_posix(),
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }
    for key, git_path in sorted(_BASELINE_GIT_PATHS.items()):
        process = subprocess.run(
            ["git", "show", f"{_CONTROLLER_BASELINE['git_commit']}:{git_path}"],
            cwd=_REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if process.returncode != 0:
            raise ValueError("frozen Controller Git source cannot be reopened")
        content = process.stdout
        if hashlib.sha256(content).hexdigest() != _BASELINE_SOURCE_HASHES[key]:
            raise ValueError("frozen Controller Git source bytes drifted")
        relative = Path("inputs") / f"{key}.md"
        files[relative] = content
        sources[key] = {
            "role": key,
            "path": relative.as_posix(),
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }
    manifest = {
        "contract": INPUT_CONTRACT,
        "sources": sources,
        "controller_baseline": copy.deepcopy(_CONTROLLER_BASELINE),
        "effects": copy.deepcopy(_ZERO_EFFECTS),
    }
    files[Path("input-manifest.json")] = pretty_json_bytes(manifest)
    _secure_mkdirs(target.parent)
    try:
        os.mkdir(target, 0o700)
    except FileExistsError as error:
        raise FileExistsError(
            f"refusing to overwrite portfolio input: {target}"
        ) from error
    os.chmod(target, 0o700)
    try:
        os.mkdir(target / "inputs", 0o700)
        os.chmod(target / "inputs", 0o700)
        for relative, content in sorted(files.items()):
            descriptor = os.open(
                target / relative,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target / "input-manifest.json"


__all__ = [
    "PORTFOLIO_CONTRACT",
    "FREEZE_PROPOSAL_CONTRACT",
    "INPUT_CONTRACT",
    "RUNTIME_ROLE_PATHS",
    "absolute_nofollow_path",
    "canonical_json_bytes",
    "pretty_json_bytes",
    "compile_benchmark_portfolio",
    "materialize_benchmark_portfolio_input",
]
