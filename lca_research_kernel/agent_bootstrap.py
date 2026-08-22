"""Fail-closed D-18 product bootstrap for every public Agent runtime."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Mapping


STARTUP_MANIFEST_CONTRACT = "lca_research_kernel.agent_startup_manifest/v1"
STARTUP_CONTEXT_CONTRACT = "lca_research_kernel.agent_startup_context/v1"
PRODUCT_ID = "tiangong_native_lca_research_agent"
CORE_OUTCOME = "auditable_lca_research_outcome_or_bounded_limitation"
ADOPTED_STARTUP_MANIFEST_SHA256 = (
    "669dff9b3e5666c48a6a7c4f51948e95d6c1920fc22ee21e876506440c875a86"
)


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def semantic_fingerprint(architecture: Mapping[str, object]) -> str:
    """Hash only the D-18 semantics that every runtime must bind."""

    product = architecture.get("product")
    authority = architecture.get("authority_policy")
    autonomy = architecture.get("autonomy_policy")
    connections = architecture.get("connections")
    modules = architecture.get("modules")
    if not all(
        isinstance(value, (Mapping, list))
        for value in (product, authority, autonomy, connections, modules)
    ):
        raise ValueError("D-18 architecture semantics are incomplete")
    assert isinstance(product, Mapping)
    not_a = product.get("not_a")
    if (
        product.get("id") != PRODUCT_ID
        or product.get("core_outcome") != CORE_OUTCOME
        or not isinstance(product.get("definition"), str)
        or not isinstance(not_a, list)
        or not not_a
        or any(not isinstance(item, str) or not item.strip() for item in not_a)
    ):
        raise ValueError("D-18 product identity, purpose, or non-goals are invalid")
    assert isinstance(authority, Mapping)
    authority_rule = authority.get("tiangong_rule")
    if not isinstance(authority_rule, str) or "TianGong是项目第一权威" not in authority_rule:
        raise ValueError("D-18 TianGong authority boundary is absent")
    assert isinstance(connections, list)
    guarded_write = any(
        isinstance(row, Mapping)
        and row.get("from") == "assurance_boundary"
        and row.get("to") == "project_record"
        for row in connections
    )
    forbidden_direct_writers = any(
        isinstance(row, Mapping)
        and row.get("from") in {"model_gateway", "capability_gateway"}
        and row.get("to") == "project_record"
        for row in connections
    )
    assert isinstance(modules, list)
    truth_planes = [
        row
        for row in modules
        if isinstance(row, Mapping) and row.get("kind") == "truth_plane"
    ]
    if len(truth_planes) != 1 or truth_planes[0].get("id") != "project_record":
        raise ValueError("D-18 Project Record truth boundary is invalid")
    if not guarded_write or forbidden_direct_writers:
        raise ValueError("D-18 accepted-write boundary is invalid")
    return canonical_sha256(
        {
            "product": {
                "id": product["id"],
                "definition": product["definition"],
                "core_outcome": product["core_outcome"],
                "not_a": deepcopy(not_a),
            },
            "authority_policy": deepcopy(dict(authority)),
            "autonomy_policy": deepcopy(dict(autonomy)),  # type: ignore[arg-type]
            "project_truth": "project_record_only",
            "accepted_write": "assurance_boundary_only",
        }
    )


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unavailable or invalid") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _bound_path(root: Path, reference: Mapping[str, object], label: str) -> Path:
    if set(reference) - {"path", "sha256", "semantic_fingerprint"}:
        raise ValueError(f"{label} reference fields are unsupported")
    raw_path = reference.get("path")
    digest = reference.get("sha256")
    if (
        not isinstance(raw_path, str)
        or not raw_path.strip()
        or not isinstance(digest, str)
        or len(digest) != 64
    ):
        raise ValueError(f"{label} reference is incomplete")
    relative = Path(raw_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{label} path is unsafe")
    path = root / relative
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"{label} path escapes the runtime root") from error
    if not path.is_file() or _file_sha256(path) != digest:
        raise ValueError(f"{label} drifted")
    return path


def validate_startup_context(value: Mapping[str, object]) -> dict:
    if not isinstance(value, Mapping) or value.get("contract") != STARTUP_CONTEXT_CONTRACT:
        raise ValueError("startup context contract is unsupported")
    normalized = deepcopy(dict(value))
    stored = normalized.pop("context_sha256", None)
    if stored != canonical_sha256(normalized):
        raise ValueError("startup context drifted")
    if (
        normalized.get("product_id") != PRODUCT_ID
        or normalized.get("core_outcome") != CORE_OUTCOME
        or normalized.get("project_truth") != "project_record_only"
    ):
        raise ValueError("startup context drifted")
    architecture_semantics = normalized.get("architecture_semantics_sha256")
    if architecture_semantics is not None and (
        not isinstance(architecture_semantics, str)
        or len(architecture_semantics) != 64
    ):
        raise ValueError("startup context drifted")
    return deepcopy(dict(value))


class AgentBootstrap:
    @classmethod
    def load(
        cls,
        root: Path,
        manifest_path: Path,
        *,
        expected_manifest_sha256: str = None,
    ) -> dict:
        runtime_root = Path(root)
        manifest_file = Path(manifest_path)
        manifest_sha256 = _file_sha256(manifest_file)
        if expected_manifest_sha256 is not None:
            if (
                not isinstance(expected_manifest_sha256, str)
                or len(expected_manifest_sha256) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in expected_manifest_sha256
                )
            ):
                raise ValueError("expected startup manifest hash is invalid")
            if manifest_sha256 != expected_manifest_sha256:
                raise ValueError("startup manifest drifted from adopted code root")
        manifest = _read_json(manifest_file, "startup manifest")
        if manifest.get("contract") != STARTUP_MANIFEST_CONTRACT:
            raise ValueError("startup manifest contract is unsupported")
        required = {
            "contract",
            "manifest_id",
            "product_contract",
            "capability_catalog",
            "source_lock",
            "permission_policy",
            "architecture_semantics",
            "semantic_graph_contract",
            "research_skill_registry",
            "governed_lca_knowledge",
            "domain_rule_registry",
        }
        if set(manifest) != required:
            raise ValueError("startup manifest fields are incomplete or unsupported")
        references = {}
        for key, label in (
            ("product_contract", "product contract"),
            ("capability_catalog", "capability catalog"),
            ("source_lock", "source lock"),
            ("permission_policy", "permission policy"),
            ("architecture_semantics", "architecture semantics"),
            ("semantic_graph_contract", "semantic graph contract"),
            ("research_skill_registry", "research Skill registry"),
            ("governed_lca_knowledge", "governed LCA knowledge"),
            ("domain_rule_registry", "domain rule registry"),
        ):
            reference = manifest.get(key)
            if not isinstance(reference, Mapping):
                raise ValueError(f"{label} reference is absent")
            references[key] = _bound_path(runtime_root, reference, label)
        architecture = _read_json(references["product_contract"], "product contract")
        try:
            fingerprint = semantic_fingerprint(architecture)
        except ValueError as error:
            raise ValueError("product contract semantic fingerprint drifted") from error
        product_reference = manifest["product_contract"]
        assert isinstance(product_reference, Mapping)
        if product_reference.get("semantic_fingerprint") != fingerprint:
            raise ValueError("product contract semantic fingerprint drifted")
        from .architecture_semantics import ArchitectureSemantics

        semantics = ArchitectureSemantics.load(
            runtime_root, references["architecture_semantics"]
        )
        if semantics.contract["architecture_ref"]["sha256"] != _file_sha256(  # type: ignore[index]
            references["product_contract"]
        ):
            raise ValueError("architecture semantics product binding drifted")
        product = architecture["product"]
        authority = architecture["authority_policy"]
        assert isinstance(product, Mapping)
        assert isinstance(authority, Mapping)
        context = {
            "contract": STARTUP_CONTEXT_CONTRACT,
            "manifest_id": manifest["manifest_id"],
            "manifest_sha256": manifest_sha256,
            "architecture_sha256": _file_sha256(references["product_contract"]),
            "semantic_fingerprint": fingerprint,
            "catalog_sha256": _file_sha256(references["capability_catalog"]),
            "source_lock_sha256": _file_sha256(references["source_lock"]),
            "permission_policy_sha256": _file_sha256(references["permission_policy"]),
            "architecture_semantics_sha256": _file_sha256(
                references["architecture_semantics"]
            ),
            "semantic_graph_contract_sha256": _file_sha256(
                references["semantic_graph_contract"]
            ),
            "research_skill_registry_sha256": _file_sha256(
                references["research_skill_registry"]
            ),
            "governed_lca_knowledge_sha256": _file_sha256(
                references["governed_lca_knowledge"]
            ),
            "domain_rule_registry_sha256": _file_sha256(
                references["domain_rule_registry"]
            ),
            "product_id": product["id"],
            "definition": product["definition"],
            "core_outcome": product["core_outcome"],
            "non_goals": deepcopy(product["not_a"]),
            "authority_rule": authority["tiangong_rule"],
            "project_truth": "project_record_only",
            "accepted_write_boundary": "assurance_boundary_only",
        }
        context["context_sha256"] = canonical_sha256(context)
        return validate_startup_context(context)
