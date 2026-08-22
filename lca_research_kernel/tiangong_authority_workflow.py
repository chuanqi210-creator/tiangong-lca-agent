"""Project orchestration for a TianGong-first, read-only authority workflow."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Protocol


JsonObject = Dict[str, object]

_ROOT = Path("tiangong-authority-following")
_ZERO_PROHIBITED_EFFECTS: JsonObject = {
    "remote_write_executed_count": 0,
    "calculation_trigger_executed_count": 0,
    "publication_executed_count": 0,
    "review_submission_executed_count": 0,
    "message_executed_count": 0,
    "role_management_executed_count": 0,
}


class ProjectPort(Protocol):
    """Minimal Project surface used by the TianGong workflow."""

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

    def restore_or_execute_tiangong_native_case(self) -> JsonObject: ...


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


def _artifact_for_bytes(project: ProjectPort, relative: Path, content: bytes) -> JsonObject:
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("artifact path must be workspace-relative")
    destination = project.workspace / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.read_bytes() != content:
        raise RuntimeError(f"content-addressed artifact drifted: {relative}")
    if not destination.exists():
        destination.write_bytes(content)
    return {
        "path": relative.as_posix(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


def _json_artifact(project: ProjectPort, relative: Path, value: object) -> JsonObject:
    return _artifact_for_bytes(project, relative, _json_bytes(value))


def _verify_artifact(project: ProjectPort, artifact: Mapping[str, object]) -> Path:
    relative = artifact.get("path")
    digest = artifact.get("sha256")
    size = artifact.get("bytes")
    if (
        not isinstance(relative, str)
        or Path(relative).is_absolute()
        or ".." in Path(relative).parts
        or not isinstance(digest, str)
        or isinstance(size, bool)
        or not isinstance(size, int)
    ):
        raise RuntimeError("registered TianGong artifact is malformed")
    path = (project.workspace / relative).resolve()
    try:
        path.relative_to(project.workspace.resolve())
    except ValueError as error:
        raise RuntimeError("registered TianGong artifact leaves the workspace") from error
    if not path.is_file():
        raise RuntimeError(f"registered TianGong artifact is missing: {relative}")
    content = path.read_bytes()
    if len(content) != size or hashlib.sha256(content).hexdigest() != digest:
        raise RuntimeError(f"registered TianGong artifact drifted: {relative}")
    return path


def _existing(project: ProjectPort, idempotency_key: str) -> Optional[JsonObject]:
    for event in project.events():
        if event.get("idempotency_key") != idempotency_key:
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            raise RuntimeError("idempotent TianGong event has a malformed payload")
        artifact = payload.get("artifact")
        if isinstance(artifact, dict):
            _verify_artifact(project, artifact)
        return payload
    return None


def _latest_payload(project: ProjectPort, event_type: str) -> JsonObject:
    for event in reversed(project.events()):
        if event.get("event_type") == event_type and isinstance(event.get("payload"), dict):
            return dict(event["payload"])
    raise RuntimeError(f"required TianGong project event is missing: {event_type}")


def _load_event_artifact(project: ProjectPort, event_type: str) -> JsonObject:
    payload = _latest_payload(project, event_type)
    artifact = payload.get("artifact")
    if not isinstance(artifact, dict):
        raise RuntimeError(f"{event_type} has no registered artifact")
    return _read_json(_verify_artifact(project, artifact), event_type)


def _effects(
    *,
    local_reads: int,
    local_writes: int,
    external_reads: int = 0,
    source_external_reads: int = 0,
) -> JsonObject:
    return {
        "local_read_executed_count": local_reads,
        "local_write_executed_count": local_writes,
        "external_read_executed_count": external_reads,
        "source_evidence_external_read_count": source_external_reads,
        **_ZERO_PROHIBITED_EFFECTS,
    }


def _record(
    project: ProjectPort,
    event_type: str,
    payload: JsonObject,
    idempotency_key: str,
) -> JsonObject:
    if sum(key == "next_route" for key in payload) != 1:
        raise ValueError("compact TianGong event must contain exactly one next_route")
    event = project.record(
        event_type,
        payload,
        actor="controller",
        idempotency_key=idempotency_key,
    )
    project.materialize_views()
    return dict(event["payload"])


def _projection_hashes(project: ProjectPort) -> JsonObject:
    result: JsonObject = {}
    for filename in (
        "research-events.jsonl",
        "project-snapshot.json",
        "machine-context.json",
        "human-status.md",
    ):
        path = project.workspace / filename
        if not path.is_file():
            raise RuntimeError(f"required Project projection is missing: {filename}")
        result[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _registered_artifact_tree(project: ProjectPort) -> JsonObject:
    """Verify and hash every content-addressed artifact registered by events."""

    descriptors: Dict[str, JsonObject] = {}

    def visit(value: object) -> None:
        if isinstance(value, dict):
            if {"path", "sha256", "bytes"}.issubset(value):
                path = _verify_artifact(project, value)
                relative = path.relative_to(project.workspace.resolve()).as_posix()
                descriptor = {
                    "path": relative,
                    "sha256": value["sha256"],
                    "bytes": value["bytes"],
                }
                prior = descriptors.get(relative)
                if prior is not None and prior != descriptor:
                    raise RuntimeError(
                        f"registered TianGong artifact identity conflicts: {relative}"
                    )
                descriptors[relative] = descriptor
                return
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for event in project.events():
        visit(event.get("payload"))
    rows = [descriptors[path] for path in sorted(descriptors)]
    return {
        "artifact_count": len(rows),
        "artifacts": rows,
        "tree_sha256": _canonical_digest(rows),
    }


def _authority_receipt_history(project: ProjectPort) -> List[JsonObject]:
    receipts = []
    for event in project.events():
        if event.get("event_type") != "research.tiangong_authority.receipt_created":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict) or not isinstance(payload.get("artifact"), dict):
            raise RuntimeError("registered Authority Receipt event is malformed")
        receipts.append(
            _read_json(
                _verify_artifact(project, payload["artifact"]),
                "Authority Receipt history",
            )
        )
    if not receipts:
        raise RuntimeError("Authority Receipt history is empty")
    return receipts


def _second_case_risks(
    selection: Mapping[str, object],
    binding: Mapping[str, object],
    transition: Mapping[str, object],
) -> List[JsonObject]:
    risks: List[JsonObject] = []
    candidate = selection.get("selected_candidate")
    retained = candidate.get("retained_risks") if isinstance(candidate, dict) else None
    if isinstance(retained, list):
        for index, value in enumerate(retained):
            if not isinstance(value, dict):
                continue
            scope = str(value.get("scope") or f"retained-{index + 1}")
            if scope == "whole_snapshot":
                risk_id = "whole-snapshot-readiness-failed"
                severity = "high"
                summary = (
                    "The whole native snapshot failed matrix readiness; the selected "
                    "root passed only its own fail-closed gates."
                )
            elif scope == "candidate_provider_weights":
                risk_id = "provider-weight-vector-not-exposed"
                severity = "high"
                summary = (
                    "Provider identities are exact but the resolved numeric Provider "
                    "weight vector is not exposed for independent matrix reconstruction."
                )
            else:
                risk_id = f"selection-retained-{_canonical_digest(value)[:12]}"
                severity = "medium"
                summary = str(value.get("interpretation") or value)
            risks.append(
                {
                    "risk_id": risk_id,
                    "severity": severity,
                    "summary": summary,
                    "evidence": [
                        f"selection#/selected_candidate/retained_risks/{index}"
                    ],
                }
            )
    missing = binding.get("missing_data")
    if isinstance(missing, list):
        for index, value in enumerate(missing):
            if not isinstance(value, str) or not value.strip():
                continue
            risk_id = f"native-case-missing-{_canonical_digest(value)[:12]}"
            if "weight vector" in value.lower():
                risk_id = "provider-weight-vector-not-exposed"
            elif "matrix readiness" in value.lower():
                risk_id = "whole-snapshot-readiness-failed"
            if any(row["risk_id"] == risk_id for row in risks):
                continue
            risks.append(
                {
                    "risk_id": risk_id,
                    "severity": "high",
                    "summary": value.rstrip("."),
                    "evidence": [f"native-case-binding#/missing_data/{index}"],
                }
            )
    if transition.get("transition_evidence_kind") == "synthetic_binding_canary":
        risks.append(
            {
                "risk_id": "no-real-official-version-pair",
                "severity": "medium",
                "summary": (
                    "Only one official selected version was available; transition "
                    "precision is demonstrated by an isolated synthetic canary."
                ),
                "evidence": ["transition#/transition_evidence_kind"],
            }
        )
    if not risks:
        risks.append(
            {
                "risk_id": "single-case-screening-claim-boundary",
                "severity": "medium",
                "summary": "Evidence supports one screening-level case, not general LCA maturity.",
                "evidence": ["native-case-binding#/claim_boundary"],
            }
        )
    return risks


def adopt_tiangong_capabilities(
    project: ProjectPort, observation_path: Path
) -> JsonObject:
    """Compile, register, and project one exact TianGong capability adoption."""

    from .tiangong_capability_adoption import (
        compile_tiangong_capability_adoption,
        verify_tiangong_capability_adoption,
    )

    observation = _read_json(observation_path, "TianGong capability observation")
    adoption = verify_tiangong_capability_adoption(
        compile_tiangong_capability_adoption(observation)
    )
    digest = str(adoption["adoption_content_sha256"])
    idempotency_key = f"tiangong-capability-adoption:{digest}"
    replay = _existing(project, idempotency_key)
    if replay is not None:
        project.materialize_views()
        return replay
    artifact = _json_artifact(
        project,
        _ROOT / "capability" / f"adoption-{adoption['adoption_id']}.json",
        adoption,
    )
    payload: JsonObject = {
        "status": adoption["status"],
        "adoption_id": adoption["adoption_id"],
        "adoption_content_sha256": digest,
        "current_package_spec": adoption["current_package_spec"],
        "current_version": adoption["current_version"],
        "historical_package_spec": adoption["historical_package_spec"],
        "direct_lcia_command_available": adoption["direct_lcia_command_available"],
        "credential_status": adoption["credential_status"],
        "artifact": artifact,
        "effects": _effects(local_reads=1, local_writes=1),
        "next_route": "capture-tiangong-open-data",
    }
    return _record(
        project,
        "research.tiangong_capabilities.adopted",
        payload,
        idempotency_key,
    )


def _tree_manifest(root: Path) -> List[JsonObject]:
    rows = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        content = path.read_bytes()
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
        )
    return rows


def capture_tiangong_open_data(
    project: ProjectPort, export_path: Optional[Path] = None
) -> JsonObject:
    """Run one bounded read/export inspection and register its immutable report."""

    from .tiangong_read_adapter import (
        capture_published_process_catalog,
        inspect_official_tidas_export,
    )

    adoption = _load_event_artifact(
        project, "research.tiangong_capabilities.adopted"
    )
    export_digest = "remote-catalog"
    if export_path is not None:
        export_file = Path(export_path).resolve()
        if not export_file.is_file():
            raise FileNotFoundError(f"official TianGong export is missing: {export_file}")
        export_digest = hashlib.sha256(export_file.read_bytes()).hexdigest()
    input_digest = _canonical_digest(
        {
            "adoption": adoption["adoption_content_sha256"],
            "mode": "official_export" if export_path is not None else "published_catalog",
            "export_sha256": export_digest,
        }
    )
    staging_parent = project.workspace / _ROOT / ".staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = staging_parent / f"capture-{input_digest[:16]}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        if export_path is None:
            report = capture_published_process_catalog(
                package_spec=str(adoption["current_package_spec"]),
                output_dir=staging,
                environment=dict(os.environ),
            )
        else:
            report = inspect_official_tidas_export(
                package_spec=str(adoption["current_package_spec"]),
                export_path=Path(export_path),
                output_dir=staging,
            )
        manifest = _tree_manifest(staging)
        tree_digest = _canonical_digest(manifest)
        destination_relative = _ROOT / "catalog" / f"capture-{tree_digest[:16]}"
        destination = project.workspace / destination_relative
        if destination.exists():
            if _tree_manifest(destination) != manifest:
                raise RuntimeError("content-addressed TianGong capture tree drifted")
            shutil.rmtree(staging)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, destination)
        report_path = destination / "capture-report.json"
        artifact = _verify_capture_report(project, report_path)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    remote_reads = int(report.get("remote_read_executed_count", 0))
    idempotency_key = f"tiangong-open-data-capture:{tree_digest}"
    replay = _existing(project, idempotency_key)
    if replay is not None:
        project.materialize_views()
        return replay
    payload: JsonObject = {
        "artifact_id": f"tiangong-open-data-capture-{tree_digest[:16]}",
        "kind": "tiangong_open_data_capture",
        "status": report["status"],
        "package_spec": report["package_spec"],
        "tree_sha256": tree_digest,
        "tree_artifact_count": len(manifest),
        "artifact": artifact,
        "effects": _effects(
            local_reads=1 + int(export_path is not None),
            local_writes=len(manifest),
            external_reads=remote_reads,
        ),
        "next_route": str(report["next_route"]),
    }
    return _record(project, "artifact.registered", payload, idempotency_key)


def _verify_capture_report(project: ProjectPort, path: Path) -> JsonObject:
    content = path.read_bytes()
    relative = path.resolve().relative_to(project.workspace.resolve())
    return {
        "path": relative.as_posix(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


def _find_previous_fingerprint(project: ProjectPort, catalog_path: Path) -> Path:
    candidates = [
        Path(catalog_path).resolve().parent / "previous-case-fingerprint.json",
        Path(catalog_path).resolve().parent.parent / "previous-case-fingerprint.json",
        project.workspace / _ROOT / "catalog" / "previous-case-fingerprint.json",
        project.workspace / "catalog" / "previous-case-fingerprint.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "previous-case-fingerprint.json must be beside the catalog or in the Project catalog"
    )


def select_tiangong_open_data_case(
    project: ProjectPort, catalog_path: Path, policy_path: Path
) -> JsonObject:
    """Compile and register the deterministic nine-gate Open Data selection."""

    from .tiangong_open_data_selection import compile_open_data_case_selection

    catalog = _read_json(catalog_path, "TianGong catalog observation")
    policy = _read_json(policy_path, "TianGong selection policy")
    previous_path = _find_previous_fingerprint(project, catalog_path)
    previous = _read_json(previous_path, "previous case fingerprint")
    input_digest = _canonical_digest(
        {"catalog": catalog, "policy": policy, "previous_case": previous}
    )
    idempotency_key = f"tiangong-open-data-selection:{input_digest}"
    replay = _existing(project, idempotency_key)
    if replay is not None:
        project.materialize_views()
        return replay
    selection = compile_open_data_case_selection(
        catalog_observation=catalog,
        selection_policy=policy,
        previous_case_fingerprint=previous,
    )
    artifact = _json_artifact(
        project,
        _ROOT / "catalog" / f"selection-{selection['selection_id']}.json",
        selection,
    )
    selected = selection.get("selected_candidate")
    selected_row = selected if isinstance(selected, dict) else {}
    dataset = selected_row.get("dataset")
    dataset_row = dataset if isinstance(dataset, dict) else {}
    payload: JsonObject = {
        "status": selection["status"],
        "selection_id": selection["selection_id"],
        "selected_candidate_id": selected_row.get("candidate_id"),
        "dataset_id": dataset_row.get("id"),
        "dataset_version": dataset_row.get("version"),
        "candidate_count": selection["candidate_count"],
        "eligible_candidate_count": selection["eligible_candidate_count"],
        "admission_criteria_lowered": False,
        "artifact": artifact,
        "effects": _effects(local_reads=3, local_writes=1),
        "next_route": selection["next_route"],
    }
    return _record(
        project,
        "research.tiangong_open_data_case.selected",
        payload,
        idempotency_key,
    )


def _receipt_from_pointer(project: ProjectPort, pointer_path: Path) -> JsonObject:
    pointer = _read_json(pointer_path, "TianGong Authority Receipt pointer")
    receipt_id = pointer.get("receipt_id")
    if not isinstance(receipt_id, str) or not receipt_id:
        raise ValueError("Authority Receipt pointer has no receipt_id")
    local = pointer_path.parent / f"receipt-{receipt_id}.json"
    receipt = _read_json(local, "TianGong Authority Receipt")
    content = local.read_bytes()
    if (
        pointer.get("sha256") != hashlib.sha256(content).hexdigest()
        or pointer.get("bytes") != len(content)
    ):
        raise ValueError("Authority Receipt pointer does not match local receipt bytes")
    return receipt


def _resolve_receipt(project: ProjectPort, case: Mapping[str, object]) -> JsonObject:
    from .tiangong_authority import verify_authority_receipt

    embedded = case.get("authority_receipt")
    if isinstance(embedded, dict):
        return verify_authority_receipt(embedded)
    try:
        return verify_authority_receipt(
            _load_event_artifact(project, "research.tiangong_authority.receipt_created")
        )
    except RuntimeError:
        pass
    pointers = [
        project.workspace / "authority" / "current-receipt-pointer.json",
        project.workspace / _ROOT / "receipts" / "current-receipt-pointer.json",
    ]
    for pointer in pointers:
        if pointer.is_file():
            return verify_authority_receipt(_receipt_from_pointer(project, pointer))
    raise RuntimeError(
        "no verified Authority Receipt is embedded, registered, or locally pointed"
    )


def bind_tiangong_native_case(
    project: ProjectPort, case_input_path: Path, result_input_path: Path
) -> JsonObject:
    """Bind exact selected scope and TianGong-native results under one receipt."""

    from .tiangong_authority import verify_authority_receipt
    from .tiangong_native_case import (
        compile_native_case_binding,
        verify_native_case_binding,
    )

    selection = _load_event_artifact(
        project, "research.tiangong_open_data_case.selected"
    )
    if selection.get("status") != "selected":
        raise RuntimeError("TianGong native case cannot bind without an accepted selection")
    case = _read_json(case_input_path, "TianGong native case observation")
    result = _read_json(result_input_path, "TianGong native result observation")
    receipt = verify_authority_receipt(_resolve_receipt(project, case))
    case_for_compiler = dict(case)
    case_for_compiler.pop("authority_receipt", None)
    binding = verify_native_case_binding(
        compile_native_case_binding(
            selection=selection,
            authority_receipt=receipt,
            case_observation=case_for_compiler,
            native_result_observation=result,
        ),
        receipt,
    )
    input_digest = _canonical_digest(
        {
            "selection_id": selection["selection_id"],
            "receipt_sha256": receipt["receipt_content_sha256"],
            "case": case_for_compiler,
            "result": result,
        }
    )
    binding_key = f"tiangong-native-case-binding:{input_digest}"
    replay = _existing(project, binding_key)
    if replay is not None:
        project.materialize_views()
        return replay

    receipt_artifact = _json_artifact(
        project,
        _ROOT / "receipts" / f"receipt-{receipt['receipt_id']}.json",
        receipt,
    )
    receipt_payload: JsonObject = {
        "status": receipt["status"],
        "receipt_id": receipt["receipt_id"],
        "receipt_content_sha256": receipt["receipt_content_sha256"],
        "dataset_id": receipt["authority"]["dataset"]["id"],
        "dataset_version": receipt["authority"]["dataset"]["version"],
        "artifact": receipt_artifact,
        "effects": _effects(local_reads=1, local_writes=1),
        "next_route": "bind-selected-tiangong-open-data-case",
    }
    _record(
        project,
        "research.tiangong_authority.receipt_created",
        receipt_payload,
        f"tiangong-authority-receipt:{receipt['receipt_content_sha256']}",
    )

    binding_artifact = _json_artifact(
        project,
        _ROOT / "case" / f"binding-{binding['binding_id']}.json",
        binding,
    )
    result_categories = binding["native_result"]["categories"]
    primary = next(
        (
            row
            for row in result_categories
            if row.get("method_id") == binding["lcia_method"]["id"]
            and row.get("method_version") == binding["lcia_method"]["version"]
        ),
        result_categories[0],
    )
    source_reads = int(binding.get("remote_read_executed_count", 0))
    binding_payload: JsonObject = {
        "status": binding["status"],
        "binding_id": binding["binding_id"],
        "binding_content_sha256": binding["binding_content_sha256"],
        "selection_id": binding["selection_id"],
        "receipt_id": binding["authority_receipt_id"],
        "candidate_id": binding["candidate_id"],
        "dataset_id": binding["model"]["dataset"]["id"],
        "dataset_version": binding["model"]["dataset"]["version"],
        "functional_unit": binding["scope"]["functional_unit"],
        "system_boundary": binding["scope"]["system_boundary"],
        "lcia_method": binding["lcia_method"],
        "primary_result": {
            "name": primary["name"],
            "value": primary["original_value"],
            "unit": primary["original_unit"],
        },
        "native_result_origin": binding["native_result"]["origin"],
        "provider_edge_count": len(binding["model"]["edges"]),
        "artifact": binding_artifact,
        "effects": _effects(
            local_reads=4,
            local_writes=1,
            source_external_reads=source_reads,
        ),
        "next_route": "prove-precise-tiangong-authority-transition",
    }
    return _record(
        project,
        "research.tiangong_native_case.accepted",
        binding_payload,
        binding_key,
    )


def restore_or_execute_tiangong_native_case(project: ProjectPort) -> JsonObject:
    """Restore and verify the current case; execute only when no binding exists."""

    from .tiangong_authority import verify_authority_receipt
    from .tiangong_native_case import verify_native_case_binding

    try:
        payload = _latest_payload(project, "research.tiangong_native_case.accepted")
    except RuntimeError:
        case_candidates = [
            project.workspace / "native-case" / "case-observation.json",
            project.workspace / _ROOT / "case" / "case-observation.json",
        ]
        result_candidates = [
            project.workspace / "native-case" / "native-result-observation.json",
            project.workspace / _ROOT / "case" / "native-result-observation.json",
        ]
        case_path = next((path for path in case_candidates if path.is_file()), None)
        result_path = next((path for path in result_candidates if path.is_file()), None)
        if case_path is None or result_path is None:
            raise RuntimeError(
                "no accepted binding and no local native case/result observations to execute"
            )
        return bind_tiangong_native_case(project, case_path, result_path)

    artifact = payload.get("artifact")
    if not isinstance(artifact, dict):
        raise RuntimeError("accepted native case has no binding artifact")
    binding = _read_json(_verify_artifact(project, artifact), "native case binding")
    receipt = verify_authority_receipt(
        _load_event_artifact(project, "research.tiangong_authority.receipt_created")
    )
    verify_native_case_binding(binding, receipt)
    project.materialize_views()
    return payload


def _request_artifact(
    project: ProjectPort, request: Mapping[str, object], field: str
) -> Path:
    binding = request.get(field)
    if not isinstance(binding, dict):
        raise ValueError(f"transition request {field} binding is required")
    return _verify_artifact(project, binding)


def _synthetic_rebuild_execution(transition: Mapping[str, object]) -> JsonObject:
    """Execute deterministic canary recomputations without touching current authority."""

    actions = transition.get("rebuild_actions")
    if not isinstance(actions, list):
        raise ValueError("transition rebuild actions are malformed")
    rows = []
    for expected_order, value in enumerate(actions, start=1):
        if not isinstance(value, dict) or value.get("order") != expected_order:
            raise ValueError("transition rebuild action order is invalid")
        node_id = value.get("node_id")
        action = value.get("rebuild_action")
        if not isinstance(node_id, str) or not isinstance(action, str):
            raise ValueError("transition rebuild action is incomplete")
        recomputed = {
            "node_id": node_id,
            "rebuild_action": action,
            "candidate_receipt_id": transition["current_receipt_id"],
            "candidate_receipt_sha256": transition["current_receipt_sha256"],
            "previous_artifact": value.get("previous_artifact"),
            "depends_on_nodes": value.get("depends_on_nodes"),
            "canary_only": True,
        }
        rows.append(
            {
                "order": expected_order,
                **recomputed,
                "status": "synthetic_canary_recomputed",
                "recomputed_artifact_sha256": _canonical_digest(recomputed),
            }
        )
    return {
        "contract": "lca_research_kernel.tiangong_authority_rebuild_execution/v1",
        "status": "passed",
        "transition_id": transition["transition_id"],
        "transition_evidence_kind": "synthetic_binding_canary",
        "official_update_claim": False,
        "executed_actions": rows,
        "executed_action_count": len(rows),
        "unplanned_action_count": 0,
        "all_rebuild_actions_passed": True,
        "current_authority_advanced": False,
        "remote_read_executed_count": 0,
        "remote_write_executed_count": 0,
        "calculation_trigger_executed_count": 0,
        "publication_executed_count": 0,
    }


def transition_tiangong_authority(
    project: ProjectPort, transition_input_path: Path
) -> JsonObject:
    """Compile one exact transition and execute its bounded rebuild route."""

    from .tiangong_authority_transition import (
        compile_authority_transition,
        verify_authority_transition,
    )

    request = _read_json(
        transition_input_path, "TianGong authority transition request"
    )
    if (
        request.get("contract")
        != "lca_research_kernel.tiangong_authority_transition_request/v1"
    ):
        raise ValueError("TianGong authority transition request contract is unsupported")
    previous = _read_json(
        _request_artifact(project, request, "previous_receipt"),
        "previous Authority Receipt",
    )
    current = _read_json(
        _request_artifact(project, request, "current_receipt"),
        "current Authority Receipt candidate",
    )
    graph = _read_json(
        _request_artifact(project, request, "dependency_graph"),
        "TianGong authority dependency graph",
    )
    registered_previous = _load_event_artifact(
        project, "research.tiangong_authority.receipt_created"
    )
    if previous.get("receipt_id") != registered_previous.get("receipt_id"):
        raise ValueError(
            "transition previous receipt is not the Project current Authority Receipt"
        )
    transition = verify_authority_transition(
        compile_authority_transition(
            previous_receipt=previous,
            current_receipt=current,
            dependency_graph=graph,
            transition_evidence_kind=str(request.get("transition_evidence_kind", "")),
        ),
        graph,
    )
    if transition["status"] == "no_change":
        project.materialize_views()
        return {
            "status": "no_change",
            "transition_id": transition["transition_id"],
            "event_recorded": False,
            "rebuild_action_count": 0,
            "all_rebuild_actions_passed": True,
            "current_receipt_id": previous["receipt_id"],
            "candidate_receipt_id": transition["current_receipt_id"],
            "official_update_claim": transition["official_update_claim"],
            "next_route": "assemble-tiangong-second-case-package",
        }

    evidence_kind = str(transition["transition_evidence_kind"])
    if evidence_kind == "synthetic_binding_canary":
        execution = _synthetic_rebuild_execution(transition)
        relative_root = _ROOT / "transitions" / "canary" / str(
            transition["transition_id"]
        )
        current_receipt_id = previous["receipt_id"]
        current_dataset_version = previous["authority"]["dataset"]["version"]
    else:
        if transition["rebuild_actions"]:
            raise RuntimeError(
                "official transition requires real node rebuild executors and passing outputs"
            )
        execution = {
            "contract": "lca_research_kernel.tiangong_authority_rebuild_execution/v1",
            "status": "passed",
            "transition_id": transition["transition_id"],
            "transition_evidence_kind": "official_version_pair",
            "official_update_claim": True,
            "executed_actions": [],
            "executed_action_count": 0,
            "unplanned_action_count": 0,
            "all_rebuild_actions_passed": True,
            "current_authority_advanced": True,
            "remote_read_executed_count": 0,
            "remote_write_executed_count": 0,
            "calculation_trigger_executed_count": 0,
            "publication_executed_count": 0,
        }
        relative_root = _ROOT / "transitions" / "official" / str(
            transition["transition_id"]
        )
        current_receipt_id = transition["current_receipt_id"]
        current_dataset_version = transition["current_receipt"]["authority"][
            "dataset"
        ]["version"]

    transition_artifact = _json_artifact(
        project, relative_root / "transition.json", transition
    )
    execution_artifact = _json_artifact(
        project, relative_root / "rebuild-execution.json", execution
    )
    idempotency_key = (
        f"tiangong-authority-transition:{transition['transition_content_sha256']}"
    )
    replay = _existing(project, idempotency_key)
    if replay is not None:
        registered_execution = replay.get("execution_artifact")
        if not isinstance(registered_execution, dict):
            raise RuntimeError("transition replay has no execution artifact")
        _verify_artifact(project, registered_execution)
        project.materialize_views()
        return replay
    payload: JsonObject = {
        "status": (
            "synthetic_canary_transition_completed"
            if evidence_kind == "synthetic_binding_canary"
            else "official_transition_completed"
        ),
        "transition_status": transition["status"],
        "transition_id": transition["transition_id"],
        "transition_content_sha256": transition["transition_content_sha256"],
        "transition_evidence_kind": evidence_kind,
        "official_update_claim": transition["official_update_claim"],
        "previous_receipt_id": previous["receipt_id"],
        "candidate_receipt_id": transition["current_receipt_id"],
        "current_receipt_id": current_receipt_id,
        "current_dataset_version": current_dataset_version,
        "change_classes": transition["change_classes"],
        "directly_invalidated_nodes": transition["directly_invalidated_nodes"],
        "invalidated_nodes": transition["invalidated_nodes"],
        "unaffected_nodes": transition["unaffected_nodes"],
        "rebuild_action_count": execution["executed_action_count"],
        "all_rebuild_actions_passed": execution["all_rebuild_actions_passed"],
        "current_authority_advanced": execution["current_authority_advanced"],
        "artifact": transition_artifact,
        "execution_artifact": execution_artifact,
        "effects": _effects(local_reads=4, local_writes=2),
        "next_route": "assemble-tiangong-second-case-package",
    }
    return _record(
        project,
        "research.tiangong_authority.transitioned",
        payload,
        idempotency_key,
    )


def assemble_tiangong_second_case_package(project: ProjectPort) -> JsonObject:
    """Assemble and register the deterministic authority-following case package."""

    from .tiangong_capability_adoption import verify_tiangong_capability_adoption
    from .tiangong_native_case import verify_native_case_binding
    from .tiangong_second_case_package import (
        build_tiangong_second_case_deliverables,
    )

    adoption = verify_tiangong_capability_adoption(
        _load_event_artifact(project, "research.tiangong_capabilities.adopted")
    )
    selection = _load_event_artifact(
        project, "research.tiangong_open_data_case.selected"
    )
    receipt_history = _authority_receipt_history(project)
    current_receipt = receipt_history[-1]
    binding = verify_native_case_binding(
        _load_event_artifact(project, "research.tiangong_native_case.accepted"),
        current_receipt,
    )
    transition_payload = _latest_payload(
        project, "research.tiangong_authority.transitioned"
    )
    transition_artifact = transition_payload.get("artifact")
    execution_artifact = transition_payload.get("execution_artifact")
    if not isinstance(transition_artifact, dict) or not isinstance(
        execution_artifact, dict
    ):
        raise RuntimeError("authority transition evidence is incomplete")
    transition = _read_json(
        _verify_artifact(project, transition_artifact), "authority transition"
    )
    execution = _read_json(
        _verify_artifact(project, execution_artifact),
        "authority rebuild execution",
    )
    transition_evidence = {
        "contract": "lca_research_kernel.tiangong_authority_transition_evidence/v1",
        "transition": transition,
        "execution": execution,
    }
    input_identity = {
        "adoption_id": adoption["adoption_id"],
        "selection_id": selection["selection_id"],
        "current_receipt_id": current_receipt["receipt_id"],
        "binding_id": binding["binding_id"],
        "transition_id": transition["transition_id"],
    }

    try:
        existing = _latest_payload(
            project, "research.tiangong_second_case_package.assembled"
        )
    except RuntimeError:
        existing = None
    if existing is not None and existing.get("input_identity") == input_identity:
        deliverables = existing.get("deliverables")
        if not isinstance(deliverables, dict) or len(deliverables) != 8:
            raise RuntimeError("registered second-case package is incomplete")
        for artifact in deliverables.values():
            if not isinstance(artifact, dict):
                raise RuntimeError("registered second-case deliverable is malformed")
            _verify_artifact(project, artifact)
        count_before = len(project.events())
        projections_before = _projection_hashes(project)
        project.materialize_views()
        if len(project.events()) != count_before or _projection_hashes(
            project
        ) != projections_before:
            raise RuntimeError("same-version package replay changed Project state")
        return existing

    project.materialize_views()
    event_count_before = len(project.events())
    projections_before = _projection_hashes(project)
    tree_before = _registered_artifact_tree(project)
    identity_before = copy.deepcopy(input_identity)

    restored = project.restore_or_execute_tiangong_native_case()
    if restored.get("binding_id") != binding["binding_id"]:
        raise RuntimeError("same-version native case restore changed binding identity")
    event_count_after = len(project.events())
    projections_after = _projection_hashes(project)
    tree_after = _registered_artifact_tree(project)
    if (
        event_count_before != event_count_after
        or projections_before != projections_after
        or tree_before != tree_after
        or input_identity != identity_before
    ):
        raise RuntimeError("same-version replay changed Project evidence or identity")

    replay_evidence: JsonObject = {
        "contract": "lca_research_kernel.tiangong_same_version_replay/v1",
        "status": "passed",
        "same_version_replay": True,
        "event_count_before": event_count_before,
        "event_count_after": event_count_after,
        "identities_unchanged": True,
        "input_identity": input_identity,
        "registered_artifact_count": tree_before["artifact_count"],
        "registered_artifact_tree_sha256_before": tree_before["tree_sha256"],
        "registered_artifact_tree_sha256_after": tree_after["tree_sha256"],
        "project_projection_hashes_before": projections_before,
        "project_projection_hashes_after": projections_after,
        "package_compilation_replayed": True,
        "remote_read_executed_count": 0,
        "remote_write_executed_count": 0,
        "calculation_trigger_executed_count": 0,
        "publication_executed_count": 0,
    }
    risks = _second_case_risks(selection, binding, transition)
    compile_inputs = {
        "capability_adoption": adoption,
        "selection": selection,
        "authority_history": receipt_history,
        "native_case_binding": binding,
        "transition": transition_evidence,
        "replay_evidence": replay_evidence,
        "risks": risks,
    }
    package = build_tiangong_second_case_deliverables(**compile_inputs)
    if build_tiangong_second_case_deliverables(**compile_inputs) != package:
        raise RuntimeError("same-input second-case package compilation was not stable")

    relative_root = _ROOT / "final" / f"package-{package['package_id']}"
    registered: Dict[str, JsonObject] = {}
    for filename, content in sorted(package["deliverables"].items()):
        relative = relative_root / filename
        if isinstance(content, str):
            artifact = _artifact_for_bytes(project, relative, content.encode("utf-8"))
        else:
            artifact = _json_artifact(project, relative, content)
        registered[filename] = artifact
    manifest_artifact = registered["package-manifest.json"]
    payload: JsonObject = {
        "status": package["status"],
        "package_id": package["package_id"],
        "package_content_sha256": package["package_content_sha256"],
        "deliverable_count": package["deliverable_count"],
        "validation_status": package["deliverables"]["validation.json"]["status"],
        "same_version_replay": True,
        "input_identity": input_identity,
        "current_receipt_id": current_receipt["receipt_id"],
        "binding_id": binding["binding_id"],
        "transition_id": transition["transition_id"],
        "artifact": manifest_artifact,
        "deliverables": registered,
        "human_final_report_path": registered["human-final-report.md"]["path"],
        "machine_final_state_path": registered["machine-final-state.json"]["path"],
        "risk_feedback_register_path": registered[
            "risk-feedback-register.json"
        ]["path"],
        "effects": _effects(local_reads=8, local_writes=8),
        "next_route": package["next_route"],
    }
    return _record(
        project,
        "research.tiangong_second_case_package.assembled",
        payload,
        f"tiangong-second-case-package:{package['package_content_sha256']}",
    )


def accept_tiangong_second_case_package_read_only(
    project: ProjectPort,
) -> JsonObject:
    """Run the pure package audit in a credential-free subprocess, then register it."""

    package_payload = _latest_payload(
        project, "research.tiangong_second_case_package.assembled"
    )
    package_id = package_payload.get("package_id")
    deliverables = package_payload.get("deliverables")
    if not isinstance(package_id, str) or not isinstance(deliverables, dict):
        raise RuntimeError("assembled second-case package event is malformed")
    package_parents = set()
    for artifact in deliverables.values():
        if not isinstance(artifact, dict):
            raise RuntimeError("assembled second-case deliverable is malformed")
        package_parents.add(_verify_artifact(project, artifact).parent)
    if len(package_parents) != 1:
        raise RuntimeError("assembled second-case deliverables do not share one root")
    package_root = package_parents.pop()

    try:
        existing = _latest_payload(
            project, "research.tiangong_second_case_package.accepted"
        )
    except RuntimeError:
        existing = None
    if existing is not None and existing.get("package_id") == package_id:
        artifact = existing.get("artifact")
        if not isinstance(artifact, dict):
            raise RuntimeError("registered independent acceptance artifact is missing")
        report = _read_json(
            _verify_artifact(project, artifact), "independent acceptance report"
        )
        if (
            report.get("acceptance_id") != existing.get("acceptance_id")
            or report.get("status") != "accepted"
        ):
            raise RuntimeError("registered independent acceptance identity drifted")
        count_before = len(project.events())
        projections_before = _projection_hashes(project)
        project.materialize_views()
        if len(project.events()) != count_before or _projection_hashes(
            project
        ) != projections_before:
            raise RuntimeError("independent acceptance replay changed Project state")
        return existing

    clean_environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8",
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "lca_research_kernel.tiangong_second_case_acceptance",
            "--package-root",
            str(package_root),
            "--project-workspace",
            str(project.workspace),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=clean_environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()
        suffix = detail[-1] if detail else "unknown clean-audit failure"
        raise RuntimeError(f"clean-context TianGong acceptance failed: {suffix}")
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("clean-context acceptance returned malformed JSON") from error
    if not isinstance(report, dict) or report.get("status") != "accepted":
        raise RuntimeError("clean-context acceptance did not return accepted")
    if (
        report.get("package_id") != package_id
        or report.get("next_route")
        != "complete-tiangong-authority-following-second-case-goal"
    ):
        raise RuntimeError("clean-context acceptance does not join the current package")
    for field in (
        "network_read_executed_count",
        "project_write_executed_count",
        "recalculation_executed_count",
        "remote_write_executed_count",
        "publication_executed_count",
        "review_submission_executed_count",
        "contact_executed_count",
        "message_executed_count",
    ):
        if report.get(field) != 0:
            raise RuntimeError(f"independent acceptance effect is nonzero: {field}")
    acceptance_id = report.get("acceptance_id")
    acceptance_digest = report.get("acceptance_content_sha256")
    if not isinstance(acceptance_id, str) or not isinstance(acceptance_digest, str):
        raise RuntimeError("clean-context acceptance identity is missing")
    artifact = _json_artifact(
        project,
        _ROOT / "acceptance" / f"acceptance-{acceptance_id}.json",
        report,
    )
    payload: JsonObject = {
        "status": "accepted",
        "acceptance_id": acceptance_id,
        "acceptance_content_sha256": acceptance_digest,
        "package_id": package_id,
        "package_content_sha256": package_payload["package_content_sha256"],
        "byte_for_byte_rebuild": report["byte_for_byte_rebuild"],
        "rebuilt_deliverable_count": report["rebuilt_deliverable_count"],
        "source_project_tree_unchanged": report["source_project_tree_unchanged"],
        "clean_context_tree_sha256": report[
            "clean_context_tree_sha256_after"
        ],
        "artifact": artifact,
        "effects": _effects(local_reads=1, local_writes=1),
        "next_route": report["next_route"],
    }
    return _record(
        project,
        "research.tiangong_second_case_package.accepted",
        payload,
        f"tiangong-second-case-acceptance:{acceptance_digest}",
    )
