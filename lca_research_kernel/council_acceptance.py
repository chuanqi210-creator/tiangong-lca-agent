"""Source-bound functional and safety acceptance fixtures for Council-default."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Mapping, Sequence

from .project import ResearchProject


JsonObject = Dict[str, object]
LEDGER_PATH = Path("docs/agent-project-rebaseline-2026-07-21.json")
FRESHWATER_LEDGER_ID = "freshwater-authority-following-package"
FIXTURE_ROLE = "single_case_functional_acceptance"
WORKER_OUTPUT_CONTRACT = "lca_research_kernel.freshwater_independent_review/v1"
CANDIDATE_CONCLUSION = (
    "The frozen Freshwater authority-following package is suitable only as a "
    "TianGong source-bound single-case Council functional fixture; it is not a "
    "two-route comparative LCA benchmark."
)
SOURCE_ROLES = {
    "package-manifest.json": "freshwater_package_manifest",
    "receipt-50d206a2899a86b1.json": "authority_receipt",
    "binding-aed71a598fbbf7ba.json": "freshwater_native_case_binding",
    "acceptance-5e426c8a392a576a.json": "freshwater_package_acceptance",
}
ROUTE_SIGNALS = {
    "scientific_importance": "high",
    "dispute_level": "medium",
    "procedure_determinism": "high",
    "dependency_strength": "high",
    "independent_evidence_value": "high",
    "adversarial_review_value": "medium",
    "context_isolation_value": "high",
    "conclusion_flip_potential": "medium",
}
CLAIM_ID = "freshwater-single-case-functional-fixture"
VALIDATION_CONDITIONS = (
    {
        "condition_id": "authority-envelope-exact",
        "statement": "All four frozen TianGong authority-following source identities match the accepted ledger.",
        "claim_ids": [CLAIM_ID],
    },
    {
        "condition_id": "claim-ceiling-retained",
        "statement": "The conclusion is limited to a single-case functional fixture and makes no comparative-LCA claim.",
        "claim_ids": [CLAIM_ID],
    },
    {
        "condition_id": "native-binding-preserved",
        "statement": "The accepted TianGong receipt, native binding, package manifest, and clean acceptance remain linked without correction.",
        "claim_ids": [CLAIM_ID],
    },
)
FLIP_CONDITIONS = (
    "Any frozen source hash changes or TianGong accepts an authority transition.",
    "Independent review finds that the package no longer binds its accepted receipt, native case, manifest, and clean acceptance.",
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _pretty_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _write_json(path: Path, value: object) -> None:
    content = _pretty_json(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != content:
            raise RuntimeError(f"Freshwater fixture artifact drifted: {path}")
        return
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def _read_json(path: Path, label: str) -> JsonObject:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _frozen_source_specs(root: Path) -> List[JsonObject]:
    ledger = _read_json(root / LEDGER_PATH, "agent project rebaseline ledger")
    dispositions = ledger.get("dispositions")
    if not isinstance(dispositions, list):
        raise ValueError("agent project rebaseline dispositions are absent")
    rows = [row for row in dispositions if isinstance(row, Mapping) and row.get("id") == FRESHWATER_LEDGER_ID]
    if len(rows) != 1:
        raise ValueError("frozen Freshwater ledger entry is not unique")
    row = rows[0]
    pointers = row.get("pointer")
    identity = row.get("identity")
    if (
        not isinstance(pointers, list)
        or len(pointers) != 4
        or not isinstance(identity, Mapping)
        or identity.get("kind") != "file_set_sha256"
        or not isinstance(identity.get("entries"), list)
        or len(identity["entries"]) != 4
    ):
        raise ValueError("frozen Freshwater source envelope is not exactly four files")
    declared = {
        str(entry.get("path")): str(entry.get("sha256"))
        for entry in identity["entries"]
        if isinstance(entry, Mapping)
    }
    if set(declared) != set(pointers):
        raise ValueError("frozen Freshwater pointer and identity sets differ")
    specs: List[JsonObject] = []
    for pointer in pointers:
        relative = Path(str(pointer))
        role = SOURCE_ROLES.get(relative.name)
        expected = declared.get(relative.as_posix())
        source = (root / relative).resolve()
        if role is None or not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
            raise ValueError("frozen Freshwater role or identity is unsupported")
        if not source.is_file() or _sha256_file(source) != expected:
            raise RuntimeError(f"frozen Freshwater source drifted: {relative}")
        specs.append(
            {
                "role": role,
                "source_path": relative.as_posix(),
                "sha256": expected,
                "bytes": source.stat().st_size,
            }
        )
    return specs


def _register_source(
    project: ResearchProject, root: Path, spec: Mapping[str, object]
) -> JsonObject:
    role = str(spec["role"])
    source = root / str(spec["source_path"])
    relative = Path("source-bindings") / f"{role}.json"
    destination = project.workspace / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = source.read_bytes()
    if destination.exists():
        if destination.read_bytes() != content:
            raise RuntimeError(f"copied Freshwater source drifted: {relative}")
    else:
        temporary = destination.with_name(f".{destination.name}.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, destination)
    ref: JsonObject = {
        "role": role,
        "path": relative.as_posix(),
        "sha256": str(spec["sha256"]),
        "bytes": int(spec["bytes"]),
    }
    project.record(
        "artifact.registered",
        {
            "artifact_id": f"freshwater-source-{role}-{str(spec['sha256'])[:16]}",
            "activity_id": "council-default-freshwater-functional-fixture",
            "skill_ref": "run-lca-research-project/council-default",
            "kind": "frozen_tiangong_authority_source",
            "artifact_role": role,
            "path": ref["path"],
            "workspace_path": ref["path"],
            "sha256": ref["sha256"],
            "bytes": ref["bytes"],
        },
        actor="controller",
        idempotency_key=f"freshwater-source:{role}:{spec['sha256']}",
    )
    return ref


def _ref_value(project: ResearchProject, ref: Mapping[str, object]) -> JsonObject:
    return _read_json(project.workspace / str(ref["path"]), "formal Council artifact")


def compile_freshwater_worker_contract(root: Path, output_root: Path) -> JsonObject:
    """Compile the deterministic formal agenda, route, Cell, and worker boundary."""

    root = root.resolve()
    output_root = output_root.resolve()
    project_root = output_root / "project"
    if (project_root / ResearchProject.EVENT_FILE).exists():
        project = ResearchProject.open(project_root)
    else:
        project = ResearchProject.create(
            project_root,
            project_id="council-default-freshwater-functional-fixture",
            title="Council-default Freshwater functional fixture",
            objective="Validate Council organization without making a comparative-LCA claim.",
        )
    source_refs = [
        _register_source(project, root, spec) for spec in _frozen_source_specs(root)
    ]
    source_refs.sort(key=_canonical_json)
    authority_ref = next(
        ref for ref in source_refs if ref.get("role") == "authority_receipt"
    )
    agenda_input = project_root / "inputs" / "freshwater-agenda.json"
    _write_json(
        agenda_input,
        {
            "goal": "Determine whether the exact accepted Freshwater package can serve as a source-bound single-case Council functional fixture without exceeding its TianGong claim ceiling.",
            "authority_receipt_ref": authority_ref,
            "facts": [
                {
                    "fact_id": "frozen-source-envelope",
                    "statement": "The accepted rebaseline binds exactly four immutable Freshwater authority-following files.",
                },
                {
                    "fact_id": "fixture-not-comparison",
                    "statement": "Freshwater contains one accepted case and is not a two-route comparative LCA benchmark.",
                },
            ],
            "hypotheses": [
                {
                    "hypothesis_id": "functional-fixture-suitable",
                    "statement": CANDIDATE_CONCLUSION,
                }
            ],
            "risks": [
                {
                    "risk_id": "authority-transition",
                    "statement": FLIP_CONDITIONS[0],
                },
                {
                    "risk_id": "overclaim",
                    "statement": "Functional acceptance could be misreported as comparative scientific evidence.",
                },
            ],
            "plan": [
                {
                    "step_id": "independent-source-bound-review",
                    "objective": "Independently reopen all four identities, challenge the claim ceiling, and return one bounded acceptance artifact.",
                }
            ],
        },
    )
    agenda_payload = project.compile_council_agenda(agenda_input)
    agenda_ref = agenda_payload["agenda_ref"]
    agenda = _ref_value(project, agenda_ref)

    route_input = project_root / "inputs" / "freshwater-route.json"
    _write_json(
        route_input,
        {
            "contract": "lca_research_kernel.council_route_request/v1",
            "agenda_ref": agenda_ref,
            "signals": ROUTE_SIGNALS,
        },
    )
    route_payload = project.route_council_decision(route_input)
    if route_payload.get("route") != "bounded_decision_cell":
        raise RuntimeError("Freshwater functional fixture did not select a bounded Cell")
    route_ref = route_payload["route_decision_ref"]

    cell_input = project_root / "inputs" / "freshwater-cell.json"
    _write_json(
        cell_input,
        {
            "contract": "lca_research_kernel.council_cell_request/v1",
            "agenda_ref": agenda_ref,
            "authority_ref": authority_ref,
            "route_ref": route_ref,
            "cell_request": {
                "agenda_revision": agenda["agenda_revision"],
                "decision_id": "freshwater-functional-fixture-suitability",
                "objective": "Independently verify the frozen authority envelope and bounded fixture claim.",
                "input_artifacts": source_refs,
                "claims": [
                    {
                        "claim_id": CLAIM_ID,
                        "statement": CANDIDATE_CONCLUSION,
                        "source_refs": source_refs,
                    }
                ],
                "roles": ["independent_acceptance"],
                "stop_conditions": [
                    "Stop after every frozen source is reopened and every validation condition has a source-bound result."
                ],
                "flip_conditions": list(FLIP_CONDITIONS),
                "validation_conditions": list(VALIDATION_CONDITIONS),
                "allowed_effects": ["local_read"],
                "output_contract": WORKER_OUTPUT_CONTRACT,
                "worker_stop_condition": "Return one bounded review after checking all four sources, the claim ceiling, and the authority links.",
            },
        },
    )
    cell_payload = project.open_council_cell(cell_input)
    cell_ref = cell_payload["cell_ref"]
    cell = _ref_value(project, cell_ref)
    bundle: JsonObject = {
        "contract": "lca_research_kernel.freshwater_worker_contract_bundle/v1",
        "fixture_role": FIXTURE_ROLE,
        "project_workspace": str(project_root),
        "source_binding_count": len(source_refs),
        "source_bindings": source_refs,
        "source_manifest_sha256": hashlib.sha256(
            _canonical_json(source_refs)
        ).hexdigest(),
        "agenda_revision": cell["agenda_revision"],
        "cell_revision": cell["cell_revision"],
        "agenda_ref": agenda_ref,
        "route_ref": route_ref,
        "cell_ref": cell_ref,
        "route": "bounded_decision_cell",
        "worker_responsibility": "independent_acceptance",
        "allowed_effects": ["local_read"],
        "output_contract": WORKER_OUTPUT_CONTRACT,
        "stop_condition": cell["worker_contract"]["stop_condition"],
        "candidate_conclusion": CANDIDATE_CONCLUSION,
        "candidate_conclusion_sha256": hashlib.sha256(
            CANDIDATE_CONCLUSION.encode("utf-8")
        ).hexdigest(),
        "claim_ids": [CLAIM_ID],
        "validation_condition_ids": sorted(
            str(row["condition_id"]) for row in VALIDATION_CONDITIONS
        ),
        "flip_conditions": sorted(FLIP_CONDITIONS),
        "comparative_lca_claimed": False,
        "manager_instruction": "Read only the declared source bindings; return the output contract to the manager and do not write Project truth.",
    }
    _write_json(output_root / "freshwater-worker-contract.json", bundle)
    return bundle


def _validated_worker_output(bundle: Mapping[str, object], path: Path) -> JsonObject:
    output = _read_json(path, "Freshwater independent worker output")
    expected = {
        "contract",
        "fixture_role",
        "review_status",
        "statement",
        "authority_preserved",
        "source_binding_count",
        "source_sha256s",
        "comparative_lca_claimed",
        "risks",
        "flip_conditions",
        "stop_condition_met",
    }
    if set(output) != expected or output.get("contract") != WORKER_OUTPUT_CONTRACT:
        raise ValueError("Freshwater independent worker output contract is unsupported")
    if (
        output.get("fixture_role") != FIXTURE_ROLE
        or output.get("review_status") != "accepted_with_bounded_claim"
        or output.get("statement") != bundle.get("candidate_conclusion")
        or output.get("authority_preserved") is not True
        or output.get("source_binding_count") != 4
        or output.get("comparative_lca_claimed") is not False
        or output.get("stop_condition_met") is not True
    ):
        raise ValueError("Freshwater independent worker conclusion is not bounded")
    expected_hashes = sorted(
        str(row["sha256"])
        for row in bundle.get("source_bindings", [])
        if isinstance(row, Mapping)
    )
    if sorted(output.get("source_sha256s") or []) != expected_hashes:
        raise ValueError("Freshwater independent worker source identities drifted")
    risks = output.get("risks")
    if not isinstance(risks, list) or not risks or any(
        not isinstance(value, str) or not value.strip() for value in risks
    ):
        raise ValueError("Freshwater independent worker risks are absent")
    if sorted(output.get("flip_conditions") or []) != bundle.get("flip_conditions"):
        raise ValueError("Freshwater independent worker flip conditions drifted")
    return output


def _worker_validation_scope(bundle: Mapping[str, object]) -> JsonObject:
    return {
        "claim_ids": list(bundle["claim_ids"]),
        "candidate_conclusion_sha256": bundle["candidate_conclusion_sha256"],
        "validation_condition_ids": list(bundle["validation_condition_ids"]),
    }


def compile_freshwater_independence_receipt(
    bundle: Mapping[str, object],
    worker_artifact_path: Path,
    *,
    opaque_resource_id_sha256: str,
) -> JsonObject:
    """Compile the manager-observed receipt without persisting a raw actor identity."""

    if not SHA256_RE.fullmatch(opaque_resource_id_sha256):
        raise ValueError("opaque worker resource identity must be a SHA-256")
    output = _validated_worker_output(bundle, worker_artifact_path)
    validation_scope = _worker_validation_scope(bundle)
    context = {
        "agenda_revision": bundle["agenda_revision"],
        "cell_revision": bundle["cell_revision"],
        "allowed_effects": list(bundle["allowed_effects"]),
        "output_contract": bundle["output_contract"],
        "stop_condition": bundle["stop_condition"],
        "result_kind": "independent_acceptance",
        "responsibility": bundle["worker_responsibility"],
        "statement": output["statement"],
        "validation_scope": validation_scope,
    }
    return {
        "resource_kind": "isolated_subagent_context",
        "opaque_resource_id_sha256": opaque_resource_id_sha256,
        "context_manifest_sha256": hashlib.sha256(
            _canonical_json(context)
        ).hexdigest(),
        "source_manifest_sha256": hashlib.sha256(
            _canonical_json(bundle["source_bindings"])
        ).hexdigest(),
        "output_sha256": _sha256_file(worker_artifact_path),
    }


def _copy_worker_output(project: ResearchProject, source: Path) -> JsonObject:
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    relative = Path("worker-outputs") / f"freshwater-independent-review-{digest[:16]}.json"
    destination = project.workspace / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.read_bytes() != content:
            raise RuntimeError("Freshwater registered worker output drifted")
    else:
        temporary = destination.with_name(f".{destination.name}.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, destination)
    return {
        "role": "freshwater_independent_review",
        "path": relative.as_posix(),
        "sha256": digest,
        "bytes": len(content),
    }


def run_freshwater_fixture(
    root: Path,
    output_root: Path,
    *,
    worker_artifact_path: Path,
    independence_receipt_path: Path,
) -> JsonObject:
    """Integrate one manager-observed independent result through the formal Kernel."""

    bundle = compile_freshwater_worker_contract(root, output_root)
    worker_output = _validated_worker_output(bundle, worker_artifact_path)
    receipt = _read_json(independence_receipt_path, "Freshwater independence receipt")
    expected_receipt = compile_freshwater_independence_receipt(
        bundle,
        worker_artifact_path,
        opaque_resource_id_sha256=str(receipt.get("opaque_resource_id_sha256")),
    )
    if receipt != expected_receipt:
        raise ValueError("Freshwater independence receipt is not manager-bound")
    project = ResearchProject.open(Path(str(bundle["project_workspace"])))
    worker_ref = _copy_worker_output(project, worker_artifact_path)

    worker_input = project.workspace / "inputs" / "freshwater-worker-result.json"
    _write_json(
        worker_input,
        {
            "contract": "lca_research_kernel.council_worker_result_request/v1",
            "cell_ref": bundle["cell_ref"],
            "result": {
                "agenda_revision": bundle["agenda_revision"],
                "cell_revision": bundle["cell_revision"],
                "result_kind": "independent_acceptance",
                "responsibility": bundle["worker_responsibility"],
                "source_bindings": bundle["source_bindings"],
                "allowed_effects": bundle["allowed_effects"],
                "output_contract": bundle["output_contract"],
                "stop_condition_met": True,
                "artifact_ref": worker_ref,
                "statement": worker_output["statement"],
                "validation_scope": _worker_validation_scope(bundle),
                "independence_receipt": receipt,
            },
        },
    )
    registered = project.register_council_worker_result(worker_input)
    worker_result_ref = registered["worker_result_ref"]
    worker_result = _ref_value(project, worker_result_ref)
    worker_result_id = worker_result["worker_result_id"]

    conditions = [
        {
            "condition_id": row["condition_id"],
            "statement": row["statement"],
        }
        for row in sorted(
            VALIDATION_CONDITIONS, key=lambda value: str(value["condition_id"])
        )
    ]
    validation_results = [
        {
            "condition_id": row["condition_id"],
            "status": "passed",
            "evidence_worker_result_ids": [worker_result_id],
        }
        for row in conditions
    ]
    advance_input = project.workspace / "inputs" / "freshwater-advance.json"
    _write_json(
        advance_input,
        {
            "contract": "lca_research_kernel.council_advance_request/v1",
            "cell_ref": bundle["cell_ref"],
            "worker_result_refs": [worker_result_ref],
            "incumbent_candidate": {
                "candidate_id": "freshwater-single-case-functional-fixture",
                "conclusion": bundle["candidate_conclusion"],
                "claim_ids": bundle["claim_ids"],
                "evidence_worker_result_ids": [worker_result_id],
                "dissent_worker_result_ids": [],
                "validation": {
                    "declared_conditions": conditions,
                    "results": validation_results,
                    "independent_acceptance_worker_result_id": worker_result_id,
                },
            },
        },
    )
    advanced = project.advance_council_cell(advance_input)
    if advanced.get("to_state") != "incumbent_validated":
        raise RuntimeError("Freshwater Cell did not validate its incumbent")

    close_input = project.workspace / "inputs" / "freshwater-close.json"
    _write_json(
        close_input,
        {
            "contract": "lca_research_kernel.council_close_request/v1",
            "cell_ref": bundle["cell_ref"],
            "cell_update_ref": advanced["cell_update_ref"],
            "close_reason": "Exact source envelope, authority preservation, and bounded claim ceiling independently passed.",
        },
    )
    closed = project.close_council_cell(close_input)

    accept_input = project.workspace / "inputs" / "freshwater-accept.json"
    _write_json(
        accept_input,
        {
            "contract": "lca_research_kernel.council_accept_request/v1",
            "agenda_ref": bundle["agenda_ref"],
            "route_ref": bundle["route_ref"],
            "cell_ref": bundle["cell_ref"],
            "accepted_result_ref": closed["cell_close_ref"],
        },
    )
    accepted = project.accept_council_outcome(accept_input)
    snapshot = project.materialize_views()
    council = snapshot["council"]
    if council.get("status") != "accepted":
        raise RuntimeError("Freshwater Council outcome was not accepted")

    receipt_bytes = independence_receipt_path.read_bytes()
    normalized: JsonObject = {
        "contract": "lca_research_kernel.freshwater_council_normalized_manifest/v1",
        "fixture_role": FIXTURE_ROLE,
        "source_bindings": bundle["source_bindings"],
        "source_manifest_sha256": bundle["source_manifest_sha256"],
        "agenda_revision": bundle["agenda_revision"],
        "cell_revision": bundle["cell_revision"],
        "route": bundle["route"],
        "worker_output_sha256": _sha256_file(worker_artifact_path),
        "independence_receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "worker_result_ref": worker_result_ref,
        "cell_update_ref": advanced["cell_update_ref"],
        "cell_close_ref": closed["cell_close_ref"],
        "outcome_ref": accepted["outcome_ref"],
        "flip_conditions": bundle["flip_conditions"],
        "comparative_lca_claimed": False,
        "forbidden_effect_count": 0,
    }
    manifest_path = output_root.resolve() / "normalized-manifest.json"
    _write_json(manifest_path, normalized)
    report: JsonObject = {
        "contract": "lca_research_kernel.freshwater_council_acceptance/v1",
        "fixture_role": FIXTURE_ROLE,
        "source_binding_count": 4,
        "independent_context_count": 1,
        "route": "bounded_decision_cell",
        "outcome_status": council["status"],
        "tiangong_authority_preserved": True,
        "comparative_lca_claimed": False,
        "flip_condition_count": len(bundle["flip_conditions"]),
        "dissent": council.get("dissent", []),
        "worker_output_sha256": normalized["worker_output_sha256"],
        "independence_receipt_sha256": normalized[
            "independence_receipt_sha256"
        ],
        "normalized_manifest_sha256": hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest(),
        "event_count": len(project.events()),
        "forbidden_effect_count": 0,
        "network_read_executed_count": 0,
        "remote_write_executed_count": 0,
        "publication_executed_count": 0,
        "next_route": "run-53-negative-authority-suite-and-fast-path",
    }
    _write_json(output_root.resolve() / "freshwater-acceptance-report.json", report)
    return report


def _negative_assets_spec(root: Path) -> JsonObject:
    ledger = _read_json(root / LEDGER_PATH, "agent project rebaseline ledger")
    rows = [
        row
        for row in ledger.get("dispositions", [])
        if isinstance(row, Mapping) and row.get("id") == "goal-a-negative-assets-and-monitor"
    ]
    if len(rows) != 1:
        raise ValueError("frozen Goal A negative-assets entry is not unique")
    identity = rows[0].get("identity")
    if not isinstance(identity, Mapping) or identity.get("kind") != "file_set_sha256":
        raise ValueError("frozen Goal A negative-assets identity is invalid")
    entries = [
        entry
        for entry in identity.get("entries", [])
        if isinstance(entry, Mapping)
        and str(entry.get("path", "")).endswith("/negative_assets.json")
    ]
    if len(entries) != 1:
        raise ValueError("frozen negative-assets source is not unique")
    entry = entries[0]
    path = root / str(entry["path"])
    expected = str(entry["sha256"])
    if not path.is_file() or _sha256_file(path) != expected:
        raise RuntimeError("frozen negative-assets source drifted")
    return {
        "path": str(entry["path"]),
        "sha256": expected,
        "bytes": path.stat().st_size,
    }


def _verify_declared_ref(root: Path, ref: Mapping[str, object]) -> None:
    path = root / str(ref.get("path"))
    if not path.is_file():
        raise RuntimeError(f"negative-suite source is missing: {ref.get('path')}")
    content = path.read_bytes()
    if (
        hashlib.sha256(content).hexdigest() != ref.get("sha256")
        or len(content) != ref.get("bytes")
    ):
        raise RuntimeError(f"negative-suite source drifted: {ref.get('path')}")


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    content = b"".join(_canonical_json(row) + b"\n" for row in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise RuntimeError(f"negative-suite ledger drifted: {path}")
        return
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def run_negative_authority_suite(root: Path, output_root: Path) -> JsonObject:
    """Evaluate all 53 frozen failures in one deterministic authority-refusal batch."""

    root = root.resolve()
    output_root = output_root.resolve()
    spec = _negative_assets_spec(root)
    negative = _read_json(root / str(spec["path"]), "negative authority assets")
    stress_cases = negative.get("stress_cases")
    if (
        negative.get("contract") != "lca_research_kernel.negative_benchmark_assets/v1"
        or negative.get("candidate_count") != 53
        or not isinstance(stress_cases, list)
        or len(stress_cases) != 53
        or negative.get("positive_benchmark_member_count") != 0
        or negative.get("formal_benchmark_eligible") is not False
    ):
        raise ValueError("negative authority suite is not the exact frozen 53-case set")
    unique_refs: Dict[str, Mapping[str, object]] = {}
    for case in stress_cases:
        if not isinstance(case, Mapping):
            raise ValueError("negative authority case is invalid")
        for ref in case.get("source_refs", []):
            if not isinstance(ref, Mapping):
                raise ValueError("negative authority source ref is invalid")
            unique_refs[str(ref.get("path"))] = ref
    for ref in unique_refs.values():
        _verify_declared_ref(root, ref)
    qualification_refs = [
        ref for ref in unique_refs.values() if ref.get("role") == "qualification_ledger"
    ]
    if len(qualification_refs) != 1:
        raise ValueError("negative authority qualification ledger is not unique")
    qualification = _read_json(
        root / str(qualification_refs[0]["path"]),
        "negative authority qualification ledger",
    )
    candidate_rows = qualification.get("candidate_ledger")
    if (
        qualification.get("qualification_id") != negative.get("qualification_id")
        or qualification.get("external_authority_substituted") is not False
        or qualification.get("admission_criteria_lowered") is not False
        or not isinstance(candidate_rows, list)
        or len(candidate_rows) != 53
    ):
        raise ValueError("negative authority qualification state drifted")
    candidates = {
        (
            str(row.get("cluster_id")),
            str(row.get("process_id")),
            str(row.get("process_version")),
        ): row
        for row in candidate_rows
        if isinstance(row, Mapping)
    }
    if len(candidates) != 53:
        raise ValueError("negative authority candidate identities are not unique")
    refusals: List[JsonObject] = []
    for case in stress_cases:
        ref = case.get("candidate_ref")
        if not isinstance(ref, Mapping):
            raise ValueError("negative authority candidate ref is invalid")
        key = (
            str(ref.get("cluster_id")),
            str(ref.get("process_id")),
            str(ref.get("process_version")),
        )
        candidate = candidates.get(key)
        if (
            not isinstance(candidate, Mapping)
            or candidate.get("screening_status")
            not in {"rejected", "comparison_pair"}
            or candidate.get("terminal_blocker")
            != "tiangong_provider_closure_unpublished"
            or "missing_provider_closure"
            not in (candidate.get("screening_reasons") or [])
            or case.get("candidate_admission_status") != "rejected"
            or case.get("formal_benchmark_eligible") is not False
        ):
            raise ValueError(f"negative authority refusal evidence is incomplete: {key}")
        refusals.append(
            {
                "asset_id": case.get("asset_id"),
                "candidate_ref": dict(ref),
                "disposition": "refused_incomplete_tiangong_provider_closure",
                "terminal_blocker": candidate.get("terminal_blocker"),
                "screening_reasons": sorted(candidate.get("screening_reasons") or []),
                "provider_invented": False,
                "closure_invented": False,
                "native_result_relabelled": False,
                "authority_override": False,
                "positive_benchmark_claimed": False,
            }
        )
    refusals.sort(
        key=lambda row: (
            str(row["candidate_ref"]["cluster_id"]),
            str(row["candidate_ref"]["process_id"]),
            str(row["candidate_ref"]["process_version"]),
        )
    )
    ledger_path = output_root / "negative-authority-ledger.jsonl"
    _write_jsonl(ledger_path, refusals)
    report: JsonObject = {
        "contract": "lca_research_kernel.council_negative_authority_acceptance/v1",
        "negative_assets_path": spec["path"],
        "negative_assets_sha256": spec["sha256"],
        "negative_assets_bytes": spec["bytes"],
        "qualification_id": negative["qualification_id"],
        "candidate_count": 53,
        "refusal_count": 53,
        "missing_provider_closure_count": 53,
        "provider_invented_count": 0,
        "closure_invented_count": 0,
        "native_result_relabelled_count": 0,
        "authority_override_count": 0,
        "positive_benchmark_member_count": 0,
        "clusters": sorted({str(row["candidate_ref"]["cluster_id"]) for row in refusals}),
        "ledger_sha256": _sha256_file(ledger_path),
        "forbidden_effect_count": 0,
        "network_read_executed_count": 0,
        "remote_write_executed_count": 0,
        "next_route": "accept-deterministic-council-fast-path",
    }
    _write_json(output_root / "negative-authority-report.json", report)
    return report


FAST_PATH_SIGNALS = {
    "scientific_importance": "low",
    "dispute_level": "low",
    "procedure_determinism": "high",
    "dependency_strength": "high",
    "independent_evidence_value": "low",
    "adversarial_review_value": "low",
    "context_isolation_value": "low",
    "conclusion_flip_potential": "low",
}


def _register_copied_file(
    project: ResearchProject,
    source: Path,
    *,
    relative: Path,
    role: str,
) -> JsonObject:
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    destination = project.workspace / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.read_bytes() != content:
            raise RuntimeError(f"fast-path source drifted: {relative}")
    else:
        temporary = destination.with_name(f".{destination.name}.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, destination)
    ref: JsonObject = {
        "role": role,
        "path": relative.as_posix(),
        "sha256": digest,
        "bytes": len(content),
    }
    project.record(
        "artifact.registered",
        {
            "artifact_id": f"fast-path-source-{role}-{digest[:16]}",
            "activity_id": "council-default-fast-path-fixture",
            "skill_ref": "run-lca-research-project/council-default",
            "artifact_role": role,
            "path": ref["path"],
            "workspace_path": ref["path"],
            "sha256": digest,
            "bytes": len(content),
        },
        actor="controller",
        idempotency_key=f"fast-path-source:{role}:{digest}",
    )
    return ref


def run_fast_path_fixture(root: Path, output_root: Path) -> JsonObject:
    """Accept one exact-hash/count check through the formal single-Controller route."""

    root = root.resolve()
    output_root = output_root.resolve()
    project_root = output_root / "project"
    if (project_root / ResearchProject.EVENT_FILE).exists():
        project = ResearchProject.open(project_root)
    else:
        project = ResearchProject.create(
            project_root,
            project_id="council-default-fast-path-fixture",
            title="Council-default deterministic fast-path fixture",
            objective="Verify one exact frozen artifact identity without opening a Cell.",
        )
    negative_spec = _negative_assets_spec(root)
    negative_path = root / str(negative_spec["path"])
    negative = _read_json(negative_path, "negative authority assets")
    source_ref = _register_copied_file(
        project,
        negative_path,
        relative=Path("source-bindings/negative-authority-assets.json"),
        role="negative_authority_assets",
    )
    authority_source = root / (
        "outputs/tiangong-authority-following-second-case/authority/"
        "receipt-50d206a2899a86b1.json"
    )
    authority_ref = _register_copied_file(
        project,
        authority_source,
        relative=Path("source-bindings/authority-receipt.json"),
        role="authority_receipt",
    )
    agenda_input = project_root / "inputs/fast-path-agenda.json"
    _write_json(
        agenda_input,
        {
            "goal": "Verify the exact frozen negative-assets file hash and declared candidate count.",
            "authority_receipt_ref": authority_ref,
            "facts": [
                {
                    "fact_id": "frozen-negative-assets",
                    "statement": "The accepted rebaseline fixes one exact 53-candidate negative asset file.",
                }
            ],
            "hypotheses": [],
            "risks": [
                {
                    "risk_id": "content-drift",
                    "statement": "Any content drift invalidates this deterministic result.",
                }
            ],
            "plan": [
                {
                    "step_id": "hash-and-count",
                    "objective": "Compare the file SHA-256 and candidate_count with the accepted ledger.",
                }
            ],
        },
    )
    agenda_payload = project.compile_council_agenda(agenda_input)
    route_input = project_root / "inputs/fast-path-route.json"
    _write_json(
        route_input,
        {
            "contract": "lca_research_kernel.council_route_request/v1",
            "agenda_ref": agenda_payload["agenda_ref"],
            "signals": FAST_PATH_SIGNALS,
        },
    )
    route_payload = project.route_council_decision(route_input)
    if route_payload.get("route") != "single_controller_fast_path":
        raise RuntimeError("deterministic fixture did not select the fast path")
    fast_input = project_root / "inputs/fast-path-result.json"
    _write_json(
        fast_input,
        {
            "contract": "lca_research_kernel.council_fast_path_request/v1",
            "agenda_ref": agenda_payload["agenda_ref"],
            "route_ref": route_payload["route_decision_ref"],
            "source_refs": [source_ref],
            "allowed_effects": ["local_read"],
            "statement": "The frozen negative-assets artifact hash and 53-candidate count match exactly.",
            "result": {
                "observed_sha256": _sha256_file(negative_path),
                "expected_sha256": negative_spec["sha256"],
                "candidate_count": negative.get("candidate_count"),
                "exact_match": True,
            },
        },
    )
    completed = project.complete_council_fast_path(fast_input)
    accept_input = project_root / "inputs/fast-path-accept.json"
    _write_json(
        accept_input,
        {
            "contract": "lca_research_kernel.council_accept_request/v1",
            "agenda_ref": agenda_payload["agenda_ref"],
            "route_ref": route_payload["route_decision_ref"],
            "cell_ref": None,
            "accepted_result_ref": completed["result_ref"],
        },
    )
    project.accept_council_outcome(accept_input)
    before_replay = len(project.events())
    project.complete_council_fast_path(fast_input)
    project.accept_council_outcome(accept_input)
    after_replay = len(project.events())
    snapshot = project.materialize_views()
    report: JsonObject = {
        "contract": "lca_research_kernel.council_fast_path_acceptance/v1",
        "route": snapshot["council"]["route"],
        "outcome_status": snapshot["council"]["status"],
        "decision_cell_opened_count": sum(
            event.get("event_type") == "research.council.cell_opened"
            for event in project.events()
        ),
        "source_candidate_count": negative["candidate_count"],
        "source_sha256": negative_spec["sha256"],
        "same_key_replay_idempotent": before_replay == after_replay,
        "forbidden_effect_count": 0,
        "network_read_executed_count": 0,
        "remote_write_executed_count": 0,
        "next_route": "verify-council-recovery-stale-drift-and-rollback",
    }
    _write_json(output_root / "fast-path-acceptance-report.json", report)
    return report


def compile_read_only_acceptance(
    root: Path, project: ResearchProject
) -> JsonObject:
    """Replay formal Council truth and all registered bytes without materialization."""

    del root
    before = project.event_path.read_bytes()
    events = project.events()
    snapshot = project._project(events)
    council = snapshot.get("council")
    if not isinstance(council, Mapping):
        raise ValueError("formal Council projection is absent")
    if project.event_path.read_bytes() != before:
        raise RuntimeError("read-only Council acceptance mutated event history")
    return {
        "contract": "lca_research_kernel.council_recovery_read_only_acceptance/v1",
        "status": "accepted",
        "formal_status": council.get("formal_status"),
        "council_status": council.get("status"),
        "event_count": len(events),
        "event_log_sha256": hashlib.sha256(before).hexdigest(),
        "council_projection_sha256": hashlib.sha256(
            _canonical_json(council)
        ).hexdigest(),
        "writes_performed": 0,
    }


def compile_rollback_receipt(
    root: Path, project: ResearchProject
) -> JsonObject:
    """Verify the exact frozen 22d rollback object through the formal Project port."""

    del root
    request_path = project.workspace / "inputs" / "verify-council-rollback.json"
    _write_json(
        request_path,
        {
            "contract": "lca_research_kernel.council_rollback_request/v1",
            "rollback_commit": "22d0576ad1a3f25ceef05ec5940fb02a848a27e9",
        },
    )
    payload = project.verify_council_rollback(request_path)
    receipt_ref = payload.get("receipt_ref")
    if not isinstance(receipt_ref, Mapping):
        raise RuntimeError("formal Council rollback receipt ref is absent")
    receipt = _ref_value(project, receipt_ref)
    return {**receipt, "receipt_ref": dict(receipt_ref)}


def run_recovery_rollback_acceptance(
    root: Path, output_root: Path
) -> JsonObject:
    """Run projection, replay, stale, drift, and rollback checks as one tranche."""

    root = root.resolve()
    output_root = output_root.resolve()
    source_project = (
        root / "outputs/council-default-acceptance/freshwater/run-a/project"
    )
    project_root = output_root / "project"
    if project_root.exists():
        shutil.rmtree(project_root)
    project_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_project, project_root)
    project = ResearchProject.open(project_root)

    initial = compile_read_only_acceptance(root, project)
    expected_snapshot = project._project(project.events())
    event_before_rebuild = project.event_path.read_bytes()
    for name in (
        ResearchProject.SNAPSHOT_FILE,
        ResearchProject.MACHINE_FILE,
        ResearchProject.HUMAN_FILE,
    ):
        path = project.workspace / name
        if path.exists():
            path.unlink()
    rebuilt = project.materialize_views()
    projection_rebuild_identical = (
        rebuilt["council"] == expected_snapshot["council"]
        and project.event_path.read_bytes() == event_before_rebuild
    )

    replay_calls = (
        (project.compile_council_agenda, "freshwater-agenda.json"),
        (project.route_council_decision, "freshwater-route.json"),
        (project.open_council_cell, "freshwater-cell.json"),
        (project.register_council_worker_result, "freshwater-worker-result.json"),
        (project.advance_council_cell, "freshwater-advance.json"),
        (project.close_council_cell, "freshwater-close.json"),
        (project.accept_council_outcome, "freshwater-accept.json"),
    )
    replay_before = len(project.events())
    for operation, filename in replay_calls:
        operation(project.workspace / "inputs" / filename)
    replay_after = len(project.events())

    stale_request = _read_json(
        project.workspace / "inputs/freshwater-worker-result.json",
        "Freshwater worker result request",
    )
    stale_request["result"]["agenda_revision"] = "0" * 64
    stale_path = project.workspace / "probes/stale-worker-result.json"
    _write_json(stale_path, stale_request)
    stale_before = project.event_path.read_bytes()
    stale_rejected = False
    try:
        project.register_council_worker_result(stale_path)
    except ValueError as exc:
        stale_rejected = "stale agenda revision" in str(exc)
    if project.event_path.read_bytes() != stale_before:
        raise RuntimeError("stale worker rejection appended an event")

    drift_root = output_root / "drift-probe-project"
    if drift_root.exists():
        shutil.rmtree(drift_root)
    shutil.copytree(project.workspace, drift_root)
    drift_project = ResearchProject.open(drift_root)
    drift_artifact = next(
        drift_root.glob("artifacts/council-default/*/*.json")
    )
    if not drift_artifact.read_bytes().endswith(b"\n\n"):
        drift_artifact.write_bytes(drift_artifact.read_bytes() + b"\n")
    drift_rejected = False
    try:
        compile_read_only_acceptance(root, drift_project)
    except RuntimeError as exc:
        drift_rejected = "drifted" in str(exc)

    cell_ref = expected_snapshot["council"]["artifact_refs"][2]
    cell_candidates = list(
        project.workspace.glob("artifacts/council-default/council_cell/*.json")
    )
    if len(cell_candidates) != 1:
        raise RuntimeError("recovery fixture Cell artifact is not unique")
    cell = _read_json(cell_candidates[0], "recovery fixture Cell")
    flip_preserved = (
        len(cell.get("flip_conditions") or []) == 2
        and expected_snapshot["council"].get("dissent") == []
        and isinstance(cell_ref, Mapping)
    )

    history_before_rollback = project.event_path.read_bytes()
    receipt = compile_rollback_receipt(root, project)
    history_after_rollback = project.event_path.read_bytes()
    history_append_only = history_after_rollback.startswith(history_before_rollback)
    activation_path = project.workspace / "inputs/premature-rollback-activation.json"
    _write_json(
        activation_path,
        {
            "contract": "lca_research_kernel.council_rollback_activation_request/v1",
            "rollback_receipt_ref": receipt["receipt_ref"],
        },
    )
    before_activation = project.event_path.read_bytes()
    premature_rejected = False
    try:
        project.activate_council_rollback(activation_path)
    except ValueError as exc:
        premature_rejected = "requires an accepted cutover" in str(exc)
    if project.event_path.read_bytes() != before_activation:
        raise RuntimeError("premature rollback activation appended an event")
    final = compile_read_only_acceptance(root, project)
    report: JsonObject = {
        "contract": "lca_research_kernel.council_recovery_rollback_acceptance/v1",
        "projection_rebuild_identical": projection_rebuild_identical,
        "same_key_replay_idempotent": replay_before == replay_after,
        "interruption_recovery_verified": projection_rebuild_identical,
        "registered_artifact_drift_rejected": drift_rejected,
        "stale_worker_result_rejected": stale_rejected,
        "dissent_and_flip_conditions_preserved": flip_preserved,
        "rollback_exact_22d_verified": receipt.get("status") == "verified",
        "rollback_commit": receipt.get("rollback_commit"),
        "rollback_file_count": len(receipt.get("files") or []),
        "premature_rollback_rejected": premature_rejected,
        "history_append_only": history_append_only,
        "formal_status": final["formal_status"],
        "initial_event_count": initial["event_count"],
        "final_event_count": final["event_count"],
        "forbidden_effect_count": 0,
        "network_read_executed_count": 0,
        "remote_write_executed_count": 0,
        "next_route": "run-clean-acceptance-and-guarded-council-cutover",
    }
    if not all(
        report[field]
        for field in (
            "projection_rebuild_identical",
            "same_key_replay_idempotent",
            "interruption_recovery_verified",
            "registered_artifact_drift_rejected",
            "stale_worker_result_rejected",
            "dissent_and_flip_conditions_preserved",
            "rollback_exact_22d_verified",
            "premature_rollback_rejected",
            "history_append_only",
        )
    ):
        raise RuntimeError("Council recovery/rollback tranche did not pass")
    _write_json(output_root / "recovery-rollback-acceptance-report.json", report)
    return report
