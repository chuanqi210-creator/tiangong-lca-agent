"""ResearchProject integration for the formal Council-default product runtime."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .council_default_contract import (
    CUTOVER_GATE_CHECKS,
    REQUIRED_GATES,
    ROLLBACK_FILES,
    derive_forbidden_effects,
)
from .council_runtime import (
    FORMAL_EVENT_ARTIFACT_ROLES,
    INTERNAL_EFFECTS,
    ROLLBACK_COMMIT,
    _validate_compiled_cell,
    _validate_compiled_worker_result,
    advance_cell,
    close_cell,
    compile_agenda,
    compile_incumbent,
    compile_worker_result,
    open_cell,
    select_route,
    validate_formal_council_events,
)
from .research_council_contracts import (
    _artifact,
    _finish,
    _reject_secret_keys,
    _strings,
    _text,
)


JsonObject = Dict[str, object]
FORMAL_ARTIFACT_ROOT = Path("artifacts/council-default")
FORMAL_SKILL_REF = "run-lca-research-project"
CUTOVER_MANIFEST_PATH = Path(
    "outputs/council-default-acceptance/final/cutover-gates.json"
)
CUTOVER_MANIFEST_WORKSPACE_PATH = Path("cutover-gates.json")
CUTOVER_GATE_ROOT = Path("outputs/council-default-acceptance/final/gates")
CUTOVER_TRANSCRIPT_ROOT = Path(
    "outputs/council-default-acceptance/final/transcripts"
)


def _json_bytes(value: object) -> bytes:
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


def _read_json(path: Path, label: str) -> JsonObject:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _operation_key(project: object, operation: str, input_path: Path) -> str:
    resolved = input_path.resolve()
    try:
        identity = resolved.relative_to(project.workspace).as_posix()
    except ValueError:
        identity = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"formal-council:{operation}:{digest}"


def _artifact_candidate(role: str, value: Mapping[str, object]) -> Tuple[bytes, JsonObject, JsonObject]:
    content = _json_bytes(value)
    digest = hashlib.sha256(content).hexdigest()
    relative = (FORMAL_ARTIFACT_ROOT / role / f"{digest[:16]}.json").as_posix()
    artifact_id = f"formal-council-{role}-{digest[:16]}"
    bus = {
        "artifact_id": artifact_id,
        "activity_id": "formal-council-runtime",
        "skill_ref": FORMAL_SKILL_REF,
        "kind": "formal_council_artifact",
        "artifact_role": role,
        "path": relative,
        "workspace_path": relative,
        "sha256": digest,
        "bytes": len(content),
    }
    ref = {
        "role": role,
        "path": relative,
        "sha256": digest,
        "bytes": len(content),
    }
    return content, bus, ref


def _preflight_event(
    project: object, event_type: str, payload: JsonObject, idempotency_key: str
) -> Optional[JsonObject]:
    for event in project.events():
        if event.get("idempotency_key") != idempotency_key:
            continue
        if (
            event.get("event_type") != event_type
            or event.get("actor") != "controller"
            or event.get("payload") != payload
        ):
            raise ValueError(
                f"Idempotency key '{idempotency_key}' was reused with different content"
            )
        return event
    return None


def _preflight_formal_append(
    project: object, rows: Sequence[Tuple[str, JsonObject, str]]
) -> None:
    """Validate formal transitions before any durable event append occurs."""

    candidate = list(project.events())
    for offset, (event_type, payload, idempotency_key) in enumerate(rows, start=1):
        candidate.append(
            {
                "event_id": f"preflight-{len(candidate) + offset}",
                "sequence": len(candidate) + 1,
                "event_type": event_type,
                "occurred_at": "preflight",
                "actor": "controller",
                "idempotency_key": idempotency_key,
                "payload": copy.deepcopy(payload),
            }
        )
    validate_formal_council_events(candidate)


def _persist_artifact(project: object, content: bytes, bus: JsonObject) -> None:
    destination = project.workspace / str(bus["workspace_path"])
    if destination.exists():
        if not destination.is_file() or destination.read_bytes() != content:
            raise RuntimeError(
                f"formal Council artifact path drifted: {bus['workspace_path']}"
            )
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, destination)
    project.record(
        "artifact.registered",
        bus,
        actor="controller",
        idempotency_key=f"artifact:{bus['artifact_id']}",
    )


def _publish(
    project: object,
    *,
    operation: str,
    input_path: Path,
    event_type: str,
    artifact_role: str,
    value: Mapping[str, object],
    payload_builder,
) -> JsonObject:
    content, bus, ref = _artifact_candidate(artifact_role, value)
    payload = payload_builder(ref, bus)
    key = _operation_key(project, operation, input_path)
    existing = _preflight_event(project, event_type, payload, key)
    if existing is None:
        _preflight_formal_append(project, [(event_type, payload, key)])
        _persist_artifact(project, content, bus)
        event = project._record_council_event(
            event_type,
            payload,
            idempotency_key=key,
        )
    else:
        event = existing
    project.materialize_views()
    return copy.deepcopy(event["payload"])


def _registered_artifacts(events: Sequence[Mapping[str, object]]) -> Dict[str, JsonObject]:
    result: Dict[str, JsonObject] = {}
    for event in events:
        if (
            event.get("event_type") != "artifact.registered"
            or event.get("actor") != "controller"
            or not isinstance(event.get("idempotency_key"), str)
            or not str(event.get("idempotency_key")).strip()
        ):
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        path = payload.get("path")
        if isinstance(path, str):
            result[path] = payload
    return result


def _load_registered_ref(
    project: object, value: object, *, expected_role: str
) -> JsonObject:
    ref = _artifact(value, "artifact_ref", expected_role=expected_role)
    registered = _registered_artifacts(project.events()).get(str(ref["path"]))
    if not isinstance(registered, dict):
        raise ValueError("formal Council artifact is not registered in the Project Artifact Bus")
    if any(registered.get(field) != ref[field] for field in ("sha256", "bytes")):
        raise ValueError("formal Council artifact registration drifted")
    _verify_registered_file(project, registered)
    path = project.workspace / str(registered["workspace_path"])
    return _read_json(path, "formal Council artifact")


def compile_council_agenda(project: object, input_path: Path) -> JsonObject:
    request = _read_json(input_path, "Council agenda input")
    _load_registered_ref(
        project,
        request.get("authority_receipt_ref"),
        expected_role="authority_receipt",
    )
    compiled = compile_agenda(request)
    return _publish(
        project,
        operation="compile-agenda",
        input_path=input_path,
        event_type="research.council.agenda_compiled",
        artifact_role="council_agenda",
        value=compiled,
        payload_builder=lambda ref, _bus: {
            "agenda_revision": compiled["agenda_revision"],
            "agenda_ref": ref,
        },
    )


def route_council_decision(project: object, input_path: Path) -> JsonObject:
    request = _read_json(input_path, "Council route input")
    if set(request) != {"contract", "agenda_ref", "signals"} or request.get(
        "contract"
    ) != "lca_research_kernel.council_route_request/v1":
        raise ValueError("Council route input contract is unsupported")
    agenda = _load_registered_ref(
        project, request.get("agenda_ref"), expected_role="council_agenda"
    )
    signals = request.get("signals")
    if not isinstance(signals, Mapping):
        raise ValueError("Council route signals must be an object")
    decision = select_route(agenda, signals)
    return _publish(
        project,
        operation="select-route",
        input_path=input_path,
        event_type="research.council.route_selected",
        artifact_role="council_route_decision",
        value=decision,
        payload_builder=lambda ref, _bus: {
            "agenda_revision": decision["agenda_revision"],
            "route": decision["route"],
            "route_decision_id": decision["route_decision_id"],
            "route_decision_ref": ref,
        },
    )


def open_council_cell(project: object, input_path: Path) -> JsonObject:
    request = _read_json(input_path, "Council Cell input")
    if set(request) != {
        "contract",
        "agenda_ref",
        "authority_ref",
        "route_ref",
        "cell_request",
    } or request.get("contract") != "lca_research_kernel.council_cell_request/v1":
        raise ValueError("Council Cell input contract is unsupported")
    agenda_ref = _artifact(
        request.get("agenda_ref"), "agenda_ref", expected_role="council_agenda"
    )
    agenda = _load_registered_ref(
        project, agenda_ref, expected_role="council_agenda"
    )
    authority_ref = _artifact(
        request.get("authority_ref"),
        "authority_ref",
        expected_role="authority_receipt",
    )
    _load_registered_ref(project, authority_ref, expected_role="authority_receipt")
    if authority_ref != agenda.get("authority_receipt_ref"):
        raise ValueError("Council Cell authority drifted from the agenda")
    route = _load_registered_ref(
        project,
        request.get("route_ref"),
        expected_role="council_route_decision",
    )
    cell_request = request.get("cell_request")
    if not isinstance(cell_request, Mapping):
        raise ValueError("Council Cell request must be an object")
    for source in cell_request.get("input_artifacts", []):
        normalized = _artifact(source, "Cell input artifact")
        _load_registered_ref(
            project, normalized, expected_role=str(normalized["role"])
        )
    core_agenda_ref = dict(agenda_ref)
    core_agenda_ref["role"] = "research_agenda"
    cell = open_cell(
        core_agenda_ref,
        authority_ref,
        route,
        cell_request,
    )
    return _publish(
        project,
        operation="open-cell",
        input_path=input_path,
        event_type="research.council.cell_opened",
        artifact_role="council_cell",
        value=cell,
        payload_builder=lambda ref, _bus: {
            "agenda_revision": cell["agenda_revision"],
            "cell_revision": cell["cell_revision"],
            "route_decision_id": cell["route_decision_id"],
            "cell_ref": ref,
        },
    )


def _verify_local_artifact_ref(project: object, value: object, field: str) -> JsonObject:
    ref = _artifact(value, field)
    path = (project.workspace / str(ref["path"])).resolve()
    try:
        path.relative_to(project.workspace)
    except ValueError as exc:
        raise ValueError(f"{field} escapes the Project workspace") from exc
    if not path.is_file():
        raise RuntimeError(f"{field} is missing: {ref['path']}")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != ref["sha256"] or len(content) != ref["bytes"]:
        raise RuntimeError(f"{field} drifted: {ref['path']}")
    return ref


def _register_existing_artifact(project: object, ref: Mapping[str, object], kind: str) -> None:
    bus = {
        "artifact_id": f"formal-council-{kind}-{str(ref['sha256'])[:16]}",
        "activity_id": "formal-council-runtime",
        "skill_ref": FORMAL_SKILL_REF,
        "kind": "formal_council_artifact",
        "artifact_role": ref["role"],
        "path": ref["path"],
        "workspace_path": ref.get("workspace_path", ref["path"]),
        "sha256": ref["sha256"],
        "bytes": ref["bytes"],
    }
    project.record(
        "artifact.registered",
        bus,
        actor="controller",
        idempotency_key=f"artifact:{bus['artifact_id']}",
    )


def register_council_worker_result(project: object, input_path: Path) -> JsonObject:
    request = _read_json(input_path, "Council worker result input")
    if set(request) != {"contract", "cell_ref", "result"} or request.get(
        "contract"
    ) != "lca_research_kernel.council_worker_result_request/v1":
        raise ValueError("Council worker result input contract is unsupported")
    cell = _load_registered_ref(
        project, request.get("cell_ref"), expected_role="council_cell"
    )
    result = request.get("result")
    if not isinstance(result, Mapping):
        raise ValueError("Council worker result must be an object")
    for source in result.get("source_bindings", []):
        normalized = _artifact(source, "worker source binding")
        _load_registered_ref(
            project, normalized, expected_role=str(normalized["role"])
        )
    output_ref = _verify_local_artifact_ref(
        project, result.get("artifact_ref"), "worker output artifact"
    )
    compiled = compile_worker_result(cell, result)
    content, bus, ref = _artifact_candidate("council_worker_result", compiled)
    payload = {
        "agenda_revision": compiled["agenda_revision"],
        "cell_revision": compiled["cell_revision"],
        "worker_result_ref": ref,
    }
    key = _operation_key(project, "register-worker-result", input_path)
    existing = _preflight_event(
        project, "research.council.worker_result_registered", payload, key
    )
    if existing is None:
        _preflight_formal_append(
            project,
            [("research.council.worker_result_registered", payload, key)],
        )
        _register_existing_artifact(project, output_ref, "worker-output")
        _persist_artifact(project, content, bus)
        event = project._record_council_event(
            "research.council.worker_result_registered",
            payload,
            idempotency_key=key,
        )
    else:
        event = existing
    project.materialize_views()
    response = copy.deepcopy(event["payload"])
    response["worker_result_id"] = compiled["worker_result_id"]
    return response


def advance_council_cell(project: object, input_path: Path) -> JsonObject:
    request = _read_json(input_path, "Council advance input")
    if set(request) != {
        "contract",
        "cell_ref",
        "worker_result_refs",
        "incumbent_candidate",
    } or request.get("contract") != "lca_research_kernel.council_advance_request/v1":
        raise ValueError("Council advance input contract is unsupported")
    cell = _load_registered_ref(
        project, request.get("cell_ref"), expected_role="council_cell"
    )
    raw_refs = request.get("worker_result_refs")
    if not isinstance(raw_refs, list):
        raise ValueError("worker_result_refs must be a list")
    worker_results = [
        _load_registered_ref(
            project, ref, expected_role="council_worker_result"
        )
        for ref in raw_refs
    ]
    candidate = request.get("incumbent_candidate")
    incumbent = None
    incumbent_candidate = None
    if candidate is not None:
        if not isinstance(candidate, Mapping):
            raise ValueError("incumbent_candidate must be an object or null")
        incumbent = compile_incumbent(cell, candidate, worker_results)
        incumbent_candidate = _artifact_candidate("council_incumbent", incumbent)
    update = advance_cell(cell, worker_results, incumbent)
    content, bus, ref = _artifact_candidate("council_cell_update", update)
    payload = {
        "agenda_revision": update["agenda_revision"],
        "cell_revision": update["cell_revision"],
        "to_state": update["to_state"],
        "cell_update_ref": ref,
    }
    key = _operation_key(project, "advance-cell", input_path)
    existing = _preflight_event(project, "research.council.cell_advanced", payload, key)
    if existing is None:
        _preflight_formal_append(
            project, [("research.council.cell_advanced", payload, key)]
        )
        if incumbent_candidate is not None:
            incumbent_content, incumbent_bus, _incumbent_ref = incumbent_candidate
            _persist_artifact(project, incumbent_content, incumbent_bus)
        _persist_artifact(project, content, bus)
        event = project._record_council_event(
            "research.council.cell_advanced",
            payload,
            idempotency_key=key,
        )
    else:
        event = existing
    project.materialize_views()
    return copy.deepcopy(event["payload"])


def close_council_cell(project: object, input_path: Path) -> JsonObject:
    request = _read_json(input_path, "Council close input")
    if set(request) != {"contract", "cell_ref", "cell_update_ref", "close_reason"} or request.get(
        "contract"
    ) != "lca_research_kernel.council_close_request/v1":
        raise ValueError("Council close input contract is unsupported")
    cell = _load_registered_ref(
        project, request.get("cell_ref"), expected_role="council_cell"
    )
    update = _load_registered_ref(
        project, request.get("cell_update_ref"), expected_role="council_cell_update"
    )
    if update.get("to_state") not in {"incumbent_validated", "replan_required"}:
        raise ValueError(
            "Council Cell can close only after a validated incumbent or bounded no-progress stop"
        )
    closed = close_cell(
        cell,
        {
            "incumbent": update.get("incumbent"),
            "worker_results": update.get("worker_results"),
            "close_reason": request.get("close_reason"),
        },
    )
    return _publish(
        project,
        operation="close-cell",
        input_path=input_path,
        event_type="research.council.cell_closed",
        artifact_role="council_cell_close",
        value=closed,
        payload_builder=lambda ref, _bus: {
            "agenda_revision": closed["agenda_revision"],
            "cell_revision": closed["cell_revision"],
            "cell_close_ref": ref,
        },
    )


def accept_council_outcome(project: object, input_path: Path) -> JsonObject:
    request = _read_json(input_path, "Council acceptance input")
    if set(request) != {
        "contract",
        "agenda_ref",
        "route_ref",
        "cell_ref",
        "accepted_result_ref",
    } or request.get("contract") != "lca_research_kernel.council_accept_request/v1":
        raise ValueError("Council acceptance input contract is unsupported")
    agenda = _load_registered_ref(
        project, request.get("agenda_ref"), expected_role="council_agenda"
    )
    route = _load_registered_ref(
        project,
        request.get("route_ref"),
        expected_role="council_route_decision",
    )
    selected_route = route.get("route")
    cell_revision = None
    if selected_route == "bounded_decision_cell":
        cell = _load_registered_ref(
            project, request.get("cell_ref"), expected_role="council_cell"
        )
        accepted = _load_registered_ref(
            project,
            request.get("accepted_result_ref"),
            expected_role="council_cell_close",
        )
        cell_revision = cell.get("cell_revision")
        if accepted.get("cell_revision") != cell_revision:
            raise ValueError("accepted Council Cell result is stale")
    elif selected_route == "single_controller_fast_path":
        if request.get("cell_ref") is not None:
            raise ValueError("fast-path acceptance cannot bind a Decision Cell")
        _load_registered_ref(
            project,
            request.get("accepted_result_ref"),
            expected_role="fast_path_result",
        )
    else:
        raise ValueError("Council acceptance route is unsupported")
    outcome = _finish(
        {
            "contract": "lca_research_kernel.council_outcome/v1",
            "agenda_revision": agenda.get("agenda_revision"),
            "route_decision_id": route.get("route_decision_id"),
            "route": selected_route,
            "cell_revision": cell_revision,
            "accepted_result_ref": request.get("accepted_result_ref"),
            "next_route": "compile-next-council-agenda",
        },
        "outcome",
    )
    return _publish(
        project,
        operation="accept-outcome",
        input_path=input_path,
        event_type="research.council.outcome_accepted",
        artifact_role="council_outcome",
        value=outcome,
        payload_builder=lambda ref, _bus: {
            "agenda_revision": outcome["agenda_revision"],
            "route_decision_id": outcome["route_decision_id"],
            "route": outcome["route"],
            "cell_revision": outcome["cell_revision"],
            "outcome_ref": ref,
        },
    )


def complete_council_fast_path(project: object, input_path: Path) -> JsonObject:
    request = _read_json(input_path, "Council fast-path input")
    if set(request) != {
        "contract",
        "agenda_ref",
        "route_ref",
        "source_refs",
        "allowed_effects",
        "statement",
        "result",
    } or request.get("contract") != "lca_research_kernel.council_fast_path_request/v1":
        raise ValueError("Council fast-path input contract is unsupported")
    _reject_secret_keys(request)
    agenda = _load_registered_ref(
        project, request.get("agenda_ref"), expected_role="council_agenda"
    )
    route = _load_registered_ref(
        project,
        request.get("route_ref"),
        expected_role="council_route_decision",
    )
    if route.get("route") != "single_controller_fast_path":
        raise ValueError("Council fast path contradicts the selected route")
    raw_sources = request.get("source_refs")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ValueError("Council fast path requires source_refs")
    sources = []
    for raw in raw_sources:
        normalized = _artifact(raw, "fast-path source")
        _load_registered_ref(
            project, normalized, expected_role=str(normalized["role"])
        )
        sources.append(normalized)
    sources.sort(key=lambda value: _json_bytes(value))
    effects = _strings(
        request.get("allowed_effects"), "fast-path allowed_effects", required=True
    )
    if not set(effects).issubset(INTERNAL_EFFECTS):
        raise ValueError("Council fast path contains a human-owned or unsupported effect")
    result = request.get("result")
    if not isinstance(result, Mapping):
        raise ValueError("Council fast-path result must be an object")
    compiled = _finish(
        {
            "contract": "lca_research_kernel.council_fast_path_result/v1",
            "agenda_revision": agenda.get("agenda_revision"),
            "route_decision_id": route.get("route_decision_id"),
            "source_refs": sources,
            "allowed_effects": effects,
            "statement": _text(request.get("statement"), "fast-path statement"),
            "result": copy.deepcopy(dict(result)),
            "next_route": "accept-council-outcome",
        },
        "fast_path_result",
    )
    content, bus, ref = _artifact_candidate("fast_path_result", compiled)
    completed_payload = {
        "agenda_revision": compiled["agenda_revision"],
        "route_decision_id": compiled["route_decision_id"],
        "result_ref": ref,
    }
    completed_key = _operation_key(project, "complete-fast-path", input_path)
    existing = _preflight_event(
        project,
        "research.council.fast_path_completed",
        completed_payload,
        completed_key,
    )
    if existing is None:
        started_payload = {
            "agenda_revision": compiled["agenda_revision"],
            "route_decision_id": compiled["route_decision_id"],
        }
        started_key = f"{completed_key}:started"
        started = _preflight_event(
            project,
            "research.council.fast_path_started",
            started_payload,
            started_key,
        )
        rows = []
        if started is None:
            rows.append(
                ("research.council.fast_path_started", started_payload, started_key)
            )
        rows.append(
            ("research.council.fast_path_completed", completed_payload, completed_key)
        )
        _preflight_formal_append(project, rows)
        if started is None:
            project._record_council_event(
                "research.council.fast_path_started",
                started_payload,
                idempotency_key=started_key,
            )
        _persist_artifact(project, content, bus)
        event = project._record_council_event(
            "research.council.fast_path_completed",
            completed_payload,
            idempotency_key=completed_key,
        )
    else:
        event = existing
    project.materialize_views()
    return copy.deepcopy(event["payload"])
def verify_council_rollback(project: object, input_path: Path) -> JsonObject:
    request = _read_json(input_path, "Council rollback input")
    if set(request) != {"contract", "rollback_commit"} or request.get(
        "contract"
    ) != "lca_research_kernel.council_rollback_request/v1":
        raise ValueError("Council rollback input contract is unsupported")
    if request.get("rollback_commit") != ROLLBACK_COMMIT:
        raise ValueError("Council rollback commit drifted")
    verified_files: List[JsonObject] = []
    repository = Path(__file__).resolve().parents[1]
    for path, expected in ROLLBACK_FILES.items():
        result = subprocess.run(
            ["git", "show", f"{ROLLBACK_COMMIT}:{path}"],
            cwd=str(repository),
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Council rollback file is absent: {path}")
        actual = hashlib.sha256(result.stdout).hexdigest()
        if actual != expected:
            raise RuntimeError(f"Council rollback file hash drifted: {path}")
        verified_files.append({"path": path, "sha256": actual})
    receipt = _finish(
        {
            "contract": "lca_research_kernel.council_rollback_receipt/v1",
            "rollback_commit": ROLLBACK_COMMIT,
            "files": verified_files,
            "history_preservation": "append_only_no_deletion",
            "status": "verified",
            "next_route": "continue-council-cutover-gates",
        },
        "rollback_receipt",
    )
    return _publish(
        project,
        operation="verify-rollback",
        input_path=input_path,
        event_type="research.council.rollback_verified",
        artifact_role="rollback_receipt",
        value=receipt,
        payload_builder=lambda ref, _bus: {
            "rollback_commit": ROLLBACK_COMMIT,
            "receipt_ref": ref,
        },
    )


def _rollback_file_contract() -> List[JsonObject]:
    return [
        {"path": path, "sha256": digest}
        for path, digest in ROLLBACK_FILES.items()
    ]


def _validate_rollback_receipt(receipt: Mapping[str, object]) -> None:
    expected = {
        "contract",
        "rollback_commit",
        "files",
        "history_preservation",
        "status",
        "next_route",
        "rollback_receipt_content_sha256",
        "rollback_receipt_id",
    }
    if set(receipt) != expected:
        raise ValueError("formal Council rollback receipt fields are invalid")
    body = {
        key: receipt[key]
        for key in receipt
        if key not in {"rollback_receipt_content_sha256", "rollback_receipt_id"}
    }
    if (
        _finish(body, "rollback_receipt") != dict(receipt)
        or receipt.get("contract")
        != "lca_research_kernel.council_rollback_receipt/v1"
        or receipt.get("rollback_commit") != ROLLBACK_COMMIT
        or receipt.get("files") != _rollback_file_contract()
        or receipt.get("history_preservation") != "append_only_no_deletion"
        or receipt.get("status") != "verified"
        or receipt.get("next_route") != "continue-council-cutover-gates"
    ):
        raise ValueError("formal Council rollback receipt failed exact verification")


def _verified_rollback_receipt_refs(
    project: object, events: Sequence[Mapping[str, object]]
) -> List[JsonObject]:
    registered = _registered_artifacts(events)
    refs: List[JsonObject] = []
    for event in events:
        if event.get("event_type") != "research.council.rollback_verified":
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        ref = _artifact(
            payload.get("receipt_ref"),
            "receipt_ref",
            expected_role="rollback_receipt",
        )
        registration = registered.get(str(ref["path"]))
        if not isinstance(registration, Mapping) or any(
            registration.get(field) != ref[field] for field in ("sha256", "bytes")
        ):
            raise ValueError("verified rollback receipt is absent from the Artifact Bus")
        _verify_registered_file(project, registration)
        receipt = _read_ref_value(project, ref)
        _validate_rollback_receipt(receipt)
        refs.append(ref)
    return refs


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _check_result_sha256(command: Sequence[str], exit_code: int) -> str:
    return hashlib.sha256(
        _json_bytes({"command": list(command), "exit_code": exit_code})
    ).hexdigest()


def _execute_cutover_check(command: Sequence[str]) -> None:
    normalized = list(command)
    allowed = {tuple(contract[1]) for contract in CUTOVER_GATE_CHECKS.values()}
    if tuple(normalized) not in allowed:
        raise ValueError("Council cutover check command is outside the local allowlist")
    result = subprocess.run(
        normalized,
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        check=False,
        timeout=300,
    )
    if result.returncode != 0:
        raise ValueError("Council cutover check command did not pass")


def _require_path_under(value: object, root: Path, label: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{label} path is invalid")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} path escapes its acceptance root")
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} path is outside its acceptance root") from exc


def _validate_check_evidence(
    project: object,
    value: object,
    *,
    gate_id: object,
    check_id: object,
    events: Sequence[Mapping[str, object]],
    execute_checks: bool,
    executed_commands: set,
) -> None:
    ref = value
    if not isinstance(ref, Mapping):
        raise ValueError("Council cutover check evidence reference is invalid")
    _require_path_under(
        ref.get("path"), CUTOVER_TRANSCRIPT_ROOT, "Council cutover check evidence"
    )
    evidence = _load_bus_reference(
        project, ref, "Council cutover check evidence", events=events
    )
    expected = {
        "contract",
        "gate_id",
        "check_id",
        "status",
        "verification_kind",
        "command",
        "exit_code",
        "output_sha256",
    }
    command = evidence.get("command")
    expected_check = CUTOVER_GATE_CHECKS.get(str(gate_id))
    if (
        set(evidence) != expected
        or evidence.get("contract")
        != "lca_research_kernel.council_default_check_evidence/v1"
        or evidence.get("gate_id") != gate_id
        or evidence.get("check_id") != check_id
        or evidence.get("status") != "passed"
        or expected_check is None
        or evidence.get("verification_kind") != expected_check[0]
        or not isinstance(command, list)
        or not command
        or any(not isinstance(part, str) or not part for part in command)
        or evidence.get("exit_code") != 0
        or evidence.get("output_sha256") != _check_result_sha256(command or [], 0)
        or command != list(expected_check[1])
    ):
        raise ValueError("Council cutover check evidence is invalid")
    command_key = tuple(command)
    if execute_checks and command_key not in executed_commands:
        _execute_cutover_check(command)
        executed_commands.add(command_key)


def _event_prefix_sha256(
    events: Sequence[Mapping[str, object]], count: int
) -> str:
    content = "".join(
        json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        for event in events[:count]
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _validate_cutover_manifest(
    project: object,
    manifest: Mapping[str, object],
    events: Sequence[Mapping[str, object]],
    *,
    execute_checks: bool = False,
) -> None:
    if set(manifest) != {
        "contract",
        "all_required_gates_passed",
        "gate_results",
        "clean_acceptance",
        "effect_ledger",
        "zero_forbidden_effects",
    } or manifest.get(
        "contract"
    ) != "lca_research_kernel.council_default_cutover_gates/v1":
        raise ValueError("Council cutover gate manifest contract is unsupported")
    if manifest.get("all_required_gates_passed") is not True:
        raise ValueError("Council cutover gates have not all passed")
    gates = manifest.get("gate_results")
    if not isinstance(gates, list):
        raise ValueError("Council cutover gates must be a list")
    gate_ids = []
    executed_commands: set = set()
    for index, row in enumerate(gates):
        if not isinstance(row, Mapping) or set(row) != {
            "gate_id",
            "status",
            "evidence_refs",
        }:
            raise ValueError(f"Council cutover gates[{index}] is invalid")
        gate_ids.append(row.get("gate_id"))
        if row.get("status") != "passed":
            raise ValueError("Council cutover gates have not all passed")
        refs = row.get("evidence_refs")
        if not isinstance(refs, list) or not refs:
            raise ValueError("Council cutover gates require registered evidence")
        for ref in refs:
            if not isinstance(ref, Mapping):
                raise ValueError("Council cutover gate evidence reference is invalid")
            _require_path_under(
                ref.get("path"), CUTOVER_GATE_ROOT, "Council cutover gate evidence"
            )
            evidence = _load_bus_reference(
                project, ref, "cutover gate evidence", events=events
            )
            if (
                evidence.get("contract")
                != "lca_research_kernel.council_default_gate_evidence/v1"
                or evidence.get("gate_id") != row.get("gate_id")
                or evidence.get("status") != "passed"
            ):
                raise ValueError("Council cutover gate evidence is invalid")
            checks = evidence.get("checks")
            if not isinstance(checks, list) or not checks:
                raise ValueError("Council cutover gate evidence has no checks")
            for check in checks:
                if (
                    not isinstance(check, Mapping)
                    or not isinstance(check.get("check_id"), str)
                    or not check.get("check_id")
                    or check.get("status") != "passed"
                    or not isinstance(check.get("evidence_refs"), list)
                    or not check.get("evidence_refs")
                ):
                    raise ValueError("Council cutover gate check is invalid")
                for leaf in check["evidence_refs"]:
                    _validate_check_evidence(
                        project,
                        leaf,
                        gate_id=row.get("gate_id"),
                        check_id=check.get("check_id"),
                        events=events,
                        execute_checks=execute_checks,
                        executed_commands=executed_commands,
                    )
    if set(gate_ids) != REQUIRED_GATES or len(gate_ids) != len(REQUIRED_GATES):
        raise ValueError("Council cutover gates are incomplete")
    clean = manifest.get("clean_acceptance")
    if (
        not isinstance(clean, Mapping)
        or clean.get("status") != "accepted"
        or clean.get("p1_count") != 0
        or clean.get("p2_count") != 0
    ):
        raise ValueError("Council cutover clean acceptance is invalid")
    clean_artifact = _load_bus_reference(
        project,
        {
            "path": clean.get("artifact_path"),
            "sha256": clean.get("artifact_sha256"),
            "artifact_id": clean.get("artifact_id"),
            "registration_event_id": clean.get("registration_event_id"),
        },
        "clean acceptance",
        events=events,
    )
    independence_ref = clean_artifact.get("independence_receipt_ref")
    if (
        set(clean_artifact)
        != {
            "contract",
            "status",
            "p1_count",
            "p2_count",
            "independence_receipt_ref",
        }
        or
        clean_artifact.get("contract")
        != "lca_research_kernel.council_default_clean_acceptance/v1"
        or clean_artifact.get("status") != "accepted"
        or clean_artifact.get("p1_count") != 0
        or clean_artifact.get("p2_count") != 0
    ):
        raise ValueError("Council cutover clean acceptance artifact is invalid")
    independence = _load_bus_reference(
        project,
        independence_ref,
        "clean acceptance independence receipt",
        events=events,
    )
    if (
        set(independence)
        != {
            "contract",
            "status",
            "resource_kind",
            "opaque_resource_id_sha256",
            "context_manifest_sha256",
            "reviewed_basis_sha256",
            "review_report_ref",
        }
        or independence.get("contract")
        != "lca_research_kernel.council_default_clean_independence_receipt/v1"
        or independence.get("status") != "accepted"
        or independence.get("resource_kind")
        not in {"isolated_subagent_context", "clean_context_worker"}
        or any(
            not _is_sha256(independence.get(field))
            for field in (
                "opaque_resource_id_sha256",
                "context_manifest_sha256",
                "reviewed_basis_sha256",
            )
        )
    ):
        raise ValueError("Council cutover clean independence receipt is invalid")
    review = _load_bus_reference(
        project,
        independence.get("review_report_ref"),
        "clean acceptance review report",
        events=events,
    )
    if (
        set(review)
        != {
            "contract",
            "status",
            "p1_count",
            "p2_count",
            "opaque_resource_id_sha256",
            "reviewed_basis_sha256",
        }
        or review.get("contract")
        != "lca_research_kernel.council_default_clean_review_report/v1"
        or review.get("status") != "accepted"
        or review.get("p1_count") != 0
        or review.get("p2_count") != 0
        or review.get("opaque_resource_id_sha256")
        != independence.get("opaque_resource_id_sha256")
        or review.get("reviewed_basis_sha256")
        != independence.get("reviewed_basis_sha256")
    ):
        raise ValueError("Council cutover clean review report is invalid")
    effect = manifest.get("effect_ledger")
    if not isinstance(effect, Mapping) or effect.get("forbidden_effect_count") != 0:
        raise ValueError("Council cutover effect ledger is invalid")
    effect_artifact = _load_bus_reference(
        project,
        {
            "path": effect.get("path"),
            "sha256": effect.get("sha256"),
            "artifact_id": effect.get("artifact_id"),
            "registration_event_id": effect.get("registration_event_id"),
        },
        "effect ledger",
        events=events,
    )
    scanned_count = effect_artifact.get("scanned_event_count")
    effect_registration_index = next(
        (
            index
            for index, event in enumerate(events)
            if event.get("event_id") == effect.get("registration_event_id")
        ),
        None,
    )
    if (
        not isinstance(scanned_count, int)
        or isinstance(scanned_count, bool)
        or effect_registration_index is None
        or scanned_count != effect_registration_index
        or effect_artifact.get("scanned_event_log_sha256")
        != _event_prefix_sha256(events, scanned_count)
    ):
        raise ValueError("Council effect ledger did not scan the complete event prefix")
    if (
        set(effect_artifact)
        != {
            "contract",
            "forbidden_effect_count",
            "forbidden_effects",
            "scanned_event_count",
            "scanned_event_log_sha256",
        }
        or
        effect_artifact.get("contract")
        != "lca_research_kernel.council_default_effect_ledger/v1"
        or effect_artifact.get("forbidden_effect_count") != 0
        or effect_artifact.get("forbidden_effects") != []
        or manifest.get("zero_forbidden_effects") is not True
    ):
        raise ValueError("Council cutover gates contain forbidden effects")
    derived_forbidden_effects = derive_forbidden_effects(events[:scanned_count])
    if (
        effect_artifact.get("forbidden_effects") != derived_forbidden_effects
        or effect_artifact.get("forbidden_effect_count")
        != len(derived_forbidden_effects)
    ):
        raise ValueError("Council cutover gates contain forbidden effects")
    suffix = events[effect_registration_index + 1 :]
    if any(
        event.get("event_type") != "artifact.registered"
        or not isinstance(event.get("payload"), Mapping)
        or event["payload"].get("workspace_path")
        != CUTOVER_MANIFEST_WORKSPACE_PATH.as_posix()
        for event in suffix
    ) or len(suffix) > 1:
        raise ValueError("Council effect ledger did not scan the complete event prefix")
    if not _verified_rollback_receipt_refs(project, events):
        raise ValueError("Council cutover gates require verified rollback evidence")


def record_council_cutover(project: object, gate_manifest_path: Path) -> JsonObject:
    try:
        relative = gate_manifest_path.resolve().relative_to(project.workspace).as_posix()
    except ValueError as exc:
        raise ValueError("Council cutover gate manifest escapes the Project") from exc
    if relative != CUTOVER_MANIFEST_WORKSPACE_PATH.as_posix():
        raise ValueError("Council cutover gate manifest path is not the formal path")
    manifest = _read_json(gate_manifest_path, "Council cutover gate manifest")
    _validate_cutover_manifest(
        project, manifest, project.events(), execute_checks=True
    )
    manifest_bytes = gate_manifest_path.read_bytes()
    manifest_ref = {
        "role": "council_cutover_gate_manifest",
        "path": CUTOVER_MANIFEST_PATH.as_posix(),
        "workspace_path": relative,
        "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "bytes": len(manifest_bytes),
    }
    bus = {
        "artifact_id": f"formal-council-cutover-gates-{str(manifest_ref['sha256'])[:16]}",
        "activity_id": "formal-council-runtime",
        "skill_ref": FORMAL_SKILL_REF,
        "kind": "formal_council_artifact",
        "artifact_role": manifest_ref["role"],
        "path": manifest_ref["path"],
        "workspace_path": manifest_ref["path"],
        "sha256": manifest_ref["sha256"],
        "bytes": manifest_ref["bytes"],
    }
    payload = {
        "all_required_gates_passed": True,
        "gate_manifest_sha256": manifest_ref["sha256"],
        "gate_manifest_artifact_id": bus["artifact_id"],
    }
    key = _operation_key(project, "accept-cutover", gate_manifest_path)
    existing = _preflight_event(
        project, "research.council.cutover_accepted", payload, key
    )
    if existing is None:
        _preflight_formal_append(
            project, [("research.council.cutover_accepted", payload, key)]
        )
        _register_existing_artifact(project, manifest_ref, "cutover-gates")
        event = project._record_council_event(
            "research.council.cutover_accepted",
            payload,
            idempotency_key=key,
        )
    else:
        event = existing
    project.materialize_views()
    return copy.deepcopy(event["payload"])


def _load_bus_reference(
    project: object,
    value: object,
    label: str,
    *,
    events: Optional[Sequence[Mapping[str, object]]] = None,
) -> JsonObject:
    if not isinstance(value, Mapping) or set(value) != {
        "path",
        "sha256",
        "artifact_id",
        "registration_event_id",
    }:
        raise ValueError(f"{label} reference is invalid")
    registration = next(
        (
            event
            for event in (project.events() if events is None else events)
            if event.get("event_id") == value.get("registration_event_id")
        ),
        None,
    )
    if not isinstance(registration, Mapping) or registration.get(
        "event_type"
    ) != "artifact.registered":
        raise ValueError(f"{label} is absent from the Project Artifact Bus")
    if (
        registration.get("actor") != "controller"
        or not isinstance(registration.get("idempotency_key"), str)
        or not str(registration.get("idempotency_key")).strip()
    ):
        raise ValueError(f"{label} was not registered by the controller")
    payload = registration.get("payload")
    if (
        not isinstance(payload, Mapping)
        or payload.get("artifact_id") != value.get("artifact_id")
        or payload.get("sha256") != value.get("sha256")
        or payload.get("path") != value.get("path")
    ):
        raise ValueError(f"{label} registration drifted")
    _verify_registered_file(project, payload)
    return _read_json(project.workspace / str(payload["workspace_path"]), label)


def activate_council_rollback(project: object, input_path: Path) -> JsonObject:
    request = _read_json(input_path, "Council rollback activation input")
    if set(request) != {"contract", "rollback_receipt_ref"} or request.get(
        "contract"
    ) != "lca_research_kernel.council_rollback_activation_request/v1":
        raise ValueError("Council rollback activation input contract is unsupported")
    snapshot = project._project(project.events())
    if snapshot.get("council", {}).get("formal_status") != "formal_operational_truth":
        raise ValueError("Council rollback activation requires an accepted cutover")
    receipt_ref = _artifact(
        request.get("rollback_receipt_ref"),
        "rollback_receipt_ref",
        expected_role="rollback_receipt",
    )
    receipt = _load_registered_ref(
        project, receipt_ref, expected_role="rollback_receipt"
    )
    _validate_rollback_receipt(receipt)
    verified_refs = _verified_rollback_receipt_refs(project, project.events())
    if receipt_ref not in verified_refs:
        raise ValueError("Council rollback activation requires a verified rollback event")
    payload = {
        "rollback_commit": ROLLBACK_COMMIT,
        "rollback_receipt_ref": receipt_ref,
    }
    key = _operation_key(project, "activate-rollback", input_path)
    existing = _preflight_event(
        project, "research.council.rollback_activated", payload, key
    )
    if existing is None:
        _preflight_formal_append(
            project, [("research.council.rollback_activated", payload, key)]
        )
        event = project._record_council_event(
            "research.council.rollback_activated",
            payload,
            idempotency_key=key,
        )
    else:
        event = existing
    project.materialize_views()
    return copy.deepcopy(event["payload"])


def accept_council_read_only(project: object) -> JsonObject:
    """Validate an already accepted formal Council state without writing it."""

    before = project.event_path.read_bytes()
    events = project.events()
    snapshot = project._project(events)
    council = snapshot.get("council")
    if not isinstance(council, Mapping):
        raise ValueError("formal Council projection is absent")
    if council.get("formal_status") != "formal_operational_truth":
        raise ValueError("formal Council read-only acceptance requires accepted cutover")
    if project.event_path.read_bytes() != before:
        raise RuntimeError("formal Council read-only acceptance mutated event history")
    return {
        "contract": "lca_research_kernel.council_read_only_acceptance/v1",
        "validation_status": "accepted",
        "formal_status": council["formal_status"],
        "council_status": council["status"],
        "cutover_event_id": council["cutover_event_id"],
        "event_count": len(events),
        "event_log_sha256": hashlib.sha256(before).hexdigest(),
        "writes_performed": 0,
    }


def validate_project_council_events(
    project: object, events: Sequence[Mapping[str, object]]
) -> List[str]:
    validate_formal_council_events(events)
    registered_by_path: Dict[str, Mapping[str, object]] = {}
    registered_by_id: Dict[str, Mapping[str, object]] = {}
    agenda: Optional[JsonObject] = None
    route: Optional[JsonObject] = None
    cell: Optional[JsonObject] = None
    registered_worker_results: Dict[str, JsonObject] = {}
    latest_update: Optional[JsonObject] = None
    latest_accepted_result_ref: Optional[JsonObject] = None
    cutover_seen = False
    rollback_seen = False
    for event_index, event in enumerate(events):
        event_type = event.get("event_type")
        payload = event.get("payload")
        if event_type == "artifact.registered" and isinstance(payload, Mapping):
            if (
                event.get("actor") != "controller"
                or not isinstance(event.get("idempotency_key"), str)
                or not str(event.get("idempotency_key")).strip()
            ):
                continue
            path = payload.get("path")
            artifact_id = payload.get("artifact_id")
            if isinstance(path, str):
                registered_by_path[path] = payload
            if isinstance(artifact_id, str):
                registered_by_id[artifact_id] = payload
            continue
        if not isinstance(event_type, str) or not event_type.startswith(
            "research.council."
        ):
            continue
        assert isinstance(payload, Mapping)
        for field, role in FORMAL_EVENT_ARTIFACT_ROLES.items():
            if field not in payload:
                continue
            ref = _artifact(payload[field], field, expected_role=role)
            registered = registered_by_path.get(str(ref["path"]))
            if not isinstance(registered, Mapping):
                raise ValueError(
                    f"{event_type} references an artifact absent from the Project Artifact Bus"
                )
            if any(registered.get(name) != ref[name] for name in ("sha256", "bytes")):
                raise ValueError(f"{event_type} artifact registration drifted")
            _verify_registered_file(project, registered)
        if event_type == "research.council.cutover_accepted":
            if cutover_seen or rollback_seen:
                raise ValueError("formal Council cutover event ordering is invalid")
            registered = registered_by_id.get(str(payload["gate_manifest_artifact_id"]))
            if not isinstance(registered, Mapping):
                raise ValueError("cutover gate manifest is absent from the Project Artifact Bus")
            if registered.get("sha256") != payload.get("gate_manifest_sha256"):
                raise ValueError("cutover gate manifest registration drifted")
            _verify_registered_file(project, registered)
            if registered.get("workspace_path") != CUTOVER_MANIFEST_WORKSPACE_PATH.as_posix():
                raise ValueError("cutover gate manifest path drifted")
            manifest = _read_json(
                project.workspace / str(registered["workspace_path"]),
                "Council cutover gate manifest",
            )
            _validate_cutover_manifest(project, manifest, events[:event_index])
            cutover_seen = True
        if event_type == "research.council.agenda_compiled":
            agenda = _read_ref_value(project, payload["agenda_ref"])
            core = agenda.get("research_agenda")
            if not isinstance(core, Mapping):
                raise ValueError("formal Council agenda core is absent")
            rebuilt = compile_agenda(
                {
                    "goal": core.get("goal"),
                    "authority_receipt_ref": core.get("authority_receipt_ref"),
                    "facts": core.get("facts"),
                    "hypotheses": core.get("hypotheses"),
                    "risks": core.get("risks"),
                    "plan": core.get("plan"),
                }
            )
            if rebuilt != agenda or agenda.get("agenda_revision") != payload.get(
                "agenda_revision"
            ):
                raise ValueError("formal Council agenda artifact failed semantic replay")
            _verify_semantic_ref_against_bus(
                project,
                agenda.get("authority_receipt_ref"),
                registered_by_path,
                "formal Council agenda authority",
            )
            route = None
            cell = None
            registered_worker_results = {}
            latest_update = None
            latest_accepted_result_ref = None
        elif event_type == "research.council.route_selected":
            if agenda is None:
                raise ValueError("formal Council route has no active agenda")
            route = _read_ref_value(project, payload["route_decision_ref"])
            rebuilt = select_route(agenda, route.get("signals", {}))
            if (
                rebuilt != route
                or route.get("route") != payload.get("route")
                or route.get("route_decision_id") != payload.get("route_decision_id")
            ):
                raise ValueError("formal Council route artifact failed semantic replay")
        elif event_type == "research.council.cell_opened":
            if route is None:
                raise ValueError("formal Council Cell has no active route")
            cell = _read_ref_value(project, payload["cell_ref"])
            _validate_compiled_cell(cell)
            if (
                cell.get("agenda_revision") != payload.get("agenda_revision")
                or cell.get("route_decision_id") != payload.get("route_decision_id")
                or cell.get("cell_revision") != payload.get("cell_revision")
            ):
                raise ValueError("formal Council Cell artifact failed semantic replay")
            _verify_semantic_ref_against_bus(
                project,
                cell.get("authority_receipt_ref"),
                registered_by_path,
                "formal Council Cell authority",
            )
            for source in cell.get("input_artifacts", []):
                _verify_semantic_ref_against_bus(
                    project,
                    source,
                    registered_by_path,
                    "formal Council Cell source",
                )
            registered_worker_results = {}
            latest_update = None
        elif event_type == "research.council.worker_result_registered":
            if cell is None:
                raise ValueError("formal Council worker result has no active Cell")
            worker = _read_ref_value(project, payload["worker_result_ref"])
            _validate_compiled_worker_result(cell, worker)
            if (
                worker.get("agenda_revision") != payload.get("agenda_revision")
                or worker.get("cell_revision") != payload.get("cell_revision")
            ):
                raise ValueError("formal Council worker artifact failed semantic replay")
            for source in worker.get("source_bindings", []):
                _verify_semantic_ref_against_bus(
                    project,
                    source,
                    registered_by_path,
                    "formal Council worker source",
                )
            _verify_semantic_ref_against_bus(
                project,
                worker.get("artifact_ref"),
                registered_by_path,
                "formal Council worker output",
            )
            registered_worker_results[str(worker["worker_result_id"])] = worker
        elif event_type == "research.council.cell_advanced":
            if cell is None:
                raise ValueError("formal Council update has no active Cell")
            latest_update = _read_ref_value(project, payload["cell_update_ref"])
            update_workers = latest_update.get("worker_results")
            if not isinstance(update_workers, list) or any(
                not isinstance(row, Mapping)
                or row.get("worker_result_id") not in registered_worker_results
                or registered_worker_results[str(row.get("worker_result_id"))]
                != row
                for row in update_workers
            ):
                raise ValueError("formal Council update uses an unregistered worker result")
            rebuilt = advance_cell(
                cell, update_workers, latest_update.get("incumbent")
            )
            if rebuilt != latest_update or latest_update.get("to_state") != payload.get(
                "to_state"
            ):
                raise ValueError("formal Council update artifact failed semantic replay")
        elif event_type == "research.council.cell_closed":
            if cell is None or latest_update is None:
                raise ValueError("formal Council close has no validated Cell update")
            closed = _read_ref_value(project, payload["cell_close_ref"])
            rebuilt = close_cell(
                cell,
                {
                    "incumbent": latest_update.get("incumbent"),
                    "worker_results": latest_update.get("worker_results"),
                    "close_reason": closed.get("close_reason"),
                },
            )
            if rebuilt != closed:
                raise ValueError("formal Council close artifact failed semantic replay")
            latest_accepted_result_ref = dict(payload["cell_close_ref"])
        elif event_type == "research.council.fast_path_completed":
            fast = _read_ref_value(project, payload["result_ref"])
            _validate_fast_path_result(
                project, fast, registered_by_path, agenda, route
            )
            latest_accepted_result_ref = dict(payload["result_ref"])
        elif event_type == "research.council.outcome_accepted":
            if agenda is None or route is None or latest_accepted_result_ref is None:
                raise ValueError("formal Council outcome has incomplete active lineage")
            outcome = _read_ref_value(project, payload["outcome_ref"])
            body = {
                key: outcome[key]
                for key in outcome
                if key not in {"outcome_content_sha256", "outcome_id"}
            }
            if (
                _finish(body, "outcome") != outcome
                or outcome.get("accepted_result_ref") != latest_accepted_result_ref
                or outcome.get("agenda_revision") != agenda.get("agenda_revision")
                or outcome.get("route_decision_id") != route.get("route_decision_id")
                or any(
                    outcome.get(field) != payload.get(field)
                    for field in (
                        "agenda_revision",
                        "route_decision_id",
                        "route",
                        "cell_revision",
                    )
                )
            ):
                raise ValueError("formal Council outcome artifact failed semantic replay")
        elif event_type == "research.council.rollback_verified":
            receipt = _read_ref_value(project, payload["receipt_ref"])
            _validate_rollback_receipt(receipt)
        elif event_type == "research.council.rollback_activated":
            if rollback_seen:
                raise ValueError("formal Council rollback event ordering is invalid")
            receipt_ref = _artifact(
                payload.get("rollback_receipt_ref"),
                "rollback_receipt_ref",
                expected_role="rollback_receipt",
            )
            if receipt_ref not in _verified_rollback_receipt_refs(
                project, events[:event_index]
            ):
                raise ValueError(
                    "Council rollback activation is not bound to a verified rollback event"
                )
            rollback_seen = True
    return []


def _validate_fast_path_result(
    project: object,
    value: Mapping[str, object],
    registered_by_path: Mapping[str, Mapping[str, object]],
    agenda: Optional[Mapping[str, object]],
    route: Optional[Mapping[str, object]],
) -> None:
    expected = {
        "contract",
        "agenda_revision",
        "route_decision_id",
        "source_refs",
        "allowed_effects",
        "statement",
        "result",
        "next_route",
        "fast_path_result_content_sha256",
        "fast_path_result_id",
    }
    if set(value) != expected or value.get(
        "contract"
    ) != "lca_research_kernel.council_fast_path_result/v1":
        raise ValueError("formal fast-path result fields are invalid")
    body = {
        key: value[key]
        for key in value
        if key not in {"fast_path_result_content_sha256", "fast_path_result_id"}
    }
    if _finish(body, "fast_path_result") != dict(value):
        raise ValueError("formal fast-path result content address drifted")
    if (
        agenda is None
        or route is None
        or value.get("agenda_revision") != agenda.get("agenda_revision")
        or value.get("route_decision_id") != route.get("route_decision_id")
        or route.get("route") != "single_controller_fast_path"
    ):
        raise ValueError("formal fast-path result lineage drifted")
    effects = _strings(value.get("allowed_effects"), "fast-path effects", required=True)
    if effects != value.get("allowed_effects") or not set(effects).issubset(
        INTERNAL_EFFECTS
    ):
        raise ValueError("formal fast-path result contains an unsupported effect")
    sources = value.get("source_refs")
    if not isinstance(sources, list) or not sources:
        raise ValueError("formal fast-path result sources are absent")
    for raw in sources:
        ref = _artifact(raw, "fast-path source")
        registered = registered_by_path.get(str(ref["path"]))
        if not isinstance(registered, Mapping) or any(
            registered.get(field) != ref[field] for field in ("sha256", "bytes")
        ):
            raise ValueError("formal fast-path result source is unregistered")
        _verify_registered_file(project, registered)


def _verify_semantic_ref_against_bus(
    project: object,
    value: object,
    registered_by_path: Mapping[str, Mapping[str, object]],
    label: str,
) -> None:
    ref = _artifact(value, label)
    registered = registered_by_path.get(str(ref["path"]))
    if not isinstance(registered, Mapping) or any(
        registered.get(field) != ref[field] for field in ("sha256", "bytes")
    ):
        raise ValueError(f"{label} is absent from the Project Artifact Bus")
    _verify_registered_file(project, registered)


def _verify_registered_file(project: object, artifact: Mapping[str, object]) -> None:
    relative = artifact.get("workspace_path")
    if not isinstance(relative, str):
        raise ValueError("formal Council artifact workspace path is invalid")
    path = (project.workspace / relative).resolve()
    try:
        path.relative_to(project.workspace)
    except ValueError as exc:
        raise ValueError("formal Council artifact path escapes the Project") from exc
    if not path.is_file():
        raise RuntimeError(f"formal Council artifact is missing: {relative}")
    content = path.read_bytes()
    if (
        hashlib.sha256(content).hexdigest() != artifact.get("sha256")
        or len(content) != artifact.get("bytes")
    ):
        raise RuntimeError(f"formal Council artifact drifted: {relative}")


def initial_council_projection() -> JsonObject:
    return {
        "status": "not_started",
        "formal_status": "approved_pending_cutover",
        "agenda_id": None,
        "agenda_revision": None,
        "route": None,
        "route_decision_id": None,
        "cell_id": None,
        "cell_revision": None,
        "incumbent_id": None,
        "dissent": [],
        "artifact_refs": [],
        "rollback_verified": False,
        "cutover_event_id": None,
        "next_route": None,
    }


def _read_ref_value(project: object, ref: Mapping[str, object]) -> JsonObject:
    registered = _registered_artifacts(project.events()).get(str(ref["path"]))
    if not isinstance(registered, Mapping):
        raise ValueError("formal Council artifact is absent from the Project Artifact Bus")
    _verify_registered_file(project, registered)
    return _read_json(
        project.workspace / str(registered["workspace_path"]),
        "formal Council artifact",
    )


def reduce_council_event(
    project: object, state: JsonObject, event: Mapping[str, object]
) -> None:
    event_type = event.get("event_type")
    if not isinstance(event_type, str) or not event_type.startswith("research.council."):
        return
    payload = event["payload"]
    assert isinstance(payload, Mapping)
    for field in FORMAL_EVENT_ARTIFACT_ROLES:
        if field in payload:
            ref = copy.deepcopy(payload[field])
            if ref not in state["artifact_refs"]:
                state["artifact_refs"].append(ref)
    if event_type == "research.council.agenda_compiled":
        agenda = _read_ref_value(project, payload["agenda_ref"])
        state.update(
            status="agenda_compiled",
            agenda_id=agenda.get("agenda_id"),
            agenda_revision=payload.get("agenda_revision"),
            route=None,
            route_decision_id=None,
            cell_id=None,
            cell_revision=None,
            incumbent_id=None,
            dissent=[],
            next_route="select-council-route",
        )
    elif event_type == "research.council.route_selected":
        state.update(
            status="route_selected",
            route=payload.get("route"),
            route_decision_id=payload.get("route_decision_id"),
            next_route=(
                "run-single-controller-fast-path"
                if payload.get("route") == "single_controller_fast_path"
                else "open-bounded-decision-cell"
            ),
        )
    elif event_type == "research.council.cell_opened":
        cell = _read_ref_value(project, payload["cell_ref"])
        state.update(
            status="cell_open",
            cell_id=cell.get("cell_id"),
            cell_revision=payload.get("cell_revision"),
            next_route="dispatch-bounded-workers",
        )
    elif event_type == "research.council.worker_result_registered":
        state.update(status="worker_results_pending", next_route="advance-decision-cell")
    elif event_type == "research.council.cell_advanced":
        update = _read_ref_value(project, payload["cell_update_ref"])
        state.update(
            status=payload.get("to_state"),
            dissent=copy.deepcopy(update.get("dissent") or []),
            next_route=update.get("next_route"),
        )
        incumbent = update.get("incumbent")
        if isinstance(incumbent, Mapping):
            state["incumbent_id"] = incumbent.get("incumbent_id")
    elif event_type == "research.council.fast_path_started":
        state.update(status="fast_path_running", next_route="complete-fast-path")
    elif event_type == "research.council.fast_path_completed":
        state.update(status="closed", next_route="accept-council-outcome")
    elif event_type == "research.council.cell_closed":
        close = _read_ref_value(project, payload["cell_close_ref"])
        state.update(
            status="closed",
            incumbent_id=close.get("incumbent_id"),
            dissent=copy.deepcopy(close.get("dissent") or []),
            next_route="accept-council-outcome",
        )
    elif event_type == "research.council.outcome_accepted":
        state.update(status="accepted", next_route="compile-next-council-agenda")
    elif event_type == "research.council.rollback_verified":
        state.update(rollback_verified=True)
    elif event_type == "research.council.cutover_accepted":
        state.update(
            formal_status="formal_operational_truth",
            cutover_event_id=event.get("event_id"),
        )
    elif event_type == "research.council.rollback_activated":
        state.update(
            status="rolled_back",
            formal_status="single_controller_rollback_active",
            next_route="run-single-controller-rollback",
        )
