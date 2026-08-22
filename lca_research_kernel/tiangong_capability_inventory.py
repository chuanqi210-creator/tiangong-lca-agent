"""Deterministic, metadata-only inventory of official TianGong capability surfaces."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Mapping


OBSERVATION_CONTRACT = "lca_research_kernel.tiangong_capability_observation/v1"
DIFF_CONTRACT = "lca_research_kernel.capability_catalog_diff/v1"
REQUIRED_KINDS = {
    "specification",
    "data",
    "native_tool",
    "skill",
    "mcp_tool",
    "build",
    "validation",
    "calculation",
    "evidence",
}


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _slug(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.casefold())).strip("-")


def _base(
    surface_id: str,
    kind: str,
    source_lock_id: str,
    *,
    adapter_id: str,
    entrypoint: object,
    authority_tier: str,
    effect: str = "local_read",
    disposition: str = "integrated",
    availability: str = "available_local",
    credential_policy: str = "none",
    execution_policy: str = "contract_and_metadata_only_in_this_phase",
    responsibility_tags: tuple[str, ...] = (),
) -> dict:
    return {
        "surface_id": surface_id,
        "kind": kind,
        "provider": "TianGong",
        "authority_tier": authority_tier,
        "source_lock_id": source_lock_id,
        "adapter_id": adapter_id,
        "transport": (
            "mcp"
            if adapter_id.startswith("mcp.")
            else "local_process"
            if adapter_id == "subprocess.argv"
            else "local_file"
        ),
        "entrypoint": deepcopy(entrypoint),
        "input_contract": f"{surface_id}.input/v1",
        "output_contract": f"{surface_id}.output/v1",
        "effect": effect,
        "responsibility_tags": list(responsibility_tags),
        "credential_policy": credential_policy,
        "availability": availability,
        "disposition": disposition,
        "timeout_seconds": 30,
        "retry_policy": {
            "max_attempts": 1,
            "retryable_codes": [],
        },
        "idempotency": "same_input_and_source_lock",
        "recovery_route": f"repair-{_slug(surface_id)}-adapter",
        "next_route": f"return-{_slug(surface_id)}-source-bound-result",
        "allowed_use": ["source_bound_capability_execution"],
        "forbidden_claims": [
            "tool_or_protocol_identity_confers_scientific_acceptance"
        ],
        "verification": {
            "kind": "metadata_contract",
            "evidence": "tests/test_tiangong_capability_inventory.py",
        },
        "supersedes": [],
        "execution_policy": execution_policy,
    }


BASE_SURFACES = [
    _base(
        "tiangong.specification.tidas-schema",
        "specification",
        "tiangong.sdk.npm",
        adapter_id="static.tidas-sdk",
        entrypoint="schema",
        authority_tier="official_tidas_specification",
    ),
    _base(
        "tiangong.specification.methodology",
        "specification",
        "tiangong.sdk.npm",
        adapter_id="static.tidas-sdk",
        entrypoint="methodology",
        authority_tier="official_tidas_methodology",
    ),
    _base(
        "tiangong.specification.runtime-ruleset",
        "specification",
        "tiangong.sdk.npm",
        adapter_id="static.tidas-sdk",
        entrypoint="runtime-ruleset",
        authority_tier="official_tidas_ruleset",
    ),
    _base(
        "tiangong.specification.context-pack",
        "specification",
        "tiangong.cli.npm",
        adapter_id="subprocess.argv",
        entrypoint=["tiangong-lca", "dataset", "context-pack"],
        authority_tier="official_native_tool",
        disposition="conditional_runtime",
        availability="requires_runtime",
    ),
    _base(
        "tiangong.data.flow-read",
        "data",
        "tiangong.cli.npm",
        adapter_id="tiangong.read-only",
        entrypoint=["tiangong-lca", "get", "flow"],
        authority_tier="official_tiangong_data",
        effect="external_read",
        disposition="human_action_required",
        availability="requires_credential",
        credential_policy="user_provisioned_identity_required",
    ),
    _base(
        "tiangong.data.process-read",
        "data",
        "tiangong.cli.npm",
        adapter_id="tiangong.read-only",
        entrypoint=["tiangong-lca", "get", "process"],
        authority_tier="official_tiangong_data",
        effect="external_read",
        disposition="human_action_required",
        availability="requires_credential",
        credential_policy="user_provisioned_identity_required",
    ),
    _base(
        "tiangong.data.lifecyclemodel-read",
        "data",
        "tiangong.cli.npm",
        adapter_id="tiangong.read-only",
        entrypoint=["tiangong-lca", "get", "lifecyclemodel"],
        authority_tier="official_tiangong_data",
        effect="external_read",
        disposition="human_action_required",
        availability="requires_credential",
        credential_policy="user_provisioned_identity_required",
    ),
    _base(
        "tiangong.data.native-lcia-result-read",
        "data",
        "tiangong.cli.npm",
        adapter_id="tiangong.read-only",
        entrypoint=["tiangong-lca", "result", "read"],
        authority_tier="official_tiangong_native_result",
        effect="external_read",
        disposition="human_action_required",
        availability="requires_credential",
        credential_policy="user_provisioned_identity_required",
    ),
    _base(
        "tiangong.native.cli-contract",
        "native_tool",
        "tiangong.cli.npm",
        adapter_id="subprocess.argv",
        entrypoint=["tiangong-lca", "--help"],
        authority_tier="official_native_tool",
    ),
    _base(
        "tiangong.native.tidas-sdk",
        "native_tool",
        "tiangong.sdk.npm",
        adapter_id="static.tidas-sdk",
        entrypoint="@tiangong-lca/tidas-sdk",
        authority_tier="official_native_tool",
    ),
    _base(
        "tiangong.build.process",
        "build",
        "tiangong.cli.npm",
        adapter_id="subprocess.argv",
        entrypoint=["tiangong-lca", "process", "auto-build"],
        authority_tier="official_native_tool",
        effect="local_write",
        disposition="conditional_runtime",
        availability="requires_runtime",
        execution_policy="registered_not_executed_in_this_phase",
    ),
    _base(
        "tiangong.build.lifecyclemodel",
        "build",
        "tiangong.cli.npm",
        adapter_id="subprocess.argv",
        entrypoint=["tiangong-lca", "lifecyclemodel", "auto-build"],
        authority_tier="official_native_tool",
        effect="local_write",
        disposition="conditional_runtime",
        availability="requires_runtime",
        execution_policy="registered_not_executed_in_this_phase",
    ),
    _base(
        "tiangong.validation.tidas",
        "validation",
        "tiangong.sdk.npm",
        adapter_id="static.tidas-sdk",
        entrypoint="validate",
        authority_tier="official_native_tool",
    ),
    _base(
        "tiangong.calculation.openlca-ipc",
        "calculation",
        "tiangong.mcp.npm",
        adapter_id="mcp.tool",
        entrypoint="OpenLCA_Impact_Assessment_Tool",
        authority_tier="official_transport_tool",
        disposition="conditional_runtime",
        availability="requires_runtime",
        execution_policy="contract_only_no_execution_in_this_phase",
    ),
    _base(
        "tiangong.evidence.source-evidence-contract",
        "evidence",
        "tiangong.skills.git",
        adapter_id="skill.metadata",
        entrypoint="source-evidence-dataset-development/SKILL.md",
        authority_tier="official_workflow_policy",
        disposition="upstream_owned",
        availability="available_public_remote",
    ),
]


def _frontmatter(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    name = re.search(r"^name:\s*[\"']?(.+?)[\"']?\s*$", text, re.MULTILINE)
    description = re.search(
        r"^description:\s*[\"']?(.+?)[\"']?\s*$", text, re.MULTILINE
    )
    if name is None or description is None:
        raise ValueError(f"Skill frontmatter is incomplete: {path}")
    return name.group(1).strip(), description.group(1).strip()


def _skill_surface(path: Path) -> dict:
    name, description = _frontmatter(path)
    sensitive = any(
        token in name
        for token in ("publish", "rls", "remote-ops", "embedding-ft")
    )
    row = _base(
        f"tiangong.skill.{_slug(name)}",
        "skill",
        "tiangong.skills.git",
        adapter_id="skill.metadata",
        entrypoint=f"{path.parent.name}/SKILL.md",
        authority_tier="official_workflow_policy",
        effect="external_write" if sensitive else "local_read",
        disposition="human_action_required" if sensitive else "upstream_owned",
        availability="requires_credential" if sensitive else "available_public_remote",
        credential_policy="user_provisioned_identity_required" if sensitive else "none",
        execution_policy="metadata_only_thin_adapter",
    )
    row["verification"] = {
        "kind": "skill_frontmatter",
        "evidence": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    row["allowed_use"] = ["stateless_professional_method"]
    row["description"] = description
    return row


def _mcp_surface(kind: str, name: str, path: Path) -> dict:
    slug = _slug(name)
    sensitive = any(
        token in slug
        for token in ("database-crud", "glad-dataset", "search-flows", "search-processes", "search-life-cycle")
    )
    calculation = "impact-assessment" in slug
    disposition = (
        "human_action_required"
        if sensitive
        else "conditional_runtime"
        if kind == "tool"
        else "upstream_owned"
    )
    row = _base(
        f"tiangong.mcp.{kind}.{slug}",
        "mcp_tool",
        "tiangong.mcp.npm",
        adapter_id=f"mcp.{kind}",
        entrypoint=name,
        authority_tier="official_transport_tool",
        effect="external_write" if "database-crud" in slug else "local_read",
        disposition=disposition,
        availability="requires_credential" if sensitive else "requires_runtime",
        credential_policy="user_provisioned_identity_required" if sensitive else "none",
        execution_policy=(
            "contract_only_no_execution_in_this_phase"
            if calculation
            else "metadata_only_in_this_phase"
        ),
    )
    row["verification"] = {
        "kind": "static_mcp_registration",
        "evidence": path.as_posix(),
    }
    return row


def _scan_mcp(root: Path) -> list[dict]:
    rows: list[dict] = []
    patterns = {
        "tool": re.compile(
            r"server\.(?:tool|registerTool)\(\s*['\"]([^'\"]+)['\"]",
            re.MULTILINE,
        ),
        "prompt": re.compile(r"server\.prompt\(\s*['\"]([^'\"]+)['\"]"),
        "resource": re.compile(r"server\.resource\(\s*['\"]([^'\"]+)['\"]"),
    }
    for path in sorted((root / "src").rglob("*.ts")):
        text = path.read_text(encoding="utf-8")
        for kind, pattern in patterns.items():
            rows.extend(_mcp_surface(kind, name, path) for name in pattern.findall(text))
    return rows


def _scan_tidas_tools(root: Path) -> list[dict]:
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return []
    text = pyproject.read_text(encoding="utf-8")
    section = re.search(
        r"^\[project\.scripts\]\s*$([\s\S]*?)(?=^\[|\Z)", text, re.MULTILINE
    )
    if section is None:
        return []
    rows = []
    for name, target in re.findall(
        r"^([A-Za-z0-9_-]+)\s*=\s*[\"']([^\"']+)[\"']\s*$",
        section.group(1),
        re.MULTILINE,
    ):
        sensitive = name in {"tidas-export", "tidas-release-tool"}
        kind = "validation" if "validate" in name else "native_tool"
        rows.append(
            _base(
                f"tiangong.tidas-tools.{_slug(name)}",
                kind,
                "tiangong.tidas-tools.pypi",
                adapter_id="subprocess.argv",
                entrypoint=[name],
                authority_tier="official_native_tool",
                effect="external_write" if sensitive else "local_write",
                disposition="human_action_required" if sensitive else "integrated",
                availability="requires_credential" if sensitive else "available_local",
                credential_policy=(
                    "user_provisioned_identity_required" if sensitive else "none"
                ),
                execution_policy="registered_not_executed_in_this_phase",
            )
        )
        rows[-1]["output_contract"] = target
    return rows


def scan_tiangong_surfaces(root: Path) -> dict:
    source_root = Path(root)
    surfaces = deepcopy(BASE_SURFACES)
    skill_root = source_root / "skills"
    if skill_root.is_dir():
        surfaces.extend(
            _skill_surface(path)
            for path in sorted(skill_root.glob("*/SKILL.md"))
        )
    mcp_root = source_root / "tiangong-lca-mcp"
    if mcp_root.is_dir():
        surfaces.extend(_scan_mcp(mcp_root))
    tidas_root = source_root / "tidas-tools"
    if tidas_root.is_dir():
        surfaces.extend(_scan_tidas_tools(tidas_root))
    by_id: dict[str, dict] = {}
    for row in surfaces:
        identity = row["surface_id"]
        if identity in by_id:
            continue
        by_id[identity] = row
    ordered = [by_id[key] for key in sorted(by_id)]
    coverage = {
        kind: sum(1 for row in ordered if row["kind"] == kind)
        for kind in sorted(REQUIRED_KINDS)
    }
    if any(count == 0 for count in coverage.values()):
        raise ValueError("TianGong capability inventory coverage is incomplete")
    observation = {
        "contract": OBSERVATION_CONTRACT,
        "observation_scope": "official_capability_interfaces_not_dataset_rows",
        "source_root_role": "discovery_copy_not_authority",
        "coverage": coverage,
        "surfaces": ordered,
    }
    observation["observation_sha256"] = _canonical_sha256(observation)
    return observation


def build_catalog(
    observation: Mapping[str, object],
    source_lock: Mapping[str, object],
    *,
    catalog_id: str,
) -> dict:
    if observation.get("contract") != OBSERVATION_CONTRACT:
        raise ValueError("capability observation contract is unsupported")
    if source_lock.get("contract") != "lca_research_kernel.source_lock/v1":
        raise ValueError("source lock contract is unsupported")
    rows = observation.get("surfaces")
    if not isinstance(rows, list):
        raise ValueError("capability observation surfaces are absent")
    descriptors = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("capability observation row is invalid")
        descriptor = {
            "contract": "lca_research_kernel.capability_descriptor/v1",
            "capability_id": row["surface_id"],
            **{
                key: deepcopy(row[key])
                for key in (
                    "kind",
                    "provider",
                    "authority_tier",
                    "source_lock_id",
                    "adapter_id",
                    "transport",
                    "entrypoint",
                    "input_contract",
                    "output_contract",
                    "effect",
                    "responsibility_tags",
                    "credential_policy",
                    "availability",
                    "disposition",
                    "timeout_seconds",
                    "retry_policy",
                    "idempotency",
                    "recovery_route",
                    "next_route",
                    "allowed_use",
                    "forbidden_claims",
                    "verification",
                    "supersedes",
                )
            },
        }
        descriptors.append(descriptor)
    return {
        "contract": "lca_research_kernel.capability_catalog/v1",
        "catalog_id": catalog_id,
        "source_lock_id": source_lock["lock_id"],
        "capabilities": descriptors,
    }


def compare_catalogs(
    adopted: Mapping[str, object], candidate: Mapping[str, object]
) -> dict:
    def rows(value: Mapping[str, object]) -> dict[str, object]:
        raw = value.get("capabilities")
        if not isinstance(raw, list):
            raise ValueError("catalog capabilities are absent")
        return {
            str(row["capability_id"]): deepcopy(dict(row))
            for row in raw
            if isinstance(row, Mapping) and "capability_id" in row
        }

    left = rows(adopted)
    right = rows(candidate)
    return {
        "contract": DIFF_CONTRACT,
        "status": "candidate_only_not_adopted",
        "adopted_catalog_id": adopted.get("catalog_id"),
        "candidate_catalog_id": candidate.get("catalog_id"),
        "added": sorted(set(right) - set(left)),
        "removed": sorted(set(left) - set(right)),
        "changed": sorted(
            key for key in set(left) & set(right) if left[key] != right[key]
        ),
        "auto_adopted": False,
    }
