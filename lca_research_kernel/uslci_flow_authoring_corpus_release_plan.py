"""Compile an immutable three-lane Flow corpus release plan without execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lca_research_kernel.uslci_flow_authoring_corpus_closure import (  # noqa: E402
    CONTRACT as CLOSURE_CONTRACT,
    EXPECTED_CLASSIFICATION_COUNTS,
    EXPECTED_NAMESPACE_COUNTS,
    EXPECTED_PROPERTY_COUNTS,
    EXPECTED_RISK_SEVERITY_COUNTS,
)


JsonObject = Dict[str, object]
CONTRACT = "lca_research_kernel.uslci_flow_authoring_corpus_release_plan/v1"
MANIFEST_CONTRACT = (
    "lca_research_kernel.uslci_flow_authoring_corpus_release_plan_manifest/v1"
)
LOGIC_VERSION = "2026-07-18.1"
POLICY_VERSION = "2026-07-18.1"
NEXT_ROUTE = "execute-uslci-flow-authoring-corpus-release-canary"
EXPECTED_COUNT = 1456
EXPECTED_RISK_COUNT = 196
EXPECTED_LANES = {
    "held_classification_namespace_conflict": 1,
    "planned_clean": 1260,
    "planned_with_retained_review": 195,
}
ZERO_SIDE_EFFECTS = (
    "network_read_executed_count",
    "remote_candidate_search_executed_count",
    "flow_creation_executed_count",
    "remote_flow_materialization_executed_count",
    "save_draft_executed_count",
    "process_creation_executed_count",
    "model_connection_executed_count",
    "model_mutation_executed_count",
    "external_write_executed_count",
    "publication_executed_count",
    "comparison_executed_count",
    "lcia_executed_count",
)


def _json_bytes(value: object, *, pretty: bool = False) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _jsonl_bytes(rows: Iterable[Mapping[str, object]]) -> bytes:
    return b"".join(_json_bytes(dict(row)) for row in rows)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return dict(value)


def _array(value: object, label: str) -> List[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return list(value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value.strip()


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _digest(value: object, label: str) -> str:
    token = _text(value, label)
    if len(token) != 64 or any(char not in "0123456789abcdef" for char in token):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return token


def _parse_json(content: bytes, label: str) -> JsonObject:
    try:
        return _object(json.loads(content), label)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error


def _parse_jsonl(content: bytes, label: str) -> List[JsonObject]:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is not UTF-8") from error
    rows = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            rows.append(_object(json.loads(line), f"{label} row {index}"))
        except json.JSONDecodeError as error:
            raise ValueError(f"{label} row {index} is invalid JSON") from error
    if not rows:
        raise ValueError(f"{label} is empty")
    return rows


def _read_binding(
    root: Path, value: object, label: str, *, jsonl: bool = False
) -> Tuple[object, JsonObject]:
    binding = _object(value, f"{label} binding")
    relative = _text(binding.get("path"), f"{label} path")
    digest = _digest(binding.get("sha256"), f"{label} SHA-256")
    size = _integer(binding.get("bytes"), f"{label} bytes")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"{label} escaped artifact root") from error
    if not candidate.is_file():
        raise ValueError(f"{label} is missing")
    content = candidate.read_bytes()
    if len(content) != size or _sha256(content) != digest:
        raise ValueError(f"{label} hash or byte count drifted")
    parsed = _parse_jsonl(content, label) if jsonl else _parse_json(content, label)
    return parsed, {"path": relative, "sha256": digest, "bytes": size}


def _artifact_binding(path: str, content: bytes) -> JsonObject:
    digest = _sha256(content)
    return {
        "path": path,
        "media_type": mimetypes.guess_type(path)[0] or "application/octet-stream",
        "sha256": digest,
        "digest": f"sha256:{digest}",
        "bytes": len(content),
        "size": len(content),
    }


def input_version_for_manifest_binding(binding_value: Mapping[str, object]) -> str:
    binding = _object(binding_value, "release-plan manifest binding")
    normalized = {
        "path": _text(binding.get("path"), "release-plan manifest path"),
        "sha256": _digest(binding.get("sha256"), "release-plan manifest SHA-256"),
        "bytes": _integer(binding.get("bytes"), "release-plan manifest bytes"),
    }
    return _sha256(
        _json_bytes(
            {
                "contract": CONTRACT,
                "logic_version": LOGIC_VERSION,
                "policy_version": POLICY_VERSION,
                "manifest": normalized,
            }
        )
    )[:16]


def _by_position(rows: Sequence[Mapping[str, object]], label: str) -> Dict[int, JsonObject]:
    result = {}
    for value in rows:
        row = _object(value, label)
        position = _integer(row.get("corpus_position"), f"{label} position")
        if position in result:
            raise ValueError(f"{label} position is duplicated")
        result[position] = row
    if sorted(result) != list(range(1, EXPECTED_COUNT + 1)):
        raise ValueError(f"{label} positions are not contiguous 1..{EXPECTED_COUNT}")
    return result


def compile_manifest(
    manifest_value: Mapping[str, object], *, artifact_root: Path
) -> JsonObject:
    """Verify exact closure/plan authorities and compile immutable release records."""

    manifest = _object(manifest_value, "release-plan manifest")
    if manifest.get("contract") != MANIFEST_CONTRACT or manifest.get("schema_version") != 1:
        raise ValueError("release-plan manifest contract drifted")
    scope_id = _text(manifest.get("scope_id"), "release-plan scope ID")
    revision = _text(manifest.get("revision"), "release-plan revision")
    closure_report, closure_report_ref = _read_binding(
        artifact_root, manifest.get("closure_report"), "closure report"
    )
    identities, identity_ref = _read_binding(
        artifact_root,
        manifest.get("closure_identity_ledger"),
        "closure identity ledger",
        jsonl=True,
    )
    risks, risk_ref = _read_binding(
        artifact_root,
        manifest.get("closure_risk_ledger"),
        "closure risk ledger",
        jsonl=True,
    )
    partition, partition_ref = _read_binding(
        artifact_root,
        manifest.get("closure_release_partition"),
        "closure release partition",
        jsonl=True,
    )
    distributions, distribution_ref = _read_binding(
        artifact_root,
        manifest.get("closure_distributions"),
        "closure distributions",
    )
    declared = _object(closure_report.get("artifact_bindings"), "closure artifact bindings")
    for name, supplied in (
        ("identity_ledger", identity_ref),
        ("risk_ledger", risk_ref),
        ("release_partition", partition_ref),
        ("distributions", distribution_ref),
    ):
        expected = _object(declared.get(name), f"closure {name} binding")
        if expected.get("sha256") != supplied["sha256"] or expected.get("bytes") != supplied["bytes"]:
            raise ValueError(f"closure {name} authority drifted")
    if (
        closure_report.get("contract") != CLOSURE_CONTRACT
        or closure_report.get("status") != "uslci_flow_authoring_corpus_closure_analyzed"
        or closure_report.get("corpus_intent_count") != EXPECTED_COUNT
        or closure_report.get("native_evidence_complete_count") != EXPECTED_COUNT
        or closure_report.get("unique_intent_count") != EXPECTED_COUNT
        or closure_report.get("unique_source_flow_count") != EXPECTED_COUNT
        or closure_report.get("unique_target_flow_count") != EXPECTED_COUNT
        or closure_report.get("identity_collision_count") != 0
        or closure_report.get("classification_code_counts") != EXPECTED_CLASSIFICATION_COUNTS
        or closure_report.get("classification_namespace_counts") != EXPECTED_NAMESPACE_COUNTS
        or closure_report.get("flow_property_counts") != EXPECTED_PROPERTY_COUNTS
        or closure_report.get("risk_marker_count") != EXPECTED_RISK_COUNT
        or closure_report.get("risk_severity_counts") != EXPECTED_RISK_SEVERITY_COUNTS
        or closure_report.get("release_lane_counts") != EXPECTED_LANES
        or closure_report.get("release_plan_compilation_authorized") is not True
        or closure_report.get("flow_entity_release_authorized") is not False
        or len(identities) != EXPECTED_COUNT
        or len(risks) != EXPECTED_RISK_COUNT
        or len(partition) != EXPECTED_COUNT
        or distributions.get("classification_code_counts") != EXPECTED_CLASSIFICATION_COUNTS
        or distributions.get("release_lane_counts") != EXPECTED_LANES
    ):
        raise ValueError("accepted Flow corpus closure authority drifted")
    identity_by = _by_position(identities, "closure identity")
    partition_by = _by_position(partition, "closure release partition")
    risk_by_id = {}
    for value in risks:
        risk = _object(value, "closure risk")
        risk_id = _text(risk.get("risk_id"), "closure risk ID")
        if risk_id in risk_by_id:
            raise ValueError("closure risk ID is duplicated")
        risk_by_id[risk_id] = risk
    authorities = [
        _object(value, "plan authority")
        for value in _array(manifest.get("plan_authorities"), "plan authorities")
    ]
    if len(authorities) != EXPECTED_COUNT:
        raise ValueError("release plan requires 1,456 plan authorities")
    authority_by = _by_position(authorities, "plan authority")
    release_plans = []
    total_plan_bytes = 0
    for position in range(1, EXPECTED_COUNT + 1):
        identity = identity_by[position]
        release = partition_by[position]
        authority = authority_by[position]
        identity_keys = (
            "authoring_intent_key",
            "source_flow_id",
            "target_flow_id",
        )
        if any(
            identity.get(key) != release.get(key)
            and key != "source_flow_id"
            for key in identity_keys
        ) or any(identity.get(key) != authority.get(key) for key in identity_keys):
            raise ValueError(f"plan authority identity drifted at position {position}")
        if (
            release.get("release_lane") != identity.get("release_lane")
            or release.get("risk_id") != identity.get("risk_id")
            or release.get("risk_severity") != identity.get("risk_severity")
            or release.get("flow_entity_release_authorized") is not False
        ):
            raise ValueError(f"release partition drifted at position {position}")
        plan_binding = _object(authority.get("saved_plan"), "saved plan binding")
        plan_path = _text(plan_binding.get("path"), "saved plan path")
        plan_sha = _digest(plan_binding.get("sha256"), "saved plan SHA-256")
        plan_bytes = _integer(plan_binding.get("bytes"), "saved plan bytes")
        candidate = (artifact_root / plan_path).resolve()
        try:
            candidate.relative_to(artifact_root.resolve())
        except ValueError as error:
            raise ValueError("saved plan escaped artifact root") from error
        if not candidate.is_file():
            raise ValueError("saved plan is missing")
        content = candidate.read_bytes()
        if len(content) != plan_bytes or _sha256(content) != plan_sha:
            raise ValueError("saved plan hash or byte count drifted")
        total_plan_bytes += plan_bytes
        for key in (
            "saved_plan_report_sha256",
            "native_gate_report_sha256",
            "native_terminal_ledger_sha256",
            "native_terminal_row_sha256",
        ):
            _digest(authority.get(key), f"plan authority {key}")
        risk_id = identity.get("risk_id")
        if risk_id is not None:
            risk = risk_by_id.get(risk_id)
            if (
                not isinstance(risk, dict)
                or risk.get("authoring_intent_key") != identity.get("authoring_intent_key")
                or risk.get("severity") != identity.get("risk_severity")
            ):
                raise ValueError(f"release risk join drifted at position {position}")
        operation = (
            "hold_no_entity_action"
            if identity.get("release_lane")
            == "held_classification_namespace_conflict"
            else "local_materialize_validate_then_save_draft_candidate"
        )
        body = {
            "schema_version": 1,
            "contract": "lca_research_kernel.uslci_flow_authoring_release_record/v1",
            "corpus_position": position,
            "slice_id": identity.get("slice_id"),
            "slice_position": identity.get("slice_position"),
            "authoring_intent_key": identity.get("authoring_intent_key"),
            "source_flow_id": identity.get("source_flow_id"),
            "target_flow_id": identity.get("target_flow_id"),
            "classification_code": identity.get("classification_code"),
            "classification_type": identity.get("classification_type"),
            "flow_property": identity.get("flow_property"),
            "release_lane": identity.get("release_lane"),
            "risk_id": risk_id,
            "risk_severity": identity.get("risk_severity"),
            "planned_operation": operation,
            "saved_plan": {"path": plan_path, "sha256": plan_sha, "bytes": plan_bytes},
            "saved_plan_report_sha256": authority.get("saved_plan_report_sha256"),
            "native_gate_report_sha256": authority.get("native_gate_report_sha256"),
            "native_terminal_ledger_sha256": authority.get("native_terminal_ledger_sha256"),
            "native_terminal_row_sha256": authority.get("native_terminal_row_sha256"),
            "closure_report_sha256": closure_report_ref["sha256"],
            "execution_authorized": False,
            "flow_entity_release_authorized": False,
            "save_draft_authorized": False,
        }
        release_plans.append({**body, "plan_sha256": _sha256(_json_bytes(body))})
    if len({row["plan_sha256"] for row in release_plans}) != EXPECTED_COUNT:
        raise ValueError("release plan hashes are not unique")
    lane_counts = dict(sorted(Counter(str(row["release_lane"]) for row in release_plans).items()))
    if lane_counts != EXPECTED_LANES:
        raise ValueError("release plan lane distribution drifted")
    held = [row for row in release_plans if row["release_lane"] == "held_classification_namespace_conflict"]
    release_canary = []
    for property_label in ("Mass|kg", "Energy|MJ", "Area*Time|m2*a"):
        candidates = [
            row
            for row in release_plans
            if row["flow_property"] == property_label
            and row["release_lane"] != "held_classification_namespace_conflict"
        ]
        if not candidates:
            raise ValueError(f"no release canary candidate for {property_label}")
        selected = candidates[0]
        release_canary.append(
            {
                "selection_order": len(release_canary) + 1,
                "corpus_position": selected["corpus_position"],
                "authoring_intent_key": selected["authoring_intent_key"],
                "target_flow_id": selected["target_flow_id"],
                "flow_property": property_label,
                "release_lane": selected["release_lane"],
                "release_plan_sha256": selected["plan_sha256"],
                "execution_authorized": False,
            }
        )
    plan_hashes = [row["plan_sha256"] for row in release_plans]
    plan_set_version = _sha256(
        _json_bytes(
            {
                "contract": CONTRACT,
                "logic_version": LOGIC_VERSION,
                "closure_report_sha256": closure_report_ref["sha256"],
                "ordered_plan_sha256": plan_hashes,
            }
        )
    )[:16]
    dag = {
        "schema_version": 1,
        "node_count": EXPECTED_COUNT + 1,
        "edge_count": EXPECTED_COUNT,
        "nodes": [
            {
                "node_id": f"release-plan-{row['corpus_position']:04d}",
                "node_type": "immutable_release_plan",
                "status": "held" if row["release_lane"] == "held_classification_namespace_conflict" else "completed",
                "plan_sha256": row["plan_sha256"],
            }
            for row in release_plans
        ]
        + [{"node_id": "release-canary-gate", "node_type": "execution_gate", "status": "pending"}],
        "edges": [
            {"from": f"release-plan-{row['corpus_position']:04d}", "to": "release-canary-gate"}
            for row in release_plans
        ],
    }
    index = {
        "schema_version": 1,
        "contract": "lca_research_kernel.uslci_flow_authoring_corpus_release_plan_index/v1",
        "release_plan_set_version": plan_set_version,
        "release_plan_count": EXPECTED_COUNT,
        "ordered_plan_sha256": plan_hashes,
        "release_lane_counts": lane_counts,
        "risk_marker_count": EXPECTED_RISK_COUNT,
        "source_saved_plan_total_bytes": total_plan_bytes,
        "execution_requires_exact_plan_record": True,
    }
    report = {
        "schema_version": 1,
        "contract": CONTRACT,
        "logic_version": LOGIC_VERSION,
        "policy_version": POLICY_VERSION,
        "status": "uslci_flow_authoring_corpus_release_plan_compiled",
        "scope_id": scope_id,
        "revision": revision,
        "source_corpus_closure_version": closure_report.get("corpus_closure_version"),
        "source_corpus_closure_report_sha256": closure_report_ref["sha256"],
        "release_plan_set_version": plan_set_version,
        "release_plan_count": EXPECTED_COUNT,
        "source_saved_plan_verified_count": EXPECTED_COUNT,
        "source_saved_plan_total_bytes": total_plan_bytes,
        "risk_marker_carry_forward_count": EXPECTED_RISK_COUNT,
        "risk_severity_counts": EXPECTED_RISK_SEVERITY_COUNTS,
        "release_lane_counts": lane_counts,
        "clean_plan_count": 1260,
        "retained_review_plan_count": 195,
        "held_plan_count": 1,
        "release_canary_count": len(release_canary),
        "execution_authorized_count": 0,
        "release_plan_dag_node_count": dag["node_count"],
        "release_plan_dag_edge_count": dag["edge_count"],
        "flow_entity_release_authorized": False,
        "save_draft_authorized": False,
        **{key: 0 for key in ZERO_SIDE_EFFECTS},
        "scientific_comparison_ready": False,
        "comparative_lcia_allowed": False,
        "routing": {"next_internal_route": NEXT_ROUTE, "pause_internal_work": False},
        "next_internal_route": NEXT_ROUTE,
        "next_action": (
            "Execute only the three exact Mass/Energy/Area*Time release canaries in an isolated local workspace; "
            "retain all risks and keep the held CPC namespace conflict, bulk release, comparison and LCIA closed."
        ),
    }
    return {
        "report": report,
        "release_plans": release_plans,
        "index": index,
        "risk_ledger": risks,
        "held_conflicts": held,
        "release_canary": release_canary,
        "dag": dag,
        "unexpected_failures": [],
    }


def execute_and_materialize(
    *, manifest_content: bytes, manifest_binding_value: Mapping[str, object], artifact_root: Path, out_dir: Path
) -> JsonObject:
    binding = _object(manifest_binding_value, "release-plan manifest binding")
    if (
        _digest(binding.get("sha256"), "release-plan manifest SHA-256") != _sha256(manifest_content)
        or _integer(binding.get("bytes"), "release-plan manifest bytes") != len(manifest_content)
    ):
        raise ValueError("release-plan manifest binding hash or byte count drifted")
    result = compile_manifest(_parse_json(manifest_content, "release-plan manifest"), artifact_root=artifact_root)
    output_rows = {
        "release_plans": ("uslci-flow-authoring-corpus-release-plans.jsonl", _jsonl_bytes(result["release_plans"])),
        "plan_index": ("uslci-flow-authoring-corpus-release-plan-index.json", _json_bytes(result["index"], pretty=True)),
        "risk_ledger": ("uslci-flow-authoring-corpus-release-risks.jsonl", _jsonl_bytes(result["risk_ledger"])),
        "held_conflicts": ("uslci-flow-authoring-corpus-release-held-conflicts.jsonl", _jsonl_bytes(result["held_conflicts"])),
        "release_canary": ("uslci-flow-authoring-corpus-release-canary.json", _json_bytes(result["release_canary"], pretty=True)),
        "release_plan_dag": ("uslci-flow-authoring-corpus-release-plan-dag.json", _json_bytes(result["dag"], pretty=True)),
        "unexpected_failures": ("uslci-flow-authoring-corpus-release-plan-failures.jsonl", b""),
    }
    artifact_bindings = {
        key: _artifact_binding(filename, content)
        for key, (filename, content) in output_rows.items()
    }
    report = {
        **result["report"],
        "input_version": input_version_for_manifest_binding(binding),
        "input_binding": {
            "path": _text(binding.get("path"), "release-plan manifest path"),
            "sha256": _sha256(manifest_content),
            "bytes": len(manifest_content),
        },
        "artifact_bindings": artifact_bindings,
        "unexpected_failure_count": 0,
    }
    outputs = out_dir / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    for filename, content in output_rows.values():
        (outputs / filename).write_bytes(content)
    (outputs / "uslci-flow-authoring-corpus-release-plan.json").write_bytes(
        _json_bytes(report, pretty=True)
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compile the immutable Flow corpus release plan.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-binding", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = execute_and_materialize(
        manifest_content=args.manifest.read_bytes(),
        manifest_binding_value=_parse_json(args.manifest_binding.read_bytes(), "release-plan manifest binding"),
        artifact_root=args.artifact_root,
        out_dir=args.out_dir,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
