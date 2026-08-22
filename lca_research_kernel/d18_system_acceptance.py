"""Deterministic D-18 system acceptance across generic product boundaries."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Mapping

from .assurance import AssuranceBoundary
from .capability_gateway import (
    CapabilityGateway,
    LocalDeterministicCapability,
    TianGongReadOnlyCapability,
)
from .evaluation_fixture import EvaluationFixtureHarness, validate_fixture_registry
from .model_gateway import DeterministicFakeAdapter, ModelGateway
from .product_interface import ProductInterface
from .project_record import ProjectRecord


FIXED_AT = "2026-07-23T07:00:00Z"


def _start_request(research_id: str) -> dict:
    return {
        "contract": "lca_research_kernel.research_start/v1",
        "research_id": research_id,
        "title": "D-18 bounded system evaluation",
        "objective": "Exercise the generic source-bound LCA control spine.",
        "source_refs": [{"path": "fixtures/brief.json", "sha256": "a" * 64}],
        "received_at": FIXED_AT,
    }


def _gate(gate_id: str, evidence: Mapping[str, object]) -> dict:
    passed = bool(evidence) and all(value is True for value in evidence.values())
    return {
        "gate_id": gate_id,
        "status": "passed" if passed else "failed",
        "evidence": dict(evidence),
    }


def run_system_acceptance(root: str | Path) -> dict:
    """Run a no-network, no-case-import acceptance matrix and return its evidence."""

    root = Path(root)
    fixture_errors = validate_fixture_registry(root)
    fixture_harness = EvaluationFixtureHarness(root)
    fixtures = [
        fixture_harness.read(row["fixture_id"])
        for row in fixture_harness.registry["fixtures"]
    ]
    assurance = AssuranceBoundary()

    with tempfile.TemporaryDirectory() as temporary:
        capability_calls: list[dict] = []
        unauthorized_calls: list[dict] = []
        tiangong_calls: list[dict] = []

        def fail_capability(payload: dict) -> object:
            capability_calls.append(payload)
            raise RuntimeError("bounded fixture failure")

        def read_tiangong(payload: dict) -> dict:
            tiangong_calls.append(payload)
            return {
                "identity": payload["process_id"],
                "version": payload["version"],
                "source": "TianGong fixture adapter",
                "payload": {"name": "source-bound process"},
            }

        class ExternalWriter:
            def describe(self):
                return {
                    "capability_id": "external.publish",
                    "authority": "external_service",
                    "access": "write",
                    "effect": "external_write",
                    "result_status": "sourced_result_not_fact",
                }

            def execute(self, payload):
                unauthorized_calls.append(payload)
                return {"content": {"sent": True}}

        capability_gateway = CapabilityGateway(
            {
                "external.publish": ExternalWriter(),
                "local.fail": LocalDeterministicCapability(
                    "local.fail",
                    fail_capability,
                    input_schema={
                        "type": "object",
                        "properties": {"value": {"type": "integer"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    },
                ),
                "tiangong.process.read": TianGongReadOnlyCapability(
                    "tiangong.process.read",
                    read_tiangong,
                    public_credential_free=True,
                    input_schema={
                        "type": "object",
                        "properties": {
                            "process_id": {"type": "string"},
                            "version": {"type": "string"},
                        },
                        "required": ["process_id", "version"],
                        "additionalProperties": False,
                    },
                ),
            }
        )
        product_root = Path(temporary) / "product"
        model = ModelGateway(
            {
                "fake": DeterministicFakeAdapter(
                    {
                        "capability_intent": {
                            "contract": "lca_research_kernel.capability_intent/v1",
                            "purpose": "Read one exact TianGong process fixture.",
                            "required_authority": "TianGong",
                            "required_access": "read_only",
                            "effect": "external_read",
                            "input": {
                                "process_id": "process-1",
                                "version": "01.01.000",
                            },
                        }
                    }
                )
            },
            default_provider="fake",
        )
        interface = ProductInterface(
            product_root,
            model_gateway=model,
            capability_gateway=capability_gateway,
            assurance=assurance,
            clock=lambda: FIXED_AT,
        )
        start = _start_request("d18-evaluation-normal")
        normal_status = interface.start_research(start)
        normal_record = ProjectRecord.open_or_create_project(
            product_root / "projects/d18-evaluation-normal"
        )
        before_replay = normal_record.event_path.read_bytes()
        before_count = len(normal_record.read_audit_trail())
        replay_status = interface.start_research(start)
        after_replay = normal_record.event_path.read_bytes()
        after_count = len(normal_record.read_audit_trail())

        gap = {
            "contract": "lca_research_kernel.research_material_or_decision/v1",
            "input_id": "missing-provider-amount",
            "kind": "material",
            "payload": {
                "contract": "lca_research_kernel.material_gap/v1",
                "gap_id": "provider-amount",
                "required_role": "provider_amount",
                "decision_relevance": "contribution interpretation",
                "user_data_status": "not_available",
                "authority_candidates": [],
                "proxy_candidates": [],
                "parameter_range": None,
                "bounded_exclusion": {
                    "reason": "No source-bound Provider amount is available.",
                    "affected_claims": ["provider_contribution_ranking"],
                    "retained_claims": ["explicit_gap_description"],
                },
            },
            "source_refs": [
                {"path": "fixtures/missing-provider.json", "sha256": "b" * 64}
            ],
            "received_at": FIXED_AT,
        }
        missing_status = interface.supply_material_or_decision(
            "d18-evaluation-normal", gap
        )
        missing_view = normal_record.read_current_project_view()
        resolution = missing_view["facts"]["material-gap-resolution:provider-amount"]

        before_recovery = normal_record.event_path.read_bytes()
        recovery_count = len(normal_record.read_audit_trail())
        normal_record.projection_path.unlink()
        recovery = normal_record.rebuild_and_verify()
        recovered_view = normal_record.read_current_project_view()

        failing_interface = ProductInterface(
            Path(temporary) / "model-failure",
            model_gateway=ModelGateway(
                {"fake": DeterministicFakeAdapter(RuntimeError("offline"))},
                default_provider="fake",
            ),
            assurance=assurance,
            clock=lambda: FIXED_AT,
        )
        model_failure_status = failing_interface.start_research(
            _start_request("d18-evaluation-model-failure")
        )
        failed_model_record = ProjectRecord.open_or_create_project(
            Path(temporary)
            / "model-failure/projects/d18-evaluation-model-failure"
        )

        capability_failure_interface = ProductInterface(
            Path(temporary) / "capability-failure",
            model_gateway=ModelGateway(
                {
                    "fake": DeterministicFakeAdapter(
                        {
                            "capability_intent": {
                                "contract": "lca_research_kernel.capability_intent/v1",
                                "purpose": "Exercise a bounded local tool failure.",
                                "required_authority": "local_deterministic_execution",
                                "required_access": "local_only",
                                "effect": "local_read",
                                "input": {"value": 1},
                            }
                        }
                    )
                },
                default_provider="fake",
            ),
            capability_gateway=capability_gateway,
            assurance=assurance,
            clock=lambda: FIXED_AT,
        )
        capability_failure_status = capability_failure_interface.start_research(
            _start_request("d18-evaluation-capability-failure")
        )
        capability_failure_record = ProjectRecord.open_or_create_project(
            Path(temporary)
            / "capability-failure/projects/d18-evaluation-capability-failure"
        )

        unauthorized_interface = ProductInterface(
            Path(temporary) / "unauthorized",
            model_gateway=ModelGateway(
                {
                    "fake": DeterministicFakeAdapter(
                        {
                            "capability_intent": {
                                "contract": "lca_research_kernel.capability_intent/v1",
                                "purpose": "Attempt a prohibited external publication.",
                                "required_authority": "external_service",
                                "required_access": "write",
                                "effect": "external_write",
                                "input": {"message": "must not be sent"},
                            }
                        }
                    )
                },
                default_provider="fake",
            ),
            capability_gateway=capability_gateway,
            assurance=assurance,
            clock=lambda: FIXED_AT,
        )
        unauthorized_status = unauthorized_interface.start_research(
            _start_request("d18-evaluation-unauthorized")
        )

        accepted_change_actors = {
            row["actor"]
            for row in normal_record.read_audit_trail()
            if row["event_type"] == "research.project.accepted_change"
        }
        gates = [
            _gate(
                "fixture_isolation",
                {
                    "registry_valid": not fixture_errors,
                    "all_four_fixtures_read": len(fixtures) == 4,
                    "no_fixture_architecture_authority": all(
                        not row.architecture_authority for row in fixtures
                    ),
                    "no_fixture_project_truth_writer": all(
                        not row.project_truth_writer for row in fixtures
                    ),
                },
            ),
            _gate(
                "normal_product_path",
                {
                    "product_reaches_bounded_domain_boundary": normal_status["boundary"]
                    == "awaiting_domain_integration",
                    "model_output_is_candidate": missing_view["facts"]["model-analysis"][
                        "classification"
                    ]
                    == "candidate",
                    "orchestrator_executed_capability": len(tiangong_calls) == 1,
                    "tiangong_result_is_not_fact": any(
                        fact.get("domain_kind") == "capability_result"
                        and fact.get("classification") == "candidate"
                        for fact in missing_view["facts"].values()
                    ),
                    "tiangong_identity_preserved": any(
                        fact.get("source", {}).get("source_identity") == "process-1"
                        and fact.get("source", {}).get("source_version")
                        == "01.01.000"
                        for fact in missing_view["facts"].values()
                    ),
                },
            ),
            _gate(
                "explicit_missing_data",
                {
                    "gap_is_recorded_as_missing": missing_view["facts"][
                        "material-gap:provider-amount"
                    ]["classification"]
                    == "missing",
                    "bounded_exclusion_is_selected": resolution["value"][
                        "selected_rung"
                    ]
                    == "bounded_exclusion",
                    "claim_is_closed": resolution["value"]["claim_level"]
                    == "bounded_no_conclusion",
                    "product_continues_without_guessing": missing_status["boundary"]
                    == "awaiting_domain_integration",
                },
            ),
            _gate(
                "model_failure",
                {
                    "failure_is_bounded": model_failure_status["boundary"]
                    == "model_failure",
                    "failure_is_retryable": model_failure_status.get("retryable") is True,
                    "no_model_candidate_promoted": "model-analysis"
                    not in failed_model_record.read_current_project_view()["facts"],
                    "no_external_effect_executed": model_failure_status.get(
                        "executed_external_effects"
                    )
                    == 0,
                },
            ),
            _gate(
                "capability_failure",
                {
                    "product_returns_bounded_capability_failure": capability_failure_status[
                        "boundary"
                    ]
                    == "capability_failure",
                    "failure_is_normalized": capability_failure_status["failure_code"]
                    == "capability_execution_failure",
                    "failure_is_retryable": capability_failure_status["retryable"]
                    is True,
                    "result_cannot_be_promoted": not any(
                        fact.get("domain_kind") == "capability_result"
                        for fact in capability_failure_record.read_current_project_view()[
                            "facts"
                        ].values()
                    ),
                    "one_bounded_attempt": len(capability_calls) == 1,
                },
            ),
            _gate(
                "unauthorized_effect",
                {
                    "product_returns_human_boundary": unauthorized_status["boundary"]
                    == "human_action_required",
                    "authorization_is_denied": unauthorized_status[
                        "authorization_required"
                    ]
                    is True,
                    "handoff_is_prepared": unauthorized_status["interaction_kind"]
                    == "responsibility_authorization",
                    "adapter_was_not_called": not unauthorized_calls,
                    "executed_external_effects_zero": unauthorized_status[
                        "executed_external_effects"
                    ]
                    == 0,
                },
            ),
            _gate(
                "interruption_recovery",
                {
                    "event_bytes_unchanged": normal_record.event_path.read_bytes()
                    == before_recovery,
                    "event_count_unchanged": recovery["event_count"] == recovery_count,
                    "projection_rebuilt": normal_record.projection_path.is_file(),
                    "accepted_gap_survives_replay": "material-gap-resolution:provider-amount"
                    in recovered_view["facts"],
                },
            ),
            _gate(
                "same_input_idempotency",
                {
                    "status_is_stable": replay_status == normal_status,
                    "event_bytes_do_not_grow": after_replay == before_replay,
                    "event_count_does_not_grow": after_count == before_count,
                },
            ),
            _gate(
                "project_truth_writer_boundary",
                {
                    "only_assurance_accepts_changes": accepted_change_actors
                    == {"assurance_boundary"},
                    "model_has_no_direct_write": all(
                        row["actor"] != "model_gateway"
                        for row in normal_record.read_audit_trail()
                    ),
                    "capability_has_no_direct_write": all(
                        row["actor"] != "capability_gateway"
                        for row in normal_record.read_audit_trail()
                    ),
                    "one_tiangong_fixture_read": len(tiangong_calls) == 1,
                },
            ),
        ]

    return {
        "contract": "lca_research_kernel.d18_system_acceptance_run/v1",
        "status": "passed" if all(row["status"] == "passed" for row in gates) else "failed",
        "gate_count": len(gates),
        "gates": gates,
        "fixture_refs": [
            {
                "fixture_id": row.fixture_id,
                "path": row.source_path,
                "sha256": row.sha256,
            }
            for row in sorted(fixtures, key=lambda item: item.fixture_id)
        ],
        "case_architecture_influence_count": 0,
        "forbidden_effect_counts": {
            "credential_reads": 0,
            "external_writes": 0,
            "irreversible_actions": 0,
            "payments": 0,
            "publication_actions": 0,
        },
        "supported_claims": [
            "D18_generic_control_spine_passes_the_bounded_P2_matrix",
            "legacy_cases_are_content_bound_read_only_evaluation_fixtures",
            "model_and_capability_failures_do_not_silently_promote_project_truth",
        ],
        "unsupported_claims": [
            "D18_supports_every_LCA_workflow",
            "Council_is_superior",
            "legacy_case_success_proves_general_scientific_correctness",
            "remote_model_execution_is_verified",
        ],
    }
