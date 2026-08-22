"""Deterministic behavioral gates for D-18 runtime architecture decisions."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Mapping, Sequence

EXPECTED_MODULES = {
    "product_interface",
    "research_orchestrator",
    "lca_domain_kernel",
    "model_gateway",
    "capability_gateway",
    "project_record",
    "assurance_boundary",
}
CANDIDATE_ACTORS = {"model", "mcp", "skill", "tool", "council"}


def _sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def evaluate_architecture_behavior(
    probes: Sequence[Mapping[str, object]],
) -> dict:
    errors = []
    routes = {}
    counterfactual_route_changed = False
    for probe in probes:
        probe_id = probe.get("probe_id")
        frame = probe.get("frame")
        expected = probe.get("expected_module")
        description = probe.get("input_description")
        if (
            not isinstance(probe_id, str)
            or not isinstance(frame, Mapping)
            or expected not in EXPECTED_MODULES
            or not isinstance(description, str)
        ):
            errors.append("architecture probe contract is invalid")
            continue
        selected = frame.get("selected_modules")
        if selected != [expected]:
            errors.append(f"{probe_id}: wrong responsibility")
        if any(module in description for module in EXPECTED_MODULES):
            errors.append(f"{probe_id}: input leaks module identity")
        change = frame.get("expected_state_change")
        if (
            not isinstance(change, Mapping)
            or change.get("from_state") == change.get("to_state")
            or not change.get("observable_delta")
        ):
            errors.append(f"{probe_id}: no observable expected state change")
        problem = frame.get("current_problem")
        if not isinstance(problem, Mapping) or not problem.get("evidence_refs"):
            errors.append(f"{probe_id}: no evidence reference")
        reason = frame.get("auditable_reason")
        if (
            not isinstance(reason, str)
            or not reason
            or "event-" not in reason
        ):
            errors.append(f"{probe_id}: reason is not evidence linked")
        irrelevant = probe.get("available_irrelevant_capability")
        if irrelevant in (frame.get("selected_capabilities") or []):
            errors.append(f"{probe_id}: irrelevant capability selected")
        routes[probe_id] = (
            tuple(selected or []),
            change.get("to_state") if isinstance(change, Mapping) else None,
        )
        parent = probe.get("counterfactual_of")
        if isinstance(parent, str) and parent in routes:
            counterfactual_route_changed = routes[parent] != routes[probe_id]
            if not counterfactual_route_changed:
                errors.append(f"{probe_id}: counterfactual did not change route")
    report = {
        "contract": "lca_research_kernel.runtime_architecture_evaluation/v1",
        "status": "passed" if not errors else "failed",
        "probe_count": len(probes),
        "failed_gate_count": len(errors),
        "errors": errors,
        "counterfactual_route_changed": counterfactual_route_changed,
    }
    report["report_sha256"] = _sha256(report)
    return report


def evaluate_candidate_write_boundaries(
    attempts: Sequence[Mapping[str, object]],
) -> dict:
    errors = []
    seen = set()
    for attempt in attempts:
        actor = attempt.get("actor")
        if actor not in CANDIDATE_ACTORS:
            errors.append("candidate actor is unsupported")
            continue
        seen.add(actor)
        if (
            attempt.get("requested_write") != "accepted_fact"
            or attempt.get("authorized") is not False
        ):
            errors.append(f"{actor}: direct accepted write was not rejected")
    if seen != CANDIDATE_ACTORS:
        errors.append("candidate write-boundary actor coverage is incomplete")
    report = {
        "contract": "lca_research_kernel.candidate_write_boundary_evaluation/v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "actor_count": len(seen),
    }
    report["report_sha256"] = _sha256(report)
    return report
