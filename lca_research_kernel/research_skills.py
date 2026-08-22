"""Source-locked, progressively disclosed research method Skills."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Mapping, Optional

from .semantic_graph import canonical_sha256
from .semantic_retrieval import SIGNATURE_CONTRACT


REGISTRY_CONTRACT = "lca_research_kernel.research_skill_registry/v1"
SKILL_CONTRACT = "lca_research_kernel.research_skill/v1"
SELECTION_CONTRACT = "lca_research_kernel.research_skill_selection/v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SKILL_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CONTRACT_REQUIRED = {
    "contract",
    "skill_id",
    "version",
    "title",
    "description",
    "applicability",
    "required_inputs",
    "expected_artifact",
    "accepted_state_delta",
    "capability_ids",
    "upstream_skill_ids",
    "domain_rule_ids",
    "source_lock_ids",
    "output_classifications",
    "outcomes",
    "acceptance_checks",
    "failure_boundaries",
    "forbidden",
    "permission_effects",
    "resources",
}


def _read_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unavailable or invalid") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bound_path(root: Path, raw: object, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{label} path is invalid")
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{label} path is unsafe")
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"{label} path escaped repository") from error
    return path


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ValueError("Skill body frontmatter is absent")
    raw, body = text[4:].split("\n---\n", 1)
    metadata = {}
    for line in raw.splitlines():
        if ":" not in line:
            raise ValueError("Skill body frontmatter is invalid")
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip()
    if set(metadata) != {"name", "description"}:
        raise ValueError("Skill body frontmatter fields are unsupported")
    if (
        SKILL_ID_RE.fullmatch(metadata["name"]) is None
        or not metadata["description"].startswith("Use when ")
        or not body.strip()
    ):
        raise ValueError("Skill body discovery metadata is invalid")
    return metadata, body.strip()


def _string_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"Skill {label} is invalid")
    if len(value) != len(set(value)):
        raise ValueError(f"Skill {label} is duplicated")
    return list(value)


class ResearchSkillRegistry:
    """Validate, discover, select, and progressively disclose thin Skills."""

    def __init__(
        self,
        *,
        root: Path,
        registry: Mapping[str, object],
        capability_ids: set[str],
        source_lock_ids: set[str],
    ):
        self.root = Path(root).resolve()
        self._registry = deepcopy(dict(registry))
        self._contracts: dict[str, dict] = {}
        self._metadata: dict[str, dict] = {}
        self._bodies: dict[str, str] = {}
        self._resources: dict[str, dict[str, dict]] = {}
        self._load_entries(capability_ids, source_lock_ids)

    @classmethod
    def load(
        cls,
        root: Path,
        registry_path: Path,
        *,
        capability_catalog_path: Path,
        source_lock_path: Path,
    ) -> "ResearchSkillRegistry":
        return cls.from_value(
            root,
            _read_json(Path(registry_path), "research Skill registry"),
            capability_catalog_path=capability_catalog_path,
            source_lock_path=source_lock_path,
        )

    @classmethod
    def from_value(
        cls,
        root: Path,
        value: Mapping[str, object],
        *,
        capability_catalog_path: Path,
        source_lock_path: Path,
    ) -> "ResearchSkillRegistry":
        catalog = _read_json(Path(capability_catalog_path), "capability catalog")
        source_lock = _read_json(Path(source_lock_path), "source lock")
        capabilities = catalog.get("capabilities")
        sources = source_lock.get("sources")
        if not isinstance(capabilities, list) or not isinstance(sources, list):
            raise ValueError("research Skill authority catalogs are invalid")
        return cls(
            root=Path(root),
            registry=value,
            capability_ids={str(row.get("capability_id")) for row in capabilities if isinstance(row, Mapping)},
            source_lock_ids={str(row.get("source_id")) for row in sources if isinstance(row, Mapping)},
        )

    def _load_entries(
        self, capability_ids: set[str], source_lock_ids: set[str]
    ) -> None:
        if (
            self._registry.get("contract") != REGISTRY_CONTRACT
            or self._registry.get("project_truth") != "project_record_only"
            or self._registry.get("selection_basis") != "semantic_state_transition"
            or not isinstance(self._registry.get("skills"), list)
        ):
            raise ValueError("research Skill registry contract is unsupported")
        seen = set()
        for entry in self._registry["skills"]:
            if not isinstance(entry, Mapping):
                raise ValueError("research Skill registry entry is invalid")
            skill_id = entry.get("skill_id")
            if (
                not isinstance(skill_id, str)
                or SKILL_ID_RE.fullmatch(skill_id) is None
                or skill_id in seen
            ):
                raise ValueError("research Skill registry identity is invalid or duplicated")
            seen.add(skill_id)
            contract_path = _bound_path(self.root, entry.get("contract_path"), "Skill contract")
            body_path = _bound_path(self.root, entry.get("skill_md_path"), "Skill body")
            if _file_sha256(contract_path) != entry.get("contract_sha256"):
                raise ValueError(f"Skill contract drifted: {skill_id}")
            if _file_sha256(body_path) != entry.get("skill_md_sha256"):
                raise ValueError(f"Skill body drifted: {skill_id}")
            contract = _read_json(contract_path, f"Skill contract {skill_id}")
            if (
                set(contract) != CONTRACT_REQUIRED
                or contract.get("contract") != SKILL_CONTRACT
                or contract.get("skill_id") != skill_id
            ):
                raise ValueError(f"Skill contract fields drifted: {skill_id}")
            metadata, body = _frontmatter(body_path.read_text(encoding="utf-8"))
            if metadata["name"] != skill_id or metadata["description"] != contract.get("description"):
                raise ValueError(f"Skill discovery metadata drifted: {skill_id}")
            capabilities = _string_list(contract.get("capability_ids"), "capability_ids")
            locks = _string_list(contract.get("source_lock_ids"), "source_lock_ids")
            if not set(capabilities).issubset(capability_ids):
                raise ValueError(f"Skill capability is not source-catalogued: {skill_id}")
            if not set(locks).issubset(source_lock_ids):
                raise ValueError(f"Skill source lock is absent: {skill_id}")
            for key in (
                "required_inputs",
                "upstream_skill_ids",
                "domain_rule_ids",
                "output_classifications",
                "outcomes",
                "acceptance_checks",
                "failure_boundaries",
                "forbidden",
                "permission_effects",
            ):
                _string_list(contract.get(key), key)
            if not isinstance(contract.get("applicability"), Mapping):
                raise ValueError(f"Skill applicability is invalid: {skill_id}")
            resources = contract.get("resources")
            if not isinstance(resources, list):
                raise ValueError(f"Skill resources are invalid: {skill_id}")
            resource_index = {}
            for resource in resources:
                if not isinstance(resource, Mapping):
                    raise ValueError(f"Skill resource is invalid: {skill_id}")
                resource_id = resource.get("resource_id")
                path = _bound_path(self.root, resource.get("path"), "Skill resource")
                if (
                    not isinstance(resource_id, str)
                    or not resource_id
                    or resource_id in resource_index
                    or _file_sha256(path) != resource.get("sha256")
                ):
                    raise ValueError(f"Skill resource drifted: {skill_id}")
                resource_index[resource_id] = {**dict(resource), "resolved_path": path}
            self._contracts[skill_id] = deepcopy(contract)
            self._metadata[skill_id] = {
                "skill_id": skill_id,
                "version": contract["version"],
                "name": contract["title"],
                "description": contract["description"],
                "routing_summary": deepcopy(contract["applicability"]),
            }
            self._bodies[skill_id] = body
            self._resources[skill_id] = resource_index

    def list_metadata(self) -> list[dict]:
        return [deepcopy(self._metadata[key]) for key in sorted(self._metadata)]

    def contract(self, skill_id: str) -> dict:
        if skill_id not in self._contracts:
            raise ValueError("research Skill identity is unknown")
        return deepcopy(self._contracts[skill_id])

    def required_domain_rule_ids(self) -> list[str]:
        """Return the deterministic rule closure required by the method pack."""

        return sorted(
            {
                rule_id
                for contract in self._contracts.values()
                for rule_id in contract["domain_rule_ids"]
            }
        )

    def projection_descriptors(self) -> list[dict]:
        descriptors = []
        registry_entries = {
            entry["skill_id"]: entry for entry in self._registry["skills"]
        }
        for skill_id in sorted(self._contracts):
            contract = self._contracts[skill_id]
            entry = registry_entries[skill_id]
            descriptors.append(
                {
                    "skill_id": skill_id,
                    "version": contract["version"],
                    "source_ref": entry["contract_path"],
                    "source_sha256": entry["contract_sha256"],
                    "required_domain_rule_ids": deepcopy(contract["domain_rule_ids"]),
                    "capability_ids": deepcopy(contract["capability_ids"]),
                }
            )
        return descriptors

    def disclose(
        self,
        skill_id: str,
        *,
        level: str,
        resource_ids: Optional[list[str]] = None,
    ) -> dict:
        if skill_id not in self._contracts:
            raise ValueError("research Skill identity is unknown")
        if level not in {"L1", "L2", "L3"}:
            raise ValueError("research Skill disclosure level is unsupported")
        result = {
            "contract": "lca_research_kernel.research_skill_disclosure/v1",
            "level": level,
            "metadata": deepcopy(self._metadata[skill_id]),
        }
        if level in {"L2", "L3"}:
            result["instructions"] = self._bodies[skill_id]
            result["method_contract"] = deepcopy(self._contracts[skill_id])
        if level == "L3":
            requested = resource_ids or []
            if any(not isinstance(item, str) or not item for item in requested):
                raise ValueError("research Skill resource selection is invalid")
            resources = self._resources[skill_id]
            if not set(requested).issubset(resources):
                raise ValueError("research Skill resource identity is unknown")
            result["resource_contents"] = [
                {
                    "resource_id": resource_id,
                    "path": resources[resource_id]["path"],
                    "sha256": resources[resource_id]["sha256"],
                    "content": resources[resource_id]["resolved_path"].read_text(
                        encoding="utf-8"
                    ),
                    "instruction_effect": "none",
                }
                for resource_id in requested
            ]
        result["disclosure_sha256"] = canonical_sha256(result)
        return result

    def select(self, signature: Mapping[str, object]) -> dict:
        normalized = self._validate_signature(signature)
        candidates = []
        for skill_id, contract in self._contracts.items():
            score, matched = self._score(contract["applicability"], normalized)
            # Object-kind overlap is useful tie-breaking evidence, but it is not
            # enough to activate a method.  Require a state/artifact predicate.
            if score < 30:
                continue
            candidates.append(
                {
                    "skill_id": skill_id,
                    "score": score,
                    "matched_semantic_predicates": matched,
                    "expected_state_change": contract["accepted_state_delta"],
                    "expected_artifact": contract["expected_artifact"],
                    "claim_ceiling": normalized["claim_ceiling"],
                    "permission_effects": deepcopy(contract["permission_effects"]),
                }
            )
        candidates.sort(key=lambda row: (-row["score"], row["skill_id"]))
        selected = candidates[:1]
        result = {
            "contract": SELECTION_CONTRACT,
            "signature_sha256": normalized["signature_sha256"],
            "selected_skill_ids": [row["skill_id"] for row in selected],
            "decisions": selected,
            "alternatives": [
                {
                    "skill_id": row["skill_id"],
                    "score": row["score"],
                    "not_selected_reason": "lower_semantic_state_contribution",
                }
                for row in candidates[1:4]
            ],
            "next_route": (
                "disclose-selected-research-method"
                if selected
                else "preserve-unresolved-method-boundary"
            ),
            "selection_basis": "semantic_state_transition",
        }
        result["selection_sha256"] = canonical_sha256(result)
        return result

    @staticmethod
    def _validate_signature(signature: Mapping[str, object]) -> dict:
        if not isinstance(signature, Mapping) or signature.get("contract") != SIGNATURE_CONTRACT:
            raise ValueError("research Skill selection signature is unsupported")
        normalized = deepcopy(dict(signature))
        stored = normalized.pop("signature_sha256", None)
        if not isinstance(stored, str) or canonical_sha256(normalized) != stored:
            raise ValueError("research Skill selection signature drifted")
        normalized["signature_sha256"] = stored
        return normalized

    @staticmethod
    def _score(
        applicability: Mapping[str, object], signature: Mapping[str, object]
    ) -> tuple[int, list[str]]:
        desired = str(signature.get("desired_artifact") or "").replace("-", "_")
        artifacts = {
            str(item).replace("-", "_")
            for item in applicability.get("desired_artifacts") or []
        }
        required_roles = set(signature.get("required_evidence_roles") or [])
        missing_roles = set(signature.get("missing_roles") or [])
        accepted_roles = set(signature.get("accepted_roles") or [])
        requirements = signature.get("requirements") or {}
        conflicts = set(signature.get("conflict_types") or [])
        target_kinds = set(signature.get("target_object_kinds") or [])
        score = 0
        matched = []
        if desired in artifacts:
            score += 100
            matched.append("desired_artifact:" + desired)
        for role in sorted(set(applicability.get("semantic_roles") or []) & required_roles):
            score += 40
            matched.append("required_role:" + role)
        for role in sorted(set(applicability.get("semantic_roles") or []) & missing_roles):
            score += 30
            matched.append("missing_role:" + role)
        for role in sorted(set(applicability.get("semantic_roles") or []) & accepted_roles):
            score += 10
            matched.append("accepted_role:" + role)
        if isinstance(requirements, Mapping):
            for requirement in sorted(applicability.get("requirements") or []):
                if requirements.get(requirement) is True:
                    score += 15
                    matched.append("requirement:" + requirement)
        for conflict in sorted(set(applicability.get("conflict_types") or []) & conflicts):
            score += 20
            matched.append("conflict:" + conflict)
        for kind in sorted(set(applicability.get("target_object_kinds") or []) & target_kinds):
            score += 5
            matched.append("target_kind:" + kind)
        return score, matched
