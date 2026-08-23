"""Validated, content-bound LCA knowledge packs behind one deep interface."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Mapping, Sequence


PACK_CONTRACT = "lca_research_kernel.lca_knowledge_pack/v1"
PACK_FIELDS = {
    "contract",
    "pack_id",
    "version",
    "status",
    "scope",
    "authority_status",
    "claim_ceiling",
    "license_policy",
    "sources",
    "entries",
}
SOURCE_FIELDS = {
    "source_id",
    "source_type",
    "owner",
    "locator",
    "version",
    "integrity",
    "accessed_at",
    "evidence_status",
    "license_status",
    "redistribution_scope",
    "applicability",
}
ENTRY_FIELDS = {
    "knowledge_id",
    "source_kind",
    "relation",
    "target_kind",
    "statement",
    "renderings",
    "knowledge_roles",
    "authority",
    "authority_status",
    "claim_ceiling",
    "applicability",
    "upstream_source_ids",
    "source_support",
}
SUPPORT_FIELDS = {
    "source_id",
    "location",
    "support_kind",
    "support_value",
    "support_sha256",
}
APPLICABILITY_FIELDS = {
    "geographies",
    "times",
    "technologies",
    "methods",
}
SOURCE_TYPES = {"official_web", "official_package", "repository_contract"}
LICENSE_STATUSES = {"MIT", "project_owned_contract", "reuse_terms_not_verified"}
REDISTRIBUTION_SCOPES = {
    "link_metadata_and_minimal_excerpt_only",
    "repository_contract_only",
    "source_metadata_and_minimal_excerpt_only",
}
EVIDENCE_STATUSES = {
    "adopted_product_contract",
    "external_conceptual_calibration_only",
    "official_identity_and_version_metadata",
}
CLAIM_CEILINGS = {
    "method_explanation_only",
    "illustration_only",
    "limitation_only",
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"knowledge pack {label} is invalid")
    return value


def _strings(value: object, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError(f"knowledge pack {label} is invalid")
    return sorted(value)


def _applicability(value: object) -> dict[str, list[str]]:
    if not isinstance(value, Mapping) or set(value) != APPLICABILITY_FIELDS:
        raise ValueError("knowledge pack applicability is invalid")
    return {
        field: _strings(value[field], f"applicability {field}")
        for field in sorted(APPLICABILITY_FIELDS)
    }


def _source_matches_entry(
    source: Mapping[str, Sequence[str]],
    entry: Mapping[str, Sequence[str]],
) -> bool:
    for field in sorted(APPLICABILITY_FIELDS):
        source_values = set(source[field])
        entry_values = set(entry[field])
        if (
            "*" not in source_values
            and "*" not in entry_values
            and not source_values.intersection(entry_values)
        ):
            return False
    return True


class GovernedKnowledgePackSet:
    """Load pack refs once and expose normalized entries, never project facts."""

    def __init__(
        self,
        root: Path,
        references: Sequence[Mapping[str, object]],
    ) -> None:
        self.root = Path(root).resolve()
        self._entries: dict[str, dict] = {}
        self._packs: dict[str, dict] = {}
        if (
            not isinstance(references, Sequence)
            or isinstance(references, (str, bytes))
            or any(not isinstance(ref, Mapping) for ref in references)
        ):
            raise ValueError("governed knowledge pack references are invalid")
        seen_paths: set[str] = set()
        for reference in references:
            if set(reference) != {"pack_id", "path", "sha256"}:
                raise ValueError("governed knowledge pack reference is invalid")
            pack_id = _text(reference.get("pack_id"), "reference identity")
            relative = _text(reference.get("path"), "reference path")
            expected_sha256 = _text(
                reference.get("sha256"), "reference sha256"
            )
            if (
                pack_id in self._packs
                or relative in seen_paths
                or Path(relative).is_absolute()
                or ".." in Path(relative).parts
                or len(expected_sha256) != 64
            ):
                raise ValueError("governed knowledge pack reference is invalid")
            path = (self.root / relative).resolve()
            try:
                path.relative_to(self.root)
            except ValueError as error:
                raise ValueError("governed knowledge pack escaped root") from error
            if (
                not path.is_file()
                or path.is_symlink()
                or _sha256_bytes(path.read_bytes()) != expected_sha256
            ):
                raise ValueError("governed knowledge pack drifted")
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("governed knowledge pack is invalid") from error
            pack = self._validate_pack(value, path, expected_sha256)
            if pack["pack_id"] != pack_id:
                raise ValueError("governed knowledge pack identity drifted")
            self._packs[pack_id] = pack
            seen_paths.add(relative)

    def projection_entries(self) -> list[dict]:
        return [
            deepcopy(self._entries[knowledge_id]["projection"])
            for knowledge_id in sorted(self._entries)
        ]

    def contains(self, knowledge_id: str) -> bool:
        return knowledge_id in self._entries

    def render_entry(self, knowledge_id: str, response_language: str) -> dict:
        stored = self._entries.get(knowledge_id)
        if stored is None:
            raise ValueError("governed knowledge pack identity is unavailable")
        if response_language not in {"en", "zh-CN"}:
            raise ValueError("governed knowledge pack language is unsupported")
        projected = deepcopy(stored["projection"])
        projected["statement"] = projected["renderings"][response_language]
        projected["rendering_language"] = response_language
        projected["rendering_lineage"] = deepcopy(stored["lineage"])
        projected["rendering_lineage"].update(
            {
                "output_language": response_language,
                "transformation": (
                    "product_governed_synthesis"
                    if response_language == "en"
                    else "product_governed_translation"
                ),
                "rendering_sha256": _sha256_bytes(
                    projected["statement"].encode("utf-8")
                ),
            }
        )
        return projected

    def _validate_pack(
        self,
        value: object,
        path: Path,
        pack_sha256: str,
    ) -> dict:
        if (
            not isinstance(value, Mapping)
            or set(value) != PACK_FIELDS
            or value.get("contract") != PACK_CONTRACT
            or value.get("status") != "active"
            or value.get("authority_status") != "product_governed_calibration"
            or value.get("claim_ceiling") not in CLAIM_CEILINGS
        ):
            raise ValueError("governed knowledge pack is invalid")
        pack = deepcopy(dict(value))
        for field in ("pack_id", "version", "scope", "license_policy"):
            _text(pack.get(field), field)
        sources = pack.get("sources")
        entries = pack.get("entries")
        if not isinstance(sources, list) or not sources:
            raise ValueError("governed knowledge pack sources are invalid")
        if not isinstance(entries, list) or not entries:
            raise ValueError("governed knowledge pack entries are invalid")
        source_by_id: dict[str, dict] = {}
        for source in sources:
            normalized = self._validate_source(source)
            source_id = normalized["source_id"]
            if source_id in source_by_id:
                raise ValueError("governed knowledge pack source is duplicated")
            source_by_id[source_id] = normalized
        for raw_entry in entries:
            self._validate_entry(
                raw_entry,
                source_by_id=source_by_id,
                pack=pack,
                path=path,
                pack_sha256=pack_sha256,
            )
        return pack

    def _validate_source(self, value: object) -> dict:
        if not isinstance(value, Mapping) or set(value) != SOURCE_FIELDS:
            raise ValueError("governed knowledge pack source is invalid")
        source = deepcopy(dict(value))
        for field in SOURCE_FIELDS - {"applicability"}:
            _text(source.get(field), f"source {field}")
        if source["source_type"] not in SOURCE_TYPES:
            raise ValueError("governed knowledge pack source type is invalid")
        if (
            source["license_status"] not in LICENSE_STATUSES
            or source["redistribution_scope"] not in REDISTRIBUTION_SCOPES
            or source["evidence_status"] not in EVIDENCE_STATUSES
        ):
            raise ValueError("governed knowledge pack source governance is invalid")
        source["applicability"] = _applicability(source["applicability"])
        locator = source["locator"]
        if source["source_type"] == "repository_contract":
            if Path(locator).is_absolute() or ".." in Path(locator).parts:
                raise ValueError("governed knowledge pack source escaped root")
            source_path = (self.root / locator).resolve()
            expected = source["integrity"].removeprefix("sha256:")
            if (
                not source_path.is_file()
                or source_path.is_symlink()
                or len(expected) != 64
                or source["version"] != f"sha256:{expected}"
                or _sha256_bytes(source_path.read_bytes()) != expected
            ):
                raise ValueError("governed knowledge pack source drifted")
        elif not locator.startswith("https://"):
            raise ValueError("governed knowledge pack source locator is invalid")
        return source

    def _validate_entry(
        self,
        value: object,
        *,
        source_by_id: Mapping[str, Mapping[str, object]],
        pack: Mapping[str, object],
        path: Path,
        pack_sha256: str,
    ) -> None:
        if (
            not isinstance(value, Mapping)
            or set(value) != ENTRY_FIELDS
            or value.get("authority") != "governed_lca_domain_knowledge"
            or value.get("authority_status") != "product_governed_calibration"
            or value.get("claim_ceiling") not in CLAIM_CEILINGS
        ):
            raise ValueError("governed knowledge pack entry is invalid")
        entry = deepcopy(dict(value))
        knowledge_id = _text(entry.get("knowledge_id"), "entry identity")
        if knowledge_id in self._entries:
            raise ValueError("governed knowledge pack entry is duplicated")
        for field in ("source_kind", "relation", "target_kind", "statement"):
            _text(entry.get(field), f"entry {field}")
        entry["knowledge_roles"] = _strings(
            entry.get("knowledge_roles"), "entry roles"
        )
        entry["upstream_source_ids"] = _strings(
            entry.get("upstream_source_ids"), "entry upstream sources"
        )
        entry["applicability"] = _applicability(entry.get("applicability"))
        renderings = entry.get("renderings")
        if (
            not isinstance(renderings, Mapping)
            or set(renderings) != {"en", "zh-CN"}
            or any(not isinstance(text, str) or not text.strip() for text in renderings.values())
            or entry["statement"] != renderings["en"]
        ):
            raise ValueError("governed knowledge pack renderings are invalid")
        if any(source_id not in source_by_id for source_id in entry["upstream_source_ids"]):
            raise ValueError("governed knowledge pack upstream source is unavailable")
        supports = entry.get("source_support")
        if not isinstance(supports, list) or not supports:
            raise ValueError("governed knowledge pack support is invalid")
        support_by_id: dict[str, dict] = {}
        for support in supports:
            if not isinstance(support, Mapping) or set(support) != SUPPORT_FIELDS:
                raise ValueError("governed knowledge pack support is invalid")
            normalized = deepcopy(dict(support))
            for field in SUPPORT_FIELDS:
                _text(normalized.get(field), f"support {field}")
            if normalized["support_kind"] not in {
                "minimal_excerpt",
                "structured_contract_projection",
            }:
                raise ValueError("governed knowledge pack support kind is invalid")
            if _sha256_bytes(normalized["support_value"].encode("utf-8")) != normalized[
                "support_sha256"
            ]:
                raise ValueError("governed knowledge pack support drifted")
            source_id = normalized["source_id"]
            if source_id in support_by_id:
                raise ValueError("governed knowledge pack support is duplicated")
            support_by_id[source_id] = normalized
        if set(support_by_id) != set(entry["upstream_source_ids"]):
            raise ValueError("governed knowledge pack support coverage is incomplete")
        for source_id in entry["upstream_source_ids"]:
            if not _source_matches_entry(
                source_by_id[source_id]["applicability"],  # type: ignore[arg-type]
                entry["applicability"],
            ):
                raise ValueError("governed knowledge pack applicability conflicts")
        relative = path.relative_to(self.root).as_posix()
        projection = {
            **{
                field: deepcopy(entry[field])
                for field in (
                    "knowledge_id",
                    "source_kind",
                    "relation",
                    "target_kind",
                    "statement",
                    "renderings",
                    "knowledge_roles",
                    "authority",
                    "authority_status",
                    "claim_ceiling",
                    "applicability",
                )
            },
            "source_ref": {
                "path": relative,
                "sha256": pack_sha256,
                "entry_id": knowledge_id,
            },
            "pack_id": pack["pack_id"],
            "pack_version": pack["version"],
        }
        self._entries[knowledge_id] = {
            "projection": projection,
            "lineage": {
                "authority_status": entry["authority_status"],
                "source_language": "en",
                "pack_id": pack["pack_id"],
                "pack_version": pack["version"],
                "pack_sha256": pack_sha256,
                "license_policy": pack["license_policy"],
                "source_support": [
                    support_by_id[source_id]
                    for source_id in entry["upstream_source_ids"]
                ],
                "upstream_sources": [
                    deepcopy(dict(source_by_id[source_id]))
                    for source_id in entry["upstream_source_ids"]
                ],
                "applicability": deepcopy(entry["applicability"]),
            },
        }
