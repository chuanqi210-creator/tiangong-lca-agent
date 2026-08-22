"""Versioned, source-bound LCA domain-rule resolution.

This is a private implementation seam of the existing LCA Domain Kernel.  It
does not create project facts or a second authority: it only resolves adopted
rules by content identity so the Research Orchestrator and Assurance can fail
closed before candidate work begins.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Mapping, Sequence

from .content_identity import canonical_sha256


REGISTRY_CONTRACT = "lca_research_kernel.domain_rule_registry/v1"
RULE_CONTRACT = "lca_research_kernel.domain_rule/v1"
CLOSURE_CONTRACT = "lca_research_kernel.domain_rule_closure/v1"
RULE_FIELDS = {
    "contract",
    "rule_id",
    "version",
    "title",
    "statement",
    "source_lock_ids",
    "enforcement",
}
REGISTRY_FIELDS = {
    "contract",
    "registry_id",
    "version",
    "project_truth",
    "rules",
}
ENFORCEMENT_MODES = {
    "mechanical_precondition",
    "assurance_check",
    "claim_ceiling",
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _strings(value: object, message: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError(message)
    return list(value)


def validate_domain_rule(
    value: Mapping[str, object], *, known_source_ids: set[str] | None = None
) -> dict:
    if (
        not isinstance(value, Mapping)
        or set(value) != RULE_FIELDS
        or value.get("contract") != RULE_CONTRACT
        or value.get("enforcement") not in ENFORCEMENT_MODES
    ):
        raise ValueError("domain rule fields are invalid")
    rule = deepcopy(dict(value))
    for field in ("rule_id", "version", "title", "statement"):
        if not isinstance(rule.get(field), str) or not rule[field].strip():
            raise ValueError("domain rule fields are invalid")
    sources = _strings(
        rule.get("source_lock_ids"), "domain rule source bindings are invalid"
    )
    if not sources or sources != sorted(sources):
        raise ValueError("domain rule source bindings are invalid")
    if known_source_ids is not None and not set(sources).issubset(known_source_ids):
        raise ValueError("domain rule source binding is unavailable")
    return rule


class DomainRuleResolver:
    """Resolve the exact adopted rule closure required by selected methods."""

    def __init__(
        self,
        *,
        registry: Mapping[str, object],
        source_lock: Mapping[str, object],
    ) -> None:
        if (
            not isinstance(registry, Mapping)
            or set(registry) != REGISTRY_FIELDS
            or registry.get("contract") != REGISTRY_CONTRACT
            or registry.get("project_truth") != "project_record_only"
            or not isinstance(registry.get("registry_id"), str)
            or not registry["registry_id"]
            or not isinstance(registry.get("version"), str)
            or not registry["version"]
        ):
            raise ValueError("domain rule registry is invalid")
        if not isinstance(source_lock, Mapping):
            raise ValueError("domain rule source lock is invalid")
        sources = source_lock.get("sources")
        if (
            source_lock.get("contract")
            != "lca_research_kernel.source_lock/v1"
            or not isinstance(sources, list)
        ):
            raise ValueError("domain rule source lock is invalid")
        source_ids = {
            str(row["source_id"])
            for row in sources
            if isinstance(row, Mapping)
            and isinstance(row.get("source_id"), str)
            and row["source_id"]
        }
        if len(source_ids) != len(sources):
            raise ValueError("domain rule source lock is invalid")
        raw_rules = registry.get("rules")
        if not isinstance(raw_rules, list) or not raw_rules:
            raise ValueError("domain rule registry is empty")
        index: dict[str, dict] = {}
        for raw in raw_rules:
            rule = validate_domain_rule(raw, known_source_ids=source_ids)
            rule_id = rule["rule_id"]
            if rule_id in index:
                raise ValueError("domain rule identity is duplicated")
            index[rule_id] = rule
        if list(index) != sorted(index):
            raise ValueError("domain rule registry order is not deterministic")
        self._registry = deepcopy(dict(registry))
        self._index = index
        self.registry_sha256 = canonical_sha256(self._registry)

    @classmethod
    def from_values(
        cls,
        *,
        registry: Mapping[str, object],
        source_lock: Mapping[str, object],
    ) -> "DomainRuleResolver":
        return cls(registry=registry, source_lock=source_lock)

    @classmethod
    def load(
        cls,
        root: Path,
        *,
        registry_path: Path,
        source_lock_path: Path,
    ) -> "DomainRuleResolver":
        root = Path(root).resolve()
        paths = (Path(registry_path).resolve(), Path(source_lock_path).resolve())
        for path in paths:
            try:
                path.relative_to(root)
            except ValueError as error:
                raise ValueError("domain rule source escaped repository") from error
        try:
            registry = json.loads(paths[0].read_text(encoding="utf-8"))
            source_lock = json.loads(paths[1].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("domain rule registry is unavailable") from error
        return cls.from_values(registry=registry, source_lock=source_lock)

    @property
    def rules(self) -> tuple[dict, ...]:
        return tuple(deepcopy(self._index[key]) for key in sorted(self._index))

    def resolve(self, required_rule_ids: Sequence[str]) -> dict:
        required = sorted(
            _strings(list(required_rule_ids), "required domain rule ids are invalid")
        )
        selected_ids = [rule_id for rule_id in required if rule_id in self._index]
        missing = sorted(set(required) - set(selected_ids))
        closure = {
            "contract": CLOSURE_CONTRACT,
            "registry_id": self._registry["registry_id"],
            "registry_version": self._registry["version"],
            "registry_sha256": self.registry_sha256,
            "required_domain_rule_ids": required,
            "selected_domain_rule_ids": selected_ids,
            "selected_domain_rules": [
                deepcopy(self._index[rule_id]) for rule_id in selected_ids
            ],
            "missing_domain_rule_ids": missing,
            "status": "complete" if not missing else "incomplete",
        }
        closure["closure_sha256"] = canonical_sha256(closure)
        return closure

    def require(self, required_rule_ids: Sequence[str]) -> dict:
        closure = self.resolve(required_rule_ids)
        if closure["missing_domain_rule_ids"]:
            raise ValueError(
                "required domain rules are unavailable: "
                + ",".join(closure["missing_domain_rule_ids"])
            )
        return closure
