"""Clean-context read-only acceptance for the LiFePO4 screening final package."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence

try:
    from .minimum_comparison_final_package import (
        CONTRACT,
        NEXT_ROUTE,
        _json_bytes,
        _list,
        _object,
        _read_json,
        _text,
        _tree_binding,
        build_minimum_comparison_final_deliverables,
    )
except ImportError:  # Direct execution from the project workspace.
    from minimum_comparison_final_package import (  # type: ignore
        CONTRACT,
        NEXT_ROUTE,
        _json_bytes,
        _list,
        _object,
        _read_json,
        _text,
        _tree_binding,
        build_minimum_comparison_final_deliverables,
    )


JsonObject = Dict[str, object]
ACCEPTANCE_CONTRACT = f"{CONTRACT}_read_only_independent_acceptance"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_manifest(root: Path, manifest: Mapping[str, object]) -> int:
    rows = _list(manifest.get("artifacts"), "final manifest artifacts")
    if manifest.get("artifact_count") != len(rows) or len(rows) != 9:
        raise ValueError("final manifest membership drifted")
    for value in rows:
        binding = _object(value, "final manifest artifact")
        relative = Path(_text(binding.get("path"), "final artifact path"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("final manifest path escaped package")
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"final manifest member is missing: {relative}")
        content = path.read_bytes()
        if (
            hashlib.sha256(content).hexdigest() != binding.get("sha256")
            or len(content) != binding.get("bytes")
        ):
            raise ValueError(f"final manifest member drifted: {relative}")
    return len(rows)


def _bound_workspace_file(
    workspace: Path,
    path_value: object,
    hash_value: object,
    label: str,
) -> Path:
    relative = Path(_text(path_value, f"{label} path"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{label} escaped workspace")
    path = workspace / relative
    if not path.is_file() or _sha256(path) != _text(
        hash_value, f"{label} SHA-256"
    ):
        raise ValueError(f"{label} binding drifted")
    return path


def audit_minimum_comparison_final_package(
    final_package_root: Path, project_workspace: Path
) -> JsonObject:
    """Rebuild all eight deliverables and verify every upstream authority binding."""

    root = final_package_root.resolve()
    workspace = project_workspace.resolve()
    try:
        root.relative_to(workspace)
    except ValueError as error:
        raise ValueError("final package must stay inside the Project workspace") from error
    if not root.is_dir():
        raise FileNotFoundError("final package is missing")
    tree_before = _tree_binding(root)
    report_path = root / "outputs/minimum-lifepo4-screening-final-package.json"
    manifest_path = root / "outputs/package-manifest.json"
    report = _read_json(report_path, "final package report")
    manifest = _read_json(manifest_path, "final package manifest")
    authority_input = _read_json(
        root / "inputs/final-authorities.json", "final authority input"
    )
    reopened = _verify_manifest(root, manifest)
    if (
        report.get("contract") != f"{CONTRACT}_execution"
        or report.get("status")
        != "minimum_lifepo4_screening_final_package_assembled"
        or report.get("validation_status") != "accepted"
        or report.get("deliverable_count") != 8
        or report.get("claim_level")
        != "screening_single_category_evidence_bounded"
        or report.get("package_manifest", {}).get("sha256") != _sha256(manifest_path)
        or report.get("general_corpus_release_frozen") is not True
        or report.get("unmaterialized_release_record_count") != 1453
        or report.get("next_internal_route") != NEXT_ROUTE
        or any(
            report.get(key) != 0
            for key in (
                "network_read_executed_count",
                "remote_write_executed_count",
                "publication_executed_count",
                "background_inventory_expansion_executed_count",
                "comparison_reexecution_count",
                "lcia_reexecution_count",
            )
        )
    ):
        raise ValueError("final package execution contract drifted")
    bindings = _object(
        authority_input.get("authority_bindings"), "final authority bindings"
    )
    authority_specs = [
        ("canary_report_path", "canary_report_sha256", "canary report"),
        ("freeze_decision_path", "freeze_decision_sha256", "freeze decision"),
        ("graph_report_path", "graph_report_sha256", "graph report"),
        ("model_report_path", "model_report_sha256", "model report"),
        ("model_acceptance_path", "model_acceptance_sha256", "model acceptance"),
        ("lcia_report_path", "lcia_report_sha256", "LCIA report"),
        ("lcia_acceptance_path", "lcia_acceptance_sha256", "LCIA acceptance"),
    ]
    upstream_paths = []
    for path_key, hash_key, label in authority_specs:
        upstream_paths.append(
            _bound_workspace_file(
                workspace, bindings.get(path_key), bindings.get(hash_key), label
            )
        )
    model_report_path = upstream_paths[3]
    lcia_report_path = upstream_paths[5]
    model_manifest_path = model_report_path.parent / "package-manifest.json"
    lcia_manifest_path = lcia_report_path.parent / "package-manifest.json"
    if (
        _sha256(model_manifest_path) != bindings.get("model_manifest_sha256")
        or _sha256(lcia_manifest_path) != bindings.get("lcia_manifest_sha256")
    ):
        raise ValueError("upstream package manifest binding drifted")
    lcia_root = lcia_report_path.parent.parent
    analysis = {
        "baseline": _read_json(lcia_root / "outputs/baseline.json", "baseline"),
        "named_scenarios": _read_json(
            lcia_root / "outputs/named-scenarios.json", "named scenarios"
        ),
        "sensitivity": _read_json(
            lcia_root / "outputs/oat-sensitivity.json", "OAT sensitivity"
        ),
        "uncertainty": _read_json(
            lcia_root / "outputs/uncertainty.json", "uncertainty"
        ),
        "contribution_table": _read_json(
            lcia_root / "outputs/contribution-table.json", "contributions"
        ),
        "interpretation": _read_json(
            lcia_root / "outputs/bounded-interpretation.json", "interpretation"
        ),
    }
    rebuilt = build_minimum_comparison_final_deliverables(
        scope_contract=_object(
            authority_input.get("scope_contract"), "scope contract"
        ),
        graph_report=_object(authority_input.get("graph_report"), "graph report"),
        model_state=_object(authority_input.get("model_state"), "model state"),
        lcia_state=_object(authority_input.get("lcia_state"), "LCIA state"),
        analysis=analysis,
        authority_bindings=bindings,
        replay_evidence=_object(
            authority_input.get("replay_evidence"), "replay evidence"
        ),
        open_risks=[
            _object(value, "open risk")
            for value in _list(authority_input.get("open_risks"), "open risks")
        ],
    )
    expected_json = {
        "outputs/machine-final-state.json": rebuilt["machine_final_state"],
        "outputs/comparison-summary.json": rebuilt["comparison_summary"],
        "outputs/comparison-tables.json": rebuilt["comparison_tables"],
        "outputs/bounded-interpretation.json": rebuilt["interpretation"],
        "outputs/replay-evidence.json": rebuilt["replay_evidence"],
        "outputs/open-risks.json": rebuilt["open_risks"],
        "outputs/validation.json": rebuilt["validation"],
    }
    for relative, expected in expected_json.items():
        if _read_json(root / relative, relative) != expected:
            raise ValueError(f"final deliverable is not reproducible: {relative}")
    if (root / "outputs/human-final-report.md").read_text(
        encoding="utf-8"
    ) != rebuilt["human_final_report"]:
        raise ValueError("human final report is not reproducible")
    snapshot = _read_json(workspace / "project-snapshot.json", "Project snapshot")
    state = _object(
        snapshot.get("minimum_lifepo4_screening_final_package"),
        "Project final package state",
    )
    if (
        state.get("report_path") != str(report_path.relative_to(workspace))
        or state.get("report_sha256") != _sha256(report_path)
        or state.get("manifest_sha256") != _sha256(manifest_path)
        or state.get("machine_final_state_sha256")
        != _sha256(root / "outputs/machine-final-state.json")
        or state.get("human_final_report_sha256")
        != _sha256(root / "outputs/human-final-report.md")
        or state.get("general_corpus_release_frozen") is not True
        or state.get("unmaterialized_release_record_count") != 1453
    ):
        raise ValueError("Project state no longer binds the final package")
    tree_after = _tree_binding(root)
    if tree_after != tree_before:
        raise RuntimeError("read-only final audit changed the source package")
    return {
        "schema_version": 1,
        "contract": ACCEPTANCE_CONTRACT,
        "status": "accepted",
        "source_package": tree_before,
        "reopened_manifest_artifact_count": reopened,
        "upstream_authority_file_count": len(authority_specs) + 2,
        "upstream_authorities_reopened": True,
        "deliverables_reproduced": True,
        "reproduced_deliverable_count": 8,
        "validation_reproduced": True,
        "uncertainty_trial_digest_sha256": report[
            "uncertainty_trial_digest_sha256"
        ],
        "claim_level": report["claim_level"],
        "direction_reversal_detected": report["direction_reversal_detected"],
        "general_corpus_release_frozen": True,
        "unmaterialized_release_record_count": 1453,
        "source_package_unchanged": True,
        "project_write_executed_count": 0,
        "network_read_executed_count": 0,
        "remote_write_executed_count": 0,
        "publication_executed_count": 0,
        "background_inventory_expansion_executed_count": 0,
        "comparison_reexecution_count": 0,
        "lcia_reexecution_count": 0,
        "next_internal_route": NEXT_ROUTE,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--final-package-root", type=Path, required=True)
    parser.add_argument("--project-workspace", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = audit_minimum_comparison_final_package(
        args.final_package_root, args.project_workspace
    )
    path = args.out_dir / "outputs/minimum-screening-final-independent-acceptance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(report))
    if _read_json(path, "written final acceptance") != report:
        raise RuntimeError("final acceptance readback drifted")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
