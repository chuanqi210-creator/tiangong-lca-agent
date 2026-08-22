#!/usr/bin/env python3
"""Build a small auditable LCA candidate package from a research case JSON.

This is a competition MVP helper. It creates deterministic candidate entities
and review artifacts; it does not claim strict TianGong/TIDAS publication
readiness unless a downstream schema validator is run separately.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import uuid
from pathlib import Path
from typing import Any


ENTITY_VERSION = "00.00.001"
NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://tiangong.earth/evidence-to-tidas-lca")
PROJECT_REQUIRED = (
    "title",
    "research_object",
    "functional_unit",
    "system_boundary",
    "geography",
    "time_period",
)


def normalized(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def stable_id(kind: str, *parts: Any) -> str:
    payload = "|".join([kind, *(normalized(part) for part in parts)])
    return str(uuid.uuid5(NAMESPACE, payload))


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def ensure_list(value: Any, label: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value


def load_case(path: Path) -> dict[str, Any]:
    return require_mapping(json.loads(path.read_text(encoding="utf-8")), "case")


def validate_case(case: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    project = require_mapping(case.get("project", {}), "project")
    for field in PROJECT_REQUIRED:
        if not project.get(field):
            issues.append(f"project.{field} is required")

    source_keys = {source.get("key") for source in ensure_list(case.get("sources"), "sources")}
    source_keys.discard(None)

    for collection in ("flows", "processes"):
        for item in ensure_list(case.get(collection), collection):
            evidence = require_mapping(item, collection[:-1]).get("evidence")
            if evidence and evidence not in source_keys:
                issues.append(f"{collection[:-1]} references missing evidence source: {evidence}")

    flow_names = {flow.get("name") for flow in ensure_list(case.get("flows"), "flows")}
    for process in ensure_list(case.get("processes"), "processes"):
        process_map = require_mapping(process, "process")
        for direction in ("inputs", "outputs"):
            for flow_name in ensure_list(process_map.get(direction), f"process.{direction}"):
                if flow_name not in flow_names:
                    issues.append(
                        f"process '{process_map.get('name', '<unnamed>')}' references missing flow: {flow_name}"
                    )
    return issues


def build_sources(case: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for raw in ensure_list(case.get("sources"), "sources"):
        source = require_mapping(raw, "source")
        key = str(source["key"])
        source_id = stable_id("source", key, source.get("title", ""))
        sources.append(
            {
                "entity_type": "source",
                "id": source_id,
                "version": ENTITY_VERSION,
                "key": key,
                "title": source.get("title", ""),
                "citation": source.get("citation", ""),
                "url": source.get("url", ""),
                "json_ordered": {
                    "sourceDataSet": {
                        "id": source_id,
                        "version": ENTITY_VERSION,
                        "shortName": source.get("title", ""),
                        "citation": source.get("citation", ""),
                        "url": source.get("url", ""),
                    }
                },
            }
        )
    return sources


def build_flows(case: dict[str, Any]) -> list[dict[str, Any]]:
    flows: list[dict[str, Any]] = []
    for raw in ensure_list(case.get("flows"), "flows"):
        flow = require_mapping(raw, "flow")
        flow_id = stable_id("flow", flow.get("name", ""), flow.get("unit", ""), flow.get("category", ""))
        flows.append(
            {
                "entity_type": "flow",
                "id": flow_id,
                "version": ENTITY_VERSION,
                "name": flow.get("name", ""),
                "direction": flow.get("direction", ""),
                "quantity": flow.get("quantity"),
                "unit": flow.get("unit", ""),
                "category": flow.get("category", ""),
                "evidence": flow.get("evidence", ""),
                "note": flow.get("note", ""),
                "json_ordered": {
                    "flowDataSet": {
                        "id": flow_id,
                        "version": ENTITY_VERSION,
                        "name": flow.get("name", ""),
                        "classification": flow.get("category", ""),
                        "referenceFlowProperty": flow.get("unit", ""),
                    }
                },
            }
        )
    return flows


def build_processes(case: dict[str, Any], flows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flow_by_name = {flow["name"]: flow for flow in flows}
    processes: list[dict[str, Any]] = []
    for raw in ensure_list(case.get("processes"), "processes"):
        process = require_mapping(raw, "process")
        process_id = stable_id("process", process.get("name", ""), process.get("description", ""))
        exchanges: list[dict[str, Any]] = []

        for direction_key, exchange_direction in (("inputs", "input"), ("outputs", "output")):
            for flow_name in ensure_list(process.get(direction_key), f"process.{direction_key}"):
                flow = flow_by_name.get(flow_name)
                exchanges.append(
                    {
                        "direction": exchange_direction,
                        "flow_id": flow["id"] if flow else "",
                        "flow_name": flow_name,
                        "amount": flow.get("quantity") if flow else None,
                        "unit": flow.get("unit", "") if flow else "",
                        "quantitative_reference": exchange_direction == "output",
                    }
                )

        processes.append(
            {
                "entity_type": "process",
                "id": process_id,
                "version": ENTITY_VERSION,
                "name": process.get("name", ""),
                "description": process.get("description", ""),
                "evidence": process.get("evidence", ""),
                "exchanges": exchanges,
                "json_ordered": {
                    "processDataSet": {
                        "id": process_id,
                        "version": ENTITY_VERSION,
                        "name": process.get("name", ""),
                        "description": process.get("description", ""),
                        "exchanges": exchanges,
                    }
                },
            }
        )
    return processes


def build_lci_rows(project: dict[str, Any], flows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "project": project["title"],
            "functional_unit": project["functional_unit"],
            "flow_name": flow["name"],
            "direction": flow["direction"],
            "quantity": flow["quantity"],
            "unit": flow["unit"],
            "category": flow["category"],
            "evidence": flow["evidence"],
        }
        for flow in flows
    ]


def build_package(case: dict[str, Any]) -> dict[str, Any]:
    project = require_mapping(case.get("project", {}), "project")
    sources = build_sources(case)
    flows = build_flows(case)
    processes = build_processes(case, flows)
    issues = validate_case(case)
    status = "passed" if not issues else "blocked"
    assumptions = [str(item) for item in ensure_list(case.get("assumptions"), "assumptions")]

    return {
        "project": project,
        "sources": sources,
        "flows": flows,
        "processes": processes,
        "assumptions": assumptions,
        "lci_rows": build_lci_rows(project, flows),
        "validation": {
            "status": status,
            "issues": issues,
            "note": "Local structural validation only; run TianGong/TIDAS schema tools before publication.",
        },
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_lci_table(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "project",
        "functional_unit",
        "flow_name",
        "direction",
        "quantity",
        "unit",
        "category",
        "evidence",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def render_assumptions(package: dict[str, Any]) -> str:
    lines = [
        f"# Assumptions - {package['project']['title']}",
        "",
        "## Modeling Assumptions",
    ]
    assumptions = package["assumptions"] or ["No assumptions were provided; this should be reviewed."]
    lines.extend(f"- {item}" for item in assumptions)
    lines.extend(["", "## Validation Issues"])
    issues = package["validation"]["issues"] or ["No local structural blockers detected."]
    lines.extend(f"- {item}" for item in issues)
    return "\n".join(lines) + "\n"


def render_report(package: dict[str, Any]) -> str:
    project = package["project"]
    lines = [
        f"# {project['title']}",
        "",
        "## Goal And Scope",
        f"- Research object: {project['research_object']}",
        f"- Functional unit: {project['functional_unit']}",
        f"- System boundary: {project['system_boundary']}",
        f"- Geography: {project['geography']}",
        f"- Time period: {project['time_period']}",
        "",
        "## Candidate Entity Counts",
        f"- Sources: {len(package['sources'])}",
        f"- Flows: {len(package['flows'])}",
        f"- Processes: {len(package['processes'])}",
        "",
        "## Validation",
        f"- Status: {package['validation']['status']}",
        f"- Note: {package['validation']['note']}",
        "",
        "## Next TianGong Integration Steps",
        "- Match candidate flows and processes with TianGong LCA MCP search.",
        "- Run strict TIDAS/TIDAS Tools schema validation.",
        "- Review assumptions and missing data before any platform publication.",
    ]
    return "\n".join(lines) + "\n"


def write_package(package: dict[str, Any], out_dir: Path) -> None:
    write_json(out_dir / "package-manifest.json", package)
    write_json(out_dir / "validation-report.json", package["validation"])

    for source in package["sources"]:
        write_json(out_dir / "tidas-candidate" / "sources" / f"{source['id']}.json", source)
    for flow in package["flows"]:
        write_json(out_dir / "tidas-candidate" / "flows" / f"{flow['id']}.json", flow)
    for process in package["processes"]:
        write_json(out_dir / "tidas-candidate" / "processes" / f"{process['id']}.json", process)

    write_lci_table(package["lci_rows"], out_dir / "lci-table.csv")
    (out_dir / "assumptions.md").write_text(render_assumptions(package), encoding="utf-8")
    (out_dir / "report.md").write_text(render_report(package), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Path to input research case JSON.")
    parser.add_argument("--out-dir", required=True, type=Path, help="Directory for generated artifacts.")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable summary.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    package = build_package(load_case(args.input))
    write_package(package, args.out_dir)
    summary = {
        "status": package["validation"]["status"],
        "out_dir": str(args.out_dir),
        "flows": len(package["flows"]),
        "processes": len(package["processes"]),
        "sources": len(package["sources"]),
        "issues": package["validation"]["issues"],
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"Wrote LCA candidate package to {args.out_dir} ({summary['status']})")
    return 0 if package["validation"]["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
