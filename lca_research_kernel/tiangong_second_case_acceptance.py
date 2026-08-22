"""Network-free, clean-context acceptance for a TianGong second-case package."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from .tiangong_second_case_package import (
    build_tiangong_second_case_deliverables,
)


JsonObject = Dict[str, object]

ACCEPTANCE_CONTRACT = "lca_research_kernel.tiangong_second_case_acceptance/v1"
_PROJECTIONS = (
    "research-events.jsonl",
    "project-snapshot.json",
    "machine-context.json",
    "human-status.md",
)
_UPSTREAM_ROLES = {
    "research.tiangong_capabilities.adopted": ("artifact",),
    "research.tiangong_open_data_case.selected": ("artifact",),
    "research.tiangong_authority.receipt_created": ("artifact",),
    "research.tiangong_native_case.accepted": ("artifact",),
    "research.tiangong_authority.transitioned": (
        "artifact",
        "execution_artifact",
    ),
}
_PROHIBITED_EFFECTS = (
    "remote_write_executed_count",
    "calculation_trigger_executed_count",
    "publication_executed_count",
    "review_submission_executed_count",
    "contact_executed_count",
    "message_executed_count",
)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _read_json(path: Path, label: str) -> JsonObject:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} must be a readable UTF-8 JSON object") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _read_events(path: Path) -> List[JsonObject]:
    events = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ValueError("Project event projection is unreadable") from error
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"Project event projection is invalid at line {line_number}"
            ) from error
        if not isinstance(value, dict):
            raise ValueError("Project event projection contains a non-object event")
        events.append(value)
    return events


def _artifact_descriptor(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} artifact descriptor is missing")
    path = value.get("path")
    digest = value.get("sha256")
    size = value.get("bytes")
    if (
        not isinstance(path, str)
        or not path
        or Path(path).is_absolute()
        or ".." in Path(path).parts
        or not isinstance(digest, str)
        or len(digest) != 64
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size < 0
    ):
        raise ValueError(f"{label} artifact descriptor is malformed")
    return {"path": path, "sha256": digest, "bytes": size}


def _verify_file(path: Path, descriptor: Mapping[str, object], label: str) -> bytes:
    if not path.is_file():
        raise RuntimeError(f"{label} is missing: {descriptor.get('path')}")
    content = path.read_bytes()
    if len(content) != descriptor.get("bytes") or hashlib.sha256(
        content
    ).hexdigest() != descriptor.get("sha256"):
        raise RuntimeError(f"{label} content identity drifted: {descriptor.get('path')}")
    return content


def _resolve_project_relative(workspace: Path, relative: str) -> Path:
    token = Path(relative)
    if token.is_absolute() or ".." in token.parts:
        raise ValueError("source binding path must be normalized and relative")
    for base in (workspace, *workspace.parents):
        candidate = (base / token).resolve()
        try:
            candidate.relative_to(base.resolve())
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    raise RuntimeError(f"source binding cannot be reopened: {relative}")


def _binding_descriptors(value: object) -> List[JsonObject]:
    rows: List[JsonObject] = []

    def collect_bindings(child: object, label: str) -> None:
        if isinstance(child, dict):
            if "path" in child or "sha256" in child:
                path = child.get("path")
                digest = child.get("sha256")
                size = child.get("bytes")
                if (
                    not isinstance(path, str)
                    or not path
                    or Path(path).is_absolute()
                    or ".." in Path(path).parts
                    or not isinstance(digest, str)
                    or len(digest) != 64
                    or (
                        size is not None
                        and (isinstance(size, bool) or not isinstance(size, int))
                    )
                ):
                    raise ValueError(f"{label} source binding is malformed")
                row: JsonObject = {"path": path, "sha256": digest}
                if size is not None:
                    row["bytes"] = size
                rows.append(row)
                return
            for nested in child.values():
                collect_bindings(nested, label)
        elif isinstance(child, list):
            for nested in child:
                collect_bindings(nested, label)

    def visit(child: object) -> None:
        if isinstance(child, dict):
            for key, nested in child.items():
                if key == "source_bindings" or key.endswith("_pointer"):
                    collect_bindings(nested, key)
                else:
                    visit(nested)
        elif isinstance(child, list):
            for nested in child:
                visit(nested)

    visit(value)
    by_identity = {
        (str(row["path"]), str(row["sha256"])): row for row in rows
    }
    return [by_identity[key] for key in sorted(by_identity)]


def _tree_digest(files: Sequence[Tuple[str, Path]]) -> Tuple[str, List[JsonObject]]:
    rows = []
    for logical_path, path in sorted(files, key=lambda item: item[0]):
        if not path.is_file():
            raise RuntimeError(f"accepted source file disappeared: {logical_path}")
        content = path.read_bytes()
        rows.append(
            {
                "logical_path": logical_path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
        )
    return _digest(rows), rows


def _latest_event(events: Sequence[Mapping[str, object]], event_type: str) -> JsonObject:
    for event in reversed(events):
        if event.get("event_type") == event_type:
            payload = event.get("payload")
            if not isinstance(payload, dict):
                raise ValueError(f"{event_type} payload is malformed")
            return dict(event)
    raise RuntimeError(f"required Project event is missing: {event_type}")


def _clean_artifact_path(clean_root: Path, descriptor: Mapping[str, object]) -> Path:
    return clean_root / "upstream" / str(descriptor["path"])


def audit_tiangong_second_case_package(
    final_package_root: Path,
    project_workspace: Path,
) -> JsonObject:
    """Reopen and byte-rebuild one package in an isolated temporary context."""

    workspace = Path(project_workspace).resolve()
    package_root = Path(final_package_root).resolve()
    if not workspace.is_dir() or not package_root.is_dir():
        raise FileNotFoundError("Project workspace or final package root is missing")
    try:
        package_root.relative_to(workspace)
    except ValueError as error:
        raise ValueError("final package root must be inside the Project workspace") from error

    package_files = sorted(path for path in package_root.iterdir() if path.is_file())
    expected_names = {
        "authority-history.json",
        "human-final-report.md",
        "machine-final-state.json",
        "package-manifest.json",
        "package-report.json",
        "replay-evidence.json",
        "risk-feedback-register.json",
        "validation.json",
    }
    if {path.name for path in package_files} != expected_names:
        raise ValueError("final package must contain exactly the eight deliverables")
    for name in _PROJECTIONS:
        if not (workspace / name).is_file():
            raise RuntimeError(f"required current projection is missing: {name}")

    source_events = _read_events(workspace / "research-events.jsonl")
    package_event = _latest_event(
        source_events, "research.tiangong_second_case_package.assembled"
    )
    package_payload = package_event["payload"]
    registered_deliverables = package_payload.get("deliverables")
    if not isinstance(registered_deliverables, dict) or set(
        registered_deliverables
    ) != expected_names:
        raise ValueError("package event does not register exactly eight deliverables")
    for filename, value in registered_deliverables.items():
        descriptor = _artifact_descriptor(value, f"deliverable {filename}")
        path = (workspace / str(descriptor["path"])).resolve()
        if path.parent != package_root or path.name != filename:
            raise ValueError("package event deliverable leaves the final package root")
        _verify_file(path, descriptor, f"deliverable {filename}")

    manifest = _read_json(package_root / "package-manifest.json", "package manifest")
    manifest_rows = manifest.get("files")
    if (
        manifest.get("deliverable_count") != 8
        or not isinstance(manifest_rows, list)
        or len(manifest_rows) != 7
    ):
        raise ValueError("package manifest deliverable declaration is invalid")
    manifest_names = set()
    for index, value in enumerate(manifest_rows):
        if not isinstance(value, dict):
            raise ValueError("package manifest contains a malformed file row")
        filename = value.get("filename")
        if not isinstance(filename, str) or filename == "package-manifest.json":
            raise ValueError("package manifest filename is invalid")
        manifest_names.add(filename)
        content = (package_root / filename).read_bytes()
        if (
            value.get("bytes") != len(content)
            or value.get("sha256") != hashlib.sha256(content).hexdigest()
        ):
            raise RuntimeError(f"package manifest binding drifted: {filename}")
    if manifest_names != expected_names - {"package-manifest.json"}:
        raise ValueError("package manifest does not cover all non-self deliverables")

    registered_upstream: List[Tuple[str, JsonObject]] = []
    for event_type, roles in _UPSTREAM_ROLES.items():
        matching = [
            event for event in source_events if event.get("event_type") == event_type
        ]
        if not matching:
            raise RuntimeError(f"required upstream event is missing: {event_type}")
        selected_events = matching if event_type.endswith("receipt_created") else [matching[-1]]
        for event_index, event in enumerate(selected_events):
            payload = event.get("payload")
            if not isinstance(payload, dict):
                raise ValueError(f"{event_type} payload is malformed")
            for role in roles:
                descriptor = _artifact_descriptor(
                    payload.get(role), f"{event_type}.{role}"
                )
                original = (workspace / str(descriptor["path"])).resolve()
                try:
                    original.relative_to(workspace)
                except ValueError as error:
                    raise ValueError("registered upstream artifact leaves workspace") from error
                _verify_file(original, descriptor, f"{event_type}.{role}")
                registered_upstream.append(
                    (f"{event_type}/{event_index}/{role}", descriptor)
                )

    original_files: List[Tuple[str, Path]] = [
        (f"package/{path.name}", path) for path in package_files
    ] + [(f"projection/{name}", workspace / name) for name in _PROJECTIONS]
    for logical, descriptor in registered_upstream:
        original_files.append(
            (f"upstream/{logical}", workspace / str(descriptor["path"]))
        )

    with tempfile.TemporaryDirectory(prefix="tiangong-second-case-acceptance-") as raw:
        clean_root = Path(raw)
        clean_package = clean_root / "final-package"
        clean_package.mkdir(parents=True)
        for path in package_files:
            shutil.copy2(path, clean_package / path.name)
        clean_projections = clean_root / "projections"
        clean_projections.mkdir()
        for name in _PROJECTIONS:
            shutil.copy2(workspace / name, clean_projections / name)
        for _, descriptor in registered_upstream:
            source = workspace / str(descriptor["path"])
            destination = _clean_artifact_path(clean_root, descriptor)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copy2(source, destination)

        clean_events = _read_events(clean_projections / "research-events.jsonl")

        def load_upstream(event_type: str, role: str = "artifact") -> JsonObject:
            event = _latest_event(clean_events, event_type)
            descriptor = _artifact_descriptor(
                event["payload"].get(role), f"{event_type}.{role}"
            )
            path = _clean_artifact_path(clean_root, descriptor)
            _verify_file(path, descriptor, f"clean {event_type}.{role}")
            return _read_json(path, f"clean {event_type}.{role}")

        adoption = load_upstream("research.tiangong_capabilities.adopted")
        selection = load_upstream("research.tiangong_open_data_case.selected")
        binding = load_upstream("research.tiangong_native_case.accepted")
        transition = load_upstream("research.tiangong_authority.transitioned")
        execution = load_upstream(
            "research.tiangong_authority.transitioned", "execution_artifact"
        )
        authority_history = _read_json(
            clean_package / "authority-history.json", "authority history"
        )
        replay = _read_json(clean_package / "replay-evidence.json", "replay evidence")
        risks = _read_json(
            clean_package / "risk-feedback-register.json", "risk register"
        )

        source_bindings = _binding_descriptors(
            [adoption, selection, binding, transition, execution, authority_history]
        )
        copied_source_paths: Dict[Tuple[str, str], Path] = {}
        for descriptor in source_bindings:
            original = _resolve_project_relative(workspace, str(descriptor["path"]))
            content = original.read_bytes()
            if hashlib.sha256(content).hexdigest() != descriptor["sha256"]:
                raise RuntimeError(
                    f"source binding content identity drifted: {descriptor['path']}"
                )
            if "bytes" in descriptor and len(content) != descriptor["bytes"]:
                raise RuntimeError(
                    f"source binding byte count drifted: {descriptor['path']}"
                )
            clean_source = (
                clean_root
                / "source-bindings"
                / str(descriptor["sha256"])
                / Path(str(descriptor["path"])).name
            )
            clean_source.parent.mkdir(parents=True, exist_ok=True)
            if not clean_source.exists():
                shutil.copy2(original, clean_source)
            copied = clean_source.read_bytes()
            if copied != content:
                raise RuntimeError("clean source-binding copy changed bytes")
            copied_source_paths[
                (str(descriptor["path"]), str(descriptor["sha256"]))
            ] = clean_source
            original_files.append(
                (f"source-binding/{descriptor['path']}", original)
            )

        source_tree_before, _ = _tree_digest(original_files)
        clean_tree_before, clean_inventory = _tree_digest(
            [
                (path.relative_to(clean_root).as_posix(), path)
                for path in clean_root.rglob("*")
                if path.is_file()
            ]
        )
        transition_evidence = {
            "contract": "lca_research_kernel.tiangong_authority_transition_evidence/v1",
            "transition": transition,
            "execution": execution,
        }
        receipts = authority_history.get("receipts")
        risk_rows = risks.get("risks")
        if not isinstance(receipts, list) or not isinstance(risk_rows, list):
            raise ValueError("authority history or risk register is malformed")
        rebuilt = build_tiangong_second_case_deliverables(
            capability_adoption=adoption,
            selection=selection,
            authority_history=receipts,
            native_case_binding=binding,
            transition=transition_evidence,
            replay_evidence=replay,
            risks=risk_rows,
        )
        if (
            rebuilt["package_id"] != package_payload.get("package_id")
            or rebuilt["package_content_sha256"]
            != package_payload.get("package_content_sha256")
        ):
            raise RuntimeError("rebuilt package identity does not match Project event")
        for filename, value in rebuilt["deliverables"].items():
            expected = value.encode("utf-8") if isinstance(value, str) else _json_bytes(value)
            if (clean_package / filename).read_bytes() != expected:
                raise RuntimeError(f"byte-for-byte package rebuild failed: {filename}")

        effects = package_payload.get("effects")
        if not isinstance(effects, dict):
            raise ValueError("package event has no effect ledger")
        for field in _PROHIBITED_EFFECTS:
            if effects.get(field, 0) != 0:
                raise ValueError(f"package prohibited effect is nonzero: {field}")
        clean_tree_after, _ = _tree_digest(
            [
                (path.relative_to(clean_root).as_posix(), path)
                for path in clean_root.rglob("*")
                if path.is_file()
            ]
        )
        source_tree_after, _ = _tree_digest(original_files)
        if clean_tree_before != clean_tree_after:
            raise RuntimeError("clean acceptance tree changed during rebuild")
        if source_tree_before != source_tree_after:
            raise RuntimeError("source Project tree changed during acceptance")

    body: JsonObject = {
        "contract": ACCEPTANCE_CONTRACT,
        "status": "accepted",
        "package_id": rebuilt["package_id"],
        "package_content_sha256": rebuilt["package_content_sha256"],
        "rebuilt_deliverable_count": 8,
        "byte_for_byte_rebuild": True,
        "reopened_manifest_file_count": 7,
        "reopened_source_binding_count": len(copied_source_paths),
        "registered_upstream_artifact_count": len(registered_upstream),
        "copied_projection_count": len(_PROJECTIONS),
        "clean_context_file_count": len(clean_inventory),
        "clean_context_tree_sha256_before": clean_tree_before,
        "clean_context_tree_sha256_after": clean_tree_after,
        "source_project_tree_sha256_before": source_tree_before,
        "source_project_tree_sha256_after": source_tree_after,
        "source_project_tree_unchanged": True,
        "dialogue_or_warm_cold_context_copied_count": 0,
        "network_read_executed_count": 0,
        "project_write_executed_count": 0,
        "recalculation_executed_count": 0,
        "remote_write_executed_count": 0,
        "publication_executed_count": 0,
        "review_submission_executed_count": 0,
        "contact_executed_count": 0,
        "message_executed_count": 0,
        "next_route": "complete-tiangong-authority-following-second-case-goal",
    }
    digest = _digest(body)
    body["acceptance_id"] = digest[:16]
    body["acceptance_content_sha256"] = digest
    return body


def main(argv: Sequence[str] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--project-workspace", type=Path, required=True)
    args = parser.parse_args(argv)
    report = audit_tiangong_second_case_package(
        args.package_root, args.project_workspace
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
