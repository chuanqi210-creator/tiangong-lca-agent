"""Private, bounded parser for source-bound TianGong/TIDAS object slices.

This module only normalizes candidate archive members.  It neither accepts
evidence nor persists a graph; those responsibilities remain in Project Record
and the Kernel semantic projection.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from typing import Any, Optional


SOURCE_LOCK_IDS = ["tiangong.sdk.npm", "tiangong.tidas-tools.pypi"]
LifecycleSummary = Callable[[Mapping[str, Any]], Optional[dict[str, Any]]]
Multilingual = Callable[[object], dict[str, str]]


def _rows(value: object) -> list[Mapping[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, Mapping)]
    return [value] if isinstance(value, Mapping) else []


def _version(dataset: Mapping[str, Any]) -> str | None:
    administration = dataset.get("administrativeInformation")
    publication = (
        administration.get("publicationAndOwnership")
        if isinstance(administration, Mapping)
        else None
    )
    value = (
        publication.get("common:dataSetVersion")
        if isinstance(publication, Mapping)
        else None
    )
    return value if isinstance(value, str) and value.strip() else None


def _ref(kind: str, identity: str, version: str) -> str:
    return f"{kind}:{identity}@{version}"


def _object(
    kind: str,
    identity: str,
    version: str,
    *,
    label: str,
    roles: list[str],
    attributes: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "object_ref": _ref(kind, identity, version),
        "object_kind": kind,
        "identity": identity,
        "version": version,
        "label": label,
        "semantic_roles": sorted(set(roles)),
        "attributes": dict(attributes or {}),
    }


def _relation(
    kind: str,
    source: str,
    target: str,
    *,
    attributes: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "edge_kind": kind,
        "source_object_ref": source,
        "target_object_ref": target,
        "attributes": dict(attributes or {}),
    }


def _finish(objects: list[dict], relations: list[dict]) -> dict:
    unique_objects = {row["object_ref"]: row for row in objects}
    unique_relations = {
        (row["edge_kind"], row["source_object_ref"], row["target_object_ref"]): row
        for row in relations
    }
    return {
        "contract": "lca_research_service.tidas_object_graph_slice/v1",
        "source_lock_ids": list(SOURCE_LOCK_IDS),
        "objects": [unique_objects[key] for key in sorted(unique_objects)],
        "relations": [unique_relations[key] for key in sorted(unique_relations)],
        "candidate_only": True,
        "scientifically_validated": False,
    }


class TidasObjectGraphParser:
    """Parse only governed TIDAS structures into a small normalized contract."""

    def __init__(
        self,
        *,
        lifecycle_summary: LifecycleSummary,
        multilingual: Multilingual,
    ) -> None:
        self._lifecycle_summary = lifecycle_summary
        self._multilingual = multilingual

    def parse(self, value: object) -> dict[str, Any] | None:
        if not isinstance(value, Mapping):
            return None
        for builder in (
            self._lifecycle,
            self._process,
            self._flow,
            self._flow_property,
            self._unit_group,
            self._method,
        ):
            result = builder(value)
            if result is not None:
                return result
        return None

    def _label(self, value: object, fallback: str) -> str:
        if isinstance(value, Mapping):
            for key in ("baseName", "common:name", "name"):
                translations = self._multilingual(value.get(key))
                if translations:
                    return (
                        translations.get("en")
                        or translations.get("zh")
                        or next(iter(translations.values()))
                    )
        translations = self._multilingual(value)
        return (
            translations.get("en")
            or translations.get("zh")
            or next(iter(translations.values()))
            if translations
            else fallback
        )

    @staticmethod
    def _header(
        value: Mapping[str, Any],
        *,
        dataset_key: str,
        information_key: str,
    ) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], str, str] | None:
        dataset = value.get(dataset_key)
        if not isinstance(dataset, Mapping):
            return None
        information = dataset.get(information_key)
        data = (
            information.get("dataSetInformation")
            if isinstance(information, Mapping)
            else None
        )
        identity = data.get("common:UUID") if isinstance(data, Mapping) else None
        version = _version(dataset)
        if not isinstance(identity, str) or not isinstance(version, str):
            return None
        return dataset, information, data, identity, version

    def _lifecycle(self, value: Mapping[str, Any]) -> dict[str, Any] | None:
        summary = self._lifecycle_summary(value)
        if summary is None:
            return None
        identity, version = summary["dataset_uuid"], summary["version"]
        model_ref = _ref("LifecycleModel", identity, version)
        base_names = summary.get("name", {}).get("base_name", {})
        label = (
            base_names.get("en") or base_names.get("zh") or identity
            if isinstance(base_names, Mapping)
            else identity
        )
        objects = [
            _object(
                "LifecycleModel",
                identity,
                version,
                label=str(label),
                roles=["dataset_identity", "dataset_version", "lca_native_package"],
                attributes={
                    "quantitative_reference": summary.get("quantitative_reference")
                },
            )
        ]
        relations = [
            _relation(
                "contains_process",
                model_ref,
                _ref(
                    "Process",
                    str(instance["process_uuid"]),
                    str(instance["version"]),
                ),
                attributes={
                    "internal_id": instance.get("internal_id"),
                    "multiplication_factor": instance.get("multiplication_factor"),
                },
            )
            for instance in summary.get("process_instances") or []
        ]
        intended = summary.get("intended_applications")
        if isinstance(intended, Mapping) and intended:
            scope_identity = identity + ":intended-application"
            scope_ref = _ref("ApplicabilityScope", scope_identity, version)
            objects.append(
                _object(
                    "ApplicabilityScope",
                    scope_identity,
                    version,
                    label=self._label(intended, "intended application"),
                    roles=["applicability", "representativeness"],
                    attributes={"statements": dict(intended)},
                )
            )
            relations.append(_relation("has_applicability", model_ref, scope_ref))

        json_tg = value.get("json_tg")
        xflow = json_tg.get("xflow") if isinstance(json_tg, Mapping) else None
        for edge in _rows(xflow.get("edges") if isinstance(xflow, Mapping) else None):
            self._append_xflow(
                edge,
                lifecycle_identity=identity,
                lifecycle_version=version,
                objects=objects,
                relations=relations,
            )
        return _finish(objects, relations)

    @staticmethod
    def _append_xflow(
        edge: Mapping[str, Any],
        *,
        lifecycle_identity: str,
        lifecycle_version: str,
        objects: list[dict],
        relations: list[dict],
    ) -> None:
        data = edge.get("data")
        node = data.get("node") if isinstance(data, Mapping) else None
        connection = data.get("connection") if isinstance(data, Mapping) else None
        output = (
            connection.get("outputExchange")
            if isinstance(connection, Mapping)
            else None
        )
        values = (
            node.get("sourceProcessId") if isinstance(node, Mapping) else None,
            node.get("sourceProcessVersion") if isinstance(node, Mapping) else None,
            node.get("targetProcessId") if isinstance(node, Mapping) else None,
            node.get("targetProcessVersion") if isinstance(node, Mapping) else None,
            edge.get("id"),
        )
        if not all(isinstance(item, str) and item.strip() for item in values):
            return
        source_id, source_version, target_id, target_version, edge_id = values
        exchange_identity = (
            f"{lifecycle_identity}:{lifecycle_version}:xflow:{edge_id}"
        )
        provider_identity = (
            f"{lifecycle_identity}:{lifecycle_version}:provider:{source_id}"
        )
        exchange_ref = _ref("Exchange", exchange_identity, lifecycle_version)
        provider_ref = _ref("Provider", provider_identity, source_version)
        process_ref = _ref("Process", target_id, target_version)
        provider_process_ref = _ref("Process", source_id, source_version)
        objects.extend(
            [
                _object(
                    "Exchange",
                    exchange_identity,
                    lifecycle_version,
                    label=edge_id,
                    roles=["provider_relation", "technosphere_exchange"],
                    attributes={
                        "xflow_edge_id": edge_id,
                        "flow_uuid": (
                            output.get("@flowUUID")
                            if isinstance(output, Mapping)
                            else None
                        ),
                    },
                ),
                _object(
                    "Provider",
                    provider_identity,
                    source_version,
                    label=source_id,
                    roles=["dataset_identity", "dataset_version", "provider_relation"],
                    attributes={"provider_process_ref": provider_process_ref},
                ),
            ]
        )
        relations.extend(
            [
                _relation("has_exchange", process_ref, exchange_ref),
                _relation("provided_by", exchange_ref, provider_ref),
                _relation("derived_from", provider_ref, provider_process_ref),
            ]
        )

    def _process(self, value: Mapping[str, Any]) -> dict[str, Any] | None:
        header = self._header(
            value,
            dataset_key="processDataSet",
            information_key="processInformation",
        )
        if header is None:
            return None
        dataset, _, data, identity, version = header
        process_ref = _ref("Process", identity, version)
        objects = [
            _object(
                "Process",
                identity,
                version,
                label=self._label(data.get("name"), identity),
                roles=["dataset_identity", "dataset_version", "process"],
            )
        ]
        relations = []
        exchanges = dataset.get("exchanges")
        for exchange in _rows(
            exchanges.get("exchange") if isinstance(exchanges, Mapping) else None
        ):
            internal_id = exchange.get("@dataSetInternalID")
            reference = exchange.get("referenceToFlowDataSet")
            flow_id = reference.get("@refObjectId") if isinstance(reference, Mapping) else None
            flow_version = reference.get("@version") if isinstance(reference, Mapping) else None
            if internal_id is None or not all(
                isinstance(item, str) and item.strip()
                for item in (flow_id, flow_version)
            ):
                continue
            exchange_identity = f"{identity}:exchange:{internal_id}"
            exchange_ref = _ref("Exchange", exchange_identity, version)
            objects.append(
                _object(
                    "Exchange",
                    exchange_identity,
                    version,
                    label=f"exchange {internal_id}",
                    roles=["technosphere_exchange", "flow_identity"],
                    attributes={
                        "internal_id": str(internal_id),
                        "direction": exchange.get("exchangeDirection"),
                        "mean_amount": exchange.get("meanAmount"),
                        "resulting_amount": exchange.get("resultingAmount"),
                        "quantitative_reference": exchange.get("quantitativeReference"),
                    },
                )
            )
            relations.extend(
                [
                    _relation("has_exchange", process_ref, exchange_ref),
                    _relation(
                        "references_flow",
                        exchange_ref,
                        _ref("Flow", flow_id, flow_version),
                    ),
                ]
            )
        return _finish(objects, relations)

    def _flow(self, value: Mapping[str, Any]) -> dict[str, Any] | None:
        header = self._header(
            value, dataset_key="flowDataSet", information_key="flowInformation"
        )
        if header is None:
            return None
        dataset, _, data, identity, version = header
        flow_ref = _ref("Flow", identity, version)
        objects = [
            _object(
                "Flow",
                identity,
                version,
                label=self._label(data.get("name"), identity),
                roles=["dataset_identity", "dataset_version", "flow_identity"],
            )
        ]
        relations = []
        properties = dataset.get("flowProperties")
        for row in _rows(
            properties.get("flowProperty")
            if isinstance(properties, Mapping)
            else None
        ):
            reference = row.get("referenceToFlowPropertyDataSet")
            property_id = reference.get("@refObjectId") if isinstance(reference, Mapping) else None
            property_version = reference.get("@version") if isinstance(reference, Mapping) else None
            if isinstance(property_id, str) and isinstance(property_version, str):
                relations.append(
                    _relation(
                        "has_property",
                        flow_ref,
                        _ref("FlowProperty", property_id, property_version),
                        attributes={
                            "internal_id": row.get("@dataSetInternalID"),
                            "mean_value": row.get("meanValue"),
                        },
                    )
                )
        return _finish(objects, relations)

    def _flow_property(self, value: Mapping[str, Any]) -> dict[str, Any] | None:
        header = self._header(
            value,
            dataset_key="flowPropertyDataSet",
            information_key="flowPropertiesInformation",
        )
        if header is None:
            return None
        _, information, data, identity, version = header
        quantitative = information.get("quantitativeReference")
        reference = (
            quantitative.get("referenceToReferenceUnitGroup")
            if isinstance(quantitative, Mapping)
            else None
        )
        group_id = reference.get("@refObjectId") if isinstance(reference, Mapping) else None
        group_version = reference.get("@version") if isinstance(reference, Mapping) else None
        if not all(
            isinstance(item, str) and item.strip()
            for item in (group_id, group_version)
        ):
            return None
        return _finish(
            [
                _object(
                    "FlowProperty",
                    identity,
                    version,
                    label=self._label(data.get("common:name"), identity),
                    roles=[
                        "dataset_identity",
                        "dataset_version",
                        "flow_property",
                        "unit_equivalence",
                    ],
                )
            ],
            [
                _relation(
                    "uses_unit_group",
                    _ref("FlowProperty", identity, version),
                    _ref("UnitGroup", group_id, group_version),
                )
            ],
        )

    def _unit_group(self, value: Mapping[str, Any]) -> dict[str, Any] | None:
        header = self._header(
            value,
            dataset_key="unitGroupDataSet",
            information_key="unitGroupInformation",
        )
        if header is None:
            return None
        dataset, _, data, identity, version = header
        group_ref = _ref("UnitGroup", identity, version)
        objects = [
            _object(
                "UnitGroup",
                identity,
                version,
                label=self._label(data.get("common:name"), identity),
                roles=["dataset_identity", "dataset_version", "unit_equivalence"],
            )
        ]
        relations = []
        units = dataset.get("units")
        for row in _rows(units.get("unit") if isinstance(units, Mapping) else None):
            internal_id, name = row.get("@dataSetInternalID"), row.get("name")
            if internal_id is None or not isinstance(name, str) or not name.strip():
                continue
            unit_identity = f"{identity}:{internal_id}"
            unit_ref = _ref("Unit", unit_identity, version)
            objects.append(
                _object(
                    "Unit",
                    unit_identity,
                    version,
                    label=name,
                    roles=["unit", "unit_equivalence"],
                    attributes={
                        "internal_id": str(internal_id),
                        "mean_value": row.get("meanValue"),
                        "name": name,
                    },
                )
            )
            relations.append(_relation("contains_unit", group_ref, unit_ref))
        return _finish(objects, relations)

    def _method(self, value: Mapping[str, Any]) -> dict[str, Any] | None:
        header = self._header(
            value,
            dataset_key="LCIAMethodDataSet",
            information_key="LCIAMethodInformation",
        )
        if header is None:
            return None
        _, information, data, identity, version = header
        method_ref = _ref("LCIAMethod", identity, version)
        objects = [
            _object(
                "LCIAMethod",
                identity,
                version,
                label=self._label(data.get("common:name"), identity),
                roles=["dataset_identity", "dataset_version", "lcia_method"],
            )
        ]
        relations = []
        category, indicator = data.get("impactCategory"), data.get("impactIndicator")
        if isinstance(category, str) and category.strip():
            category_identity = identity + ":impact-category:" + hashlib.sha256(
                category.encode("utf-8")
            ).hexdigest()[:16]
            category_ref = _ref("ImpactCategory", category_identity, version)
            objects.append(
                _object(
                    "ImpactCategory",
                    category_identity,
                    version,
                    label=category,
                    roles=["characterization_factor", "impact_category"],
                    attributes={"impact_indicator": indicator},
                )
            )
            relations.append(
                _relation("contains_impact_category", method_ref, category_ref)
            )
        time = information.get("time")
        if isinstance(time, Mapping) and time:
            scope_identity = identity + ":applicability"
            scope_ref = _ref("ApplicabilityScope", scope_identity, version)
            objects.append(
                _object(
                    "ApplicabilityScope",
                    scope_identity,
                    version,
                    label="method applicability",
                    roles=["applicability"],
                    attributes={"time": dict(time), "impact_indicator": indicator},
                )
            )
            relations.append(_relation("has_applicability", method_ref, scope_ref))
        return _finish(objects, relations)


def parse_tidas_object_graph_slice(
    value: object,
    *,
    lifecycle_summary: LifecycleSummary,
    multilingual: Multilingual,
) -> dict[str, Any] | None:
    return TidasObjectGraphParser(
        lifecycle_summary=lifecycle_summary,
        multilingual=multilingual,
    ).parse(value)
