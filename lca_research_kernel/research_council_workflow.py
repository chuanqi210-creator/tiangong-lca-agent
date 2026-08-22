"""Project orchestration for the isolated Research Council experiment."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

from .research_council_benchmark import (
    compile_benchmark_freeze,
    compile_benchmark_qualification,
)
from .research_council_portfolio import advance_portfolio, compile_portfolio
from .research_council_evaluation import (
    compile_architecture_decision,
    compile_blind_review,
    compile_quality_time_comparison,
    compile_run_trace,
)


JsonObject = Dict[str, object]
_ROOT = Path("research-council-benchmark")
_EVENT_PREFIX = "research.research_council."
_ZERO_PROHIBITED_EFFECTS: JsonObject = {
    "remote_write_executed_count": 0,
    "shared_write_executed_count": 0,
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
_BENCHMARK_PORTFOLIO_EVENT_TYPES = {
    "research.research_council.benchmark_assets_registered",
    "research.research_council.benchmark_discovery_captured",
    "research.research_council.benchmark_portfolio_decided",
}
_BENCHMARK_PORTFOLIO_ZERO_EFFECTS: JsonObject = {
    **_ZERO_PROHIBITED_EFFECTS,
    "calculation_trigger_executed_count": 0,
}


class ProjectPort(Protocol):
    """Narrow Project surface required by this workflow."""

    workspace: Path

    def events(self) -> List[JsonObject]: ...

    def record(
        self,
        event_type: str,
        payload: JsonObject,
        *,
        actor: str = "ai",
        idempotency_key: Optional[str] = None,
    ) -> JsonObject: ...

    def materialize_views(self) -> JsonObject: ...

    def clean_research_council_projection_bytes(
        self,
        events: Sequence[JsonObject],
        artifacts: Mapping[str, bytes],
    ) -> Dict[str, bytes]: ...


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _read_json(path: Path, label: str) -> JsonObject:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} is missing: {resolved}")
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} must be a UTF-8 JSON object") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _physical_evidence_path(
    project: ProjectPort, observation_path: Path, relative_text: object
) -> Path:
    if not isinstance(relative_text, str):
        raise ValueError("physical evidence path is malformed")
    relative = Path(relative_text)
    if relative.is_absolute() or ".." in relative.parts or "\\" in relative_text:
        raise ValueError("physical evidence path must be workspace-relative")
    observation = Path(observation_path).resolve()
    repository_roots = [
        parent
        for parent in (observation.parent, *observation.parents)
        if (parent / ".git").exists()
    ]
    if repository_roots:
        candidate = repository_roots[0] / relative
        if candidate.is_file() and not candidate.is_symlink():
            return candidate.resolve()
    roots = [Path.cwd().resolve(), project.workspace.resolve()]
    roots.extend(observation.parents)
    roots.extend(project.workspace.resolve().parents)
    matches = []
    for root in roots:
        candidate = root / relative
        if candidate.is_file() and not candidate.is_symlink():
            resolved = candidate.resolve()
            if resolved not in matches:
                matches.append(resolved)
    if len(matches) != 1:
        raise ValueError(
            f"physical evidence must resolve to exactly one immutable file: {relative_text}"
        )
    return matches[0]


def _reopen_physical_evidence(
    project: ProjectPort,
    observation_path: Path,
    descriptor: object,
    *,
    visited: set,
) -> Optional[Mapping[str, object]]:
    if not isinstance(descriptor, Mapping):
        raise ValueError("physical evidence descriptor is malformed")
    required = {"role", "path", "sha256", "bytes"}
    if not required.issubset(descriptor) or set(descriptor) - required not in (
        set(),
        {"candidate_count"},
    ):
        raise ValueError("physical evidence descriptor has an invalid shape")
    path = _physical_evidence_path(project, observation_path, descriptor["path"])
    content = path.read_bytes()
    if (
        descriptor["bytes"] != len(content)
        or descriptor["sha256"] != hashlib.sha256(content).hexdigest()
    ):
        raise ValueError(f"physical evidence bytes or SHA-256 drifted: {descriptor['path']}")
    if path in visited:
        return None
    visited.add(path)
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, Mapping) or "sources" not in value:
        return value if isinstance(value, Mapping) else None
    sources = value["sources"]
    if not isinstance(sources, list):
        raise ValueError(f"physical evidence sources are malformed: {descriptor['path']}")
    for nested in sources:
        _reopen_physical_evidence(
            project, observation_path, nested, visited=visited
        )
    return value


def _reopen_benchmark_evidence(
    project: ProjectPort, observation_path: Path, observation: Mapping[str, object]
) -> None:
    descriptors = [observation.get("candidate_catalog_ref")]
    authority = observation.get("authority_evidence")
    if not isinstance(authority, Mapping):
        raise ValueError("physical evidence authority block is malformed")
    descriptors.append(authority.get("artifact_ref"))
    clusters = observation.get("cluster_specs")
    if not isinstance(clusters, list):
        raise ValueError("physical evidence cluster specs are malformed")
    for cluster in clusters:
        if not isinstance(cluster, Mapping):
            raise ValueError("physical evidence cluster spec is malformed")
        descriptors.append(cluster.get("evidence_ref"))
    visited = set()
    for cluster, descriptor in zip(clusters, descriptors[2:]):
        manifest = _reopen_physical_evidence(
            project, observation_path, descriptor, visited=visited
        )
        body = {key: value for key, value in cluster.items() if key != "evidence_ref"}
        cluster_id = cluster.get("cluster_id")
        candidates = observation.get("candidates")
        if not isinstance(candidates, list):
            raise ValueError("physical candidate evidence is malformed")
        expected_candidate_rows = []
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                raise ValueError("physical candidate evidence is malformed")
            if candidate.get("cluster_id") != cluster_id:
                continue
            row = {
                key: candidate[key]
                for key in (
                    "process_id",
                    "process_version",
                    "screening_status",
                    "screening_reasons",
                    "current_read_status",
                    "terminal_blocker",
                )
                if key in candidate
            }
            expected_candidate_rows.append(row)
        expected_candidate_rows.sort(key=_canonical_digest)
        physical_candidate_rows = (
            manifest.get("candidate_rows") if isinstance(manifest, Mapping) else None
        )
        if isinstance(physical_candidate_rows, list):
            physical_candidate_rows = sorted(
                physical_candidate_rows, key=_canonical_digest
            )
        if (
            not isinstance(manifest, Mapping)
            or manifest.get("contract")
            != "lca_research_kernel.research_council_cluster_evidence_manifest/v2"
            or set(manifest)
            != {
                "candidate_count",
                "candidate_rows",
                "cluster_id",
                "cluster_spec_sha256",
                "contract",
                "sources",
            }
            or manifest.get("cluster_id") != cluster_id
            or manifest.get("cluster_spec_sha256") != _canonical_digest(body)
            or manifest.get("candidate_count") != len(expected_candidate_rows)
            or physical_candidate_rows != expected_candidate_rows
        ):
            raise ValueError("cluster evidence manifest does not bind its exact spec")
    _reopen_physical_evidence(project, observation_path, descriptors[0], visited=visited)
    authority_manifest = _reopen_physical_evidence(
        project, observation_path, descriptors[1], visited=visited
    )
    physical_bindings = (
        authority_manifest.get("bindings")
        if isinstance(authority_manifest, Mapping)
        else None
    )
    observed_bindings = authority.get("bindings")
    if (
        not isinstance(physical_bindings, list)
        or not isinstance(observed_bindings, list)
        or sorted(physical_bindings, key=_canonical_digest)
        != sorted(observed_bindings, key=_canonical_digest)
    ):
        raise ValueError(
            "physical authority bindings must correspond one-to-one with the observation"
        )


def _relative_artifact_path(value: object) -> Path:
    if not isinstance(value, (str, Path)):
        raise ValueError("Research Council artifact path is malformed")
    text = str(value)
    relative = Path(text)
    if (
        "\\" in text
        or "\x00" in text
        or relative.is_absolute()
        or ".." in relative.parts
        or relative.parts[:1] != _ROOT.parts
        or any(part in {"", "."} for part in relative.parts)
    ):
        raise ValueError(
            "Research Council artifacts must use the isolated canonical namespace"
        )
    return relative


def _council_root(project: ProjectPort, *, create: bool) -> Path:
    workspace = project.workspace.resolve()
    root = workspace / _ROOT
    if root.is_symlink():
        raise RuntimeError("Research Council artifact root must not be a symlink")
    if not root.exists():
        if not create:
            raise RuntimeError("Research Council artifact root is missing")
        root.mkdir()
    if root.is_symlink() or not root.is_dir() or root.resolve() != root:
        raise RuntimeError("Research Council artifact root is not a real local directory")
    return root


def _secure_artifact_path(
    project: ProjectPort, relative_value: object, *, create_parents: bool
) -> Path:
    relative = _relative_artifact_path(relative_value)
    root = _council_root(project, create=create_parents)
    current = root
    for component in relative.parts[1:-1]:
        current = current / component
        if current.is_symlink():
            raise RuntimeError("Research Council artifact path contains a symlink")
        if not current.exists():
            if not create_parents:
                raise RuntimeError("registered Research Council artifact parent is missing")
            current.mkdir()
        if current.is_symlink() or not current.is_dir():
            raise RuntimeError("Research Council artifact parent is not a real directory")
    destination = current / relative.name
    if destination.is_symlink():
        raise RuntimeError("Research Council artifact file must not be a symlink")
    if destination.exists() and not destination.is_file():
        raise RuntimeError("Research Council artifact path is not a regular file")
    try:
        destination.parent.resolve().relative_to(root)
    except ValueError as error:
        raise RuntimeError("Research Council artifact leaves its physical root") from error
    return destination


def _artifact_descriptor(relative: Path, value: object) -> JsonObject:
    normalized = _relative_artifact_path(relative)
    content = _json_bytes(value)
    return {
        "path": normalized.as_posix(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


def _artifact(project: ProjectPort, relative: Path, value: object) -> JsonObject:
    destination = _secure_artifact_path(
        project, relative, create_parents=True
    )
    content = _json_bytes(value)
    if destination.exists() and destination.read_bytes() != content:
        raise RuntimeError(f"content-addressed Research Council artifact drifted: {relative}")
    if not destination.exists():
        destination.write_bytes(content)
    return _artifact_descriptor(relative, value)


def _verify_artifact(project: ProjectPort, descriptor: object) -> Path:
    if not isinstance(descriptor, dict) or set(descriptor) != {"path", "sha256", "bytes"}:
        raise RuntimeError("registered Research Council artifact descriptor is malformed")
    try:
        relative = _relative_artifact_path(descriptor["path"])
        path = _secure_artifact_path(project, relative, create_parents=False)
    except ValueError as error:
        raise RuntimeError("registered Research Council artifact leaves its namespace") from error
    if not path.is_file():
        raise RuntimeError(
            f"registered Research Council artifact is missing: {relative.as_posix()}"
        )
    content = path.read_bytes()
    if (
        isinstance(descriptor["bytes"], bool)
        or not isinstance(descriptor["bytes"], int)
        or len(content) != descriptor["bytes"]
        or not isinstance(descriptor["sha256"], str)
        or hashlib.sha256(content).hexdigest() != descriptor["sha256"]
    ):
        raise RuntimeError(
            f"registered Research Council artifact drifted: {relative.as_posix()}"
        )
    return path


def _latest_artifact(
    project: ProjectPort, event_type: str
) -> tuple[JsonObject, JsonObject]:
    for event in reversed(project.events()):
        if event.get("event_type") != event_type:
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            raise RuntimeError(f"{event_type} payload is malformed")
        artifact_path = _verify_artifact(project, payload.get("artifact"))
        return dict(payload), _read_json(artifact_path, event_type)
    raise RuntimeError(f"required Research Council event is missing: {event_type}")


def _effects(*, local_reads: int, local_writes: int) -> JsonObject:
    return {
        "local_read_executed_count": local_reads,
        "local_write_executed_count": local_writes,
        "prohibited": dict(_ZERO_PROHIBITED_EFFECTS),
    }


def _logical_identity(event_type: str, payload: Mapping[str, object]) -> Tuple[object, ...]:
    if event_type in _BENCHMARK_PORTFOLIO_EVENT_TYPES:
        return (
            payload.get("portfolio_lineage_contract", "legacy-v1"),
            payload.get("portfolio_lineage_id"),
        )
    if event_type.endswith(".benchmark_qualified"):
        return (payload.get("qualification_id"),)
    if event_type.endswith(".experiment_frozen"):
        return (payload.get("qualification_id"),)
    if event_type.endswith(".cell_advanced"):
        return (
            payload.get("freeze_id"),
            payload.get("cell_id"),
            payload.get("parent_portfolio_id"),
        )
    if event_type.endswith(".trial_registered"):
        return (
            payload.get("freeze_id"),
            payload.get("trial_id"),
            payload.get("arm"),
        )
    if event_type.endswith(".evaluated") or event_type.endswith(
        ".architecture_decided"
    ):
        return (payload.get("freeze_id"),)
    if event_type.endswith(".accepted"):
        return (payload.get("decision_id"),)
    raise ValueError(f"unsupported Research Council event type: {event_type}")


def _preflight_expected(
    project: ProjectPort, event_type: str, payload: JsonObject, key: str
) -> None:
    if sum(field == "next_route" for field in payload) != 1:
        raise ValueError("Research Council event payload must contain exactly one next_route")
    descriptors = [
        value
        for value in payload.values()
        if isinstance(value, dict) and set(value) == {"path", "sha256", "bytes"}
    ]
    if len(descriptors) != 1 or payload.get("artifact") is not descriptors[0]:
        raise ValueError("Research Council event payload must contain exactly one artifact")
    current = project.events()
    validate_research_council_event_stream(project, current)
    identity = _logical_identity(event_type, payload)
    exact_replay = any(
        event.get("event_type") == event_type
        and event.get("actor") == "controller"
        and event.get("idempotency_key") == key
        and event.get("payload") == payload
        for event in current
    )
    latest_council_type = next(
        (
            event.get("event_type")
            for event in reversed(current)
            if str(event.get("event_type", "")).startswith(_EVENT_PREFIX)
        ),
        None,
    )
    if (
        latest_council_type == "research.research_council.evaluated"
        and not exact_replay
        and event_type != "research.research_council.architecture_decided"
    ):
        raise ValueError(
            "Research Council evaluation is terminal until its exact architecture decision"
        )
    for event in current:
        current_type = event.get("event_type")
        current_payload = event.get("payload")
        if current_type == event_type and isinstance(current_payload, Mapping):
            if _logical_identity(event_type, current_payload) != identity:
                continue
            if not (
                event.get("actor") == "controller"
                and event.get("idempotency_key") == key
                and dict(current_payload) == payload
            ):
                raise ValueError(
                    "Research Council logical identity already has a different successor"
                )
        if str(current_type).endswith(".accepted") and not exact_replay and not (
            current_type == event_type
            and isinstance(current_payload, Mapping)
            and _logical_identity(event_type, current_payload) == identity
        ):
            raise ValueError("accepted Research Council history is terminal")
        if (
            str(current_type).endswith(".architecture_decided")
            and not exact_replay
            and event_type not in {
            "research.research_council.architecture_decided",
            "research.research_council.accepted",
            }
        ):
            raise ValueError("Research Council decision history cannot accept stale work")


def _record_expected(
    project: ProjectPort, event_type: str, payload: JsonObject, key: str
) -> JsonObject:
    _preflight_expected(project, event_type, payload, key)
    event = project.record(
        event_type, payload, actor="controller", idempotency_key=key
    )
    if (
        event.get("event_type") != event_type
        or event.get("actor") != "controller"
        or event.get("idempotency_key") != key
        or event.get("payload") != payload
    ):
        raise RuntimeError("Project returned a mismatched idempotent Council event")
    _verify_artifact(project, payload["artifact"])
    project.materialize_views()
    return dict(event["payload"])


def _remove_new_artifact(
    project: ProjectPort, relative: Path, expected_content: bytes
) -> None:
    """Remove only the file this call created and only while its bytes still match."""

    try:
        destination = _secure_artifact_path(
            project, relative, create_parents=False
        )
    except (RuntimeError, ValueError):
        return
    if (
        destination.is_symlink()
        or not destination.is_file()
        or destination.read_bytes() != expected_content
    ):
        return
    destination.unlink()
    root = _council_root(project, create=False)
    parent = destination.parent
    while parent != root:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def _record_artifact_expected(
    project: ProjectPort,
    event_type: str,
    relative: Path,
    output: object,
    payload_factory: Callable[[JsonObject], JsonObject],
    key: str,
) -> JsonObject:
    """Preflight lineage, then register one artifact with rejection rollback."""

    descriptor = _artifact_descriptor(relative, output)
    payload = payload_factory(descriptor)
    _preflight_expected(project, event_type, payload, key)
    destination = _secure_artifact_path(project, relative, create_parents=True)
    existed = destination.exists()
    content = _json_bytes(output)
    written_descriptor = _artifact(project, relative, output)
    if written_descriptor != descriptor:
        raise RuntimeError("Research Council artifact descriptor changed while writing")
    try:
        return _record_expected(project, event_type, payload, key)
    except Exception:
        registered = any(
            event.get("event_type") == event_type
            and event.get("actor") == "controller"
            and event.get("idempotency_key") == key
            and event.get("payload") == payload
            for event in project.events()
        )
        if not existed and not registered:
            _remove_new_artifact(project, relative, content)
        raise


def _qualification_from_output(value: Mapping[str, object]) -> JsonObject:
    raw_candidate_ledger = value.get("candidate_ledger")
    if not isinstance(raw_candidate_ledger, list):
        legacy_keys = {
            "admission_criteria_lowered", "authority_evidence", "candidate_catalog_ref",
            "cluster_ledger", "cluster_order", "contract",
            "external_authority_substituted", "hard_gates", "next_route",
            "policy_contract", "qualification_content_sha256", "qualification_id",
            "qualified_cluster_count", "selected_cluster",
        }
        legacy_cluster_keys = {
            "blind_protocol", "boundary_traps", "cluster_id", "dispute_cardinality",
            "evidence_ref", "exact_members", "failed_gate", "functional_unit_comparison",
            "functional_unit_treatment", "gate_results", "native_result_comparison",
            "normalized_product_system_labels", "qualified", "reference_flow_groups",
            "route_effect_evidence", "scientific_disputes",
        }
        body = {
            key: item
            for key, item in value.items()
            if key not in {"qualification_content_sha256", "qualification_id"}
        }
        digest = _canonical_digest(body)
        if (
            value.get("contract")
            != "lca_research_kernel.research_council_benchmark_qualification/v1"
            or value.get("qualification_content_sha256") != digest
            or value.get("qualification_id") != digest[:16]
            or value.get("cluster_order")
            != ["copper_routes", "wastewater_routes", "truck_fuel_routes"]
            or not isinstance(value.get("cluster_ledger"), list)
            or len(value["cluster_ledger"]) != 3
            or set(value) != legacy_keys
            or any(
                not isinstance(row, Mapping) or set(row) != legacy_cluster_keys
                for row in value["cluster_ledger"]
            )
            or value.get("admission_criteria_lowered") is not False
            or value.get("external_authority_substituted") is not False
        ):
            raise ValueError("qualification candidate ledger is malformed")
        return dict(value)
    candidates: Dict[Tuple[str, str], JsonObject] = {}
    for row in raw_candidate_ledger:
        if not isinstance(row, Mapping):
            raise ValueError("qualification candidate ledger row is malformed")
        candidates[(str(row.get("process_id")), str(row.get("process_version")))] = dict(row)
    cluster_specs = []
    for row in value.get("cluster_ledger", []):
        if not isinstance(row, Mapping):
            raise ValueError("qualification cluster ledger is malformed")
        members = row.get("exact_members")
        if not isinstance(members, list):
            raise ValueError("qualification exact members are malformed")
        for member in members:
            if not isinstance(member, Mapping):
                raise ValueError("qualification member is malformed")
        cluster_specs.append(
            {
                "cluster_id": row.get("cluster_id"),
                "product_system_labels": row.get("normalized_product_system_labels"),
                "member_process_refs": [
                    {
                        "process_id": member.get("process_id"),
                        "process_version": member.get("process_version"),
                    }
                    for member in members
                ],
                "scientific_disputes": row.get("scientific_disputes"),
                "functional_unit_comparison": row.get("functional_unit_comparison"),
                "native_result_comparison": row.get("native_result_comparison"),
                "blind_protocol": row.get("blind_protocol"),
                "route_effect_evidence": row.get("route_effect_evidence"),
                "boundary_traps": row.get("boundary_traps"),
                "evidence_ref": row.get("evidence_ref"),
            }
        )
    observation = {
        "contract": "lca_research_kernel.research_council_benchmark_observation/v2",
        "candidate_catalog_ref": value.get("candidate_catalog_ref"),
        "authority_evidence": value.get("authority_evidence"),
        "candidates": list(candidates.values()),
        "cluster_specs": cluster_specs,
    }
    policy = {
        "contract": "lca_research_kernel.research_council_benchmark_policy/v1",
        "cluster_order": value.get("cluster_order"),
        "hard_gates": value.get("hard_gates"),
        "admission_criteria_lowering_allowed": False,
        "external_authority_substitution_allowed": False,
    }
    return compile_benchmark_qualification(
        candidate_observation=observation, policy=policy
    )


def _candidate_inputs(portfolio: Mapping[str, object]) -> List[JsonObject]:
    rows = list(portfolio.get("active_candidates", [])) + list(
        portfolio.get("parked_candidates", [])
    )
    candidates = []
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(
            row.get("routing_evidence"), Mapping
        ):
            raise ValueError("portfolio candidate is malformed")
        evidence = row["routing_evidence"]
        candidates.append(
            {
                "candidate_id": row.get("candidate_id"),
                "conclusion": row.get("conclusion"),
                "validation_status": row.get("validation_status"),
                "scientific_importance": evidence.get("scientific_importance"),
                "flip_potential": evidence.get("flip_potential"),
                "parallelizability": evidence.get("parallelizability"),
                "expected_critical_path_seconds": evidence.get(
                    "expected_critical_path_seconds"
                ),
                "token_metrics": evidence.get("token_metrics"),
            }
        )
    return candidates


def _normalized_ablation_input(decision: Mapping[str, object]) -> JsonObject:
    ablation = decision.get("ablation")
    if not isinstance(ablation, Mapping):
        raise ValueError("architecture decision ablation is malformed")
    if ablation.get("status") == "completed":
        return {"status": "completed", "observations": ablation.get("observations")}
    return {
        "status": "not_applicable_council_not_qualified",
        "reason_ref": ablation.get("reason_ref"),
    }


def _compile_evaluation_outputs(
    freeze: Mapping[str, object],
    traces: Sequence[Mapping[str, object]],
    *,
    quality_checkpoints: object,
    reviewer_observations: object,
    ablation: object,
) -> Tuple[JsonObject, JsonObject]:
    baseline = [trace for trace in traces if trace.get("arm") == "baseline"]
    council = [trace for trace in traces if trace.get("arm") == "council"]
    if not baseline or not council:
        raise RuntimeError("paired baseline and Council trial events are required")
    quality = compile_quality_time_comparison(
        baseline_traces=baseline,
        council_traces=council,
        quality_checkpoints=quality_checkpoints,
    )
    review = compile_blind_review(
        baseline_traces=baseline,
        council_traces=council,
        reviewer_observations=reviewer_observations,
    )
    decision = compile_architecture_decision(
        freeze=freeze,
        baseline_traces=baseline,
        council_traces=council,
        blind_review=review,
        quality_time=quality,
        ablation=ablation,
    )
    normalized_ablation = _normalized_ablation_input(decision)
    body: JsonObject = {
        "contract": "lca_research_kernel.research_council_evaluation/v2",
        "freeze_id": freeze["freeze_id"],
        "baseline_trace_ids": sorted(trace["trace_id"] for trace in baseline),
        "council_trace_ids": sorted(trace["trace_id"] for trace in council),
        "quality_time": quality,
        "blind_review": review,
        "normalized_ablation_input": normalized_ablation,
        "next_route": "compile-research-council-architecture-decision",
    }
    digest = _canonical_digest(body)
    evaluation = {
        **body,
        "evaluation_content_sha256": digest,
        "evaluation_id": digest[:16],
    }
    decision = compile_architecture_decision(
        freeze=freeze,
        baseline_traces=baseline,
        council_traces=council,
        blind_review=review,
        quality_time=quality,
        ablation=normalized_ablation,
    )
    return evaluation, decision


def _qualification_payload(value: Mapping[str, object], artifact: JsonObject) -> JsonObject:
    selected = value.get("selected_cluster")
    return {
        "status": "benchmark_qualified",
        "qualification_id": value["qualification_id"],
        "qualified_cluster_count": value["qualified_cluster_count"],
        "selected_cluster_id": selected.get("cluster_id") if isinstance(selected, Mapping) else None,
        "artifact": artifact,
        "effects": _effects(local_reads=2, local_writes=1),
        "blocker": None if value["qualified_cluster_count"] else "no_qualified_research_council_benchmark",
        "next_route": value["next_route"],
    }


def _freeze_payload(value: Mapping[str, object], artifact: JsonObject) -> JsonObject:
    return {
        "status": "experiment_frozen",
        "qualification_id": value["qualification_id"],
        "freeze_id": value["freeze_id"],
        "cluster_id": value["cluster_id"],
        "exact_member_count": len(value["exact_tiangong_members"]),
        "artifact": artifact,
        "effects": _effects(local_reads=2, local_writes=1),
        "blocker": None,
        "next_route": value["next_route"],
    }


def _cell_payload(
    freeze_id: object,
    value: Mapping[str, object],
    artifact: JsonObject,
    parent_portfolio_id: object,
) -> JsonObject:
    return {
        "status": "cell_advanced",
        "freeze_id": freeze_id,
        "cell_id": value["cell"]["cell_id"],
        "portfolio_id": value["portfolio_id"],
        "parent_portfolio_id": parent_portfolio_id,
        "cell_status": value["cell_status"],
        "active_candidate_count": len(value["active_candidates"]),
        "parked_candidate_count": len(value["parked_candidates"]),
        "dissent_count": len(value["dissent"]),
        "consecutive_stalls": value["consecutive_stalls"],
        "artifact": artifact,
        "effects": _effects(local_reads=2, local_writes=1),
        "blocker": None,
        "next_route": value["next_route"],
    }


def _trial_payload(value: Mapping[str, object], artifact: JsonObject) -> JsonObject:
    prohibited = value["prohibited_effects"]
    return {
        "status": "trial_registered",
        "freeze_id": value["freeze_id"],
        "trace_id": value["trace_id"],
        "trial_id": value["trial_id"],
        "arm": value["arm"],
        "hard_gate_pass_count": sum(item == "passed" for item in value["hard_gates"].values()),
        "prohibited_effect_count": sum(int(item) for item in prohibited.values()),
        "stall_count": value["stall_count"],
        "replan_count": value["replan_count"],
        "artifact": artifact,
        "effects": _effects(local_reads=2, local_writes=1),
        "blocker": None,
        "next_route": value["next_route"],
    }


def _evaluation_payload(
    value: Mapping[str, object], artifact: JsonObject, trace_count: int
) -> JsonObject:
    return {
        "status": "evaluated",
        "freeze_id": value["freeze_id"],
        "evaluation_id": value["evaluation_id"],
        "paired_trial_count": len(value["baseline_trace_ids"]),
        "reviewer_count": value["blind_review"]["reviewer_count"],
        "quality_matched_time_advantage": value["quality_time"]["quality_matched_time_advantage"],
        "artifact": artifact,
        "effects": _effects(local_reads=trace_count + 2, local_writes=1),
        "blocker": None,
        "next_route": value["next_route"],
    }


def _decision_payload(
    freeze_id: object,
    evaluation_id: object,
    value: Mapping[str, object],
    artifact: JsonObject,
) -> JsonObject:
    return {
        "status": "architecture_decided",
        "freeze_id": freeze_id,
        "evaluation_id": evaluation_id,
        "decision_id": value["decision_id"],
        "architecture_status": value["status"],
        "promoted_mechanism_count": len(value["promoted_mechanisms"]),
        "artifact": artifact,
        "effects": _effects(local_reads=3, local_writes=1),
        "blocker": None,
        "next_route": value["next_route"],
    }


def _expected_key(event_type: str, output: Mapping[str, object]) -> str:
    if event_type in _BENCHMARK_PORTFOLIO_EVENT_TYPES:
        lineage = output.get("portfolio_lineage")
        version = (
            str(lineage.get("contract", "")).rsplit("/v", 1)[-1]
            if isinstance(lineage, Mapping)
            else ""
        )
        if version in {"2", "3", "4"}:
            return (
                f"benchmark-portfolio-v{version}:{event_type.rsplit('.', 1)[-1]}:"
                f"{output['portfolio_id']}:{lineage['portfolio_lineage_id']}"
            )
        return (
            f"benchmark-portfolio:{event_type.rsplit('.', 1)[-1]}:"
            f"{output['portfolio_id']}"
        )
    if event_type.endswith(".benchmark_qualified"):
        return f"research-council:benchmark-qualified:{output['qualification_content_sha256']}"
    if event_type.endswith(".experiment_frozen"):
        return f"research-council:experiment-frozen:{output['freeze_content_sha256']}"
    if event_type.endswith(".cell_advanced"):
        return f"research-council:cell-advanced:{output['portfolio_content_sha256']}"
    if event_type.endswith(".trial_registered"):
        return f"research-council:trial-registered:{output['trace_content_sha256']}"
    if event_type.endswith(".evaluated"):
        return f"research-council:evaluated:{output['evaluation_content_sha256']}"
    if event_type.endswith(".architecture_decided"):
        return f"research-council:architecture-decided:{output['decision_content_sha256']}"
    if event_type.endswith(".accepted"):
        return f"research-council:accepted:{output['acceptance_content_sha256']}"
    raise ValueError(f"unsupported Research Council event type: {event_type}")


def validate_research_council_event_stream(
    project: ProjectPort, events: Iterable[JsonObject]
) -> JsonObject:
    """Pure validation of Council event semantics, lineage, and artifacts."""

    rows = list(events)
    qualifications: Dict[str, JsonObject] = {}
    freezes: Dict[str, JsonObject] = {}
    portfolios: Dict[str, JsonObject] = {}
    traces: Dict[Tuple[str, str, str], JsonObject] = {}
    evaluations: Dict[str, JsonObject] = {}
    decisions: Dict[str, JsonObject] = {}
    logical_seen: Dict[Tuple[str, Tuple[object, ...]], JsonObject] = {}
    council_events: List[JsonObject] = []
    decision_seen = False
    acceptance_seen = False
    pending_evaluation: Optional[JsonObject] = None
    benchmark_portfolio_stage: Dict[str, JsonObject] = {}
    benchmark_portfolio_terminal: Optional[JsonObject] = None

    for event in rows:
        event_type = event.get("event_type")
        if pending_evaluation is not None and event_type != (
            "research.research_council.architecture_decided"
        ):
            raise ValueError(
                "Research Council evaluation is terminal until its immediately succeeding architecture decision"
            )
        if not isinstance(event_type, str) or not event_type.startswith(_EVENT_PREFIX):
            continue
        if benchmark_portfolio_terminal is not None:
            payload_candidate = event.get("payload")
            from .research_council_benchmark_portfolio_workflow import (
                LINEAGE_CONTRACT,
                LINEAGE_CONTRACT_V2,
                LINEAGE_CONTRACT_V3,
            )

            terminal_contract = benchmark_portfolio_terminal.get("lineage_contract")
            successor_contract = (
                LINEAGE_CONTRACT_V2
                if terminal_contract == "legacy-v1"
                else LINEAGE_CONTRACT_V3
                if terminal_contract == LINEAGE_CONTRACT_V2
                else LINEAGE_CONTRACT
                if terminal_contract == LINEAGE_CONTRACT_V3
                else None
            )
            corrects_legacy = (
                successor_contract is not None
                and event_type
                == "research.research_council.benchmark_assets_registered"
                and isinstance(payload_candidate, Mapping)
                and payload_candidate.get("portfolio_lineage_contract")
                == successor_contract
                and payload_candidate.get("supersedes_portfolio_id")
                == benchmark_portfolio_terminal.get("portfolio_id")
                and payload_candidate.get("supersedes_manifest_sha256")
                == benchmark_portfolio_terminal.get("manifest_sha256")
            )
            if corrects_legacy:
                benchmark_portfolio_terminal = None
            else:
                raise ValueError(
                    "benchmark portfolio no-promotion decision is terminal for this event stream"
                )
        if acceptance_seen:
            raise ValueError("Research Council acceptance must be terminal")
        if (
            decision_seen
            and not event_type.endswith(".accepted")
            and event_type not in _BENCHMARK_PORTFOLIO_EVENT_TYPES
        ):
            raise ValueError("stale Research Council event follows architecture decision")
        if event.get("actor") != "controller":
            raise ValueError("Research Council events must be recorded by controller")
        if not isinstance(event.get("idempotency_key"), str):
            raise ValueError("Research Council event idempotency key is required")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("Research Council event payload is malformed")
        if sum(key == "next_route" for key in payload) != 1:
            raise ValueError("Research Council event must contain one next_route")
        effects = payload.get("effects")
        if event_type in _BENCHMARK_PORTFOLIO_EVENT_TYPES:
            if effects != _BENCHMARK_PORTFOLIO_ZERO_EFFECTS:
                raise ValueError("Research Council event effects are malformed or nonzero")
        else:
            prohibited = effects.get("prohibited") if isinstance(effects, Mapping) else None
            if (
                not isinstance(effects, Mapping)
                or set(effects) != {
                    "local_read_executed_count",
                    "local_write_executed_count",
                    "prohibited",
                }
                or not isinstance(prohibited, Mapping)
                or dict(prohibited) != _ZERO_PROHIBITED_EFFECTS
            ):
                raise ValueError("Research Council event effects are malformed or nonzero")
        artifact = payload.get("artifact")
        artifact_path = _verify_artifact(project, artifact)
        output = _read_json(artifact_path, event_type)

        if event_type in _BENCHMARK_PORTFOLIO_EVENT_TYPES:
            from .research_council_benchmark_portfolio_workflow import (
                LINEAGE_CONTRACT,
                LINEAGE_CONTRACT_V2,
                LINEAGE_CONTRACT_V3,
                validate_benchmark_portfolio_event_artifact,
            )

            expected_output, expected_payload, lineage = (
                validate_benchmark_portfolio_event_artifact(
                    project,
                    event_type,
                    payload,
                    benchmark_portfolio_stage,
                )
            )
            if event_type.endswith(".benchmark_portfolio_decided"):
                benchmark_portfolio_terminal = {
                    "lineage_contract": (
                        payload.get("portfolio_lineage_contract")
                        if payload.get("portfolio_lineage_contract")
                        in {
                            LINEAGE_CONTRACT_V2,
                            LINEAGE_CONTRACT_V3,
                            LINEAGE_CONTRACT,
                        }
                        else "legacy-v1"
                    ),
                    "portfolio_id": payload.get("portfolio_id"),
                    "manifest_sha256": artifact.get("sha256"),
                    "lineage_id": lineage,
                }
        elif event_type.endswith(".benchmark_qualified"):
            expected_output = _qualification_from_output(output)
            expected_payload = _qualification_payload(expected_output, artifact)
            qualifications[str(expected_output["qualification_id"])] = expected_output
        elif event_type.endswith(".experiment_frozen"):
            qualification_id = str(output.get("qualification_id"))
            qualification = qualifications.get(qualification_id)
            if qualification is None:
                raise ValueError("freeze lacks its preceding registered qualification")
            expected_output = compile_benchmark_freeze(
                qualification=qualification,
                task_prompt=output.get("task_prompt"),
                tools=output.get("tools"),
                allowed_effects=output.get("allowed_effects"),
                initial_evidence_set=output.get("initial_evidence_set"),
                controller_version=output.get("controller_version"),
                responsibility_boundary=output.get("responsibility_boundary"),
            )
            expected_payload = _freeze_payload(expected_output, artifact)
            freezes[str(expected_output["freeze_id"])] = expected_output
        elif event_type.endswith(".cell_advanced"):
            freeze_id = str(payload.get("freeze_id"))
            if freeze_id not in freezes:
                raise ValueError("Cell event lacks its preceding freeze")
            parent_id = payload.get("parent_portfolio_id")
            if parent_id is None:
                expected_output = compile_portfolio(
                    output.get("cell"),
                    _candidate_inputs(output),
                    output.get("incumbent"),
                )
            else:
                parent = portfolios.get(str(parent_id))
                if parent is None:
                    raise ValueError("Cell successor lacks its exact parent portfolio")
                if parent.get("cell", {}).get("cell_id") != output.get("cell", {}).get(
                    "cell_id"
                ):
                    raise ValueError("Cell successor crosses Decision Cell lineage")
                expected_output = advance_portfolio(
                    parent,
                    output.get("latest_progress_updates"),
                    output.get("observed_at_utc"),
                )
            expected_payload = _cell_payload(
                freeze_id, expected_output, artifact, parent_id
            )
            portfolios[str(expected_output["portfolio_id"])] = expected_output
        elif event_type.endswith(".trial_registered"):
            freeze_id = str(output.get("freeze_id"))
            freeze = freezes.get(freeze_id)
            if freeze is None:
                raise ValueError("trial lacks its preceding freeze")
            expected_output = compile_run_trace(
                freeze=freeze,
                arm=output.get("arm"),
                trial_id=output.get("trial_id"),
                timeline=output.get("timeline"),
                final_package=output.get("final_package"),
                effects=output.get("effects"),
            )
            expected_payload = _trial_payload(expected_output, artifact)
            traces[
                (
                    freeze_id,
                    str(expected_output["trial_id"]),
                    str(expected_output["arm"]),
                )
            ] = expected_output
        elif event_type.endswith(".evaluated"):
            freeze_id = str(output.get("freeze_id"))
            freeze = freezes.get(freeze_id)
            if freeze is None:
                raise ValueError("evaluation lacks its preceding freeze")
            freeze_traces = [
                trace for key, trace in traces.items() if key[0] == freeze_id
            ]
            expected_output, expected_decision = _compile_evaluation_outputs(
                freeze,
                freeze_traces,
                quality_checkpoints=output.get("quality_time", {}).get(
                    "quality_checkpoints"
                ),
                reviewer_observations=output.get("blind_review", {}).get(
                    "reviewer_observations"
                ),
                ablation=output.get("normalized_ablation_input"),
            )
            expected_payload = _evaluation_payload(
                expected_output, artifact, len(freeze_traces)
            )
            evaluations[freeze_id] = expected_output
            decisions[f"expected:{freeze_id}"] = expected_decision
            pending_evaluation = event
        elif event_type.endswith(".architecture_decided"):
            if pending_evaluation is None or not council_events or council_events[-1] is not pending_evaluation:
                raise ValueError(
                    "architecture decision must immediately succeed its exact evaluation"
                )
            freeze_id = str(payload.get("freeze_id"))
            evaluation = evaluations.get(freeze_id)
            expected_output = decisions.get(f"expected:{freeze_id}")
            if evaluation is None or expected_output is None:
                raise ValueError("decision lacks its exact preceding evaluation")
            expected_payload = _decision_payload(
                freeze_id, evaluation["evaluation_id"], expected_output, artifact
            )
            decisions[freeze_id] = expected_output
            decision_seen = True
            pending_evaluation = None
        elif event_type.endswith(".accepted"):
            if not decision_seen:
                raise ValueError("acceptance lacks its architecture decision")
            basis = rows[: rows.index(event)]
            expected_output = _compile_acceptance_from_basis(
                project, basis, namespace_events=rows
            )
            expected_payload = _acceptance_payload(expected_output, artifact)
            acceptance_seen = True
        else:
            raise ValueError(f"unsupported Research Council event family: {event_type}")

        if output != expected_output:
            raise ValueError(f"{event_type} artifact differs from semantic replay")
        if payload != expected_payload:
            raise ValueError(f"{event_type} payload schema or lineage is invalid")
        expected_key = _expected_key(event_type, expected_output)
        if event.get("idempotency_key") != expected_key:
            raise ValueError(f"{event_type} idempotency key is not content-derived")
        logical = (event_type, _logical_identity(event_type, payload))
        if logical in logical_seen:
            raise ValueError("duplicate or forked Research Council logical event")
        logical_seen[logical] = event
        council_events.append(event)

    return {
        "council_event_count": len(council_events),
        "qualification_count": len(qualifications),
        "freeze_count": len(freezes),
        "portfolio_count": len(portfolios),
        "trial_count": len(traces),
        "evaluation_count": len(evaluations),
        "decision_count": len([key for key in decisions if not key.startswith("expected:")]),
        "accepted": acceptance_seen,
    }


def qualify_research_council_benchmark(
    project: ProjectPort, observation_path: Path, policy_path: Path
) -> JsonObject:
    observation = _read_json(observation_path, "benchmark observation")
    _reopen_benchmark_evidence(project, observation_path, observation)
    qualification = compile_benchmark_qualification(
        candidate_observation=observation,
        policy=_read_json(policy_path, "benchmark policy"),
    )
    digest = str(qualification["qualification_content_sha256"])
    key = f"research-council:benchmark-qualified:{digest}"
    relative = (
        _ROOT
        / "qualification"
        / str(qualification["qualification_id"])
        / "ledger.json"
    )
    return _record_artifact_expected(
        project,
        "research.research_council.benchmark_qualified",
        relative,
        qualification,
        lambda descriptor: _qualification_payload(qualification, descriptor),
        key,
    )


def freeze_research_council_experiment(
    project: ProjectPort, freeze_input_path: Path
) -> JsonObject:
    request = _read_json(freeze_input_path, "Research Council freeze input")
    expected_fields = {
        "qualification_id",
        "task_prompt",
        "tools",
        "allowed_effects",
        "initial_evidence_set",
        "controller_version",
        "responsibility_boundary",
    }
    if set(request) != expected_fields:
        raise ValueError("Research Council freeze input has an invalid schema")
    qualification_payload, qualification = _latest_artifact(
        project, "research.research_council.benchmark_qualified"
    )
    if (
        request["qualification_id"] != qualification.get("qualification_id")
        or request["qualification_id"] != qualification_payload.get("qualification_id")
    ):
        raise ValueError("freeze input does not bind the registered qualification")
    freeze = compile_benchmark_freeze(
        qualification=qualification,
        task_prompt=request["task_prompt"],
        tools=request["tools"],
        allowed_effects=request["allowed_effects"],
        initial_evidence_set=request["initial_evidence_set"],
        controller_version=request["controller_version"],
        responsibility_boundary=request["responsibility_boundary"],
    )
    digest = str(freeze["freeze_content_sha256"])
    key = f"research-council:experiment-frozen:{digest}"
    relative = _ROOT / "qualification" / str(freeze["freeze_id"]) / "freeze.json"
    return _record_artifact_expected(
        project,
        "research.research_council.experiment_frozen",
        relative,
        freeze,
        lambda descriptor: _freeze_payload(freeze, descriptor),
        key,
    )


def advance_research_council_cell(
    project: ProjectPort, progress_input_path: Path
) -> JsonObject:
    request = _read_json(progress_input_path, "Research Council Cell input")
    freeze_payload, freeze = _latest_artifact(
        project, "research.research_council.experiment_frozen"
    )
    if request.get("freeze_id") != freeze.get("freeze_id") or request.get(
        "freeze_id"
    ) != freeze_payload.get("freeze_id"):
        raise ValueError("Cell input does not bind the registered experiment freeze")
    initial_fields = {"freeze_id", "cell", "candidates", "incumbent"}
    advance_fields = {
        "freeze_id",
        "prior_portfolio_id",
        "progress_updates",
        "observed_at_utc",
    }
    if set(request) == initial_fields:
        parent_portfolio_id = None
        portfolio = compile_portfolio(
            request["cell"], request["candidates"], request["incumbent"]
        )
    elif set(request) == advance_fields:
        prior_payload, prior = _latest_artifact(
            project, "research.research_council.cell_advanced"
        )
        if (
            request["prior_portfolio_id"] != prior.get("portfolio_id")
            or request["prior_portfolio_id"] != prior_payload.get("portfolio_id")
            or prior_payload.get("freeze_id") != freeze["freeze_id"]
        ):
            raise ValueError("Cell input does not bind the latest portfolio")
        portfolio = advance_portfolio(
            prior,
            request["progress_updates"],
            request["observed_at_utc"],
        )
        parent_portfolio_id = prior["portfolio_id"]
    else:
        raise ValueError("Research Council Cell input has an invalid schema")

    digest = str(portfolio["portfolio_content_sha256"])
    key = f"research-council:cell-advanced:{digest}"
    relative = (
        _ROOT
        / "cells"
        / str(portfolio["cell"]["cell_id"])
        / f"portfolio-{portfolio['portfolio_id']}.json"
    )
    return _record_artifact_expected(
        project,
        "research.research_council.cell_advanced",
        relative,
        portfolio,
        lambda descriptor: _cell_payload(
            freeze["freeze_id"], portfolio, descriptor, parent_portfolio_id
        ),
        key,
    )


def register_research_council_trial(
    project: ProjectPort, trial_input_path: Path
) -> JsonObject:
    request = _read_json(trial_input_path, "Research Council trial input")
    expected_fields = {
        "freeze_id",
        "arm",
        "trial_id",
        "timeline",
        "final_package",
        "effects",
    }
    if set(request) != expected_fields:
        raise ValueError("Research Council trial input has an invalid schema")
    freeze_payload, freeze = _latest_artifact(
        project, "research.research_council.experiment_frozen"
    )
    if request["freeze_id"] != freeze.get("freeze_id") or request[
        "freeze_id"
    ] != freeze_payload.get("freeze_id"):
        raise ValueError("trial input does not bind the registered experiment freeze")
    trace = compile_run_trace(
        freeze=freeze,
        arm=request["arm"],
        trial_id=request["trial_id"],
        timeline=request["timeline"],
        final_package=request["final_package"],
        effects=request["effects"],
    )
    digest = str(trace["trace_content_sha256"])
    key = f"research-council:trial-registered:{digest}"
    relative = (
        _ROOT
        / str(trace["freeze_id"])
        / str(trace["trial_id"])
        / str(trace["arm"])
        / f"trace-{trace['trace_id']}.json"
    )
    return _record_artifact_expected(
        project,
        "research.research_council.trial_registered",
        relative,
        trace,
        lambda descriptor: _trial_payload(trace, descriptor),
        key,
    )


def evaluate_research_council(
    project: ProjectPort, evaluation_input_path: Path
) -> JsonObject:
    request = _read_json(evaluation_input_path, "Research Council evaluation input")
    if set(request) != {
        "freeze_id",
        "quality_checkpoints",
        "reviewer_observations",
        "ablation",
    }:
        raise ValueError("Research Council evaluation input has an invalid schema")
    freeze_payload, freeze = _latest_artifact(
        project, "research.research_council.experiment_frozen"
    )
    if request["freeze_id"] != freeze.get("freeze_id") or request[
        "freeze_id"
    ] != freeze_payload.get("freeze_id"):
        raise ValueError("evaluation input does not bind the registered freeze")

    traces: List[JsonObject] = []
    for event in project.events():
        if event.get("event_type") != "research.research_council.trial_registered":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            raise RuntimeError("registered trial payload is malformed")
        trace = _read_json(
            _verify_artifact(project, payload.get("artifact")),
            "registered Research Council trace",
        )
        if (
            payload.get("freeze_id") != freeze["freeze_id"]
            or trace.get("freeze_id") != freeze["freeze_id"]
            or payload.get("trace_id") != trace.get("trace_id")
            or payload.get("arm") != trace.get("arm")
            or payload.get("trial_id") != trace.get("trial_id")
        ):
            raise RuntimeError("registered trial identity is forked or mismatched")
        traces.append(trace)
    evaluation, decision = _compile_evaluation_outputs(
        freeze,
        traces,
        quality_checkpoints=request["quality_checkpoints"],
        reviewer_observations=request["reviewer_observations"],
        ablation=request["ablation"],
    )
    evaluation_digest = str(evaluation["evaluation_content_sha256"])
    evaluation_key = f"research-council:evaluated:{evaluation_digest}"
    evaluation_relative = (
        _ROOT
        / str(freeze["freeze_id"])
        / "evaluation"
        / str(evaluation["evaluation_id"])
        / "evaluation.json"
    )
    _record_artifact_expected(
        project,
        "research.research_council.evaluated",
        evaluation_relative,
        evaluation,
        lambda descriptor: _evaluation_payload(
            evaluation, descriptor, len(traces)
        ),
        evaluation_key,
    )

    decision_digest = str(decision["decision_content_sha256"])
    decision_key = f"research-council:architecture-decided:{decision_digest}"
    decision_relative = (
        _ROOT
        / str(freeze["freeze_id"])
        / "evaluation"
        / str(decision["decision_id"])
        / "decision.json"
    )
    return _record_artifact_expected(
        project,
        "research.research_council.architecture_decided",
        decision_relative,
        decision,
        lambda descriptor: _decision_payload(
            freeze["freeze_id"], evaluation["evaluation_id"], decision, descriptor
        ),
        decision_key,
    )


_PROJECTION_FILES = (
    "project-snapshot.json",
    "machine-context.json",
    "human-status.md",
)


def _source_basis_event_bytes(
    project: ProjectPort, basis_events: Sequence[JsonObject]
) -> bytes:
    event_path = project.workspace / "research-events.jsonl"
    if not event_path.is_file() or event_path.is_symlink():
        raise RuntimeError("source Research Council event history is unavailable")
    lines = event_path.read_bytes().splitlines(keepends=True)
    if len(lines) < len(basis_events):
        raise RuntimeError("source Research Council event history is truncated")
    return b"".join(lines[: len(basis_events)])


def _source_control_bytes(project: ProjectPort) -> Dict[str, bytes]:
    paths = [project.workspace / "research-events.jsonl"]
    paths.extend(project.workspace / name for name in _PROJECTION_FILES)
    root = project.workspace / _ROOT
    if root.exists() or root.is_symlink():
        if root.is_symlink():
            raise RuntimeError("Research Council source root is a symlink")
        paths.extend(path for path in root.rglob("*") if path.is_file())
    result = {}
    for path in paths:
        relative = path.relative_to(project.workspace).as_posix()
        if path.is_symlink():
            raise RuntimeError("Research Council source control path is a symlink")
        if not path.is_file():
            raise RuntimeError(f"required source control file is missing: {relative}")
        result[relative] = path.read_bytes()
    return result


def _assert_registered_artifact_namespace(
    project: ProjectPort, events: Sequence[JsonObject]
) -> None:
    expected = set()
    for event in events:
        event_type = str(event.get("event_type", ""))
        if not event_type.startswith(_EVENT_PREFIX):
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            raise ValueError("Council event payload is malformed during inventory")
        path = _verify_artifact(project, payload.get("artifact"))
        expected.add(path.relative_to(project.workspace).as_posix())

    root = _council_root(project, create=False)
    observed = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise RuntimeError("Research Council artifact namespace contains a symlink")
        if path.is_file():
            relative = path.relative_to(project.workspace).as_posix()
            if relative not in expected:
                raise RuntimeError(
                    f"unregistered Research Council namespace file: {relative}"
                )
            observed.add(relative)
        elif not path.is_dir():
            raise RuntimeError("Research Council namespace contains a non-file entry")
    if observed != expected:
        raise RuntimeError("Research Council registered artifact namespace is incomplete")


def _clean_projection_bytes(
    project: ProjectPort, events: Sequence[JsonObject]
) -> Dict[str, bytes]:
    artifacts: Dict[str, bytes] = {}
    for event in events:
        event_type = str(event.get("event_type", ""))
        if not event_type.startswith(_EVENT_PREFIX):
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            raise ValueError("Council event payload is malformed during clean replay")
        source = _verify_artifact(project, payload.get("artifact"))
        relative = source.relative_to(project.workspace).as_posix()
        content = source.read_bytes()
        if relative in artifacts and artifacts[relative] != content:
            raise RuntimeError("Council clean replay artifact path is forked")
        artifacts[relative] = content
    return project.clean_research_council_projection_bytes(events, artifacts)


def _compile_acceptance_from_basis(
    project: ProjectPort,
    basis_events: Sequence[JsonObject],
    *,
    namespace_events: Optional[Sequence[JsonObject]] = None,
) -> JsonObject:
    validation = validate_research_council_event_stream(project, basis_events)
    if validation["decision_count"] != 1 or validation["accepted"]:
        raise ValueError("acceptance basis must end at exactly one architecture decision")
    decision_event = next(
        event
        for event in reversed(basis_events)
        if event.get("event_type")
        == "research.research_council.architecture_decided"
    )
    decision_payload = decision_event["payload"]
    decision = _read_json(
        _verify_artifact(project, decision_payload["artifact"]),
        "architecture decision",
    )
    artifacts = []
    family_counts: Dict[str, int] = {}
    for event in basis_events:
        event_type = str(event.get("event_type", ""))
        if not event_type.startswith(_EVENT_PREFIX):
            continue
        family_counts[event_type] = family_counts.get(event_type, 0) + 1
        descriptor = event["payload"]["artifact"]
        path = _verify_artifact(project, descriptor)
        artifacts.append(
            {
                "path": path.relative_to(project.workspace).as_posix(),
                "sha256": descriptor["sha256"],
                "bytes": descriptor["bytes"],
            }
        )
    artifacts.sort(key=lambda row: row["path"])
    _assert_registered_artifact_namespace(
        project, basis_events if namespace_events is None else namespace_events
    )
    clean_projections = _clean_projection_bytes(project, basis_events)
    body: JsonObject = {
        "contract": "lca_research_kernel.research_council_read_only_acceptance/v2",
        "decision_id": decision["decision_id"],
        "architecture_status": decision["status"],
        "pre_acceptance_event_history_sha256": hashlib.sha256(
            _source_basis_event_bytes(project, basis_events)
        ).hexdigest(),
        "pre_acceptance_event_count": len(basis_events),
        "pre_acceptance_event_family_counts": dict(sorted(family_counts.items())),
        "pre_acceptance_registered_artifacts": artifacts,
        "pre_acceptance_registered_artifact_count": len(artifacts),
        "pre_acceptance_registered_artifact_tree_sha256": _canonical_digest(
            artifacts
        ),
        "pre_acceptance_projection_sha256": {
            name: hashlib.sha256(content).hexdigest()
            for name, content in clean_projections.items()
        },
        "pre_acceptance_clean_replay_verified": True,
        "source_write_executed_count": 0,
        "zero_prohibited_effects": True,
        "next_route": "complete-research-council-goal",
    }
    digest = _canonical_digest(body)
    return {
        **body,
        "acceptance_content_sha256": digest,
        "acceptance_id": digest[:16],
    }


def _acceptance_payload(
    value: Mapping[str, object], artifact: JsonObject
) -> JsonObject:
    return {
        "status": "accepted",
        "decision_id": value["decision_id"],
        "acceptance_id": value["acceptance_id"],
        "registered_artifact_count": value[
            "pre_acceptance_registered_artifact_count"
        ],
        "zero_prohibited_effects": True,
        "post_registration_clean_replay_verified": True,
        "artifact": artifact,
        "effects": _effects(
            local_reads=value["pre_acceptance_registered_artifact_count"] + 4,
            local_writes=1,
        ),
        "blocker": None,
        "next_route": value["next_route"],
    }


def compile_research_council_read_only_acceptance(
    project: ProjectPort,
) -> JsonObject:
    """Audit the full Council chain in clean context without source writes."""

    before = _source_control_bytes(project)
    events = project.events()
    validation = validate_research_council_event_stream(project, events)
    acceptance_indexes = [
        index
        for index, event in enumerate(events)
        if event.get("event_type") == "research.research_council.accepted"
    ]
    if len(acceptance_indexes) > 1:
        raise ValueError("Research Council history contains duplicate acceptance")
    basis = events[: acceptance_indexes[0]] if acceptance_indexes else events
    audit = _compile_acceptance_from_basis(
        project, basis, namespace_events=events
    )
    if acceptance_indexes:
        accepted_event = events[acceptance_indexes[0]]
        accepted_artifact = _read_json(
            _verify_artifact(project, accepted_event["payload"]["artifact"]),
            "Research Council acceptance",
        )
        if accepted_artifact != audit or not validation["accepted"]:
            raise ValueError("registered acceptance differs from full read-only audit")
        clean = _clean_projection_bytes(project, events)
    else:
        clean = _clean_projection_bytes(project, basis)
    for name, expected in clean.items():
        source_path = project.workspace / name
        if not source_path.is_file() or source_path.read_bytes() != expected:
            raise RuntimeError(f"source projection differs from clean replay: {name}")
    after = _source_control_bytes(project)
    if before != after:
        raise RuntimeError("read-only acceptance audit changed source Project bytes")
    return audit


def accept_research_council_read_only(project: ProjectPort) -> JsonObject:
    audit = compile_research_council_read_only_acceptance(project)
    relative = (
        _ROOT / "acceptance" / str(audit["acceptance_id"]) / "acceptance.json"
    )
    result = _record_artifact_expected(
        project,
        "research.research_council.accepted",
        relative,
        audit,
        lambda descriptor: _acceptance_payload(audit, descriptor),
        _expected_key("research.research_council.accepted", audit),
    )
    events = project.events()
    clean = _clean_projection_bytes(project, events)
    for name, expected in clean.items():
        if (project.workspace / name).read_bytes() != expected:
            raise RuntimeError(
                f"post-registration projection differs from clean replay: {name}"
            )
    replayed = compile_research_council_read_only_acceptance(project)
    if replayed != audit:
        raise RuntimeError("acceptance audit identity changed after registration")
    return result
