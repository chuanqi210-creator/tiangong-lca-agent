#!/usr/bin/env python3
"""Deterministically verify the D-18 core-LCA evaluation evidence chain."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lca_research_evaluation.core_lca_quality_efficiency import (
    CoreLCAQualityEvaluator,
    canonical_sha256,
    classify_core_lca_evidence_changes,
    derive_effect_summary,
    derive_efficiency_metrics,
    load_core_lca_quality_contract,
    scan_secret_material,
)
from lca_research_kernel.project_record import ProjectRecord
from lca_research_kernel.run_trace import RunTrace
from scripts.run_core_lca_quality_efficiency_evaluation import (
    CONTRACT_PATH,
)


PENDING_FINAL_GATES = {
    "missing_data_prevents_complete_conclusion",
    "clean_review_p1_zero_p2_zero",
    "full_repository_verification_passed",
}


def _load_json(path: Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_git_object(
    repository_root: Path,
    ref: Mapping[str, object],
    error_prefix: str,
    errors: list[str],
) -> Optional[bytes]:
    commit = ref.get("commit")
    path = ref.get("path")
    if not isinstance(commit, str) or not isinstance(path, str):
        errors.append(f"{error_prefix}_invalid")
        return None
    try:
        completed = subprocess.run(
            ["git", "show", f"{commit}:{path}"],
            cwd=repository_root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError):
        errors.append(f"{error_prefix}_unreadable")
        return None
    payload = completed.stdout
    if (
        hashlib.sha256(payload).hexdigest() != ref.get("sha256")
        or len(payload) != ref.get("bytes")
    ):
        errors.append(f"{error_prefix}_drifted")
        return None
    return payload


def _verify_history_chains(
    record_path: Optional[Path],
    trace_path: Optional[Path],
    error_prefix: str,
    errors: list[str],
) -> None:
    if record_path is not None:
        try:
            verification = ProjectRecord.open_or_create_project(
                record_path.parent
            ).rebuild_and_verify()
            if verification.get("status") != "passed":
                errors.append(f"{error_prefix}_project_record_chain_invalid")
        except (OSError, ValueError, json.JSONDecodeError):
            errors.append(f"{error_prefix}_project_record_chain_invalid")
    if trace_path is not None:
        try:
            first = next(
                json.loads(line)
                for line in trace_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
            run_id = first.get("run_id") if isinstance(first, Mapping) else None
            if not isinstance(run_id, str):
                raise ValueError("run_id missing")
            verification = RunTrace(trace_path, run_id=run_id).verify()
            if verification.get("status") != "passed":
                errors.append(f"{error_prefix}_run_trace_chain_invalid")
        except (OSError, ValueError, StopIteration, json.JSONDecodeError):
            errors.append(f"{error_prefix}_run_trace_chain_invalid")


def _resolve_verified_ref(
    ref: object, repository_root: Path, error_prefix: str, errors: list[str]
) -> Optional[Path]:
    if not isinstance(ref, Mapping) or not isinstance(ref.get("path"), str):
        errors.append(f"{error_prefix}_ref_invalid")
        return None
    try:
        path = (repository_root / ref["path"]).resolve(strict=True)
        path.relative_to(repository_root.resolve())
    except (OSError, ValueError):
        errors.append(f"{error_prefix}_ref_invalid")
        return None
    if _sha256(path) != ref.get("sha256"):
        errors.append(f"{error_prefix}_ref_drifted")
        return None
    return path


def _accepted_fact_event(
    record_path: Path, fact_id: str
) -> Optional[tuple[dict, dict]]:
    matched = []
    for line in record_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        fact = event.get("payload", {}).get("candidate", {}).get("fact", {})
        if (
            event.get("event_type") == "research.project.accepted_change"
            and isinstance(fact, Mapping)
            and fact.get("fact_id") == fact_id
        ):
            matched.append((event, dict(fact)))
    return matched[0] if len(matched) == 1 else None


def check_content_bindings(
    calculation: Mapping[str, object],
    interpretation: Optional[Mapping[str, object]],
    repository_root: Path = ROOT,
) -> list[str]:
    """Reopen authority and Project Record bytes; never trust embedded echoes."""

    repository_root = Path(repository_root).resolve()
    errors: list[str] = []
    authority = calculation.get("authoritative_inputs")
    material = oracle = None
    if not isinstance(authority, Mapping):
        errors.append("calculation_authoritative_inputs_absent")
    else:
        material_path = _resolve_verified_ref(
            authority.get("material"), repository_root, "calculation_material", errors
        )
        oracle_path = _resolve_verified_ref(
            authority.get("oracle"), repository_root, "calculation_oracle", errors
        )
        if material_path:
            material = _load_json(material_path)
        if oracle_path:
            oracle = _load_json(oracle_path)

    source = calculation.get("source_binding")
    calc = calculation.get("calculation")
    scope = calculation.get("goal_scope")
    if isinstance(source, Mapping):
        for index, ref in enumerate(source.get("source_refs") or []):
            _resolve_verified_ref(
                ref, repository_root, f"calculation_source_{index}", errors
            )
    if isinstance(material, Mapping):
        inputs = material.get("scientific_inputs")
        if not isinstance(inputs, Mapping) or not isinstance(source, Mapping):
            errors.append("calculation_material_binding_invalid")
        elif (
            inputs.get("processes") != source.get("processes")
            or inputs.get("provider_connections") != source.get("provider_connections")
            or material.get("source_refs") != source.get("source_refs")
            or not isinstance(scope, Mapping)
            or material.get("functional_unit") != scope.get("functional_unit")
        ):
            errors.append("calculation_material_binding_drifted")
    if isinstance(oracle, Mapping):
        expected = oracle.get("expected_results", {}).get("impacts")  # type: ignore[union-attr]
        expected_row = expected[0] if isinstance(expected, list) and len(expected) == 1 else None
        if (
            not isinstance(expected_row, Mapping)
            or not isinstance(calc, Mapping)
            or str(expected_row.get("value")) != str(calc.get("oracle_value"))
            or expected_row.get("unit") != calc.get("oracle_unit")
            or oracle.get("method_identity") != calc.get("method_id")
            or oracle.get("method_version") != calc.get("method_version")
            or not isinstance(source, Mapping)
            or oracle.get("provider_connections") != source.get("provider_connections")
        ):
            errors.append("calculation_oracle_binding_drifted")

    calculation_record = calculation.get("project_record")
    if not isinstance(calculation_record, Mapping):
        errors.append("calculation_project_record_ref_invalid")
    else:
        record_path = _resolve_verified_ref(
            calculation_record.get("ref"),
            repository_root,
            "calculation_project_record",
            errors,
        )
        binding = calculation_record.get("accepted_fact")
        if record_path is not None and isinstance(binding, Mapping):
            matched = _accepted_fact_event(record_path, str(binding.get("fact_id")))
            if matched is None:
                errors.append("calculation_accepted_fact_missing")
            else:
                event, fact = matched
                value = fact.get("value")
                accepted_calculation = (
                    value.get("calculation") if isinstance(value, Mapping) else None
                )
                accepted_result = (
                    accepted_calculation.get("result")
                    if isinstance(accepted_calculation, Mapping)
                    else None
                )
                accepted_impacts = (
                    accepted_calculation.get("impacts")
                    if isinstance(accepted_calculation, Mapping)
                    else None
                )
                accepted_method = (
                    accepted_calculation.get("method")
                    if isinstance(accepted_calculation, Mapping)
                    else None
                )
                projection_matches = bool(
                    isinstance(calc, Mapping)
                    and isinstance(accepted_result, Mapping)
                    and isinstance(accepted_impacts, list)
                    and len(accepted_impacts) == 1
                    and isinstance(accepted_impacts[0], Mapping)
                    and isinstance(accepted_method, Mapping)
                    and str(accepted_result.get("value")) == str(calc.get("value"))
                    and accepted_result.get("unit") == calc.get("unit")
                    and str(accepted_impacts[0].get("amount"))
                    == str(calc.get("value"))
                    and accepted_impacts[0].get("unit") == calc.get("unit")
                    and accepted_method.get("identity") == calc.get("method_id")
                    and accepted_method.get("version") == calc.get("method_version")
                    and accepted_calculation.get("scientific_inputs_sha256")
                    == calc.get("input_sha256")
                )
                if (
                    event.get("event_sha256") != binding.get("event_sha256")
                    or canonical_sha256(value) != binding.get("value_sha256")
                    or canonical_sha256(accepted_calculation)
                    != binding.get("accepted_content_sha256")
                    or not isinstance(calc, Mapping)
                    or calc.get("output_sha256")
                    != binding.get("accepted_content_sha256")
                    or not projection_matches
                ):
                    errors.append("calculation_accepted_fact_binding_drifted")
        elif record_path is not None:
            errors.append("calculation_accepted_fact_binding_absent")

    if interpretation is not None:
        interpretation_record = interpretation.get("project_record")
        if not isinstance(interpretation_record, Mapping):
            errors.append("interpretation_project_record_ref_invalid")
        else:
            record_path = _resolve_verified_ref(
                interpretation_record.get("ref"),
                repository_root,
                "interpretation_project_record",
                errors,
            )
            binding = interpretation_record.get("accepted_fact")
            if record_path is not None and isinstance(binding, Mapping):
                matched = _accepted_fact_event(record_path, str(binding.get("fact_id")))
                if matched is None:
                    errors.append("interpretation_accepted_fact_missing")
                else:
                    event, fact = matched
                    value = fact.get("value")
                    model_output = value.get("model_output") if isinstance(value, Mapping) else None
                    if (
                        event.get("event_sha256") != binding.get("event_sha256")
                        or canonical_sha256(value) != binding.get("value_sha256")
                        or canonical_sha256(model_output)
                        != binding.get("accepted_content_sha256")
                        or binding.get("accepted_content_sha256")
                        != interpretation.get("candidate_source_sha256")
                    ):
                        errors.append("interpretation_accepted_fact_binding_drifted")
            elif record_path is not None:
                errors.append("interpretation_accepted_fact_binding_absent")
    return sorted(set(errors))


def _check_derived_effects(
    evidence: Mapping[str, object], repository_root: Path, role: str
) -> list[str]:
    errors: list[str] = []
    effects = evidence.get("effects")
    if not isinstance(effects, Mapping):
        return [f"{role}_effect_evidence_absent"]
    trace_path = _resolve_verified_ref(
        effects.get("trace_ref"), repository_root, f"{role}_effect_trace", errors
    )
    authorization_record_path: Optional[Path] = None
    stored_context = effects.get("authorization_context")
    reconstructed_context = None
    if not isinstance(stored_context, Mapping):
        errors.append(f"{role}_authorization_context_invalid")
    else:
        source_refs = stored_context.get("source_refs")
        if not isinstance(source_refs, Mapping):
            errors.append(f"{role}_authorization_context_refs_invalid")
        else:
            identity_path = _resolve_verified_ref(
                source_refs.get("execution_identity"),
                repository_root,
                f"{role}_execution_identity",
                errors,
            )
            policy_path = _resolve_verified_ref(
                source_refs.get("execution_policy"),
                repository_root,
                f"{role}_execution_policy",
                errors,
            )
            record_path = _resolve_verified_ref(
                source_refs.get("project_record"),
                repository_root,
                f"{role}_authorization_project_record",
                errors,
            )
            authorization_record_path = record_path
            if all(path is not None for path in (identity_path, policy_path, record_path)):
                identity = _load_json(identity_path)  # type: ignore[arg-type]
                policy = _load_json(policy_path)  # type: ignore[arg-type]
                if (
                    identity.get("contract")
                    != "lca_research_kernel.project_execution_identity/v1"
                    or policy.get("contract")
                    != "lca_research_kernel.project_execution_policy/v1"
                    or identity.get("policy_id") != policy.get("policy_id")
                    or identity.get("policy_sha256")
                    != hashlib.sha256(
                        json.dumps(
                            policy,
                            ensure_ascii=False,
                            allow_nan=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                ):
                    errors.append(f"{role}_authorization_policy_binding_drifted")
                events = [
                    json.loads(line)
                    for line in record_path.read_text(encoding="utf-8").splitlines()  # type: ignore[union-attr]
                    if line.strip()
                ]
                operations = []
                for event in events:
                    fact = (
                        event.get("payload", {})
                        .get("candidate", {})
                        .get("fact", {})
                    )
                    value = fact.get("value") if isinstance(fact, Mapping) else None
                    source = fact.get("source") if isinstance(fact, Mapping) else None
                    if (
                        event.get("event_type") != "research.project.accepted_change"
                        or not isinstance(value, Mapping)
                        or value.get("status") != "sourced_result_not_fact"
                        or not isinstance(value.get("authority"), Mapping)
                        or not isinstance(source, Mapping)
                    ):
                        continue
                    authority = value["authority"]
                    operations.append(
                        {
                            "operation_id": value.get("request_id"),
                            "capability_id": value.get("capability_id"),
                            "effect": value.get("effect"),
                            "source_lock_sha256": hashlib.sha256(
                                json.dumps(
                                    authority.get("source_lock"),
                                    ensure_ascii=False,
                                    allow_nan=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ).encode("utf-8")
                            ).hexdigest(),
                            "arguments_sha256": source.get(
                                "operation_payload_sha256"
                            ),
                            "entrypoint": authority.get("entrypoint"),
                        }
                    )
                reconstructed_context = {
                    "contract": "d18_core_lca_authorization_context/v1",
                    "operation_receipt_contract": (
                        "lca_research_kernel.project_operation_authorization/v1"
                    ),
                    "execution_id": identity.get("execution_id"),
                    "policy_sha256": identity.get("policy_sha256"),
                    "operations": sorted(
                        operations, key=lambda row: str(row["operation_id"])
                    ),
                }
                if {
                    key: value
                    for key, value in stored_context.items()
                    if key != "source_refs"
                } != reconstructed_context:
                    errors.append(f"{role}_authorization_context_drifted")
    _verify_history_chains(
        authorization_record_path,
        trace_path,
        f"{role}_effects",
        errors,
    )
    receipt_paths = []
    refs = effects.get("authorization_receipt_refs")
    if not isinstance(refs, list):
        errors.append(f"{role}_authorization_receipt_refs_invalid")
    else:
        for index, ref in enumerate(refs):
            path = _resolve_verified_ref(
                ref,
                repository_root,
                f"{role}_authorization_receipt_{index}",
                errors,
            )
            if path is not None:
                receipt_paths.append(path)
    if trace_path is not None and reconstructed_context is not None and not errors:
        rows = [
            json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        try:
            observed = derive_effect_summary(
                rows,
                [_load_json(path) for path in receipt_paths],
                reconstructed_context,
            )
        except ValueError:
            errors.append(f"{role}_effect_derivation_failed")
        else:
            for key, value in observed.items():
                if effects.get(key) != value:
                    errors.append(f"{role}_effect_summary_drifted")
                    break
    return errors


def _check_interpretation_fact_binding(
    evidence: Mapping[str, object], repository_root: Path, role: str
) -> list[str]:
    errors: list[str] = []
    project_record = evidence.get("project_record")
    if not isinstance(project_record, Mapping):
        return [f"{role}_project_record_ref_invalid"]
    record_path = _resolve_verified_ref(
        project_record.get("ref"), repository_root, f"{role}_project_record", errors
    )
    binding = project_record.get("accepted_fact")
    if record_path is not None and isinstance(binding, Mapping):
        matched = _accepted_fact_event(record_path, str(binding.get("fact_id")))
        if matched is None:
            errors.append(f"{role}_accepted_fact_missing")
        else:
            event, fact = matched
            value = fact.get("value")
            model_output = value.get("model_output") if isinstance(value, Mapping) else None
            if (
                event.get("event_sha256") != binding.get("event_sha256")
                or canonical_sha256(value) != binding.get("value_sha256")
                or canonical_sha256(model_output)
                != binding.get("accepted_content_sha256")
                or binding.get("accepted_content_sha256")
                != evidence.get("candidate_source_sha256")
            ):
                errors.append(f"{role}_accepted_fact_binding_drifted")
            candidate = evidence.get("candidate")
            if not isinstance(model_output, Mapping) or not isinstance(
                candidate, Mapping
            ):
                errors.append(f"{role}_candidate_projection_invalid")
            elif role == "missing":
                if (
                    candidate.get("sufficiency")
                    != model_output.get("sufficiency")
                    or candidate.get("missing_fields")
                    != model_output.get("missing_fields")
                    or candidate.get("scientific_consequence")
                    != model_output.get("bounded_interpretation")
                    or candidate.get("result_statements")
                    != model_output.get("result_statements")
                ):
                    errors.append(f"{role}_candidate_projection_drifted")
            elif (
                candidate.get("sufficiency") != model_output.get("sufficiency")
                or candidate.get("result_statements")
                != model_output.get("result_statements")
                or candidate.get("limitations") != model_output.get("limitations")
                or candidate.get("cross_unit_numeric_ranking_performed")
                != model_output.get("cross_unit_numeric_ranking_performed")
                or candidate.get("zero_value_claim")
                != model_output.get("zero_value_claim")
            ):
                errors.append(f"{role}_candidate_projection_drifted")
    elif record_path is not None:
        errors.append(f"{role}_accepted_fact_binding_absent")
    return errors


def _check_recovery_snapshots(
    recovery: Mapping[str, object],
    missing: Mapping[str, object],
    repository_root: Path,
) -> list[str]:
    errors: list[str] = []
    project_ref = missing.get("project_record", {}).get("ref")  # type: ignore[union-attr]
    trace_ref = missing.get("run_trace", {}).get("ref")  # type: ignore[union-attr]
    record_path = _resolve_verified_ref(
        project_ref, repository_root, "recovery_project_record", errors
    )
    trace_path = _resolve_verified_ref(
        trace_ref, repository_root, "recovery_run_trace", errors
    )
    record_rows = []
    trace_rows = []
    if record_path is not None:
        record_rows = [
            json.loads(line)
            for line in record_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    if trace_path is not None:
        trace_rows = [
            json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    _verify_history_chains(
        record_path,
        trace_path,
        "recovery_source",
        errors,
    )
    for name in ("checkpoint", "resumed", "replayed"):
        row = recovery.get(name)
        if not isinstance(row, Mapping):
            errors.append(f"recovery_{name}_row_invalid")
            continue
        snapshot_path = _resolve_verified_ref(
            row.get("snapshot_ref"),
            repository_root,
            f"recovery_{name}_snapshot",
            errors,
        )
        if snapshot_path is None:
            continue
        snapshot = _load_json(snapshot_path)
        trace_verification = snapshot.get("run_trace")
        run_manifest = snapshot.get("run_manifest")
        manifest_body = dict(run_manifest) if isinstance(run_manifest, Mapping) else {}
        manifest_sha = manifest_body.pop("manifest_sha256", None)
        material_ref = (
            run_manifest.get("material_ref")
            if isinstance(run_manifest, Mapping)
            else None
        )
        if (
            snapshot.get("project_record_sha256")
            != row.get("project_record_sha256")
            or not isinstance(run_manifest, Mapping)
            or hashlib.sha256(
                json.dumps(
                    manifest_body,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            != manifest_sha
            or manifest_sha != row.get("run_manifest_sha256")
            or not isinstance(material_ref, Mapping)
            or material_ref.get("sha256") != row.get("input_sha256")
            or run_manifest.get("provider_policy_sha256")
            != row.get("provider_route_sha256")
            or run_manifest.get("permission_policy_sha256")
            != row.get("permission_policy_sha256")
            or run_manifest.get("provider_route") != row.get("provider_route")
            or run_manifest.get("fixture_id") != row.get("fixture_id")
            or not isinstance(trace_verification, Mapping)
            or trace_verification.get("trace_sha256") != row.get("run_trace_sha256")
            or trace_verification.get("event_count")
            != row.get("run_trace_event_count")
            or trace_verification.get("last_event_sha256")
            != row.get("run_trace_last_event_sha256")
            or trace_verification.get("run_id") != row.get("run_id")
        ):
            errors.append(f"recovery_{name}_snapshot_binding_drifted")
            continue
        record_index = next(
            (
                index
                for index, event in enumerate(record_rows)
                if event.get("event_sha256") == row.get("project_record_sha256")
            ),
            None,
        )
        trace_index = next(
            (
                index
                for index, event in enumerate(trace_rows)
                if event.get("event_sha256") == row.get("run_trace_last_event_sha256")
            ),
            None,
        )
        if record_index is None or trace_index is None:
            errors.append(f"recovery_{name}_history_prefix_missing")
            continue
        record_prefix = record_rows[: record_index + 1]
        trace_prefix = trace_rows[: trace_index + 1]
        observed_counts = {
            "accepted_events": sum(
                event.get("event_type") == "research.project.accepted_change"
                and event.get("payload", {}).get("candidate", {}).get("fact", {}).get(
                    "fact_id"
                )
                == "controlled-evaluation-candidate"
                for event in record_prefix
            ),
            "model_calls": sum(
                event.get("event_type") == "research.run.model.completed"
                for event in trace_prefix
            ),
            "tool_calls": sum(
                event.get("event_type") == "research.run.capability.requested"
                for event in trace_prefix
            ),
        }
        if any(row.get(key) != value for key, value in observed_counts.items()):
            errors.append(f"recovery_{name}_counts_not_trace_derived")
    return errors


def _check_secret_scan(
    receipt: Mapping[str, object], repository_root: Path, output_root: Path
) -> list[str]:
    errors: list[str] = []
    targets = receipt.get("targets")
    paths = []
    if not isinstance(targets, list):
        return ["secret_scan_targets_invalid"]
    expected_paths = {
        path.resolve()
        for path in Path(output_root).resolve().glob("**/projects/**/*")
        if path.is_file()
        and "__pycache__" not in path.parts
        # The Project Record writer lock is persistent synchronization
        # metadata, not research content or an execution artifact.  Scanning
        # it would make a read/rebuild mutate the frozen evidence target set.
        and path.name != ".project-record-write.lock"
    }
    for relative in (
        "complete/calculation-evidence.json",
        "complete/interpretation-evidence.json",
        "complete/interpretation-result.json",
        "missing/checkpoint.json",
        "missing/resumed.json",
        "missing/replayed.json",
        "missing/missing-evidence.json",
        "missing/missing-evaluation.json",
        "recovery/recovery-evidence.json",
        "recovery/recovery-evaluation.json",
        "recovery/failure-diagnostics.json",
    ):
        target = (Path(output_root).resolve() / relative)
        if target.is_file():
            expected_paths.add(target.resolve())
    for index, row in enumerate(targets):
        path = _resolve_verified_ref(
            row,
            repository_root,
            f"secret_scan_target_{index}",
            errors,
        )
        if path is not None and path.stat().st_size != row.get("bytes"):
            errors.append(f"secret_scan_target_{index}_size_drifted")
        if path is not None:
            paths.append(path)
    if set(paths) != expected_paths:
        errors.append("secret_scan_target_set_incomplete")
    if not errors:
        observed = scan_secret_material(repository_root, paths)
        if observed != receipt:
            errors.append("secret_scan_receipt_drifted")
    return errors


def check_complete_evidence_documents(
    calculation: Mapping[str, object],
    interpretation: Mapping[str, object],
    stored_report: Mapping[str, object],
    secret_scan: Mapping[str, object],
) -> list[str]:
    evaluator = CoreLCAQualityEvaluator(load_core_lca_quality_contract(CONTRACT_PATH))
    observed = evaluator.evaluate_complete(calculation, interpretation, secret_scan)
    errors = []
    if observed != stored_report:
        errors.append("quality_report_drifted")
    if (
        observed.get("edge_status") != "passed"
        or observed.get("status") not in {"pending_acceptance", "passed"}
        or observed.get("edge_failures") != []
    ):
        errors.append("complete_executable_edge_failed")
    failures = set(observed.get("quality_gate_failures") or [])
    if failures - PENDING_FINAL_GATES:
        errors.append("unexpected_quality_gate_failed")
    if observed.get("executed_external_writes") != 0:
        errors.append("external_write_detected")
    return errors


def check_complete_branch(output_root: Path, repository_root: Path = ROOT) -> dict:
    output_root = Path(output_root).resolve()
    repository_root = Path(repository_root).resolve()
    complete = output_root / "complete"
    calculation = _load_json(complete / "calculation-evidence.json")
    interpretation = _load_json(complete / "interpretation-evidence.json")
    report = _load_json(complete / "quality-report.json")
    secret_scan = _load_json(output_root / "evidence/secret-scan.json")
    receipt = _load_json(complete / "execution-receipt.json")
    errors = check_complete_evidence_documents(
        calculation, interpretation, report, secret_scan
    )
    errors.extend(_check_secret_scan(secret_scan, repository_root, output_root))
    errors.extend(check_content_bindings(calculation, interpretation, repository_root))
    errors.extend(_check_derived_effects(calculation, repository_root, "calculation"))
    errors.extend(_check_derived_effects(interpretation, repository_root, "interpretation"))
    if receipt.get("status") != "passed":
        errors.append("complete_execution_receipt_failed")
    if receipt.get("calculation_sha256") != canonical_sha256(calculation):
        errors.append("calculation_receipt_binding_drifted")
    if receipt.get("interpretation_sha256") != canonical_sha256(interpretation):
        errors.append("interpretation_receipt_binding_drifted")
    if receipt.get("quality_report_sha256") != canonical_sha256(report):
        errors.append("quality_receipt_binding_drifted")

    fixture_root = complete / "interpretation-fixture"
    manifest = _load_json(fixture_root / "fixture-manifest.json")
    visible = _load_json(fixture_root / "fixture/material-package.json")
    missing = _load_json(fixture_root / "fixture/missing-material-package.json")
    serialized = json.dumps([visible, missing], sort_keys=True).casefold()
    if "oracle" in serialized or "grader" in serialized:
        errors.append("hidden_evaluation_content_leaked")
    for row in manifest.get("registered_artifacts") or []:
        if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
            errors.append("fixture_manifest_row_invalid")
            continue
        path = fixture_root / row["path"]
        if not path.is_file() or _sha256(path) != row.get("sha256"):
            errors.append("fixture_artifact_drifted")

    for role, evidence in (
        ("calculation", calculation),
        ("interpretation", interpretation),
    ):
        record_ref = evidence.get("project_record", {}).get("ref")  # type: ignore[union-attr]
        trace_ref = evidence.get("run_trace", {}).get("ref")  # type: ignore[union-attr]
        for kind, ref in (("record", record_ref), ("trace", trace_ref)):
            if not isinstance(ref, Mapping) or not isinstance(ref.get("path"), str):
                errors.append(f"{role}_{kind}_ref_invalid")
                continue
            path = repository_root / ref["path"]
            if not path.is_file() or _sha256(path) != ref.get("sha256"):
                errors.append(f"{role}_{kind}_drifted")
        if isinstance(record_ref, Mapping) and isinstance(record_ref.get("path"), str):
            record = ProjectRecord.open_or_create_project(
                (repository_root / record_ref["path"]).parent
            )
            if record.rebuild_and_verify().get("status") != "passed":
                errors.append(f"{role}_project_record_replay_failed")
        if isinstance(trace_ref, Mapping) and isinstance(trace_ref.get("path"), str):
            trace_path = repository_root / trace_ref["path"]
            if trace_path.is_file():
                rows = [
                    json.loads(line)
                    for line in trace_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                run_ids = {row.get("run_id") for row in rows}
                if len(run_ids) != 1:
                    errors.append(f"{role}_run_trace_identity_invalid")
                else:
                    trace = RunTrace(trace_path, run_id=str(next(iter(run_ids))))
                    trace.verify()
                if role == "interpretation":
                    if sum(row.get("event_type") == "research.run.model.completed" for row in rows) != 1:
                        errors.append("interpretation_model_call_count_invalid")
                    if any(row.get("event_type") == "research.run.capability.requested" for row in rows):
                        errors.append("interpretation_unnecessary_tool_called")

    result = {
        "contract": "d18_core_lca_complete_acceptance/v1",
        "status": "passed" if not errors else "failed",
        "errors": sorted(set(errors)),
        "calculation_sha256": canonical_sha256(calculation),
        "interpretation_sha256": canonical_sha256(interpretation),
        "quality_report_sha256": canonical_sha256(report),
        "project_record_replay_verified": not any("replay_failed" in error for error in errors),
        "executed_external_writes": 0,
        "next_route": "exercise-core-lca-gap-failure-and-efficiency",
    }
    result["acceptance_sha256"] = canonical_sha256(result)
    return result


def check_boundary_package(
    output_root: Path, repository_root: Path = ROOT
) -> dict:
    """Verify bounded-gap, exactly-once recovery, and Evidence DAG linkage."""

    output_root = Path(output_root).resolve()
    repository_root = Path(repository_root).resolve()
    evaluator = CoreLCAQualityEvaluator(
        load_core_lca_quality_contract(CONTRACT_PATH)
    )
    missing_evidence = _load_json(output_root / "missing/missing-evidence.json")
    missing_stored = _load_json(output_root / "missing/missing-evaluation.json")
    recovery_evidence = _load_json(
        output_root / "recovery/recovery-evidence.json"
    )
    recovery_stored = _load_json(
        output_root / "recovery/recovery-evaluation.json"
    )
    calculation = _load_json(output_root / "complete/calculation-evidence.json")
    interpretation = _load_json(
        output_root / "complete/interpretation-evidence.json"
    )
    quality_stored = _load_json(output_root / "quality-report.json")
    execution_receipt = _load_json(
        output_root / "missing/execution-receipt.json"
    )
    measurement = _load_json(
        output_root / "core-lca-quality-efficiency-receipt.json"
    )
    reuse = _load_json(output_root / "evidence/evidence-reuse-receipt.json")
    locks = {
        "execution": _load_json(output_root / "evidence/execution-lock.json"),
        "scoring": _load_json(output_root / "evidence/scoring-lock.json"),
        "acceptance": _load_json(
            output_root / "evidence/quality-acceptance-lock.json"
        ),
    }
    errors: list[str] = []
    errors.extend(
        _check_interpretation_fact_binding(
            missing_evidence, repository_root, "missing"
        )
    )
    errors.extend(_check_derived_effects(missing_evidence, repository_root, "missing"))
    errors.extend(
        _check_recovery_snapshots(
            recovery_evidence, missing_evidence, repository_root
        )
    )
    missing_observed = evaluator.evaluate_missing(missing_evidence)
    recovery_observed = evaluator.evaluate_recovery(recovery_evidence)
    if missing_observed != missing_stored:
        errors.append("missing_evaluation_drifted")
    if missing_observed.get("status") != "formal_limited_result_accepted":
        errors.append("bounded_gap_not_accepted")
    if recovery_observed != recovery_stored:
        errors.append("recovery_evaluation_drifted")
    if recovery_observed.get("status") != "passed":
        errors.append("recovery_not_exactly_once")

    combined_interpretation = json.loads(json.dumps(interpretation))
    combined_interpretation["cross_branch_gates"] = {
        "missing_data_boundary_verified": True
    }
    secret_scan = _load_json(output_root / "evidence/secret-scan.json")
    errors.extend(_check_secret_scan(secret_scan, repository_root, output_root))
    quality_observed = evaluator.evaluate_complete(
        calculation, combined_interpretation, secret_scan
    )
    if quality_observed != quality_stored:
        errors.append("combined_quality_report_drifted")
    expected_pending = {
        "clean_review_p1_zero_p2_zero",
        "full_repository_verification_passed",
    }
    if set(quality_observed.get("quality_gate_failures") or []) != expected_pending:
        errors.append("unexpected_pre_acceptance_quality_state")

    try:
        if classify_core_lca_evidence_changes([], locks) != "no_evidence_invalidation":
            errors.append("evidence_lock_empty_change_route_invalid")
    except ValueError:
        errors.append("evidence_lock_linkage_invalid")
    for layer, lock in locks.items():
        for row in lock.get("inputs") or []:
            if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
                errors.append(f"{layer}_lock_input_invalid")
                continue
            path = repository_root / row["path"]
            if (
                not path.is_file()
                or _sha256(path) != row.get("sha256")
                or path.stat().st_size != row.get("bytes")
            ):
                errors.append(f"{layer}_lock_input_drifted")

    execution_basis = locks.get("execution", {}).get("basis")
    if not isinstance(execution_basis, Mapping):
        errors.append("execution_lock_basis_invalid")
    else:
        raw_refs = execution_basis.get("raw_execution_refs")
        git_objects = execution_basis.get("immutable_execution_git_objects")
        raw_execution_source_paths = execution_basis.get(
            "raw_execution_source_paths"
        )
        if (
            not isinstance(raw_refs, Mapping)
            or canonical_sha256(raw_refs)
            != execution_basis.get("raw_execution_refs_sha256")
        ):
            errors.append("execution_lock_raw_refs_drifted")
        if not isinstance(git_objects, list) or not git_objects:
            errors.append("execution_lock_git_objects_absent")
        elif (
            not isinstance(raw_execution_source_paths, list)
            or set(raw_execution_source_paths)
            != {
                "lca_research_evaluation/core_lca_quality_efficiency.py",
                "scripts/run_core_lca_quality_efficiency_evaluation.py",
            }
        ):
            errors.append("execution_lock_raw_source_paths_invalid")
        else:
            git_object_map = {
                item.get("path"): item
                for item in git_objects
                if isinstance(item, Mapping) and isinstance(item.get("path"), str)
            }
            execution_inputs = locks.get("execution", {}).get("inputs")
            execution_input_map = {
                item.get("path"): item
                for item in execution_inputs
                if isinstance(execution_inputs, list)
                and isinstance(item, Mapping)
                and isinstance(item.get("path"), str)
            }
            expected_git_paths = {
                *execution_input_map,
                *raw_execution_source_paths,
            }
            if set(git_object_map) != expected_git_paths:
                errors.append("execution_lock_git_object_closure_incomplete")
            for index, item in enumerate(git_objects):
                if not isinstance(item, Mapping):
                    errors.append(f"execution_lock_git_object_{index}_invalid")
                    continue
                payload = _read_git_object(
                    repository_root,
                    item,
                    f"execution_lock_git_object_{index}",
                    errors,
                )
                if payload is None:
                    continue
                if item.get("commit") != execution_basis.get(
                    "accepted_execution_code_basis"
                ):
                    errors.append(
                        f"execution_lock_git_object_{index}_basis_drifted"
                    )
                live_input = execution_input_map.get(item.get("path"))
                if isinstance(live_input, Mapping) and (
                    live_input.get("sha256") != item.get("sha256")
                    or live_input.get("bytes") != item.get("bytes")
                ):
                    errors.append(
                        f"execution_lock_git_object_{index}_live_input_drifted"
                    )

    reuse_body = dict(reuse)
    reuse_sha = reuse_body.pop("receipt_sha256", None)
    if canonical_sha256(reuse_body) != reuse_sha:
        errors.append("evidence_reuse_receipt_drifted")
    if (
        reuse.get("status") != "accepted"
        or reuse.get("cross_version_splicing") is not False
        or reuse.get("failed_rows_preserved") is not True
        or reuse.get("rerun_route")
        not in {"no_evidence_invalidation", "regrade_existing_traces"}
        or reuse.get("execution_reusable") is not True
        or reuse.get("execution_lock_sha256")
        != locks["execution"].get("lock_sha256")
    ):
        errors.append("evidence_reuse_not_admissible")
    migration_path = _resolve_verified_ref(
        reuse.get("reviewed_execution_basis_migration_ref"),
        repository_root,
        "reviewed_execution_basis_migration",
        errors,
    )
    if migration_path is not None:
        migration = _load_json(migration_path)
        migration_body = dict(migration)
        migration_sha = migration_body.pop("receipt_sha256", None)
        if (
            canonical_sha256(migration_body) != migration_sha
            or migration.get("contract")
            != "d18_core_lca_reviewed_execution_basis_migration/v1"
            or migration.get("status")
            != "acceptance_hardening_of_rejected_lock"
            or migration.get("adopted_execution_lock_sha256")
            != locks["execution"].get("lock_sha256")
            or not isinstance(execution_basis, Mapping)
            or migration.get("raw_execution_refs_sha256")
            != execution_basis.get("raw_execution_refs_sha256")
            or migration.get("immutable_execution_git_objects")
            != execution_basis.get("immutable_execution_git_objects")
            or migration.get("product_or_model_visible_execution_rerun") is not False
        ):
            errors.append("reviewed_execution_basis_migration_invalid")

        expected_claim_ceiling = (
            "reconstructs_execution_identity_only; "
            "does_not_claim_new_scientific_execution"
        )
        if (
            migration.get("model_calls_added") != 0
            or migration.get("tool_calls_added") != 0
            or migration.get("external_writes_added") != 0
            or migration.get("claim_ceiling") != expected_claim_ceiling
        ):
            errors.append("reviewed_execution_basis_migration_effects_invalid")

        rejected = migration.get("rejected_lock_git_objects")
        expected_layers = {"execution", "scoring", "acceptance"}
        if not isinstance(rejected, Mapping) or set(rejected) != expected_layers:
            errors.append("reviewed_execution_basis_rejected_locks_invalid")
        else:
            for layer in sorted(expected_layers):
                row = rejected.get(layer)
                git_ref = row.get("git_object") if isinstance(row, Mapping) else None
                if not isinstance(git_ref, Mapping):
                    errors.append(
                        f"reviewed_execution_basis_rejected_{layer}_invalid"
                    )
                    continue
                payload = _read_git_object(
                    repository_root,
                    git_ref,
                    f"reviewed_execution_basis_rejected_{layer}",
                    errors,
                )
                if payload is None:
                    continue
                try:
                    rejected_lock = json.loads(payload)
                except json.JSONDecodeError:
                    errors.append(
                        f"reviewed_execution_basis_rejected_{layer}_invalid"
                    )
                    continue
                if (
                    not isinstance(rejected_lock, Mapping)
                    or rejected_lock.get("lock_sha256") != row.get("lock_sha256")
                    or git_ref.get("commit")
                    != execution_basis.get("accepted_execution_code_basis")
                ):
                    errors.append(
                        f"reviewed_execution_basis_rejected_{layer}_lock_drifted"
                    )

        diagnostic_path = _resolve_verified_ref(
            migration.get("change_diagnostic_ref"),
            repository_root,
            "reviewed_execution_basis_diagnostic",
            errors,
        )
        if diagnostic_path is not None:
            diagnostic = _load_json(diagnostic_path)
            diagnostic_body = dict(diagnostic)
            diagnostic_sha = diagnostic_body.pop("diagnostic_sha256", None)
            comparison = diagnostic.get("comparison")
            if (
                canonical_sha256(diagnostic_body) != diagnostic_sha
                or diagnostic.get("status")
                != "fail_closed_requires_explicit_basis_adoption"
                or not isinstance(comparison, Mapping)
                or comparison.get("execution_reusable") is not False
                or comparison.get("candidate_execution_lock_sha256")
                != locks["execution"].get("lock_sha256")
                or diagnostic.get("raw_execution_refs_sha256")
                != execution_basis.get("raw_execution_refs_sha256")
                or diagnostic.get("product_or_model_visible_execution_rerun")
                is not False
            ):
                errors.append("reviewed_execution_basis_diagnostic_invalid")

        checks = migration.get("raw_execution_git_object_checks")
        expected_raw_paths: dict[str, Mapping[str, object]] = {}
        def collect_raw_refs(value: object) -> None:
            if isinstance(value, Mapping):
                if isinstance(value.get("path"), str) and isinstance(
                    value.get("sha256"), str
                ):
                    expected_raw_paths[value["path"]] = value
                for child in value.values():
                    collect_raw_refs(child)
            elif isinstance(value, list):
                for child in value:
                    collect_raw_refs(child)

        if isinstance(execution_basis, Mapping):
            raw_execution_refs = execution_basis.get("raw_execution_refs")
            if isinstance(raw_execution_refs, Mapping):
                collect_raw_refs(raw_execution_refs)
        if not isinstance(checks, list):
            errors.append("reviewed_execution_basis_raw_checks_invalid")
        else:
            check_map = {
                row.get("path"): row
                for row in checks
                if isinstance(row, Mapping) and isinstance(row.get("path"), str)
            }
            if set(check_map) != set(expected_raw_paths):
                errors.append("reviewed_execution_basis_raw_checks_incomplete")
            for index, (path, ref) in enumerate(sorted(expected_raw_paths.items())):
                row = check_map.get(path)
                if not isinstance(row, Mapping):
                    continue
                payload = _read_git_object(
                    repository_root,
                    row,
                    f"reviewed_execution_basis_raw_{index}",
                    errors,
                )
                live_path = (repository_root / path).resolve()
                if (
                    payload is None
                    or row.get("commit")
                    != execution_basis.get("accepted_execution_code_basis")
                    or row.get("sha256") != ref.get("sha256")
                    or not live_path.is_file()
                    or _sha256(live_path) != row.get("sha256")
                    or live_path.stat().st_size != row.get("bytes")
                ):
                    errors.append(f"reviewed_execution_basis_raw_{index}_drifted")

    metrics = measurement.get("metrics")
    basis = measurement.get("measurement_basis")
    derived_metrics = None
    if isinstance(basis, Mapping):
        calculation_trace_path = _resolve_verified_ref(
            basis.get("calculation_trace_ref"),
            repository_root,
            "measurement_calculation_trace",
            errors,
        )
        complete_trace_path = _resolve_verified_ref(
            basis.get("complete_interpretation_trace_ref"),
            repository_root,
            "measurement_complete_trace",
            errors,
        )
        missing_trace_path = _resolve_verified_ref(
            basis.get("missing_interpretation_trace_ref"),
            repository_root,
            "measurement_missing_trace",
            errors,
        )
        diagnostics_path = _resolve_verified_ref(
            basis.get("failure_diagnostics_ref"),
            repository_root,
            "measurement_failure_diagnostics",
            errors,
        )
        if all(
            path is not None
            for path in (
                calculation_trace_path,
                complete_trace_path,
                missing_trace_path,
                diagnostics_path,
            )
        ):
            def rows(path: Path) -> list[dict]:
                return [
                    json.loads(line)
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]

            derived_metrics = derive_efficiency_metrics(
                calculation_trace=rows(calculation_trace_path),  # type: ignore[arg-type]
                interpretation_traces=[
                    rows(complete_trace_path),  # type: ignore[arg-type]
                    rows(missing_trace_path),  # type: ignore[arg-type]
                ],
                failure_diagnostics=_load_json(diagnostics_path),  # type: ignore[arg-type]
            )
    if (
        measurement.get("contract")
        != "core_lca_quality_efficiency_measurement/v1"
        or measurement.get("status")
        != "accepted_measurement_with_known_critical_path_gap"
        or measurement.get("quality_first") is not True
        or not isinstance(metrics, Mapping)
        or derived_metrics != metrics
        or not isinstance(basis, Mapping)
        or basis.get("complete_wall_clock_status")
        != "not_retrospectively_invented"
        or basis.get("accepted_result_timing_status") != "not_measured"
        or basis.get("delivered_result_timing_status") != "not_measured"
        or metrics.get("accepted_result_ms") is not None
        or metrics.get("delivered_result_ms") is not None
        or basis.get("primary_provider_duration_ms")
        != metrics.get("primary_provider_duration_ms")
        or basis.get("provider_model_duration_ms_total")
        != metrics.get("provider_model_duration_ms_total")
        or basis.get("missing_branch_wall_status") != "not_measured"
        or basis.get("missing_branch_wall_ms") is not None
    ):
        errors.append("quality_first_measurement_invalid")

    if execution_receipt.get("status") != "passed":
        errors.append("boundary_execution_receipt_failed")
    if (
        execution_receipt.get("missing_evaluation_sha256")
        != canonical_sha256(missing_stored)
        or execution_receipt.get("recovery_evaluation_sha256")
        != canonical_sha256(recovery_stored)
        or execution_receipt.get("quality_report_sha256")
        != canonical_sha256(quality_stored)
    ):
        errors.append("boundary_execution_receipt_binding_drifted")
    if execution_receipt.get("executed_external_writes") != 0:
        errors.append("boundary_external_write_detected")

    result = {
        "contract": "d18_core_lca_boundary_acceptance/v1",
        "status": "passed" if not errors else "failed",
        "errors": sorted(set(errors)),
        "missing_evaluation_sha256": canonical_sha256(missing_stored),
        "recovery_evaluation_sha256": canonical_sha256(recovery_stored),
        "quality_report_sha256": canonical_sha256(quality_stored),
        "execution_lock_sha256": locks["execution"].get("lock_sha256"),
        "executed_external_writes": 0,
        "next_route": "measure-quality-first-efficiency-and-clean-accept",
    }
    result["acceptance_sha256"] = canonical_sha256(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "outputs/d18-core-lca-quality-efficiency",
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--stage", choices=("complete", "boundaries"), default="complete"
    )
    args = parser.parse_args()
    result = (
        check_complete_branch(args.output_root)
        if args.stage == "complete"
        else check_boundary_package(args.output_root)
    )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
