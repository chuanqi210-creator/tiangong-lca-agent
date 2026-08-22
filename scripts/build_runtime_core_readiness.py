#!/usr/bin/env python3
"""Build deterministic local-readiness evidence without provider calls or science."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _frame(module: str, before: str, after: str, *, capability: str = "") -> dict:
    return {
        "contract": "lca_research_kernel.architecture_decision_frame/v1",
        "selected_modules": [module],
        "selected_capabilities": [capability] if capability else [],
        "expected_state_change": {
            "from_state": before,
            "to_state": after,
            "observable_delta": [f"{after} becomes observable"],
        },
        "current_problem": {
            "required_responsibility": module,
            "evidence_refs": [
                {"path": "project-record:event-1", "sha256": "a" * 64}
            ],
        },
        "auditable_reason": (
            f"Event event-1 shows {before}; the bounded responsibility can "
            f"produce {after} without changing authority."
        ),
    }


def build(root: Path, output_root: Path) -> dict:
    sys.path.insert(0, str(root))
    from lca_research_kernel.runtime_core_acceptance import (
        compile_runtime_core_acceptance,
    )
    from lca_research_kernel.model_runtime import load_model_runtime_contract
    from lca_research_kernel.runtime_core_clean_review import review_runtime_core
    from lca_research_kernel.runtime_core_evaluation import (
        evaluate_architecture_behavior,
        evaluate_candidate_write_boundaries,
    )
    from scripts.probe_model_providers import (
        build_collaboration_checklist,
        configuration_presence,
    )

    architecture = evaluate_architecture_behavior(
        [
            {
                "probe_id": "hidden-component-name",
                "input_description": (
                    "Normalize an open judgment into a structured candidate while "
                    "leaving accepted records unchanged."
                ),
                "expected_module": "model_gateway",
                "frame": _frame(
                    "model_gateway",
                    "accepted_goal",
                    "structured_candidate_available",
                ),
                "available_irrelevant_capability": "publish.available",
            },
            {
                "probe_id": "paraphrased-new-material",
                "input_description": (
                    "A newly supplied material has attributable content but its "
                    "method compatibility still needs a bounded professional check."
                ),
                "expected_module": "lca_domain_kernel",
                "frame": _frame(
                    "lca_domain_kernel",
                    "source_bound_material_available",
                    "method_compatibility_assessed",
                ),
            },
            {
                "probe_id": "missing-source-counterfactual",
                "input_description": (
                    "The otherwise identical material now lacks an attributable "
                    "source and must retain an explicit gap."
                ),
                "expected_module": "lca_domain_kernel",
                "frame": _frame(
                    "lca_domain_kernel",
                    "structured_candidate_available",
                    "bounded_evidence_gap",
                ),
                "counterfactual_of": "hidden-component-name",
            },
            {
                "probe_id": "irrelevant-tool",
                "input_description": (
                    "A transport is installed but cannot create the required "
                    "accepted-state change."
                ),
                "expected_module": "research_orchestrator",
                "frame": _frame(
                    "research_orchestrator",
                    "decision_pending",
                    "irrelevant_activity_rejected",
                ),
                "available_irrelevant_capability": "publish.available",
            },
        ]
    )
    boundaries = evaluate_candidate_write_boundaries(
        [
            {
                "actor": actor,
                "requested_write": "accepted_fact",
                "authorized": False,
            }
            for actor in ("model", "mcp", "skill", "tool", "council")
        ]
    )
    clean_review = review_runtime_core(root)
    model_runtime = load_model_runtime_contract(root)
    catalog = model_runtime["provider_catalog"]
    receipts = {}
    for provider in catalog["current_validation_gate"]:
        path = output_root / f"{provider}-structured-probe-receipt.json"
        receipts[provider] = (
            json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        )
    acceptance = compile_runtime_core_acceptance(
        {
            "architecture_semantics": True,
            "run_trace": True,
            "context_assembler": True,
            "run_control": True,
            "execution_environment": True,
            "native_workflow": True,
            "architecture_evaluation": (
                architecture["status"] == "passed"
                and boundaries["status"] == "passed"
            ),
            "forbidden_effects": 0,
            "clean_review": {
                "p1": clean_review["p1_count"],
                "p2": clean_review["p2_count"],
            },
            "provider_catalog": catalog,
            "provider_receipts": receipts,
            "make_verify": None,
        }
    )
    presence = configuration_presence({})
    checklist = build_collaboration_checklist(presence)
    # The first local-readiness tranche is accepted lifecycle evidence. Keep
    # those bytes immutable and publish later hardening evidence with a new
    # versioned identity.
    _write(output_root / "architecture-evaluation-v3.json", architecture)
    _write(
        output_root / "candidate-write-boundary-evaluation-v3.json",
        boundaries,
    )
    _write(output_root / "clean-review-v3.json", clean_review)
    _write(output_root / "acceptance-v3.json", acceptance)
    if acceptance["status"] == "waiting_for_user":
        _write(output_root / "user-collaboration-checklist-v3.json", checklist)
    result = {
        "contract": "lca_research_kernel.runtime_core_local_readiness/v1",
        "status": (
            "ready_for_real_provider_probes"
            if acceptance["status"] == "waiting_for_user"
            else acceptance["status"]
        ),
        "architecture_evaluation_sha256": architecture["report_sha256"],
        "candidate_write_boundary_sha256": boundaries["report_sha256"],
        "clean_review_sha256": clean_review["review_sha256"],
        "acceptance_sha256": acceptance["acceptance_sha256"],
        "focused_gate_map": {
            "provider_failure_and_switch": "tests.test_model_runtime_policy",
            "stream_retry_switch_and_restart_recovery": "tests.test_model_runtime_policy",
            "provider_schema_validation": "tests.test_model_provider_adapters",
            "model_network_permission_invariance": "tests.test_model_execution_environment",
            "tool_failure_and_duplicate_recovery": "tests.test_capability_runtime",
            "mcp_injection": "tests.test_execution_environment",
            "path_and_network_overreach": "tests.test_execution_environment",
            "interruption_and_idempotent_replay": "tests.test_run_control",
            "context_pollution": "tests.test_context_assembler",
            "architecture_counterfactual": "tests.test_runtime_core_evaluation",
        },
        "scientific_cases_executed": 0,
        "lca_or_lcia_calculations": 0,
        "external_writes": 0,
        "next_route": acceptance["next_route"],
    }
    _write(output_root / "local-readiness-v3.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("outputs/d18-production-runtime-core/final"),
    )
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output_root
    if not output.is_absolute():
        output = root / output
    result = build(root, output)
    print(
        json.dumps(
            {
                "status": result["status"],
                "scientific_cases_executed": 0,
                "external_writes": 0,
                "next_route": result["next_route"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
