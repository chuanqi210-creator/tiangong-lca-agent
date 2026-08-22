"""Read-only independent acceptance for the comparative LiFePO4 LCIA package."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

try:
    from .minimum_comparison_lcia import (
        CONTRACT,
        NEXT_ROUTE,
        _json_bytes,
        _list,
        _object,
        _read_json,
        _text,
        _tree_binding,
        _verify_source_model_package,
        build_minimum_comparison_lcia_analysis,
        compile_minimum_comparison_lcia,
    )
except ImportError:  # Direct execution from the project workspace.
    from minimum_comparison_lcia import (  # type: ignore
        CONTRACT,
        NEXT_ROUTE,
        _json_bytes,
        _list,
        _object,
        _read_json,
        _text,
        _tree_binding,
        _verify_source_model_package,
        build_minimum_comparison_lcia_analysis,
        compile_minimum_comparison_lcia,
    )


JsonObject = Dict[str, object]
ACCEPTANCE_CONTRACT = f"{CONTRACT}_read_only_independent_acceptance"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_manifest(root: Path, manifest: Mapping[str, object]) -> int:
    rows = _list(manifest.get("artifacts"), "LCIA manifest artifacts")
    if manifest.get("artifact_count") != len(rows) or len(rows) != 10:
        raise ValueError("LCIA manifest membership drifted")
    reopened = 0
    for value in rows:
        binding = _object(value, "LCIA manifest artifact")
        relative = Path(_text(binding.get("path"), "LCIA artifact path"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("LCIA manifest path escaped package")
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"LCIA manifest member is missing: {relative}")
        content = path.read_bytes()
        if (
            hashlib.sha256(content).hexdigest() != binding.get("sha256")
            or len(content) != binding.get("bytes")
        ):
            raise ValueError(f"LCIA manifest member drifted: {relative}")
        reopened += 1
    return reopened


def audit_minimum_comparison_lcia_package(
    lcia_package_root: Path,
    model_package_root: Path,
    model_acceptance_path: Path,
    comparison_scope_path: Path,
    project_workspace: Path,
) -> JsonObject:
    """Recompute every numerical output while leaving both source packages unchanged."""

    root = lcia_package_root.resolve()
    model_root = model_package_root.resolve()
    workspace = project_workspace.resolve()
    for candidate, label in ((root, "LCIA package"), (model_root, "model package")):
        try:
            candidate.relative_to(workspace)
        except ValueError as error:
            raise ValueError(f"{label} must stay inside the Project workspace") from error
        if not candidate.is_dir():
            raise FileNotFoundError(f"{label} is missing")
    lcia_tree_before = _tree_binding(root)
    model_tree_before = _tree_binding(model_root)
    report_path = root / "outputs/minimum-lifepo4-comparative-lcia.json"
    manifest_path = root / "outputs/package-manifest.json"
    report = _read_json(report_path, "LCIA report")
    manifest = _read_json(manifest_path, "LCIA manifest")
    source_bindings = _read_json(
        root / "inputs/source-bindings.json", "LCIA source bindings"
    )
    compiled = _read_json(
        root / "inputs/compiled-calculation-input.json", "compiled LCIA input"
    )
    reopened = _verify_manifest(root, manifest)
    if (
        report.get("contract") != f"{CONTRACT}_execution"
        or report.get("status") != "minimum_lifepo4_comparative_lcia_executed"
        or report.get("package_manifest", {}).get("sha256") != _sha256(manifest_path)
        or report.get("validation_status") != "accepted"
        or report.get("parameter_count") != 14
        or report.get("named_scenario_count") != 9
        or report.get("oat_perturbation_count") != 28
        or report.get("uncertainty_trial_count") != 20000
        or report.get("uncertainty_seed") != 20260719
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
            )
        )
    ):
        raise ValueError("LCIA execution report contract drifted")
    authorities = _verify_source_model_package(
        model_root,
        model_acceptance_path,
        expected_report_sha256=_text(
            source_bindings.get("model_report_sha256"), "model report SHA-256"
        ),
        expected_manifest_sha256=_text(
            source_bindings.get("model_manifest_sha256"), "model manifest SHA-256"
        ),
        expected_acceptance_sha256=_text(
            source_bindings.get("model_acceptance_sha256"),
            "model acceptance SHA-256",
        ),
    )
    if authorities["source_tree"] != source_bindings.get("model_source_tree"):
        raise ValueError("LCIA source binding no longer matches the model tree")
    scope_content = comparison_scope_path.read_bytes()
    if hashlib.sha256(scope_content).hexdigest() != source_bindings.get(
        "comparison_scope_sha256"
    ):
        raise ValueError("comparison scope binding drifted")
    scope = _object(json.loads(scope_content), "comparison scope")
    recomputed_compiled = compile_minimum_comparison_lcia(
        authorities["compiled_model"],
        authorities["compiled_native_inputs"],
        scope,
    )
    if recomputed_compiled != compiled:
        raise ValueError("compiled LCIA input is not reproducible")
    recomputed = build_minimum_comparison_lcia_analysis(recomputed_compiled)
    expected_outputs = {
        "outputs/equations.json": recomputed["equations"],
        "outputs/baseline.json": recomputed["baseline"],
        "outputs/named-scenarios.json": recomputed["named_scenarios"],
        "outputs/oat-sensitivity.json": recomputed["sensitivity"],
        "outputs/uncertainty.json": recomputed["uncertainty"],
        "outputs/contribution-table.json": recomputed["contribution_table"],
        "outputs/bounded-interpretation.json": recomputed["interpretation"],
        "outputs/validation.json": recomputed["validation"],
    }
    for relative, expected in expected_outputs.items():
        if _read_json(root / relative, relative) != expected:
            raise ValueError(f"LCIA numerical output is not reproducible: {relative}")
    uncertainty = _object(recomputed["uncertainty"], "uncertainty")
    interpretation = _object(recomputed["interpretation"], "interpretation")
    snapshot = _read_json(workspace / "project-snapshot.json", "Project snapshot")
    state = _object(
        snapshot.get("minimum_lifepo4_comparative_lcia"),
        "Project comparative LCIA state",
    )
    report_relative = str(report_path.relative_to(workspace))
    if (
        state.get("report_path") != report_relative
        or state.get("report_sha256") != _sha256(report_path)
        or state.get("manifest_sha256") != _sha256(manifest_path)
        or state.get("uncertainty_trial_digest_sha256")
        != uncertainty.get("trial_digest_sha256")
        or state.get("general_corpus_release_frozen") is not True
        or state.get("unmaterialized_release_record_count") != 1453
    ):
        raise ValueError("Project state no longer binds the LCIA package")
    lcia_tree_after = _tree_binding(root)
    model_tree_after = _tree_binding(model_root)
    if lcia_tree_after != lcia_tree_before or model_tree_after != model_tree_before:
        raise RuntimeError("read-only LCIA audit changed a source package")
    return {
        "schema_version": 1,
        "contract": ACCEPTANCE_CONTRACT,
        "status": "accepted",
        "source_package": lcia_tree_before,
        "model_source_package": model_tree_before,
        "reopened_manifest_artifact_count": reopened,
        "compiled_input_reproduced": True,
        "numerical_recomputation_reproduced": True,
        "recomputed_output_count": len(expected_outputs),
        "uncertainty_trial_count": uncertainty["trial_count"],
        "uncertainty_seed": uncertainty["seed"],
        "uncertainty_trial_digest_sha256": uncertainty["trial_digest_sha256"],
        "direction_reversal_detected": interpretation[
            "direction_reversal_detected"
        ],
        "claim_level": interpretation["claim_level"],
        "general_corpus_release_frozen": True,
        "unmaterialized_release_record_count": 1453,
        "source_package_unchanged": True,
        "model_source_package_unchanged": True,
        "project_write_executed_count": 0,
        "network_read_executed_count": 0,
        "remote_write_executed_count": 0,
        "publication_executed_count": 0,
        "background_inventory_expansion_executed_count": 0,
        "next_internal_route": NEXT_ROUTE,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lcia-package-root", type=Path, required=True)
    parser.add_argument("--model-package-root", type=Path, required=True)
    parser.add_argument("--model-acceptance", type=Path, required=True)
    parser.add_argument("--comparison-scope", type=Path, required=True)
    parser.add_argument("--project-workspace", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = audit_minimum_comparison_lcia_package(
        args.lcia_package_root,
        args.model_package_root,
        args.model_acceptance,
        args.comparison_scope,
        args.project_workspace,
    )
    out_path = (
        args.out_dir
        / "outputs/minimum-comparative-lcia-independent-acceptance.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(_json_bytes(report))
    if _read_json(out_path, "written acceptance") != report:
        raise RuntimeError("independent acceptance readback drifted")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
