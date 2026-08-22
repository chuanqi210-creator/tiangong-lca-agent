#!/usr/bin/env python3
"""Validate the compact D-18 and preserved workspace recovery contracts."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


FIELDS = (
    "thread_goal",
    "current_basis",
    "accepted_beat",
    "active_module",
    "open_risks",
    "next_route",
)
REQUIRED_FILES = (
    "AGENTS.md",
    "CONTEXT.md",
    "agent/lca-project-controller.md",
    "agent/lca-research-agent.md",
    "docs/agent-architecture.md",
    "docs/lca-agent-top-level-architecture.json",
    "lca_research_kernel/contracts/agent-startup-manifest-v2.json",
    "lca_research_kernel/contracts/tiangong-capability-catalog.json",
    "lca_research_kernel/contracts/tiangong-source-lock.json",
    "skills/evidence-to-tidas-lca/SKILL.md",
)
MAX_CONTEXT_BYTES = 20_000


def validate(root: Path) -> list[str]:
    issues: list[str] = []
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            issues.append(f"missing required file: {relative}")

    context_path = root / "CONTEXT.md"
    if not context_path.is_file():
        return issues
    raw = context_path.read_bytes()
    if len(raw) > MAX_CONTEXT_BYTES:
        issues.append(
            f"CONTEXT.md is too large for Hot State: {len(raw)} > "
            f"{MAX_CONTEXT_BYTES} bytes"
        )
    text = raw.decode("utf-8")
    for field in FIELDS:
        matches = re.findall(
            rf"^- {re.escape(field)}:\s+\S.*$",
            text,
            flags=re.MULTILINE,
        )
        if len(matches) != 1:
            issues.append(
                "CONTEXT.md must contain exactly one "
                f"'- {field}:' entry; found {len(matches)}"
            )
    if "authority: This file is the single authoritative Hot State" not in text:
        issues.append(
            "CONTEXT.md does not declare itself as the authoritative Hot State"
        )
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    arguments = parser.parse_args()
    issues = validate(arguments.root.resolve())
    if issues:
        for issue in issues:
            print(f"ERROR: {issue}")
        return 1
    print("Project recovery contract is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
