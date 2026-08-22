"""Mechanical alignment checks for the project-pinned Complex runtime."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path


ADOPTION_PATH = Path("docs/complex-runtime-adoption.json")
STAGE_FREEZE_PATH = Path("docs/complex-runtime-stage-freeze-2026-07-25.json")
STAGE_FREEZE_CONTRACT = "lca_research_kernel.complex_runtime_stage_freeze/v1"
STAGE_FREEZE_OBJECT = "9a9d28ce82a633ed2e915650633941adafa5fda4"
STAGE_FREEZE_MANIFEST_SHA256 = (
    "4f2c96a81621319a9da1092e6106e9ae33b93bde3d9f357b2e8ff8572ec998a7"
)
STAGE_FREEZE_ARCHIVE_SHA256 = (
    "f06a55dfddb5af65dfae0aabb0295c29587af55b62b67882bbffca1999fb2214"
)
PINNED_REVISION = "f055155c782ae315bd2c68e7fd332b10f4b68596"
EXPECTED_CORE_SHA256 = "9bca4d27792071eab960556ffe5f2e5d91e89785425c7b28f8b1c272eddb1e21"
EXPECTED_SKILL_SHA256 = "1a64981473ca412c5273e980a245260b2dbe320e47f2704687315d17c5a72ad9"
CONTRACT = "lca_research_kernel.complex_runtime_adoption/v1"
EXPECTED_SPINE = [
    "Restore true state and assemble the current Context Working Set.",
    "Classify project_nature as evidence_fill, model_discovery, mixed, or execution_delivery.",
    "Assign decision rights through the responsibility boundary.",
    "Establish the minimum control and Runtime Harness; add organization only when recurring lanes or multiple modules justify it.",
    "Run a bounded target-function Progress Loop that changes an outcome, exposes a decision, or falsifies a route.",
    "Deliver to the right audience and evaluate the outcome.",
    "Leave next_route, accepted artifacts, recovery pointers, and any layer-level diagnosis.",
]
EXPECTED_LAYERS = [
    "Prompt Contract",
    "Context Working Set",
    "Runtime Harness",
    "Progress Loop",
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_object_sha256(repository: Path, revision: str, path: str) -> str:
    payload = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout
    return hashlib.sha256(payload).hexdigest()


def resolve_pinned_complex_repository(adoption: dict) -> Path:
    """Resolve one relocated repository only by the immutable pinned object hashes."""

    declared = Path(adoption.get("source_repository") or "")
    override = os.environ.get("D18_COMPLEX_REPOSITORY")
    candidates = [Path(override)] if override else [declared]
    parent = declared.parent
    if not override and parent.is_dir():
        candidates.extend(
            path
            for path in sorted(parent.iterdir())
            if path.is_dir() and path != declared and (path / ".git").exists()
        )
    matches: list[Path] = []
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        try:
            core = _git_object_sha256(candidate, PINNED_REVISION, "protocol/core.md")
            skill = _git_object_sha256(
                candidate,
                PINNED_REVISION,
                ".agents/skills/complex-runtime/SKILL.md",
            )
        except (OSError, subprocess.CalledProcessError):
            continue
        if core == EXPECTED_CORE_SHA256 and skill == EXPECTED_SKILL_SHA256:
            matches.append(candidate)
    unique = list(dict.fromkeys(path.resolve() for path in matches))
    if len(unique) != 1:
        raise ValueError(
            "pinned Complex repository relocation is ambiguous or unavailable"
        )
    return unique[0]


def validate_complex_runtime_alignment(root: Path) -> list[str]:
    """Return deterministic alignment errors without mutating project state."""

    errors: list[str] = []
    adoption_path = root / ADOPTION_PATH
    agents_path = root / "AGENTS.md"
    context_path = root / "CONTEXT.md"
    for path in (adoption_path, agents_path, context_path):
        if not path.is_file():
            errors.append(f"missing required alignment file: {path.relative_to(root)}")
    if errors:
        return errors

    try:
        adoption = json.loads(adoption_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"invalid Complex adoption record: {error}"]
    agents = agents_path.read_text(encoding="utf-8")
    context = context_path.read_text(encoding="utf-8")

    if adoption.get("contract") != CONTRACT:
        errors.append("Complex adoption contract is absent or unsupported")
    if adoption.get("pinned_revision") != PINNED_REVISION:
        errors.append("pinned Complex revision is absent or drifted")
    if adoption.get("source_revision_verified") is not True:
        errors.append("pinned Complex source revision is not verified")
    if adoption.get("runtime_use_status") != "pinned_active_via_git_object":
        errors.append("pinned Complex runtime is not active")
    if adoption.get("maintainer_state_imported") is not False:
        errors.append("Complex maintainer state must not enter project facts")

    try:
        source_repository = resolve_pinned_complex_repository(adoption)
        core_object_sha256 = _git_object_sha256(
            source_repository, PINNED_REVISION, "protocol/core.md"
        )
        skill_object_sha256 = _git_object_sha256(
            source_repository,
            PINNED_REVISION,
            ".agents/skills/complex-runtime/SKILL.md",
        )
    except (OSError, ValueError, subprocess.CalledProcessError):
        errors.append("pinned Complex Git objects are unavailable")
        core_object_sha256 = None
        skill_object_sha256 = None
    if core_object_sha256 not in (None, EXPECTED_CORE_SHA256):
        errors.append("pinned Complex core Git object hash drifted")
    if skill_object_sha256 not in (None, EXPECTED_SKILL_SHA256):
        errors.append("pinned Complex Skill Git object hash drifted")
    evidence = adoption.get("evidence") or {}
    if evidence.get("complex_core_sha256") != EXPECTED_CORE_SHA256:
        errors.append("recorded Complex core Git object hash drifted")
    if evidence.get("complex_skill_sha256") != EXPECTED_SKILL_SHA256:
        errors.append("recorded Complex Skill Git object hash drifted")

    installation = adoption.get("skill_installation") or {}
    for key in (
        "user_entry",
        "source_path",
        "link_target",
        "skill_sha256",
        "pinned_object",
    ):
        if not installation.get(key):
            errors.append(f"Skill installation evidence is missing: {key}")
    if installation.get("skill_sha256") != EXPECTED_SKILL_SHA256:
        errors.append("recorded installed Skill pin hash drifted")
    if installation.get("integrity_basis") != "pinned_git_object":
        errors.append("Complex integrity basis is not the pinned Git object")
    if installation.get("live_entry_integrity_claimed") is not False:
        errors.append("live Complex entry integrity must not be claimed")
    if installation.get("live_entry_status") != "drifted_untrusted_for_this_phase":
        errors.append("live Complex entry drift is not disclosed")
    discovery = adoption.get("skill_discovery") or {}
    if discovery.get("status") != "confirmed_in_active_codex_catalog":
        errors.append("Complex Skill discovery is not confirmed")
    if discovery.get("catalog_entry") != "complex-runtime":
        errors.append("Complex Skill catalog entry is absent")
    if (
        adoption.get("latest_runtime_adoption_claim") is True
        and discovery.get("fresh_task_confirmed") is not True
    ):
        errors.append(
            "latest-runtime adoption claim requires fresh-task Skill discovery"
        )

    if adoption.get("behavior_spine") != EXPECTED_SPINE:
        errors.append("seven-behavior spine is absent or drifted")
    if adoption.get("diagnostic_layers") != EXPECTED_LAYERS:
        errors.append("four diagnostic layers are absent or drifted")
    if not adoption.get("project_contract_differences"):
        errors.append("project contract differences are absent")
    if not adoption.get("adopted_mechanisms"):
        errors.append("adopted Complex mechanisms are absent")
    if not adoption.get("inapplicable_mechanisms"):
        errors.append("inapplicable Complex mechanisms are absent")
    rollback = adoption.get("rollback") or {}
    if not rollback.get("project_revision"):
        errors.append("rollback project revision is absent")
    if not adoption.get("next_refresh_triggers"):
        errors.append("next Complex refresh triggers are absent")

    for behavior in EXPECTED_SPINE:
        if behavior not in agents:
            errors.append(f"AGENTS.md is missing runtime behavior: {behavior}")
    for layer in EXPECTED_LAYERS:
        if layer not in agents:
            errors.append(f"AGENTS.md is missing diagnostic layer: {layer}")

    stage_pointer_pattern = re.compile(
        rf"^complex_runtime_adoption: {re.escape(str(STAGE_FREEZE_PATH))}#sha256=([0-9a-f]{{64}})$",
        flags=re.MULTILINE,
    )
    stage_pointer = stage_pointer_pattern.search(context)
    if stage_pointer is not None:
        stage_path = root / STAGE_FREEZE_PATH
        if not stage_path.is_file():
            errors.append("frozen Complex stage record is absent")
        else:
            try:
                stage = json.loads(stage_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                stage = {}
                errors.append("frozen Complex stage record is invalid")
            if stage_pointer.group(1) != _sha256(stage_path):
                errors.append("CONTEXT.md Complex stage pointer hash drifted")
            if stage.get("contract") != STAGE_FREEZE_CONTRACT:
                errors.append("frozen Complex stage contract is unsupported")
            if stage.get("status") != "verified_frozen_active_for_phase":
                errors.append("frozen Complex stage is not active")
            if stage.get("frozen_working_tree_object") != STAGE_FREEZE_OBJECT:
                errors.append("frozen Complex working-tree object drifted")
            if (stage.get("manifest") or {}).get(
                "sha256"
            ) != STAGE_FREEZE_MANIFEST_SHA256:
                errors.append("frozen Complex manifest hash drifted")
            if (stage.get("archive") or {}).get(
                "sha256"
            ) != STAGE_FREEZE_ARCHIVE_SHA256:
                errors.append("frozen Complex archive hash drifted")
        if (
            f"complex_runtime_pin: working-tree-object:{STAGE_FREEZE_OBJECT}"
            not in context
        ):
            errors.append("CONTEXT.md has no exact frozen Complex runtime pin")
    else:
        pointer_pattern = re.compile(
            rf"^complex_runtime_adoption: {re.escape(str(ADOPTION_PATH))}#sha256=([0-9a-f]{{64}})$",
            flags=re.MULTILINE,
        )
        pointer = pointer_pattern.search(context)
        if pointer is None:
            errors.append("CONTEXT.md has no pinned Complex adoption pointer")
        elif pointer.group(1) != _sha256(adoption_path):
            errors.append("CONTEXT.md Complex adoption pointer hash drifted")
        if f"complex_runtime_pin: {PINNED_REVISION}" not in context:
            errors.append("CONTEXT.md has no exact Complex runtime pin")
    if "phase_goal:" not in context:
        errors.append("CONTEXT.md has no active phase Goal")
    if len(re.findall(r"^- next_route:", context, flags=re.MULTILINE)) != 1:
        errors.append("CONTEXT.md must contain exactly one next_route")

    return errors
