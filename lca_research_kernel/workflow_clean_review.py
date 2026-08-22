"""Clean, read-only fact-ledger review for the first migrated workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Mapping, Optional, Sequence

from .project import ResearchProject
from .workflow_acceptance import ACCEPTANCE_CONTRACT
from .workflow_registry import (
    CALLER_ROUTING_KEYS,
    load_workflow_registry,
    validate_workflow_registry,
    workflow_registry_identity,
)


REVIEW_CONTRACT = "lca_research_kernel.workflow_clean_review/v1"
GOAL_ACCEPTANCE_CONTRACT = (
    "lca_research_kernel.research_director_workflow_goal_acceptance/v1"
)
EXPECTED_NEXT_ROUTE = (
    "select-unadapted-closed-tiangong-case-for-director-generalization"
)
SUPPORTED_CLAIMS = {
    "director_advance_is_the_default_product_entry_for_migrated_workflows",
    "lifepo4_workflow_completed_from_source_bound_work_item",
    "lifepo4_workflow_replay_and_interruption_recovery_are_idempotent",
}
UNSUPPORTED_CLAIMS = {
    "workflow_generalizes_to_every_tiangong_case",
    "council_is_superior",
    "migration_created_new_lifepo4_science",
}
WORKFLOW_GOAL_ACCEPTANCE_COMMIT = (
    "8d87d5c803b2d6b3003f886f1df8e24afb3e98cb"
)


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _all_keys(value: object):
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield key
            yield from _all_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _all_keys(child)


def _registered_receipts(project: ResearchProject) -> list[dict]:
    receipts = []
    for event in project.events():
        if event.get("event_type") != "artifact.registered":
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping) or payload.get("artifact_role") != "director_workflow_receipt":
            continue
        relative = payload.get("workspace_path")
        if not isinstance(relative, str):
            continue
        path = project.workspace / relative
        if (
            path.is_file()
            and _sha256(path) == payload.get("sha256")
            and path.stat().st_size == payload.get("bytes")
        ):
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                receipts.append(value)
    return receipts


def review_lifepo4_workflow_acceptance(
    repository_root: Path,
    output_root: Path,
    source_workspace: Path,
) -> dict:
    """Independently reopen and verify a persisted LiFePO4 workflow acceptance."""

    repository_root = repository_root.resolve()
    output_root = output_root.resolve()
    source_workspace = source_workspace.resolve()
    acceptance_path = output_root / "acceptance.json"
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    if not isinstance(acceptance, dict):
        raise ValueError("workflow acceptance must be an object")

    registry_errors = validate_workflow_registry(repository_root)
    if registry_errors:
        raise ValueError("workflow registry is invalid: " + "; ".join(registry_errors))
    registry = load_workflow_registry(repository_root)
    workflows = registry["workflows"]
    workflow = next(
        item
        for item in workflows
        if item.get("workflow_id") == "lifepo4-screening-comparison-v1"
    )
    expected_capabilities = list(workflow["capability_sequence"])
    project = ResearchProject.open(output_root / "project")
    events = project.events()
    receipts = _registered_receipts(project)
    final_receipts = [
        receipt
        for receipt in receipts
        if receipt.get("completed_capabilities")
        and [
            row.get("capability_id")
            for row in receipt["completed_capabilities"]
            if isinstance(row, Mapping)
        ]
        == expected_capabilities
    ]
    work_item_path = project.workspace / "inputs/director-workflow/work-item.json"
    work_item = json.loads(work_item_path.read_text(encoding="utf-8"))
    acceptance_body = {
        key: value for key, value in acceptance.items() if key != "acceptance_id"
    }
    expected_acceptance_id = hashlib.sha256(_json_bytes(acceptance_body)).hexdigest()[:16]
    source_hashes = acceptance.get("source_authority_hashes")
    source_hashes_match = isinstance(source_hashes, Mapping) and all(
        isinstance(relative, str)
        and isinstance(digest, str)
        and (source_workspace / relative).is_file()
        and _sha256(source_workspace / relative) == digest
        for relative, digest in (source_hashes or {}).items()
    )
    final_refs = acceptance.get("final_artifact_refs")
    final_refs_match = isinstance(final_refs, list) and len(final_refs) == 5 and all(
        isinstance(reference, Mapping)
        and isinstance(reference.get("path"), str)
        and isinstance(reference.get("sha256"), str)
        and (project.workspace / reference["path"]).is_file()
        and _sha256(project.workspace / reference["path"]) == reference["sha256"]
        for reference in (final_refs or [])
    )
    forbidden_effects = acceptance.get("forbidden_effect_counts")
    scientific_count = sum(
        isinstance(event.get("event_type"), str)
        and event["event_type"].startswith("research.minimum_lifepo4")
        for event in events
    )
    checks = {
        "acceptance_identity_is_content_bound": (
            acceptance.get("contract") == ACCEPTANCE_CONTRACT
            and acceptance.get("status") == "accepted"
            and acceptance.get("acceptance_id") == expected_acceptance_id
        ),
        "registry_identity_and_sequence_match": (
            acceptance.get("registry_sha256")
            == workflow_registry_identity(repository_root)
            and acceptance.get("completed_capabilities") == expected_capabilities
        ),
        "single_public_entry_has_no_caller_routing": (
            acceptance.get("public_operations") == ["director-advance"]
            and acceptance.get("caller_low_level_selection_count") == 0
            and not (set(_all_keys(work_item)) & CALLER_ROUTING_KEYS)
        ),
        "registered_final_receipt_replays": len(final_receipts) == 1,
        "accepted_result_artifacts_match_registered_bytes": final_refs_match,
        "accepted_source_authority_bytes_match": source_hashes_match,
        "replay_and_interruption_checks_hold": (
            isinstance(acceptance.get("checks"), Mapping)
            and all(acceptance["checks"].values())
            and acceptance.get("scientific_event_count_before")
            == acceptance.get("scientific_event_count_after")
            == scientific_count
        ),
        "forbidden_effects_are_zero": (
            isinstance(forbidden_effects, Mapping)
            and set(forbidden_effects)
            == {
                "credential_access",
                "payment",
                "publication",
                "external_write",
                "irreversible",
            }
            and not any(forbidden_effects.values())
        ),
        "claim_ceiling_and_next_route_are_bounded": (
            acceptance.get("next_route") == EXPECTED_NEXT_ROUTE
            and set(acceptance.get("unsupported_claims") or [])
            == {
                "workflow_generalizes_to_every_tiangong_case",
                "council_is_superior",
                "migration_created_new_lifepo4_science",
            }
        ),
    }
    p1_findings = [
        {"check": check, "reason": "required acceptance fact did not reproduce"}
        for check, passed in checks.items()
        if not passed
    ]
    try:
        acceptance_pointer = acceptance_path.relative_to(repository_root).as_posix()
    except ValueError:
        acceptance_pointer = "acceptance.json"
    review_body = {
        "contract": REVIEW_CONTRACT,
        "status": "accepted" if not p1_findings else "rejected",
        "review_mode": "clean_context_read_only_fact_ledger",
        "acceptance_ref": {
            "path": acceptance_pointer,
            "sha256": _sha256(acceptance_path),
        },
        "registry_sha256": workflow_registry_identity(repository_root),
        "checks": checks,
        "p1_findings": p1_findings,
        "p2_findings": [],
        "p1_count": len(p1_findings),
        "p2_count": 0,
        "network_access_count": 0,
        "external_write_count": 0,
        "next_route": EXPECTED_NEXT_ROUTE,
    }
    review_id = hashlib.sha256(_json_bytes(review_body)).hexdigest()[:16]
    review = {**review_body, "review_id": review_id}
    destination = output_root / "clean-review.json"
    content = _json_bytes(review)
    if destination.exists() and destination.read_bytes() != content:
        raise RuntimeError("clean review identity drifted")
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, destination)
    return review


def validate_goal_acceptance(
    repository_root: Path, *, verify_references: bool = True
) -> list[str]:
    """Validate the immutable implementation-acceptance manifest."""

    path = repository_root / "docs/research-director-workflow-acceptance.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"workflow Goal acceptance is unreadable: {error}"]
    if not isinstance(value, Mapping):
        return ["workflow Goal acceptance must be an object"]
    errors = []
    if verify_references:
        try:
            accepted_bytes = subprocess.run(
                [
                    "git",
                    "show",
                    f"{WORKFLOW_GOAL_ACCEPTANCE_COMMIT}:docs/research-director-workflow-acceptance.json",
                ],
                cwd=repository_root,
                check=True,
                capture_output=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError):
            errors.append("workflow Goal acceptance Git object is unavailable")
        else:
            if path.read_bytes() != accepted_bytes:
                errors.append("workflow Goal acceptance historical bytes drifted")
    if value.get("contract") != GOAL_ACCEPTANCE_CONTRACT:
        errors.append("workflow Goal acceptance contract drifted")
    if value.get("status") != "implementation_accepted_for_lifecycle_finalization":
        errors.append("workflow Goal acceptance status drifted")
    if value.get("next_route") != EXPECTED_NEXT_ROUTE:
        errors.append("workflow Goal acceptance next route drifted")
    if set(value.get("supported_claims") or []) != SUPPORTED_CLAIMS:
        errors.append("workflow Goal acceptance supported claim ceiling drifted")
    if set(value.get("unsupported_claims") or []) != UNSUPPORTED_CLAIMS:
        errors.append("workflow Goal acceptance unsupported claim ceiling drifted")
    if value.get("public_operations") != ["director-advance"]:
        errors.append("workflow Goal acceptance public operation drifted")
    if value.get("caller_low_level_selection_count") != 0:
        errors.append("workflow Goal acceptance permits caller routing")
    if len(value.get("completed_capabilities") or []) != 7:
        errors.append("workflow Goal acceptance capability closure drifted")
    if value.get("remaining_implementation_work") != []:
        errors.append("workflow Goal acceptance has remaining implementation work")
    if value.get("no_remaining_implementation_work") is not True:
        errors.append("workflow Goal acceptance no-work audit drifted")
    if value.get("p1_count") != 0 or value.get("p2_count") != 0:
        errors.append("workflow Goal acceptance clean review has P1/P2")
    checks = value.get("checks")
    if not isinstance(checks, Mapping) or not checks or not all(checks.values()):
        errors.append("workflow Goal acceptance checks are incomplete")
    forbidden = value.get("forbidden_effect_counts")
    if not isinstance(forbidden, Mapping) or any(forbidden.values()):
        errors.append("workflow Goal acceptance has forbidden effects")
    references = value.get("references")
    if not isinstance(references, list) or not references:
        errors.append("workflow Goal acceptance references are missing")
    elif verify_references:
        for reference in references:
            if not isinstance(reference, Mapping):
                errors.append("workflow Goal acceptance reference is invalid")
                continue
            relative = reference.get("path")
            digest = reference.get("sha256")
            if not isinstance(relative, str) or not isinstance(digest, str):
                errors.append("workflow Goal acceptance reference fields are invalid")
                continue
            referenced = repository_root / relative
            if referenced.is_file() and _sha256(referenced) == digest:
                continue
            try:
                historical = subprocess.run(
                    [
                        "git",
                        "show",
                        f"{WORKFLOW_GOAL_ACCEPTANCE_COMMIT}:{relative}",
                    ],
                    cwd=repository_root,
                    check=True,
                    capture_output=True,
                ).stdout
            except (OSError, subprocess.CalledProcessError):
                historical = b""
            if hashlib.sha256(historical).hexdigest() != digest:
                errors.append(f"workflow Goal acceptance reference drifted: {relative}")
    return errors


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args(argv)
    review = review_lifepo4_workflow_acceptance(
        args.root, args.output, args.source
    )
    print(json.dumps(review, ensure_ascii=False, sort_keys=True))
    return 0 if review["status"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
