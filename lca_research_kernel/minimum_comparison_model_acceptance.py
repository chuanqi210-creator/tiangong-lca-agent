"""Read-only independent acceptance for a materialized minimum comparison model."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    from .minimum_comparison_model import (
        CONTRACT,
        LCIA_NEXT_ROUTE,
        _process_uuid,
        _reference_product,
        normalize_projected_process_rows,
        verify_minimum_comparison_native_execution,
    )
except ImportError:  # Direct execution from the project workspace.
    from minimum_comparison_model import (  # type: ignore
        CONTRACT,
        LCIA_NEXT_ROUTE,
        _process_uuid,
        _reference_product,
        normalize_projected_process_rows,
        verify_minimum_comparison_native_execution,
    )


JsonObject = Dict[str, object]
ACCEPTANCE_CONTRACT = f"{CONTRACT}_read_only_independent_acceptance"


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return dict(value)


def _list(value: object, label: str) -> List[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    return list(value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _read_json(path: Path, label: str) -> JsonObject:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} is invalid: {path}") from error
    return _object(value, label)


def _read_jsonl(path: Path, label: str) -> List[JsonObject]:
    rows: List[JsonObject] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise RuntimeError(f"{label} is unreadable: {path}") from error
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"{label} contains invalid JSONL") from error
        rows.append(_object(value, label))
    return rows


def _tree_binding(root: Path) -> JsonObject:
    rows = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        content = path.read_bytes()
        rows.append(
            {
                "path": str(path.relative_to(root)),
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
            }
        )
    return {
        "file_count": len(rows),
        "total_bytes": sum(int(row["bytes"]) for row in rows),
        "tree_sha256": hashlib.sha256(_json_bytes(rows)).hexdigest(),
    }


def audit_minimum_comparison_model_package(
    package_root: Path, project_workspace: Path
) -> JsonObject:
    """Reopen every bound member without mutating the source package or Project."""

    root = Path(package_root).resolve()
    workspace = Path(project_workspace).resolve()
    try:
        root.relative_to(workspace)
    except ValueError as error:
        raise ValueError("package root must stay inside the Project workspace") from error
    if not root.is_dir():
        raise FileNotFoundError("minimum comparison model package is missing")
    tree_before = _tree_binding(root)
    report_path = root / "outputs/minimum-lifepo4-comparison-model.json"
    manifest_path = root / "outputs/package-manifest.json"
    report = _read_json(report_path, "minimum comparison model report")
    manifest = _read_json(manifest_path, "minimum comparison model manifest")
    if (
        report.get("contract") != f"{CONTRACT}_native_execution"
        or report.get("status") != "minimum_lifepo4_comparison_model_materialized"
        or report.get("next_internal_route") != LCIA_NEXT_ROUTE
        or manifest.get("contract") != f"{CONTRACT}_package_manifest"
        or manifest.get("artifact_count") != len(manifest.get("artifacts", []))
    ):
        raise ValueError("materialized minimum comparison model contract drifted")
    reopened = 0
    for value in _list(manifest.get("artifacts"), "package artifacts"):
        binding = _object(value, "package artifact")
        relative = Path(_text(binding.get("path"), "package artifact path"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("package artifact path escaped its root")
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"package artifact is missing: {relative}")
        content = path.read_bytes()
        if (
            hashlib.sha256(content).hexdigest() != binding.get("sha256")
            or len(content) != binding.get("bytes")
        ):
            raise ValueError(f"package artifact binding drifted: {relative}")
        reopened += 1
    manifest_content = manifest_path.read_bytes()
    report_manifest = _object(report.get("package_manifest"), "report manifest")
    if (
        report_manifest.get("sha256")
        != hashlib.sha256(manifest_content).hexdigest()
        or report_manifest.get("bytes") != len(manifest_content)
        or report_manifest.get("artifact_count") != reopened
    ):
        raise ValueError("report-to-manifest binding drifted")

    compiled = _read_json(
        root / "inputs/compiled-native-inputs.json", "compiled native inputs"
    )
    evidence = _read_json(
        root / "outputs/native-execution-evidence.json", "native execution evidence"
    )
    accepted = verify_minimum_comparison_native_execution(compiled, evidence)
    stored_verification = _read_json(
        root / "outputs/native-execution-verification.json",
        "native execution verification",
    )
    if accepted != stored_verification:
        raise ValueError("stored native verification is not reproducible")

    raw_rows = _read_jsonl(
        root / "outputs/raw-resulting-processes.jsonl", "raw resulting Processes"
    )
    normalized_rows = _read_jsonl(
        root / "outputs/resulting-processes.jsonl", "resulting Processes"
    )
    recomputed_rows, recomputed_normalization = normalize_projected_process_rows(
        raw_rows
    )
    stored_normalization = _read_json(
        root / "outputs/resulting-process-normalization.json",
        "resulting Process normalization",
    )
    if (
        recomputed_rows != normalized_rows
        or recomputed_normalization != stored_normalization
        or len(normalized_rows) != 2
    ):
        raise ValueError("resulting Process normalization is not deterministic")
    result_ids = set()
    product_flows = set()
    for row in normalized_rows:
        result_ids.add(_process_uuid(row, "accepted resulting Process"))
        flow_id, amount = _reference_product(row)
        product_flows.add(flow_id)
        if amount != 1:
            raise ValueError("resulting Process functional unit drifted")
    expected_results = {
        _text(value.get("resulting_process_id"), "expected resulting Process UUID")
        for value in _list(compiled.get("projection_requests"), "projection requests")
        if isinstance(value, dict)
    }
    if result_ids != expected_results or len(product_flows) != 1:
        raise ValueError("resulting Process identity or common product drifted")

    snapshot = _read_json(workspace / "project-snapshot.json", "Project snapshot")
    state = _object(
        snapshot.get("minimum_lifepo4_comparison_model"),
        "Project minimum comparison model state",
    )
    graph = _object(
        snapshot.get("minimum_lifepo4_comparison_graph"),
        "Project minimum comparison graph state",
    )
    report_relative = str(report_path.relative_to(workspace))
    if (
        state.get("report_path") != report_relative
        or state.get("report_sha256")
        != hashlib.sha256(report_path.read_bytes()).hexdigest()
        or state.get("general_corpus_release_frozen") is not True
        or state.get("unmaterialized_release_record_count") != 1453
        or graph.get("node_count") != 33
        or graph.get("edge_count") != 55
        or graph.get("freeze", {}).get("general_corpus_release_frozen") is not True
        or graph.get("freeze", {}).get("unmaterialized_release_record_count")
        != 1453
        or any(
            state.get(key) != 0
            for key in (
                "network_read_executed_count",
                "remote_write_executed_count",
                "publication_executed_count",
                "comparison_executed_count",
                "lcia_executed_count",
            )
        )
    ):
        raise ValueError("Project state no longer binds the accepted package")
    tree_after = _tree_binding(root)
    if tree_after != tree_before:
        raise RuntimeError("read-only audit changed the source package")
    return {
        "schema_version": 1,
        "contract": ACCEPTANCE_CONTRACT,
        "status": "accepted",
        "source_package": tree_before,
        "reopened_manifest_artifact_count": reopened,
        "native_verification_reproduced": True,
        "normalization_reproduced": True,
        "resulting_process_count": 2,
        "common_reference_product_flow_id": next(iter(product_flows)),
        "functional_unit_amount": 1,
        "graph_node_count": 33,
        "graph_edge_count": 55,
        "unmaterialized_release_record_count": 1453,
        "source_package_unchanged": True,
        "project_write_executed_count": 0,
        "remote_write_executed_count": 0,
        "publication_executed_count": 0,
        "comparison_executed_count": 0,
        "lcia_executed_count": 0,
        "next_internal_route": LCIA_NEXT_ROUTE,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--project-workspace", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = audit_minimum_comparison_model_package(
        args.package_root, args.project_workspace
    )
    out = args.out_dir / "outputs/minimum-comparison-model-independent-acceptance.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    content = _json_bytes(report)
    out.write_bytes(content)
    if out.read_bytes() != content:
        raise RuntimeError("independent acceptance report readback drifted")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
