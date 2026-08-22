"""Validate source-bound TianGong/TIDAS object graph slices.

The normalized slices are candidate material content.  This module never
accepts facts and never persists a graph; it only verifies that a disposable
semantic projection can be rebuilt from accepted Project Record locators and
an adopted TianGong schema/tool source lock.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Iterable, Mapping

from .semantic_graph import canonical_sha256


SLICE_CONTRACT = "lca_research_service.tidas_object_graph_slice/v1"
SLICE_FIELDS = {
    "contract",
    "source_lock_ids",
    "objects",
    "relations",
    "candidate_only",
    "scientifically_validated",
}
OBJECT_FIELDS = {
    "object_ref",
    "object_kind",
    "identity",
    "version",
    "label",
    "semantic_roles",
    "attributes",
}
RELATION_FIELDS = {
    "edge_kind",
    "source_object_ref",
    "target_object_ref",
    "attributes",
}
OBJECT_KINDS = {
    "LifecycleModel",
    "Process",
    "Exchange",
    "Flow",
    "Provider",
    "FlowProperty",
    "UnitGroup",
    "Unit",
    "LCIAMethod",
    "ImpactCategory",
    "ApplicabilityScope",
}
VERSIONED_DATASET_KINDS = {
    "LifecycleModel",
    "Process",
    "Flow",
    "FlowProperty",
    "UnitGroup",
    "LCIAMethod",
    "ImpactCategory",
}
RELATION_KINDS = {
    "contains_process",
    "has_exchange",
    "references_flow",
    "provided_by",
    "derived_from",
    "has_property",
    "uses_unit_group",
    "contains_unit",
    "contains_impact_category",
    "has_applicability",
    "calculated_with",
}
TIDAS_VERSION_RE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{3})$")


def object_ref(kind: str, identity: str, version: str) -> str:
    return f"{kind}:{identity}@{version}"


def version_key(version: str) -> tuple[int, int, int] | None:
    match = TIDAS_VERSION_RE.fullmatch(version)
    if match is None:
        return None
    return tuple(int(value) for value in match.groups())


def _strings(value: object, label: str, *, nonempty: bool = False) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(value) != len(set(value))
        or value != sorted(value)
        or (nonempty and not value)
    ):
        raise ValueError(f"TIDAS graph slice {label} is invalid")
    return list(value)


def _text(value: Mapping[str, object], key: str, label: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"TIDAS graph slice {label} is invalid")
    return item


def validate_tidas_graph_slice(
    value: Mapping[str, object],
    *,
    known_tiangong_source_ids: set[str],
) -> dict:
    if (
        not isinstance(value, Mapping)
        or set(value) != SLICE_FIELDS
        or value.get("contract") != SLICE_CONTRACT
        or value.get("candidate_only") is not True
        or value.get("scientifically_validated") is not False
    ):
        raise ValueError("TIDAS graph slice contract is invalid")
    normalized = deepcopy(dict(value))
    source_lock_ids = _strings(
        normalized.get("source_lock_ids"), "source lock", nonempty=True
    )
    if not set(source_lock_ids).issubset(known_tiangong_source_ids):
        raise ValueError("TIDAS graph slice source lock is unavailable")
    objects = normalized.get("objects")
    relations = normalized.get("relations")
    if (
        not isinstance(objects, list)
        or not isinstance(relations, list)
        or (not objects and not relations)
    ):
        raise ValueError("TIDAS graph slice objects or relations are invalid")
    object_rows = []
    observed_refs: set[str] = set()
    for raw in objects:
        if not isinstance(raw, Mapping) or set(raw) != OBJECT_FIELDS:
            raise ValueError("TIDAS graph slice object is invalid")
        row = deepcopy(dict(raw))
        kind = _text(row, "object_kind", "object kind")
        identity = _text(row, "identity", "object identity")
        version = _text(row, "version", "object version")
        label = _text(row, "label", "object label")
        ref = _text(row, "object_ref", "object reference")
        if kind not in OBJECT_KINDS or ref != object_ref(kind, identity, version):
            raise ValueError("TIDAS graph slice object identity drifted")
        if ref in observed_refs:
            raise ValueError("TIDAS graph slice object identity is duplicated")
        observed_refs.add(ref)
        row["semantic_roles"] = _strings(
            row.get("semantic_roles"), "object semantic roles", nonempty=True
        )
        if not isinstance(row.get("attributes"), Mapping):
            raise ValueError("TIDAS graph slice object attributes are invalid")
        row["attributes"] = deepcopy(dict(row["attributes"]))
        row["label"] = label
        object_rows.append(row)
    object_rows.sort(key=lambda row: row["object_ref"])
    relation_rows = []
    relation_keys: set[tuple[str, str, str]] = set()
    for raw in relations:
        if not isinstance(raw, Mapping) or set(raw) != RELATION_FIELDS:
            raise ValueError("TIDAS graph slice relation is invalid")
        row = deepcopy(dict(raw))
        edge_kind = _text(row, "edge_kind", "relation kind")
        source_ref = _text(row, "source_object_ref", "relation source")
        target_ref = _text(row, "target_object_ref", "relation target")
        if edge_kind not in RELATION_KINDS or not isinstance(
            row.get("attributes"), Mapping
        ):
            raise ValueError("TIDAS graph slice relation is invalid")
        key = (edge_kind, source_ref, target_ref)
        if key in relation_keys:
            raise ValueError("TIDAS graph slice relation is duplicated")
        relation_keys.add(key)
        row["attributes"] = deepcopy(dict(row["attributes"]))
        relation_rows.append(row)
    relation_rows.sort(
        key=lambda row: (
            row["edge_kind"],
            row["source_object_ref"],
            row["target_object_ref"],
        )
    )
    normalized["source_lock_ids"] = source_lock_ids
    normalized["objects"] = object_rows
    normalized["relations"] = relation_rows
    return normalized


def collect_tidas_graph_slices(
    *,
    project_view: Mapping[str, object],
    source_lock: Mapping[str, object],
    material_evidence_units: Iterable[Mapping[str, object]],
) -> dict:
    """Collect exact slices and fail closed on locator or target drift."""

    sources = source_lock.get("sources") if isinstance(source_lock, Mapping) else None
    if not isinstance(sources, list):
        raise ValueError("TIDAS graph slice source lock is invalid")
    known_tiangong_sources = {
        str(row["source_id"])
        for row in sources
        if isinstance(row, Mapping)
        and row.get("owner") == "TianGong"
        and isinstance(row.get("source_id"), str)
    }
    locators = project_view.get("evidence_locators")
    if not isinstance(locators, Mapping):
        raise ValueError("TIDAS graph slice Project Record locators are absent")
    slices = []
    objects: dict[str, dict] = {}
    object_sources: dict[str, dict] = {}
    relations = []
    for binding in material_evidence_units:
        if not isinstance(binding, Mapping):
            raise ValueError("TIDAS graph slice evidence binding is invalid")
        unit = binding.get("evidence_unit")
        if not isinstance(unit, Mapping) or unit.get("kind") != "tidas_object_graph_slice":
            continue
        locator_id = binding.get("locator_id")
        locator = locators.get(locator_id) if isinstance(locator_id, str) else None
        content = unit.get("content")
        if (
            not isinstance(locator, Mapping)
            or locator.get("scientific_status") != "accepted_evidence"
            or locator.get("content_hash") != binding.get("content_hash")
            or unit.get("source_span") != binding.get("source_span")
            or not isinstance(content, Mapping)
        ):
            raise ValueError("TIDAS graph slice Project Record locator drifted")
        normalized = validate_tidas_graph_slice(
            content,
            known_tiangong_source_ids=known_tiangong_sources,
        )
        source = {
            "locator_id": locator_id,
            "revision_id": locator.get("revision_id"),
            "content_sha256": locator.get("content_hash"),
            "source_lock_ids": deepcopy(normalized["source_lock_ids"]),
            "member_locator": deepcopy(locator.get("locator")),
        }
        for row in normalized["objects"]:
            ref = row["object_ref"]
            if ref in objects and objects[ref] != row:
                raise ValueError("TIDAS graph slice object definition drifted")
            if ref in object_sources and object_sources[ref] != source:
                raise ValueError("TIDAS graph slice object source is ambiguous")
            objects[ref] = deepcopy(row)
            object_sources[ref] = deepcopy(source)
        for row in normalized["relations"]:
            relations.append({**deepcopy(row), "source": deepcopy(source)})
        slices.append(
            {
                "slice_sha256": canonical_sha256(normalized),
                "source": source,
                "object_refs": [row["object_ref"] for row in normalized["objects"]],
            }
        )
    known_refs = set(objects)
    for relation in relations:
        if relation["source_object_ref"] not in known_refs or relation[
            "target_object_ref"
        ] not in known_refs:
            raise ValueError("TIDAS graph slice relation target is unavailable")
    relations.sort(
        key=lambda row: (
            row["edge_kind"],
            row["source_object_ref"],
            row["target_object_ref"],
            str(row["source"]["locator_id"]),
        )
    )
    return {
        "objects": [objects[key] for key in sorted(objects)],
        "object_sources": {key: object_sources[key] for key in sorted(object_sources)},
        "relations": relations,
        "slices": sorted(slices, key=lambda row: row["slice_sha256"]),
    }
