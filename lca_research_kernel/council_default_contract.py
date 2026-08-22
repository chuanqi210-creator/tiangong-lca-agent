"""Deterministic validation for the Council-default product and migration contract."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import List, Mapping, Optional, Sequence


CONTRACT_PATH = Path("docs/council-default-product-contract.json")
CONTRACT = "lca_research_kernel.council_default_product_contract/v1"
COMPLEX_REVISION = "f055155c782ae315bd2c68e7fd332b10f4b68596"
COMPLEX_CORE_SHA256 = "9bca4d27792071eab960556ffe5f2e5d91e89785425c7b28f8b1c272eddb1e21"
COMPLEX_SKILL_SHA256 = "1a64981473ca412c5273e980a245260b2dbe320e47f2704687315d17c5a72ad9"
ROLLBACK_COMMIT = "22d0576ad1a3f25ceef05ec5940fb02a848a27e9"
ROLLBACK_FILES = {
    "agent/lca-project-controller.md": "5e2530288e699283d9bff8d83988b975cb5d6c6b7090226c960725b279a8db43",
    "skills/run-lca-research-project/SKILL.md": "32e83edb63104079f87807fcce0ab6213c5f631c8204a1d1df387c8a784b2cca",
    "skills/run-lca-research-project/references/state-contract.md": "695e1b4b55fbcb5078989a7bb9bed4ca8403b07af8530e48942cdbd939059fc9",
}
RESPONSIBILITIES = {
    "research_director",
    "decision_cell",
    "independent_worker",
    "research_kernel",
    "tiangong",
    "human",
}
ROUTES = {"single_controller_fast_path", "bounded_decision_cell"}
MIGRATION_CLASSES = {
    "promote_reuse",
    "adapt",
    "legacy_replay_only",
    "retire_optional_evaluation",
}
MIGRATION_MAP = {
    "research_council_contracts": (
        "lca_research_kernel/research_council_contracts.py",
        "promote_reuse",
    ),
    "research_council_workflow_runtime_mechanics": (
        "lca_research_kernel/research_council_workflow.py",
        "adapt",
    ),
    "experimental_research_director_contract": (
        "agent/lca-project-controller.md#Experimental Research Director Routing Contract",
        "adapt",
    ),
    "research.research_council.*": (
        "lca_research_kernel/research_council_workflow.py",
        "legacy_replay_only",
    ),
    "research_council_benchmark_modules": (
        "lca_research_kernel/research_council_benchmark*.py",
        "legacy_replay_only",
    ),
    "superiority_ab_and_blind_review": (
        "docs/superpowers/plans/2026-07-21-anytime-research-council.md",
        "retire_optional_evaluation",
    ),
}
REQUIRED_GATES = {
    "product_contract",
    "formal_runtime",
    "formal_controller_skill_state_integration",
    "freshwater_source_bound_fixture",
    "negative_authority_suite_53",
    "single_controller_fast_path",
    "interruption_recovery_and_replay",
    "registered_artifact_drift_rejection",
    "stale_worker_rejection",
    "rollback_without_history_deletion",
    "focused_tests",
    "full_repository_verification",
    "zero_forbidden_effects",
    "clean_independent_acceptance",
}
CUTOVER_GATE_CHECKS = {
    "product_contract": ("contract_validation", ("python3", "-m", "unittest", "tests.test_council_default_product_contract", "-v")),
    "formal_runtime": ("focused_test", ("python3", "-m", "unittest", "tests.test_council_runtime", "-v")),
    "formal_controller_skill_state_integration": ("focused_test", ("python3", "-m", "unittest", "tests.test_council_cli", "tests.test_research_council_agent_contract", "-v")),
    "freshwater_source_bound_fixture": ("fixture_acceptance", ("python3", "-m", "unittest", "tests.test_council_freshwater_acceptance", "-v")),
    "negative_authority_suite_53": ("negative_suite", ("python3", "-m", "unittest", "tests.test_council_safety_acceptance", "-v")),
    "single_controller_fast_path": ("focused_test", ("python3", "-m", "unittest", "tests.test_council_safety_acceptance", "-v")),
    "interruption_recovery_and_replay": ("deterministic_replay", ("python3", "-m", "unittest", "tests.test_council_recovery_rollback", "-v")),
    "registered_artifact_drift_rejection": ("drift_rejection", ("python3", "-m", "unittest", "tests.test_council_recovery_rollback", "-v")),
    "stale_worker_rejection": ("stale_result_rejection", ("python3", "-m", "unittest", "tests.test_council_recovery_rollback", "-v")),
    "rollback_without_history_deletion": ("rollback_verification", ("python3", "-m", "unittest", "tests.test_council_recovery_rollback", "-v")),
    "focused_tests": ("focused_test", ("python3", "-m", "unittest", "tests.test_council_default_product_contract", "tests.test_council_runtime", "tests.test_council_project_integration", "tests.test_council_cli", "tests.test_council_freshwater_acceptance", "tests.test_council_safety_acceptance", "tests.test_council_recovery_rollback", "-v")),
    "full_repository_verification": ("repository_verification", ("make", "verify")),
    "zero_forbidden_effects": ("effect_scan", ("python3", "-m", "unittest", "tests.test_council_safety_acceptance", "-v")),
    "clean_independent_acceptance": ("independent_clean_review", ("python3", "-m", "unittest", "tests.test_council_default_product_contract", "-v")),
}
OFFICIAL_SURFACES = {
    "identity_and_version",
    "tidas_semantics",
    "provider_connections",
    "lcia_methods",
    "native_tools",
    "native_results",
}
AUTHORITY_FORBIDDEN = {
    "agent_correction_as_official_fact",
    "majority_vote_over_tiangong",
    "invented_provider_or_closure",
    "unbound_native_result",
}
DIRECTOR_NONDELEGABLE = {
    "project_truth_acceptance",
    "authority_override",
    "responsibility_boundary_change",
}
DIRECTOR_OWNS = {
    "goal_continuity",
    "agenda_compilation",
    "route_selection",
    "worker_contracts",
    "kernel_acceptance",
    "final_delivery",
}
KERNEL_OWNS = {
    "event_history",
    "artifact_bus",
    "content_addressing",
    "idempotency",
    "projections",
    "replay",
    "recovery",
    "effect_boundaries",
}
HUMAN_REQUIRED_FOR = {
    "credentials",
    "payment",
    "contracts",
    "publication",
    "external_or_shared_writes",
    "irreversible_actions",
    "unsupported_strong_public_claims",
}
CELL_RESPONSIBILITIES = {
    "scientific_planning",
    "evidence_investigation",
    "model_and_method_judgment",
    "adversarial_challenge",
    "independent_acceptance",
}
ROUTE_INPUTS = {
    "scientific_importance",
    "dispute_level",
    "procedure_determinism",
    "dependency_strength",
    "independent_evidence_value",
    "adversarial_review_value",
    "context_isolation_value",
    "conclusion_flip_potential",
}
FAST_PATH_PREDICATE = "procedure_determinism=high and dispute_level=low and dependency_strength=high and all three independence values are low"
CELL_PREDICATE = "any of independent_evidence_value, adversarial_review_value, or context_isolation_value is high, or a material conclusion has medium/high dispute or flip potential"
AMBIGUOUS_ROUTE = "bounded_decision_cell"
FORMAL_STATES = {
    "not_started",
    "agenda_compiled",
    "route_selected",
    "fast_path_running",
    "cell_open",
    "worker_results_pending",
    "incumbent_validated",
    "replan_required",
    "closed",
    "accepted",
    "rolled_back",
}
FORMAL_TRANSITIONS = {
    "accepted->agenda_compiled",
    "not_started->agenda_compiled",
    "agenda_compiled->route_selected",
    "route_selected->fast_path_running",
    "route_selected->cell_open",
    "fast_path_running->closed",
    "cell_open->worker_results_pending",
    "worker_results_pending->worker_results_pending",
    "worker_results_pending->incumbent_validated",
    "worker_results_pending->replan_required",
    "replan_required->cell_open",
    "replan_required->closed",
    "incumbent_validated->closed",
    "closed->accepted",
    "accepted->rolled_back",
}
FORMAL_EVENT_TYPES = {
    "research.council.agenda_compiled",
    "research.council.route_selected",
    "research.council.cell_opened",
    "research.council.worker_result_registered",
    "research.council.cell_advanced",
    "research.council.fast_path_started",
    "research.council.fast_path_completed",
    "research.council.cell_closed",
    "research.council.outcome_accepted",
    "research.council.rollback_verified",
    "research.council.cutover_accepted",
    "research.council.rollback_activated",
}
CELL_RECORDS = {
    "agenda",
    "route_decision",
    "cell",
    "candidate_or_claim",
    "independent_evidence",
    "challenge_or_dissent",
    "validated_incumbent",
    "flip_conditions",
    "reactivation_conditions",
    "progress_classification",
    "close_or_replan_decision",
}
WORKER_INPUTS = {
    "source_bindings",
    "allowed_effects",
    "output_contract",
    "stop_condition",
    "agenda_revision",
    "cell_revision",
    "responsibility",
    "independence_receipt",
}
INDEPENDENCE_RECEIPT_FIELDS = {
    "resource_kind",
    "opaque_resource_id_sha256",
    "context_manifest_sha256",
    "source_manifest_sha256",
    "output_sha256",
}
INDEPENDENCE_POLICY = "runtime_manager_observes_isolated_resource_and_writes_receipt; worker_self_declaration_is_invalid"
ACCEPTANCE_INDEPENDENCE_POLICY = "independent_acceptance_requires_declared_responsibility_and_distinct_worker_resource"
WORKER_REPLAY_POLICY = "reuse_registered_worker_artifact_and_independence_receipt_by_hash_without_redispatch"
ALLOWED_INTERNAL_EFFECTS = {"local_read", "local_write", "external_read"}
HUMAN_OWNED_EFFECTS = {"external_write", "irreversible"}
ZERO_EFFECTS = {
    "credentials_read",
    "network_write",
    "publication",
    "payment",
    "contract_acceptance",
    "remote_entity_mutation",
}
FORBIDDEN_EXECUTED_EFFECTS = HUMAN_OWNED_EFFECTS | ZERO_EFFECTS


def derive_forbidden_effects(
    events: Sequence[Mapping[str, object]],
) -> List[str]:
    """Derive executed forbidden effects from an immutable event prefix."""

    findings: List[str] = []

    def normalized(value: object) -> str:
        return re.sub(r"[^a-z0-9]+", "_", str(value).strip().casefold()).strip("_")

    def executed(value: object) -> bool:
        if value is True:
            return True
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value > 0
        return normalized(value) in {
            "executed",
            "performed",
            "completed",
            "committed",
            "published",
            "paid",
            "written",
            "mutated",
            "irreversible",
            "yes",
            "true",
        }

    for index, event in enumerate(events):
        event_type = normalized(event.get("event_type"))
        event_executed = event_type.endswith("executed") or "effect_executed" in event_type
        identity = str(event.get("event_id") or f"index-{index}")

        def add(effect: str, field: str) -> None:
            finding = f"event={identity}:effect={effect}:field={field}"
            if finding not in findings:
                findings.append(finding)

        if event_executed:
            for effect in sorted(FORBIDDEN_EXECUTED_EFFECTS):
                if re.search(rf"(?:^|_){re.escape(effect)}(?:_|$)", event_type):
                    add(effect, "event_type")

        def walk(value: object, path: str) -> None:
            if isinstance(value, Mapping):
                sibling_executed = any(
                    normalized(key) in {"executed", "performed", "completed", "status"}
                    and executed(child)
                    for key, child in value.items()
                )
                for raw_key, child in value.items():
                    key = normalized(raw_key)
                    field = f"{path}.{key}" if path else key
                    if key in FORBIDDEN_EXECUTED_EFFECTS and executed(child):
                        add(key, field)
                    elif key in {
                        "executed_effect",
                        "executed_effect_type",
                        "performed_effect",
                    }:
                        effect = normalized(child)
                        if effect in FORBIDDEN_EXECUTED_EFFECTS:
                            add(effect, field)
                    elif key in {"executed_effects", "performed_effects"} and isinstance(child, list):
                        for item in child:
                            effect = normalized(item)
                            if effect in FORBIDDEN_EXECUTED_EFFECTS:
                                add(effect, field)
                    elif key in {"effect", "effect_type"} and (event_executed or sibling_executed):
                        effect = normalized(child)
                        if effect in FORBIDDEN_EXECUTED_EFFECTS:
                            add(effect, field)
                    elif key.endswith("_executed_count") and executed(child):
                        for effect in sorted(FORBIDDEN_EXECUTED_EFFECTS):
                            if effect in key:
                                add(effect, field)
                    walk(child, field)
            elif isinstance(value, list):
                for child_index, child in enumerate(value):
                    walk(child, f"{path}[{child_index}]")

        walk(event.get("payload"), "payload")
    return findings
TRUE_UNSUPPORTED_CLAIMS = {
    "council_superior",
    "freshwater_is_two_route_comparative_lca",
    "majority_vote_can_correct_tiangong",
}
PENDING_UNSUPPORTED_CLAIMS = TRUE_UNSUPPORTED_CLAIMS | {
    "council_already_formal_operational_truth",
}
FORMAL_UNSUPPORTED_CLAIMS = TRUE_UNSUPPORTED_CLAIMS
PENDING_SUPPORTED_CLAIMS = {
    "council_default_is_approved_target",
    "single_controller_is_current_operational_truth_until_cutover",
    "superiority_evaluation_is_optional",
}
FORMAL_SUPPORTED_CLAIMS = {
    "council_default_passed_declared_cutover_gates",
    "council_default_is_formal_operational_truth",
    "council_already_formal_operational_truth",
    "single_controller_fast_path_and_22d_rollback_remain_available",
    "superiority_evaluation_is_optional",
}
ROLLBACK_ACTIONS = {
    "verify": "research.council.rollback_verified",
    "cutover": "research.council.cutover_accepted",
    "activate": "research.council.rollback_activated",
}
ROLLBACK_STRATEGY = "project_state_pointer_and_formal_route_reactivation_without_history_deletion"
ROLLBACK_ACTIVATION_POLICY = "append_rollback_event_then_restore_formal_route_pointer_without_deleting_or_rewriting_history"
PENDING_STATUS = "approved_pending_cutover"
FORMAL_STATUS = "formal_operational_truth"
PENDING_ROUTE = "run-clean-acceptance-and-guarded-council-cutover"
FORMAL_ROUTE = "run-next-research-project-through-council-default"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CUTOVER_ACCEPTANCE_PATH = "docs/council-default-cutover-acceptance.json"
CUTOVER_ACCEPTANCE_CONTRACT = "lca_research_kernel.council_default_cutover_acceptance/v1"
CUTOVER_GATES_PATH = "outputs/council-default-acceptance/final/cutover-gates.json"
CUTOVER_EVIDENCE_POLICY = {
    "artifact_bus_actor": "controller",
    "check_evidence_contract": "lca_research_kernel.council_default_check_evidence/v1",
    "check_evidence_root": "outputs/council-default-acceptance/final/transcripts",
    "clean_acceptance_contract": "lca_research_kernel.council_default_clean_acceptance/v1",
    "clean_acceptance_independence_required": True,
    "clean_independence_receipt_contract": "lca_research_kernel.council_default_clean_independence_receipt/v1",
    "clean_review_report_contract": "lca_research_kernel.council_default_clean_review_report/v1",
    "effect_ledger_contract": "lca_research_kernel.council_default_effect_ledger/v1",
    "effect_ledger_must_bind_event_prefix": True,
    "gate_commands_reexecuted_before_cutover": True,
    "gate_evidence_contract": "lca_research_kernel.council_default_gate_evidence/v1",
}


def _git_bytes(repository: Path, revision: str, path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _path_within_root(root: Path, relative_path: object) -> Optional[Path]:
    if not isinstance(relative_path, str) or not relative_path:
        return None
    path = root / relative_path
    try:
        resolved = path.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return resolved


def _canonical_sha256(value: Mapping[str, object]) -> str:
    return _sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )


def _validate_artifact_ref(
    root: Path,
    reference: object,
    events: List[Mapping[str, object]],
    event_log_path: Path,
) -> bool:
    if not isinstance(reference, Mapping):
        return False
    if set(reference) != {
        "path",
        "sha256",
        "artifact_id",
        "registration_event_id",
    }:
        return False
    path = _path_within_root(root, reference.get("path"))
    digest = reference.get("sha256")
    if not bool(
        path is not None
        and path.is_file()
        and isinstance(digest, str)
        and SHA256_PATTERN.fullmatch(digest)
        and _sha256(path.read_bytes()) == digest
    ):
        return False
    registration = next(
        (
            row
            for row in events
            if row.get("event_id") == reference.get("registration_event_id")
        ),
        None,
    )
    if not isinstance(registration, Mapping):
        return False
    if registration.get("event_type") != "artifact.registered":
        return False
    if registration.get("actor") != "controller":
        return False
    if not isinstance(registration.get("idempotency_key"), str) or not str(
        registration.get("idempotency_key")
    ).strip():
        return False
    payload = registration.get("payload")
    if not isinstance(payload, Mapping):
        return False
    if payload.get("artifact_id") != reference.get("artifact_id"):
        return False
    if payload.get("sha256") != digest:
        return False
    workspace_path = payload.get("workspace_path")
    if not isinstance(workspace_path, str):
        return False
    try:
        registered_path = (event_log_path.parent / workspace_path).resolve()
    except OSError:
        return False
    return registered_path == path


def _validate_check_evidence_ref(
    root: Path,
    gate_id: object,
    check_id: object,
    reference: object,
    events: List[Mapping[str, object]],
    event_log_path: Path,
) -> bool:
    if not _validate_artifact_ref(root, reference, events, event_log_path):
        return False
    path = _path_within_root(root, reference.get("path"))
    try:
        path.relative_to(
            (root / "outputs/council-default-acceptance/final/transcripts").resolve()
        )
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (AttributeError, OSError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(evidence, Mapping) or set(evidence) != {
        "contract",
        "gate_id",
        "check_id",
        "status",
        "verification_kind",
        "command",
        "exit_code",
        "output_sha256",
    }:
        return False
    command = evidence.get("command")
    expected_check = CUTOVER_GATE_CHECKS.get(str(gate_id))
    expected_output = _sha256(
        (
            json.dumps(
                {"command": command, "exit_code": 0},
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    ) if isinstance(command, list) else None
    return bool(
        evidence.get("contract")
        == "lca_research_kernel.council_default_check_evidence/v1"
        and evidence.get("gate_id") == gate_id
        and evidence.get("check_id") == check_id
        and evidence.get("status") == "passed"
        and expected_check is not None
        and evidence.get("verification_kind") == expected_check[0]
        and isinstance(command, list)
        and command
        and all(isinstance(part, str) and part for part in command)
        and evidence.get("exit_code") == 0
        and evidence.get("output_sha256") == expected_output
        and command == list(expected_check[1])
    )


def _validate_gate_evidence_ref(
    root: Path,
    gate_id: object,
    reference: object,
    events: List[Mapping[str, object]],
    event_log_path: Path,
) -> bool:
    if not _validate_artifact_ref(root, reference, events, event_log_path):
        return False
    path = _path_within_root(root, reference.get("path"))
    try:
        path.relative_to(
            (root / "outputs/council-default-acceptance/final/gates").resolve()
        )
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (AttributeError, OSError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(artifact, Mapping):
        return False
    if artifact.get("contract") != "lca_research_kernel.council_default_gate_evidence/v1":
        return False
    if artifact.get("gate_id") != gate_id or artifact.get("status") != "passed":
        return False
    checks = artifact.get("checks")
    if not isinstance(checks, list) or not checks:
        return False
    for check in checks:
        if not isinstance(check, Mapping):
            return False
        if not isinstance(check.get("check_id"), str) or not check.get("check_id"):
            return False
        if check.get("status") != "passed":
            return False
        leaf_refs = check.get("evidence_refs")
        if not isinstance(leaf_refs, list) or not leaf_refs:
            return False
        if any(
            not _validate_check_evidence_ref(
                root,
                gate_id,
                check.get("check_id"),
                leaf,
                events,
                event_log_path,
            )
            for leaf in leaf_refs
        ):
            return False
    return True


def _validate_cutover_acceptance(root: Path, path: Path) -> bool:
    try:
        acceptance = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(acceptance, Mapping):
        return False
    if acceptance.get("contract") != CUTOVER_ACCEPTANCE_CONTRACT:
        return False
    if acceptance.get("formal_status") != FORMAL_STATUS:
        return False
    if acceptance.get("all_required_gates_passed") is not True:
        return False
    event = acceptance.get("cutover_event_evidence")
    if not isinstance(event, Mapping):
        return False
    if event.get("event_type") != "research.council.cutover_accepted":
        return False
    if not isinstance(event.get("event_id"), str) or not event.get("event_id"):
        return False
    for field in (
        "event_sha256",
        "event_log_sha256",
        "gate_manifest_sha256",
    ):
        if not SHA256_PATTERN.fullmatch(str(event.get(field, ""))):
            return False
    event_log_path = _path_within_root(root, event.get("event_log_path"))
    if event_log_path is None or event_log_path.suffix != ".jsonl":
        return False
    try:
        event_log_path.relative_to((root / "outputs/council-default-acceptance").resolve())
    except ValueError:
        return False
    if not event_log_path.is_file() or _sha256(event_log_path.read_bytes()) != event.get("event_log_sha256"):
        return False
    try:
        events = [
            json.loads(line)
            for line in event_log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError):
        return False
    if not events or any(not isinstance(row, Mapping) for row in events):
        return False
    if [row.get("sequence") for row in events] != list(range(1, len(events) + 1)):
        return False
    event_ids = [row.get("event_id") for row in events]
    if any(not isinstance(value, str) or not value for value in event_ids):
        return False
    if len(event_ids) != len(set(event_ids)):
        return False
    try:
        from .project import ResearchProject

        project = ResearchProject.open(event_log_path.parent)
        if project.event_path.resolve() != event_log_path:
            return False
        snapshot = project._project(events)
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        return False
    council = snapshot.get("council") if isinstance(snapshot, Mapping) else None
    if not isinstance(council, Mapping):
        return False
    if council.get("formal_status") != FORMAL_STATUS:
        return False
    if council.get("cutover_event_id") != event.get("event_id"):
        return False
    rows = acceptance.get("gate_results")
    if not isinstance(rows, list) or len(rows) != len(REQUIRED_GATES):
        return False
    if {
        row.get("gate_id") for row in rows if isinstance(row, Mapping)
    } != REQUIRED_GATES:
        return False
    if any(
        not isinstance(row, Mapping)
        or row.get("status") != "passed"
        or not isinstance(row.get("evidence_refs"), list)
        or not row.get("evidence_refs")
        or any(
            not _validate_gate_evidence_ref(
                root, row.get("gate_id"), reference, events, event_log_path
            )
            for reference in row.get("evidence_refs")
        )
        for row in rows
    ):
        return False
    clean = acceptance.get("clean_acceptance")
    if not isinstance(clean, Mapping) or clean.get("status") != "accepted":
        return False
    if clean.get("p1_count") != 0 or clean.get("p2_count") != 0:
        return False
    clean_ref = {
        "path": clean.get("artifact_path"),
        "sha256": clean.get("artifact_sha256"),
        "artifact_id": clean.get("artifact_id"),
        "registration_event_id": clean.get("registration_event_id"),
    }
    if not _validate_artifact_ref(root, clean_ref, events, event_log_path):
        return False
    clean_path = _path_within_root(root, clean.get("artifact_path"))
    try:
        clean_artifact = json.loads(clean_path.read_text(encoding="utf-8"))
    except (AttributeError, OSError, json.JSONDecodeError):
        return False
    if not isinstance(clean_artifact, Mapping):
        return False
    independence_ref = clean_artifact.get("independence_receipt_ref")
    if set(clean_artifact) != {
        "contract",
        "status",
        "p1_count",
        "p2_count",
        "independence_receipt_ref",
    }:
        return False
    if clean_artifact.get("contract") != "lca_research_kernel.council_default_clean_acceptance/v1":
        return False
    if clean_artifact.get("status") != "accepted":
        return False
    if clean_artifact.get("p1_count") != 0 or clean_artifact.get("p2_count") != 0:
        return False
    if not _validate_artifact_ref(
        root, independence_ref, events, event_log_path
    ):
        return False
    independence_path = _path_within_root(root, independence_ref.get("path"))
    try:
        independence = json.loads(independence_path.read_text(encoding="utf-8"))
    except (AttributeError, OSError, json.JSONDecodeError):
        return False
    if not isinstance(independence, Mapping) or set(independence) != {
        "contract",
        "status",
        "resource_kind",
        "opaque_resource_id_sha256",
        "context_manifest_sha256",
        "reviewed_basis_sha256",
        "review_report_ref",
    }:
        return False
    if independence.get("contract") != "lca_research_kernel.council_default_clean_independence_receipt/v1":
        return False
    if independence.get("status") != "accepted":
        return False
    if independence.get("resource_kind") not in {
        "isolated_subagent_context",
        "clean_context_worker",
    }:
        return False
    if any(
        not SHA256_PATTERN.fullmatch(str(independence.get(field, "")))
        for field in (
            "opaque_resource_id_sha256",
            "context_manifest_sha256",
            "reviewed_basis_sha256",
        )
    ):
        return False
    report_ref = independence.get("review_report_ref")
    if not _validate_artifact_ref(root, report_ref, events, event_log_path):
        return False
    report_path = _path_within_root(root, report_ref.get("path"))
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (AttributeError, OSError, json.JSONDecodeError):
        return False
    if not isinstance(report, Mapping) or set(report) != {
        "contract",
        "status",
        "p1_count",
        "p2_count",
        "opaque_resource_id_sha256",
        "reviewed_basis_sha256",
    }:
        return False
    if (
        report.get("contract")
        != "lca_research_kernel.council_default_clean_review_report/v1"
        or report.get("status") != "accepted"
        or report.get("p1_count") != 0
        or report.get("p2_count") != 0
        or report.get("opaque_resource_id_sha256")
        != independence.get("opaque_resource_id_sha256")
        or report.get("reviewed_basis_sha256")
        != independence.get("reviewed_basis_sha256")
    ):
        return False
    gate_path = _path_within_root(root, event.get("gate_manifest_path"))
    if gate_path != (root / CUTOVER_GATES_PATH).resolve():
        return False
    if not gate_path.is_file() or _sha256(gate_path.read_bytes()) != event.get("gate_manifest_sha256"):
        return False
    try:
        gate_manifest = json.loads(gate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(gate_manifest, Mapping):
        return False
    if gate_manifest.get("contract") != "lca_research_kernel.council_default_cutover_gates/v1":
        return False
    if gate_manifest.get("all_required_gates_passed") is not True:
        return False
    if gate_manifest.get("gate_results") != rows:
        return False
    if gate_manifest.get("clean_acceptance") != clean:
        return False
    gate_ref = {
        "path": event.get("gate_manifest_path"),
        "sha256": event.get("gate_manifest_sha256"),
        "artifact_id": event.get("gate_manifest_artifact_id"),
        "registration_event_id": event.get("gate_manifest_registration_event_id"),
    }
    if not _validate_artifact_ref(root, gate_ref, events, event_log_path):
        return False
    effect_ledger = acceptance.get("effect_ledger")
    if not isinstance(effect_ledger, Mapping):
        return False
    effect_ref = {
        "path": effect_ledger.get("path"),
        "sha256": effect_ledger.get("sha256"),
        "artifact_id": effect_ledger.get("artifact_id"),
        "registration_event_id": effect_ledger.get("registration_event_id"),
    }
    if not _validate_artifact_ref(root, effect_ref, events, event_log_path):
        return False
    effect_path = _path_within_root(root, effect_ledger.get("path"))
    try:
        effect_artifact = json.loads(effect_path.read_text(encoding="utf-8"))
    except (AttributeError, OSError, json.JSONDecodeError):
        return False
    if not isinstance(effect_artifact, Mapping):
        return False
    if effect_artifact.get("contract") != "lca_research_kernel.council_default_effect_ledger/v1":
        return False
    if set(effect_artifact) != {
        "contract",
        "forbidden_effect_count",
        "forbidden_effects",
        "scanned_event_count",
        "scanned_event_log_sha256",
    }:
        return False
    if effect_ledger.get("forbidden_effect_count") != 0:
        return False
    if effect_artifact.get("forbidden_effect_count") != 0 or effect_artifact.get("forbidden_effects") != []:
        return False
    scanned_count = effect_artifact.get("scanned_event_count")
    effect_registration_index = next(
        (
            index
            for index, row in enumerate(events)
            if row.get("event_id") == effect_ledger.get("registration_event_id")
        ),
        None,
    )
    if (
        not isinstance(scanned_count, int)
        or isinstance(scanned_count, bool)
        or effect_registration_index is None
        or scanned_count != effect_registration_index
    ):
        return False
    prefix = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        for row in events[:scanned_count]
    ).encode("utf-8")
    if _sha256(prefix) != effect_artifact.get("scanned_event_log_sha256"):
        return False
    derived_forbidden_effects = derive_forbidden_effects(events[:scanned_count])
    if (
        effect_artifact.get("forbidden_effects") != derived_forbidden_effects
        or effect_artifact.get("forbidden_effect_count")
        != len(derived_forbidden_effects)
    ):
        return False
    if gate_manifest.get("effect_ledger") != effect_ledger:
        return False
    if gate_manifest.get("zero_forbidden_effects") is not True:
        return False
    matched_event = next(
        (row for row in events if row.get("event_id") == event.get("event_id")),
        None,
    )
    if not isinstance(matched_event, Mapping):
        return False
    if matched_event.get("event_type") != "research.council.cutover_accepted":
        return False
    cutover_index = events.index(matched_event)
    manifest_registration_id = event.get("gate_manifest_registration_event_id")
    between = events[effect_registration_index + 1 : cutover_index]
    if len(between) != 1 or between[0].get("event_id") != manifest_registration_id:
        return False
    if _canonical_sha256(matched_event) != event.get("event_sha256"):
        return False
    payload = matched_event.get("payload")
    if not isinstance(payload, Mapping):
        return False
    if payload.get("gate_manifest_sha256") != event.get("gate_manifest_sha256"):
        return False
    if payload.get("all_required_gates_passed") is not True:
        return False
    if acceptance.get("zero_forbidden_effects") is not True:
        return False
    return (
        set(acceptance.get("unsupported_claims") or [])
        == FORMAL_UNSUPPORTED_CLAIMS
    )


def validate_contract(
    contract: Mapping[str, object],
    root: Path,
    *,
    complex_repository: Optional[Path] = None,
    verify_acceptance_ref: bool = True,
) -> List[str]:
    """Return every deterministic contract error without changing project state."""

    errors: List[str] = []
    if contract.get("schema_version") != 1 or contract.get("contract") != CONTRACT:
        errors.append("Council-default product contract identity is unsupported")
    if contract.get("product_direction") != "council_default":
        errors.append("Council-default product direction is absent")
    formal_status = contract.get("formal_status")
    next_route = contract.get("next_route")
    if formal_status == PENDING_STATUS:
        if next_route != PENDING_ROUTE or contract.get("cutover_acceptance_ref") is not None:
            errors.append("Council contract next route drifted")
    elif formal_status == FORMAL_STATUS:
        if next_route != FORMAL_ROUTE:
            errors.append("Council contract next route drifted")
        acceptance_ref = contract.get("cutover_acceptance_ref")
        if not isinstance(acceptance_ref, Mapping):
            errors.append("formal Council cutover acceptance reference is missing")
        else:
            acceptance_path = acceptance_ref.get("path")
            acceptance_sha = acceptance_ref.get("sha256")
            if not isinstance(acceptance_path, str) or not isinstance(acceptance_sha, str) or not SHA256_PATTERN.fullmatch(acceptance_sha):
                errors.append("formal Council cutover acceptance reference is invalid")
            elif verify_acceptance_ref:
                if acceptance_path != CUTOVER_ACCEPTANCE_PATH:
                    errors.append("formal Council cutover acceptance artifact is invalid")
                    acceptance_path = None
            if verify_acceptance_ref and isinstance(acceptance_path, str):
                path = root / acceptance_path
                try:
                    path.resolve().relative_to(root.resolve())
                except ValueError:
                    errors.append("formal Council cutover acceptance reference escapes the repository")
                else:
                    if not path.is_file():
                        errors.append("formal Council cutover acceptance artifact is missing")
                    elif _sha256(path.read_bytes()) != acceptance_sha:
                        errors.append("formal Council cutover acceptance artifact drifted")
                    elif not _validate_cutover_acceptance(root, path):
                        errors.append("formal Council cutover acceptance artifact is invalid")
    else:
        errors.append("Council-default formal status is unsupported")

    authority = contract.get("authority")
    if not isinstance(authority, Mapping) or authority.get("decision_rule") != (
        "exact_authority_then_evidence_bounded_director_acceptance"
    ):
        errors.append("Council authority decision rule is unsupported")
    elif authority.get("first_authority") != "TianGong":
        errors.append("TianGong first authority is absent")
    if not isinstance(authority, Mapping) or set(authority.get("official_surfaces") or []) != OFFICIAL_SURFACES:
        errors.append("TianGong official surfaces are incomplete")
    if not isinstance(authority, Mapping) or set(authority.get("forbidden") or []) != AUTHORITY_FORBIDDEN:
        errors.append("TianGong authority prohibitions are incomplete")

    responsibilities = contract.get("responsibilities")
    if not isinstance(responsibilities, Mapping) or set(responsibilities) != RESPONSIBILITIES:
        errors.append("Council responsibility set is incomplete")
    else:
        director = responsibilities.get("research_director")
        if not isinstance(director, Mapping) or director.get("kind") != "sole_user_entry_and_integrator":
            errors.append("Research Director role is unsupported")
        if not isinstance(director, Mapping) or set(director.get("cannot_delegate") or []) != DIRECTOR_NONDELEGABLE:
            errors.append("Research Director nondelegable duties are incomplete")
        elif set(director.get("owns") or []) != DIRECTOR_OWNS:
            errors.append("Research Director ownership is incomplete")
        worker = responsibilities.get("independent_worker")
        cell = responsibilities.get("decision_cell")
        if not isinstance(cell, Mapping) or cell.get("kind") != "dynamic_bounded_responsibility_set":
            errors.append("Decision Cell role is unsupported")
        elif set(cell.get("responsibilities") or []) != CELL_RESPONSIBILITIES or cell.get("not_a_fixed_headcount") is not True:
            errors.append("Decision Cell responsibilities are incomplete")
        if not isinstance(worker, Mapping) or worker.get("kind") != "ephemeral_source_bound_context":
            errors.append("independent worker role is unsupported")
        if not isinstance(worker, Mapping) or set(worker.get("required_inputs") or []) != WORKER_INPUTS:
            errors.append("independent worker inputs are incomplete")
        elif set(worker.get("independence_receipt_fields") or []) != INDEPENDENCE_RECEIPT_FIELDS:
            errors.append("independent worker receipt fields are incomplete")
        if not isinstance(worker, Mapping) or worker.get("independence_policy") != INDEPENDENCE_POLICY:
            errors.append("independent worker observation policy drifted")
        if not isinstance(worker, Mapping) or worker.get(
            "acceptance_independence_policy"
        ) != ACCEPTANCE_INDEPENDENCE_POLICY:
            errors.append("independent acceptance responsibility policy drifted")
        if not isinstance(worker, Mapping) or worker.get("replay_policy") != WORKER_REPLAY_POLICY:
            errors.append("independent worker replay policy drifted")
        if not isinstance(worker, Mapping) or worker.get("may_write_project_truth") is not False:
            errors.append("independent worker may not write project truth")
        kernel = responsibilities.get("research_kernel")
        if not isinstance(kernel, Mapping) or kernel.get("kind") != "sole_durable_project_truth":
            errors.append("Research Kernel role is unsupported")
        if not isinstance(kernel, Mapping) or set(kernel.get("owns") or []) != KERNEL_OWNS:
            errors.append("Research Kernel ownership is incomplete")
        tiangong = responsibilities.get("tiangong")
        if not isinstance(tiangong, Mapping) or tiangong.get("kind") != "official_data_and_method_authority":
            errors.append("TianGong responsibility role is unsupported")
        if not isinstance(tiangong, Mapping) or tiangong.get("voteable") is not False:
            errors.append("TianGong authority cannot be voteable")
        human = responsibilities.get("human")
        if not isinstance(human, Mapping) or human.get("kind") != "external_responsibility_owner":
            errors.append("human responsibility role is unsupported")
        if not isinstance(human, Mapping) or set(human.get("required_for") or []) != HUMAN_REQUIRED_FOR:
            errors.append("human responsibility boundary is incomplete")
    route_policy = contract.get("route_policy")
    if not isinstance(route_policy, Mapping) or set(route_policy.get("routes") or []) != ROUTES:
        errors.append("Council route set is incomplete")
    elif route_policy.get("fast_path_is_council_default_route") is not True:
        errors.append("single Controller fast path is not a Council-default route")
    if not isinstance(route_policy, Mapping) or set(route_policy.get("inputs") or []) != ROUTE_INPUTS:
        errors.append("Council route inputs are incomplete")
    if not isinstance(route_policy, Mapping) or route_policy.get("fast_path_when") != FAST_PATH_PREDICATE:
        errors.append("Council fast-path predicate drifted")
    if not isinstance(route_policy, Mapping) or route_policy.get("cell_when") != CELL_PREDICATE:
        errors.append("Council cell route predicate drifted")
    if not isinstance(route_policy, Mapping) or route_policy.get("default_on_ambiguous_route") != AMBIGUOUS_ROUTE:
        errors.append("ambiguous Council route does not fail closed")
    if contract.get("formal_event_family") != "research.council.*":
        errors.append("formal Council event family is absent")
    if set(contract.get("formal_states") or []) != FORMAL_STATES:
        errors.append("formal Council states are incomplete")
    if set(contract.get("formal_transitions") or []) != FORMAL_TRANSITIONS:
        errors.append("formal Council transitions are incomplete")
    if set(contract.get("formal_event_types") or []) != FORMAL_EVENT_TYPES:
        errors.append("formal Council event types are incomplete")

    cell_contract = contract.get("cell_contract")
    if not isinstance(cell_contract, Mapping) or set(cell_contract.get("required_records") or []) != CELL_RECORDS:
        errors.append("Decision Cell records are incomplete")
    else:
        if cell_contract.get("worker_result_policy") != "reject_stale_agenda_or_cell_revision_and_unregistered_source_binding":
            errors.append("stale worker policy drifted")
        if cell_contract.get("dissent_policy") != "retain_with_flip_and_reactivation_conditions":
            errors.append("dissent preservation policy drifted")
        if set(cell_contract.get("progress_classes") or []) != {"forward_change", "no_forward_change"}:
            errors.append("Decision Cell progress classes are incomplete")
        if cell_contract.get("incumbent_policy") != "replace_only_after_declared_validation_passes":
            errors.append("incumbent replacement policy drifted")
        if cell_contract.get("consensus_policy") != "consensus_is_neither_progress_nor_acceptance":
            errors.append("consensus policy drifted")

    effects = contract.get("effects")
    if not isinstance(effects, Mapping) or set(effects.get("allowed_without_human") or []) != ALLOWED_INTERNAL_EFFECTS:
        errors.append("Council internal effects are unsupported")
    if not isinstance(effects, Mapping) or set(effects.get("human_owned") or []) != HUMAN_OWNED_EFFECTS:
        errors.append("human-owned effects are incomplete")
    if not isinstance(effects, Mapping) or set(effects.get("fixture_acceptance_requires_zero") or []) != ZERO_EFFECTS:
        errors.append("zero-effect acceptance set is incomplete")

    migration = contract.get("migration_inventory")
    if not isinstance(migration, list) or not migration:
        errors.append("migration inventory is absent")
    else:
        assets = [row.get("asset") for row in migration if isinstance(row, Mapping)]
        classes = {row.get("classification") for row in migration if isinstance(row, Mapping)}
        if len(assets) != len(set(assets)):
            errors.append("migration inventory contains duplicate assets")
        if not classes.issubset(MIGRATION_CLASSES) or classes != MIGRATION_CLASSES:
            errors.append("migration inventory classifications are incomplete")
        migration_map = {
            row.get("asset"): (row.get("pointer"), row.get("classification"))
            for row in migration
            if isinstance(row, Mapping)
        }
        if migration_map != MIGRATION_MAP:
            errors.append("migration inventory mapping drifted")
        if any(
            migration_map.get(asset, (None, None))[0] != expected[0]
            for asset, expected in MIGRATION_MAP.items()
        ):
            errors.append("migration inventory pointer drifted")
        for asset, (pointer, _) in MIGRATION_MAP.items():
            pointer_path = pointer.split("#", 1)[0]
            if "*" in pointer_path:
                matches = list(root.glob(pointer_path))
                exists = bool(matches)
            else:
                exists = (root / pointer_path).is_file()
            if not exists:
                errors.append(f"migration inventory pointer is unavailable: {asset}")
        if migration_map.get("research.research_council.*") != (
            "lca_research_kernel/research_council_workflow.py",
            "legacy_replay_only",
        ):
            errors.append("legacy Council event family is not replay-only")

    rollback = contract.get("rollback")
    rollback_rows = rollback.get("files") if isinstance(rollback, Mapping) else None
    if not isinstance(rollback_rows, list) or {
        row.get("path") for row in rollback_rows if isinstance(row, Mapping)
    } != set(ROLLBACK_FILES):
        errors.append("frozen rollback files are incomplete")
    elif rollback.get("git_commit") != ROLLBACK_COMMIT:
        errors.append("frozen rollback commit drifted")
    else:
        for row in rollback_rows:
            path = row["path"]
            expected = ROLLBACK_FILES[path]
            if row.get("sha256") != expected:
                errors.append(f"recorded rollback hash drifted: {path}")
                continue
            try:
                actual = _sha256(_git_bytes(root, ROLLBACK_COMMIT, path))
            except (OSError, subprocess.CalledProcessError):
                errors.append(f"frozen rollback object is unavailable: {path}")
                continue
            if actual != expected:
                errors.append(f"frozen rollback object drifted: {path}")
    if not isinstance(rollback, Mapping) or rollback.get("must_preserve_new_history") is not True:
        errors.append("rollback may not delete new history")
    if not isinstance(rollback, Mapping) or rollback.get("actions") != ROLLBACK_ACTIONS:
        errors.append("rollback actions are incomplete")
    if not isinstance(rollback, Mapping) or rollback.get("strategy") != ROLLBACK_STRATEGY:
        errors.append("rollback strategy is unsupported")
    if not isinstance(rollback, Mapping) or rollback.get("activation_policy") != ROLLBACK_ACTIVATION_POLICY:
        errors.append("rollback activation policy is unsupported")

    complex_runtime = contract.get("complex_runtime")
    if not isinstance(complex_runtime, Mapping) or complex_runtime.get("revision") != COMPLEX_REVISION:
        errors.append("Complex revision drifted")
    else:
        if complex_runtime.get("integrity_basis") != "immutable_git_objects":
            errors.append("Complex integrity basis is not immutable Git objects")
        if complex_runtime.get("live_symlink_status") != "drifted_untrusted_for_this_phase":
            errors.append("Complex live drift disclosure is absent")
        if complex_runtime.get("core_sha256") != COMPLEX_CORE_SHA256:
            errors.append("recorded Complex core hash drifted")
        if complex_runtime.get("skill_sha256") != COMPLEX_SKILL_SHA256:
            errors.append("recorded Complex Skill hash drifted")
        repository = complex_repository
        if repository is None:
            try:
                from .complex_runtime_alignment import (
                    resolve_pinned_complex_repository,
                )

                adoption = json.loads(
                    (root / "docs/complex-runtime-adoption.json").read_text(
                        encoding="utf-8"
                    )
                )
                repository = resolve_pinned_complex_repository(adoption)
            except (OSError, ValueError, json.JSONDecodeError):
                repository = Path(
                    "external/complex-project-front-governance"
                )
        for path, expected, label in (
            ("protocol/core.md", COMPLEX_CORE_SHA256, "core"),
            (".agents/skills/complex-runtime/SKILL.md", COMPLEX_SKILL_SHA256, "Skill"),
        ):
            try:
                actual = _sha256(_git_bytes(repository, COMPLEX_REVISION, path))
            except (OSError, subprocess.CalledProcessError):
                errors.append(f"pinned Complex {label} object is unavailable")
                continue
            if actual != expected:
                errors.append(f"pinned Complex {label} object drifted")

    cutover = contract.get("cutover")
    gates = cutover.get("required_gate_ids") if isinstance(cutover, Mapping) else None
    if not isinstance(gates, list) or set(gates) != REQUIRED_GATES:
        errors.append("Council cutover gates are incomplete")
    if "superiority_ab" in (gates or []):
        errors.append("superiority A/B cannot be a Council cutover gate")
    if not isinstance(cutover, Mapping) or cutover.get("formal_truth_write_allowed_only_after_all_gates") is not True:
        errors.append("formal truth cutover guard is disabled")
    if not isinstance(cutover, Mapping) or cutover.get(
        "evidence_policy"
    ) != CUTOVER_EVIDENCE_POLICY:
        errors.append("Council cutover evidence policy drifted")
    claim_ceiling = contract.get("claim_ceiling")
    expected_unsupported = (
        PENDING_UNSUPPORTED_CLAIMS
        if formal_status == PENDING_STATUS
        else FORMAL_UNSUPPORTED_CLAIMS
    )
    if not isinstance(claim_ceiling, Mapping) or set(
        claim_ceiling.get("unsupported") or []
    ) != expected_unsupported:
        errors.append("Council claim ceiling is incomplete")
    expected_supported = (
        PENDING_SUPPORTED_CLAIMS if formal_status == PENDING_STATUS else FORMAL_SUPPORTED_CLAIMS
    )
    if not isinstance(claim_ceiling, Mapping) or set(claim_ceiling.get("supported_now") or []) != expected_supported:
        errors.append("Council supported claim set drifted")
    return errors


def validate_council_default_product_contract(root: Path) -> List[str]:
    """Load and validate the repository's active Council-default contract."""

    path = root / CONTRACT_PATH
    if not path.is_file():
        return ["Council-default product contract is missing"]
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"Council-default product contract is invalid: {error}"]
    if not isinstance(contract, Mapping):
        return ["Council-default product contract must be a JSON object"]
    return validate_contract(contract, root)
