#!/usr/bin/env python3
"""Run one controlled evaluation branch through the existing D-18 ports."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from lca_research_kernel.assurance import AssuranceBoundary
from lca_research_kernel.capability_gateway import (
    CapabilityGateway,
    LocalDeterministicCapability,
)
from lca_research_kernel.controlled_research_evaluation import (
    ControlledResearchEvaluation,
    _atomic_write,
)
from lca_research_kernel.model_runtime import (
    build_model_gateway,
    load_model_runtime_contract,
)


def _provider_policy(contract: dict) -> dict:
    active = sorted(
        (
            row
            for row in contract["provider_catalog"]["providers"]
            if row["admission_state"] == "active_verified"
        ),
        key=lambda row: (row["priority"], row["route_id"]),
    )
    if not active:
        raise ValueError("no active verified provider is available")
    return {
        "policy_id": "controlled-evaluation-provider-policy-v1",
        "primary_route": {
            "route_id": active[0]["route_id"],
            "adapter_id": active[0]["route_id"],
            "admission_state": active[0]["admission_state"],
            "provider_catalog_sha256": contract["provider_catalog_sha256"],
        },
        "failover_routes": [
            {
                "route_id": row["route_id"],
                "adapter_id": row["route_id"],
                "admission_state": row["admission_state"],
            }
            for row in active[1:]
        ],
    }


def _permission_policy(root: Path) -> dict:
    contract = json.loads(
        (
            root
            / "outputs/d18-controlled-research-evaluation/control/evaluation-contract.json"
        ).read_text(encoding="utf-8")
    )
    return {
        "policy_id": "controlled-evaluation-permission-policy-v1",
        "allowed_effects": contract["allowed_effects"],
        "forbidden_effects": contract["forbidden_effects"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--branch",
        choices=("complete_material", "missing_critical_field"),
        required=True,
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--fixture-root", type=Path)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--credential-env-file", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--recorded-at", required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--stop-after-model-candidate", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    fixture_root = (
        args.fixture_root.resolve()
        if args.fixture_root
        else root / "outputs/d18-controlled-research-evaluation"
    )
    contract = load_model_runtime_contract(root)
    from probe_model_providers import load_credential_environment

    runtime_environment = dict(os.environ)
    runtime_environment.update(
        load_credential_environment(
            args.credential_env_file,
            contract["provider_catalog"],
        )
    )
    gateway = build_model_gateway(
        root,
        runtime_environment,
        execution_mode="production",
        clock=lambda: args.recorded_at,
        sleep=lambda _: None,
    )
    capabilities = CapabilityGateway(
        {
            "local.deterministic.content-hash": LocalDeterministicCapability(
                "local.deterministic.content-hash",
                lambda value: {"content_sha256": "not_executed_in_this_evaluation"},
            )
        }
    )
    runner = ControlledResearchEvaluation(
        repository_root=root,
        workspace=args.workspace,
        fixture_root=fixture_root,
        model_gateway=gateway,
        capability_gateway=capabilities,
        assurance=AssuranceBoundary(),
        provider_policy=_provider_policy(contract),
        permission_policy=_permission_policy(root),
        run_id=args.run_id,
        clock=lambda: args.recorded_at,
    )
    result = runner.run(
        args.branch,
        stop_after_model_candidate=args.stop_after_model_candidate,
    )
    _atomic_write(args.result, result)
    print(
        json.dumps(
            {
                "status": result["candidate_package_status"],
                "branch": result["branch"],
                "research_id": result["research_id"],
                "project_record_sha256": result["project_record_sha256"],
                "run_trace_sha256": result["run_trace"]["trace_sha256"],
                "executed_external_writes": result["executed_external_writes"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    main()
